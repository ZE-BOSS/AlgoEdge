"""Columns added to an EXISTING table must reach an existing database.

`create_all` creates missing tables and never alters existing ones, so a new
column on an existing table needs a line in `init_db`'s
`add_column_migrations`, or every query on that model fails with "no such
column" the moment it is deployed onto a database that already has rows - the
live one. `database.py` records that this already took the VPS down once.

Two ways that list goes wrong, both of which happened on 2026-10-16:

  * a column is added to a model and no migration is written for it
  * a migration is written against the WRONG TABLE NAME - "applications"
    instead of "investor_applications" - which raises inside a loop that
    expects some statements to raise, so it fails silently and the column is
    simply never added

Both are now guarded in test_schema_migrations.py. This file covers what that
one does not: an old database that already HAS ROWS, where the NOT NULL
DEFAULT on fund_settings.referral_model decides what existing terms mean.
"""

import asyncio
import sqlite3
import tempfile
from pathlib import Path

# Registered on the shared Base only when imported, and `create_all` below has
# to build the investor tables before their columns can be stripped off.
import backend.investor.models  # noqa: F401
import backend.mt5.cash_models  # noqa: F401
import backend.rl.models  # noqa: F401


# The referral columns, added to tables that already had rows in production.
NEW_ON_EXISTING = {
    "investors": ["referred_by", "referral_code", "referred_at", "referral_source"],
    "fund_settings": ["referral_model", "referral_l1_pct", "referral_l2_pct"],
    "investor_applications": ["referral_code", "referred_by"],
}


def test_an_old_database_with_rows_comes_through_init_db(monkeypatch):
    """Build the current schema, then strip the new columns back off to make
    it look like the database on the VPS, put real rows in, and migrate."""
    tmp = Path(tempfile.mkdtemp(prefix="algoedge-mig-")) / "old.db"
    url = f"sqlite+aiosqlite:///{tmp.as_posix()}"

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from backend.data import database as dbm

    engine = create_async_engine(url)

    async def build():
        async with engine.begin() as conn:
            await conn.run_sync(dbm.Base.metadata.create_all)

    asyncio.run(build())

    # Rebuild each table WITHOUT the new columns rather than DROP COLUMN them:
    # SQLite refuses to drop a column that is part of a foreign key, and
    # `investors.referred_by` is one. A rebuilt table is also the closer model
    # of the real thing - an old database never had these columns at all.
    con = sqlite3.connect(tmp)
    for table, cols in NEW_ON_EXISTING.items():
        keep = [r[1] for r in con.execute(f"PRAGMA table_info({table})") if r[1] not in cols]
        listed = ", ".join(keep)
        con.execute(f"CREATE TABLE {table}__old AS SELECT {listed} FROM {table}")
        con.execute(f"DROP TABLE {table}")
        con.execute(f"ALTER TABLE {table}__old RENAME TO {table}")
        left = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        assert not (left & set(cols)), f"{table} still has {left & set(cols)}"
    con.execute("INSERT INTO fund_settings (id, version, performance_fee_pct, management_fee_pct, "
                "withdrawal_cap_pct, min_investment, lockup_days, notice_days, base_currency) "
                "VALUES (1, 1, 20, 5, 30, 200, 30, 7, 'USD')")
    con.execute("INSERT INTO investors (id, email, name, status, kyc_status) "
                "VALUES ('i1', 'a@x.test', 'A', 'active', 'none')")
    con.execute("INSERT INTO investor_applications (id, name, email, status) "
                "VALUES (1, 'B', 'b@x.test', 'new')")
    con.commit()
    con.close()

    monkeypatch.setattr(dbm, "engine", engine)
    monkeypatch.setattr(dbm, "async_session", async_sessionmaker(engine, expire_on_commit=False))

    async def migrate_and_read():
        await dbm.init_db()
        from sqlalchemy import select

        from backend.investor.models import Application, FundSettings, Investor

        async with dbm.async_session() as s:
            inv = (await s.execute(select(Investor))).scalars().all()
            fs = (await s.execute(select(FundSettings))).scalars().all()
            ap = (await s.execute(select(Application))).scalars().all()
        await engine.dispose()
        return inv, fs, ap

    inv, fs, ap = asyncio.run(migrate_and_read())
    assert len(inv) == 1 and len(ap) == 1
    # The terms that existed before referrals: nobody was charged a share under
    # them, so the default must be the arrangement that charges no investor.
    assert fs[0].referral_model == "HOUSE_FEE"
    assert float(fs[0].referral_l1_pct) == 0 and float(fs[0].referral_l2_pct) == 0
