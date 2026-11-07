"""
backend/copytrade/crypto.py

Encrypts the Telegram api_hash and session string at rest. A Telegram session
string is a full login to the user's Telegram account, so it is never stored or
returned in clear.

Uses ENCRYPTION_KEY (the key api_key_store uses) when set; otherwise a key
derived from JWT_SECRET_KEY, so the feature works on servers that never set
ENCRYPTION_KEY - rotating either secret logs Telegram out, which is the safe
failure.
"""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken

from backend.config import settings


def _cipher() -> Fernet:
    key = settings.security.encryption_key
    if key:
        try:
            return Fernet(key.encode() if isinstance(key, str) else key)
        except Exception:
            pass
    secret = os.getenv("JWT_SECRET_KEY", "algoedge-dev-secret-change-in-production-min-32-chars")
    derived = base64.urlsafe_b64encode(hashlib.sha256(("copytrade:" + secret).encode()).digest())
    return Fernet(derived)


def encrypt(text: str | None) -> str | None:
    if not text:
        return None
    return _cipher().encrypt(text.encode()).decode()


def decrypt(token: str | None) -> str | None:
    if not token:
        return None
    try:
        return _cipher().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return None
