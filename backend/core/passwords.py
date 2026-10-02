"""
backend/core/passwords.py

Password hashing, on `bcrypt` directly rather than through passlib.

WHY NOT PASSLIB
---------------
passlib 1.7.4 (its last release, 2020) is broken with bcrypt 5.x. It reads
`bcrypt.__about__.__version__` for backend detection; that attribute was
removed, the detection fails, and it falls through to a path bcrypt 5 rejects
outright:

    CryptContext(schemes=["bcrypt"]).hash("short-pass-123")
    ValueError: password cannot be longer than 72 bytes

That is a 14-character password. **Every** hash and verify raised, so
registration and login were broken wherever bcrypt 5 was installed — and 41
tests across the investor portal, email, notices, split, releases and admin
routes failed for this one reason.

Pinning bcrypt back would have hidden it. `bcrypt` is maintained, passlib is
not, and the hash format is identical ($2b$), so **hashes written by passlib
still verify here** and nobody has to reset a password.

THE 72-BYTE RULE
----------------
bcrypt only looks at the first 72 bytes. Truncating silently would mean two
long passwords sharing a 72-byte prefix both open the same account, so this
module REFUSES instead — the same choice investor/auth.py already made.
"""

from __future__ import annotations

import bcrypt

# bcrypt's own hard limit, not a policy choice.
MAX_PASSWORD_BYTES = 72

# A hash of a throwaway value, used to burn the same time when there is no
# stored hash, so "no password set" cannot be told from "wrong password" by how
# quickly the answer comes back.
_DUMMY_HASH = bcrypt.hashpw(b"not-a-real-password", bcrypt.gensalt())


class PasswordTooLong(ValueError):
    """Raised rather than truncating — see the module docstring."""


def hash_password(password: str) -> str:
    """A bcrypt hash, in the same $2b$ format passlib produced."""
    raw = (password or "").encode("utf-8")
    if len(raw) > MAX_PASSWORD_BYTES:
        raise PasswordTooLong(
            f"a password may be at most {MAX_PASSWORD_BYTES} bytes "
            f"({len(raw)} given)")
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str | None) -> bool:
    """Check a password, in constant-ish time whether or not a hash exists.

    Never raises on a malformed or absent hash: a corrupt row in the database
    must read as "wrong password", not as a 500 that tells an attacker the
    account exists.
    """
    raw = (password or "").encode("utf-8")
    if len(raw) > MAX_PASSWORD_BYTES:
        # Spend the time anyway, then refuse — an over-long password must not
        # answer faster than a wrong one.
        bcrypt.checkpw(raw[:MAX_PASSWORD_BYTES], _DUMMY_HASH)
        return False
    if not password_hash:
        bcrypt.checkpw(raw, _DUMMY_HASH)
        return False
    try:
        return bcrypt.checkpw(raw, password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False
