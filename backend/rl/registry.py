"""
backend/rl/registry.py

Approved agents, and how they become selectable like any other strategy.

THE ONE RULE
------------
`APPROVED` is enforced where the strategy is BUILT, not in the UI. `build()`
raises unless the agent has an approved charter, so an API call that names
`RL::thing@3` cannot route around the Charter tab. Every other guard in this
file is convenience; that one is the governance.

HOW AN AGENT BECOMES SELECTABLE
-------------------------------
`RL::<name>@<version>` is registered into `strategies.registry` through the
same `_STRATEGIES` dict every rule-based strategy uses. So the Backtester's
strategy list, the slot editor, the Strategy Lab and the live scan loop all see
it without knowing it is different - which is the point. `register_approved()`
is called at startup and after an approval.

WHAT IS STORED WHERE
--------------------
The DB holds the agent, its charters and its proposals (`models.py`). The
weights live in both: `policy_json` on the row, so a VPS with no `data/rl`
directory can still serve an approved agent, and on disk under `data/rl/` for
the training artefacts that are too big to want in a row.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select

from backend.rl import charter as ch
from backend.rl import features as feat
from backend.rl import policy as pol
from backend.rl.models import (
    RLAdherence,
    RLAgent,
    RLCharter,
    RLProposal,
    naive_utc,
)
from backend.rl.spec import AgentSpec
from backend.utils.logger import get_logger

logger = get_logger(__name__)

PREFIX = "RL::"

TRAINED = "TRAINED"
APPROVED = "APPROVED"
REJECTED = "REJECTED"


def strategy_id_for(name: str, version: int) -> str:
    return f"{PREFIX}{name}@{version}"


def parse_strategy_id(strategy_id: str) -> tuple[str, int] | None:
    """`RL::name@3` -> `("name", 3)`, or None when it is not an agent at all."""
    sid = str(strategy_id or "")
    if not sid.startswith(PREFIX):
        return None
    body = sid[len(PREFIX):]
    if "@" not in body:
        return body, 1
    name, _, ver = body.rpartition("@")
    try:
        return name, int(ver)
    except ValueError:
        return body, 1


def is_agent(strategy_id: str) -> bool:
    return str(strategy_id or "").startswith(PREFIX)


class NotApproved(PermissionError):
    """Something tried to build an agent that no admin has approved."""


# -- the loaded form --------------------------------------------------------


@dataclass
class LoadedAgent:
    """Everything needed to run an agent: spec, weights, scaler, charter."""
    agent_id: str
    spec: AgentSpec
    policy: pol.LinearPolicy
    scaler: feat.Scaler
    charter: ch.StrategyCharter
    trained_on: list

    @property
    def strategy_id(self) -> str:
        return strategy_id_for(self.spec.name, self.charter.version)

    def require_live(self) -> None:
        self.charter.require("live")

    def require_backtest(self) -> None:
        self.charter.require("backtest")


# -- reading ----------------------------------------------------------------


async def get_agent(session, agent_id: str) -> RLAgent | None:
    rows = await session.execute(select(RLAgent).where(RLAgent.agent_id == agent_id))
    return rows.scalar_one_or_none()


async def list_agents(session, *, status: str | None = None) -> list[RLAgent]:
    q = select(RLAgent).order_by(RLAgent.created_at.desc())
    if status:
        q = q.where(RLAgent.status == status)
    return list((await session.execute(q)).scalars())


async def get_charter(session, agent_id: str, version: int | None = None
                      ) -> ch.StrategyCharter | None:
    q = select(RLCharter).where(RLCharter.agent_id == agent_id)
    if version is None:
        # The APPROVED one, never "the newest". A newer DRAFT or PROPOSED
        # charter must not become what live is measured against just by
        # existing - that would be the agent changing its own strategy.
        q = q.where(RLCharter.status == ch.APPROVED)
    else:
        q = q.where(RLCharter.version == version)
    row = (await session.execute(q.order_by(RLCharter.version.desc()))).scalars().first()
    return ch.StrategyCharter.from_dict(json.loads(row.charter_json)) if row else None


async def list_charters(session, agent_id: str) -> list[ch.StrategyCharter]:
    rows = (await session.execute(
        select(RLCharter).where(RLCharter.agent_id == agent_id)
        .order_by(RLCharter.version.desc()))).scalars()
    return [ch.StrategyCharter.from_dict(json.loads(r.charter_json)) for r in rows]


async def open_proposals(session, agent_id: str) -> list[RLProposal]:
    rows = await session.execute(
        select(RLProposal).where(RLProposal.agent_id == agent_id,
                                 RLProposal.status == ch.OPEN))
    return list(rows.scalars())


async def rejected_diff_hashes(session, agent_id: str) -> list[str]:
    rows = await session.execute(
        select(RLProposal.diff_hash).where(RLProposal.agent_id == agent_id,
                                           RLProposal.status == ch.DECLINED))
    return [r for (r,) in rows.all()]


# -- building ---------------------------------------------------------------


async def load(session, agent_id: str, *, for_live: bool = False,
               version: int | None = None) -> LoadedAgent:
    """Load an agent, refusing anything its charter's status does not permit.

    This is the enforcement point named in plan 4.10: not the UI, not the
    route. Every path that ends in a running agent - the Backtester, a live
    slot, the Strategy Lab - comes through here.
    """
    row = await get_agent(session, agent_id)
    if row is None:
        raise LookupError(f"no agent {agent_id!r}")
    if row.status == REJECTED:
        raise NotApproved(f"{agent_id} was rejected: {row.reject_reason or 'no reason recorded'}")
    if row.suspended and for_live:
        raise NotApproved(
            f"{agent_id} is suspended from live: {row.suspended_reason or 'adherence drift'}. "
            "It does not fall back to trading unsupervised - re-enable it by hand once you "
            "have looked at why.")

    charter = await get_charter(session, agent_id, version)
    if charter is None:
        raise NotApproved(
            f"{agent_id} has no {'approved ' if version is None else ''}charter - "
            "nothing reaches a backtest or a live slot until the strategy it declares "
            "has been approved")
    charter.require("live" if for_live else "backtest")

    if not row.policy_json:
        raise LookupError(f"{agent_id} has no stored policy")
    spec = AgentSpec.from_dict(json.loads(row.spec_json))
    policy = pol.LinearPolicy.from_dict(json.loads(row.policy_json))
    scaler = feat.Scaler.from_dict(json.loads(row.scaler_json or "{}"))
    if not scaler.fitted:
        raise LookupError(
            f"{agent_id} has no fitted scaler - an agent whose scaler was not stored "
            "cannot be served, because the observation would be on a different scale "
            "from the one it was trained on")
    return LoadedAgent(agent_id=agent_id, spec=spec, policy=policy, scaler=scaler,
                       charter=charter,
                       trained_on=json.loads(row.trained_on_json or "[]"))


# -- writing ----------------------------------------------------------------


async def save_agent(session, *, agent_id: str, spec: AgentSpec, policy: pol.LinearPolicy,
                     scaler: feat.Scaler, trained_on: list, training: dict | None = None,
                     validation: dict | None = None, optimiser: str = "",
                     created_by: str = "", status: str = TRAINED) -> RLAgent:
    row = await get_agent(session, agent_id)
    if row is None:
        row = RLAgent(agent_id=agent_id, name=spec.name)
        session.add(row)
    row.name = spec.name
    row.created_by = created_by or row.created_by or ""
    row.spec_json = json.dumps(spec.to_dict())
    row.spec_hash = spec.content_hash
    row.optimiser = optimiser or row.optimiser or ""
    row.policy_json = json.dumps(policy.to_dict())
    row.policy_hash = policy.content_hash
    row.scaler_json = json.dumps(scaler.to_dict())
    row.trained_on_json = json.dumps(trained_on or [])
    if training is not None:
        row.training_json = json.dumps(training)
    if validation is not None:
        row.validation_json = json.dumps(validation)
    row.status = status
    return row


async def save_charter(session, charter: ch.StrategyCharter) -> RLCharter:
    rows = await session.execute(
        select(RLCharter).where(RLCharter.agent_id == charter.agent_id,
                                RLCharter.version == charter.version))
    row = rows.scalar_one_or_none()
    if row is None:
        row = RLCharter(agent_id=charter.agent_id, version=charter.version)
        session.add(row)
    row.status = charter.status
    row.charter_json = json.dumps(charter.to_dict())
    row.content_hash = charter.content_hash
    row.thesis = charter.thesis
    row.adherence_floor = float(charter.adherence_floor)
    row.validation_json = json.dumps(charter.validation or {})
    row.approved_by = charter.approved_by
    row.rejection_reason = charter.rejection_reason
    row.superseded_by = charter.superseded_by
    if charter.approved_at:
        # Naive UTC: `rl_charters.approved_at` is a plain `DateTime`, like
        # every other timestamp column in this codebase. An aware value here
        # cannot be compared against one SQLite returned from a
        # `server_default` without raising.
        row.approved_at = naive_utc(charter.approved_at)
    return row


async def record_adherence(session, agent_id: str, charter: ch.StrategyCharter,
                           score, *, symbol: str = "", direction: str = "",
                           group_id: str = "", r_net: float | None = None,
                           tripped: bool = False) -> RLAdherence:
    row = RLAdherence(
        agent_id=agent_id, charter_version=charter.version,
        charter_hash=charter.content_hash, symbol=symbol, direction=direction,
        group_id=group_id, adherence=float(score.adherence), tier=score.tier or "",
        r_net=r_net, gates_skipped_json=json.dumps(list(score.gates_skipped)),
        tripped_suspension=bool(tripped))
    session.add(row)
    return row


async def suspend(session, agent_id: str, reason: str) -> None:
    row = await get_agent(session, agent_id)
    if row is None:
        return
    row.suspended = True
    row.suspended_reason = reason
    logger.warning(f"[RL] {agent_id} suspended from live: {reason}")


async def unsuspend(session, agent_id: str) -> None:
    row = await get_agent(session, agent_id)
    if row is None:
        return
    row.suspended = False
    row.suspended_reason = ""


# -- making agents selectable ----------------------------------------------


def _register(strategy_id: str, agent_id: str) -> None:
    """Put one agent into `strategies.registry` under `RL::name@version`.

    A class per agent, because `get_strategy` returns a CLASS that the engine
    then constructs with a user config. The class carries only the agent id;
    everything else is loaded from the DB when it is constructed, so an
    approval that changes the charter does not need the registry rebuilt.
    """
    from backend.strategies.registry import _STRATEGIES
    from backend.strategies.strategy_rl.engine import RLStrategy

    cls = type(f"RLStrategy_{agent_id}", (RLStrategy,),
               {"strategy_id": strategy_id, "AGENT_ID": agent_id})
    _STRATEGIES[strategy_id] = cls


async def register_approved(session) -> list[str]:
    """Register every agent with an approved charter. Called at startup.

    Agents with no approved charter are deliberately absent from the registry
    rather than present-and-refused: a strategy you cannot select is clearer
    than one that appears in the list and then errors.
    """
    rows = await session.execute(
        select(RLCharter.agent_id, RLCharter.version)
        .where(RLCharter.status == ch.APPROVED))
    out = []
    for agent_id, version in rows.all():
        row = await get_agent(session, agent_id)
        if row is None or row.status == REJECTED or not row.policy_json:
            continue
        sid = strategy_id_for(row.name, int(version))
        try:
            cache_put(await load(session, agent_id))
        except Exception as exc:                        # noqa: BLE001
            logger.error(f"[RL] {agent_id} has an approved charter but will not load "
                         f"({type(exc).__name__}: {exc}) - not registered")
            continue
        _register(sid, agent_id)
        out.append(sid)
    if out:
        logger.info(f"[RL] registered {len(out)} approved agent(s): {', '.join(out)}")
    return out


def unregister(strategy_id: str) -> None:
    from backend.strategies.registry import _STRATEGIES
    _STRATEGIES.pop(strategy_id, None)


# -- the sync cache the live engine reads -----------------------------------
#
# `strategies.registry.get_strategy` returns a CLASS that the engine then
# constructs synchronously, inside a worker thread. Loading an agent needs the
# database, which is async. So approved agents are loaded ONCE, at registration,
# and the strategy class reads them from here.
#
# A missing entry is an error rather than a lazy load: a lazy load would mean
# an unapproved agent could be pulled in by whatever first asked for it, which
# is precisely the bypass `load()` exists to prevent.

AGENT_CACHE: dict[str, "LoadedAgent"] = {}


def cache_put(agent: "LoadedAgent") -> None:
    AGENT_CACHE[agent.agent_id] = agent


def cached(agent_id: str) -> "LoadedAgent":
    agent = AGENT_CACHE.get(agent_id)
    if agent is None:
        raise NotApproved(
            f"{agent_id} is not loaded. Only agents with an APPROVED charter are "
            "loaded at startup, so this means it has not been approved, it was "
            "rejected, or it is suspended from live.")
    return agent


def cache_clear(agent_id: str | None = None) -> None:
    if agent_id is None:
        AGENT_CACHE.clear()
    else:
        AGENT_CACHE.pop(agent_id, None)
