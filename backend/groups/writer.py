"""
backend/groups/writer.py

Engagement posts and admin video scripts, written by an LLM from facts only.

Provider: Claude through backend/services/llm_service.py (ANTHROPIC_API_KEY),
or Grok through xAI's OpenAI-compatible API (XAI_API_KEY). With neither, or
when the model's text trips the claims filter, a plain template is used - a
post never depends on the LLM being up.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

from backend.groups import templates as T
from backend.utils.logger import get_logger

logger = get_logger(__name__)

BANNED = re.compile(
    r"guarantee|risk[- ]?free|sure[- ]?(win|shot|profit)|100\s*%\s*(win|accura|profit)|no[- ]?loss|"
    r"double your|get rich|passive income|can'?t lose|never lose|easy money|financial freedom",
    re.I)
SOURCE_WORDS = re.compile(r"\b(strategy|bot|algo\w*|copy[- ]?trad\w*|channel|telegram group|signal provider|"
                          r"AlgoEdge|VWAP_v1|QL_|RQ_|Sigma|ORB)\b", re.I)


def clean(text: str) -> str | None:
    """The model's text, or None when it breaks a rule (then the template is used)."""
    t = (text or "").strip().strip('"').strip()
    if not t or len(t) > 1200:
        return None
    if BANNED.search(t) or SOURCE_WORDS.search(t):
        return None
    return T.e(t)


async def _anthropic(system: str, prompt: str, model: str) -> str:
    from backend.services.llm_service import FAST_MODELS, LLMService
    svc = LLMService(caller="groups")
    if not svc.resolve_key("anthropic"):
        raise RuntimeError("no Anthropic key")
    return await svc._call_provider("anthropic", model or FAST_MODELS["anthropic"], prompt, system=system,
                                    max_tokens=400, strict=True)


async def _xai(system: str, prompt: str, model: str) -> str:
    key = os.getenv("XAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("no xAI key")
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post("https://api.x.ai/v1/chat/completions", headers={"Authorization": f"Bearer {key}"},
                         json={"model": model or os.getenv("XAI_MODEL", "grok-3-mini"), "max_tokens": 400,
                               "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


async def write(settings: dict, task: str, facts: dict[str, Any]) -> tuple[str | None, str]:
    """(clean text or None, which provider answered or why not)."""
    provider = (settings.get("llm_provider") or "anthropic").lower()
    if provider == "none":
        return None, "llm off"
    system = settings.get("persona") or ""
    prompt = f"Task: {task}\n\nFacts (the only facts you may use):\n{json.dumps(facts, default=str, indent=1)}"
    order = [provider] + [p for p in ("anthropic", "xai") if p != provider]
    why = []
    for p in order:
        try:
            raw = await (_anthropic if p == "anthropic" else _xai)(system, prompt, settings.get("llm_model") or "")
        except Exception as e:  # noqa: BLE001
            why.append(f"{p}: {e}")
            continue
        out = clean(raw)
        if out:
            return out, p
        why.append(f"{p}: text failed the rules filter")
    return None, "; ".join(why)[:300]


# ── fallbacks (no LLM) ────────────────────────────────────────────────────────

def fallback(kind: str, facts: dict[str, Any]) -> str:
    if kind == "morning":
        ev = facts.get("events") or []
        lines = ["☀️ <b>Good morning, traders.</b>"]
        if ev:
            lines.append("On the calendar today: " + ", ".join(T.e(x) for x in ev[:4]) + ".")
        lines.append("Plan the trade, size it right, and let the stop do its job.")
        return "\n".join(lines)
    if kind == "midday":
        n = facts.get("open_signals", 0)
        return f"🕐 Midday check: {n} signal{'s' if n != 1 else ''} running. Stops in place, no chasing." if n \
            else "🕐 Midday check: nothing running right now. Waiting for clean setups."
    if kind == "education":
        return ("📚 <b>Quick lesson: the buy limit</b>\nA buy limit waits below the market and fills only if price "
                "comes down to it. When a heads-up gives an entry below the current price, a buy limit means you don't "
                "have to watch the chart.")
    return ""


def script_fallback(moment: str, facts: dict[str, Any]) -> str:
    """A plain video outline for the admin when no LLM is available."""
    lines = [f"🎬 Video idea: {moment}", "Talking points:"]
    for k, v in facts.items():
        lines.append(f"• {k.replace('_', ' ')}: {v}")
    lines.append("Close: invite viewers to the free group and explain how VIP works.")
    return T.e("\n".join(lines))
