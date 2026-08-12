"""
backend/backtester/engine.py

Bar-by-bar backtesting engine using the same RiskEngine as live.
Source: TradingBot_MasterPlan-2.md Section 11
Source: RiskManagement_Spec.md Section 7

Core behavior:
  - ALL TP positions open at entry (no deferred stacking)
  - When TP1 hits → move ALL sibling positions to break-even
  - Trades grouped by signal entry (group_id) for combined P&L
  - Entry/exit confirmation arrays, ISO timestamps, duration metrics
"""

import uuid
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from backend.analytics.reports import generate_risk_report
from backend.risk.engine import RiskEngine
from backend.risk.multi_tp import TPLevel, _is_buy
from backend.risk.position_sizer import get_pip_size, calculate_risk_dollars
from backend.risk.prop_firm_validator import PropFirmValidator
from backend.utils.logger import get_logger
from backend.utils.timeutils import detect_session
from backend.utils.trade_grouper import group_trades

logger = get_logger(__name__)


def _to_epoch_seconds(val) -> float | None:
    """
    Robustly convert an epoch number, python/pandas datetime, or numpy scalar
    to epoch seconds (float).

    This matters because portfolio_engine.py's global timeline is built from
    `df['time'].values` / `df.index.values`, which yields numpy scalars
    (np.int64, np.datetime64, ...) rather than plain python int/float.
    np.int64 does NOT satisfy `isinstance(x, int)` on most platforms and has
    no `.timestamp()` method, so the old int/float-only check silently fell
    through and returned 0 / a raw numeric string for every such value.
    """
    if val is None:
        return None
    if isinstance(val, (int, float, np.integer, np.floating)):
        return float(val)
    if isinstance(val, datetime):
        return val.timestamp()
    if hasattr(val, "timestamp"):
        try:
            return float(val.timestamp())
        except Exception:
            pass
    try:
        return pd.Timestamp(val).timestamp()
    except Exception:
        return None


def _epoch_to_iso(epoch_val) -> str:
    """Convert epoch timestamp (incl. numpy scalars) or datetime to ISO string."""
    if isinstance(epoch_val, str):
        return epoch_val
    secs = _to_epoch_seconds(epoch_val)
    if secs is None:
        return str(epoch_val)
    try:
        return datetime.fromtimestamp(secs, tz=timezone.utc).isoformat()
    except Exception:
        return str(epoch_val)


def _calc_duration_minutes(entry_time, exit_time) -> float:
    """Calculate trade duration in minutes, robust to numpy scalar / datetime / epoch inputs."""
    e = _to_epoch_seconds(entry_time)
    x = _to_epoch_seconds(exit_time)
    if e is None or x is None:
        return 0.0
    return (x - e) / 60.0


def _validate_position(direction: str, entry_price: float, stop_loss: float, take_profit: float) -> tuple:
    """
    Validate that SL/TP are on the correct side of entry for the given direction.
    Returns (is_valid, error_message).
    """
    if _is_buy(direction):
        if stop_loss >= entry_price:
            return False, f"BUY SL ({stop_loss:.5f}) must be below entry ({entry_price:.5f})"
        if take_profit <= entry_price:
            return False, f"BUY TP ({take_profit:.5f}) must be above entry ({entry_price:.5f})"
    else:
        if stop_loss <= entry_price:
            return False, f"SELL SL ({stop_loss:.5f}) must be above entry ({entry_price:.5f})"
        if take_profit >= entry_price:
            return False, f"SELL TP ({take_profit:.5f}) must be below entry ({entry_price:.5f})"
    return True, ""


