"""
backend/investor/fund.py

The fund's terms, its daily price, and the money-in / money-out state machines.

Everything that changes an investor's holding goes through here, and everything
here ends up appending to the ledger in units.py. No route writes a
`unit_transactions` row directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import desc, func, select

from backend.investor import nav as navmod
from backend.investor import units as unitsmod
from backend.investor.models import (
    DEPOSIT_CLAIMED,
    DEPOSIT_CONFIRMED,
    DEPOSIT_REJECTED,
    DEPOSIT_REQUESTED,
    KIND_SUBSCRIBE,
    METHOD_OFF_PLATFORM,
    WITHDRAWAL_APPROVED,
    WITHDRAWAL_DECLINED,
    WITHDRAWAL_EXCEPTION,
    WITHDRAWAL_PAID,
    WITHDRAWAL_REQUESTED,
    Adjustment,
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


async def owed_on(session, day: date) -> Decimal:
    """Money still in the broker account that no longer belongs to investors.

    Two kinds: withdrawals approved but not yet paid (their units are already
    cancelled), and fees charged but not yet taken out (likewise). Both must be
    subtracted before pricing a unit — otherwise the money is shared out among
    everyone who is left, and one investor's pending withdrawal shows up as
    everybody else's profit until the transfer goes out.

    As of `day`, so a back-dated snapshot counts what was owed on that day: an
    item approved/charged on or before it, and not yet paid by the end of it.
    """
    from backend.investor.models import FeeAccrual  # local: the table is Phase 4's

    total = D("0")
    rows = (await session.execute(
        select(Withdrawal.amount_requested, Withdrawal.state, Withdrawal.paid_at)
        .where(Withdrawal.effective_date <= day,
               Withdrawal.state.in_((WITHDRAWAL_APPROVED, WITHDRAWAL_PAID))))).all()
    for amount, state, paid_at in rows:
        if state == WITHDRAWAL_APPROVED or (paid_at and navmod.accounting_date(paid_at) > day):
            total += D(str(amount))
    fees = (await session.execute(
        select(FeeAccrual.fee_amount, FeeAccrual.state, FeeAccrual.paid_at)
        .where(FeeAccrual.period_end <= day, FeeAccrual.state.in_(("charged", "paid"))))).all()
    for amount, state, paid_at in fees:
        if state == "charged" or (paid_at and navmod.accounting_date(paid_at) > day):
            total += D(str(amount))
    return navmod.money(total)


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
    # `liabilities` is anything ELSE owed; what the ledger already knows is owed
    # is added here so it cannot be forgotten.
    liabilities = navmod.money(liabilities) + await owed_on(session, day)
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
    await _open_investor(session, investor_id)
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
        # normalize() so a percentage read back from a NUMERIC(9,4) column
        # reads "30%", not "30.0000%"; the :f stops 30 becoming "3E+1".
        return (f"{self.cap_pct.normalize():f}% of this month's profit of "
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


async def terms_for(session, investor_id: str) -> FundSettings:
    """The terms this investor committed under — not today's.

    A lock-up and a notice period are promises made when the money went in, so
    a later settings version must not lengthen them for money already committed.
    Falls back to the live terms for an investor with no commitment yet.
    """
    investor = await session.get(Investor, investor_id)
    if investor is not None and investor.terms_version is not None:
        row = (await session.execute(
            select(FundSettings).where(FundSettings.version == investor.terms_version)
        )).scalar_one_or_none()
        if row is not None:
            return row
    return await current_settings(session)


async def lockup_until(session, investor_id: str) -> date | None:
    """First day money may leave under the standard path, or None if never funded.

    Counted from the investor's FIRST subscription, under the terms of that
    commitment. A top-up does not restart the clock: the agreed term is "one
    month's lock-up", and restarting it on every top-up would quietly turn a
    one-month promise into an indefinite one for anyone who adds money.

    Inside the lock-up a withdrawal is not refused outright; like a request
    over the cap, it becomes an exception needing a written reason.
    """
    first = (await session.execute(
        select(func.min(UnitTransaction.effective_date))
        .where(UnitTransaction.investor_id == investor_id,
               UnitTransaction.kind == KIND_SUBSCRIBE)
    )).scalar_one()
    if first is None:
        return None
    terms = await terms_for(session, investor_id)
    return first + timedelta(days=int(terms.lockup_days or 0))


async def withdrawal_cap(session, investor_id: str, on: date | None = None) -> CapCheck:
    settings = await current_settings(session)
    pct = D(str(settings.withdrawal_cap_pct))
    profit = await month_profit(session, investor_id, on)
    cap = navmod.money(max(profit, D("0")) * pct / D("100"))
    return CapCheck(cap=cap, month_profit=profit, cap_pct=pct, within=cap > 0)


PAYOUT_COOLING_HOURS = 48


async def payout_changed_recently(session, investor_id: str,
                                  hours: int = PAYOUT_COOLING_HOURS) -> bool:
    """Was the account we pay this investor changed in the last `hours`?

    Changing the payout account and then withdrawing is the classic takeover:
    whoever controls the change controls the money. For a cooling-off period
    after a change, every withdrawal goes to review, however small.
    """
    from datetime import timedelta as _td
    since = datetime.now(timezone.utc).replace(tzinfo=None) - _td(hours=hours)
    return (await session.execute(
        select(func.count(Adjustment.id)).where(
            Adjustment.entity_type == "investor", Adjustment.entity_id == investor_id,
            Adjustment.field.in_(PAYOUT_FIELDS), Adjustment.created_at >= since,
            # entering details for the first time redirects nothing
            Adjustment.old_value.is_not(None), Adjustment.old_value != ""))).scalar_one() > 0


async def request_withdrawal(session, *, investor_id: str, amount,
                             destination: dict | None = None,
                             justification: str | None = None,
                             on: date | None = None,
                             force_review: str | None = None) -> Withdrawal:
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
    until = await lockup_until(session, investor_id)
    locked = until is not None and day < until
    over = cash > check.cap or locked or bool(force_review)
    if force_review:
        # a security review, not the investor's choice: it needs no reason from them
        justification = f"{force_review}" + (f" — investor: {justification}" if justification else "")
    if over and not (justification or "").strip():
        if locked:
            raise FundError(
                f"your money is in its lock-up period until {until.isoformat()}. "
                f"A request before then needs a written reason.")
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


def paid_moment(on: date | None) -> datetime:
    """When money left, from the day it left. A back-dated payment is stamped at
    noon WAT of that day so owed_on() treats it as gone by the end of it."""
    if on is None:
        return datetime.now(timezone.utc)
    return datetime(on.year, on.month, on.day, 11, 0, tzinfo=timezone.utc)   # 12:00 WAT


async def mark_withdrawal_paid(session, *, withdrawal_id: int, actor_id: str,
                               reference: str | None = None,
                               on: date | None = None) -> Withdrawal:
    row = await session.get(Withdrawal, withdrawal_id)
    if row is None:
        raise FundError(f"no withdrawal {withdrawal_id}")
    if row.state != WITHDRAWAL_APPROVED:
        raise FundError(f"only an approved withdrawal can be paid, not {row.state}")
    row.state = WITHDRAWAL_PAID
    row.amount_paid = row.amount_requested
    row.paid_at = paid_moment(on)
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


# ── the investor record ──────────────────────────────────────────────────────

PROFILE_FIELDS = ("name", "phone", "country", "kyc_status")
PAYOUT_FIELDS = ("payout_bank_name", "payout_account_number", "payout_account_name")


async def update_investor(session, *, investor_id: str, actor_id: str,
                          changes: dict, reason: str | None = None) -> Investor:
    """Change an investor's details.

    Payout details are where a withdrawal goes, which makes changing them the
    single most-abused flow on any investment platform. So a payout change needs
    a reason and writes an `adjustments` row per field with the old value — the
    record that answers "who pointed my money at that account?".
    """
    investor = await _open_investor(session, investor_id)
    unknown = set(changes) - set(PROFILE_FIELDS) - set(PAYOUT_FIELDS)
    if unknown:
        raise FundError(f"not editable: {sorted(unknown)}")

    changed = {f: v for f, v in changes.items() if getattr(investor, f) != v}
    payout = [f for f in changed if f in PAYOUT_FIELDS]
    if payout and not (reason or "").strip():
        raise FundError("changing where withdrawals are paid needs a reason")
    if "name" in changed and not (changed["name"] or "").strip():
        raise FundError("an investor must have a name")

    for f in payout:
        session.add(Adjustment(entity_type="investor", entity_id=investor_id, field=f,
                               old_value=getattr(investor, f), new_value=changed[f],
                               reason=reason, actor_id=actor_id))
    old = {f: getattr(investor, f) for f in changed}
    for f, v in changed.items():
        setattr(investor, f, v)
    if changed:
        await audit(session, actor_id=actor_id, action="investor.updated",
                    entity_type="investor", entity_id=investor_id,
                    detail={"fields": sorted(changed), "reason": reason,
                            # payout values are kept in `adjustments`, not here:
                            # the audit log is broader-read than the adjustment trail
                            "old": {f: v for f, v in old.items() if f not in PAYOUT_FIELDS}})
    return investor


async def _open_investor(session, investor_id: str) -> Investor:
    investor = await session.get(Investor, investor_id)
    if investor is None:
        raise FundError(f"no investor {investor_id}")
    if investor.status == "closed":
        raise FundError("this investor's account is closed")
    return investor


# ── closure (full exit) ──────────────────────────────────────────────────────

OPEN_DEPOSIT_STATES = (DEPOSIT_REQUESTED, DEPOSIT_CLAIMED)
OPEN_WITHDRAWAL_STATES = (WITHDRAWAL_REQUESTED, WITHDRAWAL_EXCEPTION, WITHDRAWAL_APPROVED)


async def request_closure(session, *, investor_id: str, actor_id: str,
                          actor_kind: str = "admin", reason: str | None = None) -> Investor:
    investor = await _open_investor(session, investor_id)
    if investor.status == "closing":
        raise FundError("closure is already requested")
    investor.status = "closing"
    await audit(session, actor_id=actor_id, actor_kind=actor_kind,
                action="closure.requested", entity_type="investor",
                entity_id=investor_id, detail={"reason": reason})
    return investor


async def cancel_closure(session, *, investor_id: str, actor_id: str) -> Investor:
    investor = await _open_investor(session, investor_id)
    if investor.status != "closing":
        raise FundError("no closure is pending")
    held = await unitsmod.ledger_units(session, investor_id)
    investor.status = "active" if held > 0 else "pending"
    await audit(session, actor_id=actor_id, action="closure.cancelled",
                entity_type="investor", entity_id=investor_id)
    return investor


async def closure_blockers(session, investor_id: str) -> list[str]:
    """Anything that must be settled before an account can be closed.

    An open deposit would arrive after the account is gone; an open withdrawal
    would either pay twice (once itself, once in the closing payout) or cancel
    units that the closure has already cancelled.
    """
    out = []
    deposits = (await session.execute(
        select(func.count(Deposit.id)).where(Deposit.investor_id == investor_id,
                                             Deposit.state.in_(OPEN_DEPOSIT_STATES))
    )).scalar_one()
    if deposits:
        out.append(f"{deposits} deposit(s) still open — confirm or reject them first")
    withdrawals = (await session.execute(
        select(Withdrawal.state, func.count(Withdrawal.id))
        .where(Withdrawal.investor_id == investor_id,
               Withdrawal.state.in_(OPEN_WITHDRAWAL_STATES))
        .group_by(Withdrawal.state)
    )).all()
    for state, n in withdrawals:
        if state == WITHDRAWAL_APPROVED:
            out.append(f"{n} approved withdrawal(s) not yet marked paid")
        else:
            out.append(f"{n} withdrawal request(s) still {state} — decide them first")

    # Last month's fees must be charged before the account empties, or they are
    # never charged at all: a month close skips holders of zero units.
    from backend.investor import fees as feesmod
    start, end = feesmod.previous_month()
    held_then = await feesmod._units_at(session, investor_id, end)
    if held_then > 0 and not await feesmod.already_closed(session, start, end):
        out.append(f"fees for {start:%B %Y} have not been charged yet — close that month first")
    return out


async def _closure_fees(session, investor_id: str, day: date):
    """Fees for the part of this month before the account closes."""
    from backend.investor import fees as feesmod
    start = day.replace(day=1)
    if await feesmod.already_closed(session, start, day):
        return None, start
    return await feesmod.compute(session, investor_id, start, day), start


async def closure_quote(session, investor_id: str, on: date | None = None) -> dict:
    """What the investor is owed if the account closed today."""
    day = on or navmod.accounting_date()
    price = await latest_nav(session, day)
    held = await unitsmod.ledger_units(session, investor_id)
    value = navmod.amount_for_units(held, price)
    line, _ = await _closure_fees(session, investor_id, day)
    owed = line.total if line else D("0.00")
    return {
        "investor_id": investor_id,
        "as_of": day.isoformat(),
        "units": navmod.text(held),
        "nav_per_unit": navmod.text(price),
        "gross_value": navmod.text(value),
        # this month's management and performance fees up to today, charged
        # at approval
        "fees_owed": navmod.text(owed),
        "fee_detail": line.as_dict() if line else None,
        "net_payable": navmod.text(navmod.money(value - owed)),
        "blockers": await closure_blockers(session, investor_id),
    }


def _anonymised_email(investor_id: str) -> str:
    # `.invalid` is reserved (RFC 2606): it can never route to a real inbox.
    return f"closed-{investor_id}@anonymised.invalid"


async def approve_closure(session, *, investor_id: str, actor_id: str,
                          payment_reference: str, on: date | None = None) -> dict:
    """Pay out everything, cancel every unit, then erase the person.

    The order is what was specified: the admin pays, then approves, and approval
    is what closes the account. `payment_reference` is required because this is
    the step that says the money has left.

    The ledger and audit rows SURVIVE, keyed by the same investor id, because a
    fund must be able to reconstruct its own history. What goes is everything
    that identifies the person: name, email, phone, country, bank details,
    credentials, and the bank details on their past withdrawals.
    """
    investor = await _open_investor(session, investor_id)
    if investor.status != "closing":
        raise FundError("closure has not been requested for this investor")
    if not (payment_reference or "").strip():
        raise FundError("record the payment reference of the closing payout")
    blockers = await closure_blockers(session, investor_id)
    if blockers:
        raise FundError("cannot close yet: " + "; ".join(blockers))

    day = on or navmod.accounting_date()
    price = await latest_nav(session, day)
    line, fee_start = await _closure_fees(session, investor_id, day)
    if line is not None and line.total > 0:
        from backend.investor import fees as feesmod
        await feesmod.charge(session, line, period_start=fee_start, period_end=day,
                             actor_id=actor_id)
    held = await unitsmod.ledger_units(session, investor_id)
    paid = D("0.00")
    if held > 0:
        # Recorded as a withdrawal so the payout appears in the investor's
        # statement and in every "money out" total like any other.
        payout = Withdrawal(
            investor_id=investor_id,
            amount_requested=navmod.amount_for_units(held, price),
            state=WITHDRAWAL_PAID, is_exception=False,
            justification="account closure", effective_date=day,
            approved_by=actor_id, approved_at=datetime.now(timezone.utc),
            paid_at=datetime.now(timezone.utc), payment_reference=payment_reference)
        session.add(payout)
        await session.flush()
        move = await unitsmod.redeem_all(session, investor_id=investor_id, price=price,
                                         effective=day, created_by=actor_id,
                                         note=f"account closure, withdrawal {payout.id}")
        payout.amount_requested = move.amount
        payout.amount_paid = move.amount
        paid = move.amount

    # erase the person, keep the history
    investor.name = "Closed investor"
    investor.email = _anonymised_email(investor_id)
    investor.phone = None
    investor.country = None
    investor.password_hash = None
    for f in PAYOUT_FIELDS:
        setattr(investor, f, None)
    past = (await session.execute(
        select(Withdrawal).where(Withdrawal.investor_id == investor_id))).scalars().all()
    for w in past:
        w.destination_bank_name = None
        w.destination_account_number = None
        w.destination_account_name = None
    proofs = (await session.execute(
        select(Deposit).where(Deposit.investor_id == investor_id))).scalars().all()
    for d in proofs:
        d.proof_path = None
    # the phones the app was installed on identify the person too
    from sqlalchemy import delete as _delete
    from backend.investor.models import InvestorDevice
    await session.execute(_delete(InvestorDevice).where(InvestorDevice.investor_id == investor_id))
    investor.status = "closed"
    investor.closed_at = datetime.now(timezone.utc)

    await audit(session, actor_id=actor_id, action="closure.approved",
                entity_type="investor", entity_id=investor_id,
                detail={"units": navmod.text(held), "nav": navmod.text(price), "paid": navmod.text(paid),
                        "reference": payment_reference})
    logger.info(f"[FUND] closed {investor_id}: {held} units, ${paid} paid ({payment_reference})")
    return {"investor_id": investor_id, "units_redeemed": navmod.text(held),
            "nav_per_unit": navmod.text(price), "amount_paid": navmod.text(paid),
            "payment_reference": payment_reference}


# ── money in, from the investor's side ───────────────────────────────────────

MAX_OPEN_CLAIMS = 3


def reference_code(investor_id: str) -> str:
    """The code an investor puts on a bank transfer so it can be matched.

    Stable per investor rather than per deposit: people save a payee once and
    reuse it, and a code that changed every time would be left off half the
    transfers. Derived from the id, so it needs no column and cannot collide
    with another investor's.
    """
    return "AVQ-" + investor_id.replace("-", "")[:8].upper()


async def claim_deposit(session, *, investor_id: str, amount, note: str | None = None,
                        ip: str | None = None) -> Deposit:
    """The investor says "I have sent it". Nothing is credited.

    This only puts the claim in the admin's queue. Units are issued by
    confirm_deposit, for the amount that actually arrived, on the day it
    arrived. The claim is a prompt to go and look, never evidence.
    """
    investor = await _open_investor(session, investor_id)
    if investor.status == "closing":
        raise FundError("your account is being closed, so it cannot take new money")
    settings = await current_settings(session)
    cash = navmod.money(amount)
    if cash < navmod.money(settings.min_investment):
        raise FundError(f"the minimum is ${navmod.money(settings.min_investment)}")
    open_claims = (await session.execute(
        select(func.count(Deposit.id)).where(Deposit.investor_id == investor_id,
                                             Deposit.state.in_(OPEN_DEPOSIT_STATES))
    )).scalar_one()
    if open_claims >= MAX_OPEN_CLAIMS:
        raise FundError(f"you already have {open_claims} transfers waiting to be confirmed; "
                        f"we will confirm those first")
    row = Deposit(investor_id=investor_id, method="BANK_TRANSFER", amount_claimed=cash,
                  currency=settings.base_currency, state=DEPOSIT_CLAIMED,
                  reference_code=reference_code(investor_id))
    session.add(row)
    await session.flush()
    await audit(session, actor_id=investor_id, actor_kind="investor", action="deposit.claimed",
                entity_type="deposit", entity_id=str(row.id),
                detail={"amount": navmod.text(cash), "note": note}, ip=ip)
    return row
