"""What can we pull DIRECTLY from MT5, independent of the saved backtest?

This decides the scope of every independent analysis that follows:
  - bars per timeframe, Jan 2026 -> now, for every backtested symbol
  - historical TICKS (this is what decides whether order flow is backtestable)
"""
from datetime import datetime, timezone
import numpy as np, MetaTrader5 as mt5
from mt5_probe import connect

SYMBOLS = [
    "BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD",
    "US Tech 100", "US SP 500", "Germany 40", "Netherlands 25", "Hong Kong 50",
    "EURUSD", "GBPUSD", "USDJPY", "GBPJPY", "EURGBP", "USDCHF", "AUDUSD",
    "XAUUSD", "XAGUSD", "XPTUSD",
    "Crash 300 Index", "Crash 500 Index", "Crash 1000 Index",
    "Jump 100 Index", "Volatility 75 Index",
]
FROM = datetime(2026, 1, 1, tzinfo=timezone.utc)
TO = datetime.now(timezone.utc)
TFS = [("M1", mt5.TIMEFRAME_M1), ("M5", mt5.TIMEFRAME_M5),
       ("M15", mt5.TIMEFRAME_M15), ("H1", mt5.TIMEFRAME_H1),
       ("H4", mt5.TIMEFRAME_H4), ("D1", mt5.TIMEFRAME_D1)]

if __name__ == "__main__":
    if not connect():
        raise SystemExit("no MT5")
    print(f"window {FROM:%Y-%m-%d} -> {TO:%Y-%m-%d}\n")
    print(f"{'symbol':22s}" + "".join(f"{n:>9s}" for n, _ in TFS) + f"{'ticks':>12s}")
    for s in SYMBOLS:
        info = mt5.symbol_info(s)
        if info is None:
            print(f"{s:22s}  NOT FOUND"); continue
        if not info.visible:
            mt5.symbol_select(s, True)
        row = f"{s:22s}"
        for _, tf in TFS:
            r = mt5.copy_rates_range(s, tf, FROM, TO)
            row += f"{(len(r) if r is not None else 0):9d}"
        # historical ticks: the question that decides order-flow backtesting
        t = mt5.copy_ticks_range(s, FROM, datetime(2026, 1, 3, tzinfo=timezone.utc),
                                 mt5.COPY_TICKS_ALL)
        row += f"{(len(t) if t is not None else 0):12d}"
        print(row)
    print("\nticks column = ticks available for 1-2 Jan 2026 (0 => no tick history)")
    mt5.shutdown()
