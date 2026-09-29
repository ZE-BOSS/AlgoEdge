"""
backend/investor/units.py

Issuing and cancelling units, and proving the cache still matches the ledger.

Every write here appends to `unit_transactions` and then refreshes
`investor_units` from it. Nothing computes a balance by adding to the cached
figure — the cache is always recomputed from the rows, so a lost or duplicated
write shows up as a mismatch instead of silently becoming the new truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select

from backend.investor import nav as navmod
from backend.investor.models import (
    KIND_CORRECTION,
    KIND_FEE,
    KIND_REDEEM,
    KIND_SUBSCRIBE,
    InvestorUnits,
    UnitTransaction,
)
from backend.utils.logger import get_logger

logger = get_logger(__name__)


class LedgerError(RuntimeError):
    """A movement that must not be written. Always surfaced, never swallowed."""


@dataclass(frozen=True)
class Movement:
    """What a subscribe/redeem actually did, for the caller to report back."""
    investor_id: str
    kind: str
    units: Decimal
    nav_per_unit: Decimal
    amount: Decimal
    effective_date: date


# ── reading ──────────────────────────────────────────────────────────────────

async def ledger_units(session, investor_id: str) -> Decimal:
    """An investor's holding, summed FROM THE LEDGER. The real answer."""
    total = (await session.execute(
        select(func.coalesce(func.sum(UnitTransaction.units), 0))
        .where(UnitTransaction.investor_id == investor_id)
    )).scalar_one()
    return navmod.units(total)


async def units_in_issue(session) -> Decimal:
    """Every unit the fund has outstanding, from the ledger."""
    total = (await session.execute(
        select(func.coalesce(func.sum(UnitTransaction.units), 0))
    )).scalar_one()
    return navmod.units(total)


async def refresh_cache(session, investor_id: str) -> Decimal:
    """Recompute `investor_units` for one investor from the ledger."""
    held = await ledger_units(session, investor_id)
    row = await session.get(InvestorUnits, investor_id)
    if row is None:
        session.add(InvestorUnits(investor_id=investor_id, units=held))
    else:
        row.units = held
        row.rebuilt_at = func.now()
    return held


async def assert_cache_matches_ledger(session) -> list[tuple[str, Decimal, Decimal]]:
    """Every investor whose cached units disagree with the ledger.

    Run nightly. An empty list is the only acceptable result: a mismatch means
    a write went missing or was applied twice, and the difference between
    finding that tonight and finding it when somebody withdraws is the whole
    reason the cache is treated as disposable.
    """
    cached = {r.investor_id: navmod.units(r.units)
              for r in (await session.execute(select(InvestorUnits))).scalars()}
    rows = (await session.execute(
        select(UnitTransaction.investor_id,
               func.coalesce(func.sum(UnitTransaction.units), 0))
        .group_by(UnitTransaction.investor_id)
    )).all()
    truth = {inv: navmod.units(total) for inv, total in rows}

    drift = []
    for investor_id in set(cached) | set(truth):
        want = truth.get(investor_id, Decimal(0))
        got = cached.get(investor_id, Decimal(0))
        if want != got:
            drift.append((investor_id, want, got))
            logger.error(
                f"[LEDGER] {investor_id}: cache says {got} units, ledger says {want}"
            )
    return drift


# ── writing ──────────────────────────────────────────────────────────────────

async def _append(session, *, investor_id: str, kind: str, unit_delta: Decimal,
                  price: Decimal, amount: Decimal, effective: date,
                  source_kind: str | None, source_id: int | None,
                  terms_version: int | None, created_by: str | None,
                  note: str | None) -> UnitTransaction:
    tx = UnitTransaction(
        investor_id=investor_id, kind=kind, units=unit_delta, nav_per_unit=price,
        amount=amount, effective_date=effective, source_kind=source_kind,
        source_id=source_id, terms_version=terms_version, created_by=created_by,
        note=note,
    )
    session.add(tx)
    await session.flush()          # surface the source uniqueness clash here
    await refresh_cache(session, investor_id)
    return tx


async def subscribe(session, *, investor_id: str, amount, price, effective: date,
                    source_kind: str | None = None, source_id: int | None = None,
                    terms_version: int | None = None, created_by: str | None = None,
                    note: str | None = None) -> Movement:
    """Issue units for money that has ARRIVED.

    `price` is the NAV on `effective` — the day the money was confirmed, not the
    day it was claimed or requested. Issuing at an earlier, lower NAV would hand
    the new investor profit the pool earned before their money was in it, which
    is precisely the failure unitisation exists to prevent.
    """
    cash = navmod.money(amount)
    if cash <= 0:
        raise LedgerError(f"a subscription must be positive, got {cash}")
    issued = navmod.units_for_amount(cash, price)
    if issued <= 0:
        raise LedgerError(
            f"{cash} at a NAV of {price} rounds to zero units — refusing to take "
            f"money and issue nothing")
    await _append(session, investor_id=investor_id, kind=KIND_SUBSCRIBE,
                  unit_delta=issued, price=navmod.nav(price), amount=cash,
                  effective=effective, source_kind=source_kind, source_id=source_id,
                  terms_version=terms_version, created_by=created_by, note=note)
    logger.info(f"[LEDGER] {investor_id} +{issued} units at {navmod.nav(price)} (${cash})")
    return Movement(investor_id, KIND_SUBSCRIBE, issued, navmod.nav(price), cash, effective)


