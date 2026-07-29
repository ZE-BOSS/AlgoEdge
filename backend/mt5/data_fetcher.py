"""
backend/mt5/data_fetcher.py

OHLCV Historical and Live Data Fetching.
Provides explicit error reporting when fetch fails rather than returning silent empty DataFrames.
"""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pandas as pd

from backend.utils.logger import get_logger

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

logger = get_logger(__name__)

# Executor for blocking MT5 calls — single worker to serialize MT5 access
_executor = ThreadPoolExecutor(max_workers=1)

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



def _normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize a candle DataFrame: convert time to epoch seconds, sort, dedup."""
    if df.empty:
        return df
    # Convert datetime to epoch seconds if needed
    if pd.api.types.is_datetime64_any_dtype(df['time']):
        if df['time'].dt.tz is not None:
            df['time'] = df['time'].dt.tz_localize(None)
        df['time'] = df['time'].astype('int64') // 10**9
    elif not pd.api.types.is_integer_dtype(df['time']):
        time_series = pd.to_datetime(df['time'])
        if time_series.dt.tz is not None:
            time_series = time_series.dt.tz_localize(None)
        df['time'] = time_series.astype('int64') // 10**9
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

    _cache = {}
    _cache_time = {}
    CACHE_DURATION = 30  # seconds — prevents hammering MT5 with repeated calls

    @classmethod
    async def get_historical_data(
        cls,
        symbol: str, 
        timeframe: str, 
        count: int = 1000
    ) -> pd.DataFrame:
        """
        Fetch OHLCV data for given symbol and timeframe.
        Returns a normalized DataFrame or raises DataFetchError on failure.
        """
        logger.debug(f"Fetching {count} candles for {symbol} on {timeframe}")
        
        cache_key = f"{symbol}_{timeframe}_{count}"
        now = time.time()
        if cache_key in cls._cache and now - cls._cache_time.get(cache_key, 0) < cls.CACHE_DURATION:
            logger.debug(f"Returning cached data for {symbol} on {timeframe}")
            return cls._cache[cache_key].copy()

        if not mt5:
            error_msg = "MT5 terminal is not available on this system. Cannot fetch live/historical data."
            logger.error(error_msg)
            raise DataFetchError(symbol, timeframe, error_msg)
            
        tf_code = _get_timeframe_code(timeframe)
        
        # Check if symbol exists
        loop = asyncio.get_running_loop()
        symbol_info = await loop.run_in_executor(
            _executor, lambda: mt5.symbol_info(symbol)
        )
        if symbol_info is None:
            # The symbol might just not be in Market Watch yet, try selecting it
            selected = await loop.run_in_executor(
                _executor, lambda: mt5.symbol_select(symbol, True)
            )
            if selected:
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
        
        cls._cache[cache_key] = df.copy()
        cls._cache_time[cache_key] = time.time()
        
        return df

    @classmethod
    async def get_data_range(
        cls,
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
            error_msg = "MT5 terminal is not available on this system. Cannot fetch live/historical data."
            logger.error(error_msg)
            raise DataFetchError(symbol, timeframe, error_msg)
            
        tf_code = _get_timeframe_code(timeframe)
        
        # Convert to epoch to avoid timezone mismatch issues
        start_ts = int(start.timestamp()) if hasattr(start, 'timestamp') else start
        end_ts = int(end.timestamp()) if hasattr(end, 'timestamp') else end

        loop = asyncio.get_running_loop()
        rates = None
        for attempt in range(2):
            rates = await loop.run_in_executor(
                _executor, 
                lambda: mt5.copy_rates_range(symbol, tf_code, start_ts, end_ts)
            )
            if rates is not None and len(rates) > 0:
                break
                
            mt5_err = await loop.run_in_executor(_executor, mt5.last_error)
            if mt5_err[0] == 1:
                logger.info(f"MT5 downloading history for {symbol} {timeframe}, attempt {attempt+1}... waiting 2s")
                await asyncio.sleep(2.0)
            else:
                break

        
        if rates is None or len(rates) == 0:
            # Fallback: copy_rates_range often fails with (-2, Invalid params) if start_ts is too old.
            # Attempt to fetch using copy_rates_from which is more resilient to history bounds.
            tf_minutes = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}
            minutes = tf_minutes.get(timeframe, 5)
            estimated_bars = min(int((end_ts - start_ts) / (minutes * 60)), 200000) # Cap at 200k bars
            logger.info(f"copy_rates_range failed for {timeframe}. Falling back to copy_rates_from with count={estimated_bars}")
            rates = await loop.run_in_executor(
                _executor, 
                lambda: mt5.copy_rates_from(symbol, tf_code, end_ts, estimated_bars)
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
        
        # Trim to the exact requested range
        df = df[(df['time'] >= start_ts) & (df['time'] <= end_ts)]
        
        if df.empty:
            error_msg = f"Data available but none falls within requested range {start} → {end} for {symbol} {timeframe}."
            logger.error(error_msg)
            raise DataFetchError(symbol, timeframe, error_msg)
            
        logger.info(f"Fetched {len(df)} candles for {symbol} {timeframe} ({start.date()} → {end.date()})")
        return df
