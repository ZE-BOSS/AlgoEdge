"""
backend/analytics/vwap_research.py

VWAP_v1, stripped to its individual confluences and put back together by
measurement rather than by belief.

WHY IT IS BUILT THIS WAY
------------------------
The live strategy fuses ten conditions into one `if`. Ask "is the momentum
filter worth having on GBPJPY?" and the only honest answer needs the trade list
with that condition off — which is a different backtest per condition per asset
per window, thousands of slow runs.

So this module runs each asset ONCE in the most permissive form of the strategy
(every optional confluence disabled) and records, for every candidate:

  * each confluence as a BOOLEAN, evaluated exactly as strategy_vwap/engine.py
    evaluates it at that bar (`features`);
  * the outcome in R for every exit variant (`outcomes`).

Any combination of confluences is then evaluated by FILTERING that list, which
is exact, not an approximation — including the two path-dependent rules
(`first_pullback_only`, `max_trades_per_day`), because filtering happens in time
order within each session and the survivors are taken in order.

FAITHFULNESS TO THE LIVE ENGINE
-------------------------------
Same 09:30-ET session anchor, same reset-per-session cumulative VWAP, same
volume-weighted running σ around the RUNNING vwap (never the session's final
vwap — that would look forward), same slope reference (the vwap
`anchor_minutes / timeframe` bars back), same momentum definition
(`(close - close[-L]) / close[-L] * 100`, L = lookback x bar_multiplier), same
volume ratio (bar volume over the mean of the previous N), same convergence
test, same stop construction (trigger extreme vs ±1σ for the pullback, ±3σ for
the reversion) and the same pip/spread floors. tests/test_vwap_research.py holds
the VWAP/σ numbers to the engine's own function.

WHAT IS DELIBERATELY NOT COPIED
-------------------------------
The daily guardrails (`max_losses_per_day`, `drawdown_kill_pct`) are account
rules, not confluences: they belong to money_sim.py, which applies them across
the whole book instead of per symbol.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

import numpy as np
import pytz

from backend.analytics.strategy_search import Bars, atr

ET = pytz.timezone("America/New_York")
_ANCHOR_SECONDS = {"et_0930": 9 * 3600 + 30 * 60, "et_0000": 0, "london_0800": 8 * 3600}
MAX_HOLD_BARS = 288          # 24h on M5
_MIN_WICK_DENOM = 1e-12

# (target, session_close_at_hard_close, breakeven_at_1R) — "sigma2" and "vwap"
# are the strategy's own structural targets; the numbers are plain R multiples.
EXIT_VARIANTS: tuple[tuple[Any, bool, bool], ...] = tuple(
    [(t, True, False) for t in ("sigma2", "vwap", 1.0, 1.5, 2.0, 3.0, 4.0)]
    + [(2.0, False, False), ("sigma2", False, False), (2.0, True, True), ("sigma2", True, True)]
)

CONFLUENCES = (
    "slope_aligned",        # VWAP sloping with the side price is on
    "momentum_aligned",     # lookback move agrees, >= threshold
    "inside_1sigma",        # pullback still inside +/-1 sigma of value
    "converging",           # distance to VWAP shrinking vs the previous bar
    "volume_ok",            # trigger-bar volume >= mult x recent mean
    "in_session_window",    # inside (exclude_end, entry_cutoff) ET
    "first_of_session",     # first surviving pullback candidate that session
    "wick_rejection",       # reversion only: rejection wick against the extension
    "slope_flat",           # reversion only: |slope| <= pct x ATR
    "trend_neutral",        # reversion only: momentum not confirming the extension
    "beyond_2sigma",        # reversion only: closed beyond the band
)


def variant_key(target: Any, hard_close: bool, breakeven: bool) -> str:
    t = target if isinstance(target, str) else f"1:{target:g}"
    return f"{t}{'' if hard_close else ' hold'}{' BE' if breakeven else ''}"


@dataclass(frozen=True)
class VWAPConfig:
    """The knobs the study varies. Everything optional is OFF here: this is the
    permissive core, and confluences are re-applied afterwards by filtering."""
    anchor: str = "et_0930"
    anchor_minutes: int = 15            # slope reference distance
    momentum_lookback_bars: int = 4     # in anchor units, as the engine does it
    momentum_threshold_pct: float = 0.1
    band_lookback: int = 0
    volume_lookback: int = 20
    volume_mult: float = 1.2
    pullback_max_distance_sigma: float = 1.0
    reversion_min_sigma: float = 2.0
    reversion_max_slope_atr_pct: float = 0.10
    min_wick_pct: float = 0.50
    session_exclude_end: str = "10:30"
    entry_cutoff: str = "15:30"
    hard_close: str = "15:55"
    stop_method: str = "structural"      # structural | atr | sigma1
    stop_atr_mult: float = 3.0
    min_stop_atr: float = 0.0            # research floor, in ATR units (0 = off)
    min_sl_spread_mult: float = 4.0
    """Stop floor as a multiple of the bar's own spread — the engine's
    `_apply_sl_floor` (min_sl_spread_mult, default 4x). Without it a structural
    stop taken when sigma is tiny (the first bars after the anchor) or when entry
    sits almost exactly on the +/-3sigma band gives a near-zero denominator, and R
    explodes: measured -6,566R and +30R on SINGLE GBPCHF trades before this floor
    existed. 0 = off (not recommended)."""

    min_stop_atr_discard: float = 0.02
    """Below this fraction of ATR a "stop" is a rounding artefact, not a risk
    level — the candidate is dropped rather than floored, because flooring it
    would invent a trade the geometry never offered. 0 = keep everything."""
    setups: tuple[str, ...] = ("pullback", "reversion")
    min_bars_between_candidates: int = 12
    """Minimum spacing between candidates, per session per setup (12 bars = 1h on M5).

    "Price is above or below VWAP" is true on almost every bar, so with every
    optional confluence disabled a 2.5-year M5 history yields ~250,000 candidates
    per market — hours of exit walks for information no configuration can use
    (the live strategy takes at most 4 trades a day, or exactly one under
    `first_pullback_only`).

    The first attempt bounded this by keeping the FIRST 12 candidates per session,
    and that was wrong in a way worth recording: the session anchor is 09:30 ET
    and the tradeable window opens at 10:30, so the quota filled up before the
    strategy was allowed to trade and the study measured
    "in_session_window: admits 0 of 7,620". Spacing bounds the work the same way
    without touching WHEN candidates occur, so the session window — itself one of
    the confluences under test — stays measurable."""

    max_candidates_per_session: int = 0
    """Optional hard cap per session per setup, applied after spacing. 0 = off."""


@dataclass
class Candidate:
    symbol: str
    setup: str
    session: int
    seq: int                 # position of this candidate within its session
    direction: int
    i_entry: int
    t_entry: int
    entry: float
    stop_dist: float
    hard_close_ts: int
    sigma2_target: float
    vwap_target: float
    features: dict[str, bool]
    detail: dict[str, float]
    outcomes: dict[str, tuple] = field(default_factory=dict)


# ── the strategy's own maths, vectorised ─────────────────────────────────────

def session_ids(times: np.ndarray, anchor: str) -> np.ndarray:
    """Session day per bar. ET anchors use New York's offset at each bar, so DST
    is handled the way the engine handles it."""
    t = np.asarray(times, dtype="int64")
    shift = _ANCHOR_SECONDS[anchor]
    if anchor == "london_0800":
        offs = np.array([int(pytz.timezone("Europe/London").utcoffset(
            datetime.fromtimestamp(int(x), tz=timezone.utc).replace(tzinfo=None)).total_seconds())
            for x in t], dtype="int64")
    else:
        offs = np.array([int(datetime.fromtimestamp(int(x), tz=ET).utcoffset().total_seconds())
                         for x in t], dtype="int64")
    return (t + offs - shift) // 86400


def anchored_vwap(b: Bars, sess: np.ndarray, band_lookback: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Session-anchored VWAP and its volume-weighted running σ — the engine's
    `_calculate_anchored_vwap_with_bands`, same reset-cumsum formulation."""
    tp = (b.high + b.low + b.close) / 3.0
    # Volume substitution matches the engine exactly: ones only when the feed
    # carries NO volume at all. Replacing individual zero-volume bars (holidays,
    # illiquid opens) would weight them differently from the live calculation.
    v = np.asarray(b.volume, dtype=float) if b.volume is not None else np.ones(len(b))
    if not (v.sum() > 0):
        v = np.ones(len(b), dtype=float)
    starts = np.flatnonzero(np.concatenate(([True], sess[1:] != sess[:-1])))
    lengths = np.diff(np.concatenate((starts, [len(sess)])))
    gid = np.repeat(np.arange(len(starts)), lengths)

    def reset_cumsum(a: np.ndarray) -> np.ndarray:
        c = np.cumsum(a)
        base = np.zeros(len(starts), dtype=float)
        if len(starts) > 1:
            base[1:] = c[starts[1:] - 1]
        return c - base[gid]

    cum_v = reset_cumsum(v)
    safe_v = np.where(cum_v > 0, cum_v, np.nan)
    vwap = reset_cumsum(tp * v) / safe_v
    sq = v * (tp - vwap) ** 2
    if band_lookback and band_lookback > 0:
        # rolling within the session
        cs = np.zeros(len(sq))
        cv = np.zeros(len(sq))
        for s, n in zip(starts, lengths):
            seg_sq, seg_v = np.nan_to_num(sq[s:s + n]), v[s:s + n]
            for k in range(n):
                lo = max(0, k - band_lookback + 1)
                cs[s + k] = seg_sq[lo:k + 1].sum()
                cv[s + k] = seg_v[lo:k + 1].sum()
        var = np.where(cv > 0, cs / cv, 0.0)
    else:
        var = np.maximum(reset_cumsum(np.nan_to_num(sq)) / safe_v, 0.0)
    std = np.nan_to_num(np.sqrt(np.nan_to_num(var)))
    # forward-fill the VWAP exactly as _ffill_bfill does
    valid = ~np.isnan(vwap)
    if not valid.all() and valid.any():
        idx = np.where(valid, np.arange(vwap.size), 0)
        np.maximum.accumulate(idx, out=idx)
        vwap = vwap[idx]
        first = int(np.argmax(valid))
        if first > 0:
            vwap[:first] = vwap[first]
    return vwap, std


