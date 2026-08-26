"""
scripts/sweep_target_rr.py

Find the take-profit distance where expectancy peaks — or show there isn't one.

The measured situation on XAUUSD/APA: a 5R target is struck ~11-13% of the time
and needs ~17% to break even. MFE data says 36% of signals reach 1.5R but only
~13% reach 5R. So a nearer target trades payoff for strike rate, and somewhere
on that curve is the best available point. This finds it.

Signals are generated ONCE and shared by every row; only `tp1_rr` changes. That
isolates the target from everything else — same entries, same stops, same costs.

    venv_win/Scripts/python.exe scripts/sweep_target_rr.py
    venv_win/Scripts/python.exe scripts/sweep_target_rr.py --symbol EURUSD

Read the `expR` column, not P&L: expectancy per trade is comparable across rows
with different trade counts, total P&L is not.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from backend.backtester.engine import BacktestEngine  # noqa: E402
from backend.core.config_schema import UserConfigV2  # noqa: E402
from backend.mt5.data_fetcher import DataFetcher  # noqa: E402
from backend.strategies.registry import get_strategy  # noqa: E402

TF_META = {
    "M1": {"window": 300, "np_td": (1, "m")}, "M5": {"window": 300, "np_td": (5, "m")},
    "M15": {"window": 300, "np_td": (15, "m")}, "M30": {"window": 300, "np_td": (30, "m")},
    "H1": {"window": 300, "np_td": (1, "h")}, "H4": {"window": 300, "np_td": (4, "h")},
}
TF_MIN = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240}

TARGETS = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 7.0, 10.0]


def _index(df):
    if "time" in df.columns:
        return df.set_index(pd.to_datetime(df["time"], unit="s")).sort_index()
    return df.sort_index()


async def generate(symbol, strategy_id, cfg, data, tfs):
    engine = get_strategy(strategy_id)(cfg)
    primary = sorted(tfs, key=lambda t: TF_MIN.get(t, 999))[0]
    ptimes = data[primary].index.values
    tf_times = {tf: data[tf].index.values for tf in tfs}
    prev = {tf: None for tf in tfs}
    sigs = []
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
            sigs.append({
                "symbol": sig.symbol, "direction": sig.direction,
                "time": int(pd.Timestamp(now).timestamp()),
                "entry_price": sig.entry_price, "stop_loss": sig.stop_loss,
                "take_profit": sig.take_profit, "timeframe": sig.timeframe,
                "confluence_score": sig.confluence_score, "metadata": sig.metadata,
            })
    return sigs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--strategy", default="APA_v1")
    ap.add_argument("--count", type=int, default=17184)
    ap.add_argument("--balance", type=float, default=10000.0)
    ap.add_argument("--max-positions", type=int, default=1,
                    help="1 = the configuration that lost least in the cap comparison")
    args = ap.parse_args()

    cfg = UserConfigV2()
    tfs = get_strategy(args.strategy)(cfg).get_required_timeframes()

    print(f"Fetching {args.symbol} {tfs}…")
    data = {}
    for tf in tfs:
        df = asyncio.run(DataFetcher.get_historical_data(args.symbol, tf, count=args.count))
        if df is None or df.empty:
            print(f"No data for {args.symbol} {tf} — is MT5 connected?")
            return 2
        data[tf] = _index(df)

    print("Generating signals once…")
    t0 = time.perf_counter()
    signals = asyncio.run(generate(args.symbol, args.strategy, UserConfigV2(), data, tfs))
    print(f"  {len(signals)} signals in {time.perf_counter() - t0:.1f}s\n")
    if not signals:
        return 1

    primary = sorted(tfs, key=lambda t: TF_MIN.get(t, 999))[0]
    candles = data[primary].copy()
    if "time" not in candles.columns:
        candles["time"] = candles.index.astype("int64") // 10**9

    hdr = (f"{'target':>7}{'fills':>7}{'W':>4}{'L':>4}{'WR':>7}{'needed':>8}"
           f"{'P&L':>11}{'expR':>8}  verdict")
    print(hdr)
    print("-" * (len(hdr) + 8))

    rows = []
    for tp in TARGETS:
        rc = {
            "risk_per_trade_pct": 1.0, "min_rr": 1.0, "tp_count": 1,
            "tp1_rr": tp,
            # Break-even must stay clear of the target or it pre-empts the fill.
            "be_trigger_rr": max(0.5, tp * 0.6), "be_mode": "RR",
            "trail_mode": "NONE",
            "max_positions_per_symbol": args.max_positions,
            "max_concurrent_positions": max(3, args.max_positions),
            "allow_pyramiding": args.max_positions > 1,
            "min_bars_between_entries": 0,
            "max_daily_drawdown_pct": 3.0, "max_weekly_drawdown_pct": 6.0,
        }
        try:
            res = BacktestEngine(rc).run(candles, list(signals), args.balance)
        except Exception as e:
            print(f"{tp:>7.1f}  FAILED: {e}")
            continue

        trades = res.get("trades", []) or []
        n = len(trades)
        if not n:
            print(f"{tp:>7.1f}{n:>7}   — no fills")
            continue
        wins = sum(1 for t in trades if (t.get("pnl") or 0) > 0)
        pnl = sum((t.get("pnl") or 0) for t in trades)
        wr = wins / n
        # Break-even strike rate for a tp:1 payoff, ignoring costs.
        needed = 1.0 / (1.0 + tp)
        # Expectancy in R from realised P&L against the 1% risk budget.
        risk_cash = args.balance * 0.01
        exp_r = (pnl / n) / risk_cash if risk_cash else float("nan")
        verdict = "PROFITABLE" if pnl > 0 else ("near" if exp_r > -0.15 else "")
        rows.append((tp, exp_r, pnl, n))
        print(f"{tp:>7.1f}{n:>7}{wins:>4}{n - wins:>4}{wr * 100:>6.0f}%"
              f"{needed * 100:>7.0f}%{pnl:>11.2f}{exp_r:>8.3f}  {verdict}")

    if rows:
        best = max(rows, key=lambda r: r[1])
        print(f"\nBest expectancy at tp1_rr={best[0]}: {best[1]:+.3f}R over {best[3]} trades "
              f"(P&L {best[2]:+.2f})")
        if best[1] <= 0:
            print("Every target tested is negative — the edge is not a targeting problem.")
    print("\n'needed' is the break-even strike rate for that payoff, before costs.")
    print("Compare WR against it: above = profitable, below = not.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
