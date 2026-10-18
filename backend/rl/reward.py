"""
backend/rl/reward.py

0.70 money + 0.30 adherence, with the money half broken into compartments.

WHY SEPARATE SERIES AND NOT ONE SCALAR
--------------------------------------
Reward decomposition is the point, not a reporting nicety. Collapsed into one
number, "it made money by abandoning its strategy" and "it followed its
strategy and the strategy works" are the same reward. Kept apart, the first is
visible: money up, adherence down.

THE MONEY TERM, AND WHY IT IS THREE THINGS
------------------------------------------
A single `actual - would-have-been` term punishes every early exit, including
the good ones. An agent that took +2.4R out of a trade that would have made
+3.0R did well, and charging it -0.6 teaches it to hold through reversals it
could see coming. So the money term asks three separate questions:

    realised       what the trade actually made, in R. MONOTONE: more return
                   is always more reward, with no cap. This is the term that
                   stops winners being cut - holding a 3R trade to 3R always
                   out-scores cutting it at 1R, because 3 > 1.

    capture        of what the market offered on this trade, how much did it
                   take? Full credit for a substantial exit - at or above
                   `FULL_CREDIT_R`, or at or above `FULL_CREDIT_FRACTION` of
                   what holding would have made. Below that the credit is
                   graded down to zero. It is NEVER negative: an early exit
                   that left money on the table does not get the bonus, and is
                   not charged for it.

    loss_avoided   how much of a loss it cut. Positive only when holding would
                   have lost more than exiting did. This is the one thing an
                   exit head is unambiguously for.

So the incentives read, in the user's own terms: more return is always better,
a good-enough exit is not punished for not being perfect, money left on the
table costs the bonus rather than the balance, and cutting a loser is paid for.

WHAT IS DEDUCTED, AS OPPOSED TO WITHHELD
----------------------------------------
Withholding and deducting are different tools and the difference matters.
Withholding says "that was not worth a bonus"; deducting says "do not do
that". Three things are deducted:

    a loss            `realised` is signed R, so a losing trade subtracts, and
                      `downside_penalty` can weight losses above gains.
    not following     the adherence term is signed around the CHARTER'S OWN
    the strategy      floor: at the floor it is zero, at 1.0 it is +1, at 0 it
                      is -1. Earning less for ignoring the charter was not
                      enough - a policy could out-earn its own strategy by
                      abandoning it and simply collecting a smaller 0.30.
    named breaches    `penalties`: an explicit, named, signed list carried on
                      every trade, so a reward that was reduced says WHY in
                      words rather than only being smaller.

A penalty has to be a measurable breach with a name, not a preference. Things
the agent cannot express - a lot size, a widened stop, a breached drawdown cap
- are absent from the action space rather than penalised, because a rule the
agent can break and be charged for is weaker than one it cannot break.

THE COUNTERFACTUAL IS A REWARD, NEVER AN OBSERVATION
----------------------------------------------------
`capture` and `loss_avoided` are computed from bars AFTER the decision. That is
legitimate here and fatal in `features.py`, so the separation is structural:
this module imports nothing from `features`, and `features` imports nothing
from here. `tests/test_rl_features.py` asserts the observation matrix is
unchanged whether or not this module has been imported.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

# -- the compartment weights ------------------------------------------------
#
# Deliberately smaller than the realised term. A bonus that outweighed the
# trade's own result would make exit timing worth more than being right about
# direction, and the literature is clear that direction is the harder problem.

CAPTURE_WEIGHT = 0.35       # paid for taking what was on offer
CUT_WEIGHT = 0.50           # paid for cutting a loser short
# The exit CALL - the user's rule (2026-10-18): an early close of a trade that
# WOULD have gone on to its stop earns +1; an early close of a trade that would
# NOT have hit its stop costs -1. Symmetric on purpose, and that is what makes
# it an anti-overfitting term: `loss_avoided` alone pays for good cuts and
# charges nothing for needless ones, so a head that cuts everything at the
# first wobble collects the good cuts for free. Measured on the 2026-10-17
# gold agent, the head added exactly +0.000R - it had nothing to lose by
# acting at random. With this term every early exit is a bet with a price.
EXIT_CALL_WEIGHT = 0.30
# A counterfactual at or below this R is "would have hit the stop". The
# counterfactual returns exactly -1.0 on a stop; the margin absorbs float noise.
STOP_HIT_R = -0.999

# "Substantial" - either of these earns the full capture credit on its own.
FULL_CREDIT_R = 1.5         # an exit at or above +1.5R is a good exit, full stop
FULL_CREDIT_FRACTION = 0.70  # or 70% of what holding would have made

# Below the full-credit line the credit grades down to zero rather than
# stepping, so there is a gradient for the optimiser to follow.
NO_CREDIT_FRACTION = 0.20

# -- named deductions -------------------------------------------------------
#
# Each entry is a breach the agent CAN commit and that is worth charging for,
# with what it costs. Kept as data so the list is readable in one place and a
# change to it is visible in a diff rather than buried in an expression.
#
# Deliberately short. Everything the agent genuinely must not do - size a
# position, widen a stop, override a halt - is absent from its action space
# instead, because a rule it cannot break beats a rule it is charged for
# breaking.
PENALTIES: dict[str, float] = {
    # A trade the APPROVED charter does not describe. Not a low-adherence
    # trade: a trade nobody signed off.
    "outside_charter": 1.00,
    # The agent acted on a position after its own charter said it may not -
    # e.g. an exit head on an agent whose charter has no exit rule.
    "exit_not_in_charter": 0.50,
    # It traded a symbol or supervised a strategy its frozen spec does not
    # permit. Normally unreachable; charged rather than silently allowed.
    "out_of_scope": 1.00,
}


@dataclass(frozen=True)
class TradeReward:
    """One closed trade's reward, with its parts intact."""
    r_net: float                     # the trade's realised R after measured costs
    adherence: float                 # fraction of the APPROVED charter's gates that held
    # What holding to the original stop or target would have returned, in R.
    # None when the trade was NOT exited early - it ran to its stop, its target
    # or its max hold - so there is nothing to compare and no bonus to pay.
    counterfactual_r: float | None = None
    money_weight: float = 0.70
    adherence_weight: float = 0.30
    downside_penalty: float = 1.0
    was_supervised: bool = False
    saved_r: float | None = None     # supervisor only: see `supervisor_reward`
    tag: str = ""
    # The charter's own adherence floor - the point at which the adherence term
    # is neither rewarded nor charged. Taken from the charter rather than fixed
    # here, because a charter that sets a stricter floor should be scored
    # against ITS floor.
    adherence_floor: float = 0.6
    # Named breaches committed on this trade, from PENALTIES. A tuple so the
    # reward stays hashable and cannot be edited after the fact.
    penalties: tuple = ()

    # -- the three compartments --------------------------------------------

    @property
    def realised(self) -> float:
        """R, with losses optionally weighted up. Monotone in the return."""
        return money_term(self.r_net, self.downside_penalty)

    @property
    def capture(self) -> float:
        """0..1: how much of what was on offer this exit took.

        1.0 when the trade was not exited early at all - it ran its course, so
        it took everything that was on offer by definition.
        """
        return capture_credit(self.r_net, self.counterfactual_r)

    @property
    def loss_avoided(self) -> float:
        """R of loss cut short. Zero unless holding would have lost more."""
        return loss_avoided(self.r_net, self.counterfactual_r)

    @property
    def left_on_table(self) -> float:
        """R the market offered and this exit did not take. Reported, never
        subtracted - it costs the capture bonus and nothing else."""
        if self.counterfactual_r is None:
            return 0.0
        return max(0.0, float(self.counterfactual_r) - float(self.r_net))

    @property
    def exit_quality(self) -> float:
        """The plain difference, kept as a DIAGNOSTIC only.

        This used to be inside the money term as a signed penalty. It is not
        any more: it charged every early exit for not being perfect, including
        the ones that were right. It is still reported, because "how much did
        early exits cost or save in total" is worth knowing.
        """
        if self.counterfactual_r is None:
            return 0.0
        return float(self.r_net) - float(self.counterfactual_r)

    @property
    def exit_call(self) -> float:
        """+1 if this early exit dodged a trade bound for its stop, -1 if the
        trade would have survived, 0 when the trade was not exited early."""
        return exit_call(self.counterfactual_r)

    @property
    def money(self) -> float:
        return (self.realised
                + CAPTURE_WEIGHT * self.capture
                + CUT_WEIGHT * self.loss_avoided
                + EXIT_CALL_WEIGHT * self.exit_call)

    @property
    def adherence_score(self) -> float:
        """-1 to +1, zero at the charter's own floor.

        Signed, and that is the point. A plain 0..1 adherence means ignoring
        the strategy simply earns a smaller bonus - so a policy that makes more
        money by abandoning its charter still comes out ahead, which is exactly
        the trade the 0.30 term exists to prevent. Below the floor it now
        subtracts.
        """
        return adherence_score(self.adherence, self.adherence_floor)

    @property
    def penalty(self) -> float:
        """What the named breaches on this trade cost, as a positive number."""
        return penalty_total(self.penalties)

    @property
    def total(self) -> float:
        return (self.money_weight * self.money
                + self.adherence_weight * self.adherence_score
                - self.penalty)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.update(money=self.money, total=self.total, realised=self.realised,
                 capture=self.capture, loss_avoided=self.loss_avoided,
                 exit_call=self.exit_call,
                 left_on_table=self.left_on_table, exit_quality=self.exit_quality,
                 adherence_score=self.adherence_score, penalty=self.penalty,
                 penalties=list(self.penalties))
        return d


