#!/usr/bin/env python
"""
scripts/run_icc_study.py

ICC — the Institutional Cash Cycle — made mechanical and tested with money.

THE RULE, AS SPECIFIED
----------------------
  1. INDICATION  a move breaks a major swing high/low on the higher timeframe,
                 setting directional bias.
  2. CORRECTION  a pullback against that bias, gathering liquidity.
  3. CONTINUATION after the pullback SWEEPS liquidity (a wick through a recent
                 low in a bull bias) and then shows a CHoCH -- a break of the
                 correction's internal structure -- price resumes.

Every term above has a mechanical reading, which is why ICC is testable where
FLOD/LLOD is not. The one discretionary word is "major", which becomes the
swing lookback parameter and is swept.

THE CONTROL
-----------
The same entry with the HTF bias RANDOMISED. If a coin-flip bias pays the same,
the "institutional cycle" is decoration and what is being measured is a sweep
and reversal -- which is already what run_pdh_pdl_sweep.py tests.

    py -3.12 scripts/run_icc_study.py --balance 10000 --risk 1.0
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MARKETS = ["XAUUSD", "EURUSD", "GBPJPY", "US Tech 100", "BTCUSD",
           "Crash 1000 Index", "Boom 1000 Index", "Step Index",
           "Volatility 75 Index", "Vol over Crash 750"]


def swings(h: np.ndarray, lo: np.ndarray, k: int):
    """Fractal swing highs/lows: an extreme with k bars lower on both sides."""
    n = len(h)
    sh = np.zeros(n, bool)
    sl = np.zeros(n, bool)
    for i in range(k, n - k):
        if h[i] == h[i - k:i + k + 1].max(): sh[i] = True
        if lo[i] == lo[i - k:i + k + 1].min(): sl[i] = True
    return sh, sl


def htf_bias(c: np.ndarray, h: np.ndarray, lo: np.ndarray, k: int) -> np.ndarray:
    """+1 after a close above the last confirmed swing high, -1 below the low.

    Confirmed means the swing is only known k bars AFTER it formed, which is
    how it would be known live. Without that delay this reads the future.
    """
    n = len(c)
    sh, sl = swings(h, lo, k)
    bias = np.zeros(n, np.int8)
    last_h = last_l = np.nan
    cur = 0
    for i in range(n):
        j = i - k                       # only swings confirmed by now
        if j >= 0:
            if sh[j]: last_h = h[j]
            if sl[j]: last_l = lo[j]
        if np.isfinite(last_h) and c[i] > last_h: cur = 1
        elif np.isfinite(last_l) and c[i] < last_l: cur = -1
        bias[i] = cur
    return bias


def simulate(r, bias: np.ndarray, *, k: int, rr: float, buf: float):
    """Sweep + CHoCH in the bias direction. Returns R per trade."""
    o, h, lo, c = (r["open"].astype(float), r["high"].astype(float),
                   r["low"].astype(float), r["close"].astype(float))
    n = len(c)
    rng = float(np.mean(h - lo))
    sh, sl = swings(h, lo, k)
    out = []
    i = k + 1
    while i < n - 2:
        b = bias[i]
        if b == 0:
            i += 1; continue
        # the most recent confirmed opposite-side swing, for the sweep level
        lvl = np.nan
        for j in range(i - k, max(k, i - 200), -1):
            if b > 0 and sl[j]: lvl = lo[j]; break
            if b < 0 and sh[j]: lvl = h[j]; break
        if not np.isfinite(lvl):
            i += 1; continue
        swept = (lo[i] < lvl and c[i] > lvl) if b > 0 else (h[i] > lvl and c[i] < lvl)
        if not swept:
            i += 1; continue
        # CHoCH: within 10 bars, close back through the sweep bar's own extreme
        choch = None
        for j in range(i + 1, min(i + 11, n - 1)):
            if (b > 0 and c[j] > h[i]) or (b < 0 and c[j] < lo[i]):
                choch = j; break
        if choch is None:
            i += 1; continue

        entry = o[choch + 1]
        stop = (lo[i] - buf * rng) if b > 0 else (h[i] + buf * rng)
        risk = (entry - stop) if b > 0 else (stop - entry)
        if risk <= 0:
            i += 1; continue
        target = entry + rr * risk if b > 0 else entry - rr * risk
        res = None
        for j in range(choch + 1, min(choch + 500, n)):
            if b > 0:
                if lo[j] <= stop: res = -1.0; break
                if h[j] >= target: res = rr; break
            else:
                if h[j] >= stop: res = -1.0; break
                if lo[j] <= target: res = rr; break
        if res is not None:
            out.append(res); i = j + 1
        else:
            i += 1
    return np.array(out)


def row(name: str, rs: np.ndarray, risk_pct: float, balance: float) -> str:
    if len(rs) < 30:
        return f"{name:<22}{len(rs):>6}  too few"
    exp = rs.mean()
    t = exp / (rs.std(ddof=1) / np.sqrt(len(rs))) if rs.std() > 0 else 0.0
    pnl = exp * len(rs) * balance * risk_pct / 100.0
    return (f"{name:<22}{len(rs):>6}{(rs > 0).mean() * 100:>6.0f}%{exp:>+9.3f}"
            f"{t:>+7.2f}{pnl:>12,.0f}{pnl / balance * 100:>9.1f}%")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rr", type=float, default=2.0)
    ap.add_argument("--swing", type=int, default=5, help='"major" swing lookback')
    ap.add_argument("--stop-buffer", type=float, default=0.25)
    ap.add_argument("--risk", type=float, default=1.0)
    ap.add_argument("--balance", type=float, default=10_000.0)
    ap.add_argument("--markets", nargs="*", default=MARKETS)
    args = ap.parse_args()

    import MetaTrader5 as mt5
    if not mt5.initialize():
        print("MT5 not available"); return

    hdr = f"{'market':<22}{'n':>6}{'win':>7}{'exp R':>9}{'t':>7}{'P&L $':>12}{'return':>9}"
    print(f"\nICC — INDICATION / CORRECTION / CONTINUATION -> {args.rr:g}R, "
          f"flat {args.risk:g}% of ${args.balance:,.0f}, swing k={args.swing}\n")
    print("REAL higher-timeframe bias")
    print(hdr); print("-" * len(hdr))
    real, ctrl = {}, {}
    rng = np.random.default_rng(11)
    for m in args.markets:
        mt5.symbol_select(m, True)
        r = mt5.copy_rates_from_pos(m, mt5.TIMEFRAME_M15, 0, 60_000)
        if r is None or len(r) < 5_000:
            print(f"{m:<22}  no data"); continue
        c, h, lo = r["close"].astype(float), r["high"].astype(float), r["low"].astype(float)
        b = htf_bias(c, h, lo, args.swing * 4)        # the HTF leg
        real[m] = simulate(r, b, k=args.swing, rr=args.rr, buf=args.stop_buffer)
        print(row(m, real[m], args.risk, args.balance))
        # control: same machinery, a bias that carries no information
        fake = rng.choice([-1, 1], size=len(b)).astype(np.int8)
        ctrl[m] = simulate(r, fake, k=args.swing, rr=args.rr, buf=args.stop_buffer)

    print("\nCONTROL — identical entries, HTF bias replaced by a coin flip")
    print(hdr); print("-" * len(hdr))
    for m, rs in ctrl.items():
        print(row(m, rs, args.risk, args.balance))

    pr = np.concatenate([v for v in real.values() if len(v)]) if real else np.array([])
    pc = np.concatenate([v for v in ctrl.values() if len(v)]) if ctrl else np.array([])
    if len(pr) and len(pc):
        print(f"\npooled real   : {len(pr):,} trades, {pr.mean():+.4f}R")
        print(f"pooled control: {len(pc):,} trades, {pc.mean():+.4f}R")
        print(f"the bias is worth: {pr.mean() - pc.mean():+.4f}R per trade")
        print("\nNear zero means the Institutional Cash Cycle's higher-timeframe bias")
        print("adds nothing over a sweep-and-reverse taken in a random direction.")
        print("Correlated markets inflate the pooled t -- read the per-market rows.")
    mt5.shutdown()


if __name__ == "__main__":
    main()
