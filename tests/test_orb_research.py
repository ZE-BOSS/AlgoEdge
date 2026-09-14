"""The research setups must be the same breakouts ORB_v1 trades, with features
that cannot see the future."""

import numpy as np
import pandas as pd

from backend.analytics.orb_research import FEATURES, build_setups, variant_key
from backend.analytics.strategy_search import Bars, _orb


def _bars(days=70, seed=5):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-02-03", periods=days * 96, freq="15min", tz="UTC")
    idx = idx[idx.dayofweek < 5]
    n = len(idx)
    close = 150 + np.cumsum(rng.normal(0, 0.08, n) + np.where(rng.random(n) < 0.03, rng.normal(0, 0.6, n), 0))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + np.abs(rng.normal(0.03, 0.02, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0.03, 0.02, n))
    vol = rng.integers(50, 500, n).astype(float)
    return Bars("GBPJPY", "M15", idx.as_unit("s").asi8.astype(np.int64), open_, high, low, close,
                np.full(n, 0.01), vol)


def test_break_setups_are_the_orb_v1_signals():
    b = _bars()
    sigs, _ = _orb(b, {"session": "london", "range_min": 60, "exit": 3.0, "side": "both"})
    setups = [s for s in build_setups(b, "london", 60) if s.entry_mode == "break"]
    # build_setups needs 20 completed days of history before its first setup
    first_t = setups[0].t_entry
    expected = [(int(b.time[s.i + 1]), s.direction) for s in sigs if int(b.time[s.i + 1]) >= first_t]
    assert [(s.t_entry, s.direction) for s in setups] == expected
    assert len(setups) >= 10


def test_features_do_not_change_when_the_future_changes():
    b = _bars()
    base = {(s.day, s.entry_mode): s.features for s in build_setups(b, "london", 60)}
    cut = len(b) - 96 * 5
    b.close[cut:] += 5.0
    b.high[cut:] += 5.0
    b.low[cut:] += 5.0
    for s in build_setups(b, "london", 60):
        if s.t_entry < int(b.time[cut]) - 86400 and (s.day, s.entry_mode) in base:
            assert s.features == base[(s.day, s.entry_mode)]


def test_every_setup_has_every_feature_and_variant():
    for s in build_setups(_bars(), "ny", 30):
        assert set(s.features) == set(FEATURES)
        assert variant_key(3.0, True, False) in s.outcomes
        r, t_exit, add_r, t_add, t_add_exit = s.outcomes[variant_key(3.0, True, False)]
        assert t_exit >= s.t_entry and r >= -1.0 - 1.0  # a gap can exceed -1R, never absurdly
        if add_r is not None:
            assert s.t_entry <= t_add <= t_add_exit
