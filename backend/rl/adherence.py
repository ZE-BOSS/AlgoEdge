"""
backend/rl/adherence.py

"Did it follow its strategy?" - computed, not asserted.

WHICH CHARTER IT IS SCORED AGAINST
----------------------------------
This is the whole point of the module and it is one rule with two halves:

  * **Live: always the APPROVED charter.** If the policy has drifted away from
    what the admin signed off, that must show up as falling adherence and stop
    the agent - not be absorbed into a P&L number.
  * **A proposal's own backtest: the PROPOSED charter.** A proposal has to be
    measurable on its own terms or the admin is comparing two different things.

`score_decision` takes the charter explicitly for that reason. There is no
default and no "current" charter to fall back on, because a silent fallback to
the wrong charter is exactly the bug that would make the 0.30 term meaningless.

THE DRIFT MONITOR
-----------------
An agent can quietly stop following its charter without proposing anything.
`DriftMonitor` watches a rolling window per agent: below the charter's own
floor for `drift_trades` consecutive trades and the agent is suspended from
live, with the skipped gates named. It does not fall back to trading
unsupervised - the slot stops taking the agent's signals and says why.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

from backend.rl.charter import APPROVED, StrategyCharter


@dataclass(frozen=True)
class DecisionScore:
    """One entry or exit decision, scored against a named charter version."""
    adherence: float
    tier: str | None
    gates_held: tuple[str, ...]
    gates_skipped: tuple[str, ...]
    charter_version: int
    charter_hash: str
    in_charter: bool                 # did the decision satisfy the entry tier at all?

    def to_dict(self) -> dict:
        return {
            "adherence": round(self.adherence, 4), "tier": self.tier,
            "gates_held": list(self.gates_held), "gates_skipped": list(self.gates_skipped),
            "charter_version": self.charter_version, "charter_hash": self.charter_hash,
            "in_charter": self.in_charter,
        }


def observation_dict(names, row) -> dict[str, float]:
    """Name the columns of one observation row.

    A charter's gates are keyed by feature name; the policy sees an unnamed
    vector. This is the join, and it is a function rather than a dict
    comprehension at four call sites so a column-order mismatch is one bug
    rather than four.
    """
    row = np.asarray(row, dtype=float).ravel()
    if len(row) != len(names):
        raise ValueError(f"{len(row)} values for {len(names)} feature names")
    return {n: float(v) for n, v in zip(names, row)}


def score_decision(charter: StrategyCharter, obs: dict[str, float],
                   direction: str | None = None) -> DecisionScore:
    """Score one decision against the charter handed in - no default, by design."""
    flags = charter.gate_flags(obs, direction)
    tier = charter.tier_for(flags)
    scoped = {g.name for g in charter.in_scope(direction)}
    held = tuple(n for n, ok in flags.items() if ok and n in scoped)
    skipped = tuple(n for n in scoped if not flags.get(n, False))
    entry_ok = True
    if charter.entry_tier:
        order = [t.name for t in charter.tiers]
        entry_ok = bool(tier) and order.index(tier) <= order.index(charter.entry_tier)
    return DecisionScore(
        adherence=charter.adherence(obs, direction), tier=tier,
        gates_held=held, gates_skipped=skipped,
        charter_version=charter.version, charter_hash=charter.content_hash,
        in_charter=entry_ok,
    )


def score_live(charter: StrategyCharter, obs: dict[str, float],
               direction: str | None = None) -> DecisionScore:
    """The live path. Refuses anything but an APPROVED charter.

    Not a convenience wrapper. A live decision scored against a PROPOSED
    charter would let an agent earn its adherence reward against a strategy
    nobody signed off, which is the loophole the whole governance design
    exists to close.
    """
    if charter.status != APPROVED:
        raise ValueError(
            f"live adherence must be scored against an APPROVED charter, not {charter.status}")
    return score_decision(charter, obs, direction)


# -- drift ------------------------------------------------------------------


SUSPEND_REASON = "ADHERENCE_DRIFT"


@dataclass
class DriftState:
    """What the monitor knows about one agent."""
    agent_id: str
    floor: float
    window: int
    recent: deque = field(default_factory=lambda: deque(maxlen=200))
    below_streak: int = 0
    suspended: bool = False
    suspended_at: str = ""
    skipped_counts: dict = field(default_factory=dict)

    @property
    def rolling(self) -> float:
        return float(np.mean(self.recent)) if self.recent else 0.0

    def top_skipped(self, n: int = 5) -> list[tuple[str, int]]:
        return sorted(self.skipped_counts.items(), key=lambda kv: -kv[1])[:n]


@dataclass
class DriftVerdict:
    """What the caller must do about it."""
    suspend: bool = False
    recovered: bool = False
    rolling: float = 0.0
    streak: int = 0
    message: str = ""
    skipped: list = field(default_factory=list)


class DriftMonitor:
    """Rolling live adherence, per agent, with a suspension rule.

    Consecutive trades below the floor rather than a rolling mean below the
    floor. A mean recovers slowly and would keep an already-drifted agent
    trading for dozens of trades after the fact; a streak reacts at the point
    the behaviour changed, and one lucky trade in the middle resetting the
    streak is the right behaviour - it means the agent is still sometimes
    following the charter.
    """

    def __init__(self) -> None:
        self._states: dict[str, DriftState] = {}

    def state(self, charter: StrategyCharter) -> DriftState:
        key = f"{charter.agent_id}@v{charter.version}"
        st = self._states.get(key)
        if st is None:
            st = DriftState(agent_id=charter.agent_id, floor=charter.adherence_floor,
                            window=charter.drift_trades)
            self._states[key] = st
        return st

    def record(self, charter: StrategyCharter, score: DecisionScore) -> DriftVerdict:
        """One closed trade's adherence. Returns what to do."""
        from datetime import datetime, timezone

        st = self.state(charter)
        st.recent.append(float(score.adherence))
        for g in score.gates_skipped:
            st.skipped_counts[g] = st.skipped_counts.get(g, 0) + 1

        v = DriftVerdict(rolling=st.rolling)
        if score.adherence < st.floor:
            st.below_streak += 1
        else:
            if st.below_streak:
                st.below_streak = 0
            if st.suspended and st.rolling >= st.floor:
                # Recovery is reported but NOT acted on automatically: an agent
                # that drifted and came back is still an agent whose charter no
                # longer describes it, and that is an admin's call.
                v.recovered = True
                v.message = (f"{charter.agent_id} adherence recovered to {st.rolling:.2f}; "
                             "it stays suspended until it is re-enabled by hand")
        v.streak = st.below_streak
        v.skipped = st.top_skipped()

        if st.below_streak >= st.window and not st.suspended:
            st.suspended = True
            st.suspended_at = datetime.now(timezone.utc).isoformat()
            v.suspend = True
            named = ", ".join(f"{g} ({n}x)" for g, n in v.skipped) or "none recorded"
            v.message = (
                f"{charter.agent_id}@v{charter.version} suspended: adherence below "
                f"{st.floor:.2f} for {st.below_streak} consecutive trades "
                f"(rolling {st.rolling:.2f}). Gates it has been skipping: {named}.")
        return v

    def suspended(self, charter: StrategyCharter) -> bool:
        return self.state(charter).suspended

    def reset(self, charter: StrategyCharter) -> None:
        """Re-enable by hand, after the admin has looked at it."""
        st = self.state(charter)
        st.suspended = False
        st.below_streak = 0
        st.skipped_counts.clear()


# One monitor per process, like `bot_service`. Live adherence is process state,
# not request state, and a per-request monitor would never accumulate a streak.
monitor = DriftMonitor()


def mean_adherence(scores) -> float:
    """The number the validation gate compares against 0.6."""
    vals = [float(s.adherence) for s in (scores or ())]
    return float(np.mean(vals)) if vals else 0.0
