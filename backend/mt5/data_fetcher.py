"""
backend/mt5/data_fetcher.py

OHLCV Historical and Live Data Fetching.
Provides explicit error reporting when fetch fails rather than returning silent empty DataFrames.
"""

import asyncio
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytz

from backend.utils.logger import get_logger
from backend.mt5.executor import mt5_executor

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

logger = get_logger(__name__)

# Cached broker UTC offset in hours (None = not yet measured).
_SERVER_OFFSET_CACHE: float | None = None
_OVERRIDE_CHECKED = False
_PINNED_CHECK: dict = {}


def server_offset_status() -> dict:
    """What the bot is using to turn broker bar times into UTC, for /api/health.
    Never calls MT5: it reports what has already been decided."""
    override = os.environ.get("ALGOEDGE_MT5_SERVER_UTC_OFFSET")
    if override is not None and override.strip() != "":
        return {"source": "env", "hours": _PINNED_CHECK.get("pinned", override),
                "bars_say": _PINNED_CHECK.get("measured")}
    return {"source": "detected", "hours": _SERVER_OFFSET_CACHE}

# Executor for blocking MT5 calls — single worker to serialize MT5 access
# Aliased to the process-wide single MT5 thread. This module used to own a
# separate pool, which meant MT5 was called from a thread that did not hold
# the terminal connection. See backend/mt5/executor.py.
_executor = mt5_executor

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



_TF_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}


def terminal_max_bars() -> int:
    """How many bars this terminal will serve in ONE history request.

    MT5's `maxbars` (Tools > Options > Charts > "Max bars in chart") caps every
    history request, and `copy_rates_range` measures a request against the number
    of PERIODS the span covers rather than the number of bars that exist inside
    it. So an 11-month M5 range holding 68,617 real bars is still refused with
    (-2, 'Terminal: Invalid params'), because the span is 101,952 five-minute
    slots against a `maxbars` of 100,000. A request for exactly `maxbars` is
    refused as well, hence the margin.

    Measured on the FundedNext terminal, 2026-09-25: 99,000 bars returns 99,000;
    100,000 and above return nothing with (-2). Before this, `get_data_range`
    asked `copy_rates_from` for up to 200,000 as its fallback, so any M5 backtest
    whose window plus warm-up exceeded a year failed outright.
    """
    try:
        n = int(getattr(mt5.terminal_info(), "maxbars", 0) or 0)
    except Exception:
        n = 0
    return max(1000, int((n or 100_000) * 0.95))


_NY = pytz.timezone("America/New_York")
# Candidate server offsets, in hours. Real MT5 servers sit on whole or half hours
# between UTC-2 and UTC+5; scoring a continuum would only fit noise.
_OFFSET_CANDIDATES = [h / 2 for h in range(-4, 11)]
# Symbols to read the cash open off, best first: an index has the sharpest jump.
_OFFSET_PROBE_SYMBOLS = ("US30", "SPX500", "NAS100", "US100", "USTEC", "US Tech 100",
                         "GER40", "Germany 40", "XAUUSD", "EURUSD")
_OFFSET_PROBE_BARS = 12 * 288          # ~12 days of M5


def _ny_open_epoch(utc_day: int) -> int:
    """09:30 New York on the true-UTC day `utc_day`, as epoch seconds."""
    d = datetime.fromtimestamp(utc_day * 86400, timezone.utc).date()
    return int(_NY.localize(datetime(d.year, d.month, d.day, 9, 30)).timestamp())


