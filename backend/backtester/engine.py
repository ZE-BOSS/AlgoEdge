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

    def run(
        self,
        candles: pd.DataFrame,
        signals: List[Dict[str, Any]],
        initial_balance: float = 10000.0,
        compounding_enabled: bool = False,
    ) -> Dict[str, Any]:
        """Run a backtest on historical candles with pre-generated signals."""
        balance = initial_balance
        self.trades = []
        self.open_positions = []
        self.equity_curve = [balance]
        self.invalid_signals = 0

        signal_idx = 0

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

            # ── Compute ATR at current bar for trailing stops ──
            atr_period = 14
            if i >= atr_period:
                tr_vals = []
                for j in range(max(0, i - atr_period), i):
                    b = candles.iloc[j]
                    prev_close = candles.iloc[j-1]["close"] if j > 0 else b["close"]
                    tr = max(b["high"] - b["low"],
                             abs(b["high"] - prev_close),
                             abs(b["low"] - prev_close))
                    tr_vals.append(tr)
                current_atr = float(np.mean(tr_vals))
            else:
                current_atr = 0.0

            # ── Build swing points for structure trail ──
            swing_points = []
            swing_lookback = 20
            sw_len = self.risk_config.get("swing_length", 5)
            if i >= swing_lookback:
                for j in range(max(sw_len, i - swing_lookback), i - sw_len):
                    window_h = candles.iloc[j-sw_len:j+1]["high"].values
                    window_l = candles.iloc[j-sw_len:j+1]["low"].values
                    if len(window_h) > 0 and candles.iloc[j]["high"] == max(window_h):
                        swing_points.append({"type": "HIGH", "price": float(candles.iloc[j]["high"])})
                    if len(window_l) > 0 and candles.iloc[j]["low"] == min(window_l):
                        swing_points.append({"type": "LOW", "price": float(candles.iloc[j]["low"])})

            # 1. Manage existing open positions
            closed_this_bar = []
            tp1_hit_groups = set()  # Track which groups had TP1 hit this bar

            for pos in self.open_positions[:]:
                # Update highest/lowest price tracking for trailing
                if pos["direction"] == "BUY":
                    pos["highest_price"] = max(pos.get("highest_price", pos["entry_price"]), high)
                else:
                    pos["lowest_price"] = min(pos.get("lowest_price", pos["entry_price"]), low)

                # Check SL hit
                if pos["direction"] == "BUY" and low <= pos["stop_loss"]:
                    pos["exit_price"] = pos["stop_loss"]
                    pos["exit_reason"] = "BE_SL" if pos.get("be_applied") else "SL"
                    contract = self._get_contract_size(pos.get("symbol", ""))
                    pos["pnl"] = (pos["exit_price"] - pos["entry_price"]) * pos["volume"] * contract
                    closed_this_bar.append(pos)
                    continue
                elif pos["direction"] == "SELL" and high >= pos["stop_loss"]:
                    pos["exit_price"] = pos["stop_loss"]
                    pos["exit_reason"] = "BE_SL" if pos.get("be_applied") else "SL"
                    contract = self._get_contract_size(pos.get("symbol", ""))
                    pos["pnl"] = (pos["entry_price"] - pos["exit_price"]) * pos["volume"] * contract
                    closed_this_bar.append(pos)
                    continue

                # Check TP hit
                if pos["direction"] == "BUY" and high >= pos["take_profit"]:
                    pos["exit_price"] = pos["take_profit"]
                    pos["exit_reason"] = f"TP{pos.get('tp_level', 1)}"
                    contract = self._get_contract_size(pos.get("symbol", ""))
                    pos["pnl"] = (pos["exit_price"] - pos["entry_price"]) * pos["volume"] * contract
                    closed_this_bar.append(pos)
                    if pos.get("tp_level") == 1:
                        tp1_hit_groups.add(pos.get("group_id"))
                    continue
                elif pos["direction"] == "SELL" and low <= pos["take_profit"]:
                    pos["exit_price"] = pos["take_profit"]
                    pos["exit_reason"] = f"TP{pos.get('tp_level', 1)}"
                    contract = self._get_contract_size(pos.get("symbol", ""))
                    pos["pnl"] = (pos["entry_price"] - pos["exit_price"]) * pos["volume"] * contract
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
                is_win = pos.get("pnl", 0) > 0
                self.risk_engine.on_position_closed(pos.get("pnl", 0), is_win)
                self.trades.append(pos)
                self.open_positions.remove(pos)

            # 2. Check for new signals on this bar
            while signal_idx < len(signals):
                sig = signals[signal_idx]
                sig_time = sig.get("time", signal_idx)
                if sig_time > i:
                    break
                signal_idx += 1

                # Evaluate signal through RiskEngine
                approved, reason, tp_levels = self.risk_engine.evaluate_signal(
                    sig, balance
                )

                if approved:
                    # Generate a group_id to link all sub-positions from this signal
                    group_id = str(uuid.uuid4())[:8]

                    for tp in tp_levels:
                        # Validate before opening
                        is_valid, err = _validate_position(
                            sig.get("direction", "BUY"),
                            sig.get("entry_price", current_price),
                            sig.get("stop_loss", 0),
                            tp.tp_price,
                        )
                        if not is_valid:
                            logger.warning(f"Invalid position rejected: {err}")
                            self.invalid_signals += 1
                            continue

                        position = self._create_position(sig, tp, current_time, current_price, group_id)
                        self.open_positions.append(position)

            self.equity_curve.append(balance)

        # Close any remaining open positions at last price
        last_price = candles.iloc[-1]["close"] if len(candles) > 0 else 0
        last_time = candles.iloc[-1].get("time", len(candles) - 1) if len(candles) > 0 else 0
        for pos in self.open_positions[:]:
            contract = self._get_contract_size(pos.get("symbol", ""))
            if pos["direction"] == "BUY":
                pos["pnl"] = (last_price - pos["entry_price"]) * pos["volume"] * contract
            else:
                pos["pnl"] = (pos["entry_price"] - last_price) * pos["volume"] * contract
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
            self.trades.append(pos)
        self.open_positions = []
        self.equity_curve.append(balance)

        # Group trades by group_id for combined P&L display
        grouped_trades = self._group_trades(self.trades)

        report = generate_risk_report(self.trades)

        return {
            "backtest_id": str(uuid.uuid4()),
            "initial_balance": initial_balance,
            "final_balance": balance,
            "total_trades": len(self.trades),
            "total_signals": len(grouped_trades),
            "invalid_signals": self.invalid_signals,
            "trades": self.trades,
            "grouped_trades": grouped_trades,
            "equity_curve": self.equity_curve,
            "report": report,
        }

    def _create_position(
        self,
        sig: Dict[str, Any],
        tp: TPLevel,
        current_time: Any,
        current_price: float,
        group_id: str,
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
            "direction": sig.get("direction", "BUY"),
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
            "entry_confirmations": entry_confirmations,
        }

    def _group_trades(self, trades: List[Dict]) -> List[Dict]:
        """Group trades by group_id for combined P&L display."""
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
            wins = sum(1 for t in g["sub_trades"] if t.get("pnl", 0) > 0)
            g["tp_wins"] = wins
            g["tp_losses"] = g["tp_count"] - wins
            g["is_net_winner"] = g["combined_pnl"] > 0
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

        return list(groups.values())

    def _get_contract_size(self, symbol: str) -> float:
        """Get contract size for P&L calculation."""
        try:
            profile = get_instrument_profile(symbol)
            return profile.get("contract_size", 1.0)
        except Exception:
            sym = symbol.upper()
            if "XAU" in sym or "GOLD" in sym:
                return 100.0  # 1 lot = 100 oz
            if "US30" in sym or "DJ" in sym:
                return 1.0
            if "BTC" in sym:
                return 1.0
            return 100000.0  # Standard forex
