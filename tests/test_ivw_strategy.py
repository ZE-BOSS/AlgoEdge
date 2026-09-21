"""IVW_v1 must trade exactly what the 2026-09-19 study measured: the same days, the
same direction, the same stop — fed through BarFeed, as a backtest and live feed it.

`_study_signals` is the study's breakout resolver (scratchpad ivw_study.study,
mode "breakout") reduced to its entry decision, written against the raw M5 bars
with no engine code, so the two can only agree if the engine reproduces it.
"""

import asyncio

import numpy as np
import pandas as pd
import pytest

from backend.core.config_schema import UserConfigV2
from backend.strategies.bar_feed import BarFeed
from backend.strategies.registry import get_strategy
from backend.strategies.strategy_ivw import engine as ivw
from backend.strategies.strategy_ivw.params import IVWParams

DAY = 86400


def _m5(weeks=62, seed=3):
    """FX-shaped M5 bars: a two-hour Sunday stub, then full Monday-Friday days.
    Volatility wanders in regimes and volume rises with the size of the move, so
    walls break, regimes vary and some breakouts are liquidation bubbles."""
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2024-01-07 22:00", tz="UTC")          # a Sunday
    idx = pd.date_range(start, periods=weeks * 7 * 288, freq="5min")
    keep = (idx.dayofweek < 5) | ((idx.dayofweek == 6) & (idx.hour >= 22))
    idx = idx[keep]
    n = len(idx)
    day = idx.as_unit("s").asi8 // DAY
    _, inv = np.unique(day, return_inverse=True)
    vol_of_day = np.exp(np.cumsum(rng.normal(0, 0.12, inv.max() + 1)))
    sig = 0.0004 * vol_of_day[inv]
    ret = rng.normal(0, 1, n) * sig
    jumps = rng.random(n) < 0.004
    ret[jumps] += rng.choice([-1, 1], jumps.sum()) * sig[jumps] * rng.uniform(6, 14, jumps.sum())
    close = 1.10 * np.exp(np.cumsum(ret))
    open_ = np.r_[close[0], close[:-1]]
    wick = np.abs(rng.normal(0, 0.5, n)) * sig * close
    high = np.maximum(open_, close) + wick
    low = np.minimum(open_, close) - wick
    tick_volume = np.round(100 * (1 + np.abs(ret) / sig * 0.8) * rng.uniform(0.8, 1.2, n))
    t = idx.as_unit("s").asi8
    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                       "tick_volume": tick_volume}, index=pd.to_datetime(t, unit="s"))
    return df


def _d1(m5: pd.DataFrame) -> pd.DataFrame:
    return m5.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last",
                                  "tick_volume": "sum"}).dropna()


def _study_signals(m5: pd.DataFrame, p: IVWParams):
    """(signal-bar epoch, direction, stop distance) — the study's breakout entry."""
    t = m5.index.as_unit("s").asi8
    o, h, lo, c, v = (m5[k].to_numpy(float) for k in ("open", "high", "low", "close", "tick_volume"))
    days, S_open, H, L = ivw.sessions_from_m5(t, o, h, lo)
    rng = (H - L) / S_open
    day_of = t // DAY
    out = []
    for k in range(len(days)):
        if k < max(int(p.lookback_days), 270):
            continue
        s = int(np.searchsorted(day_of, days[k], side="left"))
        e = int(np.searchsorted(day_of, days[k], side="right"))
        w = ivw.walls(S_open[:k], H[:k], L[:k], o[s], p.wall_percentile, p.lookback_days)
        if w is None:
            continue
        upper, lower = w
        reg = ivw.regime(rng[:k])
        bu = np.flatnonzero(c[s:e - 1] > upper)
        bd = np.flatnonzero(c[s:e - 1] < lower)
        bu = int(bu[0]) if bu.size else None
        bd = int(bd[0]) if bd.size else None
        if bu is not None and (bd is None or bu < bd):
            side, j = 1, s + bu
        elif bd is not None:
            side, j = -1, s + bd
        else:
            continue
        if p.side != "both" and (p.side == "long") != (side > 0):
            continue
        if p.regime_filter != "any" and reg != p.regime_filter:
            continue
        if p.require_bubble and not ivw.bubble(c, h, lo, v, j, side > 0, p.bubble_lookback,
                                               p.bubble_price_sigma, p.bubble_volume_sigma):
            continue
        width = (upper - o[s]) if side > 0 else (o[s] - lower)
        out.append((int(t[j]), side, p.stop_width_frac * width))
    return out


