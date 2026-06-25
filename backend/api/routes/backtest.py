"""
backend/api/routes/backtest.py

Backtest run, save/dismiss, load, delete endpoints.
Source: TradingBot_MasterPlan-2.md Section 6 — REST API
"""

import json
import uuid
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from sqlalchemy.orm import selectinload
from pydantic import BaseModel
from typing import Optional, Dict, Any
from datetime import datetime, timezone

from backend.data.database import get_db
from backend.data.models import BacktestRun, BacktestTrade, User
from backend.api.deps import get_current_user
from backend.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["backtest"])


class BacktestRequest(BaseModel):
    strategy_id: str = "SMC_v1"
    symbol: str
    timeframe: str = "H1"
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    candle_count: int = 5000
    initial_balance: float = 10000.0
    risk_config: Dict[str, Any] = {}
    # ── Strategy Params ──
    confluence_threshold: int = 55
    swing_length: int = 5
    ob_impulse_ratio: float = 1.5
    fvg_min_gap_pips: float = 3.0
    liq_sweep_min_pips: float = 2.0
    max_spread_pips: float = 3.0
    session_filter_enabled: bool = True
    news_filter_enabled: bool = True
    # ── Risk Params ──
    risk_per_trade_pct: float = 1.0
    min_rr: float = 3.0
    max_daily_consecutive_losses: int = 3
    max_weekly_consecutive_losses: int = 5
    max_consecutive_losses: int = 5
    max_concurrent_positions: int = 3
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


class SaveBacktestRequest(BaseModel):
    backtest_data: Dict[str, Any]
    save_mode: str = "FULL"  # "FULL" or "SUMMARY"




