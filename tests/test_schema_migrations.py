"""
tests/test_schema_migrations.py

A column added to a model needs a migration. This is the test that says so.

THE BUG THIS EXISTS FOR
-----------------------
`Trade.mt5_account` was added to `backend/data/models.py` with no matching
`ALTER TABLE` line in `init_db`. Every test passed, because every test builds
its database from scratch and `create_all` makes the column. The VPS does not
build from scratch — its `trades` table already existed — so on deploy every
query that selected a Trade failed:

    sqlite3.OperationalError: no such column: trades.mt5_account

The bot kept scanning and finding setups, while ghost cleanup, live position
management and the investor trade watch failed every cycle. A fresh-database
test suite is structurally blind to this, which is why these two tests build
an OLD database on purpose.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.data.models import Base
# Registered on the same Base only when imported. Without these the investor,
# cash and RL tables are invisible to every check below - which is how a
# migration naming a table that does not exist went unnoticed (2026-10-16).
import backend.investor.models  # noqa: E402,F401
import backend.mt5.cash_models  # noqa: E402,F401
import backend.rl.models  # noqa: E402,F401

# Columns added to tables that already exist in production. Each one must have
# an `ALTER TABLE ... ADD COLUMN` line in `database.init_db`, or an upgrade
# leaves the column missing and every query on that table fails.
#
# Add to this list whenever you add a column to an EXISTING table.
NEW_COLUMNS_ON_EXISTING_TABLES = [
    ("trades", "mt5_account"),
    # the investor referral tree, 2026-10-16
    ("investors", "referred_by"),
    ("investors", "referral_code"),
    ("investors", "referred_at"),
    ("investors", "referral_source"),
    ("fund_settings", "referral_model"),
    ("fund_settings", "referral_l1_pct"),
    ("fund_settings", "referral_l2_pct"),
    ("investor_applications", "referral_code"),
    ("investor_applications", "referred_by"),
]


def migration_statements() -> list[str]:
    """The ALTER TABLE list, read from the source.

    Read as text rather than imported: `init_db` is one long async function
    that connects to a database, and the list is a literal inside it.
    """
    src = Path("backend/data/database.py").read_text(encoding="utf-8")
    return re.findall(r'"(ALTER TABLE [^"]+)"', src)


def added_columns() -> set[tuple[str, str]]:
    out = set()
    for stmt in migration_statements():
        m = re.match(r"ALTER TABLE (\w+) ADD COLUMN (\w+)", stmt, re.I)
        if m:
            out.add((m.group(1).lower(), m.group(2).lower()))
    return out


@pytest.mark.parametrize("table,column", NEW_COLUMNS_ON_EXISTING_TABLES)
def test_a_new_column_on_an_existing_table_has_a_migration(table, column):
    """The direct check. `create_all` makes missing TABLES, never missing
    COLUMNS — so without a line here, an upgrade silently leaves it out."""
    assert (table, column) in added_columns(), (
        f"{table}.{column} is on the model but has no "
        f'"ALTER TABLE {table} ADD COLUMN {column} ..." in init_db(). '
        f"Fresh databases will have it and every existing one will not.")


def test_every_migration_names_a_column_the_model_still_has():
    """Drift the other way: a migration for a column that was since renamed or
    removed. Harmless at runtime (it fails and is caught) but it is a lie in
    the schema history, and it hides the real ones in the noise."""
    tables = {t.name: {c.name for c in t.columns} for t in Base.metadata.tables.values()}
    stale = []
    for table, column in added_columns():
        known = tables.get(table)
        if known is None:
            # NOT skipped. A migration naming a table no model declares is far
            # more likely a wrong name than a dropped table - "applications"
            # for "investor_applications" - and it raises inside a loop that
            # tolerates raises, so the column is silently never added.
            stale.append(f"{table} (no such table)")
            continue
        if column not in known:
            stale.append(f"{table}.{column}")
    assert not stale, f"migrations add columns no model declares: {stale}"


def test_an_old_database_is_repaired_on_startup():
    """The real thing, end to end.

    Build the schema, DROP the new column to simulate a database created
    before it existed, then run the migration list and assert the column is
    back. This is the test that would have caught the outage.
    """
    async def go():
        tmp = tempfile.mkdtemp(prefix="algoedge-migrate-")
        try:
            engine = create_async_engine(
                f"sqlite+aiosqlite:///{Path(tmp).as_posix()}/old.db")
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                # The index has to go first. SQLite refuses to drop an
                # indexed column - "error in index ix_trades_mt5_account after
                # drop column" - and an old database would not have had the
                # index either, so dropping both is the faithful simulation.
                await conn.execute(text("DROP INDEX IF EXISTS ix_trades_mt5_account"))
                try:
                    await conn.execute(text("ALTER TABLE trades DROP COLUMN mt5_account"))
                except Exception as exc:
                    pytest.skip(f"this SQLite cannot DROP COLUMN: {exc}")

            async with engine.connect() as conn:
                cols = {r[1] for r in (await conn.execute(
                    text("PRAGMA table_info(trades)"))).fetchall()}
            assert "mt5_account" not in cols, "the column should be gone now"

            # Now apply the migrations exactly as init_db does.
            async with engine.begin() as conn:
                for stmt in migration_statements():
                    try:
                        await conn.execute(text(stmt))
                    except Exception:
                        pass            # already-applied columns are expected to fail

            async with engine.connect() as conn:
                cols = {r[1] for r in (await conn.execute(
                    text("PRAGMA table_info(trades)"))).fetchall()}
            assert "mt5_account" in cols, (
                "an existing database was NOT repaired by the migration list - "
                "this is the deploy failure, reproduced")
            await engine.dispose()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    asyncio.run(go())


def test_re_applying_the_migrations_is_safe():
    """`init_db` runs on every start, so every statement runs again on every
    start. Re-applying must be harmless - that is the whole design."""
    async def go():
        tmp = tempfile.mkdtemp(prefix="algoedge-migrate2-")
        try:
            engine = create_async_engine(
                f"sqlite+aiosqlite:///{Path(tmp).as_posix()}/a.db")
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            for _ in range(2):
                async with engine.begin() as conn:
                    for stmt in migration_statements():
                        try:
                            await conn.execute(text(stmt))
                        except Exception:
                            pass
            async with engine.connect() as conn:
                n = (await conn.execute(text("SELECT COUNT(*) FROM trades"))).scalar()
            assert n == 0
            await engine.dispose()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    asyncio.run(go())
