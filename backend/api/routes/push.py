"""
backend/api/routes/push.py

Push subscription management endpoints.
Source: Frontend_PWA_LLM_Spec.md — Push Notification System
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user
from backend.data.database import get_db
from backend.data.models import PushSubscription, User
from backend.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["push"])


class PushSubscribeRequest(BaseModel):
    endpoint: str
    p256dh: str
    auth: str


@router.post("/push/subscribe")
async def subscribe(
    req: PushSubscribeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Register a push notification subscription."""
    # Check if endpoint already exists
    result = await db.execute(
        select(PushSubscription).where(PushSubscription.endpoint == req.endpoint)
    )
    existing = result.scalar_one_or_none()
    if existing:
        return {"id": existing.id, "status": "already_subscribed"}

    sub = PushSubscription(
        id=str(uuid.uuid4()),
        user_id=current_user.id,
        endpoint=req.endpoint,
        p256dh=req.p256dh,
        auth=req.auth,
    )
    db.add(sub)
    await db.commit()
    logger.info(f"Push subscription added for user {current_user.id}")
    return {"id": sub.id, "status": "subscribed"}


@router.delete("/push/unsubscribe/{subscription_id}")
async def unsubscribe(
    subscription_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove a push notification subscription."""
    result = await db.execute(
        select(PushSubscription).where(
            PushSubscription.id == subscription_id,
            PushSubscription.user_id == current_user.id,
        )
    )
    sub = result.scalar_one_or_none()
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    await db.delete(sub)
    await db.commit()
    return {"status": "unsubscribed"}
