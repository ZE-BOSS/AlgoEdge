"""
tests/test_rl_exit_and_reward.py

The exit head's precedence, and the reward that scores it.

WHY THE PRECEDENCE IS THE SAFETY PROPERTY
-----------------------------------------
A close only ever reduces exposure, which is why closes are honoured without
risk approval while opens are refusable. That argument holds only if the agent
genuinely cannot do anything else - so these tests check that it cannot widen a
stop, cannot size a position, cannot override the owning strategy's own exit,
and cannot express an action that increases risk.

WHY THE REWARD NEEDS ITS OWN TESTS
----------------------------------
The exit head is the most overfit-prone component in W4 and its failure mode is
specific: an early-exit policy trained on a profitable period learns to CUT
WINNERS, because cutting always looks good just after a drawdown inside the
training window. This project has measured exactly that - break-even at 1.5R
turned 16 of 113 BTCUSD trades into exits the research never took. So the
reward has to make cutting a winner negative, and these tests pin that.
"""

from __future__ import annotations

import numpy as np
import pytest

from backend.rl import exit_policy as xp
from backend.rl import policy as pol
from backend.rl import reward as rw
from backend.strategies.base_strategy import TradeAction

LONG = {"ticket": 7, "entry_price": 100.0, "stop_loss": 99.0, "take_profit": 103.0,
        "direction": "BUY"}
SHORT = {"ticket": 8, "entry_price": 100.0, "stop_loss": 101.0, "take_profit": 97.0,
         "direction": "SELL"}


def decisive(n_features=25, action=pol.CLOSE_ALL):
    """A policy that will definitely pick `action` on the exit head."""
    p = pol.LinearPolicy(n_features=n_features, exit_head=True)
    p.b_exit = np.zeros(len(pol.EXIT_ACTIONS))
    p.b_exit[pol.EXIT_ACTIONS.index(action)] = 20.0
    return p


# ── tightening only ────────────────────────────────────────────────────────

def test_a_long_stop_may_only_rise_and_a_short_stop_may_only_fall():
    assert xp.tightens(99.5, 99.0, "BUY") is True
    assert xp.tightens(98.5, 99.0, "BUY") is False
    assert xp.tightens(100.5, 101.0, "SELL") is True
    assert xp.tightens(101.5, 101.0, "SELL") is False


def test_a_non_numeric_or_infinite_stop_is_not_a_tightening():
    assert xp.tightens(None, 99.0, "BUY") is False
    assert xp.tightens(float("inf"), 99.0, "BUY") is False
    assert xp.tightens("tighter", 99.0, "BUY") is False


def test_break_even_is_offered_only_once_the_trade_is_a_full_R_up():
    """The one tightening that cannot turn a winner into a loser."""
    assert xp.tighten_level(LONG, price=100.5) is None
    assert xp.tighten_level(LONG, price=101.0) == pytest.approx(100.0)


def test_an_atr_trail_is_offered_behind_price():
    level = xp.tighten_level(LONG, price=100.5, atr_value=0.3, atr_multiple=1.0)
    assert level == pytest.approx(100.2)


def test_a_trail_that_would_loosen_the_stop_is_not_offered():
    pos = dict(LONG, stop_loss=100.4)
    assert xp.tighten_level(pos, price=100.5, atr_value=5.0) is None


def test_the_agent_cannot_choose_the_stop_level_itself():
    """A policy that could pick the level could widen its effective risk by
    picking a loose one, and no amount of reading the weights would show it."""
    import inspect

    sig = inspect.signature(xp.decide)
    assert "new_sl" not in sig.parameters
    d = xp.decide(decisive(action=pol.TIGHTEN), np.zeros(25), LONG,
                  price=101.0, min_confidence=0.4)
    assert d.action == pol.TIGHTEN
    assert d.new_sl == pytest.approx(100.0), "the level comes from the position, not the policy"


