"""
backend/risk/news_filter.py

Economic calendar integration for news filtering.
Supports both live trading and backtesting modes.

Data sources (tried in order):
  1. MT5 built-in calendar (if broker supports it)
  2. Free JSON calendar API (nofbs.com/economic-calendar or similar)
  3. Hardcoded high-impact event schedule (CPI/NFP/FOMC known dates)

Design:
  - Gates on SCHEDULED time only (never actual release) — per the implementation plan.
  - Supports per-currency filtering (USD events block USDCHF, not EURGBP).
  - Configurable blackout windows (before_minutes / after_minutes).
  - Backtesting mode: uses cached historical events from a local JSON file.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from backend.utils.logger import get_logger

logger = get_logger(__name__)

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

try:
    import httpx
except ImportError:
    httpx = None


# ─────────────────────────────────────────────────────────────────────────────
# Currency → Affected Symbols Mapping
# ─────────────────────────────────────────────────────────────────────────────
_CURRENCY_SYMBOLS = {
    "USD": ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD",
            "US30", "US500", "NAS100", "NDX100", "SPX500", "DJI30", "USTEC", "XAUUSD", "XAGUSD"],
    "EUR": ["EURUSD", "EURGBP", "EURJPY", "EURCHF", "EURAUD", "EURCAD", "EURNZD",
            "DE40", "EU50"],
    "GBP": ["GBPUSD", "EURGBP", "GBPJPY", "GBPCHF", "GBPAUD", "GBPCAD", "GBPNZD",
            "UK100"],
    "JPY": ["USDJPY", "EURJPY", "GBPJPY", "AUDJPY", "CADJPY", "CHFJPY", "NZDJPY",
            "JP225"],
    "AUD": ["AUDUSD", "EURAUD", "GBPAUD", "AUDJPY", "AUDCAD", "AUDCHF", "AUDNZD",
            "AU200"],
    "CAD": ["USDCAD", "EURCAD", "GBPCAD", "AUDCAD", "CADCHF", "CADJPY", "NZDCAD"],
    "CHF": ["USDCHF", "EURCHF", "GBPCHF", "AUDCHF", "CADCHF", "CHFJPY", "NZDCHF"],
    "NZD": ["NZDUSD", "EURNZD", "GBPNZD", "AUDNZD", "NZDCAD", "NZDJPY", "NZDCHF"],
    "CNY": ["USDCNH"],
}

# Keywords that identify HIGH impact events
_HIGH_IMPACT_KEYWORDS = frozenset([
    "NFP", "NON-FARM", "CPI", "FOMC", "FED FUNDS", "INTEREST RATE",
    "GDP", "EMPLOYMENT", "UNEMPLOYMENT", "INFLATION", "RETAIL SALES",
    "PMI", "ISM", "PCE", "PPI", "TRADE BALANCE", "CENTRAL BANK",
    "BOE", "ECB", "BOJ", "RBA", "BOC", "SNB", "RBNZ",
])


def _symbol_affected_by_currency(symbol: str, currency: str) -> bool:
    """Check if a symbol is affected by events for a given currency."""
    if not currency or not symbol:
        return True  # Conservative: block if we can't determine
    sym_upper = symbol.upper()
    # Direct currency match in the symbol name
    if currency.upper() in sym_upper:
        return True
    # Check the mapping
    affected = _CURRENCY_SYMBOLS.get(currency.upper(), [])
    return sym_upper in affected


class NewsFilter:
    """
    Filters trading around high-impact economic news events.
    Supports live (MT5/API) and backtest (cached JSON) modes.
    """

    def __init__(
        self,
        enabled: bool = True,
        blackout_before_minutes: int = 15,
        blackout_after_minutes: int = 45,
        cache_dir: str | None = None,
    ):
        self.enabled = enabled
        self.blackout_before = timedelta(minutes=blackout_before_minutes)
        self.blackout_after = timedelta(minutes=blackout_after_minutes)
        self.events: list[dict[str, Any]] = []
        self.last_fetch: datetime | None = None
        self.fetch_interval = timedelta(hours=4)
        self._cache_dir = Path(cache_dir) if cache_dir else Path(__file__).parent.parent / "data" / "calendar_cache"
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    # ── Public API ────────────────────────────────────────────────────────

    async def refresh_calendar(self):
        """Fetch latest high-impact events. Tries MT5 first, then API, then cache."""
        if not self.enabled:
            return

        now = datetime.now(timezone.utc)
        if self.last_fetch and (now - self.last_fetch) < self.fetch_interval:
            return

        events = []

        # Source 1: MT5 built-in calendar
        events = await self._fetch_from_mt5()

        # Source 2: Free API fallback
        if not events:
            events = await self._fetch_from_api()

        # Source 3: Load from cache
        if not events:
            events = self._load_from_cache()

        if events:
            self.events = events
            self.last_fetch = now
            self._save_to_cache(events)
            logger.info(f"News calendar refreshed: {len(events)} high-impact events loaded")
        else:
            logger.warning("News calendar: no events loaded from any source")

    def is_blocked(self, symbol: str = "", check_time: datetime | None = None) -> bool:
        """
        Check if trading is currently blocked by a nearby news event.

        Args:
            symbol: Trading symbol (e.g. "USDCHF"). If empty, blocks conservatively.
            check_time: Time to check against (default: now). Pass a historical
                        datetime for backtesting mode.
        """
        if not self.enabled:
            return False

        now = check_time or datetime.now(timezone.utc)

        for event in self.events:
            event_time = event["scheduled_time"]
            if isinstance(event_time, str):
                try:
                    event_time = datetime.fromisoformat(event_time)
                except ValueError:
                    continue

            # Ensure timezone-aware
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)

            # Check if within blackout window
            window_start = event_time - self.blackout_before
            window_end = event_time + self.blackout_after

            if window_start <= now <= window_end:
                currency = event.get("currency", "")
                if _symbol_affected_by_currency(symbol, currency):
                    logger.info(
                        f"News block active: {event.get('name', 'UNKNOWN')} "
                        f"({currency}) at {event_time.isoformat()} affects {symbol}"
                    )
                    return True

        return False

    def is_blocked_backtest(self, symbol: str, bar_time_epoch: float) -> bool:
        """
        Backtest-mode check. Same logic as is_blocked but accepts epoch time.
        """
        check_time = datetime.fromtimestamp(bar_time_epoch, tz=timezone.utc)
        return self.is_blocked(symbol=symbol, check_time=check_time)

    def get_upcoming_events(self, hours_ahead: int = 24) -> list[dict[str, Any]]:
        """Return events within the next N hours."""
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(hours=hours_ahead)
        result = []
        for e in self.events:
            t = e.get("scheduled_time")
            if isinstance(t, str):
                try:
                    t = datetime.fromisoformat(t)
                except ValueError:
                    continue
            if t and now <= t <= cutoff:
                result.append(e)
        return result

    def load_historical_events(self, start_date: datetime, end_date: datetime):
        """
        Load cached historical events for a backtest date range.
        Call this before running a backtest to populate the events list.
        """
        self.events = []
        # Scan all cached files in the date range
        current = start_date.date()
        end = end_date.date()
        while current <= end:
            cache_file = self._cache_dir / f"{current.isoformat()}.json"
            if cache_file.exists():
                try:
                    with open(cache_file, "r") as f:
                        day_events = json.load(f)
                    self.events.extend(day_events)
                except Exception as e:
                    logger.warning(f"Failed to load cached events for {current}: {e}")
            current += timedelta(days=1)

        logger.info(f"Loaded {len(self.events)} historical news events for backtest range")

    # ── Data Sources ──────────────────────────────────────────────────────

    async def _fetch_from_mt5(self) -> list[dict]:
        """Try MT5 built-in calendar (not all brokers support this)."""
        if not mt5:
            return []

        try:
            # MT5 calendar functions: calendar_request_range
            from_date = datetime.now(timezone.utc) - timedelta(days=1)
            to_date = datetime.now(timezone.utc) + timedelta(days=7)

            import asyncio
            from concurrent.futures import ThreadPoolExecutor
            _executor = ThreadPoolExecutor(max_workers=1)
            loop = asyncio.get_event_loop()

            # mt5.initialize() should already be called by the broker module
            events_raw = await loop.run_in_executor(
                _executor,
                lambda: mt5.calendar_request_range(from_date, to_date) if hasattr(mt5, 'calendar_request_range') else None
            )

            if events_raw is None:
                return []

            events = []
            for ev in events_raw:
                # MT5 CalendarEvent fields: id, type, sector, forecast_value,
                # previous_value, actual_value, name, importance, ...
                importance = getattr(ev, 'importance', 0)
                if importance >= 2:  # HIGH importance only (MT5 uses 0=none, 1=low, 2=medium, 3=high)
                    events.append({
                        "name": getattr(ev, 'name', 'Unknown'),
                        "scheduled_time": datetime.fromtimestamp(
                            getattr(ev, 'time', 0), tz=timezone.utc
                        ).isoformat(),
                        "currency": getattr(ev, 'currency', ''),
                        "impact": "HIGH" if importance >= 3 else "MEDIUM",
                        "source": "mt5_calendar",
                    })

            logger.info(f"MT5 calendar: {len(events)} events fetched")
            return events

        except Exception as e:
            logger.debug(f"MT5 calendar unavailable: {e}")
            return []

    async def _fetch_from_api(self) -> list[dict]:
        """Fetch from a free economic calendar API."""
        if not httpx:
            return []

        # Try multiple free endpoints
        endpoints = [
            "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
        ]

        for url in endpoints:
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.get(url)
                    if response.status_code == 200:
                        raw = response.json()
                        return self._parse_api_events(raw)
            except Exception as e:
                logger.debug(f"Calendar API {url} failed: {e}")
                continue

        return []

    def _parse_api_events(self, raw_events: list[dict]) -> list[dict]:
        """Parse events from the faireconomy/ForexFactory JSON feed."""
        events = []
        for ev in raw_events:
            impact = ev.get("impact", "").upper()
            if impact not in ("HIGH", "HOLIDAY"):
                continue

            title = ev.get("title", "")
            currency = ev.get("country", "")
            date_str = ev.get("date", "")

            try:
                # Format: "2026-08-18T08:30:00-04:00"
                event_time = datetime.fromisoformat(date_str)
                if event_time.tzinfo is None:
                    event_time = event_time.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue

            events.append({
                "name": title,
                "scheduled_time": event_time.astimezone(timezone.utc).isoformat(),
                "currency": currency.upper(),
                "impact": impact,
                "source": "api",
            })

        return events

    # ── Cache ─────────────────────────────────────────────────────────────

    def _save_to_cache(self, events: list[dict]):
        """Save events to per-day JSON files for backtest use."""
        by_day: dict[str, list] = {}
        for ev in events:
            t = ev.get("scheduled_time", "")
            if isinstance(t, str):
                day = t[:10]  # "2026-08-18"
            else:
                day = t.date().isoformat()
            by_day.setdefault(day, []).append(ev)

        for day, day_events in by_day.items():
            cache_file = self._cache_dir / f"{day}.json"
            try:
                with open(cache_file, "w") as f:
                    json.dump(day_events, f, indent=2)
            except Exception as e:
                logger.warning(f"Failed to cache events for {day}: {e}")

    def _load_from_cache(self) -> list[dict]:
        """Load today's and tomorrow's events from cache."""
        events = []
        today = datetime.now(timezone.utc).date()
        for delta in range(0, 8):  # Load next 7 days
            day = (today + timedelta(days=delta)).isoformat()
            cache_file = self._cache_dir / f"{day}.json"
            if cache_file.exists():
                try:
                    with open(cache_file, "r") as f:
                        events.extend(json.load(f))
                except Exception as e:
                    logger.warning(f"Failed to load cache for {day}: {e}")
        return events
