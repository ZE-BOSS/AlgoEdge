"""Measure each broker's MT5 server-time offset from UTC.

MT5 returns bar and tick timestamps in the BROKER'S server timezone, not UTC.
Deriv and FundedNext run different server clocks (observed 3h apart), so
intersecting raw timestamps compares bars from different moments — which is what
made XAUUSD look $22 apart between two brokers quoting the same metal.

The offset is measured by comparing the newest tick's stamp against real UTC.
Ticks arrive continuously while the market is open, so the newest tick is "now"
to within a second or two.
"""
from __future__ import annotations
from datetime import datetime, timezone
import numpy as np
import MetaTrader5 as mt5
from dual_broker import BROKERS, env_file, connect

PROBES = ("XAUUSD", "EURUSD", "BTCUSD")

if __name__ == "__main__":
    env = env_file()
    results = {}
    for name, cfg in BROKERS.items():
        if not connect(name, cfg, env):
            print(f"{name}: connect failed {mt5.last_error()}")
            continue
        utc_now = datetime.now(timezone.utc)
        offs = []
        for s in PROBES:
            if not mt5.symbol_select(s, True):
                continue
            t = mt5.symbol_info_tick(s)
            if not t or not t.time:
                continue
            srv = datetime.fromtimestamp(t.time, tz=timezone.utc)
            offs.append(round((srv - utc_now).total_seconds() / 3600))
        off = int(round(np.median(offs))) if offs else 0
        results[name] = off
        ai = mt5.account_info()
        print(f"{name:12s} {ai.server:22s} server offset = UTC{off:+d}  "
              f"(samples {offs})")
        mt5.shutdown()

    if len(results) == 2:
        d = results["deriv"] - results["fundednext"]
        print(f"\nDeriv is {d:+d}h relative to FundedNext.")
        print("Bars must be shifted to UTC before any cross-broker comparison.")
        print(f"\nWrite into analysis: SERVER_OFFSET_H = {results}")
