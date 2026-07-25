"""
backend/data/redis_client.py

Redis connection and Pub/Sub manager for real-time WebSocket updates.
"""

import json
from collections.abc import Awaitable, Callable

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
        import asyncio
        max_retries = 1
        retry_delay = 1
        
        for attempt in range(max_retries):
            try:
                self.redis = redis.from_url(
                    settings.redis.url, 
                    decode_responses=True,
                    socket_connect_timeout=5.0,
                    socket_timeout=5.0,
                    health_check_interval=10,
                    retry_on_timeout=True,
                    socket_keepalive=True,
                    protocol=2
                )
                await self.redis.ping()
                self.pubsub = self.redis.pubsub()
                logger.info("Redis connected successfully")
                return
            except Exception as e:
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                else:
                    self.redis = None
                    self.pubsub = None
                    logger.info("Redis not detected locally. Real-time WebSockets and caching are running in fallback mode.")
                    raise e

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
