"""
tests/test_forecast_scoring.py — [P3.8 / P3.9]

Calibration and the surrogate control.

Conviction will SIZE the trade once it reaches the risk engine's confluence
ladder, so an uncalibrated confidence is worse than none — the sizer believes
it. And any pipeline this elaborate will produce a positive number on something,
so the surrogate control is what separates signal from the pipeline.
"""

import math

import numpy as np
import pytest

from backend.analytics.forecast_scoring import (
    brier_score,
    brier_skill_score,
    expected_calibration_error,
    isotonic_calibrate,
    permutation_control,
    reliability_diagram,
    score_forecasts,
    surrogate_series,
)


# ── Brier ────────────────────────────────────────────────────────────────────

def test_perfect_forecasts_score_zero():
    assert brier_score([1.0, 0.0, 1.0], [True, False, True]) == pytest.approx(0.0)


def test_maximally_wrong_forecasts_score_one():
    assert brier_score([0.0, 1.0], [True, False]) == pytest.approx(1.0)


def test_always_saying_half_scores_a_quarter():
    """0.25 is the number to beat; worse than that actively misinforms the sizer."""
    assert brier_score([0.5] * 100, [i % 2 == 0 for i in range(100)]) == pytest.approx(0.25)


def test_brier_skill_is_zero_for_a_base_rate_forecaster():
    """A model that always predicts the base rate adds nothing, and must score 0
    rather than a flattering small Brier."""
    outcomes = [True] * 30 + [False] * 70
    assert brier_skill_score([0.3] * 100, outcomes) == pytest.approx(0.0, abs=1e-9)


def test_brier_skill_is_positive_when_confidences_inform():
    rng = np.random.default_rng(1)
    p, y = [], []
    for _ in range(500):
        prob = rng.uniform(0.05, 0.95)
        p.append(prob)
        y.append(rng.random() < prob)          # honestly calibrated
    assert brier_skill_score(p, y) > 0.2


def test_empty_input_is_nan_not_zero():
    assert math.isnan(brier_score([], []))
    assert math.isnan(brier_skill_score([], []))


# ── reliability ──────────────────────────────────────────────────────────────

def test_reliability_reports_the_gap_per_bucket():
    """The diagnostic that answers 'when it says 0.8, how often is it right?'"""
    p = [0.85] * 100
    y = [True] * 55 + [False] * 45            # claims 0.85, delivers 0.55
    rows = [r for r in reliability_diagram(p, y) if r["n"]]
    assert len(rows) == 1
    assert rows[0]["realised_rate"] == pytest.approx(0.55)
    assert rows[0]["gap"] == pytest.approx(0.30, abs=0.01)


def test_empty_buckets_report_their_count_so_nobody_reads_n_of_1():
    rows = reliability_diagram([0.95], [True], bins=10)
    assert sum(r["n"] for r in rows) == 1
    assert all(r["mean_stated"] is None for r in rows if r["n"] == 0)


def test_calibration_error_is_zero_for_an_honest_forecaster():
    rng = np.random.default_rng(2)
    p = [rng.uniform(0.05, 0.95) for _ in range(4000)]
    y = [rng.random() < x for x in p]
    assert expected_calibration_error(p, y) < 0.05


def test_calibration_error_catches_systematic_overconfidence():
    p = [0.9] * 200
    y = [i < 100 for i in range(200)]          # says 0.9, delivers 0.5
    assert expected_calibration_error(p, y) == pytest.approx(0.4, abs=0.01)


# ── isotonic recalibration ───────────────────────────────────────────────────

def test_isotonic_corrects_systematic_overconfidence():
    """An agent whose 0.8s come in at 0.55 is over-betting its best ideas. The
    map must pull them down before conviction is allowed to size."""
    rng = np.random.default_rng(3)
    p, y = [], []
    for _ in range(2000):
        stated = rng.uniform(0.5, 0.95)
        p.append(stated)
        y.append(rng.random() < stated * 0.6)   # true rate is 60% of stated
    cal = isotonic_calibrate(p, y)
    assert cal(0.9) < 0.8, "a wildly overconfident 0.9 must be pulled down"
    assert expected_calibration_error(cal.apply(p), y) < expected_calibration_error(p, y)


def test_isotonic_is_monotone_so_the_models_ordering_survives():
    rng = np.random.default_rng(4)
    p = [rng.uniform(0, 1) for _ in range(600)]
    y = [rng.random() < x for x in p]
    cal = isotonic_calibrate(p, y)
    grid = [i / 50 for i in range(51)]
    mapped = [cal(g) for g in grid]
    assert all(a <= b + 1e-9 for a, b in zip(mapped, mapped[1:])), \
        "recalibration may rescale confidence but must never reorder it"