def test_tighten_with_nowhere_to_go_reads_as_hold():
    """A no-op MODIFY_SL would be logged as an action the agent took."""
    d = xp.decide(decisive(action=pol.TIGHTEN), np.zeros(25), LONG,
                  price=100.1, min_confidence=0.4)
    assert d.action == pol.HOLD and d.new_sl is None


# ── the action space ───────────────────────────────────────────────────────

def test_the_exit_head_can_only_close_or_tighten():
    assert set(pol.EXIT_ACTIONS) == {pol.HOLD, pol.CLOSE_PARTIAL, pol.CLOSE_ALL,
                                     pol.TIGHTEN}


def test_a_close_becomes_a_close_trade_action_with_its_reason():
    d = xp.ExitDecision(action=pol.CLOSE_ALL, reason=xp.RL_EXIT)
    a = d.to_trade_action(7)
    assert a.action == "CLOSE" and a.close_pct == 1.0 and a.close_reason == xp.RL_EXIT


def test_a_partial_close_is_clamped_inside_the_position():
    d = xp.ExitDecision(action=pol.CLOSE_PARTIAL, close_pct=5.0)
    assert d.to_trade_action(7).close_pct == pytest.approx(0.99)
    d2 = xp.ExitDecision(action=pol.CLOSE_PARTIAL, close_pct=-1.0)
    assert d2.to_trade_action(7).close_pct == pytest.approx(0.01)


def test_hold_produces_no_action_at_all():
    assert xp.ExitDecision(action=pol.HOLD).to_trade_action(7) is None


def test_an_unconfident_exit_head_holds():
    """The stop, target, trail and max hold are still the backstop. The agent's
    job is to act EARLIER, never to replace them - so an unconfident head is
    harmless rather than disruptive."""
    p = pol.LinearPolicy(n_features=25, exit_head=True)      # uniform over four
    action, conf, _ = p.act_exit(np.zeros(25), np.zeros(7), min_confidence=0.55)
    assert action == pol.HOLD and conf == pytest.approx(0.25)


def test_an_agent_with_no_exit_head_always_holds():
    p = pol.LinearPolicy(n_features=25, exit_head=False)
    assert p.act_exit(np.zeros(25), np.zeros(7))[0] == pol.HOLD
    assert xp.decide(p, np.zeros(25), LONG, price=101.0).action == pol.HOLD


# ── supervision precedence ─────────────────────────────────────────────────

def test_the_owning_strategys_exit_wins_over_a_supervisor():
    """Not "the owner is consulted" - wins. A measured rule is never overridden
    by an agent; the agent can only act earlier."""
    owner = TradeAction(ticket=7, action="CLOSE", close_reason="SESSION_END")
    sup = xp.ExitDecision(action=pol.CLOSE_ALL, reason="RL_SUPERVISOR::a@1")
    out = xp.supervise(owner, sup, LONG, 7)
    assert out is owner and out.close_reason == "SESSION_END"


def test_a_supervisor_acts_only_where_the_owner_said_nothing():
    sup = xp.ExitDecision(action=pol.CLOSE_ALL, reason="RL_SUPERVISOR::a@1")
    out = xp.supervise(None, sup, LONG, 7)
    assert out is not None and out.action == "CLOSE"
    assert out.close_reason == "RL_SUPERVISOR::a@1"


def test_a_supervisor_cannot_widen_a_stop_even_if_it_asks_to():
    """Enforced here as well as in the engines. Two layers that must agree is
    harder to reason about than an action that cannot be expressed."""
    sup = xp.ExitDecision(action=pol.TIGHTEN, new_sl=98.0)    # looser than 99.0
    assert xp.supervise(None, sup, LONG, 7) is None


def test_the_supervisor_exit_reason_names_the_agent_and_version():
    """Without this the Journal cannot attribute which exits were the agent's,
    and the result cannot be attributed afterwards at all."""
    assert xp.supervisor_reason("crash-sup", 3) == "RL_SUPERVISOR::crash-sup@3"
    assert xp.supervisor_reason("crash-sup", 3).startswith(xp.SUPERVISOR_PREFIX)


