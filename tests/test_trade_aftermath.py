"""What a hand-closed trade went on to do.

The arithmetic is the risky half: an inverted sign on a SELL is invisible in
aggregate and reverses every conclusion drawn from the table, so every case
here is run in both directions.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pandas as pd
import pytest

from backend.services import trade_aftermath as A

CLOSED = datetime(2026, 1, 5, 12, 0)          # naive UTC, as the DB stores it
START = int(CLOSED.replace(tzinfo=timezone.utc).timestamp())


def bars(rows):
    """`rows` is [(minutes_after_close, high, low, close), ...]."""
    return pd.DataFrame({
        "time": [START + m * 60 for m, _, _, _ in rows],
        "high": [h for _, h, _, _ in rows],
        "low": [lo for _, _, lo, _ in rows],
        "close": [c for _, _, _, c in rows],
    })


def run(frame, **kw):
    base = dict(direction="BUY", entry_price=100.0, stop_loss=99.0,
                take_profit=103.0, exit_price=100.5, closed_at=CLOSED,
                watch_until=CLOSED + timedelta(hours=72))
    return A.measure(frame, **{**base, **kw})


# -- it reached the target ---------------------------------------------------

def test_a_long_that_went_on_to_the_target_is_recorded_as_such():
    out = run(bars([(5, 101.0, 100.2, 100.8),
                    (10, 102.0, 100.9, 101.9),
                    (15, 103.4, 101.8, 103.2)]))
    assert out["hit_tp"] is True and out["hit_sl"] is False
    assert out["tp_minutes"] == 15
    # risk is 1.00, so the 103.4 high is +3.4R
    assert out["best_r"] == pytest.approx(3.4, abs=1e-9)


def test_a_short_that_went_on_to_the_target_is_recorded_the_same_way():
    """The mirror. If the sign is wrong anywhere this is where it shows."""
    out = run(bars([(5, 99.8, 99.0, 99.2),
                    (10, 99.1, 98.0, 98.1),
                    (15, 98.2, 96.6, 96.8)]),
              direction="SELL", entry_price=100.0, stop_loss=101.0,
              take_profit=97.0, exit_price=99.5)
    assert out["hit_tp"] is True and out["hit_sl"] is False
    assert out["tp_minutes"] == 15
    assert out["best_r"] == pytest.approx(3.4, abs=1e-9)   # 100 - 96.6


# -- it reached the stop -----------------------------------------------------

def test_the_best_point_before_the_stop_is_what_gets_recorded():
    out = run(bars([(5, 101.5, 100.2, 101.2),     # +1.5R at best
                    (10, 101.3, 100.1, 100.2),
                    (15, 100.3, 98.9, 99.0)]))    # stop at 99.0
    assert out["hit_sl"] is True and out["hit_tp"] is False
    assert out["sl_minutes"] == 15
    assert out["best_r"] == pytest.approx(1.5, abs=1e-9)
    assert out["best_minutes"] == 5


def test_a_high_reached_AFTER_the_stop_does_not_count():
    """The trade was over. Counting a later high measures a position that no
    longer existed, and would make every stopped-out trade look recoverable."""
    out = run(bars([(5, 100.5, 98.5, 98.6),       # stopped here
                    (10, 105.0, 99.0, 104.5)]))   # a rally it never saw
    assert out["hit_sl"] is True
    assert out["best_r"] == pytest.approx(0.5, abs=1e-9), out["best_r"]
    assert out["best_minutes"] == 5


# -- it reached neither ------------------------------------------------------

def test_reaching_neither_reports_where_it_stood_at_the_end():
    out = run(bars([(5, 100.6, 100.1, 100.4),
                    (10, 101.2, 100.3, 100.9)]))
    assert out["hit_tp"] is False and out["hit_sl"] is False
    assert out["r_at_watch_end"] == pytest.approx(0.9, abs=1e-9)
    assert out["best_r"] == pytest.approx(1.2, abs=1e-9)
    assert "provisional" in out["note"]


# -- the bar that contains both ----------------------------------------------

def test_one_bar_covering_both_records_the_stop_and_says_so():
    out = run(bars([(5, 103.5, 98.5, 100.0)]))
    assert out["hit_sl"] is True and out["hit_tp"] is False
    assert out["ambiguous_bar"] is True
    assert "cannot say which came first" in out["note"]


# -- degenerate inputs -------------------------------------------------------

@pytest.mark.parametrize("bad", ["", None, "LONG?", "buy now"])
def test_an_unknown_direction_is_refused_not_scored_as_a_short(bad):
    out = run(bars([(5, 103.5, 100.2, 103.2)]), direction=bad)
    assert out["best_r"] is None and out["hit_tp"] is False and out["hit_sl"] is False
    assert "neither BUY nor SELL" in out["note"]


def test_direction_is_case_insensitive():
    assert run(bars([(5, 103.5, 100.2, 103.2)]), direction="buy")["hit_tp"] is True


def test_no_bars_is_reported_rather_than_crashed():
    out = A.measure(pd.DataFrame(), direction="BUY", entry_price=100.0,
                    stop_loss=99.0, take_profit=103.0, exit_price=100.0,
                    closed_at=CLOSED, watch_until=CLOSED + timedelta(hours=1))
    assert out["bars_seen"] == 0 and out["best_r"] is None
    assert "nothing could be measured" in out["note"]


def test_a_trade_with_no_stop_still_reports_prices_but_no_R():
    """R is a multiple of risk, and with no stop there is no risk to divide by.
    Reporting 0.0 would be a number that reads as 'no edge' rather than 'not
    computable'."""
    out = run(bars([(5, 101.0, 100.2, 100.8)]), stop_loss=None)
    assert out["best_price"] == pytest.approx(101.0)
    assert out["best_r"] is None