def money_term(r_net: float, downside_penalty: float = 1.0) -> float:
    """R, with losses weighted up.

    `downside_penalty=1.0` is plain R, which is the honest default - the
    penalty is a preference, not a measurement, and inflating it quietly
    changes what "profitable" means. Above 1.0 it is a stated risk preference
    and it is recorded on the spec.
    """
    r = float(r_net)
    if not np.isfinite(r):
        return 0.0
    return r if r >= 0 else r * float(max(1.0, downside_penalty))


def capture_credit(actual_r: float, counterfactual_r: float | None) -> float:
    """0..1, and never below 0.

    Full credit when the exit was substantial in its own right (`FULL_CREDIT_R`)
    or took most of what was available (`FULL_CREDIT_FRACTION`). Graded down to
    zero between there and `NO_CREDIT_FRACTION`. An exit that left money on the
    table loses the bonus; it is not charged for it.

    A losing trade earns no capture credit and no charge either - cutting a
    loser is paid for by `loss_avoided`, which is the right compartment for it.
    """
    if counterfactual_r is None:
        # Not an early exit: it ran to its stop, target or max hold, so it took
        # everything that was on offer.
        return 1.0
    a, c = float(actual_r), float(counterfactual_r)
    if not (np.isfinite(a) and np.isfinite(c)):
        return 0.0
    if a <= 0:
        return 0.0
    if a >= FULL_CREDIT_R:
        return 1.0
    if c <= 0:
        # Holding would have lost and the agent made money instead. Nothing was
        # left on the table at all.
        return 1.0
    frac = a / c
    if frac >= FULL_CREDIT_FRACTION:
        return 1.0
    if frac <= NO_CREDIT_FRACTION:
        return 0.0
    return float((frac - NO_CREDIT_FRACTION)
                 / (FULL_CREDIT_FRACTION - NO_CREDIT_FRACTION))


