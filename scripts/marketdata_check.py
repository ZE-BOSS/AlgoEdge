"""
scripts/marketdata_check.py - after a deploy: for every mapped broker symbol, what does the live
data path serve, and does it agree with the broker?

For each symbol: the source of the bars DataFetcher hands strategies (databento / MT5 fallback),
the newest closed bar's time, and that bar against the broker's own M5 bar of the same time
(close difference in points and as a share of the bar's range). Read-only: it only fetches.

    cd /opt/algoedge/<inst>/app && set -a && . ../instance.env && set +a && \
        PYTHONPATH=. ../venv/bin/python scripts/marketdata_check.py
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CANDS = ["XAUUSD", "XAGUSD", "US Tech 100", "US SP 500", "Wall Street 30", "US Oil", "Japan 225",
         "EURUSD", "GBPUSD", "AUDUSD", "USDJPY", "USDCAD", "USDCHF"]


async def main() -> int:
    import backend  # noqa: F401  (installs the MT5 bridge on Linux)
    import MetaTrader5 as mt5

    from backend.marketdata import source, store
    from backend.marketdata.instruments import mapped
    from backend.mt5.data_fetcher import DataFetcher
    mt5.initialize()
    bad = 0
    for sym in CANDS:
        if mt5.symbol_info(sym) is None:
            print(f"{sym:15s} not on this account")
            continue
        inst = mapped(sym)
        lag = store.live_lag(inst.future) if inst else None
        db = await source.bars(sym, "M5", count=300)
        mt = await DataFetcher.get_historical_data(sym, "M5", 300, _mt5_only=True)
        if db is None:
            print(f"{sym:15s} MT5 fallback (newest streamed minute "
                  f"{'none' if lag is None else f'{lag:.0f}s ago'})")
            continue
        last = db.iloc[-2]                                   # the newest CLOSED bar
        age = time.time() - (int(last["time"]) + 300)
        row = mt[mt["time"] == int(last["time"])] if mt is not None and "time" in mt.columns else None
        if row is None or not len(row):
            print(f"{sym:15s} databento {inst.future} newest closed bar {age:.0f}s old; no MT5 bar to compare")
            continue
        r = row.iloc[0]
        diff = float(last["close"]) - float(r["close"])
        rng = max(float(r["high"]) - float(r["low"]), 1e-12)
        flag = "OK" if abs(diff) <= max(0.5 * rng, 1e-9) and age < 900 else "CHECK"
        bad += flag != "OK"
        print(f"{sym:15s} databento {inst.future:7s} basis {db.attrs.get('basis'):+.5g} newest closed bar {age:4.0f}s old "
              f"| close {float(last['close']):.5g} vs MT5 {float(r['close']):.5g} diff {diff:+.4g} "
              f"({abs(diff) / rng:.0%} of the bar) flow {last['delta'] if 'delta' in db.columns else None} {flag}")
    print(f"MARKETDATA_CHECK_DONE {bad} to check")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
