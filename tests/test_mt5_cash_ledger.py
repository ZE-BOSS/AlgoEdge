"""
tests/test_mt5_cash_ledger.py

Classifying MT5 deals, using both brokers' REAL taxonomies as the fixtures.

WHY THE FIXTURES ARE WHAT THEY ARE
----------------------------------
These are not invented. They are what the two terminals actually returned on
2026-10-08:

    Deriv 6194570 (Deriv-Demo)        497 deals from 2026-08-01
      type 2 DEAL_BALANCE  n=2   +4,121.81 and -115.80
                                 both commented "Reset+Balance+for+Demo+Acount"
      profit 247.25 + swap -548.31 = -301.06 against a balance of 9,420.30

    FundedNext 11869564               171 deals from 2026-04-10
      type 4 DEAL_CHARGE   n=1   +25,000.00, commented "Initial+Deposit"
      profit 23,453.33 + commission -459.98 = 22,993.35, EXACTLY the balance

Three things follow, and each one is a test below:

  1. `type >= 2` — what the code did — counts commission, interest and tax as
     deposits. It feeds `prop_firm_validator`, which treats a FALL in that
     total as a withdrawal and re-baselines the drawdown high-water mark.
  2. `type == 2` — the obvious correction — misses FundedNext's entire
     $25,000, because that broker funds through CHARGE.
  3. The same Deriv comment covers a top-up AND a deduction, so comment text
     cannot decide direction. Sign does.

Hence: the map is per-account DATA, and anything unplaceable is UNKNOWN, which
blocks the reconciliation rather than being absorbed into it.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from backend.mt5 import history
from backend.mt5.cash_models import (
    ADJUSTMENT,
    CAPITAL_IN,
    CAPITAL_OUT,
    COST,
    CREDIT_NOT_CAPITAL,
    TRADE_RESULT,
    UNKNOWN,
    MT5Account,
)


def deal(**kw):
    """An MT5 deal as the package returns it — a plain attribute bag."""
    base = dict(ticket=1, type=0, entry=1, position_id=1, order=1, symbol="EURUSD",
                volume=0.1, price=1.1, profit=0.0, commission=0.0, swap=0.0, fee=0.0,
                comment="", magic=0, time=1759000000)
    base.update(kw)
    return SimpleNamespace(**base)


DERIV = MT5Account(
    login=6194570, broker="Deriv", server="Deriv-Demo",
    adjustment_comment_patterns=json.dumps(["reset+balance"]),
)
FUNDEDNEXT = MT5Account(
    login=11869564, broker="FundedNext", server="FundedNext-Server",
    # measured: this broker funds the account with a CHARGE
    deal_class_overrides=json.dumps({"4": CAPITAL_IN}),
)


# ── the amount ──────────────────────────────────────────────────────────────

def test_the_amount_includes_the_costs_on_the_same_row():
    """FundedNext reconciles to the cent only with the commission term.

    profit alone gives 23,453.33; profit + commission gives 22,993.35, which is
    the balance. Summing `profit` is how the costs disappear.
    """
    d = deal(profit=100.0, commission=-2.5, swap=-0.75, fee=-0.25)
    assert history.signed_amount(d) == pytest.approx(96.5)


# ── the two brokers ─────────────────────────────────────────────────────────

def test_fundednext_funds_the_account_with_a_charge():
    d = deal(ticket=9001, type=4, profit=25000.0, comment="Initial+Deposit", symbol="")
    got = history.classify(d, FUNDEDNEXT)
    assert got.flow_class == CAPITAL_IN
    assert got.amount == pytest.approx(25000.0)
    assert got.by == "override"


def test_the_obvious_fix_would_have_missed_that_entirely():
    """Pins the trap: without the account's override, a CHARGE is a cost.

    This is the test that stops someone "tidying" the classifier into
    `type == 2` and silently losing $25,000 of funding.
    """
    d = deal(ticket=9001, type=4, profit=25000.0, comment="Initial+Deposit")
    assert history.classify(d, None).flow_class == COST
    assert history.classify(d, DERIV).flow_class == COST
    assert history.classify(d, FUNDEDNEXT).flow_class == CAPITAL_IN


def test_a_deriv_demo_reset_is_an_adjustment_not_a_deposit():
    """Both of Deriv's balance deals carry the same comment, one positive and
    one negative. Neither is investor capital."""
    up = deal(ticket=1, type=2, profit=4121.81, comment="Reset+Balance+for+Demo+Acount")
    down = deal(ticket=2, type=2, profit=-115.80, comment="Reset+Balance+for+Demo+Acount")
    assert history.classify(up, DERIV).flow_class == ADJUSTMENT
    assert history.classify(down, DERIV).flow_class == ADJUSTMENT


def test_a_real_balance_deal_is_capital_and_the_sign_decides():
    """With no reset pattern matching, BALANCE is a genuine deposit or
    withdrawal — and only the sign says which."""
    assert history.classify(deal(type=2, profit=500.0, comment="Deposit"),
                            DERIV).flow_class == CAPITAL_IN
    assert history.classify(deal(type=2, profit=-200.0, comment="Withdrawal"),
                            DERIV).flow_class == CAPITAL_OUT


# ── the class the old rule got wrong ───────────────────────────────────────

@pytest.mark.parametrize("deal_type", [7, 8, 9, 10, 11, 12, 17])
def test_commission_interest_and_tax_are_costs_not_deposits(deal_type):
    """`type >= 2` called every one of these a deposit.

    It feeds prop_firm_validator.update_equity_balance(net_deposits=...), which
    treats a fall in that total as a withdrawal and moves the drawdown
    high-water mark for no reason.
    """
    got = history.classify(deal(type=deal_type, profit=-12.0), None)
    assert got.flow_class == COST, f"deal type {deal_type} must not count as capital"


@pytest.mark.parametrize("deal_type", [3, 6])
def test_credit_and_bonus_never_back_investor_money(deal_type):
    """Not withdrawable, so it must never be counted as capital in the pool."""
    assert history.classify(deal(type=deal_type, profit=1000.0), None).flow_class \
        == CREDIT_NOT_CAPITAL


@pytest.mark.parametrize("deal_type", [0, 1, 15, 16])
def test_trades_and_dividends_are_results(deal_type):
    assert history.classify(deal(type=deal_type, profit=9.0), None).flow_class == TRADE_RESULT


# ── refusing to guess ──────────────────────────────────────────────────────

def test_a_broker_correction_is_sent_for_review_rather_than_guessed():
    got = history.classify(deal(type=5, profit=-40.0, comment="correction"), None)
    assert got.flow_class == UNKNOWN
    assert got.review_reason and "review" in got.review_reason.lower()


def test_an_unheard_of_deal_type_is_sent_for_review():
    got = history.classify(deal(type=99, profit=10.0), None)
    assert got.flow_class == UNKNOWN
    assert "99" in got.review_reason


def test_a_capital_movement_of_zero_is_not_a_movement():
    """Sign decides direction, so a zero has no direction to give."""
    assert history.classify(deal(type=2, profit=0.0), None).flow_class == UNKNOWN


def test_unreadable_overrides_do_not_crash_the_classifier():
    """A bad settings value must not take the ledger down with it."""
    broken = MT5Account(login=1, deal_class_overrides="{not json")
    assert history.classify(deal(type=2, profit=100.0), broken).flow_class == CAPITAL_IN


def test_an_override_naming_a_class_we_do_not_know_is_refused():
    odd = MT5Account(login=1, deal_class_overrides=json.dumps({"2": "MAGIC"}))
    assert history.classify(deal(type=2, profit=100.0), odd).flow_class == UNKNOWN


# ── the whole FundedNext account, reconciled ───────────────────────────────

def test_the_fundednext_history_reconciles_to_the_cent():
    """The real shape of that account: 170 trade deals plus the funding charge.

    Reproduced at the measured totals rather than deal by deal — the point is
    that opening + capital + results + costs lands on the balance, which it did
    on the terminal and must keep doing here.
    """
    deals = [deal(ticket=9001, type=4, profit=25000.0, comment="Initial+Deposit")]
    # the 170 position deals, netting to the measured profit and commission
    deals.append(deal(ticket=1, type=0, profit=-630.15, commission=-229.99))
    deals.append(deal(ticket=2, type=1, profit=-916.52, commission=-229.99))

    total = 0.0
    classes = {}
    for d in deals:
        got = history.classify(d, FUNDEDNEXT)
        total += got.amount
        classes.setdefault(got.flow_class, 0.0)
        classes[got.flow_class] += got.amount

    assert classes[CAPITAL_IN] == pytest.approx(25000.0)
    assert classes[TRADE_RESULT] == pytest.approx(-630.15 - 916.52 - 459.98)
    assert total == pytest.approx(22993.35, abs=0.01), (
        "the measured FundedNext balance, to the cent"
    )


# ── storing and reconciling ────────────────────────────────────────────────
#
# The live run of this, 2026-10-08, against both terminals:
#
#   FundedNext 11869564   171 deals  {CAPITAL_IN: 1, TRADE_RESULT: 170}
#                         expected 22,993.35 = actual 22,993.35, residual 0.00
#   Deriv      6194570    497 deals  {TRADE_RESULT: 495, ADJUSTMENT: 2}
#                         residual 9,721.36 until an anchor was supplied, then 0.00
#
# Reproduced here without a terminal, because a test that needs MT5 attached is
# a test that does not run.

import asyncio
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.data.models import Base
from backend.mt5.cash_models import MT5CashFlow, OpeningAnchor


class Ledger:
    """A throwaway database with the cash tables."""

    async def __aenter__(self):
        # The investor models, which pull in the cash tables with them. The
        # curves endpoint reads `nav_snapshots` as well as the cash ledger —
        # showing the account balance without what investors were told is half
        # the picture — so both halves of the schema have to exist here.
        import backend.investor.models  # noqa: F401 — registers both

        self._tmp = tempfile.mkdtemp(prefix="algoedge-cash-")
        self.engine = create_async_engine(
            f"sqlite+aiosqlite:///{Path(self._tmp).as_posix()}/cash.db")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        return self

    async def __aexit__(self, *exc):
        await self.engine.dispose()
        shutil.rmtree(self._tmp, ignore_errors=True)


def _run(coro):
    return asyncio.run(coro)


FUNDEDNEXT_DEALS = [
    deal(ticket=9001, type=4, profit=25000.0, comment="Initial+Deposit", symbol="", time=1744300000),
    deal(ticket=9002, type=0, profit=-630.15, commission=-229.99, symbol="NDX100", time=1744400000),
    deal(ticket=9003, type=1, profit=-916.52, commission=-229.99, symbol="US30", time=1744500000),
]
DERIV_DEALS = [
    deal(ticket=1, type=2, profit=4121.81, comment="Reset+Balance+for+Demo+Acount",
         symbol="", time=1754380000),
    deal(ticket=2, type=2, profit=-115.80, comment="Reset+Balance+for+Demo+Acount",
         symbol="", time=1755370000),
    deal(ticket=3, type=0, profit=247.25, swap=-548.31, symbol="Crash 1000 Index", time=1754390000),
]


async def _seed(site, account, deals):
    async with site.Session() as s:
        s.add(account)
        await s.flush()
        await history.store_deals(s, account.login, deals)
        await s.commit()


async def _anchor(site, login, balance=0.0):
    async with site.Session() as s:
        s.add(OpeningAnchor(account_login=login, as_of=datetime(2000, 1, 1),
                            balance=balance, source="manual"))
        await s.commit()


def test_fundednext_reconciles_to_the_cent():
    async def go():
        async with Ledger() as site:
            await _seed(site, MT5Account(
                login=11869564, broker="FundedNext",
                deal_class_overrides=json.dumps({"4": CAPITAL_IN})), FUNDEDNEXT_DEALS)
            await _anchor(site, 11869564)
            async with site.Session() as s:
                rep = await history.reconcile(s, 11869564, actual_balance=22993.35)
            assert rep["capital_in"] == pytest.approx(25000.0)
            assert rep["residual"] == pytest.approx(0.0, abs=0.01)
            assert rep["healthy"] is True
            assert rep["blocked_by"] == []
    _run(go())


def test_a_reset_wiped_account_reports_its_gap_rather_than_hiding_it():
    """Deriv's pre-reset balance is not in the terminal, so the books cannot
    close — and the point is that the system says so."""
    async def go():
        async with Ledger() as site:
            await _seed(site, MT5Account(
                login=6194570, broker="Deriv",
                adjustment_comment_patterns=json.dumps(["reset+balance"])), DERIV_DEALS)
            await _anchor(site, 6194570)
            async with site.Session() as s:
                rep = await history.reconcile(s, 6194570, actual_balance=9420.30)
            # the resets are NOT deposits
            assert rep["capital_in"] == 0.0
            assert rep["adjustments"] == pytest.approx(4006.01)
            # and the gap is named, not absorbed
            assert rep["residual"] != 0
            assert rep["healthy"] is False
            assert any("residual" in b for b in rep["blocked_by"])
    _run(go())


def test_supplying_the_anchor_closes_it():
    """The residual IS the missing opening balance."""
    async def go():
        async with Ledger() as site:
            await _seed(site, MT5Account(
                login=6194570,
                adjustment_comment_patterns=json.dumps(["reset+balance"])), DERIV_DEALS)
            await _anchor(site, 6194570)
            async with site.Session() as s:
                gap = (await history.reconcile(s, 6194570, actual_balance=9420.30))["residual"]
                anchor = await history.get_anchor(s, 6194570)
                anchor.balance = gap
                await s.commit()
            async with site.Session() as s:
                rep = await history.reconcile(s, 6194570, actual_balance=9420.30)
            assert rep["residual"] == pytest.approx(0.0, abs=0.01)
            assert rep["healthy"] is True
    _run(go())


def test_syncing_twice_does_not_double_count():
    """UNIQUE(account_login, deal_ticket). This runs on every start."""
    async def go():
        async with Ledger() as site:
            await _seed(site, MT5Account(
                login=11869564,
                deal_class_overrides=json.dumps({"4": CAPITAL_IN})), FUNDEDNEXT_DEALS)
            async with site.Session() as s:
                again = await history.store_deals(s, 11869564, FUNDEDNEXT_DEALS)
                await s.commit()
            assert again["added"] == 0, "a re-sync must add nothing"
            async with site.Session() as s:
                rows = (await s.execute(select(MT5CashFlow).where(
                    MT5CashFlow.account_login == 11869564))).scalars().all()
            assert len(rows) == 3
    _run(go())


def test_an_unclassified_deal_blocks_healthy_even_when_the_books_balance():
    """A ledger that adds up while something in it is unexplained is not
    reconciled, it is lucky."""
    async def go():
        async with Ledger() as site:
            # a CORRECTION of zero: it cannot move the balance, so the
            # arithmetic still closes, but it has not been understood
            deals = FUNDEDNEXT_DEALS + [deal(ticket=9099, type=5, profit=0.0,
                                             comment="correction", time=1744600000)]
            await _seed(site, MT5Account(
                login=11869564,
                deal_class_overrides=json.dumps({"4": CAPITAL_IN})), deals)
            await _anchor(site, 11869564)
            async with site.Session() as s:
                rep = await history.reconcile(s, 11869564, actual_balance=22993.35)
            assert rep["residual"] == pytest.approx(0.0, abs=0.01)
            assert rep["unclassified_count"] == 1
            assert rep["healthy"] is False, "an unreviewed deal must block healthy"
            assert any("review" in b for b in rep["blocked_by"])
    _run(go())


def test_without_an_anchor_nothing_claims_to_be_healthy():
    async def go():
        async with Ledger() as site:
            await _seed(site, MT5Account(
                login=11869564,
                deal_class_overrides=json.dumps({"4": CAPITAL_IN})), FUNDEDNEXT_DEALS)
            async with site.Session() as s:
                rep = await history.reconcile(s, 11869564, actual_balance=22993.35)
            assert rep["has_anchor"] is False
            assert rep["healthy"] is False
            assert "no opening anchor" in rep["blocked_by"]
    _run(go())


def test_the_residual_is_reported_even_when_it_is_zero():
    """A check that only speaks up when unhappy is one people stop believing."""
    async def go():
        async with Ledger() as site:
            await _seed(site, MT5Account(
                login=11869564,
                deal_class_overrides=json.dumps({"4": CAPITAL_IN})), FUNDEDNEXT_DEALS)
            await _anchor(site, 11869564)
            async with site.Session() as s:
                rep = await history.reconcile(s, 11869564, actual_balance=22993.35)
            assert "residual" in rep and rep["residual"] == pytest.approx(0.0, abs=0.01)
    _run(go())


# ── the curves, and attributing old journal rows ───────────────────────────

from backend.data.models import Trade


def test_the_balance_curve_starts_at_the_anchor_not_at_zero():
    """A curve beginning at zero implies the account did, and on a funded
    account that is a lie."""
    async def go():
        async with Ledger() as site:
            await _seed(site, MT5Account(
                login=11869564,
                deal_class_overrides=json.dumps({"4": CAPITAL_IN})), FUNDEDNEXT_DEALS)
            await _anchor(site, 11869564, balance=500.0)
            async with site.Session() as s:
                from backend.api.routes.cash import curves
                out = await curves(login=11869564, db=s, _=None)
            assert out["opening_balance"] == 500.0
            assert out["series"]["balance"][0] != 0.0
            # and it ends where the deals leave it
            assert out["series"]["balance"][-1] == pytest.approx(500.0 + 22993.35, abs=0.01)
    _run(go())


def test_the_curves_share_one_date_axis():
    """The shape only means anything in comparison, so every series must be
    the same length as `days`."""
    async def go():
        async with Ledger() as site:
            await _seed(site, MT5Account(
                login=11869564,
                deal_class_overrides=json.dumps({"4": CAPITAL_IN})), FUNDEDNEXT_DEALS)
            await _anchor(site, 11869564)
            async with site.Session() as s:
                from backend.api.routes.cash import curves
                out = await curves(login=11869564, db=s, _=None)
            n = len(out["days"])
            assert n > 0
            for name, series in out["series"].items():
                assert len(series) == n, f"{name} is not on the same axis"
    _run(go())


def test_the_curves_say_so_rather_than_erroring_with_nothing_to_draw():
    async def go():
        async with Ledger() as site:
            async with site.Session() as s:
                from backend.api.routes.cash import curves
                out = await curves(login=None, db=s, _=None)
            assert out["days"] == []
            assert "note" in out
    _run(go())


def test_old_journal_rows_are_attributed_only_where_a_ticket_proves_it():
    """Guessing is worse than leaving a row blank: the wrong account puts a
    trade's P&L in the wrong reconciliation."""
    async def go():
        async with Ledger() as site:
            deals = [deal(ticket=9002, type=0, profit=10.0, position_id=555,
                          symbol="NDX100", time=1744400000)]
            await _seed(site, MT5Account(login=11869564), deals)
            async with site.Session() as s:
                s.add(Trade(user_id="u1", strategy_id="ORB_v1", symbol="NDX100",
                            direction="BUY", entry_price=1.0, stop_loss=0.9,
                            volume=0.1, status="CLOSED", mt5_ticket=555))
                s.add(Trade(user_id="u1", strategy_id="ORB_v1", symbol="US30",
                            direction="BUY", entry_price=1.0, stop_loss=0.9,
                            volume=0.1, status="CLOSED", mt5_ticket=999))
                s.add(Trade(user_id="u1", strategy_id="MANUAL_OFFLINE", symbol="XAUUSD",
                            direction="BUY", entry_price=1.0, stop_loss=0.9,
                            volume=0.1, status="CLOSED", mt5_ticket=None))
                await s.commit()
            async with site.Session() as s:
                out = await history.backfill_trade_accounts(s, 11869564)
                await s.commit()

            assert out["matched"] == 1, "only the row whose ticket is in this account"
            assert out["still_unattributed"] == 2
            async with site.Session() as s:
                rows = {t.symbol: t.mt5_account for t in
                        (await s.execute(select(Trade))).scalars().all()}
            assert rows["NDX100"] == 11869564
            assert rows["US30"] is None, "a ticket this account never saw is not evidence"
            assert rows["XAUUSD"] is None, "no ticket, no attribution"
    _run(go())


