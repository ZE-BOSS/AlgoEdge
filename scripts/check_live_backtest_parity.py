#!/usr/bin/env python
"""
scripts/check_live_backtest_parity.py

Run one strategy on one symbol over real MT5 bars twice:

  backtest  the routes' signal loop (strategies/bar_feed.BarFeed), bar by bar;
  live      the live scan loop's feeder (LiveBarFeed) driven by a simulated bot
            that scans every --scan-seconds with jitter, optionally goes
            offline for --outage-minutes in the middle, and trades only the
            newest bar's signal.

and list every signal that differs. A signal the simulated bot found late
(inside the outage) is reported separately: the backtest takes it, live cannot.

    python scripts/check_live_backtest_parity.py --strategy SpikeFade_v1 \
        --symbol "Crash 500 Index" --days 20 [--strategy-params '{...}'] \
        [--scan-seconds 60] [--outage-minutes 45]

Exit code 0 when every signal outside the outage matches.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _key(sig):
    return (sig.direction, round(float(sig.entry_price), 6), round(float(sig.stop_loss), 6))


async def run(args) -> int:
    import numpy as np
    import pandas as pd

    from backend.api.routes.backtest import apply_strategy_params
    from backend.core.config_schema import UserConfigV2
    from backend.mt5.data_fetcher import DataFetcher
    from backend.strategies.bar_feed import FIRST_STEP_INDEX, BarFeed, LiveBarFeed
    from backend.strategies.registry import get_strategy
    from backend.strategies.windows import WARMUP_BASE_DAYS, warmup_days

    def engine():
        cfg = UserConfigV2()
        apply_strategy_params(cfg, args.strategy, json.loads(args.strategy_params), args.symbol)
        return get_strategy(args.strategy)(cfg)

    end = datetime.now(timezone.utc).replace(tzinfo=None) if not args.end else datetime.fromisoformat(args.end)
    start = end - timedelta(days=args.days)
    probe = engine()
    tfs = probe.get_required_timeframes()
    frames = {}
    for tf in tfs:
        wd = warmup_days(tf, probe, WARMUP_BASE_DAYS.get(tf, 5))
        df = await DataFetcher.get_data_range(args.symbol, tf, start - timedelta(days=wd), end)
        frames[tf] = df.set_index(pd.to_datetime(df["time"], unit="s")).sort_index()

    # backtest
    bt_eng = engine()
    bt_eng.is_backtesting = True
    feed = BarFeed(bt_eng, args.symbol, tfs)
    feed.bind(frames)
    times = frames[feed.primary].index
    t_start = pd.Timestamp(start)
    backtest = {}
    for i in range(FIRST_STEP_INDEX, len(times)):
        s = await feed.step(i)
        if s and times[i] >= t_start:
            backtest[times[i]] = _key(s)

    # live
    live_eng = engine()
    lfeed = LiveBarFeed(live_eng, args.symbol, tfs)
    rng = np.random.default_rng(1)
    first = max(t_start, times[min(FIRST_STEP_INDEX, len(times) - 1)])
    outage_start = first + (times[-1] - first) / 2
    outage = (outage_start, outage_start + pd.Timedelta(minutes=args.outage_minutes))
    live, missed = {}, {}
    t = first + pd.Timedelta(seconds=2)
    scans = 0
    while t <= times[-1]:
        if not (outage[0] <= t < outage[1]):
            visible = {tf: frames[tf].loc[:t] for tf in tfs}
            res = await lfeed.advance(visible, prime_days=None if scans else None)
            scans += 1
            bar = visible[lfeed.primary].index[-1]
            if res.signal and bar >= t_start:
                live[bar] = _key(res.signal)
            for bt_time, s in res.missed:
                missed[pd.Timestamp(bt_time)] = _key(s)
        t += pd.Timedelta(seconds=args.scan_seconds + int(rng.integers(0, 10)))

    last_live_bar = frames[lfeed.primary].loc[:t].index[-1]
    bt_cmp = {k: v for k, v in backtest.items() if k <= last_live_bar}
    matched = sorted(k for k in bt_cmp if live.get(k) == bt_cmp[k])
    late = sorted(k for k in bt_cmp if k not in live and missed.get(k) == bt_cmp[k])
    bt_only = sorted(k for k in bt_cmp if k not in live and k not in missed)
    live_only = sorted(k for k in live if k not in bt_cmp)
    differ = sorted(k for k in bt_cmp if k in live and live[k] != bt_cmp[k])

    print(f"{args.strategy} on {args.symbol}, {start:%Y-%m-%d} -> {end:%Y-%m-%d}, {scans} simulated scans "
          f"every ~{args.scan_seconds}s, outage {outage[0]:%m-%d %H:%M}–{outage[1]:%H:%M}")
    print(f"  backtest signals {len(bt_cmp)} | live traded {len(live)} | matched {len(matched)} "
          f"| found late during the outage {len(late)}")
    for name, rows in (("backtest only", bt_only), ("live only", live_only), ("different levels", differ)):
        if rows:
            print(f"  {name}: {len(rows)}")
            for k in rows[:10]:
                print(f"    {k}  backtest={bt_cmp.get(k)}  live={live.get(k)}")
    ok = not (bt_only or live_only or differ)
    print("  PARITY OK" if ok else "  PARITY FAILED")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--days", type=float, default=20)
    ap.add_argument("--end", default=None)
    ap.add_argument("--strategy-params", default="{}")
    ap.add_argument("--scan-seconds", type=int, default=60)
    ap.add_argument("--outage-minutes", type=int, default=45)
    return asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
