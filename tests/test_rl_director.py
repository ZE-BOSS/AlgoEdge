"""
tests/test_rl_director.py

The supervised half: a frozen direction model, with the RL policy on top.

WHAT THESE TESTS ARE FOR
------------------------
The label is the forward return — literally the future — which makes this the
most leak-prone object in the whole system. So most of what follows checks the
three structural guards rather than the arithmetic:

  * it fits on the rows it was given and nothing else;
  * rows whose label window reaches past the end of the fold are purged;
  * a model that cannot beat a coin flip says so rather than producing
    confident noise.

The last group checks the join: adding the director must not disturb the
features the policy already had, and a mode-A agent with no director must be
unchanged.
"""

from __future__ import annotations

import numpy as np
import pytest

from backend.rl.director import DEFAULT_COST_R, Director, augment


def series(n=2000, seed=3, signal=True):
    """Bars with a LEARNABLE direction when `signal`, and none when not.

    Deliberately learnable, so "the model found nothing" and "the model is
    broken" are distinguishable. A pure random series cannot tell them apart.
    """
    rng = np.random.default_rng(seed)
    feature = rng.normal(0, 1, n)
    step = rng.normal(0, 0.001, n)
    if signal:
        # tomorrow follows today's feature
        step[1:] += 0.004 * np.sign(feature[:-1])
    close = 100 + np.cumsum(step)
    atr = np.full(n, 0.01)
    Z = np.column_stack([feature, rng.normal(0, 1, n), rng.normal(0, 1, n)])
    return Z, close, atr


# ── labels ─────────────────────────────────────────────────────────────────

def test_the_label_is_the_sign_of_the_forward_move():
    close = np.array([100.0, 101.0, 100.0, 103.0, 100.0], dtype=float)
    atr = np.ones(5)
    y, usable = Director.labels(close, atr, horizon=1, cost_r=0.0)
    assert list(y[:4]) == [1.0, 0.0, 1.0, 0.0]


def test_the_last_rows_are_never_usable():
    """Their label reaches past the end of the array."""
    close = np.arange(20, dtype=float)
    _, usable = Director.labels(close, np.ones(20), horizon=5, cost_r=0.0)
    assert not usable[-5:].any()
    assert usable[:-5].all()


def test_a_move_smaller_than_the_cost_floor_is_not_a_call():
    """A model trained to call every wobble inside the spread learns noise."""
    close = np.array([100.0, 100.001, 100.002, 100.003], dtype=float)
    _, usable = Director.labels(close, np.ones(4), horizon=1, cost_r=DEFAULT_COST_R)
    assert not usable.any()


def test_the_threshold_is_in_ATRs_so_one_number_works_across_markets():
    quiet = np.array([100.0, 100.5, 101.0], dtype=float)
    y1, u1 = Director.labels(quiet, np.full(3, 0.1), horizon=1, cost_r=1.0)
    y2, u2 = Director.labels(quiet, np.full(3, 10.0), horizon=1, cost_r=1.0)
    assert u1[0] and not u2[0], "the same move is a call in a quiet market, not a wild one"


# ── fitting ────────────────────────────────────────────────────────────────

def test_it_learns_a_direction_that_is_there():
    Z, close, atr = series(signal=True)
    d = Director(horizon=1, cost_r=0.0).fit(Z, close, atr, np.arange(1500),
                                            names=("a", "b", "c"))
    assert d.fitted and d.train_accuracy > 0.75, d.train_accuracy
    assert d.importance(1)[0][0] == "a", "it should lean on the feature that carries it"


def test_it_does_not_invent_one_that_is_not():
    Z, close, atr = series(signal=False)
    d = Director(horizon=1, cost_r=0.0).fit(Z, close, atr, np.arange(1500),
                                            names=("a", "b", "c"))
    assert d.train_accuracy < 0.62, (
        f"{d.train_accuracy:.2f} in-sample on a series with no signal - that is "
        f"memorisation, not prediction")


def test_it_fits_on_the_rows_it_was_given_and_nothing_else():
    """The leak this module exists to avoid. Fitting on the first half must give
    the same model whether or not the second half exists."""
    Z, close, atr = series(signal=True)
    rows = np.arange(900)
    a = Director(horizon=1, cost_r=0.0).fit(Z, close, atr, rows)
    b = Director(horizon=1, cost_r=0.0).fit(Z[:1000], close[:1000], atr[:1000], rows)
    assert np.allclose(a.w, b.w) and a.b == pytest.approx(b.b)


