"""The three strategies shipped on 2026-09-25 must trade what was measured.

TrendBreakout_v1 was measured by
`scripts/run_app_form_check.py` in the form the engine can actually trade: a
CLOSE beyond the level rather than a resting order at it, a fill at the next
bar's open, real stops, and an exit the STRATEGY owns rather than one
`trailing_manager` owns.

That last part is where a silent divergence would live. RiskParams' ATR_TRAIL
trails a multiple of the strategy timeframe's ATR from the extreme PRICE; these
trail a multiple of the DAILY ATR from the extreme CLOSE. On M15 those are two
different stops and only one of them was measured, so the rule travels with the
strategy through `on_position_bar`. These tests hold each engine to the reference
arithmetic, bar by bar, on the live window size — including across a DST change,
because both session strategies resolve New York through pytz.
"""

import asyncio

import numpy as np
import pandas as pd
import pytest
import pytz

from backend.core.config_schema import UserConfigV2
from backend.strategies.core.daily_atr import daily_atr
from backend.strategies.registry import get_strategy
from backend.strategies.strategy_trend.params import TrendBreakoutParams
from backend.strategies.windows import window_bars

NY = pytz.timezone("America/New_York")


# ── fixtures ────────────────────────────────────────────────────────────────
def _bars(freq, start, periods, seed, weekdays_only=True, drift=0.0, jump_p=0.02,
          base=100.0, unit=1.0):
    """Synthetic OHLC on a UTC index, with occasional jumps so channels break.

    `base`/`unit` rescale it to a real instrument's price level, which matters as
    soon as the RISK ENGINE is in the loop: position_sizer refuses to size a
    symbol it has no MT5 data and no profile for, so an engine-level test has to
    use a symbol the profiles know and prices that symbol could plausibly have.
    """
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=periods, freq=freq, tz="UTC")
    if weekdays_only:
        idx = idx[idx.dayofweek < 5]
    n = len(idx)
    step = rng.normal(drift, 0.08, n) + np.where(rng.random(n) < jump_p, rng.normal(0, 0.9, n), 0)
    close = base + np.cumsum(step) * unit
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + np.abs(rng.normal(0.04, 0.03, n)) * unit
    low = np.minimum(open_, close) - np.abs(rng.normal(0.04, 0.03, n)) * unit
    t = idx.as_unit("s").asi8
    return pd.DataFrame(
        {"time": t.astype(np.int64), "open": open_, "high": high, "low": low,
         "close": close, "spread": np.zeros(n),
         "tick_volume": rng.integers(50, 500, n).astype(float)},
        index=pd.to_datetime(t, unit="s"),
    )


def _ref_daily_atr(df, days=14):
    return daily_atr(df["time"].to_numpy(np.int64), df["high"].to_numpy(float),
                     df["low"].to_numpy(float), df["close"].to_numpy(float), days)


def _engine(strategy_id, attr, params):
    cfg = UserConfigV2()
    setattr(cfg, attr, params)
    return get_strategy(strategy_id)(cfg)


def _trend(**kw):
    return _engine("TrendBreakout_v1", "trend_breakout", TrendBreakoutParams(**kw))


def _scan(eng, df, timeframe, window, start=0):
    """Every signal the engine emits, driven one closed bar at a time."""
    out = []
    for i in range(start, len(df)):
        sig = asyncio.run(eng.on_bar("TESTSYM", timeframe, df.iloc[max(0, i + 1 - window):i + 1]))
        if sig is not None:
            out.append((i, sig))
    return out


# ── TrendBreakout_v1 ────────────────────────────────────────────────────────
# 3 days of M15 keeps the fixture small; the channel and ATR windows scale with it
TREND_KW = dict(channel_days=3, atr_days=3, trail_lookback_bars=20)
# always the engine's OWN declared window: a hand-written one that is a day short
# makes the strategy decline every bar, which is a test of nothing
TREND_WINDOW = window_bars("M15", _engine("TrendBreakout_v1", "trend_breakout",
                                          TrendBreakoutParams(**TREND_KW)))


