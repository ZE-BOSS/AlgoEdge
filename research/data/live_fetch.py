"""Pull both brokers' history with timestamps normalised to UTC.

Every previous cross-broker number was computed on raw MT5 timestamps, which are
in each broker's own server timezone (Deriv UTC+0, FundedNext UTC+3). That
misalignment made the same metal look $22 apart. Here every bar time is shifted
to true UTC on the way in, so the two caches are directly comparable.

Spreads are read with the symbol selected into Market Watch, during market hours.
"""
from __future__ import annotations
import sys, time
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import MetaTrader5 as mt5
from dual_broker import BROKERS, env_file, connect

SERVER_OFFSET_H = {"deriv": 0, "fundednext": 3}

SYMBOLS = {
    "deriv": ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD",
              "US Tech 100", "US SP 500", "Germany 40", "Netherlands 25",
              "Hong Kong 50",
              "EURUSD", "GBPUSD", "USDJPY", "GBPJPY", "EURGBP", "USDCHF",
              "AUDUSD",
              "XAUUSD", "XAGUSD", "XPTUSD",
              "Crash 300 Index", "Crash 500 Index", "Crash 1000 Index",
              "Jump 100 Index", "Volatility 75 Index"],
    "fundednext": ["BTCUSD", "ETHUSD", "XRPUSD",
                   "NDX100", "SPX500", "GER30", "NTH25", "HK50",
                   "EURUSD", "GBPUSD", "USDJPY", "GBPJPY", "EURGBP", "USDCHF",
                   "AUDUSD",
                   "XAUUSD", "XAGUSD", "XPTUSD",
                   "USOUSD", "UKOUSD", "US30", "UK100", "JP225"],
}

TFS = [("M5", mt5.TIMEFRAME_M5), ("M15", mt5.TIMEFRAME_M15),
       ("H1", mt5.TIMEFRAME_H1), ("H4", mt5.TIMEFRAME_H4),
       ("D1", mt5.TIMEFRAME_D1)]
FROM = datetime(2026, 1, 1, tzinfo=timezone.utc)


def main():
    env = env_file()
    for name, cfg in BROKERS.items():
        cache = Path(__file__).parent / cfg["cache"]
        cache.mkdir(exist_ok=True)
        if not connect(name, cfg, env):
            print(f"{name}: connect FAILED {mt5.last_error()}", flush=True)
            continue
        ai = mt5.account_info()
        off = SERVER_OFFSET_H[name] * 3600
        print(f"\n=== {name}: {ai.login}@{ai.server} | shifting times by "
              f"{-off/3600:+.0f}h to UTC", flush=True)
        to = datetime.now(timezone.utc)
        for s in SYMBOLS[name]:
            if not mt5.symbol_select(s, True):
                print(f"  {s:22s} NOT AVAILABLE", flush=True)
                continue
            time.sleep(0.15)
            info = mt5.symbol_info(s)
            tick = mt5.symbol_info_tick(s)
            live_spread = (tick.ask - tick.bid) if tick and tick.ask else None
            for tfname, tf in TFS:
                f = cache / f"{s.replace(' ', '_')}__{tfname}.npz"
                r = mt5.copy_rates_range(s, tf, FROM, to)
                if r is None or len(r) == 0:
                    continue
                np.savez_compressed(
                    f,
                    time=r["time"].astype(np.int64) - off,   # -> true UTC
                    open=r["open"], high=r["high"], low=r["low"],
                    close=r["close"], volume=r["tick_volume"],
                    point=info.point, spread=info.spread,
                    spread_px=(live_spread if live_spread is not None
                               else info.spread * info.point),
                    digits=info.digits,
                    contract=getattr(info, "trade_contract_size", 1.0),
                    tick_value=getattr(info, "trade_tick_value", 0.0),
                    tick_size=getattr(info, "trade_tick_size", info.point),
                    volume_min=getattr(info, "volume_min", 0.01),
                    volume_step=getattr(info, "volume_step", 0.01),
                )
            print(f"  {s:22s} spread {info.spread:6d} pts "
                  f"= {live_spread if live_spread is not None else 0:.5f} px",
                  flush=True)
        mt5.shutdown()
    print("\nDONE", flush=True)


if __name__ == "__main__":
    sys.exit(main())
