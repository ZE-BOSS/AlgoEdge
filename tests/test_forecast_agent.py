"""
tests/test_forecast_agent.py — [P3.1 / P3.5 / P3.6]

The context the agent sees, and the validator between it and the risk engine.

Two properties matter more than the rest:
  1. the blinded rendering must not leak the instrument or the date, because the
     blinded-vs-plain accuracy gap is the leakage estimate;
  2. every failure path must abstain with an accurate reason, because an
     abstention rate driven by billing is not evidence about the model.
"""

import json

import numpy as np
import pytest

from backend.analytics.forecast_context import (
    CONTEXT_VERSION,
    ContextBuilder,
    ForecastContext,
)
from backend.services.forecast_agent import (
    ProposalValidator,
    extract_json,
)


def _bars(n=400, slope=0.0008, noise=0.004, seed=1, start=2400.0):
    rng = np.random.default_rng(seed)
    c = start * np.exp(np.cumsum(rng.normal(slope, noise, n)))
    h = c * (1 + abs(rng.normal(0, noise, n)))
    l = c * (1 - abs(rng.normal(0, noise, n)))
    v = rng.integers(500, 5000, n).astype(float)
    return list(h), list(l), list(c), list(v)


def _ctx(**kw):
    h, l, c, v = _bars(**kw)
    return ContextBuilder().build(instrument_class="metal", timeframe="H1",
                                  high=h, low=l, close=c, volume=v, session="LONDON")


# ── the context ──────────────────────────────────────────────────────────────

def test_context_builds_every_feature_block():
    ctx = _ctx()
    assert ctx is not None
    for block in ("trend", "volatility", "range", "volume_profile", "vwap",
                  "order_flow_proxy", "context"):
        assert block in ctx.features, block
    assert ctx.version == CONTEXT_VERSION


def test_context_is_deterministic():
    """Same bars in, same hash out — this is the cache key, the reproducibility
    guarantee and the tripwire for accidental feature drift."""
    assert _ctx().context_hash == _ctx().context_hash


def test_different_markets_give_different_hashes():
    assert _ctx(seed=1).context_hash != _ctx(seed=2).context_hash


def test_hash_ignores_absolute_price_level():
    """Two identical shapes at different price levels are the same forecasting
    problem and should share a cache entry."""
    a = _ctx(seed=7, start=2400.0)
    b = _ctx(seed=7, start=24.0)
    assert a.context_hash == b.context_hash
    assert a._private["last_close"] != b._private["last_close"]


def test_short_history_returns_none_rather_than_a_thin_context():
    h, l, c, v = _bars(n=30)
    assert ContextBuilder().build(instrument_class="fx", timeframe="H1",
                                  high=h, low=l, close=c, volume=v) is None


def test_context_without_volume_still_builds():
    h, l, c, _ = _bars()
    ctx = ContextBuilder().build(instrument_class="fx", timeframe="H1",
                                 high=h, low=l, close=c, volume=None)
    assert ctx is not None
    assert "no volume available" in ctx.features["volume_profile"]["weighted_by"]
    assert "order_flow_proxy" not in ctx.features


# ── blinding ─────────────────────────────────────────────────────────────────

def test_blinded_rendering_leaks_neither_name_nor_date():
    ctx = _ctx()
    out = ctx.render("blinded", "XAUUSD")
    assert "XAUUSD" not in out
    assert "INSTRUMENT_A" in out
    for token in ("2024", "2025", "2026", "Jan", "Monday"):
        assert token not in out


def test_blinded_rendering_carries_no_absolute_price():
    """Every number the model sees must be relative, or it can locate the market."""
    ctx = _ctx(start=2400.0)
    out = ctx.render("blinded")
    assert f"{ctx._private['last_close']:.0f}" not in out
    assert str(int(ctx._private["last_close"])) not in out


def test_plain_rendering_exists_only_to_measure_the_gap():
    ctx = _ctx()
    assert "XAUUSD" in ctx.render("plain", "XAUUSD")
    assert "XAUUSD" not in ctx.render("blinded", "XAUUSD")


def test_snapshot_is_replayable_and_carries_its_version():
    snap = _ctx().snapshot()
    assert snap["version"] == CONTEXT_VERSION
    assert snap["context_hash"] and snap["features"]
    json.dumps(snap)  # must serialise for the forward-test journal


# ── JSON extraction ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expect", [
    ('{"direction":"LONG"}', "LONG"),
    ('```json\n{"direction":"SHORT"}\n```', "SHORT"),
    ('Here is my view:\n{"direction":"FLAT"}\nHope that helps.', "FLAT"),
    ('{"direction":"LONG","nested":{"a":1}}', "LONG"),
])
def test_extract_json_tolerates_ordinary_model_behaviour(text, expect):
    assert extract_json(text)["direction"] == expect


@pytest.mark.parametrize("text", ["", "no json here", "{broken", None])
def test_extract_json_returns_none_rather_than_guessing(text):
    assert extract_json(text) is None


