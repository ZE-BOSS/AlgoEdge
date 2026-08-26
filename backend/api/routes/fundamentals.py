"""
backend/api/routes/fundamentals.py

[Phase 13 Part E / D.2] Fundamentals data surface.

One endpoint shape per capability, all served through `data/providers.py`'s
registry so the vendor behind any of them is a settings change rather than a
code change. Every response carries the provider that served it, the latency,
and — where the data is delayed or derived — the caveat, so the UI can show what
a number actually is instead of implying it is live and direct.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.api.deps import get_current_user
from backend.data.providers import (
    ALL_CAPABILITIES,
    CAP_CALENDAR,
    CAP_CORRELATION,
    CAP_GEX,
    CAP_OPTIONS_CHAIN,
    CAP_ORDER_BOOK,
    CAP_ORDER_FLOW,
    registry,
)
from backend.data.models import User
from backend.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/fundamentals", tags=["fundamentals"])


class SelectProviderRequest(BaseModel):
    capability: str
    provider: str


@router.get("/providers")
async def get_providers(current_user: User = Depends(get_current_user)):
    """
    The provider catalogue: what serves each capability, which is selected, and
    each one's health.

    `configured` is reported separately from `available`, because the common
    failure for a paid provider is an installed SDK with no key — which
    otherwise only shows up as an authentication error at fetch time.
    """
    return {
        "catalogue": registry.catalogue(),
        "capabilities": ALL_CAPABILITIES,
        "selection": registry.selection(),
    }


@router.post("/providers/select")
async def select_provider(
    req: SelectProviderRequest,
    current_user: User = Depends(get_current_user),
):
    """Point a capability at a different provider. Free now, paid later, no deploy."""
    try:
        registry.select(req.capability, req.provider)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"selection": registry.selection()}


@router.get("/orderflow")
async def get_orderflow(
    symbol: str,
    minutes: int = Query(60, ge=1, le=1440),
    timeframe: str = "M5",
    bubbles: bool = True,
    current_user: User = Depends(get_current_user),
):
    """
    CVD, delta, volume profile, divergence/absorption, and the bubble overlay.

    `bubbles` are signed volume aggregated per price bucket — what the chart
    draws as circles. Aggregated rather than per-tick because a busy hour is
    hundreds of thousands of ticks, and neither the renderer nor the reader
    gains anything from seeing them individually.
    """
    result = await registry.fetch(
        CAP_ORDER_FLOW, symbol=symbol, minutes=minutes,
        timeframe=timeframe, bubbles=bubbles,
    )
    return result.to_dict()


@router.get("/orderbook")
async def get_orderbook(
    symbol: str,
    current_user: User = Depends(get_current_user),
):
    """Level-2 depth and resting-size imbalance."""
    result = await registry.fetch(CAP_ORDER_BOOK, symbol=symbol)
    return result.to_dict()


@router.get("/correlation")
async def get_correlation(
    symbols: str = Query(..., description="Comma-separated symbols"),
    timeframe: str = "H1",
    count: int = Query(500, ge=50, le=5000),
    current_user: User = Depends(get_current_user),
):
    """
    Correlation across instruments, computed on RETURNS rather than prices —
    two upward-drifting price series correlate near 1.0 whether or not they
    actually co-move, which would make the whole matrix useless.
    """
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if len(syms) < 2:
        raise HTTPException(status_code=400, detail="At least two symbols are required")
    result = await registry.fetch(
        CAP_CORRELATION, symbols=syms, timeframe=timeframe, count=count,
    )
    return result.to_dict()


@router.get("/options")
async def get_options_chain(
    ticker: str,
    expiry: str | None = None,
    current_user: User = Depends(get_current_user),
):
    """Options chain with open interest and (where the source publishes them) greeks."""
    result = await registry.fetch(CAP_OPTIONS_CHAIN, ticker=ticker.upper(), expiry=expiry)
    return result.to_dict()


@router.get("/gex")
async def get_gex(
    ticker: str,
    expiry: str | None = None,
    current_user: User = Depends(get_current_user),
):
    """
    Dealer gamma exposure by strike, plus the flip point.

    Computed here from the chain rather than bought: every GEX vendor is doing
    this same arithmetic over the same public chain, so the thing worth paying
    for is the chain, not the model.
    """
    result = await registry.fetch(CAP_GEX, ticker=ticker.upper(), expiry=expiry)
    return result.to_dict()


@router.get("/calendar")
async def get_calendar(
    impact: str | None = None,
    current_user: User = Depends(get_current_user),
):
    """Economic calendar — the same feed the live bot's news filter uses."""
    result = await registry.fetch(CAP_CALENDAR, impact=impact)
    return result.to_dict()


@router.get("/health")
async def get_health(current_user: User = Depends(get_current_user)):
    """
    Per-provider latency and error rate.

    Exists so choosing free over paid is an informed decision rather than a
    hopeful one: free tiers fail differently, and the registry's job is to make
    that visible rather than silent.
    """
    return {"providers": registry.health(), "selection": registry.selection()}
