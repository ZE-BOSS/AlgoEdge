"""
backend/notify/company.py

The legal identity, cached, so a synchronous email template can read it.

WHY A CACHE
-----------
`templates.py` builds a message body synchronously, at the moment the event
happens, inside whatever transaction is open. It cannot await a database read,
and threading a profile argument through twenty-five template functions would
put the same parameter in twenty-five signatures to serve one footer.

So the profile is loaded into a process-wide cache and read synchronously.
A single row that changes when an admin saves it is exactly the shape a cache
suits: `refresh()` on startup and on every write, `invalidate()` if in doubt.

WHAT HAPPENS WHILE IT IS EMPTY
------------------------------
Nothing breaks and nothing is invented. `Snapshot.empty()` carries blank
strings, the email footer omits what it does not have, and
`can_send_commercial_email` is False — which `outbox.deliver` treats as a
refusal for commercial mail and ignores for transactional mail. An unsent
marketing email costs nothing; one sent without a postal address is charged
per message.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class Snapshot:
    """A read-only copy of the profile, safe to hold across event loops."""

    legal_entity_name: str = ""
    trading_name: str = ""
    jurisdiction: str = ""
    registration_number: str = ""
    postal_address: str = ""
    support_email: str = ""
    privacy_email: str = ""
    policy_version: str = ""

    @property
    def display_name(self) -> str:
        return self.legal_entity_name or self.trading_name

    @property
    def can_send_commercial_email(self) -> bool:
        return bool(self.display_name and self.postal_address)

    @property
    def footer_identity(self) -> str:
        """The line a compliant footer must carry, or "" when we cannot yet.

        Never a partial claim: without both a name and an address this returns
        nothing rather than half of a legal notice.
        """
        if not self.can_send_commercial_email:
            return ""
        bits = [self.display_name]
        if self.registration_number:
            bits.append(f"registered no. {self.registration_number}")
        bits.append(self.postal_address)
        return " · ".join(bits)

    @classmethod
    def empty(cls) -> "Snapshot":
        return cls()


_cached: Snapshot | None = None


def get() -> Snapshot:
    """The current profile. Never raises, never returns None."""
    return _cached if _cached is not None else Snapshot.empty()


def set_snapshot(snap: Snapshot | None) -> None:
    """Replace the cache. Used by `refresh()` and directly by tests."""
    global _cached
    _cached = snap


def invalidate() -> None:
    set_snapshot(None)


def _from_row(row) -> Snapshot:
    return Snapshot(
        legal_entity_name=(row.legal_entity_name or "").strip(),
        trading_name=(row.trading_name or "").strip(),
        jurisdiction=(row.jurisdiction or "").strip(),
        registration_number=(row.registration_number or "").strip(),
        postal_address=row.postal_address,
        support_email=(row.support_email or "").strip(),
        privacy_email=(row.privacy_email or "").strip(),
        policy_version=(row.policy_version or "").strip(),
    )


async def current_row(session):
    """The newest version, or None. Does NOT create a row.

    Unlike `fund.current_settings`, an absent profile is not defaulted into
    existence: a blank legal identity written to the database looks like a
    decision somebody made, and this one has not been made yet.
    """
    from sqlalchemy import desc, select

    from backend.investor.models import CompanyProfile

    return (await session.execute(
        select(CompanyProfile).order_by(desc(CompanyProfile.version)).limit(1)
    )).scalar_one_or_none()


async def refresh(session=None) -> Snapshot:
    """Reload the cache from the database and return what it now holds."""
    if session is not None:
        row = await current_row(session)
        snap = _from_row(row) if row is not None else Snapshot.empty()
        set_snapshot(snap)
        return snap

    from backend.data.database import async_session

    async with async_session() as s:
        return await refresh(s)


async def refresh_quietly() -> Snapshot:
    """`refresh()` for startup, where a database that is not ready yet must not
    stop the application from booting."""
    try:
        return await refresh()
    except Exception as exc:
        logger.warning(f"[COMPANY] could not load the legal profile: {exc}")
        set_snapshot(Snapshot.empty())
        return get()
