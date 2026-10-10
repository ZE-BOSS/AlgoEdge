"""
tests/test_exit_replay_speed.py

Three hot-path rewrites, and the proof that each one changed only the speed.

WHY THEY MATTERED
-----------------
An RL rollout over 20,000 bars took 20.5 seconds — 1,026 microseconds per
bar — which made one CEM training job eleven hours and the whole run
impossible to sit through. Profiling put almost none of that in the learning:

    exit_replay.swing_points_at   77% of the rollout   20,000 calls,
                                                       ~600,000 numpy
                                                       reductions between them
    exit_replay.atr_series        34% of what remained  np.mean called once
                                                        per bar on a 14-element
                                                        slice
    policy.entry_probs            2.6s of 20.5s        np.apply_along_axis,
                                                        which is a Python loop
                                                        wearing a numpy coat

After all three: 0.89 seconds, 45 microseconds per bar, 23x faster.

WHY THE EQUIVALENCE TESTS ARE THE POINT
---------------------------------------
Two of these live in `backend/risk/exit_replay.py`, which both backtest
engines AND the live position manager share — it is the module that exists so
a leg trails identically in all three. ATR sets stop distances. A rewrite that
is "close enough" would move every stop by a hair and quietly change what the
backtests say, in a way no test of this project would otherwise catch.

So each test compares against the ORIGINAL implementation, inlined here as the
reference, and asserts exact equality rather than approximate.
"""

from __future__ import annotations

import time

import numpy as np
import pytest

from backend.risk.exit_replay import (ATR_PERIOD, SWING_LOOKBACK, atr_series,
                                      swing_masks, swing_points_at)


# -- the implementations as they were, kept as the reference ---------------


def reference_atr(high, low, close, period: int = ATR_PERIOD) -> np.ndarray:
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    close = np.asarray(close, dtype=float)
    n = len(close)
    out = np.zeros(n)
    if n == 0:
        return out
    prev = np.roll(close, 1)
    prev[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev), np.abs(low - prev)))
    for i in range(period, n):
        out[i] = np.mean(tr[i - period:i])
    return out


def reference_swings(high, low, i: int, sw_len: int, lookback: int = SWING_LOOKBACK):
    if i < lookback:
        return []
    points = []
    for j in range(max(sw_len, i - lookback), i - sw_len):
        if j - sw_len < 0:
            continue
        window_h = high[j - sw_len:j + sw_len + 1]
        window_l = low[j - sw_len:j + sw_len + 1]
        if high[j] == window_h.max():
            points.append({"type": "HIGH", "price": float(high[j])})
        if low[j] == window_l.min():
            points.append({"type": "LOW", "price": float(low[j])})
    return points


