"""
backend/api/routes/backtest.py

Backtest run, save/dismiss, load, delete endpoints.
Source: TradingBot_MasterPlan-2.md Section 6 — REST API
"""

import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from sqlalchemy.orm import selectinload
from pydantic import BaseModel
from typing import Optional, Dict, Any
from datetime import datetime

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
    risk_config: Dict[str, Any] = {}
    initial_balance: float = 10000.0
    tp_count: int = 3                    # How many TPs (1-5)
    session_filter_enabled: bool = True   # Enable/disable session filter


class SaveBacktestRequest(BaseModel):
    backtest_data: Dict[str, Any]
    save_mode: str = "FULL"  # "FULL" or "SUMMARY"


def _generate_signals_from_candles(candles, symbol: str, timeframe: str) -> list:
    """
    Run a simplified SMC-style signal generation on historical candles.
    Returns a list of signal dicts compatible with BacktestEngine.
    Each signal has: time (bar index), symbol, direction, entry_price, stop_loss, confluence_score.
    """
    import pandas as pd
    import numpy as np

    signals = []
    if len(candles) < 50:
        return signals

    # Use closing prices for structure detection
    closes = candles['close'].values
    highs = candles['high'].values
    lows = candles['low'].values

    swing_len = 5

    for i in range(swing_len * 2 + 10, len(candles) - 1):
        # Simple swing high/low detection
        window_highs = highs[i - swing_len:i]
        window_lows = lows[i - swing_len:i]

        is_swing_high = highs[i - swing_len] == max(window_highs)
        is_swing_low = lows[i - swing_len] == min(window_lows)

        if not (is_swing_high or is_swing_low):
            continue

        # Determine bias from recent price action
        recent_close = closes[i]
        lookback_close = closes[i - 20]
        bias = "BUY" if recent_close > lookback_close else "SELL"

        # Check for order block (strong move away from zone)
        body_sizes = abs(closes[i-3:i] - candles['open'].values[i-3:i])
        avg_body = np.mean(body_sizes) if len(body_sizes) > 0 else 0

        # Only take signals with reasonable candle bodies (filtering noise)
        current_body = abs(closes[i] - candles['open'].values[i])
        if avg_body == 0 or current_body < avg_body * 0.5:
            continue

        # Calculate entry, SL, TP
        entry = closes[i]
        atr_period = min(14, i)
        atr = np.mean(highs[i-atr_period:i] - lows[i-atr_period:i])
        if atr == 0:
            continue

        if bias == "BUY":
            sl = entry - atr * 1.5
            tp = entry + atr * 4.5  # 3R minimum
        else:
            sl = entry + atr * 1.5
            tp = entry - atr * 4.5

        # Confluence score (simplified)
        score = 50
        # Trend alignment
        if bias == "BUY" and closes[i] > np.mean(closes[max(0,i-50):i]):
            score += 15
        elif bias == "SELL" and closes[i] < np.mean(closes[max(0,i-50):i]):
            score += 15
        # Swing structure
        if is_swing_low and bias == "BUY":
            score += 10
        elif is_swing_high and bias == "SELL":
            score += 10

        # Only take high-confluence signals (avoid spam)
        if score < 60:
            continue

        # Throttle: no signal within 5 bars of last one
        if signals and i - signals[-1]["time"] < 5:
            continue

        signals.append({
            "time": i,
            "symbol": symbol,
            "direction": bias,
            "entry_price": float(entry),
            "stop_loss": float(sl),
            "take_profit": float(tp),
            "confluence_score": score,
        })

    logger.info(f"Generated {len(signals)} signals from {len(candles)} candles for {symbol}")
    return signals


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
            logger.info(f"  Fetching data range: {start} → {end}")
            candles = await DataFetcher.get_data_range(req.symbol, req.timeframe, start, end)
        else:
            logger.info(f"  Fetching last {req.candle_count} candles")
            candles = await DataFetcher.get_historical_data(req.symbol, req.timeframe, count=req.candle_count)
    except DataFetchError as e:
        logger.error(f"  Data fetch failed: {e}")
        bot_service.log_system_event(f"Backtest data fetch failed: {e.reason}", category="BACKTEST", level="ERROR")
        raise HTTPException(status_code=400, detail=str(e))

    if candles is None or candles.empty:
        logger.warning(f"  No data available for {req.symbol} — aborting backtest")
        raise HTTPException(status_code=400, detail="No data available for backtest")

    logger.info(f"  Fetched {len(candles)} candles")

    # Generate signals from the SMC strategy engine on historical data
    logger.info(f"  Generating signals from candle data...")
    signals = _generate_signals_from_candles(candles, req.symbol, req.timeframe)
    logger.info(f"  Generated {len(signals)} signals")

    # Merge tp_count and session_filter into risk_config
    merged_risk_config = {
        **req.risk_config,
        "tp_count": req.tp_count,
        "session_filter_enabled": req.session_filter_enabled,
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

    # Return full results for frontend display
    return {
        "backtest_id": results["backtest_id"],
        "initial_balance": results["initial_balance"],
        "final_balance": results["final_balance"],
        "total_trades": results["total_trades"],
        "equity_curve": results.get("equity_curve", []),
        "trades": results.get("trades", []),
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


@router.post("/backtests/{backtest_id}/save")
async def save_backtest(
    backtest_id: str,
    req: SaveBacktestRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """User explicitly saves a backtest after reviewing results."""
    data = req.backtest_data
    report = data.get("report", {})

    run = BacktestRun(
        id=backtest_id,
        user_id=current_user.id,
        strategy_id=data.get("strategy_id", "SMC_v1"),
        symbol=data.get("symbol", ""),
        start_date=data.get("start_date") or datetime.now(),
        end_date=data.get("end_date") or datetime.now(),
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
                entry_time=trade_data.get("entry_time"),
                exit_time=trade_data.get("exit_time"),
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
