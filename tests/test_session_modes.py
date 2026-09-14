"""ORB_v1's M5 trend form and VWAP_v1's session modes must trade exactly what the
edge lab measured: the same bar, direction and stop as the research candidate
list filtered by the same confluences, one trade per session — on the window
size the app hands them, and with a forming session exactly as live sees it."""

import asyncio

import numpy as np
import pandas as pd
import pytest

from backend.analytics import edge_lab as lab
from backend.analytics.strategy_search import Bars
from backend.core.config_schema import UserConfigV2
from backend.strategies.registry import get_strategy
from backend.strategies.strategy_orb.params import ORBParams
from backend.strategies.windows import window_bars


def _m5(days=36, seed=3):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-02-03", periods=days * 288, freq="5min", tz="UTC")
    idx = idx[idx.dayofweek < 5]
    n = len(idx)
    drift = np.repeat(rng.normal(0, 0.012, n // 400 + 1), 400)[:n]
    close = 2000 + np.cumsum(drift + rng.normal(0, 0.35, n))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + np.abs(rng.normal(0.15, 0.08, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0.15, 0.08, n))
    vol = rng.integers(40, 200, n).astype(float)
    t = idx.as_unit("s").asi8.astype(np.int64)
    df = pd.DataFrame({"time": t, "open": open_, "high": high, "low": low, "close": close, "tick_volume": vol},
                      index=pd.to_datetime(t, unit="s"))
    b = Bars("XAUUSD", "M5", t, open_, high, low, close, np.zeros(n), vol)
    return df, b


def _expected(b, family, axis, gates, session="ny", first_session=20):
    ctx = lab.build_ctx(b, session)
    cs = {"orb_break": lab.fam_orb_break, "vwap_trend": lab.fam_vwap_trend,
          "vwap_pullback": lab.fam_vwap_pullback}[family](ctx, axis)
    fam = lab.FAMILY_BY_NAME[family]
    idx = lab.pick(cs, (0, 2**40), tuple(fam.features.index(g) for g in gates), 0)
    start = ctx.sessions[first_session].open_ts
    return [(int(cs.t_entry[k]) - lab.TF, int(cs.direction[k]), float(cs.stop_dist[k]))
            for k in idx if cs.t_entry[k] - lab.TF >= start], start


def _run(eng, df, start, tf="M5"):
    w = window_bars(tf, eng)
    got = []
    t = df["time"].to_numpy()
    for i in range(int(np.searchsorted(t, start)), len(df) - 1):
        sig = asyncio.run(eng.on_bar("XAUUSD", tf, df.iloc[max(0, i + 1 - w): i + 1]))
        if sig:
            got.append((int(t[i]), 1 if sig.direction == "BUY" else -1, abs(sig.entry_price - sig.stop_loss)))
    return got


def _assert_same(got, want):
    assert len(want) >= 5, "fixture too quiet to test anything"
    assert [(a, d) for a, d, _ in got] == [(a, d) for a, d, _ in want]
    for (_, _, x), (_, _, y) in zip(got, want):
        assert x == pytest.approx(y, rel=1e-6)


@pytest.mark.parametrize("trend", [True, False])
def test_orb_m5_reproduces_the_edge_lab_breaks(trend):
    df, b = _m5(days=70, seed=5)     # one break a session at most: needs more sessions
    want, start = _expected(b, "orb_break", "native|60", ("htf_trend",) if trend else ())
    cfg = UserConfigV2()
    cfg.orb = ORBParams(session="ny", range_minutes=60, breakout_timeframe="M5", require_trend=trend, min_stop_atr=0.5)
    eng = get_strategy("ORB_v1")(cfg)
    assert eng.get_required_timeframes() == ["M5"] and window_bars("M5", eng) == 5000
    _assert_same(_run(eng, df, start), want)


@pytest.mark.parametrize("mode,family,gates", [
    ("SESSION_TREND", "vwap_trend", ("day_dir", "gap_dir", "early")),
    ("SESSION_TREND", "vwap_trend", ()),
    ("SESSION_PULLBACK", "vwap_pullback", ("gap_dir", "early")),
])
def test_vwap_session_modes_reproduce_the_edge_lab_candidates(mode, family, gates):
    df, b = _m5(days=70, seed=11)
    want, start = _expected(b, family, "native", gates)
    cfg = UserConfigV2()
    cfg.vwap.entry_mode = mode
    cfg.vwap.session_mode_session = "ny"
    cfg.vwap.session_mode_gates = list(gates)
    eng = get_strategy("VWAP_v1")(cfg)
    _assert_same(_run(eng, df, start), want)


def test_session_modes_flatten_at_the_close():
    df, _ = _m5(days=3)
    cfg = UserConfigV2()
    cfg.vwap.entry_mode = "SESSION_TREND"
    cfg.vwap.session_mode_session = "ny"
    eng = get_strategy("VWAP_v1")(cfg)
    assert eng.LIVE_POSITION_EXITS
    ny = df.index.tz_localize("UTC").tz_convert("America/New_York")
    at_noon = int(np.flatnonzero((ny.hour == 12) & (ny.minute == 0))[0])
    at_close = int(np.flatnonzero((ny.hour == 15) & (ny.minute == 55))[0])
    assert eng.on_position_bar("XAUUSD", "M5", df.iloc[: at_noon + 1], {"ticket": 1}) is None
    act = eng.on_position_bar("XAUUSD", "M5", df.iloc[: at_close + 1], {"ticket": 1})
    assert act is not None and act.close_reason == "SESSION_END"
    cfg.vwap.entry_mode = "PULLBACK_TO_VALUE"
    assert not get_strategy("VWAP_v1")(cfg).LIVE_POSITION_EXITS