def test_the_backfill_is_idempotent():
    async def go():
        async with Ledger() as site:
            await _seed(site, MT5Account(login=11869564), [
                deal(ticket=1, type=0, profit=1.0, position_id=555, time=1744400000)])
            async with site.Session() as s:
                s.add(Trade(user_id="u1", strategy_id="ORB_v1", symbol="NDX100",
                            direction="BUY", entry_price=1.0, stop_loss=0.9,
                            volume=0.1, status="CLOSED", mt5_ticket=555))
                await s.commit()
            async with site.Session() as s:
                first = await history.backfill_trade_accounts(s, 11869564)
                await s.commit()
            async with site.Session() as s:
                second = await history.backfill_trade_accounts(s, 11869564)
                await s.commit()
            assert first["matched"] == 1
            assert second["matched"] == 0, "an attributed row is not reconsidered"
    _run(go())


def test_an_anchor_later_than_every_deal_says_so():
    """Zeros that mean "the window is wrong" must not read as "nothing happened".

    Caught on screen: the summary tiles showed the real figures beside a
    reconciliation of 0.00 throughout, and nothing said why.
    """
    async def go():
        async with Ledger() as site:
            await _seed(site, MT5Account(
                login=11869564,
                deal_class_overrides=json.dumps({"4": CAPITAL_IN})), FUNDEDNEXT_DEALS)
            async with site.Session() as s:
                s.add(OpeningAnchor(account_login=11869564,
                                    as_of=datetime(2030, 1, 1), balance=0.0,
                                    source="manual"))
                await s.commit()
            async with site.Session() as s:
                rep = await history.reconcile(s, 11869564, actual_balance=22993.35)

            assert rep["anchor_excludes_everything"] is True
            assert rep["deals_in_window"] == 3, "the deals exist; the window excludes them"
            assert rep["healthy"] is False
            assert any("later than every deal" in b for b in rep["blocked_by"])
    _run(go())


