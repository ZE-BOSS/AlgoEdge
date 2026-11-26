"""
scripts/of_fetch.py - CME futures history from Databento for the strategy research.

For each market: 1-minute OHLCV bars (the strategies' candles) and, from every
trade, 1-minute order flow - buy volume and sell volume by aggressor side,
delta, trade count, large-trade buy/sell volume, and the minute's VWAP.

    python scripts/of_fetch.py --cost                      # what it would cost; fetches nothing
    python scripts/of_fetch.py --start 2026-01-01 --end 2026-11-26 --out /opt/algoedge/research_data

Refuses to fetch when Databento quotes more than --max-cost dollars (the
Standard plan covers 1 year of L1 history, so the quote should be ~0).
Reads DATABENTO_API_KEY from the environment or live's .env; never prints it.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

MARKETS = {  # continuous symbol -> (name, large-trade threshold in contracts)
    "GC.v.0": ("gold", 20), "SI.v.0": ("silver", 10), "NQ.v.0": ("nasdaq", 15), "ES.v.0": ("sp500", 50),
}
DATASET = "GLBX.MDP3"


def key() -> str:
    k = os.getenv("DATABENTO_API_KEY", "").strip()
    if not k:
        try:
            from dotenv import dotenv_values
            k = (dotenv_values("/opt/algoedge/live/app/.env").get("DATABENTO_API_KEY") or "").strip()
        except Exception:  # noqa: BLE001
            pass
    if not k:
        sys.exit("no DATABENTO_API_KEY")
    return k


def save(df: pd.DataFrame, path: Path) -> None:
    try:
        df.to_parquet(path.with_suffix(".parquet"))
    except Exception:  # noqa: BLE001 - no pyarrow
        df.to_csv(path.with_suffix(".csv.gz"), index=False)


def minute_flow(trades: pd.DataFrame, big: int) -> pd.DataFrame:
    t = trades
    ts = pd.to_datetime(t["ts_event"] if "ts_event" in t.columns else t.index, utc=True)
    m = ts.dt.floor("1min").dt.tz_localize(None)
    size = t["size"].astype(float)
    side = t["side"].astype(str)
    px = t["price"].astype(float)
    df = pd.DataFrame({"minute": m.values, "size": size.values, "buy": (size * (side == "B")).values,
                       "sell": (size * (side == "A")).values, "pv": (px * size).values,
                       "big_buy": (size * ((side == "B") & (size >= big))).values,
                       "big_sell": (size * ((side == "A") & (size >= big))).values})
    g = df.groupby("minute").agg(vol=("size", "sum"), buy=("buy", "sum"), sell=("sell", "sum"), pv=("pv", "sum"),
                                 n=("size", "count"), big_buy=("big_buy", "sum"), big_sell=("big_sell", "sum"))
    g = g.reset_index()
    g["time"] = (g["minute"].astype("int64") // 1_000_000_000).astype("int64")
    return g.drop(columns="minute")


def main() -> int:
    import databento as db
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--end", default=(date.today()).isoformat())
    ap.add_argument("--out", default="/opt/algoedge/research_data")
    ap.add_argument("--markets", default=",".join(MARKETS))
    ap.add_argument("--cost", action="store_true")
    ap.add_argument("--max-cost", type=float, default=5.0)
    ap.add_argument("--no-trades", action="store_true")
    ap.add_argument("--bars-start", default="2025-10-01", help="candles start earlier so strategies have history")
    a = ap.parse_args()
    c = db.Historical(key())
    syms = [s for s in a.markets.split(",") if s]
    total = 0.0
    for s in syms:
        for schema in (["ohlcv-1m"] + ([] if a.no_trades else ["trades"])):
            cost = c.metadata.get_cost(dataset=DATASET, symbols=[s], schema=schema, stype_in="continuous",
                                       start=a.start, end=a.end)
            size = c.metadata.get_billable_size(dataset=DATASET, symbols=[s], schema=schema, stype_in="continuous",
                                                start=a.start, end=a.end)
            total += float(cost)
            print(f"cost {s:8s} {schema:9s} ${float(cost):9.2f}  {size / 1e9:6.2f} GB", flush=True)
    print(f"cost total ${total:.2f}", flush=True)
    if a.cost:
        return 0
    if total > a.max_cost:
        print(f"STOP: quote ${total:.2f} is above --max-cost {a.max_cost}; nothing fetched")
        return 2
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for s in syms:
        name, big = MARKETS[s]
        have = lambda stem: (out / f"{stem}.parquet").exists() or (out / f"{stem}.csv.gz").exists()  # noqa: E731
        if have(f"{name}_m1") and (a.no_trades or have(f"{name}_of")):
            print(f"{name}: already fetched", flush=True)
            continue
        bars = c.timeseries.get_range(dataset=DATASET, schema="ohlcv-1m", symbols=[s], stype_in="continuous",
                                      start=a.bars_start, end=a.end).to_df().reset_index()
        bars["time"] = (pd.to_datetime(bars["ts_event"], utc=True).astype("int64") // 1_000_000_000).astype("int64")
        bars = bars.rename(columns={"volume": "tick_volume"})
        bars["real_volume"] = bars["tick_volume"]
        bars["spread"] = 0
        keep = ["time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume", "instrument_id"]
        save(bars[[k for k in keep if k in bars.columns]], out / f"{name}_m1")
        print(f"{name}: {len(bars)} 1m bars", flush=True)
        if a.no_trades:
            continue
        parts = []
        d = datetime.fromisoformat(a.start)
        end = datetime.fromisoformat(a.end)
        while d < end:
            d2 = min(d + timedelta(days=7), end)
            with tempfile.TemporaryDirectory() as td:
                path = os.path.join(td, "week.dbn.zst")        # must not exist yet: databento won't overwrite
                c.timeseries.get_range(dataset=DATASET, schema="trades", symbols=[s], stype_in="continuous",
                                       start=d.isoformat(), end=d2.isoformat(), path=path)
                store = db.DBNStore.from_file(path)
                for chunk in store.to_df(count=2_000_000):
                    if len(chunk):
                        parts.append(minute_flow(chunk.reset_index(), big))
            print(f"{name}: trades to {d2.date()}  ({sum(len(p) for p in parts)} minutes)", flush=True)
            d = d2
        flow = pd.concat(parts).groupby("time", as_index=False).sum(numeric_only=True)
        flow["delta"] = flow["buy"] - flow["sell"]
        flow["vwap"] = flow["pv"] / flow["vol"]
        save(flow.drop(columns="pv"), out / f"{name}_of")
        print(f"{name}: {len(flow)} order-flow minutes", flush=True)
    print("FETCH_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
