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

logger = get_logger(__name__)

MAX_LOG_ENTRIES = 500


class BotService:
    """Singleton service managing the trading bot lifecycle."""

    def __init__(self):
        self.running = False
        self.symbols: List[str] = []
        self.last_scan: Optional[str] = None
        self.total_signals_today: int = 0
        self.scan_interval: int = 60  # seconds between scans
        self._task: Optional[asyncio.Task] = None
        self._events: deque = deque(maxlen=MAX_LOG_ENTRIES)

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
        asyncio.ensure_future(self._broadcast_event(event))

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

        self._log_event(
            f"Bot started — scanning {', '.join(self.symbols)} every {self.scan_interval}s",
            category="BOT"
        )

        # Start background scanning task
        self._task = asyncio.create_task(self._scan_loop(user_id))

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
                for symbol in self.symbols:
                    if not self.running:
                        break

                    self._log_event(f"Scanning {symbol}...", category="SCAN")

                    try:
                        # Fetch recent candles
                        candles = await DataFetcher.get_historical_data(symbol, "M15", count=200)
                        if candles is None or candles.empty:
                            self._log_event(f"No data for {symbol}", "WARN", "SCAN")
                            continue

                        self._log_event(f"Fetched {len(candles)} candles for {symbol}", category="DATA")

                        # Try to run strategy engine
                        try:
                            from backend.strategies.smc.engine import SMCEngine
                            from backend.strategies.smc.params import UserConfig
                            config = UserConfig()
                            engine = SMCEngine(config)

                            import pandas as pd
                            # Ensure candles have proper index for the strategy
                            if 'time' in candles.columns:
                                candles_indexed = candles.set_index(
                                    pd.to_datetime(candles['time'], unit='s')
                                )
                            else:
                                candles_indexed = candles

                            signal = await engine.on_bar(symbol, "M15", candles_indexed)
                            if signal:
                                self.total_signals_today += 1
                                self._log_event(
                                    f"Signal: {signal.direction} {symbol} @ {signal.entry_price} "
                                    f"| SL: {signal.stop_loss} | Score: {signal.confluence_score}",
                                    "SIGNAL", "SIGNAL"
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
                self._log_event(f"Scan loop error: {str(e)[:200]}", "ERROR", "BOT")
                await asyncio.sleep(10)

        self._log_event("Scan loop exited", category="BOT")


# Singleton instance
bot_service = BotService()
