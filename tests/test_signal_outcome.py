"""What each signal's own plan would have done, and what each gate cost.

The arithmetic is `trade_aftermath.measure`, tested in depth there. These
cover what is new: picking out the gate, turning a measurement into the R the
plan would have banked, and the per-gate ledger - whose SIGN is the whole
point. `net_r` positive must mean the gate threw money away.
"""

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS

import pandas as pd
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.services import signal_outcome as SO

T0 = datetime(2026, 1, 5, 12, 0)
START = int(T0.replace(tzinfo=timezone.utc).timestamp())


def bars(rows):
    return pd.DataFrame({"time": [START + m * 60 for m, *_ in rows],
                         "high": [r[1] for r in rows], "low": [r[2] for r in rows],
                         "close": [r[3] for r in rows]})


# -- which gate --------------------------------------------------------------

def test_the_first_clause_is_the_gate():
    assert SO.gate_of("adx_below_20; spread_wide") == "adx_below_20"
    assert SO.gate_of("") == "" and SO.gate_of(None) == ""


# -- the R the plan would have banked ----------------------------------------

def test_a_target_hit_banks_the_targets_R():
    r = SO.outcome_r({"hit_tp": True}, entry=100.0, stop=99.0, tp=102.0, direction="BUY")
    assert r == pytest.approx(2.0)


def test_a_stop_hit_is_minus_one():
    assert SO.outcome_r({"hit_sl": True}, entry=100.0, stop=99.0, tp=102.0,
                        direction="BUY") == -1.0


def test_a_short_target_is_positive_too():
    r = SO.outcome_r({"hit_tp": True}, entry=100.0, stop=101.0, tp=97.0, direction="SELL")
    assert r == pytest.approx(3.0)


def test_neither_falls_back_to_where_it_stood():
    assert SO.outcome_r({"r_at_watch_end": 0.4}, entry=100, stop=99, tp=102,
                        direction="BUY") == 0.4


# -- the per-gate ledger ------------------------------------------------------

def _row(gate, r, *, acted=False, done=True):
    return NS(acted_on=acted, watch_complete=done, outcome_r=r, skip_gate=gate)


def test_a_gate_that_skipped_winners_is_called_costly():
    out = {g["gate"]: g for g in SO.gate_cost([
        _row("adx", 2.0), _row("adx", 2.0), _row("adx", -1.0)])}
    assert out["adx"]["net_r"] == pytest.approx(3.0)
    assert out["adx"]["verdict"] == "COSTING_MONEY"
    assert out["adx"]["would_win"] == 2 and out["adx"]["would_lose"] == 1


def test_a_gate_that_skipped_losers_is_credited():
    out = {g["gate"]: g for g in SO.gate_cost([_row("news", -1.0), _row("news", -1.0)])}
    assert out["news"]["verdict"] == "SAVING_MONEY"
    assert out["news"]["net_r"] == pytest.approx(-2.0)


def test_taken_and_provisional_signals_do_not_count_against_a_gate():
    out = SO.gate_cost([_row("adx", 5.0, acted=True),        # it was traded
                        _row("adx", 5.0, done=False),        # window still open
                        _row("adx", -1.0)])
    assert out[0]["skipped"] == 1 and out[0]["net_r"] == pytest.approx(-1.0)


def test_the_costliest_gate_comes_first():
    out = SO.gate_cost([_row("a", -1.0), _row("b", 3.0), _row("c", 1.0)])
    assert [g["gate"] for g in out] == ["b", "c", "a"]


# -- the sweep, against a real database ---------------------------------------

@pytest_asyncio.fixture
async def db():
    from backend.data.database import Base

    tmp = tempfile.mkdtemp(prefix="algoedge-sig-")
    engine = create_async_engine(f"sqlite+aiosqlite:///{Path(tmp).as_posix()}/t.db")
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        yield s
    await engine.dispose()


def _signal(**kw):
    from backend.data.models import Signal

    base = dict(user_id="u1", strategy_id="ORB_v1", symbol="XAUUSD",
                direction="BUY", timeframe="M15", entry_price=100.0,
                stop_loss=99.0, tp1_price=102.0, acted_on=False,
                skip_reason="adx_below_20; spread_wide",
                signal_time=T0, created_at=T0)
    return Signal(**{**base, **kw})


@pytest.mark.asyncio
async def test_a_skipped_winner_is_scored_and_charged_to_its_gate(db, monkeypatch):
    from sqlalchemy import select

    from backend.data.models import SignalOutcome
    from backend.mt5 import data_fetcher as DF

    async def fake_range(symbol, timeframe, start, end):
        return bars([(15, 100.8, 99.6, 100.6), (30, 102.4, 100.5, 102.2)])

    monkeypatch.setattr(DF.DataFetcher, "get_data_range", staticmethod(fake_range))
    db.add(_signal())
    await db.commit()

    stats = await SO.sweep(db, lookback_days=3650)
    assert stats["measured"] == 1, stats
    row = (await db.execute(select(SignalOutcome))).scalars().first()
    assert row.hit_tp is True and row.outcome_r == pytest.approx(2.0)
    assert row.skip_gate == "adx_below_20"
    assert "fill assumed" in row.note
    gates = SO.gate_cost([row])
    assert gates[0]["gate"] == "adx_below_20"
    assert gates[0]["verdict"] == "COSTING_MONEY"


@pytest.mark.asyncio
async def test_a_signal_with_no_stop_is_not_scored(db, monkeypatch):
    """No stop means no R and no plan to follow. Inventing one would put a
    number in the gate ledger that came from nowhere."""
    from backend.mt5 import data_fetcher as DF

    async def fake_range(*a, **k):
        return bars([(15, 103.0, 99.0, 102.0)])

    monkeypatch.setattr(DF.DataFetcher, "get_data_range", staticmethod(fake_range))
    db.add(_signal(stop_loss=None))
    await db.commit()
    stats = await SO.sweep(db, lookback_days=3650)
    assert stats["measured"] == 0 and stats["no_plan_or_bars"] == 1


@pytest.mark.asyncio
async def test_sweeping_twice_converges(db, monkeypatch):
    from sqlalchemy import func, select

    from backend.data.models import SignalOutcome
    from backend.mt5 import data_fetcher as DF

    async def fake_range(*a, **k):
        return bars([(15, 102.5, 99.5, 102.0)])

    monkeypatch.setattr(DF.DataFetcher, "get_data_range", staticmethod(fake_range))
    db.add(_signal())
    await db.commit()
    await SO.sweep(db, lookback_days=3650)
    second = await SO.sweep(db, lookback_days=3650)
    n = (await db.execute(select(func.count()).select_from(SignalOutcome))).scalar()
    assert n == 1 and second["already_complete"] == 1
