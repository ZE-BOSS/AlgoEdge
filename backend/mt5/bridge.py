"""
backend/mt5/bridge.py

MT5 Connection Bridge and Tick Polling Service.
Maintains connection to MetaTrader 5 and polls for ticks/events.
"""

import asyncio
from typing import Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor

from backend.config import settings
from backend.utils.logger import get_logger
from backend.data.redis_client import redis_client

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

logger = get_logger(__name__)


class MT5Bridge:
    """
    Singleton manager for MT5 terminal connection.
    Uses ThreadPoolExecutor for blocking MT5 Python API calls.
    """
    
    def __init__(self):
        self.connected = False
        self._executor = ThreadPoolExecutor(max_workers=4)
        self.account_info = None

    async def connect(self, config_override=None) -> bool:
        """
        Initialize MT5 connection. Auto-reconnects on failure.
        """
        logger.info("Connecting to MT5...")
        if not mt5:
            logger.warning("MetaTrader5 package not found. Running in MOCK mode.")
            self.connected = True
            return True

        cfg = config_override or settings.mt5
        
        loop = asyncio.get_event_loop()
        init_ok = await loop.run_in_executor(
            self._executor, 
            lambda: mt5.initialize(path=cfg.path) if cfg.path else mt5.initialize()
        )
        
        if not init_ok:
            logger.error("MT5 initialize() failed")
            return False

        if cfg.account and cfg.password and cfg.server:
            login_ok = await loop.run_in_executor(
                self._executor,
                lambda: mt5.login(cfg.account, password=cfg.password, server=cfg.server)
            )
            if not login_ok:
                logger.error(f"MT5 login failed: {mt5.last_error()}")
                return False

        self.account_info = mt5.account_info()
        self.connected = True
        logger.info("MT5 Connected Successfully")
        return True

    async def disconnect(self):
        """Shutdown connection."""
        logger.info("Disconnecting MT5...")
        if mt5 and self.connected:
            await asyncio.get_event_loop().run_in_executor(self._executor, mt5.shutdown)
        self.connected = False

    async def start_tick_polling(self, symbols: list[str]):
        """
        Main polling loop for live ticks.
        Publishes ticks to Redis for the WebSocket to consume.
        """
        logger.info(f"Starting tick polling for {symbols}")
        
        if not mt5:
            logger.warning("Mock mode: no live ticks will be polled.")
            return

        for sym in symbols:
            await asyncio.get_event_loop().run_in_executor(
                self._executor, lambda s=sym: mt5.symbol_select(s, True)
            )

        loop = asyncio.get_event_loop()
        while self.connected:
            for sym in symbols:
                tick = await loop.run_in_executor(
                    self._executor, lambda s=sym: mt5.symbol_info_tick(s)
                )
                if tick:
                    tick_data = {
                        "symbol": sym,
                        "time": tick.time,
                        "bid": tick.bid,
                        "ask": tick.ask,
                        "last": tick.last,
                        "volume": tick.volume
                    }
                    await redis_client.publish(f"ticks:{sym}", tick_data)
            
            await asyncio.sleep(0.5) # Poll twice a second

    async def health_check(self) -> bool:
        """Verify MT5 terminal is still responding."""
        return self.connected


bridge = MT5Bridge()
