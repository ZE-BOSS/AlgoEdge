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
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import pandas as pd
import numpy as np

from backend.risk.engine import RiskEngine
from backend.risk.multi_tp import MultiTPManager, TPLevel, _is_buy
from backend.risk.position_sizer import get_pip_size
from backend.risk.compounding import get_instrument_profile
from backend.analytics.metrics import compute_portfolio_stats
from backend.analytics.reports import generate_risk_report, RiskReport
from backend.utils.logger import get_logger
from backend.utils.timeutils import detect_session

logger = get_logger(__name__)


def _epoch_to_iso(epoch_val) -> str:
    """Convert epoch timestamp or datetime to ISO string."""
    try:
        if isinstance(epoch_val, (int, float)):
            return datetime.fromtimestamp(int(epoch_val), tz=timezone.utc).isoformat()
        elif isinstance(epoch_val, datetime):
            return epoch_val.isoformat()
        elif isinstance(epoch_val, str):
            return epoch_val
        return str(epoch_val)
    except Exception:
        return str(epoch_val)


def _calc_duration_minutes(entry_time, exit_time) -> float:
    """Calculate trade duration in minutes from timestamps."""
    try:
        if isinstance(entry_time, (int, float)) and isinstance(exit_time, (int, float)):
            return (exit_time - entry_time) / 60.0
        elif hasattr(entry_time, "timestamp") and hasattr(exit_time, "timestamp"):
            return (exit_time.timestamp() - entry_time.timestamp()) / 60.0
        return 0.0
    except Exception:
        return 0.0


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

    def __init__(self, risk_config: Dict[str, Any]):
        self.risk_engine = RiskEngine(risk_config)
        self.risk_config = risk_config
        self.trades: List[Dict[str, Any]] = []
        self.open_positions: List[Dict[str, Any]] = []
        self.equity_curve: List[float] = []
        self.invalid_signals: int = 0
        self.rejection_funnel: Dict[str, Any] = {
            "total_evaluated": 0,
            "strategy_rejections": {},
            "risk_rejections": {},
            "errors": 0,
            "approved": 0
        }

    def run(
        self,
        candles: pd.DataFrame,
        signals: List[Dict[str, Any]],
        initial_balance: float = 10000.0,
        candles_m15: pd.DataFrame = None,
        candles_m5: pd.DataFrame = None,
        compounding_enabled: bool = False,
    ) -> Dict[str, Any]:
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

        logger.info(f"[ENGINE] ═══ Starting backtest engine ═══")
        logger.info(f"[ENGINE] Balance: ${initial_balance} | Signals: {len(signals)} | Candles: {len(candles)}")
        logger.info(f"[ENGINE] Risk config: risk_pct={self.risk_config.get('risk_per_trade_pct')}% | min_rr={self.risk_config.get('min_rr')} | tp_count={self.risk_config.get('tp_count')}")

        signal_idx = 0

        # ── Pre-compute ATR array (vectorized, O(n)) ──
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
        sw_len = self.risk_config.get("swing_length", 5)
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
            bar = candles.iloc[i]
            if 'time' in candles.columns:
                current_time = bar['time']
            elif hasattr(bar.name, 'timestamp'):
                current_time = bar.name
            else:
                current_time = i
            current_price = bar["close"]
            high = bar["high"]
            low = bar["low"]

            # Look up pre-computed ATR and swing points
            current_atr = atr_array[i] if i < len(atr_array) else 0.0
            swing_points = swing_cache.get(i, [])

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
                c_ts = current_time.timestamp() if hasattr(current_time, "timestamp") else current_time
                e_ts = pos["entry_time"].timestamp() if hasattr(pos.get("entry_time"), "timestamp") else pos.get("entry_time")
                
                pos["bars_held"] = pos.get("bars_held", 0) + 1
                symbol_upper = pos.get("symbol", "").upper()
                is_crashboom = "CRASH" in symbol_upper or "BOOM" in symbol_upper
                
                limit_hit = False
                if is_crashboom and pos["bars_held"] >= 400:
                    limit_hit = True
                elif not is_crashboom and isinstance(c_ts, (int, float)) and isinstance(e_ts, (int, float)):
                    if c_ts > 1e8 and e_ts > 1e8 and (c_ts - e_ts >= 48 * 3600):
                        limit_hit = True
                        
                if limit_hit:
                    pos["exit_price"] = current_price
                    pos["exit_reason"] = "TIME_LIMIT"
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
                    
                # C9: Look-ahead bias handling
                if sl_hit and tp_hit:
                    dist_to_sl = abs(pos["stop_loss"] - bar.get("open", current_price))
                    dist_to_tp = abs(pos["take_profit"] - bar.get("open", current_price))
                    if dist_to_sl <= dist_to_tp:
                        tp_hit = False
                    else:
                        sl_hit = False

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

                # Update MAE/MFE
                if pos["direction"] == "BUY":
                    adverse = pos["entry_price"] - low
                    favorable = high - pos["entry_price"]
                else:
                    adverse = high - pos["entry_price"]
                    favorable = pos["entry_price"] - low

                pip_size = get_pip_size(pos.get("symbol", ""))
                pos["mae_pips"] = max(pos.get("mae_pips", 0), adverse / pip_size if pip_size else 0)
                pos["mfe_pips"] = max(pos.get("mfe_pips", 0), favorable / pip_size if pip_size else 0)

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
                    pos["session"] = detect_session(current_time)
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
                self.risk_engine.on_position_closed(pos.get("group_id", "unknown"), pos.get("pnl", 0), current_time)
                self.trades.append(pos)
                positions_to_remove.append(pos)
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

                current_time_dt = datetime.fromtimestamp(float(current_time), timezone.utc) if isinstance(current_time, (int, float)) else pd.to_datetime(current_time).to_pydatetime()
                
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
                try:
                    approved, reason, tp_levels = self.risk_engine.evaluate_signal(
                        signal_data=sig,
                        account_balance=balance,
                        current_time=current_time_dt
                    )
                except Exception as e:
                    import traceback
                    traceback.print_exc()
                    self.rejection_funnel["errors"] += 1
                    logger.error(f"[ENGINE] ❌ Error evaluating signal: {str(e)}")
                    continue

                if approved:
                    self.rejection_funnel["approved"] += 1
                    logger.trace(f"[ENGINE] ✅ Signal APPROVED at bar {i}: {sig.get('direction')} @ {sig.get('entry_price', current_price):.5f} | {len(tp_levels)} TP levels | balance=${balance:.2f}")

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

                        # FIX 1 (Lookahead bias): entry price is the OPEN of the current bar
                        # (which is the bar AFTER the signal was generated — guaranteed by the
                        # sig_time >= current_timestamp guard above). This eliminates same-bar fill.
                        bar_open_price = float(bar.get("open", current_price))
                        position = self._create_position(sig, tp, current_time, bar_open_price, group_id, balance)
                        self.open_positions.append(position)
                        logger.debug(f"[ENGINE]   Position opened: TP{tp.level} @ {bar_open_price:.5f} (bar open) | vol={tp.volume:.4f}")
                else:
                    self.rejection_funnel["risk_rejections"][reason] = self.rejection_funnel["risk_rejections"].get(reason, 0) + 1
                    logger.trace(f"[ENGINE] ❌ Signal REJECTED (Risk): {reason}")

            self.equity_curve.append(balance)

        # Close any remaining open positions at last price
        last_price = candles.iloc[-1]["close"] if len(candles) > 0 else 0
        last_time = candles.iloc[-1].get("time", len(candles) - 1) if len(candles) > 0 else 0
        for pos in self.open_positions[:]:
            pos["pnl"] = self._calc_pnl(pos["direction"], pos["entry_price"], last_price, pos["volume"], pos.get("symbol", ""))
            pos["exit_price"] = last_price
            pos["exit_reason"] = "END_OF_DATA"
            pos["exit_time"] = last_time
            pos["duration_minutes"] = _calc_duration_minutes(pos.get("entry_time"), last_time)
            pos["entry_time_iso"] = _epoch_to_iso(pos.get("entry_time"))
            pos["exit_time_iso"] = _epoch_to_iso(last_time)
            pos["exit_confirmations"] = [
                f"Exit Reason: END_OF_DATA (forced close)",
                f"Exit Price: {last_price:.5f}",
                f"PnL: ${pos.get('pnl', 0):.2f}",
            ]
            balance += pos.get("pnl", 0)
            pos["balance_after"] = balance
            self.trades.append(pos)
        self.open_positions = []
        self.equity_curve.append(balance)

        # Group trades by group_id for combined P&L display
        grouped_trades = self._group_trades(self.trades, candles, candles_m15, candles_m5)

        report = generate_risk_report(grouped_trades)
        report.rejection_funnel = self.rejection_funnel

        # ── Engine completion summary ──
        total_pnl = balance - initial_balance
        wins = sum(1 for t in grouped_trades if t.get("pnl", 0) > 0)
        losses = sum(1 for t in grouped_trades if t.get("pnl", 0) <= 0)
        logger.info(f"[ENGINE] ═══ Backtest engine complete ═══")
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
        }

    def _create_position(
        self,
        sig: Dict[str, Any],
        tp: TPLevel,
        current_time: Any,
        current_price: float,
        group_id: str,
        balance: float,
    ) -> Dict[str, Any]:
        """Create a position dict with entry confirmations and group_id."""
        entry_price = sig.get("entry_price", current_price)

        # Detect entry session
        try:
            entry_session = detect_session(current_time)
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
            f"Entry Mode: ALL AT ENTRY",
            f"Pattern: {sig.get('pattern', 'N/A')}",
            f"FVG: {'Yes' if sig.get('has_fvg') else 'No'}",
            f"Liquidity Sweep: {'Yes' if sig.get('has_liquidity_sweep') else 'No'}",
        ]
        # Append full SMC analysis
        if signal_confirmations:
            entry_confirmations.append("── SMC Analysis ──")
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

    def _group_trades(self, trades: List[Dict], candles: Any = None, candles_m15: Any = None, candles_m5: Any = None) -> List[Dict]:
        """Group trades by group_id for combined P&L display, extracting chart data for frontend."""
        from collections import OrderedDict
        groups = OrderedDict()
        for t in trades:
            gid = t.get("group_id", t.get("id", "unknown"))
            if gid not in groups:
                groups[gid] = {
                    "group_id": gid,
                    "symbol": t.get("symbol", ""),
                    "direction": t.get("direction", ""),
                    "entry_price": t.get("entry_price", 0),
                    "entry_time": t.get("entry_time"),
                    "entry_time_iso": t.get("entry_time_iso", ""),
                    "entry_session": t.get("entry_session", "UNKNOWN"),
                    "stop_loss": t.get("stop_loss", 0),
                    "sub_trades": [],
                    "combined_pnl": 0,
                    "combined_volume": 0,
                    "tp_count": 0,
                    "best_exit": None,
                    "worst_exit": None,
                    "entry_snapshot_b64": t.get("entry_snapshot_b64", ""),
                    "entry_confirmations": t.get("entry_confirmations", []),
                    "confluence_score": t.get("confluence_score", 0),
                    "balance_before": t.get("balance_before", 0),
                    "chart_data": [],
                    "smc_data": {"boxes": [], "lines": [], "markers": []},
                    "original_signal": t.get("original_signal", {}),
                }
            g = groups[gid]
            g["sub_trades"].append(t)
            g["combined_pnl"] += t.get("pnl", 0)
            g["combined_volume"] += t.get("volume", 0)
            g["tp_count"] += 1

            # Track best/worst exits
            if g["best_exit"] is None or t.get("pnl", 0) > g["best_exit"].get("pnl", 0):
                g["best_exit"] = t
            if g["worst_exit"] is None or t.get("pnl", 0) < g["worst_exit"].get("pnl", 0):
                g["worst_exit"] = t

        # Add summary fields
        for g in groups.values():
            sub_trades = g["sub_trades"]
            wins = sum(1 for t in sub_trades if t.get("pnl", 0) > 0)
            g["tp_wins"] = wins
            g["tp_losses"] = g["tp_count"] - wins
            g["is_net_winner"] = g["combined_pnl"] > 0
            
            # These aliases allow the group to be treated as a single trade by the reporting engine
            g["pnl"] = g["combined_pnl"]
            g["exit_price"] = g["best_exit"].get("exit_price", 0) if g["best_exit"] else 0
            g["exit_reason"] = g["best_exit"].get("exit_reason", "UNKNOWN") if g["best_exit"] else "UNKNOWN"
            # FIX 3: Propagate balance_before/after and net_pnl from sub_trades
            sorted_subs = sorted(sub_trades, key=lambda x: x.get("exit_time", 0))
            g["balance_before"] = sub_trades[0].get("balance_before") if sub_trades else None
            g["balance_after"]  = sorted_subs[-1].get("balance_after") if sorted_subs else None
            g["net_pnl"]        = sum(t.get("pnl", 0) for t in sub_trades)
            
            # Find the last exit time
            exit_times = [t.get("exit_time", 0) for t in g["sub_trades"] if t.get("exit_time")]
            if exit_times:
                g["exit_time"] = max(exit_times)
                g["exit_time_iso"] = _epoch_to_iso(max(exit_times))
                g["duration_minutes"] = _calc_duration_minutes(g["entry_time"], max(exit_times))
            # Detect exit session
            try:
                g["exit_session"] = detect_session(g.get("exit_time", 0))
            except Exception:
                g["exit_session"] = "UNKNOWN"

            # ── Extract Chart Data and SMC Zones ──
            if candles is not None and not candles.empty and g.get("entry_time"):
                # Find entry index
                entry_time = g["entry_time"]
                exit_time = g.get("exit_time", entry_time)
                
                try:
                    # Find indices for entry and exit times
                    def _extract_chart_data(df, e_time, x_time, pad_start, pad_end):
                        if df is None or df.empty: return []
                        e_time_dt = pd.to_datetime(e_time, unit='s', utc=True) if isinstance(e_time, (int, float)) else pd.to_datetime(e_time)
                        x_time_dt = pd.to_datetime(x_time, unit='s', utc=True) if isinstance(x_time, (int, float)) else pd.to_datetime(x_time)
                        
                        if isinstance(df.index, pd.DatetimeIndex):
                            e_time_naive = e_time_dt.tz_localize(None) if getattr(e_time_dt, 'tzinfo', None) else e_time_dt
                            x_time_naive = x_time_dt.tz_localize(None) if getattr(x_time_dt, 'tzinfo', None) else x_time_dt
                            idx_naive = df.index.tz_localize(None) if getattr(df.index, 'tz', None) else df.index
                            
                            s_idx = max(0, idx_naive.searchsorted(e_time_naive, side='right') - 1 - pad_start)
                            e_idx = min(len(df), idx_naive.searchsorted(x_time_naive, side='left') + pad_end)
                        else:
                            s_idx = max(0, df['time'].searchsorted(e_time, side='right') - 1 - pad_start)
                            e_idx = min(len(df), df['time'].searchsorted(x_time, side='left') + pad_end)
                            
                        # CRITICAL FIX: Cap the slice to prevent MemoryError on massive multi-week trades
                        if e_idx - s_idx > 500:
                            e_idx = s_idx + 500
                            
                        slice_d = df.iloc[s_idx:e_idx]
                        c_data = []
                        for idx, row in slice_d.iterrows():
                            t = int(idx.timestamp()) if isinstance(idx, pd.Timestamp) else int(row.get("time", 0))
                            c_data.append({
                                "time": t,
                                "open": float(row.get("open", 0)),
                                "high": float(row.get("high", 0)),
                                "low": float(row.get("low", 0)),
                                "close": float(row.get("close", 0))
                            })
                        return c_data

                    g["chart_data"] = _extract_chart_data(candles, entry_time, exit_time, 30, 15)
                    g["chart_data_m15"] = _extract_chart_data(candles_m15, entry_time, exit_time, 20, 5)
                    g["chart_data_m5"] = _extract_chart_data(candles_m5, entry_time, exit_time, 30, 10)
                    
                    # Generate SMC zones based on the first trade's signal data
                    sig = g.get("original_signal", {})
                    # Generate SMC zones from signal metadata
                    markings = sig.get("metadata", {}).get("markings", [])
                    g["smc_data"]["boxes"] = [m for m in markings if m["type"] in ("OB", "FVG")]
                    g["smc_data"]["markers"] = [m for m in markings if m["type"] == "STRUCTURE"]
                    
                except Exception as e:
                    import traceback
                    traceback.print_exc()

        return list(groups.values())

    def _calc_pnl(self, direction: str, entry: float, exit_price: float, volume: float, symbol: str) -> float:
        """
        Calculate P&L using InstrumentProfile point-value model.
        Formula: pnl = (price_diff / point_size) * point_value_per_lot * volume
        Fixes Issue #1: was using contract_size which gives wildly wrong results for synthetics.
        """
        profile = get_instrument_profile(symbol)
        if profile:
            price_diff = exit_price - entry
            points = price_diff / profile.point_size if profile.point_size else 0
            raw_pnl = points * profile.point_value_per_lot * volume
        else:
            # Fallback for unknown symbols: assume standard forex
            pip_size = get_pip_size(symbol)
            price_diff = exit_price - entry
            pips = price_diff / pip_size if pip_size else 0
            # Standard forex: 1 pip = $10 per standard lot
            raw_pnl = pips * 10.0 * volume
            logger.warning(f"No InstrumentProfile for '{symbol}', using forex fallback")

        return raw_pnl if direction == "BUY" else -raw_pnl
