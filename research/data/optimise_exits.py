"""Best stop distance and best R:R, measured from the market itself.

Independent of any strategy and of the saved backtest. At every M15 bar we ask:
entering here, with a stop k x ATR away, how far does price go in our favour
before that stop is hit? Then we price every take-profit level against that
distribution, charging the real spread.

This answers three of the user's questions directly:
  - what stop distance is optimal
  - what R:R to target
  - whether partials pay

Random entry has no directional edge by construction, so the expectancy here is
essentially "the cost of trading this instrument at this stop distance". That is
exactly the quantity to minimise before any confluence is layered on top: a
confluence adds edge, but it cannot claw back a structurally bad stop.
"""
from __future__ import annotations
import sys
import numpy as np
from confluence_atoms import load, atr

TF = "M15"
HORIZON = 96                 # 96 x M15 = 24 hours to work
STOPS = [0.5, 0.75, 1.0, 1.5, 2.0, 3.0]
TPS = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]

SYMBOLS = [
    "BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD",
    "US Tech 100", "US SP 500", "Germany 40", "Netherlands 25", "Hong Kong 50",
    "EURUSD", "GBPUSD", "USDJPY", "GBPJPY", "EURGBP", "USDCHF", "AUDUSD",
    "XAUUSD", "XAGUSD", "XPTUSD",
    "Crash 300 Index", "Crash 500 Index", "Crash 1000 Index",
    "Jump 100 Index", "Volatility 75 Index",
]


def excursion(h, l, c, risk, direction, horizon):
    """Stop-aware max favourable excursion in R, for an entry at every close."""
    n = len(c)
    buy = direction == "BUY"
    stop = c - risk if buy else c + risk
    max_r = np.zeros(n)
    alive = np.isfinite(risk) & (risk > 0)
    valid = alive.copy()
    for step in range(1, horizon + 1):
        idx = np.arange(n) + step
        ok = idx < n
        ic = np.clip(idx, 0, n - 1)
        fh, fl = h[ic], l[ic]
        if buy:
            stopped = alive & ok & (fl <= stop)
            fav = (fh - c) / np.where(risk > 0, risk, np.nan)
        else:
            stopped = alive & ok & (fh >= stop)
            fav = (c - fl) / np.where(risk > 0, risk, np.nan)
        live = alive & ok & ~stopped
        max_r = np.where(live & (fav > max_r), fav, max_r)
        alive &= ~stopped & ok
    return np.maximum(max_r, 0.0), valid


def analyse(sym):
    b = load(sym, TF)
    if b is None:
        return None
    h, l, c = b["high"], b["low"], b["close"]
    a = atr(h, l, c)
    spread_price = float(b["spread"]) * float(b["point"])
    rows = []
    for k in STOPS:
        risk = k * a
        med_risk = np.nanmedian(risk[np.isfinite(risk) & (risk > 0)])
        cost = spread_price / med_risk if med_risk else np.nan   # round trip in R
        mr_b, v = excursion(h, l, c, risk, "BUY", HORIZON)
        mr_s, _ = excursion(h, l, c, risk, "SELL", HORIZON)
        series = {"BUY": mr_b[v], "SELL": mr_s[v],
                  "BOTH": np.concatenate([mr_b[v], mr_s[v]])}
        for tp in TPS:
            for side, mr in series.items():
                win = mr >= tp
                exp = np.where(win, tp - cost, -1.0 - cost).mean()
                rows.append({"stop": k, "tp": tp, "cost_r": cost, "side": side,
                             "hit": win.mean(), "exp_r": exp, "n": len(mr)})
    return rows


if __name__ == "__main__":
    only = sys.argv[1:] or SYMBOLS
    print(f"{TF} bars, horizon {HORIZON} bars, both directions, "
          f"spread charged as a fraction of R\n")
    summary = []
    for sym in only:
        rows = analyse(sym)
        if not rows:
            print(f"{sym}: no cache"); continue
        costs = {r["stop"]: r["cost_r"] for r in rows}
        print(f"=== {sym}")
        print("  spread as a fraction of R, by stop distance:")
        print("   " + "  ".join(f"{k}xATR={costs[k]:.3f}R" for k in STOPS))
        for side in ("BOTH", "BUY", "SELL"):
            sub = [r for r in rows if r["side"] == side]
            best = max(sub, key=lambda r: r["exp_r"])
            print(f"  -- {side} (n={best['n']:,})   "
                  f"best: stop {best['stop']}xATR / TP {best['tp']}R "
                  f"-> {best['exp_r']:+.4f}R  P(hit)={best['hit']:.1%}")
            if side == "BOTH":
                print(f"  {'stop':>6s} " + "".join(f"{t:>8.1f}R" for t in TPS))
                for k in STOPS:
                    cells = [r for r in sub if r["stop"] == k]
                    print(f"  {k:6.2f} " + "".join(f"{r['exp_r']:+9.3f}" for r in cells))
            summary.append((sym, side, best))
        print()
    print("\n================ SUMMARY: optimal geometry per symbol ================")
    print(f"{'symbol':22s}{'side':>6s}{'stop':>7s}{'TP':>6s}{'P(hit)':>9s}{'cost R':>9s}{'exp R':>9s}")
    for sym, side, b in sorted(summary, key=lambda x: -x[2]["exp_r"]):
        if side != "BOTH":
            continue
        print(f"{sym:22s}{side:>6s}{b['stop']:7.2f}{b['tp']:6.1f}{b['hit']:9.1%}"
              f"{b['cost_r']:9.3f}{b['exp_r']:+9.4f}")
    print("\n-- directional detail (BUY vs SELL best) --")
    for sym, side, b in sorted(summary, key=lambda x: (x[0], x[1])):
        if side == "BOTH":
            continue
        print(f"{sym:22s}{side:>6s}{b['stop']:7.2f}{b['tp']:6.1f}{b['hit']:9.1%}{b['exp_r']:+9.4f}")
