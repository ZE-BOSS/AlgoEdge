"""
tests/test_significance.py — [P5.9 / P5.10]

The corrections that decide whether a result is real. The single most important
test here is `test_overlapping_trades_manufacture_significance`, which
reproduces research/24 §4's central finding: random entries score 4 sigma when
their trades overlap and fair when they do not.
"""

import math

import numpy as np
import pytest

from backend.analytics.significance import (
    assess,
    deflated_sharpe_ratio,
    effective_sample_size,
    expected_max_abs_t,
    non_overlapping_subset,
    probabilistic_sharpe_ratio,
    probability_of_backtest_overfitting,
    purged_kfold_splits,
)


# ── the central finding ──────────────────────────────────────────────────────

def test_overlapping_trades_manufacture_significance():
    """A fair game, sampled with overlap, reads as an edge. This is exactly how
    DriftJumpAlpha's +0.2039 R survived a walk-forward split and then evaporated."""
    rng = np.random.default_rng(4)
    # One underlying price path. 400 trades, each holding 100 bars, entered every
    # 2 bars — so ~50 of them share any given stretch of path.
    path = np.cumsum(rng.normal(0.02, 1.0, 2000))
    entries = np.arange(0, 800, 2, dtype=float)
    exits = entries + 100.0
    rs = np.array([path[int(e + 100)] - path[int(e)] for e in entries])

    rep = assess(rs, entries, exits, n_trials=1)
    assert rep.n == len(rs)
    assert rep.n_nonoverlap < rep.n / 4, "overlap must collapse the independent count"
    assert abs(rep.t_naive) > abs(rep.t_nonoverlap), (
        "the naive t must be inflated relative to the honest one — that inflation "
        "is the whole point of this module"
    )
    assert rep.effective_n < rep.n


def test_non_overlapping_subset_is_maximal_and_disjoint():
    entries = [0.0, 1.0, 2.0, 10.0, 11.0, 20.0]
    exits = [5.0, 3.0, 8.0, 15.0, 12.0, 25.0]
    keep = non_overlapping_subset(entries, exits)
    for a, b in zip(keep, keep[1:]):
        assert entries[b] >= exits[a], "selected trades must not overlap"
    # earliest-exit-first is the interval-scheduling optimum
    assert len(keep) == 3


def test_effective_sample_size_tracks_concurrency():
    n = 50
    # Well-spaced, never overlapping — and with idle gaps between them. Idle time
    # must not INFLATE the sample above n, or every standard error derived from
    # it is too small.
    seq_e = [float(i * 10) for i in range(n)]
    seq_x = [float(i * 10 + 9) for i in range(n)]
    assert effective_sample_size(seq_e, seq_x) == pytest.approx(n, rel=0.001)
    assert effective_sample_size(seq_e, seq_x) <= n

    ov_e = [float(i) for i in range(n)]
    ov_x = [float(i + 10) for i in range(n)]
    assert effective_sample_size(ov_e, ov_x) < n / 2


# ── data-mining deflation ────────────────────────────────────────────────────

def test_deflation_threshold_matches_monte_carlo():
    """The formula must match the thing it approximates. Ground truth from
    200,000-path simulation of max|Z| over N standard normals:

        N=10 -> 1.881    N=50 -> 2.509    N=84 -> 2.691    N=500 -> 3.241

    research/24 §6 quoted ~2.64 for "~84 hypotheses"; the exact figure is 2.69,
    and the candidate's observed 2.30 sits inside either band."""
    for n, simulated in ((10, 1.881), (50, 2.509), (84, 2.691), (500, 3.241)):
        assert expected_max_abs_t(n) == pytest.approx(simulated, abs=0.03), n
    assert expected_max_abs_t(1) == 0.0
    assert expected_max_abs_t(10) < expected_max_abs_t(100)


def test_a_2_30_result_from_84_trials_is_not_significant():
    rng = np.random.default_rng(9)
    r = rng.normal(0, 1, 200)
    r = r - r.mean() + 2.30 * r.std(ddof=1) / math.sqrt(len(r))  # force t = 2.30
    assert assess(r, n_trials=84).verdict == "NOT SIGNIFICANT"
    assert any("2.6" in s or "bar for" in s for s in assess(r, n_trials=84).reasons)


def test_the_same_result_from_one_trial_clears_the_t_bar():
    rng = np.random.default_rng(9)
    r = rng.normal(0, 1, 200)
    r = r - r.mean() + 3.5 * r.std(ddof=1) / math.sqrt(len(r))
    rep = assess(r, n_trials=1)
    assert abs(rep.t_nonoverlap) > rep.deflation_threshold


# ── Sharpe ───────────────────────────────────────────────────────────────────

def test_probabilistic_sharpe_is_high_for_a_strong_series():
    rng = np.random.default_rng(2)
    assert probabilistic_sharpe_ratio(rng.normal(0.5, 1.0, 500)) > 0.99


