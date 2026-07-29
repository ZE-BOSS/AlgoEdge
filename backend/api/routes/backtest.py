"""
backend/api/routes/backtest.py

Backtest run, save/dismiss, load, delete endpoints.
Source: TradingBot_MasterPlan-2.md Section 6 — REST API
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer, selectinload

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

USER_BACKTEST_STATE = {}  # Fallback in-memory persistence: user_id -> state
router = APIRouter(prefix="/api", tags=["backtest"])


class BulkBacktestRequest(BaseModel):
    ids: list[str]

class BacktestRequest(BaseModel):
    strategy_id: str = "SMC_v1"
    symbol: str
    start_date: str | None = None
    end_date: str | None = None
    candle_count: int = 5000
    initial_balance: float = 10000.0
    risk_config: dict[str, Any] = {}
    prop_firm: dict[str, Any] = {}
    # ── Dynamic Strategy Params ──
    strategy_params: dict[str, Any] = {}
    # ── Risk Params ──
    risk_per_trade_pct: float = 1.0
    min_rr: float = 3.0
    max_daily_consecutive_losses: int = 3
    max_weekly_consecutive_losses: int = 5
    max_consecutive_losses: int = 5
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
    # ── Compounding ──
    compounding_enabled: bool = False
    # ── Multi-Strategy Filters ──
    session_filter_enabled: bool = True
    manual_bias_overrides: dict[str, Any] = {}

class SaveBacktestRequest(BaseModel):
    backtest_data: dict[str, Any]
    save_mode: str = "FULL"  # "FULL" or "SUMMARY"


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
                await redis_client.redis.set(f"backtest_state:{current_user.id}", json.dumps(state), ex=3600)
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
    from backend.services.bot_service import bot_service
    bot_service.log_system_event(f"Backtest queued: {req.symbol}", category="BACKTEST")

    async def _run_backtest_task():
        global USER_BACKTEST_STATE
        initial_state = {
            "status": "running",
            "progress": {"stage": "Fetching data...", "pct": 0},
            "result": None
        }
        
        async def _save_state(state):
            USER_BACKTEST_STATE[current_user.id] = state
            if HAS_REDIS and redis_client and redis_client.redis:
                try:
                    import asyncio

                    import redis.exceptions
                    await redis_client.redis.set(f"backtest_state:{current_user.id}", json.dumps(state), ex=3600)
                except (Exception, asyncio.CancelledError, redis.exceptions.TimeoutError) as e:
                    logger.warning(f"[BACKTEST] Failed to save state to Redis: {e}")

        async def _get_state():
            if HAS_REDIS and redis_client and redis_client.redis:
                try:
                    import asyncio

                    import redis.exceptions
                    data = await redis_client.redis.get(f"backtest_state:{current_user.id}")
                    if data:
                        return json.loads(data)
                except (Exception, asyncio.CancelledError, redis.exceptions.TimeoutError) as e:
                    logger.warning(f"[BACKTEST] Failed to get state from Redis: {e}")
            return USER_BACKTEST_STATE.get(current_user.id, initial_state.copy())

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
            config.risk.max_daily_consecutive_losses = req.max_daily_consecutive_losses
            config.risk.max_weekly_consecutive_losses = req.max_weekly_consecutive_losses
            config.risk.max_consecutive_losses = req.max_consecutive_losses
            config.risk.max_concurrent_positions = req.max_concurrent_positions
            config.risk.max_positions_per_symbol = req.max_positions_per_symbol
            config.risk.max_daily_trades = req.max_daily_trades
            config.smc.manual_bias_overrides = req.manual_bias_overrides
            
            # Inject dynamic strategy parameters
            if req.strategy_id == "SMC_v1":
                for k, v in req.strategy_params.items():
                    if hasattr(config.smc, k):
                        setattr(config.smc, k, v)
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
                        
            config.instrument_settings = [InstrumentSettings(symbol=req.symbol, strategy_id=req.strategy_id)]
            
            engine_class = get_strategy(req.strategy_id)
            engine = engine_class(config)
            engine.is_backtesting = True

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

                for i in range(300, len(primary_times)):
                    if time.monotonic() - last_yield_time > 0.05:
                        await asyncio.sleep(0)
                        last_yield_time = time.monotonic()

                    if i % 600 == 0:
                        pct = int((i / len(primary_times)) * 80) + 10
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
                    is_warmup = req.start_date and current_time < np.datetime64(datetime.fromisoformat(req.start_date))

                    sig = None

                    for tf in required_tfs:
                        meta = TF_META.get(tf, TF_META["M5"])
                        sorted_tf = indexed_by_tf[tf]
                        tf_times = sorted_tf.index.values

                        if tf == primary_tf:
                            # Primary TF: use current bar index minus 1 (closed candle)
                            tf_end = i
                            tf_start_idx = max(0, tf_end - meta["window"])
                            slice_tf = sorted_tf.iloc[tf_start_idx:tf_end]
                            last_tf_time = primary_times[i]
                        else:
                            # HTF: find fully closed candle before current_time
                            np_td, np_unit = meta["np_td"]
                            cutoff = current_time - np.timedelta64(np_td, np_unit)
                            tf_end = int(np.searchsorted(tf_times, cutoff, side='right'))
                            tf_start_idx = max(0, tf_end - meta["window"])
                            slice_tf = sorted_tf.iloc[tf_start_idx:tf_end]
                            last_tf_time = tf_times[tf_end - 1] if tf_end > 0 else None

                        if len(slice_tf) < 20:
                            continue

                        if last_tf_time != prev_time_by_tf[tf]:
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
                            "confluence_score": sig.confluence_score,
                            "score_breakdown": sig.metadata.get("score_breakdown", {}),
                            "metadata": sig.metadata,
                        }
                        sigs.append(sig_dict)
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
                "max_daily_consecutive_losses": req.max_daily_consecutive_losses,
                "max_weekly_consecutive_losses": req.max_weekly_consecutive_losses,
                "max_consecutive_losses": req.max_consecutive_losses,
                "max_concurrent_positions": req.max_concurrent_positions,
                "max_positions_per_symbol": req.max_positions_per_symbol,
                "max_daily_trades": req.max_daily_trades,
                "target_profit_enabled": req.target_profit_enabled,
                "max_daily_profit": req.max_daily_profit,
                "max_weekly_profit": req.max_weekly_profit,
                "compounding_enabled": req.compounding_enabled,
                "manual_bias_overrides": req.manual_bias_overrides,
                "prop_firm": req.prop_firm,
                **req.risk_config,
            }

            current_state = await _get_state()
            current_state["progress"] = {"stage": "Finalizing backtest...", "pct": 95}
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
                compounding_enabled=req.compounding_enabled,
            )

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
                "run_logs": getattr(engine, 'run_logs', [])[-100:],  # Only keep last 100 to prevent UI/Redis freezing
                "params_snapshot": getattr(results, 'params_snapshot', req.strategy_params),
                "report": {
                    "win_rate": report.win_rate if report else 0,
                    "profit_factor": report.profit_factor if report else 0,
                    "sharpe_ratio": report.sharpe_ratio if report else 0,
                    "sortino_ratio": report.sortino_ratio if report else 0,
                    "max_drawdown_pct": report.max_drawdown_pct if report else 0,
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
            
            sanitized = _sanitize(response)
            
            current_state = await _get_state()
            current_state["status"] = "complete"
            current_state["progress"] = {"stage": "complete", "pct": 100}
            current_state["result"] = sanitized
            
            # Create a stripped payload for the frontend to prevent UI freezing
            import copy
            ws_payload = copy.deepcopy(sanitized)
            if "grouped_trades" in ws_payload:
                for t in ws_payload["grouped_trades"]:
                    t.pop("chart_data", None)
                    t.pop("chart_data_h1", None)
                    t.pop("chart_data_m15", None)
            
            # Broadcast IMMEDIATELY so the frontend gets the data even if Redis is slow/failing
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

    background_tasks.add_task(_run_backtest_task)
    return {"status": "started", "message": "Backtest queued and running in the background."}

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
            "smc_data": safe_json_loads(t.smc_data, {}),
            "sub_trades": sub_trades,
            "entry_snapshot_b64": ""
        })

    resp = {
        "run": {
            "id": run.id,
            "symbol": run.symbol,
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
            "bias_stats": params.get("bias_stats"),
            "confluence_stats": params.get("confluence_stats"),
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
        risk_report = generate_risk_report(grouped_trades_out)
        resp["report"] = dataclasses.asdict(risk_report)
        resp["session_win_rates"] = {
            "LONDON": risk_report.london_win_rate,
            "NY": risk_report.ny_win_rate,
            "OVERLAP": risk_report.overlap_win_rate,
            "ASIAN": risk_report.asian_win_rate,
            "UNKNOWN": risk_report.other_win_rate
        }
        
        # Inject saved fields that cannot be easily regenerated from just grouped_trades
        resp["report"]["bias_stats"] = params.get("bias_stats", {})
        resp["report"]["confluence_stats"] = params.get("confluence_stats", {})
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
    result = await db.execute(
        select(BacktestTrade)
        .where(BacktestTrade.backtest_id == backtest_id, BacktestTrade.id == group_id)
    )
    t = result.scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=404, detail="Trade not found")
        
    return {
        "chart_data": safe_json_loads(t.chart_data, []),
        "chart_data_m15": safe_json_loads(t.chart_data_m15, []),
        "chart_data_m5": safe_json_loads(t.chart_data_m5, [])
    }


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
    
    start_date = datetime.now(timezone.utc).replace(tzinfo=None)
    end_date = datetime.now(timezone.utc).replace(tzinfo=None)
    trades = data.get("grouped_trades", data.get("trades", []))
    if trades:
        try:
            start_date = datetime.fromisoformat(trades[0].get("entry_time")).replace(tzinfo=None)
            end_date = datetime.fromisoformat(trades[-1].get("exit_time") or trades[-1].get("entry_time")).replace(tzinfo=None)
        except:
            pass

    run = BacktestRun(
        id=backtest_id,
        user_id=current_user.id,
        strategy_id=data.get("strategy_id", "SMC_v1"),
        symbol=data.get("symbol", "Volatility 75 Index"),
        start_date=start_date,
        end_date=end_date,
        params_snapshot=json.dumps({
            **data.get("risk_config", {}),
            "bias_stats": report.get("bias_stats", {}),
            "confluence_stats": report.get("confluence_stats", {})
        }),
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
    )
    
    db.add(run)
    
    # Inject chart data back from state since frontend strips it to save bandwidth
    state_trades = []
    try:
        from backend.api.routes.backtest import USER_BACKTEST_STATE
        state = USER_BACKTEST_STATE.get(current_user.id)
        if state and state.get("result"):
            state_trades = state["result"].get("grouped_trades", [])
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
            confluence_score=t.get("confluence_score"),
            chart_data=json.dumps(t.get("chart_data", [])),
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
            sub_trades=json.dumps(t.get("sub_trades", []))
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
            "strategy_id": run.strategy_id,
            "initial_balance": initial_balance,
            "trades": trades_out
        })
        
    return {"data": response}
