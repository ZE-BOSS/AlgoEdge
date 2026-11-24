"""Admin API: the order-flow levels (backend/orderflow)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.api.deps import require_admin
from backend.data.models import User
from backend.orderflow.feed import orderflow_feed, read_snapshot

router = APIRouter(prefix="/api/orderflow", tags=["orderflow"])


@router.get("/levels")
async def levels(_: User = Depends(require_admin)):
    snap = read_snapshot()
    return {"enabled": orderflow_feed.enabled, "running": orderflow_feed.thread is not None, **snap}