# ── the reward ─────────────────────────────────────────────────────────────

def test_money_is_plain_R_by_default():
    """The penalty is a preference, not a measurement, so 1.0 is the honest
    default and anything above it is recorded on the spec."""
    assert rw.money_term(0.8) == pytest.approx(0.8)
    assert rw.money_term(-0.8) == pytest.approx(-0.8)


def test_a_downside_penalty_weights_losses_only():
    assert rw.money_term(1.0, 2.0) == pytest.approx(1.0)
    assert rw.money_term(-1.0, 2.0) == pytest.approx(-2.0)


def test_a_good_enough_early_exit_is_not_punished():
    """The rule the user set: an exit that captured a substantial return gets
    full credit, even though holding would have made more.

    The old signed penalty charged this trade -0.6 for taking +2.4R out of a
    +3.0R move, which teaches an agent to hold through reversals it can see
    coming.
    """
    good = rw.TradeReward(r_net=2.4, adherence=1.0, counterfactual_r=3.0)
    assert good.capture == 1.0
    assert good.left_on_table == pytest.approx(0.6)
    assert good.money > 2.4, "the capture bonus is paid on top of the return"


def test_more_return_is_always_more_reward():
    """The term that actually stops winners being cut: `realised` is monotone
    and uncapped, so holding to 3R always out-scores cutting at 1R."""
    cut = rw.TradeReward(r_net=1.0, adherence=1.0, counterfactual_r=3.0)
    held = rw.TradeReward(r_net=3.0, adherence=1.0, counterfactual_r=None)
    assert held.money > cut.money
    for a, b in ((0.5, 1.0), (1.0, 2.0), (2.0, 5.0)):
        lo = rw.TradeReward(r_net=a, adherence=1.0, counterfactual_r=None)
        hi = rw.TradeReward(r_net=b, adherence=1.0, counterfactual_r=None)
        assert hi.money > lo.money


def test_money_left_on_the_table_costs_the_bonus_and_not_the_balance():
    """"It doesn't get deducted - it doesn't get the reward." """
    poor = rw.TradeReward(r_net=0.3, adherence=1.0, counterfactual_r=3.0)
    assert poor.capture == 0.0, "10% of what was available earns no bonus"
    assert poor.money == pytest.approx(0.3), "and is not charged for the rest"
    assert poor.left_on_table == pytest.approx(2.7)


def test_the_capture_credit_grades_rather_than_stepping():
    """A cliff gives the optimiser nothing to follow."""
    fracs = [0.25, 0.4, 0.55, 0.69]
    credits = [rw.capture_credit(f * 2.0, 2.0) for f in fracs]
    assert credits == sorted(credits)
    assert 0.0 < credits[0] < 1.0 and credits[-1] < 1.0


def test_a_substantial_exit_earns_full_credit_on_its_own():
    """At or above FULL_CREDIT_R, the exit is good whatever was left behind."""
    assert rw.capture_credit(rw.FULL_CREDIT_R, 99.0) == 1.0


def test_a_trade_that_ran_its_course_took_everything_on_offer():
    ran = rw.TradeReward(r_net=1.2, adherence=1.0, counterfactual_r=None)
    assert ran.capture == 1.0 and ran.left_on_table == 0.0 and ran.exit_quality == 0.0


def test_cutting_a_loser_is_paid_for():
    """The one thing an exit head is unambiguously for."""
    cut = rw.TradeReward(r_net=-0.3, adherence=1.0, counterfactual_r=-1.0)
    assert cut.loss_avoided == pytest.approx(0.7)
    assert cut.money > -0.3
    held = rw.TradeReward(r_net=-1.0, adherence=1.0, counterfactual_r=None)
    assert cut.money > held.money