def loss_avoided(actual_r: float, counterfactual_r: float | None) -> float:
    """R of loss this exit cut short. Zero when holding would have done better.

    One-sided on purpose. The symmetric version - crediting the difference in
    both directions - is the signed penalty this design replaced, and it is
    what taught an exit head to hold losers as eagerly as it cut winners.
    """
    if counterfactual_r is None:
        return 0.0
    a, c = float(actual_r), float(counterfactual_r)
    if not (np.isfinite(a) and np.isfinite(c)):
        return 0.0
    if c >= 0:
        return 0.0        # holding was not a loss; nothing was avoided
    return max(0.0, a - c)


def exit_call(counterfactual_r: float | None) -> float:
    """Was the early exit RIGHT about the stop? +1 yes, -1 no, 0 not an exit.

    "Right" means holding would have hit the original stop. A trade that would
    have reached its target, or merely drifted, was cut needlessly - that is the
    -1, and it is what stops an exit head from closing everything early.
    """
    if counterfactual_r is None:
        return 0.0
    c = float(counterfactual_r)
    if not np.isfinite(c):
        return 0.0
    return 1.0 if c <= STOP_HIT_R else -1.0


def adherence_score(adherence: float, floor: float = 0.6) -> float:
    """Map 0..1 adherence onto -1..+1, with zero at the charter's floor.

    Piecewise linear through three points: 0 -> -1, floor -> 0, 1 -> +1. The
    floor is the natural neutral point because that is precisely what the floor
    means - below it the drift monitor suspends the agent, so below it the
    reward should be charging rather than merely paying less.
    """
    a = float(adherence)
    f = min(max(float(floor), 1e-6), 1.0 - 1e-6)
    if not np.isfinite(a):
        return -1.0
    a = min(max(a, 0.0), 1.0)
    return float((a - f) / f) if a < f else float((a - f) / (1.0 - f))


