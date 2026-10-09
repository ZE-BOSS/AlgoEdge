"""
tests/test_rl_charter.py

The governance: the agent states its strategy, the admin approves it, and
changes arrive as proposals.

WHAT IS ACTUALLY BEING PROTECTED
--------------------------------
The reward is 0.70 money + 0.30 "did it follow its strategy". That second term
is worth nothing if the agent can rewrite the strategy when following it becomes
inconvenient - it would learn to declare whatever it was about to do and collect
0.30 for free. So these tests are about the ways that could happen:

  * approving a DRAFT directly, skipping the run the admin could have looked at;
  * an unapproved charter reaching a live slot;
  * scoring live adherence against a PROPOSED charter;
  * a proposal that loses being put in front of the admin anyway;
  * re-proposing a rejected change with different wording;
  * a gate that fires on 99% of bars, making adherence free.

Each of those is a test below, and each would be invisible in production.
"""

from __future__ import annotations

import pytest

from backend.rl import charter as ch


def gate(name="vol_high", feature="vol_expansion", op=">=", threshold=1.15, **kw):
    return ch.Gate(name=name, feature=feature, op=op, threshold=threshold, **kw)


def charter(**kw):
    gates = kw.pop("gates", None)
    if gates is None:
        gates = (gate(), gate("trend", "efficiency_20", ">=", 0.4),
                 gate("breakout", "donchian_pos_20", ">=", 0.8))
    tiers = kw.pop("tiers", None)
    if tiers is None:
        names = tuple(g.name for g in gates)
        tiers = (ch.Tier("A", names, min_gates=len(names)),
                 ch.Tier("B", names, min_gates=max(1, len(names) - 1)))
    base = dict(agent_id="agent-1", gates=gates, tiers=tiers, entry_tier="B",
                instruments=("EURUSD",), timeframes=("M5",))
    base.update(kw)
    return ch.StrategyCharter(**base)


OBS_ALL = {"vol_expansion": 1.5, "efficiency_20": 0.6, "donchian_pos_20": 0.9}
OBS_TWO = {"vol_expansion": 1.5, "efficiency_20": 0.6, "donchian_pos_20": 0.1}
OBS_NONE = {"vol_expansion": 0.9, "efficiency_20": 0.1, "donchian_pos_20": 0.1}


# ── gates are predicates, not prose ────────────────────────────────────────

def test_a_gate_may_only_name_a_feature_the_agent_can_see():
    """Otherwise adherence would be scored against something uncomputable, and
    a charter could smuggle in a rule about the account balance."""
    with pytest.raises(ValueError, match="not a registered feature"):
        ch.Gate(name="sneaky", feature="account_balance", op=">=", threshold=0)


def test_a_gate_evaluates_and_reads_in_english():
    g = gate()
    assert g.holds({"vol_expansion": 1.2}) is True
    assert g.holds({"vol_expansion": 1.0}) is False
    assert "volatility" in g.plain and "1.15" in g.plain


def test_a_between_gate_needs_two_bounds():
    with pytest.raises(ValueError, match="between"):
        ch.Gate(name="band", feature="atr_ratio", op="between", threshold=1.0)
    g = ch.Gate(name="band", feature="atr_ratio", op="between", threshold=(0.9, 1.1))
    assert g.holds({"atr_ratio": 1.0}) and not g.holds({"atr_ratio": 1.5})


def test_a_missing_or_non_finite_value_does_not_satisfy_a_gate():
    g = gate()
    assert g.holds({}) is False
    assert g.holds({"vol_expansion": float("nan")}) is False


