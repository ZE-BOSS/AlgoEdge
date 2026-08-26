"""
backend/api/routes/analysis.py

[Phase 13 §F] Claude-powered analysis of the platform's own data.

The point: analysing a backtest, a set of live trades, a signal funnel, or a log
slice should not require copying JSON out of the platform and into a chat
window. These endpoints assemble the context server-side, call the model, and
persist the answer so it is still there tomorrow.

Key handling: the Anthropic key is read from the backend environment and never
leaves the server. The browser calls this API; this API calls Anthropic.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user
from backend.data.database import get_db
from backend.data.models import BacktestRun, LLMAnalysis, Signal, Trade, User
from backend.services.analysis_context import (
    SYSTEM_PROMPT,
    build_backtest_context,
    build_logs_context,
    build_orderflow_context,
    build_prompt,
    build_signals_context,
    build_strategy_config_context,
    build_trade_chart_context,
    build_trades_context,
)
from backend.services.llm_service import (
    DEFAULT_EFFORT,
    DEFAULT_MODELS,
    EFFORT_LEVELS,
    FAST_MODELS,
    LLMService,
    resolve_max_tokens,
)
from backend.services.log_stream import log_hub
from backend.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/analysis", tags=["analysis"])

VALID_TARGETS = {
    "backtest", "portfolio", "trades", "signals", "logs",
    # [Phase 13] Claude reaches every surface, not just run results.
    "trade",            # one trade + the markings its strategy emitted
    "strategy_config",  # live parameters, optionally against the strategy's spec
    "orderflow",        # CVD / OFI for a symbol
    "fundamentals",     # provider data (options, GEX, correlation)
}


class AnalysisRequest(BaseModel):
    target_type: str                    # see VALID_TARGETS
    target_id: str | None = None        # backtest id, log session id, group_id, ...
    question: str | None = None
    provider: str = "anthropic"
    model: str | None = None
    fast: bool = False                  # use the cheap/fast model tier
    save: bool = True
    # Output ceiling. None means "this model's real maximum" — the point of
    # Phase 13's registry change. A number here can only lower it: an over-large
    # max_tokens is a 400 from the API, not a longer answer.
    max_tokens: int | None = None
    # Thinking depth (low|medium|high|xhigh|max). Ignored for models that
    # predate output_config.effort, which would error on receiving it.
    effort: str | None = None
    # Free-form payload for targets whose context isn't in the database —
    # a strategy's current form values, a fundamentals panel's fetched data.
    payload: dict | None = None


@router.get("/providers")
async def get_providers(current_user: User = Depends(get_current_user)):
    """
    Which providers are installed and actually configured.

    Reports `configured` separately from `installed` because the common failure
    is a present SDK with no API key — which previously surfaced only as an
    authentication error at call time.
    """
    service = LLMService(await _user_api_keys(current_user.id))
    return {"providers": service.available_providers(), "targets": sorted(VALID_TARGETS)}


@router.post("/run")
async def run_analysis(
    req: AnalysisRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if req.target_type not in VALID_TARGETS:
        raise HTTPException(status_code=400, detail=f"Unknown target_type '{req.target_type}'")

    context_text, digest, label = await _build_context(req, current_user, db)
    if not context_text:
        raise HTTPException(status_code=404, detail="Nothing to analyse for that target")

    model = req.model or (
        FAST_MODELS if req.fast else DEFAULT_MODELS
    ).get(req.provider) or DEFAULT_MODELS["anthropic"]

    prompt = build_prompt(context_text, req.question, req.target_type)
    # Per-user encrypted keys take precedence over the process environment.
    # `services/api_key_store.py` has existed (with Fernet encryption and an
    # APIKey table) wired to absolutely nothing; this is its first consumer.
    # Falling back to the env var keeps a single-user local setup working with
    # no UI step.
    service = LLMService(await _user_api_keys(current_user.id))
    answer = await service._call_provider(
        req.provider, model, prompt,
        system=SYSTEM_PROMPT,
        # None -> the model's own ceiling. See llm_service.resolve_max_tokens.
        max_tokens=req.max_tokens,
        effort=req.effort,
    )

    record_id = None
    if req.save:
        record_id = str(uuid.uuid4())
        db.add(LLMAnalysis(
            id=record_id,
            user_id=current_user.id,
            context_type=req.target_type.upper(),
            source_id=req.target_id,
            provider=req.provider,
            model=model,
            analysis_text=answer,
            # The question AND the digest are stored together: an answer read
            # back in a month is uninterpretable without knowing what was asked
            # and what numbers it was asked about.
            user_question=json.dumps({
                "question": req.question,
                "label": label,
                "digest": digest,
            })[:60000],
        ))
        await db.commit()

    return {
        "id": record_id,
        "analysis": answer,
        "model": model,
        "provider": req.provider,
        "label": label,
        "digest": digest,
        # Echo what the request actually resolved to, so the UI can show
        # "answered by Opus 5 at 128K / effort high" rather than guessing.
        "max_tokens": resolve_max_tokens(req.provider, model, req.max_tokens),
        "effort": req.effort or DEFAULT_EFFORT,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


async def _user_api_keys(user_id: str) -> dict[str, str]:
    """Whatever provider keys this user has stored. Missing keys are simply absent."""
    from backend.services.api_key_store import get_api_key
    out: dict[str, str] = {}
    for provider in ("anthropic", "openai", "gemini"):
        try:
            key = await get_api_key(user_id, provider)
        except Exception:
            key = None
        if key:
            out[provider] = key
    return out


class ApiKeyRequest(BaseModel):
    provider: str
    api_key: str


@router.post("/keys")
async def set_api_key(
    req: ApiKeyRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Store an provider API key for this user, encrypted at rest.

    The key is written straight to the encrypted store and is NEVER returned by
    any endpoint — `/keys` below reports only whether one exists. It also never
    reaches the browser again after this call: the backend is what talks to
    Anthropic.
    """
    from backend.services.api_key_store import store_api_key

    provider = req.provider.strip().lower()
    if provider not in ("anthropic", "openai", "gemini"):
        raise HTTPException(status_code=400, detail=f"Unknown provider '{provider}'")
    if not req.api_key.strip():
        raise HTTPException(status_code=400, detail="Empty key")

    ok = await store_api_key(current_user.id, provider, req.api_key.strip())
    if not ok:
        raise HTTPException(
            status_code=500,
            detail="Could not store the key — ENCRYPTION_KEY is missing or invalid "
                   "in the backend environment, so it cannot be encrypted at rest.",
        )
    return {"provider": provider, "stored": True}


