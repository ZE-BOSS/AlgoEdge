"""
backend/mt5/data_fetcher.py

OHLCV Historical and Live Data Fetching.
"""

import pandas as pd
from typing import Optional
from datetime import datetime

from backend.utils.logger import get_logger
import asyncio
from concurrent.futures import ThreadPoolExecutor
import numpy as np

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

logger = get_logger(__name__)

# Executor for blocking MT5 calls
_executor = ThreadPoolExecutor(max_workers=4)

def _get_timeframe_code(tf_str: str):
    if not mt5:
        return tf_str
    
    mapping = {
        "M1": mt5.TIMEFRAME_M1,
        "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30,
        "H1": mt5.TIMEFRAME_H1,
        "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1,
        "W1": mt5.TIMEFRAME_W1,
        "MN1": mt5.TIMEFRAME_MN1
    }
    return mapping.get(tf_str.upper(), mt5.TIMEFRAME_H1)

def _generate_mock_data(symbol: str, count: int) -> pd.DataFrame:
    """Generate dummy OHLCV data when MT5 is missing."""
    dates = pd.date_range(end=datetime.now(), periods=count, freq='h')
    base_price = 1.1000 if "EUR" in symbol else 1900.0 if "XAU" in symbol else 100.0
    
    # Random walk
    closes = base_price + np.random.randn(count).cumsum() * (base_price * 0.001)
    highs = closes + np.random.rand(count) * (base_price * 0.002)
    lows = closes - np.random.rand(count) * (base_price * 0.002)
    opens = np.roll(closes, 1)
    opens[0] = closes[0]
    
    df = pd.DataFrame({
        "time": dates,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "tick_volume": np.random.randint(100, 1000, size=count),
        "spread": np.random.randint(1, 10, size=count),
        "real_volume": np.zeros(count)
    })
    
    # Convert to unix epoch seconds, sort ascending, remove duplicates
    df['time'] = df['time'].astype('int64') // 10**9
    df = df.sort_values('time').drop_duplicates(subset=['time'], keep='last').reset_index(drop=True)
    return df


def _normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize a candle DataFrame: convert time to epoch seconds, sort, dedup."""
    if df.empty:
        return df
    # Convert datetime to epoch seconds if needed
    if pd.api.types.is_datetime64_any_dtype(df['time']):
        df['time'] = df['time'].astype('int64') // 10**9
    elif not pd.api.types.is_integer_dtype(df['time']):
        df['time'] = pd.to_datetime(df['time']).astype('int64') // 10**9
    # Sort ascending by time and remove any duplicate timestamps
    df = df.sort_values('time').drop_duplicates(subset=['time'], keep='last').reset_index(drop=True)
    return df


class DataFetcher:
    """Handles retrieval of historical candles from MT5."""

    @staticmethod
    async def get_historical_data(
        symbol: str, 
        timeframe: str, 
        count: int = 1000
    ) -> pd.DataFrame:
        """
        Fetch OHLCV data for given symbol and timeframe.
        """
        logger.debug(f"Fetching {count} candles for {symbol} on {timeframe}")
        
        if not mt5:
            return _generate_mock_data(symbol, count)
            
        tf_code = _get_timeframe_code(timeframe)
        
        loop = asyncio.get_event_loop()
        rates = await loop.run_in_executor(
            _executor, 
            lambda: mt5.copy_rates_from_pos(symbol, tf_code, 0, count)
        )
        
        if rates is None or len(rates) == 0:
            logger.error(f"Failed to fetch data for {symbol} {timeframe}")
            return pd.DataFrame()
            
        df = pd.DataFrame(rates)
        return _normalize_df(df)

    @staticmethod
    async def get_data_range(
        symbol: str, 
        timeframe: str, 
        start: datetime, 
        end: datetime
    ) -> pd.DataFrame:
        """
        Fetch OHLCV data for a specific date range (for backtesting).
        """
        if not mt5:
            return _generate_mock_data(symbol, 1000)
            
        tf_code = _get_timeframe_code(timeframe)
        
        loop = asyncio.get_event_loop()
        rates = await loop.run_in_executor(
            _executor, 
            lambda: mt5.copy_rates_range(symbol, tf_code, start, end)
        )
        
        if rates is None or len(rates) == 0:
            return pd.DataFrame()
            
        df = pd.DataFrame(rates)
        return _normalize_df(df)