def test_isotonic_declines_to_fit_on_almost_no_data():
    cal = isotonic_calibrate([0.5], [True])
    assert cal.fitted is False
    assert cal(0.7) == pytest.approx(0.7), "an unfitted map must be the identity"


# ── scoring ──────────────────────────────────────────────────────────────────

def test_abstention_is_free_and_reported():
    """A FLAT that would have been wrong costs nothing. Scoring it as a loss
    would push the model toward always having a view."""
    s = score_forecasts([0.6, 0.7], [2.0, -1.0], n_total_forecasts=10)
    assert s.n_forecasts == 10
    assert s.n_actionable == 2
    assert s.abstain_rate == pytest.approx(0.8)
    assert s.expectancy_r == pytest.approx(0.5)


def test_expectancy_not_accuracy_is_the_headline():
    """Right 70% of the time on trades paying 0.3 R and wrong 30% on trades
    losing 1 R is worse than a coin flip. Accuracy hides that completely."""
    rs = [0.3] * 70 + [-1.0] * 30
    s = score_forecasts([0.7] * 100, rs, n_total_forecasts=100)
    assert s.win_rate == pytest.approx(0.70)
    assert s.expectancy_r < 0, "a 70% win rate that still loses money must read as negative"


def test_no_actionable_forecasts_is_not_a_crash():
    s = score_forecasts([], [], n_total_forecasts=50)
    assert s.n_actionable == 0 and s.abstain_rate == 1.0
    assert math.isnan(s.brier)
    assert s.to_dict()["brier"] is None


def test_score_serialises():
    d = score_forecasts([0.6], [1.0], n_total_forecasts=4).to_dict()
    for k in ("n_forecasts", "abstain_rate", "expectancy_r", "brier", "reliability"):
        assert k in d


# ── surrogates ───────────────────────────────────────────────────────────────

def _px(n=1200, seed=5):
    rng = np.random.default_rng(seed)
    return list(100 * np.exp(np.cumsum(rng.normal(0.0002, 0.01, n))))


@pytest.mark.parametrize("method", ["iid_shuffle", "block_shuffle", "sign_flip"])
def test_surrogates_preserve_the_return_distribution(method):
    px = _px()
    sur = surrogate_series(px, method=method, seed=1)
    r_real = np.diff(np.log(px))
    r_sur = np.diff(np.log(sur))
    assert len(sur) == len(px)
    # same dispersion; iid/block are permutations so identical, sign_flip negates
    assert np.std(r_sur) == pytest.approx(np.std(r_real), rel=0.02)
    assert sur[0] == pytest.approx(px[0])


def test_iid_shuffle_destroys_volatility_clustering():
    """The point of the control: same fat tails, no temporal structure."""
    from backend.risk.vol_target import clustering_strength

    rng = np.random.default_rng(6)
    vol, p, px = 0.01, 100.0, []
    for _ in range(3000):                       # GARCH-like: clustered by construction
        r = rng.normal(0, vol)
        vol = math.sqrt(2e-6 + 0.12 * r * r + 0.86 * vol * vol)
        p *= math.exp(r)
        px.append(p)
    assert clustering_strength(px)["clustering"] is True
    assert clustering_strength(surrogate_series(px, "iid_shuffle", seed=2))["clustering"] is False


def test_sign_flip_keeps_the_volatility_path_but_kills_direction():
    """The right control for a DIRECTIONAL claim — every volatility feature the
    forecaster reads survives intact."""
    px = _px()
    sur = surrogate_series(px, "sign_flip", seed=3)
    a = np.abs(np.diff(np.log(px)))
    b = np.abs(np.diff(np.log(sur)))
    assert np.allclose(np.sort(a), np.sort(b))


def test_unknown_surrogate_method_raises():
    with pytest.raises(ValueError, match="unknown surrogate"):
        surrogate_series(_px(), method="nonsense")


def test_degenerate_series_survives():
    assert surrogate_series([1.0], "iid_shuffle") == [1.0]


# ── the control verdict ──────────────────────────────────────────────────────

def test_a_result_inside_the_surrogate_distribution_is_the_pipeline():
    rng = np.random.default_rng(7)
    sur = list(rng.normal(0.0, 0.1, 200))
    res = permutation_control(real_expectancy=0.02, surrogate_expectancies=sur)
    assert res["beats_control"] is False
    assert "the result is the pipeline" in res["note"]


def test_a_result_beyond_the_surrogates_clears_the_control():
    rng = np.random.default_rng(8)
    sur = list(rng.normal(0.0, 0.1, 200))
    res = permutation_control(real_expectancy=0.6, surrogate_expectancies=sur)
    assert res["beats_control"] is True
    assert res["p_value"] < 0.05


def test_too_few_surrogates_declines_to_answer():
    res = permutation_control(0.5, [0.1, 0.2, 0.3])
    assert math.isnan(res["p_value"])
    assert "at least 10" in res["note"]