def test_trend_signals_exactly_where_a_close_leaves_the_channel():
    df = _bars("15min", "2025-02-03", 30 * 96, seed=11)
    eng = _trend(**TREND_KW)
    win = int(TREND_KW["channel_days"]) * 96
    high, low, close = (df[c].to_numpy(float) for c in ("high", "low", "close"))

    want = []
    for i in range(win + 1, len(df)):
        # the reference is deliberately written from the rule, not from the engine
        sl = df.iloc[max(0, i + 1 - TREND_WINDOW):i + 1]
        if _ref_daily_atr(sl, TREND_KW["atr_days"]) is None:
            continue
        lo_i = max(0, i - win)
        if close[i] > high[lo_i:i].max():
            want.append((i, "BUY"))
        elif close[i] < low[lo_i:i].min():
            want.append((i, "SELL"))

    got = [(i, s.direction) for i, s in _scan(eng, df, "M15", TREND_WINDOW, start=win + 1)]
    assert len(want) >= 10, "fixture too quiet to prove anything"
    assert got == want


def test_trend_stop_is_the_measured_multiple_of_the_daily_atr():
    df = _bars("15min", "2025-02-03", 30 * 96, seed=12)
    eng = _trend(**TREND_KW)
    sigs = _scan(eng, df, "M15", TREND_WINDOW, start=4 * 96)
    assert sigs, "no signals"
    for i, sig in sigs:
        atr = _ref_daily_atr(df.iloc[max(0, i + 1 - TREND_WINDOW):i + 1], TREND_KW["atr_days"])
        assert atr is not None
        assert abs(sig.entry_price - sig.stop_loss) == pytest.approx(2.0 * atr, rel=1e-12)
        assert sig.metadata["atr_val"] == pytest.approx(atr, rel=1e-12)


def test_trend_target_is_a_placeholder_far_beyond_anything_observed():
    """The measured rule has no target. A reachable one truncates the right tail
    the whole strategy lives on, so the shipped one must not be reachable: the
    best single trade in five years was +5.9R."""
    df = _bars("15min", "2025-02-03", 30 * 96, seed=13)
    sigs = _scan(_trend(**TREND_KW), df, "M15", TREND_WINDOW, start=4 * 96)
    assert sigs
    for _, sig in sigs:
        risk = abs(sig.entry_price - sig.stop_loss)
        reward = abs(sig.take_profit - sig.entry_price)
        assert reward / risk >= 10.0, f"target at {reward / risk:.1f}R is reachable"


@pytest.mark.parametrize("side,expect", [("long", {"BUY"}), ("short", {"SELL"})])
def test_trend_side_filter(side, expect):
    df = _bars("15min", "2025-02-03", 30 * 96, seed=14)
    sigs = _scan(_trend(side=side, **TREND_KW), df, "M15", TREND_WINDOW, start=4 * 96)
    assert sigs
    assert {s.direction for _, s in sigs} == expect


def test_trend_trail_is_the_best_close_since_entry_less_the_atr_multiple():
    df = _bars("15min", "2025-02-03", 30 * 96, seed=15)
    eng = _trend(**TREND_KW)
    sigs = _scan(eng, df, "M15", TREND_WINDOW, start=4 * 96)
    assert sigs
    i0, sig = next((i, s) for i, s in sigs if s.direction == "BUY")
    entry_t = pd.Timestamp(df.index[i0 + 1])
    pbw = eng.POSITION_BAR_WINDOW
    lookback = int(TREND_KW["trail_lookback_bars"])
    close = df["close"].to_numpy(float)

    checked = 0
    for i in range(i0 + 1, min(i0 + 60, len(df))):
        sl = df.iloc[max(0, i + 1 - pbw):i + 1]
        act = eng.on_position_bar("TESTSYM", "M15", sl,
                                  {"ticket": 1, "direction": "BUY", "entry_time": entry_t,
                                   "stop_loss": sig.stop_loss})
        atr = _ref_daily_atr(sl, TREND_KW["atr_days"])
        assert act is not None and act.action == "MODIFY_SL"
        anchor = max(i0 + 1, i - lookback + 1)      # never reaches before the entry bar
        assert act.new_sl == pytest.approx(close[anchor:i + 1].max() - 1.5 * atr, rel=1e-12)
        checked += 1
    assert checked >= 20