def _et_hhmm(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone(ET).strftime("%H:%M")


def _hard_close_ts(ts: int, hard_close: str) -> int:
    d = datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone(ET)
    h, m = (int(x) for x in hard_close.split(":"))
    return int(ET.localize(datetime(d.year, d.month, d.day, h, m)).timestamp())


# ── candidate generation (permissive: nothing is filtered out) ────────────────

def build_candidates(b: Bars, cfg: VWAPConfig = VWAPConfig()) -> list[Candidate]:
    n = len(b)
    tf_sec = int(b.time[1] - b.time[0]) if n > 1 else 300
    bar_mult = max(1, (cfg.anchor_minutes * 60) // tf_sec)
    look = cfg.momentum_lookback_bars * bar_mult
    sess = session_ids(b.time, cfg.anchor)
    vwap, std = anchored_vwap(b, sess, cfg.band_lookback)
    a = atr(b, 14)
    vol = np.asarray(b.volume if b.volume is not None else np.ones(n), dtype=float)
    vol_mean = np.full(n, np.nan)
    if cfg.volume_lookback > 0:
        c = np.cumsum(np.insert(vol, 0, 0.0))
        for i in range(cfg.volume_lookback, n):
            vol_mean[i] = (c[i] - c[i - cfg.volume_lookback]) / cfg.volume_lookback

    out: list[Candidate] = []
    seq_by_setup: dict[tuple[int, str], int] = {}
    last_i_by_setup: dict[tuple[int, str], int] = {}
    start = max(look + 2, cfg.volume_lookback + 1, 20)
    for i in range(start, n - 1):
        if not (np.isfinite(vwap[i]) and np.isfinite(a[i]) and a[i] > 0):
            continue
        close, prev_close = float(b.close[i]), float(b.close[i - 1])
        v_now, v_prev = float(vwap[i]), float(vwap[i - bar_mult]) if i >= bar_mult else float(vwap[i])
        s_now = float(std[i])
        slope = v_now - v_prev
        dist_now, dist_prev = abs(close - v_now), abs(prev_close - v_prev)
        base = float(b.close[i - look])
        mom_pct = (close - base) / base * 100.0 if base else 0.0
        ratio = (float(vol[i]) / vol_mean[i]) if np.isfinite(vol_mean[i]) and vol_mean[i] > 0 else None
        hhmm = _et_hhmm(int(b.time[i]))
        in_window = cfg.session_exclude_end < hhmm < cfg.entry_cutoff
        vol_ok = (cfg.volume_mult <= 0) or (ratio is not None and ratio >= cfg.volume_mult)
        rng = float(b.high[i] - b.low[i])

        for setup in cfg.setups:
            if setup == "pullback":
                side = 1 if close > v_now else (-1 if close < v_now else 0)
                if side == 0:
                    continue
                feats = {
                    "slope_aligned": (slope > 0) if side > 0 else (slope < 0),
                    "momentum_aligned": (mom_pct >= cfg.momentum_threshold_pct) if side > 0
                    else (mom_pct <= -cfg.momentum_threshold_pct),
                    "inside_1sigma": s_now <= 0 or dist_now <= cfg.pullback_max_distance_sigma * s_now,
                    "converging": dist_now < dist_prev,
                    "volume_ok": vol_ok,
                    "in_session_window": in_window,
                    # Only the confluences this setup actually has. A reversion-only
                    # flag set True on a pullback candidate would block nothing and
                    # make its ablation row read "no effect" on a pooled population.
                }
                extreme = float(b.low[i]) if side > 0 else float(b.high[i])
                band1 = v_now - s_now if side > 0 else v_now + s_now
                if cfg.stop_method == "atr":
                    stop_dist = cfg.stop_atr_mult * float(a[i])
                elif cfg.stop_method == "sigma1":
                    stop_dist = abs(float(b.open[i + 1]) - band1)
                else:
                    ref = min(extreme, band1) if side > 0 else max(extreme, band1)
                    stop_dist = abs(float(b.open[i + 1]) - ref)
                sigma2 = v_now + 2.0 * s_now if side > 0 else v_now - 2.0 * s_now
            else:
                if s_now <= 0 or rng <= _MIN_WICK_DENOM:
                    continue
                upper, lower = v_now + cfg.reversion_min_sigma * s_now, v_now - cfg.reversion_min_sigma * s_now
                side = -1 if close >= upper else (1 if close <= lower else 0)
                if side == 0:
                    continue
                wick = (float(b.high[i]) - max(float(b.open[i]), close)) if side < 0 else \
                       (min(float(b.open[i]), close) - float(b.low[i]))
                feats = {
                    "beyond_2sigma": True,
                    "slope_flat": abs(slope) <= cfg.reversion_max_slope_atr_pct * float(a[i]),
                    "trend_neutral": (mom_pct <= cfg.momentum_threshold_pct) if side < 0
                    else (mom_pct >= -cfg.momentum_threshold_pct),
                    "wick_rejection": (wick / rng) >= cfg.min_wick_pct,
                    "volume_ok": vol_ok,
                    "in_session_window": in_window,
                }
                band3 = v_now + 3.0 * s_now if side < 0 else v_now - 3.0 * s_now
                stop_dist = (cfg.stop_atr_mult * float(a[i])) if cfg.stop_method == "atr" \
                    else abs(float(b.open[i + 1]) - band3)
                sigma2 = v_now
            # Engine-faithful floors first, then drop what is still degenerate.
            if cfg.min_sl_spread_mult > 0:
                stop_dist = max(stop_dist, cfg.min_sl_spread_mult * float(b.spread[i + 1]))
            if cfg.min_stop_atr > 0:
                stop_dist = max(stop_dist, cfg.min_stop_atr * float(a[i]))
            if not (stop_dist > 0 and math.isfinite(stop_dist)):
                continue
            if cfg.min_stop_atr_discard > 0 and stop_dist < cfg.min_stop_atr_discard * float(a[i]):
                continue
            key = (int(sess[i]), setup)
            if cfg.min_bars_between_candidates and (i - last_i_by_setup.get(key, -10**9)) < cfg.min_bars_between_candidates:
                continue
            if cfg.max_candidates_per_session and seq_by_setup.get(key, 0) >= cfg.max_candidates_per_session:
                continue
            last_i_by_setup[key] = i
            seq_by_setup[key] = seq_by_setup.get(key, 0) + 1
            c = Candidate(
                symbol=b.symbol, setup=setup, session=int(sess[i]), seq=seq_by_setup[key],
                direction=side, i_entry=i + 1, t_entry=int(b.time[i + 1]),
                entry=float(b.open[i + 1]), stop_dist=float(stop_dist),
                hard_close_ts=_hard_close_ts(int(b.time[i]), cfg.hard_close),
                sigma2_target=float(sigma2), vwap_target=float(v_now),
                features=feats,
                detail={"sigma": s_now, "atr": float(a[i]), "slope": slope, "momentum_pct": mom_pct,
                        "volume_ratio": float(ratio) if ratio is not None else float("nan"),
                        "distance_sigma": (dist_now / s_now) if s_now > 0 else float("nan")},
            )
            _resolve_all(b, c)
            out.append(c)
    return out


# ── outcome resolution ───────────────────────────────────────────────────────

class _Lists:
    def __init__(self, b: Bars):
        self.open, self.high, self.low = b.open.tolist(), b.high.tolist(), b.low.tolist()
        self.close, self.time, self.spread = b.close.tolist(), b.time.tolist(), b.spread.tolist()


_CACHE: dict[int, tuple[Bars, _Lists]] = {}


def _lists(b: Bars) -> _Lists:
    hit = _CACHE.get(id(b))
    if hit is None or hit[0] is not b:
        _CACHE.clear()
        hit = _CACHE[id(b)] = (b, _Lists(b))
    return hit[1]


def _resolve_all(b: Bars, c: Candidate) -> None:
    bl = _lists(b)
    tf = int(bl.time[1] - bl.time[0]) if len(bl.time) > 1 else 300
    for target, hard_close, be in EXIT_VARIANTS:
        c.outcomes[variant_key(target, hard_close, be)] = _resolve(bl, c, target, hard_close, be, tf)


def _resolve(bl: _Lists, c: Candidate, target: Any, hard_close: bool, be: bool, tf: int) -> tuple:
    n, d, e, entry, sd = len(bl.close), c.direction, c.i_entry, c.entry, c.stop_dist
    if target == "sigma2":
        tp = c.sigma2_target
    elif target == "vwap":
        tp = c.vwap_target
    else:
        tp = entry + d * float(target) * sd
    if (tp - entry) * d <= 0:          # a structural target already behind price
        return (0.0, int(bl.time[e]), "no_target")
    stop = entry - d * sd
    end = min(n - 1, e + MAX_HOLD_BARS)
    be_armed, j, exit_px, reason = False, e, None, ""
    while j <= end:
        o, h, lo, cl = bl.open[j], bl.high[j], bl.low[j], bl.close[j]
        if be_armed:
            stop = entry
        if j > e:
            if (d > 0 and o <= stop) or (d < 0 and o >= stop):
                exit_px, reason = o, "STOP_GAP"
                break
            if (d > 0 and o >= tp) or (d < 0 and o <= tp):
                exit_px, reason = o, "TARGET_GAP"
                break
        if (d > 0 and lo <= stop) or (d < 0 and h >= stop):
            exit_px, reason = stop, "STOP"
            break
        if (d > 0 and h >= tp) or (d < 0 and lo <= tp):
            exit_px, reason = tp, "TARGET"
            break
        if hard_close and bl.time[j] + tf >= c.hard_close_ts:
            exit_px, reason = cl, "HARD_CLOSE"
            break
        if be and ((d > 0 and h >= entry + sd) or (d < 0 and lo <= entry - sd)):
            be_armed = True
        j += 1
    if exit_px is None:
        j = end
        exit_px, reason = bl.close[j], "TIME"
    r = d * (exit_px - entry) / sd - bl.spread[e] / sd
    return (r, int(bl.time[j]), reason)


# ── evaluation: any confluence combination, by filtering ─────────────────────

def apply_combination(cands: Sequence[Candidate], *, use: Iterable[str] = (),
                      first_only: bool = False, max_per_session: int = 0,
                      setups: Iterable[str] | None = None) -> list[Candidate]:
    """The trades a configuration would have taken. `use` names the confluences
    that must hold; `first_only` / `max_per_session` are applied afterwards in
    time order, which is what makes them exact rather than approximate."""
    want = tuple(use)
    keep_setups = set(setups) if setups is not None else None
    picked, taken = [], {}
    for c in sorted(cands, key=lambda c: (c.t_entry, c.setup)):
        if keep_setups is not None and c.setup not in keep_setups:
            continue
        # A confluence the setup does not have cannot exclude it (`.get(f, True)`):
        # "wick rejection" is meaningless for a pullback, not a failed test.
        if any(not c.features.get(f, True) for f in want):
            continue
        key = (c.session, c.setup if first_only else "")
        count = taken.get(key, 0)
        cap = 1 if first_only else (max_per_session or 10_000)
        if count >= cap:
            continue
        taken[key] = count + 1
        picked.append(c)
    return picked


def stats(cands: Sequence[Candidate], key: str) -> dict[str, Any]:
    rs = [c.outcomes[key][0] for c in cands if took_trade(c, key)]
    if not rs:
        return {"n": 0, "avg_r": None, "total_r": 0.0, "win_rate": None, "pf": None, "t": 0.0}
    gw = sum(r for r in rs if r > 0)
    gl = -sum(r for r in rs if r < 0)
    sd = statistics.stdev(rs) if len(rs) > 2 else 0.0
    return {
        "n": len(rs),
        "avg_r": round(statistics.mean(rs), 4),
        "total_r": round(sum(rs), 2),
        "win_rate": round(sum(r > 0 for r in rs) / len(rs), 4),
        "pf": round(gw / gl, 3) if gl > 0 else None,
        "t": round(statistics.mean(rs) / (sd / math.sqrt(len(rs))), 2) if sd > 0 else 0.0,
    }


def ablate(cands: Sequence[Candidate], key: str, *, base: Iterable[str] = (),
           first_only: bool = False) -> list[dict[str, Any]]:
    """Marginal value of each confluence: the population with it ON versus the
    same population with it OFF, everything else held at `base`."""
    base = tuple(base)
    rows = []
    for f in CONFLUENCES:
        # Skip a confluence none of these candidates carries — an ablation row for
        # a gate that cannot apply is noise, and averaging it across setups is how
        # "blocks 0, edge +0.000R" rows appeared.
        if not any(f in c.features for c in cands):
            continue
        on = apply_combination(cands, use=tuple(dict.fromkeys(base + (f,))), first_only=first_only)
        off = apply_combination(cands, use=tuple(x for x in base if x != f), first_only=first_only)
        blocked = [c for c in off if f in c.features and not c.features[f]]
        s_on, s_off, s_blk = stats(on, key), stats(off, key), stats(blocked, key)
        rows.append({
            "confluence": f, "n_on": s_on["n"], "avg_r_on": s_on["avg_r"],
            "n_blocked": s_blk["n"], "avg_r_blocked": s_blk["avg_r"],
            "avg_r_without": s_off["avg_r"],
            "edge_added": None if (s_on["avg_r"] is None or s_off["avg_r"] is None)
            else round(s_on["avg_r"] - s_off["avg_r"], 4),
        })
    rows.sort(key=lambda r: -(r["edge_added"] or -9))
    return rows


def took_trade(c, key: str) -> bool:
    """Did this candidate actually produce a trade under `key`?

    A structural target can already sit BEHIND price when the candidate triggers
    (VWAP itself, for a pullback that is hugging value), and the resolver reports
    that as `(0.0, t_entry, "no_target")` rather than inventing a fill. Counting
    those as 0R trades is how markets came back showing ~2,075 "trades" at
    +-0.00xR with a meaningless profit factor — they were mostly non-trades.
    """
    o = c.outcomes.get(key)
    return o is not None and (len(o) < 3 or o[2] != "no_target")


def score(cands: Sequence[Candidate], key: str, min_n: int) -> float:
    s = stats(cands, key)
    if s["n"] < min_n or s["avg_r"] is None:
        return -math.inf
    return s["avg_r"] * math.sqrt(s["n"])


def in_window(cands: Iterable[Candidate], lo: int, hi: int) -> list[Candidate]:
    return [c for c in cands if lo <= c.t_entry < hi]
