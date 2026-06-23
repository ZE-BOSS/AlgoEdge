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
    risk_config: Dict[str, Any] = {}
    initial_balance: float = 10000.0


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
    from backend.mt5.data_fetcher import DataFetcher

    candles = await DataFetcher.get_historical_data(req.symbol, req.timeframe, count=5000)
    if candles is None or candles.empty:
        raise HTTPException(status_code=400, detail="No data available for backtest")

    results = await run_backtest(
        user_id=current_user.id,
        strategy_id=req.strategy_id,
        symbol=req.symbol,
        candles=candles,
        signals=[],
        risk_config=req.risk_config,
        initial_balance=req.initial_balance,
        save_mode="DISCARD",  # Never auto-save — user decides
    )

    report = results.get("report")

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
        start_date=data.get("start_date"),
        end_date=data.get("end_date"),
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