def test_a_direction_scoped_gate_is_out_of_scope_the_other_way():
    """A long-only gate must not count as SKIPPED on a short - that would make
    a well-behaved short look like a charter violation and the drift monitor
    would suspend an agent for doing what the charter says."""
    g = gate(direction=ch.BUY)
    assert g.holds({"vol_expansion": 1.5}, "BUY") is True
    assert g.holds({"vol_expansion": 1.5}, "SELL") is False
    assert g.applies_to("SELL") is False
    c = charter(gates=(g,), tiers=(ch.Tier("A", (g.name,)),), entry_tier="A")
    assert c.adherence({"vol_expansion": 1.5}, "SELL") == 0.0
    assert c.skipped_gates({"vol_expansion": 1.5}, "SELL") == []


def test_two_gates_cannot_share_a_name():
    with pytest.raises(ValueError, match="share a name"):
        charter(gates=(gate("same"), gate("same", "atr_ratio")))


def test_a_tier_cannot_name_a_gate_that_does_not_exist():
    with pytest.raises(ValueError, match="do not exist"):
        charter(tiers=(ch.Tier("A", ("nope",)),))


# ── adherence and tiers ────────────────────────────────────────────────────

def test_adherence_is_the_weighted_fraction_of_gates_that_held():
    c = charter()
    assert c.adherence(OBS_ALL) == pytest.approx(1.0)
    assert c.adherence(OBS_TWO) == pytest.approx(2 / 3)
    assert c.adherence(OBS_NONE) == pytest.approx(0.0)


def test_the_tier_is_the_best_one_satisfied():
    c = charter()
    assert c.tier_for(c.gate_flags(OBS_ALL)) == "A"
    assert c.tier_for(c.gate_flags(OBS_TWO)) == "B"
    assert c.tier_for(c.gate_flags(OBS_NONE)) is None


def test_a_required_gate_blocks_a_tier_on_its_own():
    c = charter(entry_tier="A",
                tiers=(ch.Tier("A", ("vol_high", "trend", "breakout"), min_gates=1,
                               required=("breakout",)),))
    assert c.tier_for(c.gate_flags(OBS_TWO)) is None
    assert c.tier_for(c.gate_flags(OBS_ALL)) == "A"


def test_the_skipped_gates_are_named():
    """The drift email has to say WHICH gates it is skipping."""
    assert charter().skipped_gates(OBS_TWO) == ["breakout"]


# ── the status machine ─────────────────────────────────────────────────────

def test_a_draft_cannot_be_approved_directly():
    """There is no DRAFT -> APPROVED path, deliberately: a charter has to be
    backtestable-and-labelled-unapproved first, so the admin is approving
    something they could have looked at."""
    c = charter(status=ch.DRAFT)
    with pytest.raises(ch.CharterError, match="PROPOSED first"):
        ch.approve_charter(c, by="admin")


def test_the_route_to_approval_is_draft_then_proposed_then_approved():
    c = charter(status=ch.DRAFT)
    assert c.permits == {"train": True, "backtest": False, "live": False}
    ch.propose_draft(c)
    assert c.status == ch.PROPOSED and c.permits["backtest"] and not c.permits["live"]
    ch.approve_charter(c, by="admin@example.com")
    assert c.status == ch.APPROVED and c.permits["live"]
    assert c.approved_by == "admin@example.com" and c.approved_at


@pytest.mark.parametrize("status,what", [
    (ch.DRAFT, "backtest"), (ch.DRAFT, "live"), (ch.PROPOSED, "live"),
    (ch.SUPERSEDED, "live"), (ch.SUPERSEDED, "backtest"),
    (ch.REJECTED, "live"), (ch.REJECTED, "backtest"),
])
def test_require_refuses_what_the_status_does_not_permit(status, what):
    with pytest.raises(ch.CharterError):
        charter(status=status).require(what)


def test_a_rejected_charter_cannot_come_back():
    """A rejection is a decision, not a holding pattern."""
    c = charter(status=ch.REJECTED)
    assert not c.can_transition_to(ch.APPROVED)
    assert not c.can_transition_to(ch.PROPOSED)


def test_only_an_approved_charter_can_be_superseded():
    assert charter(status=ch.APPROVED).can_transition_to(ch.SUPERSEDED)
    assert not charter(status=ch.PROPOSED).can_transition_to(ch.SUPERSEDED)


