"""
backend/rl/models.py

The four tables the agents and their charters live in.

WHY SEPARATE TABLES FOR AGENT AND CHARTER
-----------------------------------------
An agent has ONE policy and MANY charters over its life - v1 approved, v2
approved and v1 superseded, and so on. Every historical backtest has to stay
reproducible against the charter it actually ran under, so a charter is never
updated in place: approving v2 writes a new row and marks v1 `SUPERSEDED`.
Collapsing the two into one table would mean either losing the old charter or
losing the ability to say which one a run used.

`RLAdherence` is one row per closed trade. Small (a few hundred bytes), and it
is what makes the live drift chart and the suspension decision auditable
afterwards - "the agent stopped following its charter" has to be a record, not
a log line that rotated away.

TIMESTAMPS ARE NAIVE UTC, LIKE EVERY OTHER TABLE HERE
-----------------------------------------------------
`DateTime`, not `DateTime(timezone=True)`. Every other model in this codebase
stores naive UTC, and mixing the two conventions is not a style question: a
column written by `server_default=func.now()` comes back NAIVE from SQLite, so
a query that compares it against a timezone-aware value raises
`TypeError: can't compare offset-naive and offset-aware datetimes`. That fires
in production, where rows exist, and not in a test that only inserts them.

These tables were briefly timezone-aware and that is exactly the bug it
produced. Everything written here strips tzinfo first - see `naive_utc`.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)

from backend.data.models import Base, generate_id


def naive_utc(value=None):
    """A naive UTC datetime, which is what every table here stores.

    Takes a `datetime`, an ISO string, or nothing (meaning now). Returns the
    same instant with `tzinfo` removed, so it can be compared against a value
    SQLite returned from a `server_default` without raising.
    """
    from datetime import datetime, timezone

    if value is None:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return datetime.now(timezone.utc).replace(tzinfo=None)
    if getattr(value, "tzinfo", None) is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


class RLAgent(Base):
    """A trained agent: its pre-registration, its weights, and what it saw."""
    __tablename__ = "rl_agents"

    id = Column(Integer, primary_key=True, default=generate_id)
    agent_id = Column(String(120), unique=True, nullable=False, index=True)
    name = Column(String(120), nullable=False)
    created_by = Column(String(120), default="")

    # The frozen pre-registration, including the full list of symbols ATTEMPTED.
    spec_json = Column(Text, nullable=False)
    spec_hash = Column(String(32), index=True)
    optimiser = Column(String(32), default="")

    policy_json = Column(Text)
    scaler_json = Column(Text)
    policy_hash = Column(String(32), index=True)

    # [{symbol, timeframe, start, end, hash}] - what `datasets.overlap` reads to
    # decide whether a backtest window is in sample. Without this the overlap
    # check has nothing to compare against and would silently say "clean".
    trained_on_json = Column(Text, default="[]")
    training_json = Column(Text)              # the run: seeds, folds, dispersion
    validation_json = Column(Text)            # the section 4.6 report, pass or fail

    # REJECTED agents are kept. The record of what did not work is worth more
    # than another candidate, and a rejected spec stops the same idea being
    # retried as if it were new.
    status = Column(String(24), default="TRAINED", index=True)
    reject_reason = Column(Text, default="")

    live_adherence = Column(Float)             # rolling, written by the drift monitor
    suspended = Column(Boolean, default=False, index=True)
    suspended_reason = Column(Text, default="")

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now())


class RLCharter(Base):
    """One version of one agent's declared strategy. Never updated in place."""
    __tablename__ = "rl_charters"
    __table_args__ = (
        UniqueConstraint("agent_id", "version", name="uq_rl_charter_version"),
        Index("ix_rl_charter_agent_status", "agent_id", "status"),
    )

    id = Column(Integer, primary_key=True, default=generate_id)
    agent_id = Column(String(120), nullable=False, index=True)
    version = Column(Integer, nullable=False, default=1)
    status = Column(String(24), nullable=False, default="DRAFT", index=True)

    charter_json = Column(Text, nullable=False)
    # Identity by CONTENT - the strategy, not the approval metadata. Two
    # charters with the same hash are the same strategy, which is what lets a
    # rejected diff be blocked by hash.
    content_hash = Column(String(32), nullable=False, index=True)

    thesis = Column(Text, default="")
    adherence_floor = Column(Float, default=0.6)
    validation_json = Column(Text)

    approved_by = Column(String(120), default="")
    approved_at = Column(DateTime)
    superseded_by = Column(Integer)
    rejection_reason = Column(Text, default="")
    created_at = Column(DateTime, server_default=func.now())


class RLProposal(Base):
    """A change the agent wants and only the admin can make."""
    __tablename__ = "rl_proposals"
    __table_args__ = (
        Index("ix_rl_proposal_agent_status", "agent_id", "status"),
    )

    id = Column(Integer, primary_key=True, default=generate_id)
    agent_id = Column(String(120), nullable=False, index=True)
    from_version = Column(Integer, nullable=False)
    kind = Column(String(32), nullable=False)
    status = Column(String(16), default="OPEN", index=True)

    proposal_json = Column(Text, nullable=False)
    # The hash of the CHANGE. A rejection blocks this hash, so the same diff
    # cannot be resubmitted with different wording and no new evidence.
    diff_hash = Column(String(32), nullable=False, index=True)
    why = Column(Text, default="")

    evidence_approved_json = Column(Text)
    evidence_proposed_json = Column(Text)

    decided_by = Column(String(120), default="")
    decided_at = Column(DateTime)
    reason = Column(Text, default="")
    created_at = Column(DateTime, server_default=func.now())


class RLAdherence(Base):
    """One closed trade's adherence, live. The drift chart and the audit trail."""
    __tablename__ = "rl_adherence"
    __table_args__ = (
        Index("ix_rl_adherence_agent_time", "agent_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, default=generate_id)
    agent_id = Column(String(120), nullable=False, index=True)
    charter_version = Column(Integer, nullable=False)
    charter_hash = Column(String(32), default="")

    symbol = Column(String(60), default="")
    direction = Column(String(8), default="")
    group_id = Column(String(40), default="", index=True)
    adherence = Column(Float, nullable=False)
    tier = Column(String(8), default="")
    r_net = Column(Float)
    gates_skipped_json = Column(Text, default="[]")
    # Set on the row that tripped the suspension, so "why was it suspended" is
    # answerable from the data rather than from a log file.
    tripped_suspension = Column(Boolean, default=False)

    created_at = Column(DateTime, server_default=func.now())
