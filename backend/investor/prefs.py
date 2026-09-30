"""
backend/investor/prefs.py

An investor's own settings: how the dashboard looks and which notifications
they get. Stored as JSON on the investor row so a new setting needs no
migration; unknown keys are refused, so the store cannot fill with junk.

Transactional emails (money received, withdrawal decided, password changed,
closure) always go: they are the record of what happened to their money. What
can be switched off is the monthly statement, trade and fee EMAILS (all of it
stays in the dashboard and the bell) and each kind of phone/browser push.
"""

from __future__ import annotations

import json

DEFAULTS: dict = {
    "hide_balances": False,          # blur amounts until tapped (privacy in public)
    "chart_range": "ALL",            # default range on the balance chart
    "compact_numbers": False,        # $12.5k instead of $12,480.00 in charts and tiles
    "email": {"statements": True, "trades": True, "live_trades": True, "fees": True},
    "push": {"money": True, "withdrawals": True, "statements": True, "trades": True,
             "live_trades": True, "fees": True},
}
CHART_RANGES = ("1M", "3M", "6M", "1Y", "ALL")

# outbox message kind -> the push setting that controls it
# (kinds not listed, such as a password or payout-account change, always push:
# they are about the account's safety)
PUSH_CATEGORY = {
    "deposit_claimed": "money", "deposit_confirmed": "money", "deposit_rejected": "money",
    "withdrawal_received": "withdrawals", "withdrawal_approved": "withdrawals",
    "withdrawal_paid": "withdrawals", "withdrawal_declined": "withdrawals",
    "statement": "statements",
    "trade_published": "trades",
    "trade_opened": "live_trades",
    "fee_charged": "fees",
}
# outbox message kind -> the email setting that controls it (others always send)
EMAIL_CATEGORY = {"statement": "statements", "trade_published": "trades",
                  "trade_opened": "live_trades", "fee_charged": "fees"}


def load(raw: str | None) -> dict:
    """Stored preferences merged over the defaults."""
    try:
        saved = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        saved = {}
    out = json.loads(json.dumps(DEFAULTS))
    for k, v in (saved or {}).items():
        if k in ("email", "push") and isinstance(v, dict):
            out[k].update({kk: bool(vv) for kk, vv in v.items() if kk in DEFAULTS[k]})
        elif k in DEFAULTS:
            out[k] = v
    return out


def merge(raw: str | None, changes: dict) -> str:
    """Apply `changes` (a partial preferences dict), validate, and return JSON."""
    current = load(raw)
    for k, v in (changes or {}).items():
        if k not in DEFAULTS:
            raise ValueError(f"unknown setting {k}")
        if k in ("email", "push"):
            if not isinstance(v, dict):
                raise ValueError(f"{k} must be an object")
            for kk, vv in v.items():
                if kk not in DEFAULTS[k]:
                    raise ValueError(f"unknown {k} setting {kk}")
                current[k][kk] = bool(vv)
        elif k == "chart_range":
            if v not in CHART_RANGES:
                raise ValueError(f"chart_range must be one of {', '.join(CHART_RANGES)}")
            current[k] = v
        else:
            current[k] = bool(v)
    return json.dumps(current, sort_keys=True)


def wants_push(raw: str | None, kind: str) -> bool:
    cat = PUSH_CATEGORY.get(kind)
    return True if cat is None else bool(load(raw)["push"].get(cat, True))


def wants_email(raw: str | None, kind: str) -> bool:
    cat = EMAIL_CATEGORY.get(kind)
    return True if cat is None else bool(load(raw)["email"].get(cat, True))