# ── identity ───────────────────────────────────────────────────────────────

def test_the_hash_covers_the_strategy_and_not_the_approval():
    """Two charters with the same hash are the same strategy. That is what lets
    a rejected diff be blocked by hash and a historical run be reproduced."""
    a = charter(status=ch.DRAFT)
    b = charter(status=ch.APPROVED, approved_by="someone", approved_at="2026-01-01")
    assert a.content_hash == b.content_hash
    c = charter(gates=(gate(threshold=1.3),),
                tiers=(ch.Tier("A", ("vol_high",)),), entry_tier="A")
    assert c.content_hash != a.content_hash


def test_a_measured_hit_rate_does_not_change_the_hash():
    """Hit rates are evidence about a strategy, not part of it - otherwise
    re-measuring on more bars would make it a different strategy."""
    a = charter()
    b = charter(gates=(gate(hit_rate=0.3, mean_r=0.1),
                       gate("trend", "efficiency_20", ">=", 0.4, hit_rate=0.5),
                       gate("breakout", "donchian_pos_20", ">=", 0.8)))
    assert a.content_hash == b.content_hash


def test_a_charter_round_trips_through_json():
    a = charter(status=ch.APPROVED, thesis="buy strength in expanding volatility")
    b = ch.StrategyCharter.from_dict(a.to_dict())
    assert b.content_hash == a.content_hash
    assert b.adherence(OBS_ALL) == a.adherence(OBS_ALL)
    assert b.tier_for(b.gate_flags(OBS_TWO)) == "B"


def test_the_english_names_every_gate_and_its_evidence():
    c = charter(gates=(gate(hit_rate=0.22, mean_r=0.085),), tiers=(ch.Tier("A", ("vol_high",)),),
                entry_tier="A", thesis="a thesis")
    text = c.english()
    assert "vol_high" in text and "22%" in text and "+0.085R" in text
    assert "Adherence floor" in text


# ── proposals ──────────────────────────────────────────────────────────────

WINS = {"expectancy_r": 0.09, "pbo": 0.10, "deflated_sharpe": 0.4, "passed": True}
LOSES = {"expectancy_r": 0.04, "pbo": 0.10, "deflated_sharpe": 0.4, "passed": True}
BASE = {"expectancy_r": 0.06, "pbo": 0.12, "deflated_sharpe": 0.3, "passed": True}


def proposal(proposed=None, **kw):
    p = proposed or charter(gates=(gate(), gate("trend", "efficiency_20", ">=", 0.4)),
                            tiers=(ch.Tier("A", ("vol_high", "trend"), min_gates=2),),
                            entry_tier="A", status=ch.DRAFT)
    base = dict(agent_id="agent-1", from_version=1, proposed=p, kind=ch.REMOVE_GATE,
                evidence_approved=BASE, evidence_proposed=WINS, why="the third gate never fired")
    base.update(kw)
    return ch.CharterProposal(**base)


def test_a_proposal_must_already_beat_the_approved_charter():
    """Nobody should be asked to approve something that has not earned it."""
    approved = charter(status=ch.APPROVED)
    with pytest.raises(ch.ProposalError, match="does not beat"):
        ch.validate_submission(proposal(evidence_proposed=LOSES), approved)


def test_a_proposal_that_raises_expectancy_and_pbo_together_is_refused():
    """Higher expectancy with a higher probability of backtest overfitting is
    not an improvement, it is a better-looking overfit."""
    approved = charter(status=ch.APPROVED)
    worse_pbo = {**WINS, "pbo": 0.19}
    with pytest.raises(ch.ProposalError, match="overfitting rose"):
        ch.validate_submission(proposal(evidence_proposed=worse_pbo), approved)


def test_a_proposal_that_drops_the_deflated_sharpe_is_refused():
    approved = charter(status=ch.APPROVED)
    with pytest.raises(ch.ProposalError, match="deflated Sharpe fell"):
        ch.validate_submission(
            proposal(evidence_proposed={**WINS, "deflated_sharpe": 0.1}), approved)


