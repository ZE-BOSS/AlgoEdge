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


class MT5Bridge:
    """
    Singleton manager for MT5 terminal connection.
    Uses ThreadPoolExecutor for blocking MT5 Python API calls.
    """
    
    def __init__(self):
        self.connected = False
        self._executor = ThreadPoolExecutor(max_workers=4)
        self.account_info = None
        self._connected_account: Optional[int] = None  # Track which account is connected

    async def connect(self, config_override=None) -> bool:
        """
        Initialize MT5 connection using .env settings.
        Auto-reconnects on failure.
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

        self.account_info = mt5.account_info()
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

        self.account_info = mt5.account_info()
        self._connected_account = account
        self.connected = True
        logger.info(f"Deriv MT5 connected for user {user_id}: account={account}")
        return True

    async def disconnect(self):
        """Shutdown connection."""
        logger.info("Disconnecting MT5...")
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

    def get_connected_account(self) -> Optional[int]:
        """Return the currently connected MT5 account number."""
        return self._connected_account


bridge = MT5Bridge()