def test_trend_trail_anchor_never_reaches_before_the_entry_bar():
    """An anchor from before the trade would put the stop where the position never
    was — and with a long lookback that is most of the window."""
    df = _bars("15min", "2025-02-03", 30 * 96, seed=16, drift=-0.02)
    eng = _trend(channel_days=3, atr_days=3, trail_lookback_bars=5000)
    pbw = eng.POSITION_BAR_WINDOW
    i0 = 10 * 96
    entry_t = pd.Timestamp(df.index[i0])
    sl = df.iloc[max(0, i0 + 3 - pbw):i0 + 3]
    act = eng.on_position_bar("TESTSYM", "M15", sl,
                              {"ticket": 1, "direction": "BUY", "entry_time": entry_t,
                               "stop_loss": 0.0})
    atr = _ref_daily_atr(sl, 3)
    close = df["close"].to_numpy(float)
    assert act.new_sl == pytest.approx(close[i0:i0 + 3].max() - 1.5 * atr, rel=1e-12)


def test_trend_windows_are_big_enough_for_their_own_atr():
    """A window one bar short of the daily ATR makes the strategy decline every
    bar, silently, forever — the failure mode is "no signals at all"."""
    eng = _trend()
    df = _bars("15min", "2025-01-02", 60 * 96, seed=17)
    for name, n in (("WINDOW_BARS", window_bars("M15", eng)),
                    ("POSITION_BAR_WINDOW", eng.POSITION_BAR_WINDOW)):
        assert _ref_daily_atr(df.iloc[-n:], eng.params.atr_days) is not None, (
            f"{name}={n} M15 bars does not hold a {eng.params.atr_days}-day ATR")
    assert window_bars("M15", eng) > eng.params.channel_days * 96, "channel does not fit"


# ── wiring: a strategy the UI can pick must be configurable end to end ──────
def test_every_registered_strategy_is_wired_into_every_list():
    """Coverage by construction. A strategy missing from any one of these is
    selectable and silently unconfigurable: no parameter form, no saved block, or
    a slot whose parameters never reach the engine."""
    import json
    import re
    from pathlib import Path

    from backend.api.routes.backtest import STRATEGY_PARAM_SECTION
    from backend.core.config_schema import UserConfigV2
    from backend.core.schema_introspection import build_full_schema
    from backend.strategies.registry import list_strategies

    groups = {row["group"] for row in build_full_schema()}
    cfg = UserConfigV2()
    js = (Path(__file__).resolve().parents[1] / "frontend/src/components/slotSpec.js").read_text()
    block = re.search(r"STRATEGY_OPTIONS = \[(.*?)\];", js, re.S).group(1)
    ui = dict(re.findall(r"\['([^']+)',\s*'[^']*',\s*'([^']+)'\]", block))

    for sid in list_strategies():
        section = STRATEGY_PARAM_SECTION.get(sid)
        assert section, f"{sid} has no STRATEGY_PARAM_SECTION entry"
        assert section in groups, f"{sid} -> {section!r} is not a schema group"
        assert hasattr(cfg, section), f"UserConfigV2 has no {section!r} block"
        assert ui.get(sid) == section, (
            f"slotSpec.js STRATEGY_OPTIONS maps {sid} to {ui.get(sid)!r}, not {section!r}")
    assert json.dumps(sorted(ui)), "sanity"