def test_a_sensible_anchor_does_not_trip_that_guard():
    async def go():
        async with Ledger() as site:
            await _seed(site, MT5Account(
                login=11869564,
                deal_class_overrides=json.dumps({"4": CAPITAL_IN})), FUNDEDNEXT_DEALS)
            await _anchor(site, 11869564)
            async with site.Session() as s:
                rep = await history.reconcile(s, 11869564, actual_balance=22993.35)
            assert rep["anchor_excludes_everything"] is False
            assert rep["healthy"] is True
    _run(go())


# ── how much of what I made is still here ──────────────────────────────────

def test_profit_remaining_attributes_withdrawals_to_profit_first():
    """`profit_remaining = realised profit - profit withdrawn`.

    Nothing in the MT5 ledger says whether a withdrawal was profit or returned
    capital, so the convention has to be chosen and stated. Profit-first is the
    conservative reading of "how much of what I made is still here" - it says
    "less than you think" when both readings are available, which is the right
    direction to err about your own money.
    """
    from backend.api.routes.cash import profit_split

    out = profit_split(500.0, -200.0)
    assert out["profit_withdrawn"] == 200.0
    assert out["profit_remaining"] == 300.0
    assert out["capital_withdrawn"] == 0.0


def test_withdrawing_more_than_the_profit_starts_eating_capital():
    from backend.api.routes.cash import profit_split

    out = profit_split(500.0, -900.0)
    assert out["profit_withdrawn"] == 500.0
    assert out["profit_remaining"] == 0.0
    assert out["capital_withdrawn"] == 400.0, "the rest came out of capital"


def test_a_losing_account_has_no_profit_to_withdraw():
    """`profit_remaining` is then the loss itself - negative - and nothing is
    attributed to profit, because you cannot withdraw a profit never made."""
    from backend.api.routes.cash import profit_split

    out = profit_split(-300.0, -100.0)
    assert out["profit_withdrawn"] == 0.0
    assert out["profit_remaining"] == -300.0
    assert out["capital_withdrawn"] == 100.0


def test_the_two_attributions_always_add_up_to_what_left():
    from backend.api.routes.cash import profit_split

    for profit in (-500.0, 0.0, 250.0, 10_000.0):
        for withdrawn in (0.0, -100.0, -5_000.0):
            out = profit_split(profit, withdrawn)
            assert out["profit_withdrawn"] + out["capital_withdrawn"] == \
                pytest.approx(abs(withdrawn)), (profit, withdrawn)


def test_the_summary_endpoint_reports_it():
    """A figure nobody can reach is a figure nobody reads."""
    from pathlib import Path

    src = Path("backend/api/routes/cash.py").read_text(encoding="utf-8")
    body = src.split("async def summary", 1)[1].split("@router.get", 1)[0]
    assert "profit_split(" in body