def penalty_total(names) -> float:
    """What a list of named breaches costs. Unknown names cost nothing.

    Unknown rather than raising: a penalty name that no longer exists in
    `PENALTIES` should stop being charged, not break the scoring of a historic
    run that recorded it.
    """
    return float(sum(PENALTIES.get(str(n), 0.0) for n in (names or ())))


def exit_quality(actual_r: float, counterfactual_r: float | None) -> float:
    """The plain signed difference. A diagnostic, not part of the money term."""
    if counterfactual_r is None:
        return 0.0
    a, c = float(actual_r), float(counterfactual_r)
    if not (np.isfinite(a) and np.isfinite(c)):
        return 0.0
    return a - c


def supervisor_reward(actual_r: float, unsupervised_r: float, adherence: float,
                      *, money_weight: float = 0.70, adherence_weight: float = 0.30,
                      downside_penalty: float = 1.0) -> TradeReward:
    """Reward for a trade the agent did not open (plan 4.9).

    "Did it make money" is the wrong question for something that never opens a
    trade, so the money term is the DIFFERENCE from the same trade left alone:

        saved_r = what the trade returned - what it would have returned unsupervised

    Signed, unlike the exit head's compartments, and deliberately so. A
    supervisor's ONLY effect is the difference it makes; if that difference is
    negative it has done nothing but harm, and there is no "it still made
    money" term to hide behind. An agent that closes everything on the first
    bar scores near zero, not well, because the average early exit lands near
    the average trade.
    """
    saved = float(actual_r) - float(unsupervised_r)
    if not np.isfinite(saved):
        saved = 0.0
    return TradeReward(
        r_net=saved, adherence=float(adherence), counterfactual_r=None,
        money_weight=money_weight, adherence_weight=adherence_weight,
        downside_penalty=downside_penalty, was_supervised=True, saved_r=saved,
        tag="supervisor",
    )


@dataclass
class RewardBreakdown:
    """An episode's reward, by component. What the UI charts and the report
    prints - never only the sum."""
    n_trades: int = 0
    money: float = 0.0
    adherence: float = 0.0
    realised: float = 0.0
    capture: float = 0.0
    loss_avoided: float = 0.0
    left_on_table: float = 0.0
    exit_quality: float = 0.0
    total: float = 0.0
    mean_r: float = 0.0
    sum_r: float = 0.0
    win_rate: float = 0.0
    worst_r: float = 0.0
    saved_r: float = 0.0
    early_exits: int = 0
    # The exit head's calls: right = the trade it closed would have hit its
    # stop; wrong = it would have survived. The hit rate is what says whether
    # the head is reading the market or just closing trades.
    exit_call: float = 0.0
    exit_calls_right: int = 0
    exit_calls_wrong: int = 0
    adherence_score: float = 0.0
    penalty: float = 0.0
    penalties: dict = field(default_factory=dict)
    per_trade: list[dict] = field(default_factory=list)

    @property
    def followed_its_strategy(self) -> bool:
        """The 0.6 floor from the validation gate. Below it the stated strategy
        is fiction, whatever the money did."""
        return self.n_trades > 0 and self.adherence >= 0.6

    @property
    def cut_winners(self) -> bool:
        """Early exits gave up more than they saved, on average.

        The one failure mode an exit head has. Reported as a flag because it is
        the thing to look at first when an exit head looks good on money.
        """
        return self.early_exits > 0 and self.exit_quality < 0

    def summary(self) -> str:
        if not self.n_trades:
            return "no trades - nothing to score"
        bits = [f"{self.n_trades} trades", f"{self.mean_r:+.4f}R",
                f"money {self.money:+.4f} (realised {self.realised:+.4f}, "
                f"capture {self.capture:.2f}, cut {self.loss_avoided:+.4f})",
                f"adherence {self.adherence:.2f} ({self.adherence_score:+.2f})",
                f"left on the table {self.left_on_table:.4f}R"]
        if self.penalty:
            bits.append("penalties " + ", ".join(
                f"{k} x{v}" for k, v in sorted(self.penalties.items())))
        bits.append(f"total {self.total:+.4f}")
        return " | ".join(bits)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["followed_its_strategy"] = self.followed_its_strategy
        d["cut_winners"] = self.cut_winners
        return d


