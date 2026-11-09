"""
scripts/trend_scalp.py

Trend scalper: follow a clear trend with several small, managed positions,
close them all when the move turns, open again when it continues.

  trend      higher timeframe (htf minutes) EMA 20 over EMA 50 and price above
             EMA 20 = up (mirror for down); only COMPLETED htf bars are used
  entry      on the 1-minute chart, a pullback in that trend ends: close
             crosses back over the fast EMA after the bar's low (high) touched
             the slow EMA within the last `pb` bars. Each new signal opens one
             more position, up to `max_pos` open at once (stacking).
  exits      each position: take profit tp x ATR, stop sl x ATR (ATR(14), 1m)
             reversal: the fast EMA crosses the slow EMA against the trade, or
             the trend flips -> close ALL of that side at the next bar's open
  sizing     each position risks `risk` % of the balance at entry (its stop
             distance), in 0.01-lot steps (0.01 lot of gold = $1 per $1)

Fills: signals on a bar's close, orders at the next bar's open; buys at the
ask (bid + the bar's spread) and sells at the bid; a bar that touches both a
position's stop and target counts as the stop.

    python scripts/trend_scalp.py BARS.npz [--start 1000] [--risk 1]
Rules are ranked on the first 60 % of the bars; the top ones are then shown on
the last 40 % they never saw - the only number that counts.
"""

from __future__ import annotations

import argparse
import itertools
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

PER_LOT = 100.0


def resample(b, minutes: int):
    """1m bars -> `minutes` bars (spread: the median of the 1m spreads)."""
    k = b["time"] // (minutes * 60)
    idx = np.flatnonzero(np.diff(k) != 0) + 1
    starts = np.concatenate([[0], idx])
    ends = np.concatenate([idx, [len(k)]])
    return {"time": k[starts] * minutes * 60, "open": b["open"][starts], "close": b["close"][ends - 1],
            "high": np.maximum.reduceat(b["high"], starts), "low": np.minimum.reduceat(b["low"], starts),
            "spread": np.array([np.median(b["spread"][a:z]) for a, z in zip(starts, ends)])}


def load(path: str):
    d = np.load(path)
    pt = float(np.atleast_1d(d["point"])[0]) if "point" in d else 0.01
    spr = d["spread"].astype(float) * pt if "spread" in d else np.full(len(d["time"]), 0.2)
    return {"time": d["time"].astype(np.int64), "open": d["open"].astype(float), "high": d["high"].astype(float),
            "low": d["low"].astype(float), "close": d["close"].astype(float), "spread": spr}


def ema(x: np.ndarray, n: int) -> np.ndarray:
    a = 2.0 / (n + 1)
    out = np.empty_like(x)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = out[i - 1] + a * (x[i] - out[i - 1])
    return out


def atr(b, n: int = 14) -> np.ndarray:
    h, l_, c = b["high"], b["low"], b["close"]
    pc = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l_, np.maximum(abs(h - pc), abs(l_ - pc)))
    return ema(tr, n)


def htf_trend(b, minutes: int) -> np.ndarray:
    """+1 / -1 / 0 for each 1m bar, from the last COMPLETED htf bar."""
    t = b["time"]
    bucket = t // (minutes * 60)
    idx_last = np.flatnonzero(np.diff(bucket) != 0)          # last 1m bar of each htf bar
    closes = b["close"][idx_last]
    e20, e50 = ema(closes, 20), ema(closes, 50)
    st = np.where((e20 > e50) & (closes > e20), 1, np.where((e20 < e50) & (closes < e20), -1, 0))
    out = np.zeros(len(t), dtype=np.int8)
    # a htf bar is known from the 1m bar AFTER its last one
    pos = np.searchsorted(idx_last, np.arange(len(t)), side="left") - 1
    ok = pos >= 0
    out[ok] = st[pos[ok]]
    return out


