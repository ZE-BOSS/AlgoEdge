"""
backend/backtester/runner.py

Backtest job execution and result persistence.
Source: TradingBot_MasterPlan-2.md Section 11
"""

import uuid
import json
from typing import Dict, Any, Optional
from datetime import datetime

import pandas as pd

from backend.backtester.engine import BacktestEngine
from backend.data.database import get_session
from backend.data.models import BacktestRun, BacktestTrade
from backend.utils.logger import get_logger

logger = get_logger(__name__)


async def run_backtest(
    user_id: str,
    strategy_id: str,
    symbol: str,
    candles: pd.DataFrame,
    signals: list,
    risk_config: Dict[str, Any],
    initial_balance: float = 10000.0,
    save_mode: str = "FULL",
) -> Dict[str, Any]:
    """
    Execute a backtest and optionally persist results to PostgreSQL.
    save_mode: "FULL", "SUMMARY", "DISCARD"
    Source: RiskManagement_Spec.md Section 7.3
    """
    engine = BacktestEngine(risk_config)
    results = engine.run(candles, signals, initial_balance)

    if save_mode == "DISCARD":
        logger.info("Backtest completed — results discarded")
        return results

    # Persist to database
    backtest_id = results["backtest_id"]
    report = results["report"]

    # Extract date range from candle data (time column is epoch seconds)
    if len(candles) > 0:
        if 'time' in candles.columns:
            from datetime import timezone
            start_date = datetime.fromtimestamp(int(candles['time'].iloc[0]), tz=timezone.utc)
            end_date = datetime.fromtimestamp(int(candles['time'].iloc[-1]), tz=timezone.utc)
        elif hasattr(candles.index[0], 'timestamp'):
            start_date = candles.index[0]
            end_date = candles.index[-1]
        else:
            start_date = datetime.now()
            end_date = datetime.now()
    else:
        start_date = datetime.now()
        end_date = datetime.now()

    run = BacktestRun(
        id=backtest_id,
        user_id=user_id,
        strategy_id=strategy_id,
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        params_snapshot=json.dumps(risk_config),
        total_trades=report.total_trades,
        win_rate=report.win_rate,
        profit_factor=report.profit_factor,
        sharpe_ratio=report.sharpe_ratio,
        max_drawdown_pct=report.max_drawdown_pct,
        total_pnl=report.total_pnl,
        tp1_hit_rate=report.tp1_hit_rate,
        tp2_hit_rate=report.tp2_hit_rate,
        tp3_hit_rate=report.tp3_hit_rate,
        be_hit_rate=report.be_hit_rate,
        trail_hit_rate=report.trail_hit_rate,
    )

    async with get_session() as session:
        session.add(run)

        if save_mode == "FULL":
            for trade_data in results["trades"]:
                bt_trade = BacktestTrade(
                    backtest_id=backtest_id,
                    symbol=trade_data.get("symbol", symbol),
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
                session.add(bt_trade)

    logger.info(f"Backtest saved: {backtest_id} ({save_mode})")
    return results
