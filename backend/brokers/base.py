from abc import ABC, abstractmethod
from typing import Any

import pandas as pd


class BaseBroker(ABC):
    """
    Abstract base class for all broker integrations (MT5, cTrader, REST, etc).
    All brokers must implement this interface to be compatible with BotService.
    """
    
    @abstractmethod
    async def initialize(self) -> bool:
        """Initialize connection to the broker terminal/API."""

    @abstractmethod
    async def shutdown(self):
        """Shutdown connection to the broker terminal/API."""

    @abstractmethod
    async def check_connection(self) -> bool:
        """Check if the broker connection is currently active."""

    @abstractmethod
    async def get_rates(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        """
        Fetch historical rates for a symbol.
        Returns a DataFrame with ['time', 'open', 'high', 'low', 'close', 'tick_volume']
        """
        
    @abstractmethod
    async def get_symbol_info(self, symbol: str) -> dict[str, Any] | None:
        """
        Fetch symbol information (point, digits, spread, etc.).
        """
        
    @abstractmethod
    async def get_account_info(self) -> dict[str, Any] | None:
        """
        Fetch account information (balance, equity, margin, etc.).
        """
        
    @abstractmethod
    async def get_open_positions(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """
        Fetch open positions, optionally filtered by symbol.
        """

    @abstractmethod
    async def execute_market_order(self, symbol: str, direction: str, volume: float, sl: float | None = None, tp: float | None = None, comment: str = "") -> dict[str, Any] | None:
        """
        Execute a market order.
        direction: 'BUY' or 'SELL'
        """

    @abstractmethod
    async def close_position(self, ticket: int, volume: float | None = None) -> bool:
        """
        Close an open position (partially or fully).
        """

    @abstractmethod
    async def modify_position(self, ticket: int, sl: float | None = None, tp: float | None = None) -> bool:
        """
        Modify Stop Loss and Take Profit of an existing position.
        """
