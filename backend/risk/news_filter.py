"""
backend/risk/news_filter.py

Economic calendar feed integration for news filtering.
Source: TradingBot_MasterPlan-2.md Session & News Filters
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional
from backend.utils.logger import get_logger

logger = get_logger(__name__)

try:
    import feedparser
except ImportError:
    feedparser = None

try:
    import httpx
except ImportError:
    httpx = None


class NewsFilter:
    """
    Filters trading around high-impact economic news events.
    Uses ForexFactory RSS feed for free calendar data.
    Source: TradingBot_MasterPlan-2.md — Session & News Filters
    """

    FOREX_FACTORY_RSS = "https://www.forexfactory.com/rss.xml"
    BLOCK_WINDOW_MINUTES = 30  # ±30 minutes around HIGH-impact news

    def __init__(self, enabled: bool = True, block_window_minutes: int = 30):
        self.enabled = enabled
        self.block_window = timedelta(minutes=block_window_minutes)
        self.high_impact_events: List[Dict[str, Any]] = []
        self.last_fetch: Optional[datetime] = None
        self.fetch_interval = timedelta(hours=4)  # Refresh every 4 hours

    async def refresh_calendar(self):
        """Fetch latest high-impact events from ForexFactory RSS."""
        if not self.enabled:
            return

        if self.last_fetch and datetime.now(timezone.utc) - self.last_fetch < self.fetch_interval:
            return  # Don't fetch too often

        try:
            if httpx:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(self.FOREX_FACTORY_RSS)
                    if response.status_code == 200 and feedparser:
                        feed = feedparser.parse(response.text)
                        self._parse_events(feed)
                        self.last_fetch = datetime.now(timezone.utc)
                        logger.info(f"News calendar refreshed: {len(self.high_impact_events)} events loaded")
        except Exception as e:
            logger.warning(f"Failed to fetch news calendar: {e}")

    def _parse_events(self, feed):
        """Parse RSS feed entries for HIGH impact events."""
        self.high_impact_events = []
        if not feed or not hasattr(feed, 'entries'):
            return

        for entry in feed.entries:
            title = getattr(entry, 'title', '').upper()
            # Look for high-impact indicators in title
            high_impact_keywords = ['NFP', 'CPI', 'FOMC', 'GDP', 'INTEREST RATE',
                                    'EMPLOYMENT', 'NON-FARM', 'INFLATION', 'FED']
            is_high = any(kw in title for kw in high_impact_keywords)

            if is_high:
                published = getattr(entry, 'published_parsed', None)
                if published:
                    import time
                    event_time = datetime.fromtimestamp(time.mktime(published), tz=timezone.utc)
                    self.high_impact_events.append({
                        "title": entry.title,
                        "time": event_time,
                        "currency": self._extract_currency(entry.title),
                    })

    def _extract_currency(self, title: str) -> str:
        """Try to extract affected currency from event title."""
        currencies = ["USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD"]
        for curr in currencies:
            if curr in title.upper():
                return curr
        return "UNKNOWN"  # Default to UNKNOWN

    def is_blocked(self, symbol: str = "") -> bool:
        """
        Check if trading is currently blocked by a nearby news event.
        Blocks ±30 minutes around HIGH-impact news.
        """
        if not self.enabled:
            return False

        now = datetime.now(timezone.utc)

        for event in self.high_impact_events:
            event_time = event["time"]
            # Check if we're within the block window
            if abs((now - event_time).total_seconds()) < self.block_window.total_seconds():
                # Optionally check if the symbol contains the affected currency
                currency = event.get("currency", "")
                if currency and symbol:
                    if currency in symbol.upper():
                        logger.info(f"News block active: {event['title']} affects {symbol}")
                        return True
                elif not symbol:
                    # If no symbol specified, block conservatively
                    return True

        return False

    def get_upcoming_events(self, hours_ahead: int = 24) -> List[Dict[str, Any]]:
        """Return events within the next N hours."""
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(hours=hours_ahead)
        return [e for e in self.high_impact_events if now <= e["time"] <= cutoff]
