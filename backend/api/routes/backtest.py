"""
backend/api/routes/backtest.py

Backtest run, save/dismiss, load, delete endpoints.
Source: TradingBot_MasterPlan-2.md Section 6 — REST API
"""

# [T2.1 fix] asyncio is used at several points in this module (notably the
# off-loop _sanitize and json.dumps calls). It was only ever imported inside
# nested function scopes, so the module-level uses raised
# "name 'asyncio' is not defined" AFTER the simulation had already finished —
# the run completed, logged its P&L, then failed during finalisation and the
# result was discarded. Imported once, here, where every scope can see it.
import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer, selectinload

from backend.strategies.strategy_defaults import get_slot_tp1_rr_defaults
from backend.api.deps import get_current_user
from backend.data.database import get_db
from backend.data.models import BacktestRun, BacktestTrade, User
from backend.utils.logger import get_logger

try:
    from backend.data.redis_client import redis_client
    HAS_REDIS = True
except ImportError:
    HAS_REDIS = False
    redis_client = None


logger = get_logger(__name__)

from backend.services.replay_stream import ReplayStreamer, bar_from_row  # noqa: E402

# Ceiling on signals from one symbol in one run. Not a tuning knob — a circuit
# breaker. See the guard at its use site for what it protects against.
MAX_SIGNALS_PER_RUN = 5000
from backend.services.log_stream import log_hub  # noqa: E402

USER_BACKTEST_STATE = {}  # Fallback in-memory persistence: user_id -> state


def _redis_safe_state(state):
    """
    State minus the megabyte payloads, for the Redis mirror.

    [T2.1] chart_data / smc_data / replay / run_logs are re-derivable and are
    served from the in-memory copy by their own endpoints. Writing them into
    Redis made every SET a multi-hundred-MB serialise-and-write that could time
    out — and a failed SET left a STALE percentage pinned in Redis for the key's
    full 3600s TTL, which is one of the three causes of the "stuck at 95%" bug.

    Memory remains the source of truth; Redis only has to answer "is it done,
    and what were the headline numbers" after a process restart.
    """
    if not isinstance(state, dict):
        return state
    out = dict(state)
    res = out.get("result")
    if isinstance(res, dict):
        res = {k: v for k, v in res.items() if k != "replay"}
        heavy = ("chart_data", "chart_data_h1", "chart_data_m15",
                 "chart_data_m5", "smc_data")
        for key in ("grouped_trades", "trades"):
            rows = res.get(key)
            if isinstance(rows, list):
                res[key] = [
                    {k: v for k, v in t.items() if k not in heavy}
                    if isinstance(t, dict) else t
                    for t in rows
                ]
        res["run_logs"] = []
        out["result"] = res
    return out
router = APIRouter(prefix="/api", tags=["backtest"])


def _filter_run_logs(logs: list[dict], cap: int = 2000) -> list[dict]:
    """
    Task [I2]: a flat `logs[-100:]` slice discarded exactly the WARNING/ERROR
    entries that explain a run's behaviour (margin-ceiling truncation, hard-cap
    triggers, SL-floor widening) in favour of whatever INFO noise happened to be
    most recent. Keep every WARNING/ERROR (they are the diagnostic signal and are
    comparatively rare), then fill the remaining budget with an even sample of
    INFO/DEBUG entries so the log still reads as a coherent timeline.
    """
    if not logs:
        return []
    important = [l for l in logs if str(l.get("level", "")).upper() in ("WARNING", "ERROR", "CRITICAL")]
    other = [l for l in logs if str(l.get("level", "")).upper() not in ("WARNING", "ERROR", "CRITICAL")]

    if len(important) >= cap:
        # Even a WARNING/ERROR-only log exceeds the cap — keep the most recent ones.
        return important[-cap:]

    budget = cap - len(important)
    if len(other) > budget:
        step = len(other) / budget
        other = [other[int(i * step)] for i in range(budget)]

    merged = important + other
    merged.sort(key=lambda l: l.get("time", ""))
    return merged


class BulkBacktestRequest(BaseModel):
    ids: list[str]

class BacktestRequest(BaseModel):
    strategy_id: str = "APA_v1"
    symbol: str
    start_date: str | None = None
    end_date: str | None = None
    # Raised from 5000 after measuring the live terminal (build 6140,
    # maxbars=100000): a 50,000-bar M5 request is served in full and reaches
    # back ~8.5 months, which is the window the optimization work needs.
    # M1 history is much shorter (~7 weeks) — prefer M5 as the primary TF
    # for long runs.
    candle_count: int = 50000
    initial_balance: float = 10000.0
    risk_config: dict[str, Any] = {}
    prop_firm: dict[str, Any] = {}
    # ── Dynamic Strategy Params ──
    strategy_params: dict[str, Any] = {}
    # ── Risk Params ──

    risk_per_trade_pct: float = 1.0
    min_rr: float = 3.0
    max_daily_drawdown_pct: float = 3.0
    max_weekly_drawdown_pct: float = 6.0
    max_concurrent_positions: int = 3
    max_positions_per_symbol: int = 1
    max_daily_trades: int = 5
    target_profit_enabled: bool = False
    max_daily_profit: float = 500.0
    max_weekly_profit: float = 2000.0
    # ── TP Config (defaults per spec: TP1=1:1, TP2=3:1, TP3=5:1) ──
    tp_count: int = 3
    tp1_rr: float = 1.0
    tp2_rr: float = 3.0
    tp3_rr: float = 5.0
    tp4_rr: float = 10.0
    tp5_rr: float = 15.0
    tp_splits: str = "30,25,20,15,10"
    # ── Break-Even ──
    be_trigger_rr: float = 1.0
    be_buffer_pips: float = 2.0
    # ── Trailing Stops ──
    trail_method_tp2: str = "ATR_TRAIL"
    trail_method_tp3: str = "STRUCTURE_TRAIL"
    trail_method_tp4: str = "ATR_TRAIL"
    trail_method_tp5: str = "STRUCTURE_TRAIL"
    atr_trail_multiplier: float = 1.5
    trail_pips: float = 15.0
    # ── Risk Safety Cap ──
    max_risk_hard_cap_pct: float = 3.0  # Absolute safety cap from PropFirmParams
    # ── Multi-Strategy Filters ──
    # None = use the strategy's measured default (see strategy_defaults.py).
    # Explicit True/False still wins.
    session_filter_enabled: bool | None = None
    # [L1-opt] Per-bar replay animation. On by default so the UI is
    # unchanged, but it is measurably expensive: the streamer retains a
    # dict per bar and emits WebSocket batches throughout the run. Measured
    # on APA/BTCUSD at 50k bars, the API loop cost ~1,005s against ~335s for
    # the same work without it. An automated sweep has no viewer, so it can
    # turn this off and get the run itself rather than the animation.
    replay_enabled: bool = True
    manual_bias_overrides: dict[str, Any] = {}
    # ── [Phase 2 sizing-truth] typed, optional — None resolves to the
    # RiskParams default inside risk/engine.py & position_sizer.py, so these
    # are real settings rather than only reachable via the untyped
    # risk_config passthrough dict. See core/config_schema.py::RiskParams.
    max_margin_utilisation_pct: float | None = None
    min_deployable_risk_pct: float | None = None
    min_stop_spread_multiple: float | None = None
    confluence_risk_tiers: list[tuple[int, float]] | None = None
    reject_below_confluence: bool | None = None
    post_split_risk_tolerance_pct: float | None = None
    exit_slippage_pips: float | str | None = None
    open_risk_weight: float | None = None
    allow_pyramiding: bool | None = None
    min_bars_between_entries: int | None = None
    max_account_leverage: float | None = None
    min_sl_pips: float | None = None
    # ── [Phase 4/5] backtest<->live parity + exit architecture ──
    sizing_basis: str | None = None
    be_spread_multiple: float | None = None
    trail_require_be_first: bool | None = None
    be_mode: str | None = None
    be_trigger_tp_level: int | None = None
    trail_method_tp1: str | None = None
    trail_mode: str | None = None
    trail_trigger_rr: float | None = None
    trail_trigger_tp_level: int | None = None
    tp_volume_pcts: list[float] | None = None
    # ── [Phase 9] Portfolio governor ──
    max_cluster_risk_pct: float | None = None
    max_net_direction_risk_pct: float | None = None
    symbol_cluster_overrides: dict[str, str] | None = None
    strategy_risk_budget_pct: dict[str, float] | None = None
    # ── Simulation Costs — broker-sourced by default ──
    # `None` (the default) means "NOT explicitly set by the user": the engine
    # resolves the value from backend.risk.broker_costs.get_broker_costs(),
    # which prefers live MT5 symbol data and falls back to asset-class averages.
    # These were previously `float = 0.0`, which made "unset" indistinguishable
    # from "deliberately zero" — so EVERY run was silently executed with zero
    # transaction costs, materially overstating every strategy's edge.
    # A caller that genuinely wants a costless run passes 0.0 explicitly, and any
    # previously-saved params_snapshot (which stores literal 0.0) replays
    # unchanged. The string "auto" is also accepted as an explicit request for
    # broker-sourced defaults.
    slippage_pips: float | str | None = None       # Entry/exit slippage per trade in pips
    commission_per_lot: float | str | None = None  # Round-turn commission in account currency per lot
    spread_pips: float | str | None = None         # Fixed spread cost applied at entry in pips
    # Overnight financing per lot per day, signed MT5-style (negative = charge).
    # Unset -> broker-sourced. Applied per rollover crossed on multi-day holds.
    swap_long_per_lot_per_day: float | str | None = None
    swap_short_per_lot_per_day: float | str | None = None
    # Broker minimum stop distance in pips, used to reject positions whose SL/TP
    # would be un-placeable at the actual fill price. Unset -> broker-sourced.
    stops_level_pips: float | str | None = None
    # ── Wick Simulation (BUG-9) ──
    simulate_wicks: bool = True      # Use OHLC shadow-weighted model for ambiguous SL/TP bars

class SaveBacktestRequest(BaseModel):
    backtest_data: dict[str, Any]
    save_mode: str = "FULL"  # "FULL" or "SUMMARY"


class PortfolioSymbolConfig(BaseModel):
    """Config for a single symbol/strategy pair in a portfolio backtest."""
    symbol: str
    strategy_id: str = "APA_v1"
    strategy_params: dict[str, Any] = {}
    # [12.8/Part14] Optional — when two rows share the same `symbol` under
    # different strategies, each needs its own slot_id so their candle data,
    # signals, and positions don't collide in PortfolioBacktestEngine's
    # internal dicts (which are keyed by slot, not bare symbol — see
    # portfolio_engine.py::run()'s symbol_map parameter). Unset = the route
    # derives one automatically from symbol+strategy_id.
    slot_id: str | None = None
    # [17.1] Per-row take-profit R:R and count. None = inherit the portfolio-wide
    # value. This is what makes a portfolio backtest able to give DriftJumpAlpha
    # on Crash 1000 a 1:3 target while BiasIFVG on USOUSD runs 1:5 in the SAME
    # run — research/16 measured those as materially different optima, and a
    # single shared tp1_rr could not express it.
    tp1_rr: float | None = None
    tp_count: int | None = None


class PortfolioBacktestRequest(BaseModel):
    """Request model for a portfolio (multi-symbol) backtest."""
    symbols: list[PortfolioSymbolConfig]
    start_date: str | None = None
    end_date: str | None = None
    # Raised from 5000 after measuring the live terminal (build 6140,
    # maxbars=100000): a 50,000-bar M5 request is served in full and reaches
    # back ~8.5 months, which is the window the optimization work needs.
    # M1 history is much shorter (~7 weeks) — prefer M5 as the primary TF
    # for long runs.
    candle_count: int = 50000
    initial_balance: float = 10000.0
    prop_firm: dict[str, Any] = {}
    # ── Risk Params (shared across portfolio) ──

    risk_per_trade_pct: float = 1.0
    min_rr: float = 3.0
    max_daily_drawdown_pct: float = 3.0
    max_weekly_drawdown_pct: float = 6.0
    max_concurrent_positions: int = 5
    max_positions_per_symbol: int = 1
    max_daily_trades: int = 5
    target_profit_enabled: bool = False
    max_daily_profit: float = 500.0
    max_weekly_profit: float = 2000.0
    # ── TP Config ──
    tp_count: int = 3
    tp1_rr: float = 1.0
    tp2_rr: float = 3.0
    tp3_rr: float = 5.0
    tp4_rr: float = 10.0
    tp5_rr: float = 15.0
    tp_splits: str = "30,25,20,15,10"
    # ── Break-Even ──
    be_trigger_rr: float = 1.0
    be_buffer_pips: float = 2.0
    # ── Trailing Stops ──
    trail_method_tp2: str = "ATR_TRAIL"
    trail_method_tp3: str = "STRUCTURE_TRAIL"
    trail_method_tp4: str = "ATR_TRAIL"
    trail_method_tp5: str = "STRUCTURE_TRAIL"
    atr_trail_multiplier: float = 1.5
    trail_pips: float = 15.0
    # ── Risk Safety Cap ──
    max_risk_hard_cap_pct: float = 3.0  # Absolute safety cap from PropFirmParams
    # None = use the strategy's measured default (see strategy_defaults.py).
    # Explicit True/False still wins.
    session_filter_enabled: bool | None = None
    # [L1-opt] Per-bar replay animation. On by default so the UI is
    # unchanged, but it is measurably expensive: the streamer retains a
    # dict per bar and emits WebSocket batches throughout the run. Measured
    # on APA/BTCUSD at 50k bars, the API loop cost ~1,005s against ~335s for
    # the same work without it. An automated sweep has no viewer, so it can
    # turn this off and get the run itself rather than the animation.
    replay_enabled: bool = True
    # ── [Phase 2 sizing-truth] — see BacktestRequest above for field meanings.
    max_margin_utilisation_pct: float | None = None
    min_deployable_risk_pct: float | None = None
    min_stop_spread_multiple: float | None = None
    confluence_risk_tiers: list[tuple[int, float]] | None = None
    reject_below_confluence: bool | None = None
    post_split_risk_tolerance_pct: float | None = None
    exit_slippage_pips: float | str | None = None
    open_risk_weight: float | None = None
    allow_pyramiding: bool | None = None
    min_bars_between_entries: int | None = None
    max_account_leverage: float | None = None
    min_sl_pips: float | None = None
    # ── [Phase 4/5] backtest<->live parity + exit architecture ──
    sizing_basis: str | None = None
    be_spread_multiple: float | None = None
    trail_require_be_first: bool | None = None
    be_mode: str | None = None
    be_trigger_tp_level: int | None = None
    trail_method_tp1: str | None = None
    trail_mode: str | None = None
    trail_trigger_rr: float | None = None
    trail_trigger_tp_level: int | None = None
    tp_volume_pcts: list[float] | None = None
    # ── [Phase 9] Portfolio governor ──
    max_cluster_risk_pct: float | None = None
    max_net_direction_risk_pct: float | None = None
    symbol_cluster_overrides: dict[str, str] | None = None
    strategy_risk_budget_pct: dict[str, float] | None = None
    # ── Simulation Costs — broker-sourced by default ──
    # `None` (the default) means "NOT explicitly set by the user": the engine
    # resolves the value from backend.risk.broker_costs.get_broker_costs(),
    # which prefers live MT5 symbol data and falls back to asset-class averages.
    # These were previously `float = 0.0`, which made "unset" indistinguishable
    # from "deliberately zero" — so EVERY run was silently executed with zero
    # transaction costs, materially overstating every strategy's edge.
    # A caller that genuinely wants a costless run passes 0.0 explicitly, and any
    # previously-saved params_snapshot (which stores literal 0.0) replays
    # unchanged. The string "auto" is also accepted as an explicit request for
    # broker-sourced defaults.
    slippage_pips: float | str | None = None       # Entry/exit slippage per trade in pips
    commission_per_lot: float | str | None = None  # Round-turn commission in account currency per lot
    spread_pips: float | str | None = None         # Fixed spread cost applied at entry in pips
    # Overnight financing per lot per day, signed MT5-style (negative = charge).
    # Unset -> broker-sourced. Applied per rollover crossed on multi-day holds.
    swap_long_per_lot_per_day: float | str | None = None
    swap_short_per_lot_per_day: float | str | None = None
    # Broker minimum stop distance in pips, used to reject positions whose SL/TP
    # would be un-placeable at the actual fill price. Unset -> broker-sourced.
    stops_level_pips: float | str | None = None
    # ── Wick Simulation (BUG-9) ──
    simulate_wicks: bool = True      # Use OHLC shadow-weighted model for ambiguous SL/TP bars


