"""The synth research harness must fire on exactly the bars, in exactly the
directions, that the live strategy engines fire — on the 500-bar window the app
hands them — or its confluence numbers describe a different strategy."""

import asyncio

import numpy as np
import pandas as pd
import pytest

from backend.analytics import synth_research as sr
from backend.core.config_schema import SynthParams, UserConfigV2
from backend.strategies.registry import get_strategy
from backend.strategies.windows import window_bars


def _bars(n=1400, seed=11):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-03-03", periods=n, freq="5min", tz="UTC")
    regime = np.repeat(rng.normal(0, 0.03, n // 150 + 1), 150)[:n]
    jumps = np.where(rng.random(n) < 0.01, rng.normal(0, 3.0, n), 0.0)
    close = 1000 + np.cumsum(regime + rng.normal(0, 0.4, n) + jumps)
    open_ = np.r_[close[0], close[:-1]] + rng.normal(0, 0.05, n)
    high = np.maximum(open_, close) + np.abs(rng.normal(0.2, 0.15, n)) + np.abs(np.minimum(jumps, 0)) * 0
    low = np.minimum(open_, close) - np.abs(rng.normal(0.2, 0.15, n))
    t = idx.as_unit("s").asi8.astype(np.int64)
    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                       "tick_volume": np.ones(n), "time": t}, index=idx.tz_localize(None))
    arrays = {"time": t, "open": open_, "high": high, "low": low, "close": close, "spread": np.zeros(n)}
    return df, arrays


CASES = [
    ("SpikeFade_v1", {"spike_k_atr": 2.0}),
    ("RangeRevert_v1", {"revert_k_atr": 1.5}),
    ("RangeBreakout_v1", {"breakout_lookback": 20}),
    ("TrendDrift_v1", {"min_adx_to_trade": 20}),
    ("TrendDrift_v1", {"min_adx_to_trade": 0}),
]


@pytest.mark.parametrize("sid,params", CASES)
def test_research_fires_where_the_engine_fires(sid, params):
    df, arrays = _bars()
    cfg = UserConfigV2()
    synth = SynthParams(max_trades_per_day=10_000, max_daily_risk_pct=1e9)
    for k, v in params.items():
        if k == "min_adx_to_trade":
            synth.require_adx = v > 0
            synth.min_adx_to_trade = int(v) if v > 0 else 20
        else:
            setattr(synth, k, v)
    cfg.synth = synth
    eng = get_strategy(sid)(cfg)
    win = window_bars("M5", eng)

    engine_hits = {}
    for i in range(1, len(df)):
        sl = df.iloc[max(0, i - win):i]
        sig = asyncio.run(eng.on_bar("TEST", "M5", sl))
        if sig:
            engine_hits[i - 1] = 1 if sig.direction == "BUY" else -1

    frame = sr.build_frame("TEST", arrays)
    d = sr.raw_signals(frame, sid, params)
    # the research frame uses the whole history; compare only once the engine's
    # 500-bar window is full so both see converged EMAs
    start = win + 5
    research_hits = {int(i): int(d[i]) for i in np.flatnonzero(d) if start <= i < len(df) - 1}
    engine_hits = {i: v for i, v in engine_hits.items() if start <= i < len(df) - 1}
    assert engine_hits, f"{sid}: engine produced no signals to compare"
    assert research_hits == engine_hits


def test_indicators_match_the_engine_frame():
    df, arrays = _bars()
    eng = get_strategy("TrendDrift_v1")(UserConfigV2())
    i = 1200
    fr = eng._frame(df.iloc[i - 500:i])
    f = sr.build_frame("TEST", arrays)
    j = i - 1
    for col, arr in (("atr", f.atr), ("adx", f.adx), ("ema_f", f.ema_f), ("ema_s", f.ema_s)):
        assert fr[col].iloc[-1] == pytest.approx(arr[j], rel=1e-6, abs=1e-9), col


def test_resolver_books_stops_targets_gaps_and_costs():
    n = 10
    o = np.array([100, 100, 100, 100, 100, 100, 100, 100, 100, 100], dtype=float)
    h = np.array([100, 101, 100.5, 103, 100, 100, 100, 100, 100, 100], dtype=float)
    lo = np.array([100, 99.5, 99.4, 100, 100, 100, 100, 100, 100, 100], dtype=float)
    c = o.copy()
    spread = np.full(n, 0.1)
    # long from bar 1 open 100, stop 1.0 -> 99, target 2R -> 102: bar 3 high 103 hits target
    r, ex, why = sr.resolve(o, h, lo, c, spread, np.array([1]), np.array([1]), np.array([1.0]),
                            np.array([2.0]), 288, 0.0, 0.0)
    assert why[0, 0] == 2 and ex[0, 0] == 3 and r[0, 0] == pytest.approx(2.0 - 0.1)
    # same trade with a 0.5 stop -> 99.5: bar 1 low 99.5 touches it on the entry bar
    r, ex, why = sr.resolve(o, h, lo, c, spread, np.array([1]), np.array([1]), np.array([0.5]),
                            np.array([2.0]), 288, 0.0, 0.3)
    assert why[0, 0] == 1 and ex[0, 0] == 1
    # overshoot 0.3 x 0.5 = 0.15 but the bar only reached the stop exactly: clamped to 0
    assert r[0, 0] == pytest.approx(-1.0 - 0.1 / 0.5)


GATED = [
    ("SpikeFade_v1", {"spike_k_atr": 2.0}, {"require_trend_with": True, "time_filter": "LONDON"}, ("trend_with", "london")),
    ("RangeRevert_v1", {"revert_k_atr": 1.5}, {"adx_filter": "RANGE", "require_candle_confirm": True}, ("adx_range", "candle_confirm")),
    ("RangeBreakout_v1", {"breakout_lookback": 20}, {"require_vol_high": True, "side": "long"}, ("vol_high",)),
    ("TrendDrift_v1", {"min_adx_to_trade": 0}, {"require_strong_close": True, "time_filter": "NEWYORK"}, ("strong_close", "newyork")),
]


@pytest.mark.parametrize("sid,params,gates,flags", GATED)
def test_engine_confluence_params_match_research_flags(sid, params, gates, flags):
    df, arrays = _bars(n=1500, seed=21)
    cfg = UserConfigV2()
    synth = SynthParams(max_trades_per_day=10_000, max_daily_risk_pct=1e9, **gates)
    for k, v in params.items():
        if k == "min_adx_to_trade":
            synth.require_adx = v > 0
        else:
            setattr(synth, k, v)
    cfg.synth = synth
    eng = get_strategy(sid)(cfg)
    win = window_bars("M5", eng)
    hits = {}
    for i in range(1, len(df)):
        sig = asyncio.run(eng.on_bar("TEST", "M5", df.iloc[max(0, i - win):i]))
        if sig:
            hits[i - 1] = 1 if sig.direction == "BUY" else -1
    f = sr.build_frame("TEST", arrays)
    d = sr.raw_signals(f, sid, params)
    idx = np.flatnonzero(d)
    feats = sr.features(f, idx, d[idx].astype(np.int64))
    keep = np.ones(idx.size, dtype=bool)
    for fl in flags:
        keep &= feats[fl]
    if gates.get("side") == "long":
        keep &= d[idx] > 0
    start = 600
    want = {int(i): int(d[i]) for i in idx[keep] if start <= i < len(df) - 1}
    got = {i: v for i, v in hits.items() if start <= i < len(df) - 1}
    assert want, f"{sid}: no gated research signals to compare"
    assert got == want
