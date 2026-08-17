"""
backend/api/websocket.py

WebSocket + Redis Pub/Sub bridge for real-time data delivery.
Supports direct broadcasting (without Redis) for activity logs and backtest progress.
Source: TradingBot_MasterPlan-2.md Section 6
"""

import json

from fastapi import WebSocket, WebSocketDisconnect

from backend.config import settings
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
        self.active: dict[str, list[WebSocket]] = {}

    async def connect(self, ws: WebSocket, user_id: str):
        await ws.accept()
        self.active.setdefault(user_id, []).append(ws)
        logger.info(f"WebSocket connected: user={user_id} (total: {sum(len(v) for v in self.active.values())})")

    def disconnect(self, ws: WebSocket, user_id: str):
        if user_id in self.active:
            self.active[user_id] = [w for w in self.active[user_id] if w != ws]
            if not self.active[user_id]:
                del self.active[user_id]
        logger.info(f"WebSocket disconnected: user={user_id}")

    async def broadcast_to_user(self, user_id: str, message: dict):
        """Send message to all connections for a specific user."""
        dead = []
        for ws in self.active.get(user_id, []):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        # Clean up dead connections
        if dead and user_id in self.active:
            self.active[user_id] = [w for w in self.active[user_id] if w not in dead]
            if not self.active[user_id]:
                del self.active[user_id]

    async def broadcast_all(self, message: dict):
        """Send message to all connected users."""
        for user_id in list(self.active.keys()):
            await self.broadcast_to_user(user_id, message)

    async def broadcast_backtest_progress(self, user_id: str, progress: dict):
        """
        Send backtest progress update to a specific user.
        Message types: backtest_progress
        Progress fields: stage, pct, message, total_trades, etc.
        """
        await self.broadcast_to_user(user_id, {
            "type": "backtest_progress",
            **progress,
        })

    def get_connection_count(self) -> int:
        """Get total number of active WebSocket connections."""
        return sum(len(v) for v in self.active.values())

    def get_connected_users(self) -> list[str]:
        """Get list of user IDs with active WebSocket connections."""
        return list(self.active.keys())


manager = ConnectionManager()


async def websocket_handler(websocket: WebSocket, user_id: str, token: str = None, redis_url: str = settings.redis.url):
    """
    WebSocket endpoint handler. Subscribes to Redis channels and forwards to client.
    Enforces JWT authentication to prevent unauthorized stream interception.
    Source: TradingBot_MasterPlan-2.md Section 6
    """
    if not token:
        logger.warning(f"WebSocket connection rejected: Missing token for user {user_id}")
        await websocket.close(code=4001, reason="Missing token")
        return

    from jose import JWTError, jwt

    from backend.api.deps import JWT_ALGORITHM, JWT_SECRET_KEY

    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        token_user_id = payload.get("sub")
        if token_user_id != user_id:
            logger.warning(f"WebSocket connection rejected: Token mismatch for user {user_id}")
            await websocket.close(code=4001, reason="Token mismatch")
            return
    except JWTError:
        logger.warning(f"WebSocket connection rejected: Invalid/expired token for user {user_id}")
        await websocket.close(code=4001, reason="Token expired")
        return

    await manager.connect(websocket, user_id)

    from backend.data.redis_client import redis_client
    if not HAS_REDIS or not redis_client.redis:
        # Fallback: just keep connection alive without Redis
        # Direct broadcasts (activity_log, backtest_progress) still work
        # because they use manager.broadcast_to_user() directly
        try:
            while True:
                data = await websocket.receive_text()
                # Echo for keepalive
        except WebSocketDisconnect:
            manager.disconnect(websocket, user_id)
        return

    try:
        redis = Redis.from_url(redis_url, protocol=2)
        pubsub = redis.pubsub()

        # Subscribe to user-specific and global channels
        await pubsub.subscribe(
            "channel:ticks",
            f"channel:trades:{user_id}",
            f"channel:signals:{user_id}",
            f"channel:account:{user_id}",
            f"channel:backtest:{user_id}",
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