def _offset_from_volume_profile() -> float | None:
    """The server's UTC offset, read from WHERE the New York cash open sits.

    Independent of whether the market is open right now, which is the whole point
    — see detect_server_utc_offset_hours. Index (and FX) tick volume jumps at
    09:30 New York; the boundary is resolved per DAY through pytz, so it is right
    on both sides of a DST change, and only whole/half-hour candidates are scored.

    Returns None rather than a guess when no probe symbol has usable bars.
    """
    if mt5 is None:
        return None
    for symbol in _OFFSET_PROBE_SYMBOLS:
        try:
            rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, _OFFSET_PROBE_BARS)
        except Exception:
            continue
        if rates is None or len(rates) < 2000:
            continue
        t = np.asarray([int(r[0]) for r in rates], dtype=np.int64)
        vol = np.asarray([float(r[5]) for r in rates], dtype=float)   # tick_volume
        if vol.sum() <= 0:
            continue
        best, best_score = None, -1.0
        for off in _OFFSET_CANDIDATES:
            true_utc = t - int(off * 3600)
            days = true_utc // 86400
            opens = {int(d): _ny_open_epoch(int(d)) for d in np.unique(days)}
            base = np.asarray([opens[int(d)] for d in days], dtype=np.int64)
            rel = (true_utc - base) // 60
            inside = (rel >= 0) & (rel < 30)          # the opening half hour
            if not inside.any():
                continue
            score = float(vol[inside].mean())
            if score > best_score:
                best, best_score = off, score
        if best is not None:
            logger.info(f"[DATA] Server offset from {symbol}'s cash open: {best:+.1f}h")
            return best
    return None


def detect_server_utc_offset_hours() -> float:
    """
    Return the broker server's UTC offset in hours, so bar timestamps can be
    converted to TRUE UTC.

    MT5 reports bar times in SERVER time, encoded as a Unix epoch as though that
    server time were UTC. Reading it as UTC therefore shifts every timestamp by the
    server's offset, and every downstream Eastern-Time session gate with it.

    Measured on the supplied runs: the FX trading week ran Mon 00:00 -> Sat 00:00 in
    stored time, with ZERO Saturday/Sunday bars. The real FX week opens Sun 21:00 UTC
    and closes Fri 21:00 UTC, and Sun 21:00 UTC == Mon 00:00 at UTC+3 — so the feed
    was UTC+3 being read as UTC. Consequence: the code's "09:30 ET" was really
    06:30 ET, and every session-anchored strategy (VWAP, NY Open Retest, CRT, APA)
    traded three hours earlier than documented — the London morning, not the NY open.

    Preference order:
      1. ALGOEDGE_MT5_SERVER_UTC_OFFSET env var (explicit override, in hours)
      1b. WHERE THE NEW YORK CASH OPEN SITS IN THE BARS, which does not care
          whether the market is open now. A tick-based estimate is only trusted
          when it agrees with this one, because a STALE TICK CAN ONLY
          UNDERESTIMATE: the tick is in the past, so `tick.time - now` is the
          offset MINUS however long the market has been shut. Measured on the
          FundedNext terminal on 2026-09-25, whose real offset is +3h: two
          backends started 33 minutes apart, both after the Friday close, read
          +2.0h and +1.0h. Every session-anchored strategy -- VWAP, ORB, APA's
          session modes, IVW's day-end flat, OvernightSession_v1 and
          OpeningDrive_v1 -- therefore traded up to two hours early, and which
          hours depended on when the backend happened to start.
      2. Live measurement: the gap between the server's clock and true UTC
      3. 0.0 — assume already-UTC, and warn
    """
    global _SERVER_OFFSET_CACHE, _OVERRIDE_CHECKED
    override = os.environ.get("ALGOEDGE_MT5_SERVER_UTC_OFFSET")
    if override is not None and override.strip() != "":
        try:
            pinned = float(override)
        except ValueError:
            logger.warning(f"[DATA] Invalid ALGOEDGE_MT5_SERVER_UTC_OFFSET={override!r}; ignoring.")
        else:
            # A pinned offset is right for ONE broker. +3 is FundedNext's clock;
            # left in .env after switching the terminal to Deriv (UTC+0) it made
            # every session strategy trade three hours late (2026-09-29). So once
            # per process, compare it with what the bars say and shout if they
            # disagree -- the pin still wins, because it is an explicit choice.
            if not _OVERRIDE_CHECKED:
                _OVERRIDE_CHECKED = True
                try:
                    measured = _offset_from_volume_profile()
                except Exception:
                    measured = None
                if measured is not None and measured != pinned:
                    logger.warning(
                        f"[DATA] ALGOEDGE_MT5_SERVER_UTC_OFFSET is pinned to {pinned:+.1f}h but this "
                        f"broker's bars put the New York open at {measured:+.1f}h. Every session "
                        f"strategy is trading {pinned - measured:+.1f}h off. Remove the line from "
                        f".env (or set it to {measured:g}) and restart.")
                else:
                    logger.info(f"[DATA] MT5 server UTC offset pinned by .env: {pinned:+.1f}h"
                                + (" (matches the bars)" if measured is not None else ""))
                _PINNED_CHECK["pinned"], _PINNED_CHECK["measured"] = pinned, measured
            return pinned

    if _SERVER_OFFSET_CACHE is not None:
        return _SERVER_OFFSET_CACHE

    from_ticks = None
    try:
        if mt5 is not None:
            tick = mt5.symbol_info_tick("EURUSD")
            if tick and getattr(tick, "time", 0):
                # tick.time is server time as a pseudo-UTC epoch; compare with real UTC.
                delta_h = (tick.time - datetime.now(timezone.utc).timestamp()) / 3600.0
                # Broker offsets are whole (occasionally half) hours; snap to the
                # nearest half hour to absorb latency and clock skew.
                offset = round(delta_h * 2) / 2
                if -12 <= offset <= 14:
                    from_ticks = offset
    except Exception as e:
        logger.debug(f"[DATA] Tick-based server offset failed: {e}")

    from_bars = None
    try:
        from_bars = _offset_from_volume_profile()
    except Exception as e:
        logger.debug(f"[DATA] Bar-based server offset failed: {e}")

    # A stale tick reads LOW, never high, so the larger estimate is the one to
    # trust when they disagree. Agreement is the common case and says nothing new.
    if from_bars is not None and from_ticks is not None and from_bars != from_ticks:
        logger.warning(
            f"[DATA] Server UTC offset: the live tick says {from_ticks:+.1f}h but the "
            f"New York cash open sits at {from_bars:+.1f}h. A tick read while the market "
            f"is shut can only read LOW, so using {max(from_bars, from_ticks):+.1f}h. "
            f"Set ALGOEDGE_MT5_SERVER_UTC_OFFSET to pin it."
        )
    chosen = max([x for x in (from_bars, from_ticks) if x is not None], default=None)
    if chosen is not None:
        _SERVER_OFFSET_CACHE = chosen
        logger.info(f"[DATA] Detected MT5 server UTC offset: {chosen:+.1f}h")
        return chosen

    logger.warning(
        "[DATA] Could not determine MT5 server UTC offset — assuming server time IS UTC. "
        "If your broker is not UTC, every ET session gate will be shifted by the offset. "
        "Set ALGOEDGE_MT5_SERVER_UTC_OFFSET to correct this."
    )
    _SERVER_OFFSET_CACHE = 0.0
    return 0.0