def test_cutting_a_winner_earns_no_bonus_at_all():
    """Not charged, but it gets nothing: no capture credit and nothing for a
    loss it did not avoid."""
    cut = rw.TradeReward(r_net=0.4, adherence=1.0, counterfactual_r=3.0)
    assert cut.capture == 0.0 and cut.loss_avoided == 0.0
    assert cut.money == pytest.approx(0.4)


def test_loss_avoided_is_one_sided():
    """Crediting the difference in both directions is the signed penalty this
    design replaced."""
    assert rw.loss_avoided(0.5, 3.0) == 0.0
    assert rw.loss_avoided(-0.2, -1.0) == pytest.approx(0.8)
    assert rw.loss_avoided(1.0, None) == 0.0


def test_exit_quality_survives_as_a_diagnostic():
    """Still reported - "what did early exits cost or save in total" is worth
    knowing - but no longer inside the money term."""
    t = rw.TradeReward(r_net=0.5, adherence=0.0, counterfactual_r=2.0)
    assert t.exit_quality == pytest.approx(-1.5)
    # The money is the return plus whatever bonus was earned - never less than
    # the return. However much was left behind, it is withheld, not charged.
    assert t.money >= t.r_net
    worse = rw.TradeReward(r_net=0.5, adherence=0.0, counterfactual_r=50.0)
    assert worse.money >= worse.r_net
    assert worse.money <= t.money, "a worse capture earns less bonus, never a charge"


def test_the_breakdown_flags_an_exit_head_that_cut_winners():
    cutter = rw.aggregate([rw.TradeReward(r_net=0.4, adherence=1.0, counterfactual_r=2.5)] * 5)
    assert cutter.cut_winners is True
    saver = rw.aggregate([rw.TradeReward(r_net=-0.2, adherence=1.0, counterfactual_r=-1.0)] * 5)
    assert saver.cut_winners is False


def test_adherence_cannot_be_farmed_by_never_trading():
    """Credited per CLOSED TRADE. A policy that opens nothing scores exactly
    zero, not 0.30."""
    empty = rw.aggregate([])
    assert empty.n_trades == 0 and empty.total == 0.0 and empty.adherence == 0.0


def test_the_components_survive_aggregation():
    rewards = [rw.TradeReward(r_net=1.0, adherence=0.8, counterfactual_r=0.8),
               rw.TradeReward(r_net=-1.0, adherence=0.4, counterfactual_r=-0.9)]
    out = rw.aggregate(rewards, keep_per_trade=True)
    assert out.n_trades == 2
    assert out.adherence == pytest.approx(0.6)
    assert out.early_exits == 2
    assert out.exit_quality == pytest.approx(((1.0 - 0.8) + (-1.0 + 0.9)) / 2)
    assert out.mean_r == pytest.approx(0.0)
    assert out.win_rate == pytest.approx(0.5)
    assert out.worst_r == pytest.approx(-1.0)
    assert len(out.per_trade) == 2


def test_the_total_is_the_weighted_sum_of_its_parts():
    t = rw.TradeReward(r_net=1.0, adherence=1.0, counterfactual_r=1.2,
                       money_weight=0.70, adherence_weight=0.30)
    expected_money = (t.realised + rw.CAPTURE_WEIGHT * t.capture
                      + rw.CUT_WEIGHT * t.loss_avoided)
    assert t.money == pytest.approx(expected_money)
    assert t.total == pytest.approx(0.70 * t.money + 0.30 * 1.0)


def test_it_is_averaged_per_trade_rather_than_summed():
    """A sum rewards trading more, and an overlapping-trade study in this
    project inflated expectancy until the sign of the result flipped."""
    one = rw.aggregate([rw.TradeReward(r_net=0.5, adherence=1.0)])
    ten = rw.aggregate([rw.TradeReward(r_net=0.5, adherence=1.0)] * 10)
    assert one.total == pytest.approx(ten.total)
    assert ten.sum_r == pytest.approx(10 * one.sum_r)


