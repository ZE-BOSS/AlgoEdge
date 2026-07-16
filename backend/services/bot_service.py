"""
backend/services/bot_service.py

Bot lifecycle management — start, stop, status, activity log.
Runs the SMC strategy engine in a background loop, scanning configured symbols.
Broadcasts all events to the frontend via WebSocket for real-time visibility.
"""

import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from collections import deque

from backend.utils.logger import get_logger
from backend.config import settings
from backend.services.profit_tracker import profit_tracker

logger = get_logger(__name__)

MAX_LOG_ENTRIES = 1000

class BotService:
    """Singleton service managing the trading bot lifecycle."""

    def __init__(self):
        self.running = False
        self.symbols: List[str] = []
        self.last_scan: Optional[str] = None
        self.scan_interval: int = 60  # seconds between scans
        self._task: Optional[asyncio.Task] = None
        self._sync_task: Optional[asyncio.Task] = None
        self._events: deque = deque(maxlen=MAX_LOG_ENTRIES)
        self.user_id: Optional[str] = None
        self.total_signals_today = 0
        self._last_signal_time = {}
        self.engine = None
        self.circuit_breaker = None

    def _log_event(self, message: str, level: str = "INFO", category: str = "BOT"):
        """Log an event to memory, terminal, and WebSocket."""
        event = {
            "time": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "category": category,
            "message": message,
        }
        self._events.appendleft(event)

        # Terminal log with colored category
        if level == "ERROR":
            logger.error(f"[{category}] {message}")
        elif level == "WARN":
            logger.warning(f"[{category}] {message}")
        elif level == "SIGNAL":
            logger.info(f"[{category}] 🎯 {message}")
        else:
            logger.info(f"[{category}] {message}")

        # Broadcast to all connected WebSocket clients
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._broadcast_event(event))
        except RuntimeError:
            pass

    async def _broadcast_event(self, event: dict):
        """Send event to all connected WebSocket clients."""
        try:
            from backend.api.websocket import manager as ws_manager
            await ws_manager.broadcast_all({
                "type": "activity_log",
                "event": event,
            })
        except Exception:
            pass  # WebSocket may not be connected

    async def _broadcast_notification(self, title: str, message: str, notification_type: str = "info"):
        """Broadcast an explicit frontend/browser notification."""
        try:
            from backend.api.websocket import manager as ws_manager
            await ws_manager.broadcast_all({
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

    def log_system_event(self, message: str, level: str = "INFO", category: str = "SYSTEM"):
        """Public method for other modules to log events visible on the frontend."""
        self._log_event(message, level, category)

    async def start(self, user_id: str, symbols: Optional[List[str]] = None,
                    scan_interval: int = 60) -> Dict[str, Any]:
        """Start the bot scanning loop."""
        if self.running:
            return {"running": True, "message": "Bot is already running"}

        self.symbols = symbols or ["XAUUSD", "EURUSD", "GBPUSD"]
        self.scan_interval = scan_interval
        self.running = True
        self.total_signals_today = 0
        self.user_id = user_id

        self._log_event(
            f"Bot started — scanning {', '.join(self.symbols)} every {self.scan_interval}s",
            category="BOT"
        )

        # Start background scanning task
        self._task = asyncio.create_task(self._scan_loop(user_id))
        self._sync_task = asyncio.create_task(self._trade_sync_loop())

        # Server restart recovery: Check DB for OPEN trades and verify with MT5
        try:
            from backend.data.database import async_session
            from backend.data.models import TradePosition
            from sqlalchemy import select
            import MetaTrader5 as mt5
            
            if mt5.terminal_info():
                async with async_session() as session:
                    result = await session.execute(select(TradePosition).where(TradePosition.status == "OPEN"))
                    open_pos = result.scalars().all()
                    for pos in open_pos:
                        mt5_pos = mt5.positions_get(ticket=pos.mt5_ticket)
                        if not mt5_pos:
                            self._log_event(f"Position {pos.mt5_ticket} no longer open in MT5. Trade sync will catch it.", "WARN", "BOT")
        except Exception as e:
            logger.error(f"Recovery error: {e}")

        return {"running": True, "message": "Bot started successfully", "symbols": self.symbols}

    async def stop(self) -> Dict[str, Any]:
        """Stop the bot scanning loop."""
        if not self.running:
            return {"running": False, "message": "Bot is already stopped"}

        self.running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        
        if self._sync_task and not self._sync_task.done():
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
        self._sync_task = None

        self._log_event("Bot stopped by user", category="BOT")
        return {"running": False, "message": "Bot stopped"}

    def get_status(self) -> Dict[str, Any]:
        """Get current bot status."""
        return {
            "running": self.running,
            "symbols": self.symbols,
            "last_scan": self.last_scan,
            "total_signals_today": self.total_signals_today,
            "scan_interval": self.scan_interval,
        }

    def get_logs(self, limit: int = 50) -> Dict[str, Any]:
        """Get recent activity log entries."""
        events = list(self._events)[:limit]
        return {"events": events, "total": len(self._events)}

    async def _scan_loop(self, user_id: str):
        """Main scanning loop — runs the SMC strategy on each symbol."""
        from backend.mt5.data_fetcher import DataFetcher

        self._log_event("Scan loop started — entering main cycle", category="BOT")

        while self.running:
            try:
                from backend.data.database import async_session
                from backend.data.models import UserConfigModel
                from sqlalchemy import select
                from backend.core.config_schema import UserConfigV2, UserConfig
                import json
                
                async with async_session() as session:
                    result = await session.execute(select(UserConfigModel).where(UserConfigModel.user_id == user_id))
                    config_db = result.scalar_one_or_none()
                    if config_db and getattr(config_db, 'config_json', None):
                        try:
                            config_dict = json.loads(config_db.config_json)
                            config = UserConfigV2.from_dict(config_dict)
                        except Exception:
                            config = UserConfig()
                    elif config_db and getattr(config_db, 'config', None):
                        config = UserConfig.parse_obj(config_db.config)
                    else:
                        config = UserConfig()

                from backend.risk.circuit_breaker import CircuitBreaker
                from backend.strategies.registry import get_strategy

                if not self.circuit_breaker:
                    self.circuit_breaker = CircuitBreaker(config.risk.to_dict() if hasattr(config.risk, 'to_dict') else config.risk.__dict__)

                # Maintain a dictionary of engines per symbol
                if not hasattr(self, 'engines'):
                    self.engines = {}

                for symbol in self.symbols:
                    if not self.running:
                        break

                    # Yield control to event loop to allow WebSocket pings and API requests
                    await asyncio.sleep(0.1)

                    self._log_event(f"Scanning {symbol}...", category="SCAN")

                    try:
                        from backend.risk.engine import RiskEngine
                        from backend.mt5.order_manager import OrderManager
                        import pandas as pd
                        
                        def _index_candles(df):
                            if 'time' in df.columns:
                                return df.set_index(pd.to_datetime(df['time'], unit='s'))
                            return df

                        # ── DYNAMIC STRATEGY RESOLUTION ──
                        strategy_id = "SMC_v1"
                        if hasattr(config, 'instrument_settings') and config.instrument_settings:
                            for settings in config.instrument_settings:
                                if settings.symbol == symbol:
                                    strategy_id = getattr(settings, 'strategy_id', "SMC_v1")
                                    break
                        
                        # Instantiate engine if not exists
                        if symbol not in self.engines or getattr(self.engines[symbol], 'strategy_id', None) != strategy_id:
                            engine_class = get_strategy(strategy_id)
                            self.engines[symbol] = engine_class(config)
                            self.engines[symbol].strategy_id = strategy_id
                            self._log_event(f"[{symbol}] Instantiated {strategy_id} Engine", "INFO", "BOT")
                            
                        current_engine = self.engines[symbol]
                        
                        # Get required timeframes for the strategy
                        req_tfs = current_engine.get_required_timeframes() if hasattr(current_engine, 'get_required_timeframes') else ["H4", "M15", "M5"]
                        
                        fetched_data = {}
                        has_missing_data = False
                        for tf in req_tfs:
                            tf_data = await DataFetcher.get_historical_data(symbol, tf, count=5000)
                            await asyncio.sleep(0.05)
                            if tf_data is None or tf_data.empty:
                                has_missing_data = True
                                break
                            fetched_data[tf] = tf_data
                            
                        if has_missing_data:
                            self._log_event(f"Incomplete MTF data for {symbol} on required timeframes {req_tfs}", "WARN", "SCAN")
                            continue
                            
                        self._log_event(f"Fetched MTF data for {symbol} ({'/'.join(req_tfs)})", category="DATA")

                        # Try to run strategy engine
                        try:
                            # ── ENFORCE CLOSED CANDLE LOGIC (Fix Repainting) ──
                            signal = None
                            for i, tf in enumerate(req_tfs):
                                tf_data = fetched_data[tf]
                                closed_data = tf_data.iloc[:-1] if len(tf_data) > 1 else tf_data
                                
                                await asyncio.sleep(0.01)
                                res = await current_engine.on_bar(symbol, tf, _index_candles(closed_data))
                                # Only the last timeframe might return a signal in hierarchical strategies
                                if i == len(req_tfs) - 1:
                                    signal = res
                                    
                            await asyncio.sleep(0.01)
                            
                            if signal:
                                # Cooldown check
                                sig_time = getattr(signal, 'timestamp', None)
                                if not sig_time and hasattr(signal, 'chart_data') and signal.chart_data:
                                    sig_time = signal.chart_data[-1].get('time')
                                if sig_time and sig_time == self._last_signal_time.get(symbol):
                                    continue
                                if sig_time:
                                    self._last_signal_time[symbol] = sig_time
                                    
                                try:
                                    last_tf = req_tfs[-1]
                                    cd_df = fetched_data[last_tf].tail(100).copy()
                                    if "time" not in cd_df.columns and cd_df.index.name == "time":
                                        cd_df = cd_df.reset_index()
                                    # DataFetcher already returns epoch seconds for time, so no conversion is needed
                                    signal.chart_data = cd_df.to_dict(orient="records")
                                except Exception as e:
                                    logger.error(f"Failed to inject chart_data: {e}")

                                self.total_signals_today += 1

                                passed_gates = getattr(signal, "metadata", {}).get("passed_gates", True)
                                if not passed_gates:
                                    reasons = getattr(signal, "metadata", {}).get("rejection_reasons", [])
                                    for reason in reasons:
                                        self._log_event(f"[REJECTED] {reason}", "SIGNAL", "SIGNAL")
                                    continue

                                self._log_event(
                                    f"Signal: {signal.direction} {symbol} @ {signal.entry_price} "
                                    f"| SL: {signal.stop_loss} | Score: {signal.confluence_score}",
                                    "SIGNAL", "SIGNAL"
                                )

                                # === Execute trade via RiskEngine ===
                                try:
                                    from backend.mt5.bridge import bridge
                                    if not bridge.account_info:
                                        self._log_event("Trade rejected: MT5 bridge offline or account info unavailable.", "ERROR", "RISK")
                                        continue
                                    account_balance = bridge.account_info.balance

                                    risk_config = {
                                        "risk_per_trade_pct": config.risk.risk_per_trade_pct,
                                        "min_rr": config.risk.min_rr,
                                        "tp1_rr": config.risk.tp1_rr,
                                        "tp2_rr": config.risk.tp2_rr,
                                        "tp3_rr": config.risk.tp3_rr,
                                        "tp4_rr": config.risk.tp4_rr if hasattr(config.risk, 'tp4_rr') else 0,
                                        "tp5_rr": config.risk.tp5_rr if hasattr(config.risk, 'tp5_rr') else 0,
                                        "tp_count": config.risk.tp_count if hasattr(config.risk, 'tp_count') else 3,
                                        "tp_splits": config.risk.tp_splits if hasattr(config.risk, 'tp_splits') else [40, 35, 25],
                                        "multi_position_mode": True,
                                        "max_daily_consecutive_losses": config.risk.max_daily_consecutive_losses,
                                        "max_weekly_consecutive_losses": config.risk.max_weekly_consecutive_losses if hasattr(config.risk, 'max_weekly_consecutive_losses') else 5,
                                        "max_consecutive_losses": config.risk.max_consecutive_losses if hasattr(config.risk, 'max_consecutive_losses') else 5,
                                        "max_daily_trades": config.risk.max_daily_trades if hasattr(config.risk, 'max_daily_trades') else 5,
                                        "max_concurrent_positions": config.risk.max_concurrent_positions,
                                        "target_profit_enabled": config.risk.target_profit_enabled if hasattr(config.risk, 'target_profit_enabled') else False,
                                        "max_daily_profit": config.risk.max_daily_profit if hasattr(config.risk, 'max_daily_profit') else 500.0,
                                        "max_weekly_profit": config.risk.max_weekly_profit if hasattr(config.risk, 'max_weekly_profit') else 2000.0,
                                        "compounding_enabled": config.risk.compounding_enabled if hasattr(config.risk, 'compounding_enabled') else False,
                                        "be_trigger_rr": config.risk.be_trigger_rr,
                                        "be_buffer_pips": config.risk.be_buffer_pips,
                                        "trail_method_tp2": config.risk.trail_method_tp2 if hasattr(config.risk, 'trail_method_tp2') else "ATR_TRAIL",
                                        "trail_method_tp3": config.risk.trail_method_tp3 if hasattr(config.risk, 'trail_method_tp3') else "STRUCTURE_TRAIL",
                                        "trail_method_tp4": config.risk.trail_method_tp4 if hasattr(config.risk, 'trail_method_tp4') else "ATR_TRAIL",
                                        "trail_method_tp5": config.risk.trail_method_tp5 if hasattr(config.risk, 'trail_method_tp5') else "ATR_TRAIL",
                                    }
                                    risk_engine = RiskEngine(risk_config)
                                    risk_engine.circuit = self.circuit_breaker

                                    import uuid
                                    group_id = str(uuid.uuid4())[:8]

                                    signal_data = {
                                        "group_id": group_id,
                                        "symbol": signal.symbol,
                                        "direction": signal.direction,
                                        "entry_price": signal.entry_price,
                                        "stop_loss": signal.stop_loss,
                                        "take_profit": signal.take_profit,
                                        "chart_data": signal.chart_data,
                                    }

                                    compounding_risk = config.get_risk_amount(account_balance) if hasattr(config, 'get_risk_amount') else account_balance * (config.risk.risk_per_trade_pct / 100)

                                    approved, reason, tp_levels = risk_engine.evaluate_signal(
                                        signal_data, account_balance, compounding_risk_dollars=compounding_risk
                                    )

                                    if approved:
                                        # Place ALL TP positions at entry (no deferred stacking)
                                        self._log_event(
                                            f"Trade approved: {len(tp_levels)} positions — all at entry",
                                            "INFO", "TRADE"
                                        )

                                        # Place orders via OrderManager
                                        db_positions = []
                                        for tp in tp_levels:
                                            try:
                                                result = await OrderManager.place_market_order(
                                                    symbol=signal.symbol,
                                                    direction=signal.direction,
                                                    volume=tp.volume,
                                                    sl=signal.stop_loss,
                                                    tp=tp.tp_price,
                                                    magic=1001 + (tp.level * 10),
                                                    comment=f"AE_TP{tp.level}",
                                                )
                                                if result.get("success"):
                                                    ticket = result.get("ticket")
                                                    db_positions.append({
                                                        "tp_level": tp.level,
                                                        "volume": tp.volume,
                                                        "tp_price": tp.tp_price,
                                                        "ticket": ticket
                                                    })
                                                    self._log_event(
                                                        f"Order placed: TP{tp.level} | "
                                                        f"{tp.volume} lots @ {signal.entry_price} "
                                                        f"→ TP: {tp.tp_price:.5f}",
                                                        "INFO", "TRADE"
                                                    )
                                                    asyncio.ensure_future(self._broadcast_notification(
                                                        "Trade Entered",
                                                        f"{signal.direction} {signal.symbol} @ {signal.entry_price}",
                                                        "success"
                                                    ))
                                                else:
                                                    self._log_event(
                                                        f"Order failed: TP{tp.level} — {result.get('error', 'unknown')}",
                                                        "ERROR", "TRADE"
                                                    )
                                            except Exception as order_err:
                                                self._log_event(
                                                    f"Order placement error: {str(order_err)[:100]}",
                                                    "ERROR", "TRADE"
                                                )
                                    
                                        if db_positions and self.user_id:
                                            try:
                                                import json
                                                from backend.data.database import async_session
                                                from backend.data.models import Trade, TradePosition
                                                
                                                async with async_session() as session:
                                                    trade = Trade(
                                                        user_id=self.user_id,
                                                        symbol=signal.symbol,
                                                        direction=signal.direction,
                                                        entry_price=signal.entry_price,
                                                        stop_loss=signal.stop_loss,
                                                        volume=sum(p["volume"] for p in db_positions),
                                                        status="OPEN",
                                                        chart_data=json.dumps(signal.chart_data) if signal.chart_data else None,
                                                    )
                                                    session.add(trade)
                                                    await session.flush()
                                                    
                                                    for p in db_positions:
                                                        pos = TradePosition(
                                                            parent_trade_id=trade.id,
                                                            user_id=self.user_id,
                                                            tp_level=p["tp_level"],
                                                            mt5_ticket=p["ticket"],
                                                            volume=p["volume"],
                                                            entry_price=signal.entry_price,
                                                            stop_loss=signal.stop_loss,
                                                            take_profit=p["tp_price"],
                                                            status="OPEN"
                                                        )
                                                        session.add(pos)
                                                    await session.commit()
                                            except Exception as db_err:
                                                logger.error(f"Failed to save live trade to DB: {db_err}")
                                                
                                    else:
                                        self._log_event(
                                            f"Trade rejected by risk engine: {reason}",
                                            "WARN", "RISK"
                                        )
                                        # Only trigger explicit popup for risk-based rejections, not basic RR rejections to avoid spam
                                        if "Broker minimum lot forces risk" in reason or "Proposed risk" in reason:
                                            asyncio.ensure_future(self._broadcast_notification(
                                                "Trade Rejected (Risk Limit)",
                                                reason,
                                                "error"
                                            ))

                                except Exception as exec_err:
                                    self._log_event(
                                        f"Execution error: {str(exec_err)[:150]}",
                                        "ERROR", "TRADE"
                                    )

                            else:
                                self._log_event(f"No setup found for {symbol}", category="SCAN")
                        except Exception as e:
                            self._log_event(f"Strategy error on {symbol}: {str(e)[:150]}", "ERROR", "STRATEGY")

                    except Exception as e:
                        self._log_event(f"Data fetch error for {symbol}: {str(e)[:150]}", "ERROR", "DATA")

                self.last_scan = datetime.now(timezone.utc).isoformat()
                self._log_event(
                    f"Scan cycle complete — {len(self.symbols)} symbols checked — "
                    f"next scan in {self.scan_interval}s",
                    category="BOT"
                )

                # Wait for next cycle
                await asyncio.sleep(self.scan_interval)

            except asyncio.CancelledError:
                self._log_event("Scan loop cancelled", category="BOT")
                break
            except Exception as e:
                import traceback
                error_trace = traceback.format_exc()
                logger.error(f"Scan loop error: {e}\n{error_trace}")
                self._log_event(f"Scan loop error: {str(e)[:200]}", "ERROR", "BOT")
                await asyncio.sleep(10)

        self._log_event("Scan loop exited", category="BOT")

    async def _trade_sync_loop(self):
        """Monitors closed MT5 positions and updates ProfitTracker."""
        from backend.mt5.order_manager import OrderManager
        
        last_check_time = datetime.now(timezone.utc).timestamp() - 86400 * 3 # lookback 3 days initially to catch missed closures
        
        while self.running:
            try:
                await asyncio.sleep(15) # Poll every 15s
                
                deals = await OrderManager.get_closed_positions_since(last_check_time)
                if deals:
                    # Sort by time so we process chronologically
                    deals.sort(key=lambda x: x["time"])
                    
                    for deal in deals:
                        if deal["time"] > last_check_time:
                            net_profit = deal["profit"] + deal["commission"] + deal["swap"]
                            await profit_tracker.add_profit(net_profit)
                            
                            self._log_event(
                                f"Trade closed: {deal['symbol']} | P&L: ${net_profit:.2f}",
                                "INFO", "TRADE"
                            )
                            # Push notification for closed trade
                            asyncio.ensure_future(self._broadcast_notification(
                                "Trade Closed",
                                f"{deal['symbol']} closed for ${net_profit:.2f}",
                                "success" if net_profit >= 0 else "error"
                            ))
                            
                            # Also update database
                            try:
                                from backend.data.database import async_session
                                from backend.data.models import Trade, TradePosition
                                from sqlalchemy import select
                                import json
                                
                                async with async_session() as session:
                                    pos_id = deal.get("position_id")
                                    if pos_id:
                                        result = await session.execute(
                                            select(TradePosition).where(TradePosition.mt5_ticket == pos_id)
                                        )
                                        pos = result.scalar_one_or_none()
                                        if pos and pos.status == "OPEN":
                                            pos.status = "CLOSED"
                                            pos.pnl = net_profit
                                            pos.exit_price = deal.get("price", 0.0)
                                            pos.exit_time = datetime.fromtimestamp(deal["time"], timezone.utc)
                                            
                                            result2 = await session.execute(
                                                select(TradePosition).where(TradePosition.parent_trade_id == pos.parent_trade_id)
                                            )
                                            siblings = result2.scalars().all()
                                            if all(s.status == "CLOSED" for s in siblings):
                                                trade = await session.get(Trade, pos.parent_trade_id)
                                                if trade:
                                                    trade.status = "CLOSED"
                                                    trade.exit_time = pos.exit_time
                                                    trade.pnl = sum(s.pnl for s in siblings if getattr(s, 'pnl', None) is not None)
                                                    
                                                    # UPDATE CHART DATA (9.1 Fill Trade.chart_data on live trade close)
                                                    try:
                                                        from backend.mt5.data_fetcher import DataFetcher
                                                        import pandas as pd
                                                        candles = await DataFetcher.get_historical_data(trade.symbol, "M5", 100)
                                                        if not candles.empty:
                                                            cd_df = candles.copy()
                                                            if "time" not in cd_df.columns and cd_df.index.name == "time":
                                                                cd_df = cd_df.reset_index()
                                                            # DataFetcher already returns epoch seconds for time, so no conversion is needed
                                                            trade.chart_data = json.dumps(cd_df.to_dict(orient="records"))
                                                    except Exception as chart_err:
                                                        logger.warning(f"Failed to fetch exit chart data: {chart_err}")
                                            
                                            await session.commit()
                            except Exception as db_err:
                                logger.error(f"Failed to update trade in DB: {db_err}")
                            
                            last_check_time = max(last_check_time, deal["time"])
                            
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Trade sync loop error: {e}")
                await asyncio.sleep(5)


# Singleton instance
bot_service = BotService()