@router.get("/keys")
async def list_api_keys(current_user: User = Depends(get_current_user)):
    """Which providers have a stored key. Never returns the keys themselves."""
    keys = await _user_api_keys(current_user.id)
    import os
    return {
        provider: {
            "stored": provider in keys,
            # Reported separately so "it works but I never set it here" is
            # explainable rather than mysterious.
            "from_env": bool(os.environ.get(f"{provider.upper()}_API_KEY")),
        }
        for provider in ("anthropic", "openai", "gemini")
    }


@router.delete("/keys/{provider}")
async def remove_api_key(
    provider: str,
    current_user: User = Depends(get_current_user),
):
    from backend.services.api_key_store import delete_api_key
    return {"provider": provider, "deleted": await delete_api_key(current_user.id, provider.lower())}


@router.get("/models")
async def get_models(current_user: User = Depends(get_current_user)):
    """
    The model catalogue for the frontend picker.

    Carries each model's real output ceiling, context window and price, so the
    choice is made with the tradeoff visible instead of by name recognition.
    """
    service = LLMService(await _user_api_keys(current_user.id))
    providers = service.available_providers()
    return {
        "providers": providers,
        "default_provider": "anthropic",
        "default_model": DEFAULT_MODELS["anthropic"],
        "effort_levels": EFFORT_LEVELS,
        "default_effort": DEFAULT_EFFORT,
        "targets": sorted(VALID_TARGETS),
    }