def test_the_new_strategies_round_trip_through_from_dict():
    """A slot's parameters reach the engine only if the block survives a save."""
    from backend.core.config_schema import UserConfigV2
    cfg = UserConfigV2.from_dict({
        "trend_breakout": {"channel_days": 40, "trail_lookback_bars": 250, "bogus": 1},
        "orb": {"range_minutes": 45},
    })
    assert cfg.trend_breakout.channel_days == 40
    assert cfg.trend_breakout.trail_lookback_bars == 250
    assert cfg.orb.range_minutes == 45
    # and the engines read the block rather than their own dataclass defaults
    assert get_strategy("TrendBreakout_v1")(cfg).params.channel_days == 40
    assert get_strategy("ORB_v1")(cfg).params.range_minutes == 45


def test_measured_exits_turn_the_generic_trailing_ladder_off():
    """These strategies own their exits. If RiskParams' trailing were left on it
    would fight them with a stop measured on the wrong timeframe."""
    from backend.strategies.strategy_defaults import NO_MEASURED_TARGET, get_strategy_defaults
    for sid in ("TrendBreakout_v1",):
        d = get_strategy_defaults(sid)
        assert d["tp_count"] == 1
        assert d["tp1_rr"] >= 10.0
        assert d["be_mode"] == "NONE"
        assert d["trail_mode"] == "NONE" and d["trail_method_tp1"] == "NONE"
        assert sid in NO_MEASURED_TARGET


# -- the strategy-owned trail must reach BOTH backtesters identically ---------
#
# These are the first strategies in the book to return MODIFY_SL from
# on_position_bar. engine.py has always applied it; portfolio_engine.py did not
# until the per-slot work, and its own comment names this exact case ("a Donchian
# leg never trailed in a basket while the same leg run alone did both"). Nothing
# exercised it, so here is the thing that would have caught it.
TRAIL_RISK = {
    "commission_per_lot": 0.0, "slippage_pips": 0.0, "exit_slippage_pips": 0.0,
    "spread_pips": 0.0, "stops_level_pips": 0.0, "swap_long_per_lot_per_day": 0.0,
    "swap_short_per_lot_per_day": 0.0, "stop_fill_model": "OFF",
    "risk_per_trade_pct": 1.0, "min_rr": 0.5, "max_risk_hard_cap_pct": 3.0,
    "tp_count": 1, "tp1_rr": 20.0, "tp_splits": "100",
    "be_mode": "NONE", "trail_mode": "NONE", "trail_method_tp1": "NONE",
    "max_daily_drawdown_pct": 100.0, "max_weekly_drawdown_pct": 100.0,
    "use_strategy_exit_defaults": False,
}


def _trail_scenario(seed=4):
    """One real TrendBreakout_v1 signal on EURUSD-scaled bars, so the trail is
    what books the trade rather than the initial stop or the placeholder target."""
    df = _bars("15min", "2025-02-03", 30 * 96, seed=seed, base=1.1000, unit=0.0004)
    eng = _trend(**TREND_KW)
    sigs = _scan(eng, df, "M15", TREND_WINDOW, start=4 * 96)
    i, sig = next((i, s) for i, s in sigs if s.direction == "BUY" and i < len(df) - 200)
    signal = {"symbol": "EURUSD", "_cache_key": "EURUSD", "direction": "BUY",
              "time": int(df["time"].iloc[i]), "entry_price": sig.entry_price,
              "stop_loss": sig.stop_loss, "take_profit": sig.take_profit,
              "timeframe": "M15", "confluence_score": 80, "strategy_id": "TrendBreakout_v1",
              "metadata": dict(sig.metadata)}
    return df, signal


def test_the_strategy_trail_books_the_trade_in_the_single_symbol_engine():
    from backend.backtester.engine import BacktestEngine
    df, signal = _trail_scenario()
    res = BacktestEngine(dict(TRAIL_RISK)).run(
        df, [signal], 10_000.0, df, df, _trend(**TREND_KW), None, None)
    trades = res.get("trades") or []
    assert len(trades) == 1, res.get("rejection_funnel")
    t = trades[0]
    assert t.get("trail_applied"), "the strategy's MODIFY_SL never reached the position"
    assert t["stop_loss"] > t.get("initial_stop_loss", t["original_sl"]), "stop never ratcheted up"
    assert t["exit_reason"] != "TP_HIT", "the placeholder target should be unreachable"


