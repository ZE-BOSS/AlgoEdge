"""
backend/investor/fund.py

The fund's terms, its daily price, and the money-in / money-out state machines.

Everything that changes an investor's holding goes through here, and everything
here ends up appending to the ledger in units.py. No route writes a
`unit_transactions` row directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import desc, func, select

from backend.investor import nav as navmod
from backend.investor import units as unitsmod
from backend.investor.models import (
    DEPOSIT_CONFIRMED,
    DEPOSIT_REJECTED,
    KIND_SUBSCRIBE,
    METHOD_OFF_PLATFORM,
    WITHDRAWAL_APPROVED,
    WITHDRAWAL_DECLINED,
    WITHDRAWAL_EXCEPTION,
    WITHDRAWAL_PAID,
    WITHDRAWAL_REQUESTED,
    Deposit,
    FundSettings,
    Investor,
    InvestorAuditLog,
    NavSnapshot,
    UnitTransaction,
    Withdrawal,
)
from backend.utils.logger import get_logger

logger = get_logger(__name__)

D = Decimal


class FundError(RuntimeError):
    """A refusal the caller must show the user. Never swallowed."""


# ── settings ─────────────────────────────────────────────────────────────────

DEFAULT_SETTINGS = {
    "performance_fee_pct": D("20"),
    "management_fee_pct": D("2"),
    "withdrawal_cap_pct": D("30"),
    "min_investment": D("200"),
    "lockup_days": 30,
    "notice_days": 7,
    "base_currency": "USD",
}


async def current_settings(session) -> FundSettings:
    """The live terms, creating version 1 from the agreed defaults if absent."""
    row = (await session.execute(
        select(FundSettings).order_by(desc(FundSettings.version)).limit(1)
    )).scalar_one_or_none()
    if row is not None:
        return row
    row = FundSettings(version=1, **DEFAULT_SETTINGS)
    session.add(row)
    await session.flush()
    logger.info("[FUND] created settings version 1 from defaults")
    return row


async def new_settings_version(session, *, actor_id: str, **changes) -> FundSettings:
    """Write a NEW settings version rather than editing the live one.

    A lock-up and a notice period are promises. Editing them in place would
    silently rewrite the terms of money already committed, so each commitment
    records the version it was made under and old versions stay readable.
    """
    live = await current_settings(session)
    fields = {c.name for c in FundSettings.__table__.columns}
    unknown = set(changes) - fields
    if unknown:
        raise FundError(f"not settings fields: {sorted(unknown)}")

    carried = {f: getattr(live, f) for f in fields
               if f not in ("id", "version", "effective_from", "created_at", "created_by")}
    row = FundSettings(**{**carried, **changes}, version=live.version + 1,
                       created_by=actor_id)
    session.add(row)
    await session.flush()
    await audit(session, actor_id=actor_id, action="settings.version",
                entity_type="fund_settings", entity_id=str(row.version),
                detail={k: str(v) for k, v in changes.items()})
    logger.info(f"[FUND] settings v{row.version} by {actor_id}: {changes}")
    return row


async def audit(session, *, actor_id: str | None, action: str,
                entity_type: str | None = None, entity_id: str | None = None,
                detail: dict | None = None, actor_kind: str = "admin",
                ip: str | None = None) -> None:
    session.add(InvestorAuditLog(
        actor_id=actor_id, actor_kind=actor_kind, action=action,
        entity_type=entity_type, entity_id=entity_id, detail=detail, ip=ip))


# ── the daily price ──────────────────────────────────────────────────────────

async def latest_nav(session, on: date | None = None) -> Decimal:
    """The most recent NAV on or before `on`, or the opening NAV if none.

    Deliberately "on or before" rather than "exactly on": a missed snapshot must
    not stop a deposit being priced, and the last known price is the honest
    figure to use. It is also why `NavSnapshot.as_of_date` is unique — two
    prices for one day would make this ambiguous.
    """
    q = select(NavSnapshot).order_by(desc(NavSnapshot.as_of_date)).limit(1)
    if on is not None:
        q = select(NavSnapshot).where(NavSnapshot.as_of_date <= on) \
            .order_by(desc(NavSnapshot.as_of_date)).limit(1)
    row = (await session.execute(q)).scalar_one_or_none()
    return navmod.nav(row.nav_per_unit) if row else navmod.INITIAL_NAV


async def take_snapshot(session, *, pool_equity, liabilities=0,
                        on: date | None = None, source: str = "MT5") -> NavSnapshot:
    """Price the fund for one accounting day.

    Re-running for the same day OVERWRITES that day's snapshot rather than
    adding a second. A NAV is a statement about a day, not an event log, and two
    prices for one day would make every lookup ambiguous — but note this is the
    one row in the module that is legitimately mutable, and the ledger rows that
    referenced the old value keep the price they actually executed at.
    """
    day = on or navmod.accounting_date()
    outstanding = await unitsmod.units_in_issue(session)
    price = navmod.nav_per_unit(pool_equity, liabilities, outstanding)

    row = (await session.execute(
        select(NavSnapshot).where(NavSnapshot.as_of_date == day)
    )).scalar_one_or_none()
    if row is None:
        row = NavSnapshot(as_of_date=day)
        session.add(row)
    row.pool_equity = navmod.money(pool_equity)
    row.liabilities = navmod.money(liabilities)
    row.units_in_issue = outstanding
    row.nav_per_unit = price
    row.source = source
    await session.flush()
    logger.info(f"[FUND] NAV {day}: {price} ({outstanding} units, "
                f"equity {navmod.money(pool_equity)})")
    return row


# ── money in ─────────────────────────────────────────────────────────────────

async def record_admin_deposit(session, *, investor_id: str, amount, actor_id: str,
                               on: date | None = None, method: str = METHOD_OFF_PLATFORM,
                               note: str | None = None) -> Deposit:
    """Admin records money that has already arrived, including historic ones.

    This is the Phase 1 onboarding path: the people already in the fund are
    entered this way, with the effective date they actually paid, so their units
    are issued at the NAV that applied then rather than today's.
    """
    settings = await current_settings(session)
    cash = navmod.money(amount)
    if cash < navmod.money(settings.min_investment):
        raise FundError(
            f"minimum investment is {navmod.money(settings.min_investment)}, got {cash}")

    day = on or navmod.accounting_date()
    deposit = Deposit(investor_id=investor_id, method=method, amount_claimed=cash,
                      amount_confirmed=cash, state=DEPOSIT_CONFIRMED,
                      effective_date=day, confirmed_by=actor_id,
                      confirmed_at=datetime.now(timezone.utc),
                      currency=settings.base_currency)
    session.add(deposit)
    await session.flush()

    await _issue_for_deposit(session, deposit, actor_id=actor_id, note=note)
    await audit(session, actor_id=actor_id, action="deposit.recorded",
                entity_type="deposit", entity_id=str(deposit.id),
                detail={"investor_id": investor_id, "amount": str(cash),
                        "effective_date": day.isoformat(), "method": method})
    return deposit


async def confirm_deposit(session, *, deposit_id: int, amount_confirmed, actor_id: str,
                          on: date | None = None) -> Deposit:
    """Admin confirms what actually landed, and units are issued for THAT figure.

    The investor's claimed amount is not evidence. Units are issued against the
    confirmed amount at the CONFIRMED date's NAV, because the money was not at
    risk until it arrived — issuing at the claim date would hand the investor
    profit the pool earned while the transfer was in flight.
    """
    deposit = await session.get(Deposit, deposit_id)
    if deposit is None:
        raise FundError(f"no deposit {deposit_id}")
    if deposit.state == DEPOSIT_CONFIRMED:
        raise FundError(f"deposit {deposit_id} is already confirmed")

    cash = navmod.money(amount_confirmed)
    if cash <= 0:
        raise FundError("a confirmed deposit must be positive")

    deposit.amount_confirmed = cash
    deposit.state = DEPOSIT_CONFIRMED
    deposit.effective_date = on or navmod.accounting_date()
    deposit.confirmed_by = actor_id
    deposit.confirmed_at = datetime.now(timezone.utc)
    await session.flush()

    await _issue_for_deposit(session, deposit, actor_id=actor_id)
    await audit(session, actor_id=actor_id, action="deposit.confirmed",
                entity_type="deposit", entity_id=str(deposit.id),
                detail={"claimed": str(deposit.amount_claimed), "confirmed": str(cash)})
    return deposit


async def reject_deposit(session, *, deposit_id: int, reason: str, actor_id: str) -> Deposit:
    deposit = await session.get(Deposit, deposit_id)
    if deposit is None:
        raise FundError(f"no deposit {deposit_id}")
    if deposit.state == DEPOSIT_CONFIRMED:
        raise FundError("a confirmed deposit cannot be rejected; write a correction")
    if not reason or not reason.strip():
        raise FundError("a rejection must say why")
    deposit.state = DEPOSIT_REJECTED
    deposit.rejected_reason = reason
    await audit(session, actor_id=actor_id, action="deposit.rejected",
                entity_type="deposit", entity_id=str(deposit.id),
                detail={"reason": reason})
    return deposit


async def _issue_for_deposit(session, deposit: Deposit, *, actor_id: str,
                             note: str | None = None) -> None:
    settings = await current_settings(session)
    price = await latest_nav(session, deposit.effective_date)
    await unitsmod.subscribe(
        session, investor_id=deposit.investor_id, amount=deposit.amount_confirmed,
        price=price, effective=deposit.effective_date,
        source_kind="deposit", source_id=deposit.id,
        terms_version=settings.version, created_by=actor_id, note=note)

    investor = await session.get(Investor, deposit.investor_id)
    if investor is not None:
        if investor.terms_version is None:
            investor.terms_version = settings.version
        if investor.status == "pending":
            investor.status = "active"
            investor.activated_at = datetime.now(timezone.utc)


# ── money out ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CapCheck:
    """Why a withdrawal is or is not within the standard limit."""
    cap: Decimal
    month_profit: Decimal
    cap_pct: Decimal
    within: bool

    @property
    def explanation(self) -> str:
        if self.month_profit <= 0:
            return ("no profit this month, so nothing is available under the standard "
                    "limit — this needs an exception request")
        return (f"{self.cap_pct:g}% of this month's profit of "
                f"${self.month_profit} is ${self.cap}")


async def month_profit(session, investor_id: str, on: date | None = None) -> Decimal:
    """What this investor's holding GAINED over the current WAT month.

    Measured on the units they held at the month's start, priced at the start
    and at now. Deliberately excludes deposits made during the month: money
    paid in this month is capital, not profit, and letting it inflate the
    withdrawable figure would turn the cap into a way to cycle cash straight
    back out.
    """
    day = on or navmod.accounting_date()
    first, _ = navmod.month_bounds(day)

    opening_units = (await session.execute(
        select(func.coalesce(func.sum(UnitTransaction.units), 0))
        .where(UnitTransaction.investor_id == investor_id,
               UnitTransaction.effective_date < first)
    )).scalar_one()
    opening_units = navmod.units(opening_units)
    if opening_units <= 0:
        return D("0.00")

    start_price = await latest_nav(session, first)
    now_price = await latest_nav(session, day)
    return navmod.money(opening_units * (navmod.nav(now_price) - navmod.nav(start_price)))


async def withdrawal_cap(session, investor_id: str, on: date | None = None) -> CapCheck:
    settings = await current_settings(session)
    pct = D(str(settings.withdrawal_cap_pct))
    profit = await month_profit(session, investor_id, on)
    cap = navmod.money(max(profit, D("0")) * pct / D("100"))
    return CapCheck(cap=cap, month_profit=profit, cap_pct=pct, within=cap > 0)


async def request_withdrawal(session, *, investor_id: str, amount,
                             destination: dict | None = None,
                             justification: str | None = None,
                             on: date | None = None) -> Withdrawal:
    """An investor asks for money.

    Over the cap this is NOT refused — it becomes an exception needing a written
    justification and a separate approval. Refusing outright would be the wrong
    behaviour and would push the conversation off-platform, where there is no
    record of it.
    """
    day = on or navmod.accounting_date()
    cash = navmod.money(amount)
    if cash <= 0:
        raise FundError("a withdrawal must be positive")

    price = await latest_nav(session, day)
    held_value = navmod.amount_for_units(
        await unitsmod.ledger_units(session, investor_id), price)
    if cash > held_value:
        raise FundError(f"holding is worth ${held_value}; cannot withdraw ${cash}")

    check = await withdrawal_cap(session, investor_id, day)
    over = cash > check.cap
    if over and not (justification or "").strip():
        raise FundError(
            f"${cash} is above the standard limit — {check.explanation}. "
            f"A request above it needs a written reason.")

    row = Withdrawal(
        investor_id=investor_id, amount_requested=cash,
        state=WITHDRAWAL_EXCEPTION if over else WITHDRAWAL_REQUESTED,
        is_exception=over, justification=justification, cap_at_request=check.cap,
        destination_bank_name=(destination or {}).get("bank_name"),
        destination_account_number=(destination or {}).get("account_number"),
        destination_account_name=(destination or {}).get("account_name"),
    )
    session.add(row)
    await session.flush()
    await audit(session, actor_id=investor_id, actor_kind="investor",
                action="withdrawal.requested", entity_type="withdrawal",
                entity_id=str(row.id),
                detail={"amount": str(cash), "cap": str(check.cap), "exception": over})
    return row


async def approve_withdrawal(session, *, withdrawal_id: int, actor_id: str,
                             on: date | None = None) -> Withdrawal:
    """Approve and cancel the units. The money is not yet marked paid."""
    row = await session.get(Withdrawal, withdrawal_id)
    if row is None:
        raise FundError(f"no withdrawal {withdrawal_id}")
    if row.state in (WITHDRAWAL_APPROVED, WITHDRAWAL_PAID):
        raise FundError(f"withdrawal {withdrawal_id} is already {row.state}")
    if row.state == WITHDRAWAL_DECLINED:
        raise FundError("a declined withdrawal cannot be approved; ask again")

    day = on or navmod.accounting_date()
    price = await latest_nav(session, day)
    try:
        await unitsmod.redeem(session, investor_id=row.investor_id,
                              amount=row.amount_requested, price=price, effective=day,
                              source_kind="withdrawal", source_id=row.id,
                              created_by=actor_id)
    except unitsmod.LedgerError as exc:
        # The NAV can fall between the request and the approval, so a request
        # that was affordable on Monday may not be on Friday. That is an ordinary
        # business outcome the admin must be shown, not a crash — without this it
        # surfaces as a 500 and the queue looks broken.
        raise FundError(
            f"cannot approve: the holding no longer covers "
            f"${navmod.money(row.amount_requested)} at today's NAV of {price}. {exc}"
        ) from exc
    row.state = WITHDRAWAL_APPROVED
    row.effective_date = day
    row.approved_by = actor_id
    row.approved_at = datetime.now(timezone.utc)
    await audit(session, actor_id=actor_id, action="withdrawal.approved",
                entity_type="withdrawal", entity_id=str(row.id),
                detail={"amount": str(row.amount_requested), "nav": str(price)})
    return row


async def mark_withdrawal_paid(session, *, withdrawal_id: int, actor_id: str,
                               reference: str | None = None) -> Withdrawal:
    row = await session.get(Withdrawal, withdrawal_id)
    if row is None:
        raise FundError(f"no withdrawal {withdrawal_id}")
    if row.state != WITHDRAWAL_APPROVED:
        raise FundError(f"only an approved withdrawal can be paid, not {row.state}")
    row.state = WITHDRAWAL_PAID
    row.amount_paid = row.amount_requested
    row.paid_at = datetime.now(timezone.utc)
    row.payment_reference = reference
    await audit(session, actor_id=actor_id, action="withdrawal.paid",
                entity_type="withdrawal", entity_id=str(row.id),
                detail={"reference": reference})
    return row


async def decline_withdrawal(session, *, withdrawal_id: int, reason: str,
                             actor_id: str) -> Withdrawal:
    row = await session.get(Withdrawal, withdrawal_id)
    if row is None:
        raise FundError(f"no withdrawal {withdrawal_id}")
    if row.state in (WITHDRAWAL_APPROVED, WITHDRAWAL_PAID):
        raise FundError("units are already cancelled; write a correction instead")
    if not reason or not reason.strip():
        raise FundError("a decline must say why")
    row.state = WITHDRAWAL_DECLINED
    row.declined_reason = reason
    await audit(session, actor_id=actor_id, action="withdrawal.declined",
                entity_type="withdrawal", entity_id=str(row.id),
                detail={"reason": reason})
    return row