@router.get("/history")
async def analysis_history(
    limit: int = 50,
    target_type: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(LLMAnalysis).where(LLMAnalysis.user_id == current_user.id)
    if target_type:
        stmt = stmt.where(LLMAnalysis.context_type == target_type.upper())
    result = await db.execute(stmt.order_by(desc(LLMAnalysis.created_at)).limit(limit))

    out = []
    for a in result.scalars().all():
        meta = {}
        if a.user_question:
            try:
                meta = json.loads(a.user_question)
            except (ValueError, TypeError):
                meta = {"question": a.user_question}
        out.append({
            "id": a.id,
            "context_type": a.context_type,
            "source_id": a.source_id,
            "provider": a.provider,
            "model": a.model,
            "question": meta.get("question"),
            "label": meta.get("label"),
            "digest": meta.get("digest"),
            "analysis_text": a.analysis_text,
            "created_at": a.created_at,
        })
    return {"analyses": out}


@router.delete("/{analysis_id}")
async def delete_analysis(
    analysis_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(LLMAnalysis).where(
            LLMAnalysis.id == analysis_id, LLMAnalysis.user_id == current_user.id
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Analysis not found")
    await db.delete(row)
    await db.commit()
    return {"deleted": True}


# ─────────────────────────────────────────────────────────────────────────

async def _build_context(
    req: AnalysisRequest, user: User, db: AsyncSession
) -> tuple[str, dict, str]:
    """Assemble the target's context. Returns (text, digest, human label)."""

    if req.target_type in ("backtest", "portfolio"):
        result = await _load_backtest(req.target_id, user, db)
        if not result:
            return "", {}, ""
        text, digest = build_backtest_context(result)
        label = f"{result.get('symbol') or 'portfolio'} · {result.get('strategy_id', '')}".strip(" ·")
        return text, digest, label

    if req.target_type == "trades":
        rows = await db.execute(
            select(Trade)
            .where(Trade.user_id == user.id, Trade.status == "CLOSED")
            .order_by(desc(Trade.id)).limit(200)
        )
        trades = [{
            "symbol": t.symbol, "strategy_id": t.strategy_id, "direction": t.direction,
            "entry_price": t.entry_price, "exit_price": getattr(t, "exit_price", None),
            "pnl": getattr(t, "pnl", None),
            "exit_reason": getattr(t, "exit_reason", None) or getattr(t, "close_reason", None),
        } for t in rows.scalars().all()]
        if not trades:
            return "", {}, ""
        text, digest = build_trades_context(trades)
        return text, digest, f"{len(trades)} closed trades"

    if req.target_type == "signals":
        rows = await db.execute(
            select(Signal).where(Signal.user_id == user.id)
            .order_by(desc(Signal.id)).limit(300)
        )
        signals = [{
            "symbol": s.symbol, "strategy_id": s.strategy_id, "direction": s.direction,
            "status": s.status, "reject_reason": getattr(s, "reject_reason", None),
            "created_at": str(s.created_at),
        } for s in rows.scalars().all()]
        if not signals:
            return "", {}, ""
        text, digest = build_signals_context(signals)
        return text, digest, f"{len(signals)} signals"

    if req.target_type == "logs":
        logs = log_hub.query(session_id=req.target_id, limit=2000) if req.target_id \
            else log_hub.query(limit=2000)
        if not logs:
            return "", {}, ""
        label = req.target_id or "recent"
        text, digest = build_logs_context(logs, label=label)
        return text, digest, f"logs · {label}"

    # [Phase 13] One trade, with the markings its strategy emitted at signal
    # time — the context behind "was this strategy implemented correctly here".
    if req.target_type == "trade":
        group = await _load_trade_group(req.target_id, user, db)
        if not group:
            return "", {}, ""
        text, digest = build_trade_chart_context(group)
        return text, digest, f"{group.get('symbol', '?')} · {group.get('entry_time', '')}"

    # Strategy parameters, optionally against the strategy's own spec doc. The
    # params come from the request payload rather than the DB because the most
    # useful moment to ask is while editing them, before they are saved.
    if req.target_type == "strategy_config":
        payload = req.payload or {}
        strategy_id = payload.get("strategy_id") or req.target_id or "unknown"
        params = payload.get("params") or {}
        if not params:
            return "", {}, ""
        spec = _load_strategy_spec(strategy_id) if payload.get("include_spec", True) else None
        text, digest = build_strategy_config_context(strategy_id, params, spec)
        return text, digest, f"config · {strategy_id}"

    if req.target_type == "orderflow":
        payload = req.payload or {}
        symbol = payload.get("symbol") or req.target_id or "?"
        flow = payload.get("flow") or {}
        if not flow:
            return "", {}, ""
        text, digest = build_orderflow_context(symbol, flow)
        return text, digest, f"order flow · {symbol}"

    # Fundamentals panels hand their already-fetched data straight through:
    # re-fetching it here would risk analysing something different from what
    # the user is looking at.
    if req.target_type == "fundamentals":
        payload = req.payload or {}
        if not payload:
            return "", {}, ""
        label = payload.get("label") or req.target_id or "fundamentals"
        text = f"## {label}\n\n```json\n{json.dumps(payload, indent=2, default=str)[:40000]}\n```"
        return text, {"label": label, "keys": sorted(payload)}, f"fundamentals · {label}"

    return "", {}, ""


def _load_strategy_spec(strategy_id: str) -> str | None:
    """
    Pull the strategy's spec document so "do these params match the spec" is
    answerable rather than guessable.

    Best-effort by design: a missing spec produces a config-only analysis, not
    an error — the parameters are still worth reviewing without it.
    """
    from pathlib import Path
    docs = Path(__file__).resolve().parents[3] / "docs"
    stems = {
        "APA_v1": ["apa_strategy_implementation_plan"],
        "VWAP_v1": ["vwap_strategy_v2", "vwap_strategy_implementation_plan"],
        "CRT_v1": ["CRT_Strategy_Spec"],
        "HTFFVGFlip_v1": ["strategy-1-htf-fvg-flip"],
        "BiasIFVG_v1": ["strategy-2-bias-keylevel-ifvg"],
        "NYOpenRetest_v1": ["strategy-3-nyopen-break-retest"],
    }.get(strategy_id, [])
    for stem in stems:
        p = docs / f"{stem}.md"
        if p.exists():
            try:
                return p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
    return None


async def _load_trade_group(group_id: str | None, user: User, db: AsyncSession) -> dict | None:
    """
    Find one grouped trade, in the current run first and saved runs second.

    Current-run-first because the common case is analysing a trade you are
    looking at right now, in a run you have not saved yet.
    """
    from backend.api.routes.backtest import USER_BACKTEST_STATE, safe_json_loads

    state = USER_BACKTEST_STATE.get(user.id)
    if state and state.get("result"):
        for g in state["result"].get("grouped_trades", []):
            if str(g.get("group_id")) == str(group_id):
                return g

    if group_id is None:
        return None
    try:
        row_id = int(group_id)
    except (TypeError, ValueError):
        return None

    from backend.data.models import BacktestTrade
    res = await db.execute(
        select(BacktestTrade)
        .join(BacktestRun, BacktestRun.id == BacktestTrade.backtest_id)
        .where(BacktestTrade.id == row_id, BacktestRun.user_id == user.id)
    )
    t = res.scalar_one_or_none()
    if not t:
        return None
    return {
        "group_id": t.id,
        "symbol": t.symbol,
        "direction": getattr(t, "direction", None),
        "entry_price": getattr(t, "entry_price", None),
        "exit_price": getattr(t, "exit_price", None),
        "entry_time": str(getattr(t, "entry_time", "") or ""),
        "exit_time": str(getattr(t, "exit_time", "") or ""),
        "combined_pnl": getattr(t, "pnl", None),
        "confluence_score": getattr(t, "confluence_score", None),
        "sub_trades": safe_json_loads(getattr(t, "sub_trades", None), []) or [],
        "smc_data": safe_json_loads(getattr(t, "smc_data", None), {}) or {},
    }


async def _load_backtest(target_id: str | None, user: User, db: AsyncSession) -> dict | None:
    """
    Resolve a backtest by id, falling back to the in-memory current run.

    The fallback matters: the most common thing to analyse is the run that just
    finished and has not been saved yet.
    """
    if target_id:
        row = await db.execute(
            select(BacktestRun).where(BacktestRun.id == target_id, BacktestRun.user_id == user.id)
        )
        run = row.scalar_one_or_none()
        if run is None:
            return None
        # Rebuild enough of the result shape for the context builder. Trades are
        # loaded through the saved-run endpoint's own accessor rather than
        # duplicated here.
        from backend.api.routes.backtest import safe_json_loads
        return {
            "symbol": run.symbol,
            "strategy_id": run.strategy_id,
            "start_date": str(getattr(run, "start_date", "") or ""),
            "end_date": str(getattr(run, "end_date", "") or ""),
            "initial_balance": getattr(run, "initial_balance", None),
            "final_balance": getattr(run, "final_balance", None),
            "report": safe_json_loads(getattr(run, "report", None), {}) or {},
            "grouped_trades": [],
            "rejection_funnel": safe_json_loads(getattr(run, "rejection_funnel", None), {}) or {},
        }

    from backend.api.routes.backtest import USER_BACKTEST_STATE
    state = USER_BACKTEST_STATE.get(user.id)
    if state and state.get("result"):
        return state["result"]
    return None