def test_followed_its_strategy_is_the_six_tenths_floor():
    assert rw.aggregate([rw.TradeReward(r_net=1.0, adherence=0.61)]).followed_its_strategy
    assert not rw.aggregate([rw.TradeReward(r_net=1.0, adherence=0.59)]).followed_its_strategy


def test_a_non_finite_R_does_not_poison_the_reward():
    assert rw.money_term(float("nan")) == 0.0
    assert rw.exit_quality(float("inf"), 1.0) == 0.0
    assert rw.capture_credit(float("nan"), 1.0) == 0.0
    assert rw.loss_avoided(float("inf"), -1.0) == 0.0


# ── the supervisor's reward ────────────────────────────────────────────────

def test_a_supervisor_is_scored_on_the_difference_it_made():
    """"Did it make money" is the wrong question for something that never opens
    a trade."""
    cut_a_loser = rw.supervisor_reward(-0.3, -1.0, adherence=0.8)
    assert cut_a_loser.saved_r == pytest.approx(0.7)
    assert cut_a_loser.r_net == pytest.approx(0.7)

    cut_a_winner = rw.supervisor_reward(0.5, 2.0, adherence=0.8)
    assert cut_a_winner.saved_r == pytest.approx(-1.5)
    assert cut_a_winner.total < 0


def test_closing_everything_immediately_scores_near_zero_not_well():
    """The degenerate policy to guard against. If a supervisor closes every
    trade at its first bar, its result is the average trade's own result, so
    the DIFFERENCE it made is about zero."""
    rng = np.random.default_rng(3)
    outcomes = rng.normal(0.05, 1.0, 400)
    # closed immediately: each trade returns roughly nothing
    rewards = [rw.supervisor_reward(0.0, float(o), adherence=1.0) for o in outcomes]
    out = rw.aggregate(rewards)
    assert abs(out.saved_r) < 0.2, out.saved_r


def test_a_measurability_check_is_not_part_of_the_reward():
    """A thin sample must not be tradeable off against profit by the optimiser:
    a verdict over 40 trades is not a verdict."""
    assert rw.enough_trades(40, 100) is False
    assert rw.enough_trades(150, 100) is True


# ── policy mechanics ───────────────────────────────────────────────────────

def test_the_parameter_vector_round_trips():
    p = pol.LinearPolicy(n_features=25, exit_head=True)
    theta = np.random.default_rng(1).normal(0, 1, p.n_params)
    p.from_flat(theta)
    assert np.allclose(p.to_flat(), theta)


def test_the_wrong_number_of_parameters_is_refused():
    p = pol.LinearPolicy(n_features=25)
    with pytest.raises(ValueError, match="expected"):
        p.from_flat(np.zeros(3))


def test_a_policy_round_trips_through_json_and_keeps_its_hash():
    p = pol.LinearPolicy(n_features=25, exit_head=True, hidden=4)
    p.from_flat(np.random.default_rng(2).normal(0, 1, p.n_params))
    back = pol.LinearPolicy.from_dict(p.to_dict())
    assert back.content_hash == p.content_hash
    assert np.allclose(back.to_flat(), p.to_flat())


def test_the_feature_ranking_is_long_minus_short():
    p = pol.LinearPolicy(n_features=3)
    p.W_entry = np.array([[2.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, 0.0, 0.0]])
    ranked = dict(p.feature_importance(["a", "b", "c"]))
    assert ranked["a"] == pytest.approx(2.0)
    assert ranked["b"] == pytest.approx(-1.0)
    assert ranked["c"] == pytest.approx(0.0)


def test_a_zero_policy_is_recognised_as_untrained():
    assert pol.LinearPolicy(n_features=5).is_flat_everywhere() is True


# ── what is DEDUCTED, as opposed to withheld ───────────────────────────────
#
# Withholding says "that was not worth a bonus". Deducting says "do not do
# that". These pin which is which, because getting them the wrong way round is
# how an exit head learns to cut winners or a policy learns to ignore its
# charter and simply collect a smaller bonus.

