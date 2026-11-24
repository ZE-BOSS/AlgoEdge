"""
backend/groups/settings.py

The admin-editable settings of the group business, stored as one JSON row.
Missing keys always fall back to DEFAULTS, so a new setting needs no migration.
"""
from __future__ import annotations

import copy
import os
from typing import Any

from backend.groups.models import GroupSettings

PERSONA = """You write short Telegram posts for a trading signal community run by Alpha Vantiq Capital.
Voice: a calm, experienced desk trader. Short sentences, plain English, light Nigerian warmth. At most one emoji.
Rules you never break:
- Use only the facts given to you. Never invent a price, a result, a percentage, a member or a story.
- Never promise profit, guaranteed returns, "risk-free" trading or income. Never say "sure win".
- Never give personal financial advice (how much to invest, which broker to fund). Point to the pinned risk guide or the admin.
- Never name where signals come from (no strategy names, no other channels, no software names).
- Losses are reported as plainly as wins.
- Keep it under 80 words unless asked otherwise. No hashtags. Write the post only, no preamble."""

FAQ = [
    {"q": "join vip", "a": "Tap /start in a private chat with me and choose Join VIP. You need an MT5 account opened with our partner link and funded with the minimum balance."},
    {"q": "lot size", "a": "Lot size = (balance × risk %) ÷ (stop distance × value per point). Risk 1-2% per signal. The pinned message has the details."},
    {"q": "what is r", "a": "R is the risk on a trade. +2R means the trade made twice what it risked; -1R means it lost the planned risk."},
    {"q": "buy limit", "a": "A buy limit waits below the current price and fills only if price drops to it. Use it when a heads-up gives an entry below the market."},
    {"q": "broker", "a": "Our partner broker link is in the VIP join flow: tap /start in a private chat with me."},
]

DEFAULTS: dict[str, Any] = {
    "brand": "Alpha Vantiq Capital",
    "timezone": "Africa/Lagos",
    "persona": PERSONA,
    "faq": FAQ,
    "llm_provider": "anthropic",          # anthropic / xai / none
    "llm_model": "",                      # "" = the service's fast model
    "approval": "drafts",                 # drafts (admin approves every LLM post) / auto
    "auto_kinds": ["morning", "education"],
    "schedule": {                          # local times, weekdays unless noted
        "morning": "07:30", "midday": "13:00", "summary": "21:15", "weekly": "Fri 21:30", "education": "Sat 11:00",
    },
    "moderate": True,                      # delete links / phone numbers / "DM me" from non-admins
    "answer_faq": True,
    "vip_min_balance": 300.0,
    "vip_check": "admin",                  # admin (approve in Telegram) / exness (partner API, when configured)
    "affiliate_links": {},                 # broker -> link
    "vip_months": 1,
    "vvip_levels": {"STARTER": 300.0, "PRO": 500.0, "ELITE": 1000.0},
    "vvip_period_days": 30,
    "admin_ids": [],                       # Telegram user ids allowed to command the bot
    "content_alerts": True,
    "admin_contact": "",                   # @username members are sent to for money / account questions
    "portal_url": "https://app.alphavantiqcapital.com/bot",
}


async def load(s) -> dict[str, Any]:
    row = await s.get(GroupSettings, 1)
    data = copy.deepcopy(DEFAULTS)
    if row is not None and isinstance(row.data, dict):
        data.update(row.data)
    env_admins = [int(x) for x in os.getenv("GROUP_ADMIN_IDS", "").replace(" ", "").split(",") if x.strip().lstrip("-").isdigit()]
    data["admin_ids"] = sorted(set(int(x) for x in (data.get("admin_ids") or [])) | set(env_admins))
    return data


async def save(s, patch: dict[str, Any]) -> dict[str, Any]:
    row = await s.get(GroupSettings, 1)
    if row is None:
        row = GroupSettings(id=1, data={})
        s.add(row)
    cur = dict(row.data or {})
    for k, v in patch.items():
        if k in DEFAULTS:
            cur[k] = v
    row.data = cur
    await s.flush()
    return await load(s)
