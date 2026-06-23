"""
backend/api/routes/config.py

User config + risk + compounding settings endpoints.
Source: TradingBot_MasterPlan-2.md Section 6
"""

import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional, Dict, Any

from backend.data.database import get_db
from backend.data.models import UserConfigModel, User
from backend.api.deps import get_current_user
from backend.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["config"])


class UpdateConfigRequest(BaseModel):
    config: Dict[str, Any]
    preset_name: Optional[str] = None


@router.get("/config")
async def get_user_config(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get user strategy + risk configuration."""
    result = await db.execute(
        select(UserConfigModel).where(UserConfigModel.user_id == current_user.id)
    )
    config = result.scalar_one_or_none()
    if not config:
        return {"config": {}, "preset_name": None}
    return {"config": json.loads(config.config_json), "preset_name": config.preset_name}


@router.put("/config")
async def update_user_config(
    req: UpdateConfigRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update user strategy config. Takes effect on next signal evaluation."""
    result = await db.execute(
        select(UserConfigModel).where(UserConfigModel.user_id == current_user.id)
    )
    config = result.scalar_one_or_none()

    if config:
        config.config_json = json.dumps(req.config)
        config.preset_name = req.preset_name
    else:
        config = UserConfigModel(
            user_id=current_user.id,
            config_json=json.dumps(req.config),
            preset_name=req.preset_name,
        )
        db.add(config)

    return {"updated": True}