def test_a_loss_subtracts():
    assert rw.TradeReward(r_net=-1.0, adherence=1.0).total < 0


def test_a_bigger_loss_subtracts_more():
    small = rw.TradeReward(r_net=-0.5, adherence=1.0)
    big = rw.TradeReward(r_net=-2.0, adherence=1.0)
    assert big.total < small.total


def test_the_downside_penalty_weights_losses_above_gains():
    plain = rw.TradeReward(r_net=-1.0, adherence=1.0, downside_penalty=1.0)
    harsh = rw.TradeReward(r_net=-1.0, adherence=1.0, downside_penalty=2.0)
    assert harsh.total < plain.total


def test_ignoring_the_strategy_subtracts_rather_than_earning_less():
    """A plain 0..1 adherence means ignoring the charter earns a SMALLER bonus,
    so a policy that makes more money by abandoning its strategy still comes
    out ahead. That is the trade the 0.30 term exists to prevent."""
    followed = rw.TradeReward(r_net=1.0, adherence=1.0)
    ignored = rw.TradeReward(r_net=1.0, adherence=0.0)
    assert ignored.adherence_score == -1.0
    assert ignored.total < followed.total
    assert ignored.total < 1.0 * followed.money_weight * followed.money


def test_the_charters_own_floor_is_the_neutral_point():
    """Not a fixed 0.5: a charter that sets a stricter floor is scored against
    ITS floor, because that is what the floor means."""
    at_floor = rw.TradeReward(r_net=1.0, adherence=0.6, adherence_floor=0.6)
    assert at_floor.adherence_score == pytest.approx(0.0)
    strict = rw.TradeReward(r_net=1.0, adherence=0.6, adherence_floor=0.8)
    assert strict.adherence_score < 0, "0.6 is a breach of a 0.8 floor"


def test_adherence_score_runs_from_minus_one_to_plus_one():
    assert rw.adherence_score(0.0, 0.6) == pytest.approx(-1.0)
    assert rw.adherence_score(0.6, 0.6) == pytest.approx(0.0)
    assert rw.adherence_score(1.0, 0.6) == pytest.approx(1.0)
    assert -1.0 <= rw.adherence_score(0.3, 0.6) <= 0.0


def test_a_named_breach_is_deducted_and_says_what_it_was():
    clean = rw.TradeReward(r_net=1.0, adherence=1.0)
    breach = rw.TradeReward(r_net=1.0, adherence=1.0, penalties=("outside_charter",))
    assert breach.penalty == rw.PENALTIES["outside_charter"]
    assert breach.total == pytest.approx(clean.total - breach.penalty)
    assert breach.to_dict()["penalties"] == ["outside_charter"]


def test_an_unknown_breach_name_costs_nothing():
    """A penalty that no longer exists should stop being charged, not break the
    scoring of a historic run that recorded it."""
    assert rw.penalty_total(("retired_rule",)) == 0.0


def test_the_breakdown_counts_breaches_by_name():
    out = rw.aggregate([
        rw.TradeReward(r_net=1.0, adherence=1.0, penalties=("outside_charter",)),
        rw.TradeReward(r_net=1.0, adherence=1.0, penalties=("outside_charter", "out_of_scope")),
        rw.TradeReward(r_net=1.0, adherence=1.0),
    ])
    assert out.penalties == {"outside_charter": 2, "out_of_scope": 1}
    assert out.penalty > 0
    assert "penalties" in out.summary()


def test_nothing_the_agent_cannot_express_is_in_the_penalty_list():
    """A rule the agent cannot break beats a rule it is charged for breaking.
    Lot size, stop distance and the drawdown caps are absent from the action
    space, so they must not appear here as if they were a priced option."""
    forbidden = ("lot", "size", "drawdown", "risk_pct", "stop_distance", "balance")
    for name in rw.PENALTIES:
        assert not any(word in name for word in forbidden), name
