"""The referral tree and what it earns.

Money arithmetic first, because an error there only ever shows up as a figure
that is slightly wrong on somebody's statement. Then the tree, because a loop
in it makes the walk that PAYS people run forever.
"""

import tempfile
import uuid
from decimal import Decimal as D
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.investor import referrals as R


# -- the split, both models ----------------------------------------------------

def test_house_fee_model_pays_the_introducer_out_of_our_fee():
    out = R.split_for(model="HOUSE_FEE", fee_total=D("100.00"), period_profit=D("500"),
                      l1_pct=D("20"), l2_pct=D("5"), has_l1=True, has_l2=True)
    assert out["l1"] == D("20.00") and out["l2"] == D("5.00")
    # the house keeps what is left of ITS OWN fee; the investor is untouched
    assert out["house_remainder"] == D("75.00")
    assert out["basis"] == D("100.00")


def test_investor_profit_model_takes_it_from_the_investors_profit():
    out = R.split_for(model="INVESTOR_PROFIT", fee_total=D("100.00"),
                      period_profit=D("500.00"), l1_pct=D("10"), l2_pct=D("2"),
                      has_l1=True, has_l2=True)
    assert out["basis"] == D("500.00")
    assert out["l1"] == D("50.00") and out["l2"] == D("10.00")
    # the house fee is NOT reduced under this model
    assert out["house_remainder"] == D("100.00")


def test_a_losing_period_earns_nobody_anything_under_investor_profit():
    out = R.split_for(model="INVESTOR_PROFIT", fee_total=D("0"), period_profit=D("-300"),
                      l1_pct=D("10"), l2_pct=D("2"), has_l1=True, has_l2=True)
    assert out["l1"] == D("0.00") and out["l2"] == D("0.00")


def test_no_level_two_means_no_level_two_payment():
    out = R.split_for(model="HOUSE_FEE", fee_total=D("100"), period_profit=D("0"),
                      l1_pct=D("20"), l2_pct=D("5"), has_l1=True, has_l2=False)
    assert out["l1"] == D("20.00") and out["l2"] == D("0.00")
    assert out["house_remainder"] == D("80.00")


def test_paying_out_more_than_the_fee_is_capped_not_made_negative():
    """Rates summing past 100% of our fee would make the house take negative -
    that is not an arrangement, it is a bug with a settings screen in front."""
    out = R.split_for(model="HOUSE_FEE", fee_total=D("100"), period_profit=D("0"),
                      l1_pct=D("90"), l2_pct=D("30"), has_l1=True, has_l2=True)
    assert out["capped"] is True
    assert out["l1"] + out["l2"] <= D("100.00")
    assert out["house_remainder"] >= D("0")


def test_off_pays_nothing_and_keeps_the_whole_fee():
    out = R.split_for(model="OFF", fee_total=D("100"), period_profit=D("500"),
                      l1_pct=D("20"), l2_pct=D("5"), has_l1=True, has_l2=True)
    assert out["l1"] == D("0") and out["l2"] == D("0")
    assert out["house_remainder"] == D("100")


def test_an_unknown_model_is_refused_not_guessed():
    with pytest.raises(ValueError):
        R.split_for(model="SOMETHING", fee_total=D("1"), period_profit=D("1"),
                    l1_pct=D("1"), l2_pct=D("1"), has_l1=True, has_l2=False)


# -- the code ------------------------------------------------------------------

def test_codes_avoid_the_characters_people_misread():
    for _ in range(300):
        c = R.new_code()
        assert len(c) == R.CODE_LEN
        assert not set(c) & set("IO01")


# -- the tree, against a real database ------------------------------------------

@pytest_asyncio.fixture
async def db():
    from backend.data.database import Base
    import backend.investor.models  # noqa: F401  - registers the tables

    tmp = tempfile.mkdtemp(prefix="algoedge-ref-")
    engine = create_async_engine(f"sqlite+aiosqlite:///{Path(tmp).as_posix()}/t.db")
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        yield s
    await engine.dispose()


async def _inv(db, name):
    from backend.investor.models import Investor

    i = Investor(id=str(uuid.uuid4()), email=f"{name}@x.test", name=name, status="active")
    db.add(i)
    await db.flush()
    return i


@pytest.mark.asyncio
async def test_a_two_level_chain_walks_up_and_down(db):
    a, b, c = await _inv(db, "a"), await _inv(db, "b"), await _inv(db, "c")
    await R.set_referrer(db, b.id, a.id)          # a introduced b
    await R.set_referrer(db, c.id, b.id)          # b introduced c
    assert await R.upline(db, c.id) == [(1, b.id), (2, a.id)]
    down = await R.downline_ids(db, a.id)
    assert down == {1: [b.id], 2: [c.id]}