def test_single_and_portfolio_engines_apply_the_strategy_trail_identically():
    from backend.backtester.engine import BacktestEngine
    from backend.backtester.portfolio_engine import PortfolioBacktestEngine
    df, signal = _trail_scenario()

    single = (BacktestEngine(dict(TRAIL_RISK)).run(
        df, [signal], 10_000.0, df, df, _trend(**TREND_KW), None, None).get("trades") or [])
    basket = (PortfolioBacktestEngine(dict(TRAIL_RISK)).run(
        {"EURUSD": df}, {"EURUSD": [signal]}, 10_000.0, None, None, None, None,
        {"EURUSD": _trend(**TREND_KW)}).get("trades") or [])

    assert len(single) == len(basket) == 1
    for key in ("exit_reason", "tp_level"):
        assert single[0][key] == basket[0][key], f"{key} differs between the two engines"
    for key in ("stop_loss", "exit_price", "entry_price"):
        assert single[0][key] == pytest.approx(basket[0][key], rel=1e-12), f"{key} differs"


def test_a_basket_without_the_strategy_never_trails_at_all():
    """Proves the assertion above is about the hook and not about the fixture: the
    same bars and the same signal, with no strategy handed to the engine, run to
    the initial stop instead."""
    from backend.backtester.portfolio_engine import PortfolioBacktestEngine
    df, signal = _trail_scenario()
    trades = PortfolioBacktestEngine(dict(TRAIL_RISK)).run(
        {"EURUSD": df}, {"EURUSD": [signal]}, 10_000.0).get("trades") or []
    assert len(trades) == 1
    assert not trades[0].get("trail_applied")


# -- the volatility-regime filter (strategies/core/vol_regime.py) ------------
#
# TrendBreakout's biggest measured improvement: trade only when the instrument's
# own 20-day realised volatility is high relative to its 60-day. Chosen on
# 2021-10 -> 2024-09 and scored unchanged after: +0.216R in-sample (n 66) ->
# +0.232R out (n 56, PF 2.89) against +0.102R -> +0.023R unfiltered.
#
# It ships OFF. These tests are about the mechanics being right either way,
# because a filter that silently blocks everything looks exactly like a strategy
# that found no setups.
def _vol_frame(daily_sigma, days=90, bars_per_day=96, seed=3):
    """Bars whose daily volatility follows `daily_sigma(day_index)`."""
    rng = np.random.default_rng(seed)
    n = days * bars_per_day
    step = np.concatenate([rng.normal(0, daily_sigma(d), bars_per_day) for d in range(days)])
    close = 100 + np.cumsum(step)
    open_ = np.r_[close[0], close[:-1]]
    t = 1_767_571_200 + 900 * np.arange(n)
    return pd.DataFrame({"time": t, "open": open_,
                         "high": np.maximum(open_, close) + 0.01,
                         "low": np.minimum(open_, close) - 0.01, "close": close,
                         "spread": np.zeros(n), "tick_volume": np.full(n, 100.0)},
                        index=pd.to_datetime(t, unit="s"))


def test_the_vol_ratio_rises_when_volatility_expands():
    from backend.strategies.core.vol_regime import vol_ratio
    calm = _vol_frame(lambda d: 0.02)
    expanding = _vol_frame(lambda d: 0.02 if d < 70 else 0.12)
    dying = _vol_frame(lambda d: 0.12 if d < 70 else 0.02)
    assert vol_ratio(calm) == pytest.approx(1.0, abs=0.35)
    assert vol_ratio(expanding) > 1.5, "a fourfold jump must read as expanding"
    assert vol_ratio(dying) < 0.7, "a fourfold collapse must read as quiet"


