"""
The nine roboquant.dev templates ported through strategy_roboquant/replay.py
(2026-10-20).

1. Parity: the backtest path (whole history prepared once, each call a slice)
   and the live path (each call rebuilds its window) must give the SAME signals
   - the replay anchor exists for exactly this.
2. Template rules pinned on hand-built bars, so an edit cannot quietly change
   what was replicated.
"""

from __future__ import annotations

import asyncio
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from backend.core.config_schema import UserConfigV2
from backend.strategies.bar_feed import BarFeed
from backend.strategies.registry import get_strategy

ET = ZoneInfo("America/New_York")

PORTS = [
    ("RQ_FlashTriggerORB_v1", "M15"), ("RQ_ScarfaceORB_v1", "M1"), ("RQ_IVB_v1", "M5"),
    ("RQ_GigaORB_v1", "M15"), ("RQ_SigmaBreakout_v1", "M30"), ("RQ_DailyVWAP_v1", "M1"),
    ("RQ_FridayRevisit_v1", "M5"), ("RQ_M30ORB_v1", "M5"), ("RQ_SRSweep_v1", "M5"),
]
MIN = {"M1": 1, "M5": 5, "M15": 15, "M30": 30}


def _walk(minutes: int, days: int, seed: int = 3) -> pd.DataFrame:
    """A random walk with a busier US session, 23h a day, weekdays only."""
    idx = pd.date_range("2026-03-02", periods=days * 1440 // minutes, freq=f"{minutes}min")
    idx = idx[(idx.dayofweek < 5) & (idx.hour != 21)]
    rng = np.random.default_rng(seed)
    et_h = idx.tz_localize("UTC").tz_convert(ET).hour
    vol = np.where((et_h >= 9) & (et_h < 16), 3.0, 1.0) * np.sqrt(minutes)
    steps = rng.normal(0, 1, len(idx)) * vol
    close = 20000 + np.cumsum(steps)
    opn = np.r_[close[0], close[:-1]]
    spread = np.abs(rng.normal(0, 1, len(idx))) * vol
    f = pd.DataFrame({"open": opn, "high": np.maximum(opn, close) + spread,
                      "low": np.minimum(opn, close) - spread, "close": close,
                      "tick_volume": rng.integers(50, 400, len(idx)).astype(float) * vol}, index=idx)
    f["time"] = idx.as_unit("s").asi8
    return f


def _d1(f: pd.DataFrame) -> pd.DataFrame:
    d = f.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last",
                              "tick_volume": "sum"}).dropna()
    d["time"] = d.index.as_unit("s").asi8
    return d


async def _run(sid, tf, frames, backtest: bool):
    eng = get_strategy(sid)(UserConfigV2())
    eng.is_backtesting = backtest
    feed = BarFeed(eng, "NDX100")
    feed.bind(frames)
    if not backtest:
        assert not getattr(eng, "_full", {}), "the live path must not use the prepared history"
    out = []
    n = len(frames[tf])
    for i in range(300, n):
        s = await feed.step(i)
        if s is not None:
            out.append((i, s.direction, round(s.stop_loss, 6), round(s.take_profit, 6)))
    return out


@pytest.mark.parametrize("sid,tf", PORTS)
def test_live_and_backtest_paths_give_the_same_signals(sid, tf):
    days = {"M1": 6, "M5": 18, "M15": 24, "M30": 30}[tf]
    f = _walk(MIN[tf], days)
    frames = {tf: f, "D1": _d1(f)}
    bt = asyncio.run(_run(sid, tf, frames, True))
    live = asyncio.run(_run(sid, tf, frames, False))
    assert bt == live
    assert all(d in ("BUY", "SELL") for _, d, _, _ in bt)


def test_the_ports_are_wired_end_to_end():
    from backend.api.routes.backtest import STRATEGY_PARAM_SECTION
    from backend.strategies.strategy_defaults import STRATEGY_DEFAULTS
    cfg = UserConfigV2()
    for sid, _ in PORTS:
        eng = get_strategy(sid)(cfg)
        assert getattr(cfg, eng.PARAMS_ATTR) is eng.params, sid
        assert STRATEGY_PARAM_SECTION[sid] == eng.PARAMS_ATTR
        assert sid in STRATEGY_DEFAULTS


# -- template rules --------------------------------------------------------------


def _bars(times_utc, o, h, lo, c, v=None):
    idx = pd.DatetimeIndex(times_utc)
    f = pd.DataFrame({"open": o, "high": h, "low": lo, "close": c,
                      "tick_volume": v if v is not None else np.full(len(idx), 100.0)}, index=idx)
    f["time"] = idx.as_unit("s").asi8
    return f


def _flat_m15(day: str, n_days: int = 4):
    idx = pd.date_range(f"{day} 00:00", periods=n_days * 96, freq="15min")
    p = np.full(len(idx), 100.0)
    return idx, p.copy(), p + 0.2, p - 0.2, p.copy()