class Prep:
    def __init__(self, b, htf, fast, slow):
        self.b = b
        self.tr = htf_trend(b, htf)
        self.ef = ema(b["close"], fast)
        self.es = ema(b["close"], slow)
        self.atr = atr(b)
        hrs = (b["time"] // 3600) % 24
        self.hour = hrs


def simulate(P: Prep, lo, hi, tp, sl, max_pos, pb, session, risk, start=1000.0, slip=0.05):
    b = P.b
    o, h, l_, c, sp = b["open"], b["high"], b["low"], b["close"], b["spread"]
    ef, es, tr, at = P.ef, P.es, P.tr, P.atr
    bal = start
    peak, maxdd = start, 0.0
    pos = []            # [side, entry, stop, target, lots]
    trades = []         # P/L $ per closed position
    pending = None      # ("open", side) or ("close", side) decided at a bar close, done at the next open
    for i in range(max(lo, 60), hi - 1):
        # 1) orders decided at the previous close fill at this open
        if pending:
            kind, side = pending
            if kind == "close":
                for p in [p for p in pos if p[0] == side]:
                    px = o[i] if side == 1 else o[i] + sp[i]
                    trades.append((px - p[1]) * side * p[4] * PER_LOT)
                    bal += trades[-1]
                    pos.remove(p)
            elif kind == "open" and len(pos) < max_pos:
                e = o[i] + sp[i] + slip if side == 1 else o[i] - slip
                dist = sl * at[i - 1]
                lots = np.floor(bal * risk / 100.0 / (dist * PER_LOT) / 0.01 + 1e-9) * 0.01
                if lots >= 0.01 and dist > 0:
                    tgt = e + side * tp * at[i - 1] if tp < 50 else None
                    pos.append([side, e, e - side * dist, tgt, lots])
            pending = None
        # 2) stops and targets inside this bar (stop first when both)
        for p in list(pos):
            side, e, stp, tgt, lots = p
            bid_lo, bid_hi = l_[i], h[i]
            ask_lo, ask_hi = l_[i] + sp[i], h[i] + sp[i]
            hit_sl = (bid_lo <= stp) if side == 1 else (ask_hi >= stp)
            hit_tp = tgt is not None and ((bid_hi >= tgt) if side == 1 else (ask_lo <= tgt))
            if hit_sl or hit_tp:
                px = stp if hit_sl else tgt
                trades.append((px - e) * side * lots * PER_LOT)
                bal += trades[-1]
                pos.remove(p)
        eq = bal + sum((c[i] - p[1]) * p[0] * p[4] * PER_LOT if p[0] == 1 else (p[1] - c[i] - sp[i]) * p[4] * PER_LOT
                       for p in pos)
        peak = max(peak, eq)
        maxdd = max(maxdd, (peak - eq) / peak if peak > 0 else 0)
        if bal <= start * 0.05:
            break
        # 3) decisions at this close
        for side in (1, -1):
            if any(p[0] == side for p in pos):
                crossed = (ef[i] - es[i]) * side < 0 <= (ef[i - 1] - es[i - 1]) * side
                if crossed or tr[i] != side:
                    pending = ("close", side)
        if pending:
            continue
        s = int(tr[i])
        if s == 0 or (session and not (7 <= P.hour[i] < 20)):
            continue
        recovered = (c[i] - ef[i]) * s > 0 and (c[i - 1] - ef[i - 1]) * s <= 0
        lows = l_[i - pb + 1:i + 1] if s == 1 else h[i - pb + 1:i + 1]
        touched = np.any(lows <= es[i - pb + 1:i + 1]) if s == 1 else np.any(lows >= es[i - pb + 1:i + 1])
        if recovered and touched and (ef[i] - es[i]) * s > 0 and not any(p[0] == -s for p in pos):
            pending = ("open", s)
    # whatever is open at the end closes at the last close
    for p in pos:
        px = c[hi - 1] if p[0] == 1 else c[hi - 1] + sp[hi - 1]
        trades.append((px - p[1]) * p[0] * p[4] * PER_LOT)
        bal += trades[-1]
    return bal, maxdd, np.array(trades)


def fmt(start, bal, dd, tr):
    if len(tr) == 0:
        return "no trades"
    w = tr[tr > 0]
    pf = w.sum() / -tr[tr < 0].sum() if (tr < 0).any() else float("inf")
    return (f"{len(tr):5d} pos  win {100 * len(w) / len(tr):3.0f}%  PF {pf:4.2f}  "
            f"${start:,.0f} -> ${bal:,.0f} ({(bal / start - 1) * 100:+6.1f}%)  maxDD {dd * 100:4.1f}%  "
            f"avg win ${w.mean() if len(w) else 0:,.2f}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("bars")
    ap.add_argument("--start", type=float, default=1000.0)
    ap.add_argument("--risk", type=float, default=1.0, help="%% of balance per position")
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args()
    b = load(a.bars)
    N = len(b["time"])
    cut = int(N * 0.6)
    d = lambda i: datetime.fromtimestamp(int(b["time"][i]), timezone.utc).strftime("%Y-%m-%d")  # noqa: E731
    tf = int(np.median(np.diff(b["time"][:1000])) // 60)
    print(f"{N} {tf}m bars {d(0)} .. {d(N - 1)}; in-sample to {d(cut)}, out-of-sample after; "
          f"spread median ${np.median(b['spread']):.2f}; {a.risk:g}% risk per position, ${a.start:,.0f} start")
    grid = []
    htfs = (15,) if a.quick else (5, 15, 60)
    for htf, (f, s) in itertools.product(htfs, ((9, 21), (5, 13))):
        P = Prep(b, htf, f, s)
        for tp, sl, mp, pb, sess in itertools.product((0.5, 1.0, 2.0, 99), (1.0, 2.0, 3.0), (1, 3, 5), (5,),
                                                      (False, True)):
            grid.append((P, (htf, f, s, tp, sl, mp, pb, sess)))
    res = []
    for P, g in grid:
        htf, f, s, tp, sl, mp, pb, sess = g
        bal, dd, tr = simulate(P, 0, cut, tp, sl, mp, pb, sess, a.risk, a.start)
        res.append((bal, P, g, dd, tr))
    res.sort(key=lambda r: -r[0])
    pos_in = sum(1 for r in res if r[0] > a.start)
    print(f"{len(res)} rule sets; {pos_in} grew the account in-sample.\n")
    out_pos = 0
    for k, (bal, P, g, dd, tr) in enumerate(res):
        htf, f, s, tp, sl, mp, pb, sess = g
        b2, dd2, tr2 = simulate(P, cut, N, tp, sl, mp, pb, sess, a.risk, a.start)
        out_pos += b2 > a.start
        if k < a.top:
            name = (f"htf {htf}m ema {f}/{s} tp {'reversal' if tp > 50 else f'{tp}xATR'} sl {sl}xATR "
                    f"max {mp} {'London+NY' if sess else '24h'}")
            print(name)
            print(f"   IN  {fmt(a.start, bal, dd, tr)}")
            print(f"   OUT {fmt(a.start, b2, dd2, tr2)}")
    print(f"\nout-of-sample: {out_pos}/{len(res)} rule sets grew the account "
          f"(ALL rule sets, not just the best in-sample)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
