"""
backend/backtester/portfolio_engine.py

Unified Portfolio Backtesting Engine.
Simulates multiple symbols chronologically on a single global timeline.
"""
import uuid
import time
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from backend.analytics.reports import generate_risk_report
from backend.risk.engine import RiskEngine
from backend.risk.multi_tp import TPLevel, _is_buy
from backend.risk.position_sizer import get_pip_size
from backend.risk.prop_firm_validator import PropFirmValidator
from backend.utils.logger import get_logger
from backend.utils.trade_grouper import group_trades
from backend.utils.timeutils import detect_session
from backend.backtester.engine import _epoch_to_iso, _calc_duration_minutes, _validate_position
from backend.risk.compounding import get_instrument_profile

logger = get_logger(__name__)

class PortfolioBacktestEngine:
    def __init__(self, risk_config: dict[str, Any]):
        self.risk_engine = RiskEngine(risk_config)
        self.risk_config = risk_config
        prop_firm_config = risk_config.get("prop_firm", {})
        self.prop_firm_validator = PropFirmValidator(prop_firm_config)
        self.risk_engine.prop_firm_validator = self.prop_firm_validator
        
        self.trades = []
        self.open_positions = []
        self.equity_curve = []
        self.invalid_signals = 0
        self.rejection_funnel = {
            "total_evaluated": 0,
            "strategy_rejections": {},
            "risk_rejections": {},
            "errors": 0,
            "approved": 0
        }
        self.run_logs = []

    def _calc_pnl(self, direction: str, entry: float, exit_price: float, volume: float, symbol: str) -> float:
        profile = get_instrument_profile(symbol)
        if profile:
            price_diff = exit_price - entry
            points = price_diff / profile.point_size if profile.point_size else 0
            raw_pnl = points * profile.point_value_per_lot * volume
        else:
            price_diff = exit_price - entry
            tick_value = 1.0
            tick_size = 0.00001
            try:
                import MetaTrader5 as mt5
                if mt5 and mt5.terminal_info() and mt5.terminal_info().connected:
                    info = mt5.symbol_info(symbol)
                    if info:
                        tick_value = info.trade_tick_value or 1.0
                        tick_size = info.trade_tick_size or 0.00001
            except Exception:
                pass
            value_per_unit_move = tick_value / tick_size
            raw_pnl = price_diff * value_per_unit_move * volume

        return raw_pnl if direction == "BUY" else -raw_pnl

    def run(
        self,
        portfolio_data: dict[str, pd.DataFrame],
        portfolio_signals: dict[str, list[dict[str, Any]]],
        initial_balance: float = 10000.0,
        compounding_enabled: bool = False,
        portfolio_data_m15: dict[str, pd.DataFrame] = None,
        portfolio_data_m5: dict[str, pd.DataFrame] = None,
    ) -> dict[str, Any]:
        
        balance = initial_balance
        self.trades = []
        self.open_positions = []
        self.equity_curve = [balance]
        
        self.prop_firm_validator.is_breached = False
        self.prop_firm_validator.breach_reason = ""
        self.prop_firm_validator._breach_logged = False
        self.prop_firm_validator._alerts_sent = set()
        
        logger.info("[PORTFOLIO] ═══ Starting global portfolio engine ═══")
        
        all_timestamps = set()
        for sym, df in portfolio_data.items():
            if 'time' in df.columns:
                all_timestamps.update(df['time'].values)
            else:
                all_timestamps.update(df.index.values)
                
        global_timeline = sorted(list(all_timestamps))
        
        symbol_cache = {}
        for sym, df in portfolio_data.items():
            if 'time' not in df.columns:
                df['time'] = df.index
            
            bar_dict = df.set_index('time').to_dict('index')
            
            highs_arr = df["high"].values.astype(float)
            lows_arr = df["low"].values.astype(float)
            closes_arr = df["close"].values.astype(float)
            atr_period = 14
            
            prev_closes = np.roll(closes_arr, 1)
            prev_closes[0] = closes_arr[0]
            tr_all = np.maximum(
                highs_arr - lows_arr,
                np.maximum(np.abs(highs_arr - prev_closes), np.abs(lows_arr - prev_closes))
            )
            atr_array = np.zeros(len(df))
            for i in range(atr_period, len(df)):
                atr_array[i] = np.mean(tr_all[i - atr_period:i])
                
            time_vals = df['time'].values
            atr_dict = dict(zip(time_vals, atr_array))
            
            sw_len = self.risk_config.get("trail_structure_bars", self.risk_config.get("swing_length", 5))
            swing_lookback = 20
            swing_dict = {}
            for i in range(swing_lookback, len(df)):
                points = []
                for j in range(max(sw_len, i - swing_lookback), i - sw_len):
                    if j - sw_len < 0: continue
                    window_h = highs_arr[j - sw_len:j + sw_len + 1]
                    window_l = lows_arr[j - sw_len:j + sw_len + 1]
                    if highs_arr[j] == window_h.max():
                        points.append({"type": "HIGH", "price": float(highs_arr[j])})
                    if lows_arr[j] == window_l.min():
                        points.append({"type": "LOW", "price": float(lows_arr[j])})
                if points:
                    swing_dict[time_vals[i]] = points
                    
            symbol_cache[sym] = {
                "bars": bar_dict,
                "atr": atr_dict,
                "swings": swing_dict
            }
            
        all_signals = []
        for sym, sigs in portfolio_signals.items():
            for sig in sigs:
                sig["symbol"] = sym  # Ensure symbol is attached
                all_signals.append(sig)
        
        all_signals.sort(key=lambda x: float(x.get("time", float("inf"))))
        signal_idx = 0
        
        for current_time in global_timeline:
            current_timestamp = float(current_time)
            
            # 1. Update floating equity and check limits
            open_pnl = 0.0
            for pos in self.open_positions:
                sym = pos.get("symbol")
                if sym in symbol_cache and current_time in symbol_cache[sym]["bars"]:
                    pos["_last_known_close"] = symbol_cache[sym]["bars"][current_time]["close"]
                last_close = pos.get("_last_known_close", pos["entry_price"])
                open_pnl += self._calc_pnl(pos["direction"], pos["entry_price"], last_close, pos["volume"], sym)
            
            current_time_dt = datetime.fromtimestamp(current_timestamp, timezone.utc)
            self.prop_firm_validator.update_equity_balance(balance + open_pnl, balance, current_time_dt)
            if self.prop_firm_validator.is_breached and not self.prop_firm_validator._breach_logged:
                logger.warning(f"[PROP FIRM MONITOR] Breach detected: {self.prop_firm_validator.breach_reason} — continuing")
                self.prop_firm_validator._breach_logged = True
                
            # 2. Process open positions
            closed_this_bar = []
            tp1_hit_groups = set()
            
            for pos in self.open_positions[:]:
                sym = pos.get("symbol")
                if sym not in symbol_cache or current_time not in symbol_cache[sym]["bars"]:
                    continue # No tick for this symbol at this time
                
                bar = symbol_cache[sym]["bars"][current_time]
                current_price = bar["close"]
                high = bar["high"]
                low = bar["low"]
                
                if pos["direction"] == "BUY":
                    pos["highest_price"] = max(pos.get("highest_price", pos["entry_price"]), high)
                else:
                    pos["lowest_price"] = min(pos.get("lowest_price", pos["entry_price"]), low)

                pos["bars_held"] = pos.get("bars_held", 0) + 1
                symbol_upper = sym.upper()
                is_crashboom = "CRASH" in symbol_upper or "BOOM" in symbol_upper
                if is_crashboom and pos["bars_held"] >= 400:
                    pos["exit_price"] = current_price
                    pos["exit_reason"] = "TIME_LIMIT"
                    pos["pnl"] = self._calc_pnl(pos["direction"], pos["entry_price"], pos["exit_price"], pos["volume"], sym)
                    closed_this_bar.append(pos)
                    continue

                sl_hit = False
                tp_hit = False
                if pos["direction"] == "BUY":
                    sl_hit = low <= pos["stop_loss"]
                    tp_hit = high >= pos["take_profit"]
                else:
                    sl_hit = high >= pos["stop_loss"]
                    tp_hit = low <= pos["take_profit"]
                    
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
                    pos["pnl"] = self._calc_pnl(pos["direction"], pos["entry_price"], pos["exit_price"], pos["volume"], sym)
                    closed_this_bar.append(pos)
                    continue
                elif tp_hit:
                    pos["exit_price"] = pos["take_profit"]
                    pos["exit_reason"] = f"TP{pos.get('tp_level', 1)}"
                    pos["pnl"] = self._calc_pnl(pos["direction"], pos["entry_price"], pos["exit_price"], pos["volume"], sym)
                    closed_this_bar.append(pos)
                    if pos.get("tp_level") == 1:
                        tp1_hit_groups.add(pos.get("group_id"))
                    continue

                if pos["direction"] == "BUY":
                    adverse = pos["entry_price"] - low
                    favorable = high - pos["entry_price"]
                else:
                    adverse = high - pos["entry_price"]
                    favorable = pos["entry_price"] - low

                pip_size = get_pip_size(sym)
                pos["mae_pips"] = max(pos.get("mae_pips", 0), adverse / pip_size if pip_size else 0)
                pos["mfe_pips"] = max(pos.get("mfe_pips", 0), favorable / pip_size if pip_size else 0)

                current_atr = symbol_cache[sym]["atr"].get(current_time, 0.0)
                swing_points = symbol_cache[sym]["swings"].get(current_time, [])

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

            positions_to_remove = []
            for pos in closed_this_bar:
                pos["exit_time"] = current_time
                try:
                    pos["session"] = detect_session(current_time)
                except Exception:
                    pos["session"] = "UNKNOWN"
                pos["duration_minutes"] = _calc_duration_minutes(pos.get("entry_time"), pos.get("exit_time"))
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
                self.risk_engine.on_position_closed(pos.get("group_id", "unknown"), pos.get("pnl", 0), current_time)
                self.trades.append(pos)
                positions_to_remove.append(pos)
                
                self.run_logs.append({
                    "time": _epoch_to_iso(current_time),
                    "level": "INFO",
                    "category": "BACKTEST_LOG",
                    "message": f"Closed {pos['direction']} {sym} {pos.get('exit_reason')} | PnL: ${pos.get('pnl', 0):.2f}"
                })
                
            for p in positions_to_remove:
                if p in self.open_positions:
                    self.open_positions.remove(p)

            # 3. Process new signals
            while signal_idx < len(all_signals):
                sig = all_signals[signal_idx]
                sig_time = float(sig.get("time", float("inf")))
                if sig_time >= current_timestamp:
                    break
                signal_idx += 1

                symbol = sig.get("symbol")
                sig_is_buy = _is_buy(sig.get("direction", "BUY"))
                
                already_open = False
                for p in self.open_positions:
                    p_is_buy = _is_buy(p.get("direction", "BUY"))
                    if p.get("symbol") == symbol and p_is_buy == sig_is_buy:
                        already_open = True
                        break
                        
                if already_open:
                    continue

                group_id = str(uuid.uuid4())[:8]
                sig["group_id"] = group_id
                
                self.rejection_funnel["total_evaluated"] += 1
                
                passed_gates = sig.get("metadata", {}).get("passed_gates", True)
                if not passed_gates:
                    reasons = sig.get("metadata", {}).get("rejection_reasons", [])
                    for r in reasons:
                        gate = r.split(":")[0] if ":" in r else "Unknown Strategy Rule"
                        self.rejection_funnel["strategy_rejections"][gate] = self.rejection_funnel["strategy_rejections"].get(gate, 0) + 1
                    continue

                approved, reason, tp_levels = False, "Error", []
                try:
                    approved, reason, tp_levels = self.risk_engine.evaluate_signal(
                        signal_data=sig,
                        account_balance=balance,
                        current_time=current_time_dt,
                        initial_balance=initial_balance
                    )
                except Exception as e:
                    self.invalid_signals += 1
                    self.rejection_funnel["errors"] += 1
                    continue

                if not approved:
                    self.invalid_signals += 1
                    self.rejection_funnel["risk_rejections"][reason] = self.rejection_funnel["risk_rejections"].get(reason, 0) + 1
                    continue

                is_valid, val_reason = _validate_position(
                    sig["direction"], sig["entry_price"], sig["stop_loss"], tp_levels[-1].tp_price
                )
                if not is_valid:
                    self.invalid_signals += 1
                    self.rejection_funnel["risk_rejections"][val_reason] = self.rejection_funnel["risk_rejections"].get(val_reason, 0) + 1
                    continue

                self.rejection_funnel["approved"] += 1
                self.run_logs.append({
                    "time": _epoch_to_iso(sig_time),
                    "level": "INFO",
                    "category": "BACKTEST_LOG",
                    "message": f"Opened {sig['direction']} {symbol} @ {sig['entry_price']:.5f} | {len(tp_levels)} TPs"
                })

                # Detect entry session (used for session win-rate breakdown and
                # displayed directly in the trade-expand panel)
                try:
                    entry_session = detect_session(sig_time)
                except Exception:
                    entry_session = "UNKNOWN"

                strategy_id = sig.get("strategy_name", sig.get("strategy_id", "UNKNOWN"))

                # Mirror engine.py's confirmations list so the frontend's
                # "Entry Confirmations" panel has something to render.
                signal_confirmations = sig.get("confirmations", [])
                entry_confirmations = [
                    f"Direction: {sig.get('direction', 'UNKNOWN')}",
                    f"Symbol: {symbol}",
                    f"Strategy: {strategy_id}",
                    f"Entry Price: {sig['entry_price']:.5f}",
                    f"Stop Loss: {sig['stop_loss']:.5f}",
                    f"Take Profit (TP{lvl.level}): {lvl.tp_price:.5f}",
                    f"RR Multiplier: 1:{lvl.rr_multiplier:.1f}",
                    f"Volume: {lvl.volume:.2f} lots ({lvl.volume_pct * 100:.0f}%)",
                    f"Entry Session: {entry_session}",
                    "Entry Mode: ALL AT ENTRY",
                ]
                if signal_confirmations:
                    entry_confirmations.append("── SMC Analysis ──")
                    entry_confirmations.extend(signal_confirmations)

                for lvl in tp_levels:
                    new_pos = {
                        "id": str(uuid.uuid4()),
                        "group_id": group_id,
                        "symbol": symbol,
                        "strategy_id": strategy_id,
                        "strategy": strategy_id,  # kept as an alias for backward compatibility
                        "direction": sig["direction"],
                        "entry_time": sig_time,
                        "entry_time_iso": _epoch_to_iso(sig_time),
                        "entry_session": entry_session,
                        "entry_price": sig["entry_price"],
                        "stop_loss": sig["stop_loss"],
                        "take_profit": lvl.tp_price,
                        "volume": lvl.volume,
                        "tp_level": lvl.level,
                        "be_applied": False,
                        "trail_applied": False,
                        "trail_method": self.risk_config.get("trailing_method", "NONE"),
                        "metadata": sig.get("metadata", {}),
                        "mae_pips": 0.0,
                        "mfe_pips": 0.0,
                        # ── Previously missing fields — see fix notes ──
                        "confluence_score": sig.get("confluence_score", 0),
                        "balance_before": balance,
                        "status": "OPEN",
                        "entry_confirmations": entry_confirmations,
                        "entry_snapshot_b64": sig.get("metadata", {}).get("entry_snapshot_b64", ""),
                        "original_signal": sig,
                        "_last_known_close": sig["entry_price"]
                    }
                    self.open_positions.append(new_pos)

        # 4. Force close remaining positions at end of backtest
        if self.open_positions:
            for pos in self.open_positions:
                sym = pos.get("symbol")
                if sym in symbol_cache:
                    last_time = max(symbol_cache[sym]["bars"].keys())
                    bar = symbol_cache[sym]["bars"][last_time]
                    pos["exit_price"] = bar["close"]
                    pos["exit_time"] = last_time
                else:
                    pos["exit_price"] = pos["entry_price"]
                    pos["exit_time"] = global_timeline[-1]

                pos["exit_reason"] = "END_OF_BACKTEST"
                pos["pnl"] = self._calc_pnl(pos["direction"], pos["entry_price"], pos["exit_price"], pos["volume"], pos.get("symbol", ""))
                pos["duration_minutes"] = _calc_duration_minutes(pos.get("entry_time"), pos.get("exit_time"))
                pos["entry_time_iso"] = _epoch_to_iso(pos.get("entry_time"))
                pos["exit_time_iso"] = _epoch_to_iso(pos.get("exit_time"))
                
                balance += pos.get("pnl", 0)
                pos["balance_after"] = balance
                self.trades.append(pos)
                
            self.open_positions.clear()

        # Generate metrics
        from backend.analytics.reports import generate_risk_report
        import uuid as _uuid
        
        # Group trades using the shared utility. Pass the per-symbol candle
        # dicts through so trade_grouper can extract chart_data / SMC zones
        # per group (previously this was called with no candle data at all,
        # so chart_data/chart_data_m15/chart_data_m5 were always empty for
        # every portfolio backtest — see trade_grouper.py's symbol-aware
        # candle resolution for how the dict form is handled).
        grouped_trades = group_trades(self.trades, portfolio_data, portfolio_data_m15, portfolio_data_m5)
        
        # Pass the true initial_balance explicitly — do NOT rely on inference
        # from trades[0].balance_before (fragile, and was the root cause of
        # every report metric — Sharpe/Sortino/drawdown/equity curve — being
        # computed off a $0 baseline before balance_before was fixed above).
        report = generate_risk_report(grouped_trades, initial_balance=initial_balance)
        # Attach rejection funnel directly to the RiskReport object (same pattern as engine.py)
        report.rejection_funnel = self.rejection_funnel

        total_pnl = balance - initial_balance

        results = {
            "backtest_id": str(_uuid.uuid4()),
            "initial_balance": initial_balance,
            "final_balance": balance,
            "total_pnl": total_pnl,
            "total_trades": len(self.trades),
            "invalid_signals": self.invalid_signals,
            "trades": self.trades,
            "grouped_trades": grouped_trades,
            "equity_curve": self.equity_curve,
            "report": report,
            "rejection_funnel": self.rejection_funnel,
            "run_logs": self.run_logs,
        }

        logger.info(f"[PORTFOLIO] ═══ Global backtest complete ═══")
        logger.info(f"[PORTFOLIO] Final Balance: ${balance:.2f} | Trades: {len(self.trades)}")
        
        return results