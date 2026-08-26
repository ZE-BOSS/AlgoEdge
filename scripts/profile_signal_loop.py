"""
scripts/profile_signal_loop.py

Measure where a backtest's Phase-1 signal loop actually spends its time.

Written because "the backtest is slow" was being answered with guesses. This
reproduces the loop from `api/routes/backtest.py` faithfully — same slicing,
same per-timeframe advance guard, same on_bar calls — against cached MT5 data,
under cProfile.

    venv_win/Scripts/python.exe scripts/profile_signal_loop.py --bars 1500
    venv_win/Scripts/python.exe scripts/profile_signal_loop.py --strategy VWAP_v1
"""

from __future__ import annotations

import argparse
import asyncio
import cProfile
import pstats
import sys
import time
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from backend.core.config_schema import UserConfigV2  # noqa: E402
from backend.mt5.data_fetcher import DataFetcher  # noqa: E402
from backend.strategies.registry import get_strategy  # noqa: E402

TF_META = {
    "M1": {"window": 300, "np_td": (1, "m")},
    "M5": {"window": 300, "np_td": (5, "m")},
    "M15": {"window": 300, "np_td": (15, "m")},
    "M30": {"window": 300, "np_td": (30, "m")},
    "H1": {"window": 300, "np_td": (1, "h")},
    "H4": {"window": 300, "np_td": (4, "h")},
    "D1": {"window": 300, "np_td": (1, "D")},
}
TF_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}


def _index(df):
    if "time" in df.columns:
        return df.set_index(pd.to_datetime(df["time"], unit="s")).sort_index()
    return df.sort_index()


async def build(symbol: str, strategy_id: str, count: int):
    cfg = UserConfigV2()
    engine = get_strategy(strategy_id)(cfg)
    tfs = engine.get_required_timeframes()
    data = {}
    for tf in tfs:
        df = await DataFetcher.get_historical_data(symbol, tf, count=count)
        if df is None or df.empty:
            raise SystemExit(f"No data for {symbol} {tf} — is MT5 connected?")
        data[tf] = _index(df)
    return engine, tfs, data


async def run_loop(engine, tfs, data, bars: int, skip_unadvanced: bool):
    """
    `skip_unadvanced` is the thing under test: when True, the DataFrame slice is
    built only after we know the timeframe has produced a new bar. When False it
    reproduces the current code, which slices on every iteration and then throws
    most of them away.
    """
    primary = sorted(tfs, key=lambda t: TF_MINUTES.get(t, 999))[0]
    ptimes = data[primary].index.values
    tf_times = {tf: data[tf].index.values for tf in tfs}
    prev = {tf: None for tf in tfs}

    start = max(300, len(ptimes) - bars)
    slices_built = 0
    on_bar_calls = 0
    signals = 0

    for i in range(start, len(ptimes)):
        now = ptimes[i]
        for tf in tfs:
            meta = TF_META.get(tf, TF_META["M5"])
            sdf = data[tf]
            if tf == primary:
                tf_end = i
                last = ptimes[i]
            else:
                td, unit = meta["np_td"]
                tf_end = int(np.searchsorted(tf_times[tf], now - np.timedelta64(td, unit), side="right"))
                last = tf_times[tf][tf_end - 1] if tf_end > 0 else None

            if skip_unadvanced and (last is None or last == prev[tf]):
                continue

            sl = sdf.iloc[max(0, tf_end - meta["window"]):tf_end]
            slices_built += 1
            if len(sl) < 20:
                continue
            if last != prev[tf]:
                on_bar_calls += 1
                if await engine.on_bar("PROFILE", tf, sl):
                    signals += 1
                prev[tf] = last

    return {"slices": slices_built, "on_bar": on_bar_calls, "signals": signals}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--strategy", default="APA_v1")
    ap.add_argument("--bars", type=int, default=1500, help="primary-TF bars to simulate")
    ap.add_argument("--count", type=int, default=5000, help="candles to fetch per timeframe")
    ap.add_argument("--top", type=int, default=25)
    args = ap.parse_args()

    engine, tfs, data = asyncio.run(build(args.symbol, args.strategy, args.count))
    print(f"{args.strategy} on {args.symbol}: timeframes {tfs}, "
          f"{ {tf: len(d) for tf, d in data.items()} }")

    # A/B the slice-on-advance change before profiling, so the headline number
    # is measured rather than argued.
    for label, skip in (("current (slice every bar)", False), ("slice only on advance", True)):
        for tf in tfs:
            pass
        engine2, tfs2, data2 = asyncio.run(build(args.symbol, args.strategy, args.count))
        t0 = time.perf_counter()
        stats = asyncio.run(run_loop(engine2, tfs2, data2, args.bars, skip))
        dt = time.perf_counter() - t0
        print(f"  {label:<28} {dt:7.2f}s   slices={stats['slices']:>6}  "
              f"on_bar={stats['on_bar']:>6}  signals={stats['signals']}")

    print("\n--- cProfile (current behaviour) ---")
    engine3, tfs3, data3 = asyncio.run(build(args.symbol, args.strategy, args.count))
    pr = cProfile.Profile()
    pr.enable()
    asyncio.run(run_loop(engine3, tfs3, data3, args.bars, False))
    pr.disable()
    buf = StringIO()
    pstats.Stats(pr, stream=buf).sort_stats("cumulative").print_stats(args.top)
    # Trim the profiler's own frames — they dominate the head and say nothing.
    for line in buf.getvalue().splitlines():
        if "asyncio" in line and "base_events" in line:
            continue
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
