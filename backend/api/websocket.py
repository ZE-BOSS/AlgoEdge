"""
backend/api/websocket.py

WebSocket + Redis Pub/Sub bridge for real-time data delivery.
Source: TradingBot_MasterPlan-2.md Section 6
"""

from typing import Dict, List
from fastapi import WebSocket, WebSocketDisconnect
import json

from backend.utils.logger import get_logger

logger = get_logger(__name__)

try:
    from redis.asyncio import Redis
    HAS_REDIS = True
except ImportError:
    HAS_REDIS = False


class ConnectionManager:
    """WebSocket connection pool — one per user."""

    def __init__(self):
        self.active: Dict[str, List[WebSocket]] = {}

    async def connect(self, ws: WebSocket, user_id: str):
        await ws.accept()
        self.active.setdefault(user_id, []).append(ws)
        logger.info(f"WebSocket connected: user={user_id}")

    def disconnect(self, ws: WebSocket, user_id: str):
        if user_id in self.active:
            self.active[user_id] = [w for w in self.active[user_id] if w != ws]
            if not self.active[user_id]:
                del self.active[user_id]
        logger.info(f"WebSocket disconnected: user={user_id}")

    async def broadcast_to_user(self, user_id: str, message: dict):
        """Send message to all connections for a specific user."""
        for ws in self.active.get(user_id, []):
            try:
                await ws.send_json(message)
            except Exception:
                self.active[user_id].remove(ws)

    async def broadcast_all(self, message: dict):
        """Send message to all connected users."""
        for user_id in list(self.active.keys()):
            await self.broadcast_to_user(user_id, message)


manager = ConnectionManager()


async def websocket_handler(websocket: WebSocket, user_id: str, redis_url: str = "redis://localhost:6379"):
    """
    WebSocket endpoint handler. Subscribes to Redis channels and forwards to client.
    Source: TradingBot_MasterPlan-2.md Section 6
    """
    await manager.connect(websocket, user_id)

    if not HAS_REDIS:
        # Fallback: just keep connection alive without Redis
        try:
            while True:
                data = await websocket.receive_text()
                # Echo for keepalive
        except WebSocketDisconnect:
            manager.disconnect(websocket, user_id)
        return

    try:
        redis = Redis.from_url(redis_url)
        pubsub = redis.pubsub()

        # Subscribe to user-specific and global channels
        await pubsub.subscribe(
            "channel:ticks",
            f"channel:trades:{user_id}",
            f"channel:signals:{user_id}",
            f"channel:account:{user_id}",
        )

        async for message in pubsub.listen():
            if message["type"] == "message":
                try:
                    data = json.loads(message["data"])
                    await websocket.send_json(data)
                except (json.JSONDecodeError, Exception):
                    await websocket.send_text(str(message["data"]))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"WebSocket error for user {user_id}: {e}")
    finally:
        manager.disconnect(websocket, user_id)
        try:
            await pubsub.unsubscribe()
            await redis.close()
        except Exception:
            pass
