"""
backend/data/migrate.py

Lightweight migration utility for adding columns to existing tables.
Run with: python -m backend.data.migrate

Since the project uses SQLAlchemy create_all (which only creates missing tables,
not missing columns), this script handles ALTER TABLE for production databases.
"""

import asyncio
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.utils.logger import get_logger

logger = get_logger(__name__)


# Migrations: (description, SQL, rollback_SQL)
MIGRATIONS = [
    (
        "Add sl_hit_rate column to backtest_runs",
        "ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS sl_hit_rate FLOAT;",
        "ALTER TABLE backtest_runs DROP COLUMN IF EXISTS sl_hit_rate;",
    ),
    (
        "Add deriv_mt5_account column to users",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS deriv_mt5_account BIGINT;",
        "ALTER TABLE users DROP COLUMN IF EXISTS deriv_mt5_account;",
    ),
    (
        "Add deriv_mt5_password_encrypted column to users",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS deriv_mt5_password_encrypted BYTEA;",
        "ALTER TABLE users DROP COLUMN IF EXISTS deriv_mt5_password_encrypted;",
    ),
    (
        "Add deriv_mt5_server column to users",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS deriv_mt5_server VARCHAR(100);",
        "ALTER TABLE users DROP COLUMN IF EXISTS deriv_mt5_server;",
    ),
    (
        "Add deriv_mt5_path column to users",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS deriv_mt5_path VARCHAR(500);",
        "ALTER TABLE users DROP COLUMN IF EXISTS deriv_mt5_path;",
    ),
    (
        "Add balance_before to backtest_trades",
        "ALTER TABLE backtest_trades ADD COLUMN IF NOT EXISTS balance_before FLOAT;",
        "ALTER TABLE backtest_trades DROP COLUMN IF EXISTS balance_before;",
    ),
    (
        "Add balance_after to backtest_trades",
        "ALTER TABLE backtest_trades ADD COLUMN IF NOT EXISTS balance_after FLOAT;",
        "ALTER TABLE backtest_trades DROP COLUMN IF EXISTS balance_after;",
    ),
    (
        "Add balance_before to trades",
        "ALTER TABLE trades ADD COLUMN IF NOT EXISTS balance_before FLOAT;",
        "ALTER TABLE trades DROP COLUMN IF EXISTS balance_before;",
    ),
    (
        "Add balance_after to trades",
        "ALTER TABLE trades ADD COLUMN IF NOT EXISTS balance_after FLOAT;",
        "ALTER TABLE trades DROP COLUMN IF EXISTS balance_after;",
    ),
    (
        "Add confluence_score to trades",
        "ALTER TABLE trades ADD COLUMN IF NOT EXISTS confluence_score INTEGER;",
        "ALTER TABLE trades DROP COLUMN IF EXISTS confluence_score;",
    ),
    (
        "Add chart_data to trades",
        "ALTER TABLE trades ADD COLUMN IF NOT EXISTS chart_data TEXT;",
        "ALTER TABLE trades DROP COLUMN IF EXISTS chart_data;",
    ),
    (
        "Add chart_data to signals",
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS chart_data TEXT;",
        "ALTER TABLE signals DROP COLUMN IF EXISTS chart_data;",
    ),
]


async def run_migrations():
    """Apply all pending migrations."""
    db_url = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/algoedge")
    engine = create_async_engine(db_url)

    async with engine.begin() as conn:
        for desc, sql, _ in MIGRATIONS:
            try:
                await conn.execute(text(sql))
                logger.info(f"✅ Migration applied: {desc}")
            except Exception as e:
                logger.warning(f"⚠️ Migration skipped ({desc}): {e}")

    await engine.dispose()
    logger.info("All migrations processed")


if __name__ == "__main__":
    asyncio.run(run_migrations())