async def redeem(session, *, investor_id: str, amount, price, effective: date,
                 kind: str = KIND_REDEEM, source_kind: str | None = None,
                 source_id: int | None = None, terms_version: int | None = None,
                 created_by: str | None = None, note: str | None = None) -> Movement:
    """Cancel units to pay money OUT.

    Refuses to take an investor negative. That check reads the LEDGER, not the
    cache — a stale cache must never be able to authorise a payout, which is the
    one place the distinction actually costs money.
    """
    cash = navmod.money(amount)
    if cash <= 0:
        raise LedgerError(f"a redemption must be positive, got {cash}")

    wanted = navmod.units_for_amount(cash, price)
    held = await ledger_units(session, investor_id)
    if wanted > held:
        raise LedgerError(
            f"{investor_id} holds {held} units ({navmod.amount_for_units(held, price)}) "
            f"and cannot redeem {wanted} ({cash})")

    await _append(session, investor_id=investor_id, kind=kind, unit_delta=-wanted,
                  price=navmod.nav(price), amount=cash, effective=effective,
                  source_kind=source_kind, source_id=source_id,
                  terms_version=terms_version, created_by=created_by, note=note)
    logger.info(f"[LEDGER] {investor_id} -{wanted} units at {navmod.nav(price)} (${cash})")
    return Movement(investor_id, kind, -wanted, navmod.nav(price), cash, effective)


async def redeem_all(session, *, investor_id: str, price, effective: date,
                     created_by: str | None = None, note: str | None = None) -> Movement:
    """Cancel every unit an investor holds — the full-exit path.

    Works in UNITS rather than money so the holding lands at exactly zero.
    Converting to cash and back would leave a dust balance of a few units behind
    on almost every exit, and an investor who has been paid out in full should
    not still appear in the register.
    """
    held = await ledger_units(session, investor_id)
    if held <= 0:
        raise LedgerError(f"{investor_id} holds no units")
    cash = navmod.amount_for_units(held, price)
    await _append(session, investor_id=investor_id, kind=KIND_REDEEM,
                  unit_delta=-held, price=navmod.nav(price), amount=cash,
                  effective=effective, source_kind="closure", source_id=None,
                  terms_version=None, created_by=created_by,
                  note=note or "full redemption")
    logger.info(f"[LEDGER] {investor_id} fully redeemed: -{held} units (${cash})")
    return Movement(investor_id, KIND_REDEEM, -held, navmod.nav(price), cash, effective)


async def charge_fee(session, *, investor_id: str, amount, price, effective: date,
                     source_id: int | None = None,
                     created_by: str | None = None, note: str | None = None) -> Movement:
    """Cancel units to settle a fee. Same mechanics as a redemption, different
    `kind` so fees are separable from investor-initiated withdrawals in every
    report."""
    return await redeem(session, investor_id=investor_id, amount=amount, price=price,
                        effective=effective, kind=KIND_FEE,
                        source_kind="fee_accrual", source_id=source_id,
                        created_by=created_by, note=note)


async def correct(session, *, investor_id: str, unit_delta, price, effective: date,
                  reason: str, created_by: str) -> Movement:
    """Write a COMPENSATING row. History is never edited.

    `reason` is required and `created_by` is required. A correction with no
    stated cause is indistinguishable from an error six months later, and this
    is the one entry point that can move units without money moving.
    """
    if not reason or not str(reason).strip():
        raise LedgerError("a correction must say why")
    delta = navmod.units(unit_delta)
    if delta == 0:
        raise LedgerError("a correction of zero units is not a correction")

    held = await ledger_units(session, investor_id)
    if held + delta < 0:
        raise LedgerError(
            f"correction would take {investor_id} to {held + delta} units")

    await _append(session, investor_id=investor_id, kind=KIND_CORRECTION,
                  unit_delta=delta, price=navmod.nav(price),
                  amount=navmod.amount_for_units(abs(delta), price),
                  effective=effective, source_kind="correction",
                  # NULL source_id: NULL does not participate in the
                  # (source_kind, source_id) UNIQUE constraint, so many
                  # corrections coexist without colliding.
                  source_id=None,
                  terms_version=None, created_by=created_by, note=reason)
    logger.warning(f"[LEDGER] CORRECTION {investor_id} {delta:+} units by {created_by}: {reason}")
    return Movement(investor_id, KIND_CORRECTION, delta, navmod.nav(price),
                    navmod.amount_for_units(abs(delta), price), effective)
