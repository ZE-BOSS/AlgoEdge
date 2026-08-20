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
        self._cached_atr = {}
        self._cached_structure = {}

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

                        if pos.status != "CLOSED" or pos.pnl != net_profit:
                            pos.status = "CLOSED"
                            pos.pnl = net_profit
                            pos.exit_price = exit_deal.price
                            if not pos.exit_time:
                                pos.exit_time = datetime.utcnow()
                            
                            reason = "CLOSED"
                            if exit_deal.reason == mt5.DEAL_REASON_SL:
                                reason = "TRAIL" if pos.be_applied else "SL"
                            elif exit_deal.reason == mt5.DEAL_REASON_TP:
                                reason = f"TP{pos.tp_level}" if pos.tp_level else "TP"
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
                                    trade.exit_time = pos.exit_time
                                    trade.exit_reason = getattr(trade, 'exit_reason', "CLIENT")
                                    if trade.exit_reason is None:
                                        trade.exit_reason = "CLIENT"
                                    
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

                # --- 1. BREAKEVEN LOGIC ---
                if hasattr(risk, 'be_trigger_rr') and risk.be_trigger_rr > 0 and not pos.be_applied:
                    pips_in_profit = (current_price - entry_price) / pip_size_val if is_buy else (entry_price - current_price) / pip_size_val

                    # Phase-5 fix: use the TRUE original SL from the parent Trade as
                    # the risk basis, not pos.stop_loss (which may have already been
                    # modified by a prior BE/trailing move) — matches the adjacent
                    # trailing-stop code's pattern below.
                    original_sl = 0.0
                    tq_be = await session.execute(select(Trade).where(Trade.id == pos.parent_trade_id))
                    pt_be = tq_be.scalars().first()
                    if pt_be:
                        original_sl = pt_be.stop_loss
                    if original_sl == 0.0:
                        risk_pips = 20.0 # fallback
                    else:
                        risk_pips = abs(entry_price - original_sl) / pip_size_val
                    
                    if risk_pips > 0:
                        current_rr = pips_in_profit / risk_pips
                        if current_rr >= risk.be_trigger_rr:
                            be_atr_mult = getattr(risk, 'be_buffer_atr_mult', 0.0)
                            be_pips = getattr(risk, 'be_buffer_pips', 2.0)
                            atr_val = await self._get_or_fetch_atr(symbol, pip_size_val)
                            be_buffer = max(be_atr_mult * atr_val, be_pips * pip_size_val)
                            
                            # Ensure BE buffer covers spread + commission to prevent net loss
                            sym_info = mt5.symbol_info(symbol)
                            tick = mt5.symbol_info_tick(symbol)
                            
                            real_spread = 0.0
                            if tick and tick.ask > tick.bid:
                                real_spread = tick.ask - tick.bid
                            elif sym_info and sym_info.spread > 0:
                                real_spread = sym_info.spread * sym_info.point
                            
                            if real_spread <= 0:
                                real_spread = 2.0 * pip_size_val # fallback 2 pips
                                
                            min_spread_buffer = real_spread * 2.0 # 2x spread for safety against commission/slippage
                            be_buffer = max(be_buffer, min_spread_buffer)
                                
                            new_sl = entry_price + be_buffer if is_buy else entry_price - be_buffer
                            
                            move_sl = False
                            if is_buy and new_sl > current_sl or not is_buy and (current_sl == 0.0 or new_sl < current_sl):
                                move_sl = True
                                
                            if move_sl:
                                success = await self._modify_sl(live_pos.ticket, symbol, new_sl)
                                if success:
                                    modifications_made = True
                                    pos.stop_loss = new_sl
                                    pos.be_applied = True
                                    logger.info(f"Moved SL to Breakeven: {live_pos.ticket} -> {new_sl}")
                                continue  # Skip trailing SL this tick — BE was attempted (success or fail)

                # --- 2. TRAILING SL LOGIC ---
                tp_level = getattr(pos, 'tp_level', 1) if pos else 1
                trail_method = getattr(risk, f'trail_method_tp{tp_level}', 'NONE')
                if trail_method != "NONE":
                    if pos and getattr(pos, 'trail_activated', False):
                        current_rr = 999.0
                        activation_rr = 0.0
                    else:
                        original_sl = 0.0
                        if pos:
                            tq = await session.execute(select(Trade).where(Trade.id == pos.parent_trade_id))
                            pt = tq.scalars().first()
                            if pt: original_sl = pt.stop_loss
                            
                        if original_sl != 0.0:
                            risk_pips = abs(entry_price - original_sl) / pip_size_val
                        else:
                            risk_pips = 20.0
                            
                        pips_in_profit = (current_price - entry_price) / pip_size_val if is_buy else (entry_price - current_price) / pip_size_val
                        current_rr = pips_in_profit / risk_pips if risk_pips > 0 else 0
                        activation_rr = getattr(risk, 'trail_activation_rr', 1.0)
                        
                    if current_rr >= activation_rr:
                        new_trail_sl = await self._calculate_trailing_sl(symbol, is_buy, current_price, entry_price, current_sl, risk, trail_method, tp_level)
                        if new_trail_sl is not None:
                            move_sl = False
                            if is_buy and new_trail_sl > current_sl or not is_buy and (current_sl == 0.0 or new_trail_sl < current_sl):
                                move_sl = True
                                
                            if move_sl:
                                success = await self._modify_sl(live_pos.ticket, symbol, new_trail_sl)
                                if success:
                                    modifications_made = True
                                    pos.stop_loss = new_trail_sl
                                    logger.info(f"Trailed SL: {live_pos.ticket} -> {new_trail_sl}")
                                    
                                    if not getattr(pos, 'trail_activated', False):
                                        pos.trail_activated = True

            # --- BREAKEVEN CASCADE CHECK ---
            # Trigger off the LOWEST tp_level sub-position that has closed, not hardcoded TP1.
            # This handles partial fills where TP1's order failed but TP2/TP3 succeeded.
            for parent_id, positions in trades_map.items():
                sorted_positions = sorted(positions, key=lambda p: p.tp_level)
                trigger_pos = None
                for sp in sorted_positions:
                    if sp.mt5_ticket not in mt5_tickets and sp.status in ("CLOSED", "RECONCILE_FAILED"):
                        trigger_pos = sp
                        break
                
                if trigger_pos:
                    alive_positions = [p for p in positions if p.mt5_ticket in mt5_tickets]
                    if alive_positions:
                        is_buy = alive_positions[0].entry_price < alive_positions[0].take_profit if alive_positions[0].take_profit else (mt5_tickets[alive_positions[0].mt5_ticket].type == mt5.POSITION_TYPE_BUY)
                        entry_price = alive_positions[0].entry_price
                        symbol = mt5_tickets[alive_positions[0].mt5_ticket].symbol
                        pip_size_val = get_pip_size(symbol)
                        # Use both ATR and Pip relative buffers, taking the max
                        be_atr_mult = getattr(risk, 'be_buffer_atr_mult', 0.0)
                        be_pips = getattr(risk, 'be_buffer_pips', 2.0)
                        # Fetch real ATR
                        atr_val = await self._get_or_fetch_atr(symbol, pip_size_val)
                        be_buffer = max(be_atr_mult * atr_val, be_pips * pip_size_val)
                        
                        # Ensure BE buffer covers spread + commission to prevent net loss
                        sym_info = mt5.symbol_info(symbol)
                        tick = mt5.symbol_info_tick(symbol)
                        
                        real_spread = 0.0
                        if tick and tick.ask > tick.bid:
                            real_spread = tick.ask - tick.bid
                        elif sym_info and sym_info.spread > 0:
                            real_spread = sym_info.spread * sym_info.point
                        
                        if real_spread <= 0:
                            real_spread = 2.0 * pip_size_val # fallback 2 pips
                            
                        min_spread_buffer = real_spread * 2.0 # 2x spread for safety against commission/slippage
                        be_buffer = max(be_buffer, min_spread_buffer)
                            
                        new_sl = entry_price + be_buffer if is_buy else entry_price - be_buffer
                        
                        for alive_pos in alive_positions:
                            current_sl = alive_pos.stop_loss
                            move_sl = False
                            if is_buy and new_sl > current_sl or not is_buy and (current_sl == 0.0 or new_sl < current_sl):
                                move_sl = True
                            
                            if move_sl and not alive_pos.be_applied:
                                success = await self._modify_sl(alive_pos.mt5_ticket, symbol, new_sl)
                                if success:
                                    alive_pos.stop_loss = new_sl
                                    alive_pos.be_applied = True
                                    modifications_made = True
                                    logger.info(f"Cascade BE (triggered by TP{trigger_pos.tp_level}): {alive_pos.mt5_ticket} -> {new_sl}")

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

    async def _calculate_trailing_sl(self, symbol: str, is_buy: bool, current_price: float, entry_price: float, current_sl: float, risk, trail_method: str, tp_level: int = 1) -> float | None:
        """
        Calculate the trailing SL based on user method.
        Returns the new SL price, or None if no trailing adjustment should be made.
        """
        pip_size_val = get_pip_size(symbol)
        step = getattr(risk, 'trail_step_pips', 5.0) * pip_size_val
        if step <= 0: step = pip_size_val

        if trail_method == "FIXED_PIPS":
            trail_distance = getattr(risk, 'trail_pips', 15.0) * pip_size_val
            new_sl = current_price - trail_distance if is_buy else current_price + trail_distance
            
            if is_buy:
                if new_sl >= current_sl + step: return new_sl
            else:
                if current_sl == 0.0 or new_sl <= current_sl - step: return new_sl
            return None

        elif trail_method == "ATR_TRAIL":
            atr_val = await self._get_or_fetch_atr(symbol, pip_size_val)
            if not atr_val: return None
            atr = atr_val
            
            multiplier = 0.5 if tp_level == "aggressive" else getattr(risk, f'atr_trail_multiplier_tp{tp_level}', getattr(risk, 'atr_trail_multiplier', 1.5))
            trail_distance = atr * multiplier
            new_sl = current_price - trail_distance if is_buy else current_price + trail_distance
            
            if is_buy:
                if new_sl >= current_sl + step: return new_sl
            else:
                if current_sl == 0.0 or new_sl <= current_sl - step: return new_sl
            return None
            
        elif trail_method == "PCT_TRAIL":
            trail_pct = getattr(risk, 'trail_pct', 0.5) / 100.0
            trail_distance = current_price * trail_pct
            new_sl = current_price - trail_distance if is_buy else current_price + trail_distance
            
            if is_buy:
                if new_sl >= current_sl + step: return new_sl
            else:
                if current_sl == 0.0 or new_sl <= current_sl - step: return new_sl
            return None

        elif trail_method == "STRUCTURE_TRAIL":
            now = datetime.utcnow().timestamp()
            struct_data = self._cached_structure.get(symbol)
            
            if not struct_data or (now - struct_data['time']) > 300: # 5 min cache
                try:
                    from backend.mt5.data_fetcher import DataFetcher
                    from backend.strategies.core.market_structure import (
                        MarketStructureDetector,
                    )
                    candles = await DataFetcher.get_historical_data(symbol, "M15", 100)
                    if not candles.empty:
                        bars = getattr(risk, 'trail_structure_bars', 3)
                        structure = MarketStructureDetector(swing_length=bars)
                        structure.update(candles)
                        self._cached_structure[symbol] = {'swings': structure.swings, 'time': now}
                        struct_data = self._cached_structure[symbol]
                except Exception as e:
                    logger.warning(f"Failed to fetch structure for trailing SL on {symbol}: {e}")
            
            if not struct_data: return None
            swings = struct_data['swings']
            
            if is_buy:
                recent_lows = [s for s in swings if s["type"] == "LOW" and s["price"] < current_price]
                if recent_lows:
                    last_low = recent_lows[-1]["price"]
                    buffer = 2.0 * pip_size_val
                    new_sl = last_low - buffer
                    if new_sl > current_sl + step: return new_sl
            else:
                recent_highs = [s for s in swings if s["type"] == "HIGH" and s["price"] > current_price]
                if recent_highs:
                    last_high = recent_highs[-1]["price"]
                    buffer = 2.0 * pip_size_val
                    new_sl = last_high + buffer
                    if current_sl == 0.0 or new_sl < current_sl - step: return new_sl
            return None

        return None

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

    async def _get_or_fetch_atr(self, symbol: str, pip_size_val: float) -> float:
        now = datetime.utcnow().timestamp()
        atr_data = self._cached_atr.get(symbol)
        
        if not atr_data or (now - atr_data['time']) > 60:
            try:
                import pandas as pd
                from backend.mt5.data_fetcher import DataFetcher
                candles = await DataFetcher.get_historical_data(symbol, "M5", 30)
                if not candles.empty:
                    candles['prev_close'] = candles['close'].shift(1)
                    candles['tr1'] = candles['high'] - candles['low']
                    candles['tr2'] = abs(candles['high'] - candles['prev_close'])
                    candles['tr3'] = abs(candles['low'] - candles['prev_close'])
                    candles['tr'] = candles[['tr1', 'tr2', 'tr3']].max(axis=1)
                    atr_val = candles['tr'].rolling(window=14).mean().iloc[-1]
                    if not pd.isna(atr_val):
                        self._cached_atr[symbol] = {'atr': atr_val, 'time': now}
                        return atr_val
            except Exception as e:
                logger.warning(f"Failed to fetch ATR for {symbol}: {e}")
        elif atr_data:
            return atr_data['atr']
            
        return 10.0 * pip_size_val

position_manager = PositionManager()