def test_probabilistic_sharpe_is_exactly_half_at_zero_sample_sharpe():
    """PSR answers P(true SR > 0). A single random draw of a fair series has a
    UNIFORMLY distributed PSR — testing one draw tests nothing — so pin the
    sample mean to exactly zero, where the answer must be exactly 0.5."""
    rng = np.random.default_rng(3)
    r = rng.normal(0.0, 1.0, 500)
    r = r - r.mean()
    assert probabilistic_sharpe_ratio(r) == pytest.approx(0.5, abs=1e-9)


def test_deflated_sharpe_falls_as_trials_rise():
    rng = np.random.default_rng(5)
    r = rng.normal(0.15, 1.0, 400)
    assert deflated_sharpe_ratio(r, 1) > deflated_sharpe_ratio(r, 500), \
        "the more configurations you tried, the higher the bar the winner must clear"


def test_negative_skew_is_penalised():
    """Stop/target strategies are skewed by construction: many small wins and a
    rare large loss looks identical to an edge under the normal formula."""
    rng = np.random.default_rng(6)
    sym = rng.normal(0.1, 1.0, 600)
    skewed = np.where(rng.random(600) < 0.03, -8.0, 0.35)  # same mean, ugly tail
    assert probabilistic_sharpe_ratio(skewed) < probabilistic_sharpe_ratio(sym)


# ── PBO ──────────────────────────────────────────────────────────────────────

def test_pbo_is_high_when_every_configuration_is_noise():
    rng = np.random.default_rng(11)
    res = probability_of_backtest_overfitting(rng.normal(0, 1, (600, 12)), n_splits=8)
    assert res["pbo"] == pytest.approx(0.5, abs=0.25), \
        "picking the best of 12 noise series must not carry out of sample"


def test_pbo_is_low_when_one_configuration_is_genuinely_better():
    rng = np.random.default_rng(12)
    X = rng.normal(0, 1, (600, 8))
    X[:, 3] += 0.45                      # a real, persistent edge in column 3
    res = probability_of_backtest_overfitting(X, n_splits=8)
    assert res["pbo"] < 0.2
    assert "carries out of sample" in res["note"]


def test_pbo_declines_gracefully_on_unusable_input():
    assert math.isnan(probability_of_backtest_overfitting(np.zeros((10, 1)))["pbo"])
    assert math.isnan(probability_of_backtest_overfitting(np.zeros((4, 5)))["pbo"])


# ── purged CV ────────────────────────────────────────────────────────────────

def test_purged_kfold_removes_every_overlapping_training_label():
    n = 100
    entries = [float(i) for i in range(n)]
    exits = [float(i + 5) for i in range(n)]
    for train, test in purged_kfold_splits(entries, exits, n_splits=5, embargo_pct=0.0):
        t0 = min(entries[i] for i in test)
        t1 = max(exits[i] for i in test)
        for i in train:
            assert not (exits[i] >= t0 and entries[i] <= t1), \
                "a training label overlapping the test window leaks price path"


def test_embargo_removes_the_window_after_the_test_fold():
    n = 100
    entries = [float(i) for i in range(n)]
    exits = [float(i + 1) for i in range(n)]
    no_emb = purged_kfold_splits(entries, exits, 5, embargo_pct=0.0)
    with_emb = purged_kfold_splits(entries, exits, 5, embargo_pct=0.10)
    assert sum(len(t) for t, _ in with_emb) < sum(len(t) for t, _ in no_emb)


def test_every_sample_is_tested_exactly_once():
    n, k = 60, 5
    entries = [float(i) for i in range(n)]
    exits = [float(i + 2) for i in range(n)]
    tested = [i for _, test in purged_kfold_splits(entries, exits, k) for i in test]
    assert sorted(tested) == list(range(n))


def test_purged_kfold_handles_degenerate_input():
    assert purged_kfold_splits([], [], 5) == []
    assert purged_kfold_splits([1.0], [2.0], 1) == []


# ── the verdict ──────────────────────────────────────────────────────────────

def test_a_small_sample_is_insufficient_not_significant():
    """21 trades over 10 days — the shape of the user's DJA run — must never be
    reported as a conclusion in either direction."""
    rng = np.random.default_rng(8)
    rep = assess(rng.normal(-0.35, 2.2, 21), n_trials=1)
    assert rep.verdict == "INSUFFICIENT"
    assert any("too few" in s for s in rep.reasons)


def test_a_losing_strategy_is_never_significant():
    rng = np.random.default_rng(10)
    rep = assess(rng.normal(-0.3, 1.0, 300), n_trials=1)
    assert rep.verdict == "NOT SIGNIFICANT"
    assert any("not positive" in s for s in rep.reasons)


def test_a_genuinely_strong_independent_result_passes():
    rng = np.random.default_rng(13)
    n = 400
    rep = assess(rng.normal(0.45, 1.0, n),
                 entries=[float(i * 10) for i in range(n)],
                 exits=[float(i * 10 + 5) for i in range(n)],
                 n_trials=1)
    assert rep.verdict == "SIGNIFICANT", rep.reasons


def test_report_serialises_for_a_run_summary():
    rng = np.random.default_rng(14)
    d = assess(rng.normal(0.1, 1.0, 100), n_trials=20).to_dict()
    for k in ("t_naive", "t_nonoverlap", "deflation_threshold", "verdict", "reasons"):
        assert k in d
    assert isinstance(d["reasons"], list)
