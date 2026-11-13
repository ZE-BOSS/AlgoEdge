"""
tests/test_tv_mirror.py

The strategies that mirror a TradingView Pine script (T-Mean Reversion,
Zero Loss Boom & Crash v3.2) and the engine pieces that make the mirror exact:
value-based ("notional") sizing, the minimum-lot round-up a Pine script asks
for, the Pine calendar exit, and M5 bars settling same-bar stop/target ties
where MT5 has no M1 history.
"""

from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd
import pytest

from backend.risk import position_sizer as ps


@pytest.fixture
def deriv_specs(monkeypatch):
    specs = {
        "US Tech 100": dict(volume_min=0.1, volume_max=50.0, volume_step=0.01, contract_size=1.0,
                            tick_value=0.01, tick_size=0.01, digits=2, point=0.01),
        "Boom 1000 Index": dict(volume_min=0.2, volume_max=50.0, volume_step=0.01, contract_size=1.0,
                                tick_value=0.0001, tick_size=0.0001, digits=4, point=0.0001),
    }
    for k, v in specs.items():
        monkeypatch.setitem(ps._MT5_SYMBOL_CACHE, k, dict(v, stops_level_points=0, spread_points=0.0,
                                                          source="MT5_CACHE"))
    ps._symbol_info_cache.clear()
    yield specs
    ps._symbol_info_cache.clear()


def test_notional_lot_size_is_value_over_price(deriv_specs):
    # $10,000 x 1.0 at 27,000 with $1 per point per lot -> 0.37 lots
    assert ps.notional_lot_size(10_000.0, 27_000.0, "US Tech 100", use_live_mt5=False) == pytest.approx(0.37)
    # $1,000 is 0.037 lots: below Deriv's 0.1 minimum, refused (never rounded up)
    assert ps.notional_lot_size(1_000.0, 27_000.0, "US Tech 100", use_live_mt5=False) == 0.0
    assert ps.get_last_sizing_diagnostics()["refused_reason"] == "below_broker_min_lot"


def test_open_risk_is_capped_for_value_sized_trades(deriv_specs):
    full = ps.calculate_risk_dollars(0.37, 27_000.0, 18_900.0, "US Tech 100", use_live_mt5=False)
    assert full == pytest.approx(0.37 * 8_100.0)
    assert ps.open_risk_dollars(0.37, 27_000.0, 18_900.0, "US Tech 100",
                                {"open_risk_cap_dollars": 100.0}) == pytest.approx(100.0)
    assert ps.open_risk_dollars(0.37, 27_000.0, 18_900.0, "US Tech 100", {}) == pytest.approx(full)


def _risk_engine():
    from backend.risk.engine import RiskEngine
    cfg = {"risk_per_trade_pct": 1.0, "max_daily_drawdown_pct": 10.0, "max_weekly_drawdown_pct": 30.0,
           "max_concurrent_positions": 5, "max_positions_per_symbol": 1, "max_daily_trades": 20,
           "tp_count": 1, "tp1_rr": 40.0, "min_rr": 0.1, "max_risk_hard_cap_pct": 3.0,
           "use_live_mt5": False}
    return RiskEngine(cfg)


def test_risk_engine_sizes_t_mean_by_value_not_by_its_crash_stop(deriv_specs):
    eng = _risk_engine()
    sig = {"symbol": "US Tech 100", "direction": "BUY", "entry_price": 27_000.0,
           "stop_loss": 27_000.0 * 0.7, "take_profit": 27_000.0 * 13.0, "timeframe": "H1",
           "metadata": {"strategy_id": "RQ_TMeanReversion_v1", "size_notional_x": 1.0}}
    ok, reason, tps = eng.evaluate_signal(sig, 10_000.0)
    assert ok, reason
    assert sum(t.volume for t in tps) == pytest.approx(0.37)
    # the circuit breaker books the 1% budget, not 30% of a crash stop
    assert sig["metadata"]["open_risk_cap_dollars"] == pytest.approx(100.0)


