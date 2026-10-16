"""
backend/investor/referrals.py

Who introduced whom, what that earns, and who is allowed to see it.

THE SHAPE
---------
An investor may bring in other investors. `Investor.referred_by` points at the
one who introduced them, so the tree is held as a parent link rather than a
materialised path - a link is one write to move, and admins DO move people
(the commonest reason this feature is used at all is someone who applied
without the link).

TWO LEVELS, AND NO MORE
-----------------------
Level 1 is the investor who introduced them. Level 2 is whoever introduced
THAT investor. Nothing earns above level 2. That is a product decision, not a
limitation of the walk: earnings that flow up an unbounded chain are the shape
regulators treat as a red flag, and they are also the shape nobody can audit.

WHO PAYS - THE PART THAT IS A BUSINESS DECISION
-----------------------------------------------
`FundSettings.referral_model`:

  HOUSE_FEE        the introducer is paid out of the management and
                   performance fees WE collect from their referral. The
                   referred investor pays exactly what everyone else pays.
  INVESTOR_PROFIT  the referred investor's own profit is split, so they net
                   less than an investor nobody introduced. This has to be
                   disclosed at application time, because it makes a person's
                   terms depend on who brought them.
  OFF              no earnings, without unpicking the tree.

Versioned with the rest of the terms: somebody was introduced under one
arrangement and must not be moved onto another retroactively.

VISIBILITY
----------
An investor may see their own figures and those of people BELOW them - that is
what they are paid on. They may never see anything about the investor above
them except the rate they are paying. `visible_tree` is the only function that
decides this, so there is one place to check rather than a rule repeated in
every route.
"""

from __future__ import annotations

import secrets
import string
from datetime import datetime, timezone
from decimal import Decimal as D
from typing import Any

from backend.utils.logger import get_logger

logger = get_logger(__name__)

MODEL_HOUSE_FEE = "HOUSE_FEE"
MODEL_INVESTOR_PROFIT = "INVESTOR_PROFIT"
MODEL_OFF = "OFF"
MODELS = (MODEL_HOUSE_FEE, MODEL_INVESTOR_PROFIT, MODEL_OFF)

MAX_LEVELS = 2

# No I, O, 0 or 1: these codes get read off a screen and typed by hand, and
# every one of those pairs is a support ticket.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LEN = 8


# ── the code ────────────────────────────────────────────────────────────────


def new_code(n: int = CODE_LEN) -> str:
    """A fresh affiliate code. Random, not derived from the name.

    A code built from somebody's name leaks who they are to anyone who reads
    the link, and collides as soon as two investors share a surname.
    """
    return "".join(secrets.choice(_ALPHABET) for _ in range(n))


async def ensure_code(session, investor) -> str:
    """This investor's code, issued on first use and never changed after.

    Never regenerated: the code ends up in other people's browsers, in chat
    messages and sometimes in print, and rotating it silently breaks every
    link already handed out.
    """
    from sqlalchemy import select

    from backend.investor.models import Investor

    if investor.referral_code:
        return investor.referral_code
    for _ in range(12):
        code = new_code()
        clash = (await session.execute(
            select(Investor.id).where(Investor.referral_code == code))).scalar()
        if not clash:
            investor.referral_code = code
            await session.flush()
            return code
    raise RuntimeError("could not issue a unique referral code after 12 tries")


async def by_code(session, code: str):
    """The investor a code belongs to, or None. Case-insensitive."""
    from sqlalchemy import func, select

    from backend.investor.models import Investor

    if not code:
        return None
    return (await session.execute(
        select(Investor).where(
            func.upper(Investor.referral_code) == str(code).strip().upper()))).scalar_one_or_none()


# ── the tree ────────────────────────────────────────────────────────────────


async def upline(session, investor_id: str, levels: int = MAX_LEVELS) -> list[tuple[int, str]]:
    """[(level, investor_id), ...] walking upwards, nearest first.

    Bounded by `levels` AND by a seen-set. The bound alone is enough today
    because `set_referrer` refuses a cycle, but a cycle introduced by a direct
    database edit would otherwise hang the job that pays people.
    """
    from sqlalchemy import select

    from backend.investor.models import Investor

    out: list[tuple[int, str]] = []
    seen = {investor_id}
    current = investor_id
    for lvl in range(1, int(levels) + 1):
        parent = (await session.execute(
            select(Investor.referred_by).where(Investor.id == current))).scalar()
        if not parent or parent in seen:
            break
        out.append((lvl, parent))
        seen.add(parent)
        current = parent
    return out


async def downline_ids(session, investor_id: str,
                       levels: int = MAX_LEVELS) -> dict[int, list[str]]:
    """{1: [direct ids], 2: [their referrals' ids]} - who is below this person."""
    from sqlalchemy import select

    from backend.investor.models import Investor

    out: dict[int, list[str]] = {}
    frontier = [investor_id]
    seen = {investor_id}
    for lvl in range(1, int(levels) + 1):
        if not frontier:
            break
        kids = (await session.execute(
            select(Investor.id).where(Investor.referred_by.in_(frontier)))).scalars().all()
        kids = [k for k in kids if k not in seen]
        if not kids:
            break
        out[lvl] = kids
        seen.update(kids)
        frontier = kids
    return out


