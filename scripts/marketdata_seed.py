"""
scripts/marketdata_seed.py - fill the shared market-data store from the research downloads.

The order-flow research (of_fetch.py) already holds a year of 1-minute bars and
aggressor flow for nine CME markets. This writes them into the store as day
files, so backtests and strategies have the history without fetching it again.

    sudo -u algoedge /opt/algoedge/live/venv/bin/python scripts/marketdata_seed.py \
        --data /opt/algoedge/research_data
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.marketdata import store  # noqa: E402

NAMES = {"gold": "GC.v.0", "silver": "SI.v.0", "nasdaq": "NQ.v.0", "sp500": "ES.v.0", "us30": "YM.v.0",
         "oil": "CL.v.0", "eurusd": "6E.v.0", "gbpusd": "6B.v.0", "usdjpy": "6J.v.0"}


def load(data: Path, stem: str) -> pd.DataFrame | None:
    for ext in (".parquet", ".csv.gz"):
        p = data / f"{stem}{ext}"
        if p.exists():
            return pd.read_parquet(p) if ext == ".parquet" else pd.read_csv(p)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="/opt/algoedge/research_data")
    ap.add_argument("--until", default="", help="last day to write (default: the day before the data ends)")
    a = ap.parse_args()
    data = Path(a.data)
    for name, fut in NAMES.items():
        m1 = load(data, f"{name}_m1")
        if m1 is None:
            print(f"{name}: no bars, skipped")
            continue
        m1 = m1.drop_duplicates("time").set_index(pd.to_datetime(m1.drop_duplicates("time")["time"], unit="s"))
        df = m1[["open", "high", "low", "close"]].astype(float)
        df["volume"] = m1["tick_volume"].astype(float) if "tick_volume" in m1.columns else m1.get("volume")
        of = load(data, f"{name}_of")
        if of is not None:
            of = of.drop_duplicates("time")
            of.index = pd.to_datetime(of["time"], unit="s")
            df = df.join(of[["buy", "sell", "big_buy", "big_sell", "pv"]], how="left")
            # a day with flow rows: a minute without one had no trades -> 0; a day without
            # any (before the flow download began, or a failed day) stays NaN = "no flow data"
            flow_days = set(of.index.normalize())
            on = df.index.normalize().isin(list(flow_days))
            cols = ["buy", "sell", "big_buy", "big_sell", "pv"]
            df.loc[on, cols] = df.loc[on, cols].fillna(0.0)
        df = store.conform(df)
        last = pd.Timestamp(a.until) if a.until else df.index.max().normalize() - pd.Timedelta(days=1)
        n = 0
        for day, part in df.groupby(df.index.normalize()):
            if day > last:
                continue
            store.write_day(fut, day.date(), part)
            n += 1
        print(f"{name}: {n} days -> {store.root() / fut}", flush=True)
    print("SEED_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
