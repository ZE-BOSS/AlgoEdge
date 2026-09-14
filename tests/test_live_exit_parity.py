"""
Live break-even and trailing must move a trade's stops exactly where a backtest
of that trade moves them.

Both backtest engines are run on the same bars with the same signal; every
RiskEngine.manage_open_position decision they take is recorded. The live
replay (risk/exit_replay.replay_stops, what position_manager now calls) is then
run over the same closed bars and must take the same decisions, bar for bar,
and end with the same stops — including the TP1 break-even cascade.
"""

import numpy as np
import pandas as pd
import pytest

from backend.backtester.engine import BacktestEngine
from backend.backtester.portfolio_engine import PortfolioBacktestEngine
from backend.risk.engine import RiskEngine
from backend.risk.exit_replay import LegState, atr_series, replay_stops

ZERO_COSTS = {"commission_per_lot": 0.0, "slippage_pips": 0.0, "exit_slippage_pips": 0.0, "spread_pips": 0.0,
              "stops_level_pips": 0.0, "swap_long_per_lot_per_day": 0.0, "swap_short_per_lot_per_day": 0.0,
              "stop_fill_model": "OFF"}
RISK = {**ZERO_COSTS, "risk_per_trade_pct": 1.0, "min_rr": 0.5, "max_risk_hard_cap_pct": 3.0,
        "tp_count": 2, "tp1_rr": 1.0, "tp2_rr": 12.0,
        "be_mode": "EITHER", "be_trigger_rr": 0.8, "be_buffer_pips": 1.0, "be_spread_multiple": 0.0,
        "trail_method_tp1": "NONE", "trail_method_tp2": "ATR_TRAIL", "atr_trail_multiplier_tp2": 2.0,
        "trail_mode": "RR", "trail_trigger_rr": 1.2, "trail_step_pips": 0.5,
        "max_daily_drawdown_pct": 100.0, "max_weekly_drawdown_pct": 100.0}
T0 = 1_767_571_200  # 2026-01-05 00:00 UTC, a Monday


def _bars(n=420, seed=3):
    rng = np.random.default_rng(seed)
    drift = np.r_[np.zeros(70), np.full(n - 70, 0.00012)]
    close = 1.1000 + np.cumsum(drift + rng.normal(0, 0.00018, n)) + np.arange(n) * 1e-9  # unique closes
    open_ = np.r_[1.1000, close[:-1]]
    wick = np.abs(rng.normal(0, 0.00012, n))
    t = T0 + 300 * np.arange(n)
    return pd.DataFrame({"time": t, "open": open_, "high": np.maximum(open_, close) + wick,
                         "low": np.minimum(open_, close) - wick, "close": close, "spread": np.zeros(n),
                         "tick_volume": np.full(n, 100.0)})


def _signal(df, i=65):
    return {"symbol": "EURUSD", "_cache_key": "EURUSD", "direction": "BUY", "time": int(df["time"][i]),
            "entry_price": float(df["close"][i]), "stop_loss": float(df["close"][i]) - 0.0020,
            "take_profit": float(df["close"][i]) + 0.0020, "timeframe": "M5", "confluence_score": 80,
            "metadata": {}}


def _record(monkeypatch):
    log = []
    real = RiskEngine.manage_open_position

    def wrapped(self, position, current_price, atr_value=0.0, swing_points=None):
        acts = real(self, position, current_price, atr_value=atr_value, swing_points=swing_points)
        for a in acts:
            if a.get("action") == "MODIFY_SL":
                log.append((round(float(current_price), 8), int(position.get("tp_level", 1)),
                            round(float(a["new_sl"]), 8), a.get("reason")))
        return acts

    monkeypatch.setattr(RiskEngine, "manage_open_position", wrapped)
    return log


def _replay_against(trades, df, log):
    log.clear()
    legs = sorted(trades, key=lambda t: t["tp_level"])
    entry = float(legs[0]["entry_price"])
    initial = float(legs[0].get("initial_stop_loss") or legs[0]["original_sl"])
    states = [LegState(level=int(t["tp_level"]), stop_loss=initial,
                       closed_at=int(t["exit_time"]) if t.get("exit_reason") != "END_OF_DATA" else None,
                       closed_by_target=str(t.get("exit_reason", "")).startswith("TP"))
              for t in legs]
    out = replay_stops(direction="BUY", entry_price=entry, initial_stop=initial, legs=states,
                       times=df["time"].to_numpy(), high=df["high"].to_numpy(), low=df["low"].to_numpy(),
                       close=df["close"].to_numpy(), entry_bar_time=int(legs[0]["entry_time"]),
                       risk_config=RISK, symbol="EURUSD", spread_pips=0.0)
    return out