# ── the validator ────────────────────────────────────────────────────────────

def _v():
    return ProposalValidator()


def test_valid_long_converts_to_absolute_levels():
    ctx = _ctx()
    f = _v().validate({"direction": "LONG", "conviction": 0.6,
                       "invalidation_atr": -2.0, "target_atr": 4.0,
                       "regime": "TRENDING", "contradicting_evidence": ["x"]},
                      ctx, "XAUUSD")
    last, atr = ctx._private["last_close"], ctx._private["atr"]
    assert f.direction == "LONG" and f.is_actionable
    assert f.invalidation == pytest.approx(last - 2.0 * atr)
    assert f.targets[0]["level"] == pytest.approx(last + 4.0 * atr)
    assert f.source == "llm"


def test_valid_short_mirrors():
    ctx = _ctx()
    f = _v().validate({"direction": "SHORT", "conviction": 0.5,
                       "invalidation_atr": 2.0, "target_atr": -3.0}, ctx, "X")
    last, atr = ctx._private["last_close"], ctx._private["atr"]
    assert f.invalidation == pytest.approx(last + 2.0 * atr)
    assert f.targets[0]["level"] == pytest.approx(last - 3.0 * atr)


def test_inverted_geometry_abstains_rather_than_being_corrected():
    """A LONG with its stop above entry means the model misunderstood the task.
    Silently flipping it would hide that."""
    f = _v().validate({"direction": "LONG", "conviction": 0.8,
                       "invalidation_atr": 2.0, "target_atr": 4.0}, _ctx(), "X")
    assert f.direction == "FLAT"
    assert "wrong sides" in f.abstain_reason


@pytest.mark.parametrize("conviction", [0.0, -0.1, 1.5])
def test_out_of_range_conviction_abstains(conviction):
    f = _v().validate({"direction": "LONG", "conviction": conviction,
                       "invalidation_atr": -2.0, "target_atr": 4.0}, _ctx(), "X")
    assert f.direction == "FLAT"


def test_absurd_stop_distance_abstains():
    for stop in (-0.05, -50.0):
        f = _v().validate({"direction": "LONG", "conviction": 0.5,
                           "invalidation_atr": stop, "target_atr": 4.0}, _ctx(), "X")
        assert f.direction == "FLAT", stop


def test_poor_reward_to_risk_abstains():
    f = _v().validate({"direction": "LONG", "conviction": 0.9,
                       "invalidation_atr": -4.0, "target_atr": 1.0}, _ctx(), "X")
    assert f.direction == "FLAT"
    assert "reward:risk" in f.abstain_reason


def test_flat_is_accepted_as_a_real_answer():
    f = _v().validate({"direction": "FLAT", "abstain_reason": "no edge visible"},
                      _ctx(), "X")
    assert f.direction == "FLAT"
    assert f.abstain_reason == "no edge visible"
    assert not f.is_actionable


@pytest.mark.parametrize("raw", [
    None, {}, {"direction": "SIDEWAYS"},
    {"direction": "LONG", "conviction": "high", "invalidation_atr": -2, "target_atr": 4},
    {"direction": "LONG", "conviction": 0.5, "invalidation_atr": None, "target_atr": 4},
])
def test_malformed_proposals_abstain_with_a_reason(raw):
    f = _v().validate(raw, _ctx(), "X")
    assert f.direction == "FLAT"
    assert f.abstain_reason


def test_validator_needs_a_price_anchor():
    ctx = _ctx()
    ctx._private.clear()
    f = _v().validate({"direction": "LONG", "conviction": 0.5,
                       "invalidation_atr": -2.0, "target_atr": 4.0}, ctx, "X")
    assert f.direction == "FLAT"
    assert "price anchor" in f.abstain_reason


# ── operational failures must not masquerade as model behaviour ──────────────

def test_provider_errors_are_reported_as_themselves():
    """An abstention rate driven by billing is not evidence about the model."""
    import inspect

    from backend.services import forecast_agent

    src = inspect.getsource(forecast_agent.ForecastAgent.forecast)
    for prefix in ("LLM call refused", "Analysis failed", "No Anthropic API key"):
        assert prefix in src, f"{prefix} must be distinguished from a parse failure"


def test_agent_never_sizes_or_places():
    """The division of authority is the design. Assert it in the source."""
    import inspect

    from backend.services import forecast_agent

    src = inspect.getsource(forecast_agent)
    for forbidden in ("lot", "volume=", "order_send", "place_market_order", "risk_per_trade"):
        assert forbidden not in src, f"the forecasting agent must not reference {forbidden!r}"


def test_system_prompt_forbids_guessing_the_instrument():
    from backend.services.forecast_agent import SYSTEM_PROMPT

    low = SYSTEM_PROMPT.lower()
    assert "do not guess the instrument" in low
    assert "flat is a real" in low
    assert "calibrated" in low
