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

logger = get_logger(__name__)

class PositionManager:
    def __init__(self):
        self.running = False
        self._task = None

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
                await asyncio.sleep(3)
                await self._manage_positions(user_id)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"PositionManager loop error: {e}")
                await asyncio.sleep(5)

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
            result = await session.execute(
                select(TradePosition).where(TradePosition.user_id == user_id, TradePosition.status == "OPEN")
            )
            db_positions = result.scalars().all()
            db_tickets = {p.mt5_ticket: p for p in db_positions}

            # Group by parent trade for Breakeven Cascade
            trades_map = {}
            for p in db_positions:
                trades_map.setdefault(p.parent_trade_id, []).append(p)

            # 3. Fetch all open positions from MT5
            mt5_positions = mt5.positions_get()
            mt5_tickets = {p.ticket: p for p in mt5_positions} if mt5_positions else {}

            modifications_made = False

            # --- GOD SYNC (Adopt Missing MT5 Positions) ---
            for ticket, live_pos in mt5_tickets.items():
                if ticket not in db_tickets:
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

            # --- LIVE MANAGEMENT (Trailing & Standard BE & Manual Sync) ---
            for pos in db_positions:
                if pos.mt5_ticket not in mt5_tickets:
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
                    # Update parent trade as well
                    trade_query = await session.execute(select(Trade).where(Trade.id == pos.parent_trade_id))
                    parent_trade = trade_query.scalar_one_or_none()
                    if parent_trade:
                        parent_trade.stop_loss = current_sl
                        parent_trade.take_profit = current_tp
                    modifications_made = True
                
                pip_size = self._get_pip_size(symbol)

                # --- 1. BREAKEVEN LOGIC ---
                if risk.breakeven_trigger_rr > 0 and not pos.be_applied:
                    pips_in_profit = (current_price - entry_price) / pip_size if is_buy else (entry_price - current_price) / pip_size
                    
                    original_sl = pos.stop_loss
                    if original_sl == 0.0:
                        risk_pips = 20.0 # fallback
                    else:
                        risk_pips = abs(entry_price - original_sl) / pip_size
                    
                    if risk_pips > 0:
                        current_rr = pips_in_profit / risk_pips
                        if current_rr >= risk.breakeven_trigger_rr:
                            be_buffer = risk.breakeven_buffer_pips * pip_size
                            new_sl = entry_price + be_buffer if is_buy else entry_price - be_buffer
                            
                            move_sl = False
                            if is_buy and new_sl > current_sl:
                                move_sl = True
                            elif not is_buy and (current_sl == 0.0 or new_sl < current_sl):
                                move_sl = True
                                
                            if move_sl:
                                await self._modify_sl(live_pos.ticket, symbol, new_sl)
                                modifications_made = True
                                pos.stop_loss = new_sl
                                pos.be_applied = True
                                trade_query = await session.execute(select(Trade).where(Trade.id == pos.parent_trade_id))
                                parent_trade = trade_query.scalar_one_or_none()
                                if parent_trade:
                                    parent_trade.stop_loss = new_sl
                                msg = f"🛡️ *Breakeven Triggered*\nSymbol: {telegram_service.escape_markdown(symbol)}\nTicket: {live_pos.ticket}\nNew SL: {new_sl:.5f}"
                                logger.info(f"Moved SL to Breakeven: {live_pos.ticket} -> {new_sl}")
                                asyncio.create_task(telegram_service.send_message(msg))
                                continue

                # --- 2. TRAILING SL LOGIC ---
                if risk.trailing_sl_method != "NONE":
                    new_trail_sl = await self._calculate_trailing_sl(symbol, is_buy, current_price, entry_price, current_sl, risk)
                    if new_trail_sl is not None:
                        move_sl = False
                        if is_buy and new_trail_sl > current_sl:
                            move_sl = True
                        elif not is_buy and (current_sl == 0.0 or new_trail_sl < current_sl):
                            move_sl = True
                            
                        if move_sl:
                            await self._modify_sl(live_pos.ticket, symbol, new_trail_sl)
                            modifications_made = True
                            pos.stop_loss = new_trail_sl
                            trade_query = await session.execute(select(Trade).where(Trade.id == pos.parent_trade_id))
                            parent_trade = trade_query.scalar_one_or_none()
                            if parent_trade:
                                parent_trade.stop_loss = new_trail_sl
                            msg = f"🏃 *Trailing SL Updated*\nSymbol: {telegram_service.escape_markdown(symbol)}\nTicket: {live_pos.ticket}\nNew SL: {new_trail_sl:.5f}"
                            logger.info(f"Trailed SL: {live_pos.ticket} -> {new_trail_sl}")
                            asyncio.create_task(telegram_service.send_message(msg))

            # --- BREAKEVEN CASCADE CHECK ---
            for parent_id, positions in trades_map.items():
                tp1_pos = next((p for p in positions if p.tp_level == 1), None)
                if tp1_pos and tp1_pos.mt5_ticket not in mt5_tickets:
                    alive_positions = [p for p in positions if p.mt5_ticket in mt5_tickets]
                    if alive_positions:
                        is_buy = alive_positions[0].entry_price < alive_positions[0].take_profit if alive_positions[0].take_profit else (mt5_tickets[alive_positions[0].mt5_ticket].type == mt5.POSITION_TYPE_BUY)
                        entry_price = alive_positions[0].entry_price
                        symbol = mt5_tickets[alive_positions[0].mt5_ticket].symbol
                        pip_size = self._get_pip_size(symbol)
                        be_buffer = risk.breakeven_buffer_pips * pip_size
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
                                await self._modify_sl(alive_pos.mt5_ticket, symbol, new_sl)
                                alive_pos.stop_loss = new_sl
                                alive_pos.be_applied = True
                                modifications_made = True
                                trade_query = await session.execute(select(Trade).where(Trade.id == alive_pos.parent_trade_id))
                                parent_trade = trade_query.scalar_one_or_none()
                                if parent_trade:
                                    parent_trade.stop_loss = new_sl
                                msg = f"🛡️ *Cascade Breakeven*\nSymbol: {telegram_service.escape_markdown(symbol)}\nTicket: {alive_pos.mt5_ticket}\nNew SL: {new_sl:.5f}"
                                logger.info(f"Cascade BE: {alive_pos.mt5_ticket} -> {new_sl}")
                                asyncio.create_task(telegram_service.send_message(msg))

            if modifications_made:
                await session.commit()
                await ws_manager.broadcast_all({"type": "trade_update"})

    async def _calculate_trailing_sl(self, symbol: str, is_buy: bool, current_price: float, entry_price: float, current_sl: float, risk) -> float | None:
        """
        Calculate the trailing SL based on user method.
        Returns the new SL price, or None if no trailing adjustment should be made.
        """
        pip_size = self._get_pip_size(symbol)
        
        # We only start trailing if the market has moved in our favor
        # You can add a minimum profit condition here if needed.

        if risk.trailing_sl_method == "PIPS":
            step = risk.trailing_sl_step_pips * pip_size
            if step <= 0: return None
            
            # Simple fixed distance trailing
            trail_distance = 15.0 * pip_size # Hardcoded default distance for basic pip trail
            new_sl = current_price - trail_distance if is_buy else current_price + trail_distance
            
            # Apply step logic: SL only moves in increments of `step`
            if is_buy:
                if new_sl >= current_sl + step: return new_sl
            else:
                if current_sl == 0.0 or new_sl <= current_sl - step: return new_sl
            return None

        elif risk.trailing_sl_method == "ATR_TRAIL":
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
            trail_distance = atr * 1.5  # You could expose this multiplier in Risk config
            new_sl = current_price - trail_distance if is_buy else current_price + trail_distance
            
            # Add a step buffer so we aren't modifying it every tick
            step = 2.0 * pip_size
            if is_buy:
                if new_sl >= current_sl + step: return new_sl
            else:
                if current_sl == 0.0 or new_sl <= current_sl - step: return new_sl
            return None
            
        elif risk.trailing_sl_method == "STRUCTURE_TRAIL":
            # Complex Structure Trailing
            from backend.mt5.data_fetcher import DataFetcher
            from backend.strategies.core.structure import Structure
            
            # Fetch more candles to find swing points
            candles = await DataFetcher.get_historical_data(symbol, "M15", 100)
            if candles.empty: return None
            
            structure = Structure(left_bars=3, right_bars=3)
            _, swings = structure.analyze(candles)
            
            if is_buy:
                # Find the most recent Valid Swing Low
                recent_lows = [s for s in swings if s["type"] == "LOW" and s["price"] < current_price]
                if recent_lows:
                    last_low = recent_lows[-1]["price"]
                    buffer = 2.0 * pip_size
                    new_sl = last_low - buffer
                    if new_sl > current_sl + (1.0 * pip_size): return new_sl
            else:
                # Find the most recent Valid Swing High
                recent_highs = [s for s in swings if s["type"] == "HIGH" and s["price"] > current_price]
                if recent_highs:
                    last_high = recent_highs[-1]["price"]
                    buffer = 2.0 * pip_size
                    new_sl = last_high + buffer
                    if current_sl == 0.0 or new_sl < current_sl - (1.0 * pip_size): return new_sl
            return None

        return None

    def _get_pip_size(self, symbol: str) -> float:
        info = mt5.symbol_info(symbol)
        if not info: return 0.0001
        
        # Synthetic indices like Crash 1000 have different logic
        if "Crash" in symbol or "Boom" in symbol or "Jump" in symbol or "Step" in symbol:
            return info.point
            
        # Standard Forex
        if info.digits == 3 or info.digits == 5:
            return 10.0 * info.point
        return info.point

    async def _modify_sl(self, ticket: int, symbol: str, new_sl: float):
        from backend.mt5.order_manager import OrderManager
        loop = asyncio.get_running_loop()
        success = await loop.run_in_executor(
            None,
            lambda: OrderManager.modify_sl(ticket, symbol, new_sl)
        )
        if not success:
            logger.warning(f"Failed to modify SL for ticket {ticket} to {new_sl}")

position_manager = PositionManager()
