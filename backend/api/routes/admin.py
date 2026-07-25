"""
backend/api/routes/admin.py

Admin operations (requires auth). User management CRUD.
Source: TradingBot_MasterPlan-2.md Section 12
"""


from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user
from backend.data.database import get_db
from backend.data.models import User
from backend.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])


class UpdateUserRequest(BaseModel):
    name: str | None = None
    mt5_account: int | None = None
    mt5_server: str | None = None
    deriv_mt5_account: int | None = None
    deriv_mt5_server: str | None = None
    risk_per_trade: float | None = None
    max_daily_loss: float | None = None
    is_active: bool | None = None


@router.get("/users")
async def list_users(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all users (admin-only)."""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")
    result = await db.execute(select(User))
    users = result.scalars().all()
    return [{
        "id": u.id,
        "email": u.email,
        "name": u.name,
        "mt5_account": u.mt5_account,
        "deriv_mt5_account": u.deriv_mt5_account,
        "active_strategy": u.active_strategy,
        "is_active": u.is_active,
        "risk_per_trade": u.risk_per_trade,
    } for u in users]


@router.get("/users/{user_id}")
async def get_user(
    user_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific user."""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "mt5_account": user.mt5_account,
        "mt5_server": user.mt5_server,
        "deriv_mt5_account": user.deriv_mt5_account,
        "deriv_mt5_server": user.deriv_mt5_server,
        "active_strategy": user.active_strategy,
        "risk_per_trade": user.risk_per_trade,
        "max_daily_loss": user.max_daily_loss,
        "is_active": user.is_active,
    }


@router.put("/users/{user_id}")
async def update_user(
    user_id: str,
    req: UpdateUserRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update user settings."""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    for field, value in req.model_dump(exclude_unset=True).items():
        setattr(user, field, value)

    await db.commit()
    return {"id": user.id, "updated": True}


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a user."""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    await db.delete(user)
    await db.commit()
    return {"deleted": True}
