"""
backend/backtester/runner.py

Backtest job execution and result persistence.
Broadcasts real-time progress via WebSocket during backtest execution.
Source: TradingBot_MasterPlan-2.md Section 11
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from backend.backtester.engine import BacktestEngine, _epoch_to_iso
from backend.data.database import get_session
from backend.data.models import BacktestRun, BacktestTrade
from backend.utils.logger import get_logger

logger = get_logger(__name__)


def _json_default(obj):
    """
    Fallback serializer for json.dumps() when persisting nested trade data
    (sub_trades, original_signal, chart_data, ...) which can contain
    datetime/pandas Timestamp/numpy scalar values that json can't handle
    natively.
    """
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if hasattr(obj, "item"):  # numpy scalar (np.int64, np.float64, ...)
        return obj.item()
    return str(obj)


def _slim_sub_trades(sub_trades: list) -> list:
    """
    Strip heavy/duplicative fields before persisting a group's sub_trades.

    Each TP-level leg carries a full copy of `original_signal` (which nests
    `metadata`, potentially including a base64 chart snapshot) and its own
    `entry_snapshot_b64` — all identical across every leg of the same group.
    None of this is read back from sub_trades by the frontend (chart_data /
    smc_data / entry_snapshot_b64 are already surfaced once at the group
    level by trade_grouper.py). Persisting it per-leg multiplies storage for
    no benefit, so drop it here rather than at read time.
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


async def _broadcast_progress(user_id: str, progress: dict):
    """Send backtest progress event to the user via WebSocket."""
    try:
        from backend.api.websocket import manager as ws_manager
        await ws_manager.broadcast_to_user(user_id, {
            "type": "backtest_progress",
            **progress,
        })
    except Exception:
        pass  # WebSocket may not be connected