def test_the_ratio_excludes_the_day_in_progress():
    """A bar must not help decide its own regime — the same rule the daily ATR
    follows. A huge partial final day must not move the reading."""
    from backend.strategies.core.vol_regime import vol_ratio
    base = _vol_frame(lambda d: 0.02)
    before = vol_ratio(base)
    spiked = base.copy()
    tail = spiked.index[-40:]
    spiked.loc[tail, "close"] = spiked.loc[tail, "close"] + np.arange(40) * 5.0
    assert vol_ratio(spiked) == pytest.approx(before, rel=1e-9)


def test_too_little_history_refuses_rather_than_guesses():
    from backend.strategies.core.vol_regime import vol_ratio
    assert vol_ratio(_vol_frame(lambda d: 0.02, days=30), long_days=60) is None


def test_the_filter_is_off_by_default_and_blocks_nothing():
    cfg = UserConfigV2()
    eng = get_strategy("TrendBreakout_v1")(cfg)
    assert eng.params.vol_filter == "off"
    ok, why = eng.vol_regime_ok(_vol_frame(lambda d: 0.02))
    assert ok and why == ""
    assert eng.vol_regime_bars_needed(96) == 0, "off must not widen the window"


@pytest.mark.parametrize("mode,shape,expect", [
    ("expanding", lambda d: 0.02 if d < 70 else 0.12, True),
    ("expanding", lambda d: 0.12 if d < 70 else 0.02, False),
    ("quiet", lambda d: 0.12 if d < 70 else 0.02, True),
    ("quiet", lambda d: 0.02 if d < 70 else 0.12, False),
])
def test_each_mode_admits_only_its_own_regime(mode, shape, expect):
    cfg = UserConfigV2()
    cfg.trend_breakout = TrendBreakoutParams(vol_filter=mode)
    eng = get_strategy("TrendBreakout_v1")(cfg)
    ok, _ = eng.vol_regime_ok(_vol_frame(shape))
    assert ok is expect


def test_turning_the_filter_on_widens_the_window_to_fit_it():
    """60 days of daily vol cannot be computed from 21 days of bars, and a
    filter that quietly returns None on every bar blocks every signal forever."""
    off = get_strategy("TrendBreakout_v1")(UserConfigV2())
    cfg = UserConfigV2()
    cfg.trend_breakout = TrendBreakoutParams(vol_filter="expanding")
    on = get_strategy("TrendBreakout_v1")(cfg)
    w_off, w_on = window_bars("M15", off), window_bars("M15", on)
    assert w_on > w_off
    assert on.vol_regime_ok(_vol_frame(lambda d: 0.02, days=w_on // 96))[0] is not None


def test_the_filter_actually_gates_signals_in_on_bar():
    """End to end: the same bars produce a signal with the filter off and none
    with it set to the regime those bars are not in."""
    df, i = _boom_like_breakout()
    off = get_strategy("TrendBreakout_v1")(UserConfigV2())
    assert asyncio.run(off.on_bar("EURUSD", "M15", df)) is not None
    cfg = UserConfigV2()
    cfg.trend_breakout = TrendBreakoutParams(vol_filter="quiet", vol_quiet_below=0.01)
    strict = get_strategy("TrendBreakout_v1")(cfg)
    assert asyncio.run(strict.on_bar("EURUSD", "M15", df)) is None


def _boom_like_breakout(days=80, bars_per_day=96, seed=5):
    """A clean upward channel break on the last bar, with enough history for
    both the 20-day channel and the 60-day volatility window."""
    rng = np.random.default_rng(seed)
    n = days * bars_per_day
    close = 1.1000 + np.cumsum(rng.normal(0, 0.00008, n))
    close[-1] = close[:-1].max() + 0.0050          # a decisive break
    open_ = np.r_[close[0], close[:-1]]
    t = 1_767_571_200 + 900 * np.arange(n)
    return pd.DataFrame({"time": t, "open": open_,
                         "high": np.maximum(open_, close) + 1e-5,
                         "low": np.minimum(open_, close) - 1e-5, "close": close,
                         "spread": np.zeros(n), "tick_volume": np.full(n, 100.0)},
                        index=pd.to_datetime(t, unit="s")), n - 1
