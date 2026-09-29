"""
backend/investor/auth.py

Investor credentials. Kept apart from the operators' auth in backend/api on
purpose: an investor must never be able to reach an admin or trading route, and
that should not depend on every such route remembering to check a role.

THREE THINGS KEEP THE TWO AUDIENCES APART
-----------------------------------------
1. A different signing key. An investor token is signed with INVESTOR_JWT_SECRET
   (derived from the admin key when unset, but never equal to it). The admin
   dependency verifies against the admin key, so an investor token fails on its
   signature before any claim is read. Nothing has to remember to reject it.
2. An audience claim. Investor tokens carry aud=alphavantiq-investor and the
   investor dependency requires it, which is the second check if the keys were
   ever configured the same.
3. Different tables. `sub` is an investors.id, which no `users` row has.

A token also carries a fingerprint of the password hash, so changing the
password signs out every other session. A stolen refresh token dies the moment
the investor resets their password, which is the thing they will actually do.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select, update

from backend.api.deps import JWT_ALGORITHM, JWT_SECRET_KEY
from backend.investor.fund import FundError, audit
from backend.investor.models import (
    TOKEN_INVITE,
    TOKEN_RESET,
    Investor,
    InvestorToken,
)

AUDIENCE = "alphavantiq-investor"
INVESTOR_JWT_SECRET = os.getenv("INVESTOR_JWT_SECRET") or hashlib.sha256(
    f"{JWT_SECRET_KEY}|investor-audience".encode()).hexdigest()

ACCESS_MINUTES = int(os.getenv("INVESTOR_ACCESS_MINUTES", "30"))
REFRESH_DAYS = int(os.getenv("INVESTOR_REFRESH_DAYS", "14"))
INVITE_HOURS = 72
RESET_MINUTES = 30
MIN_PASSWORD = 10

# bcrypt, matching the operators' auth. (The plan named Argon2id; it is not a
# dependency yet, and bcrypt at passlib's default cost is sound. Moving is a
# one-line scheme change here, with passlib re-hashing on next login.)
_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


class AuthError(FundError):
    """A refused sign-in or token. The message is safe to show the investor."""


def _now() -> datetime:
    # Stored naive-UTC, matching every other DateTime column on the investor side.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _fingerprint(password_hash: str | None) -> str:
    return hashlib.sha256((password_hash or "").encode()).hexdigest()[:16]


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ── passwords ────────────────────────────────────────────────────────────────

def check_password_rules(password: str) -> None:
    if len(password or "") < MIN_PASSWORD:
        raise AuthError(f"use at least {MIN_PASSWORD} characters")
    if password.strip() != password:
        raise AuthError("a password cannot start or end with a space")
    if len(password.encode()) > 72:
        # bcrypt silently ignores everything past 72 bytes, so two long
        # passwords sharing a prefix would both work. Refuse rather than truncate.
        raise AuthError("use at most 72 characters")


def hash_password(password: str) -> str:
    check_password_rules(password)
    return _pwd.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        # Still spend the time, so "no password set" is not distinguishable
        # from "wrong password" by how fast the answer comes back.
        _pwd.dummy_verify()
        return False
    return _pwd.verify(password, password_hash)


# ── tokens ───────────────────────────────────────────────────────────────────

def issue(investor: Investor, kind: str) -> str:
    minutes = ACCESS_MINUTES if kind == "access" else REFRESH_DAYS * 24 * 60
    now = datetime.now(timezone.utc)
    return jwt.encode({
        "sub": investor.id, "type": kind, "aud": AUDIENCE,
        "pwf": _fingerprint(investor.password_hash),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=minutes)).timestamp()),
    }, INVESTOR_JWT_SECRET, algorithm=JWT_ALGORITHM)


async def resolve(session, token: str | None, kind: str = "access") -> Investor:
    """The investor a token belongs to, or AuthError. Every check, every time."""
    if not token:
        raise AuthError("not signed in")
    try:
        claims = jwt.decode(token, INVESTOR_JWT_SECRET, algorithms=[JWT_ALGORITHM],
                            audience=AUDIENCE)
    except JWTError as exc:
        raise AuthError("your session has expired — sign in again") from exc
    if claims.get("type") != kind:
        raise AuthError("your session has expired — sign in again")
    investor = await session.get(Investor, claims.get("sub"))
    if investor is None or investor.status == "closed" or not investor.password_hash:
        raise AuthError("your session has expired — sign in again")
    if claims.get("pwf") != _fingerprint(investor.password_hash):
        # the password changed since this token was issued
        raise AuthError("your password was changed — sign in again")
    return investor


# ── sign-in, with a brake on guessing ────────────────────────────────────────

_FAIL_WINDOW = 15 * 60
_FAIL_LIMIT = 5
_failures: dict[str, deque] = defaultdict(deque)


def _recent_failures(key: str) -> deque:
    q = _failures[key]
    cutoff = time.monotonic() - _FAIL_WINDOW
    while q and q[0] < cutoff:
        q.popleft()
    return q


def reset_rate_limit() -> None:
    """For tests."""
    _failures.clear()
    _resets.clear()


async def login(session, *, email: str, password: str, ip: str | None = None) -> Investor:
    """Five wrong passwords for one address in fifteen minutes locks that
    address for the rest of the window.

    Keyed by email, not IP: the attack that matters here is guessing one
    investor's password from many addresses. In-process, so it resets on a
    restart and is per-worker; Phase 7 moves it to Redis with the other limits.
    """
    key = (email or "").strip().lower()
    if len(_recent_failures(key)) >= _FAIL_LIMIT:
        raise AuthError("too many attempts — wait 15 minutes and try again")

    investor = (await session.execute(
        select(Investor).where(Investor.email == key))).scalar_one_or_none()
    ok = verify_password(password, investor.password_hash if investor else None)
    if not ok or investor is None or investor.status == "closed":
        _recent_failures(key).append(time.monotonic())
        raise AuthError("email or password is not right")

    _failures.pop(key, None)
    if _pwd.needs_update(investor.password_hash):
        investor.password_hash = _pwd.hash(password)
    await audit(session, actor_id=investor.id, actor_kind="investor", action="auth.login",
                entity_type="investor", entity_id=investor.id, ip=ip)
    return investor


# ── invitation and reset links ───────────────────────────────────────────────

async def create_link(session, *, investor_id: str, purpose: str,
                      actor_id: str | None) -> tuple[str, datetime]:
    """A fresh single-use token. Any earlier unused one for the same purpose is
    retired, so only the newest link sent to an investor works."""
    investor = await session.get(Investor, investor_id)
    if investor is None or investor.status == "closed":
        raise AuthError("no open account for that investor")
    await session.execute(
        update(InvestorToken)
        .where(InvestorToken.investor_id == investor_id, InvestorToken.purpose == purpose,
               InvestorToken.used_at.is_(None))
        .values(used_at=_now()))
    raw = secrets.token_urlsafe(32)
    life = timedelta(hours=INVITE_HOURS) if purpose == TOKEN_INVITE else timedelta(minutes=RESET_MINUTES)
    expires = _now() + life
    session.add(InvestorToken(investor_id=investor_id, purpose=purpose, token_hash=_sha(raw),
                              expires_at=expires, created_by=actor_id))
    await audit(session, actor_id=actor_id, actor_kind="admin" if actor_id else "system",
                action=f"auth.{purpose}_link", entity_type="investor", entity_id=investor_id)
    return raw, expires


_RESET_LIMIT = 3
_RESET_WINDOW = 60 * 60
_resets: dict[str, deque] = defaultdict(deque)


async def request_reset(session, *, email: str, ip: str | None = None):
    """(investor, raw token) for an open account, else None. At most three per
    address per hour — the reset form must not be a way to spam someone."""
    key = (email or "").strip().lower()
    q = _resets[key]
    cutoff = time.monotonic() - _RESET_WINDOW
    while q and q[0] < cutoff:
        q.popleft()
    if len(q) >= _RESET_LIMIT:
        raise AuthError("too many reset requests")
    q.append(time.monotonic())
    investor = (await session.execute(
        select(Investor).where(Investor.email == key))).scalar_one_or_none()
    if investor is None or investor.status == "closed":
        return None
    raw, _ = await create_link(session, investor_id=investor.id, purpose=TOKEN_RESET, actor_id=None)
    await audit(session, actor_id=investor.id, actor_kind="investor", action="auth.reset_requested",
                entity_type="investor", entity_id=investor.id, ip=ip)
    return investor, raw


async def use_link(session, *, token: str, password: str) -> Investor:
    """Set a password from an invite or reset link. Works once."""
    check_password_rules(password)
    row = (await session.execute(
        select(InvestorToken).where(InvestorToken.token_hash == _sha(token or "")))
    ).scalar_one_or_none()
    if row is None or row.used_at is not None or row.expires_at < _now():
        raise AuthError("this link has expired or was already used — ask for a new one")
    investor = await session.get(Investor, row.investor_id)
    if investor is None or investor.status == "closed":
        raise AuthError("this link has expired or was already used — ask for a new one")
    row.used_at = _now()
    investor.password_hash = hash_password(password)
    await audit(session, actor_id=investor.id, actor_kind="investor",
                action=f"auth.password_set.{row.purpose}", entity_type="investor",
                entity_id=investor.id)
    return investor


async def change_password(session, *, investor: Investor, current: str, new: str) -> Investor:
    if not verify_password(current, investor.password_hash):
        raise AuthError("your current password is not right")
    if current == new:
        raise AuthError("choose a password different from the current one")
    investor.password_hash = hash_password(new)
    await audit(session, actor_id=investor.id, actor_kind="investor",
                action="auth.password_changed", entity_type="investor", entity_id=investor.id)
    return investor


__all__ = ["AUDIENCE", "AuthError", "TOKEN_INVITE", "TOKEN_RESET", "change_password",
           "create_link", "issue", "login", "request_reset", "resolve", "use_link"]