def series(n: int, seed: int = 11):
    """Prices with deliberate ties and a flat stretch.

    Both swing rules are `==` comparisons against a window extreme, so ties
    are exactly where a rewrite would diverge — a plateau makes several bars
    equal to the window maximum at once.
    """
    rng = np.random.default_rng(seed)
    px = 100 + np.cumsum(rng.normal(0, 0.3, n))
    high = px + np.abs(rng.normal(0, 0.2, n))
    low = px - np.abs(rng.normal(0, 0.2, n))
    if n > 80:
        high[rng.integers(0, n, n // 12)] = high[0]      # scattered ties
        low[50:70] = low[50]                             # a flat stretch
    return high, low, px


# -- ATR --------------------------------------------------------------------


@pytest.mark.parametrize("n", [0, 1, 5, 13, 14, 15, 100, 3000])
def test_atr_is_bit_identical_to_the_loop_it_replaced(n):
    """Exactly equal, not approximately.

    A cumulative sum would also give a rolling mean and would be faster
    still, but it carries one running total whose rounding drifts from what
    a per-slice mean produced. ATR sets stop distances, so the sliding
    window — which reduces each window independently, as the loop did — is
    the right trade.
    """
    high, low, close = series(n)
    got, want = atr_series(high, low, close), reference_atr(high, low, close)
    assert got.shape == want.shape
    assert np.array_equal(got, want), (
        f"ATR changed for n={n}: max difference {np.max(np.abs(got - want))}")


def test_atr_leaves_the_warmup_bars_at_zero():
    """0 until `period` bars exist, which both engines and live rely on."""
    high, low, close = series(50)
    assert np.all(atr_series(high, low, close)[:ATR_PERIOD] == 0.0)


def test_atr_beats_the_loop_by_a_wide_margin():
    """Relative, not absolute: a wall-clock bound is a flaky test.

    Measured 211x when this was written. Asserting 5x fails only if someone
    puts the per-bar loop back, which is the regression worth catching.
    """
    high, low, close = series(20000)
    atr_series(high, low, close)                       # warm

    t0 = time.perf_counter()
    reference_atr(high, low, close)
    slow = time.perf_counter() - t0
    t0 = time.perf_counter()
    atr_series(high, low, close)
    fast = max(time.perf_counter() - t0, 1e-9)

    assert slow / fast > 5, f"only {slow / fast:.1f}x faster than the old loop"


# -- swing points -----------------------------------------------------------


@pytest.mark.parametrize("sw_len", [1, 2, 3, 5, 8])
def test_swing_points_match_the_per_bar_version(sw_len):
    """Including the dicts' order, which the trailing logic reads."""
    high, low, _ = series(1200)
    masks = swing_masks(high, low, sw_len)
    for i in range(0, 1200, 17):
        assert swing_points_at(high, low, i, sw_len, masks=masks) == \
            reference_swings(high, low, i, sw_len), f"i={i} sw_len={sw_len}"


def test_supplying_masks_changes_nothing(monkeypatch):
    """The masks are an optimisation, not a different answer.

    `replay_stops` and both engines pass them; anything else calls without
    them and must get the same list.
    """
    high, low, _ = series(600)
    for i in range(0, 600, 29):
        with_masks = swing_points_at(high, low, i, 5, masks=swing_masks(high, low, 5))
        without = swing_points_at(high, low, i, 5)
        assert with_masks == without, f"i={i}"


def test_a_flat_stretch_makes_every_bar_a_swing_in_both_versions():
    """The tie case, stated on purpose.

    On a plateau every bar equals the window extreme, so every bar is both a
    swing high and a swing low. That is what the original did and it is load
    bearing — a rewrite that broke ties differently would silently change
    where stops trail to.
    """
    high = np.full(200, 1.0)
    low = np.full(200, 1.0)
    is_high, is_low = swing_masks(high, low, 5)
    assert is_high[5:195].all() and is_low[5:195].all()
    assert swing_points_at(high, low, 100, 5) == reference_swings(high, low, 100, 5)


def test_the_bars_at_each_end_are_never_swings():
    """A bar without `sw_len` neighbours on both sides cannot be confirmed."""
    high, low, _ = series(300)
    is_high, is_low = swing_masks(high, low, 5)
    assert not is_high[:5].any() and not is_high[-5:].any()
    assert not is_low[:5].any() and not is_low[-5:].any()


def test_a_series_shorter_than_the_window_has_no_swings():
    high, low, _ = series(6)
    is_high, is_low = swing_masks(high, low, 5)
    assert not is_high.any() and not is_low.any()


def test_swing_lookup_beats_recomputation():
    high, low, _ = series(20000)
    masks = swing_masks(high, low, 5)

    t0 = time.perf_counter()
    for i in range(200, 700):
        reference_swings(high, low, i, 5)
    slow = time.perf_counter() - t0
    t0 = time.perf_counter()
    for i in range(200, 700):
        swing_points_at(high, low, i, 5, masks=masks)
    fast = max(time.perf_counter() - t0, 1e-9)

    assert slow / fast > 4, f"only {slow / fast:.1f}x faster per bar"


# -- the policy's softmax ---------------------------------------------------


def test_batched_entry_probs_match_the_row_by_row_version():
    """`np.apply_along_axis` is a Python loop, and it was called per bar.

    The irony on record: `entry_decisions` directly below it carries a
    docstring about how a Python loop there is the difference between a
    training run that finishes and one that does not.
    """
    from backend.rl.policy import LinearPolicy, softmax

    rng = np.random.default_rng(1)
    p = LinearPolicy(n_features=25)
    Z = rng.normal(0, 1, (4000, p.W_entry.shape[0]))

    got = p.entry_probs(Z)
    want = np.apply_along_axis(softmax, 1, p.entry_logits(Z))
    assert np.allclose(got, want, atol=1e-12)
    assert np.allclose(got.sum(axis=1), 1.0)


def test_the_single_row_path_is_untouched():
    from backend.rl.policy import LinearPolicy, softmax

    rng = np.random.default_rng(2)
    p = LinearPolicy(n_features=25)
    z = rng.normal(0, 1, p.W_entry.shape[0])
    assert np.allclose(p.entry_probs(z), softmax(p.entry_logits(z)))
