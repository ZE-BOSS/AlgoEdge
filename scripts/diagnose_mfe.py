"""
scripts/diagnose_mfe.py

How far does each trade actually travel in its favour before it dies?

Motivated by a comparison run in which four different exit configurations —
including one with break-even and trailing switched OFF entirely — produced
byte-identical results (12 trades, 100% SL, same P&L). That can only happen if
no exit rule ever fires, which means every position goes from entry straight to
its stop.

This measures Maximum Favourable Excursion in R for each leg, so the question
"is the exit ladder mis-tuned, or does price simply never get there" is answered
with a distribution rather than a guess.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from backend.core.config_schema import UserConfigV2  # noqa: E402
from backend.mt5.data_fetcher import DataFetcher  # noqa: E402
from backend.strategies.registry import get_strategy  # noqa: E402

TF_META = {
    "M1": {"window": 300, "np_td": (1, "m")}, "M5": {"window": 300, "np_td": (5, "m")},
    "M15": {"window": 300, "np_td": (15, "m")}, "M30": {"window": 300, "np_td": (30, "m")},
    "H1": {"window": 300, "np_td": (1, "h")}, "H4": {"window": 300, "np_td": (4, "h")},
}
TF_MIN = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240}


def _index(df):
    if "time" in df.columns:
        return df.set_index(pd.to_datetime(df["time"], unit="s")).sort_index()
    return df.sort_index()


async def gen(symbol, strategy_id, data, tfs, cfg=None):
    engine = get_strategy(strategy_id)(cfg or UserConfigV2())
    primary = sorted(tfs, key=lambda t: TF_MIN.get(t, 999))[0]
    ptimes = data[primary].index.values
    tf_times = {tf: data[tf].index.values for tf in tfs}
    prev = {tf: None for tf in tfs}
    out = []
    for i in range(300, len(ptimes)):
        now, sig = ptimes[i], None
        for tf in tfs:
            meta = TF_META.get(tf, TF_META["M5"])
            if tf == primary:
                tf_end, last = i, ptimes[i]
            else:
                td, unit = meta["np_td"]
                tf_end = int(np.searchsorted(tf_times[tf], now - np.timedelta64(td, unit), side="right"))
                last = tf_times[tf][tf_end - 1] if tf_end > 0 else None
            if last is None or last == prev[tf]:
                continue
            sl = data[tf].iloc[max(0, tf_end - meta["window"]):tf_end]
            if len(sl) < 20:
                continue
            s = await engine.on_bar(symbol, tf, sl)
            if s:
                sig = s
            prev[tf] = last
        if sig:
            out.append((pd.Timestamp(now), sig))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--strategy", default="APA_v1")
    ap.add_argument("--count", type=int, default=5000)
    ap.add_argument("--max-bars", type=int, default=2000, help="bars to follow a trade before giving up")
    ap.add_argument("--require-retest", action="store_true",
                    help="APA only: require a retest into the Invalidation Zone before entry")
    args = ap.parse_args()

    cfg = UserConfigV2()
    if hasattr(cfg, "apa"):
        cfg.apa.require_retest = bool(args.require_retest)
    tfs = get_strategy(args.strategy)(cfg).get_required_timeframes()
    data = {}
    for tf in tfs:
        df = asyncio.run(DataFetcher.get_historical_data(args.symbol, tf, count=args.count))
        if df is None or df.empty:
            print(f"No data for {args.symbol} {tf}")
            return 2
        data[tf] = _index(df)

    sigs = asyncio.run(gen(args.symbol, args.strategy, data, tfs, cfg))
    print(f"{len(sigs)} signals\n")
    if not sigs:
        return 1

    primary = sorted(tfs, key=lambda t: TF_MIN.get(t, 999))[0]
    px = data[primary]
    highs, lows = px["high"].to_numpy(float), px["low"].to_numpy(float)
    times = px.index.values

    print(f"{'#':>3}{'dir':>5}{'entry':>11}{'stop':>11}{'risk':>9}{'MFE_R':>8}{'MAE_R':>8}{'bars':>6}  outcome")
    print("-" * 78)

    reached = {"0.5R": 0, "1.0R": 0, "1.5R": 0, "2.0R": 0, "3.0R": 0}
    mfes = []

    for n, (t, s) in enumerate(sigs, 1):
        entry, stop = float(s.entry_price), float(s.stop_loss)
        risk = abs(entry - stop)
        if risk <= 0:
            continue
        is_buy = str(s.direction).upper().startswith("B")
        i0 = int(np.searchsorted(times, np.datetime64(t), side="right"))

        best = worst = 0.0
        bars = 0
        outcome = "still open at data end"
        for i in range(i0, min(i0 + args.max_bars, len(highs))):
            bars = i - i0 + 1
            fav = (highs[i] - entry) / risk if is_buy else (entry - lows[i]) / risk
            adv = (entry - lows[i]) / risk if is_buy else (highs[i] - entry) / risk
            best = max(best, fav)
            worst = max(worst, adv)
            if adv >= 1.0:                     # stop touched
                outcome = "SL"
                break
        else:
            if best >= 1.5:
                outcome = "reached TP1 distance"

        mfes.append(best)
        for k, thr in (("0.5R", 0.5), ("1.0R", 1.0), ("1.5R", 1.5), ("2.0R", 2.0), ("3.0R", 3.0)):
            if best >= thr:
                reached[k] += 1

        print(f"{n:>3}{'BUY' if is_buy else 'SELL':>5}{entry:>11.3f}{stop:>11.3f}"
              f"{risk:>9.3f}{best:>8.2f}{worst:>8.2f}{bars:>6}  {outcome}")

    n = len(mfes)
    print(f"\nMaximum Favourable Excursion across {n} signals")
    print(f"  median {np.median(mfes):.2f}R   mean {np.mean(mfes):.2f}R   best {max(mfes):.2f}R")
    print("\nHow many ever reached:")
    for k, v in reached.items():
        bar = "#" * int(v / max(n, 1) * 40)
        print(f"  {k:>5}: {v:>3}/{n}  {v / n * 100:>5.1f}%  {bar}")

    tp1 = reached["1.5R"]
    print()
    if tp1 == 0:
        print("  No signal ever reached TP1's distance. The exit ladder is NOT the")
        print("  binding constraint — nothing it could be set to would change the")
        print("  result. The stop is being hit before price travels 1.5x its width.")
    else:
        print(f"  {tp1} of {n} reached 1.5R, so TP1 was reachable and exit tuning matters.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
