"""One-time bulk pull of MT5 history to local .npz, so every later analysis is fast.

MT5 downloads history from the broker on first request per symbol/timeframe,
which is slow. This does it once and caches to research/data/cache/.
Re-running skips anything already cached.
"""
from datetime import datetime, timezone
from pathlib import Path
import sys, time
import numpy as np
import MetaTrader5 as mt5
from mt5_probe import connect

CACHE = Path(__file__).parent / "cache"
CACHE.mkdir(exist_ok=True)

SYMBOLS = [
    "BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD",
    "US Tech 100", "US SP 500", "Germany 40", "Netherlands 25", "Hong Kong 50",
    "EURUSD", "GBPUSD", "USDJPY", "GBPJPY", "EURGBP", "USDCHF", "AUDUSD",
    "XAUUSD", "XAGUSD", "XPTUSD",
    "Crash 300 Index", "Crash 500 Index", "Crash 1000 Index",
    "Jump 100 Index", "Volatility 75 Index",
]
TFS = [("M5", mt5.TIMEFRAME_M5), ("M15", mt5.TIMEFRAME_M15),
       ("H1", mt5.TIMEFRAME_H1), ("H4", mt5.TIMEFRAME_H4), ("D1", mt5.TIMEFRAME_D1)]
FROM = datetime(2026, 1, 1, tzinfo=timezone.utc)
TO = datetime.now(timezone.utc)


def slug(s):
    return s.replace(" ", "_")


def main():
    if not connect():
        print("no MT5", flush=True); return 1
    for s in SYMBOLS:
        info = mt5.symbol_info(s)
        if info is None:
            print(f"{s:22s} NOT FOUND", flush=True); continue
        if not info.visible:
            mt5.symbol_select(s, True)
            time.sleep(0.3)
        for name, tf in TFS:
            f = CACHE / f"{slug(s)}__{name}.npz"
            if f.exists():
                print(f"{s:22s} {name:4s} cached", flush=True); continue
            t0 = time.time()
            r = mt5.copy_rates_range(s, tf, FROM, TO)
            n = len(r) if r is not None else 0
            if n:
                np.savez_compressed(
                    f, time=r["time"], open=r["open"], high=r["high"],
                    low=r["low"], close=r["close"], volume=r["tick_volume"],
                    point=info.point, spread=info.spread, digits=info.digits,
                    contract=getattr(info, "trade_contract_size", 1.0))
            print(f"{s:22s} {name:4s} {n:7d} bars  {time.time()-t0:5.1f}s",
                  flush=True)
    mt5.shutdown()
    print("DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
