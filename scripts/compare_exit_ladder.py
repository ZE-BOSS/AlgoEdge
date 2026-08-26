"""
scripts/compare_exit_ladder.py

Run the same window under several exit-ladder configurations and print the
outcomes side by side.

Written because "does moving break-even later actually help" is an empirical
question, and the only honest way to answer it is to run both and look. Every
variant uses identical data, identical signals and identical costs — the ONLY
thing that differs is the exit configuration named in the row.

    venv_win/Scripts/python.exe scripts/compare_exit_ladder.py
    venv_win/Scripts/python.exe scripts/compare_exit_ladder.py --symbol EURUSD --strategy VWAP_v1
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
    "D1": {"window": 300, "np_td": (1, "D")},
}
TF_MIN = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}

# name -> risk-config overrides. tp_count is included so the comparison can also
# answer "does a 3-TP ladder behave differently from a single target".
VARIANTS = {
    "old: BE/trail at 1.5R (= TP1)": {
        "be_mode": "EITHER", "be_trigger_rr": 1.5,
        "trail_mode": "RR", "trail_activation_rr": 1.5,
    },
    "new: BE/trail at 2.0R, EITHER": {
        "be_mode": "EITHER", "be_trigger_rr": 2.0,
        "trail_mode": "EITHER", "trail_activation_rr": 2.0,
    },
    "TP-fill only (no R trigger)": {
        "be_mode": "TP_HIT", "be_trigger_rr": 2.0,
        "trail_mode": "TP_HIT", "trail_activation_rr": 2.0,
    },
    "no BE, no trail": {
        "be_mode": "NONE", "trail_mode": "NONE",
    },
}


def _index(df):
    if "time" in df.columns:
        return df.set_index(pd.to_datetime(df["time"], unit="s")).sort_index()
    return df.sort_index()


async def generate_signals(symbol, strategy_id, cfg, data, tfs):
    """Phase 1, mirroring api/routes/backtest.py including the advance guard."""
    engine = get_strategy(strategy_id)(cfg)
    primary = sorted(tfs, key=lambda t: TF_MIN.get(t, 999))[0]
    ptimes = data[primary].index.values
    tf_times = {tf: data[tf].index.values for tf in tfs}
    prev = {tf: None for tf in tfs}
    sigs = []

    for i in range(300, len(ptimes)):
        now = ptimes[i]
        sig = None
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
    ap.add_argument("--count", type=int, default=5000)
    ap.add_argument("--balance", type=float, default=10000.0)
    ap.add_argument("--tp-count", type=int, default=3)
    args = ap.parse_args()

    base = UserConfigV2()
    tfs = get_strategy(args.strategy)(base).get_required_timeframes()

    print(f"Fetching {args.symbol} {tfs} x{args.count}…")
    data = {}
    for tf in tfs:
        df = asyncio.run(DataFetcher.get_historical_data(args.symbol, tf, count=args.count))
        if df is None or df.empty:
            print(f"No data for {args.symbol} {tf} — is MT5 connected?")
            return 2
        data[tf] = _index(df)

    print("Generating signals (identical across every variant)…")
    t0 = time.perf_counter()
    signals = asyncio.run(generate_signals(args.symbol, args.strategy, UserConfigV2(), data, tfs))
    print(f"  {len(signals)} signals in {time.perf_counter() - t0:.1f}s\n")
    if not signals:
        print("No signals — nothing to compare.")
        return 1

    primary = sorted(tfs, key=lambda t: TF_MIN.get(t, 999))[0]
    candles = data[primary].copy()
    if "time" not in candles.columns:
        candles["time"] = candles.index.astype("int64") // 10**9

    hdr = f"{'variant':<32}{'trades':>7}{'W':>4}{'L':>4}{'WR':>7}{'P&L':>10}{'TP1':>7}{'SL':>7}{'BE':>7}{'TRAIL':>7}"
    print(hdr)
    print("-" * len(hdr))

    for name, overrides in VARIANTS.items():
        rc = {
            "risk_per_trade_pct": 1.0, "min_rr": 1.0,
            "tp_count": args.tp_count,
            "tp1_rr": 1.5, "tp2_rr": 3.0, "tp3_rr": 5.0,
            "tp_splits": [50, 30, 20][:args.tp_count],
            "max_positions_per_symbol": 1,
            "max_concurrent_positions": 3,
            "trail_method_tp2": "ATR_TRAIL", "atr_trail_multiplier": 1.5,
            **overrides,
        }
        try:
            res = BacktestEngine(rc).run(candles, list(signals), args.balance)
        except Exception as e:
            print(f"{name:<32}  FAILED: {e}")
            continue

        trades = res.get("trades", []) or []
        wins = sum(1 for t in trades if (t.get("pnl") or 0) > 0)
        pnl = sum((t.get("pnl") or 0) for t in trades)
        n = len(trades)
        reasons = {}
        for t in trades:
            k = str(t.get("exit_reason", "?")).upper()
            reasons[k] = reasons.get(k, 0) + 1
        tp = sum(v for k, v in reasons.items() if k.startswith("TP"))
        sl = sum(v for k, v in reasons.items() if k == "SL")
        be = sum(v for k, v in reasons.items() if "BE" in k or "BREAKEVEN" in k)
        tr = sum(v for k, v in reasons.items() if "TRAIL" in k)
        wr = f"{(wins / n * 100):.0f}%" if n else "—"
        print(f"{name:<32}{n:>7}{wins:>4}{n - wins:>4}{wr:>7}{pnl:>10.2f}{tp:>7}{sl:>7}{be:>7}{tr:>7}")

    print("\nSame data, same signals, same costs — only the exit config differs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
