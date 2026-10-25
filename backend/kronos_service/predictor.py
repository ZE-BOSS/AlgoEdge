"""
backend/kronos_service/predictor.py

Kronos inference that KEEPS the sample paths, and is reproducible per series.

WHY NOT `KronosPredictor.predict`
---------------------------------
Upstream `auto_regressive_inference` averages the Monte Carlo paths before it
returns (`np.mean(preds, axis=1)`), so `p_up` ("what share of paths end
higher") and `dispersion` (how much they disagree) cannot be computed from it.
This module runs the same steps - same tokenizer encode, same two-stage s1/s2
sampling with the same temperature / top-p filter, same decode - and returns
every path.

WHY ITS OWN SAMPLER
-------------------
Upstream samples with `torch.multinomial` on the global RNG, so one series'
paths depend on what else was in the batch and on everything sampled before.
Here each (symbol, bar time, path) row gets its own seeded generator. The same
window therefore gives the same features whether it is forecast alone or in a
batch, in the offline precompute or live - which is what backtest/live parity
needs from a sampled model.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

import numpy as np
import pandas as pd

from backend.kronos_service.config import KronosConfig

PRICE_COLS = ["open", "high", "low", "close"]
ALL_COLS = PRICE_COLS + ["volume", "amount"]
CLIP = 5.0


def _seed_for(base: int, symbol: str, asof: int, path: int) -> int:
    h = hashlib.sha256(f"{base}|{symbol}|{int(asof)}|{path}".encode()).digest()
    return int.from_bytes(h[:8], "little") & 0x7FFF_FFFF_FFFF_FFFF


@dataclass
class Window:
    """One series to forecast: the bars up to and INCLUDING the decision bar."""
    symbol: str
    frame: pd.DataFrame          # open, high, low, close[, volume]; DatetimeIndex or 'time' (s)
    future_times: pd.DatetimeIndex
    asof: int                    # decision bar's open time, epoch seconds


def _times(frame: pd.DataFrame) -> pd.DatetimeIndex:
    if "time" in frame.columns:
        return pd.DatetimeIndex(pd.to_datetime(frame["time"].to_numpy(), unit="s"))
    return pd.DatetimeIndex(frame.index)


def _stamps(t: pd.DatetimeIndex) -> np.ndarray:
    # Upstream `calc_time_stamps`: minute, hour, weekday, day, month.
    return np.stack([t.minute, t.hour, t.weekday, t.day, t.month], axis=1).astype(np.float32)


def future_times(last_bar_time: pd.Timestamp, minutes: int, pred_len: int) -> pd.DatetimeIndex:
    """Calendar-spaced future bar times. Used identically in precompute and
    live so the time embedding the model sees is the same on both paths; it
    does not know about weekends or session gaps, and neither path pretends to."""
    return pd.DatetimeIndex([last_bar_time + pd.Timedelta(minutes=minutes * (k + 1))
                             for k in range(pred_len)])


class KronosEngine:
    """Loads the checkpoint once (offline, from `models/kronos/`) and forecasts."""

    def __init__(self, cfg: KronosConfig):
        import torch

        from backend.kronos_service.vendor.kronos import Kronos, KronosTokenizer

        self.cfg = cfg
        self.torch = torch
        torch.set_num_threads(max(1, int(cfg.threads)))
        try:
            torch.set_num_interop_threads(1)
        except RuntimeError:            # may only be set once per process
            pass
        self.tokenizer = KronosTokenizer.from_pretrained(str(cfg.tokenizer_dir())).eval()
        self.model = Kronos.from_pretrained(str(cfg.model_dir())).eval()

    # -- the sampler ---------------------------------------------------------

    def _sample(self, logits, gens):
        from backend.kronos_service.vendor.kronos import top_k_top_p_filtering
        torch = self.torch
        logits = logits / max(1e-6, self.cfg.temperature)
        logits = top_k_top_p_filtering(logits, top_k=0, top_p=self.cfg.top_p)
        probs = torch.softmax(logits, dim=-1)
        out = torch.empty((probs.shape[0], 1), dtype=torch.long)
        for r in range(probs.shape[0]):
            out[r, 0] = torch.multinomial(probs[r], 1, generator=gens[r])[0]
        return out

    # -- generation ------------------------------------------------------------
    #
    # Upstream re-runs the WHOLE window through every layer for every forecast
    # step - no KV cache - so a 12-bar forecast over a 400-bar window processes
    # ~4,900 positions instead of ~412. Measured on CPU (1 thread, 5 paths):
    # 36 s/symbol for Kronos-mini at 1000 bars and 63 s for Kronos-small at 400
    # (2026-10-19), which fits ~1 symbol in an M5 bar's budget.
    #
    # `_generate_cached` is the same computation done incrementally: causal
    # self-attention means a past position's keys and values never change, so
    # they are kept per layer and each step runs only the new token. Exact only
    # while the window does not slide (lookback + pred_len <= max_context) -
    # once it slides upstream re-encodes every position with new rotary
    # positions - so that case takes `_generate_full`, which is upstream's loop.
    # `tests/test_kronos_service.py` checks the two agree.

    cached = True

    def _generate_full(self, pre, post, full_stamp, n0, H, ctx, gens):
        torch = self.torch
        pre_seq, post_seq = pre, post
        for i in range(H):
            cur = n0 + i
            lo = max(0, cur - ctx)
            s1_logits, context = self.model.decode_s1(
                pre_seq[:, lo:cur], post_seq[:, lo:cur], full_stamp[:, lo:cur, :].contiguous())
            s1 = self._sample(s1_logits[:, -1, :], gens)
            s2_logits = self.model.decode_s2(context, s1)
            s2 = self._sample(s2_logits[:, -1, :], gens)
            pre_seq = torch.cat([pre_seq, s1], dim=1)
            post_seq = torch.cat([post_seq, s2], dim=1)
        return pre_seq, post_seq

    @staticmethod
    def _rope(att, pos0: int, n: int, like):
        import torch
        t = torch.arange(pos0, pos0 + n, dtype=att.rotary.inv_freq.dtype)
        freqs = torch.einsum("i,j->ij", t, att.rotary.inv_freq)
        emb = torch.cat((freqs, freqs), dim=-1)
        return emb.cos()[None, None].to(like.dtype), emb.sin()[None, None].to(like.dtype)

    @staticmethod
    def _rot(x):
        import torch
        x1, x2 = x.chunk(2, dim=-1)
        return torch.cat((-x2, x1), dim=-1)

    def _blocks(self, x, caches, pos0):
        """Run the transformer stack on `x` (positions pos0..), extending caches."""
        import torch.nn.functional as F
        torch = self.torch
        for li, layer in enumerate(self.model.transformer):
            att = layer.self_attn
            B, T, _ = x.shape
            h = layer.norm1(x)
            q = att.q_proj(h).view(B, T, att.n_heads, att.head_dim).transpose(1, 2)
            k = att.k_proj(h).view(B, T, att.n_heads, att.head_dim).transpose(1, 2)
            v = att.v_proj(h).view(B, T, att.n_heads, att.head_dim).transpose(1, 2)
            cos, sin = self._rope(att, pos0, T, q)
            q = q * cos + self._rot(q) * sin
            k = k * cos + self._rot(k) * sin
            if caches[li] is not None:
                k = torch.cat([caches[li][0], k], dim=2)
                v = torch.cat([caches[li][1], v], dim=2)
            caches[li] = (k, v)
            # Prefill: q and k are the same span -> causal. A one-token step
            # attends to everything before it, which is what causal means there.
            o = F.scaled_dot_product_attention(q, k, v, is_causal=(T > 1))
            o = o.transpose(1, 2).contiguous().view(B, T, att.d_model)
            x = x + att.out_proj(o)
            x = x + layer.ffn(layer.norm2(x))
        return x

    def _s2_logits(self, ctx_kv, ctx_last, s1):
        """`decode_s2` for the LAST position only: the cross-attention's keys
        and values over every context position are cached and extended."""
        import torch.nn.functional as F
        dep = self.model.dep_layer
        ca = dep.cross_attn
        sib = self.model.embedding.emb_s1(s1)                       # (N, 1, d)
        N = sib.shape[0]
        # Upstream rotates q and k with the QUERY length (1): position 0,
        # i.e. no rotation at all. Reproduced by simply not rotating.
        q = ca.q_proj(sib).view(N, 1, ca.n_heads, ca.head_dim).transpose(1, 2)
        o = F.scaled_dot_product_attention(q, ctx_kv[0], ctx_kv[1], is_causal=False)
        o = ca.out_proj(o.transpose(1, 2).contiguous().view(N, 1, ca.d_model))
        return self.model.head.cond_forward(dep.norm(ctx_last + o))[:, -1, :]

    def _ctx_kv(self, ca, ctx, prev):
        torch = self.torch
        N, T, _ = ctx.shape
        k = ca.k_proj(ctx).view(N, T, ca.n_heads, ca.head_dim).transpose(1, 2)
        v = ca.v_proj(ctx).view(N, T, ca.n_heads, ca.head_dim).transpose(1, 2)
        if prev is not None:
            k = torch.cat([prev[0], k], dim=2)
            v = torch.cat([prev[1], v], dim=2)
        return k, v

    def _generate_cached(self, pre, post, full_stamp, n0, H, gens):
        torch = self.torch
        m = self.model
        ca = m.dep_layer.cross_attn
        caches = [None] * len(m.transformer)

        def embed(p, q, lo, hi):
            return m.embedding([p, q]) + m.time_emb(full_stamp[:, lo:hi, :].contiguous())

        hidden = m.norm(self._blocks(embed(pre, post, 0, n0), caches, 0))
        ctx_kv = self._ctx_kv(ca, hidden, None)
        last = hidden[:, -1:, :]
        s1_seq, s2_seq = [pre], [post]
        for i in range(H):
            s1 = self._sample(m.head(last)[:, -1, :], gens)
            s2 = self._sample(self._s2_logits(ctx_kv, last, s1), gens)
            s1_seq.append(s1)
            s2_seq.append(s2)
            if i == H - 1:
                break
            pos = n0 + i
            h_new = m.norm(self._blocks(embed(s1, s2, pos, pos + 1), caches, pos))
            ctx_kv = self._ctx_kv(ca, h_new, ctx_kv)
            last = h_new
        return torch.cat(s1_seq, dim=1), torch.cat(s2_seq, dim=1)

    # -- the forecast --------------------------------------------------------

    def paths(self, windows: list[Window]) -> tuple[np.ndarray, float]:
        """(B, S, pred_len, 6) sampled paths in PRICE space, and seconds taken.

        Every window must have the same length (`cfg.lookback`) - the batch is
        one tensor, as upstream's `predict_batch` requires."""
        torch = self.torch
        cfg = self.cfg
        S, H, ctx = cfg.sample_count, cfg.pred_len, cfg.max_context
        t0 = time.perf_counter()
        xs, xst, yst, mus, sds = [], [], [], [], []
        for w in windows:
            df = w.frame
            if len(df) != cfg.lookback:
                raise ValueError(f"{w.symbol}: window has {len(df)} bars, need {cfg.lookback}")
            x = np.zeros((len(df), 6), dtype=np.float32)
            x[:, :4] = df[PRICE_COLS].to_numpy(dtype=np.float32)
            if "volume" in df.columns:
                x[:, 4] = df["volume"].to_numpy(dtype=np.float32)
                x[:, 5] = x[:, 4] * x[:, :4].mean(axis=1)
            mu, sd = x.mean(axis=0), x.std(axis=0)
            xs.append(np.clip((x - mu) / (sd + 1e-5), -CLIP, CLIP))
            mus.append(mu)
            sds.append(sd)
            xst.append(_stamps(_times(df)))
            yst.append(_stamps(pd.DatetimeIndex(w.future_times)))
        B = len(windows)
        gens = []
        for w in windows:
            for s in range(S):
                g = torch.Generator()
                g.manual_seed(_seed_for(cfg.seed, w.symbol, w.asof, s))
                gens.append(g)

        with torch.inference_mode():
            x = torch.from_numpy(np.stack(xs))
            x_stamp = torch.from_numpy(np.stack(xst))
            y_stamp = torch.from_numpy(np.stack(yst))
            # (B, ...) -> (B*S, ...): row b*S + s is path s of series b.
            rep = lambda t: t.unsqueeze(1).repeat(1, S, 1, 1).reshape(-1, t.size(1), t.size(2))
            x, x_stamp, y_stamp = rep(x), rep(x_stamp), rep(y_stamp)
            pre, post = self.tokenizer.encode(x, half=True)
            n0 = x.size(1)
            full_stamp = torch.cat([x_stamp, y_stamp], dim=1)
            if self.cached and n0 + H <= ctx:
                pre_seq, post_seq = self._generate_cached(pre, post, full_stamp, n0, H, gens)
            else:
                pre_seq, post_seq = self._generate_full(pre, post, full_stamp, n0, H, ctx, gens)
            total = n0 + H
            lo = max(0, total - ctx)
            z = self.tokenizer.decode([pre_seq[:, lo:total].contiguous(),
                                       post_seq[:, lo:total].contiguous()], half=True)
            z = z[:, -H:, :].cpu().numpy().reshape(B, S, H, 6)
        mu = np.stack(mus)[:, None, None, :]
        sd = np.stack(sds)[:, None, None, :]
        return z * (sd + 1e-5) + mu, time.perf_counter() - t0