def test_rows_whose_label_reaches_past_the_end_are_purged():
    Z, close, atr = series(n=300, signal=True)
    d = Director(horizon=20, cost_r=0.0).fit(Z, close, atr, np.arange(300))
    assert d.n_train <= 280, "the last 20 rows have no label and must be dropped"


def test_too_few_usable_rows_is_refused_rather_than_fitted():
    Z, close, atr = series(n=300)
    with pytest.raises(ValueError, match="noise with coefficients"):
        Director(horizon=1, cost_r=0.0).fit(Z, close, atr, np.arange(20))


def test_an_unfitted_director_refuses_to_predict():
    with pytest.raises(RuntimeError, match="not been fitted"):
        Director().probability(np.zeros((3, 3)))


# ── the call ───────────────────────────────────────────────────────────────

def test_no_call_is_a_real_answer():
    """A director that always has an opinion has none."""
    d = Director(names=("a",))
    d.w, d.b = np.array([0.0]), 0.0          # a coin flip on every row
    calls = d.call(np.zeros((10, 1)), min_confidence=0.55)
    assert (calls == 0).all()


def test_a_confident_model_calls_both_ways():
    d = Director(names=("a",))
    d.w, d.b = np.array([4.0]), 0.0
    Z = np.array([[2.0], [-2.0], [0.0]])
    assert list(d.call(Z, 0.55)) == [1, -1, 0]


def test_confidence_is_distance_from_a_coin_flip():
    d = Director(names=("a",))
    d.w, d.b = np.array([0.0]), 0.0
    assert d.confidence(np.zeros((1, 1)))[0] == pytest.approx(0.0)
    d.w = np.array([10.0])
    assert d.confidence(np.array([[5.0]]))[0] > 0.99


# ── the join with the policy ───────────────────────────────────────────────

def test_the_director_adds_two_columns_and_disturbs_none():
    """The policy keeps every market feature it had and gains an opinion
    alongside, so it can learn WHERE to believe it."""
    Z, close, atr = series(signal=True)
    d = Director(horizon=1, cost_r=0.0).fit(Z, close, atr, np.arange(1500))
    out = augment(Z, d)
    assert out.shape == (len(Z), Z.shape[1] + 2)
    assert np.array_equal(out[:, :Z.shape[1]], Z)


def test_no_director_leaves_the_observation_exactly_as_it_was():
    """A mode-A agent must be byte-identical to one built before this existed."""
    Z, _, _ = series()
    assert augment(Z, None) is Z or np.array_equal(augment(Z, None), Z)
    assert augment(Z, Director()).shape == Z.shape


def test_it_round_trips_through_json():
    Z, close, atr = series(signal=True)
    d = Director(horizon=1, cost_r=0.0).fit(Z, close, atr, np.arange(1500),
                                            names=("a", "b", "c"),
                                            fitted_on="fold 0 train")
    back = Director.from_dict(d.to_dict())
    assert back.content_hash == d.content_hash
    assert np.allclose(back.probability(Z), d.probability(Z))
    assert back.fitted_on == "fold 0 train"


def test_it_says_what_it_leans_on_in_english():
    """The charter has to state what the agent does. A direction model whose
    reasoning cannot be read makes the charter a description of a black box."""
    Z, close, atr = series(signal=True)
    d = Director(horizon=1, cost_r=0.0).fit(Z, close, atr, np.arange(1500),
                                            names=("ret_1", "atr_pct", "gap"))
    text = d.english()
    assert "accuracy" in text and "ret_1" in text and "horizon" in text


def test_training_rows_and_accuracy_are_recorded():
    """Provenance, so a model fitted on the test fold is visible in the
    artefact rather than only in the code that made it."""
    Z, close, atr = series(signal=True)
    d = Director(horizon=1, cost_r=0.0).fit(Z, close, atr, np.arange(1500),
                                            fitted_on="fold 2 train rows")
    assert d.n_train > 0 and "fold 2" in d.to_dict()["fitted_on"]
