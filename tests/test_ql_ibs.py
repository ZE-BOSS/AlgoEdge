"""QL_IBS_v1: IBS mean reversion at the US cash close (quant lab, 21 Nov 2026)."""

from __future__ import annotations

import asyncio
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from backend.core.config_schema import UserConfigV2
from backend.strategies.registry import get_strategy
from backend.strategies.strategy_roboquant.engine_quant import cash_sessions

ET = ZoneInfo("America/New_York")


def _sessions(days_ohlc):
    """M30 bars 09:30..15:30 ET for each (open, high, low, close) day, plus one 16:00 bar."""
    rows = []
    for d, (o, h, lo, c) in zip(pd.bdate_range("2026-03-02", periods=len(days_ohlc)), days_ohlc):
        times = [pd.Timestamp(d).replace(hour=9, minute=30, tzinfo=ET) + pd.Timedelta(minutes=int(30 * k)) for k in range(14)]
        path = np.linspace(o, c, 13)
        for k, t in enumerate(times):
            if k < 13:
                p0 = path[k - 1] if k else o
                p1 = path[k]
                hi = max(p0, p1) + (h - max(o, c)) * (k == 3)
                lw = min(p0, p1) - (min(o, c) - lo) * (k == 6)
                rows.append((int(t.timestamp()), p0, hi, lw, p1))
            else:                                   # 16:00 after-hours bar
                rows.append((int(t.timestamp()), c, c, c, c))
    return pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"])


def _engine(sma=3, thr=0.2):
    cfg = UserConfigV2()
    cfg.ql_ibs.sma_days = sma
    cfg.ql_ibs.ibs_below = thr
    return get_strategy("QL_IBS_v1")(cfg)


def _upto_last_bar_of(df, day_index):
    """window ending with the 15:30 bar of the day_index-th session."""
    return df.iloc[: day_index * 14 + 13].reset_index(drop=True)


# rising closes (above SMA) with a weak close on day 4 and 5, strong on day 6
DAYS = [(100, 101, 99, 100.5), (100.5, 102, 100, 101.8), (101.8, 103, 101, 102.6),
        (102.6, 104, 102.4, 103.6),        # IBS (103.6-102.4)/1.6 = 0.75: no signal
        (103.6, 106, 103.0, 103.2),        # IBS 0.067 < 0.2, close 103.2 > SMA3: signal
        (103.2, 106, 103.1, 103.5),        # IBS 0.14, close > SMA3 103.43: signal repeats -> hold
        (103.5, 108, 103.4, 107.5)]        # IBS 0.9: exit


def test_cash_sessions_match_the_lab_definition():
    df = _sessions(DAYS)
    days, closes, sig = cash_sessions(df, 0.2, 3)
    assert len(days) == 7 and closes[-1] == 107.5
    assert list(sig) == [False, False, False, False, True, True, False]


def test_signal_only_at_the_cash_close_and_hold_then_exit():
    df = _sessions(DAYS)
    eng = _engine()
    s = asyncio.run(eng.on_bar("US Tech 100", "M30", _upto_last_bar_of(df, 4)))
    assert s is not None and s.direction == "BUY" and s.metadata["size_notional_x"] == 1.0
    assert s.stop_loss == s.entry_price * 0.90
    # same day, an earlier bar: nothing
    assert asyncio.run(eng.on_bar("US Tech 100", "M30", df.iloc[: 4 * 14 + 10])) is None
    entry_time = int(_upto_last_bar_of(df, 4).time.iloc[-1])
    pos = {"ticket": 7, "direction": "BUY", "entry_time": entry_time}
    assert eng.on_position_bar("US Tech 100", "", _upto_last_bar_of(df, 5), pos) is None      # repeats: hold
    act = eng.on_position_bar("US Tech 100", "", _upto_last_bar_of(df, 6), pos)
    assert act is not None and act.action == "CLOSE" and act.close_reason == "QL_IBS_EXIT"
    # mid-day bars never close it
    assert eng.on_position_bar("US Tech 100", "", df.iloc[: 6 * 14 + 8], pos) is None


def test_no_signal_below_the_trend_filter():
    falling = [(110 - k, 111 - k, 108 - k, 108.1 - k) for k in range(7)]   # weak closes, below SMA
    df = _sessions(falling)
    assert asyncio.run(_engine().on_bar("US Tech 100", "M30", _upto_last_bar_of(df, 6))) is None
