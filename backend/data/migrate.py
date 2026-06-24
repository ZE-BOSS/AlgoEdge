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
]


async def run_migrations():
    """Apply all pending migrations."""
    db_url = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/algoedge"
    )
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
