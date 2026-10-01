#!/usr/bin/env python
"""
scripts/export_new_synthetics.py

Export bars and contract specs for Deriv's newer synthetic indices from the
trading server's MT5 terminal, so research can run where MT5 is not available.

    .\\venv\\Scripts\\python.exe scripts\\export_new_synthetics.py
    git add data/deriv_new && git commit -m "Synthetic bars for research" && git push

Finds the instruments by NAME in the terminal (Vol over Crash/Boom, DEX
UP/DOWN, Drift Switch), so the exact broker spelling does not matter, and adds
the classic Crash/Boom 500/1000 for comparison. For each: M1 and M5 bars from
--start to now (UTC, the same server->UTC shift the backtester uses), saved as
compressed .npz, plus data/deriv_new/specs.json with every field the dollar
arithmetic depends on (tick value and size, contract size, minimum lot, point,
digits, stops level, typical spread).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PATTERNS = ("vol over", "dex", "drift", "crash 500", "crash 1000", "boom 500", "boom 1000")
CHUNK_DAYS = {"M1": 20, "M5": 90}


def fname(symbol: str, tf: str) -> str:
    return f"{symbol.replace(' ', '_')}_{tf}.npz"


async def harvest(symbol: str, tf: str, start: datetime, end: datetime, out: Path) -> str:
    from backend.mt5.data_fetcher import DataFetcher
    frames, cur = [], pd.Timestamp(start)
    stop, step = pd.Timestamp(end), pd.Timedelta(days=CHUNK_DAYS[tf])
    while cur < stop:
        nxt = min(cur + step, stop)
        try:
            df = await DataFetcher.get_data_range(symbol, tf, cur.to_pydatetime(), nxt.to_pydatetime())
        except Exception:
            df = None
        if df is not None and not df.empty:
            frames.append(df)
        cur = nxt
    if not frames:
        return f"{symbol} {tf}: NO DATA"
    df = pd.concat(frames, ignore_index=True).drop_duplicates("time").sort_values("time")
    vol_col = next((c for c in ("tick_volume", "volume", "real_volume") if c in df.columns), None)
    np.savez_compressed(
        out / fname(symbol, tf),
        time=df["time"].to_numpy(dtype=np.int64),
        open=df["open"].to_numpy(dtype=np.float64), high=df["high"].to_numpy(dtype=np.float64),
        low=df["low"].to_numpy(dtype=np.float64), close=df["close"].to_numpy(dtype=np.float64),
        spread_points=df["spread"].to_numpy(dtype=np.float64) if "spread" in df.columns else np.zeros(len(df)),
        tick_volume=df[vol_col].to_numpy(dtype=np.float64) if vol_col else np.zeros(len(df)),
    )
    first = datetime.fromtimestamp(int(df["time"].iloc[0]), timezone.utc).strftime("%Y-%m-%d")
    last = datetime.fromtimestamp(int(df["time"].iloc[-1]), timezone.utc).strftime("%Y-%m-%d")
    return f"{symbol} {tf}: {len(df):,} bars {first} -> {last}"


def spec(mt5, name: str) -> dict:
    mt5.symbol_select(name, True)
    i = mt5.symbol_info(name)
    if i is None:
        return {}
    return {k: getattr(i, k) for k in (
        "path", "description", "digits", "point", "spread", "spread_float", "trade_tick_value",
        "trade_tick_value_profit", "trade_tick_value_loss", "trade_tick_size", "trade_contract_size",
        "volume_min", "volume_max", "volume_step", "trade_stops_level", "currency_profit",
        "swap_long", "swap_short", "trade_calc_mode") if hasattr(i, k)}


async def main_async(args) -> None:
    import MetaTrader5 as mt5
    if not mt5.initialize():
        raise SystemExit("MT5 is not connected: open the terminal and log in first")
    names = sorted({s.name for s in mt5.symbols_get()
                    if any(p in s.name.lower() for p in PATTERNS)})
    if args.only:
        names = [n for n in names if any(o.lower() in n.lower() for o in args.only)]
    if not names:
        raise SystemExit("no matching symbols in this terminal")
    print("Exporting:", ", ".join(names), flush=True)
    out = ROOT / "data" / "deriv_new"
    out.mkdir(parents=True, exist_ok=True)
    specs = {n: spec(mt5, n) for n in names}
    (out / "specs.json").write_text(json.dumps(specs, indent=1, default=str), encoding="utf-8")
    start = datetime.fromisoformat(args.start)
    end = datetime.now(timezone.utc).replace(tzinfo=None)
    for n in names:
        for tf in args.tfs:
            t0 = time.time()
            try:
                msg = await harvest(n, tf, start, end, out)
            except Exception as e:
                msg = f"{n} {tf}: FAILED {type(e).__name__}: {e}"
            print(f"{msg}  ({time.time() - t0:.0f}s)", flush=True)
    print(f"\nDone. Files in {out}. Now: git add data/deriv_new; git commit -m \"Synthetic bars\"; git push")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--tfs", nargs="*", default=["M1", "M5"])
    ap.add_argument("--only", nargs="*", help="name substrings to restrict to")
    asyncio.run(main_async(ap.parse_args()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