def aggregate(rewards, *, keep_per_trade: bool = False) -> RewardBreakdown:
    """Mean reward over closed trades, with every component carried through.

    MEAN rather than sum, deliberately. A sum rewards trading more, and this
    project has measured what that does: an overlapping-trade study inflated
    expectancy until the sign of the result flipped once overlap was removed.
    Per-trade averages make a 2,000-trade run and a 200-trade run comparable,
    and the trade count is reported next to them so a thin sample cannot hide.
    """
    rewards = list(rewards or ())
    out = RewardBreakdown()
    if not rewards:
        return out
    r_net = np.array([r.r_net for r in rewards], dtype=float)
    out.n_trades = len(rewards)
    out.money = float(np.mean([r.money for r in rewards]))
    out.adherence = float(np.mean([r.adherence for r in rewards]))
    out.realised = float(np.mean([r.realised for r in rewards]))
    out.capture = float(np.mean([r.capture for r in rewards]))
    out.loss_avoided = float(np.mean([r.loss_avoided for r in rewards]))
    out.left_on_table = float(np.mean([r.left_on_table for r in rewards]))
    out.exit_quality = float(np.mean([r.exit_quality for r in rewards]))
    out.total = float(np.mean([r.total for r in rewards]))
    out.mean_r = float(np.mean(r_net))
    out.sum_r = float(np.sum(r_net))
    out.win_rate = float(np.mean(r_net > 0))
    out.worst_r = float(np.min(r_net))
    out.early_exits = int(sum(1 for r in rewards if r.counterfactual_r is not None))
    calls = [r.exit_call for r in rewards]
    out.exit_call = float(np.mean(calls))
    out.exit_calls_right = int(sum(1 for c in calls if c > 0))
    out.exit_calls_wrong = int(sum(1 for c in calls if c < 0))
    out.adherence_score = float(np.mean([r.adherence_score for r in rewards]))
    out.penalty = float(np.mean([r.penalty for r in rewards]))
    for r in rewards:
        for name in r.penalties:
            out.penalties[str(name)] = out.penalties.get(str(name), 0) + 1
    saved = [r.saved_r for r in rewards if r.saved_r is not None]
    out.saved_r = float(np.mean(saved)) if saved else 0.0
    if keep_per_trade:
        out.per_trade = [r.to_dict() for r in rewards]
    return out


def enough_trades(n_trades: int, minimum: int = 100) -> bool:
    """Whether a result is measurable at all.

    Separate from the reward on purpose. The temptation is to penalise a thin
    sample inside the reward, which turns "I cannot tell" into a number the
    optimiser can trade off against profit. It cannot be traded off: a verdict
    over 40 trades is not a verdict, so the trainer and the validator reject
    the run instead of discounting it.

    An episode with no trades scores zero rather than a free 0.30 of adherence -
    `aggregate` over an empty list returns a zeroed breakdown, which is that
    rule. Crediting adherence per BAR instead of per trade is the silent
    version of the same mistake: every gate is trivially satisfied when no
    decision is made, so the best policy becomes one that never trades.
    """
    return int(n_trades) >= max(1, int(minimum))