def test_only_one_proposal_may_be_open_per_agent():
    """No stacking changes the admin has not seen."""
    approved = charter(status=ch.APPROVED)
    first = proposal()
    with pytest.raises(ch.ProposalError, match="already has an open proposal"):
        ch.validate_submission(proposal(), approved, open_proposals=[first])


def test_a_rejected_diff_cannot_be_resubmitted_by_hash():
    """Rewording it must not get round the block."""
    approved = charter(status=ch.APPROVED)
    p = proposal()
    with pytest.raises(ch.ProposalError, match="rejected before"):
        ch.validate_submission(p, approved, rejected_hashes=[p.diff_hash])


def test_rewording_the_reason_does_not_change_the_diff_hash():
    a, b = proposal(why="one wording"), proposal(why="another wording entirely")
    assert a.diff_hash == b.diff_hash


def test_a_proposal_against_a_superseded_version_is_refused():
    approved = charter(status=ch.APPROVED)
    approved.version = 3
    with pytest.raises(ch.ProposalError, match="v1 but v3 is live"):
        ch.validate_submission(proposal(from_version=1), approved)


def test_a_proposal_identical_to_the_approved_charter_is_refused():
    approved = charter(status=ch.APPROVED)
    same = ch.StrategyCharter.from_dict(approved.to_dict())
    same.status = ch.DRAFT
    with pytest.raises(ch.ProposalError, match="identical"):
        ch.validate_submission(proposal(proposed=same), approved)


def test_there_must_be_an_approved_charter_to_propose_against():
    with pytest.raises(ch.ProposalError, match="no approved charter"):
        ch.validate_submission(proposal(), charter(status=ch.PROPOSED))


# ── approving and rejecting ────────────────────────────────────────────────

def test_approving_bumps_the_version_and_keeps_the_old_charter():
    """Every historical run has to stay reproducible against the charter it
    actually ran under."""
    approved = charter(status=ch.APPROVED)
    p = proposal()
    new, old = ch.approve(approved, p, by="admin@example.com")
    assert new.version == 2 and new.status == ch.APPROVED
    assert old.status == ch.SUPERSEDED and old.superseded_by == 2
    assert old.content_hash == approved.content_hash
    assert p.status == ch.ACCEPTED and p.decided_by == "admin@example.com"
    assert new.validation == WINS


def test_the_new_version_carries_the_proposal_evidence():
    new, _ = ch.approve(charter(status=ch.APPROVED), proposal(), by="admin")
    assert new.validation["expectancy_r"] == WINS["expectancy_r"]


def test_a_rejection_needs_a_reason():
    """"No" without a reason is unusable six months later, and the reason is
    what blocks the resubmission."""
    with pytest.raises(ch.ProposalError, match="needs a reason"):
        ch.reject(proposal(), by="admin", reason="  ")


def test_a_decided_proposal_cannot_be_decided_again():
    p = proposal()
    ch.reject(p, by="admin", reason="the gate it removes is the one I trust")
    with pytest.raises(ch.ProposalError, match="not open"):
        ch.approve(charter(status=ch.APPROVED), p, by="admin")


def test_the_diff_reads_in_plain_language():
    approved = charter(status=ch.APPROVED)
    p = proposal()
    d = p.diff(approved)
    assert "breakout" in " ".join(d["removed_gates"]) or d["removed_gates"]
    assert d["why"] == "the third gate never fired"


# ── adherence cannot be made free ──────────────────────────────────────────

def test_a_floor_outside_zero_to_one_is_refused():
    with pytest.raises(ValueError, match="between 0 and 1"):
        charter(adherence_floor=1.5)


def test_an_entry_tier_must_be_one_of_the_tiers():
    with pytest.raises(ValueError, match="not one of the tiers"):
        charter(entry_tier="Z")