def test_flash_trigger_trades_the_first_close_beyond_the_1430_utc_candle():
    idx, o, h, lo, c = _flat_m15("2026-03-02")
    k = idx.get_loc(pd.Timestamp("2026-03-05 14:30"))
    h[k], lo[k] = 101.0, 99.0                        # the targeted candle
    c[k + 2], h[k + 2] = 101.5, 101.6                # first close above it
    f = _bars(idx, o, h, lo, c)
    eng = get_strategy("RQ_FlashTriggerORB_v1")(UserConfigV2())
    assert asyncio.run(eng.on_bar("NDX100", "M15", f.iloc[:k + 2])) is None
    s = asyncio.run(eng.on_bar("NDX100", "M15", f.iloc[:k + 3]))
    assert s is not None and s.direction == "BUY"
    assert s.stop_loss == pytest.approx(99.0)
    assert (s.take_profit - s.entry_price) == pytest.approx(3.0 * (s.entry_price - 99.0))
    c[k + 3], h[k + 3] = 101.7, 101.8
    f = _bars(idx, o, h, lo, c)
    assert asyncio.run(eng.on_bar("NDX100", "M15", f.iloc[:k + 4])) is None, "one trade per range"


def test_sigma_breakout_flips_and_flattens_at_the_close():
    eng = get_strategy("RQ_SigmaBreakout_v1")(UserConfigV2())
    days = pd.bdate_range("2026-03-02", periods=5)
    times, o, h, lo, c = [], [], [], [], []
    for d in days:
        for m in range(0, 24 * 60, 30):
            t = pd.Timestamp(d) + pd.Timedelta(minutes=m)
            times.append(t)
    times = pd.DatetimeIndex(times)
    et = times.tz_localize("UTC").tz_convert(ET)
    base = np.full(len(times), 100.0)
    # every RTH slot moves 1 point from the open on the first four days
    rth = (et.hour * 100 + et.minute >= 930) & (et.hour * 100 + et.minute < 1600)
    close = base + np.where(rth, 1.0, 0.0)
    last = et.normalize() == pd.Timestamp(days[-1]).tz_localize(ET)
    i0 = np.flatnonzero(last & rth)[0]
    close[i0 + 1] = 103.0                             # far above open + 1.5 x 1.0
    f = _bars(times, base, np.maximum(base, close) + 0.1, np.minimum(base, close) - 0.1, close)
    s = asyncio.run(eng.on_bar("NDX100", "M30", f.iloc[:i0 + 2]))
    assert s is not None and s.direction == "BUY"
    # at/after 16:00 ET an open position is closed
    k16 = np.flatnonzero(last & (et.hour == 16) & (et.minute == 0))[0]
    # "" as both backtesters pass it: positions carry no timeframe.
    act = eng.on_position_bar("NDX100", "", f.iloc[:k16 + 1], {"ticket": 1, "direction": "BUY"})
    assert act is not None and act.action == "CLOSE"


def test_friday_revisit_shorts_monday_open_inside_fridays_range_only():
    idx = pd.date_range("2026-01-26 00:00", "2026-03-16 23:55", freq="5min")   # 7 weeks: >= 20 daily bars
    idx = idx[idx.dayofweek < 5]
    et = idx.tz_localize("UTC").tz_convert(ET)
    p = np.full(len(idx), 100.0)
    h, lo = p + 0.5, p - 0.5
    hhmm = et.hour * 100 + et.minute
    rth = (hhmm >= 930) & (hhmm < 1600)
    thu = (et.normalize() == pd.Timestamp("2026-03-12", tz=ET)) & rth
    fri = (et.normalize() == pd.Timestamp("2026-03-13", tz=ET)) & rth
    h[thu], lo[thu] = 104.0, 96.0                     # Thursday 96-104
    h[fri], lo[fri] = 103.0, 97.0                     # Friday inside it: setup valid
    f = _bars(idx, p, h, lo, p.copy())
    d1 = _d1(f)
    eng = get_strategy("RQ_FridayRevisit_v1")(UserConfigV2())
    asyncio.run(eng.on_bar("NDX100", "D1", d1[d1.index < pd.Timestamp("2026-03-16")]))
    mon = np.flatnonzero((et.normalize() == pd.Timestamp("2026-03-16", tz=ET)) & (hhmm == 930))[0]
    s = asyncio.run(eng.on_bar("NDX100", "M5", f.iloc[:mon + 1]))
    assert s is not None and s.direction == "SELL"
    assert s.stop_loss > 100.0 > s.take_profit


def test_daily_vwap_steps_on_three_minute_bars_from_an_m1_feed():
    eng = get_strategy("RQ_DailyVWAP_v1")(UserConfigV2())
    assert eng.TEMPLATE_MINUTES == 3
    f = _walk(1, 5)
    # Only an M1 bar that completes a 3-minute bar can carry a signal.
    for i in range(3000, 3010):
        t = int(f["time"].iloc[i - 1])
        tb = eng._last_template_bar(f.iloc[:i])
        assert (tb is None) == ((t // 60) % 3 != 2)
