"""
backend/services/push_service.py

Web Push VAPID notification sender.
Source: Frontend_PWA_LLM_Spec.md — Push Notification System
"""

import json
from typing import Any

from sqlalchemy import select

from backend.config import settings
from backend.data.database import get_session
from backend.data.models import PushSubscription
from backend.utils.logger import get_logger

logger = get_logger(__name__)

try:
    from pywebpush import WebPushException, webpush
    HAS_WEBPUSH = True
except ImportError:
    HAS_WEBPUSH = False
    logger.warning("pywebpush not installed — push notifications disabled")


# Event urgency mapping
URGENCY_MAP = {
    "TRADE_OPENED": "high",
    "TP1_HIT": "high",
    "TP2_HIT": "high",
    "TP3_HIT": "high",
    "SL_HIT": "high",
    "BREAKEVEN_TRIGGERED": "normal",
    "CIRCUIT_BREAKER": "high",
    "CONSECUTIVE_LOSSES": "high",
    "NEW_SIGNAL": "normal",
    "BACKEND_RECONNECTED": "normal",
    "LLM_ANALYSIS_READY": "normal",
    "DAILY_SUMMARY": "low",
    "STEP_ADVANCED": "normal",
    "STEP_REDUCED": "high",
}

# Events that stay on screen until dismissed
REQUIRE_INTERACTION = {"SL_HIT", "CIRCUIT_BREAKER", "CONSECUTIVE_LOSSES"}


async def send_push_notification(
    user_id: str,
    event_type: str,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
):
    """
    Send a push notification to all subscriptions for a user.
    Source: TradingBot_MasterPlan-2.md Section 8.4
    """
    if not HAS_WEBPUSH:
        logger.debug(f"Push skipped (no pywebpush): {title}")
        return

    vapid_private = settings.vapid.private_key
    vapid_claims = {"sub": settings.vapid.claims_email}

    if not vapid_private:
        logger.warning("VAPID private key not configured — push disabled")
        return

    # Get all subscriptions for this user
    async with get_session() as session:
        result = await session.execute(
            select(PushSubscription).where(PushSubscription.user_id == user_id)
        )
        subscriptions = result.scalars().all()

    if not subscriptions:
        return

    payload = json.dumps({
        "title": title,
        "body": body,
        "event_type": event_type,
        "urgency": URGENCY_MAP.get(event_type, "normal"),
        "requireInteraction": event_type in REQUIRE_INTERACTION,
        "data": data or {},
    })

    for sub in subscriptions:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
                },
                data=payload,
                vapid_private_key=vapid_private,
                vapid_claims=vapid_claims,
            )
            logger.debug(f"Push sent to user {user_id}: {title}")
        except WebPushException as e:
            logger.warning(f"Push failed for {sub.endpoint}: {e}")
            # Remove stale subscriptions (410 Gone)
            if "410" in str(e) or "404" in str(e):
                await _remove_subscription(sub.id)
        except Exception as e:
            logger.error(f"Push error: {e}")


async def _remove_subscription(subscription_id: str):
    """Remove a stale push subscription."""
    async with get_session() as session:
        result = await session.execute(
            select(PushSubscription).where(PushSubscription.id == subscription_id)
        )
        sub = result.scalar_one_or_none()
        if sub:
            await session.delete(sub)
            logger.info(f"Removed stale push subscription: {subscription_id}")
