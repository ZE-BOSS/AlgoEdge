"""AlphaVantiq research presets: wiring, the rules on built scenarios, live = backtest."""
import asyncio

import numpy as np
import pandas as pd
import pytest

from backend.core.config_schema import AV_SECTIONS, UserConfigV2
from backend.strategies.bar_feed import BarFeed
from backend.strategies.registry import get_strategy, list_strategies
from backend.strategies.strategy_av.level_pa import LEVEL_PRESETS
from backend.strategies.strategy_av.zone_flow import ZONE_PRESETS
from backend.strategies.strategy_defaults import get_strategy_defaults

ALL = {**ZONE_PRESETS, **LEVEL_PRESETS}


def test_every_preset_is_wired():
    from backend.api.routes.backtest import STRATEGY_PARAM_SECTION
    from backend.core.schema_introspection import build_full_schema
    groups = {x["group"] for x in build_full_schema()}
    have = set(list_strategies())
    cfg = UserConfigV2()
    for sid, (attr, pcls, _label) in ALL.items():
        assert sid in have
        eng = get_strategy(sid)(cfg)
        assert eng.PARAMS_ATTR == attr and isinstance(eng.params, pcls)
        assert getattr(cfg, attr) is eng.params
        assert STRATEGY_PARAM_SECTION[sid] == attr and attr in AV_SECTIONS and attr in groups
        d = get_strategy_defaults(sid)
        assert d["tp_count"] == 1 and d["be_mode"] == "NONE" and d["min_rr"] == 0.8
    with open("frontend/src/components/slotSpec.js") as fh:
        js = fh.read()
    assert all(f"'{sid}'" in js for sid in ALL)


def test_saved_params_load_and_unknown_keys_are_dropped():
    c = UserConfigV2.from_dict({"av_oil_trend_engulf": {"target": "r3", "old_key": 1}})
    assert c.av_oil_trend_engulf.target == "r3" and c.av_oil_trend_engulf.timeframe == "M5"


# -- built scenarios ---------------------------------------------------------------


def _frame(o, h, l, c, start="2026-03-02 00:00", minutes=15, flow=None):  # noqa: E741
    idx = pd.date_range(start, periods=len(c), freq=f"{minutes}min")
    f = pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "tick_volume": np.full(len(c), 100.0)}, index=idx)
    f["time"] = idx.as_unit("s").asi8
    if flow is not None:
        for k, v in flow.items():
            f[k] = v
    return f


def _quiet(n, px=100.0, seed=0):
    rng = np.random.default_rng(seed)
    c = px + np.cumsum(rng.normal(0, 0.02, n))
    o = np.r_[c[0], c[:-1]]
    return o, np.maximum(o, c) + 0.1, np.minimum(o, c) - 0.1, c


def _demand_then_retest(n=8 * 96):
    """Quiet days, a base candle at bar B, an up impulse, a drift back into the base and
    on bar T an engulfing candle that closes back out of it."""
    o, h, l, c = (x.copy() for x in _quiet(n))  # noqa: E741
    B = n - 40
    o[B], c[B], h[B], l[B] = 100.0, 99.95, 100.05, 99.9          # the base (a small red bar)
    for j in range(1, 4):                                          # impulse: 3 green bars, +1.5 each
        o[B + j] = c[B + j - 1]
        c[B + j] = o[B + j] + 1.5
        h[B + j], l[B + j] = c[B + j] + 0.05, o[B + j] - 0.05
    top = c[B + 3]
    for j in range(B + 4, n - 3):                                  # drift back down, above the zone
        o[j] = c[j - 1]
        c[j] = max(top - (j - B - 3) * 0.15, 100.4)
        h[j], l[j] = max(o[j], c[j]) + 0.03, min(o[j], c[j]) - 0.03
    T = n - 2
    o[T - 1], c[T - 1] = 100.35, 100.2                             # red bar above the zone
    h[T - 1], l[T - 1] = 100.4, 100.15
    o[T], c[T], l[T], h[T] = 100.1, 100.45, 99.95, 100.5           # dips into 99.9-100.0, engulfs
    o[T + 1] = c[T + 1] = 100.45
    h[T + 1], l[T + 1] = 100.5, 100.4
    return (o, h, l, c), B, T


def _flow(n, delta_h2_sign=1.0):
    return {"of_vol": np.full(n, 100.0), "delta": np.zeros(n), "delta_h2": np.full(n, delta_h2_sign * 5.0),
            "big_buy": np.zeros(n), "big_sell": np.zeros(n), "vwap": np.full(n, np.nan)}


async def _signal_at(sid, frame, i, params=None):
    eng = get_strategy(sid)(UserConfigV2())
    if params:
        for k, v in params.items():
            setattr(eng.params, k, v)
    eng.is_backtesting = True
    return await eng.on_bar("XAUUSD", eng.params.timeframe, frame.iloc[:i + 1])


def test_zone_engulf_with_delta_flip_buys_the_retest():
    (o, h, l, c), B, T = _demand_then_retest()  # noqa: E741
    f = _frame(o, h, l, c, flow=_flow(len(c)))
    p = {"context": "origin_zone", "target": "r2"}
    s = asyncio.run(_signal_at("AV_GoldenEngulfFlip_v1", f, T, p))
    assert s is not None and s.direction == "BUY"
    assert s.stop_loss < s.metadata["zone_lo"] and s.stop_loss < 99.95  # beyond the zone and the wick
    assert s.metadata["zone"] == "origin" and s.metadata["rl_rr"] == pytest.approx(2.0)
    assert all(type(v) in (str, int, float) for v in s.metadata.values() if v is not None)
    assert s.take_profit - s.entry_price == pytest.approx(2.0 * (s.entry_price - s.stop_loss))
    # one bar earlier there is nothing
    assert asyncio.run(_signal_at("AV_GoldenEngulfFlip_v1", f, T - 1, p)) is None


