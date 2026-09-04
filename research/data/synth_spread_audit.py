"""Live spread vs historical spread - which one should the cost gate use?

Report 08 was burned by reading live spreads on a Saturday and treating them as
representative. Report 19 section 0.3 read them just after midnight and may have
repeated it: Boom 1000's live quote says 0.01002% of price while every H1 bar
since 2019 says 0.00100%.

`MqlRates.spread` is recorded per bar in points, so seven years of it is the
honest basis for a cost model. This prints both, plus the ratio, so any symbol
whose live read is unrepresentative is obvious.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import MetaTrader5 as mt5                                    # noqa: E402
from mt5_probe import connect                                # noqa: E402

SYMBOLS = [
    "Boom 1000 Index", "Boom 500 Index", "Boom 300 Index",
    "Crash 1000 Index", "Crash 500 Index", "Crash 300 Index",
    "Step Index", "Step Index 500",
    "Range Break 100 Index", "Range Break 200 Index",
    "Jump 25 Index", "Jump 100 Index",
    "Volatility 25 Index", "Volatility 75 Index", "Volatility 100 Index",
    "Volatility 75 (1s) Index",
]


def main() -> None:
    if not connect():
        raise SystemExit("no MT5")
    print(f"{'symbol':26s}{'live%':>9s}{'hist30d%':>10s}{'hist1y%':>9s}{'ratio':>7s}"
          f"{'M5 ATR%':>9s}{'cost@1ATR':>10s}{'@0.5ATR':>9s}  basis")
    rows = []
    for s in SYMBOLS:
        i = mt5.symbol_info(s)
        if i is None:
            print(f"{s:26s}  NOT FOUND"); continue
        mt5.symbol_select(s, True)
        t = mt5.symbol_info_tick(s)
        h1 = mt5.copy_rates_from_pos(s, mt5.TIMEFRAME_H1, 0, 99999)
        m5 = mt5.copy_rates_from_pos(s, mt5.TIMEFRAME_M5, 0, 5000)
        if h1 is None or m5 is None:
            print(f"{s:26s}  no bars"); continue

        px = float(m5["close"][-1])
        live = (t.ask - t.bid) / px * 100 if t and t.ask > t.bid else np.nan
        sp_h1 = h1["spread"].astype(float) * i.point / h1["close"].astype(float) * 100
        hist30 = float(np.median(sp_h1[-24 * 30:]))
        hist1y = float(np.median(sp_h1[-24 * 365:]))

        hi, lo, c = (m5["high"].astype(float), m5["low"].astype(float),
                     m5["close"].astype(float))
        pc = np.concatenate(([c[0]], c[:-1]))
        atr = float(np.maximum(hi - lo, np.maximum(np.abs(hi - pc),
                                                   np.abs(lo - pc)))[-14:].mean())
        atr_pct = atr / px * 100
        # cost in R, using the HISTORICAL spread as the honest basis
        cost1 = hist30 / atr_pct
        cost05 = hist30 / (0.5 * atr_pct)
        ratio = live / hist30 if hist30 else np.nan
        basis = "live inflated" if ratio > 2 else ("live low" if ratio < 0.5 else "agree")
        rows.append((s, cost1))
        print(f"{s:26s}{live:9.5f}{hist30:10.5f}{hist1y:9.5f}{ratio:7.1f}"
              f"{atr_pct:9.4f}{cost1:10.3f}{cost05:9.3f}  {basis}", flush=True)

    print("\ncost@1ATR = historical spread as a fraction of R with a 1 x M5-ATR stop.")
    print("report 08 rule: an atom is worth ~0.03 R; above ~0.10 R needs a very strong edge.")
    best = sorted(rows, key=lambda x: x[1])
    print("\ncheapest:", ", ".join(f"{b[0].replace(' Index','')} {b[1]:.3f}R" for b in best[:6]))
    print("dearest :", ", ".join(f"{b[0].replace(' Index','')} {b[1]:.3f}R" for b in best[-4:]))
    mt5.shutdown()


if __name__ == "__main__":
    main()
