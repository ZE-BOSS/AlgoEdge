"""
backend/api/routes/compounding.py

Compounding state, step history, and projection endpoints.
Source: CompoundingPlan_Spec.md
"""

from fastapi import APIRouter, Depends
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user
from backend.data.database import get_db
from backend.data.models import CompoundingEvent, CompoundingStateModel, User
from backend.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["compounding"])


@router.get("/compounding")
async def get_compounding_state(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get current compounding state for the authenticated user."""
    result = await db.execute(
        select(CompoundingStateModel).where(CompoundingStateModel.user_id == current_user.id)
    )
    state = result.scalar_one_or_none()
    if not state:
        return {"enabled": False, "current_step": 1, "risk_amount": 20.0}

    return {
        "enabled": True,
        "current_step": state.current_step,
        "risk_amount": state.risk_amount,
        "entry_balance": state.entry_balance,
        "consecutive_wins": state.consecutive_wins,
        "consecutive_losses": state.consecutive_losses,
        "total_wins_at_level": state.total_wins_at_level,
        "last_step_change_reason": state.last_step_change_reason,
    }


@router.get("/compounding/events")
async def get_compounding_events(
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get compounding step transition history."""
    result = await db.execute(
        select(CompoundingEvent)
        .where(CompoundingEvent.user_id == current_user.id)
        .order_by(desc(CompoundingEvent.created_at))
        .limit(limit)
    )
    events = result.scalars().all()
    return [{
        "event_type": e.event_type,
        "from_step": e.from_step,
        "to_step": e.to_step,
        "from_risk": e.from_risk,
        "to_risk": e.to_risk,
        "balance_at_event": e.balance_at_event,
        "created_at": e.created_at,
    } for e in events]
