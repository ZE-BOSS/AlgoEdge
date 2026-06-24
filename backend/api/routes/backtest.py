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
    max_daily_loss_pct: float = 5.0
    max_weekly_loss_pct: float = 10.0
    max_consecutive_losses: int = 5
    max_concurrent_positions: int = 3
    # ── TP Config ──
    tp_count: int = 3
    tp1_rr: float = 3.0
    tp2_rr: float = 5.0
    tp3_rr: float = 7.0
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


def _generate_signals_from_candles(candles, symbol: str, timeframe: str,
                                    strategy_config: dict = None) -> list:
    """
    SMC signal generation using configurable strategy parameters.
    Uses confluence_threshold, fvg_min_gap_pips, liq_sweep_min_pips,
    ob_impulse_ratio from strategy_config.
    """
    import pandas as pd
    import numpy as np
    from backend.risk.position_sizer import get_pip_size

    cfg = strategy_config or {}
    confluence_threshold = cfg.get("confluence_threshold", 55)
    swing_len = cfg.get("swing_length", 5)
    ob_impulse = cfg.get("ob_impulse_ratio", 1.5)
    fvg_min_pips = cfg.get("fvg_min_gap_pips", 3.0)
    liq_min_pips = cfg.get("liq_sweep_min_pips", 2.0)
    pip_size = get_pip_size(symbol)

    signals = []
    if len(candles) < 50:
        return signals

    closes = candles['close'].values
    highs = candles['high'].values
    lows = candles['low'].values
    opens = candles['open'].values

    swing_len = 5
    # Calculate rolling ATR for proper SL distance
    atr_period = 14

    for i in range(max(swing_len * 2 + 10, atr_period + 5), len(candles) - 1):
        # Calculate ATR at this bar
        tr_values = []
        for j in range(max(0, i - atr_period), i):
            tr = max(
                highs[j] - lows[j],
                abs(highs[j] - closes[j-1]) if j > 0 else highs[j] - lows[j],
                abs(lows[j] - closes[j-1]) if j > 0 else highs[j] - lows[j],
            )
            tr_values.append(tr)
        atr = np.mean(tr_values) if tr_values else 0
        if atr == 0:
            continue

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
        body_sizes = abs(closes[i-3:i] - opens[i-3:i])
        avg_body = np.mean(body_sizes) if len(body_sizes) > 0 else 0

        # Only take signals with reasonable candle bodies (filtering noise)
        current_body = abs(closes[i] - opens[i])
        if avg_body == 0 or current_body < avg_body * 0.3:
            continue

        # Calculate entry, SL with MINIMUM distance enforcement
        entry = closes[i]
        sl_distance = max(atr * 1.5, entry * 0.002)  # At least 1.5×ATR or 0.2% of price

        if bias == "BUY":
            sl = entry - sl_distance
            # Validate: SL must be below entry
            if sl >= entry:
                continue
        else:
            sl = entry + sl_distance
            # Validate: SL must be above entry
            if sl <= entry:
                continue

        # ── Detailed SMC Confirmation Analysis ──
        score = 0
        confirmations = []
        score_breakdown = []

        # 1. Trend Alignment (SMA50)
        sma50 = np.mean(closes[max(0,i-50):i])
        if bias == "BUY" and closes[i] > sma50:
            score += 15
            score_breakdown.append("Trend Alignment: +15 (price above SMA50)")
            confirmations.append(f"✓ Trend: BULLISH — Price {closes[i]:.2f} above SMA50 {sma50:.2f}")
        elif bias == "SELL" and closes[i] < sma50:
            score += 15
            score_breakdown.append("Trend Alignment: +15 (price below SMA50)")
            confirmations.append(f"✓ Trend: BEARISH — Price {closes[i]:.2f} below SMA50 {sma50:.2f}")
        else:
            score_breakdown.append("Trend Alignment: +0 (counter-trend)")
            confirmations.append(f"✗ Trend: Counter-trend signal")

        # 2. Swing Structure
        if is_swing_low and bias == "BUY":
            score += 15
            score_breakdown.append("Swing Structure: +15 (swing low + BUY)")
            confirmations.append(f"✓ Structure: Swing Low at {lows[i-swing_len]:.2f} — demand zone")
        elif is_swing_high and bias == "SELL":
            score += 15
            score_breakdown.append("Swing Structure: +15 (swing high + SELL)")
            confirmations.append(f"✓ Structure: Swing High at {highs[i-swing_len]:.2f} — supply zone")
        else:
            score += 5
            score_breakdown.append("Swing Structure: +5 (swing present, direction mismatch)")
            confirmations.append(f"△ Structure: Swing detected but not aligned with {bias}")

        # 3. Fair Value Gap (FVG) Detection — with minimum gap size
        has_fvg = False
        if i >= 3:
            if bias == "BUY" and lows[i] > highs[i-2]:
                gap_size = (lows[i] - highs[i-2]) / pip_size
                if gap_size >= fvg_min_pips:
                    has_fvg = True
                    score += 10
                    score_breakdown.append(f"FVG: +10 (bullish gap {gap_size:.1f} pips)")
                    confirmations.append(f"✓ FVG: Bullish gap {gap_size:.1f} pips (min: {fvg_min_pips})")
                else:
                    score_breakdown.append(f"FVG: +0 (gap {gap_size:.1f} < min {fvg_min_pips} pips)")
                    confirmations.append(f"✗ FVG: Gap too small ({gap_size:.1f} < {fvg_min_pips} pips)")
            elif bias == "SELL" and highs[i] < lows[i-2]:
                gap_size = (lows[i-2] - highs[i]) / pip_size
                if gap_size >= fvg_min_pips:
                    has_fvg = True
                    score += 10
                    score_breakdown.append(f"FVG: +10 (bearish gap {gap_size:.1f} pips)")
                    confirmations.append(f"✓ FVG: Bearish gap {gap_size:.1f} pips (min: {fvg_min_pips})")
                else:
                    score_breakdown.append(f"FVG: +0 (gap {gap_size:.1f} < min {fvg_min_pips} pips)")
                    confirmations.append(f"✗ FVG: Gap too small ({gap_size:.1f} < {fvg_min_pips} pips)")
            else:
                score_breakdown.append("FVG: +0 (no gap)")
                confirmations.append("✗ FVG: No fair value gap detected")

        # 4. Liquidity Sweep Detection — with minimum sweep size
        has_sweep = False
        if i >= 10:
            recent_low = min(lows[i-10:i-1])
            recent_high = max(highs[i-10:i-1])
            if bias == "BUY" and lows[i-1] < recent_low and closes[i] > opens[i]:
                sweep_size = (recent_low - lows[i-1]) / pip_size
                if sweep_size >= liq_min_pips:
                    has_sweep = True
                    score += 15
                    score_breakdown.append(f"Liquidity Sweep: +15 (swept {sweep_size:.1f} pips below)")
                    confirmations.append(f"✓ Liquidity: Swept low by {sweep_size:.1f} pips then reclaimed")
                else:
                    score_breakdown.append(f"Liquidity Sweep: +0 (sweep {sweep_size:.1f} < min {liq_min_pips})")
                    confirmations.append(f"✗ Liquidity: Sweep too small ({sweep_size:.1f} < {liq_min_pips} pips)")
            elif bias == "SELL" and highs[i-1] > recent_high and closes[i] < opens[i]:
                sweep_size = (highs[i-1] - recent_high) / pip_size
                if sweep_size >= liq_min_pips:
                    has_sweep = True
                    score += 15
                    score_breakdown.append(f"Liquidity Sweep: +15 (swept {sweep_size:.1f} pips above)")
                    confirmations.append(f"✓ Liquidity: Swept high by {sweep_size:.1f} pips then reclaimed")
                else:
                    score_breakdown.append(f"Liquidity Sweep: +0 (sweep {sweep_size:.1f} < min {liq_min_pips})")
                    confirmations.append(f"✗ Liquidity: Sweep too small ({sweep_size:.1f} < {liq_min_pips} pips)")
            else:
                score_breakdown.append("Liquidity Sweep: +0 (no sweep)")
                confirmations.append("✗ Liquidity: No sweep detected in last 10 bars")

        # 5. Candlestick Pattern
        pattern_name = "None"
        c_open, c_close, c_high, c_low = opens[i], closes[i], highs[i], lows[i]
        body = abs(c_close - c_open)
        upper_wick = c_high - max(c_open, c_close)
        lower_wick = min(c_open, c_close) - c_low
        total_range = c_high - c_low if c_high != c_low else 0.0001

        if body < total_range * 0.1 and lower_wick > body * 2:
            pattern_name = "Doji / Hammer"
            score += 5
            score_breakdown.append(f"Candle Pattern: +5 ({pattern_name})")
        elif bias == "BUY" and lower_wick > body * 2 and upper_wick < body * 0.5:
            pattern_name = "Bullish Pin Bar"
            score += 10
            score_breakdown.append(f"Candle Pattern: +10 ({pattern_name})")
        elif bias == "SELL" and upper_wick > body * 2 and lower_wick < body * 0.5:
            pattern_name = "Bearish Pin Bar"
            score += 10
            score_breakdown.append(f"Candle Pattern: +10 ({pattern_name})")
        elif bias == "BUY" and c_close > c_open and body > avg_body * 1.5:
            pattern_name = "Bullish Engulfing"
            score += 10
            score_breakdown.append(f"Candle Pattern: +10 ({pattern_name})")
        elif bias == "SELL" and c_close < c_open and body > avg_body * 1.5:
            pattern_name = "Bearish Engulfing"
            score += 10
            score_breakdown.append(f"Candle Pattern: +10 ({pattern_name})")
        elif current_body > avg_body * 1.5:
            pattern_name = "Strong Momentum Candle"
            score += 5
            score_breakdown.append(f"Candle Pattern: +5 ({pattern_name})")
        else:
            score_breakdown.append("Candle Pattern: +0 (no significant pattern)")

        confirmations.append(f"{'✓' if pattern_name != 'None' else '✗'} Candlestick: {pattern_name}")

        # 6. Order Block Check — with impulse ratio validation
        if i >= 5:
            ob_found = False
            for k in range(i-5, i-1):
                impulse_body = abs(closes[k+1] - opens[k+1])
                if bias == "BUY" and closes[k] < opens[k] and closes[k+1] > opens[k+1] and closes[k+1] > highs[k]:
                    if avg_body > 0 and impulse_body >= avg_body * ob_impulse:
                        ob_found = True
                        score += 10
                        score_breakdown.append(f"Order Block: +10 (bullish OB, impulse {impulse_body/avg_body:.1f}x)")
                        confirmations.append(f"✓ Order Block: Bullish OB — impulse {impulse_body/avg_body:.1f}x avg body")
                        break
                elif bias == "SELL" and closes[k] > opens[k] and closes[k+1] < opens[k+1] and closes[k+1] < lows[k]:
                    if avg_body > 0 and impulse_body >= avg_body * ob_impulse:
                        ob_found = True
                        score += 10
                        score_breakdown.append(f"Order Block: +10 (bearish OB, impulse {impulse_body/avg_body:.1f}x)")
                        confirmations.append(f"✓ Order Block: Bearish OB — impulse {impulse_body/avg_body:.1f}x avg body")
                        break
            if not ob_found:
                score_breakdown.append("Order Block: +0 (none found)")
                confirmations.append(f"✗ Order Block: None with impulse >= {ob_impulse}x avg body")

        # Total score requirement — uses configurable confluence_threshold
        if score < confluence_threshold:
            continue

        # Throttle: no signal within 3 bars of last one
        if signals and i - signals[-1]["time"] < 3:
            continue

        # Build final confirmation summary
        confirmations.insert(0, f"═══ Confluence Score: {score}/75 ═══")
        confirmations.append(f"── Score Breakdown ──")
        confirmations.extend(score_breakdown)
        confirmations.append(f"ATR(14): {atr:.5f} | SL Distance: {sl_distance:.5f}")
        confirmations.append(f"Risk: {sl_distance:.2f} pips | Min SL enforced: max(1.5×ATR, 0.2% price)")

        signals.append({
            "time": i,
            "symbol": symbol,
            "direction": bias,
            "entry_price": float(entry),
            "stop_loss": float(sl),
            "confluence_score": int(score),
            "confirmations": confirmations,
            "pattern": pattern_name,
            "has_fvg": bool(has_fvg),
            "has_liquidity_sweep": bool(has_sweep),
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
    strategy_config = {
        "confluence_threshold": req.confluence_threshold,
        "swing_length": req.swing_length,
        "ob_impulse_ratio": req.ob_impulse_ratio,
        "fvg_min_gap_pips": req.fvg_min_gap_pips,
        "liq_sweep_min_pips": req.liq_sweep_min_pips,
        "max_spread_pips": req.max_spread_pips,
    }
    logger.info(f"  Generating signals (threshold={req.confluence_threshold}, "
                f"fvg_min={req.fvg_min_gap_pips}, liq_min={req.liq_sweep_min_pips}, "
                f"ob_impulse={req.ob_impulse_ratio})...")
    signals = _generate_signals_from_candles(candles, req.symbol, req.timeframe, strategy_config)
    logger.info(f"  Generated {len(signals)} signals")

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
        "max_daily_loss_pct": req.max_daily_loss_pct,
        "max_weekly_loss_pct": req.max_weekly_loss_pct,
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
