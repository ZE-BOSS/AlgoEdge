"""
backend/services/bot_service.py

Bot lifecycle management — start, stop, status, activity log.
Runs the SMC strategy engine in a background loop, scanning configured symbols.
Broadcasts all events to the frontend via WebSocket for real-time visibility.
"""

import asyncio
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.services.profit_tracker import profit_tracker
from backend.utils.logger import get_logger

logger = get_logger(__name__)

MAX_LOG_ENTRIES = 1000

class BotService:
    """Singleton service managing the trading bot lifecycle."""

    def __init__(self):
        self.running = False
        self.symbols: list[str] = []
        self.last_scan: str | None = None
        self.scan_interval: int = 60  # seconds between scans
        self._task: asyncio.Task | None = None
        self._sync_task: asyncio.Task | None = None
        self._events: deque = deque(maxlen=MAX_LOG_ENTRIES)
        self.user_id: str | None = None
        self.total_signals_today = 0
        self._last_signal_time = {}
        self.engine = None
        self.circuit_breaker = None
        self.prop_firm_validator = None

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

    async def _save_signal_state(self, signal_domain, status: str, reject_reason: str = "", tp_levels: list = None):
        """Saves or updates a signal in the database and dispatches Telegram alert instantly."""
        # 1. Dispatch Telegram Alert Immediately (Decoupled from DB success)
        try:
            from backend.services.telegram import telegram_service
            
            # Use escape_markdown to prevent MarkdownV1 parse crashes
            sym = telegram_service.escape_markdown(signal_domain.symbol)
            direction = telegram_service.escape_markdown(signal_domain.direction)
            price = telegram_service.escape_markdown(str(getattr(signal_domain, 'entry_price', '')))
            reason = telegram_service.escape_markdown(reject_reason) if reject_reason else ""
            stat = telegram_service.escape_markdown(status)
            
            # Extract additional details if available
            strategy_id = telegram_service.escape_markdown(str(getattr(signal_domain, 'strategy_id', 'UNKNOWN')))
            timeframe = telegram_service.escape_markdown(str(getattr(signal_domain, 'timeframe', 'UNKNOWN')))
            signal_type = telegram_service.escape_markdown(str(getattr(signal_domain, 'signal_type', 'UNKNOWN')))
            score = telegram_service.escape_markdown(str(getattr(signal_domain, 'score', getattr(signal_domain, 'confluence_score', 0))))
            
            msg = f"🟢 *Signal {stat}*\n" if status == "EXECUTED" else f"⚪ *Signal {stat}*\n"
            msg += f"Symbol: {sym}\n"
            msg += f"Strategy: {strategy_id} ({timeframe})\n"
            msg += f"Type: {signal_type}\n"
            msg += f"Direction: {direction}\n"
            msg += f"Entry: {price}\n"
            
            sl = str(getattr(signal_domain, 'stop_loss', 'N/A'))
            msg += f"SL: {telegram_service.escape_markdown(sl)}\n"
            
            if tp_levels:
                msg += f"Positions: {len(tp_levels)}\n"
                for i, tp in enumerate(tp_levels):
                    tp_price = getattr(tp, 'tp_price', tp.get('tp_price') if isinstance(tp, dict) else 'N/A')
                    vol = getattr(tp, 'volume', tp.get('volume') if isinstance(tp, dict) else 'N/A')
                    if tp_price != 'N/A':
                        tp_price = f"{float(tp_price):.5f}"
                    msg += f"  - TP{i+1}: {telegram_service.escape_markdown(tp_price)} (Vol: {telegram_service.escape_markdown(str(vol))})\n"
            else:
                tp_fallback = str(getattr(signal_domain, 'take_profit', 'N/A'))
                msg += f"TP: {telegram_service.escape_markdown(tp_fallback)}\n"

            msg += f"Score: {score}/100\n"
            if reason:
                msg += f"Reason: {reason}\n"
                
            asyncio.create_task(telegram_service.send_message(msg))
        except Exception as e:
            logger.error(f"Failed to dispatch Telegram: {e}")

        # 2. Save to Database
        try:
            import json

            from sqlalchemy import select

            from backend.data.database import async_session
            from backend.data.models import Signal
            
            async with async_session() as session:
                sig_time = getattr(signal_domain, 'timestamp', None)
                if not sig_time and hasattr(signal_domain, 'chart_data') and signal_domain.chart_data:
                    sig_time = signal_domain.chart_data[-1].get('time')
                if not sig_time:
                    sig_time = datetime.utcnow().timestamp()
                
                dt_time = datetime.utcfromtimestamp(sig_time)
                
                query = select(Signal).where(
                    Signal.user_id == self.user_id,
                    Signal.symbol == signal_domain.symbol,
                    Signal.timeframe == getattr(signal_domain, 'timeframe', 'M5'),
                    Signal.signal_time == dt_time
                )
                result = await session.execute(query)
                sig_db = result.scalar_one_or_none()
                
                if not sig_db:
                    sig_db = Signal(
                        user_id=self.user_id,
                        strategy_id=getattr(signal_domain, 'strategy_id', 'UNKNOWN'),
                        symbol=signal_domain.symbol,
                        timeframe=getattr(signal_domain, 'timeframe', 'M5'),
                        signal_type=getattr(signal_domain, 'signal_type', 'UNKNOWN'),
                        direction=signal_domain.direction,
                        price_at_signal=getattr(signal_domain, 'price_at_signal', signal_domain.entry_price),
                        entry_price=signal_domain.entry_price,
                        stop_loss=signal_domain.stop_loss,
                        tp1_price=getattr(signal_domain, 'take_profit', None), # fallback
                        confluence_score=getattr(signal_domain, 'confluence_score', getattr(signal_domain, 'score', 0.0)),
                        acted_on=(status == "EXECUTED"),
                        skip_reason=reject_reason if status != "EXECUTED" else None,
                        signal_time=dt_time,
                        chart_data=json.dumps(signal_domain.chart_data) if getattr(signal_domain, 'chart_data', None) else None
                    )
                    session.add(sig_db)
                else:
                    sig_db.acted_on = (status == "EXECUTED")
                    if reject_reason:
                        sig_db.skip_reason = reject_reason
                
                await session.commit()
                return sig_db.id
        except Exception as e:
            logger.error(f"Failed to save signal to DB: {e}")
            return None

    async def start(self, user_id: str, symbols: list[str] | None = None,
                    scan_interval: int = 60) -> dict[str, Any]:
        """Start the bot scanning loop."""
        if self.running:
            return {"running": True, "message": "Bot is already running"}

        self.symbols = symbols or ["XAUUSD", "EURUSD", "GBPUSD", "USOIL", "ETHUSD", "GBPJPY"]
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
        from backend.services.position_manager import position_manager
        position_manager.start(user_id)

        # Server restart recovery: Check DB for OPEN trades and verify with MT5
        try:
            import MetaTrader5 as mt5
            from sqlalchemy import select

            from backend.data.database import async_session
            from backend.data.models import TradePosition
            
            if mt5.terminal_info():
                async with async_session() as session:
                    result = await session.execute(select(TradePosition).where(TradePosition.status == "OPEN"))
                    open_pos = result.scalars().all()
                    for pos in open_pos:
                        mt5_pos = mt5.positions_get(ticket=pos.mt5_ticket)
                        if not mt5_pos:
                            # Instead of immediately marking as ghost, check history
                            # to see if it closed while offline.
                            now = datetime.now(timezone.utc)
                            from_date = now - timedelta(days=14)
                            deals = mt5.history_deals_get(from_date, now, group=f"*{pos.mt5_ticket}*")
                            
                            if deals and len(deals) > 0:
                                # We have a history deal for this ticket. Let position_manager resolve it 
                                # naturally via God Sync / closed_positions check. 
                                # Don't start a ghost grace period since we know it's in history.
                                pass
                            else:
                                # Not in open positions, not in history. Might be a startup delay.
                                # Seed ghost grace period so reconciliation handles it gracefully.
                                from backend.services.position_manager import (
                                    position_manager,
                                )
                                position_manager._ghost_strike_counts[pos.mt5_ticket] = 1
                                self._log_event(
                                    f"Position {pos.mt5_ticket} missing from MT5 (no history found yet). "
                                    f"Ghost grace period started (1/{position_manager.GHOST_GRACE_POLLS}).",
                                    "WARN", "BOT"
                                )
        except Exception as e:
            logger.error(f"Recovery error: {e}")

        return {"running": True, "message": "Bot started successfully", "symbols": self.symbols}

    async def stop(self) -> dict[str, Any]:
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
        
        from backend.services.position_manager import position_manager
        position_manager.stop()

        self._log_event("Bot stopped by user", category="BOT")
        return {"running": False, "message": "Bot stopped"}

    def get_status(self) -> dict[str, Any]:
        """Get current bot status."""
        return {
            "running": self.running,
            "symbols": self.symbols,
            "last_scan": self.last_scan,
            "total_signals_today": self.total_signals_today,
            "scan_interval": self.scan_interval,
        }

    def get_logs(self, limit: int = 50) -> dict[str, Any]:
        """Get recent activity log entries."""
        events = list(self._events)[:limit]
        return {"events": events, "total": len(self._events)}

    async def _scan_loop(self, user_id: str):
        """Main scanning loop — runs the SMC strategy on each symbol."""
        from backend.mt5.data_fetcher import DataFetcher

        self._log_event("Scan loop started — entering main cycle", category="BOT")

        while self.running:
            try:
                import json

                from sqlalchemy import select

                from backend.core.config_schema import UserConfig, UserConfigV2
                from backend.data.database import async_session
                from backend.data.models import UserConfigModel
                
                async with async_session() as session:
                    result = await session.execute(select(UserConfigModel).where(UserConfigModel.user_id == user_id))
                    config_db = result.scalar_one_or_none()
                    if config_db and getattr(config_db, 'config_json', None):
                        config_dict = json.loads(config_db.config_json)
                        
                        # Update Telegram config dynamically
                        from backend.services.telegram import telegram_service
                        telegram_service.update_config(
                            config_dict.get('telegram_bot_token', ''),
                            config_dict.get('telegram_chat_id', '')
                        )
                        
                        config = UserConfigV2.from_dict(config_dict)
                    elif config_db and getattr(config_db, 'config', None):
                        config = UserConfig.parse_obj(config_db.config)
                    else:
                        config = UserConfig()

                from backend.risk.circuit_breaker import CircuitBreaker
                from backend.risk.prop_firm_validator import PropFirmValidator
                from backend.strategies.registry import get_strategy

                if not self.circuit_breaker:
                    self.circuit_breaker = CircuitBreaker(config.risk.to_dict() if hasattr(config.risk, 'to_dict') else config.risk.__dict__)
                if not getattr(self, "prop_firm_validator", None):
                    self.prop_firm_validator = PropFirmValidator(getattr(config, "prop_firm", None) or config)
                else:
                    pf_config = getattr(config, "prop_firm", None) or config
                    self.prop_firm_validator.enabled = getattr(pf_config, "account_mode", "personal") == "prop_firm"
                    self.prop_firm_validator.challenge_type = getattr(pf_config, "challenge_type", "none")
                    self.prop_firm_validator.account_size = getattr(pf_config, "account_size", 10000.0)
                    self.prop_firm_validator.initial_balance = getattr(pf_config, "initial_balance", 10000.0)
                    self.prop_firm_validator.max_lot_sizes = getattr(pf_config, "max_lot_sizes", {})

                # Initialize and refresh news filter if enabled
                if not hasattr(self, 'news_filter'):
                    from backend.risk.news_filter import NewsFilter
                    self.news_filter = NewsFilter(
                        enabled=getattr(config.smc, 'news_filter_enabled', False),
                        block_window_minutes=getattr(config.smc, 'news_buffer_minutes', 30)
                    )
                else:
                    self.news_filter.enabled = getattr(config.smc, 'news_filter_enabled', False)
                    self.news_filter.block_window = timedelta(minutes=getattr(config.smc, 'news_buffer_minutes', 30))
                
                if self.news_filter.enabled:
                    await self.news_filter.refresh_calendar()
                    
                if getattr(self, "prop_firm_validator", None) and self.prop_firm_validator.enabled:
                    try:
                        from backend.brokers.factory import broker_factory
                        broker = broker_factory.get_broker()
                        if broker.account_info:
                            self.prop_firm_validator.update_equity_balance(
                                equity=broker.account_info.equity,
                                balance=broker.account_info.balance,
                                current_time=datetime.now(timezone.utc)
                            )
                    except Exception as e:
                        logger.error(f"Failed to update prop firm equity: {e}")

                # Maintain a dictionary of engines per symbol
                if not hasattr(self, 'engines'):
                    self.engines = {}

                for symbol in self.symbols:
                    if not self.running:
                        break

                    # Throttle between symbols to avoid overwhelming MT5
                    await asyncio.sleep(0.5)

                    self._log_event(f"Scanning {symbol}...", category="SCAN")

                    try:
                        import pandas as pd

                        from backend.mt5.order_manager import OrderManager
                        from backend.risk.engine import RiskEngine
                        
                        def _index_candles(df):
                            if 'time' in df.columns:
                                return df.set_index(pd.to_datetime(df['time'], unit='s'))
                            return df

                        # ── DYNAMIC STRATEGY RESOLUTION ──
                        strategy_id = "SMC_v1"
                        
                        # Debug exactly what is in the config
                        self._log_event(f"Debug Config: symbols={getattr(config, 'symbols', self.symbols)}, instruments={[s.symbol + ':' + s.strategy_id for s in getattr(config, 'instrument_settings', [])]}", "DEBUG", "BOT")
                        
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
                            await asyncio.sleep(0.2)  # Throttle between timeframe fetches
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
                                # Inject live context not available to the backtester engine directly
                                if hasattr(current_engine, 'context'):
                                    current_engine.context["news_blocked"] = self.news_filter.is_blocked(symbol) if hasattr(self, 'news_filter') else False
                                
                                # Only the last timeframe might return a signal in hierarchical strategies
                                if i == len(req_tfs) - 1:
                                    signal = res
                                    
                            await asyncio.sleep(0.01)
                            
                            if signal:
                                # Cooldown check
                                sig_time = signal.metadata.get('timestamp') if isinstance(getattr(signal, 'metadata', None), dict) else getattr(signal, 'timestamp', None)
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
                                    import json
                                    # Use to_json and loads to get clean Python types
                                    signal.chart_data = json.loads(cd_df.to_json(orient="records"))
                                except Exception as e:
                                    logger.error(f"Failed to inject chart_data: {e}")

                                self.total_signals_today += 1

                                passed_gates = getattr(signal, "metadata", {}).get("passed_gates", True)
                                if not passed_gates:
                                    reasons = getattr(signal, "metadata", {}).get("rejection_reasons", [])
                                    reason_str = "; ".join(reasons)
                                    for reason in reasons:
                                        self._log_event(f"[REJECTED] {reason}", "SIGNAL", "SIGNAL")
                                    await self._save_signal_state(signal, "SKIPPED", reason_str)
                                    continue

                                self._log_event(
                                    f"Signal: {signal.direction} {symbol} @ {signal.entry_price} "
                                    f"| SL: {signal.stop_loss} | Score: {signal.confluence_score}",
                                    "SIGNAL", "SIGNAL"
                                )

                                # === Enforce Max Concurrent Positions (MT5 = source of truth) ===
                                try:
                                    import MetaTrader5 as mt5
                                    mt5_positions = mt5.positions_get()
                                    # Count unique position groups by symbol+time to match our Trade grouping
                                    # Each group of TP positions counts as 1 trade
                                    if mt5_positions:
                                        seen_groups = set()
                                        symbol_groups = set()
                                        for p in mt5_positions:
                                            group_key = (p.symbol, round(p.time / 5) * 5)
                                            seen_groups.add(group_key)
                                            if p.symbol == signal.symbol:
                                                symbol_groups.add(group_key)
                                        live_trade_count = len(seen_groups)
                                        symbol_trade_count = len(symbol_groups)
                                    else:
                                        live_trade_count = 0
                                        symbol_trade_count = 0
                                        
                                    max_concurrent = getattr(config.risk, 'max_concurrent_positions', 3)
                                    max_per_symbol = getattr(config.risk, 'max_positions_per_symbol', 1)
                                    
                                    if live_trade_count >= max_concurrent:
                                        self._log_event(f"[REJECTED] Max open positions reached globally ({live_trade_count}/{max_concurrent})", "SIGNAL", "RISK")
                                        await self._save_signal_state(signal, "SKIPPED", f"Max open positions reached globally ({live_trade_count}/{max_concurrent})")
                                        continue
                                        
                                    if symbol_trade_count >= max_per_symbol:
                                        self._log_event(f"[REJECTED] Max positions reached for {signal.symbol} ({symbol_trade_count}/{max_per_symbol})", "SIGNAL", "RISK")
                                        await self._save_signal_state(signal, "SKIPPED", f"Max positions reached for {signal.symbol} ({symbol_trade_count}/{max_per_symbol})")
                                        continue
                                except Exception as e:
                                    logger.error(f"Error checking open positions: {e}")

                                # === Check Circuit Breaker ===
                                if self.circuit_breaker:
                                    cb_ok, cb_reason = self.circuit_breaker.check_symbol(signal.symbol)
                                    if not cb_ok:
                                        self._log_event(f"[REJECTED] Circuit breaker blocked {signal.symbol}: {cb_reason}", "SIGNAL", "RISK")
                                        await self._save_signal_state(signal, "SKIPPED", cb_reason)
                                        continue

                                # === Execute trade via RiskEngine ===
                                try:
                                    from backend.brokers.factory import broker_factory
                                    broker = broker_factory.get_broker()
                                    if not broker.account_info:
                                        logger.warning("No MT5 account info available for risk sizing")
                                        continue
                                    account_balance = broker.account_info.balance

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
                                        "prop_firm": {
                                            "account_mode": getattr(config.prop_firm, "account_mode", "personal"),
                                            "max_lot_sizes": getattr(config.prop_firm, "max_lot_sizes", {}),
                                            "initial_balance": getattr(config.prop_firm, "initial_balance", 10000.0)
                                        },
                                        "target_profit_enabled": config.risk.target_profit_enabled if hasattr(config.risk, 'target_profit_enabled') else False,
                                        "max_daily_profit": config.risk.max_daily_profit if hasattr(config.risk, 'max_daily_profit') else 500.0,
                                        "max_weekly_profit": config.risk.max_weekly_profit if hasattr(config.risk, 'max_weekly_profit') else 2000.0,
                                        "compounding_enabled": config.compounding.enabled if hasattr(config, 'compounding') else False,
                                        "be_trigger_rr": config.risk.be_trigger_rr,
                                        "be_buffer_pips": config.risk.be_buffer_pips,
                                        "be_buffer_atr_mult": getattr(config.risk, "be_buffer_atr_mult", 0.0),
                                        "trail_method_tp2": config.risk.trail_method_tp2 if hasattr(config.risk, 'trail_method_tp2') else "ATR_TRAIL",
                                        "trail_method_tp3": config.risk.trail_method_tp3 if hasattr(config.risk, 'trail_method_tp3') else "STRUCTURE_TRAIL",
                                        "trail_method_tp4": config.risk.trail_method_tp4 if hasattr(config.risk, 'trail_method_tp4') else "ATR_TRAIL",
                                        "trail_method_tp5": config.risk.trail_method_tp5 if hasattr(config.risk, 'trail_method_tp5') else "ATR_TRAIL",
                                    }
                                    risk_engine = RiskEngine(risk_config)
                                    risk_engine.circuit = self.circuit_breaker
                                    risk_engine.prop_firm_validator = getattr(self, "prop_firm_validator", None)

                                    # Enforces 1 active signal per symbol, so symbol is uniquely identifying
                                    group_id = signal.symbol

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
                                        signal_data, account_balance, compounding_risk_dollars=compounding_risk,
                                        initial_balance=getattr(config.prop_firm, "initial_balance", 10000.0) if hasattr(config, "prop_firm") else getattr(config.compounding, "starting_balance", 10000.0) if hasattr(config, "compounding") else 10000.0
                                    )

                                    if approved:
                                        # Place ALL TP positions at entry (no deferred stacking)
                                        self._log_event(
                                            f"Trade approved: {len(tp_levels)} positions — all at entry",
                                            "INFO", "TRADE"
                                        )

                                        # Place orders via OrderManager with Retry Logic
                                        db_positions = []
                                        import MetaTrader5 as mt5
                                        
                                        for tp in tp_levels:
                                            max_retries = 5
                                            for attempt in range(max_retries):
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
                                                        break  # Success! Break the retry loop for this TP
                                                    else:
                                                        err_msg = result.get('error', 'unknown')
                                                        self._log_event(
                                                            f"Order failed (Attempt {attempt+1}/{max_retries}): TP{tp.level} — {err_msg}",
                                                            "WARN", "TRADE"
                                                        )
                                                        if attempt < max_retries - 1:
                                                            mt5.initialize() # Try re-init MT5
                                                            await asyncio.sleep(0.5)
                                                        else:
                                                            self._log_event(f"Order failed permanently after {max_retries} attempts: TP{tp.level}", "ERROR", "TRADE")
                                                            asyncio.ensure_future(self._broadcast_notification(
                                                                "MT5 Execution Failed",
                                                                f"Order failed for {signal.symbol}: {err_msg}",
                                                                "error"
                                                            ))
                                                except Exception as order_err:
                                                    self._log_event(
                                                        f"Order placement error (Attempt {attempt+1}): {str(order_err)[:100]}",
                                                        "WARN", "TRADE"
                                                    )
                                                    if attempt < max_retries - 1:
                                                        mt5.initialize()
                                                        await asyncio.sleep(0.5)
                                                    else:
                                                        self._log_event(f"Permanent placement error: {str(order_err)[:100]}", "ERROR", "TRADE")
                                                        asyncio.ensure_future(self._broadcast_notification(
                                                                "MT5 Execution Failed",
                                                                f"Order placement error for {signal.symbol}: {str(order_err)[:100]}",
                                                                "error"
                                                        ))
                                    
                                        if not db_positions:
                                            self.circuit_breaker.rollback_position(group_id)
                                            self._log_event(f"All orders failed. Rolled back risk state for {group_id}.", "WARN", "RISK")
                                            await self._save_signal_state(signal, "FAILED", "MT5 execution failed", tp_levels=tp_levels)
                                        elif len(db_positions) < len(tp_levels):
                                            self.circuit_breaker.active_groups[group_id]["sub_trades"] = len(db_positions)
                                    
                                        if db_positions and self.user_id:
                                            sig_id = await self._save_signal_state(signal, "EXECUTED", tp_levels=db_positions)
                                            try:
                                                import json

                                                from backend.data.database import (
                                                    async_session,
                                                )
                                                from backend.data.models import (
                                                    Trade,
                                                    TradePosition,
                                                    Signal,
                                                )
                                                
                                                async with async_session() as session:
                                                    trade = Trade(
                                                        user_id=self.user_id,
                                                        symbol=signal.symbol,
                                                        direction=signal.direction,
                                                        entry_price=signal.entry_price,
                                                        stop_loss=signal.stop_loss,
                                                        volume=sum(p["volume"] for p in db_positions),
                                                        status="OPEN",
                                                        entry_time=datetime.utcnow(),
                                                        chart_data=json.dumps(signal.chart_data) if signal.chart_data else None,
                                                        confluence_score=getattr(signal, 'confluence_score', None),
                                                        balance_before=account_balance,
                                                        mt5_ticket=db_positions[0]["ticket"] if db_positions else None,
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
                                                        
                                                    # Link the signal to this trade
                                                    if sig_id:
                                                        sig_db = await session.get(Signal, sig_id)
                                                        if sig_db:
                                                            sig_db.trade_id = trade.id
                                                            
                                                    await session.commit()
                                                    
                                                    # Force frontend Journal/Dashboard to refetch
                                                    from backend.api.websocket import (
                                                        manager as ws_manager,
                                                    )
                                                    await ws_manager.broadcast_all({"type": "trade_update"})
                                                    
                                            except Exception as db_err:
                                                logger.error(f"Failed to save live trade to DB: {db_err}")
                                                
                                    else:
                                        self._log_event(
                                            f"Trade rejected by risk engine: {reason}",
                                            "WARN", "RISK"
                                        )
                                        await self._save_signal_state(signal, "REJECTED", reason, tp_levels=tp_levels)
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
        """Monitors closed MT5 positions for ProfitTracker and notifications.
        
        NOTE: All DB state transitions (TradePosition.status, .pnl, Trade.status, Trade.pnl)
        are owned exclusively by PositionManager._manage_positions to prevent race conditions.
        This loop only handles: profit accumulation, activity log, Telegram, circuit breaker.
        """
        from backend.mt5.order_manager import OrderManager
        import json
        import os
        state_file = "backend/data/bot_sync_state.json"
        
        last_check_time = datetime.now(timezone.utc).timestamp() - 86400 * 3
        processed_deal_tickets = set()
        
        if os.path.exists(state_file):
            try:
                with open(state_file, "r") as f:
                    data = json.load(f)
                    last_check_time = data.get("last_check_time", last_check_time)
                    processed_deal_tickets = set(data.get("processed_deal_tickets", []))
            except Exception as e:
                logger.error(f"Failed to load bot sync state: {e}")
        
        while self.running:
            try:
                await asyncio.sleep(15)
                
                deals = await OrderManager.get_closed_positions_since(last_check_time)
                if deals:
                    deals.sort(key=lambda x: x["time"])
                    
                    for deal in deals:
                        ticket = deal.get("ticket")
                        
                        if deal["time"] > last_check_time:
                            processed_deal_tickets.clear()
                            last_check_time = deal["time"]
                            
                        if deal["time"] >= last_check_time and ticket not in processed_deal_tickets:
                            processed_deal_tickets.add(ticket)
                            net_profit = deal["profit"] + deal["commission"] + deal["swap"]
                            await profit_tracker.add_profit(net_profit)
                            
                            self._log_event(
                                f"Trade closed: {deal['symbol']} | P&L: ${net_profit:.2f}",
                                "INFO", "TRADE"
                            )
                            asyncio.ensure_future(self._broadcast_notification(
                                "Trade Closed",
                                f"{deal['symbol']} closed for ${net_profit:.2f}",
                                "success" if net_profit >= 0 else "error"
                            ))
                            
                            # Update circuit breaker state
                            if self.circuit_breaker:
                                pos_id = deal.get("position_id")
                                symbol = deal.get("symbol", "UNKNOWN")
                                close_time = datetime.fromtimestamp(deal["time"], timezone.utc) if deal.get("time") else None
                                self.circuit_breaker.position_closed(symbol, net_profit, close_time)
                            if getattr(self, "prop_firm_validator", None):
                                self.prop_firm_validator.record_trade_closed(deal.get("symbol", "UNKNOWN"), deal.get("volume", 0.0), net_profit)
                                
                            try:
                                os.makedirs(os.path.dirname(state_file), exist_ok=True)
                                with open(state_file, "w") as f:
                                    json.dump({
                                        "last_check_time": last_check_time,
                                        "processed_deal_tickets": list(processed_deal_tickets)
                                    }, f)
                            except Exception as e:
                                logger.error(f"Failed to save bot sync state: {e}")
                            
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Trade sync loop error: {e}")
                await asyncio.sleep(5)


# Singleton instance
bot_service = BotService()
