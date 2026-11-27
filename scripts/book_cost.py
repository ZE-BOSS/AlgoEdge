"""
scripts/book_cost.py - what the order-book schemas would cost on this Databento plan.

    DATABENTO_API_KEY=... python book_cost.py GC.v.0 NQ.v.0 --start 2026-01-01 --end 2026-11-26
"""
from __future__ import annotations

import argparse
import os

import databento as db


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("symbols", nargs="+")
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--end", default="2026-11-26")
    ap.add_argument("--schemas", default="bbo-1m,bbo-1s,tbbo,mbp-1,mbp-10,trades,ohlcv-1m")
    a = ap.parse_args()
    c = db.Historical(os.environ["DATABENTO_API_KEY"])
    for sym in a.symbols:
        for sc in a.schemas.split(","):
            for start in (a.start, "2026-11-01"):
                try:
                    cost = c.metadata.get_cost(dataset="GLBX.MDP3", symbols=[sym], stype_in="continuous",
                                               schema=sc, start=start, end=a.end)
                    size = c.metadata.get_billable_size(dataset="GLBX.MDP3", symbols=[sym], stype_in="continuous",
                                                        schema=sc, start=start, end=a.end)
                    print(f"{sym:8s} {sc:8s} {start}..{a.end}  ${cost:9.2f}  {size / 1e9:8.2f} GB", flush=True)
                except Exception as e:  # noqa: BLE001
                    print(f"{sym:8s} {sc:8s} {start}: {type(e).__name__}: {str(e)[:120]}", flush=True)
    print("BOOK_COST_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
