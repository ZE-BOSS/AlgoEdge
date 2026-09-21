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
from backend.strategies.strategy_defaults import get_slot_tp1_rr_defaults
from backend.strategies.windows import window_bars
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
        # [P2.3] Keyed by slot_id, NOT symbol — see the dedupe check in _scan_loop.
        self._last_signal_time = {}
        # [P1.1] Last resolved gate parameters per slot, exposed on /bot/status so
        # the UI can show what the ENGINE resolved rather than what the form holds.
        self._last_resolved_params: dict[str, dict] = {}
        # [P1.1] Live signal-suppression funnel, mirroring the backtester's
        # rejection_funnel. Every dropped signal gets a named reason, so "the bot
        # isn't trading" is always answerable from the status endpoint.
        self._suppression_funnel: dict[str, int] = {}
        self._suppression_day = None
        self.engine = None
        # [per-slot] One RiskEngine + CircuitBreaker per slot, built from each
        # slot's own profile (risk/slot_book.py). There is no account-wide risk
        # engine any more: a slot's limits gate that slot and nothing else.
        self.slot_book = None
        # [4.2/D1] For personal accounts under sizing_basis="STATIC", the
        # static anchor is the balance first observed after this service
        # started — NOT prop_firm.initial_balance (that field's $10,000
        # default previously caused "Bug 12": lot sizes computed against
        # $10k instead of a real $25k personal account). Cached once per
        # process lifetime; a bot restart re-anchors to the balance at
        # that moment, matching what "static" should mean for an account
        # with no explicit starting-balance concept of its own.
        self._static_personal_balance_anchor: float | None = None
        # Kept as the breaker of the slot most recently evaluated, for status
        # endpoints that want "a" breaker; every decision uses the slot's own.
        self.circuit_breaker = None
        self.prop_firm_validator = None
        # UserConfig.magic_base — the base of the magic-number range that marks
        # an order as this bot's. Refreshed from config on every scan cycle; the
        # default matches config_schema.UserConfig.magic_base.
        self._magic_base: int = 1001
        # The MT5 login currently connected, and the balance last seen on it.
        # Both are surfaced to the frontend so the user can see which account
        # the risk percentage is actually being sized against.
        self.account_id: int | None = None
        self.account_balance: float = 0.0
        self.account_equity: float = 0.0
        self.account_currency: str = ""
        self.account_server: str = ""
        self._bot_tickets_cache: set[int] = set()
        self._bot_tickets_cache_at: float = 0.0

    # ── MT5 account identity, ownership and balance ──────────────────────────

    async def _load_magic_base(self, user_id: str) -> int:
        """Read UserConfig.magic_base from the DB into self._magic_base.

        Ownership is decided by magic number, so anything that reads MT5 state
        needs this set first — see backend/services/trade_ownership.py.
        """
        import json

        from sqlalchemy import select

        from backend.data.database import async_session
        from backend.data.models import UserConfigModel

        try:
            async with async_session() as session:
                row = (
                    await session.execute(
                        select(UserConfigModel).where(UserConfigModel.user_id == user_id)
                    )
                ).scalar_one_or_none()
                if row and row.config_json:
                    cfg = json.loads(row.config_json)
                    self._magic_base = int(cfg.get("magic_base", 1001) or 1001)
        except Exception as e:
            logger.warning(f"[BOT] Could not load magic_base, using {self._magic_base}: {e}")
        try:
            from backend.services.position_manager import position_manager
            position_manager.magic_base = self._magic_base
        except Exception:
            pass
        return self._magic_base

    async def _current_mt5_account(self) -> int | None:
        """The MT5 login now connected, or None if MT5 is unreachable."""
        try:
            import MetaTrader5 as mt5

            from backend.mt5.executor import run_mt5
            info = await run_mt5(mt5.account_info)
            if info is None:
                return None
            self.account_id = int(info.login)
            return self.account_id
        except Exception as e:
            logger.warning(f"[SYNC] Could not read MT5 account: {e}")
            return None

    async def _sync_account_balance(self) -> dict:
        """Read the live balance/equity and hand it to the circuit breaker.

        Two jobs:

          1. Give the frontend a real number. `get_broker_status` only ever
             returned the masked login and server name, so the dashboard could
             not show what balance the risk percentage was being applied to.
          2. Let the circuit breaker distinguish a *balance reset* (new account,
             deposit, withdrawal, demo reset) from a trading loss — see
             CircuitBreaker.note_account_balance().
        """
        try:
            import MetaTrader5 as mt5

            from backend.mt5.executor import run_mt5
            info = await run_mt5(mt5.account_info)
            if info is None:
                return {}
            _new_login = int(info.login)
            if self.account_id is not None and _new_login != self.account_id:
                # The terminal was switched to a different login while this
                # process kept running. STATIC sizing anchors to "the first
                # balance seen this process", so without clearing it here a
                # $700 account would keep being sized against the $10,000
                # account's anchor for the life of the process — a stop/start
                # of the bot does NOT clear it, only a full restart did.
                logger.warning(
                    f"[SYNC] MT5 login changed {self.account_id} -> {_new_login}; "
                    f"clearing the static balance anchor so sizing re-anchors."
                )
                self._log_event(
                    f"MT5 account changed to #{_new_login} — position sizing re-anchored "
                    f"to this account's balance.",
                    "WARNING", "RISK",
                )
                self._static_personal_balance_anchor = None
                self._bot_tickets_cache = set()
                self._bot_tickets_cache_at = 0.0
            self.account_id = _new_login
            self.account_balance = float(info.balance)
            self.account_equity = float(info.equity)
            self.account_currency = getattr(info, "currency", "") or ""
            self.account_server = getattr(info, "server", "") or ""
            if self.slot_book:
                if self.slot_book.note_account_balance(self.account_balance, self.account_id):
                    self._log_event(
                        f"Account balance re-baselined to "
                        f"{self.account_balance:.2f} {self.account_currency} "
                        f"(account {self.account_id}) — this is a balance reset, "
                        f"not a drawdown.",
                        "WARNING", "RISK",
                    )
            return {
                "login": self.account_id,
                "balance": self.account_balance,
                "equity": self.account_equity,
                "currency": self.account_currency,
                "server": self.account_server,
            }
        except Exception as e:
            logger.warning(f"[SYNC] Could not read MT5 balance: {e}")
            return {}

    async def _bot_ticket_set(self, max_age_s: float = 60.0) -> set[int]:
        """Position ids the bot recorded when it opened them.

        The authoritative half of the ownership test in
        backend/services/trade_ownership.py. Cached for `max_age_s` because the
        sync loop runs every 15 seconds and this set only grows when the bot
        itself places an order.
        """
        now = datetime.now(timezone.utc).timestamp()
        if self._bot_tickets_cache and (now - self._bot_tickets_cache_at) < max_age_s:
            return self._bot_tickets_cache
        try:
            from backend.data.database import async_session
            from backend.services.trade_ownership import load_bot_tickets
            async with async_session() as session:
                self._bot_tickets_cache = await load_bot_tickets(session, self.user_id)
                self._bot_tickets_cache_at = now
        except Exception as e:
            logger.warning(f"[SYNC] Could not refresh bot ticket set: {e}")
        return self._bot_tickets_cache

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

    # ── [P1.1] Resolved-parameter transparency ──────────────────────────────
    #
    # Every value below is one that can silently stop the bot trading. They were
    # spread across RiskParams, the strategy's own Params dataclass, and the
    # per-slot overrides, with no single place showing what the engine ended up
    # with — so "live takes a third of the backtest's trades" had no observable
    # cause. `resolve_slot_gate_params` is deliberately the ONLY definition of
    # that list, and `backtest.py` reads the same function for its snapshot, so
    # the live block and the backtest block cannot drift apart.
    # ── [per-slot] the slot book ──────────────────────────────────────────
    # Every slot trades under its own RiskEngine and CircuitBreaker, built from
    # its own profile. No limit is shared, so a slot behaves live exactly as it
    # does in a backtest of that slot — see risk/slot_book.py and
    # implementation/PER-SLOT-RISK-DESIGN-2026-09-19.md.

    def _rebuild_slot_book(self, config, risk_dict: dict) -> None:
        """Point the book at the current account config; slots resolve on use."""
        from backend.risk.slot_book import SlotBook

        if self.slot_book is None:
            self.slot_book = SlotBook(risk_dict)
        else:
            self.slot_book.base_config = dict(risk_dict)

    def _reconcile_slots_with_mt5(self) -> None:
        """Point every slot's open-position count at what MT5 actually holds.

        Runs once per bot start, and only after the slot book exists: a slot's
        saved state can carry positions that were closed while the bot was off
        (a manual close, a stop hit during downtime), and a stale count blocks
        that slot's next entry against its own max_positions_per_symbol.
        """
        if getattr(self, "_slots_reconciled", False) or self.slot_book is None:
            return
        try:
            import MetaTrader5 as mt5
            if not mt5.terminal_info():
                return
            # Bot-owned positions only. Feeding every live position in here made
            # the breaker count somebody else's open trade against
            # max_positions_per_symbol, so a manual position sitting on Crash
            # 1000 silently blocked the bot from ever entering Crash 1000
            # itself. See backend/services/trade_ownership.py.
            from backend.services.trade_ownership import is_bot_position
            _all = list(mt5.positions_get() or [])
            _mine = [p for p in _all if is_bot_position(p, magic_base=self._magic_base)]
            if len(_all) != len(_mine):
                self._log_event(
                    f"Ignoring {len(_all) - len(_mine)} open MT5 position(s) not placed "
                    f"by this bot — they do not count towards position limits.",
                    "INFO", "SYNC",
                )
            open_symbols = [p.symbol for p in _mine]
            self.slot_book.reconcile_from_mt5(open_symbols)
            self._slots_reconciled = True
            self._log_event(
                f"Slot breakers reconciled with MT5: {len(open_symbols)} open position(s) — "
                + (", ".join(sorted(set(open_symbols))) if open_symbols else "none"),
                "INFO", "BOT",
            )
        except Exception as e:
            logger.error(f"Slot/MT5 reconciliation error: {e}")

    def _slot_engine(self, slot, config):
        """This slot's engine, rebuilt in place whenever its settings change."""
        from backend.risk.live_risk_config import build_live_risk_config
        from backend.risk.slot_book import resolve_slot_risk_config, slot_overrides_from

        if self.slot_book is None:
            self._rebuild_slot_book(config, dict(getattr(self, "_live_risk_base", None) or {}))
        base, _ = build_live_risk_config(config, slot.strategy_id)
        base = dict(base)
        base["mt5_account"] = self.account_id
        resolved = resolve_slot_risk_config(
            base, slot.strategy_id,
            overrides=slot_overrides_from(slot),
            use_strategy_exit_defaults=getattr(getattr(config, "risk", None), "use_strategy_exit_defaults", True),
            # One state file per slot: a slot's counters survive a restart
            # without being mixed into another slot's.
            state_file=f"backend/data/cb_state/{slot.slot_id}.json",
        )
        self.slot_book.set_slot_config(slot.slot_id, resolved, symbol=slot.symbol)
        return self.slot_book.engine(slot.slot_id)

    def _resolved_slot_params(self, slot, config, engine) -> dict:
        risk = getattr(config, "risk", None)
        sp = getattr(engine, "params", None)

        def _r(name, default=None):
            return getattr(risk, name, default) if risk is not None else default

        def _s(name, default=None):
            """Strategy-param value, showing the per-slot override if there is one."""
            if sp is None:
                return default
            return getattr(sp, name, default)

        out = {
            "slot_id": slot.slot_id,
            "symbol": slot.symbol,
            "strategy_id": getattr(engine, "strategy_id", slot.strategy_id),
            # Gates that cap how many trades a day is allowed to produce.
            "risk_per_trade_pct": (
                slot.risk_per_trade_pct if slot.risk_per_trade_pct is not None
                else _r("risk_per_trade_pct")
            ),
            "max_risk_hard_cap_pct": _r("max_risk_hard_cap_pct"),
            "max_daily_trades": _r("max_daily_trades"),
            "max_concurrent_positions": _r("max_concurrent_positions"),
            "max_positions_per_symbol": (
                slot.max_positions_per_symbol if slot.max_positions_per_symbol is not None
                else _r("max_positions_per_symbol")
            ),
            "max_daily_drawdown_pct": _r("max_daily_drawdown_pct"),
            "allow_pyramiding": _r("allow_pyramiding"),
            "min_bars_between_entries": _r("min_bars_between_entries"),
            "sizing_basis": _r("sizing_basis"),
            "reject_below_confluence": _r("reject_below_confluence"),
            "confluence_risk_tiers": _r("confluence_risk_tiers"),
            "tp_count": slot.tp_count if slot.tp_count is not None else _r("tp_count"),
            "tp1_rr": slot.tp1_rr if slot.tp1_rr is not None else _r("tp1_rr"),
            # The strategy's OWN daily budget — the one that shipped at 6 trades
            # / 4.0% for every synthetic slot and is invisible in the Risk tab.
            "strategy.max_trades_per_day": (
                slot.max_trades_per_day if slot.max_trades_per_day is not None
                else _s("max_trades_per_day")
            ),
            "strategy.max_daily_risk_pct": _s("max_daily_risk_pct"),
        }
        # Strategy-specific gates worth surfacing, when the engine has them.
        for extra in ("stop_atr_multiple", "spike_k_atr", "revert_k_atr",
                      "breakout_lookback", "require_adx", "min_adx_to_trade",
                      "enable_jump_trades", "trade_jump_entries",
                      "session_filter_enabled"):
            val = _s(extra, "__absent__")
            if val != "__absent__":
                out[f"strategy.{extra}"] = val
        if getattr(slot, "strategy_params_override", None):
            out["slot_overrides"] = dict(slot.strategy_params_override)
        return out

    @staticmethod
    def active_slot_symbols(config) -> list[str]:
        """Symbols with at least one ENABLED strategy slot, in slot order — the
        list Settings → Strategy shows as active and the only list the bot trades."""
        seen: dict[str, None] = {}
        for slot in (getattr(config, "instrument_slots", None) or []):
            if getattr(slot, "enabled", True) and getattr(slot, "symbol", None):
                seen.setdefault(slot.symbol, None)
        return list(seen)

    def _live_feed(self, slot, engine, symbol: str, timeframes: list[str]):
        """The slot's LiveBarFeed (strategies/bar_feed.py), rebuilt with its engine."""
        from backend.strategies.bar_feed import LiveBarFeed

        feeds = self.__dict__.setdefault("_bar_feeds", {})
        feed = feeds.get(slot.slot_id)
        if feed is None or feed.engine is not engine:
            feed = feeds[slot.slot_id] = LiveBarFeed(engine, symbol, timeframes)
        return feed

    def _primary_tf_seconds(self) -> int | None:
        """Seconds in the fastest timeframe any live engine is scanning.

        A scan cycle longer than this steps over closed bars, and the strategy
        never sees the setups on them.
        """
        tf_secs = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800,
                   "H1": 3600, "H4": 14400, "D1": 86400}
        best = None
        for eng in (getattr(self, "engines", None) or {}).values():
            try:
                for tf in eng.get_required_timeframes():
                    s = tf_secs.get(str(tf).upper())
                    if s and (best is None or s < best):
                        best = s
            except Exception:
                continue
        return best

    def _suppressed(self, reason: str, slot_id: str = "") -> None:
        """[P1.1] Count one signal the live path dropped, by named reason.

        The backtester has produced a `rejection_funnel` for every run for
        months; the live path had nothing equivalent, so a signal the bot found
        and then discarded looked exactly like no signal at all. Same shape as
        the backtest funnel so the two can be diffed directly.
        """
        today = datetime.now(timezone.utc).date()
        if self._suppression_day != today:
            self._suppression_day = today
            self._suppression_funnel = {}
        key = f"{reason}|{slot_id}" if slot_id else reason
        self._suppression_funnel[key] = self._suppression_funnel.get(key, 0) + 1

    def _log_resolved_slot_params(self, slot, config, engine) -> None:
        """Emit the resolved gate parameters once per slot per UTC day."""
        today = datetime.now(timezone.utc).date()
        key = (slot.slot_id, today)
        if not hasattr(self, "_resolved_params_logged"):
            self._resolved_params_logged = set()
        if key in self._resolved_params_logged:
            return
        self._resolved_params_logged.add(key)
        try:
            params = self._resolved_slot_params(slot, config, engine)
            self._last_resolved_params[slot.slot_id] = params
            summary = ", ".join(
                f"{k}={v}" for k, v in params.items()
                if k not in ("slot_id", "symbol", "strategy_id")
            )
            self._log_event(
                f"[{slot.symbol}] {params['strategy_id']} resolved parameters — {summary}",
                "INFO", "CONFIG",
            )
            logger.info(f"[LIVE][RESOLVED] {params}")
        except Exception as e:
            logger.warning(f"[P1.1] could not publish resolved slot params: {e}")

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

                from backend.utils.timeutils import detect_session, get_session_close
                _sess_dt = dt_time.replace(tzinfo=timezone.utc)
                _is_synthetic = any(
                    k in (signal_domain.symbol or "").upper()
                    for k in ("BOOM", "CRASH", "VOLATILITY", "STEP", "JUMP", "RANGE BREAK")
                )
                _sess = "24/7" if _is_synthetic else detect_session(_sess_dt)
                _close = None if _is_synthetic else get_session_close(_sess_dt)
                _sess_close = _close.replace(tzinfo=None) if _close else None
                
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
                        # Session tagging. The Signal model has carried a
                        # `session` column since day one and the live path never
                        # wrote it, so every signal in the UI showed a blank
                        # session; `session_close_time` is new alongside it, so
                        # a late entry can be recognised as late.
                        session=_sess,
                        session_close_time=_sess_close,
                        signal_time=dt_time,
                        chart_data=json.dumps(signal_domain.chart_data) if getattr(signal_domain, 'chart_data', None) else None
                    )
                    session.add(sig_db)
                else:
                    sig_db.acted_on = (status == "EXECUTED")
                    if reject_reason:
                        sig_db.skip_reason = reject_reason
                    if not sig_db.session:
                        sig_db.session = _sess
                        sig_db.session_close_time = _sess_close
                
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

        # Load the ownership magic base BEFORE anything reads MT5 state.
        # _scan_loop sets this from config, but start() reconciles the circuit
        # breaker against live positions first — so without this the reconcile
        # (and the position_manager gates it configures) would run against the
        # default base 1001 even for a user who configured a different one.
        await self._load_magic_base(user_id)

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

        # The breakers are reconciled against MT5 once the slot book exists — see
        # _reconcile_slots_with_mt5, called from the scan loop.
        self._slots_reconciled = False

        return {"running": True, "message": "Bot started successfully", "symbols": self.symbols}


    async def stop(self) -> dict[str, Any]:
        """Stop the bot scanning loop."""
        if not self.running:
            return {"running": False, "message": "Bot is already stopped"}

        self.running = False
        self.symbols = []  # a stopped bot scans nothing; the Dashboard then shows the configured slots
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
            # [P1.1] Everything needed to answer "why isn't the bot trading?"
            # without reading the source. `resolved_params` is what each engine
            # actually resolved (diff it against a backtest's params_snapshot);
            # `suppression_funnel` names every signal the live path dropped today;
            # `cycle_seconds` vs `primary_tf_seconds` says whether closed bars are
            # being skipped.
            "resolved_params": self._last_resolved_params,
            "suppression_funnel": dict(self._suppression_funnel),
            "cycle_seconds": getattr(self, "_last_cycle_seconds", None),
            "cycle_overruns": getattr(self, "_cycle_overruns", 0),
            "primary_tf_seconds": self._primary_tf_seconds(),
        }

    def get_logs(self, limit: int = 50) -> dict[str, Any]:
        """Get recent activity log entries."""
        events = list(self._events)[:limit]
        return {"events": events, "total": len(self._events)}

    async def _scan_loop(self, user_id: str):
        """Main scanning loop — runs the SMC strategy on each symbol."""
        import time

        from backend.mt5.data_fetcher import DataFetcher

        self._log_event("Scan loop started — entering main cycle", category="BOT")
        self._cycle_overruns = 0
        self._last_cycle_seconds = None

        while self.running:
            _cycle_started = time.monotonic()
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

                from backend.risk.prop_firm_validator import PropFirmValidator
                from backend.strategies.registry import get_strategy

                # Bug 11 fix: a settings change must reach a RUNNING bot. Each
                # slot's engine is now rebuilt in place when that slot's resolved
                # config changes (SlotBook.set_slot_config), keeping the live
                # counters — so editing a limit takes effect on the next scan.
                risk_dict = config.risk.to_dict() if hasattr(config.risk, 'to_dict') else vars(config.risk)
                # The magic-number base that marks an order as ours — used by
                # the ownership filter so the bot never books somebody else's
                # trades. See backend/services/trade_ownership.py.
                self._magic_base = int(getattr(config, "magic_base", 1001) or 1001)
                try:
                    from backend.services.position_manager import position_manager
                    position_manager.magic_base = self._magic_base
                except Exception:
                    pass
                # Tag the circuit breaker with the connected MT5 login so its
                # persisted daily/weekly P&L cannot leak across accounts.
                risk_dict = dict(risk_dict)
                risk_dict["mt5_account"] = await self._current_mt5_account()
                # [per-slot] Every slot's own engine and breaker, rebuilt in
                # place when that slot's settings change (live counters carried
                # over by SlotBook.set_slot_config).
                self._rebuild_slot_book(config, risk_dict)
                # First scan after a start: correct each slot's open-position counts.
                self._reconcile_slots_with_mt5()
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

                # Settings is the source of truth, re-read every cycle: every
                # ENABLED slot is scanned — one enabled while the bot runs is
                # picked up next cycle, one disabled is dropped. A default APA_v1
                # slot is synthesised only when the config has no slots at all.
                # It used to be created for ANY started symbol without a slot, so
                # a stale symbol list from the Dashboard traded APA on markets the
                # user never selected while their real slots went unscanned.
                effective_slots = []
                if getattr(config, 'instrument_slots', None):
                    for _matching in _slots_by_symbol.values():
                        effective_slots.extend(_matching)
                    self.symbols = list(_slots_by_symbol)
                else:
                    for _sym in self.symbols:
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

                        # A saved slot can outlive its strategy — nine were removed
                        # on 2026-09-11. Without this, get_strategy() raises into the
                        # per-slot handler below, which reports it as a "data fetch
                        # error" on every scan cycle. Say what it is, once, and skip.
                        from backend.strategies.registry import list_strategies
                        if strategy_id not in list_strategies():
                            _warned = self.__dict__.setdefault("_missing_strategy_warned", set())
                            if slot.slot_id not in _warned:
                                _warned.add(slot.slot_id)
                                self._log_event(
                                    f"[{symbol}] slot {slot.slot_id}: strategy {strategy_id} no longer "
                                    f"exists — slot skipped. Remove it or choose another strategy in Settings.",
                                    "WARN", "BOT",
                                )
                            continue

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
                            # Measured per-symbol parameters first, the slot's own
                            # override on top. SYNTH_SLOT_PARAMS was dead code —
                            # get_synth_slot_params() had no callers on either the
                            # live or the backtest path — so every synthetic slot
                            # ran the generic SynthParams defaults rather than the
                            # values the parameter search selected for that symbol.
                            # backtest.py::apply_strategy_params does the same, so
                            # the two paths configure the engine identically.
                            _slot_measured = {}
                            try:
                                from backend.strategies.strategy_defaults import get_synth_slot_params
                                if getattr(slot, "use_measured_params", True) is not False:
                                    _slot_measured = get_synth_slot_params(symbol, strategy_id)
                            except Exception as _e:
                                logger.warning(f"[LIVE] per-symbol synth params not loaded: {_e}")
                            _override = getattr(slot, "strategy_params_override", None) or {}
                            if _slot_measured:
                                import copy as _copy
                                new_engine.params = _copy.deepcopy(new_engine.params)
                                _land = {}
                                for _k, _v in _slot_measured.items():
                                    if _k in _override:
                                        continue  # the slot's own setting wins
                                    if hasattr(new_engine.params, _k):
                                        setattr(new_engine.params, _k, _v)
                                        _land[_k] = _v
                                if _land:
                                    self._log_event(
                                        f"[{symbol}] {strategy_id}: applied measured per-symbol "
                                        f"params {_land}", category="RISK",
                                    )

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
                                    _blk_name = {"APA_v1": "apa", "VWAP_v1": "vwap"}.get(strategy_id)
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

                        # [P1.1] Once per slot per UTC day, publish the parameters
                        # the ENGINE actually resolved — not what the Settings form
                        # is holding. "Does the backend ignore what I set in the
                        # UI?" was previously unanswerable without reading code,
                        # and a UI control whose value never reaches the engine is
                        # indistinguishable from one that does. This is the live
                        # half of the backtest's `params_snapshot`; diffing the two
                        # is the standing parity check.
                        self._log_resolved_slot_params(slot, config, current_engine)

                        # Get required timeframes for the strategy
                        req_tfs = current_engine.get_required_timeframes() if hasattr(current_engine, 'get_required_timeframes') else ["H4", "M15", "M5"]
                        
                        fetched_data = {}
                        has_missing_data = False
                        for tf in req_tfs:
                            tf_data = await DataFetcher.get_historical_data(
                                symbol, tf, count=self._live_feed(slot, current_engine, symbol, req_tfs).fetch_count(tf))
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
                            # ── The backtest's bar sequence, live ─────────────────────────
                            # [2026-09-15] Every closed bar reaches the engine exactly once, in
                            # order, through the same feeder the backtest routes use. This loop
                            # used to call on_bar for every timeframe on every scan (re-feeding
                            # bars the engine had already processed) and skipped any bar that
                            # closed during a slow scan or while the bot was offline, so engines
                            # that count bars (HTF FVG, Bias IFVG, APA, DriftJumpAlpha,
                            # BoomDriftJump, VWAP) drifted away from their backtests.
                            from backend.strategies.bar_feed import LiveBarFeed
                            _feeds = self.__dict__.setdefault("_bar_feeds", {})
                            _feed = _feeds.get(slot.slot_id)
                            if _feed is None or _feed.engine is not current_engine:
                                _feed = _feeds[slot.slot_id] = LiveBarFeed(current_engine, symbol, req_tfs)
                            if hasattr(current_engine, 'context'):
                                current_engine.context["news_blocked"] = self.news_filter.is_blocked(symbol) if hasattr(self, 'news_filter') else False
                            _fed = await _feed.advance({tf: _index_candles(fetched_data[tf]).sort_index() for tf in req_tfs})
                            if _fed.history_gap:
                                self._log_event(
                                    f"[{symbol}] {strategy_id}: offline longer than the fetched history — "
                                    f"rebuilding the engine and warming it up again", "WARN", "BOT")
                                self.engines.pop(slot.slot_id, None)
                                _feeds.pop(slot.slot_id, None)
                                continue
                            if _fed.primed:
                                self._log_event(
                                    f"[{symbol}] {strategy_id}: warmed up on {_fed.stepped} {_feed.primary} bars "
                                    f"(the same warm-up a backtest uses)", category="BOT")
                            for _bar_time, _missed in _fed.missed:
                                self._suppressed("missed_bar_signal", slot.slot_id)
                                self._log_event(
                                    f"[{symbol}] {strategy_id}: {_missed.direction} signal on the bar opening "
                                    f"{pd.Timestamp(_bar_time)} was only seen late (bot offline or a slow scan) — "
                                    f"not traded; the backtest would have taken it", "WARN", "SIGNAL")
                            signal = _fed.signal
                                    
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
                                #
                                # [P2.3] Keyed by SLOT, not by symbol. With two
                                # slots on one symbol a symbol key gives them one
                                # shared cell, and it fails in both directions:
                                # slot B's fingerprint overwrites slot A's (so
                                # slot A's next re-scan of a bar it already traded
                                # is no longer deduped — a duplicate entry), and
                                # two slots emitting the same (entry, SL,
                                # direction) on one bar — entirely possible for
                                # SpikeFade and RangeRevert at the same
                                # stop_atr_multiple — silently drop the second.
                                _sig_fp = (str(_feed.last_time), round(signal.entry_price, 5), round(signal.stop_loss, 5), signal.direction)
                                _dedupe_key = slot.slot_id
                                if _sig_fp == self._last_signal_time.get(_dedupe_key):
                                    self._suppressed("duplicate_signal_same_bar", slot.slot_id)
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
                                    self._last_signal_time[_dedupe_key] = _sig_fp  # Prevent re-evaluation on next scan
                                    await self._save_signal_state(signal, "SKIPPED", reason_str)
                                    self._suppressed("strategy_gate", slot.slot_id)
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
                                _slot_engine = self._slot_engine(slot, config)
                                _slot_circuit = _slot_engine.circuit
                                self.circuit_breaker = _slot_circuit  # for status endpoints
                                if _slot_circuit:
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
                                    cb_ok, cb_reason = _slot_circuit.check_symbol(
                                        signal.symbol,
                                        slot_id=slot.slot_id,
                                        slot_max_positions=_slot_max_positions,
                                        slot_max_losses_per_day=slot.max_losses_per_day,
                                    )
                                    if not cb_ok:
                                        self._log_event(f"[REJECTED] Circuit breaker blocked {signal.symbol}: {cb_reason}", "SIGNAL", "RISK")
                                        await self._save_signal_state(signal, "SKIPPED", cb_reason)
                                        self._suppressed(f"circuit_breaker:{cb_reason}", slot.slot_id)
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
                                    self._suppressed("news_filter", slot.slot_id)
                                    continue

                                # === Execute trade via RiskEngine ===
                                try:
                                    from backend.brokers.factory import broker_factory
                                    broker = broker_factory.get_broker()

                                    # Refresh the account snapshot before sizing.
                                    # `broker.account_info` was assigned ONLY at
                                    # connect time and get_live_account_info() had
                                    # no callers anywhere in the bot, so every
                                    # position was sized against the balance as it
                                    # stood when the process last connected. After
                                    # switching MT5 logins that meant sizing a
                                    # $700 account with the $10,000 account's
                                    # balance — over-risking by 14x.
                                    try:
                                        if hasattr(broker, "get_live_account_info"):
                                            await broker.get_live_account_info()
                                    except Exception as _acc_err:
                                        logger.warning(f"Could not refresh MT5 account info: {_acc_err}")

                                    if not broker.account_info:
                                        # This used to `continue` with nothing but a
                                        # logger.warning: no activity-log entry, no
                                        # journal row, no Telegram. A signal the bot
                                        # found and then dropped looked identical to
                                        # no signal at all, which is why "the bot
                                        # isn't trading" came with no alert.
                                        _acc_msg = "No MT5 account info available for risk sizing — broker not connected"
                                        self._log_event(f"[REJECTED] {_acc_msg} — {signal.symbol}", "SIGNAL", "RISK")
                                        self._last_signal_time[_dedupe_key] = _sig_fp
                                        await self._save_signal_state(signal, "SKIPPED", _acc_msg)
                                        self._suppressed("no_account_info", slot.slot_id)
                                        continue
                                    account_balance = broker.account_info.balance

                                    # The entry and the live management of this trade (position_manager ->
                                    # risk/exit_replay.py) both run under this one config, including the
                                    # strategy's measured exits — see risk/live_risk_config.py.
                                    from backend.risk.live_risk_config import build_live_risk_config
                                    risk_config, _applied = build_live_risk_config(config, strategy_id)
                                    # The engine was built from this slot's profile above; the
                                    # measured-defaults log below is the only thing risk_config
                                    # is still read for here.
                                    if _applied and strategy_id not in self.__dict__.setdefault("_logged_sdefaults", set()):
                                        from backend.strategies.strategy_defaults import get_strategy_evidence
                                        self._logged_sdefaults.add(strategy_id)
                                        self._log_event(
                                            f"[{symbol}] {strategy_id}: applied measured exit defaults "
                                            f"{_applied} — {get_strategy_evidence(strategy_id)}",
                                            category="RISK",
                                        )

                                    # Cache RiskEngine across scan cycles — only rebuild when key settings change.
                                    # MultiTPManager/BreakevenManager/TrailingManager are expensive to construct;
                                    # risk_config dict is still built fresh each signal to pick up config edits.
                                    # [3.16/D4] Was a 3-field tuple (risk_per_trade_pct, tp_count, min_rr) — any
                                    # OTHER field changing (e.g. margin cap, confluence tiers, be/trail settings)
                                    # was silently invisible to the cache, so RiskEngine kept running with a
                                    # stale config until one of those 3 fields also happened to change. A hash
                                    # of the full serialised dict catches every field.
                                    # Prop-firm limits belong to the account, so one validator
                                    # is shared by every slot's engine.
                                    _slot_engine.prop_firm_validator = getattr(self, "prop_firm_validator", None)

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
                                        # [P1.4] MISSING until now, and it silently
                                        # made live risk MORE than every backtest.
                                        # RiskEngine.evaluate_signal reads
                                        # `confluence_score` top-level (falling back
                                        # to metadata) and scales risk_pct down the
                                        # tier ladder; the backtest route puts it on
                                        # the signal dict, the live path did not, and
                                        # the strategies keep it as a TradeSignal
                                        # FIELD rather than a metadata key — so live
                                        # resolved None and skipped the scaling
                                        # entirely. Measured on SpikeFade (score 70,
                                        # tier 65 -> 75% of base): backtest sized at
                                        # 1.35% while live sized at the full 1.8%,
                                        # a third more risk per trade than any
                                        # backtest ever reported.
                                        "confluence_score": getattr(signal, "confluence_score", None),
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
                                    _stated = getattr(config.risk, "sizing_static_balance", None)
                                    if _stated:
                                        # The user stated the capital to size against, so neither
                                        # the firm's figure nor a restart-dependent anchor applies.
                                        _static_balance = float(_stated)
                                    elif _account_mode == "prop_firm":
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

                                    approved, reason, tp_levels = _slot_engine.evaluate_signal(
                                        signal_data,
                                        account_balance,
                                        current_time=datetime.now(timezone.utc),  # explicit time
                                        initial_balance=_sizing_base_balance,
                                    )

                                    if approved:
                                        group_id = signal_data.get("group_id", "unknown")
                                        if _slot_circuit:
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
                                            _slot_circuit.position_opened(
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
                                                        # Was the literal 1001, not self._magic_base —
                                                        # so a user who changed magic_base placed orders
                                                        # outside the range trade_ownership.py uses to
                                                        # recognise them, and the bot stopped seeing its
                                                        # own positions.
                                                        magic=self._magic_base + (tp.level * 10),
                                                        # [P2.7] Slot stamped into the comment. With two
                                                        # slots on one symbol, symbol alone can no longer
                                                        # say which strategy owns a position — and
                                                        # position_manager would happily trail slot A's
                                                        # trade with slot B's ATR multiplier. MT5 caps the
                                                        # comment at 31 chars; this is 15.
                                                        comment=f"AE_TP{tp.level}_{slot.slot_id[:8]}",
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
                                            if _slot_circuit:
                                                _slot_circuit.rollback_position(group_id)
                                            # Detect if ALL failures were due to stale signal
                                            all_stale = tp_failure_details and all("Stale Signal" in d for d in tp_failure_details)
                                            if all_stale:
                                                self._log_event(f"Signal skipped (stale): price moved past SL before execution for {symbol}", "INFO", "TRADE")
                                                self._last_signal_time[_dedupe_key] = _sig_fp
                                                await self._save_signal_state(signal, "SKIPPED", "Stale signal — price moved past SL before execution", tp_levels=tp_levels)
                                            else:
                                                self._log_event(f"All orders failed. Rolled back risk state for {group_id}.", "WARN", "RISK")
                                                # Build detailed failure reason for Telegram (includes MT5 error codes)
                                                fail_reason = "MT5 execution failed: " + " | ".join(tp_failure_details) if tp_failure_details else "MT5 execution failed for all TP levels"
                                                self._last_signal_time[_dedupe_key] = _sig_fp  # Prevent same signal from re-firing
                                                await self._save_signal_state(signal, "FAILED", fail_reason, tp_levels=tp_levels)
                                            had_execution_failure = True
                                        elif len(db_positions) < len(tp_levels):
                                            _slot_circuit.active_groups[group_id]["sub_trades"] = len(db_positions)
                                    
                                        if db_positions and self.user_id:
                                            sig_id = await self._save_signal_state(signal, "EXECUTED", tp_levels=db_positions)
                                            self._last_signal_time[_dedupe_key] = _sig_fp
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
                                                
                                                from backend.utils.timeutils import (
                                                    detect_session,
                                                    get_session_close,
                                                )
                                                _now_utc = datetime.now(timezone.utc)
                                                _t_is_synth = any(
                                                    k in (signal.symbol or "").upper()
                                                    for k in ("BOOM", "CRASH", "VOLATILITY",
                                                              "STEP", "JUMP", "RANGE BREAK")
                                                )
                                                _t_sess = "24/7" if _t_is_synth else detect_session(_now_utc)
                                                _t_close = None if _t_is_synth else get_session_close(_now_utc)
                                                _t_sess_close = _t_close.replace(tzinfo=None) if _t_close else None
                                                # Planned R:R measured to the FURTHEST take-profit, which is
                                                # the target the trade is actually aiming at once the nearer
                                                # legs have banked.
                                                _risk_dist = abs(signal.entry_price - signal.stop_loss)
                                                _planned_rr = None
                                                if _risk_dist > 0 and db_positions:
                                                    _far_tp = db_positions[-1]["tp_price"]
                                                    if _far_tp:
                                                        _planned_rr = abs(_far_tp - signal.entry_price) / _risk_dist

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
                                                        # Journal completeness: the session the trade was
                                                        # opened in, when that session closes, and the
                                                        # PLANNED reward-to-risk. All three columns existed
                                                        # and only the backtester ever filled them, so the
                                                        # live journal showed blanks.
                                                        session=_t_sess,
                                                        session_close_time=_t_sess_close,
                                                        risk_reward=_planned_rr,
                                                        take_profit=(db_positions[-1]["tp_price"] if db_positions else None),
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
                                                            status="OPEN",
                                                            planned_rr=(
                                                                abs(p["tp_price"] - signal.entry_price) / _risk_dist
                                                                if _risk_dist > 0 and p["tp_price"] else None
                                                            ),
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
                                        self._last_signal_time[_dedupe_key] = _sig_fp
                                        self._suppressed(f"risk_engine:{reason}", slot.slot_id)
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
                    # [P1.1] Cycle timing. The other candidate cause of "live takes
                    # a third of the backtest's trades" is a scan cycle longer than
                    # the strategy's primary timeframe: this loop fetches 5,000 bars
                    # per slot per timeframe with MT5 serialised onto one thread, so
                    # a slow cycle silently steps over closed bars and the strategy
                    # never sees them. That was invisible — it produces no error,
                    # no gap, just fewer trades. Now it is loud.
                    _cycle_secs = time.monotonic() - _cycle_started
                    self._last_cycle_seconds = round(_cycle_secs, 2)
                    _budget = self._primary_tf_seconds()
                    if _budget and _cycle_secs >= _budget:
                        self._cycle_overruns += 1
                        self._log_event(
                            f"Scan cycle took {_cycle_secs:.1f}s, which is at or beyond the "
                            f"{_budget}s bar it is supposed to track — closed bars are being "
                            f"skipped and signals on them will never be seen. "
                            f"({self._cycle_overruns} overruns this run)",
                            "WARN", "BOT",
                        )
                    self._log_event(
                        f"Scan cycle complete in {_cycle_secs:.1f}s — {len(self.symbols)} symbols "
                        f"checked — next scan in {self.scan_interval}s",
                        category="BOT"
                    )
                    # Wake ~2 s after the next bar of the fastest timeframe opens, so an
                    # entry lands near that bar's open — the price a backtest fills at —
                    # instead of up to a whole scan interval later. Never sleeps longer
                    # than scan_interval.
                    _sleep = float(self.scan_interval)
                    if _budget:
                        _sleep = max(1.0, min(_sleep, _budget - (time.time() % _budget) + 2.0))
                    await asyncio.sleep(_sleep)

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
        
        # The MT5 login this loop's state belongs to. Sync state written for a
        # different account must never be reused: `last_check_time` would open
        # a window into an account whose history we have never seen, and every
        # deal in that window would be booked as ours.
        current_account = await self._current_mt5_account()

        # Default when there is no usable state: start from NOW, not three days
        # ago. Reaching back 3 days on a fresh login is what made the bot ingest
        # a brand-new account's pre-existing history and book months of somebody
        # else's losses as today's realised P&L, tripping max-daily-drawdown on
        # an account that had not lost a cent under this bot.
        last_check_time = datetime.now(timezone.utc).timestamp()
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
                saved_account = data.get("mt5_account")
                account_matches = (
                    current_account is not None
                    and saved_account is not None
                    and int(saved_account) == int(current_account)
                )
                if not account_matches:
                    logger.warning(
                        f"[SYNC] bot_sync_state.json was written for MT5 account "
                        f"{saved_account}, connected account is {current_account} - "
                        f"ignoring it and syncing from now onwards only."
                    )
                    self._log_event(
                        f"New MT5 account detected ({current_account}) - previous sync "
                        f"state discarded. Pre-existing account history will NOT be "
                        f"counted as this bot's trades.",
                        "WARNING", "SYNC",
                    )
                    data = {}
                if data:
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
                
                # Ownership filter. Only deals this bot actually placed reach
                # the P&L accumulator, the journal and the circuit breaker -
                # everything else in the account's history is left alone.
                # See backend/services/trade_ownership.py.
                _known = await self._bot_ticket_set()
                deals = await OrderManager.get_closed_positions_since(
                    last_check_time,
                    bot_only=True,
                    known_tickets=_known,
                    magic_base=self._magic_base,
                )

                # Feed the live balance to the circuit breaker so a balance
                # reset / deposit / withdrawal re-baselines the drawdown
                # denominators instead of reading as a loss.
                await self._sync_account_balance()

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

                        # Update the OWNING slot's breaker — see SlotBook.route_close.
                        if self.slot_book:
                            cb_symbol = deal.get("symbol", "UNKNOWN")
                            close_time = datetime.fromtimestamp(deal["time"], timezone.utc) if deal.get("time") else None
                            # record_external_close() handles the post-restart case where
                            # active_groups is empty (position_closed() would be a no-op).
                            # [4.8/D10] group_id is a UUID now, not the symbol — record_external_close()
                            # looks the active group up by its stored "symbol" field internally.
                            self.slot_book.route_close(cb_symbol, net_profit, close_time)
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
                                # Tag the state with the account it describes, so a
                                # later run against a different login discards it
                                # instead of replaying that login's window.
                                "mt5_account": current_account,
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