async def would_cycle(session, investor_id: str, new_parent_id: str) -> bool:
    """True when making `new_parent_id` the parent would close a loop.

    A loop is not a theoretical worry: it makes the upline walk that PAYS
    people run forever, and the obvious way to create one is an admin tidying
    the tree by hand - put A under B today, B under A next week.
    """
    if not new_parent_id:
        return False
    if new_parent_id == investor_id:
        return True
    # Walk up from the proposed parent as far as the tree goes. If we meet the
    # investor, they are already above it.
    from sqlalchemy import select

    from backend.investor.models import Investor

    seen = {new_parent_id}
    current = new_parent_id
    while True:
        parent = (await session.execute(
            select(Investor.referred_by).where(Investor.id == current))).scalar()
        if not parent:
            return False
        if parent == investor_id:
            return True
        if parent in seen:          # a loop that already exists; do not add to it
            return True
        seen.add(parent)
        current = parent


async def set_referrer(session, investor_id: str, referrer_id: str | None, *,
                       source: str = "ADMIN", actor_id: str = "") -> dict:
    """Put an investor under another one, or detach them.

    THE ONLY supported way to write `referred_by`. It refuses a cycle, refuses
    an investor who does not exist, and records how the link was made - a link
    someone used is a different fact from one an admin applied afterwards, and
    only the second needs explaining later.
    """
    from sqlalchemy import select

    from backend.investor.models import Investor

    inv = (await session.execute(
        select(Investor).where(Investor.id == investor_id))).scalar_one_or_none()
    if inv is None:
        raise ValueError(f"no investor {investor_id}")

    if not referrer_id:
        inv.referred_by, inv.referral_source, inv.referred_at = None, None, None
        await session.flush()
        return {"investor_id": investor_id, "referred_by": None, "detached": True}

    ref = (await session.execute(
        select(Investor).where(Investor.id == referrer_id))).scalar_one_or_none()
    if ref is None:
        raise ValueError(f"no investor {referrer_id} to refer them")
    if await would_cycle(session, investor_id, referrer_id):
        raise ValueError(
            f"{referrer_id} is already below {investor_id}; linking them would "
            f"make the tree a loop and the payout walk would never finish")

    inv.referred_by = referrer_id
    inv.referral_source = (source or "ADMIN").upper()[:16]
    inv.referred_at = datetime.now(timezone.utc)
    await session.flush()
    logger.info(f"[REFERRAL] {investor_id} placed under {referrer_id} "
                f"({inv.referral_source}, by {actor_id or 'system'})")
    return {"investor_id": investor_id, "referred_by": referrer_id,
            "source": inv.referral_source}


# ── the arithmetic, pure ────────────────────────────────────────────────────


def split_for(*, model: str, fee_total: D, period_profit: D,
              l1_pct: D, l2_pct: D, has_l1: bool, has_l2: bool) -> dict[str, Any]:
    """What each level earns for one investor over one period.

    Pure: no session, no clock. The money arithmetic is the part that must be
    testable on its own, because an error here is only visible as a number
    that is slightly wrong on somebody's statement.

    HOUSE_FEE       the rate applies to the FEES we collected. The house keeps
                    the remainder, so paying out more than 100% is refused
                    rather than quietly producing a negative house take.
    INVESTOR_PROFIT the rate applies to the investor's PERIOD PROFIT, and the
                    investor bears it on top of the fees.

    Returns {basis, l1, l2, house_remainder, model, capped}.
    """
    model = (model or MODEL_OFF).upper()
    l1_pct, l2_pct = D(str(l1_pct or 0)), D(str(l2_pct or 0))
    if model == MODEL_OFF or (not has_l1 and not has_l2):
        return {"basis": D("0"), "l1": D("0"), "l2": D("0"),
                "house_remainder": D(str(fee_total or 0)), "model": model, "capped": False}

    if model == MODEL_HOUSE_FEE:
        basis = D(str(fee_total or 0))
    elif model == MODEL_INVESTOR_PROFIT:
        basis = max(D("0"), D(str(period_profit or 0)))
    else:
        raise ValueError(f"unknown referral model {model!r}")

    pct1 = l1_pct if has_l1 else D("0")
    pct2 = l2_pct if has_l2 else D("0")
    capped = False
    if model == MODEL_HOUSE_FEE and (pct1 + pct2) > D("100"):
        # Paying out more than was collected would make the house take
        # negative, which is not a business arrangement, it is a bug with a
        # settings screen in front of it.
        capped = True
        total = pct1 + pct2
        pct1 = pct1 / total * D("100")
        pct2 = pct2 / total * D("100")

    l1 = (basis * pct1 / D("100")).quantize(D("0.01"))
    l2 = (basis * pct2 / D("100")).quantize(D("0.01"))
    house = (D(str(fee_total or 0)) - l1 - l2) if model == MODEL_HOUSE_FEE \
        else D(str(fee_total or 0))
    return {"basis": basis, "l1": l1, "l2": l2, "house_remainder": house,
            "model": model, "capped": capped,
            "l1_pct": pct1, "l2_pct": pct2}