def _normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize a candle DataFrame: convert time to epoch seconds, shift server time
    to TRUE UTC, sort, dedup.

    The server->UTC shift is what makes every downstream `astimezone(America/New_York)`
    conversion correct. See detect_server_utc_offset_hours().
    """
    if df.empty:
        return df
    # Convert datetime to epoch seconds if needed
    if pd.api.types.is_datetime64_any_dtype(df['time']):
        if df['time'].dt.tz is not None:
            df['time'] = df['time'].dt.tz_localize(None)
        # as_unit('s'): correct whatever resolution pandas stored (ns on 2.x, us on 3.x).
        df['time'] = df['time'].dt.as_unit('s').astype('int64')
    elif not pd.api.types.is_integer_dtype(df['time']):
        time_series = pd.to_datetime(df['time'])
        if time_series.dt.tz is not None:
            time_series = time_series.dt.tz_localize(None)
        df['time'] = time_series.dt.as_unit('s').astype('int64')
    # Shift broker server time -> true UTC. Everything downstream (ET session gates,
    # VWAP session anchoring, swap rollover boundaries) assumes UTC and is wrong by
    # exactly this offset without it.
    offset_h = detect_server_utc_offset_hours()
    if offset_h:
        df['time'] = df['time'] - int(round(offset_h * 3600))

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


def _fetch_rates_frame(symbol: str, tf_code: int, timeframe: str, count: int):
    """The blocking half of DataFetcher.get_historical_data, run on the MT5 thread.
    Returns (normalised frame, None) or (None, error message)."""
    terminal_info = mt5.terminal_info()
    if terminal_info is None or not terminal_info.connected:
        logger.warning("MT5 connection lost in DataFetcher. Attempting to re-initialize IPC...")
        mt5.initialize()
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        # The symbol might just not be in Market Watch yet, try selecting it
        if mt5.symbol_select(symbol, True):
            symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            return None, f"Symbol '{symbol}' not found in MT5. Check broker symbol list."
    if not symbol_info.visible:
        mt5.symbol_select(symbol, True)
    rates = mt5.copy_rates_from_pos(symbol, tf_code, 0, count)
    if rates is None or len(rates) == 0:
        return None, (
            f"MT5 returned no data for {symbol} {timeframe}. "
            f"MT5 error: {mt5.last_error()}. "
            f"Possible causes: market closed, symbol not subscribed, "
            f"or insufficient history for {count} candles."
        )
    return _normalize_df(pd.DataFrame(rates)), None


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

        # Everything that touches MT5 or builds the frame runs on the MT5 thread.
        # This used to run on the event loop — terminal_info(), a blocking
        # re-initialise when IPC dropped, and a 5,000-row DataFrame build and
        # normalise per timeframe per slot per scan — and stalled every API call
        # and WebSocket ~0.4 s at a time while the bot scanned (measured 2026-09-15).
        tf_code = _get_timeframe_code(timeframe)
        loop = asyncio.get_running_loop()
        df, error_msg = await loop.run_in_executor(
            _executor, lambda: _fetch_rates_frame(symbol, tf_code, timeframe, count)
        )
        if error_msg:
            logger.error(error_msg)
            raise DataFetchError(symbol, timeframe, error_msg)
        logger.info(f"Fetched {len(df)} candles for {symbol} {timeframe}")

        cls._cache[cache_key] = df
        cls._cache_time[cache_key] = time.time()
        return df.copy()

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

        # ── IPC Auto-Recovery ──
        terminal_info = mt5.terminal_info()
        if terminal_info is None or not terminal_info.connected:
            logger.warning("MT5 connection lost in DataFetcher. Attempting to re-initialize IPC...")
            mt5.initialize()
            
        tf_code = _get_timeframe_code(timeframe)
        
        # Convert to epoch to avoid timezone mismatch issues
        start_ts = int(start.timestamp()) if hasattr(start, 'timestamp') else start
        end_ts = int(end.timestamp()) if hasattr(end, 'timestamp') else end

        loop = asyncio.get_running_loop()
        # Slice the range so no single request covers more PERIODS than the
        # terminal will serve — see terminal_max_bars(). A window short enough
        # is still one request, so the common case is byte-for-byte unchanged.
        step = _TF_MINUTES.get(str(timeframe).upper(), 5) * 60
        max_span = terminal_max_bars() * step
        cuts = list(range(start_ts, end_ts, max_span)) or [start_ts]
        cuts.append(end_ts)
        if len(cuts) > 2:
            logger.info(
                f"{symbol} {timeframe}: {end_ts - start_ts} seconds is more than this "
                f"terminal serves in one request; fetching in {len(cuts) - 1} slices"
            )
        pieces = []
        for lo_ts, hi_ts in zip(cuts, cuts[1:]):
            got = None
            for attempt in range(2):
                got = await loop.run_in_executor(
                    _executor,
                    lambda a=lo_ts, b=hi_ts: mt5.copy_rates_range(symbol, tf_code, a, b)
                )
                if got is not None and len(got) > 0:
                    break

                mt5_err = await loop.run_in_executor(_executor, mt5.last_error)
                if mt5_err[0] == 1:
                    logger.info(f"MT5 downloading history for {symbol} {timeframe}, attempt {attempt+1}... waiting 2s")
                    await asyncio.sleep(2.0)
                else:
                    break
            # A slice can legitimately be empty (a holiday week, or history that
            # starts mid-range); only every slice being empty is a failure.
            if got is not None and len(got) > 0:
                pieces.append(got)
        rates = np.concatenate(pieces) if pieces else None

        
        if rates is None or len(rates) == 0:
            # Fallback: copy_rates_range often fails with (-2, Invalid params) if start_ts is too old.
            # Attempt to fetch using copy_rates_from which is more resilient to history bounds.
            minutes = _TF_MINUTES.get(str(timeframe).upper(), 5)
            # Capped at what the terminal will actually serve: 200,000 was above
            # every real `maxbars`, so this fallback could only ever fail too.
            estimated_bars = min(int((end_ts - start_ts) / (minutes * 60)), terminal_max_bars())
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
