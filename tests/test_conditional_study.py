"""
tests/test_conditional_study.py — [P5.2 / P5.3]

Regime labels with no look-ahead, and a conditional-expectancy study that finds
a planted edge, ignores noise, and refuses an edge that ended — whether by
flipping sign or by quietly decaying to nothing.
"""

import statistics

import numpy as np
import pytest

from backend.analytics.conditional_expectancy import (
    TradeObs,
    enumerate_cells,
    one_sample_t,
    run_study,
    welch_t,
)
from backend.analytics.htf_regime import HTFSeries, RegimeLabeler

DAY = 86400
H4 = 14400
H1 = 3600


def _series(close, period, volume=None, spread=0.001):
    close = np.asarray(close, dtype=float)
    n = len(close)
    t = 1_700_000_000 + np.arange(n) * period
    o = np.r_[close[0], close[:-1]]
    return HTFSeries(time=t, open=o, high=np.maximum(o, close) * (1 + spread),
                     low=np.minimum(o, close) * (1 - spread), close=close,
                     period_seconds=period,
                     volume=None if volume is None else np.asarray(volume, dtype=float))


# ── look-ahead ───────────────────────────────────────────────────────────────

def test_a_bar_still_forming_is_never_used():
    """A D1 bar is stamped at its OPEN. Using it at 01:00 the same day would use
    a close that has not happened yet."""
    d1 = _series([100.0, 101.0, 102.0], DAY)
    t0 = int(d1.time[0])
    assert d1.last_closed_index(t0 + 3600) is None
    assert d1.last_closed_index(t0 + DAY + 3600) == 0, "day 2 is still forming"
    assert d1.last_closed_index(t0 + 2 * DAY) == 1, "day 2 closed exactly at day 3's open"


# ── the labels ───────────────────────────────────────────────────────────────

def test_d1_trend_up_down_and_flat():
    n = 300
    base = _series(np.full(10, 100.0), H1)
    up = RegimeLabeler(base=base, d1=_series(100 * np.exp(np.arange(n) * 0.004), DAY))
    down = RegimeLabeler(base=base, d1=_series(100 * np.exp(-np.arange(n) * 0.004), DAY))
    flat = RegimeLabeler(base=base, d1=_series(np.full(n, 100.0), DAY))
    end = int(up.d1.time[-1]) + DAY
    assert up.d1_trend(end) == "up"
    assert down.d1_trend(end) == "down"
    assert flat.d1_trend(end) == "flat"


def test_d1_trend_declines_before_the_emas_have_warmed_up():
    lab = RegimeLabeler(base=_series(np.full(10, 100.0), H1),
                        d1=_series(100 * np.exp(np.arange(60) * 0.004), DAY))
    assert lab.d1_trend(int(lab.d1.time[-1]) + DAY) is None


def test_h4_vol_high_after_a_volatility_spike():
    rng = np.random.default_rng(1)
    r = np.r_[rng.normal(0, 0.001, 150), rng.normal(0, 0.01, 25)]
    lab = RegimeLabeler(base=_series(np.full(10, 100.0), H1), h4=_series(100 * np.exp(np.cumsum(r)), H4))
    assert lab.h4_vol(int(lab.h4.time[-1]) + H4) == "high"


def test_h4_vol_low_after_calm_returns():
    rng = np.random.default_rng(2)
    r = np.r_[rng.normal(0, 0.01, 150), rng.normal(0, 0.001, 45)]
    lab = RegimeLabeler(base=_series(np.full(10, 100.0), H1), h4=_series(100 * np.exp(np.cumsum(r)), H4))
    assert lab.h4_vol(int(lab.h4.time[-1]) + H4) == "low"


def test_vwap_distance_buckets():
    closes = np.r_[np.full(23, 100.0), 103.0]
    far = RegimeLabeler(base=_series(closes, H1, volume=np.full(24, 1000.0), spread=0.005))
    assert far.vwap_distance(int(far.base.time[-1]) + H1) == "beyond_2sd"

    # Distance is measured against the dispersion of TYPICAL prices, which move
    # less than closes — so "near" has to sit on the VWAP itself (z ~ 0.14 here),
    # not merely a close's width away from it.
    wobble = np.r_[np.tile([99.8, 100.2], 12)[:23], 100.0]
    near = RegimeLabeler(base=_series(wobble, H1, volume=np.full(24, 1000.0)))
    assert near.vwap_distance(int(near.base.time[-1]) + H1) == "inside_1sd"


def test_profile_position_above_and_below_value():
    vol = np.r_[np.full(110, 1000.0), np.full(10, 10.0)]
    rising = RegimeLabeler(base=_series(np.r_[np.full(110, 100.0), np.linspace(101, 110, 10)], H1, volume=vol))
    falling = RegimeLabeler(base=_series(np.r_[np.full(110, 100.0), np.linspace(99, 90, 10)], H1, volume=vol))
    end = int(rising.base.time[-1]) + H1
    assert rising.profile_position(end) == "above_value"
    assert falling.profile_position(end) == "below_value"


def test_label_returns_every_variable_and_none_when_unobservable():
    lab = RegimeLabeler(base=_series(np.full(10, 100.0), H1))
    got = lab.label(int(lab.base.time[-1]) + H1)
    assert set(got) == {"d1_trend", "h4_vol", "session", "vwap_distance", "profile_position"}
    assert got["d1_trend"] is None and got["h4_vol"] is None
    assert got["profile_position"] is None, "10 bars cannot carry a 120-bar profile"


def test_off_hours_are_named_rather_than_unknown():
    """The gap between the NY close and the Asian open is a real block of the
    day. Reported as UNKNOWN it read like broken data in the live study."""
    lab = RegimeLabeler(base=_series(np.full(10, 100.0), H1))
    t0 = int(lab.base.time[0])
    sessions = {lab.label(t0 + h * H1)["session"] for h in range(24)}
    assert "UNKNOWN" not in sessions
    assert "off_hours" in sessions