def test_zone_needs_order_flow_when_the_preset_does():
    (o, h, l, c), B, T = _demand_then_retest()  # noqa: E741
    p = {"context": "origin_zone"}
    no_flow = _frame(o, h, l, c)                                   # MT5 bars: no flow columns
    assert asyncio.run(_signal_at("AV_GoldenEngulfFlip_v1", no_flow, T, p)) is None
    against = _frame(o, h, l, c, flow=_flow(len(c), delta_h2_sign=-1.0))
    assert asyncio.run(_signal_at("AV_GoldenEngulfFlip_v1", against, T, p)) is None


def test_zone_session_filter():
    (o, h, l, c), B, T = _demand_then_retest()  # noqa: E741
    f = _frame(o, h, l, c, flow=_flow(len(c)))
    hr = f.index[T].hour + f.index[T].minute / 60
    outside = "ny_only" if not (13.5 <= hr < 20) else "london_ny"
    if outside == "london_ny" and 7 <= hr < 20:
        pytest.skip("scenario bar falls inside both sessions")
    assert asyncio.run(_signal_at("AV_GoldenEngulfFlip_v1", f, T, {"context": "origin_zone", "session": outside})) is None


def test_level_doji_at_previous_day_low():
    """Day 1 sets a low; on day 2 price comes down to it and prints a doji -> buy."""
    n1, n2 = 96 * 14, 40
    o, h, l, c = (x.copy() for x in _quiet(n1 + n2, px=200.0, seed=3))  # noqa: E741
    day_start = n1 - 96                                           # the last full day before the test day
    low = l[day_start:n1].min() - 1.0
    l[day_start + 50] = low                                       # that day's low
    for j in range(n1, n1 + n2 - 2):                              # next day trades well above it
        o[j] = c[j] = low + 2.0
        h[j], l[j] = low + 2.1, low + 1.9
    T = n1 + n2 - 2
    o[T - 1] = c[T - 1] = low + 1.0
    h[T - 1], l[T - 1] = low + 1.2, low + 0.8                     # previous bar entirely above the level
    o[T], c[T], h[T], l[T] = low + 0.30, low + 0.32, low + 0.6, low - 0.4   # doji through the level
    f = _frame(o, h, l, c)
    s = asyncio.run(_signal_at("AV_NasdaqPrevDayDoji_v1", f, T))
    assert s is not None and s.direction == "BUY"
    assert s.metadata["level_kind"] == "prev_day" and s.stop_loss < low - 0.4
    assert s.metadata["rl_rr"] == pytest.approx(1.0)


# -- the same signals live (window per bar) and in a backtest (prepared history) ----


def _walk(minutes: int, days: int, seed: int = 7) -> pd.DataFrame:
    idx = pd.date_range("2026-03-02", periods=days * 1440 // minutes, freq=f"{minutes}min")
    idx = idx[idx.dayofweek < 5]
    rng = np.random.default_rng(seed)
    steps = rng.standard_t(3, len(idx)) * np.sqrt(minutes) * 0.4
    close = 2000 + np.cumsum(steps)
    opn = np.r_[close[0], close[:-1]]
    wick = np.abs(rng.normal(0, 1, len(idx))) * np.sqrt(minutes) * 0.3
    vol = rng.integers(50, 400, len(idx)).astype(float)
    f = pd.DataFrame({"open": opn, "high": np.maximum(opn, close) + wick, "low": np.minimum(opn, close) - wick,
                      "close": close, "tick_volume": vol, "of_vol": vol,
                      "delta": rng.normal(0, 40, len(idx)), "delta_h2": rng.normal(0, 25, len(idx)),
                      "big_buy": rng.poisson(3, len(idx)).astype(float),
                      "big_sell": rng.poisson(3, len(idx)).astype(float)}, index=idx)
    f["vwap"] = (f["high"] + f["low"] + f["close"]) / 3
    f["time"] = idx.as_unit("s").asi8
    return f


async def _run(sid, frame, backtest: bool, last: int):
    eng = get_strategy(sid)(UserConfigV2())
    eng.params.context, eng.params.session = "none", "all_day"     # loosest preset: more signals to compare
    eng.is_backtesting = backtest
    tf = eng.params.timeframe
    feed = BarFeed(eng, "XAUUSD")
    feed.bind({tf: frame})
    out = []
    for i in range(len(frame) - last, len(frame)):
        s = await feed.step(i)
        if s is not None:
            out.append((i, s.direction, round(s.stop_loss, 6), round(s.take_profit, 6)))
    return out


@pytest.mark.parametrize("sid", ["AV_SilverAbsorbFlip_v1", "AV_SPFreshDoji_v1", "AV_OilTrendEngulf_v1",
                                 "AV_DAXRetestRejection_v1"])
def test_live_and_backtest_paths_give_the_same_signals(sid):
    eng = get_strategy(sid)(UserConfigV2())
    tf = eng.params.timeframe
    minutes = {"M5": 5, "M15": 15}[tf]
    frame = _walk(minutes, 16 if tf == "M15" else 10)
    last = 160
    bt = asyncio.run(_run(sid, frame, True, last))
    live = asyncio.run(_run(sid, frame, False, last))
    assert bt == live
    _SEEN[sid] = len(bt)


_SEEN: dict = {}


def test_the_parity_runs_compared_real_signals():
    if len(_SEEN) < 4:
        pytest.skip("run with the parity tests")
    assert sum(_SEEN.values()) >= 3, _SEEN
