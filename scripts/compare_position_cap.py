"""
scripts/compare_position_cap.py

Is the position cap costing you the winners?

On the measured XAUUSD run, 41 of 71 signals (58%) never became trades:

    Max positions reached for XAUUSD (3/3) ..... 21
    min_bars_between_entries .................. 20

Those were discarded by *slot availability*, not by quality — whichever signal
happened to arrive while a slot was free got taken. If the discarded ones were
better than the taken ones, the cap alone could account for the negative
expectancy.

This runs the SAME signals through the real BacktestEngine under several
position-management configurations and prints the outcomes side by side. Signal
generation happens once; only the risk config differs between rows.

Defaults mirror the user's actual run: tp_count=1, tp1_rr=5.0, be_trigger_rr=2.0,
be_mode/trail_mode=RR, risk 1%.
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

# label -> position-management overrides. Everything else is held constant.
VARIANTS = {
    "1 position, no pyramiding": {
        "max_positions_per_symbol": 1, "allow_pyramiding": False,
        "min_bars_between_entries": 0,
    },
    "3 positions, 5-bar gap (yours)": {
        "max_positions_per_symbol": 3, "allow_pyramiding": True,
        "min_bars_between_entries": 5,
    },
    "3 positions, no gap": {
        "max_positions_per_symbol": 3, "allow_pyramiding": True,
        "min_bars_between_entries": 0,
    },
    "5 positions, no gap": {
        "max_positions_per_symbol": 5, "allow_pyramiding": True,
        "min_bars_between_entries": 0,
    },
    "unlimited (10), no gap": {
        "max_positions_per_symbol": 10, "allow_pyramiding": True,
        "min_bars_between_entries": 0,
    },
}


def _index(df):
    if "time" in df.columns:
        return df.set_index(pd.to_datetime(df["time"], unit="s")).sort_index()
    return df.sort_index()


async def generate(symbol, strategy_id, cfg, data, tfs):
    """Phase 1, mirroring api/routes/backtest.py including the advance guard."""
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
    ap.add_argument("--count", type=int, default=17184, help="primary-TF candles (match your UI run)")
    ap.add_argument("--balance", type=float, default=10000.0)
    ap.add_argument("--tp1-rr", type=float, default=5.0)
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
    print(f"  bars: { {tf: len(d) for tf, d in data.items()} }")

    print("Generating signals once (shared by every row)…")
    t0 = time.perf_counter()
    signals = asyncio.run(generate(args.symbol, args.strategy, UserConfigV2(), data, tfs))
    print(f"  {len(signals)} signals in {time.perf_counter() - t0:.1f}s\n")
    if not signals:
        print("No signals — nothing to compare.")
        return 1

    primary = sorted(tfs, key=lambda t: TF_MIN.get(t, 999))[0]
    candles = data[primary].copy()
    if "time" not in candles.columns:
        candles["time"] = candles.index.astype("int64") // 10**9

    hdr = (f"{'position config':<32}{'fills':>6}{'W':>4}{'L':>4}{'WR':>7}"
           f"{'P&L':>11}{'TP':>5}{'SL':>5}{'expR':>8}")
    print(hdr)
    print("-" * len(hdr))

    for name, overrides in VARIANTS.items():
        rc = {
            "risk_per_trade_pct": 1.0, "min_rr": 1.0, "tp_count": 1,
            "tp1_rr": args.tp1_rr,
            "be_trigger_rr": 2.0, "be_mode": "RR",
            "trail_mode": "RR", "trail_activation_rr": 2.0,
            "max_concurrent_positions": max(3, overrides["max_positions_per_symbol"]),
            "max_daily_drawdown_pct": 3.0, "max_weekly_drawdown_pct": 6.0,
            **overrides,
        }
        try:
            res = BacktestEngine(rc).run(candles, list(signals), args.balance)
        except Exception as e:
            print(f"{name:<32}  FAILED: {e}")
            continue

        trades = res.get("trades", []) or []
        n = len(trades)
        wins = sum(1 for t in trades if (t.get("pnl") or 0) > 0)
        pnl = sum((t.get("pnl") or 0) for t in trades)
        rs = [t.get("pnl_r") for t in trades if t.get("pnl_r") is not None]
        exp = (sum(rs) / len(rs)) if rs else float("nan")
        reasons = {}
        for t in trades:
            k = str(t.get("exit_reason", "?")).upper()
            reasons[k] = reasons.get(k, 0) + 1
        tp = sum(v for k, v in reasons.items() if k.startswith("TP"))
        sl = reasons.get("SL", 0)
        wr = f"{(wins / n * 100):.0f}%" if n else "—"
        print(f"{name:<32}{n:>6}{wins:>4}{n - wins:>4}{wr:>7}{pnl:>11.2f}{tp:>5}{sl:>5}{exp:>8.3f}")

    print(f"\n{len(signals)} signals available to every row. "
          f"'fills' is how many actually became trades under that cap.")
    print("Same data, same signals, same costs — only position management differs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