async def _broadcast_notification(user_id: str, title: str, message: str, notification_type: str = "info"):
    """Broadcast an explicit frontend/browser notification to a specific user."""
    try:
        from datetime import datetime, timezone

        from backend.api.websocket import manager as ws_manager
        await ws_manager.broadcast_to_user(user_id, {
            "type": "notification",
            "payload": {
                "title": title,
                "message": message,
                "type": notification_type,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        })
    except Exception:
        pass


async def run_backtest(
    user_id: str,
    strategy_id: str,
    symbol: str,
    candles: pd.DataFrame,
    signals: list[dict[str, Any]],
    risk_config: dict[str, Any],
    initial_balance: float = 10000.0,
    save_mode: str = "FULL",
    candles_m15: pd.DataFrame = None,
    candles_m5: pd.DataFrame = None,
    compounding_enabled: bool = False,
    title: str | None = None,
) -> dict[str, Any]:
    """
    Execute a backtest and optionally persist results to PostgreSQL.
    save_mode: "FULL", "SUMMARY", "DISCARD"
    Source: RiskManagement_Spec.md Section 7.3
    """
    # Broadcast: starting
    await _broadcast_progress(user_id, {
        "stage": "starting",
        "symbol": symbol,
        "total_candles": len(candles),
        "total_signals": len(signals),
        "pct": 0,
    })

    engine = BacktestEngine(risk_config)

    # Broadcast: running engine
    await _broadcast_progress(user_id, {
        "stage": "running",
        "pct": 10,
        "message": f"Processing {len(candles)} candles with {len(signals)} signals...",
    })

    # Run CPU-bound engine in a thread pool so it doesn't block the event loop
    # (without this, ALL other API requests hang until the backtest finishes)
    import asyncio
    results = await asyncio.to_thread(engine.run, candles, signals, initial_balance, candles_m15, candles_m5, compounding_enabled)

    # Broadcast: engine complete
    await _broadcast_progress(user_id, {
        "stage": "engine_complete",
        "pct": 70,
        "total_trades": results["total_trades"],
        "message": f"Engine complete — {results['total_trades']} trades simulated",
    })

    # Format all trade timestamps to ISO strings
    for trade in results.get("trades", []):
        if "entry_time" in trade and "entry_time_iso" not in trade:
            trade["entry_time_iso"] = _epoch_to_iso(trade["entry_time"])
        if "exit_time" in trade and "exit_time_iso" not in trade:
            trade["exit_time_iso"] = _epoch_to_iso(trade["exit_time"])

    if save_mode == "DISCARD":
        await _broadcast_progress(user_id, {
            "stage": "complete",
            "pct": 100,
            "message": "Backtest complete — results ready for review",
        })
        logger.info("Backtest completed — results discarded")
        
        asyncio.create_task(_broadcast_notification(
            user_id,
            "Backtest Complete",
            f"Simulated {results.get('total_trades', 0)} trades. Final P&L: ${results.get('total_pnl', 0):.2f}.",
            "success"
        ))
        return results

    # Persist to database
    await _broadcast_progress(user_id, {
        "stage": "saving",
        "pct": 80,
        "message": "Saving results to database...",
    })

    backtest_id = results["backtest_id"]
    report = results["report"]

    # Extract date range from candle data (time column is epoch seconds)
    if len(candles) > 0:
        if 'time' in candles.columns:
            start_date = datetime.fromtimestamp(int(candles['time'].iloc[0]), tz=timezone.utc)
            end_date = datetime.fromtimestamp(int(candles['time'].iloc[-1]), tz=timezone.utc)
        elif hasattr(candles.index[0], 'timestamp'):
            start_date = candles.index[0]
            end_date = candles.index[-1]
        else:
            start_date = datetime.now(timezone.utc)
            end_date = datetime.now(timezone.utc)
    else:
        start_date = datetime.now(timezone.utc)
        end_date = datetime.now(timezone.utc)

    run = BacktestRun(
        id=backtest_id,
        user_id=user_id,
        strategy_id=strategy_id,
        symbol=symbol,
        title=(title or "").strip() or symbol,
        start_date=start_date,
        end_date=end_date,
        params_snapshot=json.dumps(risk_config),
        total_trades=report.total_trades,
        win_rate=report.win_rate,
        profit_factor=report.profit_factor,
        sharpe_ratio=report.sharpe_ratio,
        sortino_ratio=report.sortino_ratio,
        expectancy_r=report.expectancy_r,
        max_drawdown_pct=report.max_drawdown_pct,
        total_pnl=report.total_pnl,
        tp1_hit_rate=report.tp1_hit_rate,
        tp2_hit_rate=report.tp2_hit_rate,
        tp3_hit_rate=report.tp3_hit_rate,
        tp4_hit_rate=report.tp4_hit_rate,
        tp5_hit_rate=report.tp5_hit_rate,
        be_hit_rate=report.be_hit_rate,
        sl_hit_rate=report.sl_hit_rate,
        trail_hit_rate=report.trail_hit_rate,
        rejection_funnel=report.rejection_funnel,
        bias_stats=report.bias_stats,
        confluence_stats=report.confluence_stats,
        run_logs=json.dumps(results.get("run_logs", []), default=_json_default),
    )

    async with get_session() as session:
        session.add(run)

        if save_mode == "FULL":
            for trade_data in results["grouped_trades"]:
                bt_trade = BacktestTrade(
                    id=uuid.uuid4().int & ((1 << 63) - 1),  # generate_id()'s default only has 100 values/ms — unsafe for a tight bulk-insert loop like this one
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
                    session=trade_data.get("entry_session", "UNKNOWN"),
                    strategy_id=trade_data.get("strategy_id", trade_data.get("strategy", strategy_id)),
                    balance_before=trade_data.get("balance_before"),
                    balance_after=trade_data.get("balance_after"),
                    # Was reading trade_data["score_breakdown"], a key that
                    # doesn't exist on grouped trades — smc_data (the actual
                    # OB/FVG/marker zones drawn on the chart, produced by
                    # trade_grouper.group_trades) was being silently dropped.
                    smc_data=json.dumps(trade_data.get("smc_data") or {}, default=_json_default),
                    # Previously never set at all — without these, saved
                    # backtests can never render a trade chart, and without
                    # sub_trades the "expand trade" panel (entry confirmations,
                    # exit info, TP breakdown, confluence score) has nothing
                    # to key off, since the frontend reads
                    # group.sub_trades[0].entry_confirmations etc.
                    chart_data=json.dumps(trade_data.get("chart_data") or [], default=_json_default),
                    chart_data_m15=json.dumps(trade_data.get("chart_data_m15") or [], default=_json_default),
                    chart_data_m5=json.dumps(trade_data.get("chart_data_m5") or [], default=_json_default),
                    sub_trades=json.dumps(_slim_sub_trades(trade_data.get("sub_trades")), default=_json_default),
                )
                session.add(bt_trade)

    await _broadcast_progress(user_id, {
        "stage": "complete",
        "pct": 100,
        "message": f"Backtest saved — {report.total_trades} trades, PnL: ${report.total_pnl:.2f}",
    })

    logger.info(f"Backtest saved: {backtest_id} ({save_mode})")
    
    import asyncio
    asyncio.create_task(_broadcast_notification(
        user_id,
        "Backtest Complete",
        f"Simulated {report.total_trades} trades. Final P&L: ${report.total_pnl:.2f}.",
        "success"
    ))
    return results