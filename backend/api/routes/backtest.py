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


def _generate_signals_from_candles(candles, symbol: str, timeframe: str,
                                    strategy_config: dict = None) -> list:
    """
    SMC signal generation — O(n) single-pass approach.
    Runs structure detection once on full dataset, then scans for entries.
    """
    import numpy as np
    from backend.risk.position_sizer import get_pip_size

    cfg = strategy_config or {}
    confluence_threshold = cfg.get("confluence_threshold", 55)
    swing_len = cfg.get("swing_length", 5)
    fvg_min_pips = cfg.get("fvg_min_gap_pips", 3.0)
    liq_min_pips = cfg.get("liq_sweep_min_pips", 2.0)
    pip_size = get_pip_size(symbol)

    signals = []
    if len(candles) < 100:
        return signals

    closes = candles['close'].values.astype(float)
    highs = candles['high'].values.astype(float)
    lows = candles['low'].values.astype(float)
    opens = candles['open'].values.astype(float)
    n = len(candles)

    # ── PRE-COMPUTE: ATR array (vectorized) ──
    atr_period = 14
    prev_c = np.roll(closes, 1); prev_c[0] = closes[0]
    tr_all = np.maximum(highs - lows, np.maximum(np.abs(highs - prev_c), np.abs(lows - prev_c)))
    atr_array = np.zeros(n)
    for i in range(atr_period, n):
        atr_array[i] = np.mean(tr_all[i - atr_period:i])

    # ── PRE-COMPUTE: Swing points ──
    swing_highs = []  # (index, price)
    swing_lows = []   # (index, price)
    for i in range(swing_len, n - swing_len):
        window_h = highs[i - swing_len:i + swing_len + 1]
        window_l = lows[i - swing_len:i + swing_len + 1]
        if highs[i] == window_h.max():
            swing_highs.append((i, float(highs[i])))
        if lows[i] == window_l.min():
            swing_lows.append((i, float(lows[i])))

    # ── PRE-COMPUTE: Trend at each bar (BOS tracking) ──
    trend_at = ["NEUTRAL"] * n
    bos_count_at = [0] * n
    confirmed_at = [False] * n
    current_trend = "NEUTRAL"
    consecutive_bos = 0
    last_sh_idx = -1
    last_sl_idx = -1

    sh_idx = 0  # pointer into swing_highs
    sl_idx = 0  # pointer into swing_lows

    for i in range(swing_len * 3, n):
        # Advance swing pointers to swings before bar i
        while sh_idx < len(swing_highs) - 1 and swing_highs[sh_idx + 1][0] < i:
            sh_idx += 1
        while sl_idx < len(swing_lows) - 1 and swing_lows[sl_idx + 1][0] < i:
            sl_idx += 1

        # Only re-evaluate when a NEW swing appears
        new_swing = (sh_idx != last_sh_idx or sl_idx != last_sl_idx)
        if new_swing and sh_idx >= 1 and sl_idx >= 1:
            last_sh_idx = sh_idx
            last_sl_idx = sl_idx

            curr_sh = swing_highs[sh_idx][1]
            prev_sh = swing_highs[sh_idx - 1][1]
            curr_sl = swing_lows[sl_idx][1]
            prev_sl = swing_lows[sl_idx - 1][1]

            hh = curr_sh > prev_sh
            hl = curr_sl > prev_sl
            lh = curr_sh < prev_sh
            ll = curr_sl < prev_sl

            # Bullish: HH or HL (either is a bullish structural shift)
            if hh or hl:
                if current_trend == "BULLISH":
                    consecutive_bos += 1
                else:
                    current_trend = "BULLISH"
                    consecutive_bos = 1
            # Bearish: LH or LL
            elif lh or ll:
                if current_trend == "BEARISH":
                    consecutive_bos += 1
                else:
                    current_trend = "BEARISH"
                    consecutive_bos = 1

        trend_at[i] = current_trend
        bos_count_at[i] = consecutive_bos
        confirmed_at[i] = consecutive_bos >= 2

    # ── PRE-COMPUTE: Simple OB, FVG, Liquidity checks (vectorized where possible) ──
    # FVG: gap between candle i low and candle i-2 high (bullish) or vice versa
    fvg_bull = np.zeros(n, dtype=bool)
    fvg_bear = np.zeros(n, dtype=bool)
    for i in range(3, n):
        if lows[i] > highs[i-2]:
            gap = (lows[i] - highs[i-2]) / pip_size if pip_size > 0 else 0
            if gap >= fvg_min_pips:
                fvg_bull[i] = True
        if highs[i] < lows[i-2]:
            gap = (lows[i-2] - highs[i]) / pip_size if pip_size > 0 else 0
            if gap >= fvg_min_pips:
                fvg_bear[i] = True

    # ── SIGNAL SCAN (O(n)) ──
    _diag_no_trend = 0
    _diag_no_bos = 0
    _diag_score_reject = 0
    _diag_throttle = 0
    _diag_max_score = 0
    _diag_score_samples = []  # log first 10 score breakdowns for debugging

    logger.info(f"[SIGNAL] ═══ Pre-compute complete ═══")
    logger.info(f"[SIGNAL] Bars: {n} | Swing Highs: {len(swing_highs)} | Swing Lows: {len(swing_lows)}")
    logger.info(f"[SIGNAL] FVG Bull bars: {int(fvg_bull.sum())} | FVG Bear bars: {int(fvg_bear.sum())}")
    n_confirmed = sum(1 for x in confirmed_at if x)
    n_bullish = sum(1 for x in trend_at if x == "BULLISH")
    n_bearish = sum(1 for x in trend_at if x == "BEARISH")
    logger.info(f"[SIGNAL] Trend: bullish_bars={n_bullish}, bearish_bars={n_bearish}, confirmed_bars(BOS>=2)={n_confirmed}")
    logger.info(f"[SIGNAL] Config: threshold={confluence_threshold}, swing_len={swing_len}, pip_size={pip_size}")

    for i in range(max(50, swing_len * 3), n - 1):
        atr = atr_array[i]
        if atr == 0:
            continue

        trend = trend_at[i]
        if trend == "NEUTRAL":
            _diag_no_trend += 1
            continue

        if not confirmed_at[i]:
            _diag_no_bos += 1
            continue

        bias = "BUY" if trend == "BULLISH" else "SELL"

        # ── Score confluence ──
        score = 0
        score_breakdown = []
        confirmations = []

        # +15: Trend confirmed (2+ BOS)
        score += 15
        bos_n = bos_count_at[i]
        score_breakdown.append(f"Trend: +15 ({trend}, {bos_n} BOS)")
        confirmations.append(f"✓ Trend: {trend} — {bos_n} consecutive BOS")

        # +15: Liquidity sweep (wick through recent swing then close back)
        has_sweep = False
        if i >= 10:
            recent_low = np.min(lows[i-10:i-1])
            recent_high = np.max(highs[i-10:i-1])
            if bias == "BUY" and lows[i-1] < recent_low and closes[i] > opens[i]:
                sweep_size = (recent_low - lows[i-1]) / pip_size if pip_size > 0 else 0
                if sweep_size >= liq_min_pips:
                    has_sweep = True
                    score += 15
                    score_breakdown.append(f"Liquidity: +15 (SSL sweep {sweep_size:.1f} pips)")
                    confirmations.append(f"✓ Liquidity: SSL swept {sweep_size:.1f} pips")
            elif bias == "SELL" and highs[i-1] > recent_high and closes[i] < opens[i]:
                sweep_size = (highs[i-1] - recent_high) / pip_size if pip_size > 0 else 0
                if sweep_size >= liq_min_pips:
                    has_sweep = True
                    score += 15
                    score_breakdown.append(f"Liquidity: +15 (BSL sweep {sweep_size:.1f} pips)")
                    confirmations.append(f"✓ Liquidity: BSL swept {sweep_size:.1f} pips")
        if not has_sweep:
            score_breakdown.append("Liquidity: +0")
            confirmations.append("✗ Liquidity: No sweep")

        # +15: Order Block (strong impulse away from zone)
        ob_found = False
        if i >= 5:
            avg_body = np.mean(np.abs(closes[i-5:i] - opens[i-5:i]))
            for k in range(i-5, i-1):
                impulse = abs(closes[k+1] - opens[k+1])
                if bias == "BUY" and closes[k] < opens[k] and closes[k+1] > opens[k+1] and closes[k+1] > highs[k]:
                    if avg_body > 0 and impulse >= avg_body * 1.5:
                        ob_found = True
                        score += 15
                        score_breakdown.append(f"OB: +15 (bullish, {impulse/avg_body:.1f}x)")
                        confirmations.append(f"✓ Order Block: Bullish OB")
                        break
                elif bias == "SELL" and closes[k] > opens[k] and closes[k+1] < opens[k+1] and closes[k+1] < lows[k]:
                    if avg_body > 0 and impulse >= avg_body * 1.5:
                        ob_found = True
                        score += 15
                        score_breakdown.append(f"OB: +15 (bearish, {impulse/avg_body:.1f}x)")
                        confirmations.append(f"✓ Order Block: Bearish OB")
                        break
        if not ob_found:
            score_breakdown.append("OB: +0")
            confirmations.append("✗ Order Block: None")

        # +10: FVG present
        has_fvg = (bias == "BUY" and fvg_bull[i]) or (bias == "SELL" and fvg_bear[i])
        if has_fvg:
            score += 10
            score_breakdown.append("FVG: +10")
            confirmations.append("✓ FVG: Fair Value Gap detected")
        else:
            score_breakdown.append("FVG: +0")
            confirmations.append("✗ FVG: None")

        # +15/+10/+5: Candlestick pattern
        body = abs(closes[i] - opens[i])
        upper_wick = highs[i] - max(opens[i], closes[i])
        lower_wick = min(opens[i], closes[i]) - lows[i]
        total_range = highs[i] - lows[i] if highs[i] != lows[i] else 0.0001
        avg_body = np.mean(np.abs(closes[max(0,i-5):i] - opens[max(0,i-5):i])) if i >= 5 else body
        pattern_name = "None"
        candle_pts = 0

        if bias == "BUY" and lower_wick > body * 2 and upper_wick < body * 0.5:
            pattern_name = "Bullish Pin Bar"
            candle_pts = 15
        elif bias == "SELL" and upper_wick > body * 2 and lower_wick < body * 0.5:
            pattern_name = "Bearish Pin Bar"
            candle_pts = 15
        elif bias == "BUY" and closes[i] > opens[i] and body > avg_body * 1.5:
            pattern_name = "Bullish Engulfing"
            candle_pts = 10
        elif bias == "SELL" and closes[i] < opens[i] and body > avg_body * 1.5:
            pattern_name = "Bearish Engulfing"
            candle_pts = 10
        elif body < total_range * 0.15 and (lower_wick > body * 2 or upper_wick > body * 2):
            pattern_name = "Doji"
            candle_pts = 5

        if candle_pts > 0:
            score += candle_pts
            score_breakdown.append(f"Candle: +{candle_pts} ({pattern_name})")
            confirmations.append(f"✓ Candlestick: {pattern_name}")
        else:
            score_breakdown.append("Candle: +0")
            confirmations.append("✗ Candlestick: No pattern")

        # +10: Volume (if available)
        if 'tick_volume' in candles.columns and i >= 20:
            vols = candles['tick_volume'].values[i-20:i].astype(float)
            avg_vol = np.mean(vols) if len(vols) > 0 else 0
            curr_vol = float(candles['tick_volume'].values[i])
            if avg_vol > 0 and curr_vol > avg_vol * 1.2:
                score += 10
                score_breakdown.append(f"Volume: +10 ({curr_vol/avg_vol:.1f}x)")
                confirmations.append(f"✓ Volume: {curr_vol/avg_vol:.1f}x average")

        # Track max score seen & log sample breakdowns
        _diag_max_score = max(_diag_max_score, score)
        if len(_diag_score_samples) < 10:
            _diag_score_samples.append(f"  Bar {i}: score={score}/{confluence_threshold} | {' | '.join(score_breakdown)}")

        # Check threshold
        if score < confluence_threshold:
            _diag_score_reject += 1
            continue

        # Throttle: no signal within 5 bars of last
        if signals and i - signals[-1]["time"] < 5:
            _diag_throttle += 1
            continue

        # Calculate SL from swing structure
        entry = float(closes[i])
        sl_buffer = atr * 0.3
        if bias == "BUY":
            nearby_lows = [p for idx, p in swing_lows if idx < i and idx > i - 30]
            swing_low = min(nearby_lows) if nearby_lows else entry - atr * 1.5
            sl = swing_low - sl_buffer
            if sl >= entry:
                sl = entry - atr * 1.5
        else:
            nearby_highs = [p for idx, p in swing_highs if idx < i and idx > i - 30]
            swing_high = max(nearby_highs) if nearby_highs else entry + atr * 1.5
            sl = swing_high + sl_buffer
            if sl <= entry:
                sl = entry + atr * 1.5

        logger.info(f"[SIGNAL] ✅ SIGNAL #{len(signals)+1} at bar {i}: {bias} @ {entry:.5f} | SL={sl:.5f} | score={score} | {pattern_name}")

        confirmations.insert(0, f"═══ Confluence Score: {score}/100 ═══")
        confirmations.append("── Score Breakdown ──")
        confirmations.extend(score_breakdown)
        confirmations.append(f"ATR(14): {atr:.5f} | SL: {abs(entry - sl):.5f}")

        signals.append({
            "time": i,
            "symbol": symbol,
            "direction": bias,
            "entry_price": entry,
            "stop_loss": float(sl),
            "confluence_score": int(score),
            "confirmations": confirmations,
            "pattern": pattern_name,
            "has_fvg": bool(has_fvg),
            "has_liquidity_sweep": bool(has_sweep),
        })

    # ── Final diagnostic summary ──
    logger.info(f"[SIGNAL] ═══ SIGNAL GENERATION COMPLETE ═══")
    logger.info(f"[SIGNAL] Results: {len(signals)} signals from {n} candles")
    logger.info(f"[SIGNAL] Filters: no_trend={_diag_no_trend} | no_bos={_diag_no_bos} | score_reject={_diag_score_reject} | throttle={_diag_throttle}")
    logger.info(f"[SIGNAL] Max score seen: {_diag_max_score}/{confluence_threshold} threshold")
    if _diag_score_samples:
        logger.info(f"[SIGNAL] ─── Sample score breakdowns (first 10 bars that passed BOS gate) ───")
        for sample in _diag_score_samples:
            logger.info(f"[SIGNAL] {sample}")
    if _diag_max_score < confluence_threshold:
        logger.warning(f"[SIGNAL] ⚠️ MAX SCORE ({_diag_max_score}) IS BELOW THRESHOLD ({confluence_threshold})! No signal can ever pass. Lower threshold or check scoring.")
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
    # Run in thread pool to prevent blocking the event loop (Fix: server-wide block)
    import asyncio
    signals = await asyncio.to_thread(
        _generate_signals_from_candles, candles, req.symbol, req.timeframe, strategy_config
    )
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
