from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
import pandas as pd

class BaseBroker(ABC):
    """
    Abstract base class for all broker integrations (MT5, cTrader, REST, etc).
    All brokers must implement this interface to be compatible with BotService.
    """
    
    @abstractmethod
    async def initialize(self) -> bool:
        """Initialize connection to the broker terminal/API."""
        pass

    @abstractmethod
    async def shutdown(self):
        """Shutdown connection to the broker terminal/API."""
        pass

    @abstractmethod
    async def check_connection(self) -> bool:
        """Check if the broker connection is currently active."""
        pass

    @abstractmethod
    async def get_rates(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        """
        Fetch historical rates for a symbol.
        Returns a DataFrame with ['time', 'open', 'high', 'low', 'close', 'tick_volume']
        """
        pass
        
    @abstractmethod
    async def get_symbol_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Fetch symbol information (point, digits, spread, etc.).
        """
        pass
        
    @abstractmethod
    async def get_account_info(self) -> Optional[Dict[str, Any]]:
        """
        Fetch account information (balance, equity, margin, etc.).
        """
        pass
        
    @abstractmethod
    async def get_open_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Fetch open positions, optionally filtered by symbol.
        """
        pass

    @abstractmethod
    async def execute_market_order(self, symbol: str, direction: str, volume: float, sl: Optional[float] = None, tp: Optional[float] = None, comment: str = "") -> Optional[Dict[str, Any]]:
        """
        Execute a market order.
        direction: 'BUY' or 'SELL'
        """
        pass

    @abstractmethod
    async def close_position(self, ticket: int, volume: Optional[float] = None) -> bool:
        """
        Close an open position (partially or fully).
        """
        pass

    @abstractmethod
    async def modify_position(self, ticket: int, sl: Optional[float] = None, tp: Optional[float] = None) -> bool:
        """
        Modify Stop Loss and Take Profit of an existing position.
        """
        pass
