"""
backend/rl/suspension.py

What happens when an agent stops following its charter.

WHY THIS IS NOT DONE WHERE IT IS DETECTED
-----------------------------------------
Drift is detected in `notify_outcome`, which the backtester and the live
position manager both call from inside a worker thread, synchronously, with no
database session and no event loop. Writing a row or sending an email from
there would mean either opening a session on a thread that must not block or
calling `asyncio.run` inside a running loop. This project has already stalled
the whole API by doing work on the loop, so detection records and `drain`
acts - from the jobs loop, which has a session and is allowed to be slow.

THE SUSPENSION IS NOT THE EMAIL
-------------------------------
The in-process monitor stops taking the agent's signals the moment it fires;
`RLStrategy.on_bar` checks `monitor.suspended` before anything else. `drain`
makes that durable (so a restart does not quietly re-enable the agent) and
tells the admin. If the email fails, the agent stays suspended - the safety
property does not depend on the notification.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

from backend.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class Pending:
    agent_id: str
    charter_version: int
    message: str
    rolling: float
    streak: int
    skipped: list = field(default_factory=list)
    at: str = ""

    def __post_init__(self) -> None:
        self.at = self.at or datetime.now(timezone.utc).isoformat()


# Process-level, like `bot_service`. A per-request list would never accumulate.
_pending: list[Pending] = []


def record(agent_id: str, charter_version: int, verdict) -> None:
    """Called from the detection point. Does no I/O, by design."""
    _pending.append(Pending(agent_id=agent_id, charter_version=int(charter_version),
                            message=verdict.message, rolling=float(verdict.rolling),
                            streak=int(verdict.streak),
                            skipped=[list(x) for x in (verdict.skipped or [])]))
    logger.error(f"[RL] {verdict.message}")


def peek() -> list[dict]:
    return [p.__dict__ for p in _pending]


def clear() -> None:
    _pending.clear()


async def drain(session) -> int:
    """Make every pending suspension durable, and email the admin.

    Returns how many it SUCCESSFULLY handled, which is not the same as how many
    it took: one that fails is put back and retried on the next tick, and
    counting it as handled would make a wedged drain look like a quiet one.
    Idempotent in effect: suspending an already-suspended agent writes the same
    two fields again.
    """
    from backend.rl import registry as rlreg

    if not _pending:
        return 0
    taken, _pending[:] = list(_pending), []
    done = 0
    for p in taken:
        try:
            await rlreg.suspend(session, p.agent_id, p.message)
            _queue_email(session, p)
            # Telegram as well as email: an agent trading a strategy nobody
            # approved is the one RL event worth a phone buzzing for.
            from backend.rl import notify as rl_notify
            rl_notify.suspended(p.agent_id, p.charter_version, p.message)
            done += 1
        except Exception as exc:                        # noqa: BLE001
            # Put it back so it is retried rather than lost: an agent that is
            # suspended in memory and not in the database would come back after
            # a restart.
            _pending.append(p)
            logger.error(f"[RL] could not record the suspension of {p.agent_id}: {exc}")
    return done


def admin_address() -> str:
    """Who to tell. One place, so the three call sites cannot disagree."""
    return (os.getenv("ADMIN_EMAIL") or os.getenv("COMPANY_EMAIL")
            or os.getenv("SMTP_FROM") or "").strip()


def _queue_email(session, p: Pending) -> None:
    to = admin_address()
    if not to:
        logger.warning("[RL] no ADMIN_EMAIL set - the suspension is recorded but "
                       "nobody has been told")
        return
    from backend.notify.outbox import TRANSACTIONAL, Message, queue

    gates = "".join(f"<li>{name} &mdash; skipped {count} times</li>"
                    for name, count in (p.skipped or []))
    html = (f"<p>{p.message}</p>"
            f"<p>Rolling adherence {p.rolling:.2f} over the last window; "
            f"{p.streak} consecutive trades below the charter's floor.</p>"
            + (f"<p>Gates it has been skipping:</p><ul>{gates}</ul>" if gates else "")
            + "<p>The agent has stopped taking signals on that slot. It does not fall "
              "back to trading unsupervised - re-enable it by hand once you have looked "
              "at why it drifted.</p>")
    queue(session, Message(
        kind="rl_agent_suspended", to=to,
        subject=f"RL agent suspended: {p.agent_id} v{p.charter_version}",
        html=html, text=p.message,
        push_body=f"{p.agent_id} suspended - adherence drift",
        category=TRANSACTIONAL))
