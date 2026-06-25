"""
backend/data/database.py

Async PostgreSQL session factory using SQLAlchemy + asyncpg.
Provides dependency injection for FastAPI routes.
"""

from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
)
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from backend.config import settings
from backend.data.models import Base
from backend.utils.logger import get_logger

logger = get_logger(__name__)

# Create async engine — connects to Railway PostgreSQL
engine = create_async_engine(
    settings.database.url,
    echo=settings.server.debug,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,  # verify connections are alive before use
)

# Session factory
async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db():
    """Create all tables if they don't exist."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
        # Simple schema migrations for newly added columns
        try:
            from sqlalchemy import text
            await conn.execute(text("ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS sl_hit_rate FLOAT;"))
            await conn.execute(text("ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS trail_hit_rate FLOAT;"))
            await conn.execute(text("ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS run_logs TEXT;"))
        except Exception as e:
            logger.warning(f"Simple migration failed (likely already applied): {e}")
            
    logger.info("Database tables initialized")


async def close_db():
    """Dispose engine connections on shutdown."""
    await engine.dispose()
    logger.info("Database connections closed")


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields an async session."""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Standalone context manager for non-route usage (e.g. background tasks)."""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