# ── the study ────────────────────────────────────────────────────────────────

def _population(n=1200, seed=3, effects=None, halves=None):
    """One instrument, non-overlapping trades, random labels, optional planted effects."""
    rng = np.random.default_rng(seed)
    obs, labels = [], []
    for i in range(n):
        val = str(rng.choice(["a", "b", "c"]))
        r = float(rng.normal(0.0, 1.0))
        if effects and val in effects:
            r += effects[val]
        if halves and val in halves:
            r += halves[val][0] if i < n // 2 else halves[val][1]
        obs.append(TradeObs("X", float(i * 10), float(i * 10 + 5), r))
        labels.append({"regime": val})
    return obs, labels


def test_welch_and_one_sample_t_are_signed_and_safe():
    assert welch_t([1.0, 1.2, 0.8, 1.1], [0.0, 0.1, -0.1, 0.05]) > 0
    assert welch_t([1.0], [0.0, 0.1]) == 0.0
    assert one_sample_t([-1.0, -1.2, -0.8]) < 0
    assert one_sample_t([2.0]) == 0.0


def test_a_planted_edge_is_found():
    obs, labels = _population(effects={"a": 0.6})
    res = run_study(obs, labels, n_permutations=100, seed=1)
    by = {c["value"]: c for c in res["cells"]}
    assert by["a"]["verdict"] == "ACTIONABLE", by["a"]
    assert by["b"]["verdict"] != "ACTIONABLE" and by["c"]["verdict"] != "ACTIONABLE"
    assert res["permutation_control"]["p_value"] < 0.05
    assert res["headline"].startswith("CANDIDATES")


def test_pure_noise_produces_nothing_actionable():
    obs, labels = _population(seed=4)
    res = run_study(obs, labels, n_permutations=100, seed=2)
    assert res["actionable"] == []
    assert res["permutation_control"]["p_value"] > 0.05
    assert res["headline"] == "NO CONDITIONAL EDGE"


def test_an_edge_that_flipped_sign_is_unstable_not_actionable():
    obs, labels = _population(seed=5, halves={"a": (1.5, -1.0)})
    res = run_study(obs, labels, n_permutations=100, seed=3)
    cell = next(c for c in res["cells"] if c["value"] == "a")
    assert cell["verdict"] == "UNSTABLE", cell
    assert "changes sign" in cell["note"]


def test_an_edge_that_decayed_to_nothing_is_unstable_not_actionable():
    """Same sign in both halves, but the second half is pure noise — the shape of
    an edge that stopped working. A sign-only rule passed exactly this on
    2026-09-11 (+1.23 R first half, +0.03 R second) and called it ACTIONABLE.

    The cell's later half is demeaned to exactly zero so the test is decided by
    the rule, not by which way the noise happened to fall."""
    obs, labels = _population(seed=12, halves={"a": (1.2, 0.0)})
    a_idx = sorted((k for k, lab in enumerate(labels) if lab["regime"] == "a"),
                   key=lambda k: obs[k].entry_time)
    late = a_idx[len(a_idx) // 2:]
    m = statistics.mean(obs[k].r for k in late)
    for k in late:
        o = obs[k]
        obs[k] = TradeObs(o.instrument, o.entry_time, o.exit_time, o.r - m)

    res = run_study(obs, labels, n_permutations=100, seed=6)
    cell = next(c for c in res["cells"] if c["value"] == "a")
    assert cell["verdict"] == "UNSTABLE", cell
    assert abs(cell["second_half_t"]) < 1.0
    assert "decayed" in cell["note"]


def test_a_reliably_harmful_regime_is_flagged_avoid():
    obs, labels = _population(seed=6, effects={"b": -0.6})
    res = run_study(obs, labels, n_permutations=100, seed=4)
    cell = next(c for c in res["cells"] if c["value"] == "b")
    assert cell["verdict"] == "AVOID", cell
    assert res["headline"].startswith("HARMFUL")


def test_small_cells_are_too_few_whatever_their_mean():
    obs, labels = _population(n=1200, seed=7, effects={"a": 0.6})
    for i in range(0, 1200, 60):
        labels[i] = {"regime": "rare"}
        obs[i] = TradeObs("X", obs[i].entry_time, obs[i].exit_time, 3.0)
    res = run_study(obs, labels, n_permutations=50, seed=5)
    rare = next(c for c in res["cells"] if c["value"] == "rare")
    assert rare["verdict"] == "TOO FEW"


def test_the_family_wise_bar_rises_with_the_number_of_cells():
    obs, labels = _population(seed=8)
    few = run_study(obs, labels, n_permutations=0)
    wide = [dict(lab, **{f"v{k}": str(k % 3 == int(o.r > 0))
                         for k in range(6)}) for lab, o in zip(labels, obs)]
    many = run_study(obs, wide, n_permutations=0)
    assert many["n_cells_tested"] > few["n_cells_tested"]
    assert many["critical_welch_t"] >= few["critical_welch_t"] >= 2.0


def test_unlabelled_trades_are_in_neither_cell_nor_complement():
    labels = [{"v": "a"}, {"v": None}, {"v": "b"}, {}]
    assert enumerate_cells(labels, ["v"]) == [("v", "a"), ("v", "b")]


def test_mismatched_inputs_raise():
    with pytest.raises(ValueError):
        run_study([TradeObs("X", 0.0, 1.0, 1.0)], [], n_permutations=0)
    with pytest.raises(ValueError):
        run_study([TradeObs("X", 0.0, 1.0, 1.0)], [{"v": "a"}], overlap_scope="sideways",
                  n_permutations=0)
