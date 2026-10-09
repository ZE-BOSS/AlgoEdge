"""
backend/rl/charter.py

The agent's declared strategy, and the only thing it is allowed to be judged
against.

THE PROBLEM THIS SOLVES
-----------------------
The reward is 0.70 money + 0.30 "did it follow its strategy". That second term
is worth nothing if the agent can rewrite the strategy whenever following it
becomes inconvenient - it would simply learn to declare whatever it was about
to do anyway and collect 0.30 for free. So the strategy is a frozen,
versioned, content-hashed artefact that an admin approves, and adherence is
always scored against the APPROVED one.

The agent cannot edit it. When continued training finds something the charter
does not express, it emits a `CharterProposal` - a diff, with the validation
report for both the approved and the proposed version, on the same seeds and
the same folds. The admin approves or rejects. Until then live keeps trading
the approved charter.

GATES ARE PREDICATES, NOT PROSE
-------------------------------
A charter whose gates are sentences cannot be checked, so "it followed its
strategy" would be an assertion. Here a gate is `(feature, op, threshold)`
over the observation - executable, serialisable, and printable in English. That
is what makes `adherence.py` a computation.

WHAT A CHARTER CANNOT CONTAIN
-----------------------------
No risk percentage, no lot size, no stop distance, no daily loss limit. A gate
may only name a registered feature (`features.FEATURES`), and no feature
carries money management. `_validate_gates` enforces it, so an approved
charter cannot smuggle a position-sizing rule past the admin.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

import numpy as np

from backend.rl.features import FEATURES

# -- status -----------------------------------------------------------------

DRAFT = "DRAFT"
PROPOSED = "PROPOSED"
APPROVED = "APPROVED"
SUPERSEDED = "SUPERSEDED"
REJECTED = "REJECTED"

STATUSES = (DRAFT, PROPOSED, APPROVED, SUPERSEDED, REJECTED)

# What each status permits. The table from plan 4.10, as data, so the three
# call sites cannot disagree with each other.
PERMITS: dict[str, dict[str, bool]] = {
    DRAFT:      {"train": True,  "backtest": False, "live": False},
    PROPOSED:   {"train": False, "backtest": True,  "live": False},
    APPROVED:   {"train": False, "backtest": True,  "live": True},
    SUPERSEDED: {"train": False, "backtest": False, "live": False},
    REJECTED:   {"train": False, "backtest": False, "live": False},
}

# Which transitions are legal. Anything absent is refused, including
# REJECTED -> APPROVED: a rejection is a decision, not a holding pattern.
TRANSITIONS: dict[str, tuple[str, ...]] = {
    DRAFT: (PROPOSED, REJECTED),
    PROPOSED: (APPROVED, REJECTED, DRAFT),
    APPROVED: (SUPERSEDED,),
    SUPERSEDED: (),
    REJECTED: (),
}


class CharterError(RuntimeError):
    """A charter was used in a way its status does not permit."""


# -- gates ------------------------------------------------------------------

OPS: dict[str, Any] = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "between": None,          # handled separately: threshold is (lo, hi)
}

OP_ENGLISH = {">": "above", ">=": "at or above", "<": "below", "<=": "at or below"}

# A gate may fire for one direction only, both, or neither-specific.
BUY, SELL, EITHER = "BUY", "SELL", "EITHER"


@dataclass(frozen=True)
class Gate:
    """One named, executable condition over the observation.

    `hit_rate` and `mean_r` are measured, not asserted: they come from the
    training fold and are printed on the Charter tab so an admin approving a
    charter can see which of its gates actually carry the edge.
    """
    name: str
    feature: str
    op: str
    threshold: Any                      # float, or (lo, hi) for "between"
    direction: str = EITHER
    weight: float = 1.0
    hit_rate: float | None = None
    mean_r: float | None = None

    def __post_init__(self) -> None:
        if self.feature not in FEATURES:
            raise ValueError(
                f"gate {self.name!r} names {self.feature!r}, which is not a registered "
                "feature - a charter may only test things the agent can actually see")
        if self.op not in OPS:
            raise ValueError(f"gate {self.name!r} has unknown operator {self.op!r}")
        if self.op == "between":
            if not (isinstance(self.threshold, (tuple, list)) and len(self.threshold) == 2):
                raise ValueError(f"gate {self.name!r}: 'between' needs (lo, hi)")
        elif not isinstance(self.threshold, (int, float)):
            raise ValueError(f"gate {self.name!r}: threshold must be a number")
        if self.direction not in (BUY, SELL, EITHER):
            raise ValueError(f"gate {self.name!r}: direction must be BUY, SELL or EITHER")

    # -- evaluation ---------------------------------------------------------

    def value(self, obs: dict[str, float]) -> float | None:
        v = obs.get(self.feature)
        return None if v is None or not np.isfinite(v) else float(v)

    def holds(self, obs: dict[str, float], direction: str | None = None) -> bool:
        """True when this gate's condition is met on this observation.

        A gate scoped to BUY is vacuously NOT met on a sell - it is not an
        error and it is not a pass. That asymmetry matters for adherence: a
        long-only gate must not be counted as skipped when the agent went
        short under a short-only tier.
        """
        if direction and self.direction != EITHER and self.direction != direction:
            return False
        v = self.value(obs)
        if v is None:
            return False
        if self.op == "between":
            lo, hi = self.threshold
            return bool(lo <= v <= hi)
        return bool(OPS[self.op](v, self.threshold))

    def applies_to(self, direction: str | None) -> bool:
        """Whether this gate is even in scope for a trade in this direction."""
        return not direction or self.direction in (EITHER, direction)

    # -- English ------------------------------------------------------------

    @property
    def plain(self) -> str:
        what = FEATURES[self.feature].plain
        if self.op == "between":
            lo, hi = self.threshold
            cond = f"between {lo:g} and {hi:g}"
        else:
            cond = f"{OP_ENGLISH[self.op]} {float(self.threshold):g}"
        scope = "" if self.direction == EITHER else f" (longs only)" if self.direction == BUY \
            else " (shorts only)"
        return f"{what} is {cond}{scope}"

    def to_dict(self) -> dict:
        d = asdict(self)
        if isinstance(d.get("threshold"), tuple):
            d["threshold"] = list(d["threshold"])
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Gate":
        t = d.get("threshold")
        if isinstance(t, list):
            t = tuple(t)
        return cls(name=d["name"], feature=d["feature"], op=d["op"], threshold=t,
                   direction=d.get("direction", EITHER), weight=float(d.get("weight", 1.0)),
                   hit_rate=d.get("hit_rate"), mean_r=d.get("mean_r"))


@dataclass(frozen=True)
class Tier:
    """A named subset of gates, in the user's own A / B / C wording.

    `min_gates` is how many of `gates` must hold. A tier that required all of
    them would be one gate with a longer name; the point of a tier is to say
    "four of these six, and these two always".
    """
    name: str
    gates: tuple[str, ...]
    min_gates: int = 1
    required: tuple[str, ...] = ()
    direction: str = EITHER

    def __post_init__(self) -> None:
        if not self.gates:
            raise ValueError(f"tier {self.name!r} lists no gates")
        if self.min_gates < 1 or self.min_gates > len(self.gates):
            raise ValueError(f"tier {self.name!r}: min_gates must be 1..{len(self.gates)}")
        missing = set(self.required) - set(self.gates)
        if missing:
            raise ValueError(f"tier {self.name!r}: required gates not in the tier: {sorted(missing)}")

    def met(self, flags: dict[str, bool]) -> bool:
        if any(not flags.get(g, False) for g in self.required):
            return False
        return sum(1 for g in self.gates if flags.get(g, False)) >= self.min_gates

    def to_dict(self) -> dict:
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in asdict(self).items()}

    @classmethod
    def from_dict(cls, d: dict) -> "Tier":
        return cls(name=d["name"], gates=tuple(d.get("gates") or ()),
                   min_gates=int(d.get("min_gates", 1)),
                   required=tuple(d.get("required") or ()),
                   direction=d.get("direction", EITHER))


# -- the charter ------------------------------------------------------------


@dataclass
class StrategyCharter:
    """The agent's strategy, frozen and approved.

    `content_hash` covers the STRATEGY and nothing else - not the status, not
    who approved it, not when. Two charters with the same hash are the same
    strategy, which is what lets a rejected diff be blocked by hash and a
    historical run be reproduced against the charter it actually ran under.
    """
    agent_id: str
    version: int = 1
    status: str = DRAFT

    thesis: str = ""
    gates: tuple[Gate, ...] = ()
    tiers: tuple[Tier, ...] = ()
    entry_tier: str = ""
    exit_rule: str = ""
    instruments: tuple[str, ...] = ()
    timeframes: tuple[str, ...] = ()
    supervises: tuple[str, ...] = ()

    adherence_floor: float = 0.6
    drift_trades: int = 20               # consecutive trades below the floor -> suspend

    validation: dict = field(default_factory=dict)
    spec_hash: str = ""

    approved_by: str = ""
    approved_at: str = ""
    superseded_by: int | None = None
    rejection_reason: str = ""

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"unknown status {self.status!r}")
        names = [g.name for g in self.gates]
        if len(set(names)) != len(names):
            raise ValueError("two gates share a name - adherence would be ambiguous")
        known = set(names)
        for t in self.tiers:
            missing = set(t.gates) - known
            if missing:
                raise ValueError(f"tier {t.name!r} names gates that do not exist: {sorted(missing)}")
        if self.entry_tier and self.entry_tier not in {t.name for t in self.tiers}:
            raise ValueError(f"entry_tier {self.entry_tier!r} is not one of the tiers")
        if not 0.0 <= self.adherence_floor <= 1.0:
            raise ValueError("adherence_floor must be between 0 and 1")

    # -- permissions --------------------------------------------------------

    @property
    def permits(self) -> dict[str, bool]:
        return PERMITS[self.status]

    def require(self, what: str) -> None:
        """Raise unless this charter's status permits `what`.

        Called where the strategy is BUILT, not in the UI (plan 4.10), so an
        API call cannot route around it.
        """
        if not self.permits.get(what, False):
            raise CharterError(
                f"charter {self.agent_id}@v{self.version} is {self.status}, "
                f"which does not permit {what}")

    def can_transition_to(self, status: str) -> bool:
        return status in TRANSITIONS.get(self.status, ())

    # -- evaluation ---------------------------------------------------------

    def gate_flags(self, obs: dict[str, float], direction: str | None = None) -> dict[str, bool]:
        """Which of this charter's gates held on this observation."""
        return {g.name: g.holds(obs, direction) for g in self.gates}

    def in_scope(self, direction: str | None = None) -> list[Gate]:
        return [g for g in self.gates if g.applies_to(direction)]

    def tier_for(self, flags: dict[str, bool]) -> str | None:
        """The best tier this observation satisfies, in declared order."""
        for t in self.tiers:
            if t.met(flags):
                return t.name
        return None

    def adherence(self, obs: dict[str, float], direction: str | None = None) -> float:
        """The fraction of the IN-SCOPE gates that held, weighted.

        In-scope rather than all: a long-only gate is not a gate the agent
        skipped when it went short. Scoring it as a miss would make a
        well-behaved short look like a charter violation, and the drift monitor
        would suspend an agent for doing what the charter says.
        """
        scoped = self.in_scope(direction)
        if not scoped:
            return 0.0
        total = sum(g.weight for g in scoped) or 1.0
        got = sum(g.weight for g in scoped if g.holds(obs, direction))
        return float(got / total)

    def skipped_gates(self, obs: dict[str, float], direction: str | None = None) -> list[str]:
        """Named, because the drift email has to say WHICH gates it is skipping."""
        return [g.name for g in self.in_scope(direction) if not g.holds(obs, direction)]

    # -- English ------------------------------------------------------------

    def english(self) -> str:
        """The charter, readable. This is what an admin approves."""
        lines = [f"{self.agent_id} v{self.version} ({self.status})", ""]
        if self.thesis:
            lines += [self.thesis.strip(), ""]
        if self.instruments:
            lines.append(f"Instruments: {', '.join(self.instruments)}"
                         + (f" on {', '.join(self.timeframes)}" if self.timeframes else ""))
        lines.append("")
        lines.append("Gates:")
        for g in self.gates:
            measured = ""
            if g.hit_rate is not None:
                measured = f"   [fires {g.hit_rate:.0%}"
                if g.mean_r is not None:
                    measured += f", mean {g.mean_r:+.3f}R"
                measured += "]"
            lines.append(f"  - {g.name}: {g.plain}{measured}")
        if self.tiers:
            lines.append("")
            lines.append("Setups:")
            for t in self.tiers:
                req = f", always {', '.join(t.required)}" if t.required else ""
                lines.append(f"  {t.name}: {t.min_gates} of {len(t.gates)} "
                             f"({', '.join(t.gates)}){req}")
        if self.entry_tier:
            lines += ["", f"Entry: tier {self.entry_tier} or better."]
        if self.exit_rule:
            lines.append(f"Exit: {self.exit_rule}")
        if self.supervises:
            lines.append(f"Supervises: {', '.join(self.supervises)}")
        lines.append(f"Adherence floor: {self.adherence_floor:.2f} "
                     f"(suspended after {self.drift_trades} trades below it)")
        return "\n".join(lines)

    # -- identity -----------------------------------------------------------

    def strategy_content(self) -> dict:
        """Just the strategy. Status and approval metadata deliberately out."""
        return {
            "agent_id": self.agent_id,
            "thesis": self.thesis.strip(),
            "gates": [g.to_dict() | {"hit_rate": None, "mean_r": None} for g in self.gates],
            "tiers": [t.to_dict() for t in self.tiers],
            "entry_tier": self.entry_tier,
            "exit_rule": self.exit_rule,
            "instruments": sorted(self.instruments),
            "timeframes": sorted(self.timeframes),
            "supervises": sorted(self.supervises),
            "adherence_floor": round(float(self.adherence_floor), 6),
        }

    @property
    def content_hash(self) -> str:
        blob = json.dumps(self.strategy_content(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id, "version": self.version, "status": self.status,
            "thesis": self.thesis,
            "gates": [g.to_dict() for g in self.gates],
            "tiers": [t.to_dict() for t in self.tiers],
            "entry_tier": self.entry_tier, "exit_rule": self.exit_rule,
            "instruments": list(self.instruments), "timeframes": list(self.timeframes),
            "supervises": list(self.supervises),
            "adherence_floor": self.adherence_floor, "drift_trades": self.drift_trades,
            "validation": self.validation, "spec_hash": self.spec_hash,
            "approved_by": self.approved_by, "approved_at": self.approved_at,
            "superseded_by": self.superseded_by,
            "rejection_reason": self.rejection_reason,
            "content_hash": self.content_hash,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "StrategyCharter":
        return cls(
            agent_id=d["agent_id"], version=int(d.get("version", 1)),
            status=d.get("status", DRAFT), thesis=d.get("thesis", ""),
            gates=tuple(Gate.from_dict(g) for g in d.get("gates") or ()),
            tiers=tuple(Tier.from_dict(t) for t in d.get("tiers") or ()),
            entry_tier=d.get("entry_tier", ""), exit_rule=d.get("exit_rule", ""),
            instruments=tuple(d.get("instruments") or ()),
            timeframes=tuple(d.get("timeframes") or ()),
            supervises=tuple(d.get("supervises") or ()),
            adherence_floor=float(d.get("adherence_floor", 0.6)),
            drift_trades=int(d.get("drift_trades", 20)),
            validation=d.get("validation") or {}, spec_hash=d.get("spec_hash", ""),
            approved_by=d.get("approved_by", ""), approved_at=d.get("approved_at", ""),
            superseded_by=d.get("superseded_by"),
            rejection_reason=d.get("rejection_reason", ""),
        )


# -- proposals --------------------------------------------------------------

ADD_GATE = "ADD_GATE"
REMOVE_GATE = "REMOVE_GATE"
RETIER = "RETIER"
WIDEN_INSTRUMENTS = "WIDEN_INSTRUMENTS"
CHANGE_EXIT = "CHANGE_EXIT"
KINDS = (ADD_GATE, REMOVE_GATE, RETIER, WIDEN_INSTRUMENTS, CHANGE_EXIT)

OPEN = "OPEN"
ACCEPTED = "ACCEPTED"
DECLINED = "DECLINED"


class ProposalError(RuntimeError):
    """A proposal that may not be submitted."""


@dataclass
class CharterProposal:
    """A change the agent wants, which only the admin can make.

    Three rules are enforced in `validate_submission`, not in the UI:

      * one open proposal per agent, so changes cannot stack up unseen;
      * the proposal must already BEAT the approved charter on the same gate,
        the same seeds and the same folds - nobody should be asked to approve
        something that has not earned it;
      * the same diff cannot be re-proposed after a rejection without new
        evidence, compared by hash.
    """
    agent_id: str
    from_version: int
    proposed: StrategyCharter
    kind: str = ADD_GATE
    why: str = ""
    status: str = OPEN
    evidence_approved: dict = field(default_factory=dict)
    evidence_proposed: dict = field(default_factory=dict)
    created_at: str = ""
    decided_at: str = ""
    decided_by: str = ""
    reason: str = ""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown proposal kind {self.kind!r}")
        self.created_at = self.created_at or datetime.now(timezone.utc).isoformat()

    @property
    def diff_hash(self) -> str:
        """Identity of the CHANGE, so a rejected diff can be blocked by hash
        even if the agent re-derives it with different wording."""
        blob = json.dumps({"from": self.from_version,
                           "to": self.proposed.strategy_content()},
                          sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def diff(self, approved: StrategyCharter) -> dict:
        """Gate by gate, old against new, in plain language."""
        old = {g.name: g for g in approved.gates}
        new = {g.name: g for g in self.proposed.gates}
        added = [new[n].plain for n in new if n not in old]
        removed = [old[n].plain for n in old if n not in new]
        changed = [
            {"gate": n, "was": old[n].plain, "now": new[n].plain}
            for n in new if n in old and old[n].to_dict() != new[n].to_dict()
        ]
        return {
            "added_gates": added,
            "removed_gates": removed,
            "changed_gates": changed,
            "tiers_changed": [t.to_dict() for t in self.proposed.tiers]
            != [t.to_dict() for t in approved.tiers],
            "instruments_added": sorted(set(self.proposed.instruments) - set(approved.instruments)),
            "instruments_removed": sorted(set(approved.instruments) - set(self.proposed.instruments)),
            "exit_was": approved.exit_rule,
            "exit_now": self.proposed.exit_rule,
            "why": self.why,
        }


def beats(proposed: dict, approved: dict) -> tuple[bool, str]:
    """Does the proposed charter's validation report beat the approved one?

    Deliberately conservative and deliberately multi-criteria: a proposal that
    raises expectancy while raising the probability of backtest overfitting is
    not an improvement, it is a better-looking overfit. Expectancy must rise,
    and neither PBO nor the deflated Sharpe may get worse.
    """
    def num(d: dict, key: str, default=None):
        v = (d or {}).get(key)
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    p_exp, a_exp = num(proposed, "expectancy_r"), num(approved, "expectancy_r")
    if p_exp is None or a_exp is None:
        return False, "both versions need an expectancy measured the same way"
    if p_exp <= a_exp:
        return False, f"expectancy {p_exp:+.4f}R does not beat {a_exp:+.4f}R"

    p_pbo, a_pbo = num(proposed, "pbo"), num(approved, "pbo")
    if p_pbo is not None and a_pbo is not None and p_pbo > a_pbo + 1e-9:
        return False, f"probability of backtest overfitting rose from {a_pbo:.1%} to {p_pbo:.1%}"

    p_dsr, a_dsr = num(proposed, "deflated_sharpe"), num(approved, "deflated_sharpe")
    if p_dsr is not None and a_dsr is not None and p_dsr < a_dsr - 1e-9:
        return False, f"deflated Sharpe fell from {a_dsr:.3f} to {p_dsr:.3f}"

    if not (proposed or {}).get("passed", True):
        return False, "the proposed charter does not pass the validation gate"
    return True, (f"expectancy {a_exp:+.4f}R -> {p_exp:+.4f}R "
                  f"with no worsening of PBO or the deflated Sharpe")


def validate_submission(proposal: CharterProposal, approved: StrategyCharter,
                        open_proposals: Iterable[CharterProposal] = (),
                        rejected_hashes: Iterable[str] = ()) -> None:
    """Raise unless this proposal may be put in front of the admin."""
    if approved.status != APPROVED:
        raise ProposalError("there is no approved charter to propose a change to")
    if proposal.from_version != approved.version:
        raise ProposalError(
            f"proposal is against v{proposal.from_version} but v{approved.version} is live")
    if proposal.proposed.content_hash == approved.content_hash:
        raise ProposalError("the proposed charter is identical to the approved one")

    still_open = [p for p in open_proposals if p.status == OPEN and p.agent_id == proposal.agent_id]
    if still_open:
        raise ProposalError(
            "this agent already has an open proposal - decide that one first, so "
            "changes cannot stack up unseen")

    if proposal.diff_hash in set(rejected_hashes):
        raise ProposalError(
            "this exact change was rejected before; it needs new evidence, not a resubmission")

    ok, why = beats(proposal.evidence_proposed, proposal.evidence_approved)
    if not ok:
        raise ProposalError(f"a proposal must already beat the approved charter: {why}")


def approve(approved: StrategyCharter, proposal: CharterProposal, by: str,
            ) -> tuple[StrategyCharter, StrategyCharter]:
    """Approve a proposal: bump the version, supersede the old charter.

    Returns `(new_approved, superseded_old)`. The old one is kept rather than
    overwritten, because every historical run has to stay reproducible against
    the charter it actually ran under.
    """
    if proposal.status != OPEN:
        raise ProposalError(f"proposal is {proposal.status}, not open")
    if not approved.can_transition_to(SUPERSEDED):
        raise CharterError(f"a {approved.status} charter cannot be superseded")

    now = datetime.now(timezone.utc).isoformat()
    new = StrategyCharter.from_dict(proposal.proposed.to_dict())
    new.version = approved.version + 1
    new.status = APPROVED
    new.approved_by, new.approved_at = by, now
    new.validation = proposal.evidence_proposed or new.validation

    old = StrategyCharter.from_dict(approved.to_dict())
    old.status = SUPERSEDED
    old.superseded_by = new.version

    proposal.status = ACCEPTED
    proposal.decided_at, proposal.decided_by = now, by
    return new, old


def reject(proposal: CharterProposal, by: str, reason: str) -> CharterProposal:
    """Record the admin's reason. The reason is required, not optional: the
    agent may not re-propose the same diff, and "no" without a reason is
    unusable six months later."""
    if proposal.status != OPEN:
        raise ProposalError(f"proposal is {proposal.status}, not open")
    if not (reason or "").strip():
        raise ProposalError("a rejection needs a reason - it is what blocks the resubmission")
    proposal.status = DECLINED
    proposal.reason = reason.strip()
    proposal.decided_by = by
    proposal.decided_at = datetime.now(timezone.utc).isoformat()
    return proposal


def propose_draft(charter: StrategyCharter) -> StrategyCharter:
    """DRAFT -> PROPOSED. Freezes the charter and opens it for backtesting.

    A draft may be trained against and nothing else; it is the proposal step
    that makes a charter backtestable, labelled unapproved. There is no path
    from DRAFT straight to APPROVED, so an agent cannot reach a live slot
    without a run the admin could have looked at.
    """
    if not charter.can_transition_to(PROPOSED):
        raise CharterError(f"a {charter.status} charter cannot be proposed")
    charter.status = PROPOSED
    return charter


def approve_charter(charter: StrategyCharter, by: str) -> StrategyCharter:
    """The first approval: a PROPOSED charter becomes the live one."""
    if not charter.can_transition_to(APPROVED):
        raise CharterError(
            f"a {charter.status} charter cannot be approved - it must be PROPOSED first, "
            "which is what makes it backtestable before anyone signs it off")
    charter.status = APPROVED
    charter.approved_by = by
    charter.approved_at = datetime.now(timezone.utc).isoformat()
    return charter
