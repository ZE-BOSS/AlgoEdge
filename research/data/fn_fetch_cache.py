"""Bulk-pull FundedNext history into research/data/cache_fn/.

Mirrors fetch_cache.py (which pulled Deriv) so the two brokers can be compared
on identical code paths. Symbol names differ between brokers; the mapping is
declared here and reused by every FundedNext analysis.
"""
import os, sys, time
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import MetaTrader5 as mt5
from fn_probe import connect

CACHE = Path(__file__).parent / "cache_fn"
CACHE.mkdir(exist_ok=True)

# Deriv name -> FundedNext name. None = not offered by FundedNext.
MAP = {
    "BTCUSD": "BTCUSD", "ETHUSD": "ETHUSD", "XRPUSD": "XRPUSD",
    "SOLUSD": None,
    "US Tech 100": "NDX100", "US SP 500": "SPX500", "Germany 40": "GER30",
    "Netherlands 25": "NTH25", "Hong Kong 50": "HK50",
    "EURUSD": "EURUSD", "GBPUSD": "GBPUSD", "USDJPY": "USDJPY",
    "GBPJPY": "GBPJPY", "EURGBP": "EURGBP", "USDCHF": "USDCHF",
    "AUDUSD": "AUDUSD",
    "XAUUSD": "XAUUSD", "XAGUSD": "XAGUSD", "XPTUSD": "XPTUSD",
    # synthetics — FundedNext trades real markets only
    "Crash 300 Index": None, "Crash 500 Index": None, "Crash 1000 Index": None,
    "Jump 100 Index": None, "Volatility 75 Index": None,
}
FN_SYMBOLS = [v for v in MAP.values() if v]

TFS = [("M5", mt5.TIMEFRAME_M5), ("M15", mt5.TIMEFRAME_M15),
       ("H1", mt5.TIMEFRAME_H1), ("H4", mt5.TIMEFRAME_H4),
       ("D1", mt5.TIMEFRAME_D1)]
FROM = datetime(2026, 1, 1, tzinfo=timezone.utc)
TO = datetime.now(timezone.utc)


def main():
    if not connect():
        print("connect failed", mt5.last_error(), flush=True)
        return 1
    print(f"pulling {len(FN_SYMBOLS)} symbols x {len(TFS)} timeframes "
          f"{FROM:%Y-%m-%d} -> {TO:%Y-%m-%d}\n", flush=True)
    for s in FN_SYMBOLS:
        info = mt5.symbol_info(s)
        if info is None:
            print(f"{s:10s} NOT FOUND", flush=True)
            continue
        if not info.visible:
            mt5.symbol_select(s, True)
            time.sleep(0.2)
            info = mt5.symbol_info(s)
        for name, tf in TFS:
            f = CACHE / f"{s}__{name}.npz"
            if f.exists():
                continue
            t0 = time.time()
            r = mt5.copy_rates_range(s, tf, FROM, TO)
            n = len(r) if r is not None else 0
            if n:
                np.savez_compressed(
                    f, time=r["time"], open=r["open"], high=r["high"],
                    low=r["low"], close=r["close"], volume=r["tick_volume"],
                    point=info.point, spread=info.spread, digits=info.digits,
                    contract=getattr(info, "trade_contract_size", 1.0))
            print(f"{s:10s} {name:4s} {n:7d} bars {time.time()-t0:5.1f}s",
                  flush=True)
    mt5.shutdown()
    print("DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
