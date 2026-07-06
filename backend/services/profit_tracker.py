"""
backend/services/profit_tracker.py

Service for tracking daily and weekly accumulated profit to enforce target profit halts.
Uses Redis for persistence across restarts.
"""

import os
from datetime import datetime, timezone, timedelta
import redis.asyncio as redis
from backend.utils.logger import get_logger

logger = get_logger(__name__)

class DailyProfitTracker:
    def __init__(self):
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.redis = redis.from_url(redis_url, decode_responses=True)

    def _get_daily_key(self) -> str:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return f"profit:daily:{today}"
        
    def _get_weekly_key(self) -> str:
        # Get ISO year and week number
        year, week, _ = datetime.now(timezone.utc).isocalendar()
        return f"profit:weekly:{year}-W{week}"

    async def add_profit(self, amount: float):
        """Add realized profit/loss to the daily and weekly totals."""
        if amount == 0:
            return
            
        daily_key = self._get_daily_key()
        weekly_key = self._get_weekly_key()
        
        try:
            # Increment and set expiry (7 days for daily, 30 days for weekly)
            await self.redis.incrbyfloat(daily_key, amount)
            await self.redis.expire(daily_key, 7 * 86400)
            
            await self.redis.incrbyfloat(weekly_key, amount)
            await self.redis.expire(weekly_key, 30 * 86400)
            
            logger.info(f"[ProfitTracker] Added ${amount:.2f}. Daily/Weekly updated.")
        except Exception as e:
            logger.error(f"[ProfitTracker] Failed to update Redis: {e}")

    async def get_daily_profit(self) -> float:
        try:
            val = await self.redis.get(self._get_daily_key())
            return float(val) if val else 0.0
        except Exception as e:
            logger.error(f"[ProfitTracker] Redis error: {e}")
            return 0.0

    async def get_weekly_profit(self) -> float:
        try:
            val = await self.redis.get(self._get_weekly_key())
            return float(val) if val else 0.0
        except Exception as e:
            logger.error(f"[ProfitTracker] Redis error: {e}")
            return 0.0

profit_tracker = DailyProfitTracker()
