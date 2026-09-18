"""
backend/services/position_manager.py

Actively manages open MT5 positions. Runs in a frequent loop (e.g. every 3s) to:
1. Reconcile DB open positions with live MT5 positions.
2. Apply Breakeven rules (move SL to entry + buffer when price hits trigger).
3. Apply Trailing Stop Loss rules (ATR or Structure based) if requested.
"""

import asyncio
from datetime import datetime

import MetaTrader5 as mt5
from sqlalchemy import select

from backend.api.websocket import manager as ws_manager
from backend.risk.position_sizer import get_pip_size
from backend.services.trade_ownership import is_bot_deal, is_bot_position
from backend.utils.logger import get_logger

logger = get_logger(__name__)

class PositionManager:
    def __init__(self):
        self.running = False
        self._task = None
        self.pending_adoptions = {}
        self._ghost_strike_counts: dict = {}  # ticket -> consecutive empty-history poll count
        self._notified_closes: set = set()    # tickets already notified, prevents duplicate telegrams
        self.GHOST_GRACE_POLLS = 10           # require 10 consecutive polls (~200s) before marking ghost
        # parent trade id -> open time of the last closed bar its exits were replayed on
        self._last_exit_bar: dict = {}
        self._primary_tf_by_strategy: dict = {}
        self._exit_risk_engines: dict = {}
        # UserConfig.magic_base — set by bot_service so the ownership gates
        # below use the user's configured magic range, not just the default.
        self.magic_base: int = 1001

    def start(self, user_id: str):
        if not self.running:
            self.running = True
            self._task = asyncio.create_task(self._management_loop(user_id))
            logger.info("PositionManager started")

    def stop(self):
        self.running = False
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        logger.info("PositionManager stopped")

    async def _management_loop(self, user_id: str):
        while self.running:
            try:
                await self._manage_positions(user_id)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"PositionManager loop error: {e}")
                await asyncio.sleep(5)
            finally:
                await asyncio.sleep(20)

    async def _manage_positions(self, user_id: str):
        if not mt5.terminal_info():
            return

        from backend.core.config_schema import UserConfigV2
        from backend.data.database import async_session
        from backend.data.models import Trade, TradePosition, UserConfigModel
        
        async with async_session() as session:
            # 1. Fetch user config for risk settings
            result = await session.execute(select(UserConfigModel).where(UserConfigModel.user_id == user_id))
            config_db = result.scalars().first()
            if not config_db or not config_db.config_json:
                return
            import json
            config = UserConfigV2.from_dict(json.loads(config_db.config_json))
            risk = config.risk

            # 2. Fetch open positions from DB
            # Fetch ALL OPEN positions and any position created in the last 24 hours (for robust sync)
            from datetime import timedelta
            last_24h = datetime.utcnow() - timedelta(hours=24)
            db_positions_query = await session.execute(
                select(TradePosition).where(
                    TradePosition.user_id == user_id,
                    ((TradePosition.status == "OPEN") | (TradePosition.created_at >= last_24h))
                )
            )
            db_positions = list(db_positions_query.scalars().all())
            db_tickets = {p.mt5_ticket: p for p in db_positions}

            # Build a COMPLETE set of all known tickets for this user (no time limit).
            # This prevents Ghost Sync and God Sync from re-creating trades that
            # already exist in the DB but fell outside the 24h window above.
            all_tickets_q = await session.execute(
                select(TradePosition.mt5_ticket).where(TradePosition.user_id == user_id)
            )
            all_known_tickets = {row[0] for row in all_tickets_q.all() if row[0] is not None}

            # Group by parent trade for Breakeven Cascade
            trades_map = {}
            for p in db_positions:
                trades_map.setdefault(p.parent_trade_id, []).append(p)

            # parent_trade_id -> symbol, so the exit handler can size pips
            # without an extra query per closed leg.
            trades_map_symbol: dict = {}
            if trades_map:
                _sym_rows = await session.execute(
                    select(Trade.id, Trade.symbol).where(Trade.id.in_(list(trades_map.keys())))
                )
                trades_map_symbol = {tid: sym for tid, sym in _sym_rows.all()}

            # The account balance right now, stamped onto every trade that closes
            # this cycle as `balance_after`. Read once per loop, not per trade.
            account_balance_now = None
            try:
                _acc = mt5.account_info()
                if _acc is not None:
                    account_balance_now = float(_acc.balance)
            except Exception:
                pass

            # 3. Fetch all open positions from MT5
            mt5_positions = mt5.positions_get()
            mt5_tickets = {p.ticket: p for p in mt5_positions} if mt5_positions else {}

            modifications_made = False

            # --- ORPHANED PARENT TRADE CLEANUP ---
            # Fetch all OPEN Trades to ensure no dashboard stuck trades
            open_trades_res = await session.execute(select(Trade).where(Trade.user_id == user_id, Trade.status == "OPEN"))
            for t in open_trades_res.scalars().all():
                pos_res = await session.execute(select(TradePosition).where(TradePosition.parent_trade_id == t.id))
                siblings = pos_res.scalars().all()
                if not siblings:
                    # Parent trade with no positions? Force close it.
                    t.status = "CLOSED"
                    t.exit_time = datetime.utcnow()
                    t.exit_reason = "NO_POSITIONS"
                    modifications_made = True
                elif all(s.status in ("CLOSED", "RECONCILE_FAILED") for s in siblings):
                    t.status = "CLOSED"
                    t.exit_time = max((s.exit_time for s in siblings if s.exit_time), default=datetime.utcnow())
                    t.exit_price = siblings[-1].exit_price if siblings[-1].exit_price else t.entry_price
                    t.exit_reason = "CLIENT"
                    t.pnl = sum(s.pnl for s in siblings if getattr(s, 'pnl', None) is not None)
                    modifications_made = True

            # Clean up pending adoptions that are now in DB or no longer in MT5
            self.pending_adoptions = {
                t: time for t, time in self.pending_adoptions.items() 
                if t in mt5_tickets and t not in db_tickets
            }

            # --- GOD SYNC (Adopt Missing MT5 Positions) ---
            current_time = datetime.utcnow().timestamp()
            unrecorded_live_tickets = []
            for ticket, live_pos in mt5_tickets.items():
                if ticket not in all_known_tickets:
                    # God Sync DB Check
                    existing_q = await session.execute(select(TradePosition).where(TradePosition.mt5_ticket == ticket))
                    existing_pos = existing_q.scalars().first()
                    if existing_pos:
                        db_tickets[ticket] = existing_pos
                        continue
                        
                    # Delay adoption by 15s to avoid race condition with bot execution
                    if ticket not in self.pending_adoptions:
                        self.pending_adoptions[ticket] = current_time
                        continue
                    if current_time - self.pending_adoptions[ticket] < 15:
                        continue
                        
                    # OWNERSHIP GATE. "God Sync" adopts an MT5 position that
                    # is not in our DB. Without this check it adopted EVERY
                    # such position — including manual trades and positions
                    # that were already open on the account before this bot
                    # ever logged in — writing them into the journal as
                    # strategy_id="MANUAL" and feeding their P&L into the
                    # drawdown counters. Only positions carrying the bot's own
                    # magic number are ours to adopt.
                    # See backend/services/trade_ownership.py.
                    if not is_bot_position(live_pos, magic_base=self.magic_base):
                        logger.debug(
                            f"God Sync: ignoring position {ticket} on {live_pos.symbol} "
                            f"(magic {getattr(live_pos, 'magic', 0)}) — not placed by this bot."
                        )
                        self.pending_adoptions.pop(ticket, None)
                        continue

                    unrecorded_live_tickets.append(live_pos)
            
            if unrecorded_live_tickets:
                # Group by time and symbol to merge TP levels
                god_groups = {}
                for lp in unrecorded_live_tickets:
                    # Time might be slightly off by a second, group by rounded time
                    group_key = (lp.symbol, round(lp.time / 5) * 5)
                    god_groups.setdefault(group_key, []).append(lp)
                
                for key, lps in god_groups.items():
                    first_lp = lps[0]
                    is_buy = (first_lp.type == mt5.POSITION_TYPE_BUY)
                    new_trade = Trade(
                        user_id=user_id,
                        strategy_id="MANUAL",
                        symbol=first_lp.symbol,
                        direction="BUY" if is_buy else "SELL",
                        entry_price=first_lp.price_open,
                        stop_loss=first_lp.sl,
                        take_profit=first_lp.tp,
                        volume=sum(lp.volume for lp in lps),
                        status="OPEN",
                        entry_time=datetime.utcfromtimestamp(first_lp.time)
                    )
                    session.add(new_trade)
                    await session.flush() # Get trade.id
                    
                    for i, lp in enumerate(lps):
                        new_pos = TradePosition(
                            parent_trade_id=new_trade.id,
                            user_id=user_id,
                            tp_level=i+1,
                            mt5_ticket=lp.ticket,
                            volume=lp.volume,
                            entry_price=lp.price_open,
                            stop_loss=lp.sl,
                            take_profit=lp.tp,
                            status="OPEN"
                        )
                        session.add(new_pos)
                        db_tickets[lp.ticket] = new_pos
                        db_positions.append(new_pos)
                        trades_map.setdefault(new_trade.id, []).append(new_pos)
                        logger.info(f"God Sync: Adopted ghost/manual trade {lp.ticket} as TP{i+1}")
                    modifications_made = True

            # --- HISTORICAL GHOST SYNC (Offline Trades) ---
            from datetime import timedelta
            sync_start_time = datetime.utcnow() - timedelta(days=14)
            deals = mt5.history_deals_get(sync_start_time, datetime.utcnow())
            if deals:
                deals_by_pos = {}
                for d in deals:
                    deals_by_pos.setdefault(d.position_id, []).append(d)
                
                unrecorded_closed_positions = []
                for pos_id, pos_deals in deals_by_pos.items():
                    if not pos_id or pos_id == 0: continue
                    in_deals = [d for d in pos_deals if d.entry == mt5.DEAL_ENTRY_IN]
                    out_deals = [d for d in pos_deals if d.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_INOUT)]
                    
                    # OWNERSHIP GATE — same reasoning as God Sync above, for
                    # CLOSED positions. This block reaches 14 days back into
                    # the account's deal history; on a freshly connected
                    # account that history belongs to somebody else, and
                    # importing it as MANUAL_OFFLINE trades is what put months
                    # of unrelated losses into the journal.
                    if in_deals and not is_bot_deal(in_deals[0], magic_base=self.magic_base):
                        continue

                    if in_deals and out_deals and pos_id not in all_known_tickets:
                        existing_q = await session.execute(select(TradePosition).where(TradePosition.mt5_ticket == pos_id))
                        existing_pos = existing_q.scalars().first()
                        if existing_pos:
                            db_tickets[pos_id] = existing_pos
                            continue
                            
                        # Profit includes swap and commission for exact correlation with MT5
                        total_profit = sum(d.profit + getattr(d, 'commission', 0.0) + getattr(d, 'swap', 0.0) + getattr(d, 'fee', 0.0) for d in out_deals)
                        
                        unrecorded_closed_positions.append({
                            "pos_id": pos_id,
                            "first_in": in_deals[0],
                            "last_out": out_deals[-1],
                            "pnl": total_profit
                        })
                
                if unrecorded_closed_positions:
                    ghost_groups = {}
                    for ucp in unrecorded_closed_positions:
                        first_in = ucp["first_in"]
                        group_key = (first_in.symbol, round(first_in.time / 5) * 5)
                        ghost_groups.setdefault(group_key, []).append(ucp)
                    
                    for key, ucps in ghost_groups.items():
                        first_in = ucps[0]["first_in"]
                        last_out = max([u["last_out"] for u in ucps], key=lambda d: d.time)
                        is_buy = (first_in.type == mt5.DEAL_TYPE_BUY)
                        total_trade_pnl = sum(u["pnl"] for u in ucps)
                        
                        new_trade = Trade(
                            user_id=user_id,
                            strategy_id="MANUAL_OFFLINE",
                            symbol=first_in.symbol,
                            direction="BUY" if is_buy else "SELL",
                            entry_price=first_in.price,
                            stop_loss=0.0,
                            take_profit=0.0,
                            volume=sum(u["first_in"].volume for u in ucps),
                            status="CLOSED",
                            pnl=total_trade_pnl,
                            entry_time=datetime.utcfromtimestamp(first_in.time),
                            exit_time=datetime.utcfromtimestamp(last_out.time),
                            exit_price=last_out.price,
                            exit_reason="CLIENT"
                        )
                        session.add(new_trade)
                        await session.flush()
                        
                        for i, ucp in enumerate(ucps):
                            new_pos = TradePosition(
                                parent_trade_id=new_trade.id,
                                user_id=user_id,
                                tp_level=i+1,
                                mt5_ticket=ucp["pos_id"],
                                volume=ucp["first_in"].volume,
                                entry_price=ucp["first_in"].price,
                                stop_loss=0.0,
                                take_profit=0.0,
                                status="CLOSED",
                                pnl=ucp["pnl"],
                                exit_price=ucp["last_out"].price,
                                exit_time=datetime.utcfromtimestamp(ucp["last_out"].time)
                            )
                            session.add(new_pos)
                            db_tickets[ucp["pos_id"]] = new_pos
                            logger.info(f"Historical Ghost Sync: Recovered offline trade {ucp['pos_id']} as TP{i+1}")
                        modifications_made = True

            # --- EXIT HANDLER ---
            for pos in db_positions:
                # Skip positions that are already fully closed — no work needed
                if pos.status != "OPEN":
                    continue
                if pos.mt5_ticket not in mt5_tickets:
                    # Position is closed in MT5, but OPEN in DB
                    deals = mt5.history_deals_get(position=pos.mt5_ticket)
                    if deals:
                        # Item 3.8: sum ALL OUT/INOUT deals for this position instead
                        # of only the most recent one (deals[-1]) — a position closed
                        # in 2+ parts (partial close, then final close) has multiple
                        # OUT deals, and using only the last one dropped the realized
                        # PnL from every earlier partial close. Matches the correct
                        # pattern already used elsewhere in this file (see the
                        # HISTORICAL GHOST SYNC and partial-close-detection blocks
                        # above/below).
                        out_deals = [d for d in deals if d.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_INOUT)]
                        if out_deals:
                            exit_deal = max(out_deals, key=lambda d: d.time)  # most recent, for exit price/reason
                            net_profit = sum(
                                d.profit + getattr(d, 'commission', 0.0) + getattr(d, 'swap', 0.0) + getattr(d, 'fee', 0.0)
                                for d in out_deals
                            )
                        else:
                            # No OUT-type deal found (unexpected) — fall back to the
                            # last deal for exit price/reason only.
                            exit_deal = deals[-1]
                            net_profit = exit_deal.profit + getattr(exit_deal, 'commission', 0.0) + getattr(exit_deal, 'swap', 0.0) + getattr(exit_deal, 'fee', 0.0)

                        reason = "CLOSED"
                        if exit_deal.reason == mt5.DEAL_REASON_SL:
                            reason = "TRAIL" if pos.be_applied else "SL"
                        elif exit_deal.reason == mt5.DEAL_REASON_TP:
                            reason = f"TP{pos.tp_level}" if pos.tp_level else "TP"

                        if pos.status != "CLOSED" or pos.pnl != net_profit:
                            pos.status = "CLOSED"
                            pos.pnl = net_profit
                            pos.exit_price = exit_deal.price
                            # The BROKER's exit timestamp, not the moment this
                            # 20-second poll happened to notice. `datetime.utcnow()`
                            # here is why the journal's close time could be minutes
                            # off the actual fill.
                            pos.exit_time = datetime.utcfromtimestamp(exit_deal.time)

                            # Journal completeness. `exit_reason` and `pnl_pips` are
                            # new columns; `realized_rr` existed and was never
                            # written by the live path, so the journal showed the
                            # planned R:R with no way to see what was achieved.
                            pos.exit_reason = reason
                            try:
                                _sym = trades_map_symbol.get(pos.parent_trade_id) or ""
                                _pip = get_pip_size(_sym) if _sym else 0.0
                                if _pip and pos.entry_price and pos.exit_price:
                                    _dir_sign = 1.0 if (pos.take_profit or 0) >= (pos.entry_price or 0) else -1.0
                                    pos.pnl_pips = ((pos.exit_price - pos.entry_price) / _pip) * _dir_sign
                                _risk = abs((pos.entry_price or 0) - (pos.stop_loss or 0))
                                if _risk > 0 and pos.exit_price is not None:
                                    _dir_sign = 1.0 if (pos.take_profit or 0) >= (pos.entry_price or 0) else -1.0
                                    pos.realized_rr = ((pos.exit_price - pos.entry_price) * _dir_sign) / _risk
                            except Exception as _e:
                                logger.debug(f"Could not compute exit metrics for {pos.mt5_ticket}: {_e}")

                            modifications_made = True

                        # ALWAYs check if parent trade needs closing, even if was_open was False (fixes UI stuck trades)
                        result2 = await session.execute(
                            select(TradePosition).where(TradePosition.parent_trade_id == pos.parent_trade_id)
                        )
                        siblings = result2.scalars().all()
                        trade = await session.get(Trade, pos.parent_trade_id)
                        if trade:
                            # Update realized P&L immediately
                            trade.pnl = sum(s.pnl for s in siblings if getattr(s, 'pnl', None) is not None)
                            
                            if all(s.status in ("CLOSED", "RECONCILE_FAILED") for s in siblings):
                                if trade.status != "CLOSED":
                                    trade.status = "CLOSED"
                                    trade.exit_time = max(
                                        (sb.exit_time for sb in siblings if sb.exit_time),
                                        default=pos.exit_time,
                                    )
                                    # The reason the LAST leg closed for, rather
                                    # than a hardcoded "CLIENT" — the journal
                                    # labelled every bot exit a manual close.
                                    _last_leg = max(
                                        (sb for sb in siblings if sb.exit_time),
                                        key=lambda sb: sb.exit_time,
                                        default=pos,
                                    )
                                    trade.exit_reason = (
                                        getattr(_last_leg, "exit_reason", None)
                                        or getattr(trade, "exit_reason", None)
                                        or "CLIENT"
                                    )
                                    trade.exit_price = (
                                        _last_leg.exit_price
                                        if _last_leg.exit_price else trade.entry_price
                                    )

                                    # balance_after / pnl_pips / achieved R:R.
                                    # All three columns are read by the journal UI
                                    # and only the backtester ever wrote them, so
                                    # the live journal showed balance_before with
                                    # nothing after it, and no pip or R result.
                                    try:
                                        if account_balance_now is not None:
                                            trade.balance_after = account_balance_now
                                        elif trade.balance_before is not None:
                                            trade.balance_after = trade.balance_before + (trade.pnl or 0.0)
                                        _pips = [sb.pnl_pips for sb in siblings if sb.pnl_pips is not None]
                                        if _pips:
                                            # Volume-weighted so a 3-leg trade reports the
                                            # pip result of the position, not of one leg.
                                            _vols = [sb.volume or 0.0 for sb in siblings if sb.pnl_pips is not None]
                                            _tv = sum(_vols)
                                            trade.pnl_pips = (
                                                sum(p * v for p, v in zip(_pips, _vols)) / _tv
                                                if _tv > 0 else sum(_pips) / len(_pips)
                                            )
                                        _risk_amt = None
                                        _rrs = [sb.realized_rr for sb in siblings if sb.realized_rr is not None]
                                        if _rrs:
                                            _vols = [sb.volume or 0.0 for sb in siblings if sb.realized_rr is not None]
                                            _tv = sum(_vols)
                                            trade.risk_reward = (
                                                sum(r * v for r, v in zip(_rrs, _vols)) / _tv
                                                if _tv > 0 else sum(_rrs) / len(_rrs)
                                            )
                                    except Exception as _e:
                                        logger.debug(f"Could not finalise trade metrics for {trade.id}: {_e}")
                                    
                                    # Chart data was attached at signal creation time and does not
                                    # need to be refreshed on every close. Fetching M5 candles
                                    # here was a blocking MT5 network call on every trade close
                                    # inside the 20-second management loop (Bug 14), adding
                                    # latency and potential timeouts. Removed.
                                    pass

                        # Send Telegram notification for the closed position (inside deals branch so net_profit is defined)
                        if pos.mt5_ticket not in self._notified_closes:
                            self._notified_closes.add(pos.mt5_ticket)
                            try:
                                import asyncio

                                from backend.services.telegram import telegram_service
                                emoji = "✅" if net_profit >= 0 else "❌"
                                reason_str = reason if 'reason' in locals() else "CLOSED"
                                if reason_str == "SL": reason_str = "Stop Loss Hit"
                                elif reason_str == "TRAIL": reason_str = "Trailing Stop Hit"
                                elif reason_str.startswith("TP"): reason_str = "Take Profit Hit"
                                elif reason_str == "CLIENT": reason_str = "Manual Close"
                                msg = f"{emoji} *{reason_str}*\nSymbol: {trade.symbol if trade else 'Unknown'}\nTicket: {pos.mt5_ticket}\nExit Price: {exit_deal.price}\nP&L: ${net_profit:.2f}"
                                asyncio.create_task(telegram_service.send_message(msg))
                            except Exception as tg_err:
                                logger.warning(f"Telegram notification failed: {tg_err}")
                    else:
                        # Position missing from MT5 and no history found.
                        # Grace period: require N consecutive polls before marking as ghost.
                        # This prevents data loss when MT5 history hasn't synced yet (e.g. after restart).
                        strikes = self._ghost_strike_counts.get(pos.mt5_ticket, 0) + 1
                        self._ghost_strike_counts[pos.mt5_ticket] = strikes
                        
                        # Also try secondary signal: order history
                        orders = mt5.history_orders_get(position=pos.mt5_ticket)
                        if orders and strikes < self.GHOST_GRACE_POLLS:
                            # Order history exists — this is NOT a ghost, just deal history lag
                            logger.info(f"Position {pos.mt5_ticket} has order history but no deal history yet. Waiting for sync (strike {strikes}/{self.GHOST_GRACE_POLLS}).")
                            continue
                        
                        if strikes < self.GHOST_GRACE_POLLS:
                            logger.info(f"Position {pos.mt5_ticket} missing from MT5 with no history. Strike {strikes}/{self.GHOST_GRACE_POLLS} — waiting for grace period.")
                            continue
                        
                        # Grace period exhausted — soft-mark, never hard-delete
                        logger.warning(f"Position {pos.mt5_ticket} confirmed ghost after {strikes} polls. Marking RECONCILE_FAILED (NOT deleting).")
                        pos.status = "RECONCILE_FAILED"
                        pos.exit_time = datetime.utcnow()
                        modifications_made = True
                        
                        # Clean up ghost counter
                        self._ghost_strike_counts.pop(pos.mt5_ticket, None)
                        
                        # Check if parent trade needs status update
                        parent_id = pos.parent_trade_id
                        if parent_id in trades_map:
                            trades_map[parent_id] = [p for p in trades_map[parent_id] if p.id != pos.id]
                        
                        trade_query = await session.execute(select(Trade).where(Trade.id == parent_id))
                        parent_trade = trade_query.scalars().first()
                        
                        if parent_trade:
                            siblings = trades_map.get(parent_id, [])
                            if not siblings:
                                # All siblings are ghosts — mark parent as failed too
                                parent_trade.status = "RECONCILE_FAILED"
                                parent_trade.exit_time = datetime.utcnow()
                                parent_trade.exit_reason = "GHOST_UNRESOLVED"
                            else:
                                # Recalculate if remaining siblings are closed
                                all_done = all(
                                    sib.mt5_ticket not in mt5_tickets
                                    for sib in siblings
                                )
                                if all_done and parent_trade.status != "CLOSED":
                                    total_pnl = sum(s.pnl for s in siblings if s.pnl is not None)
                                    parent_trade.status = "CLOSED"
                                    parent_trade.pnl = total_pnl
                                    parent_trade.exit_time = datetime.utcnow()
                                    parent_trade.exit_reason = "PARTIAL_GHOST"
                                    parent_trade.exit_price = parent_trade.entry_price
                        continue

            # --- LIVE MANAGEMENT (Trailing & Standard BE & Manual Sync) ---
            for pos in db_positions:
                if pos.status == "CLOSED" or pos.mt5_ticket not in mt5_tickets:
                    continue

                live_pos = mt5_tickets[pos.mt5_ticket]
                
                # Check for partial close (volume decreased but position is still open)
                if live_pos.volume < pos.volume:
                    deals = mt5.history_deals_get(position=pos.mt5_ticket)
                    if deals:
                        out_deals = [d for d in deals if d.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_INOUT)]
                        realized_profit = sum(d.profit for d in out_deals)
                        
                        pos.volume = live_pos.volume
                        pos.pnl = realized_profit
                        modifications_made = True
                        logger.info(f"Partial close detected for {pos.mt5_ticket}: new volume {live_pos.volume}, realized pnl ${realized_profit:.2f}")
                        
                        tq = await session.execute(select(TradePosition).where(TradePosition.parent_trade_id == pos.parent_trade_id))
                        siblings = tq.scalars().all()
                        pt_q = await session.execute(select(Trade).where(Trade.id == pos.parent_trade_id))
                        parent_trade = pt_q.scalars().first()
                        if parent_trade:
                            parent_trade.pnl = sum(s.pnl for s in siblings if getattr(s, 'pnl', None) is not None)

                symbol = live_pos.symbol
                current_price = live_pos.price_current
                entry_price = live_pos.price_open
                current_sl = live_pos.sl
                current_tp = live_pos.tp
                is_buy = (live_pos.type == mt5.POSITION_TYPE_BUY)
                
                # Sync manual MT5 modifications to DB
                if current_sl != pos.stop_loss or current_tp != pos.take_profit:
                    pos.stop_loss = current_sl
                    pos.take_profit = current_tp
                    # Note: We do not update parent_trade SL/TP here
                    modifications_made = True
                
                pip_size_val = get_pip_size(symbol)

                # --- VWAP mandatory ET hard-close (spec: no VWAP position may run past its configured cutoff) ---
                if await self._check_vwap_hard_close(pos, live_pos, session, config):
                    continue

                # --- ORB session close (part of the tested rule, not an add-on) ---
                if await self._check_orb_session_close(pos, live_pos, session, config):
                    continue

                # --- Strategy-owned exits (classic families, VWAP/APA session modes) ---
                if await self._check_strategy_position_exit(pos, live_pos, session, config):
                    continue

            # --- Break-even / trailing: exactly as a backtest of each trade applies them ---
            # One pass per newly closed bar through risk/exit_replay.py (the code both
            # backtest engines run), under the trade's own strategy exits. This replaced
            # a per-tick implementation that read the global risk settings, trailed from
            # the tick price and computed its own ATR and swings.
            try:
                if await self._apply_backtest_exits(session, config, trades_map, mt5_tickets):
                    modifications_made = True
            except Exception as e:
                logger.error(f"[EXITS] backtest-parity exit management failed: {e}")

            if modifications_made:
                await session.commit()
                await ws_manager.broadcast_all({"type": "trade_update"})

            # Broadcast live positions to frontend for real-time dashboard ticking
            if mt5_tickets:
                live_data = []
                for ticket, live_pos in mt5_tickets.items():
                    live_data.append({
                        "ticket": live_pos.ticket,
                        "symbol": live_pos.symbol,
                        "type": "BUY" if live_pos.type == mt5.POSITION_TYPE_BUY else "SELL",
                        "volume": live_pos.volume,
                        "price_open": live_pos.price_open,
                        "price_current": live_pos.price_current,
                        "sl": live_pos.sl,
                        "tp": live_pos.tp,
                        "profit": live_pos.profit,
                        "time": live_pos.time,
                    })
                await ws_manager.broadcast_all({
                    "type": "live_mt5_positions",
                    "data": live_data
                })

    async def _apply_backtest_exits(self, session, config, trades_map, mt5_tickets) -> bool:
        """Move each bot trade's stops to where a backtest of that trade holds them.

        For every trade group with an open leg, once per newly closed bar of the
        strategy's primary timeframe: replay the closed bars since entry through
        risk/exit_replay.replay_stops — highest/lowest, RiskEngine.manage_open_position
        with the shared ATR and swing points, the TP1 break-even cascade — under the
        risk config the entry was placed with (risk/live_risk_config.py, strategy
        exits included), then tighten any leg whose stop is behind the replay.
        Stateless: a restart or a skipped pass lands on the same stops.
        Returns True if a stop or a flag changed."""
        import hashlib
        import json
        import time

        import pandas as pd

        from backend.backtester.engine import resolve_effective_costs
        from backend.data.models import Trade
        from backend.mt5.data_fetcher import DataFetcher
        from backend.risk.engine import RiskEngine
        from backend.risk.exit_replay import LegState, replay_stops
        from backend.risk.live_risk_config import build_live_risk_config
        from backend.strategies.bar_feed import TF_MINUTES, primary_timeframe
        from backend.strategies.registry import get_strategy, list_strategies

        changed = False
        for parent_id, legs in trades_map.items():
            alive = [lp for lp in legs if lp.mt5_ticket in mt5_tickets]
            if not alive:
                self._last_exit_bar.pop(parent_id, None)
                continue
            trade = await session.get(Trade, parent_id)
            if trade is None or not trade.entry_time or not trade.stop_loss or not trade.entry_price:
                continue
            if trade.strategy_id not in list_strategies():
                continue  # manual / adopted trades have no backtest to follow

            tf = self._primary_tf_by_strategy.get(trade.strategy_id)
            if tf is None:
                try:
                    tf = primary_timeframe(get_strategy(trade.strategy_id)(config).get_required_timeframes())
                except Exception:
                    tf = "M5"
                self._primary_tf_by_strategy[trade.strategy_id] = tf
            tf_sec = TF_MINUTES.get(tf, 5) * 60
            entry_ts = int(pd.Timestamp(trade.entry_time).timestamp())  # stored naive UTC
            entry_bar = entry_ts - entry_ts % tf_sec
            count = int(min(5000, max(80, (int(time.time()) - entry_bar) // tf_sec + 60)))
            df = await DataFetcher.get_historical_data(trade.symbol, tf, count=count)
            if df is None or len(df) < 3:
                continue
            df = df.iloc[:-1]  # the last row is still forming
            if "time" in df.columns:
                times = pd.to_numeric(df["time"]).to_numpy(dtype="int64")
            else:
                times = pd.DatetimeIndex(df.index).as_unit("s").asi8
            last_closed = int(times[-1])
            if last_closed <= entry_bar or self._last_exit_bar.get(parent_id) == last_closed:
                continue

            risk_config, applied = build_live_risk_config(config, trade.strategy_id)
            fp = hashlib.sha256(json.dumps(risk_config, sort_keys=True, default=str).encode()).hexdigest()
            engine = self._exit_risk_engines.get(fp)
            if engine is None:
                engine = self._exit_risk_engines[fp] = RiskEngine(risk_config)
                # Once per distinct exit config: say which Settings values this
                # strategy's measured exits replaced, so "break-even never fires"
                # is answerable from the Logs page instead of from the code.
                exit_keys = {k: v for k, v in applied.items()
                             if k.startswith(("be_", "trail_")) or k in ("tp_count", "tp1_rr")}
                if exit_keys:
                    logger.info(f"[EXITS] {trade.strategy_id} runs its measured exits "
                                f"({', '.join(f'{k}={v}' for k, v in sorted(exit_keys.items()))}); your Settings "
                                f"values for these are not used. Turn off 'Use each strategy's measured exits' "
                                f"in Settings -> Risk to apply yours.")

            is_buy = str(trade.direction).upper() in ("BUY", "BULLISH", "LONG")
            base = mt5_tickets[min(alive, key=lambda lp: lp.tp_level or 1).mt5_ticket]
            entry_px = float(base.price_open)
            dist = float(trade.entry_price) - float(trade.stop_loss)  # order_manager keeps it across the fill
            states = []
            for lp in legs:
                live = mt5_tickets.get(lp.mt5_ticket)
                closed_at, by_target = None, False
                if live is None:
                    if lp.exit_time is None:
                        continue  # closed but not reconciled yet — the next pass picks it up
                    ts = int(pd.Timestamp(lp.exit_time).timestamp())
                    closed_at = ts - ts % tf_sec
                    by_target = str(lp.exit_reason or "").upper().startswith("TP")
                states.append(LegState(level=int(lp.tp_level or 1), stop_loss=entry_px - dist,
                                       closed_at=closed_at, closed_by_target=by_target))
            costs = resolve_effective_costs(trade.symbol, risk_config)
            replayed = replay_stops(
                direction="BUY" if is_buy else "SELL", entry_price=entry_px, initial_stop=entry_px - dist,
                legs=states, times=times, high=df["high"].to_numpy(dtype=float),
                low=df["low"].to_numpy(dtype=float), close=df["close"].to_numpy(dtype=float),
                entry_bar_time=entry_bar, risk_config=risk_config, symbol=trade.symbol,
                spread_pips=float(costs.get("spread_pips") or 0.0), risk_engine=engine,
            )
            self._last_exit_bar[parent_id] = last_closed

            info = mt5.symbol_info(trade.symbol)
            point = float(getattr(info, "point", 0.0) or 0.0)
            tick = mt5.symbol_info_tick(trade.symbol)
            for lp in alive:
                st = replayed.get(int(lp.tp_level or 1))
                if st is None:
                    continue
                live = mt5_tickets[lp.mt5_ticket]
                target = st.stop_loss + (float(live.price_open) - entry_px)
                current = float(live.sl or 0.0)
                tighter = (target > current + point / 2) if is_buy else (current == 0.0 or target < current - point / 2)
                if tighter:
                    beyond = tick is not None and ((is_buy and target >= tick.bid) or (not is_buy and target <= tick.ask))
                    if beyond:
                        # The backtest's stop for the next bar is already through the
                        # market, so it fills at that bar's open — close now.
                        from backend.mt5.order_manager import OrderManager
                        if await OrderManager.close_position(lp.mt5_ticket):
                            logger.info(f"[EXITS] {lp.mt5_ticket} ({trade.symbol}) closed: replayed stop "
                                        f"{target} is through the market, as the backtest would fill it")
                            changed = True
                        continue
                    if await self._modify_sl(lp.mt5_ticket, trade.symbol, target):
                        lp.stop_loss = target
                        changed = True
                        logger.info(f"[EXITS] {lp.mt5_ticket} ({trade.symbol}) stop -> {target} "
                                    f"({'break-even' if st.be_applied else ''}{' trail' if st.trail_applied else ''})")
                if st.be_applied and not lp.be_applied:
                    lp.be_applied = True
                    changed = True
                if st.trail_applied and not getattr(lp, "trail_activated", False):
                    lp.trail_activated = True
                    changed = True
        return changed


    async def _check_vwap_hard_close(self, pos, live_pos, session, config) -> bool:
        """
        VWAP strategy mandatory ET hard-close (spec: strategy_vwap must not carry a
        position past its configured `hard_close` ET cutoff, e.g. 15:55, to avoid
        overnight gap risk). This was previously enforced only in the backtester
        (backtester/engine.py's hard_close_time check on signal metadata) — live
        positions ran unmanaged through the cutoff.

        There is no per-position metadata store for live trades (Trade/TradePosition
        have no metadata column), so this reads the hard-close time from the user's
        current VWAP config instead — the same value strategy_vwap/engine.py stamps
        into signal metadata (`self.params.hard_close`) — and identifies VWAP
        positions via the parent Trade's strategy_id. Force-closes via
        OrderManager.close_position, the same mechanism used for manual/API closes.

        Returns True if the position was force-closed this tick (caller should skip
        further management of it, since it's no longer open).
        """
        try:
            from backend.data.models import Trade

            vwap_params = getattr(config, "vwap", None)
            hard_close_str = getattr(vwap_params, "hard_close", None) if vwap_params else None
            if not hard_close_str:
                return False

            trade = await session.get(Trade, pos.parent_trade_id)
            if not trade or trade.strategy_id != "VWAP_v1":
                return False

            import pytz
            now_et = datetime.utcnow().replace(tzinfo=pytz.UTC).astimezone(pytz.timezone("America/New_York"))
            time_str = now_et.strftime("%H:%M")
            if time_str < hard_close_str:
                return False

            from backend.mt5.order_manager import OrderManager
            success = await OrderManager.close_position(live_pos.ticket)
            if success:
                logger.info(
                    f"[VWAP] Mandatory hard-close enforced: ticket {live_pos.ticket} "
                    f"({live_pos.symbol}) flattened at {time_str} ET (cutoff {hard_close_str})"
                )
            else:
                logger.warning(f"[VWAP] Hard-close attempt failed for ticket {live_pos.ticket} — will retry next tick")
            return success
        except Exception as e:
            logger.error(f"Error enforcing VWAP hard close for ticket {getattr(live_pos, 'ticket', '?')}: {e}")
            return False

    async def _check_strategy_position_exit(self, pos, live_pos, session, config) -> bool:
        """
        Live half of BaseStrategy.on_position_bar for strategies whose measured
        rule includes its own exit — the backtester calls that hook every bar,
        and without this a live position would only ever leave at SL/TP:

          * classic families: 3xATR trail (MODIFY_SL, tightening only), channel,
            mean and flip exits, maximum holding period;
          * VWAP_v1 / APA_v1 session modes: flat at the session close.

        A strategy opts in with a truthy LIVE_POSITION_EXITS. The engine is built
        from the live config with the same per-symbol table and slot override
        bot_service applies. Returns True if the position was closed.
        """
        try:
            import dataclasses

            import pandas as pd

            from backend.data.models import Trade
            from backend.strategies.registry import get_strategy, list_strategies

            trade = await session.get(Trade, pos.parent_trade_id)
            if not trade or trade.strategy_id not in list_strategies():
                return False
            symbol = live_pos.symbol
            engine = get_strategy(trade.strategy_id)(config)
            params = getattr(engine, "params", None)
            if dataclasses.is_dataclass(params):
                from backend.strategies.strategy_defaults import get_synth_slot_params
                overrides = {}
                _slot = next((s for s in getattr(config, "instrument_slots", None) or []
                              if s.symbol == symbol and s.strategy_id == trade.strategy_id), None)
                if _slot is None or getattr(_slot, "use_measured_params", True) is not False:
                    overrides = get_synth_slot_params(symbol, trade.strategy_id)
                if _slot is not None:
                    overrides.update(getattr(_slot, "strategy_params_override", None) or {})
                engine.params = dataclasses.replace(params)
                for k, v in overrides.items():
                    if hasattr(engine.params, k):
                        setattr(engine.params, k, v)
            if not getattr(engine, "LIVE_POSITION_EXITS", False):
                return False

            from backend.mt5.data_fetcher import DataFetcher
            from backend.mt5.order_manager import OrderManager

            tf = getattr(engine, "TIMEFRAME", None) or engine.get_required_timeframes()[-1]
            count = max(60, int(getattr(engine, "POSITION_BAR_WINDOW", 50) or 50) + 5)
            df = await DataFetcher.get_historical_data(symbol, tf, count=count)
            if df is None or len(df) < 3:
                return False
            closed = df.iloc[:-1]
            closed = closed.set_index(pd.to_datetime(closed["time"], unit="s")) if "time" in closed.columns else closed
            is_buy = live_pos.type == 0
            position = {"ticket": live_pos.ticket, "direction": "BUY" if is_buy else "SELL",
                        "entry_time": trade.entry_time or datetime.utcfromtimestamp(live_pos.time),
                        "stop_loss": live_pos.sl}
            act = engine.on_position_bar(symbol, tf, closed, position)
            if act is None:
                return False
            if act.action == "CLOSE":
                ok = await OrderManager.close_position(live_pos.ticket)
                if ok:
                    logger.info(f"[{trade.strategy_id}] {act.close_reason}: ticket {live_pos.ticket} ({symbol}) closed")
                else:
                    logger.warning(f"[{trade.strategy_id}] exit for ticket {live_pos.ticket} failed — will retry next cycle")
                return ok
            if act.action == "MODIFY_SL" and act.new_sl:
                cur = float(live_pos.sl or 0.0)
                new = float(act.new_sl)
                tighter = (is_buy and (cur == 0.0 or new > cur)) or (not is_buy and (cur == 0.0 or new < cur))
                beyond_market = (is_buy and new >= live_pos.price_current) or (not is_buy and new <= live_pos.price_current)
                if tighter and not beyond_market:
                    await self._modify_sl(live_pos.ticket, symbol, new)
            return False
        except Exception as e:
            logger.error(f"Error in strategy exit for ticket {getattr(live_pos, 'ticket', '?')}: {e}")
            return False

    async def _check_orb_session_close(self, pos, live_pos, session, config) -> bool:
        """
        ORB_v1 flattens at the close of the session it entered in. The backtester
        applies that through ORBStrategy.on_position_bar; live positions are managed
        here instead, so without this a live ORB trade would be held overnight — and
        holding past the close was measured to turn BTCUSD's and XAUUSD's
        profitable periods flat.

        The session is resolved the way the live engine is configured: the global
        ORB block, then the measured per-symbol table, then the slot's own override.
        Returns True if the position was closed this cycle.
        """
        try:
            import calendar
            import time as _time

            from backend.data.models import Trade

            orb = getattr(config, "orb", None)
            if orb is None or not getattr(orb, "close_at_session_end", True):
                return False
            trade = await session.get(Trade, pos.parent_trade_id)
            if not trade or trade.strategy_id != "ORB_v1":
                return False

            from backend.strategies.strategy_defaults import get_synth_slot_params
            from backend.strategies.strategy_orb.engine import SESSIONS, session_bounds

            symbol = live_pos.symbol
            sess = getattr(orb, "session", "london")
            _slot = next((s for s in getattr(config, "instrument_slots", None) or []
                          if s.symbol == symbol and s.strategy_id == "ORB_v1"), None)
            if _slot is None or getattr(_slot, "use_measured_params", True) is not False:
                sess = get_synth_slot_params(symbol, "ORB_v1").get("session", sess)
            if _slot is not None:
                sess = (getattr(_slot, "strategy_params_override", None) or {}).get("session", sess)
            if sess not in SESSIONS:
                return False

            entered = trade.entry_time or datetime.utcfromtimestamp(live_pos.time)
            _, close_ts = session_bounds(sess, calendar.timegm(entered.utctimetuple()))
            if _time.time() < close_ts:
                return False

            from backend.mt5.order_manager import OrderManager
            success = await OrderManager.close_position(live_pos.ticket)
            if success:
                logger.info(f"[ORB] Session close: ticket {live_pos.ticket} ({symbol}) flattened after the {sess} close")
            else:
                logger.warning(f"[ORB] Session-close attempt failed for ticket {live_pos.ticket} — will retry next cycle")
            return success
        except Exception as e:
            logger.error(f"Error enforcing ORB session close for ticket {getattr(live_pos, 'ticket', '?')}: {e}")
            return False

    async def _modify_sl(self, ticket: int, symbol: str, new_sl: float) -> bool:
        from backend.mt5.order_manager import OrderManager
        try:
            success = await OrderManager.modify_sl(ticket, new_sl)
            if not success:
                # Downgrade to warning — SL modify failures are usually transient
                # ("Invalid stops" = price hasn't moved far enough yet, retried next tick)
                logger.warning(f"Failed to modify SL for {ticket} to {new_sl} — will retry next tick")
                return False
            return True
        except Exception as e:
            logger.error(f"Error modifying SL for {ticket}: {e}")
            return False

position_manager = PositionManager()
