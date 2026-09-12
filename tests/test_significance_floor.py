"""
tests/test_significance_floor.py — [P5.10]

The data-mining threshold must never be MORE lenient than a single test.

`expected_max_abs_t(N)` is the expected maximum |t| across N null trials. For
small families it sits below 2.0 — three configurations give ~1.30 — so using it
raw as the bar let a result selected from three variants pass at a t that would
have failed had only one been tried. Found 2026-09-11 while building the
conditional-expectancy study, whose small variable families hit exactly that
range. The bar is now floored at the single-test 2.0.
"""

import math

import numpy as np
import pytest

from backend.analytics.significance import assess, expected_max_abs_t


def test_expected_max_is_below_two_for_small_families():
    """The reason the floor exists — if this ever stops holding, revisit it."""
    assert expected_max_abs_t(3) < 2.0


@pytest.mark.parametrize("n_trials", [1, 2, 3, 5, 8])
def test_threshold_is_never_below_a_single_test(n_trials):
    r = np.random.default_rng(0).normal(0.2, 1.0, 300)
    assert assess(r, n_trials=n_trials).deflation_threshold >= 2.0


def test_a_t_of_1_5_selected_from_three_does_not_clear_the_bar():
    rng = np.random.default_rng(1)
    r = rng.normal(0.0, 1.0, 400)
    r = r - r.mean() + 1.5 * r.std(ddof=1) / math.sqrt(len(r))   # force t = 1.5
    rep = assess(r, n_trials=3)
    assert rep.verdict != "SIGNIFICANT"
    assert any("does not clear the 2.00 bar" in s for s in rep.reasons)


def test_large_families_keep_their_higher_bar():
    r = np.random.default_rng(2).normal(0.2, 1.0, 300)
    assert assess(r, n_trials=84).deflation_threshold == pytest.approx(expected_max_abs_t(84))


def test_too_few_trades_report_the_same_floored_bar():
    assert assess([0.5], n_trials=3).deflation_threshold == 2.0
