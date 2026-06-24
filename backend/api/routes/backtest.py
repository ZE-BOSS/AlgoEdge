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
    SMC signal generation using the 4-layer model with actual SMC modules.
    Replaces the old momentum-heuristic approach.
    """
    import numpy as np
    from backend.risk.position_sizer import get_pip_size
    from backend.strategies.smc.market_structure import MarketStructureDetector
    from backend.strategies.smc.order_blocks import OrderBlockDetector
    from backend.strategies.smc.fvg import FVGDetector
    from backend.strategies.smc.liquidity import LiquidityMapper
    from backend.strategies.smc.ipdm import IPDMDetector
    from backend.strategies.smc.candlestick import detect_confirmation_pattern

    cfg = strategy_config or {}
    confluence_threshold = cfg.get("confluence_threshold", 55)
    swing_len = cfg.get("swing_length", 5)
    fvg_min_pips = cfg.get("fvg_min_gap_pips", 3.0)
    liq_min_pips = cfg.get("liq_sweep_min_pips", 2.0)
    pip_size = get_pip_size(symbol)

    signals = []
    if len(candles) < 100:
        return signals

    closes = candles['close'].values
    highs = candles['high'].values
    lows = candles['low'].values
    opens = candles['open'].values

    # Initialize SMC modules
    structure = MarketStructureDetector(swing_length=swing_len, min_bos_count=2)
    ob_detector = OrderBlockDetector()
    fvg_detector = FVGDetector(fvg_min_pips)
    liq_mapper = LiquidityMapper(liq_min_pips)
    ipdm = IPDMDetector()

    # Pre-compute ATR array
    atr_period = 14
    atr_array = np.zeros(len(candles))
    for i in range(atr_period + 1, len(candles)):
        tr_vals = []
        for j in range(i - atr_period, i):
            tr = max(highs[j] - lows[j],
                     abs(highs[j] - closes[j-1]) if j > 0 else highs[j] - lows[j],
                     abs(lows[j] - closes[j-1]) if j > 0 else highs[j] - lows[j])
            tr_vals.append(tr)
        atr_array[i] = np.mean(tr_vals)

    # Sliding window analysis
    window_size = 100
    _diag_no_trend = 0
    _diag_no_bos = 0
    _diag_score_reject = 0
    _diag_throttle = 0

    for i in range(window_size, len(candles) - 1):
        atr = atr_array[i]
        if atr == 0:
            continue

        # Run structure detection on sliding window
        window = candles.iloc[max(0, i - window_size):i + 1].copy()
        ms = structure.update(window)

        trend = ms.get("trend", "NEUTRAL")
        if trend == "NEUTRAL":
            _diag_no_trend += 1
            continue

        # Need 2+ BOS confirmed
        if not ms.get("trend_confirmed", False):
            _diag_no_bos += 1
            continue

        bias = "BUY" if trend == "BULLISH" else "SELL"

        # Run sub-modules on window
        obs = ob_detector.update(window)
        fvgs = fvg_detector.update(window)
        swings = ms.get("swings", [])
        liq = liq_mapper.update(window, swings)
        high_swings = [s for s in swings if s["type"] == "HIGH"]
        low_swings = [s for s in swings if s["type"] == "LOW"]
        ipdm_result = ipdm.update(window, high_swings, low_swings)

        # IPDM gate: skip accumulation/active manipulation
        phase = ipdm_result.get("phase", "UNKNOWN")
        if phase == "ACCUMULATION":
            continue
        if phase == "MANIPULATION" and not ipdm_result.get("manipulation_completed", False):
            continue

        # Score confluence
        score = 0
        score_breakdown = []
        confirmations = []

        # +15: Trend confirmed (2+ BOS)
        score += 15
        score_breakdown.append(f"Trend: +15 ({trend}, {ms.get('consecutive_bos',0)} BOS)")
        confirmations.append(f"✓ Trend: {trend} — {ms.get('consecutive_bos',0)} consecutive BOS confirmed")

        # +15: Liquidity sweep
        sweep = liq.get("recent_sweep") if isinstance(liq, dict) else None
        if sweep:
            score += 15
            score_breakdown.append("Liquidity Sweep: +15")
            confirmations.append(f"✓ Liquidity: Sweep detected")
        else:
            score_breakdown.append("Liquidity Sweep: +0")
            confirmations.append("✗ Liquidity: No sweep")

        # +15: Fresh Order Block
        fresh_ob = None
        for ob in reversed(obs if isinstance(obs, list) else []):
            if ob.get("type") == trend and ob.get("touches", 99) == 0:
                fresh_ob = ob
                break
        if fresh_ob:
            score += 15
            score_breakdown.append("Order Block: +15 (fresh OB)")
            confirmations.append("✓ Order Block: Fresh unmitigated OB found")
        else:
            score_breakdown.append("Order Block: +0")
            confirmations.append("✗ Order Block: None found")

        # +10: FVG inside OB
        fvg_inside = False
        if fresh_ob and isinstance(fvgs, list) and fvgs:
            ob_h = fresh_ob.get("high", 0)
            ob_l = fresh_ob.get("low", 0)
            for fvg in fvgs:
                mid = (fvg.get("high", 0) + fvg.get("low", 0)) / 2
                if ob_l <= mid <= ob_h:
                    fvg_inside = True
                    break
        if fvg_inside:
            score += 10
            score_breakdown.append("FVG+OB: +10")
            confirmations.append("✓ FVG inside Order Block — high probability")
        elif isinstance(fvgs, list) and fvgs:
            score += 5
            score_breakdown.append("FVG: +5 (present, not in OB)")
            confirmations.append("△ FVG: Present but not inside OB")

        # +10: Candlestick pattern
        try:
            pattern = detect_confirmation_pattern(window, bias=trend)
            if pattern:
                tier = getattr(pattern, 'tier', 0)
                if isinstance(tier, str):
                    tier = {"TIER_1": 1, "TIER_2": 2, "TIER_3": 3}.get(tier, 0)
                pts = {1: 15, 2: 10, 3: 5}.get(tier, 5)
                score += pts
                pname = getattr(pattern, 'name', str(pattern))
                score_breakdown.append(f"Candle: +{pts} ({pname})")
                confirmations.append(f"✓ Candlestick: {pname}")
            else:
                score_breakdown.append("Candle: +0")
                confirmations.append("✗ Candlestick: No pattern")
        except Exception:
            score_breakdown.append("Candle: +0 (error)")

        # +5: IPDM Expansion phase
        if phase == "EXPANSION":
            score += 5
            score_breakdown.append("IPDM: +5 (Expansion)")
            confirmations.append("✓ IPDM: Expansion phase")

        # +10: Kill zone
        try:
            from backend.utils.timeutils import detect_session
            session = detect_session()
            if session in ("LONDON", "NY", "LONDON/NY"):
                score += 10
                score_breakdown.append(f"Session: +10 ({session})")
                confirmations.append(f"✓ Session: {session} kill zone")
        except Exception:
            pass

        # Check threshold
        if score < confluence_threshold:
            _diag_score_reject += 1
            continue

        # Throttle: no signal within 5 bars of last
        if signals and i - signals[-1]["time"] < 5:
            _diag_throttle += 1
            continue

        # Calculate entry + SL from structure
        entry = float(closes[i])
        # SL from swing extreme + buffer
        sl_buffer = atr * 0.3
        if bias == "BUY":
            swing_low = min(s["price"] for s in low_swings[-3:]) if low_swings else entry - atr * 1.5
            sl = swing_low - sl_buffer
            if sl >= entry:
                sl = entry - atr * 1.5
        else:
            swing_high = max(s["price"] for s in high_swings[-3:]) if high_swings else entry + atr * 1.5
            sl = swing_high + sl_buffer
            if sl <= entry:
                sl = entry + atr * 1.5

        confirmations.insert(0, f"═══ Confluence Score: {score}/100 ═══")
        confirmations.append("── Score Breakdown ──")
        confirmations.extend(score_breakdown)
        confirmations.append(f"ATR(14): {atr:.5f} | IPDM: {phase}")

        signals.append({
            "time": i,
            "symbol": symbol,
            "direction": bias,
            "entry_price": entry,
            "stop_loss": float(sl),
            "confluence_score": int(score),
            "confirmations": confirmations,
            "pattern": "SMC",
            "has_fvg": bool(fvg_inside or (isinstance(fvgs, list) and len(fvgs) > 0)),
            "has_liquidity_sweep": bool(sweep),
        })

    logger.info(f"Generated {len(signals)} signals from {len(candles)} candles for {symbol} "
                f"no_bos={_diag_no_bos}, score={_diag_score_reject}, throttle={_diag_throttle})")
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