# -- the verdict -------------------------------------------------------------

def test_a_close_that_gave_up_more_than_the_band_is_called_out():
    v, forgone = A.verdict_of(0.3, 2.1, hit_tp=True, hit_sl=False)
    assert v == "LEFT_MONEY" and forgone == pytest.approx(1.8)


def test_a_close_that_avoided_the_stop_is_credited():
    v, forgone = A.verdict_of(-0.2, 0.1, hit_tp=False, hit_sl=True)
    assert v == "SAVED_LOSS" and forgone == pytest.approx(0.3)


def test_a_close_inside_the_noise_band_is_neither():
    assert A.verdict_of(0.8, 1.0, hit_tp=False, hit_sl=False)[0] == "FAIR"


def test_an_unmeasurable_close_is_not_silently_called_fair():
    assert A.verdict_of(None, 1.0, hit_tp=False, hit_sl=False)[0] == "UNKNOWN"
    assert A.verdict_of(0.5, None, hit_tp=False, hit_sl=False)[0] == "UNKNOWN"


# -- which closes are worth scoring at all -----------------------------------

@pytest.mark.parametrize("reason,planned", [
    ("TP", True), ("SL", True), ("TRAIL", True), ("TP2", True),
    ("STOP_LOSS", True), ("BREAKEVEN", True),
    ("MANUAL", False), ("MANUAL_OVERRIDE", False), ("CLIENT", False),
    ("NO_POSITIONS", False), ("", False), (None, False),
])
def test_only_closes_the_plan_did_not_make_are_scored(reason, planned):
    assert A.was_planned_exit(reason) is planned


def test_who_closed_it_is_recorded_not_guessed():
    assert A.closed_by("MANUAL_OVERRIDE") == "USER"
    assert A.closed_by("CLIENT") == "BOT"
    assert A.closed_by("something new") == "UNKNOWN"


# -- the sweep: the half that touches the database ---------------------------
# The arithmetic above is pure. This drives the real `sweep` against a real
# (temporary) database with the terminal stubbed out, because the bugs that
# live here are different ones: the wrong trades selected, a row written
# twice, or a provisional measurement frozen as if it were final.

import tempfile
from pathlib import Path

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def db():
    from backend.data.database import Base

    tmp = tempfile.mkdtemp(prefix="algoedge-after-")
    engine = create_async_engine(f"sqlite+aiosqlite:///{Path(tmp).as_posix()}/t.db")
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        yield s
    await engine.dispose()


def _trade(**kw):
    from backend.data.models import Trade

    base = dict(user_id="u1", strategy_id="ORB_v1", symbol="XAUUSD",
                direction="BUY", entry_price=100.0, exit_price=100.5,
                stop_loss=99.0, take_profit=103.0, volume=0.1,
                entry_time=CLOSED - timedelta(hours=2), exit_time=CLOSED,
                status="CLOSED", exit_reason="MANUAL")
    return Trade(**{**base, **kw})


def _stub_bars(monkeypatch, frame):
    """Stand in for the terminal. `measure_trade` is the only thing that fetches."""
    from backend.mt5 import data_fetcher as DF

    async def fake_range(symbol, timeframe, start, end):
        return frame

    monkeypatch.setattr(DF.DataFetcher, "get_data_range", staticmethod(fake_range))


