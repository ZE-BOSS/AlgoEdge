"""max_hold_bars: the research resolvers' time exit (synth_research.MAX_HOLD),
booked through on_position_bar by both backtesters and by live."""

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from backend.core.config_schema import UserConfigV2
from backend.strategies.registry import get_strategy

CASES = [("SpikeFade_v1", "synth"), ("TrendDrift_v1", "synth"),
         ("HTFFVGFlip_v1", "htf_fvg_flip"), ("BiasIFVG_v1", "bias_ifvg")]
T0 = 1_767_225_600  # 2026-01-01 00:00 UTC


def _candles(times):
    idx = pd.to_datetime(np.asarray(times, dtype=np.int64), unit="s")
    return pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0}, index=idx)


def _engine(sid, block, hold):
    cfg = UserConfigV2()
    setattr(getattr(cfg, block), "max_hold_bars", hold)
    return get_strategy(sid)(cfg)


@pytest.mark.parametrize("sid,block", CASES)
def test_off_by_default(sid, block):
    eng = get_strategy(sid)(UserConfigV2())
    assert not eng.LIVE_POSITION_EXITS
    times = T0 + 300 * np.arange(400)
    assert eng.on_position_bar("X", "M5", _candles(times), {"entry_time": T0}) is None


@pytest.mark.parametrize("sid,block", CASES)
def test_closes_on_the_bar_max_hold_after_the_entry_bar(sid, block):
    eng = _engine(sid, block, 288)
    assert eng.LIVE_POSITION_EXITS and eng.POSITION_BAR_WINDOW >= 289
    times = T0 + 300 * np.arange(300)
    pos = {"ticket": 7, "entry_time": T0}
    # the resolver exits at close[e + 288]: bar index 288 when the entry bar is 0
    assert eng.on_position_bar("X", "M5", _candles(times[:288]), pos) is None
    act = eng.on_position_bar("X", "M5", _candles(times[:289]), pos)
    assert act is not None and act.action == "CLOSE" and act.close_reason == "MAX_HOLD" and act.ticket == 7


def test_live_fill_inside_its_bar_and_datetime_entry():
    eng = _engine("RangeBreakout_v1", "synth", 10)
    times = T0 + 300 * np.arange(20)
    entry = dt.datetime(2026, 1, 1, 0, 0, 4)  # filled 4 s into bar 0, naive UTC
    assert eng.on_position_bar("X", "M5", _candles(times[:10]), {"entry_time": entry}) is None
    assert eng.on_position_bar("X", "M5", _candles(times[:11]), {"entry_time": entry}) is not None


def test_counts_bars_not_hours_across_a_weekend_gap():
    eng = _engine("TrendDrift_v1", "synth", 10)
    times = T0 + 300 * np.arange(20)
    times[5:] += 2 * 86400  # market shut for two days after bar 4
    pos = {"entry_time": int(times[0])}
    assert eng.on_position_bar("X", "M5", _candles(times[:10]), pos) is None
    assert eng.on_position_bar("X", "M5", _candles(times[:11]), pos) is not None
