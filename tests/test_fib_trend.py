"""
FibTrend_v1 (research/fib, 2026-10-25): the live path must trade exactly what a
backtest trades, and the rule must never use a swing before it is confirmed.

  * live (LiveBarFeed: capped fetches, a scan clock with an outage) finds the
    same signals, on the same bars, at the same prices, as the backtest feed;
  * the trailing stop computed from a backtest's position slice equals the one
    computed from a live fetch;
  * the H4 structure does not depend on where the M15 window starts;
  * the missed-entry and void rules, and the wiring into config/UI.
"""

import asyncio
import types
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backend.core.config_schema import UserConfigV2
from backend.strategies.bar_feed import BarFeed, LiveBarFeed
from backend.strategies.registry import get_strategy, list_strategies
from backend.strategies.strategy_fib import core
from backend.strategies.strategy_fib.params import FibTrendParams

SID = "FibTrend_v1"
SYMBOL = "Step Index 200"


def _m15(n: int = 16_000, seed: int = 3) -> pd.DataFrame:
    """M15 bars: trending regimes (so the trend filter passes) with a slow swing
    on top (so legs form and pull back to 0.5)."""
    rng = np.random.default_rng(seed)
    drift = np.repeat(rng.choice([-0.15, 0.15], size=n // 3000 + 1), 3000)[:n]
    close = (10_000 + np.cumsum(rng.normal(drift, 2.0))
             + 80 * np.sin(np.arange(n) * 2 * np.pi / 400))
    open_ = np.r_[close[0], close[:-1]]
    wick = np.abs(rng.normal(0, 1.0, n))
    idx = pd.date_range("2025-01-06 00:00", periods=n, freq="15min")
    return pd.DataFrame({"open": open_, "high": np.maximum(open_, close) + wick,
                         "low": np.minimum(open_, close) - wick, "close": close,
                         "tick_volume": np.full(n, 100.0), "spread": np.full(n, 2.0)}, index=idx)


def _key(s):
    return (s.direction, round(float(s.entry_price), 6), round(float(s.stop_loss), 6))


async def _backtest(df):
    eng = get_strategy(SID)(UserConfigV2())
    eng.is_backtesting = True
    feed = BarFeed(eng, SYMBOL)
    feed.bind({"M15": df})
    out = {}
    for i in range(300, len(df)):
        s = await feed.step(i)
        if s:
            out[df.index[i - 1]] = _key(s)        # step(i) decides on the bar closed at i-1
    return out


async def _live(df, first_scan_bar):
    eng = get_strategy(SID)(UserConfigV2())
    feed = LiveBarFeed(eng, SYMBOL)
    found = {}
    times = df.index
    outage = range(first_scan_bar + 1500, first_scan_bar + 1530)   # 7.5 hours offline
    for k in range(first_scan_bar, len(df)):
        if k in outage:
            continue
        # the scan sees bars up to k, the last one still forming, and fetches
        # only what LiveBarFeed asks for (capped at MAX_FETCH_BARS)
        need = feed.fetch_count("M15", now_s=times[k].timestamp() + 30)
        visible = df.iloc[max(0, k + 1 - need):k + 1]
        res = await feed.advance({"M15": visible})
        assert not res.history_gap
        if res.signal:
            found[visible.index[-2]] = _key(res.signal)
        for bar_time, s in res.missed:
            # stepped late (after the outage): the backtest saw it on time
            found[pd.Timestamp(bar_time) - pd.Timedelta(minutes=15)] = _key(s)
    return found


def test_live_finds_exactly_the_backtest_signals():
    df = _m15()
    bt = asyncio.run(_backtest(df))
    assert len(bt) >= 5, f"fixture produced only {len(bt)} signals; the comparison would be empty"
    first = 10_500                                   # live starts with a full capped fetch behind it
    live = asyncio.run(_live(df, first))
    bt_after = {t: k for t, k in bt.items() if t >= df.index[first]}
    assert bt_after, "no backtest signals after live started"
    assert live == bt_after


def test_trail_is_the_same_from_a_backtest_slice_and_a_live_fetch():
    df = _m15()
    eng = get_strategy(SID)(UserConfigV2())
    pbw = eng.POSITION_BAR_WINDOW
    rng = np.random.default_rng(5)
    checked = 0
    for i in rng.integers(6_000, len(df) - 2, size=40):
        entry_time = df.index[i - int(rng.integers(20, 400))]
        pos = {"ticket": 1, "direction": "BUY" if rng.random() < 0.5 else "SELL", "entry_time": entry_time}
        bt_slice = df.iloc[max(0, i - (pbw - 1)):i + 1]                    # backtester: bar i has closed
        fetched = df.iloc[max(0, i + 2 - (max(60, pbw + 5))):i + 2]         # live: the fetch ends with the forming bar
        live_slice = fetched.iloc[:-1]
        a = eng.on_position_bar(SYMBOL, "", bt_slice, pos)
        b = eng.on_position_bar(SYMBOL, "M15", live_slice, pos)
        assert (a is None) == (b is None)
        if a is not None:
            assert a.action == b.action == "MODIFY_SL"
            assert a.new_sl == pytest.approx(b.new_sl, abs=1e-9)
            checked += 1
    assert checked >= 20


def test_h4_structure_does_not_depend_on_where_the_window_starts():
    df = _m15()
    p = FibTrendParams()
    eng = get_strategy(SID)(UserConfigV2())
    n = eng.h4_bars_needed()
    t = pd.DatetimeIndex(df.index).as_unit("s").asi8
    a = df.to_numpy()
    end = 15_000
    results = []
    for extra in (0, 7, 15, 300):
        lo = end - eng.WINDOW_BARS["M15"] - extra
        H = core.h4_bars(t[lo:end], a[lo:end, 0], a[lo:end, 1], a[lo:end, 2], a[lo:end, 3],
                         int(t[end - 1]) + core.M15, n)
        assert H is not None
        results.append([(s["conf_time"], s["d"], round(s["entry"], 9)) for s in core.setups(H, p)])
    assert all(r == results[0] for r in results)


def _h4(prices):
    """H4 dict from a list of (high, low, close)."""
    h, l, c = (np.array(x, dtype=float) for x in zip(*prices))
    return {"t": np.arange(len(h)) * core.H4 + 1_700_000_000 // core.H4 * core.H4, "h": h, "l": l, "c": c}


def _one_leg(pullback_before_confirm: bool):
    """A swing low of 90 at bar 45, an up leg to a swing high of 201 at bar 59,
    then the bars that confirm it - the first dipping to 140 (below the 0.5
    level, 145.5) if `pullback_before_confirm`."""
    bars = [(101, 99, 100)] * 50
    bars[45] = (96, 90, 92)
    bars += [(100 + 10 * i + 1, 100 + 10 * i - 1, 100 + 10 * i) for i in range(1, 11)]   # high 201 at bar 59
    dip = 140 if pullback_before_confirm else 185
    bars += [(195, dip, 190)] + [(194, 186, 190)] * 9
    return _h4(bars)


def test_a_pullback_before_the_leg_is_confirmed_is_a_missed_entry():
    p = FibTrendParams(trend_filter=False, swing_bars=5, min_leg_atr=1.0)
    ok = [s for s in core.setups(_one_leg(False), p) if s["d"] > 0]
    missed = [s for s in core.setups(_one_leg(True), p) if s["d"] > 0]
    assert ok and ok[-1]["entry"] == pytest.approx(201 - 0.5 * 111)
    assert ok[-1]["stop"] == pytest.approx(201 - 0.786 * 111)
    assert ok[-1]["conf_time"] == int(_one_leg(False)["t"][59 + 5]) + core.H4
    assert not missed


def test_setup_is_void_once_price_runs_past_the_leg_end_first():
    s = {"d": 1, "entry": 150.0, "end0": 200.0, "conf_time": 0, "expires": 10**9}
    t = np.arange(5) * core.M15
    # bar 2 makes a new high, bar 4 then trades back to the entry: void
    h = np.array([190, 195, 201, 190, 180.0])
    l = np.array([185, 188, 190, 170, 149.0])
    assert not core.triggered(s, t, h, l, "fib")
    # without the new high, the same touch triggers
    h2 = np.array([190, 195, 199, 190, 180.0])
    assert core.triggered(s, t, h2, l, "fib")
    # and only on the FIRST touching bar
    assert not core.triggered(s, np.r_[t, 5 * core.M15], np.r_[h2, 160.0], np.r_[l, 148.0], "fib")


def test_signals_never_use_an_unconfirmed_swing():
    df = _m15()
    eng = get_strategy(SID)(UserConfigV2())
    eng.is_backtesting = True
    feed = BarFeed(eng, SYMBOL)
    feed.bind({"M15": df})

    async def run():
        n = 0
        for i in range(300, len(df)):
            s = await feed.step(i)
            if s:
                bar_close = df.index[i - 1].timestamp() + core.M15
                md = s.metadata
                assert md["leg_confirmed_time"] <= bar_close
                # the end swing is K H4 bars before confirmation
                assert md["leg_end_time"] + (eng.params.swing_bars + 1) * core.H4 <= md["leg_confirmed_time"]
                n += 1
        return n
    assert asyncio.run(run()) > 0


def test_wired_into_config_backtest_and_ui():
    from backend.api.routes.backtest import STRATEGY_PARAM_SECTION
    from backend.core.config_schema import UserConfigV2 as Cfg
    from backend.strategies.strategy_defaults import (
        NO_MEASURED_TARGET, get_strategy_defaults, get_synth_slot_params,
    )
    assert SID in list_strategies()
    assert STRATEGY_PARAM_SECTION[SID] == "fib_trend"
    cfg = Cfg.from_dict({"fib_trend": {"entry_mode": "immediate", "nonsense": 1}})
    assert cfg.fib_trend.entry_mode == "immediate"
    assert SID in NO_MEASURED_TARGET and get_strategy_defaults(SID)["tp1_rr"] >= 10
    assert get_synth_slot_params("XAUUSD", SID) == {"entry_mode": "immediate", "stop_level": 0.5, "swing_bars": 3}
    assert get_synth_slot_params("Volatility 25 Index", SID) == {}
    root = Path(__file__).resolve().parents[1]
    assert f"['{SID}'," in (root / "frontend/src/components/slotSpec.js").read_text(encoding="utf-8")
    from backend.core.schema_introspection import build_full_schema
    fields = [f for f in build_full_schema() if f.get("group") == "fib_trend"]
    assert {f["key"].split(".", 1)[1] for f in fields} >= {"entry_mode", "entry_level", "stop_level", "trail_atr_multiple"}


def test_immediate_entry_fires_after_a_session_break():
    """Indices/FX/gold have no bar ending exactly on some H4 boundaries (daily
    break, weekend). The leg must still be entered, on the first bar past it."""
    conf = 10 * core.H4
    s = {"d": 1, "entry": 0.0, "end0": 0.0, "conf_time": conf, "expires": conf + 30 * core.H4}
    # last bar before the break opens 2h before the boundary; trading resumes 1h after it
    before = np.array([conf - 2 * core.H4 // 4 - core.M15 * k for k in range(3, 0, -1)])
    after = conf + 3600
    assert not core.triggered(s, before, before * 0, before * 0, "immediate")
    t = np.r_[before, after]
    assert core.triggered(s, t, t * 0, t * 0, "immediate")
    # ...and only once
    t2 = np.r_[t, after + core.M15]
    assert not core.triggered(s, t2, t2 * 0, t2 * 0, "immediate")
    # a bar ending exactly on the boundary still fires
    t3 = np.array([conf - 2 * core.M15, conf - core.M15])
    assert core.triggered(s, t3, t3 * 0, t3 * 0, "immediate")
