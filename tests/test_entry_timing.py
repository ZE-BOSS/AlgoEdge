"""
A backtest entry must land where live's would: at the first price after the
signal bar closes, i.e. the NEXT bar's open.

The engine fills a signal on the first bar whose open is strictly after
`sig["time"]`. So the route must stamp a signal with the bar that produced it.
It used to stamp the following bar's open, which put every entry one bar late —
measured on ORB_v1/GBPJPY: +21.7R booked against +32.0R for the same setups
filled at live's price.
"""

import numpy as np
import pandas as pd

from backend.backtester.engine import BacktestEngine

T0 = 1_767_225_600  # 2026-01-01 00:00 UTC


def _bars(n=12, step=3600):
    t = np.arange(n) * step + T0
    # a clean uptrend so a long entered at any bar's open reaches its target
    close = 1.1000 + np.arange(n) * 0.0010
    return pd.DataFrame(
        {"time": t, "open": close - 0.0002, "high": close + 0.0004,
         "low": close - 0.0006, "close": close},
        index=pd.to_datetime(t, unit="s"),
    )


def _run(df, sig_time):
    cfg = {"risk_per_trade_pct": 1.0, "min_rr": 1.0, "tp_count": 1, "tp1_rr": 2.0,
           "tp_splits": "100", "be_mode": "NONE", "trail_method_tp1": "NONE", "trail_mode": "NONE",
           "spread_pips": 0.0, "commission_per_lot": 0.0, "slippage_pips": 0.0,
           "exit_slippage_pips": 0.0, "stops_level_pips": 0.0, "swap_long_per_lot_per_day": 0.0,
           "swap_short_per_lot_per_day": 0.0, "stop_fill_model": "OFF", "simulate_wicks": False,
           "max_concurrent_positions": 5, "max_daily_trades": 20, "max_daily_drawdown_pct": 50.0,
           "max_weekly_drawdown_pct": 90.0, "max_margin_utilisation_pct": 100.0}
    sig = {"symbol": "EURUSD", "direction": "BUY", "time": int(sig_time),
           "entry_price": float(df["close"].iloc[3]), "stop_loss": float(df["close"].iloc[3]) - 0.0020,
           "take_profit": float(df["close"].iloc[3]) + 0.0040, "timeframe": "H1",
           "confluence_score": 70, "metadata": {"size_modifier": 1.0}}
    return BacktestEngine(cfg).run(df, [sig], 10_000.0)


def test_signal_fills_at_the_open_of_the_bar_after_its_own():
    df = _bars()
    signal_bar = 3
    res = _run(df, df["time"].iloc[signal_bar])
    assert res["trades"], "no trade was opened"
    assert res["trades"][0]["entry_price"] == float(df["open"].iloc[signal_bar + 1])


def test_stamping_the_following_bar_would_cost_one_bar():
    """Guards the actual defect: stamping bar i+1 fills at bar i+2."""
    df = _bars()
    res = _run(df, df["time"].iloc[4])
    assert res["trades"][0]["entry_price"] == float(df["open"].iloc[5])


def test_route_and_headless_runner_stamp_the_signal_bar():
    for path in ("backend/api/routes/backtest.py", "scripts/run_app_backtest.py"):
        src = open(path, encoding="utf-8").read()
        assert "_signal_bar_time" in src, f"{path} no longer stamps the signal's own bar"
        assert '"time": _signal_bar_time' in src, f"{path} stamps something else on the signal"