def _check(trades, df, engine_log):
    assert len(trades) == 2, [t.get("exit_reason") for t in trades]
    assert any(r == "BREAKEVEN" for *_, r in engine_log) or any(t.get("be_applied") for t in trades), \
        "scenario must exercise break-even"
    assert any(r == "TRAIL" for *_, r in engine_log), "scenario must exercise trailing"
    decisions = list(engine_log)
    live_log: list = []
    out = _replay_against(trades, df, live_log)
    return decisions, out


def test_replay_takes_the_single_symbol_engines_decisions(monkeypatch):
    df = _bars()
    log = _record(monkeypatch)
    res = BacktestEngine(RISK).run(df, [_signal(df)], 10_000.0, df, df, None, None, None)
    trades = res.get("trades") or []
    engine_log = list(log)
    assert len(trades) == 2, res.get("rejection_funnel")
    assert any(r == "TRAIL" for *_, r in engine_log), "scenario must exercise trailing"
    log.clear()
    out = _replay_against(trades, df, log)
    assert log == engine_log, "live replay decided break-even/trailing differently from engine.py"
    for t in trades:
        if t["exit_reason"] != "END_OF_DATA":
            assert out[t["tp_level"]].stop_loss == pytest.approx(t["stop_loss"], abs=1e-9), \
                f"TP{t['tp_level']} ends on a different stop"


def test_replay_takes_the_portfolio_engines_decisions(monkeypatch):
    df = _bars()
    log = _record(monkeypatch)
    res = PortfolioBacktestEngine({**RISK, "use_strategy_exit_defaults": False}).run(
        {"EURUSD": df}, {"EURUSD": [_signal(df)]}, 10_000.0)
    trades = res.get("trades") or []
    engine_log = list(log)
    assert len(trades) == 2, res.get("rejection_funnel")
    log.clear()
    _replay_against(trades, df, log)
    assert log == engine_log, "live replay decided break-even/trailing differently from portfolio_engine.py"


def test_single_and_portfolio_engines_now_manage_identically(monkeypatch):
    df = _bars()
    log = _record(monkeypatch)
    BacktestEngine(RISK).run(df, [_signal(df)], 10_000.0, df, df, None, None, None)
    single = list(log)
    log.clear()
    PortfolioBacktestEngine({**RISK, "use_strategy_exit_defaults": False}).run(
        {"EURUSD": df}, {"EURUSD": [_signal(df)]}, 10_000.0)
    assert log == single


def test_atr_is_the_mean_of_the_bars_before():
    h = np.array([2, 3, 4, 5, 6.0] * 4)
    l = h - 1
    c = h - 0.5
    a = atr_series(h, l, c, period=3)
    assert a[:3].tolist() == [0, 0, 0]
    prev = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(abs(h - prev), abs(l - prev)))
    assert a[7] == pytest.approx(tr[4:7].mean())


def test_live_management_runs_the_replay_under_the_strategy_exits():
    import inspect

    from backend.risk.live_risk_config import build_live_risk_config
    from backend.core.config_schema import UserConfigV2
    from backend.services import bot_service, position_manager
    from backend.strategies.strategy_defaults import get_strategy_defaults

    src = inspect.getsource(position_manager)
    assert "replay_stops(" in src and "build_live_risk_config(config, trade.strategy_id)" in src
    assert "risk.be_trigger_rr" not in src, "the old per-tick break-even must be gone"
    assert "build_live_risk_config(config, strategy_id)" in inspect.getsource(bot_service)

    cfg = UserConfigV2()
    rc, applied = build_live_risk_config(cfg, "SpikeFade_v1")
    for k, v in get_strategy_defaults("SpikeFade_v1").items():
        if k != "session_filter_enabled" and hasattr(cfg.risk, k):
            assert rc[k] == v and applied[k] == v
