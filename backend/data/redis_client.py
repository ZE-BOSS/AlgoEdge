"""
backend/data/redis_client.py

Redis connection and Pub/Sub manager for real-time WebSocket updates.
"""

import json
from typing import Any, Callable, Awaitable
import redis.asyncio as redis
from backend.config import settings
from backend.utils.logger import get_logger

logger = get_logger(__name__)


class RedisClient:
    """Async Redis client wrapper."""
    
    def __init__(self):
        self.redis = None
        self.pubsub = None

    async def connect(self):
        logger.info(f"Connecting to Redis at {settings.redis.url}")
        self.redis = redis.from_url(settings.redis.url, decode_responses=True)
        self.pubsub = self.redis.pubsub()
        await self.redis.ping()
        logger.info("Redis connected successfully")

    async def disconnect(self):
        if self.pubsub:
            await self.pubsub.close()
        if self.redis:
            await self.redis.close()
        logger.info("Redis disconnected")

    async def publish(self, channel: str, message: dict):
        """Publish JSON message to channel."""
        if self.redis:
            await self.redis.publish(channel, json.dumps(message))

    async def subscribe(self, channel: str, callback: Callable[[dict], Awaitable[None]]):
        """Subscribe to a channel and handle messages via callback."""
        if self.pubsub:
            await self.pubsub.subscribe(channel)
            logger.info(f"Subscribed to {channel}")
            
            async for message in self.pubsub.listen():
                if message["type"] == "message":
                    data = json.loads(message["data"])
                    await callback(data)


redis_client = RedisClient()
