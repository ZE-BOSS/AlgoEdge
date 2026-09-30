"""
backend/data/database.py

Async PostgreSQL session factory using SQLAlchemy + asyncpg.
Provides dependency injection for FastAPI routes.
"""

import os
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

# SQL echo used to be tied to DEBUG, which meant a normal `DEBUG=true` dev setup
# logged EVERY statement. That is not free: a backtest writes hundreds of trade
# rows, and echoing each one costs formatting, I/O, and — since Phase 13 put a
# WebSocket sink on the logger — pressure on the same socket the replay stream
# uses. Given its own switch so verbose SQL is a deliberate choice.
_sql_echo = os.environ.get("ALGOEDGE_SQL_ECHO", "").strip().lower() in ("1", "true", "yes")
engine_kwargs = {
    "echo": _sql_echo,
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
    # A model registers with Base.metadata when its MODULE is imported, so the
    # investor tables are only created if something has imported them first.
    # Without this line create_all silently skips them and the first deposit
    # fails against a table that does not exist.
    import backend.investor.models  # noqa: F401 — imported for the side effect

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
            # investor platform, Phase 4 — fee lifecycle
            "ALTER TABLE fee_accruals ADD COLUMN paid_at TIMESTAMP;",
            # per-investor fee terms and minimum
            "ALTER TABLE investors ADD COLUMN performance_fee_pct NUMERIC(9, 4);",
            "ALTER TABLE investors ADD COLUMN management_fee_pct NUMERIC(9, 4);",
            "ALTER TABLE investors ADD COLUMN min_investment NUMERIC(18, 2);",
            "ALTER TABLE investors ADD COLUMN preferences TEXT;",
            "ALTER TABLE trade_disclosures ADD COLUMN booked_amount NUMERIC(18, 2);",
            "ALTER TABLE trade_disclosures ADD COLUMN booked_on DATE;",
            "ALTER TABLE fee_accruals ADD COLUMN days INTEGER;",
            "ALTER TABLE fee_accruals ADD COLUMN decided_by VARCHAR(36);",
            "ALTER TABLE backtest_runs ADD COLUMN sl_hit_rate FLOAT;",
            "ALTER TABLE backtest_runs ADD COLUMN trail_hit_rate FLOAT;",
            "ALTER TABLE backtest_runs ADD COLUMN run_logs TEXT;",
            "ALTER TABLE backtest_runs ADD COLUMN rejection_funnel TEXT;",
            "ALTER TABLE backtest_runs ADD COLUMN sortino_ratio FLOAT;",
            "ALTER TABLE backtest_runs ADD COLUMN expectancy_r FLOAT;",
            # Excursion-in-R columns. mae_pips/mfe_pips already existed but were
            # NULL on every row because trade_grouper never propagated them;
            # these three land alongside that fix.
            "ALTER TABLE backtest_trades ADD COLUMN mae_r FLOAT;",
            "ALTER TABLE backtest_trades ADD COLUMN mfe_r FLOAT;",
            "ALTER TABLE backtest_trades ADD COLUMN risk_pips FLOAT;",
            "ALTER TABLE backtest_trades ADD COLUMN chart_data TEXT;",
            "ALTER TABLE backtest_trades ADD COLUMN chart_data_h1 TEXT;",
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
            "ALTER TABLE backtest_runs ADD COLUMN replay_data TEXT;",
            "ALTER TABLE trades ADD COLUMN group_id VARCHAR(36);",
            # `strategy_id` was added to the BacktestTrade MODEL without a
            # matching migration, so every save failed with
            # "table backtest_trades has no column named strategy_id" — a hard
            # 500 that the frontend surfaced only as "Network error". It is the
            # column that tells a saved portfolio run which strategy produced
            # each trade, so without it a multi-strategy run is unreadable
            # after saving.
            "ALTER TABLE backtest_trades ADD COLUMN strategy_id TEXT;",
            # Live journal completeness. Every one of these columns is read by
            # the journal/signal UI but was never written by the LIVE path (only
            # by the backtester), so the frontend rendered blanks for session,
            # exit reason per leg, achieved R:R, P&L in pips and balance-after.
            "ALTER TABLE signals ADD COLUMN session_close_time TIMESTAMP;",
            "ALTER TABLE trades ADD COLUMN session VARCHAR(20);",
            "ALTER TABLE trades ADD COLUMN session_close_time TIMESTAMP;",
            "ALTER TABLE trade_positions ADD COLUMN exit_reason VARCHAR(30);",
            "ALTER TABLE trade_positions ADD COLUMN pnl_pips FLOAT;",
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

        await _ensure_an_admin(conn)

    logger.info("Database tables initialized")


async def _ensure_an_admin(conn):
    """Make sure someone can open the admin-only sections (Investors, Users).

    Nothing else ever sets `users.is_admin`, so on an existing install every
    account was a plain operator and the Investors section stayed hidden.

    * ADMIN_EMAILS (comma-separated) in .env: those accounts are admins.
    * Otherwise, if nobody is an admin yet, the oldest account becomes one:
      that is the owner, who created it before anyone else.
    """
    from sqlalchemy import text

    emails = [e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()]
    try:
        for email in emails:
            res = await conn.execute(
                text("UPDATE users SET is_admin = :t WHERE lower(email) = :e AND (is_admin IS NULL OR is_admin = :f)"),
                {"t": True, "f": False, "e": email},
            )
            if res.rowcount:
                logger.info(f"Admin rights granted to {email} (ADMIN_EMAILS)")
        if (await conn.execute(text("SELECT 1 FROM users WHERE is_admin = :t LIMIT 1"), {"t": True})).first():
            return
        first = (await conn.execute(
            text("SELECT id, email FROM users ORDER BY created_at ASC, email ASC LIMIT 1")
        )).first()
        if first:
            await conn.execute(text("UPDATE users SET is_admin = :t WHERE id = :i"), {"t": True, "i": first[0]})
            logger.info(f"No admin existed: admin rights granted to the first account, {first[1]}")
    except Exception as e:  # never block startup over this
        logger.warning(f"Could not check admin accounts: {e}")


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