@pytest.mark.asyncio
async def test_a_hand_closed_trade_is_measured_and_scored(db, monkeypatch):
    from backend.data.models import TradeAftermath
    from backend.services import trade_aftermath as A
    from sqlalchemy import select

    db.add(_trade())
    await db.commit()
    _stub_bars(monkeypatch, bars([(5, 101.5, 100.2, 101.2),
                                  (10, 103.5, 101.0, 103.2)]))

    stats = await A.sweep(db, lookback_days=3650)
    assert stats["measured"] == 1, stats

    row = (await db.execute(select(TradeAftermath))).scalars().first()
    assert row.hit_tp is True
    assert row.closed_r == pytest.approx(0.5)      # closed at 100.5, risk 1.0
    assert row.best_r == pytest.approx(3.5)        # it ran to 103.5
    assert row.verdict == "LEFT_MONEY"
    assert row.forgone_r == pytest.approx(3.0)
    assert row.closed_by == "USER"


@pytest.mark.asyncio
async def test_a_stop_or_target_exit_is_never_scored(db, monkeypatch):
    """There is nothing to second-guess about an exit the plan made, and
    scoring them would drown the hand-closes this table exists for."""
    from backend.services import trade_aftermath as A

    db.add(_trade(exit_reason="TP"))
    db.add(_trade(exit_reason="SL"))
    db.add(_trade(exit_reason="TRAIL"))
    await db.commit()
    _stub_bars(monkeypatch, bars([(5, 101.0, 100.0, 100.5)]))

    stats = await A.sweep(db, lookback_days=3650)
    assert stats["measured"] == 0
    assert stats["skipped_planned"] == 3


@pytest.mark.asyncio
async def test_running_twice_updates_rather_than_duplicates(db, monkeypatch):
    """The sweep runs daily and backfills on demand, so it has to converge."""
    from backend.data.models import TradeAftermath
    from backend.services import trade_aftermath as A
    from sqlalchemy import select

    db.add(_trade())
    await db.commit()
    _stub_bars(monkeypatch, bars([(5, 103.5, 100.2, 103.2)]))

    await A.sweep(db, lookback_days=3650)
    await A.sweep(db, lookback_days=3650)

    rows = (await db.execute(select(TradeAftermath))).scalars().all()
    assert len(rows) == 1, f"{len(rows)} rows for one trade"


@pytest.mark.asyncio
async def test_a_finished_row_is_not_measured_again(db, monkeypatch):
    from backend.services import trade_aftermath as A

    db.add(_trade())
    await db.commit()
    _stub_bars(monkeypatch, bars([(5, 103.5, 100.2, 103.2)]))   # hits TP -> done

    first = await A.sweep(db, lookback_days=3650)
    second = await A.sweep(db, lookback_days=3650)
    assert first["measured"] == 1
    assert second["measured"] == 0 and second["already_complete"] == 1


# -- the summary route: provisional rows must not move the headline ----------

@pytest.mark.asyncio
async def test_the_summary_counts_only_finished_rows(db):
    """A verdict whose window is still open can flip tomorrow. It belongs in
    `still_watching`, not in "% closed too early" - and `gate_cost` already
    excludes provisional rows, so the two summaries must agree."""
    import httpx
    from fastapi import FastAPI

    from backend.api.deps import get_current_user
    from backend.api.routes import trades as trades_routes
    from backend.data.database import get_db
    from backend.data.models import TradeAftermath

    for i, (verdict, forgone, done) in enumerate([("LEFT_MONEY", 2.2, True),
                                                  ("SAVED_LOSS", 0.4, True),
                                                  ("LEFT_MONEY", 9.9, False)]):
        t = _trade()
        db.add(t)
        await db.flush()
        db.add(TradeAftermath(trade_id=t.id, user_id="u1", symbol="XAUUSD",
                              closed_at=datetime.utcnow() - timedelta(days=1 + i),
                              verdict=verdict, forgone_r=forgone, watch_complete=done))
    await db.commit()

    app = FastAPI()
    app.include_router(trades_routes.router)

    async def _db():
        yield db

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: NS(id="u1")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://t") as c:
        body = (await c.get("/api/trades/aftermath")).json()

    s = body["summary"]
    assert body["total"] == 3
    assert s["scored"] == 2 and s["still_watching"] == 1
    assert s["pct_left_money"] == 50.0
    assert s["median_forgone_r"] == pytest.approx(1.3), "the provisional 9.9R leaked in"