@router.post("/backtest")
async def run_backtest_endpoint(
    req: BacktestRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Run a backtest — returns results WITHOUT saving.
    User decides to save or dismiss after reviewing.
    """
    from backend.backtester.runner import run_backtest
    from backend.mt5.data_fetcher import DataFetcher, DataFetchError
    from backend.services.bot_service import bot_service
    import time as _time

    bt_start = _time.time()
    logger.info(f"═══ BACKTEST START ═══ {req.symbol} {req.timeframe} | user={current_user.email}")
    logger.info(f"  Config: balance=${req.initial_balance} | dates={req.start_date}→{req.end_date} | candles={req.candle_count} | tp_count={req.tp_count} | session_filter={req.session_filter_enabled}")
    bot_service.log_system_event(f"Backtest started: {req.symbol} {req.timeframe}", category="BACKTEST")

    # Fetch candles: use date range if provided, otherwise use candle_count
    try:
        if req.start_date and req.end_date:
            try:
                start = datetime.fromisoformat(req.start_date)
                end = datetime.fromisoformat(req.end_date)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD or ISO format.")
            logger.info(f"  Fetching data range: {start} → {end} for H4, H1, M15, M5")
            candles_h4 = await DataFetcher.get_data_range(req.symbol, "H4", start, end)
            candles_h1 = await DataFetcher.get_data_range(req.symbol, "H1", start, end)
            candles_m15 = await DataFetcher.get_data_range(req.symbol, "M15", start, end)
            candles_m5 = await DataFetcher.get_data_range(req.symbol, "M5", start, end)
        else:
            logger.info(f"  Fetching last {req.candle_count} candles for H4, H1, M15, M5")
            # If candle_count is provided, we fetch it for the base timeframe (M15), 
            # and fetch proportionally more for M5, less for H1/H4 so they align roughly in time.
            # However, DataFetcher limits apply, so we fetch count for all to ensure coverage.
            candles_h4 = await DataFetcher.get_historical_data(req.symbol, "H4", count=req.candle_count)
            candles_h1 = await DataFetcher.get_historical_data(req.symbol, "H1", count=req.candle_count)
            candles_m15 = await DataFetcher.get_historical_data(req.symbol, "M15", count=req.candle_count)
            candles_m5 = await DataFetcher.get_historical_data(req.symbol, "M5", count=req.candle_count)
    except DataFetchError as e:
        logger.error(f"  Data fetch failed: {e}")
        bot_service.log_system_event(f"Backtest data fetch failed: {e.reason}", category="BACKTEST", level="ERROR")
        raise HTTPException(status_code=400, detail=str(e))

    if any(c is None or c.empty for c in [candles_h4, candles_h1, candles_m15, candles_m5]):
        logger.warning(f"  Incomplete MTF data available for {req.symbol} — aborting backtest")
        raise HTTPException(status_code=400, detail="Incomplete MTF data available for backtest")

    logger.info(f"  Fetched MTF candles successfully.")

    # Generate signals using the unified SMCEngine
    import pandas as pd
    from backend.strategies.smc.engine import SMCEngine
    from backend.strategies.smc.params import UserConfig
    
    config = UserConfig()
    config.smc.min_signal_score = req.confluence_threshold
    config.smc.swing_length_htf = req.swing_length
    config.smc.ob_impulse_min_ratio = req.ob_impulse_ratio
    config.smc.fvg_min_gap_pips = req.fvg_min_gap_pips
    config.smc.liq_sweep_min_pips = req.liq_sweep_min_pips
    config.smc.session_filter_enabled = req.session_filter_enabled
    config.smc.news_filter_enabled = req.news_filter_enabled
    engine = SMCEngine(config)
    engine.is_backtesting = True

    def _index_candles(df):
        if 'time' in df.columns:
            return df.set_index(pd.to_datetime(df['time'], unit='s'))
        return df
        
    candles_h4_idx = _index_candles(candles_h4)
    candles_h1_idx = _index_candles(candles_h1)
    candles_m15_idx = _index_candles(candles_m15)
    candles_m5_idx = _index_candles(candles_m5)
    
    # In order to simulate time progression, we iterate over the M15 timeframe (primary).
    # We pass the data slices to the engine to simulate historical state.
    signals = []
    
    async def generate_signals_simulated():
        sigs = []
        prev_h4_time = None
        prev_h1_time = None
        prev_m5_time = None
        
        import asyncio
        
        # Sort indices once just to be perfectly safe for .loc slicing
        candles_h4_sorted = candles_h4_idx.sort_index()
        candles_h1_sorted = candles_h1_idx.sort_index()
        candles_m5_sorted = candles_m5_idx.sort_index()

        for i in range(100, len(candles_m15_idx)):
            # Yield to event loop periodically to prevent blocking the server!
            if i % 10 == 0:
                await asyncio.sleep(0)

            current_time = candles_m15_idx.index[i]
            
            # Use rolling windows to prevent O(N^2) explosion during backtest
            start_h4 = current_time - pd.Timedelta(days=200)
            start_h1 = current_time - pd.Timedelta(days=50)
            start_m15_idx = max(0, i - 1000)
            start_m5 = current_time - pd.Timedelta(days=10)
            
            # FAST SLICING using .loc on sorted datetime index
            slice_h4 = candles_h4_sorted.loc[start_h4:current_time]
            slice_h1 = candles_h1_sorted.loc[start_h1:current_time]
            slice_m15 = candles_m15_idx.iloc[start_m15_idx:i+1]
            slice_m5 = candles_m5_sorted.loc[start_m5:current_time]
            
            if len(slice_h4) < 20 or len(slice_h1) < 20 or len(slice_m5) < 20:
                continue
                
            # CACHING: Only evaluate HTF if a new HTF candle has appeared
            last_h4_time = slice_h4.index[-1]
            if last_h4_time != prev_h4_time:
                await engine.on_bar(req.symbol, "H4", slice_h4)
                prev_h4_time = last_h4_time
                
            last_h1_time = slice_h1.index[-1]
            if last_h1_time != prev_h1_time:
                await engine.on_bar(req.symbol, "H1", slice_h1)
                prev_h1_time = last_h1_time
                
            # Primary timeframe updates every step
            await engine.on_bar(req.symbol, "M15", slice_m15)
            
            # M5 updates
            last_m5_time = slice_m5.index[-1]
            sig = None
            if last_m5_time != prev_m5_time:
                sig = await engine.on_bar(req.symbol, "M5", slice_m5)
                prev_m5_time = last_m5_time
            
            if sig:
                sig_dict = {
                    "symbol": sig.symbol,
                    "direction": sig.direction,
                    "time": int(current_time.timestamp()),
                    "entry_price": sig.entry_price,
                    "stop_loss": sig.stop_loss,
                    "take_profit": sig.take_profit,
                    "confluence_score": sig.confluence_score,
                    "score_breakdown": sig.metadata.get("score_breakdown", {}),
                    "metadata": sig.metadata,
                }
                sigs.append(sig_dict)
        return sigs

    logger.info(f"  Generating signals by simulating SMCEngine over {len(candles_m15_idx)} bars...")
    # Wrap in to_thread if it's CPU bound, but since it has awaits it must run in the event loop.
    # It's an async generator loop now.
    signals = await generate_signals_simulated()
    
    logger.info(f"  Generated {len(signals)} signals")
    
    # We need to pass the base timeframe candles to the BacktestEngine so it can walk through prices.
    # The M15 timeframe is ideal as the primary risk management ticker.
    candles = candles_m15

    # Build merged risk config with ALL override fields from the backtest form
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
        "compounding_enabled": req.compounding_enabled,
        **req.risk_config,  # Any extra overrides from risk_config dict
    }

    logger.info(f"  Running backtest engine...")
    results = await run_backtest(
        user_id=current_user.id,
        strategy_id=req.strategy_id,
        symbol=req.symbol,
        candles=candles,
        signals=signals,
        risk_config=merged_risk_config,
        initial_balance=req.initial_balance,
        save_mode="DISCARD",  # Never auto-save — user decides
    )

    report = results.get("report")
    elapsed = (_time.time() - bt_start) * 1000
    pnl = results.get('final_balance', 0) - req.initial_balance
    wr = (report.win_rate if report else 0) * 100
    logger.info(f"═══ BACKTEST COMPLETE ═══ {req.symbol} | {results['total_trades']} trades | "
                f"P&L=${pnl:.2f} | WR={wr:.1f}% | {elapsed:.0f}ms")
    bot_service.log_system_event(
        f"Backtest complete: {req.symbol} | {results['total_trades']} trades | P&L=${pnl:.2f} | WR={wr:.1f}%",
        category="BACKTEST"
    )

    # ── Sanitize numpy types to native Python for JSON serialization ──
    import numpy as _np

    def _sanitize(obj):
        """Recursively convert numpy types to native Python types."""
        if isinstance(obj, dict):
            return {k: _sanitize(v) for k, v in obj.items()}
        elif isinstance(obj, (list, tuple)):
            return [_sanitize(v) for v in obj]
        elif isinstance(obj, (_np.bool_, bool)):
            return bool(obj)
        elif isinstance(obj, (_np.integer,)):
            return int(obj)
        elif isinstance(obj, (_np.floating,)):
            return float(obj)
        elif isinstance(obj, _np.ndarray):
            return obj.tolist()
        return obj

    # Return full results for frontend display
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
        "run_logs": engine.run_logs,
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
        },
    }

    return _sanitize(response)


@router.post("/backtests/{backtest_id}/save")
async def save_backtest(
    backtest_id: str,
    req: SaveBacktestRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """User explicitly saves a backtest after reviewing results."""

    def _epoch_to_dt(val):
        """Convert epoch int/float to datetime, pass through datetime/None."""
        if val is None:
            return None
        if isinstance(val, (int, float)):
            return datetime.fromtimestamp(val, tz=timezone.utc)
        if isinstance(val, str):
            try:
                return datetime.fromisoformat(val.replace('Z', '+00:00'))
            except ValueError:
                return None
        return val

    data = req.backtest_data
    report = data.get("report", {})

    run = BacktestRun(
        id=backtest_id,
        user_id=current_user.id,
        strategy_id=data.get("strategy_id", "SMC_v1"),
        symbol=data.get("symbol", ""),
        start_date=_epoch_to_dt(data.get("start_date")) or datetime.now(timezone.utc),
        end_date=_epoch_to_dt(data.get("end_date")) or datetime.now(timezone.utc),
        params_snapshot=json.dumps(data.get("risk_config", {})),
        total_trades=data.get("total_trades", 0),
        win_rate=report.get("win_rate", 0),
        profit_factor=report.get("profit_factor", 0),
        sharpe_ratio=report.get("sharpe_ratio", 0),
        max_drawdown_pct=report.get("max_drawdown_pct", 0),
        total_pnl=report.get("total_pnl", 0),
        tp1_hit_rate=report.get("tp1_hit_rate", 0),
        tp2_hit_rate=report.get("tp2_hit_rate", 0),
        tp3_hit_rate=report.get("tp3_hit_rate", 0),
        tp4_hit_rate=report.get("tp4_hit_rate", 0),
        tp5_hit_rate=report.get("tp5_hit_rate", 0),
        be_hit_rate=report.get("be_hit_rate", 0),
        trail_hit_rate=report.get("trail_hit_rate", 0),
        run_logs=json.dumps(data.get("run_logs", [])),
    )
    db.add(run)

    if req.save_mode == "FULL":
        for trade_data in data.get("trades", []):
            bt_trade = BacktestTrade(
                backtest_id=backtest_id,
                symbol=trade_data.get("symbol", ""),
                direction=trade_data.get("direction"),
                entry_price=trade_data.get("entry_price"),
                exit_price=trade_data.get("exit_price"),
                stop_loss=trade_data.get("stop_loss"),
                entry_time=_epoch_to_dt(trade_data.get("entry_time")),
                exit_time=_epoch_to_dt(trade_data.get("exit_time")),
                tp_level_hit=trade_data.get("tp_level"),
                exit_reason=trade_data.get("exit_reason"),
                pnl=trade_data.get("pnl"),
                be_applied=trade_data.get("be_applied", False),
                trail_method=trade_data.get("trail_method"),
                mae_pips=trade_data.get("mae_pips"),
                mfe_pips=trade_data.get("mfe_pips"),
                confluence_score=trade_data.get("confluence_score"),
            )
            db.add(bt_trade)

    await db.commit()
    logger.info(f"Backtest saved: {backtest_id} ({req.save_mode})")
    return {"saved": True, "backtest_id": backtest_id}


@router.get("/backtests")
async def list_backtests(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all saved backtests for the authenticated user."""
    result = await db.execute(
        select(BacktestRun)
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
    } for r in runs]


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
    balance = 10000.0  # Default; should come from params_snapshot
    try:
        params = json.loads(run.params_snapshot) if run.params_snapshot else {}
        balance = params.get("initial_balance", 10000.0)
    except (json.JSONDecodeError, TypeError):
        pass

    equity_curve.append(balance)
    for t in run.trades:
        balance += (t.pnl or 0)
        equity_curve.append(round(balance, 2))

    # TP distribution
    tp_dist = {"TP1": 0, "TP2": 0, "TP3": 0, "TP4": 0, "TP5": 0, "SL": 0, "TRAIL": 0, "BE": 0}
    session_stats = {"LONDON": {"wins": 0, "total": 0}, "NY": {"wins": 0, "total": 0}, "OVERLAP": {"wins": 0, "total": 0}}

    for t in run.trades:
        reason = t.exit_reason or ""
        if reason in tp_dist:
            tp_dist[reason] += 1
        elif reason == "SL":
            tp_dist["SL"] += 1

        # Session breakdown (if available)
        session = getattr(t, "session", None) or ""
        if session in session_stats:
            session_stats[session]["total"] += 1
            if (t.pnl or 0) > 0:
                session_stats[session]["wins"] += 1

    session_win_rates = {}
    for sess, data in session_stats.items():
        session_win_rates[sess] = data["wins"] / data["total"] if data["total"] > 0 else 0

    return {
        "run": {
            "id": run.id,
            "symbol": run.symbol,
            "strategy_id": run.strategy_id,
            "total_trades": run.total_trades,
            "win_rate": run.win_rate,
            "total_pnl": run.total_pnl,
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
        },
        "trades": [{
            "symbol": t.symbol,
            "direction": t.direction,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "stop_loss": t.stop_loss,
            "pnl": t.pnl,
            "exit_reason": t.exit_reason,
            "tp_level_hit": t.tp_level_hit,
            "entry_time": t.entry_time,
            "exit_time": t.exit_time,
            "be_applied": t.be_applied,
            "trail_method": t.trail_method,
            "mae_pips": t.mae_pips,
            "mfe_pips": t.mfe_pips,
            "confluence_score": t.confluence_score,
        } for t in run.trades],
        "equity_curve": equity_curve,
        "tp_distribution": tp_dist,
        "session_win_rates": session_win_rates,
        "run_logs": json.loads(run.run_logs) if run.run_logs else [],
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
    return {"deleted": True}
