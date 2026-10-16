"""The two learning-data sweeps run ONCE a day.

`jobs.tick` runs every 15 minutes and calls the daily jobs on every pass after
RUN_AFTER_WAT_HOUR - so a job that does not claim its day runs about ninety
times. These sweeps fetch bars for every unfinished trade and signal through
the single MT5 executor the live bot also uses; unclaimed, they would have
competed with live trading for the terminal all day.
"""

import asyncio
import tempfile
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.fixture
def Session():
    from backend.data.database import Base
    import backend.investor.models  # noqa: F401  - JobRun lives here

    tmp = Path(tempfile.mkdtemp(prefix="algoedge-jobs-")) / "t.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp.as_posix()}")

    async def build():
        async with engine.begin() as c:
            await c.run_sync(Base.metadata.create_all)

    asyncio.run(build())
    yield async_sessionmaker(engine, expire_on_commit=False)
    asyncio.run(engine.dispose())


@pytest.mark.parametrize("job_name,module,attr", [
    ("trade_aftermath_sweep", "backend.services.trade_aftermath", "sweep"),
    ("signal_outcome_sweep", "backend.services.signal_outcome", "sweep"),
])
def test_a_sweep_runs_once_per_day_however_often_tick_calls_it(
        Session, monkeypatch, job_name, module, attr):
    import importlib

    from backend.investor import jobs

    calls = []

    async def fake_sweep(session, **kw):
        calls.append(kw)
        return {"measured": 1}

    monkeypatch.setattr(importlib.import_module(module), attr, fake_sweep)
    job = getattr(jobs, job_name)

    async def go():
        day = date(2026, 10, 15)
        for _ in range(5):                         # five ticks, same day
            async with Session() as s:
                await job(s, day)
        async with Session() as s:                 # the next day runs again
            await job(s, date(2026, 10, 16))

    asyncio.run(go())
    assert len(calls) == 2, f"ran {len(calls)} times for 2 days"
