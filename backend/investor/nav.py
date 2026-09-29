"""
backend/investor/nav.py

What one unit of the fund is worth, and when a day ends.

    NAV per unit = (pool equity - liabilities) / units in issue

Every number here is `decimal.Decimal`. Not one of them is a float, and nothing
in this module accepts a float without quantising it immediately — see `money()`
and the note on `from_float`.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation

# The fund opens at 100.0000 per unit. The starting figure is arbitrary; what
# matters is that it never changes once a single unit has been issued, because
# every historic statement is denominated in it.
INITIAL_NAV = Decimal("100.00000000")

MONEY_Q = Decimal("0.01")          # 2dp, matches Numeric(18,2)
UNITS_Q = Decimal("0.00000001")    # 8dp, matches Numeric(24,8)
NAV_Q = Decimal("0.00000001")      # 8dp, matches Numeric(18,8)

# The accounting day is West African Time, matching the trading side
# (risk/circuit_breaker.py). A fund whose "today" disagrees with the bot's
# "today" produces a daily P&L that reconciles to nothing.
ACCOUNTING_UTC_OFFSET_HOURS = 1.0


def accounting_date(when: datetime | None = None,
                    offset_hours: float = ACCOUNTING_UTC_OFFSET_HOURS) -> date:
    """The WAT calendar date `when` falls on. Naive input is treated as UTC."""
    now = when or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (now + timedelta(hours=offset_hours)).date()


def month_bounds(on: date) -> tuple[date, date]:
    """First and last WAT day of `on`'s month — the window the withdrawal cap
    and the management fee are measured over."""
    first = on.replace(day=1)
    nxt = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first, nxt - timedelta(days=1)


def _dec(value) -> Decimal:
    """Anything -> Decimal, without going through binary floating point.

    `Decimal(0.1)` is 0.1000000000000000055511151231257827, so a float is routed
    through `str()` first. Callers should be handing Decimals in already; this
    exists so a stray float from JSON or an ORM row cannot poison the ledger.
    """
    if isinstance(value, Decimal):
        return value
    if value is None:
        return Decimal(0)
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"not a number: {value!r}") from exc


def money(value) -> Decimal:
    """Round to 2dp, half-up — the convention a bank statement uses."""
    return _dec(value).quantize(MONEY_Q, rounding=ROUND_HALF_UP)


def units(value) -> Decimal:
    """Round to 8dp, DOWN.

    Down, not half-up: rounding units up would issue a fraction of a unit that
    nobody paid for, diluting every existing holder by a hair on every single
    deposit. The residue stays in the pool, where it belongs.
    """
    return _dec(value).quantize(UNITS_Q, rounding=ROUND_DOWN)


def nav(value) -> Decimal:
    return _dec(value).quantize(NAV_Q, rounding=ROUND_HALF_UP)


def text(value) -> str | None:
    """A Decimal as the plain string an API returns — never scientific notation.

    `str()` is not safe here: a zero quantized to 8dp is `Decimal('0E-8')`, and
    str() prints exactly that. Every fully-redeemed investor's balance would
    reach the screen as "0E-8". format(..., "f") always writes positional digits.
    """
    if value is None:
        return None
    return format(_dec(value), "f")


def nav_per_unit(pool_equity, liabilities, units_in_issue) -> Decimal:
    """What one unit is worth right now.

    With no units in issue the fund has no price, so it opens at INITIAL_NAV —
    that is the only sane answer and it keeps the first deposit's arithmetic
    trivial.

    A pool worth zero or less with units still outstanding is NOT rounded up to
    something comfortable: the units really are worthless, and quietly flooring
    the NAV at a positive number would let redemptions pay out money the fund
    does not have.
    """
    outstanding = units(units_in_issue)
    if outstanding <= 0:
        return INITIAL_NAV
    net = money(pool_equity) - money(liabilities)
    if net <= 0:
        return Decimal("0E-8")
    return nav(net / outstanding)


def units_for_amount(amount, nav_per_unit_value) -> Decimal:
    """How many units `amount` buys at this NAV."""
    price = nav(nav_per_unit_value)
    if price <= 0:
        raise ValueError("cannot issue units at a NAV of zero or less")
    return units(money(amount) / price)


def units_to_cancel(amount, nav_per_unit_value) -> Decimal:
    """How many units must be cancelled to pay `amount` out — rounded UP.

    The mirror of units_for_amount rounding DOWN on the way in. Either way the
    sub-unit residue stays with the pool: rounding a cancellation down would pay
    the leaver cash worth more than the units they give up, a hair at the
    expense of everyone who stays, on every withdrawal and every fee.
    """
    from decimal import ROUND_CEILING
    price = nav(nav_per_unit_value)
    if price <= 0:
        raise ValueError("cannot cancel units at a NAV of zero or less")
    return (money(amount) / price).quantize(UNITS_Q, rounding=ROUND_CEILING)


def amount_for_units(unit_count, nav_per_unit_value) -> Decimal:
    """What `unit_count` units are worth at this NAV."""
    return money(units(unit_count) * nav(nav_per_unit_value))