class BacktestEngine:
    """
    Backtesting engine that uses the identical RiskEngine as live trading.
    What you backtest = what runs live.
    """

    def __init__(self, risk_config: dict[str, Any]):
        self.risk_config = risk_config.copy()
        self.risk_config["is_backtest"] = True  # CRITICAL: Prevent loading live bot state from disk
        
        self.risk_engine = RiskEngine(self.risk_config)
        # Mark as backtesting for informational purposes / future guards.
        self.risk_engine.is_backtesting = True
        prop_firm_config = self.risk_config.get("prop_firm", {})
        if isinstance(prop_firm_config, dict):
            prop_firm_config = prop_firm_config.copy()
            prop_firm_config["is_backtesting"] = True
        else:
            setattr(prop_firm_config, "is_backtesting", True)
            
        self.prop_firm_validator = PropFirmValidator(prop_firm_config)
        self.risk_engine.prop_firm_validator = self.prop_firm_validator
        self.trades: list[dict[str, Any]] = []
        self.open_positions: list[dict[str, Any]] = []
        self.equity_curve: list[float] = []
        self.invalid_signals: int = 0
        self.rejection_funnel: dict[str, Any] = {
            "total_evaluated": 0,
            "strategy_rejections": {},
            "risk_rejections": {},
            "errors": 0,
            "approved": 0
        }
        self.run_logs = []
        # Simulation cost params (read from risk_config; default 0 = no cost)
        self._slippage_pips = float(risk_config.get("slippage_pips", 0.0))
        self._commission_per_lot = float(risk_config.get("commission_per_lot", 0.0))
        self._spread_pips = float(risk_config.get("spread_pips", 0.0))
        # Wick simulation: use OHLC shadow-weighted path model for same-bar SL+TP resolution
        self._simulate_wicks = bool(risk_config.get("simulate_wicks", True))

    def run(
        self,
        candles: pd.DataFrame,
        signals: list[dict[str, Any]],
        initial_balance: float = 10000.0,
        candles_m15: pd.DataFrame = None,
        candles_m5: pd.DataFrame = None,
    ) -> dict[str, Any]:
        """Run a backtest on historical candles with pre-generated signals."""
        balance = initial_balance
        self.trades = []
        self.open_positions = []
        self.equity_curve = [balance]
        self.invalid_signals = 0
        self.rejection_funnel = {
            "total_evaluated": 0,
            "strategy_rejections": {},
            "risk_rejections": {},
            "errors": 0,
            "approved": 0
        }

        logger.info("[ENGINE] ═══ Starting backtest engine ═══")
        logger.info(f"[ENGINE] Balance: ${initial_balance} | Signals: {len(signals)} | Candles: {len(candles)}")
        logger.info(f"[ENGINE] Risk config: risk_pct={self.risk_config.get('risk_per_trade_pct')}% | min_rr={self.risk_config.get('min_rr')} | tp_count={self.risk_config.get('tp_count')}")

        # Reset prop firm state for a fresh backtest run
        self.prop_firm_validator.is_breached = False
        self.prop_firm_validator.breach_reason = ""
        self.prop_firm_validator._breach_logged = False
        self.prop_firm_validator._alerts_sent = set()


        signal_idx = 0

        # ── Pre-compute time arrays to avoid pd.to_datetime in the loop ──
        if 'time' in candles.columns:
            time_series = candles['time']
        else:
            time_series = pd.Series(candles.index)
            
        if pd.api.types.is_datetime64_any_dtype(time_series):
            time_arr = time_series.astype('int64').values / 10**9
        else:
            try:
                time_arr = time_series.astype(float).values
            except Exception:
                time_arr = pd.to_datetime(time_series).astype('int64').values / 10**9
                
        dt_series = pd.to_datetime(time_series, unit='s' if not pd.api.types.is_datetime64_any_dtype(time_series) else None, utc=True)
        dt_arr = dt_series.dt.to_pydatetime()
        
        # ── Pre-compute OHLC arrays (vectorized, O(n)) ──
        opens_arr = candles["open"].values.astype(float)
        highs_arr = candles["high"].values.astype(float)
        lows_arr = candles["low"].values.astype(float)
        closes_arr = candles["close"].values.astype(float)
        atr_period = 14

        prev_closes = np.roll(closes_arr, 1)
        prev_closes[0] = closes_arr[0]
        tr_all = np.maximum(
            highs_arr - lows_arr,
            np.maximum(np.abs(highs_arr - prev_closes), np.abs(lows_arr - prev_closes))
        )
        # Rolling mean ATR
        atr_array = np.zeros(len(candles))
        for i in range(atr_period, len(candles)):
            atr_array[i] = np.mean(tr_all[i - atr_period:i])

        # ── Pre-compute swing point cache ──
        sw_len = self.risk_config.get("trail_structure_bars", self.risk_config.get("swing_length", 5))
        swing_lookback = 20
        swing_cache = {}
        for i in range(swing_lookback, len(candles)):
            points = []
            for j in range(max(sw_len, i - swing_lookback), i - sw_len):
                if j - sw_len < 0:
                    continue
                window_h = highs_arr[j - sw_len:j + sw_len + 1]
                window_l = lows_arr[j - sw_len:j + sw_len + 1]
                if highs_arr[j] == window_h.max():
                    points.append({"type": "HIGH", "price": float(highs_arr[j])})
                if lows_arr[j] == window_l.min():
                    points.append({"type": "LOW", "price": float(lows_arr[j])})
            if points:
                swing_cache[i] = points


        for i in range(len(candles)):
            current_time = time_arr[i]
            current_time_dt = dt_arr[i]
            current_price = closes_arr[i]
            high = highs_arr[i]
            low = lows_arr[i]
            open_p = opens_arr[i]

            # Look up pre-computed ATR and swing points
            current_atr = atr_array[i] if i < len(atr_array) else 0.0
            swing_points = swing_cache.get(i, [])

            # Calculate floating equity for Prop Firm tracking
            open_pnl = sum(self._calc_pnl(p["direction"], p["entry_price"], current_price, p["volume"], p.get("symbol", "")) for p in self.open_positions)
            self.prop_firm_validator.update_equity_balance(balance + open_pnl, balance, current_time_dt)
            if self.prop_firm_validator.is_breached and not getattr(self.prop_firm_validator, '_breach_logged', False):
                logger.warning(f"[PROP FIRM MONITOR] Drawdown breach detected: {self.prop_firm_validator.breach_reason} — continuing backtest (informational only)")
                self.prop_firm_validator._breach_logged = True  # log once, don't spam

            # 1. Manage existing open positions
            closed_this_bar = []
            tp1_hit_groups = set()  # Track which groups had TP1 hit this bar

            for pos in self.open_positions[:]:
                # Update highest/lowest price tracking for trailing
                if pos["direction"] == "BUY":
                    pos["highest_price"] = max(pos.get("highest_price", pos["entry_price"]), high)
                else:
                    pos["lowest_price"] = min(pos.get("lowest_price", pos["entry_price"]), low)

                # Check Max Holding Time (48 hours for Forex, 400 bars for CrashBoom)
                c_ts = current_time
                e_ts = pos["entry_time"].timestamp() if hasattr(pos.get("entry_time"), "timestamp") else pos.get("entry_time")
                
                pos["bars_held"] = pos.get("bars_held", 0) + 1
                symbol_upper = pos.get("symbol", "").upper()
                is_crashboom = "CRASH" in symbol_upper or "BOOM" in symbol_upper
                
                limit_hit = False
                if is_crashboom and pos["bars_held"] >= 400:
                    limit_hit = True
                        
                if limit_hit:
                    pos["exit_price"] = current_price
                    pos["exit_reason"] = "TIME_LIMIT"
                    pos["pnl"] = self._calc_pnl(pos["direction"], pos["entry_price"], pos["exit_price"], pos["volume"], pos.get("symbol", ""))
                    closed_this_bar.append(pos)
                    continue

                hard_close_time = pos.get("hard_close_time")
                if hard_close_time:
                    import pytz
                    et = current_time_dt.astimezone(pytz.timezone("America/New_York"))
                    time_str = et.strftime("%H:%M")
                    if time_str >= hard_close_time:
                        pos["exit_price"] = current_price
                        pos["exit_reason"] = "SESSION_END"
                        pos["pnl"] = self._calc_pnl(pos["direction"], pos["entry_price"], pos["exit_price"], pos["volume"], pos.get("symbol", ""))
                        closed_this_bar.append(pos)
                        continue

                # Check SL and TP hits
                sl_hit = False
                tp_hit = False
                if pos["direction"] == "BUY":
                    sl_hit = low <= pos["stop_loss"]
                    tp_hit = high >= pos["take_profit"]
                else:
                    sl_hit = high >= pos["stop_loss"]
                    tp_hit = low <= pos["take_profit"]
                    
                # Resolve ambiguous same-bar SL+TP hit using OHLC shadow-weighted path model.
                # If both SL and TP were touched in the same bar, use shadow lengths and
                # distance from open to determine which was most likely hit first.
                # Longer SL-side shadow = SL assumed first (conservative).
                if sl_hit and tp_hit:
                    if self._simulate_wicks:
                        if pos["direction"] == "BUY":
                            sl_shadow = open_p - low   # downward shadow towards SL
                            tp_shadow = high - open_p  # upward shadow towards TP
                        else:
                            sl_shadow = high - open_p  # upward shadow towards SL
                            tp_shadow = open_p - low   # downward shadow towards TP
                        dist_to_sl = abs(pos["stop_loss"] - open_p)
                        dist_to_tp = abs(pos["take_profit"] - open_p)
                        sl_wins = (sl_shadow >= tp_shadow) or (dist_to_sl <= dist_to_tp)
                        if sl_wins:
                            tp_hit = False
                        else:
                            sl_hit = False
                    else:
                        # Fallback: distance-from-open tie-breaker
                        dist_to_sl = abs(pos["stop_loss"] - open_p)
                        dist_to_tp = abs(pos["take_profit"] - open_p)
                        if dist_to_sl <= dist_to_tp:
                            tp_hit = False
                        else:
                            sl_hit = False

                # Update MAE/MFE using this bar's high/low BEFORE the exit checks below,
                # so the excursion on the closing bar itself is captured — previously
                # this ran after the sl_hit/tp_hit `continue`s, so any trade that hit
                # its SL/TP on the same bar it was evaluated (i.e. most quick trades)
                # closed with mae_pips/mfe_pips frozen at their 0.0 initial value.
                if pos["direction"] == "BUY":
                    adverse = pos["entry_price"] - low
                    favorable = high - pos["entry_price"]
                else:
                    adverse = high - pos["entry_price"]
                    favorable = pos["entry_price"] - low

                pip_size = get_pip_size(pos.get("symbol", ""))
                pos["mae_pips"] = max(pos.get("mae_pips", 0), adverse / pip_size if pip_size else 0)
                pos["mfe_pips"] = max(pos.get("mfe_pips", 0), favorable / pip_size if pip_size else 0)

                if sl_hit:
                    pos["exit_price"] = pos["stop_loss"]
                    pos["exit_reason"] = "BE_SL" if pos.get("be_applied") else "SL"
                    pos["pnl"] = self._calc_pnl(pos["direction"], pos["entry_price"], pos["exit_price"], pos["volume"], pos.get("symbol", ""))
                    closed_this_bar.append(pos)
                    continue
                elif tp_hit:
                    pos["exit_price"] = pos["take_profit"]
                    pos["exit_reason"] = f"TP{pos.get('tp_level', 1)}"
                    pos["pnl"] = self._calc_pnl(pos["direction"], pos["entry_price"], pos["exit_price"], pos["volume"], pos.get("symbol", ""))
                    closed_this_bar.append(pos)
                    if pos.get("tp_level") == 1:
                        tp1_hit_groups.add(pos.get("group_id"))
                    continue

                # Run BE/trailing checks via RiskEngine — WITH ATR + swing data
                actions = self.risk_engine.manage_open_position(
                    pos, current_price,
                    atr_value=current_atr,
                    swing_points=swing_points,
                )
                for action in actions:
                    if action["action"] == "MODIFY_SL":
                        old_sl = pos["stop_loss"]
                        pos["stop_loss"] = action["new_sl"]
                        if action.get("reason") == "BREAKEVEN":
                            pos["be_applied"] = True
                        elif action.get("reason") == "TRAIL":
                            pos["trail_applied"] = True

            # ── CRITICAL: When TP1 hits, move ALL siblings to break-even ──
            if tp1_hit_groups:
                for pos in self.open_positions:
                    if pos.get("group_id") in tp1_hit_groups and pos not in closed_this_bar:
                        pip_size = get_pip_size(pos.get("symbol", ""))
                        buffer = self.risk_config.get("be_buffer_pips", 2.0) * pip_size
                        if pos["direction"] == "BUY":
                            new_sl = pos["entry_price"] + buffer
                            if new_sl > pos["stop_loss"]:
                                pos["stop_loss"] = new_sl
                                pos["be_applied"] = True
                        else:
                            new_sl = pos["entry_price"] - buffer
                            if new_sl < pos["stop_loss"]:
                                pos["stop_loss"] = new_sl
                                pos["be_applied"] = True

            # Close positions and build exit confirmations
            # FIX 2: Collect positions to remove after the loop (avoids O(n) list.remove in hot loop)
            positions_to_remove = []
            for pos in closed_this_bar:
                pos["exit_time"] = current_time
                try:
                    # current_time may be a numpy.int64/float64 (e.g. when it
                    # comes from bar['time']). numpy.int64 is NOT an instance
                    # of Python's int, so detect_session()'s isinstance checks
                    # silently fall through to "UNKNOWN" for it — even though
                    # no exception is raised. Normalize to a plain Python
                    # float first, same as sig_time already is at entry time.
                    pos["session"] = detect_session(_to_epoch_seconds(current_time))
                except Exception:
                    pos["session"] = "UNKNOWN"

                pos["duration_minutes"] = _calc_duration_minutes(
                    pos.get("entry_time"), pos.get("exit_time")
                )
                pos["entry_time_iso"] = _epoch_to_iso(pos.get("entry_time"))
                pos["exit_time_iso"] = _epoch_to_iso(pos.get("exit_time"))

                pos["exit_confirmations"] = [
                    f"Exit Reason: {pos.get('exit_reason', 'UNKNOWN')}",
                    f"Exit Price: {pos.get('exit_price', 0):.5f}",
                    f"PnL: ${pos.get('pnl', 0):.2f}",
                    f"Duration: {pos.get('duration_minutes', 0):.1f} min",
                    f"MAE: {pos.get('mae_pips', 0):.1f} pips",
                    f"MFE: {pos.get('mfe_pips', 0):.1f} pips",
                    f"BE Applied: {'Yes' if pos.get('be_applied') else 'No'}",
                    f"Trail Method: {pos.get('trail_method') or 'NONE'}",
                    f"Session: {pos.get('session', 'UNKNOWN')}",
                ]

                balance += pos.get("pnl", 0)
                pos["balance_after"] = balance
                is_win = pos.get("pnl", 0) > 0
                # If the group is now fully closed, notify strategy
                group_id_closed = pos.get("group_id", "unknown")
                # Fallback to simple sub-trade counting to know when group is done
                # Count remaining open positions for this group
                # Count remaining open positions for this group, ignoring those already queued for removal
                remaining_legs = sum(1 for p in self.open_positions if p.get("group_id") == group_id_closed and p not in positions_to_remove and p != pos)
                
                if remaining_legs == 0:
                    group_pnl = sum(
                        p.get("pnl", 0) for p in self.trades
                        if p.get("group_id") == group_id_closed
                    ) + pos.get("pnl", 0)
                    
                    group_lots = sum(
                        p.get("volume", 0.0) for p in self.trades
                        if p.get("group_id") == group_id_closed
                    ) + pos.get("volume", 0.0)
                    
                    # Safely feed PnL back to the Risk Engine's Circuit Breaker
                    if hasattr(self.risk_engine, "on_backtest_position_closed"):
                        self.risk_engine.on_backtest_position_closed(group_id_closed, group_pnl, current_time, pos.get("symbol", ""), group_lots)
                        
                    strategy = getattr(self, "_strategy", None)
                    if strategy is not None:
                        strategy.notify_outcome(
                            symbol=pos.get("symbol", ""),
                            group_id=group_id_closed,
                            is_win=group_pnl > 0,
                            pnl=group_pnl,
                        )
                self.trades.append(pos)
                positions_to_remove.append(pos)

                self.run_logs.append({
                    "time": _epoch_to_iso(current_time),
                    "level": "INFO",
                    "category": "BACKTEST_LOG",
                    "message": f"Closed {pos['direction']} {pos.get('symbol')} {pos.get('exit_reason')} | PnL: ${pos.get('pnl', 0):.2f}"
                })
            # FIX 2: Bulk removal after the loop — avoids O(n) list.remove per closed position
            for p in positions_to_remove:
                if p in self.open_positions:
                    self.open_positions.remove(p)

            current_timestamp = current_time.timestamp() if hasattr(current_time, "timestamp") else float(current_time)
            # 2. Check for new signals on this bar
            # FIX 1 (Lookahead bias): use >= so a signal is only actioned on the NEXT bar
            # after it was generated (sig_time must be strictly less than current_timestamp).
            while signal_idx < len(signals):
                sig = signals[signal_idx]
                sig_time = float(sig.get("time", float("inf")))
                if sig_time >= current_timestamp:
                    break
                signal_idx += 1

                # Prevent taking multiple positions on the same symbol in the same direction (pyramiding)
                symbol = sig.get("symbol")
                from backend.risk.multi_tp import _is_buy
                sig_is_buy = _is_buy(sig.get("direction", "BUY"))
                
                already_open = False
                for p in self.open_positions:
                    p_is_buy = _is_buy(p.get("direction", "BUY"))
                    if p.get("symbol") == symbol and p_is_buy == sig_is_buy:
                        already_open = True
                        break
                        
                if already_open:
                    continue

                # Generate a group_id to link all sub-positions from this signal
                group_id = str(uuid.uuid4())[:8]
                sig["group_id"] = group_id

                current_time_dt = dt_arr[i]
                
                self.rejection_funnel["total_evaluated"] += 1
                
                # Check Strategy Rejections first
                passed_gates = sig.get("metadata", {}).get("passed_gates", True)
                if not passed_gates:
                    reasons = sig.get("metadata", {}).get("rejection_reasons", [])
                    for r in reasons:
                        gate = r.split(":")[0] if ":" in r else "Unknown Strategy Rule"
                        self.rejection_funnel["strategy_rejections"][gate] = self.rejection_funnel["strategy_rejections"].get(gate, 0) + 1
                    logger.trace(f"[ENGINE] ❌ Signal REJECTED (Strategy): {reasons}")
                    continue

                # Evaluate signal through RiskEngine
                approved, reason, tp_levels = False, "Error during evaluation", []

                # (Prop Firm validator is informational only in backtesting, we do not block signals here so the backtest can continue)

                try:
                    approved, reason, tp_levels = self.risk_engine.evaluate_signal(
                        signal_data=sig,
                        account_balance=balance,
                        current_time=current_time_dt,
                        initial_balance=initial_balance
                    )
                except Exception as e:
                    import traceback
                    traceback.print_exc()
                    self.rejection_funnel["errors"] += 1
                    logger.error(f"[ENGINE] ❌ Error evaluating signal: {e!s}")
                    continue

                if approved:
                    self.rejection_funnel["approved"] += 1
                    logger.trace(f"[ENGINE] ✅ Signal APPROVED at bar {i}: {sig.get('direction')} @ {sig.get('entry_price', current_price):.5f} | {len(tp_levels)} TP levels | balance=${balance:.2f}")

                    actual_opened_count = 0
                    for tp in tp_levels:
                        # Validate before opening
                        is_valid, err = _validate_position(
                            sig.get("direction", "BUY"),
                            sig.get("entry_price", current_price),
                            sig.get("stop_loss", 0),
                            tp.tp_price,
                        )
                        if not is_valid:
                            logger.warning(f"[ENGINE] ❌ Invalid position rejected: {err}")
                            self.invalid_signals += 1
                            continue

                        # (which is the bar AFTER the signal was generated — guaranteed by the
                        # sig_time >= current_timestamp guard above). This eliminates same-bar fill.
                        bar_open_price = float(open_p)
                        position = self._create_position(sig, tp, current_time, bar_open_price, group_id, balance)
                        self.open_positions.append(position)
                        logger.debug(f"[ENGINE]   Position opened: TP{tp.level} @ {bar_open_price:.5f} (bar open) | vol={tp.volume:.4f}")
                        
                    self.run_logs.append({
                        "time": _epoch_to_iso(current_time),
                        "level": "INFO",
                        "category": "BACKTEST_LOG",
                        "message": f"Opened {sig.get('direction')} {sig.get('symbol', 'UNKNOWN')} @ {bar_open_price:.5f} | {len(tp_levels)} TPs"
                    })
                    
                    # Notify CircuitBreaker of the new group
                    if hasattr(self.risk_engine, "circuit") and hasattr(self.risk_engine.circuit, "position_opened"):
                        actual_risk_dollars = sum(
                            calculate_risk_dollars(tp.volume, sig.get("entry_price", current_price), sig.get("stop_loss", 0), sig.get("symbol", ""))
                            for tp in tp_levels
                        )
                        self.risk_engine.circuit.position_opened(
                            group_id, 
                            len(tp_levels), 
                            symbol=sig.get("symbol", ""),
                            initial_risk_dollars=actual_risk_dollars
                        )
                else:
                    self.rejection_funnel["risk_rejections"][reason] = self.rejection_funnel["risk_rejections"].get(reason, 0) + 1
                    logger.trace(f"[ENGINE] ❌ Signal REJECTED (Risk): {reason}")

            # Track floating equity AFTER processing closes this bar:
            # Recompute open_pnl from positions that are still actually open
            # (not those just closed above). Prevents closed-trade PnL from
            # being double-counted in the equity curve on the bar they exit.
            post_close_pnl = sum(
                self._calc_pnl(p["direction"], p["entry_price"], current_price, p["volume"], p.get("symbol", ""))
                for p in self.open_positions
            )
            self.equity_curve.append(balance + post_close_pnl)

        # Close any remaining open positions at last price
        last_price = closes_arr[-1] if len(closes_arr) > 0 else 0
        last_time = time_arr[-1] if len(time_arr) > 0 else 0
        for pos in self.open_positions[:]:
            pos["pnl"] = self._calc_pnl(pos["direction"], pos["entry_price"], last_price, pos["volume"], pos.get("symbol", ""))
            pos["exit_price"] = last_price
            pos["exit_reason"] = "END_OF_DATA"
            pos["exit_time"] = last_time
            pos["duration_minutes"] = _calc_duration_minutes(pos.get("entry_time"), last_time)
            pos["entry_time_iso"] = _epoch_to_iso(pos.get("entry_time"))
            pos["exit_time_iso"] = _epoch_to_iso(last_time)
            pos["exit_confirmations"] = [
                "Exit Reason: END_OF_DATA (forced close)",
                f"Exit Price: {last_price:.5f}",
                f"PnL: ${pos.get('pnl', 0):.2f}",
            ]
            balance += pos.get("pnl", 0)
            pos["balance_after"] = balance
            self.trades.append(pos)
        self.open_positions = []
        self.equity_curve.append(balance)

        # Group trades by group_id for combined P&L display
        grouped_trades = group_trades(self.trades, candles, candles_m15, candles_m5)

        report = generate_risk_report(grouped_trades, initial_balance=initial_balance)
        report.rejection_funnel = self.rejection_funnel

        # ── Engine completion summary ──
        total_pnl = balance - initial_balance
        wins = sum(1 for t in grouped_trades if t.get("pnl", 0) > 0)
        losses = sum(1 for t in grouped_trades if t.get("pnl", 0) <= 0)
        logger.info("[ENGINE] ═══ Backtest engine complete ═══")
        logger.info(f"[ENGINE] Trades: {len(grouped_trades)} ({wins}W / {losses}L) | Invalid: {self.invalid_signals}")
        logger.info(f"[ENGINE] P&L: ${total_pnl:.2f} | Final balance: ${balance:.2f}")
        if self.trades:
            best = max(t.get("pnl", 0) for t in self.trades)
            worst = min(t.get("pnl", 0) for t in self.trades)
            logger.info(f"[ENGINE] Best trade: ${best:.2f} | Worst trade: ${worst:.2f}")

        return {
            "backtest_id": str(uuid.uuid4()),
            "initial_balance": initial_balance,
            "final_balance": balance,
            "total_pnl": total_pnl,
            "total_trades": len(self.trades),
            "total_signals": len(grouped_trades),
            "invalid_signals": self.invalid_signals,
            "trades": self.trades,
            "grouped_trades": grouped_trades,
            "equity_curve": self.equity_curve,
            "report": report,
            "rejection_funnel": self.rejection_funnel,
            "run_logs": self.run_logs,
        }

    def _create_position(
        self,
        sig: dict[str, Any],
        tp: TPLevel,
        current_time: Any,
        current_price: float,
        group_id: str,
        balance: float,
    ) -> dict[str, Any]:
        """Create a position dict with entry confirmations and group_id."""
        entry_price = sig.get("entry_price", current_price)

        # Detect entry session
        try:
            entry_session = detect_session(_to_epoch_seconds(current_time))
        except Exception:
            entry_session = "UNKNOWN"

        # Use detailed SMC confirmations from signal if available, plus position-specific info
        signal_confirmations = sig.get("confirmations", [])
        entry_confirmations = [
            f"Direction: {sig.get('direction', 'UNKNOWN')}",
            f"Entry Price: {entry_price:.5f}",
            f"Stop Loss: {sig.get('stop_loss', 0):.5f}",
            f"Take Profit (TP{tp.level}): {tp.tp_price:.5f}",
            f"RR Multiplier: 1:{tp.rr_multiplier:.1f}",
            f"Volume: {tp.volume:.2f} lots ({tp.volume_pct * 100:.0f}%)",
            f"Entry Session: {entry_session}",
            "Entry Mode: ALL AT ENTRY",
            f"Pattern: {sig.get('pattern', 'N/A')}",
            f"FVG: {'Yes' if sig.get('has_fvg') else 'No'}",
            f"Liquidity Sweep: {'Yes' if sig.get('has_liquidity_sweep') else 'No'}",
        ]
        # Append full Structural analysis
        if "confluence_breakdown" in sig.get("metadata", {}):
            entry_confirmations.append("── Structural Analysis ──")
            entry_confirmations.extend(signal_confirmations)

        return {
            "id": str(uuid.uuid4()),
            "group_id": group_id,
            "symbol": sig.get("symbol", ""),
            "direction": "BUY" if _is_buy(sig.get("direction", "BUY")) else "SELL",
            "entry_price": entry_price,
            "stop_loss": sig.get("stop_loss", 0),
            "take_profit": tp.tp_price,
            "volume": tp.volume,
            "tp_level": tp.level,
            "trail_method": tp.trail_method,
            "entry_time": current_time,
            "entry_time_iso": _epoch_to_iso(current_time),
            "entry_session": entry_session,
            "be_applied": False,
            "mae_pips": 0.0,
            "mfe_pips": 0.0,
            "confluence_score": sig.get("confluence_score", 0),
            "balance_before": balance,
            "status": "OPEN",
            "entry_confirmations": entry_confirmations,
            "entry_snapshot_b64": sig.get("metadata", {}).get("entry_snapshot_b64", ""),
            "original_signal": sig,
        }

    def _calc_pnl(self, direction: str, entry: float, exit_price: float, volume: float, symbol: str) -> float:
        """
        Calculate P&L using the same data source chain as position sizing:
        MT5 live data (when connected) -> InstrumentProfile -> Standard defaults.

        Applies simulation costs when configured:
          - slippage_pips: shifts effective entry price against the trade direction
          - spread_pips: pip cost of crossing bid/ask at entry (deducted from PnL)
          - commission_per_lot: round-turn broker commission (deducted from PnL)

        Formula: pnl = (price_diff / tick_size) * tick_value * volume - costs
        """
        from backend.risk.position_sizer import get_symbol_info
        info = get_symbol_info(symbol)
        tick_value = info.get("tick_value", 1.0)
        tick_size  = info.get("tick_size",  0.00001)
        source     = info.get("source", "UNKNOWN")
        pip_size   = get_pip_size(symbol)

        if source == "DEFAULT":
            logger.warning(f"[_calc_pnl] {symbol}: PnL computed with DEFAULT fallback — may be incorrect!")

        if tick_size == 0 or tick_value == 0:
            logger.warning(f"[_calc_pnl] {symbol}: tick_size or tick_value is zero — returning 0 PnL.")
            return 0.0

        value_per_unit_move = tick_value / tick_size

        # Apply slippage: shift effective entry against the trade direction
        if self._slippage_pips > 0 and pip_size > 0:
            slippage_price = self._slippage_pips * pip_size
            if _is_buy(direction):
                entry = entry + slippage_price  # BUY fills higher (worse)
            else:
                entry = entry - slippage_price  # SELL fills lower (worse)

        price_diff = exit_price - entry
        raw_pnl = price_diff * value_per_unit_move * volume
        if not _is_buy(direction):
            raw_pnl = -raw_pnl

        # Deduct spread cost (pip cost of crossing bid/ask at entry)
        if self._spread_pips > 0 and pip_size > 0:
            spread_cost = self._spread_pips * pip_size * value_per_unit_move * volume
            raw_pnl -= spread_cost

        # Deduct round-turn commission
        if self._commission_per_lot > 0:
            raw_pnl -= self._commission_per_lot * volume

        return raw_pnl