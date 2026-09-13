"""ORB_v1 must trade exactly what the strategy search measured — same bars, same
direction, same stop — on the live window size, across a DST change."""

import asyncio
from datetime import datetime

import numpy as np
import pandas as pd
import pytest
import pytz

from backend.analytics.strategy_search import Bars, _orb
from backend.core.config_schema import UserConfigV2
from backend.strategies.registry import get_strategy
from backend.strategies.strategy_orb.params import ORBParams
from backend.strategies.windows import window_bars


def _m15(start="2025-03-17", days=21, seed=7):
    """Weekday M15 bars, UTC, with session-sized moves so ranges actually break."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=days * 96, freq="15min", tz="UTC")
    idx = idx[idx.dayofweek < 5]
    n = len(idx)
    close = 150 + np.cumsum(rng.normal(0, 0.08, n) + np.where(rng.random(n) < 0.03, rng.normal(0, 0.6, n), 0))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + np.abs(rng.normal(0.03, 0.02, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0.03, 0.02, n))
    t = idx.asi8 // 10**9
    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close},
                      index=pd.to_datetime(t, unit="s"))
    bars = Bars("GBPJPY", "M15", t.astype(np.int64), open_, high, low, close, np.zeros(n))
    return df, bars


def _engine(**params):
    cfg = UserConfigV2()
    cfg.orb = ORBParams(**params)
    return get_strategy("ORB_v1")(cfg)


@pytest.mark.parametrize("session,range_min", [("london", 60), ("ny", 30)])
def test_live_engine_reproduces_the_measured_signals(session, range_min):
    df, bars = _m15()
    expected, _ = _orb(bars, {"session": session, "range_min": range_min, "exit": 3.0, "side": "both"})
    eng = _engine(session=session, range_minutes=range_min)
    w = window_bars("M15")
    got = []
    for i in range(30, len(df)):
        sig = asyncio.run(eng.on_bar("GBPJPY", "M15", df.iloc[max(0, i + 1 - w): i + 1]))
        if sig:
            got.append((i, 1 if sig.direction == "BUY" else -1, abs(sig.entry_price - sig.stop_loss)))
    want = [(s.i, s.direction, s.stop_dist) for s in expected if s.i >= 30]
    assert len(want) >= 8, "fixture too quiet to test anything"
    assert [(i, d) for i, d, _ in got] == [(i, d) for i, d, _ in want]
    for (_, _, a), (_, _, b) in zip(got, want):
        assert a == pytest.approx(b, rel=1e-9)


def test_one_trade_per_session_even_when_both_sides_break():
    df, _ = _m15(seed=11)
    eng = _engine()
    per_day = {}
    for i in range(30, len(df)):
        sig = asyncio.run(eng.on_bar("GBPJPY", "M15", df.iloc[: i + 1]))
        if sig:
            day = df.index[i].date()
            per_day[day] = per_day.get(day, 0) + 1
    assert per_day and max(per_day.values()) == 1


def test_long_only_never_sells():
    df, _ = _m15(seed=3)
    eng = _engine(side="long")
    dirs = {s.direction for i in range(30, len(df))
            if (s := asyncio.run(eng.on_bar("GBPJPY", "M15", df.iloc[: i + 1])))}
    assert dirs <= {"BUY"}


def test_flattens_at_the_london_close_and_not_before():
    uk = pytz.timezone("Europe/London")
    eng = _engine(session="london")

    def bar_at(h, m):
        ts = int(uk.localize(datetime(2025, 7, 2, h, m)).timestamp())
        return pd.DataFrame({"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0]},
                            index=pd.to_datetime([ts], unit="s"))

    assert eng.on_position_bar("GBPJPY", "M15", bar_at(12, 0), {"ticket": 1}) is None
    act = eng.on_position_bar("GBPJPY", "M15", bar_at(16, 15), {"ticket": 1})
    assert act is not None and act.action == "CLOSE" and act.close_reason == "SESSION_END"
    assert _engine(close_at_session_end=False).on_position_bar("GBPJPY", "M15", bar_at(16, 15), {}) is None


def test_orb_is_wired_everywhere_a_strategy_must_be():
    from backend.api.routes.backtest import STRATEGY_PARAM_SECTION
    from backend.strategies.strategy_defaults import (
        SLOT_TP1_RR, get_strategy_defaults, get_synth_slot_params,
    )

    assert STRATEGY_PARAM_SECTION["ORB_v1"] == "orb"
    assert UserConfigV2.from_dict({"orb": {"session": "ny"}}).orb.session == "ny"
    assert get_strategy_defaults("ORB_v1")["tp_count"] == 1
    assert get_synth_slot_params("GBPJPY", "ORB_v1") == {
        "session": "london", "range_minutes": 60, "breakout_timeframe": "M5",
        "require_trend": True, "min_stop_atr": 0.5}
    assert get_synth_slot_params("btcusd", "ORB_v1")["session"] == "ny"
    assert SLOT_TP1_RR["BTCUSD|ORB_v1"] == 3.0