@pytest.mark.asyncio
async def test_earnings_stop_at_two_levels(db):
    """d is FOUR below a. a must not appear in d's upline at all."""
    a, b, c, d = [await _inv(db, n) for n in "abcd"]
    await R.set_referrer(db, b.id, a.id)
    await R.set_referrer(db, c.id, b.id)
    await R.set_referrer(db, d.id, c.id)
    up = await R.upline(db, d.id)
    assert [lvl for lvl, _ in up] == [1, 2]
    assert a.id not in [i for _, i in up]


@pytest.mark.asyncio
async def test_a_loop_is_refused(db):
    """Put A under B, then try to put B under A. The second must fail, or the
    payout walk never finishes."""
    a, b = await _inv(db, "a"), await _inv(db, "b")
    await R.set_referrer(db, b.id, a.id)
    with pytest.raises(ValueError, match="loop"):
        await R.set_referrer(db, a.id, b.id)


@pytest.mark.asyncio
async def test_a_longer_loop_is_refused_too(db):
    a, b, c = [await _inv(db, n) for n in "abc"]
    await R.set_referrer(db, b.id, a.id)
    await R.set_referrer(db, c.id, b.id)
    with pytest.raises(ValueError):
        await R.set_referrer(db, a.id, c.id)     # would close a -> b -> c -> a


@pytest.mark.asyncio
async def test_nobody_can_be_their_own_referrer(db):
    a = await _inv(db, "a")
    with pytest.raises(ValueError):
        await R.set_referrer(db, a.id, a.id)


@pytest.mark.asyncio
async def test_an_admin_can_move_and_detach_someone(db):
    a, b, c = [await _inv(db, n) for n in "abc"]
    await R.set_referrer(db, c.id, a.id, source="LINK")
    moved = await R.set_referrer(db, c.id, b.id, source="ADMIN")
    assert moved["referred_by"] == b.id and moved["source"] == "ADMIN"
    gone = await R.set_referrer(db, c.id, None)
    assert gone["detached"] is True
    assert await R.upline(db, c.id) == []


@pytest.mark.asyncio
async def test_a_code_is_issued_once_and_never_changes(db):
    a = await _inv(db, "a")
    first = await R.ensure_code(db, a)
    again = await R.ensure_code(db, a)
    assert first == again
    found = await R.by_code(db, first.lower())     # case-insensitive
    assert found is not None and found.id == a.id


# -- accruing what is owed, through the real tables ---------------------------

from datetime import date  # noqa: E402


async def _settings(db, model="HOUSE_FEE", l1="20", l2="5", version=1):
    from backend.investor.models import FundSettings

    fs = FundSettings(version=version, referral_model=model,
                      referral_l1_pct=D(l1), referral_l2_pct=D(l2))
    db.add(fs)
    await db.flush()
    return fs


async def _accrual(db, investor_id, kind, fee, state="charged"):
    from backend.investor.models import FeeAccrual

    a = FeeAccrual(investor_id=investor_id, kind=kind, period_start=date(2026, 1, 1),
                   period_end=date(2026, 2, 28), basis_amount=D("0"), rate_pct=D("0"),
                   fee_amount=D(fee), state=state)
    db.add(a)
    await db.flush()
    return a


async def _chain(db):
    a, b, c = [await _inv(db, n) for n in "abc"]
    await R.set_referrer(db, b.id, a.id)
    await R.set_referrer(db, c.id, b.id)      # c is the investor being charged
    return a, b, c


async def _accrue(db, c, accruals, profit="500", waived=False):
    return await R.accrue(db, investor_id=c.id, accruals=accruals,
                          period_profit=D(profit), period_start=date(2026, 1, 1),
                          period_end=date(2026, 2, 28), nav_price=D("1"),
                          actor_id="admin", waived=waived)


@pytest.mark.asyncio
async def test_house_fee_pays_both_levels_a_share_of_what_we_collected(db):
    await _settings(db, "HOUSE_FEE", "20", "5")
    a, b, c = await _chain(db)
    m = await _accrual(db, c.id, "MANAGEMENT", "40.00")
    p = await _accrual(db, c.id, "PERFORMANCE", "60.00")
    rows = await _accrue(db, c, [m, p])
    by = {r.level: r for r in rows}
    assert by[1].earner_id == b.id and by[1].amount == D("20.00")   # 20% of 100
    assert by[2].earner_id == a.id and by[2].amount == D("5.00")    # 5% of 100
    assert all(r.source_investor_id == c.id for r in rows)


@pytest.mark.asyncio
async def test_running_the_same_period_twice_pays_nobody_twice(db):
    await _settings(db)
    a, b, c = await _chain(db)
    p = await _accrual(db, c.id, "PERFORMANCE", "100.00")
    first = await _accrue(db, c, [p])
    second = await _accrue(db, c, [p])
    assert len(first) == 2 and second == []


