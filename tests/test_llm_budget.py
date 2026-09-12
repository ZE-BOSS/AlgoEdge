"""
tests/test_llm_budget.py — [P3.12]

The cap that stops the forecasting agent spending money by accident. 8
instruments on a 15-minute trigger is 768 calls/day unguarded, and the
expensive failures are silent ones: a retry loop, a double-firing scheduler, a
context builder that stops truncating.
"""

import json

import pytest

from backend.services.llm_budget import BudgetExceeded, LLMBudget, estimate_cost


@pytest.fixture
def budget(tmp_path):
    return LLMBudget(daily_cost_cap_usd=1.0, max_tokens_per_call=5_000,
                     max_calls_per_day=10, state_path=tmp_path / "b.json")


# ── pricing ──────────────────────────────────────────────────────────────────

def test_cost_uses_the_published_rates():
    # Opus 5: $5/Mtok in, $25/Mtok out
    assert estimate_cost("anthropic", "claude-opus-5", 1_000_000, 100_000) == pytest.approx(7.5)
    assert estimate_cost("anthropic", "claude-haiku-4-5", 1_000_000, 100_000) == pytest.approx(1.5)


def test_unknown_models_price_at_the_registry_maximum():
    """A pricing table that silently prices an unrecognised model at zero is a
    budget that does not bind."""
    unknown = estimate_cost("anthropic", "some-future-model", 1_000_000, 100_000)
    opus = estimate_cost("anthropic", "claude-opus-5", 1_000_000, 100_000)
    assert unknown >= opus > 0


def test_zero_tokens_cost_nothing():
    assert estimate_cost("anthropic", "claude-opus-5", 0, 0) == 0.0


# ── the gate ─────────────────────────────────────────────────────────────────

def test_a_fresh_budget_permits_calls(budget):
    budget.check("anthropic", "claude-opus-5", 1_000)   # must not raise


def test_per_call_token_ceiling_refuses_before_spending(budget):
    with pytest.raises(BudgetExceeded, match="per-call ceiling"):
        budget.check("anthropic", "claude-opus-5", 50_000)
    assert budget.status()["calls"] == 0, "a refused call must not be booked"


def test_cost_cap_trips_the_breaker_and_it_latches(budget):
    budget.record("anthropic", "claude-opus-5", 1_000_000, 0, caller="test")  # $5 > $1 cap
    s = budget.status()
    assert s["tripped"] is True
    assert "cost cap" in s["trip_reason"]
    with pytest.raises(BudgetExceeded, match="circuit breaker is OPEN"):
        budget.check("anthropic", "claude-haiku-4-5", 100)
    # latched: even a trivially cheap call stays refused for the rest of the day
    with pytest.raises(BudgetExceeded):
        budget.check("anthropic", "claude-haiku-4-5", 1)


def test_call_count_cap_trips_independently_of_cost(budget):
    for _ in range(10):
        budget.record("anthropic", "claude-haiku-4-5", 10, 10, caller="test")
    assert budget.status()["cost_usd"] < 1.0, "cost is nowhere near the cap"
    with pytest.raises(BudgetExceeded, match="call cap"):
        budget.check("anthropic", "claude-haiku-4-5", 100)


# ── accounting ───────────────────────────────────────────────────────────────

def test_spend_is_attributed_per_model_and_per_caller(budget):
    budget.record("anthropic", "claude-haiku-4-5", 1000, 500, caller="XAUUSD")
    budget.record("anthropic", "claude-opus-5", 2000, 800, caller="XAUUSD")
    budget.record("anthropic", "claude-haiku-4-5", 500, 100, caller="EURUSD")
    s = budget.status()
    assert s["calls"] == 3
    assert set(s["by_model"]) == {"claude-haiku-4-5", "claude-opus-5"}
    assert set(s["by_caller"]) == {"XAUUSD", "EURUSD"}
    assert s["by_caller"]["XAUUSD"]["calls"] == 2
    assert s["by_caller"]["XAUUSD"]["cost_usd"] > s["by_caller"]["EURUSD"]["cost_usd"]


def test_remaining_budget_is_reported(budget):
    budget.record("anthropic", "claude-haiku-4-5", 100_000, 20_000, caller="t")
    s = budget.status()
    assert s["remaining_usd"] == pytest.approx(1.0 - s["cost_usd"], abs=1e-6)


# ── persistence ──────────────────────────────────────────────────────────────

def test_spend_survives_a_process_restart(tmp_path):
    """A crash loop must not get an unlimited budget by restarting."""
    path = tmp_path / "b.json"
    a = LLMBudget(daily_cost_cap_usd=1.0, state_path=path)
    a.record("anthropic", "claude-haiku-4-5", 100_000, 10_000, caller="t")
    spent = a.status()["cost_usd"]
    assert spent > 0

    b = LLMBudget(daily_cost_cap_usd=1.0, state_path=path)   # fresh process
    assert b.status()["cost_usd"] == pytest.approx(spent)
    assert b.status()["calls"] == 1


def test_a_tripped_breaker_survives_a_restart(tmp_path):
    path = tmp_path / "b.json"
    a = LLMBudget(daily_cost_cap_usd=0.01, state_path=path)
    a.record("anthropic", "claude-opus-5", 100_000, 10_000, caller="t")
    assert a.status()["tripped"]
    with pytest.raises(BudgetExceeded):
        LLMBudget(daily_cost_cap_usd=0.01, state_path=path).check("anthropic", "claude-haiku-4-5", 10)


def test_state_from_a_previous_day_does_not_carry_over(tmp_path):
    path = tmp_path / "b.json"
    path.write_text(json.dumps({
        "date": "1999-01-01", "cost_usd": 999.0, "calls": 9999, "tripped": True,
        "trip_reason": "ancient", "input_tokens": 1, "output_tokens": 1,
        "by_model": {}, "by_caller": {},
    }), encoding="utf-8")
    b = LLMBudget(daily_cost_cap_usd=1.0, state_path=path)
    assert b.status()["cost_usd"] == 0.0
    assert b.status()["tripped"] is False
    b.check("anthropic", "claude-haiku-4-5", 10)


def test_corrupt_state_file_does_not_crash(tmp_path):
    path = tmp_path / "b.json"
    path.write_text("{not json", encoding="utf-8")
    b = LLMBudget(daily_cost_cap_usd=1.0, state_path=path)
    assert b.status()["cost_usd"] == 0.0


def test_reset_is_available_but_explicit(budget):
    budget.record("anthropic", "claude-opus-5", 1_000_000, 0, caller="t")
    assert budget.status()["tripped"]
    budget.reset_today()
    assert budget.status()["tripped"] is False
    assert budget.status()["cost_usd"] == 0.0
    budget.check("anthropic", "claude-haiku-4-5", 10)


# ── enforced at the point of spend ───────────────────────────────────────────

def test_the_anthropic_call_path_checks_and_records():
    """The gate must live in _call_anthropic, not in whichever caller remembers."""
    import inspect

    from backend.services import llm_service

    src = inspect.getsource(llm_service.LLMService._call_anthropic)
    assert "llm_budget.check(" in src, "must refuse before spending"
    assert "llm_budget.record(" in src, "must book actual usage after the call"
    i_check = src.index("llm_budget.check(")
    i_stream = src.index("client.messages.stream")
    assert i_check < i_stream, "the check must precede the request, not follow it"


def test_refusal_is_a_message_not_an_exception_to_the_caller():
    """A budget refusal must not look like a crash to the analysis UI."""
    import inspect

    from backend.services import llm_service

    src = inspect.getsource(llm_service.LLMService._call_anthropic)
    assert "LLM call refused" in src