# ── accruing what is owed ───────────────────────────────────────────────────


async def accrue(session, *, investor_id: str, accruals: list, period_profit,
                 period_start, period_end, nav_price, actor_id: str = "",
                 waived: bool = False) -> list:
    """Write what this investor's period earned the people above them.

    Called from `fees.charge`, inside the same transaction as the fees
    themselves. If this raises, the fee charge rolls back with it: a ledger
    where we collected a fee and silently did not record what we owe the
    introducer is worse than a period that has to be closed again.

    WHOSE TERMS
    The DOWNLINE investor's - `fund.terms_for` reads the version they
    committed under. They were introduced under that arrangement, and a later
    settings change must not reprice them retroactively.

    IDEMPOTENT
    One earning per (fee accrual, earner). Re-running a period finds the rows
    and writes nothing, so a retried close cannot pay someone twice.

    WAIVED FEES
    HOUSE_FEE: the introducer is paid a share of what we COLLECTED, and a
    waived fee collected nothing, so nothing is owed. INVESTOR_PROFIT: a waiver
    lets the investor off the period entirely, so the introducer share is not
    charged to them either.
    """
    from sqlalchemy import select

    from backend.investor import fund as fundmod
    from backend.investor import units as unitsmod
    from backend.investor.models import FEE_PERFORMANCE, KIND_FEE, ReferralEarning

    terms = await fundmod.terms_for(session, investor_id)
    model = str(getattr(terms, "referral_model", None) or MODEL_OFF).upper()
    if model == MODEL_OFF:
        return []
    up = await upline(session, investor_id)
    if not up:
        return []
    l1_id = next((i for lvl, i in up if lvl == 1), None)
    l2_id = next((i for lvl, i in up if lvl == 2), None)

    present = [a for a in accruals if a is not None]
    if not present:
        return []
    # The performance accrual is written every period, even at zero, so it is
    # the stable anchor for idempotency.
    anchor = next((a for a in present if getattr(a, "kind", "") == FEE_PERFORMANCE),
                  present[0])
    fee_total = sum((D(str(a.fee_amount)) for a in present
                     if getattr(a, "state", "") == "charged"), D("0"))

    if model == MODEL_INVESTOR_PROFIT and waived:
        return []

    split = split_for(model=model, fee_total=fee_total, period_profit=period_profit,
                      l1_pct=getattr(terms, "referral_l1_pct", 0) or 0,
                      l2_pct=getattr(terms, "referral_l2_pct", 0) or 0,
                      has_l1=bool(l1_id), has_l2=bool(l2_id))

    rows = []
    for lvl, earner, amount, pct in ((1, l1_id, split["l1"], split.get("l1_pct")),
                                     (2, l2_id, split["l2"], split.get("l2_pct"))):
        if not earner or amount <= 0:
            continue
        exists = (await session.execute(select(ReferralEarning.id).where(
            ReferralEarning.fee_accrual_id == anchor.id,
            ReferralEarning.earner_id == earner))).scalar()
        if exists:
            continue
        row = ReferralEarning(
            earner_id=earner, source_investor_id=investor_id, level=lvl,
            fee_accrual_id=anchor.id, model=model,
            terms_version=getattr(terms, "version", None),
            period_start=period_start, period_end=period_end,
            basis_amount=split["basis"], rate_pct=D(str(pct or 0)), amount=amount,
            note=("capped: the configured rates summed past 100% of the fee"
                  if split.get("capped") else None))
        session.add(row)
        rows.append(row)
    await session.flush()

    # Under INVESTOR_PROFIT the introducer's share is a real charge on the
    # investor's own money: units are cancelled, as for a fee, so it shows in
    # their ledger and statement like any other deduction.
    #
    # ONE LEDGER ENTRY PER EARNING, sourced from the earning itself - NOT
    # `charge_fee`, which sources from the fee accrual. `unit_transactions` has
    # UniqueConstraint(source_kind, source_id), and the performance fee has
    # already written ("fee_accrual", <that accrual>); a second row with the
    # same pair raised IntegrityError and rolled the WHOLE fee charge back, so
    # closing a fee period failed for every referred investor in any period
    # with a performance fee. Sourcing from the earning also says exactly
    # which introducer share each cancelled unit paid for.
    if model == MODEL_INVESTOR_PROFIT:
        for r in rows:
            if r.amount <= 0:
                continue
            await unitsmod.redeem(
                session, investor_id=investor_id, amount=r.amount, price=nav_price,
                effective=period_end, kind=KIND_FEE, source_kind="referral_earning",
                source_id=r.id, created_by=actor_id,
                note=f"introducer share (level {r.level}) {period_start}..{period_end}")
    if rows:
        logger.info(f"[REFERRAL] {investor_id} {period_start}..{period_end} "
                    f"({model}): " + ", ".join(f"L{r.level} {r.earner_id} {r.amount}"
                                               for r in rows))
    return rows
