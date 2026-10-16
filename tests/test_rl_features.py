"""
tests/test_rl_features.py

The observation: what the agent sees, and what it must never see.

WHY THESE TESTS AND NOT OTHERS
------------------------------
Three properties, each of which fails silently if it breaks:

  * every feature is prefix-stable - a leak makes the backtest better and live
    worse, and never announces itself;
  * no feature carries money management - the claim "the agent cannot breach a
    risk limit" is only true if the numbers are genuinely absent;
  * the scaler is fitted on the training fold - the one-line mistake that looks
    like a normalisation and is a lookahead.

The counterfactual separation is tested here rather than in test_rl_reward,
because the thing being asserted is a property of the OBSERVATION: it must be
identical whether or not the reward module has been imported.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.rl import features as F
from backend.rl import leakguard as lg


@pytest.fixture
def bars():
    return lg.synthetic_bars(400)


# ── causality ──────────────────────────────────────────────────────────────

def test_every_registered_feature_is_prefix_stable():
    """The one that matters. A new feature that leaks fails here before it can
    be named on an AgentSpec."""
    findings = F.assert_registry_causal()
    leaking = [f.summary for f in findings if not f.causal]
    assert not leaking, "\n".join(leaking)
    assert len(findings) == len(F.FEATURES)


def test_the_probe_is_checked_before_the_features_are():
    """`assert_registry_causal` runs `self_check` first. A probe that has
    stopped detecting leaks would otherwise pass everything."""
    import backend.rl.leakguard as lgmod

    calls = []
    real = lgmod.self_check
    try:
        lgmod.self_check = lambda frame=None: calls.append(1) or real(frame)
        F.assert_registry_causal()
    finally:
        lgmod.self_check = real
    assert calls, "the feature scan must check the probe's own controls first"


def test_no_banned_primitive_in_the_feature_source():
    """A leading back-fill contaminates only the first few bars, which a cut
    three quarters of the way in never reaches - so it is caught by name."""
    F.assert_no_banned_primitives()


@pytest.mark.parametrize("name", sorted(F.FEATURES))
def test_each_feature_returns_one_value_per_bar(bars, name):
    v = np.asarray(F.FEATURES[name](bars), dtype=float).ravel()
    assert len(v) == len(bars), name


# ── no money management ────────────────────────────────────────────────────

FORBIDDEN = ("balance", "equity", "drawdown", "lot", "volume_usd", "risk_pct",
             "margin", "pnl", "profit", "dollar", "stop_distance", "position_size")


def test_no_feature_names_anything_about_money():
    """Not a style check. "The agent has no access to money management" is only
    true if there is nowhere to put it."""
    for name, f in F.FEATURES.items():
        lowered = f"{name} {f.plain}".lower()
        for word in FORBIDDEN:
            if word == "volume_usd":
                continue
            assert word not in lowered.replace("volume", ""), (name, word)


def test_the_observation_has_no_account_state(bars):
    """A frame with a balance column must not change the observation."""
    X = F.matrix(bars)
    richer = bars.copy()
    richer["balance"] = 50_000.0
    richer["equity"] = 49_000.0
    richer["risk_pct"] = 1.8
    assert np.allclose(X, F.matrix(richer), equal_nan=True)


def test_position_vector_is_in_R_and_carries_no_dollars():
    """R is a ratio of prices, which is why it is allowed; a dollar amount
    would leak account size into the policy."""
    small = F.position_vector({"entry_price": 100.0, "stop_loss": 99.0,
                               "take_profit": 103.0, "direction": "BUY"}, price=101.0)
    big = F.position_vector({"entry_price": 10000.0, "stop_loss": 9900.0,
                             "take_profit": 10300.0, "direction": "BUY"}, price=10100.0)
    assert np.allclose(small, big), (
        "the same trade at a different price scale must look identical - that is "
        "what lets one policy transfer between GBPJPY and Crash 1000")


def test_a_degenerate_stop_reads_as_no_information():
    """A zero-width stop makes every R infinite. The research harness has faked
    whole results this way."""
    v = F.position_vector({"entry_price": 100.0, "stop_loss": 100.0,
                           "direction": "BUY"}, price=101.0)
    assert np.allclose(v, 0.0)


# ── the scaler ─────────────────────────────────────────────────────────────

def test_the_scaler_refuses_to_transform_before_it_is_fitted(bars):
    with pytest.raises(RuntimeError, match="training fold"):
        F.Scaler().transform(F.matrix(bars))


def test_a_scaler_fitted_on_a_fold_does_not_see_the_rest(bars):
    """The whole-dataset-scaling leak, as a measurement.

    A scaler fitted on the first 200 bars must give the same answer for those
    bars whether or not bars 200-400 exist. If it does not, every observation
    in the training fold carries information from the test fold.
    """
    X = F.matrix(bars)
    fitted_on_part = F.Scaler().fit(X[:200], F.DEFAULT_FEATURES)
    a = fitted_on_part.transform(X[:200])
    fitted_again = F.Scaler().fit(X[:200], F.DEFAULT_FEATURES)
    b = fitted_again.transform(X[:200])
    assert np.allclose(a, b)
    whole = F.Scaler().fit(X, F.DEFAULT_FEATURES)
    assert not np.allclose(whole.transform(X[:200]), a), (
        "fitting on the whole series must give a DIFFERENT answer - if it did not, "
        "this test could not detect the leak it exists to detect")


def test_a_constant_column_does_not_divide_by_zero(bars):
    flat = bars.copy()
    flat["volume"] = 0.0
    X = F.matrix(flat)
    Z = F.Scaler().fit(X, F.DEFAULT_FEATURES).transform(X)
    assert np.isfinite(Z).all()


def test_prescaled_features_are_left_alone(bars):
    """Scaling a sine wave by its own inter-quartile range destroys the thing
    that made it useful."""
    X = F.matrix(bars)
    s = F.Scaler().fit(X, F.DEFAULT_FEATURES)
    for i, n in enumerate(F.DEFAULT_FEATURES):
        if F.FEATURES[n].prescaled:
            assert s.centre[i] == 0.0 and s.scale[i] == 1.0, n


def test_the_scaler_round_trips_through_json(bars):
    X = F.matrix(bars)
    s = F.Scaler().fit(X, F.DEFAULT_FEATURES, fitted_on="rows 0-400")
    back = F.Scaler.from_dict(s.to_dict())
    assert back.fitted_on == "rows 0-400"
    assert np.allclose(back.transform(X), s.transform(X))


def test_the_scaler_records_what_it_was_fitted_on(bars):
    """Provenance, not a comment: a scaler fitted on the test fold should be
    visible in the artefact rather than only in the code that made it."""
    s = F.Scaler().fit(F.matrix(bars), F.DEFAULT_FEATURES, fitted_on="fold 3 train")
    assert "fold 3" in s.to_dict()["fitted_on"]


def test_an_extreme_value_is_clipped_rather_than_dominating(bars):
    X = F.matrix(bars)
    s = F.Scaler().fit(X, F.DEFAULT_FEATURES)
    X2 = X.copy()
    X2[10, 0] = 1e9
    assert abs(s.transform(X2)[10, 0]) <= s.clip + 1e-9


# ── higher timeframes ──────────────────────────────────────────────────────

def test_htf_merge_uses_only_bars_that_had_closed():
    """The usual mistake is an as-of join on the HTF bar's OPEN, which hands a
    primary bar an H1 bar that is still forming."""
    m5 = lg.synthetic_bars(120, step_seconds=300)
    h1 = lg.synthetic_bars(10, step_seconds=3600)
    out = F.htf_merge(m5, h1, 60, columns=("close",))
    # The first 12 M5 bars fall inside the first H1 bar, which has not closed.
    assert out["htf_close"].iloc[:12].isna().all()
    assert out["htf_close"].iloc[12] == pytest.approx(h1["close"].iloc[0])


def test_htf_merge_rejects_a_close_stamped_htf_frame():
    """An HTF frame already stamped by close looks one bar early, so the merge
    would hand over a bar that had not finished."""
    m5 = lg.synthetic_bars(120, step_seconds=300)
    h1 = lg.synthetic_bars(10, step_seconds=3600)
    shifted = h1.copy()
    shifted["time"] = shifted["time"] + 3600          # pretend the stamp is the close
    out = F.htf_merge(m5, shifted, 60, columns=("close",))
    # Shifted forward, fewer primary bars have a usable HTF bar - never more.
    assert int(out["htf_close"].isna().sum()) >= 12


def test_htf_merge_is_prefix_stable():
    """It is part of the observation, so the same rule applies to it."""
    m5 = lg.synthetic_bars(300, step_seconds=300)
    h1 = lg.synthetic_bars(30, step_seconds=3600)
    full = F.htf_merge(m5, h1, 60)["htf_close"].to_numpy()
    part = F.htf_merge(m5.iloc[:200].copy(), h1, 60)["htf_close"].to_numpy()
    assert np.allclose(full[:200], part, equal_nan=True)


# ── the counterfactual is not in the observation ───────────────────────────

def test_the_observation_is_identical_with_and_without_the_reward_module(bars, monkeypatch):
    """The structural separation the plan asks for, as an assertion.

    `reward.py` computes `exit_quality` and `saved_R` from bars AFTER the
    decision. Legitimate in a reward, fatal in an observation - so importing it
    must not be able to change what the agent sees.

    Through `monkeypatch`, so the ORIGINAL modules are put back afterwards.
    A bare `sys.modules.pop` left a second copy of `backend.rl.env` behind,
    and any later test that pickles an `EpisodeData` (the parallel trainer)
    failed with "it's not the same object as backend.rl.env.EpisodeData".
    """
    import sys

    import backend.rl as pkg

    for mod in ("backend.rl.reward", "backend.rl.env"):
        monkeypatch.delitem(sys.modules, mod, raising=False)
        # The re-import below also rebinds the PACKAGE attribute
        # (`backend.rl.env`), which sys.modules alone does not restore - so
        # `from backend.rl import env` kept returning the second copy.
        attr = mod.rsplit(".", 1)[1]
        if hasattr(pkg, attr):
            monkeypatch.setattr(pkg, attr, getattr(pkg, attr))
    before = F.matrix(bars)
    import backend.rl.reward  # noqa: F401
    import backend.rl.env     # noqa: F401
    after = F.matrix(bars)
    assert np.array_equal(before, after)


def test_features_does_not_import_the_counterfactual():
    """Checked in the source as well as in behaviour: a future edit that added
    the import would pass the behavioural test on the day it was written."""
    from pathlib import Path

    src = Path(F.__file__).read_text(encoding="utf-8")
    body = src.split("# -- the guard, as callable API", 1)[0]
    assert "backend.rl.reward" not in body
    assert "from backend.rl import reward" not in body


# ── shape and robustness ───────────────────────────────────────────────────

def test_an_unknown_feature_name_is_refused(bars):
    with pytest.raises(KeyError, match="not registered"):
        F.matrix(bars, ["ret_1", "made_up_thing"])


def test_a_missing_volume_column_is_ones_not_an_error():
    """Synthetic indices carry a tick count, not volume, and some feeds carry
    nothing. One feature list has to work across instruments."""
    bars = lg.synthetic_bars(100).drop(columns=["volume"])
    v = F.volume_ratio(bars)
    assert np.allclose(v, 1.0)


def test_an_empty_frame_gives_an_empty_matrix():
    X = F.matrix(pd.DataFrame(columns=["time", "open", "high", "low", "close"]))
    assert X.shape == (0, len(F.DEFAULT_FEATURES))


def test_no_feature_produces_a_non_finite_value_in_the_matrix():
    """A non-finite observation propagates into every weight it touches."""
    bars = lg.synthetic_bars(200)
    bars.loc[50:60, ["open", "high", "low", "close"]] = 0.0   # a dead session
    assert np.isfinite(F.matrix(bars)).all()