@pytest.mark.asyncio
async def test_a_waived_fee_owes_the_introducer_nothing_under_house_fee(db):
    await _settings(db)
    a, b, c = await _chain(db)
    p = await _accrual(db, c.id, "PERFORMANCE", "100.00", state="waived")
    assert await _accrue(db, c, [p], waived=True) == []


@pytest.mark.asyncio
async def test_investor_profit_charges_the_investor_for_the_share(db, monkeypatch):
    """Under this model the introducer is paid out of the investor's OWN
    profit, so their units must actually be reduced - through the same path
    as a fee, so it appears in their ledger."""
    from backend.investor import units as unitsmod

    charged = []

    async def fake_redeem(session, **kw):
        charged.append(kw)

    monkeypatch.setattr(unitsmod, "redeem", fake_redeem)
    await _settings(db, "INVESTOR_PROFIT", "10", "2")
    a, b, c = await _chain(db)
    p = await _accrual(db, c.id, "PERFORMANCE", "100.00")
    rows = await _accrue(db, c, [p], profit="500")
    assert {r.level: r.amount for r in rows} == {1: D("50.00"), 2: D("10.00")}
    # one ledger entry PER EARNING, each sourced from its own earning row
    assert sorted(k["amount"] for k in charged) == [D("10.00"), D("50.00")]
    assert {k["source_kind"] for k in charged} == {"referral_earning"}
    assert {k["source_id"] for k in charged} == {r.id for r in rows}
    assert all(k["investor_id"] == c.id and k["kind"] == "FEE" for k in charged)


@pytest.mark.asyncio
async def test_investor_profit_waiver_lets_the_investor_off_the_share_too(db, monkeypatch):
    from backend.investor import units as unitsmod

    called = []
    monkeypatch.setattr(unitsmod, "charge_fee", lambda *a, **k: called.append(1))
    await _settings(db, "INVESTOR_PROFIT", "10", "2")
    a, b, c = await _chain(db)
    p = await _accrual(db, c.id, "PERFORMANCE", "100.00", state="waived")
    assert await _accrue(db, c, [p], waived=True) == []
    assert called == []


@pytest.mark.asyncio
async def test_an_investor_nobody_introduced_produces_nothing(db):
    await _settings(db)
    loner = await _inv(db, "loner")
    p = await _accrual(db, loner.id, "PERFORMANCE", "100.00")
    assert await _accrue(db, loner, [p]) == []


@pytest.mark.asyncio
async def test_off_produces_nothing_even_with_a_tree(db):
    await _settings(db, "OFF")
    a, b, c = await _chain(db)
    p = await _accrual(db, c.id, "PERFORMANCE", "100.00")
    assert await _accrue(db, c, [p]) == []



@pytest.mark.asyncio
async def test_investor_profit_survives_the_real_ledger_after_a_performance_fee(db):
    """NOT stubbed, on purpose. `unit_transactions` is unique on (source_kind,
    source_id). The performance fee writes ("fee_accrual", <accrual>) first;
    the introducer share used to write the SAME pair, which raised
    IntegrityError and rolled back the whole fee charge - so closing a period
    failed for every referred investor whenever a performance fee was due.
    A stubbed ledger cannot see a database constraint; only this can."""
    from sqlalchemy import select

    from backend.investor import units as unitsmod
    from backend.investor.models import UnitTransaction

    await _settings(db, "INVESTOR_PROFIT", "10", "2")
    a, b, c = await _chain(db)
    await unitsmod.subscribe(db, investor_id=c.id, amount=D("1000.00"), price=D("1"),
                             effective=date(2026, 1, 1), note="seed")
    p = await _accrual(db, c.id, "PERFORMANCE", "100.00")
    # exactly what fees.charge does for the performance fee, before accrue runs
    await unitsmod.charge_fee(db, investor_id=c.id, amount=D("100.00"), price=D("1"),
                              effective=date(2026, 2, 28), source_id=p.id,
                              note="performance fee")
    rows = await _accrue(db, c, [p], profit="500")
    await db.flush()

    assert {r.level: r.amount for r in rows} == {1: D("50.00"), 2: D("10.00")}
    fees = (await db.execute(select(UnitTransaction).where(
        UnitTransaction.investor_id == c.id, UnitTransaction.kind == "FEE"))).scalars().all()
    by_source = sorted((f.source_kind, f.money_amount if hasattr(f, "money_amount") else None)
                       for f in fees)
    assert len(fees) == 3, by_source                    # performance + two shares
    assert sum(1 for f in fees if f.source_kind == "referral_earning") == 2
