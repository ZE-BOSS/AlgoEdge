"""
backend/backtester/engine.py

Bar-by-bar backtesting engine using the same RiskEngine as live.
Source: TradingBot_MasterPlan-2.md Section 11
Source: RiskManagement_Spec.md Section 7

Enhanced with:
  - Trade entry/exit validation (directional SL/TP checks)
  - Entry AND exit confirmation arrays
  - ISO datetime timestamps + trade duration
  - Deferred TP re-entry based on candlestick + volume conviction
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
from backend.strategies.smc.candlestick import detect_confirmation_pattern
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
    Source: RiskManagement_Spec.md Section 7
    """

    def __init__(self, risk_config: Dict[str, Any]):
        self.risk_engine = RiskEngine(risk_config)
        self.risk_config = risk_config
        self.trades: List[Dict[str, Any]] = []
        self.open_positions: List[Dict[str, Any]] = []
        self.equity_curve: List[float] = []
        self.deferred_tps: List[Dict[str, Any]] = []  # Pending deferred TP positions
        self.invalid_signals: int = 0  # Count of rejected signals due to validation

    def run(
        self,
        candles: pd.DataFrame,
        signals: List[Dict[str, Any]],
        initial_balance: float = 10000.0,
        compounding_enabled: bool = False,
    ) -> Dict[str, Any]:
        """
        Run a backtest on historical candles with pre-generated signals.
        Returns full backtest results with entry/exit confirmations.
        """
        balance = initial_balance
        self.trades = []
        self.open_positions = []
        self.deferred_tps = []
        self.equity_curve = [balance]
        self.invalid_signals = 0

        signal_idx = 0

        for i in range(len(candles)):
            bar = candles.iloc[i]
            # Use 'time' column if available, otherwise fall back to index
            if 'time' in candles.columns:
                current_time = bar['time']
            elif hasattr(bar.name, 'timestamp'):
                current_time = bar.name
            else:
                current_time = i
            current_price = bar["close"]
            high = bar["high"]
            low = bar["low"]

            # 1. Manage existing open positions
            closed_this_bar = []
            for pos in self.open_positions[:]:
                # Check SL hit
                if pos["direction"] == "BUY" and low <= pos["stop_loss"]:
                    pos["exit_price"] = pos["stop_loss"]
                    pos["exit_reason"] = "SL"
                    contract = self._get_contract_size(pos.get("symbol", ""))
                    pos["pnl"] = (pos["exit_price"] - pos["entry_price"]) * pos["volume"] * contract
                    closed_this_bar.append(pos)
                    continue
                elif pos["direction"] == "SELL" and high >= pos["stop_loss"]:
                    pos["exit_price"] = pos["stop_loss"]
                    pos["exit_reason"] = "SL"
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
                    # Check if TP1 hit should trigger deferred TP activation
                    if pos.get("tp_level") == 1:
                        self._check_deferred_activation(candles, i, pos)
                    continue
                elif pos["direction"] == "SELL" and low <= pos["take_profit"]:
                    pos["exit_price"] = pos["take_profit"]
                    pos["exit_reason"] = f"TP{pos.get('tp_level', 1)}"
                    contract = self._get_contract_size(pos.get("symbol", ""))
                    pos["pnl"] = (pos["entry_price"] - pos["exit_price"]) * pos["volume"] * contract
                    closed_this_bar.append(pos)
                    if pos.get("tp_level") == 1:
                        self._check_deferred_activation(candles, i, pos)
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

                # Run BE/trailing checks via RiskEngine
                actions = self.risk_engine.manage_open_position(pos, current_price)
                for action in actions:
                    if action["action"] == "MODIFY_SL":
                        pos["stop_loss"] = action["new_sl"]
                        if action.get("reason") == "BREAKEVEN":
                            pos["be_applied"] = True

            # Close positions and build exit confirmations
            for pos in closed_this_bar:
                pos["exit_time"] = current_time
                # Tag session from exit time
                try:
                    pos["session"] = detect_session(current_time)
                except Exception:
                    pos["session"] = "UNKNOWN"

                # Calculate duration
                pos["duration_minutes"] = _calc_duration_minutes(
                    pos.get("entry_time"), pos.get("exit_time")
                )

                # ISO timestamps
                pos["entry_time_iso"] = _epoch_to_iso(pos.get("entry_time"))
                pos["exit_time_iso"] = _epoch_to_iso(pos.get("exit_time"))

                # Exit confirmation array
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

                is_win = pos.get("pnl", 0) > 0
                balance += pos.get("pnl", 0)
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
                    # Separate immediate vs deferred TPs
                    immediate_tps = [tp for tp in tp_levels if not tp.deferred]
                    deferred_tps = [tp for tp in tp_levels if tp.deferred]

                    for tp in immediate_tps:
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

                        position = self._create_position(sig, tp, current_time, current_price)
                        self.open_positions.append(position)

                    # Store deferred TPs for later activation
                    for tp in deferred_tps:
                        self.deferred_tps.append({
                            "signal": sig,
                            "tp_level": tp,
                            "parent_entry_time": current_time,
                            "activated": False,
                        })

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
                f"Duration: {pos.get('duration_minutes', 0):.1f} min",
            ]
            balance += pos.get("pnl", 0)
            self.trades.append(pos)
        self.open_positions = []
        self.equity_curve.append(balance)

        # Generate report
        report = generate_risk_report(self.trades)

        return {
            "backtest_id": str(uuid.uuid4()),
            "initial_balance": initial_balance,
            "final_balance": balance,
            "total_trades": len(self.trades),
            "invalid_signals": self.invalid_signals,
            "deferred_activations": sum(1 for d in self.deferred_tps if d["activated"]),
            "trades": self.trades,
            "equity_curve": self.equity_curve,
            "report": report,
        }

    def _create_position(
        self,
        sig: Dict[str, Any],
        tp: TPLevel,
        current_time: Any,
        current_price: float,
    ) -> Dict[str, Any]:
        """Create a position dict with entry confirmations."""
        entry_price = sig.get("entry_price", current_price)

        # Build entry confirmation array
        entry_confirmations = [
            f"Direction: {sig.get('direction', 'UNKNOWN')}",
            f"Entry Price: {entry_price:.5f}",
            f"Stop Loss: {sig.get('stop_loss', 0):.5f}",
            f"Take Profit (TP{tp.level}): {tp.tp_price:.5f}",
            f"RR Multiplier: 1:{tp.rr_multiplier:.1f}",
            f"Volume: {tp.volume:.2f} lots ({tp.volume_pct * 100:.0f}%)",
            f"Confluence Score: {sig.get('confluence_score', 0)}",
            f"Entry Type: {sig.get('signal_type', sig.get('entry_type', 'OB_ENTRY'))}",
        ]

        # Add deferred info if applicable
        if tp.deferred:
            entry_confirmations.append("Entry Mode: DEFERRED (conviction-based re-entry)")
        else:
            entry_confirmations.append("Entry Mode: IMMEDIATE")

        return {
            "id": str(uuid.uuid4()),
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
            "be_applied": False,
            "mae_pips": 0.0,
            "mfe_pips": 0.0,
            "confluence_score": sig.get("confluence_score", 0),
            "original_sl": sig.get("stop_loss", 0),
            "highest_price": entry_price,
            "lowest_price": entry_price,
            "entry_confirmations": entry_confirmations,
        }

    def _check_deferred_activation(
        self,
        candles: pd.DataFrame,
        bar_idx: int,
        closed_pos: Dict[str, Any],
    ):
        """
        After TP1 hits, check if deferred TPs (TP3–5) should be activated.
        Conviction criteria:
          1. Candlestick confirmation pattern in trade direction
          2. Volume surge (tick_volume > 1.5x 20-bar average)
          3. Price holding in profitable territory
        """
        direction = closed_pos.get("direction", "BUY")
        symbol = closed_pos.get("symbol", "")

        # Find deferred TPs for this signal
        pending = [d for d in self.deferred_tps
                    if not d["activated"]
                    and d["signal"].get("symbol") == symbol
                    and d["signal"].get("direction") == direction]

        if not pending:
            return

        # Check conviction criteria
        lookback = 3
        start_idx = max(0, bar_idx - lookback)
        recent = candles.iloc[start_idx:bar_idx + 1]

        # 1. Candlestick confirmation
        bias = "BULLISH" if _is_buy(direction) else "BEARISH"
        pattern = None
        try:
            pattern = detect_confirmation_pattern(recent, bias=bias)
        except Exception:
            pass

        # 2. Volume confirmation (if tick_volume column exists)
        volume_confirmed = False
        if 'tick_volume' in candles.columns and bar_idx >= 20:
            avg_vol = candles['tick_volume'].iloc[max(0, bar_idx-20):bar_idx].mean()
            curr_vol = candles['tick_volume'].iloc[bar_idx]
            volume_confirmed = curr_vol > avg_vol * 1.5 if avg_vol > 0 else False

        # Need at least one conviction criterion met
        if pattern is not None or volume_confirmed:
            current_time = candles.iloc[bar_idx].get('time', bar_idx) if 'time' in candles.columns else bar_idx
            current_price = candles.iloc[bar_idx]['close']

            for deferred in pending:
                tp = deferred["tp_level"]
                sig = deferred["signal"]

                # Validate before activating
                is_valid, err = _validate_position(
                    direction,
                    current_price,  # Re-enter at current price
                    sig.get("stop_loss", 0),
                    tp.tp_price,
                )
                if not is_valid:
                    logger.debug(f"Deferred TP{tp.level} invalid at activation: {err}")
                    continue

                # Create the deferred position at current price
                deferred_sig = {**sig, "entry_price": current_price}
                position = self._create_position(deferred_sig, tp, current_time, current_price)
                position["entry_confirmations"].append(
                    f"Deferred Conviction: "
                    f"{'Pattern: ' + pattern.name if pattern else 'Volume surge confirmed'}"
                )
                self.open_positions.append(position)
                deferred["activated"] = True

                logger.debug(
                    f"Deferred TP{tp.level} activated for {symbol} @ {current_price:.5f} "
                    f"(conviction: pattern={pattern is not None}, volume={volume_confirmed})"
                )

    def _get_contract_size(self, symbol: str) -> float:
        """Get the contract size for a symbol via instrument profile or default."""
        profile = get_instrument_profile(symbol)
        if profile:
            return profile.contract_size
        # Sensible defaults
        if any(s in symbol.upper() for s in ["V10", "V25", "V50", "V75", "V100",
                                               "BOOM", "CRASH", "JUMP", "STEP", "DSI", "RANGE"]):
            return 1  # Synthetics
        if "XAU" in symbol.upper() or "GOLD" in symbol.upper():
            return 100  # Gold
        if any(s in symbol.upper() for s in ["US30", "NAS", "US500"]):
            return 1  # Indices
        return 100000  # Forex default
