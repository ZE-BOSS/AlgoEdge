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

from backend.risk.multi_tp import slot_overrides_from_config
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
        self.risk_engine = None          # Cached RiskEngine — rebuilt only when config changes
        self._risk_engine_fp = None      # Fingerprint tuple for cache invalidation
        # [4.2/D1] For personal accounts under sizing_basis="STATIC", the
        # static anchor is the balance first observed after this service
        # started — NOT prop_firm.initial_balance (that field's $10,000
        # default previously caused "Bug 12": lot sizes computed against
        # $10k instead of a real $25k personal account). Cached once per
        # process lifetime; a bot restart re-anchors to the balance at
        # that moment, matching what "static" should mean for an account
        # with no explicit starting-balance concept of its own.
        self._static_personal_balance_anchor: float | None = None
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

        self.symbols = symbols or []
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

        try:
            from backend.risk.position_sizer import update_mt5_cache
            update_mt5_cache(self.symbols)
        except Exception as e:
            logger.error(f"Failed to update MT5 symbol cache on startup: {e}")

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

        # ── Reconcile CircuitBreaker open_positions_by_symbol with actual MT5 state ──
        # After a restart, cb_state.json may have stale open-position counts for symbols that
        # were closed while the bot was offline (manual close, SL hit during downtime, etc.).
        # Query MT5 for truly-open positions and correct the CB before the scan loop starts.
        try:
            import MetaTrader5 as mt5
            if mt5.terminal_info() and self.circuit_breaker:
                live_positions = mt5.positions_get()
                open_symbols = [p.symbol for p in live_positions] if live_positions else []
                self.circuit_breaker.reconcile_from_mt5(open_symbols)
                self._log_event(
                    f"CB reconciled with MT5: {len(open_symbols)} open position(s) — "
                    + (", ".join(set(open_symbols)) if open_symbols else "none"),
                    "INFO", "BOT"
                )
        except Exception as e:
            logger.error(f"CB MT5 reconciliation error: {e}")

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

                from backend.core.config_schema import UserConfigV2
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
                        # Migrate legacy Pydantic blob to UserConfigV2 if dict conversion is possible
                        try:
                            config = UserConfigV2.from_dict(json.loads(config_db.config)) if isinstance(config_db.config, str) else UserConfigV2.from_dict(config_db.config)
                        except Exception:
                            config = UserConfigV2()
                    else:
                        config = UserConfigV2()

                from backend.risk.circuit_breaker import CircuitBreaker
                from backend.risk.prop_firm_validator import PropFirmValidator
                from backend.strategies.registry import get_strategy

                # Bug 11 fix: rebuild CircuitBreaker when key risk settings change.
                # Previously initialized once (`if not self.circuit_breaker`) and
                # never updated, so max_daily_trades / max_concurrent_positions
                # changes made in the frontend had no effect on a running bot.
                risk_dict = config.risk.to_dict() if hasattr(config.risk, 'to_dict') else vars(config.risk)
                _cb_fp = (
                    risk_dict.get("max_daily_trades"),
                    risk_dict.get("max_concurrent_positions"),
                    risk_dict.get("max_daily_drawdown_pct"),
                    risk_dict.get("max_weekly_drawdown_pct"),
                    risk_dict.get("max_positions_per_symbol"),
                )
                if self.circuit_breaker is None or getattr(self, "_cb_fp", None) != _cb_fp:
                    # Preserve live state when only non-limit settings change
                    old_cb = self.circuit_breaker
                    self.circuit_breaker = CircuitBreaker(risk_dict)
                    if old_cb is not None:
                        # Carry over live state counters so open positions aren't lost
                        self.circuit_breaker.daily_trades_count = old_cb.daily_trades_count
                        self.circuit_breaker.daily_pnl = old_cb.daily_pnl
                        self.circuit_breaker.weekly_pnl = old_cb.weekly_pnl
                        self.circuit_breaker.open_positions_by_symbol = old_cb.open_positions_by_symbol
                        self.circuit_breaker.active_groups = old_cb.active_groups
                        self.circuit_breaker.is_paused = old_cb.is_paused
                        self.circuit_breaker.pause_reason = old_cb.pause_reason
                        self.circuit_breaker.last_reset_day = old_cb.last_reset_day
                        self.circuit_breaker.last_reset_week = old_cb.last_reset_week
                        self.circuit_breaker._day_start_balance = old_cb._day_start_balance
                    self._cb_fp = _cb_fp
                    logger.info("[BOT] CircuitBreaker rebuilt with updated risk config.")
                if not getattr(self, "prop_firm_validator", None):
                    self.prop_firm_validator = PropFirmValidator(getattr(config, "prop_firm", None) or config)
                else:
                    pf_config = getattr(config, "prop_firm", None) or config
                    self.prop_firm_validator.enabled = getattr(pf_config, "account_mode", "personal") == "prop_firm"
                    self.prop_firm_validator.challenge_type = getattr(pf_config, "challenge_type", "none")
                    self.prop_firm_validator.account_size = getattr(pf_config, "account_size", 10000.0)
                    self.prop_firm_validator.initial_balance = getattr(pf_config, "initial_balance", 10000.0)
                    self.prop_firm_validator.max_lot_sizes = getattr(pf_config, "max_lot_sizes", {})

                # Initialize and refresh news filter if enabled (defaults to False, no global config)
                if not hasattr(self, 'news_filter'):
                    from backend.risk.news_filter import NewsFilter
                    self.news_filter = NewsFilter(
                        enabled=False,
                        blackout_before_minutes=15,
                        blackout_after_minutes=30,
                    )
                else:
                    self.news_filter.enabled = False
                    self.news_filter.blackout_before = timedelta(minutes=15)
                    self.news_filter.blackout_after = timedelta(minutes=30)
                
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

                # Maintain a dictionary of engines per SLOT (was per symbol)
                if not hasattr(self, 'engines'):
                    self.engines = {}

                had_execution_failure = False

                # [12.7/Part14] Slot-aware dispatch — the same symbol may now
                # run under more than one strategy concurrently
                # (InstrumentSlot, [12.1]). self.symbols remains the
                # authoritative filter of which symbols are active for this
                # bot run (set by /bot/start); each active symbol resolves to
                # its one-or-more ENABLED slots. A symbol with no matching
                # slot at all (e.g. a bare symbol list with no
                # instrument_slots/instrument_settings backing it) gets a
                # synthetic single default-strategy slot, reproducing today's
                # "APA_v1 fallback" behaviour exactly. slot_id is derived
                # DETERMINISTICALLY (not a fresh uuid4 per scan) so
                # self.engines[slot_id] correctly finds and reuses the SAME
                # engine instance across scan cycles — a fresh id every cycle
                # would silently wipe every strategy's internal state machine
                # on every single scan.
                import uuid as _uuid_mod
                from backend.core.config_schema import InstrumentSlot as _InstrumentSlot
                _slots_by_symbol: dict[str, list] = {}
                for _slot in (getattr(config, 'instrument_slots', None) or []):
                    if _slot.enabled:
                        _slots_by_symbol.setdefault(_slot.symbol, []).append(_slot)

                effective_slots = []
                for _sym in self.symbols:
                    _matching = _slots_by_symbol.get(_sym)
                    if _matching:
                        effective_slots.extend(_matching)
                    else:
                        effective_slots.append(_InstrumentSlot(
                            slot_id=_uuid_mod.uuid5(_uuid_mod.NAMESPACE_OID, f"{_sym}:APA_v1").hex[:12],
                            symbol=_sym, strategy_id="APA_v1",
                        ))

                for slot in effective_slots:
                    symbol = slot.symbol
                    if not self.running:
                        break

                    # Throttle between symbols to avoid overwhelming MT5
                    await asyncio.sleep(0.5)

                    self._log_event(f"Scanning {symbol} [{slot.strategy_id}]...", category="SCAN")

                    try:
                        import pandas as pd

                        from backend.mt5.order_manager import OrderManager
                        from backend.risk.engine import RiskEngine

                        def _index_candles(df):
                            if 'time' in df.columns:
                                return df.set_index(pd.to_datetime(df['time'], unit='s'))
                            return df

                        # ── STRATEGY RESOLUTION — now via the slot itself, not a
                        # separate instrument_settings lookup keyed by symbol ──
                        strategy_id = slot.strategy_id
                        if strategy_id == "SMC_v1":
                            strategy_id = "APA_v1"

                        # Instantiate engine if not exists — keyed by slot_id so
                        # two slots on the same symbol never share one engine's
                        # (and therefore one strategy's) internal state dict.
                        if slot.slot_id not in self.engines or getattr(self.engines[slot.slot_id], 'strategy_id', None) != strategy_id:
                            engine_class = get_strategy(strategy_id)
                            new_engine = engine_class(config)
                            # [12.1/12.7] strategy_params_override — engine.params
                            # starts as a REFERENCE to the shared config.<strategy>
                            # object (config.apa, config.vwap, ...), which every
                            # OTHER slot running the same strategy also reads. A
                            # per-slot override must rebind engine.params to its
                            # OWN copy first, or applying it here would silently
                            # leak into every sibling slot sharing that strategy.
                            if getattr(slot, "strategy_params_override", None):
                                import copy as _copy
                                new_engine.params = _copy.deepcopy(new_engine.params)
                                for _k, _v in slot.strategy_params_override.items():
                                    if hasattr(new_engine.params, _k):
                                        setattr(new_engine.params, _k, _v)
                                    else:
                                        logger.warning(f"[{symbol}] slot {slot.slot_id}: strategy_params_override key '{_k}' not found on {strategy_id} params — ignored")
                            self.engines[slot.slot_id] = new_engine
                            # [T3.5] Session gating is a per-strategy verdict, not a global
                            # one: the ablation measured -0.170 for HTFFVGFlip (actively
                            # harmful) through +0.126 for BiasIFVG (best in the study).
                            # Applied here so live matches the backtester.
                            try:
                                from backend.strategies.strategy_defaults import get_strategy_defaults
                                _sd_live = get_strategy_defaults(strategy_id)
                                if "session_filter_enabled" in _sd_live:
                                    _blk_name = {
                                        "APA_v1": "apa", "VWAP_v1": "vwap", "CRT_v1": "crt",
                                        "BiasIFVG_v1": "bias_ifvg",
                                        "HTFFVGFlip_v1": "htf_fvg_flip",
                                        "NYOpenRetest_v1": "ny_open_retest",
                                    }.get(strategy_id)
                                    _blk = getattr(config, _blk_name, None) if _blk_name else None
                                    if _blk is not None and hasattr(_blk, "session_filter_enabled"):
                                        _blk.session_filter_enabled = _sd_live["session_filter_enabled"]
                                        logger.info(
                                            f"[LIVE] {strategy_id}: session_filter_enabled="
                                            f"{_sd_live['session_filter_enabled']} (measured default)"
                                        )
                            except Exception as _e:
                                logger.warning(f"[LIVE] session default not applied: {_e}")

                            self.engines[slot.slot_id].strategy_id = strategy_id
                            self._log_event(f"[{symbol}] Instantiated {strategy_id} Engine (slot {slot.slot_id})", "INFO", "BOT")

                        current_engine = self.engines[slot.slot_id]
                        
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
                                # We have a signal. Inject strategy ID.
                                signal.strategy_id = strategy_id

                                # Cooldown check
                                sig_time = signal.metadata.get('timestamp') if isinstance(getattr(signal, 'metadata', None), dict) else getattr(signal, 'timestamp', None)
                                if not sig_time and hasattr(signal, 'chart_data') and signal.chart_data:
                                    sig_time = signal.chart_data[-1].get('time')
                                # Build a setup fingerprint from (entry, SL, direction).
                                # Using chart_data[-1]['time'] alone would make all scans
                                # within the same 5-min candle share the same cooldown key,
                                # silently dropping every re-scan after the first one.
                                _sig_fp = (round(signal.entry_price, 5), round(signal.stop_loss, 5), signal.direction)
                                if _sig_fp == self._last_signal_time.get(symbol):
                                    continue
                                
                                # Do NOT update self._last_signal_time here. Wait until we know if it was EXECUTED or REJECTED.
                                    
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
                                    self._last_signal_time[symbol] = _sig_fp  # Prevent re-evaluation on next scan
                                    await self._save_signal_state(signal, "SKIPPED", reason_str)
                                    continue

                                self._log_event(
                                    f"Signal: {signal.direction} {symbol} @ {signal.entry_price} "
                                    f"| SL: {signal.stop_loss} | Score: {signal.confluence_score}",
                                    "SIGNAL", "SIGNAL"
                                )

                                # === Check Circuit Breaker ===
                                # [4.8/D10] Position-per-symbol and max-concurrent-positions limits
                                # are now enforced ONLY here. This used to be preceded by a SEPARATE
                                # MT5-live-position-count check duplicating exactly what
                                # CircuitBreaker.check_symbol (per-symbol) and check_all (global
                                # concurrent, inside evaluate_signal below) already enforce — two
                                # independent implementations of the same rule is exactly the kind
                                # of divergence Rule-5 exists to catch, and CircuitBreaker's state is
                                # kept accurate against MT5 via reconcile_from_mt5() on startup, so
                                # it doesn't need a parallel direct-MT5-count fallback here.
                                if self.circuit_breaker:
                                    # [12.5/Part14] Slot-aware — was a bare check_symbol(symbol),
                                    # which would incorrectly block a second slot's signal against
                                    # the GLOBAL symbol-wide position count even though
                                    # evaluate_signal's own (slot-aware) check further down would
                                    # have allowed it. Must agree with evaluate_signal's internal
                                    # check_symbol call or this early-exit silently overrides it.
                                    _slot_max_positions = (
                                        slot.max_positions_per_symbol
                                        if slot.max_positions_per_symbol is not None
                                        else getattr(config.risk, "max_positions_per_symbol", 1)
                                    )
                                    cb_ok, cb_reason = self.circuit_breaker.check_symbol(
                                        signal.symbol,
                                        slot_id=slot.slot_id,
                                        slot_max_positions=_slot_max_positions,
                                        slot_max_losses_per_day=slot.max_losses_per_day,
                                    )
                                    if not cb_ok:
                                        self._log_event(f"[REJECTED] Circuit breaker blocked {signal.symbol}: {cb_reason}", "SIGNAL", "RISK")
                                        await self._save_signal_state(signal, "SKIPPED", cb_reason)
                                        continue

                                # === News Filter Hard Gate ===
                                # Explicit block here (not just context injection into the strategy engine)
                                # ensures no order is ever placed during a news window, regardless of
                                # whether the strategy checks context["news_blocked"] or not.
                                if hasattr(self, 'news_filter') and self.news_filter.enabled and self.news_filter.is_blocked(signal.symbol):
                                    _block_mins = int(self.news_filter.blackout_before.total_seconds() // 60) if hasattr(self.news_filter, 'blackout_before') else 30
                                    _news_msg = f"News filter: high-impact event within {_block_mins}min window"
                                    self._log_event(f"[REJECTED] {_news_msg} — {signal.symbol}", "SIGNAL", "RISK")
                                    await self._save_signal_state(signal, "SKIPPED", _news_msg)
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
                                        "tp4_rr": config.risk.tp4_rr,
                                        "tp5_rr": config.risk.tp5_rr,
                                        "tp_count": config.risk.tp_count,
                                        "tp_splits": config.risk.tp_splits,
                                        # [17.1] Per-symbol/per-strategy R:R.
                                        # Built from InstrumentSlot.tp1_rr by the
                                        # same helper the backtest routes use, so
                                        # a target measured in a backtest is the
                                        # target actually traded live.
                                        **slot_overrides_from_config(
                                            getattr(config, "instrument_slots", None)),
                                        "multi_position_mode": True,

                                        "max_daily_drawdown_pct": config.risk.max_daily_drawdown_pct,
                                        "max_weekly_drawdown_pct": config.risk.max_weekly_drawdown_pct,
                                        "max_daily_trades": config.risk.max_daily_trades,
                                        "max_concurrent_positions": config.risk.max_concurrent_positions,
                                        "max_positions_per_symbol": config.risk.max_positions_per_symbol,
                                        "prop_firm": {
                                            "account_mode": getattr(config.prop_firm, "account_mode", "personal"),
                                            "max_lot_sizes": getattr(config.prop_firm, "max_lot_sizes", {}),
                                            "initial_balance": getattr(config.prop_firm, "initial_balance", 10000.0),
                                            # [3.10/3.11] real, user-editable settings — see PropFirmParams.
                                            "default_max_lot": getattr(config.prop_firm, "default_max_lot", None),
                                            "max_positions_per_symbol": getattr(config.prop_firm, "max_positions_per_symbol", 5),
                                            "max_total_positions": getattr(config.prop_firm, "max_total_positions", 13),
                                            "trading_day_rule": getattr(config.prop_firm, "trading_day_rule", "PROFIT_PCT"),
                                            "trading_day_profit_pct": getattr(config.prop_firm, "trading_day_profit_pct", 0.5),
                                        },
                                        # max_risk_hard_cap_pct: user-configurable absolute safety cap from RiskParams.
                                        # Active for ALL account modes (personal and prop_firm).
                                        "max_risk_hard_cap_pct": getattr(config.risk, "max_risk_hard_cap_pct", 3.0),
                                        "target_profit_enabled": config.risk.target_profit_enabled,
                                        "max_daily_profit": config.risk.max_daily_profit,
                                        "max_weekly_profit": config.risk.max_weekly_profit,
                                        # Break-even settings
                                        "be_trigger_rr": config.risk.be_trigger_rr,
                                        "be_buffer_pips": config.risk.be_buffer_pips,
                                        "be_buffer_atr_mult": config.risk.be_buffer_atr_mult,
                                        # Trailing stop settings — all previously missing, causing
                                        # TrailingManager to silently use its hardcoded defaults
                                        # regardless of what the user configured in the Settings panel.
                                        "trail_method_tp1": getattr(config.risk, "trail_method_tp1", "NONE"),
                                        "trail_method_tp2": config.risk.trail_method_tp2,
                                        "trail_method_tp3": config.risk.trail_method_tp3,
                                        "trail_method_tp4": config.risk.trail_method_tp4,
                                        "trail_method_tp5": config.risk.trail_method_tp5,
                                        "atr_trail_multiplier": getattr(config.risk, "atr_trail_multiplier", 1.5),
                                        "atr_trail_multiplier_tp1": getattr(config.risk, "atr_trail_multiplier_tp1", 1.5),
                                        "atr_trail_multiplier_tp2": getattr(config.risk, "atr_trail_multiplier_tp2", 1.5),
                                        "atr_trail_multiplier_tp3": getattr(config.risk, "atr_trail_multiplier_tp3", 1.5),
                                        "atr_trail_multiplier_tp4": getattr(config.risk, "atr_trail_multiplier_tp4", 1.5),
                                        "atr_trail_multiplier_tp5": getattr(config.risk, "atr_trail_multiplier_tp5", 1.5),
                                        "trail_pips": getattr(config.risk, "trail_pips", 15.0),
                                        "trail_pct": getattr(config.risk, "trail_pct", 0.5),
                                        "trail_activation_rr": getattr(config.risk, "trail_activation_rr", 1.0),
                                        "trail_step_pips": getattr(config.risk, "trail_step_pips", 5.0),
                                        "trail_structure_bars": getattr(config.risk, "trail_structure_bars", 3),
                                        # [Phase 2 sizing-truth] real, user-editable settings that used to
                                        # be hardcoded module constants — see core/config_schema.py::RiskParams.
                                        # getattr(..., None) so an older saved config without these fields
                                        # still resolves to the position_sizer.py fallback default, not 0/False.
                                        "max_margin_utilisation_pct": getattr(config.risk, "max_margin_utilisation_pct", None),
                                        "max_account_leverage": getattr(config.risk, "max_account_leverage", None),
                                        "min_deployable_risk_pct": getattr(config.risk, "min_deployable_risk_pct", 0.0),
                                        "min_stop_spread_multiple": getattr(config.risk, "min_stop_spread_multiple", None),
                                        "confluence_risk_tiers": getattr(config.risk, "confluence_risk_tiers", None),
                                        "reject_below_confluence": getattr(config.risk, "reject_below_confluence", True),
                                        "post_split_risk_tolerance_pct": getattr(config.risk, "post_split_risk_tolerance_pct", 5.0),
                                        "exit_slippage_pips": getattr(config.risk, "exit_slippage_pips", None),
                                        "open_risk_weight": getattr(config.risk, "open_risk_weight", 0.5),
                                        "allow_pyramiding": getattr(config.risk, "allow_pyramiding", False),
                                        "min_bars_between_entries": getattr(config.risk, "min_bars_between_entries", 0),
                                        "min_sl_pips": getattr(config.risk, "min_sl_pips", 0.0),
                                        # [Phase 4 parity]
                                        "sizing_basis": getattr(config.risk, "sizing_basis", "STATIC"),
                                        "be_spread_multiple": getattr(config.risk, "be_spread_multiple", 2.0),
                                        "trail_require_be_first": getattr(config.risk, "trail_require_be_first", False),
                                        # [Phase 5 exit-architecture]
                                        "be_mode": getattr(config.risk, "be_mode", "EITHER"),
                                        "be_trigger_tp_level": getattr(config.risk, "be_trigger_tp_level", 1),
                                        "trail_method_tp1": getattr(config.risk, "trail_method_tp1", "NONE"),
                                        "trail_mode": getattr(config.risk, "trail_mode", "RR"),
                                        "trail_trigger_rr": getattr(config.risk, "trail_trigger_rr", 1.5),
                                        "trail_trigger_tp_level": getattr(config.risk, "trail_trigger_tp_level", 1),
                                        "tp_volume_pcts": getattr(config.risk, "tp_volume_pcts", None),
                                        # [Phase 9 portfolio governor]
                                        "max_cluster_risk_pct": getattr(config.risk, "max_cluster_risk_pct", 0.0),
                                        "max_net_direction_risk_pct": getattr(config.risk, "max_net_direction_risk_pct", 0.0),
                                        "symbol_cluster_overrides": getattr(config.risk, "symbol_cluster_overrides", None),
                                        "strategy_risk_budget_pct": getattr(config.risk, "strategy_risk_budget_pct", None),
                                        # [2.15] Per-strategy TP1 RR override — see DriftJumpAlphaParams.tp1_rr_override.
                                        "tp1_rr_overrides_by_strategy": (
                                            {"DriftJumpAlpha": config.drift_jump_alpha.tp1_rr_override}
                                            if getattr(config.drift_jump_alpha, "tp1_rr_override", None) is not None
                                            else {}
                                        ),
                                    }

                                    # ── Per-strategy exit defaults (LIVE) ────────────────
                                    # Same registry the backtester uses, applied to the live
                                    # risk config so live and backtest cannot diverge — a
                                    # strategy trailed in backtest but not live (or the
                                    # reverse) would make every backtest result unusable as a
                                    # prediction of live behaviour.
                                    #
                                    # Only fills fields the user left at the RiskParams
                                    # default; an explicit setting always wins. See
                                    # backend/strategies/strategy_defaults.py for the
                                    # measurement behind each value.
                                    try:
                                        from backend.strategies.strategy_defaults import (
                                            get_strategy_defaults, get_strategy_evidence,
                                        )
                                        from backend.core.config_schema import RiskParams as _RP
                                        _sd = get_strategy_defaults(strategy_id)
                                        _base = _RP()
                                        _applied = {}
                                        for _k, _v in _sd.items():
                                            if _k == "session_filter_enabled":
                                                continue  # strategy params, applied at engine build
                                            # "User did not change it" == still equal to the
                                            # shipped RiskParams default.
                                            if hasattr(_base, _k) and getattr(config.risk, _k, None) == getattr(_base, _k):
                                                risk_config[_k] = _v
                                                _applied[_k] = _v
                                        if _applied and not getattr(self, "_logged_sdefaults", set()) & {strategy_id}:
                                            if not hasattr(self, "_logged_sdefaults"):
                                                self._logged_sdefaults = set()
                                            self._logged_sdefaults.add(strategy_id)
                                            self._log_event(
                                                f"[{symbol}] {strategy_id}: applied measured exit defaults "
                                                f"{_applied} — {get_strategy_evidence(strategy_id)}",
                                                category="RISK",
                                            )
                                    except Exception as _e:
                                        logger.warning(
                                            f"[LIVE] strategy exit defaults not applied for "
                                            f"{strategy_id}: {_e}"
                                        )

                                    # Cache RiskEngine across scan cycles — only rebuild when key settings change.
                                    # MultiTPManager/BreakevenManager/TrailingManager are expensive to construct;
                                    # risk_config dict is still built fresh each signal to pick up config edits.
                                    # [3.16/D4] Was a 3-field tuple (risk_per_trade_pct, tp_count, min_rr) — any
                                    # OTHER field changing (e.g. margin cap, confluence tiers, be/trail settings)
                                    # was silently invisible to the cache, so RiskEngine kept running with a
                                    # stale config until one of those 3 fields also happened to change. A hash
                                    # of the full serialised dict catches every field.
                                    import hashlib as _hashlib
                                    import json as _json
                                    _rf = _hashlib.sha256(
                                        _json.dumps(risk_config, sort_keys=True, default=str).encode()
                                    ).hexdigest()
                                    if self.risk_engine is None or self._risk_engine_fp != _rf:
                                        self.risk_engine = RiskEngine(risk_config)
                                        self._risk_engine_fp = _rf
                                    # Always refresh injected singletons (they may update between scans)
                                    self.risk_engine.circuit = self.circuit_breaker
                                    self.risk_engine.prop_firm_validator = getattr(self, "prop_firm_validator", None)

                                    # Feed live equity to prop firm validator so drawdown checks use
                                    # floating equity (balance + unrealized P&L), not just closed balance.
                                    if getattr(self, "prop_firm_validator", None) and self.prop_firm_validator.enabled:
                                        live_equity = broker.account_info.equity if hasattr(broker.account_info, 'equity') else account_balance
                                        self.prop_firm_validator.update_equity_balance(
                                            live_equity, account_balance, datetime.now(timezone.utc)
                                        )


                                    # [4.8/D10] Was `group_id = signal.symbol` — relying on the
                                    # symbol string doubling as a de-facto "1 active group per
                                    # symbol" enforcement mechanism, duplicating (and only
                                    # coincidentally agreeing with) what
                                    # CircuitBreaker.check_symbol's max_positions_per_symbol
                                    # already enforces properly. A real per-signal UUID (matching
                                    # how the backtester generates group_id) is required for
                                    # allow_pyramiding (2.17) to ever stack >1 group on one
                                    # symbol — with the old scheme two groups on the same symbol
                                    # would collide on and silently overwrite one shared
                                    # active_groups[symbol] entry.
                                    import uuid as _uuid
                                    group_id = str(_uuid.uuid4())[:8]

                                    _signal_metadata = dict(getattr(signal, 'metadata', {}) or {})
                                    # [12.5/12.6/Part14] Slot-aware circuit-breaker/risk
                                    # checks — None-defaulted overrides fall back to the
                                    # global RiskParams value, same "None = inherit"
                                    # convention as InstrumentSlot's own fields.
                                    _signal_metadata["slot_id"] = slot.slot_id
                                    _signal_metadata["slot_max_positions"] = (
                                        slot.max_positions_per_symbol
                                        if slot.max_positions_per_symbol is not None
                                        else getattr(config.risk, "max_positions_per_symbol", 1)
                                    )
                                    if slot.max_losses_per_day is not None:
                                        _signal_metadata["slot_max_losses_per_day"] = slot.max_losses_per_day
                                    if slot.risk_per_trade_pct is not None:
                                        _signal_metadata["slot_risk_per_trade_pct"] = slot.risk_per_trade_pct

                                    signal_data = {
                                        "group_id": group_id,
                                        "symbol": signal.symbol,
                                        "direction": signal.direction,
                                        "entry_price": signal.entry_price,
                                        "stop_loss": signal.stop_loss,
                                        "take_profit": signal.take_profit,
                                        "timeframe": signal.timeframe,  # [12.10] see risk/engine.py's note
                                        "chart_data": signal.chart_data,
                                        # Include metadata so size_modifier and confluence-based
                                        # risk scaling (get_confluence_scaled_risk) reach the engine.
                                        "metadata": _signal_metadata,
                                    }

                                    # [4.2/D1] What position sizing is computed against — see
                                    # RiskParams.sizing_basis and resolve_sizing_base_balance().
                                    # STATIC (the default) previously meant "always
                                    # prop_firm.initial_balance", which is exactly Bug 12
                                    # (defaults to $10,000, wrong for a personal account with
                                    # no such field of its own) — for personal accounts the
                                    # static anchor is now this process's first-observed
                                    # balance instead, never a config default that may not
                                    # match the real account.
                                    from backend.risk.position_sizer import resolve_sizing_base_balance
                                    _account_mode = getattr(getattr(config, "prop_firm", None), "account_mode", "personal")
                                    if _account_mode == "prop_firm":
                                        _static_balance = getattr(config.prop_firm, "initial_balance", account_balance)
                                    else:
                                        if self._static_personal_balance_anchor is None:
                                            self._static_personal_balance_anchor = account_balance
                                        _static_balance = self._static_personal_balance_anchor
                                    _live_equity = broker.account_info.equity if hasattr(broker.account_info, 'equity') else account_balance
                                    _sizing_base_balance = resolve_sizing_base_balance(
                                        getattr(config.risk, "sizing_basis", "STATIC"),
                                        static_balance=_static_balance,
                                        live_balance=account_balance,
                                        live_equity=_live_equity,
                                    )

                                    approved, reason, tp_levels = self.risk_engine.evaluate_signal(
                                        signal_data,
                                        account_balance,
                                        current_time=datetime.now(timezone.utc),  # explicit time
                                        initial_balance=_sizing_base_balance,
                                    )

                                    if approved:
                                        group_id = signal_data.get("group_id", "unknown")
                                        if self.circuit_breaker:
                                            # §2.3 fix: live trading never reported open-position risk to the
                                            # circuit breaker (initial_risk_dollars defaulted to 0.0), so
                                            # get_open_risk() always returned 0.0 live — the "predictive
                                            # drawdown guard" in RiskEngine.evaluate_signal was inert outside
                                            # backtesting. Compute the same actual_risk_dollars the engine
                                            # already derives internally (sum of post-split TP volumes priced
                                            # at the signal's entry/SL distance) and pass it through.
                                            from backend.risk.position_sizer import calculate_risk_dollars
                                            initial_risk_dollars = calculate_risk_dollars(
                                                sum(tp.volume for tp in tp_levels),
                                                signal.entry_price,
                                                signal.stop_loss,
                                                signal.symbol,
                                            )
                                            self.circuit_breaker.position_opened(
                                                group_id, len(tp_levels), symbol=signal.symbol,
                                                initial_risk_dollars=initial_risk_dollars,
                                                strategy_id=signal.strategy_id,
                                                direction=signal.direction,  # [9.6]
                                                slot_id=slot.slot_id,  # [12.5/12.6]
                                            )

                                        # Place ALL TP positions at entry (no deferred stacking)
                                        self._log_event(
                                            f"Trade approved: {len(tp_levels)} positions — all at entry",
                                            "INFO", "TRADE"
                                        )

                                        # Place orders via OrderManager with Retry Logic
                                        db_positions = []
                                        import MetaTrader5 as mt5
                                        # Track per-TP failure messages for detailed Telegram alert
                                        tp_failure_details: list[str] = []
                                        
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
                                                        deviation_points=getattr(config.risk, "mt5_order_deviation_points", 20),
                                                        # [2.8/A5] re-anchor SL/TP to the actual live fill —
                                                        # backtest/live parity with engine.py::_create_position (2.7).
                                                        signal_entry_price=signal.entry_price,
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
                                                        is_stale = result.get('stale', False)
                                                        is_permanent = result.get('permanent', False)
                                                        if is_stale:
                                                            # Price moved past SL — signal is genuinely stale, no point retrying
                                                            detail = f"TP{tp.level} ({tp.volume}L): {err_msg}"
                                                            tp_failure_details.append(detail)
                                                            self._log_event(
                                                                f"Signal stale at execution (price moved): TP{tp.level} skipped",
                                                                "INFO", "TRADE"
                                                            )
                                                            break  # Break retry loop immediately — retrying won't help
                                                        if is_permanent:
                                                            # Broker/account-side condition (e.g. trade disabled, market
                                                            # closed, no permission) — retrying won't help, back off now.
                                                            detail = f"TP{tp.level} ({tp.volume}L): {err_msg}"
                                                            tp_failure_details.append(detail)
                                                            self._log_event(
                                                                f"Broker rejected order permanently, not retrying: TP{tp.level} — {err_msg}",
                                                                "ERROR", "TRADE"
                                                            )
                                                            asyncio.ensure_future(self._broadcast_notification(
                                                                "MT5 Trading Disabled",
                                                                f"{signal.symbol} order rejected by broker: {err_msg}",
                                                                "error"
                                                            ))
                                                            break  # Break retry loop immediately — retrying won't help
                                                        self._log_event(
                                                            f"Order failed (Attempt {attempt+1}/{max_retries}): TP{tp.level} — {err_msg}",
                                                            "WARN", "TRADE"
                                                        )
                                                        if attempt < max_retries - 1:
                                                            if not mt5.terminal_info():
                                                                mt5.initialize()
                                                            await asyncio.sleep(0.5)
                                                        else:
                                                            detail = f"TP{tp.level} ({tp.volume}L): {err_msg}"
                                                            tp_failure_details.append(detail)
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
                                                        if not mt5.terminal_info():
                                                            mt5.initialize()
                                                        await asyncio.sleep(0.5)
                                                    else:
                                                        detail = f"TP{tp.level} ({tp.volume}L): {str(order_err)[:80]}"
                                                        tp_failure_details.append(detail)
                                                        self._log_event(f"Permanent placement error: {str(order_err)[:100]}", "ERROR", "TRADE")
                                                        asyncio.ensure_future(self._broadcast_notification(
                                                                "MT5 Execution Failed",
                                                                f"Order placement error for {signal.symbol}: {str(order_err)[:100]}",
                                                                "error"
                                                        ))

                                        if not db_positions:
                                            if self.circuit_breaker:
                                                self.circuit_breaker.rollback_position(group_id)
                                            # Detect if ALL failures were due to stale signal
                                            all_stale = tp_failure_details and all("Stale Signal" in d for d in tp_failure_details)
                                            if all_stale:
                                                self._log_event(f"Signal skipped (stale): price moved past SL before execution for {symbol}", "INFO", "TRADE")
                                                self._last_signal_time[symbol] = _sig_fp
                                                await self._save_signal_state(signal, "SKIPPED", "Stale signal — price moved past SL before execution", tp_levels=tp_levels)
                                            else:
                                                self._log_event(f"All orders failed. Rolled back risk state for {group_id}.", "WARN", "RISK")
                                                # Build detailed failure reason for Telegram (includes MT5 error codes)
                                                fail_reason = "MT5 execution failed: " + " | ".join(tp_failure_details) if tp_failure_details else "MT5 execution failed for all TP levels"
                                                self._last_signal_time[symbol] = _sig_fp  # Prevent same signal from re-firing
                                                await self._save_signal_state(signal, "FAILED", fail_reason, tp_levels=tp_levels)
                                            had_execution_failure = True
                                        elif len(db_positions) < len(tp_levels):
                                            self.circuit_breaker.active_groups[group_id]["sub_trades"] = len(db_positions)
                                    
                                        if db_positions and self.user_id:
                                            sig_id = await self._save_signal_state(signal, "EXECUTED", tp_levels=db_positions)
                                            self._last_signal_time[symbol] = _sig_fp
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
                                                        # Was missing entirely — Trade.strategy_id defaults to
                                                        # "APA_v1" at the model level, so every live trade was
                                                        # silently mislabeled regardless of which strategy actually
                                                        # generated it. This broke any strategy_id-based logic
                                                        # downstream (e.g. VWAP's live hard-close gate).
                                                        strategy_id=getattr(signal, 'strategy_id', None) or "APA_v1",
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
                                        self._last_signal_time[symbol] = _sig_fp
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
                
                if had_execution_failure:
                    self._log_event(
                        f"Execution failed this cycle. Fast-tracking next scan to retry in 5s.",
                        category="BOT"
                    )
                    await asyncio.sleep(5)
                else:
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
        # Item 3.9 + Phase-5 pruning: keyed by individual deal ticket -> deal
        # time (not position_id — see loop below for why), so we can bound
        # growth by dropping entries whose deal time has fallen behind
        # last_check_time (they can never be re-seen: get_closed_positions_since
        # queries start at last_check_time, and the `deal["time"] < last_check_time`
        # guard below already skips anything older before ever consulting this dict).
        processed_deal_tickets: dict = {}

        if os.path.exists(state_file):
            try:
                with open(state_file, "r") as f:
                    data = json.load(f)
                    last_check_time = data.get("last_check_time", last_check_time)
                    raw_tickets = data.get("processed_deal_tickets", [])
                    if isinstance(raw_tickets, dict):
                        processed_deal_tickets = {int(k): v for k, v in raw_tickets.items()}
                    else:
                        # Backward compat: older state files stored a flat list
                        # (previously of position_ids). Seed with last_check_time
                        # so they still get pruned on the next cycle.
                        processed_deal_tickets = {int(t): last_check_time for t in raw_tickets}
            except Exception as e:
                logger.error(f"Failed to load bot sync state: {e}")
        
        while self.running:
            try:
                await asyncio.sleep(15)
                
                deals = await OrderManager.get_closed_positions_since(last_check_time)
                if deals:
                    deals.sort(key=lambda x: x["time"])

                    # Phase-5: consolidate Telegram close-notifications for all
                    # deals processed in this sync cycle into ONE message instead
                    # of firing one send per deal — a backlog of closes after
                    # extended bot downtime could otherwise burst-send enough
                    # messages to hit Telegram's rate limit and silently drop
                    # alerts. Grouped by position_id so a position closed in
                    # multiple parts this cycle shows as a single consolidated
                    # line instead of N separate ones.
                    tg_events: dict = {}

                    for deal in deals:
                        ticket = deal.get("ticket")
                        # Item 3.9 fix: dedupe by the INDIVIDUAL deal ticket, not
                        # position_id. A position closed in 2+ parts (partial
                        # close, then final close) generates multiple OUT deals
                        # sharing one position_id — deduping by position_id alone
                        # permanently skipped every deal after the first seen for
                        # that position, losing the PnL from later partial/final
                        # closes. Deduping by ticket instead still processes each
                        # of the 3 separate TP-leg positions of one signal exactly
                        # once each (they have distinct position_ids AND distinct
                        # deal tickets), so the original triple-counting protection
                        # is unaffected. `pos_id` is retained only for grouping
                        # notifications/logs below, not for dedup.
                        pos_id = deal.get("position_id") or ticket

                        # Guard against replaying deals from a previous cycle. The sorted()
                        # call above ensures ascending time order, so once we see a deal
                        # older than last_check_time we can skip it. The ticket check
                        # handles duplicate entries at the exact boundary second.
                        if deal["time"] < last_check_time:
                            continue
                        if ticket in processed_deal_tickets:
                            continue
                        processed_deal_tickets[ticket] = deal["time"]
                        if deal["time"] > last_check_time:
                            last_check_time = deal["time"]

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

                        # Accumulate for the consolidated Telegram message sent
                        # once after this batch, rather than sending per-deal.
                        grp = tg_events.setdefault(pos_id, {
                            "symbol": deal.get("symbol", "UNKNOWN"),
                            "pnl": 0.0,
                            "commission": 0.0,
                            "swap": 0.0,
                        })
                        grp["pnl"] += net_profit
                        grp["commission"] += deal.get("commission", 0) or 0
                        grp["swap"] += deal.get("swap", 0) or 0

                        # Update circuit breaker state
                        if self.circuit_breaker:
                            cb_symbol = deal.get("symbol", "UNKNOWN")
                            close_time = datetime.fromtimestamp(deal["time"], timezone.utc) if deal.get("time") else None
                            # record_external_close() handles the post-restart case where
                            # active_groups is empty (position_closed() would be a no-op).
                            # [4.8/D10] group_id is a UUID now, not the symbol — record_external_close()
                            # looks the active group up by its stored "symbol" field internally.
                            self.circuit_breaker.record_external_close(cb_symbol, net_profit, close_time)
                        if getattr(self, "prop_firm_validator", None):
                            self.prop_firm_validator.record_trade_closed(deal.get("symbol", "UNKNOWN"), deal.get("volume", 0.0), net_profit)

                    # Send the consolidated Telegram notification(s) for this cycle.
                    if tg_events:
                        try:
                            from backend.services.telegram import telegram_service
                            if len(tg_events) == 1:
                                ev = next(iter(tg_events.values()))
                                emoji = "✅" if ev["pnl"] >= 0 else "❌"
                                sym = telegram_service.escape_markdown(ev["symbol"])
                                pnl_str = telegram_service.escape_markdown(f"{ev['pnl']:+.2f}")
                                tg_msg = (
                                    f"{emoji} *Trade Closed*\n"
                                    f"Symbol: {sym}\n"
                                    f"P&L: ${pnl_str}\n"
                                    f"Commission: ${ev['commission']:.2f} | Swap: ${ev['swap']:.2f}"
                                )
                            else:
                                total_pnl = sum(ev["pnl"] for ev in tg_events.values())
                                emoji = "✅" if total_pnl >= 0 else "❌"
                                lines = [f"{emoji} *{len(tg_events)} Trades Closed*"]
                                for ev in tg_events.values():
                                    sym = telegram_service.escape_markdown(ev["symbol"])
                                    lines.append(f"• {sym}: ${ev['pnl']:+.2f}")
                                lines.append(f"Total P&L: ${total_pnl:+.2f}")
                                tg_msg = "\n".join(lines)
                            asyncio.ensure_future(telegram_service.send_message(tg_msg))
                        except Exception as tg_err:
                            logger.error(f"Failed to send trade-close Telegram: {tg_err}")

                    # Phase-5 pruning: drop entries that can never be revisited
                    # again (their deal time has fallen behind last_check_time)
                    # so this set/state file doesn't grow unbounded over a
                    # long-running bot.
                    processed_deal_tickets = {
                        t: ts for t, ts in processed_deal_tickets.items() if ts >= last_check_time
                    }

                    try:
                        os.makedirs(os.path.dirname(state_file), exist_ok=True)
                        with open(state_file, "w") as f:
                            json.dump({
                                "last_check_time": last_check_time,
                                "processed_deal_tickets": processed_deal_tickets
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
