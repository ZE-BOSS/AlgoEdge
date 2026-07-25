"""
backend/api/routes/llm.py

LLM analysis endpoints.
Source: TradingBot_MasterPlan-2.md Section 9
"""


from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user
from backend.data.database import get_db
from backend.data.models import LLMAnalysis, Trade, User
from backend.services.llm_service import LLMService
from backend.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["llm"])


class AnalyzeTradeRequest(BaseModel):
    trade_id: int
    provider: str = "anthropic"
    model: str | None = None


class CustomQuestionRequest(BaseModel):
    question: str
    context_data: dict | None = None
    provider: str = "anthropic"


@router.post("/llm/analyze-trade")
async def analyze_trade(
    req: AnalyzeTradeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Analyze a single trade with AI."""
    result = await db.execute(
        select(Trade).where(Trade.id == req.trade_id, Trade.user_id == current_user.id)
    )
    trade = result.scalar_one_or_none()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")

    service = LLMService()
    analysis = await service.analyze_trade(trade, req.provider, req.model)

    return {"analysis": analysis}


@router.post("/llm/custom")
async def custom_question(
    req: CustomQuestionRequest,
    current_user: User = Depends(get_current_user),
):
    """Ask a custom question about trading data."""
    service = LLMService()
    result = await service.custom_question(req.question, req.context_data, req.provider)
    return {"answer": result}


@router.get("/analyses")
async def get_analyses(
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get recent LLM analyses for the authenticated user."""
    result = await db.execute(
        select(LLMAnalysis)
        .where(LLMAnalysis.user_id == current_user.id)
        .order_by(LLMAnalysis.created_at.desc())
        .limit(limit)
    )
    analyses = result.scalars().all()
    return [{
        "id": a.id,
        "context_type": a.context_type,
        "provider": a.provider,
        "model": a.model,
        "analysis_text": a.analysis_text,
        "created_at": a.created_at,
    } for a in analyses]
