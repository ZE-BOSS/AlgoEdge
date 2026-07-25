"""
backend/mt5/bridge.py

MT5 Connection Bridge and Tick Polling Service.
Maintains connection to MetaTrader 5 and polls for ticks/events.
Supports both .env-based connection and per-user credential connection.
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


from backend.brokers.base import BaseBroker

class MT5Broker(BaseBroker):
    """
    Singleton manager for MT5 terminal connection.
    Uses ThreadPoolExecutor for blocking MT5 Python API calls.
    """
    
    def __init__(self):
        self.connected = False
        self._executor = ThreadPoolExecutor(max_workers=4)
        self.account_info = None
        self._connected_account: Optional[int] = None  # Track which account is connected
        self._intentional_disconnect = False
        self._last_method = None
        self._last_kwargs = {}
        self._reconnect_task = None

    async def connect(self, config_override=None) -> bool:
        """
        Initialize MT5 connection using .env settings.
        Auto-reconnects on failure.
        """
        logger.info("Connecting to MT5...")
        self._intentional_disconnect = False
        self._last_method = self.connect
        self._last_kwargs = {"config_override": config_override}
        if self._reconnect_task is None:
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())
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

        self.account_info = await loop.run_in_executor(self._executor, mt5.account_info)
        self._connected_account = cfg.account if cfg.account else None
        self.connected = True
        logger.info("MT5 Connected Successfully")
        return True

    async def connect_for_user(self, user_id: str) -> bool:
        """
        Connect to MT5 using a specific user's stored credentials.
        Decrypts the password from the database and initializes the terminal.
        
        This method should be used for live trading to ensure the correct
        broker account is used per user.
        """
        logger.info(f"Connecting to MT5 for user {user_id}...")
        self._intentional_disconnect = False
        self._last_method = self.connect_for_user
        self._last_kwargs = {"user_id": user_id}
        if self._reconnect_task is None:
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())
        
        if not mt5:
            logger.warning("MetaTrader5 package not found. Running in MOCK mode.")
            self.connected = True
            return True

        # Load user credentials from DB
        try:
            from backend.data.database import get_session
            from backend.data.models import User
            from backend.utils.encryption import get_encryption_service
            from sqlalchemy import select

            async with get_session() as session:
                result = await session.execute(select(User).where(User.id == user_id))
                user = result.scalar_one_or_none()

            if not user:
                logger.error(f"User {user_id} not found")
                return False

            if not user.mt5_account or not user.mt5_password_encrypted:
                logger.error(f"No broker credentials stored for user {user_id}")
                return False

            # Decrypt password
            encryption = get_encryption_service()
            password = encryption.decrypt(user.mt5_password_encrypted)

            account = user.mt5_account
            server = user.mt5_server or ""
            path = user.mt5_path or ""

        except Exception as e:
            logger.error(f"Failed to load user credentials: {e}")
            return False

        # Initialize MT5 terminal
        loop = asyncio.get_event_loop()
        if path:
            init_ok = await loop.run_in_executor(
                self._executor, lambda: mt5.initialize(path=path)
            )
        else:
            init_ok = await loop.run_in_executor(
                self._executor, mt5.initialize
            )

        if not init_ok:
            logger.error(f"MT5 initialize() failed for user {user_id}: {mt5.last_error()}")
            return False

        # Login with user credentials
        login_ok = await loop.run_in_executor(
            self._executor,
            lambda: mt5.login(account, password=password, server=server)
        )

        if not login_ok:
            error = mt5.last_error()
            logger.error(f"MT5 login failed for user {user_id}: {error}")
            await loop.run_in_executor(self._executor, mt5.shutdown)
            return False

        self.account_info = await loop.run_in_executor(self._executor, mt5.account_info)
        self._connected_account = account
        self.connected = True
        logger.info(
            f"MT5 connected for user {user_id}: "
            f"account={account}, server={server}, "
            f"balance={self.account_info.balance if self.account_info else 'N/A'}"
        )
        return True

    async def connect_for_user_deriv(self, user_id: str) -> bool:
        """
        Connect to MT5 using a specific user's Deriv (synthetics) credentials.
        Same as connect_for_user but uses deriv_mt5_* fields.
        """
        logger.info(f"Connecting to Deriv MT5 for user {user_id}...")
        self._intentional_disconnect = False
        self._last_method = self.connect_for_user_deriv
        self._last_kwargs = {"user_id": user_id}
        if self._reconnect_task is None:
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())
        
        if not mt5:
            logger.warning("MetaTrader5 package not found. Running in MOCK mode.")
            self.connected = True
            return True

        try:
            from backend.data.database import get_session
            from backend.data.models import User
            from backend.utils.encryption import get_encryption_service
            from sqlalchemy import select

            async with get_session() as session:
                result = await session.execute(select(User).where(User.id == user_id))
                user = result.scalar_one_or_none()

            if not user:
                logger.error(f"User {user_id} not found")
                return False

            if not user.deriv_mt5_account or not user.deriv_mt5_password_encrypted:
                logger.error(f"No Deriv credentials stored for user {user_id}")
                return False

            encryption = get_encryption_service()
            password = encryption.decrypt(user.deriv_mt5_password_encrypted)

            account = user.deriv_mt5_account
            server = user.deriv_mt5_server or ""
            path = user.deriv_mt5_path or ""

        except Exception as e:
            logger.error(f"Failed to load Deriv credentials: {e}")
            return False

        loop = asyncio.get_event_loop()
        if path:
            init_ok = await loop.run_in_executor(
                self._executor, lambda: mt5.initialize(path=path)
            )
        else:
            init_ok = await loop.run_in_executor(
                self._executor, mt5.initialize
            )

        if not init_ok:
            logger.error(f"MT5 initialize() failed for Deriv user {user_id}")
            return False

        login_ok = await loop.run_in_executor(
            self._executor,
            lambda: mt5.login(account, password=password, server=server)
        )

        if not login_ok:
            error = mt5.last_error()
            logger.error(f"Deriv MT5 login failed for user {user_id}: {error}")
            await loop.run_in_executor(self._executor, mt5.shutdown)
            return False

        self.account_info = await loop.run_in_executor(self._executor, mt5.account_info)
        self._connected_account = account
        self.connected = True
        logger.info(f"Deriv MT5 connected for user {user_id}: account={account}")
        return True

    async def get_live_account_info(self):
        """Fetch live account info and update self.account_info."""
        if not mt5 or not self.connected:
            return None
        loop = asyncio.get_running_loop()
        self.account_info = await loop.run_in_executor(self._executor, mt5.account_info)
        return self.account_info

    async def disconnect(self):
        """Shutdown connection."""
        logger.info("Disconnecting MT5...")
        self._intentional_disconnect = True
        self._last_method = None
        if mt5 and self.connected:
            await asyncio.get_event_loop().run_in_executor(self._executor, mt5.shutdown)
        self.connected = False
        self._connected_account = None

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
        while not getattr(self, '_intentional_disconnect', False):
            if not self.connected:
                await asyncio.sleep(1)
                continue
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

    def get_connected_account(self) -> Optional[int]:
        """Return the currently connected MT5 account number."""
        return self._connected_account

    async def _reconnect_loop(self):
        """Background daemon to check MT5 health and auto-reconnect every 10s."""
        while True:
            await asyncio.sleep(10)
            if getattr(self, '_intentional_disconnect', False) or not getattr(self, '_last_method', None) or not mt5:
                continue

            try:
                loop = asyncio.get_event_loop()
                terminal = await loop.run_in_executor(self._executor, mt5.terminal_info)
                is_connected = terminal is not None and terminal.connected
            except Exception:
                is_connected = False

            if not is_connected:
                logger.warning("MT5 connection lost! Attempting to auto-reconnect...")
                self.connected = False
                try:
                    success = await self._last_method(**self._last_kwargs)
                    if success:
                        logger.info("MT5 auto-reconnect successful.")
                    else:
                        logger.error("MT5 auto-reconnect failed. Retrying in 10s...")
                except Exception as e:
                    logger.error(f"Error during auto-reconnect: {e}")

    # --- BaseBroker Interface Implementation ---
    
    async def initialize(self) -> bool:
        return await self.connect()

    async def shutdown(self):
        await self.disconnect()

    async def connect_for_user_deriv(self, user_id: str) -> bool:
        """
        Connect to MT5 using a specific user's Deriv (synthetics) credentials.
        Same as connect_for_user but uses deriv_mt5_* fields.
        """
        logger.info(f"Connecting to Deriv MT5 for user {user_id}...")
        self._intentional_disconnect = False
        self._last_method = self.connect_for_user_deriv
        self._last_kwargs = {"user_id": user_id}
        if self._reconnect_task is None:
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())
        
        if not mt5:
            logger.warning("MetaTrader5 package not found. Running in MOCK mode.")
            self.connected = True
            return True

        try:
            from backend.data.database import get_session
            from backend.data.models import User
            from backend.utils.encryption import get_encryption_service
            from sqlalchemy import select

            async with get_session() as session:
                result = await session.execute(select(User).where(User.id == user_id))
                user = result.scalar_one_or_none()

            if not user:
                logger.error(f"User {user_id} not found")
                return False

            if not user.deriv_mt5_account or not user.deriv_mt5_password_encrypted:
                logger.error(f"No Deriv credentials stored for user {user_id}")
                return False

            encryption = get_encryption_service()
            password = encryption.decrypt(user.deriv_mt5_password_encrypted)

            account = user.deriv_mt5_account
            server = user.deriv_mt5_server or ""
            path = user.deriv_mt5_path or ""

        except Exception as e:
            logger.error(f"Failed to load Deriv credentials: {e}")
            return False

        loop = asyncio.get_event_loop()
        if path:
            init_ok = await loop.run_in_executor(
                self._executor, lambda: mt5.initialize(path=path)
            )
        else:
            init_ok = await loop.run_in_executor(
                self._executor, mt5.initialize
            )

        if not init_ok:
            logger.error(f"MT5 initialize() failed for Deriv user {user_id}")
            return False

        login_ok = await loop.run_in_executor(
            self._executor,
            lambda: mt5.login(account, password=password, server=server)
        )

        if not login_ok:
            error = mt5.last_error()
            logger.error(f"Deriv MT5 login failed for user {user_id}: {error}")
            await loop.run_in_executor(self._executor, mt5.shutdown)
            return False

        self.account_info = await loop.run_in_executor(self._executor, mt5.account_info)
        self._connected_account = account
        self.connected = True
        logger.info(f"Deriv MT5 connected for user {user_id}: account={account}")
        return True

    async def get_live_account_info(self):
        """Fetch live account info and update self.account_info."""
        if not mt5 or not self.connected:
            return None
        loop = asyncio.get_running_loop()
        self.account_info = await loop.run_in_executor(self._executor, mt5.account_info)
        return self.account_info

    async def disconnect(self):
        """Shutdown connection."""
        logger.info("Disconnecting MT5...")
        self._intentional_disconnect = True
        self._last_method = None
        if mt5 and self.connected:
            await asyncio.get_event_loop().run_in_executor(self._executor, mt5.shutdown)
        self.connected = False
        self._connected_account = None

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
        while not getattr(self, '_intentional_disconnect', False):
            if not self.connected:
                await asyncio.sleep(1)
                continue
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

    def get_connected_account(self) -> Optional[int]:
        """Return the currently connected MT5 account number."""
        return self._connected_account

    async def _reconnect_loop(self):
        """Background daemon to check MT5 health and auto-reconnect every 10s."""
        while True:
            await asyncio.sleep(10)
            if getattr(self, '_intentional_disconnect', False) or not getattr(self, '_last_method', None) or not mt5:
                continue

            try:
                loop = asyncio.get_event_loop()
                terminal = await loop.run_in_executor(self._executor, mt5.terminal_info)
                is_connected = terminal is not None and terminal.connected
            except Exception:
                is_connected = False

            if not is_connected:
                logger.warning("MT5 connection lost! Attempting to auto-reconnect...")
                self.connected = False
                try:
                    success = await self._last_method(**self._last_kwargs)
                    if success:
                        logger.info("MT5 auto-reconnect successful.")
                    else:
                        logger.error("MT5 auto-reconnect failed. Retrying in 10s...")
                except Exception as e:
                    logger.error(f"Error during auto-reconnect: {e}")

    # --- BaseBroker Interface Implementation ---
    
    async def initialize(self) -> bool:
        return await self.connect()

    async def shutdown(self):
        await self.disconnect()

    async def check_connection(self) -> bool:
        return await self.health_check()

    async def get_rates(self, symbol: str, timeframe: str, count: int):
        from backend.mt5.data_fetcher import DataFetcher
        return await DataFetcher.get_historical_data(symbol, timeframe, count)
        
    async def get_symbol_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        if not mt5: return None
        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(self._executor, lambda: mt5.symbol_info(symbol))
        return info._asdict() if info else None
        
    async def get_account_info(self) -> Optional[Dict[str, Any]]:
        info = await self.get_live_account_info()
        return info._asdict() if info else None
        
    async def get_open_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        if not mt5: return []
        loop = asyncio.get_running_loop()
        positions = await loop.run_in_executor(self._executor, lambda: mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get())
        return [p._asdict() for p in positions] if positions else []

    async def execute_market_order(self, symbol: str, direction: str, volume: float, sl: Optional[float] = None, tp: Optional[float] = None, comment: str = "") -> Optional[Dict[str, Any]]:
        from backend.mt5.order_manager import OrderManager
        magic = 0 # Default magic
        return await OrderManager.place_market_order(symbol, direction, volume, sl or 0.0, tp or 0.0, magic, comment)

    async def close_position(self, ticket: int, volume: Optional[float] = None) -> bool:
        from backend.mt5.order_manager import OrderManager
        return await OrderManager.close_position(ticket, volume)

    async def modify_position(self, ticket: int, sl: Optional[float] = None, tp: Optional[float] = None) -> bool:
        from backend.mt5.order_manager import OrderManager
        if sl is not None:
            return await OrderManager.modify_sl(ticket, sl)
        return False
