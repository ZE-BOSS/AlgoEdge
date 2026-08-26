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


@router.get("/config/parameter_schema")
async def get_parameter_schema():
    """
    [7.1/H2] Machine-readable parameter schema generated from RiskParams,
    PropFirmParams, and every strategy's Params dataclass — `{key, group,
    label, type, unit, help, default, affects}` per field, with `help`
    extracted from each field's existing attribute-docstring. No auth
    required — this describes the config SHAPE, not any user's data.
    """
    from backend.core.schema_introspection import build_full_schema
    return {"fields": build_full_schema()}


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

    # [12.9/Part14] Non-blocking cross-validation — same pattern as the
    # existing risk_per_trade_pct-vs-max_risk_hard_cap_pct frontend warning:
    # informational, never rejects the save.
    validation_warnings: list[str] = []
    try:
        from backend.core.config_schema import UserConfigV2
        parsed = UserConfigV2.from_dict(json.loads(config.config_json))
        validation_warnings = parsed.validate_slot_position_caps()
    except Exception as e:
        logger.warning(f"[12.9] Slot cap cross-validation skipped (config didn't parse cleanly): {e}")

    return {"updated": True, "validation_warnings": validation_warnings}
