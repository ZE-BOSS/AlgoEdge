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
        
        # Simple schema migrations for newly added columns.
        from sqlalchemy import text

        # ADD COLUMN migrations. Neither SQLite nor (reliably) older Postgres
        # support "IF NOT EXISTS" here, so these are plain ALTER TABLE ADD
        # COLUMN statements — re-applying an already-added column is expected
        # to raise, and is caught below by matching the "already exists"
        # style message rather than by clause syntax.
        add_column_migrations = [
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
            # FIX: previously glued onto the deriv_mt5_path DROP statement
            # below via a missing comma (Python silently concatenates
            # adjacent string literals), producing one invalid two-statement
            # string that always failed — so bias_stats was never actually
            # added by this auto-migration path. Confirmed by parsing the
            # original list literally: it collapsed to 17 entries instead of
            # 18, with the DROP and this ADD fused into a single string.
            "ALTER TABLE backtest_runs ADD COLUMN bias_stats TEXT;",
            "ALTER TABLE backtest_runs ADD COLUMN confluence_stats TEXT;",
            "ALTER TABLE backtest_runs ADD COLUMN title TEXT;",
        ]

        # DROP COLUMN ... IF EXISTS is Postgres syntax — SQLite's ALTER TABLE
        # DROP COLUMN does not accept IF EXISTS at all, so on this
        # deployment's actual SQLite database these always raised a syntax
        # error. That error was being silently swallowed by the old
        # "operationalerror in str(e)" catch below (SQLite routes nearly
        # every failure — syntax errors, locked db, anything — through
        # OperationalError, not just "already applied" cases), so the
        # failure was invisible. Only run these against Postgres, where the
        # syntax is actually valid.
        drop_column_migrations = [
            "ALTER TABLE users DROP COLUMN IF EXISTS deriv_mt5_account;",
            "ALTER TABLE users DROP COLUMN IF EXISTS deriv_mt5_password_encrypted;",
            "ALTER TABLE users DROP COLUMN IF EXISTS deriv_mt5_server;",
            "ALTER TABLE users DROP COLUMN IF EXISTS deriv_mt5_path;",
        ]

        migrations = add_column_migrations + ([] if is_sqlite else drop_column_migrations)

        for query in migrations:
            try:
                await conn.execute(text(query))
            except Exception as e:
                msg = str(e).lower()
                # Only swallow the specific "already applied" cases each
                # database actually raises for a re-run migration:
                #   SQLite:   "duplicate column name: x"
                #   Postgres: 'column "x" of relation "y" already exists'
                # FIX: the old check also matched "operationalerror" on its
                # own, which is the base exception class SQLite uses for
                # essentially any failure — that masked real errors (like
                # the concatenated-statement bug above) instead of only
                # skipping genuinely-already-applied migrations.
                already_applied = "duplicate column name" in msg or "already exists" in msg
                if not already_applied:
                    logger.warning(f"Migration failed: {query!r} — {e}")

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