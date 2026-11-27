"""
scripts/bbo_fetch.py - top of the order book once a minute (Databento bbo-1m, $0 on the plan) for the
research markets, saved as <name>_bbo.parquet: time (minute, epoch s), bid_px, ask_px, bid_sz, ask_sz,
bid_ct, ask_ct. trend_of.py turns it into a size imbalance.

    python bbo_fetch.py --data /opt/algoedge/research_data --start 2026-01-01 --end 2026-11-26
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import databento as db
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from of_fetch import MARKETS, key, retry  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="/opt/algoedge/research_data")
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--end", default="2026-11-26")
    ap.add_argument("--markets", default=",".join(MARKETS))
    a = ap.parse_args()
    c = db.Historical(key())
    for s in a.markets.split(","):
        name = MARKETS[s][0]
        cost = retry(c.metadata.get_cost, dataset="GLBX.MDP3", symbols=[s], schema="bbo-1m", stype_in="continuous",
                     start=a.start, end=a.end)
        if cost > 0:
            print(f"{name}: bbo-1m would cost ${cost:.2f}, skipped", flush=True)
            continue
        df = retry(c.timeseries.get_range, dataset="GLBX.MDP3", schema="bbo-1m", symbols=[s], stype_in="continuous",
                   start=a.start, end=a.end).to_df().reset_index()
        ts = "ts_recv" if "ts_recv" in df.columns else df.columns[0]
        out = pd.DataFrame({
            "time": (pd.to_datetime(df[ts], utc=True).dt.floor("1min").astype("int64") // 1_000_000_000).astype("int64"),
            "bid_px": df["bid_px_00"].astype(float), "ask_px": df["ask_px_00"].astype(float),
            "bid_sz": df["bid_sz_00"].astype(float), "ask_sz": df["ask_sz_00"].astype(float),
            "bid_ct": df.get("bid_ct_00", 0), "ask_ct": df.get("ask_ct_00", 0)})
        out = out.drop_duplicates("time", keep="last").sort_values("time")
        out.to_parquet(Path(a.data) / f"{name}_bbo.parquet")
        print(f"{name}: {len(out)} minutes {pd.to_datetime(out.time.min(), unit='s')} .. "
              f"{pd.to_datetime(out.time.max(), unit='s')}", flush=True)
    print("BBO_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