async def reconcile_orphaned_runs() -> int:
    """
    Clear any backtest left marked "running" by a previous process.

    A backtest lives entirely inside one server process, but its status is
    persisted to Redis with a 1-hour TTL so the UI survives a page reload. Those
    two facts combine badly on restart: the process dies mid-run, the Redis key
    outlives it, and every client that asks then sees `status: "running"` for a
    run that no longer exists anywhere. The Backtester sits on "Stop Backtest"
    forever, refuses to start another, and shows no result — which is exactly
    the "everything hangs, I see nothing" symptom.

    Marked as an ERROR rather than deleted, so the UI can say what happened
    instead of silently forgetting a run the user was watching.

    Called once from the startup hook, before any request is served.
    """
    if not (HAS_REDIS and redis_client and redis_client.redis):
        return 0

    cleared = 0
    try:
        async for key in redis_client.redis.scan_iter(match="backtest_state:*"):
            try:
                raw = await redis_client.redis.get(key)
                if not raw:
                    continue
                state = json.loads(raw)
                if state.get("status") != "running":
                    continue
                state["status"] = "error"
                state["progress"] = {
                    "stage": "error",
                    "pct": 0,
                    "message": (
                        "The backend restarted while this backtest was running, so "
                        "it did not finish. Run it again."
                    ),
                }
                await redis_client.redis.set(key, json.dumps(_redis_safe_state(state), default=str), ex=3600)
                cleared += 1
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"[BACKTEST] Could not reconcile orphaned runs: {e}")

    if cleared:
        logger.info(f"[BACKTEST] Cleared {cleared} orphaned 'running' state(s) left by a previous process")
    return cleared


@router.get("/backtest_status")
async def get_backtest_status(current_user: User = Depends(get_current_user)):
    # 1. Try local memory first (instant)
    state = USER_BACKTEST_STATE.get(current_user.id)
    
    # 2. Fallback to Redis if missing (e.g. server restarted)
    if state is None and HAS_REDIS and redis_client and redis_client.redis:
        try:
            import asyncio

            import redis.exceptions
            data = await redis_client.redis.get(f"backtest_state:{current_user.id}")
            if data:
                state = json.loads(data)
                # Cache it back to memory
                USER_BACKTEST_STATE[current_user.id] = state
        except (Exception, asyncio.CancelledError, redis.exceptions.TimeoutError) as e:
            logger.warning(f"[API] Redis get status failed: {e}")
            
    if state is None:
        state = {"status": "idle", "progress": None}
        
    return {"status": state.get("status"), "progress": state.get("progress")}

@router.get("/latest_result")
async def get_backtest_latest_result(current_user: User = Depends(get_current_user)):
    state = USER_BACKTEST_STATE.get(current_user.id)
    
    if state is None and HAS_REDIS and redis_client and redis_client.redis:
        try:
            import asyncio

            import redis.exceptions
            data = await redis_client.redis.get(f"backtest_state:{current_user.id}")
            if data:
                state = json.loads(data)
                USER_BACKTEST_STATE[current_user.id] = state
        except (Exception, asyncio.CancelledError, redis.exceptions.TimeoutError) as e:
            logger.warning(f"[API] Redis get latest_result failed: {e}")
            
    if state and state.get("status") == "complete":
        import copy
        result_data = copy.deepcopy(state.get("result", {}))
        # Strip massive chart data from main payload
        trades = result_data.get("grouped_trades", result_data.get("trades", []))
        if isinstance(trades, list):
            for t in trades:
                if isinstance(t, dict):
                    t.pop("chart_data", None)
                    t.pop("chart_data_m15", None)
                    t.pop("chart_data_m5", None)
        
        # Strip massive run_logs array
        if "run_logs" in result_data:
            result_data["run_logs"] = []
            
        return result_data
    return {}

@router.post("/stop")
async def stop_backtest_endpoint(current_user: User = Depends(get_current_user)):
    state = USER_BACKTEST_STATE.get(current_user.id)
    
    if state is None and HAS_REDIS and redis_client and redis_client.redis:
        try:
            import asyncio

            import redis.exceptions
            data = await redis_client.redis.get(f"backtest_state:{current_user.id}")
            if data:
                state = json.loads(data)
        except (Exception, asyncio.CancelledError, redis.exceptions.TimeoutError) as e:
            logger.warning(f"[API] Redis get cancel failed: {e}")
        
    if state and state.get("status") == "running":
        state["status"] = "cancelled"
        USER_BACKTEST_STATE[current_user.id] = state
        if HAS_REDIS and redis_client and redis_client.redis:
            try:
                import asyncio

                import redis.exceptions
                await redis_client.redis.set(f"backtest_state:{current_user.id}", json.dumps(_redis_safe_state(state), default=str), ex=3600)
            except (Exception, asyncio.CancelledError, redis.exceptions.TimeoutError) as e:
                logger.warning(f"[API] Redis set cancel failed: {e}")
        return {"message": "Backtest cancelled"}
    return {"message": "No running backtest found"}

