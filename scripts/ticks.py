"""Tick store CLI (run on the server, inside an instance's environment).

    cd /opt/algoedge/<inst>/app && set -a && . ../instance.env && set +a
    PYTHONPATH=<research checkout> ../venv/bin/python <research>/scripts/ticks.py probe XAUUSDm USTECm
    ... ticks.py fetch --days 60 XAUUSDm USTECm
    ... ticks.py info

Read-only towards the account: it only downloads price history.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.scalping import ticks as T  # noqa: E402


def connect():
    import backend  # noqa: F401 - installs the MT5 bridge on Linux
    import MetaTrader5 as mt5
    if not mt5.initialize():
        raise SystemExit(f"MT5 initialize failed: {mt5.last_error()}")
    ai = mt5.account_info()
    broker = (getattr(ai, "company", "") or getattr(ai, "server", "") or "mt5").split()[0]
    return mt5, broker


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["probe", "fetch", "info"])
    ap.add_argument("symbols", nargs="*")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--broker", default=None, help="store under this name (default: the account's company)")
    a = ap.parse_args()
    if a.cmd == "info":
        for b in sorted(p for p in T.STORE.glob("*") if p.is_dir()):
            for s in sorted(p for p in b.glob("*") if p.is_dir()):
                days = T.stored_days(b.name, s.name)
                tk = T.load(b.name, s.name, days[-1], days[-1]) if days else None
                print("INFO|" + json.dumps({"broker": b.name, "symbol": s.name, "days": len(days),
                                             "first": str(days[0]) if days else None,
                                             "last": str(days[-1]) if days else None,
                                             "ticks_last_day": len(tk) if tk else 0}))
        return
    mt5, broker = connect()
    broker = a.broker or broker
    for sym in a.symbols:
        mt5.symbol_select(sym, True)
        off = T.server_offset_s(mt5, sym)
        if a.cmd == "probe":
            r = T.probe(mt5, sym, off)
            si = mt5.symbol_info(sym)
            r.update({"broker": broker, "offset_h": off / 3600,
                      "digits": getattr(si, "digits", None), "point": getattr(si, "point", None),
                      "contract": getattr(si, "trade_contract_size", None),
                      "tick_value": getattr(si, "trade_tick_value", None),
                      "tick_size": getattr(si, "trade_tick_size", None),
                      "vol_min": getattr(si, "volume_min", None), "stops_level": getattr(si, "trade_stops_level", None)})
            print("PROBE|" + json.dumps(r))
            continue
        today = datetime.now(timezone.utc).date()
        n_days = n_ticks = 0
        for k in range(a.days, -1, -1):
            day = today - timedelta(days=k)
            p = T.day_path(broker, sym, day)
            if p.exists() and day < today:
                continue                       # already stored (today is always refreshed)
            got = T.fetch_day(mt5, sym, day, off)
            if got is None:
                continue
            T.save_day(broker, sym, day, *got)
            n_days += 1
            n_ticks += len(got[0])
        print("FETCH|" + json.dumps({"broker": broker, "symbol": sym, "days_saved": n_days, "ticks": n_ticks}))
    print("TICKS_DONE")


if __name__ == "__main__":
    main()
