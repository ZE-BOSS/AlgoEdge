#!/usr/bin/env python
"""
scripts/run_pdh_pdl_sweep.py

The one falsifiable fragment of FLOD/LLOD, tested with money attached.

WHY ONLY A FRAGMENT
-------------------
FLOD (First Low Of Day) and LLOD (Lowest Low Of Day) are not a strategy; they
are a CHOICE of which PD array to trade from, and the ICT material selects
between them on "Draw on Liquidity" and "minimal resistance" — judgements, not
predicates. A backtest of that measures whoever wrote the code.

What IS computable is the claim underneath it: price sweeps a prior day's
extreme to take liquidity, then reverses to a fair value gap. Both PDH/PDL and
FVGs are mechanical. So:

    sweep   = the bar's high exceeds the previous day's high (or low < PDL)
              and it CLOSES back inside the prior day's range
    entry   = next bar's open, against the sweep (the app fills next-bar-open)
    stop    = beyond the sweep's extreme, plus a buffer
    target  = the nearest opposing FVG, or a fixed R if none is in range

A CONTROL matters more than the result here. The same rule is run against
RANDOM days' extremes instead of the previous day's: if sweeping a meaningless
level pays the same, the "liquidity" story is decoration and what is really
being measured is mean reversion after an outside bar.

    py -3.12 scripts/run_pdh_pdl_sweep.py
    py -3.12 scripts/run_pdh_pdl_sweep.py --rr 2.0 --risk 1.0
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

DAY = 86_400


def bars(symbol: str, tf, count: int = 60_000):
    import MetaTrader5 as mt5
    mt5.symbol_select(symbol, True)
    r = mt5.copy_rates_from_pos(symbol, tf, 0, count)
    return r if r is not None and len(r) > 2_000 else None


def day_extremes(t: np.ndarray, h: np.ndarray, lo: np.ndarray):
    """Previous day's high/low aligned to every bar, without look-ahead."""
    d = t // DAY
    days = np.unique(d)
    hi_by, lo_by = {}, {}
    for day in days:
        m = d == day
        hi_by[day], lo_by[day] = h[m].max(), lo[m].min()
    pdh = np.array([hi_by.get(x - 1, np.nan) for x in d])
    pdl = np.array([lo_by.get(x - 1, np.nan) for x in d])
    return pdh, pdl


def simulate(r, pdh, pdl, *, rr: float, stop_buf: float, shuffle: np.random.Generator | None):
    """Every sweep-and-reject, resolved bar by bar. Returns R per trade."""
    t, o, h, lo, c = (r["time"].astype("int64"), r["open"].astype(float),
                      r["high"].astype(float), r["low"].astype(float), r["close"].astype(float))
    if shuffle is not None:                      # the control: meaningless levels
        idx = shuffle.permutation(len(pdh))
        pdh, pdl = pdh[idx], pdl[idx]

    rng = np.nanmean(h - lo)
    out = []
    i = 1
    while i < len(t) - 1:
        up = np.isfinite(pdh[i]) and h[i] > pdh[i] and c[i] < pdh[i]      # swept the high, closed back under
        dn = np.isfinite(pdl[i]) and lo[i] < pdl[i] and c[i] > pdl[i]     # swept the low, closed back over
        if not (up or dn):
            i += 1
            continue
        entry = o[i + 1]
        if up:
            stop = h[i] + stop_buf * rng
            risk = stop - entry
            target = entry - rr * risk
        else:
            stop = lo[i] - stop_buf * rng
            risk = entry - stop
            target = entry + rr * risk
        if risk <= 0:
            i += 1
            continue
        # resolve forward; a bar touching both is scored as the loss
        res = None
        for j in range(i + 1, min(i + 500, len(t))):
            if up:
                if h[j] >= stop: res = -1.0; break
                if lo[j] <= target: res = rr; break
            else:
                if lo[j] <= stop: res = -1.0; break
                if h[j] >= target: res = rr; break
        if res is not None:
            out.append(res)
            i = j + 1
        else:
            i += 1
    return np.array(out)


def report(name: str, rs: np.ndarray, risk_pct: float, balance: float) -> str:
    if len(rs) < 30:
        return f"{name:<22}{len(rs):>6}  too few"
    exp = rs.mean()
    t = exp / (rs.std(ddof=1) / np.sqrt(len(rs))) if rs.std() > 0 else 0.0
    wins = (rs > 0).sum()
    risk_usd = balance * risk_pct / 100.0
    pnl = exp * len(rs) * risk_usd
    return (f"{name:<22}{len(rs):>6}{wins/len(rs)*100:>6.0f}%{exp:>+9.3f}{t:>+7.2f}"
            f"{pnl:>12,.0f}{pnl/balance*100:>9.1f}%")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rr", type=float, default=2.0)
    ap.add_argument("--stop-buffer", type=float, default=0.25, help="in mean bar ranges")
    ap.add_argument("--risk", type=float, default=1.0, help="% of balance per trade")
    ap.add_argument("--balance", type=float, default=10_000.0)
    ap.add_argument("--markets", nargs="*", default=MARKETS)
    args = ap.parse_args()

    import MetaTrader5 as mt5
    if not mt5.initialize():
        print("MT5 not available"); return

    hdr = (f"{'market':<22}{'n':>6}{'win':>7}{'exp R':>9}{'t':>7}"
           f"{'P&L $':>12}{'return':>9}")
    print(f"\nPDH/PDL SWEEP AND REJECT -> {args.rr:g}R, flat {args.risk:g}% of "
          f"${args.balance:,.0f}\n")
    print("REAL previous-day levels")
    print(hdr); print("-" * len(hdr))
    real, ctrl = {}, {}
    for m in args.markets:
        r = bars(m, mt5.TIMEFRAME_M15)
        if r is None:
            print(f"{m:<22}  no data"); continue
        pdh, pdl = day_extremes(r["time"].astype("int64"), r["high"].astype(float),
                                r["low"].astype(float))
        rs = simulate(r, pdh, pdl, rr=args.rr, stop_buf=args.stop_buffer, shuffle=None)
        real[m] = rs
        print(report(m, rs, args.risk, args.balance))
        ctrl[m] = simulate(r, pdh, pdl, rr=args.rr, stop_buf=args.stop_buffer,
                           shuffle=np.random.default_rng(7))

    print("\nCONTROL — the same rule against SHUFFLED days' levels")
    print(hdr); print("-" * len(hdr))
    for m, rs in ctrl.items():
        print(report(m, rs, args.risk, args.balance))

    pooled_r = np.concatenate([v for v in real.values() if len(v)]) if real else np.array([])
    pooled_c = np.concatenate([v for v in ctrl.values() if len(v)]) if ctrl else np.array([])
    print()
    if len(pooled_r) and len(pooled_c):
        print(f"pooled real   : {len(pooled_r):,} trades, {pooled_r.mean():+.4f}R")
        print(f"pooled control: {len(pooled_c):,} trades, {pooled_c.mean():+.4f}R")
        print(f"difference    : {pooled_r.mean()-pooled_c.mean():+.4f}R")
        print("\nIf the difference is near zero, the 'liquidity sweep' story adds nothing")
        print("over mean reversion after an outside bar, and FLOD/LLOD has no edge to")
        print("build on. Pooling across correlated markets inflates t -- read the")
        print("per-market rows, not the pool.")
    mt5.shutdown()


if __name__ == "__main__":
    main()
