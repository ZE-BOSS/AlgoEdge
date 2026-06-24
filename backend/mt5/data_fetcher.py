"""
backend/mt5/data_fetcher.py

OHLCV Historical and Live Data Fetching.
Provides explicit error reporting when fetch fails rather than returning silent empty DataFrames.
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


def _generate_mock_data(symbol: str, count: int, timeframe: str = "H1",
                        start_date: datetime = None, end_date: datetime = None) -> pd.DataFrame:
    """
    Generate realistic mock OHLCV data when MT5 is not available.
    Uses symbol-specific base prices and volatility profiles.
    If start_date and end_date are provided, generates data spanning that range.
    """
    # Determine candle interval based on timeframe
    freq_map = {
        "M1": "1min", "M5": "5min", "M15": "15min", "M30": "30min",
        "H1": "1h", "H4": "4h", "D1": "1D", "W1": "1W", "MN1": "1ME",
    }
    freq = freq_map.get(timeframe.upper(), "1h")
    
    # Use date range if provided, otherwise use count from now
    if start_date and end_date:
        dates = pd.date_range(start=start_date, end=end_date, freq=freq)
        count = len(dates)
        if count < 10:
            dates = pd.date_range(start=start_date, periods=max(100, count), freq=freq)
            count = len(dates)
    else:
        dates = pd.date_range(end=datetime.now(), periods=count, freq=freq)
    
    # Symbol-specific base prices and volatility
    sym_upper = symbol.upper()
    if "XAU" in sym_upper or "GOLD" in sym_upper:
        base_price = 2350.0
        volatility = 0.003
    elif "EUR" in sym_upper:
        base_price = 1.0850
        volatility = 0.001
    elif "GBP" in sym_upper:
        base_price = 1.2700
        volatility = 0.0012
    elif "JPY" in sym_upper:
        base_price = 157.50
        volatility = 0.0008
    elif "US30" in sym_upper:
        base_price = 39500.0
        volatility = 0.004
    elif "BTC" in sym_upper:
        base_price = 65000.0
        volatility = 0.015
    elif any(s in sym_upper for s in ["V75", "VOLATILITY 75"]):
        base_price = 450000.0
        volatility = 0.02
    elif any(s in sym_upper for s in ["BOOM", "CRASH"]):
        base_price = 9500.0
        volatility = 0.01
    elif any(s in sym_upper for s in ["JUMP", "STEP"]):
        base_price = 1200.0
        volatility = 0.008
    else:
        base_price = 100.0
        volatility = 0.002
    
    # Generate price with trend phases and pullbacks (more realistic than pure random walk)
    np.random.seed(None)
    # Create trend regime changes every ~200 bars
    regime_length = max(50, count // 10)
    n_regimes = max(1, count // regime_length)
    trends = []
    for _ in range(n_regimes):
        # Each regime: trend direction + strength
        trend_dir = np.random.choice([-1, 1])
        trend_strength = np.random.uniform(0.0001, 0.0005) * trend_dir
        noise = np.random.randn(regime_length) * volatility
        segment = noise + trend_strength
        trends.extend(segment.tolist())
    
    # Trim or extend to match count
    returns = np.array(trends[:count])
    if len(returns) < count:
        extra = np.random.randn(count - len(returns)) * volatility
        returns = np.concatenate([returns, extra])
    
    closes = base_price * np.exp(np.cumsum(returns))
    
    # Generate realistic OHLC from close with proper candle structure
    candle_range = volatility * base_price * 0.8
    highs = closes + np.abs(np.random.randn(count)) * candle_range
    lows = closes - np.abs(np.random.randn(count)) * candle_range
    opens = np.roll(closes, 1)
    opens[0] = closes[0]
    
    # Ensure OHLC consistency: high >= max(open,close), low <= min(open,close)
    highs = np.maximum(highs, np.maximum(opens, closes))
    lows = np.minimum(lows, np.minimum(opens, closes))
    
    # Generate realistic volume with session patterns
    base_volume = np.random.randint(500, 3000, size=count)
    # Add volume spikes at regular intervals (simulating high-impact events)
    spike_indices = np.random.choice(count, size=max(1, count // 50), replace=False)
    base_volume[spike_indices] *= np.random.randint(3, 8, size=len(spike_indices))
    
    df = pd.DataFrame({
        "time": dates,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "tick_volume": base_volume,
        "spread": np.random.randint(1, 10, size=count),
        "real_volume": np.zeros(count)
    })
    
    # Convert to unix epoch seconds, sort ascending, remove duplicates
    df['time'] = df['time'].astype('int64') // 10**9
    df = df.sort_values('time').drop_duplicates(subset=['time'], keep='last').reset_index(drop=True)
    
    logger.info(f"Generated {len(df)} mock candles for {symbol} ({timeframe})")
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


class DataFetchError(Exception):
    """Raised when MT5 data fetch fails with an explicit reason."""
    def __init__(self, symbol: str, timeframe: str, reason: str):
        self.symbol = symbol
        self.timeframe = timeframe
        self.reason = reason
        super().__init__(f"Data fetch failed for {symbol} {timeframe}: {reason}")


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
        Returns a normalized DataFrame or raises DataFetchError on failure.
        """
        logger.debug(f"Fetching {count} candles for {symbol} on {timeframe}")
        
        if not mt5:
            logger.info(f"MT5 not available — generating mock data for {symbol} ({timeframe})")
            return _generate_mock_data(symbol, count, timeframe)
            
        tf_code = _get_timeframe_code(timeframe)
        
        # Check if symbol exists
        loop = asyncio.get_event_loop()
        symbol_info = await loop.run_in_executor(
            _executor, lambda: mt5.symbol_info(symbol)
        )
        if symbol_info is None:
            error_msg = f"Symbol '{symbol}' not found in MT5. Check broker symbol list."
            logger.error(error_msg)
            raise DataFetchError(symbol, timeframe, error_msg)
        
        # Ensure symbol is selected in Market Watch
        if not symbol_info.visible:
            await loop.run_in_executor(
                _executor, lambda: mt5.symbol_select(symbol, True)
            )
        
        rates = await loop.run_in_executor(
            _executor, 
            lambda: mt5.copy_rates_from_pos(symbol, tf_code, 0, count)
        )
        
        if rates is None or len(rates) == 0:
            mt5_error = mt5.last_error()
            error_msg = (
                f"MT5 returned no data for {symbol} {timeframe}. "
                f"MT5 error: {mt5_error}. "
                f"Possible causes: market closed, symbol not subscribed, "
                f"or insufficient history for {count} candles."
            )
            logger.error(error_msg)
            raise DataFetchError(symbol, timeframe, error_msg)
            
        df = pd.DataFrame(rates)
        df = _normalize_df(df)
        logger.info(f"Fetched {len(df)} candles for {symbol} {timeframe}")
        return df

    @staticmethod
    async def get_data_range(
        symbol: str, 
        timeframe: str, 
        start: datetime, 
        end: datetime
    ) -> pd.DataFrame:
        """
        Fetch OHLCV data for a specific date range (for backtesting).
        Returns a normalized DataFrame or raises DataFetchError on failure.
        """
        logger.info(f"Fetching data range for {symbol} {timeframe}: {start} → {end}")
        
        if not mt5:
            # Estimate candle count from date range and timeframe
            range_hours = (end - start).total_seconds() / 3600
            candles_per_hour = {"M1": 60, "M5": 12, "M15": 4, "M30": 2, "H1": 1, "H4": 0.25, "D1": 1/24}
            multiplier = candles_per_hour.get(timeframe.upper(), 1)
            estimated_count = max(100, int(range_hours * multiplier))
            logger.info(f"MT5 not available — generating {estimated_count} mock candles for date range")
            return _generate_mock_data(symbol, estimated_count, timeframe, start_date=start, end_date=end)
            
        tf_code = _get_timeframe_code(timeframe)
        
        loop = asyncio.get_event_loop()
        rates = await loop.run_in_executor(
            _executor, 
            lambda: mt5.copy_rates_range(symbol, tf_code, start, end)
        )
        
        if rates is None or len(rates) == 0:
            mt5_error = mt5.last_error()
            error_msg = (
                f"MT5 returned no data for {symbol} {timeframe} "
                f"between {start.isoformat()} and {end.isoformat()}. "
                f"MT5 error: {mt5_error}. "
                f"Possible causes: date range outside available history, "
                f"market was closed, or symbol not subscribed."
            )
            logger.error(error_msg)
            raise DataFetchError(symbol, timeframe, error_msg)
            
        df = pd.DataFrame(rates)
        df = _normalize_df(df)
        logger.info(f"Fetched {len(df)} candles for {symbol} {timeframe} ({start.date()} → {end.date()})")
        return df