def _engine_signals(m5: pd.DataFrame, p: IVWParams, from_day: int):
    cfg = UserConfigV2()
    cfg.ivw = p
    eng = get_strategy("IVW_v1")(cfg)
    feed = BarFeed(eng, "EURUSD")
    feed.bind({"D1": _d1(m5), "M5": m5})
    t = m5.index.as_unit("s").asi8

    async def run():
        got = []
        for i in range(int(np.searchsorted(t // DAY, from_day)), len(m5)):
            sig = await feed.step(i)
            if sig:
                got.append((int(sig.timestamp), 1 if sig.direction == "BUY" else -1,
                            abs(sig.entry_price - sig.stop_loss)))
        return got
    return asyncio.run(run())


@pytest.fixture(scope="module")
def bars():
    return _m5()


@pytest.mark.parametrize("params", [
    IVWParams(wall_percentile=70, regime_filter="any", require_bubble=False),
    IVWParams(wall_percentile=80, regime_filter="high", require_bubble=False, side="short"),
    IVWParams(wall_percentile=70, regime_filter="any", require_bubble=True),
])
def test_engine_reproduces_the_study(bars, params):
    t = bars.index.as_unit("s").asi8
    want = _study_signals(bars, params)
    # the engine needs its D1 window (the study only its 270 prior sessions),
    # so compare from the first day both can trade
    first_day = int(t[0] // DAY) + 380
    want = [w for w in want if w[0] // DAY >= first_day]
    got = _engine_signals(bars, params, first_day)
    assert len(want) >= 3, f"fixture too quiet to test anything ({len(want)})"
    assert [(a, d) for a, d, _ in got] == [(a, d) for a, d, _ in want]
    for (_, _, a), (_, _, b) in zip(got, want):
        assert a == pytest.approx(b, rel=1e-9)


def test_sunday_stubs_are_not_sessions(bars):
    d1 = _d1(bars)
    keep = ivw.session_mask(d1)
    sunday = d1.index.dayofweek == 6
    assert sunday.any() and not keep[sunday].any()
    assert keep[~sunday][25:].all()


def test_regime_ranks_the_20_day_sum():
    quiet = np.r_[np.full(260, 0.01), np.full(20, 0.001)]
    loud = np.r_[np.full(260, 0.01), np.full(20, 0.05)]
    assert ivw.regime(quiet) == "low"
    assert ivw.regime(loud) == "high"
    assert ivw.regime(np.full(200, 0.01)) is None


def test_bubble_needs_price_and_volume_stress():
    n = 60
    c = np.full(n, 100.0) + np.sin(np.arange(n)) * 0.1
    h, lo, v = c + 0.05, c - 0.05, np.full(n, 100.0) + np.cos(np.arange(n))
    h2, v2 = h.copy(), v.copy()
    h2[-1], v2[-1] = 101.0, 500.0
    assert ivw.bubble(c, h2, lo, v2, n - 1, True)
    assert not ivw.bubble(c, h2, lo, v, n - 1, True)       # no volume stress
    assert not ivw.bubble(c, h, lo, v2, n - 1, True)       # no price stress
    assert not ivw.bubble(c, h2, lo, v2, n - 1, False)     # wrong side


def test_flat_at_day_end_and_across_a_gap():
    cfg = UserConfigV2()
    eng = get_strategy("IVW_v1")(cfg)
    idx = pd.date_range("2025-03-14 20:40", periods=4, freq="5min")     # a Friday
    df = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0}, index=idx)
    pos = {"ticket": 7, "entry_time": pd.Timestamp("2025-03-14 10:00")}
    assert eng.on_position_bar("EURUSD", "M5", df, pos) is None
    late = df.copy()
    late.index = pd.date_range("2025-03-14 23:40", periods=4, freq="5min")
    assert eng.on_position_bar("EURUSD", "M5", late.iloc[:3], pos) is None
    act = eng.on_position_bar("EURUSD", "M5", late, pos)
    assert act.action == "CLOSE" and act.close_reason == "DAY_END" and act.ticket == 7
    monday = df.copy()
    monday.index = pd.date_range("2025-03-17 00:00", periods=4, freq="5min")
    assert eng.on_position_bar("EURUSD", "M5", monday, pos).close_reason == "DAY_END"


def test_live_manages_positions_on_m5():
    eng = get_strategy("IVW_v1")(UserConfigV2())
    tf = getattr(eng, "TIMEFRAME", None) or eng.get_required_timeframes()[-1]   # position_manager's rule
    assert tf == "M5" and eng.LIVE_POSITION_EXITS


def test_wired_into_config_and_backtest():
    from backend.api.routes.backtest import STRATEGY_PARAM_SECTION
    from backend.strategies.strategy_defaults import get_strategy_defaults
    cfg = UserConfigV2.from_dict({"ivw": {"wall_percentile": 70, "bogus": 1}})
    assert cfg.ivw.wall_percentile == 70
    assert STRATEGY_PARAM_SECTION["IVW_v1"] == "ivw"
    d = get_strategy_defaults("IVW_v1")
    assert d["tp_count"] == 1 and d["tp1_rr"] == 2.0 and d["be_mode"] == "NONE"
    assert d["min_rr"] <= d["tp1_rr"], "min_rr above the measured target rejects every signal"


def _rr_engine(**cfg):
    from backend.risk.engine import RiskEngine
    base = {"risk_per_trade_pct": 1.0, "min_rr": 3.0, "tp_count": 1, "tp1_rr": 2.0,
            "tp_splits": [100], "multi_position_mode": True, "is_backtest": True}
    return RiskEngine({**base, **cfg})


@pytest.mark.parametrize("sid,approved", [("IVW_v1", True), ("ORB_v1", False)])
def test_measured_min_rr_lowers_the_gate_for_its_strategy_only(sid, approved):
    from backend.strategies.strategy_defaults import measured_min_rr_by_strategy
    eng = _rr_engine(min_rr_by_strategy=measured_min_rr_by_strategy(["IVW_v1", "ORB_v1"]))
    sig = {"symbol": "EURUSD", "direction": "BUY", "entry_price": 1.10, "stop_loss": 1.095,
           "confluence_score": 100, "strategy_name": sid}
    ok, reason, _ = eng.evaluate_signal(sig, 10000, initial_balance=10000)
    assert ok is approved, reason
    if not approved:
        assert "below minimum 3.0" in reason


def test_every_path_carries_the_measured_min_rr():
    from backend.risk.live_risk_config import build_live_risk_config
    cfg = UserConfigV2()
    rc, _ = build_live_risk_config(cfg, "IVW_v1")
    assert rc["min_rr_by_strategy"] == {"IVW_v1": 2.0}
    cfg.risk.use_strategy_exit_defaults = False
    rc, _ = build_live_risk_config(cfg, "IVW_v1")
    assert rc["min_rr_by_strategy"] == {"IVW_v1": 2.0}
    from backend.api.routes.backtest import BacktestRequest, build_merged_risk_config
    req = BacktestRequest(strategy_id="IVW_v1", symbol="EURUSD", min_rr=3.0)
    assert build_merged_risk_config(req)["min_rr_by_strategy"] == {"IVW_v1": 2.0}
