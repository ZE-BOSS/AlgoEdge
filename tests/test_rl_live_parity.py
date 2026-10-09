"""
tests/test_rl_live_parity.py

Training sees the whole frame. Live sees a rolling window. Do they agree?

WHY THIS IS NOT A THEORETICAL CONCERN
-------------------------------------
`env.py` computes features over the whole frame at once, vectorised, because an
episode is tens of thousands of bars and a population optimiser evaluates
hundreds of policies on them. `RLStrategy.on_bar` computes them on whatever
window `BarFeed` hands it.

For a trailing feature with finite memory - a 20-bar rolling mean, a Donchian
position - the two are identical. For the exponentially-weighted ones (ATR, the
EMAs) they are not: a truncated window has lost the weight the EWM still places
on bars that fell off the start. The difference shrinks with window length and
is never exactly zero, which is the kind of thing this project has been bitten
by before - so it is MEASURED here and `PARITY_WINDOW_BARS` is set from the
measurement rather than from a round number.

The second half of the file is the one that would catch a real regression: the
same policy, on the same bars, must produce the same DECISIONS through both
paths. A feature that is 1e-9 different does not matter; a decision that flips
does.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.rl import features as F
from backend.rl import policy as pol
from backend.rl.spec import AgentSpec
from backend.strategies.strategy_rl.engine import PARITY_WINDOW_BARS


def frame(n=3000, seed=21):
    rng = np.random.default_rng(seed)
    start = 1735689600
    t = np.arange(start, start + n * 300, 300, dtype="int64")
    close = 1.08 + np.cumsum(rng.normal(0, 0.00025, n))
    return pd.DataFrame({"time": t, "open": close - 0.00001, "high": close + 0.00015,
                         "low": close - 0.00015, "close": close,
                         "volume": rng.integers(50, 500, n).astype(float)})


@pytest.fixture(scope="module")
def bars():
    return frame()


# ── the residual, measured ─────────────────────────────────────────────────

def worst_residual(bars, window, at_bars=(1500, 2000, 2500)):
    """The largest difference between a windowed feature and the same feature
    computed over the whole frame, at the bar they are both describing."""
    full = F.matrix(bars)
    worst = 0.0
    worst_name = ""
    for i in at_bars:
        windowed = F.matrix(bars.iloc[max(0, i - window + 1):i + 1])[-1]
        for k, name in enumerate(F.DEFAULT_FEATURES):
            d = abs(float(full[i, k]) - float(windowed[k]))
            scale = max(1.0, abs(float(full[i, k])))
            if d / scale > worst:
                worst, worst_name = d / scale, name
    return worst, worst_name


def test_the_declared_window_makes_the_residual_negligible(bars):
    """The number PARITY_WINDOW_BARS is chosen from, rather than a round 500."""
    worst, name = worst_residual(bars, PARITY_WINDOW_BARS)
    assert worst < 1e-6, f"{name} differs by {worst:.3e} relative at {PARITY_WINDOW_BARS} bars"


def test_a_short_window_is_measurably_worse(bars):
    """If this did not hold, the test above would be passing for the wrong
    reason - every feature finite-memory and the window irrelevant."""
    short, _ = worst_residual(bars, 120)
    long, _ = worst_residual(bars, PARITY_WINDOW_BARS)
    assert short > long


def test_the_window_is_long_enough_for_the_slowest_feature():
    """ATR(50) is an EWM with alpha 1/50; after n bars the residual weight is
    (1-alpha)^n. At 1200 bars that is about 1e-11."""
    residual = (1 - 1 / 50) ** PARITY_WINDOW_BARS
    assert residual < 1e-9, residual


# Features with NO exponentially-weighted term anywhere in them. Named
# explicitly rather than selected by `group`, because the group is about what a
# feature MEANS and this list is about how it is computed: `dist_high_20` is a
# range feature that divides by ATR, so it inherits ATR's infinite memory and
# belongs with the approximate ones. Selecting by group put three of those in
# here and the test failed for the right reason with the wrong label.
EXACT = ("donchian_pos_20", "donchian_pos_60", "body_frac", "upper_wick",
         "lower_wick", "tod_sin", "tod_cos", "dow_sin", "dow_cos", "volume_ratio")

# Everything that divides by, or is built from, an ATR or an EMA. These can
# only be approximately equal, and how close is what the window length buys.
APPROXIMATE = tuple(n for n in F.DEFAULT_FEATURES if n not in EXACT)


@pytest.mark.parametrize("name", EXACT)
def test_finite_memory_features_are_exactly_equal(bars, name):
    """No EWM in these, so "approximately" would be hiding something."""
    full = np.asarray(F.FEATURES[name](bars), dtype=float)
    i = 2000
    windowed = np.asarray(F.FEATURES[name](bars.iloc[i - 300:i + 1]), dtype=float)
    assert full[i] == pytest.approx(windowed[-1], abs=1e-12), name


@pytest.mark.parametrize("name", APPROXIMATE)
def test_ewm_features_converge_as_the_window_grows(bars, name):
    """The honest statement about the other half: not equal, convergent.

    Checked as a direction rather than a bound - a longer window must be
    closer. A feature that did not converge would mean something non-trailing
    had got in, which the prefix probe would also catch, but this says it in
    the units that matter here.
    """
    i = 2500
    full = float(np.asarray(F.FEATURES[name](bars), dtype=float)[i])
    near = float(np.asarray(F.FEATURES[name](bars.iloc[i - 119:i + 1]), dtype=float)[-1])
    far = float(np.asarray(F.FEATURES[name](
        bars.iloc[i - PARITY_WINDOW_BARS + 1:i + 1]), dtype=float)[-1])
    scale = max(1.0, abs(full))
    assert abs(far - full) / scale <= abs(near - full) / scale + 1e-12, name
    assert abs(far - full) / scale < 1e-6, name


# ── the decisions, which are what actually matter ──────────────────────────

def test_the_same_policy_makes_the_same_decisions_through_both_paths(bars):
    """A feature 1e-9 different does not matter. A decision that flips does."""
    spec = AgentSpec(name="parity", symbols=("EURUSD",))
    X = F.matrix(bars, spec.features)
    scaler = F.Scaler().fit(X[:1500], spec.features, fitted_on="rows 0-1500")
    p = pol.LinearPolicy(n_features=spec.n_features, exit_head=True)
    p.from_flat(np.random.default_rng(5).normal(0, 0.6, p.n_params))

    Z_full = scaler.transform(X)
    side_full, _, _ = p.entry_decisions(Z_full, spec.min_confidence)

    disagreements = 0
    checked = range(1500, len(bars), 37)
    for i in checked:
        window = bars.iloc[max(0, i - PARITY_WINDOW_BARS + 1):i + 1]
        z = scaler.transform(F.matrix(window, spec.features)[-1:])[0]
        direction, _, _ = p.act_entry(z, spec.min_confidence)
        live_side = 1 if direction == "BUY" else (-1 if direction == "SELL" else 0)
        if live_side != int(side_full[i]):
            disagreements += 1
    assert disagreements == 0, (
        f"{disagreements} of {len(list(checked))} decisions differ between the "
        f"vectorised training path and the windowed live path")


def test_a_deliberately_short_window_does_flip_decisions(bars):
    """Proves the test above is capable of failing. With a 60-bar window the
    EMAs and ATRs are visibly different and some decisions change - which is
    exactly why the declared window is 1,200 and not 60."""
    spec = AgentSpec(name="parity", symbols=("EURUSD",))
    X = F.matrix(bars, spec.features)
    scaler = F.Scaler().fit(X[:1500], spec.features)
    p = pol.LinearPolicy(n_features=spec.n_features)
    p.from_flat(np.random.default_rng(6).normal(0, 0.6, p.n_params))
    side_full, _, _ = p.entry_decisions(scaler.transform(X), spec.min_confidence)

    flipped = 0
    for i in range(1500, len(bars), 13):
        z = scaler.transform(F.matrix(bars.iloc[i - 59:i + 1], spec.features)[-1:])[0]
        direction, _, _ = p.act_entry(z, spec.min_confidence)
        live = 1 if direction == "BUY" else (-1 if direction == "SELL" else 0)
        flipped += int(live != int(side_full[i]))
    assert flipped > 0


def test_the_strategy_declares_that_window_to_bar_feed():
    """`strategies.windows` reads WINDOW_BARS to decide how much history to
    fetch. Declaring less than the features need is how live and backtest
    diverge without anything looking wrong."""
    from backend.strategies.strategy_rl.engine import RLStrategy

    assert RLStrategy.POSITION_BAR_WINDOW == PARITY_WINDOW_BARS


def test_the_exit_head_sees_the_same_observation_as_the_entry_head(bars):
    """Both read `_observe`, so a change to one cannot silently diverge from
    the other."""
    import inspect

    from backend.strategies.strategy_rl.engine import RLStrategy

    src = inspect.getsource(RLStrategy)
    assert src.count("self._observe(candles)") >= 2
