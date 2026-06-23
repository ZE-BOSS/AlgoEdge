"""
backend/backtester/engine.py

Bar-by-bar backtesting engine using the same RiskEngine as live.
Source: TradingBot_MasterPlan-2.md Section 11
Source: RiskManagement_Spec.md Section 7
"""

import uuid
from typing import Dict, Any, List, Optional
from datetime import datetime
import pandas as pd
import numpy as np

from backend.risk.engine import RiskEngine
from backend.risk.position_sizer import get_pip_size
from backend.risk.compounding import get_instrument_profile
from backend.analytics.metrics import compute_portfolio_stats
from backend.analytics.reports import generate_risk_report, RiskReport
from backend.utils.logger import get_logger
from backend.utils.timeutils import detect_session

logger = get_logger(__name__)


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

    def run(
        self,
        candles: pd.DataFrame,
        signals: List[Dict[str, Any]],
        initial_balance: float = 10000.0,
        compounding_enabled: bool = False,
    ) -> Dict[str, Any]:
        """
        Run a backtest on historical candles with pre-generated signals.
        Returns full backtest results.
        """
        balance = initial_balance
        self.trades = []
        self.open_positions = []
        self.equity_curve = [balance]

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
                    continue
                elif pos["direction"] == "SELL" and low <= pos["take_profit"]:
                    pos["exit_price"] = pos["take_profit"]
                    pos["exit_reason"] = f"TP{pos.get('tp_level', 1)}"
                    contract = self._get_contract_size(pos.get("symbol", ""))
                    pos["pnl"] = (pos["entry_price"] - pos["exit_price"]) * pos["volume"] * contract
                    closed_this_bar.append(pos)
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

            # Close positions
            for pos in closed_this_bar:
                pos["exit_time"] = current_time
                # Tag session from entry time
                try:
                    pos["session"] = detect_session(current_time)
                except Exception:
                    pos["session"] = "UNKNOWN"
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
                    for tp in tp_levels:
                        position = {
                            "id": str(uuid.uuid4()),
                            "symbol": sig.get("symbol", ""),
                            "direction": sig.get("direction", "BUY"),
                            "entry_price": sig.get("entry_price", current_price),
                            "stop_loss": sig.get("stop_loss", 0),
                            "take_profit": tp.tp_price,
                            "volume": tp.volume,
                            "tp_level": tp.level,
                            "trail_method": tp.trail_method,
                            "entry_time": current_time,
                            "be_applied": False,
                            "mae_pips": 0.0,
                            "mfe_pips": 0.0,
                            "confluence_score": sig.get("confluence_score", 0),
                            "original_sl": sig.get("stop_loss", 0),
                            "highest_price": sig.get("entry_price", current_price),
                            "lowest_price": sig.get("entry_price", current_price),
                        }
                        self.open_positions.append(position)

            self.equity_curve.append(balance)

        # Close any remaining open positions at last price
        last_price = candles.iloc[-1]["close"] if len(candles) > 0 else 0
        for pos in self.open_positions[:]:
            contract = self._get_contract_size(pos.get("symbol", ""))
            if pos["direction"] == "BUY":
                pos["pnl"] = (last_price - pos["entry_price"]) * pos["volume"] * contract
            else:
                pos["pnl"] = (pos["entry_price"] - last_price) * pos["volume"] * contract
            pos["exit_price"] = last_price
            pos["exit_reason"] = "END_OF_DATA"
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
            "trades": self.trades,
            "equity_curve": self.equity_curve,
            "report": report,
        }

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
