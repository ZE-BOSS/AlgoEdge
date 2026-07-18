"""
backend/services/position_manager.py

Actively manages open MT5 positions. Runs in a frequent loop (e.g. every 3s) to:
1. Reconcile DB open positions with live MT5 positions.
2. Apply Breakeven rules (move SL to entry + buffer when price hits trigger).
3. Apply Trailing Stop Loss rules (ATR or Structure based) if requested.
"""

import asyncio
import MetaTrader5 as mt5
from datetime import datetime, timezone
from sqlalchemy import select

from backend.utils.logger import get_logger
from backend.services.telegram import telegram_service
from backend.api.websocket import manager as ws_manager
from backend.risk.position_sizer import get_pip_size

logger = get_logger(__name__)

class PositionManager:
    def __init__(self):
        self.running = False
        self._task = None
        self.pending_adoptions = {}

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

        from backend.data.database import async_session
        from backend.data.models import TradePosition, Trade, UserConfigModel
        from backend.core.config_schema import UserConfigV2
        
        async with async_session() as session:
            # 1. Fetch user config for risk settings
            result = await session.execute(select(UserConfigModel).where(UserConfigModel.user_id == user_id))
            config_db = result.scalar_one_or_none()
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

            # Group by parent trade for Breakeven Cascade
            trades_map = {}
            for p in db_positions:
                trades_map.setdefault(p.parent_trade_id, []).append(p)

            # 3. Fetch all open positions from MT5
            mt5_positions = mt5.positions_get()
            mt5_tickets = {p.ticket: p for p in mt5_positions} if mt5_positions else {}

            modifications_made = False

            # Clean up pending adoptions that are now in DB or no longer in MT5
            self.pending_adoptions = {
                t: time for t, time in self.pending_adoptions.items() 
                if t in mt5_tickets and t not in db_tickets
            }

            # --- GOD SYNC (Adopt Missing MT5 Positions) ---
            current_time = datetime.utcnow().timestamp()
            for ticket, live_pos in mt5_tickets.items():
                if ticket not in db_tickets:
                    # Delay adoption by 15s to avoid race condition with bot execution
                    if ticket not in self.pending_adoptions:
                        self.pending_adoptions[ticket] = current_time
                        continue
                    if current_time - self.pending_adoptions[ticket] < 15:
                        continue

                    # Adopt manual trade!
                    is_buy = (live_pos.type == mt5.POSITION_TYPE_BUY)
                    new_trade = Trade(
                        user_id=user_id,
                        strategy_id="MANUAL",
                        symbol=live_pos.symbol,
                        direction="BUY" if is_buy else "SELL",
                        entry_price=live_pos.price_open,
                        stop_loss=live_pos.sl,
                        take_profit=live_pos.tp,
                        volume=live_pos.volume,
                        status="OPEN",
                        entry_time=datetime.utcfromtimestamp(live_pos.time)
                    )
                    session.add(new_trade)
                    await session.flush() # Get trade.id
                    
                    new_pos = TradePosition(
                        parent_trade_id=new_trade.id,
                        user_id=user_id,
                        tp_level=1,
                        mt5_ticket=ticket,
                        volume=live_pos.volume,
                        entry_price=live_pos.price_open,
                        stop_loss=live_pos.sl,
                        take_profit=live_pos.tp,
                        status="OPEN"
                    )
                    session.add(new_pos)
                    db_tickets[ticket] = new_pos
                    db_positions.append(new_pos)
                    trades_map.setdefault(new_trade.id, []).append(new_pos)
                    modifications_made = True
                    logger.info(f"God Sync: Adopted ghost/manual trade {ticket}")

            # --- EXIT HANDLER ---
            for pos in db_positions:
                if pos.mt5_ticket not in mt5_tickets:
                    # Position is closed in MT5, but OPEN in DB
                    deals = mt5.history_deals_get(position=pos.mt5_ticket)
                    if deals:
                        exit_deal = deals[-1]
                        
                        # Only update if status is OPEN or PNL differs
                        if pos.status != "CLOSED" or pos.pnl != exit_deal.profit:
                            was_open = pos.status != "CLOSED"
                            pos.status = "CLOSED"
                            pos.pnl = exit_deal.profit
                            pos.exit_price = exit_deal.price
                            # Use system UTC to align with entry_time instead of broker time
                            if was_open:
                                pos.exit_time = datetime.utcnow()
                            
                            # Map MT5 Deal Reason to our DB Reason
                            reason = "CLOSED"
                            if exit_deal.reason == mt5.DEAL_REASON_SL:
                                # If BE/Trailing was applied and the current SL != initial, it's a TRAIL stop
                                reason = "TRAIL" if pos.be_applied else "SL"
                            elif exit_deal.reason == mt5.DEAL_REASON_TP:
                                reason = f"TP{pos.tp_level}" if pos.tp_level else "TP"
                            elif exit_deal.reason == mt5.DEAL_REASON_CLIENT:
                                reason = "CLIENT"
                                
                            pos.exit_reason = reason
                            modifications_made = True
                    else:
                        # Position missing from MT5 and no history found! It was voided/rejected or is a ghost.
                        logger.warning(f"Position {pos.mt5_ticket} missing from MT5 with no history. Deleting ghost trade.")
                        
                        parent_id = pos.parent_trade_id
                        await session.delete(pos)
                        modifications_made = True
                        
                        # Remove from in-memory maps to avoid recalculation errors
                        if parent_id in trades_map:
                            trades_map[parent_id] = [p for p in trades_map[parent_id] if p.id != pos.id]
                        
                        # Fetch parent trade to check if all siblings are closed or deleted
                        trade_query = await session.execute(select(Trade).where(Trade.id == parent_id))
                        parent_trade = trade_query.scalar_one_or_none()
                        
                        if parent_trade:
                            siblings = trades_map.get(parent_id, [])
                            if not siblings:
                                # All siblings were deleted ghosts! Delete the parent trade too.
                                logger.warning(f"All positions for Trade {parent_id} were ghosts. Deleting parent trade.")
                                await session.delete(parent_trade)
                            else:
                                # Recalculate if remaining siblings are closed
                                all_closed = True
                                total_pnl = 0.0
                                for sib in siblings:
                                    if sib.mt5_ticket in mt5_tickets:
                                        all_closed = False
                                        break
                                    sib_pnl = sib.pnl if sib.pnl is not None else 0.0
                                    total_pnl += sib_pnl
                                    
                                if all_closed and parent_trade.status != "CLOSED":
                                    parent_trade.status = "CLOSED"
                                    parent_trade.pnl = total_pnl
                                    parent_trade.exit_time = datetime.utcnow()
                                    parent_trade.exit_reason = "REJECTED"
                                    parent_trade.exit_price = parent_trade.entry_price
                        continue

            # --- LIVE MANAGEMENT (Trailing & Standard BE & Manual Sync) ---
            for pos in db_positions:
                if pos.status == "CLOSED" or pos.mt5_ticket not in mt5_tickets:
                    continue

                live_pos = mt5_tickets[pos.mt5_ticket]
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

                # --- 1. BREAKEVEN LOGIC ---
                if hasattr(risk, 'be_trigger_rr') and risk.be_trigger_rr > 0 and not pos.be_applied:
                    pips_in_profit = (current_price - entry_price) / pip_size_val if is_buy else (entry_price - current_price) / pip_size_val
                    
                    original_sl = pos.stop_loss
                    if original_sl == 0.0:
                        risk_pips = 20.0 # fallback
                    else:
                        risk_pips = abs(entry_price - original_sl) / pip_size_val
                    
                    if risk_pips > 0:
                        current_rr = pips_in_profit / risk_pips
                        if current_rr >= risk.be_trigger_rr:
                            be_buffer = risk.be_buffer_pips * pip_size_val
                            new_sl = entry_price + be_buffer if is_buy else entry_price - be_buffer
                            
                            move_sl = False
                            if is_buy and new_sl > current_sl:
                                move_sl = True
                            elif not is_buy and (current_sl == 0.0 or new_sl < current_sl):
                                move_sl = True
                                
                            if move_sl:
                                success = await self._modify_sl(live_pos.ticket, symbol, new_sl)
                                if success:
                                    modifications_made = True
                                    pos.stop_loss = new_sl
                                    pos.be_applied = True
                                    logger.info(f"Moved SL to Breakeven: {live_pos.ticket} -> {new_sl}")
                                continue

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
                            pt = tq.scalar_one_or_none()
                            if pt: original_sl = pt.stop_loss
                            
                        if original_sl != 0.0:
                            risk_pips = abs(entry_price - original_sl) / pip_size_val
                        else:
                            risk_pips = 20.0
                            
                        pips_in_profit = (current_price - entry_price) / pip_size_val if is_buy else (entry_price - current_price) / pip_size_val
                        current_rr = pips_in_profit / risk_pips if risk_pips > 0 else 0
                        activation_rr = getattr(risk, 'trail_activation_rr', 1.0)
                        
                    if current_rr >= activation_rr:
                        new_trail_sl = await self._calculate_trailing_sl(symbol, is_buy, current_price, entry_price, current_sl, risk, trail_method)
                        if new_trail_sl is not None:
                            move_sl = False
                            if is_buy and new_trail_sl > current_sl:
                                move_sl = True
                            elif not is_buy and (current_sl == 0.0 or new_trail_sl < current_sl):
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
            for parent_id, positions in trades_map.items():
                tp1_pos = next((p for p in positions if p.tp_level == 1), None)
                if tp1_pos and tp1_pos.mt5_ticket not in mt5_tickets:
                    alive_positions = [p for p in positions if p.mt5_ticket in mt5_tickets]
                    if alive_positions:
                        is_buy = alive_positions[0].entry_price < alive_positions[0].take_profit if alive_positions[0].take_profit else (mt5_tickets[alive_positions[0].mt5_ticket].type == mt5.POSITION_TYPE_BUY)
                        entry_price = alive_positions[0].entry_price
                        symbol = mt5_tickets[alive_positions[0].mt5_ticket].symbol
                        pip_size_val = get_pip_size(symbol)
                        be_buffer = getattr(risk, 'be_buffer_pips', 2.0) * pip_size_val
                        new_sl = entry_price + be_buffer if is_buy else entry_price - be_buffer
                        
                        for alive_pos in alive_positions:
                            # Use pos.stop_loss in case it was modified in the Live Management block
                            current_sl = alive_pos.stop_loss
                            move_sl = False
                            if is_buy and new_sl > current_sl:
                                move_sl = True
                            elif not is_buy and (current_sl == 0.0 or new_sl < current_sl):
                                move_sl = True
                            
                            if move_sl and not alive_pos.be_applied:
                                success = await self._modify_sl(alive_pos.mt5_ticket, symbol, new_sl)
                                if success:
                                    alive_pos.stop_loss = new_sl
                                    alive_pos.be_applied = True
                                    modifications_made = True
                                    logger.info(f"Cascade BE: {alive_pos.mt5_ticket} -> {new_sl}")

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

    async def _calculate_trailing_sl(self, symbol: str, is_buy: bool, current_price: float, entry_price: float, current_sl: float, risk, trail_method: str) -> float | None:
        """
        Calculate the trailing SL based on user method.
        Returns the new SL price, or None if no trailing adjustment should be made.
        """
        pip_size_val = get_pip_size(symbol)
        step = getattr(risk, 'trail_step_pips', 5.0) * pip_size_val
        if step <= 0: step = pip_size_val

        if trail_method == "FIXED_PIPS":
            
            # Simple fixed distance trailing
            trail_distance = getattr(risk, 'trail_pips', 15.0) * pip_size_val
            new_sl = current_price - trail_distance if is_buy else current_price + trail_distance
            
            # Apply step logic: SL only moves in increments of `step`
            if is_buy:
                if new_sl >= current_sl + step: return new_sl
            else:
                if current_sl == 0.0 or new_sl <= current_sl - step: return new_sl
            return None

        elif trail_method == "ATR_TRAIL":
            # Complex ATR Trailing
            from backend.mt5.data_fetcher import DataFetcher
            import pandas as pd
            
            # Fetch recent candles to calculate ATR
            candles = await DataFetcher.get_historical_data(symbol, "M5", 30)
            if candles.empty: return None
            
            # Calculate ATR(14)
            candles['prev_close'] = candles['close'].shift(1)
            candles['tr1'] = candles['high'] - candles['low']
            candles['tr2'] = abs(candles['high'] - candles['prev_close'])
            candles['tr3'] = abs(candles['low'] - candles['prev_close'])
            candles['tr'] = candles[['tr1', 'tr2', 'tr3']].max(axis=1)
            atr = candles['tr'].rolling(window=14).mean().iloc[-1]
            
            if pd.isna(atr): return None
            
            # Trail distance is ATR * multiplier
            multiplier = getattr(risk, 'atr_trail_multiplier', 1.5)
            trail_distance = atr * multiplier
            new_sl = current_price - trail_distance if is_buy else current_price + trail_distance
            
            # Add a step buffer so we aren't modifying it every tick
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
            # Complex Structure Trailing
            from backend.mt5.data_fetcher import DataFetcher
            from backend.strategies.core.market_structure import MarketStructureDetector
            
            # Fetch more candles to find swing points
            candles = await DataFetcher.get_historical_data(symbol, "M15", 100)
            if candles.empty: return None
            
            bars = getattr(risk, 'trail_structure_bars', 3)
            structure = MarketStructureDetector(swing_length=bars)
            structure.update(candles)
            swings = structure.swings
            
            if is_buy:
                # Find the most recent Valid Swing Low
                recent_lows = [s for s in swings if s["type"] == "LOW" and s["price"] < current_price]
                if recent_lows:
                    last_low = recent_lows[-1]["price"]
                    buffer = 2.0 * pip_size_val
                    new_sl = last_low - buffer
                    if new_sl > current_sl + step: return new_sl
            else:
                # Find the most recent Valid Swing High
                recent_highs = [s for s in swings if s["type"] == "HIGH" and s["price"] > current_price]
                if recent_highs:
                    last_high = recent_highs[-1]["price"]
                    buffer = 2.0 * pip_size_val
                    new_sl = last_high + buffer
                    if current_sl == 0.0 or new_sl < current_sl - step: return new_sl
            return None

        return None

    async def _modify_sl(self, ticket: int, symbol: str, new_sl: float) -> bool:
        from backend.mt5.order_manager import OrderManager
        loop = asyncio.get_running_loop()
        success = await loop.run_in_executor(
            None,
            lambda: OrderManager.modify_sl(ticket, symbol, new_sl)
        )
        if not success:
            logger.error(f"Failed to modify SL for {ticket} to {new_sl}")
            return False
        return True

position_manager = PositionManager()
