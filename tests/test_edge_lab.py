"""edge_lab must score exits without look-ahead, never overlap trades, and the
account simulator's Sharpe/Sortino must come from daily returns."""

from datetime import datetime

import numpy as np
import pandas as pd
import pytest
import pytz

from backend.analytics import edge_lab as lab
from backend.analytics.money_sim import AccountRules, Contract, Leg, simulate_account
from backend.analytics.strategy_search import Bars


def _m5(days=45, seed=1, drift=0.0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-06", periods=days * 288, freq="5min", tz="UTC")
    idx = idx[idx.dayofweek < 5]
    n = len(idx)
    close = 100 + np.cumsum(rng.normal(drift, 0.05, n))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + np.abs(rng.normal(0.02, 0.01, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0.02, 0.01, n))
    t = idx.as_unit("s").asi8.astype(np.int64)
    return Bars("XAUUSD", "M5", t, open_, high, low, close, np.full(n, 0.001), rng.integers(50, 150, n).astype(float))


def _flat_bars(prices_hl, spread=0.0):
    """Bars from explicit (open, high, low, close) rows, 5 minutes apart."""
    rows = np.array(prices_hl, dtype=float)
    t = np.arange(len(rows), dtype=np.int64) * 300 + 1_736_000_000
    return Bars("X", "M5", t, rows[:, 0], rows[:, 1], rows[:, 2], rows[:, 3], np.full(len(rows), spread),
                np.ones(len(rows)))


def _ctx_for(b):
    ctx = lab.build_ctx(_m5(), "ny")
    ctx.b = b
    ctx.atr_d = np.full(10, np.nan)
    return ctx


def test_sessions_are_dst_aware_and_vwap_is_cumulative():
    b = _m5()
    ctx = lab.build_ctx(b, "ny")
    ny = pytz.timezone("America/New_York")
    assert ctx.sessions
    for s in ctx.sessions:
        local = datetime.fromtimestamp(s.open_ts, ny)
        assert (local.hour, local.minute) == (9, 30)
        seg = slice(s.r0, s.r1)
        tp = (b.high[seg] + b.low[seg] + b.close[seg]) / 3
        v = b.volume[seg]
        assert s.vwap[-1] == pytest.approx((tp * v).sum() / v.sum())


def test_stop_checked_before_target_and_gap_fills_at_open():
    # entry bar 1 opens 100; stop 1.0; bar 2 touches both 99 and 101 -> loss
    b = _flat_bars([(100, 100, 100, 100), (100, 100.2, 99.8, 100), (100, 101.5, 98.5, 100), (100, 100, 100, 100)])
    r, _ = lab.score_exits(_ctx_for(b), 0, 1, 1.0, 3, None)
    assert r[lab.EXITS.index("1:1")] == pytest.approx(-1.0)
    # bar 2 opens at 98 (below the 99 stop) -> filled at 98 = -2R
    b = _flat_bars([(100, 100, 100, 100), (100, 100.2, 99.8, 100), (98, 98.5, 97.5, 98), (98, 98, 98, 98)])
    r, _ = lab.score_exits(_ctx_for(b), 0, 1, 1.0, 3, None)
    assert r[lab.EXITS.index("eod")] == pytest.approx(-2.0)


def test_target_and_session_close_and_spread_cost():
    b = _flat_bars([(100, 100, 100, 100), (100, 100.5, 99.9, 100.4), (100.4, 102.1, 100.3, 102), (102, 102, 102, 101.5)],
                   spread=0.1)
    r, _ = lab.score_exits(_ctx_for(b), 0, 1, 1.0, 3, None)
    assert r[lab.EXITS.index("1:2")] == pytest.approx(2.0 - 0.1)
    assert r[lab.EXITS.index("eod")] == pytest.approx(1.5 - 0.1)
    assert r[lab.EXITS.index("1:10")] == pytest.approx(1.5 - 0.1)


def test_close_based_trail_exits_on_the_wrong_side_close():
    b = _flat_bars([(100, 100, 100, 100), (100, 100.6, 99.9, 100.5), (100.5, 100.6, 99.95, 100.0), (100, 100, 100, 100)])
    trail = np.array([np.nan, 100.2, 100.2])
    r, _ = lab.score_exits(_ctx_for(b), 0, 1, 1.0, 3, trail)
    assert r[lab.EXITS.index("trail")] == pytest.approx(0.0)


@pytest.mark.parametrize("fam", lab.FAMILIES, ids=lambda f: f.name)
def test_every_family_builds_sane_candidates(fam):
    b = _m5(days=60, seed=4)
    ctxs = {"native": lab.build_ctx(b, "ny"), "alt": lab.build_ctx(b, "london")}
    sets = lab.build_family(ctxs, fam)
    assert sets
    for key, cs in sets.items():
        assert cs.feats.shape == (len(cs), len(fam.features))
        if len(cs):
            assert np.all(cs.stop_dist >= lab.MIN_STOP_SPREADS * 0.001 - 1e-12)
            assert np.all(np.isfinite(cs.r)) and np.all(cs.r > -10)
            for k, rr in enumerate(lab.RR):          # a fixed target caps the win at its R:R
                assert np.all(cs.r[:, k] <= rr + 1e-9)
            assert np.all(cs.tx >= cs.t_entry[:, None])
            assert np.all(np.diff(cs.t_entry) >= 0)


def test_one_trade_per_session_and_filters_only_remove():
    b = _m5(days=60, seed=9)
    cs = lab.fam_vwap_trend(lab.build_ctx(b, "ny"), "native")
    span = (0, 2**40)
    all_idx = lab.pick(cs, span, (), 0)
    assert len(np.unique(cs.sess[all_idx])) == len(all_idx)
    for f in range(len(cs.features)):
        sub = lab.pick(cs, span, (f,), 0)
        assert cs.feats[sub, f].all()
        assert len(np.unique(cs.sess[sub])) == len(sub)


def test_sharpe_and_sortino_count_flat_days():
    c = {"X": Contract(1.0, 0.01, 0.01)}
    day = 86400
    legs = [Leg("X", 10 * day + 100, 10 * day + 200, 1.0, 1.0, group="a"),
            Leg("X", 12 * day + 100, 12 * day + 200, -1.0, 1.0, group="b"),
            Leg("X", 14 * day + 100, 14 * day + 200, 2.0, 1.0, group="c")]
    s = simulate_account(legs, c, AccountRules(10000.0, 1.0))
    rets = np.array([0.01, 0, -100 / 10100, 0, 200 / 10000])
    assert s["sharpe"] == pytest.approx(round(rets.mean() / rets.std(ddof=1) * np.sqrt(365), 2))
    down = np.sqrt(np.mean(np.minimum(rets, 0) ** 2))
    assert s["sortino"] == pytest.approx(round(rets.mean() / down * np.sqrt(365), 2))