@router.post("/backtest")
async def run_backtest_endpoint(
    req: BacktestRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Run a backtest in the background to prevent frontend timeouts.
    """
    state = await get_backtest_status(current_user)
    if state and state.get("status") == "running":
        raise HTTPException(status_code=400, detail="A backtest is already running for this user.")
        
    from backend.services.bot_service import bot_service
    bot_service.log_system_event(f"Backtest queued: {req.symbol}", category="BACKTEST")

    # [Phase 13 section G] Tag every log record this run emits with a session id,
    # so "show me the logs from that run" is a query rather than a grep.
    log_session_id = log_hub.start_session(
        f"{req.symbol} / {req.strategy_id}", kind="backtest",
    )

    async def _run_backtest_task():
        global USER_BACKTEST_STATE
        initial_state = {
            "status": "running",
            "progress": {"stage": "Fetching data...", "pct": 0},
            "result": None
        }
        
        async def _save_state(state):
            # Memory is the source of truth and is updated synchronously, so a
            # slow or failing Redis can never make a finished run look unfinished.
            USER_BACKTEST_STATE[current_user.id] = state
            if HAS_REDIS and redis_client and redis_client.redis:
                try:
                    import asyncio

                    import redis.exceptions
                    # [T2.1] json.dumps of a finished run walks every trade's
                    # chart_data plus the replay series — hundreds of MB of pure
                    # Python work. Doing it inline blocked the event loop, so
                    # /backtest_status could not be served and the UI froze at
                    # whatever percentage was last written. Serialise in a
                    # thread, and strip the heavy payloads first.
                    payload = await asyncio.to_thread(
                        json.dumps, _redis_safe_state(state), default=str
                    )
                    await redis_client.redis.set(
                        f"backtest_state:{current_user.id}", payload, ex=3600
                    )
                except (Exception, asyncio.CancelledError, redis.exceptions.TimeoutError) as e:
                    logger.warning(f"[BACKTEST] Failed to save state to Redis: {e}")

        async def _get_state():
            # [T2.1] Memory FIRST, matching /backtest_status. This used to read
            # Redis first, so during the finalisation window the two disagreed:
            # if the last Redis SET failed (oversized payload / timeout) the
            # Redis copy stayed pinned at the old percentage for its full 3600s
            # TTL while memory already said "complete".
            cached = USER_BACKTEST_STATE.get(current_user.id)
            if cached is not None:
                return cached
            if HAS_REDIS and redis_client and redis_client.redis:
                try:
                    import asyncio

                    import redis.exceptions
                    data = await redis_client.redis.get(f"backtest_state:{current_user.id}")
                    if data:
                        return json.loads(data)
                except (Exception, asyncio.CancelledError, redis.exceptions.TimeoutError) as e:
                    logger.warning(f"[BACKTEST] Failed to get state from Redis: {e}")
            return initial_state.copy()

        await _save_state(initial_state)
        try:
            import time as _time

            from backend.api.websocket import manager as ws_manager
            from backend.backtester.runner import run_backtest
            from backend.mt5.data_fetcher import DataFetcher, DataFetchError
            from backend.services.bot_service import bot_service

            bt_start = _time.time()
            logger.info(f"═══ BACKTEST START ═══ {req.symbol} | user={current_user.email}")
            bot_service.log_system_event(f"Backtest started: {req.symbol}", category="BACKTEST")

            try:
                current_state = await _get_state()
                current_state["progress"] = {"stage": "Fetching historical data...", "pct": 5}
                await _save_state(current_state)
            except Exception as _e:
                logger.warning(f"[BACKTEST] Could not update progress state: {_e}")

            # NOTE: Engine is built first so we can call get_required_timeframes()
            # before deciding which data to fetch. Data fetch happens below.
            import pandas as pd

            from backend.core.config_schema import InstrumentSettings, UserConfigV2
            from backend.strategies.registry import get_strategy
            
            config = UserConfigV2()
            
            # Map risk fields
            config.risk.min_rr = req.min_rr
            config.risk.risk_per_trade_pct = req.risk_per_trade_pct
            config.risk.max_daily_drawdown_pct = req.max_daily_drawdown_pct
            config.risk.max_weekly_drawdown_pct = req.max_weekly_drawdown_pct
            config.risk.max_concurrent_positions = req.max_concurrent_positions
            config.risk.max_positions_per_symbol = req.max_positions_per_symbol
            config.risk.max_daily_trades = req.max_daily_trades
            
            # Inject dynamic strategy parameters
            if req.strategy_id == "APA_v1":
                for k, v in req.strategy_params.items():
                    if hasattr(config.apa, k):
                        setattr(config.apa, k, v)
            elif req.strategy_id == "VWAP_v1":
                for k, v in req.strategy_params.items():
                    if hasattr(config.vwap, k):
                        setattr(config.vwap, k, v)
                # Sync VWAP's internal daily trade cap with the CB limit,
                # unless the user explicitly provided one in strategy_params.
                if "max_trades_per_day" not in req.strategy_params and req.max_daily_trades is not None:
                    config.vwap.max_trades_per_day = req.max_daily_trades
            elif req.strategy_id == "DriftJumpAlpha_v1":
                for k, v in req.strategy_params.items():
                    if hasattr(config.drift_jump_alpha, k):
                        setattr(config.drift_jump_alpha, k, v)
            elif req.strategy_id == "CRT_v1":
                for k, v in req.strategy_params.items():
                    if hasattr(config.crt, k):
                        setattr(config.crt, k, v)
            elif req.strategy_id == "HTFFVGFlip_v1":
                for k, v in req.strategy_params.items():
                    if hasattr(config.htf_fvg_flip, k):
                        setattr(config.htf_fvg_flip, k, v)
            elif req.strategy_id == "BiasIFVG_v1":
                for k, v in req.strategy_params.items():
                    if hasattr(config.bias_ifvg, k):
                        setattr(config.bias_ifvg, k, v)
            elif req.strategy_id == "NYOpenRetest_v1":
                for k, v in req.strategy_params.items():
                    if hasattr(config.ny_open_retest, k):
                        setattr(config.ny_open_retest, k, v)
                        
            # [T2.7/T3.5] Session enablement is a strategy-level parameter, and the
            # ablation gave three different verdicts: remove it for HTFFVGFlip
            # (-0.170 contribution, actively harmful), loosen it for CRT (+0.009
            # while discarding 88.2% of candidates), keep it for VWAP (+0.064) and
            # BiasIFVG (+0.126). Applied here unless the request set it explicitly.
            try:
                from backend.strategies.strategy_defaults import get_strategy_defaults
                _sd = get_strategy_defaults(req.strategy_id)
                if "session_filter_enabled" in _sd and req.session_filter_enabled is None:
                    _target = {
                        "APA_v1": "apa", "VWAP_v1": "vwap", "CRT_v1": "crt",
                        "BiasIFVG_v1": "bias_ifvg", "HTFFVGFlip_v1": "htf_fvg_flip",
                        "NYOpenRetest_v1": "ny_open_retest",
                    }.get(req.strategy_id)
                    _blk = getattr(config, _target, None) if _target else None
                    if _blk is not None and hasattr(_blk, "session_filter_enabled"):
                        _blk.session_filter_enabled = _sd["session_filter_enabled"]
                        logger.info(
                            f"[BACKTEST] {req.strategy_id}: session_filter_enabled="
                            f"{_sd['session_filter_enabled']} (measured strategy default)"
                        )
            except Exception as _e:
                logger.warning(f"[BACKTEST] strategy session default not applied: {_e}")

            config.instrument_settings = [InstrumentSettings(symbol=req.symbol, strategy_id=req.strategy_id)]
            
            strategy_id = req.strategy_id
            if strategy_id == "SMC_v1":
                strategy_id = "APA_v1"
            engine_class = get_strategy(strategy_id)
            engine = engine_class(config)
            engine.is_backtesting = True
            # [B7] Record per-gate telemetry for backtests.
            #
            # `GateRecorder` defaults to enabled=False so live trading pays
            # nothing for it, and until now NOTHING on this route turned it on —
            # only `ablation.run_recording_pass` did, which the route cannot
            # reach. The consequence was that `rejection_funnel.strategy_rejections`
            # came back `{}` for every run ever saved, and every trade carried the
            # single lumped confluence tag `base_structure`. The 49 `self.gate(...)`
            # call sites across the engines recorded nothing.
            #
            # A backtest is offline and already CPU-bound, so the recorder's cost
            # is irrelevant here; `engine.py` picks the data up on completion.
            engine.gates.enabled = True

            # ── Dynamic timeframe dispatch ───────────────────────────────────────
            # Ask the engine which timeframes it actually needs instead of always
            # fetching H4 / M15 / M5. This ensures M5-only strategies (DriftJumpAlpha)
            # don't receive empty HTF slices that could trip minimum-bar guards.
            required_tfs = engine.get_required_timeframes()  # e.g. ["M5"] or ["H4","M15","M5"]
            logger.info(f"[BACKTEST] Strategy {req.strategy_id} requires timeframes: {required_tfs}")

            # ── Timeframe metadata used for data-fetch warmup & slice windows ──
            TF_META = {
                "M1":  {"np_td": (1,  'm'), "warmup_days": 1,   "window": 500},
                "M5":  {"np_td": (5,  'm'), "warmup_days": 5,   "window": 500},
                "M15": {"np_td": (15, 'm'), "warmup_days": 10,  "window": 300},
                "M30": {"np_td": (30, 'm'), "warmup_days": 15,  "window": 200},
                "H1":  {"np_td": (1,  'h'), "warmup_days": 30,  "window": 200},
                "H4":  {"np_td": (4,  'h'), "warmup_days": 150, "window": 200},
                "D1":  {"np_td": (1,  'D'), "warmup_days": 365, "window": 100},
            }

            # The fastest (primary clock) timeframe drives the simulation loop.
            # Sort TFs by their minute-equivalent so we pick the smallest.
            TF_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}
            sorted_tfs = sorted(required_tfs, key=lambda t: TF_MINUTES.get(t, 999))
            primary_tf = sorted_tfs[0]  # fastest TF = clock driver

            # ── Fetch data for all required timeframes ───────────────────────────
            try:
                candles_by_tf: dict = {}
                if req.start_date and req.end_date:
                    start_dt = datetime.fromisoformat(req.start_date)
                    end_dt = datetime.fromisoformat(req.end_date)
                    for tf in required_tfs:
                        meta = TF_META.get(tf, TF_META["M5"])
                        tf_start = start_dt - pd.Timedelta(days=meta["warmup_days"])
                        candles_by_tf[tf] = await DataFetcher.get_data_range(req.symbol, tf, tf_start, end_dt)
                else:
                    for tf in required_tfs:
                        candles_by_tf[tf] = await DataFetcher.get_historical_data(req.symbol, tf, count=req.candle_count)
            except DataFetchError as e:
                bot_service.log_system_event(f"Backtest data fetch failed: {e.reason}", category="BACKTEST", level="ERROR")
                await ws_manager.broadcast_to_user(current_user.id, {"type": "backtest_error", "message": str(e)})
                return

            missing = [tf for tf, df in candles_by_tf.items() if df is None or df.empty]
            if missing:
                bot_service.log_system_event(f"Incomplete MTF data for backtest ({missing})", category="BACKTEST", level="ERROR")
                await ws_manager.broadcast_to_user(current_user.id, {"type": "backtest_error", "message": f"No data for {missing}"})
                return

            def _index_candles(df):
                if 'time' in df.columns:
                    return df.set_index(pd.to_datetime(df['time'], unit='s'))
                return df

            indexed_by_tf = {tf: _index_candles(df).sort_index() for tf, df in candles_by_tf.items()}

            # Convenience aliases for code below that still needs them (run_backtest call)
            candles_m15_idx = indexed_by_tf.get("M15", indexed_by_tf.get(primary_tf))
            candles_m5_idx  = indexed_by_tf.get("M5",  indexed_by_tf.get(primary_tf))

            signals = []

            # [Phase 13 section C.3] Replay stream. Announces one leg (this is
            # the single-symbol route) so the frontend can build its chart
            # before any bars arrive, then feeds it the Phase-1 bar walk.
            replay = ReplayStreamer(ws_manager, current_user.id, mode="single",
                                   enabled=bool(getattr(req, "replay_enabled", True)))
            replay.init([{
                "slot_id": req.symbol,
                "symbol": req.symbol,
                "strategy_id": req.strategy_id,
                "timeframe": primary_tf,
            }])

            async def generate_signals_simulated():
                import asyncio
                import numpy as np
                import time

                sigs = []
                primary_sorted = indexed_by_tf[primary_tf]
                primary_times = primary_sorted.index.values

                # Prev-time trackers per TF to avoid calling on_bar for the same candle twice
                prev_time_by_tf: dict = {tf: None for tf in required_tfs}

                last_yield_time = time.monotonic()

                replay.leg_start(req.symbol, total_bars=max(0, len(primary_times) - 300))

                # ── Hoisted per-loop work ────────────────────────────────
                # Everything below used to be recomputed on EVERY bar. On a
                # 5,000-bar run that is thousands of redundant pandas/parse
                # operations, and they are what starved the event loop badly
                # enough to time the WebSocket out mid-run.
                #
                # OHLC as flat numpy arrays: `primary_sorted.iloc[i]` builds a
                # pandas Series per bar (~100us). Indexing a numpy array is ~1us.
                _rep_o = primary_sorted["open"].to_numpy(dtype=float)
                _rep_h = primary_sorted["high"].to_numpy(dtype=float)
                _rep_l = primary_sorted["low"].to_numpy(dtype=float)
                _rep_c = primary_sorted["close"].to_numpy(dtype=float)
                _rep_t = primary_times.astype("datetime64[s]").astype("int64")

                # Was `np.datetime64(datetime.fromisoformat(req.start_date))`
                # inside the loop — a date string parsed once per bar.
                _warmup_cutoff = (
                    np.datetime64(datetime.fromisoformat(req.start_date))
                    if req.start_date else None
                )
                # `sorted_tf.index.values` was also re-read per bar, per timeframe.
                _tf_times_by_tf = {tf: indexed_by_tf[tf].index.values for tf in required_tfs}

                for i in range(300, len(primary_times)):
                    # Yield on ELAPSED TIME, not bar count. `last_yield_time` was
                    # assigned here and never read — the time-based yield this
                    # implements was clearly intended and never written, so the
                    # loop yielded every 50 bars no matter how long those 50 bars
                    # took. With multi-timeframe pandas slicing per bar that is
                    # easily >1s of solid blocking, which is exactly long enough
                    # to drop the WebSocket and fail the health poll ("system
                    # goes offline"). Capping the block at ~40ms keeps the socket
                    # and the UI alive without adding a yield per bar.
                    _now = time.monotonic()
                    if (_now - last_yield_time) > 0.04:
                        await asyncio.sleep(0)
                        last_yield_time = time.monotonic()

                    if i % 600 == 0:
                        # [T2.1] Was `* 80 + 10`, which put signal generation at
                        # 10-90% and left the ENTIRE trade simulation crammed
                        # into the jump from 90 to 95 with no events at all —
                        # the "hangs at 95%" symptom. Signal generation is the
                        # cheaper half; give it 15-35 and let the simulation
                        # report across 35-90.
                        pct = int((i / len(primary_times)) * 20) + 15
                        current_state = await _get_state()
                        if current_state.get("status") == "cancelled":
                            bot_service.log_system_event("Backtest cancelled by user", category="BACKTEST")
                            raise Exception("Backtest Cancelled")
                        current_state["progress"] = {"stage": "Running simulation...", "pct": pct}
                        await _save_state(current_state)
                        try:
                            asyncio.create_task(ws_manager.broadcast_to_user(current_user.id, {
                                "type": "backtest_progress", "stage": "Running simulation...", "pct": pct
                            }))
                        except: pass

                    current_time = primary_times[i]
                    is_warmup = _warmup_cutoff is not None and current_time < _warmup_cutoff

                    # Stream the bar being processed. Warm-up bars are skipped:
                    # they exist only to prime indicator state and are outside
                    # the window the user asked to see.
                    if not is_warmup:
                        replay.bar(req.symbol, {
                            "time": int(_rep_t[i]),
                            "open": float(_rep_o[i]),
                            "high": float(_rep_h[i]),
                            "low": float(_rep_l[i]),
                            "close": float(_rep_c[i]),
                        })

                    sig = None

                    for tf in required_tfs:
                        meta = TF_META.get(tf, TF_META["M5"])
                        sorted_tf = indexed_by_tf[tf]
                        tf_times = _tf_times_by_tf[tf]

                        if tf == primary_tf:
                            # Primary TF: use current bar index minus 1 (closed candle)
                            tf_end = i
                            last_tf_time = primary_times[i]
                        else:
                            # HTF: find fully closed candle before current_time
                            np_td, np_unit = meta["np_td"]
                            cutoff = current_time - np.timedelta64(np_td, np_unit)
                            tf_end = int(np.searchsorted(tf_times, cutoff, side='right'))
                            last_tf_time = tf_times[tf_end - 1] if tf_end > 0 else None

                        # Build the DataFrame slice ONLY once this timeframe has
                        # actually produced a new bar.
                        #
                        # The slice used to be built on every iteration and then
                        # thrown away unless the timeframe had advanced — so on
                        # M5-driven data an H4 slice was constructed ~48x more
                        # often than it was used. Measured on XAUUSD/APA over
                        # 1,200 bars (scripts/profile_signal_loop.py):
                        #     slice every bar   18.22s   2,400 slices / 1,602 used
                        #     slice on advance   8.66s   1,602 slices / 1,602 used
                        # Identical signals out; 2.1x faster.
                        if last_tf_time is None or last_tf_time == prev_time_by_tf[tf]:
                            continue

                        slice_tf = sorted_tf.iloc[max(0, tf_end - meta["window"]):tf_end]
                        if len(slice_tf) < 20:
                            continue

                        s = await engine.on_bar(req.symbol, tf, slice_tf)
                        if s:
                            sig = s
                        prev_time_by_tf[tf] = last_tf_time

                    if sig and not is_warmup:
                        sig_dict = {
                            "symbol": sig.symbol,
                            "direction": sig.direction,
                            "time": int(current_time.astype('datetime64[s]').astype(int)) if hasattr(current_time, 'astype') else int(current_time),
                            "entry_price": sig.entry_price,
                            "stop_loss": sig.stop_loss,
                            "take_profit": sig.take_profit,
                            "timeframe": sig.timeframe,  # [12.10] see risk/engine.py's note
                            "confluence_score": sig.confluence_score,
                            "score_breakdown": sig.metadata.get("score_breakdown", {}),
                            "metadata": sig.metadata,
                            # Carry confirmations list so engine.py's _create_position()
                            # populates the Entry Confirmations panel in the frontend.
                            "confirmations": sig.metadata.get("confirmations", []),
                        }
                        sigs.append(sig_dict)
                        replay.signal(req.symbol, sig_dict)

                        # Runaway guard. A strategy that re-detects the same setup
                        # every bar can emit tens of thousands of signals, and the
                        # cost is not just a bad result: the log grows without
                        # bound, memory climbs, and the event loop starves until
                        # the whole backend stops answering — which is what
                        # "backtest never finishes / bot goes offline" actually was.
                        #
                        # 5,000 is far above any legitimate run (the widest real
                        # one so far produced 77 on 17k bars), so hitting this
                        # means something is wrong, and stopping with a partial
                        # result plus a loud log beats hanging the process.
                        if len(sigs) >= MAX_SIGNALS_PER_RUN:
                            logger.error(
                                f"[BACKTEST] {req.symbol}: signal cap {MAX_SIGNALS_PER_RUN} hit at "
                                f"bar {i}/{len(primary_times)} — aborting signal generation. "
                                f"This almost always means a setup is re-arming every bar rather "
                                f"than once. Check the run log for a repeated neckline/zone."
                            )
                            bot_service.log_system_event(
                                f"Backtest {req.symbol}: signal cap hit ({MAX_SIGNALS_PER_RUN}) — "
                                f"stopped early to protect the server.",
                                category="BACKTEST", level="ERROR",
                            )
                            break

                replay.leg_done(req.symbol)
                return sigs

            signals = await generate_signals_simulated()
            candles = indexed_by_tf.get("M5", indexed_by_tf[primary_tf])

            merged_risk_config = {
                "risk_per_trade_pct": req.risk_per_trade_pct,
                "min_rr": req.min_rr,
                "tp_count": req.tp_count,
                "tp1_rr": req.tp1_rr,
                "tp2_rr": req.tp2_rr,
                "tp3_rr": req.tp3_rr,
                "tp4_rr": req.tp4_rr,
                "tp5_rr": req.tp5_rr,
                "tp_splits": req.tp_splits,
                "be_trigger_rr": req.be_trigger_rr,
                "be_buffer_pips": req.be_buffer_pips,
                "be_buffer_atr_mult": getattr(req, "be_buffer_atr_mult", 0.0) if hasattr(req, "be_buffer_atr_mult") else req.risk_config.get("be_buffer_atr_mult", 0.0),
                "trail_method_tp2": req.trail_method_tp2,
                "trail_method_tp3": req.trail_method_tp3,
                "trail_method_tp4": req.trail_method_tp4,
                "trail_method_tp5": req.trail_method_tp5,
                "atr_trail_multiplier": req.atr_trail_multiplier,
                "trail_pips": req.trail_pips,
                "session_filter_enabled": req.session_filter_enabled,
                "multi_position_mode": req.tp_count > 1,
                "max_daily_drawdown_pct": req.max_daily_drawdown_pct,
                "max_weekly_drawdown_pct": req.max_weekly_drawdown_pct,
                "max_concurrent_positions": req.max_concurrent_positions,
                "max_positions_per_symbol": req.max_positions_per_symbol,
                "max_daily_trades": req.max_daily_trades,
                "target_profit_enabled": req.target_profit_enabled,
                "max_daily_profit": req.max_daily_profit,
                "max_weekly_profit": req.max_weekly_profit,
                "manual_bias_overrides": req.manual_bias_overrides,
                "prop_firm": req.prop_firm,
                "max_risk_hard_cap_pct": req.max_risk_hard_cap_pct,
                # Simulation costs and wick simulation.
                # None values are passed through deliberately: the engine reads
                # them as "unset" and sources the value from broker data
                # (live MT5 -> asset-class default). Explicit numbers still win.
                "slippage_pips": req.slippage_pips,
                "commission_per_lot": req.commission_per_lot,
                "spread_pips": req.spread_pips,
                "swap_long_per_lot_per_day": req.swap_long_per_lot_per_day,
                "swap_short_per_lot_per_day": req.swap_short_per_lot_per_day,
                "stops_level_pips": req.stops_level_pips,
                "simulate_wicks": req.simulate_wicks,
                # Strategy attribution: signal dicts carry no strategy_id, so the
                # engine falls back to this when stamping trades (was "UNKNOWN"
                # on every saved grouped_trade).
                "strategy_id": req.strategy_id,
                # [2.15] Per-strategy TP1 RR override — see DriftJumpAlphaParams.tp1_rr_override.
                # [17.1] Single-symbol runs resolve through the same slot map as
                # live, so a backtest and a live trade on the same symbol+strategy
                # read their target from identical code.
                # [18.3] Measured defaults seeded first; the request's own
                # tp1_rr wins, so the UI still controls the run.
                "tp1_rr_overrides_by_slot": {
                    **get_slot_tp1_rr_defaults(),
                    f"{req.symbol.upper()}|{req.strategy_id}": req.tp1_rr,
                },
                "tp_count_overrides_by_slot": {
                    f"{req.symbol.upper()}|{req.strategy_id}": req.tp_count
                },
                "tp1_rr_overrides_by_strategy": (
                    {req.strategy_id: req.strategy_params["tp1_rr_override"]}
                    if req.strategy_params.get("tp1_rr_override") is not None else {}
                ),
                # [Phase 2 sizing-truth] only merged when explicitly set — None
                # left out entirely so risk/engine.py's own None-means-default
                # resolution (matching RiskParams) is what actually applies.
                **{
                    k: v for k, v in {
                        "max_margin_utilisation_pct": req.max_margin_utilisation_pct,
                        "min_deployable_risk_pct": req.min_deployable_risk_pct,
                        "min_stop_spread_multiple": req.min_stop_spread_multiple,
                        "confluence_risk_tiers": req.confluence_risk_tiers,
                        "reject_below_confluence": req.reject_below_confluence,
                        "post_split_risk_tolerance_pct": req.post_split_risk_tolerance_pct,
                        "exit_slippage_pips": req.exit_slippage_pips,
                        "open_risk_weight": req.open_risk_weight,
                        "allow_pyramiding": req.allow_pyramiding,
                        "min_bars_between_entries": req.min_bars_between_entries,
                        "max_account_leverage": req.max_account_leverage,
                        "min_sl_pips": req.min_sl_pips,
                        "sizing_basis": req.sizing_basis,
                        "be_spread_multiple": req.be_spread_multiple,
                        "trail_require_be_first": req.trail_require_be_first,
                        "be_mode": req.be_mode,
                        "be_trigger_tp_level": req.be_trigger_tp_level,
                        "trail_method_tp1": req.trail_method_tp1,
                        "trail_mode": req.trail_mode,
                        "trail_trigger_rr": req.trail_trigger_rr,
                        "trail_trigger_tp_level": req.trail_trigger_tp_level,
                        "tp_volume_pcts": req.tp_volume_pcts,
                        "max_cluster_risk_pct": req.max_cluster_risk_pct,
                        "max_net_direction_risk_pct": req.max_net_direction_risk_pct,
                        "symbol_cluster_overrides": req.symbol_cluster_overrides,
                        "strategy_risk_budget_pct": req.strategy_risk_budget_pct,
                    }.items() if v is not None
                },
                **req.risk_config,
            }

            # ── Per-strategy exit defaults ────────────────────────────────
            # Trailing / break-even used to be one global setting for all seven
            # strategies. Measurement says that cannot be right: the 15-cell
            # trailing sweep improved 10 cells and made 5 WORSE, with nearly all
            # the gain in NYOpenRetest (+1,765 PnL) while DriftJumpAlpha lost
            # 1,299 on Crash 1000. So the unit is the strategy.
            #
            # Applied only where the request left the field unset, so anything
            # the user explicitly chose still wins.
            from backend.strategies.strategy_defaults import (
                get_strategy_defaults, get_strategy_evidence,
            )
            _sdefaults = get_strategy_defaults(req.strategy_id)
            _applied = {}
            for _k, _v in _sdefaults.items():
                if _k == "session_filter_enabled":
                    continue  # lives on the strategy params object, applied below
                if getattr(req, _k, None) is None and _k not in (req.risk_config or {}):
                    merged_risk_config[_k] = _v
                    _applied[_k] = _v
            if _applied:
                logger.info(
                    f"[BACKTEST] Applied {req.strategy_id} exit defaults: {_applied} "
                    f"| {get_strategy_evidence(req.strategy_id)}"
                )
            merged_risk_config["_strategy_defaults_applied"] = _applied

            current_state = await _get_state()
            # [T2.1] The simulation reports its own progress across 35-90 via
            # this callback. Previously the bar jumped straight to 95 here and
            # then sat there for the whole simulation with nothing emitted.
            #
            # [B4] `run_backtest` hands the engine to `asyncio.to_thread`, so
            # this callback runs on a WORKER THREAD. `asyncio.create_task()`
            # requires a running loop in the calling thread and raises
            # `RuntimeError: no running event loop` there — which the old bare
            # `except Exception: pass` swallowed, so not one progress frame was
            # ever broadcast during a simulation. The bar sat at 35 until the
            # thread returned and then jumped to 100.
            #
            # Capture the loop here (we are still on it) and hand work back to
            # it with `run_coroutine_threadsafe`, which is the thread-safe entry
            # point. Failures are logged rather than silently dropped.
            # [18.4] Simulation progress via a real BacktestProgress phase.
            #
            # A plain callback cannot work here: runner.run_backtest tests
            # `progress_phase is None` to decide whether it owns the progress
            # scale, and calls `.set()` on it. Passing a bare function raised
            # NameError and killed every run.
            #
            # `_owned_scale` is also what stops run_backtest broadcasting
            # stage="complete" before this route has assembled the result —
            # the actual cause of "results only appear after a refresh".
            from backend.services.backtest_progress import BacktestProgress

            _progress = BacktestProgress(
                user_id=current_user.id,
                broadcast=lambda uid, payload: ws_manager.broadcast_to_user(uid, payload),
                save_state=lambda payload: _save_state(
                    {**USER_BACKTEST_STATE.get(current_user.id, {}), "progress": payload}
                ),
            )
            PH_SIM = _progress.phase(35, 90, "Simulating trades...")
            # `.note` is a pure assignment inside the worker thread; this task
            # does the actual I/O on the event loop.
            _pump_task = asyncio.create_task(_progress.pump())

            current_state["progress"] = {"stage": "Simulating trades...", "pct": 35}
            await _save_state(current_state)
            results = await run_backtest(
                user_id=current_user.id,
                strategy_id=req.strategy_id,
                symbol=req.symbol,
                candles=candles,
                signals=signals,
                risk_config=merged_risk_config,
                initial_balance=req.initial_balance,
                candles_m15=candles_m15_idx,
                candles_m5=candles_m5_idx,
                save_mode="DISCARD",
                strategy=engine,  # [Phase 14 B2.3] enables on_position_bar hook
                progress_phase=PH_SIM,
                # [B6] H1 context for the trade viewer's higher-timeframe pane.
                candles_h1=indexed_by_tf.get("H1"),
            )

            # [18.4] Stop the pump as soon as the engine returns. Leaving it
            # running would keep polling for the life of the request, and
            # `close()` before the route emits its own stage="complete" is what
            # guarantees nothing else claims the run is finished first.
            _progress.close()
            _pump_task.cancel()

            report = results.get("report")
            elapsed = (_time.time() - bt_start) * 1000
            pnl = results.get('final_balance', 0) - req.initial_balance
            wr = (report.win_rate if report else 0) * 100
            bot_service.log_system_event(
                f"Backtest complete: {req.symbol} | {results['total_trades']} trades | P&L=${pnl:.2f} | WR={wr:.1f}%",
                category="BACKTEST"
            )

            import numpy as _np

            def _sanitize(obj):
                import math
                if isinstance(obj, dict):
                    return {k: _sanitize(v) for k, v in obj.items()}
                elif isinstance(obj, (list, tuple)):
                    return [_sanitize(v) for v in obj]
                elif isinstance(obj, (_np.bool_, bool)):
                    return bool(obj)
                elif isinstance(obj, (_np.integer, int)):
                    return int(obj)
                elif isinstance(obj, (_np.floating, float)):
                    if math.isnan(obj) or math.isinf(obj):
                        return None
                    return float(obj)
                elif isinstance(obj, _np.ndarray):
                    return _sanitize(obj.tolist())
                elif isinstance(obj, (int, float)) and pd.isna(obj):
                    return None
                elif isinstance(obj, pd.Timestamp) or hasattr(obj, 'isoformat'):
                    return obj.isoformat()
                elif isinstance(obj, (str, type(None))):
                    return obj
                return str(obj)

            response = {
                "backtest_id": results["backtest_id"],
                "initial_balance": results["initial_balance"],
                "final_balance": results["final_balance"],
                "total_trades": results["total_trades"],
                "total_signals": results.get("total_signals", 0),
                "invalid_signals": results.get("invalid_signals", 0),
                "equity_curve": results.get("equity_curve", []),
                "trades": results.get("trades", []),
                "grouped_trades": results.get("grouped_trades", []),
                "run_logs": _filter_run_logs(getattr(engine, 'run_logs', [])),
                # [I1] The funnel is computed by the engine (self.rejection_funnel)
                # and was silently dropped here — the frontend's rejection-funnel
                # panel (Backtester.jsx) has never had data to render for a
                # single-symbol run. The portfolio route already includes this key;
                # this brings the single-symbol route to parity with it.
                "rejection_funnel": results.get("rejection_funnel", {}),
                # [I4] Every signal the strategy emitted that did NOT become a trade,
                # with the gate that stopped it — answers "was there a setup that day"
                # without re-running. Capped upstream in the engine at 500.
                "blocked_signals": results.get("blocked_signals", []),
                # [2.24] Distinguishes a drawdown-latched stretch from "no setups".
                "circuit_breaker_summary": results.get("circuit_breaker_summary", {}),
                # params_snapshot records the REQUEST as submitted. Merge in the
                # cost model the engine actually resolved, so a saved backtest
                # records what costs it assumed and where each came from
                # (USER vs MT5 vs ASSET_CLASS_DEFAULT) — otherwise an "auto"/None
                # request is unreproducible after the fact.
                "params_snapshot": {
                    **(req.model_dump() if hasattr(req, "model_dump") else req.dict()),
                    "resolved_cost_model": results.get("cost_model", {}),
                },
                "cost_model": results.get("cost_model", {}),
                "log_session_id": log_session_id,
                "report": {
                    "win_rate": report.win_rate if report else 0,
                    "profit_factor": report.profit_factor if report else 0,
                    "sharpe_ratio": report.sharpe_ratio if report else 0,
                    "sortino_ratio": report.sortino_ratio if report else 0,
                    "max_drawdown_pct": report.max_drawdown_pct if report else 0,
                    "max_drawdown_pct_of_peak": getattr(report, "max_drawdown_pct_of_peak", 0) if report else 0,
                    "total_pnl": report.total_pnl if report else 0,
                    "expectancy_r": report.expectancy_r if report else 0,
                    "tp1_hit_rate": report.tp1_hit_rate if report else 0,
                    "tp2_hit_rate": report.tp2_hit_rate if report else 0,
                    "tp3_hit_rate": report.tp3_hit_rate if report else 0,
                    "tp4_hit_rate": report.tp4_hit_rate if report else 0,
                    "tp5_hit_rate": report.tp5_hit_rate if report else 0,
                    "sl_hit_rate": report.sl_hit_rate if report else 0,
                    "trail_hit_rate": report.trail_hit_rate if report else 0,
                    "be_hit_rate": report.be_hit_rate if report else 0,
                    "london_win_rate": report.london_win_rate if report else 0,
                    "ny_win_rate": report.ny_win_rate if report else 0,
                    "overlap_win_rate": report.overlap_win_rate if report else 0,
                    "max_consecutive_wins": report.max_consecutive_wins if report else 0,
                    "max_consecutive_losses": report.max_consecutive_losses if report else 0,
                    "bias_stats": report.bias_stats if report and hasattr(report, "bias_stats") else {},
                    "confluence_stats": report.confluence_stats if report and hasattr(report, "confluence_stats") else {},
                },
            }
            
            # [Phase 13 section C.7] The continuous per-leg bar series, so the
            # finished run can be scrubbed end-to-end. Distinct from each
            # trade's own chart_data, which is only a +/-30-bar slice capped at
            # 500 bars and cannot show the run as a whole.
            try:
                response["replay"] = replay.series_payload()
                replay.done()
                logger.info(f"[replay] single run complete: {replay.stats}")
            except Exception as e:
                logger.warning(f"[replay] series attach failed (chart only, run unaffected): {e}")

            # [T2.1] _sanitize recursively walks every bar of every trade's
            # chart_data, chart_data_m15, chart_data_m5 and smc_data plus the
            # replay series. On a large run that is tens of millions of Python
            # objects — seconds to minutes of solid CPU. Run it in a thread so
            # the event loop keeps answering /backtest_status.
            sanitized = await asyncio.to_thread(_sanitize, response)

            current_state = await _get_state()
            current_state["status"] = "complete"
            current_state["progress"] = {"stage": "complete", "pct": 100}
            current_state["result"] = sanitized
            
            # Create a stripped payload for the frontend to prevent UI freezing.
            #
            # [T2.1] This used to deepcopy the whole sanitized result and then
            # delete the heavy keys from the copy — i.e. duplicate hundreds of
            # MB purely to throw most of it away, synchronously, on the event
            # loop. Build the trimmed view directly instead: shallow-copy each
            # trade dict without the chart keys and share everything else.
            _HEAVY = ("chart_data", "chart_data_h1", "chart_data_m15", "chart_data_m5")
            # Never read by the frontend (grepped: zero references). Free to drop.
            _UNUSED = ("gate_vector", "confluence_tags")
            # Read only by the expanded-trade panel. Measured on a 994-group
            # DriftJumpAlpha run: the payload is 22.0 MB with these in and
            # 2.6 MB without, and the browser blocks 1,229 ms vs 105 ms just
            # parsing it — before any of it renders. The bulk is not the SMC
            # overlay, it is `original_signal` + `entry_confirmations` repeated
            # on every group AND every leg inside `sub_trades`.
            #
            # Dropped only above the threshold, so ordinary runs keep full
            # fidelity and only the runs that would otherwise hang the tab lose
            # the overlay detail. `smc_data` is already absent from the Redis
            # restore path (see _slim_state), so shedding it here makes the two
            # delivery paths agree rather than introducing a new gap.
            _PANEL_ONLY = ("smc_data", "original_signal", "entry_confirmations")
            _LEAN_GROUP_THRESHOLD = 300

            ws_payload = {k: v for k, v in sanitized.items() if k != "replay"}
            _groups = ws_payload.get("grouped_trades")
            if isinstance(_groups, list):
                _drop = _HEAVY + _UNUSED
                if len(_groups) > _LEAN_GROUP_THRESHOLD:
                    _drop = _drop + _PANEL_ONLY
                    logger.info(
                        f"[BT-WS] {len(_groups)} groups > {_LEAN_GROUP_THRESHOLD}: "
                        f"sending lean payload (overlay detail refetched on demand)"
                    )

                def _slim_trade(t):
                    if not isinstance(t, dict):
                        return t
                    out = {k: v for k, v in t.items() if k not in _drop}
                    subs = out.get("sub_trades")
                    if isinstance(subs, list):
                        # The legs carry their own copies of the same heavy
                        # fields — stripping only the group left most of the
                        # weight behind.
                        out["sub_trades"] = [
                            {k: v for k, v in s.items() if k not in _drop}
                            if isinstance(s, dict) else s
                            for s in subs
                        ]
                    return out

                ws_payload["grouped_trades"] = [_slim_trade(t) for t in _groups]
            # (replay is already excluded above — it is megabytes and the
            # client already has it from the live stream; it stays in saved
            # state and is fetched via /replay when needed.)
            
            # Send the run logs immediately as part of the WS payload
            await ws_manager.broadcast_to_user(current_user.id, {"type": "backtest_progress", "stage": "complete", "result": ws_payload})
            
            # Now save to state (which writes to Redis and might block)
            await _save_state(current_state)
            
        except Exception as e:
            current_state = await _get_state()
            current_state["status"] = "error"
            current_state["progress"] = {"stage": "error", "message": str(e), "pct": 0}
            current_state["result"] = None
            
            try:
                await ws_manager.broadcast_to_user(current_user.id, {"type": "backtest_error", "message": str(e)})
            except: pass

            await _save_state(current_state)
            
            import traceback
            logger.error(f"Backtest error: {e}\n{traceback.format_exc()}")
            from backend.api.websocket import manager as ws_manager
            from backend.services.bot_service import bot_service
            bot_service.log_system_event(f"Backtest failed: {e!s}", category="BACKTEST", level="ERROR")
            try:
                await ws_manager.broadcast_to_user(current_user.id, {"type": "backtest_error", "message": str(e)})
            except: pass
        finally:
            log_hub.end_session(log_session_id)

    background_tasks.add_task(_run_backtest_task)
    return {"status": "started", "message": "Backtest queued and running in the background.", "log_session_id": log_session_id}


@router.post("/portfolio_backtest")
async def run_portfolio_backtest_endpoint(
    req: PortfolioBacktestRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
):
    """
    Run a portfolio (multi-symbol, multi-strategy) backtest in the background.
    All symbols share the same global risk parameters, tracked on a single timeline.
    """
    state = await get_backtest_status(current_user)
    if state and state.get("status") == "running":
        raise HTTPException(status_code=400, detail="A backtest is already running for this user.")
        
    from backend.services.bot_service import bot_service
    sym_list = [s.symbol for s in req.symbols]
    bot_service.log_system_event(f"Portfolio backtest queued: {', '.join(sym_list)}", category="BACKTEST")

    # [Phase 13 section G] See the single-symbol route — same reasoning.
    log_session_id = log_hub.start_session(
        f"Portfolio: {', '.join(sym_list[:4])}{'...' if len(sym_list) > 4 else ''}",
        kind="portfolio_backtest",
    )

    async def _run_portfolio_task():
        global USER_BACKTEST_STATE
        initial_state = {"status": "running", "progress": {"stage": "Fetching data...", "pct": 0}, "result": None}

        async def _save_state(state):
            USER_BACKTEST_STATE[current_user.id] = state
            if HAS_REDIS and redis_client and redis_client.redis:
                try:
                    import redis.exceptions
                    await redis_client.redis.set(f"backtest_state:{current_user.id}", json.dumps(_redis_safe_state(state), default=str), ex=3600)
                except Exception as e:
                    logger.warning(f"[PORTFOLIO_BT] Redis save failed: {e}")

        async def _get_state():
            if HAS_REDIS and redis_client and redis_client.redis:
                try:
                    import redis.exceptions
                    data = await redis_client.redis.get(f"backtest_state:{current_user.id}")
                    if data:
                        return json.loads(data)
                except Exception:
                    pass
            return USER_BACKTEST_STATE.get(current_user.id, initial_state.copy())

        await _save_state(initial_state)
        try:
            import asyncio
            import time as _time
            import pandas as pd

            from backend.api.websocket import manager as ws_manager
            from backend.backtester.portfolio_engine import PortfolioBacktestEngine
            from backend.core.config_schema import InstrumentSettings, UserConfigV2
            from backend.mt5.data_fetcher import DataFetcher, DataFetchError
            from backend.strategies.registry import get_strategy
            from backend.services.bot_service import bot_service

            bt_start = _time.time()
            logger.info(f"═══ PORTFOLIO BACKTEST START ═══ {sym_list} | user={current_user.email}")

            TF_META = {
                "M1":  {"np_td": (1,  'm'), "warmup_days": 1,   "window": 500},
                "M5":  {"np_td": (5,  'm'), "warmup_days": 5,   "window": 500},
                "M15": {"np_td": (15, 'm'), "warmup_days": 10,  "window": 300},
                "M30": {"np_td": (30, 'm'), "warmup_days": 15,  "window": 200},
                "H1":  {"np_td": (1,  'h'), "warmup_days": 30,  "window": 200},
                "H4":  {"np_td": (4,  'h'), "warmup_days": 150, "window": 200},
                "D1":  {"np_td": (1,  'D'), "warmup_days": 365, "window": 100},
            }
            TF_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}

            # Build shared risk config
            merged_risk_config = {

                "risk_per_trade_pct": req.risk_per_trade_pct,
                "min_rr": req.min_rr,
                "tp_count": req.tp_count,
                "tp1_rr": req.tp1_rr, "tp2_rr": req.tp2_rr, "tp3_rr": req.tp3_rr,
                "tp4_rr": req.tp4_rr, "tp5_rr": req.tp5_rr,
                "tp_splits": req.tp_splits,
                "be_trigger_rr": req.be_trigger_rr,
                "be_buffer_pips": req.be_buffer_pips,
                "trail_method_tp2": req.trail_method_tp2, "trail_method_tp3": req.trail_method_tp3,
                "trail_method_tp4": req.trail_method_tp4, "trail_method_tp5": req.trail_method_tp5,
                "atr_trail_multiplier": req.atr_trail_multiplier,
                "trail_pips": req.trail_pips,
                "session_filter_enabled": req.session_filter_enabled,
                "multi_position_mode": req.tp_count > 1,
                "max_daily_drawdown_pct": req.max_daily_drawdown_pct,
                "max_weekly_drawdown_pct": req.max_weekly_drawdown_pct,
                "max_concurrent_positions": req.max_concurrent_positions,
                "max_positions_per_symbol": req.max_positions_per_symbol,
                "max_daily_trades": req.max_daily_trades,
                "target_profit_enabled": req.target_profit_enabled,
                "max_daily_profit": req.max_daily_profit,
                "max_weekly_profit": req.max_weekly_profit,
                "prop_firm": req.prop_firm,
                "max_risk_hard_cap_pct": req.max_risk_hard_cap_pct,
                # Simulation costs and wick simulation.
                # None -> engine sources the value from broker data
                # (live MT5 -> asset-class default). Explicit numbers still win.
                "slippage_pips": req.slippage_pips,
                "commission_per_lot": req.commission_per_lot,
                "spread_pips": req.spread_pips,
                "swap_long_per_lot_per_day": req.swap_long_per_lot_per_day,
                "swap_short_per_lot_per_day": req.swap_short_per_lot_per_day,
                "stops_level_pips": req.stops_level_pips,
                "simulate_wicks": req.simulate_wicks,
                # [2.15] Per-strategy TP1 RR override — see DriftJumpAlphaParams.tp1_rr_override.
                "tp1_rr_overrides_by_strategy": {
                    sym_cfg.strategy_id: sym_cfg.strategy_params["tp1_rr_override"]
                    for sym_cfg in req.symbols
                    if sym_cfg.strategy_params.get("tp1_rr_override") is not None
                },
                # [17.1] Per-ROW (symbol+strategy) R:R and TP count. More
                # specific than the per-strategy map above and takes priority,
                # so one portfolio run can hold several different targets.
                # [18.3] Measured defaults, overridden per row where the UI
                # set one. A row left blank inherits the measured value.
                "tp1_rr_overrides_by_slot": {
                    **get_slot_tp1_rr_defaults(),
                    **{f"{sym_cfg.symbol.upper()}|{sym_cfg.strategy_id}": sym_cfg.tp1_rr
                       for sym_cfg in req.symbols if sym_cfg.tp1_rr is not None},
                },
                "tp_count_overrides_by_slot": {
                    f"{sym_cfg.symbol.upper()}|{sym_cfg.strategy_id}": sym_cfg.tp_count
                    for sym_cfg in req.symbols if sym_cfg.tp_count is not None
                },
                # [Phase 2 sizing-truth] only merged when explicitly set — None
                # left out so risk/engine.py's own default resolution applies.
                **{
                    k: v for k, v in {
                        "max_margin_utilisation_pct": req.max_margin_utilisation_pct,
                        "min_deployable_risk_pct": req.min_deployable_risk_pct,
                        "min_stop_spread_multiple": req.min_stop_spread_multiple,
                        "confluence_risk_tiers": req.confluence_risk_tiers,
                        "reject_below_confluence": req.reject_below_confluence,
                        "post_split_risk_tolerance_pct": req.post_split_risk_tolerance_pct,
                        "exit_slippage_pips": req.exit_slippage_pips,
                        "open_risk_weight": req.open_risk_weight,
                        "allow_pyramiding": req.allow_pyramiding,
                        "min_bars_between_entries": req.min_bars_between_entries,
                        "max_account_leverage": req.max_account_leverage,
                        "min_sl_pips": req.min_sl_pips,
                        "sizing_basis": req.sizing_basis,
                        "be_spread_multiple": req.be_spread_multiple,
                        "trail_require_be_first": req.trail_require_be_first,
                        "be_mode": req.be_mode,
                        "be_trigger_tp_level": req.be_trigger_tp_level,
                        "trail_method_tp1": req.trail_method_tp1,
                        "trail_mode": req.trail_mode,
                        "trail_trigger_rr": req.trail_trigger_rr,
                        "trail_trigger_tp_level": req.trail_trigger_tp_level,
                        "tp_volume_pcts": req.tp_volume_pcts,
                        "max_cluster_risk_pct": req.max_cluster_risk_pct,
                        "max_net_direction_risk_pct": req.max_net_direction_risk_pct,
                        "symbol_cluster_overrides": req.symbol_cluster_overrides,
                        "strategy_risk_budget_pct": req.strategy_risk_budget_pct,
                    }.items() if v is not None
                },
            }


            # ── Fetch data & generate signals per symbol ──
            portfolio_data = {}
            portfolio_data_m15 = {}
            portfolio_data_m5 = {}
            portfolio_signals = {}
            # [12.8/Part14] cache_key (usually slot_id) -> real symbol, passed
            # to PortfolioBacktestEngine.run() so it can resolve costs/pip-size
            # against the real symbol while keying its internal per-slot dicts
            # by cache_key — this is what lets two rows share `symbol` under
            # different strategies without colliding.
            symbol_map: dict[str, str] = {}
            portfolio_run_logs = []  # Aggregated across all per-symbol strategy engines
            total_symbols = len(req.symbols)

            # [Phase 13 section C.5] Replay stream. One leg per SLOT, not per
            # symbol: Phase 12 allows the same symbol under two strategies, and
            # keying tabs by symbol alone would merge two independent legs into
            # one chart — the exact aliasing the "independent legs" principle
            # forbids. Announced up front so every tab exists before the first
            # bar arrives, and the tab strip doesn't reflow mid-run.
            replay = ReplayStreamer(ws_manager, current_user.id, mode="portfolio",
                                   enabled=bool(getattr(req, "replay_enabled", True)))
            replay.init([
                {
                    "slot_id": c.slot_id or f"{c.symbol}::{c.strategy_id}",
                    "symbol": c.symbol,
                    "strategy_id": c.strategy_id,
                    "timeframe": None,  # filled in by replay_leg_start
                }
                for c in req.symbols
            ])

            for sym_idx, sym_cfg in enumerate(req.symbols):
                sym = sym_cfg.symbol
                strat_id = sym_cfg.strategy_id
                # [12.8] Falls back to a deterministic symbol+strategy composite
                # when the caller doesn't supply slot_id — still collision-free
                # for the common "same symbol, different strategy" case.
                slot_key = sym_cfg.slot_id or f"{sym}::{strat_id}"
                symbol_map[slot_key] = sym

                pct_base = int((sym_idx / total_symbols) * 80)
                current_state = await _get_state()
                current_state["progress"] = {"stage": f"Fetching {sym} ({sym_idx + 1}/{total_symbols})...", "pct": pct_base}
                await _save_state(current_state)
                await ws_manager.broadcast_to_user(current_user.id, {"type": "backtest_progress", **current_state["progress"]})

                # Check for cancellation
                if current_state.get("status") == "cancelled":
                    raise Exception("Portfolio Backtest Cancelled")

                # Build strategy config for this symbol
                config = UserConfigV2()
                config.risk.min_rr = req.min_rr
                config.risk.risk_per_trade_pct = req.risk_per_trade_pct
                for k, v in sym_cfg.strategy_params.items():
                    if strat_id == "APA_v1" and hasattr(config.apa, k):
                        setattr(config.apa, k, v)
                    elif strat_id == "VWAP_v1" and hasattr(config.vwap, k):
                        setattr(config.vwap, k, v)
                    elif strat_id == "DriftJumpAlpha_v1" and hasattr(config.drift_jump_alpha, k):
                        setattr(config.drift_jump_alpha, k, v)
                    elif strat_id == "CRT_v1" and hasattr(config.crt, k):
                        setattr(config.crt, k, v)
                    elif strat_id == "HTFFVGFlip_v1" and hasattr(config.htf_fvg_flip, k):
                        setattr(config.htf_fvg_flip, k, v)
                    elif strat_id == "BiasIFVG_v1" and hasattr(config.bias_ifvg, k):
                        setattr(config.bias_ifvg, k, v)
                    elif strat_id == "NYOpenRetest_v1" and hasattr(config.ny_open_retest, k):
                        setattr(config.ny_open_retest, k, v)
                # Sync VWAP's internal daily trade cap with the CB limit,
                # unless the user explicitly provided one in strategy_params.
                if strat_id == "VWAP_v1" and "max_trades_per_day" not in sym_cfg.strategy_params:
                    config.vwap.max_trades_per_day = req.max_daily_trades

                config.instrument_settings = [InstrumentSettings(symbol=sym, strategy_id=strat_id)]
                if strat_id == "SMC_v1":
                    strat_id = "APA_v1"
                engine_class = get_strategy(strat_id)
                strategy_engine = engine_class(config)
                strategy_engine.is_backtesting = True
                strategy_engine.gates.enabled = True  # [B7] see the single-symbol path

                required_tfs = strategy_engine.get_required_timeframes()
                sorted_tfs = sorted(required_tfs, key=lambda t: TF_MINUTES.get(t, 999))
                primary_tf = sorted_tfs[0]

                # Fetch data
                candles_by_tf = {}
                try:
                    if req.start_date and req.end_date:
                        start_dt = datetime.fromisoformat(req.start_date)
                        end_dt = datetime.fromisoformat(req.end_date)
                        for tf in required_tfs:
                            meta = TF_META.get(tf, TF_META["M5"])
                            tf_start = start_dt - pd.Timedelta(days=meta["warmup_days"])
                            candles_by_tf[tf] = await DataFetcher.get_data_range(sym, tf, tf_start, end_dt)
                    else:
                        for tf in required_tfs:
                            candles_by_tf[tf] = await DataFetcher.get_historical_data(sym, tf, count=req.candle_count)
                except DataFetchError as e:
                    logger.error(f"[PORTFOLIO_BT] Data fetch failed for {sym}: {e}")
                    await ws_manager.broadcast_to_user(current_user.id, {"type": "backtest_error", "message": f"Data failed for {sym}: {e}"})
                    return

                def _index_candles(df):
                    if 'time' in df.columns:
                        return df.set_index(pd.to_datetime(df['time'], unit='s'))
                    return df

                indexed_by_tf = {tf: _index_candles(df).sort_index() for tf, df in candles_by_tf.items()}
                primary_sorted = indexed_by_tf[primary_tf]
                primary_times = primary_sorted.index.values
                prev_time_by_tf = {tf: None for tf in required_tfs}

                # Generate signals for this symbol
                sym_signals = []
                import numpy as np
                # `time` is used by the elapsed-time yield below. The
                # single-symbol route imports it in its own scope; this one did
                # not, so every portfolio run would have died with a NameError
                # on the first bar. Caught by pyflakes, not by a test — there is
                # no automated portfolio-backtest coverage.
                import time

                # Fires the frontend's tab auto-advance: the active tab follows
                # replay_leg_start unless the user has manually pinned one.
                replay.leg_start(slot_key, total_bars=max(0, len(primary_times) - 300))

                # Same hoisting as the single-symbol route — see the comment
                # block there. This loop runs once PER LEG, so the cost of
                # recomputing these per bar multiplies by the leg count.
                _rep_o = primary_sorted["open"].to_numpy(dtype=float)
                _rep_h = primary_sorted["high"].to_numpy(dtype=float)
                _rep_l = primary_sorted["low"].to_numpy(dtype=float)
                _rep_c = primary_sorted["close"].to_numpy(dtype=float)
                _rep_t = primary_times.astype("datetime64[s]").astype("int64")
                _warmup_cutoff = (
                    np.datetime64(datetime.fromisoformat(req.start_date))
                    if req.start_date else None
                )
                _tf_times_by_tf = {tf: indexed_by_tf[tf].index.values for tf in required_tfs}
                _last_yield = time.monotonic()

                for i in range(300, len(primary_times)):
                    # Elapsed-time yield rather than every-50-bars — see the
                    # single-symbol route. This is what keeps the WebSocket and
                    # the health poll alive through a long run.
                    _now = time.monotonic()
                    if (_now - _last_yield) > 0.04:
                        await asyncio.sleep(0)
                        _last_yield = time.monotonic()

                    if i % 600 == 0:
                        cur_state = await _get_state()
                        if cur_state.get("status") == "cancelled":
                            raise Exception("Portfolio Backtest Cancelled")
                        
                        # Add progress update here for portfolio backtest
                        pct_within = i / len(primary_times)
                        pct = int(((sym_idx + pct_within) / total_symbols) * 80)
                        cur_state["progress"] = {"stage": f"Simulating {sym} ({sym_idx + 1}/{total_symbols})...", "pct": pct}
                        await _save_state(cur_state)
                        try:
                            asyncio.create_task(ws_manager.broadcast_to_user(current_user.id, {
                                "type": "backtest_progress", **cur_state["progress"]
                            }))
                        except: pass

                    current_time = primary_times[i]
                    is_warmup = _warmup_cutoff is not None and current_time < _warmup_cutoff

                    if not is_warmup:
                        replay.bar(slot_key, {
                            "time": int(_rep_t[i]),
                            "open": float(_rep_o[i]),
                            "high": float(_rep_h[i]),
                            "low": float(_rep_l[i]),
                            "close": float(_rep_c[i]),
                        })

                    sig = None

                    for tf in required_tfs:
                        meta = TF_META.get(tf, TF_META["M5"])
                        sorted_tf = indexed_by_tf[tf]
                        tf_times = _tf_times_by_tf[tf]

                        if tf == primary_tf:
                            tf_end = i
                            last_tf_time = primary_times[i]
                        else:
                            np_td, np_unit = meta["np_td"]
                            cutoff = current_time - np.timedelta64(np_td, np_unit)
                            tf_end = int(np.searchsorted(tf_times, cutoff, side='right'))
                            last_tf_time = tf_times[tf_end - 1] if tf_end > 0 else None

                        # Slice only once the timeframe has advanced — see the
                        # single-symbol route for the measurement (2.1x).
                        if last_tf_time is None or last_tf_time == prev_time_by_tf[tf]:
                            continue

                        slice_tf = sorted_tf.iloc[max(0, tf_end - meta["window"]):tf_end]
                        if len(slice_tf) < 20:
                            continue

                        s = await strategy_engine.on_bar(sym, tf, slice_tf)
                        if s:
                            sig = s
                        prev_time_by_tf[tf] = last_tf_time

                    if sig and not is_warmup:
                        sig_time = int(current_time.astype('datetime64[s]').astype(int)) if hasattr(current_time, 'astype') else int(current_time)
                        # [12.5/12.6/12.8] Stamp slot_id onto the signal's own
                        # metadata so risk/engine.py's slot-aware checks and
                        # CircuitBreaker.position_opened(slot_id=...) resolve
                        # correctly once this slot is in play.
                        _sig_metadata = dict(sig.metadata or {})
                        _sig_metadata["slot_id"] = slot_key
                        # [12.5] Each slot gets its OWN independent
                        # max_positions_per_symbol quota (not a shared
                        # symbol-wide counter) — this is what actually lets
                        # two strategies hold simultaneous positions on the
                        # same real symbol. Same value as the portfolio-wide
                        # setting; a per-slot override isn't exposed on
                        # PortfolioSymbolConfig yet.
                        _sig_metadata.setdefault("slot_max_positions", req.max_positions_per_symbol)
                        sym_signals.append({
                            "symbol": sig.symbol,
                            "_cache_key": slot_key,  # [12.8]
                            "strategy_name": strat_id,
                            "direction": sig.direction,
                            "time": sig_time,
                            "entry_price": sig.entry_price,
                            "stop_loss": sig.stop_loss,
                            "take_profit": sig.take_profit,
                            "timeframe": sig.timeframe,  # [12.10] see risk/engine.py's note
                            "confluence_score": sig.confluence_score,
                            "metadata": _sig_metadata,
                        })
                        replay.signal(slot_key, sym_signals[-1])

                        # Same runaway guard as the single-symbol route, per leg.
                        # It matters more here: a portfolio run loops this per
                        # symbol, so one misbehaving leg would otherwise hang the
                        # whole basket.
                        if len(sym_signals) >= MAX_SIGNALS_PER_RUN:
                            logger.error(
                                f"[PORTFOLIO_BT] {sym}: signal cap {MAX_SIGNALS_PER_RUN} hit at "
                                f"bar {i}/{len(primary_times)} — aborting this leg's signal "
                                f"generation and continuing with the rest of the portfolio."
                            )
                            break

                replay.leg_done(slot_key)

                # Use the primary timeframe candles as the simulation dataframe for this symbol
                primary_df = primary_sorted.copy()
                if 'time' not in primary_df.columns:
                    primary_df['time'] = primary_df.index.astype('int64') // 10**9

                # [12.8] Keyed by slot_key, not bare `sym` — lets two rows
                # share `sym` under different strategies without colliding.
                portfolio_data[slot_key] = primary_df
                # Multi-timeframe candle sets for the HTF chart tabs, when the
                # strategy actually fetched M15/M5 (mirrors the single-symbol
                # /backtest endpoint's candles_m15_idx / candles_m5_idx).
                if "M15" in indexed_by_tf:
                    portfolio_data_m15[slot_key] = indexed_by_tf["M15"]
                if "M5" in indexed_by_tf:
                    portfolio_data_m5[slot_key] = indexed_by_tf["M5"]
                portfolio_signals[slot_key] = sym_signals

                # Capture this symbol's strategy-engine logs before it goes out
                # of scope — without this, portfolio backtests never surface
                # any run log at all (unlike single-symbol runs).
                for entry in getattr(strategy_engine, 'run_logs', []):
                    tagged = dict(entry) if isinstance(entry, dict) else {"message": str(entry)}
                    tagged.setdefault("symbol", sym)
                    portfolio_run_logs.append(tagged)

                logger.info(f"[PORTFOLIO_BT] {sym}: {len(sym_signals)} signals from {len(primary_df)} bars")
                
                try:
                    asyncio.create_task(ws_manager.broadcast_to_user(current_user.id, {
                        "type": "activity_log",
                        "event": {
                            "time": datetime.now(timezone.utc).isoformat(),
                            "level": "INFO",
                            "category": "BACKTEST_LOG",
                            "message": f"Generated {len(sym_signals)} signals for {sym}"
                        }
                    }))
                except: pass

            # ── 3. Run Global Simulation ──
            current_state = await _get_state()
            current_state["progress"] = {"stage": "Running global portfolio simulation...", "pct": 85}
            await _save_state(current_state)
            await ws_manager.broadcast_to_user(current_user.id, {"type": "backtest_progress", **current_state["progress"]})

            portfolio_engine = PortfolioBacktestEngine(merged_risk_config)

            # [17.2] Progress during the global simulation. This is the same
            # thread-crossing problem as B4: `run` executes inside
            # `asyncio.to_thread`, so `asyncio.create_task` would raise
            # "no running event loop" and be swallowed. Capture the loop here
            # (still on it) and hand work back with run_coroutine_threadsafe.
            _pf_loop = asyncio.get_running_loop()
            _pf_last_emit = [0.0]

            # [merge] fraction signature, as above.
            def _pf_progress(fraction: float):
                import time as _t
                now = _t.monotonic()
                if now - _pf_last_emit[0] < 0.25:
                    return
                _pf_last_emit[0] = now
                # global simulation occupies the 85-98 band
                pct = min(98, 85 + int(13 * max(0.0, min(1.0, fraction))))
                payload = {"stage": "Running global portfolio simulation...", "pct": pct}
                try:
                    state = USER_BACKTEST_STATE.setdefault(current_user.id, {})
                    state["progress"] = payload
                    asyncio.run_coroutine_threadsafe(
                        _save_state({**state, "progress": payload}), _pf_loop)
                    asyncio.run_coroutine_threadsafe(
                        ws_manager.broadcast_to_user(
                            current_user.id, {"type": "backtest_progress", **payload}),
                        _pf_loop)
                except Exception as _e:
                    logger.warning(f"[PORTFOLIO_BT] progress emit failed: {_e}")

            results = await asyncio.to_thread(
                portfolio_engine.run,
                portfolio_data,
                portfolio_signals,
                req.initial_balance,
                portfolio_data_m15,
                portfolio_data_m5,
                symbol_map,  # [12.8]
                _pf_progress,
            )

            # ── Sanitize and broadcast results ──
            import numpy as _np
            import math

            def _sanitize(obj):
                if isinstance(obj, dict):
                    return {k: _sanitize(v) for k, v in obj.items()}
                elif isinstance(obj, (list, tuple)):
                    return [_sanitize(v) for v in obj]
                elif isinstance(obj, (_np.bool_, bool)):
                    return bool(obj)
                elif isinstance(obj, (_np.integer, int)):
                    return int(obj)
                elif isinstance(obj, (_np.floating, float)):
                    if math.isnan(obj) or math.isinf(obj):
                        return None
                    return float(obj)
                elif isinstance(obj, _np.ndarray):
                    return _sanitize(obj.tolist())
                elif isinstance(obj, pd.Timestamp) or hasattr(obj, 'isoformat'):
                    return obj.isoformat()
                elif isinstance(obj, (str, type(None))):
                    return obj
                return str(obj)

            report = results.get("report")
            elapsed = (_time.time() - bt_start)
            total_trades = results.get("total_trades", 0)
            final_balance = results.get("final_balance", req.initial_balance)
            total_pnl = final_balance - req.initial_balance

            logger.info(f"[PORTFOLIO_BT] Complete in {elapsed:.1f}s — {total_trades} trades | PnL=${total_pnl:.2f}")
            bot_service.log_system_event(
                f"Portfolio backtest complete: {len(req.symbols)} symbols | {total_trades} trades | PnL=${total_pnl:.2f}",
                category="BACKTEST"
            )

            response = {
                "backtest_id": results.get("backtest_id", str(uuid.uuid4())),
                "portfolio": True,
                "symbols": sym_list,
                "initial_balance": req.initial_balance,
                "final_balance": final_balance,
                "total_trades": total_trades,
                "total_signals": results.get("total_signals", 0),
                "invalid_signals": results.get("invalid_signals", 0),
                "equity_curve": results.get("equity_curve", []),
                "trades": results.get("trades", []),
                "grouped_trades": results.get("grouped_trades", []),
                "rejection_funnel": results.get("rejection_funnel", {}),
                # [I4] see single-symbol route for rationale.
                "blocked_signals": results.get("blocked_signals", []),
                # [2.24] Distinguishes a drawdown-latched stretch from "no setups".
                "circuit_breaker_summary": results.get("circuit_breaker_summary", {}),
                # Per-symbol transaction costs the engine actually applied, with
                # provenance (USER / MT5 / ASSET_CLASS_DEFAULT) for each field.
                "cost_model": results.get("cost_model", {}),
                "params_snapshot": {
                    **(req.model_dump() if hasattr(req, "model_dump") else req.dict()),
                    "resolved_cost_model": results.get("cost_model", {}),
                },
                # [I2] Merge strategy + engine logs, then keep all WARNING/ERROR +
                # a sampled INFO tail instead of a flat last-100 slice.
                "run_logs": _filter_run_logs(portfolio_run_logs + results.get("run_logs", [])),
                "report": {
                    "win_rate": getattr(report, 'win_rate', 0) if report else results.get("win_rate", 0),
                    "profit_factor": getattr(report, 'profit_factor', 0) if report else results.get("profit_factor", 0),
                    "sharpe_ratio": getattr(report, 'sharpe_ratio', 0) if report else results.get("sharpe_ratio", 0),
                    "sortino_ratio": getattr(report, 'sortino_ratio', 0) if report else results.get("sortino_ratio", 0),
                    "max_drawdown_pct": getattr(report, 'max_drawdown_pct', 0) if report else results.get("max_drawdown_pct", 0),
                    "max_drawdown_pct_of_peak": getattr(report, 'max_drawdown_pct_of_peak', 0) if report else results.get("max_drawdown_pct_of_peak", 0),
                    "total_pnl": total_pnl,
                    "expectancy_r": getattr(report, 'expectancy_r', 0) if report else results.get("expectancy_r", 0),
                    "tp1_hit_rate": getattr(report, 'tp1_hit_rate', 0) if report else 0,
                    "tp2_hit_rate": getattr(report, 'tp2_hit_rate', 0) if report else 0,
                    "tp3_hit_rate": getattr(report, 'tp3_hit_rate', 0) if report else 0,
                    "tp4_hit_rate": getattr(report, 'tp4_hit_rate', 0) if report else 0,
                    "tp5_hit_rate": getattr(report, 'tp5_hit_rate', 0) if report else 0,
                    "sl_hit_rate": getattr(report, 'sl_hit_rate', 0) if report else 0,
                    "be_hit_rate": getattr(report, 'be_hit_rate', 0) if report else 0,
                    "trail_hit_rate": getattr(report, 'trail_hit_rate', 0) if report else 0,
                    "max_consecutive_wins": getattr(report, 'max_consecutive_wins', 0) if report else 0,
                    "max_consecutive_losses": getattr(report, 'max_consecutive_losses', 0) if report else 0,
                    # FIX: these two were missing entirely from the portfolio response.
                    # The frontend only renders the Win Rate by Score/Confirmation/Bias
                    # panels when `report.confluence_stats || report.bias_stats` is
                    # truthy — with the keys absent, that check was always false for
                    # every portfolio backtest (multi-symbol runs), regardless of
                    # whether the underlying trades had confluence/bias data.
                    "bias_stats": getattr(report, 'bias_stats', {}) if report else {},
                    "confluence_stats": getattr(report, 'confluence_stats', {}) if report else {},
                },
                "log_session_id": log_session_id,
            }

            # [Phase 13 section C.7] Continuous per-leg series for replay-mode
            # scrubbing, keyed by slot_id to match the tab strip.
            try:
                response["replay"] = replay.series_payload()
                replay.done()
                logger.info(f"[replay] portfolio run complete: {replay.stats}")
            except Exception as e:
                logger.warning(f"[replay] series attach failed (chart only, run unaffected): {e}")

            sanitized = _sanitize(response)
            current_state = await _get_state()
            current_state["status"] = "complete"
            current_state["progress"] = {"stage": "complete", "pct": 100}
            # Keep the FULL result (with chart_data) server-side — this is
            # what /backtest_result/trade/{group_id}/chart reads from on demand.
            current_state["result"] = sanitized

            # Stripped copy for the WS broadcast itself. Previously this
            # endpoint broadcast `sanitized` directly, with every trade's
            # chart_data / chart_data_m15 / chart_data_m5 embedded — for a
            # multi-symbol portfolio run with hundreds of grouped trades,
            # each carrying up to ~500 candles per timeframe, this is exactly
            # the "tremendous load" the on-demand chart endpoint exists to
            # avoid. Mirror the /backtest endpoint's pattern: strip the heavy
            # fields from what actually goes over the wire.
            import copy
            ws_payload = copy.deepcopy(sanitized)
            if "grouped_trades" in ws_payload:
                for t in ws_payload["grouped_trades"]:
                    if isinstance(t, dict):
                        t.pop("chart_data", None)
                        t.pop("chart_data_m15", None)
                        t.pop("chart_data_m5", None)
            # Same reasoning as the per-trade chart_data above — the client
            # already received this series live; it is refetched from saved
            # state via /replay rather than pushed again at completion.
            ws_payload.pop("replay", None)

            await ws_manager.broadcast_to_user(current_user.id, {"type": "backtest_progress", "stage": "complete", "result": ws_payload})
            await _save_state(current_state)

        except Exception as e:
            import traceback
            logger.error(f"[PORTFOLIO_BT] Error: {e}\n{traceback.format_exc()}")
            current_state = await _get_state()
            current_state["status"] = "error"
            current_state["progress"] = {"stage": "error", "message": str(e), "pct": 0}
            current_state["result"] = None
            try:
                await ws_manager.broadcast_to_user(current_user.id, {"type": "backtest_error", "message": str(e)})
            except Exception:
                pass
            await _save_state(current_state)
            from backend.services.bot_service import bot_service
            bot_service.log_system_event(f"Portfolio backtest failed: {e!s}", category="BACKTEST", level="ERROR")
        finally:
            log_hub.end_session(log_session_id)

    background_tasks.add_task(_run_portfolio_task)
    return {
        "status": "started",
        "message": f"Portfolio backtest queued for {len(req.symbols)} symbol(s).",
        "log_session_id": log_session_id,
    }

@router.get("/backtests")
async def list_backtests(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all saved backtests for the authenticated user."""
    result = await db.execute(
        select(BacktestRun)
        .options(defer(BacktestRun.run_logs), defer(BacktestRun.params_snapshot))
        .where(BacktestRun.user_id == current_user.id)
        .order_by(desc(BacktestRun.created_at))
    )
    runs = result.scalars().all()
    return [{
        "id": r.id,
        "strategy_id": r.strategy_id,
        "symbol": r.symbol,
        "title": r.title or r.symbol,
        "total_trades": r.total_trades,
        "win_rate": r.win_rate,
        "sharpe_ratio": r.sharpe_ratio,
        "total_pnl": r.total_pnl,
        "max_drawdown_pct": r.max_drawdown_pct,
        "profit_factor": r.profit_factor,
        "created_at": r.created_at,
        "notes_preview": r.notes[:50] + "..." if getattr(r, "notes", None) else None,
    } for r in runs]

def safe_json_loads(data, default=None):
    if not data:
        return default if default is not None else {}
    try:
        return json.loads(data)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else {}


def _slim_sub_trades(sub_trades: list) -> list:
    """
    Strip heavy/duplicative fields (original_signal, metadata, and all but
    the first leg's entry_snapshot_b64) before persisting a group's
    sub_trades — see the matching helper/comment in backend/backtester/runner.py.
    """
    slim = []
    for i, t in enumerate(sub_trades or []):
        if not isinstance(t, dict):
            continue
        st = {k: v for k, v in t.items() if k not in ("original_signal", "metadata")}
        if i > 0:
            st.pop("entry_snapshot_b64", None)
        slim.append(st)
    return slim

@router.get("/backtests/{backtest_id}")
async def get_backtest(
    backtest_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get full backtest detail with all trades, equity data, and session breakdown."""
    result = await db.execute(
        select(BacktestRun)
        .options(selectinload(BacktestRun.trades))
        .where(BacktestRun.id == backtest_id, BacktestRun.user_id == current_user.id)
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Backtest not found")

    # Build equity curve from trades
    equity_curve = []
    initial_balance = 10000.0  # Default; should come from params_snapshot
    params = {}
    try:
        params = safe_json_loads(run.params_snapshot, {})
        initial_balance = params.get("initial_balance", 10000.0)
    except Exception:
        pass

    balance = initial_balance

    equity_curve.append(balance)
    for t in run.trades:
        balance += (t.pnl or 0)
        equity_curve.append(round(balance, 2))

    # TP distribution
    tp_dist = {"TP1": 0, "TP2": 0, "TP3": 0, "TP4": 0, "TP5": 0, "SL": 0, "TRAIL": 0, "BE": 0}
    session_stats = {
        "LONDON": {"wins": 0, "total": 0}, 
        "NY": {"wins": 0, "total": 0}, 
        "LONDON/NY": {"wins": 0, "total": 0},
        "ASIAN": {"wins": 0, "total": 0},
        "UNKNOWN": {"wins": 0, "total": 0}
    }

    for t in run.trades:
        reason = t.exit_reason or ""
        if reason in tp_dist:
            tp_dist[reason] += 1
        elif reason == "SL":
            tp_dist["SL"] += 1

        # Session breakdown (if available)
        session = getattr(t, "session", None) or "UNKNOWN"
        if session not in session_stats:
            session = "UNKNOWN"
        
        session_stats[session]["total"] += 1
        if (t.pnl or 0) > 0:
            session_stats[session]["wins"] += 1

    session_win_rates = {}
    for sess, data in session_stats.items():
        key = "OVERLAP" if sess == "LONDON/NY" else sess
        session_win_rates[key] = data["wins"] / data["total"] if data["total"] > 0 else 0

    grouped_trades_out = []
    for t in run.trades:
        sub_trades = safe_json_loads(t.sub_trades, [])
        tp_count = len(sub_trades)
        tp_wins = sum(1 for st in sub_trades if st.get("pnl", 0) > 0)
        tp_losses = tp_count - tp_wins
        duration = 0
        if t.entry_time and t.exit_time:
            duration = int((t.exit_time - t.entry_time).total_seconds() / 60)
            
        grouped_trades_out.append({
            "group_id": str(t.id),
            "symbol": t.symbol,
            "direction": t.direction,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "stop_loss": t.stop_loss,
            "pnl": t.pnl,
            "combined_pnl": t.pnl,
            "exit_reason": t.exit_reason,
            "tp_level_hit": t.tp_level_hit,
            "balance_before": t.balance_before,
            "balance_after": t.balance_after,
            "tp1_price": t.tp1_price,
            "tp2_price": t.tp2_price,
            "tp3_price": t.tp3_price,
            "tp4_price": t.tp4_price,
            "tp5_price": t.tp5_price,
            "pnl_r": t.pnl_r,
            "planned_rr": t.planned_rr,
            "realized_rr": t.realized_rr,
            "entry_time": t.entry_time.isoformat() if t.entry_time else None,
            "entry_time_iso": t.entry_time.isoformat() if t.entry_time else None,
            "exit_time": t.exit_time.isoformat() if t.exit_time else None,
            "exit_time_iso": t.exit_time.isoformat() if t.exit_time else None,
            "duration_minutes": duration,
            "entry_session": getattr(t, "session", None) or "UNKNOWN",
            "tp_count": tp_count,
            "tp_wins": tp_wins,
            "tp_losses": tp_losses,
            "be_applied": t.be_applied,
            "trail_method": t.trail_method,
            "mae_pips": t.mae_pips,
            "mfe_pips": t.mfe_pips,
            "confluence_score": t.confluence_score,
            "strategy_id": getattr(t, "strategy_id", None) or run.strategy_id,
            "smc_data": safe_json_loads(t.smc_data, {}),
            "sub_trades": sub_trades,
            # runner.py's _slim_sub_trades keeps entry_snapshot_b64 on
            # sub_trades[0] specifically (it's identical across every leg of
            # a group, so it's stripped from legs 1+ to save space, not
            # dropped entirely). Read it back from there instead of
            # hardcoding "" and silently losing the saved snapshot image.
            "entry_snapshot_b64": (sub_trades[0].get("entry_snapshot_b64", "") if sub_trades else "")
        })

    resp = {
        "run": {
            "id": run.id,
            "symbol": run.symbol,
            "title": run.title or run.symbol,
            "strategy_id": run.strategy_id,
            "total_trades": run.total_trades,
            "win_rate": run.win_rate,
            "total_pnl": run.total_pnl,
            "initial_balance": initial_balance,
            "final_balance": round(balance, 2),
            "sharpe_ratio": run.sharpe_ratio,
            "profit_factor": run.profit_factor,
            "max_drawdown_pct": run.max_drawdown_pct,
            "tp1_hit_rate": run.tp1_hit_rate,
            "tp2_hit_rate": run.tp2_hit_rate,
            "tp3_hit_rate": run.tp3_hit_rate,
            "tp4_hit_rate": run.tp4_hit_rate,
            "tp5_hit_rate": run.tp5_hit_rate,
            "be_hit_rate": run.be_hit_rate,
            "trail_hit_rate": run.trail_hit_rate,
            "start_date": run.start_date,
            "end_date": run.end_date,
            "created_at": run.created_at,
            "notes": run.notes,
            "params_snapshot": params,
            "bias_stats": run.bias_stats or {},
            "confluence_stats": run.confluence_stats or {},
        },
        "grouped_trades": grouped_trades_out,
        "equity_curve": equity_curve,
        "tp_distribution": tp_dist,
        "session_win_rates": session_win_rates,
        "run_logs": [],  # Stripped to prevent UI freeze
    }

    try:
        import dataclasses

        from backend.analytics.reports import generate_risk_report
        risk_report = generate_risk_report(
            grouped_trades_out,
            initial_balance=initial_balance,
            # Score a saved run on the basis it was RUN with, not today's default.
            sizing_basis=(params or {}).get("sizing_basis") or "STATIC",
        )
        resp["report"] = dataclasses.asdict(risk_report)
        resp["session_win_rates"] = {
            "LONDON": risk_report.london_win_rate,
            "NY": risk_report.ny_win_rate,
            "OVERLAP": risk_report.overlap_win_rate,
            "ASIAN": risk_report.asian_win_rate,
            "UNKNOWN": risk_report.other_win_rate
        }
        
        # Prefer the persisted values (computed once, right after the backtest
        # ran, while original_signal/metadata were still in memory) over
        # recomputing from grouped_trades_out here — that data has already
        # been stripped for storage (see runner.py's _slim_sub_trades), so a
        # from-scratch recompute can only ever produce a degenerate
        # by_confirmation breakdown. Fall back to the fresh recompute only
        # for backtests saved before these columns existed.
        resp["report"]["bias_stats"] = run.bias_stats or risk_report.bias_stats
        resp["report"]["confluence_stats"] = run.confluence_stats or risk_report.confluence_stats
        resp["report"]["sortino_ratio"] = run.sortino_ratio or risk_report.sortino_ratio
        resp["report"]["expectancy_r"] = run.expectancy_r or risk_report.expectancy_r
    except Exception as e:
        import logging
        logging.error(f"Failed to generate full risk report on the fly: {e}")

    return resp


@router.get("/backtests/{backtest_id}/trade/{group_id}/chart")
async def get_saved_trade_chart(
    backtest_id: str,
    group_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Fetch massive chart data for a specific trade separately to prevent UI freeze."""
    try:
        group_id_int = int(group_id)
    except (TypeError, ValueError):
        # Not a valid BacktestTrade row id — e.g. a stale unsaved-run UUID
        # group_id being used against the saved-backtest endpoint by mistake.
        raise HTTPException(status_code=404, detail="Trade not found")

    # Ownership check — every other saved-backtest endpoint scopes by
    # user_id (see get_backtest / delete_backtest above); this one didn't,
    # which let any authenticated user read another user's chart data for a
    # guessed/enumerated backtest_id.
    owner_check = await db.execute(
        select(BacktestRun.id).where(BacktestRun.id == backtest_id, BacktestRun.user_id == current_user.id)
    )
    if owner_check.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Backtest not found")

    result = await db.execute(
        select(BacktestTrade)
        .where(BacktestTrade.backtest_id == backtest_id, BacktestTrade.id == group_id_int)
    )
    t = result.scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=404, detail="Trade not found")
        
    return {
        "chart_data": safe_json_loads(t.chart_data, []),
        "chart_data_m15": safe_json_loads(t.chart_data_m15, []),
        "chart_data_m5": safe_json_loads(t.chart_data_m5, [])
    }


@router.get("/backtest_result/replay")
async def get_replay_series(
    current_user: User = Depends(get_current_user),
):
    """
    [Phase 13 section C.7] The continuous per-leg bar series for the current
    run, for replay-mode scrubbing.

    Deliberately NOT included in the completion WebSocket payload: for a
    multi-leg portfolio run this is the single largest object in the result,
    and the client already streamed it live. It is fetched here only when a
    page reload or a revisit means the client no longer has it.
    """
    state = USER_BACKTEST_STATE.get(current_user.id)
    if state is None and HAS_REDIS and redis_client and redis_client.redis:
        try:
            data = await redis_client.redis.get(f"backtest_state:{current_user.id}")
            if data:
                state = json.loads(data)
        except Exception:
            pass

    if not state or not state.get("result"):
        raise HTTPException(status_code=404, detail="No active or completed backtest found")

    replay = state["result"].get("replay")
    if replay and replay.get("series"):
        return {**replay, "available": True, "reconstructed": False}

    # No stored series — rebuild from the run's grouped trades, the same way the
    # saved-run endpoint does. Covers a result produced before the replay stream
    # existed, and one whose live stream failed mid-run.
    #
    # Deliberately NOT a 404: the client needs to tell "ask again later" (no run
    # yet) apart from "this is all there will ever be".
    groups = state["result"].get("grouped_trades") or []

    class _G:
        """
        Attribute view over a grouped-trade dict.

        `_replay_from_trades` reads via getattr because its primary caller passes
        ORM rows; this adapts the in-memory dict shape to the same interface.
        `entry_price`/`pnl` differ in name between the two shapes, so they are
        mapped rather than passed straight through.
        """
        _ALIAS = {"pnl": "combined_pnl", "tp1_price": "take_profit"}

        def __init__(self, d):
            self._d = d

        def __getattr__(self, k):
            if k in self._ALIAS and self._ALIAS[k] in self._d:
                return self._d[self._ALIAS[k]]
            return self._d.get(k)

    rebuilt = _replay_from_trades([_G(g) for g in groups],
                                  state["result"].get("symbol"))
    return {**rebuilt, "available": bool(rebuilt["series"])}


def _replay_from_trades(trades: list, fallback_symbol: str | None = None) -> dict:
    """
    Rebuild a replay payload from a saved run's per-trade chart data.

    Runs saved before `replay_data` existed have no continuous series — but they
    DO carry a candle window around every trade (`chart_data*`, +/-30 bars, three
    timeframes). Stitching those windows together, de-duplicated and sorted, gives
    a series that covers exactly the parts of the run where something happened.

    It is not the same thing as the live series: there are gaps between trades,
    because nothing was recorded there. That is honest — and for reviewing a
    finished run it is arguably the more useful view, since the empty stretches
    carried no trades to look at. The response says `reconstructed: True` so the
    client can label it rather than implying continuity it does not have.
    """
    def _load(value, default):
        """
        Accept both storage shapes.

        A saved trade holds these fields as JSON STRINGS (SQLAlchemy Text
        columns); an in-memory grouped trade holds them as live lists/dicts.
        `safe_json_loads` returns the default for a non-string, so calling it on
        the in-memory shape would silently discard the data — which is exactly
        the bug this helper exists to avoid.
        """
        if value is None:
            return default
        if isinstance(value, (list, dict)):
            return value
        return safe_json_loads(value, default)

    series: dict[str, list] = {}
    signals: dict[str, list] = {}

    for t in trades or []:
        sym = (getattr(t, "symbol", None) or fallback_symbol or "UNKNOWN")
        slot = sym
        # Prefer the richest timeframe actually stored for this trade.
        bars = []
        for field in ("chart_data_m5", "chart_data_m15", "chart_data"):
            raw = _load(getattr(t, field, None), [])
            if raw and len(raw) > len(bars):
                bars = raw
        if bars:
            series.setdefault(slot, []).extend(bars)

        entry_t = getattr(t, "entry_time", None)
        if entry_t is not None:
            try:
                ts = int(entry_t.timestamp()) if hasattr(entry_t, "timestamp") else int(entry_t)
            except Exception:
                ts = 0
            smc = _load(getattr(t, "smc_data", None), {}) or {}
            markings = (smc.get("boxes") or []) + (smc.get("lines") or []) + (smc.get("markers") or [])
            signals.setdefault(slot, []).append({
                "slot_id": slot,
                "time": ts,
                "direction": getattr(t, "direction", None),
                "entry": getattr(t, "entry_price", None),
                "sl": getattr(t, "stop_loss", None),
                "tp": getattr(t, "tp1_price", None),
                "confluence_score": getattr(t, "confluence_score", None),
                "strategy_id": getattr(t, "strategy_id", None),
                "exit_reason": getattr(t, "exit_reason", None),
                "pnl": getattr(t, "pnl", None),
                "markings": markings,
                "confluence_summary": smc.get("confluence_summary", {}),
            })

    # De-duplicate by timestamp and sort — the per-trade windows overlap wherever
    # two trades were close together, and the chart requires strictly ascending
    # unique times or it silently drops bars.
    cleaned: dict[str, list] = {}
    for slot, bars in series.items():
        seen: dict[int, dict] = {}
        for b in bars:
            try:
                seen[int(b["time"])] = b
            except (KeyError, TypeError, ValueError):
                continue
        cleaned[slot] = [seen[k] for k in sorted(seen)]

    legs = [
        {"slot_id": slot, "symbol": slot, "strategy_id": None, "timeframe": None}
        for slot in sorted(cleaned)
    ]
    return {
        "run_id": None,
        "mode": "portfolio" if len(legs) > 1 else "single",
        "legs": legs,
        "series": cleaned,
        "signals": signals,
        "reconstructed": True,
    }


@router.get("/backtests/{backtest_id}/replay")
async def get_saved_replay_series(
    backtest_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Replay series for a SAVED run. Ownership-scoped, like every other saved-run route.

    Falls back to reconstructing from per-trade chart data when the run predates
    `replay_data`, so an old saved backtest still replays rather than showing an
    empty panel.
    """
    result = await db.execute(
        select(BacktestRun)
        .options(selectinload(BacktestRun.trades))
        .where(BacktestRun.id == backtest_id, BacktestRun.user_id == current_user.id)
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Backtest not found")

    payload = safe_json_loads(getattr(run, "replay_data", None), None)
    if payload and payload.get("series"):
        return {**payload, "available": True, "reconstructed": False}

    rebuilt = _replay_from_trades(list(run.trades or []), run.symbol)
    return {**rebuilt, "available": bool(rebuilt["series"])}


@router.get("/backtest_result/trade/{group_id}/chart")
async def get_unsaved_trade_chart(
    group_id: str,
    current_user: User = Depends(get_current_user),
):
    """Fetch massive chart data for an unsaved trade from the current running/completed backtest state."""
    state = USER_BACKTEST_STATE.get(current_user.id)
    if state is None and HAS_REDIS and redis_client and redis_client.redis:
        try:
            import json
            data = await redis_client.redis.get(f"backtest_state:{current_user.id}")
            if data:
                state = json.loads(data)
        except Exception:
            pass
            
    if not state or not state.get("result"):
        raise HTTPException(status_code=404, detail="No active or completed backtest found")
        
    result = state["result"]
    trades = result.get("grouped_trades", [])
    
    # Find the trade by group_id
    trade = next((t for t in trades if t.get("group_id") == group_id), None)
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found in current backtest")
        
    return {
        "chart_data": trade.get("chart_data", []),
        "chart_data_m15": trade.get("chart_data_m15", []),
        "chart_data_m5": trade.get("chart_data_m5", [])
    }


@router.delete("/backtests/{backtest_id}")
async def delete_backtest(
    backtest_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a saved backtest and all associated trades."""
    result = await db.execute(
        select(BacktestRun).where(BacktestRun.id == backtest_id, BacktestRun.user_id == current_user.id)
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Backtest not found")
    await db.delete(run)
    await db.commit()
    return {"deleted": True}
@router.post("/backtests/{backtest_id}/save")
async def save_backtest_from_client(
    backtest_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Save a discarded backtest from the client."""
    import json
    from datetime import datetime

    from sqlalchemy.future import select

    from backend.data.models import BacktestRun
    
    raw_data = await request.json()
    data = raw_data.get("backtest_data", raw_data)
    
    # Check if already exists
    res = await db.execute(select(BacktestRun).where(BacktestRun.id == backtest_id))
    if res.scalars().first():
        return {"status": "ok", "message": "Already saved"}

    report = data.get("report", {})
    
    # [B8] Default the stored range to what the run actually REQUESTED, not to
    # "now". A zero-trade run has no trades to derive dates from, so both ends
    # collapsed to the save timestamp — which is why 9 of the 57 saved runs show
    # start_date == end_date == the moment Save was clicked, losing the one piece
    # of context needed to reproduce them. The requested window is right there in
    # the payload; use it, and only refine from the trades when there are any.
    _req_params = data.get("params_snapshot") or data.get("risk_config") or {}

    def _parse_req_date(key):
        raw = _req_params.get(key) if isinstance(_req_params, dict) else None
        if not raw:
            return None
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).replace(tzinfo=None)
        except (ValueError, TypeError):
            return None

    _now = datetime.now(timezone.utc).replace(tzinfo=None)
    start_date = _parse_req_date("start_date") or _now
    end_date = _parse_req_date("end_date") or _now
    trades = data.get("grouped_trades", data.get("trades", []))
    if trades:
        try:
            valid_start = []
            valid_end = []
            for t in trades:
                # Try iso strings first
                t_start_iso = t.get("entry_time_iso")
                t_end_iso = t.get("exit_time_iso") or t.get("entry_time_iso")
                
                # If missing, try epoch timestamps
                if not t_start_iso and t.get("entry_time"):
                    t_start_iso = datetime.fromtimestamp(t["entry_time"], timezone.utc).isoformat()
                if not t_end_iso:
                    if t.get("exit_time"):
                        t_end_iso = datetime.fromtimestamp(t["exit_time"], timezone.utc).isoformat()
                    elif t.get("entry_time"):
                        t_end_iso = datetime.fromtimestamp(t["entry_time"], timezone.utc).isoformat()
                
                if t_start_iso:
                    # Clean up 'Z' if present
                    valid_start.append(datetime.fromisoformat(t_start_iso.replace('Z', '+00:00')).replace(tzinfo=None))
                if t_end_iso:
                    valid_end.append(datetime.fromisoformat(t_end_iso.replace('Z', '+00:00')).replace(tzinfo=None))

            if valid_start:
                start_date = min(valid_start)
            if valid_end:
                end_date = max(valid_end)
        except Exception as e:
            logger.warning(f"Failed to parse dates for backtest {backtest_id}: {e}")

    run = BacktestRun(
        id=backtest_id,
        user_id=current_user.id,
        strategy_id=data.get("strategy_id", "APA_v1"),
        symbol=data.get("symbol", "Volatility 75 Index"),
        # User-supplied title takes priority. Falls back to symbol so
        # single-symbol saves (which didn't bother typing a title) still get
        # a sensible default, matching prior behavior for that case only.
        title=(data.get("title") or "").strip() or data.get("symbol", "Volatility 75 Index"),
        start_date=start_date,
        end_date=end_date,
        # params_snapshot records the RUN CONFIGURATION only. bias_stats and
        # confluence_stats used to be stuffed in here as well, which is why the
        # dedicated columns below were empty on every saved run: this path
        # simply never passed them to the model, so SQLAlchemy applied its
        # `default={}`. Analysis code reads the columns, not the blob, so all
        # 57 historical runs looked like they had no analytics at all.
        params_snapshot=json.dumps(data.get("risk_config", {})),
        # The three analytics columns, now actually populated on this path.
        # runner.py's server-side save has always set these correctly; the
        # client "Save" button path had not, and the client path is the one
        # that produced every saved run.
        rejection_funnel=data.get("rejection_funnel", report.get("rejection_funnel", {})) or {},
        bias_stats=report.get("bias_stats", {}) or {},
        confluence_stats=report.get("confluence_stats", {}) or {},
        notes=data.get("notes", ""),
        total_trades=data.get("total_trades", report.get("total_trades", 0)),
        win_rate=data.get("win_rate", report.get("win_rate", 0)),
        profit_factor=data.get("profit_factor", report.get("profit_factor", 0)),
        sharpe_ratio=data.get("sharpe_ratio", report.get("sharpe_ratio", 0)),
        sortino_ratio=data.get("sortino_ratio", report.get("sortino_ratio", 0)),
        expectancy_r=data.get("expectancy_r", report.get("expectancy_r", 0)),
        max_drawdown_pct=data.get("max_drawdown_pct", report.get("max_drawdown_pct", 0)),
        total_pnl=data.get("total_pnl", report.get("total_pnl", 0)),
        tp1_hit_rate=report.get("tp1_hit_rate", 0),
        tp2_hit_rate=report.get("tp2_hit_rate", 0),
        tp3_hit_rate=report.get("tp3_hit_rate", 0),
        tp4_hit_rate=report.get("tp4_hit_rate", 0),
        tp5_hit_rate=report.get("tp5_hit_rate", 0),
        sl_hit_rate=report.get("sl_hit_rate", 0),
        be_hit_rate=report.get("be_hit_rate", 0),
        trail_hit_rate=report.get("trail_hit_rate", 0),
        run_logs=json.dumps(data.get("run_logs", []), default=str),
        # [Phase 13 section C.7] See runner.py — same field, client-save path.
        replay_data=json.dumps(data.get("replay"), default=str) if data.get("replay") else None,
    )
    
    db.add(run)
    
    # Inject chart data back from state since frontend strips it to save bandwidth
    state_trades = []
    try:
        from backend.api.routes.backtest import USER_BACKTEST_STATE
        state = USER_BACKTEST_STATE.get(current_user.id)
        if state and state.get("result"):
            state_result = state["result"]
            # USER_BACKTEST_STATE is a single slot per user, not per-backtest —
            # if the user ran ANY other backtest (even just a preview) between
            # finishing this run and clicking Save, this slot now belongs to a
            # different run entirely. Using it anyway means every group_id
            # lookup below silently misses, and since the frontend's own copy
            # of `trades` already had chart_data stripped before the save
            # request was sent, BOTH sources end up empty with no error raised
            # anywhere — exactly the "chart_data": [] symptom. Check the id
            # actually matches before trusting it.
            if state_result.get("backtest_id") == backtest_id:
                state_trades = state_result.get("grouped_trades", [])
            else:
                logger.warning(
                    f"[save_backtest] USER_BACKTEST_STATE for user={current_user.id} holds "
                    f"backtest_id={state_result.get('backtest_id')!r}, not the one being saved "
                    f"({backtest_id!r}) — likely another backtest ran in between. chart_data for "
                    f"this save will be empty for every trade."
                )
    except Exception as e:
        import logging
        logging.error(f"Failed to load state for chart injection: {e}")
    
    for t in trades:
        group_id = t.get("group_id")
        state_t = next((st for st in state_trades if st.get("group_id") == group_id), {})
        
        t["chart_data"] = state_t.get("chart_data", t.get("chart_data", []))
        t["chart_data_m15"] = state_t.get("chart_data_m15", t.get("chart_data_m15", []))
        t["chart_data_m5"] = state_t.get("chart_data_m5", t.get("chart_data_m5", []))

        try:
            etime = t.get("entry_time_iso") or t.get("entry_time")
            if isinstance(etime, (int, float)):
                entry_time = datetime.fromtimestamp(etime)
            else:
                entry_time = datetime.fromisoformat(etime).replace(tzinfo=None) if etime else None
                
            xtime = t.get("exit_time_iso") or t.get("exit_time")
            if isinstance(xtime, (int, float)):
                exit_time = datetime.fromtimestamp(xtime)
            else:
                exit_time = datetime.fromisoformat(xtime).replace(tzinfo=None) if xtime else None
        except Exception as e:
            import logging
            logging.error(f"Failed to parse time for trade: {e}")
            entry_time, exit_time = None, None
            
        bt_trade = BacktestTrade(
            id=uuid.uuid4().int & ((1 << 63) - 1),  # Bypass SQLite BIGINT autoincrement issue & ensure signed 64-bit fit
            backtest_id=backtest_id,
            symbol=t.get("symbol", run.symbol),
            direction=t.get("direction"),
            entry_price=t.get("entry_price"),
            exit_price=t.get("exit_price"),
            stop_loss=t.get("stop_loss"),
            pnl=t.get("combined_pnl", t.get("pnl", 0)),
            balance_before=t.get("balance_before"),
            balance_after=t.get("balance_after"),
            session=t.get("entry_session", "UNKNOWN"),
            exit_reason=t.get("exit_reason"),
            tp_level_hit=t.get("tp_level_hit") or t.get("tp_level"),
            entry_time=entry_time,
            exit_time=exit_time,
            be_applied=t.get("be_applied", False),
            trail_method=t.get("trail_method"),
            mae_pips=t.get("mae_pips"),
            mfe_pips=t.get("mfe_pips"),
            mae_r=t.get("mae_r"),
            mfe_r=t.get("mfe_r"),
            risk_pips=t.get("risk_pips"),
            confluence_score=t.get("confluence_score"),
            strategy_id=t.get("strategy_id", t.get("strategy", run.strategy_id)),
            chart_data=json.dumps(t.get("chart_data", [])),
            chart_data_h1=json.dumps(t.get("chart_data_h1", [])),
            chart_data_m15=json.dumps(t.get("chart_data_m15", [])),
            chart_data_m5=json.dumps(t.get("chart_data_m5", [])),
            tp1_price=t.get("tp1_price"),
            tp2_price=t.get("tp2_price"),
            tp3_price=t.get("tp3_price"),
            tp4_price=t.get("tp4_price"),
            tp5_price=t.get("tp5_price"),
            pnl_r=t.get("pnl_r"),
            planned_rr=t.get("planned_rr"),
            realized_rr=t.get("realized_rr"),
            smc_data=json.dumps(t.get("smc_data", {})),
            sub_trades=json.dumps(_slim_sub_trades(t.get("sub_trades", [])))
        )
        db.add(bt_trade)
        
    await db.commit()
    return {"status": "ok", "message": "Backtest saved successfully"}


@router.post("/backtests/bulk")
async def get_bulk_backtests(
    req: BulkBacktestRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return lightweight trade data for multiple backtests at once."""
    result = await db.execute(
        select(BacktestRun)
        .options(selectinload(BacktestRun.trades))
        .where(BacktestRun.id.in_(req.ids), BacktestRun.user_id == current_user.id)
    )
    runs = result.scalars().all()
    
    response = []
    for run in runs:
        params = safe_json_loads(run.params_snapshot, {})
        initial_balance = params.get("initial_balance", 10000.0)
        
        trades_out = []
        for t in run.trades:
            sub_trades = safe_json_loads(t.sub_trades, [])
            trades_out.append({
                "symbol": t.symbol,
                "direction": t.direction,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "stop_loss": t.stop_loss,
                "pnl": t.pnl,
                "balance_before": t.balance_before,
                "balance_after": t.balance_after,
                "exit_reason": t.exit_reason,
                "tp_level_hit": t.tp_level_hit,
                "entry_time": t.entry_time.isoformat() if t.entry_time else None,
                "exit_time": t.exit_time.isoformat() if t.exit_time else None,
                "session": getattr(t, "session", None) or "UNKNOWN",
                "confluence_score": t.confluence_score,
                "strategy_id": getattr(t, "strategy_id", None) or run.strategy_id,
                "be_applied": t.be_applied,
                "pnl_r": t.pnl_r,
                "realized_rr": t.realized_rr,
                "mae_pips": t.mae_pips,
                "mfe_pips": t.mfe_pips,
                "sub_trades": sub_trades,
            })
            
        response.append({
            "id": run.id,
            "symbol": run.symbol,
            "title": run.title or run.symbol,
            "strategy_id": run.strategy_id,
            "initial_balance": initial_balance,
            # The frontend's summaryEngine needs this to pick the right
            # normaliser for drawdown %, Sharpe/Sortino and Calmar — a run sized
            # off a compounding balance is not comparable to a STATIC one on a
            # fixed-capital denominator.
            "sizing_basis": params.get("sizing_basis") or "STATIC",
            "trades": trades_out
        })
        
    return {"data": response}
