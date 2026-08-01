"""
backend/data/database.py

Async PostgreSQL session factory using SQLAlchemy + asyncpg.
Provides dependency injection for FastAPI routes.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.config import settings
from backend.data.models import Base
from backend.utils.logger import get_logger

logger = get_logger(__name__)

# Create async engine
is_sqlite = settings.database.url.startswith("sqlite")
engine_kwargs = {
    "echo": settings.server.debug,
}
if not is_sqlite:
    engine_kwargs.update({
        "pool_size": 10,
        "max_overflow": 20,
        "pool_pre_ping": True,
    })

engine = create_async_engine(settings.database.url, **engine_kwargs)

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
        # Simple schema migrations for newly added columns
        from sqlalchemy import text
        migrations = [
            "ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT FALSE;",
            "ALTER TABLE backtest_runs ADD COLUMN sl_hit_rate FLOAT;",
            "ALTER TABLE backtest_runs ADD COLUMN trail_hit_rate FLOAT;",
            "ALTER TABLE backtest_runs ADD COLUMN run_logs TEXT;",
            "ALTER TABLE backtest_runs ADD COLUMN rejection_funnel TEXT;",
            "ALTER TABLE backtest_runs ADD COLUMN sortino_ratio FLOAT;",
            "ALTER TABLE backtest_runs ADD COLUMN expectancy_r FLOAT;",
            "ALTER TABLE backtest_trades ADD COLUMN chart_data TEXT;",
            "ALTER TABLE backtest_trades ADD COLUMN chart_data_m15 TEXT;",
            "ALTER TABLE backtest_trades ADD COLUMN chart_data_m5 TEXT;",
            "ALTER TABLE backtest_trades ADD COLUMN smc_data TEXT;",
            "ALTER TABLE backtest_trades ADD COLUMN sub_trades TEXT;",
            "ALTER TABLE users DROP COLUMN IF EXISTS deriv_mt5_account;",
            "ALTER TABLE users DROP COLUMN IF EXISTS deriv_mt5_password_encrypted;",
            "ALTER TABLE users DROP COLUMN IF EXISTS deriv_mt5_server;",
            "ALTER TABLE users DROP COLUMN IF EXISTS deriv_mt5_path;"
            "ALTER TABLE backtest_runs ADD COLUMN bias_stats TEXT;",
            "ALTER TABLE backtest_runs ADD COLUMN confluence_stats TEXT;",
        ]
        
        for query in migrations:
            try:
                await conn.execute(text(query))
            except Exception as e:
                # Ignore duplicate column errors
                if "duplicate column name" not in str(e).lower() and "operationalerror" not in str(e).lower():
                    logger.warning(f"Migration failed: {e}")
            
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
