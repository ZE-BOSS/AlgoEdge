"""
backend/api/routes/config.py

User config + risk + compounding settings endpoints.
Source: TradingBot_MasterPlan-2.md Section 6
"""

import json
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user
from backend.data.database import get_db
from backend.data.models import User, UserConfigModel
from backend.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["config"])


class UpdateConfigRequest(BaseModel):
    config: dict[str, Any]
    preset_name: str | None = None


@router.get("/config")
async def get_user_config(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get user strategy + risk configuration."""
    logger.info(f"Loading config for user {current_user.email}")
    result = await db.execute(
        select(UserConfigModel).where(UserConfigModel.user_id == current_user.id)
    )
    config = result.scalar_one_or_none()
    if not config:
        logger.info(f"No saved config for {current_user.email} — returning defaults")
        return {"config": {}, "preset_name": None}
    logger.info(f"Config loaded for {current_user.email}: preset={config.preset_name}")
    return {"config": json.loads(config.config_json), "preset_name": config.preset_name}


@router.put("/config")
async def update_user_config(
    req: UpdateConfigRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update user strategy config. Takes effect on next signal evaluation."""
    from backend.services.bot_service import bot_service
    result = await db.execute(
        select(UserConfigModel).where(UserConfigModel.user_id == current_user.id)
    )
    config = result.scalar_one_or_none()

    if config:
        try:
            existing_config = json.loads(config.config_json) if config.config_json else {}
        except Exception:
            existing_config = {}
            
        def deep_update(d, u):
            for k, v in u.items():
                if isinstance(v, dict) and k in d and isinstance(d[k], dict):
                    deep_update(d[k], v)
                else:
                    d[k] = v
            return d

        merged_config = deep_update(existing_config, req.config)
        config.config_json = json.dumps(merged_config)
        config.preset_name = req.preset_name
        logger.info(f"Config updated for {current_user.email}: {list(req.config.keys())}")
    else:
        config = UserConfigModel(
            user_id=current_user.id,
            config_json=json.dumps(req.config),
            preset_name=req.preset_name,
        )
        db.add(config)
        logger.info(f"Config created for {current_user.email}: {list(req.config.keys())}")

    bot_service.log_system_event(f"Config saved: {', '.join(list(req.config.keys())[:5])}", category="CONFIG")
    return {"updated": True}
