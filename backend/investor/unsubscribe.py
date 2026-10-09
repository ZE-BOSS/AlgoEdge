"""
backend/investor/unsubscribe.py

One click, no sign-in, no database row.

WHY STATELESS
-------------
An unsubscribe link has to work from an email that may be opened months later,
possibly by a mail provider rather than a person: RFC 8058 one-click means
Gmail or Yahoo issues a POST to the link on the reader's behalf, with no
cookies and no session. A link backed by a stored token would need that row to
live forever, and a link that has expired is an unsubscribe that failed.

So the token is a signature over (investor_id, setting), not a lookup. It
cannot be guessed without the key, it names exactly one setting, and it carries
no expiry because the right to unsubscribe does not expire.

WHY IT IS NOT THE INVESTOR'S JWT
--------------------------------
A session token in an email is a session token in everyone's inbox, mail
archive and provider logs. This token can do one thing — turn one email
category off for one investor — and cannot be exchanged for anything else. It
is signed with its own derived key, so a leaked unsubscribe link does not
authenticate anybody.

WHAT IT MAY SWITCH OFF
----------------------
Only the categories `prefs.EMAIL_CATEGORY` already treats as optional, and the
marketing category. Transactional mail — money received, a withdrawal decided,
a password changed — is the record of what happened to someone's money, and
`outbox` never attaches an unsubscribe link to it. Asking this module to switch
one off is refused rather than honoured.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

from backend.api.deps import JWT_SECRET_KEY
from backend.investor import prefs

# Its own key, derived rather than shared: a leak here must not authenticate.
_KEY = (os.getenv("UNSUBSCRIBE_SECRET") or hashlib.sha256(
    f"{JWT_SECRET_KEY}|unsubscribe".encode()).hexdigest()).encode()

# What a link is allowed to turn off. "marketing" is the blanket opt-out a
# commercial message must offer; the rest mirror prefs.EMAIL_CATEGORY.
SETTINGS = ("marketing", *sorted(set(prefs.EMAIL_CATEGORY.values())))


class BadToken(Exception):
    """The token is malformed, or was not signed by us."""


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    try:
        return base64.urlsafe_b64decode(text + pad)
    except Exception as exc:                       # noqa: BLE001 — any decode failure is one answer
        raise BadToken("malformed link") from exc


def _sign(payload: bytes) -> str:
    return _b64(hmac.new(_KEY, payload, hashlib.sha256).digest()[:16])


def make_token(investor_id: str, setting: str = "marketing") -> str:
    """A link token for one investor and one setting."""
    if setting not in SETTINGS:
        raise ValueError(f"{setting!r} is not an unsubscribable setting")
    payload = f"{investor_id}|{setting}".encode()
    return f"{_b64(payload)}.{_sign(payload)}"


def read_token(token: str) -> tuple[str, str]:
    """(investor_id, setting), or raise `BadToken`."""
    if not token or token.count(".") != 1:
        raise BadToken("malformed link")
    body, sig = token.split(".", 1)
    payload = _unb64(body)
    # compare_digest, not ==: a timing comparison on a signature is a way to
    # forge one, one byte at a time.
    if not hmac.compare_digest(sig, _sign(payload)):
        raise BadToken("this link was not issued by us")
    text = payload.decode("utf-8", "replace")
    if text.count("|") != 1:
        raise BadToken("malformed link")
    investor_id, setting = text.split("|", 1)
    if setting not in SETTINGS:
        raise BadToken("this link refers to a setting that no longer exists")
    return investor_id, setting


def link_for(investor_id: str, setting: str = "marketing", *, base: str | None = None) -> str:
    """The absolute url to put in an email."""
    root = (base or os.getenv("PUBLIC_SITE_URL") or os.getenv("INVESTOR_APP_URL")
            or "http://localhost:5174").rstrip("/")
    return f"{root}/api/public/unsubscribe?t={make_token(investor_id, setting)}"


def apply_to(raw_preferences: str | None, setting: str) -> str:
    """The investor's preferences JSON with `setting` switched off.

    `marketing` turns off every optional email category, because someone
    clicking "unsubscribe" on a promotional email means all of it, not just the
    one they happened to receive.
    """
    if setting == "marketing":
        changes = {"email": {k: False for k in prefs.DEFAULTS["email"]}}
    else:
        changes = {"email": {setting: False}}
    return prefs.merge(raw_preferences, changes)
