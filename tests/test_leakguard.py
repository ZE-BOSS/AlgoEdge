"""
tests/test_leakguard.py

The guard that has to work before anything in `backend/rl/` can be believed.

WHY THIS FILE IS LARGER THAN THE THING IT TESTS
-----------------------------------------------
A leak probe that has quietly stopped detecting leaks is worse than no probe,
because it is trusted. So most of what follows is not "does the probe run" but
"does it still fail on things that are known to be leaky" — planted leaks of
several shapes, each one a mistake that has actually been made in production
code somewhere.

The project's own scars set the thresholds:

  * a route that stamped entries one bar late cost 10R over 152 GBPJPY trades.
    Real, measurable, and NOT a collapse — which is why `shift_verdict` asks
    for half the edge to survive rather than all of it.
  * `_ffill_bfill` back-fills leading NaNs from the first valid value, a
    three-bar lookahead that a probe cutting three quarters of the way in
    would never see. Hence the banned-primitive list as well as the probe.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.rl import leakguard as lg


@pytest.fixture
def bars():
    return lg.synthetic_bars(400)


# ── the probe detects what it must ─────────────────────────────────────────

def test_the_probe_still_catches_its_own_controls():
    """The whole guard rests on this. If `self_check` ever passes a leaky
    control, every clean scan afterwards is meaningless."""
    findings = lg.self_check()
    by_name = {f.name: f for f in findings}
    assert by_name["control: trailing mean (must pass)"].causal is True
    assert by_name["control: centred mean (must fail)"].causal is False
    assert by_name["control: whole-series z-score (must fail)"].causal is False


def test_a_broken_probe_is_reported_rather_than_trusted(monkeypatch):
    """Simulate the probe losing its teeth. `self_check` must refuse."""
    monkeypatch.setattr(lg, "probe",
                        lambda name, fn, frame, cut=None: lg.Finding(name, True, "series"))
    with pytest.raises(lg.LookaheadError, match="no longer detecting"):
        lg.self_check()


@pytest.mark.parametrize("name,fn,least_reach", [
    # each is a real mistake, not a toy
    ("centred window", lambda d: d["close"].rolling(21, center=True, min_periods=1).mean(), 5),
    ("negative shift", lambda d: d["close"].shift(-3), 1),
    ("whole-series mean", lambda d: d["close"] - d["close"].mean(), 50),
    ("whole-series minmax", lambda d: (d["close"] - d["close"].min())
                                      / (d["close"].max() - d["close"].min()), 50),
    ("expanding max from the end", lambda d: d["close"][::-1].cummax()[::-1], 50),
    ("backfill", lambda d: d["close"].where(d.index % 7 != 0).bfill(), 1),
    ("whole-series quantile", lambda d: (d["close"] > d["close"].quantile(0.8)).astype(float), 1),
])
def test_planted_leaks_are_caught(bars, name, fn, least_reach):
    found = lg.probe(name, fn, bars)
    assert found.causal is False, f"{name} was not caught"
    assert found.reach >= least_reach, (name, found.reach)


@pytest.mark.parametrize("name,fn", [
    ("trailing mean", lambda d: d["close"].rolling(20, min_periods=1).mean()),
    ("trailing std", lambda d: d["close"].rolling(20, min_periods=1).std()),
    ("difference", lambda d: d["close"].diff()),
    ("positive shift", lambda d: d["close"].shift(3)),
    ("expanding mean", lambda d: d["close"].expanding().mean()),
    ("forward fill", lambda d: d["close"].where(d.index % 7 != 0).ffill()),
    ("cumulative max", lambda d: d["close"].cummax()),
    ("bar range", lambda d: d["high"] - d["low"]),
])
def test_causal_features_are_not_rejected(bars, name, fn):
    """A guard that cries wolf gets switched off."""
    found = lg.probe(name, fn, bars)
    assert found.causal is True, found.summary


def test_the_reach_says_which_mistake_it_is(bars):
    """A centred window of 20 contaminates about 10 bars; a whole-series
    statistic contaminates all of them. The number is the diagnosis."""
    centred = lg.probe("centred", lambda d: d["close"].rolling(20, center=True,
                                                               min_periods=1).mean(), bars)
    whole = lg.probe("whole", lg.whole_series_control, bars)
    assert centred.reach < 30, centred.reach
    assert whole.reach > 200, whole.reach


# ── this codebase's own primitives ─────────────────────────────────────────

def test_the_shared_vwap_feature_is_causal(bars):
    """Every strategy reads `vwap_zone`, so if this leaked the whole engine
    would be compromised, not only the RL work."""
    from backend.strategies.strategy_vwap.engine import _calculate_anchored_vwap_with_bands

    for i, label in enumerate(("line", "sigma")):
        found = lg.probe(f"anchored VWAP {label}",
                         lambda d, i=i: _calculate_anchored_vwap_with_bands(d, 15, 0)[i], bars)
        assert found.causal is True, found.summary


def test_the_known_backfill_primitive_is_banned_by_name():
    """`_ffill_bfill` takes leading NaNs from the FIRST VALID value, which is
    a lookahead at the start of an array — exactly where a probe cut three
    quarters of the way in cannot see it. Caught by name instead."""
    assert lg.scan_source("x = _ffill_bfill(a)") == ["_ffill_bfill"]
    assert "center=True" in lg.BANNED_CALLS
    assert ".shift(-" in lg.BANNED_CALLS


def test_clean_feature_source_passes_the_name_check():
    assert lg.scan_source("x = s.rolling(20).mean().ffill()") == []


# ── the gate ───────────────────────────────────────────────────────────────

def test_assert_causal_blocks_a_leaky_feature(bars):
    with pytest.raises(lg.LookaheadError):
        lg.assert_causal({"bad": lg.centred_control}, bars)


def test_assert_causal_passes_a_clean_set(bars):
    lg.assert_causal({"ok": lg.causal_control}, bars)


def test_a_feature_that_raises_is_a_failure_not_a_pass(bars):
    """An exception must never read as "no leak found"."""
    def boom(_):
        raise ValueError("nope")

    found = lg.probe("boom", boom, bars)
    assert found.causal is False and found.kind == "error"
    with pytest.raises(lg.LookaheadError):
        lg.assert_causal({"boom": boom}, bars)


def test_a_feature_returning_nothing_is_a_failure(bars):
    found = lg.probe("empty", lambda d: np.array([]), bars)
    assert found.causal is False


# ── the shuffled-label control ─────────────────────────────────────────────

def test_shuffled_labels_that_still_make_money_are_a_leak():
    rng = np.random.default_rng(1)
    v = lg.shuffled_label_verdict(rng.normal(0.25, 1.0, 500), cost_r=0.02)
    assert v.passed is False
    assert "carrying the answer" in v.detail


def test_shuffled_labels_losing_their_costs_is_correct():
    rng = np.random.default_rng(2)
    v = lg.shuffled_label_verdict(rng.normal(-0.02, 1.0, 500), cost_r=0.02)
    assert v.passed is True


def test_losing_more_than_costs_on_shuffled_labels_is_not_a_leak():
    """One-sided on purpose. Doing badly on noise is not evidence of cheating."""
    rng = np.random.default_rng(4)
    v = lg.shuffled_label_verdict(rng.normal(-0.30, 1.0, 500), cost_r=0.02)
    assert v.passed is True


def test_too_few_trades_fails_rather_than_passes():
    """A verdict over 40 trades is not a verdict, and "inconclusive" must not
    be allowed to look like "clean"."""
    rng = np.random.default_rng(5)
    v = lg.shuffled_label_verdict(rng.normal(0.0, 1.0, 40), cost_r=0.02)
    assert v.passed is False
    assert "not enough" in v.detail


# ── the shifted-entry control ──────────────────────────────────────────────

def test_an_edge_that_vanishes_with_one_bar_of_delay_is_a_leak():
    rng = np.random.default_rng(6)
    base = rng.normal(0.10, 1.0, 400)
    v = lg.shift_verdict(base, base * 0.05)
    assert v.passed is False
    assert "not an edge" in v.detail


def test_an_edge_that_merely_degrades_passes():
    """Calibrated against the real scar: one bar late cost 10R over 152
    GBPJPY trades and the edge survived. That must not read as a leak."""
    rng = np.random.default_rng(7)
    base = rng.normal(0.10, 1.0, 400)
    assert lg.shift_verdict(base, base * 0.75).passed is True


def test_no_edge_to_begin_with_is_not_a_leak():
    rng = np.random.default_rng(8)
    losing = rng.normal(-0.05, 1.0, 400)
    v = lg.shift_verdict(losing, losing * 0.1)
    assert v.passed is True
    assert "no edge to collapse" in v.detail


def test_the_shift_control_also_refuses_a_thin_sample():
    rng = np.random.default_rng(9)
    small = rng.normal(0.1, 1.0, 50)
    assert lg.shift_verdict(small, small).passed is False


def test_a_single_cut_would_have_missed_the_sparse_backfill(bars):
    """Pins why the probe tries several cuts.

    `where(every 7th is NaN).bfill()` is a plain lookahead, but it only shows
    when a gap touches the END of the prefix. Cut in the wrong place and the
    two runs agree. This is the case that caught the first version out.
    """
    leaky = lambda d: d["close"].where(d.index % 7 != 0).bfill()  # noqa: E731

    # the cut the old single-cut probe used: passes, wrongly
    assert lg.probe("bfill", leaky, bars, cut=300).causal is True
    # the probe as it stands, free to choose: catches it
    assert lg.probe("bfill", leaky, bars).causal is False


def test_several_cuts_do_not_make_a_causal_feature_look_leaky(bars):
    """More cuts must not mean more false alarms."""
    for name, fn in (("trailing", lg.causal_control),
                     ("ffill", lambda d: d["close"].where(d.index % 7 != 0).ffill())):
        assert lg.probe(name, fn, bars).causal is True, name