def test_min_lot_round_up_only_when_asked_and_within_the_hard_cap(deriv_specs):
    eng = _risk_engine()
    base = {"symbol": "Boom 1000 Index", "direction": "SELL", "entry_price": 15_000.0,
            "stop_loss": 15_060.0, "take_profit": 12_600.0, "timeframe": "M5"}
    # 1% of $500 = $5 over a 60-point stop -> 0.083 lots, below the 0.2 minimum
    ok, _, _ = eng.evaluate_signal(dict(base, metadata={"strategy_id": "X"}), 500.0)
    assert not ok
    sig = dict(base, metadata={"strategy_id": "ZeroLossBoomCrash_v32", "round_up_to_min_lot": True})
    ok, reason, tps = _risk_engine().evaluate_signal(sig, 500.0)
    assert ok, reason
    assert sum(t.volume for t in tps) == pytest.approx(0.2)
    # the minimum lot would risk $12 = 2.4% of $500: inside the 3% hard cap. At
    # $300 it is 4% - over the cap, still refused.
    ok, _, _ = _risk_engine().evaluate_signal(
        dict(base, metadata={"strategy_id": "ZeroLossBoomCrash_v32", "round_up_to_min_lot": True}), 300.0)
    assert not ok


def _h1(start: str, days: int, price=100.0) -> pd.DataFrame:
    idx = pd.date_range(start, periods=days * 24, freq="h")
    rng = np.random.default_rng(1)
    c = price + np.cumsum(rng.normal(0, 0.2, len(idx)))
    df = pd.DataFrame({"open": c, "high": c + 0.3, "low": c - 0.3, "close": c, "tick_volume": 1}, index=idx)
    df["time"] = (df.index.astype("int64") // 10**9).astype("int64")
    return df


def test_t_mean_exit_waits_for_a_wednesday_10utc_bar_like_pine():
    from backend.core.config_schema import UserConfigV2
    from backend.strategies.strategy_roboquant.engine import RQTMeanReversion
    eng = RQTMeanReversion(UserConfigV2())
    df = _h1("2024-12-23", 20)
    entry = int(pd.Timestamp("2024-12-27 19:00").timestamp())
    pos = {"ticket": 1, "direction": "BUY", "entry_time": entry}
    # Wednesday 1 Jan 2025 has no 10:00 bar (holiday): still open on Thursday
    holiday = df[~((df.index.date == pd.Timestamp("2025-01-01").date()) & (df.index.hour < 23))]
    upto_thu = holiday[holiday.index <= "2025-01-02 12:00"]
    assert eng.on_position_bar("US Tech 100", "H1", upto_thu, pos) is None
    # ...and closes at the next Wednesday's 10:00 bar
    upto_next_wed = holiday[holiday.index <= "2025-01-08 10:00"]
    act = eng.on_position_bar("US Tech 100", "H1", upto_next_wed, pos)
    assert act is not None and act.action == "CLOSE"
    # a bot that missed that one bar still closes on the next one
    later = holiday[holiday.index <= "2025-01-08 13:00"]
    assert eng.on_position_bar("US Tech 100", "H1", later, pos).action == "CLOSE"


def test_t_mean_signal_carries_value_sizing():
    from backend.core.config_schema import UserConfigV2
    from backend.strategies.strategy_roboquant.engine import RQTMeanReversion
    eng = RQTMeanReversion(UserConfigV2())
    df = _h1("2025-01-01", 20)
    # make Friday 3 Jan red, then ask at Friday 10 Jan 18:00
    fri = df.index.date == pd.Timestamp("2025-01-03").date()
    df.loc[fri, "close"] = df.loc[fri, "open"].iloc[0] - 5.0
    window = df[df.index <= "2025-01-10 18:00"]
    sig = asyncio.run(eng.on_bar("US Tech 100", "H1", window))
    assert sig is not None and sig.direction == "BUY"
    assert sig.metadata["size_notional_x"] == 1.0
    assert sig.stop_loss == pytest.approx(sig.entry_price * 0.70)


def test_zero_loss_signal_matches_the_pine_rules():
    from backend.strategies.strategy_zero_loss.engine import indicators, signal_at
    from backend.strategies.strategy_zero_loss.params import ZeroLossBoomCrashParams
    p = ZeroLossBoomCrashParams()
    n = 300
    t = pd.date_range("2026-01-01", periods=n, freq="5min")
    # a rally into the top of the range, then three falling closes and a bar
    # that opens above the EMA30 and closes below it
    c = np.r_[np.linspace(100, 110, n - 6), [110.5, 110.3, 110.0, 109.7, 109.4, 104.0]]
    o = np.r_[c[0], c[:-1]]
    o[-1] = 112.0
    df = pd.DataFrame({"open": o, "high": np.maximum(o, c) + 0.05, "low": np.minimum(o, c) - 0.05,
                       "close": c}, index=t)
    ind = indicators(df, p)
    side, tag = signal_at(ind, n - 1, p, "BOOM")
    assert side == "SELL" and tag == "SELL video"
    # direction switched off -> no signal
    p.boom_sell = False
    assert signal_at(ind, n - 1, p, "BOOM")[0] is None
    # on Crash the same candle is the spike-side sell
    p.boom_sell = True
    assert signal_at(ind, n - 1, p, "CRASH") == ("SELL", "SELL spike")


def test_m5_backfills_intrabar_where_m1_history_ends(monkeypatch):
    from backend.api.routes import backtest as route
    from backend.mt5.data_fetcher import DataFetcher

    def frame(start, periods, freq):
        idx = pd.date_range(start, periods=periods, freq=freq)
        return pd.DataFrame({"time": (idx.astype("int64") // 10**9).astype("int64"),
                             "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0})
    m5 = frame("2026-01-01", 12 * 24 * 10, "5min")         # ten days of M5
    m1 = frame("2026-01-08", 60 * 24 * 3, "1min")          # M1 only for the last three

    async def fake(cls, symbol, tf, start, end):
        return m5 if tf == "M5" else m1
    monkeypatch.setattr(DataFetcher, "get_data_range", classmethod(fake))
    out = asyncio.run(route._with_m5_backfill("X", "M15", m1, pd.Timestamp("2026-01-01"),
                                              pd.Timestamp("2026-01-10")))
    assert out["time"].is_monotonic_increasing
    assert int(out["time"].min()) == int(m5["time"].min())
    assert (out["time"] >= int(m1["time"].min())).sum() == len(m1)   # M1 kept where it exists
    # an M5 strategy gains nothing from M5 bars
    assert asyncio.run(route._with_m5_backfill("X", "M5", m1, pd.Timestamp("2026-01-01"),
                                               pd.Timestamp("2026-01-10"))) is m1


def _floor_engine(tp_count=1, **extra):
    from backend.risk.engine import RiskEngine
    cfg = {"risk_per_trade_pct": 1.0, "max_daily_drawdown_pct": 50.0, "max_weekly_drawdown_pct": 80.0,
           "max_concurrent_positions": 5, "max_positions_per_symbol": 1, "max_daily_trades": 20,
           "tp_count": tp_count, "tp1_rr": 2.0, "tp2_rr": 4.0, "tp3_rr": 6.0, "tp_splits": [40, 30, 30],
           "min_rr": 0.1, "max_risk_hard_cap_pct": 3.0, "use_live_mt5": False, "min_lot_floor": True, **extra}
    return RiskEngine(cfg)


def test_min_lot_floor_trades_the_minimum_whatever_the_balance(deriv_specs):
    """13 Nov: "do the same thing for main bot trades" - a trade too small for
    the account goes in at the broker minimum instead of being refused."""
    base = {"symbol": "Boom 1000 Index", "direction": "SELL", "entry_price": 15_000.0,
            "stop_loss": 15_060.0, "take_profit": 12_600.0, "timeframe": "M5", "metadata": {"strategy_id": "X"}}
    # $300: the 0.2 minimum risks $12 = 4%, over the 3% hard cap - refused without the floor...
    ok, _, _ = _floor_engine(min_lot_floor=False).evaluate_signal(dict(base, metadata={"strategy_id": "X"}), 300.0)
    assert not ok
    # ...traded at the minimum with it, even on $50 (24% risk)
    for bal in (300.0, 50.0):
        ok, reason, tps = _floor_engine().evaluate_signal(dict(base, metadata={"strategy_id": "X"}), bal)
        assert ok, reason
        assert sum(t.volume for t in tps) == pytest.approx(0.2)


def test_min_lot_floor_keeps_every_tp_leg_at_the_minimum(deriv_specs):
    base = {"symbol": "Boom 1000 Index", "direction": "SELL", "entry_price": 15_000.0,
            "stop_loss": 15_060.0, "take_profit": 12_600.0, "timeframe": "M5", "metadata": {"strategy_id": "X"}}
    ok, reason, tps = _floor_engine(tp_count=3).evaluate_signal(dict(base, metadata={"strategy_id": "X"}), 1_000.0)
    assert ok, reason
    assert [t.volume for t in tps] == pytest.approx([0.2, 0.2, 0.2])          # 3 legs, none dropped
    # a big account is sized exactly as without the floor
    a = _floor_engine(tp_count=3).evaluate_signal(dict(base, metadata={"strategy_id": "X"}), 1e6)
    b = _floor_engine(tp_count=3, min_lot_floor=False).evaluate_signal(dict(base, metadata={"strategy_id": "X"}), 1e6)
    assert [t.volume for t in a[2]] == pytest.approx([t.volume for t in b[2]])


def test_min_lot_floor_is_on_in_the_app_settings():
    from backend.core.config_schema import RiskParams
    assert RiskParams().min_lot_floor is True
