"""
scripts/optimise_strategy.py

Strategy optimisation study — one expensive pass, many answers.

Every previous investigation re-ran the whole engine per variant, which was slow
and made it easy to change two things at once (the earlier target sweep moved
break-even along with the target, so it measured neither cleanly).

This does it differently. For each signal it walks forward bar by bar and records
the exact path: how far price went in favour (MFE) and against (MAE) in R, and
which came first. From that single record every fixed-target question is
ARITHMETIC rather than a simulation:

    outcome at target T  =  WIN  if the trade reached +T R before -1R
                            LOSS otherwise

That is exact for a fixed TP/SL with no break-even or trailing, so target sweeps,
confluence filters and session windows are all answerable from one pass, with no
risk of confounding.

What it deliberately does NOT model: break-even, trailing, partial exits, costs.
Those need the real engine. Use this to find WHERE the edge is, then confirm with
`compare_exit_ladder.py` / the real backtester.

    venv_win/Scripts/python.exe scripts/optimise_strategy.py --symbols XAUUSD,EURUSD
    venv_win/Scripts/python.exe scripts/optimise_strategy.py --symbols XAUUSD --count 17184
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
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

TARGETS = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 8.0, 10.0]


def _index(df):
    if "time" in df.columns:
        return df.set_index(pd.to_datetime(df["time"], unit="s")).sort_index()
    return df.sort_index()


async def collect(symbol, strategy_id, data, tfs, max_bars=3000):
    """
    Generate signals and record each one's forward path.

    `first_to` is the key field: the R-multiple reached in favour BEFORE price
    ever traded -1R. A fixed target T is hit if and only if first_to >= T, so one
    number settles every target in the sweep.
    """
    engine = get_strategy(strategy_id)(UserConfigV2())
    primary = sorted(tfs, key=lambda t: TF_MIN.get(t, 999))[0]
    px = data[primary]
    ptimes = px.index.values
    highs = px["high"].to_numpy(float)
    lows = px["low"].to_numpy(float)
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
            sl_df = data[tf].iloc[max(0, tf_end - meta["window"]):tf_end]
            if len(sl_df) < 20:
                continue
            s = await engine.on_bar(symbol, tf, sl_df)
            if s:
                sig = s
            prev[tf] = last

        if not sig:
            continue
        entry, stop = float(sig.entry_price), float(sig.stop_loss)
        risk = abs(entry - stop)
        if risk <= 0:
            continue
        is_buy = str(sig.direction).upper().startswith("B")

        best = 0.0          # MFE over the whole life
        first_to = 0.0      # best reached BEFORE the stop was touched
        stopped = False
        bars = 0
        for j in range(i + 1, min(i + 1 + max_bars, len(highs))):
            bars = j - i
            fav = (highs[j] - entry) / risk if is_buy else (entry - lows[j]) / risk
            adv = (entry - lows[j]) / risk if is_buy else (highs[j] - entry) / risk
            best = max(best, fav)
            if not stopped:
                first_to = max(first_to, fav)
            # Same-bar ambiguity: if a bar spans both, assume the STOP came first.
            # Conservative, and consistent with the engine's own wick handling.
            if adv >= 1.0:
                stopped = True
                break

        ts = pd.Timestamp(now)
        meta_d = sig.metadata or {}
        out.append({
            "symbol": symbol,
            "time": ts.isoformat(),
            "hour": int(ts.hour),
            "dow": int(ts.dayofweek),
            "direction": "BUY" if is_buy else "SELL",
            "entry": entry, "stop": stop, "risk": risk,
            "confluence": int(getattr(sig, "confluence_score", 0) or 0),
            "mfe": round(best, 3),
            "first_to": round(first_to, 3),
            "stopped": stopped,
            "bars": bars,
            "retest_occurred": bool(meta_d.get("retest_occurred")),
            "retest_rejected": bool(meta_d.get("retest_rejected")),
            "sl_floored": bool(meta_d.get("sl_floored")),
        })
    return out


def expectancy_at(rows, target):
    """Expectancy in R for a fixed target, no BE/trail/costs."""
    if not rows:
        return float("nan"), 0, 0
    wins = sum(1 for r in rows if r["first_to"] >= target)
    n = len(rows)
    return (wins * target - (n - wins) * 1.0) / n, wins, n


def table(title, groups, targets=TARGETS):
    """groups: list of (label, rows)."""
    print(f"\n{title}")
    hdr = f"{'':<26}{'n':>5}" + "".join(f"{t:>7.1f}" for t in targets)
    print(hdr)
    print("-" * len(hdr))
    for label, rows in groups:
        if not rows:
            print(f"{label:<26}{0:>5}")
            continue
        cells = "".join(f"{expectancy_at(rows, t)[0]:>7.2f}" for t in targets)
        print(f"{label:<26}{len(rows):>5}{cells}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="XAUUSD")
    ap.add_argument("--strategy", default="APA_v1")
    ap.add_argument("--count", type=int, default=17184)
    ap.add_argument("--save", default="debug/apa_optimisation.json")
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    cfg = UserConfigV2()
    tfs = get_strategy(args.strategy)(cfg).get_required_timeframes()

    allrows = []
    for sym in symbols:
        print(f"[{sym}] fetching {tfs}…", end=" ", flush=True)
        data = {}
        ok = True
        for tf in tfs:
            df = asyncio.run(DataFetcher.get_historical_data(sym, tf, count=args.count))
            if df is None or df.empty:
                print("NO DATA"); ok = False; break
            data[tf] = _index(df)
        if not ok:
            continue
        t0 = time.perf_counter()
        rows = asyncio.run(collect(sym, args.strategy, data, tfs))
        print(f"{len(rows)} signals in {time.perf_counter() - t0:.0f}s")
        allrows.extend(rows)

    if not allrows:
        print("No signals collected.")
        return 1

    Path(args.save).parent.mkdir(parents=True, exist_ok=True)
    Path(args.save).write_text(json.dumps(allrows, indent=1), encoding="utf-8")
    print(f"\n{len(allrows)} signals total → {args.save}")

    print("\n" + "=" * 78)
    print("EXPECTANCY (R per trade) BY FIXED TARGET — no BE, no trail, no costs")
    print("=" * 78)

    table("ALL SIGNALS", [("all", allrows)])

    if len(symbols) > 1:
        table("BY SYMBOL", [(s, [r for r in allrows if r["symbol"] == s]) for s in symbols])

    # Does the confluence score predict anything? The audit noted it gates
    # nothing; if it also predicts nothing, it is decoration.
    scores = sorted({r["confluence"] for r in allrows})
    if len(scores) > 1:
        lo, hi = np.percentile([r["confluence"] for r in allrows], [50, 50])
        table("BY CONFLUENCE", [
            (f"score <= {int(lo)}", [r for r in allrows if r["confluence"] <= lo]),
            (f"score >  {int(lo)}", [r for r in allrows if r["confluence"] > lo]),
        ])
        print(f"  scores present: {scores}")

    table("BY DIRECTION", [
        ("BUY", [r for r in allrows if r["direction"] == "BUY"]),
        ("SELL", [r for r in allrows if r["direction"] == "SELL"]),
    ])

    table("BY SESSION (entry hour, UTC)", [
        ("00-07", [r for r in allrows if r["hour"] < 7]),
        ("07-12", [r for r in allrows if 7 <= r["hour"] < 12]),
        ("12-17", [r for r in allrows if 12 <= r["hour"] < 17]),
        ("17-24", [r for r in allrows if r["hour"] >= 17]),
    ])

    table("BY STOP QUALITY", [
        ("structural stop", [r for r in allrows if not r["sl_floored"]]),
        ("floored stop", [r for r in allrows if r["sl_floored"]]),
    ])

    # Headline: the reach curve is what every target decision rests on.
    print("\nREACH CURVE — share of signals reaching +XR before their stop")
    n = len(allrows)
    for t in TARGETS:
        k = sum(1 for r in allrows if r["first_to"] >= t)
        need = 1.0 / (1.0 + t)
        mark = "  <-- profitable" if (k / n) > need else ""
        print(f"  {t:>5.1f}R : {k:>4}/{n}  {k / n * 100:>5.1f}%   "
              f"(needs {need * 100:>4.1f}%){mark}")

    print("\nExpectancy here EXCLUDES break-even, trailing and costs — it isolates")
    print("entry quality and target choice. Confirm any promising row against the")
    print("real engine before trusting it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
