"""
backend/investor/disclosure.py

Which closed trades investors may see, and exactly which fields of them.

Every closed live trade lands in the admin's queue as undisclosed. The admin
publishes it as-is, publishes it edited, or hides it. A published trade carries
symbol, direction, date and result — never the strategy, its parameters, the
entry logic, the ticket or the size.

The strip happens HERE, at the boundary, not in any frontend. `TradeDisclosure`
has no column that could hold strategy identity, and `public_view()` returns a
whitelist rather than "the row minus some fields", so a column added to the
table later is private until someone deliberately adds it to PUBLIC_FIELDS.

This module reads `trades`. It never writes to it.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import desc, func, select

from backend.data.models import Trade
from backend.investor import fund as fundmod
from backend.investor import nav as navmod
from backend.investor.models import (
    DISCLOSURE_HIDDEN,
    DISCLOSURE_PUBLISHED,
    Adjustment,
    TradeDisclosure,
)

D = Decimal

# The ONLY fields an investor-facing response may carry. A whitelist, so that a
# new column is private by default.
PUBLIC_FIELDS = ("id", "symbol", "direction", "closed_on", "result_amount",
                 "result_pct", "note")

# What an admin may change before publishing. Symbol and direction are here
# because a trade can be legitimately relabelled (a broker suffix like
# "EURUSD.m" is noise to an investor); the result is here because a trade that
# was partly hedged elsewhere has a different net effect on the pool.
EDITABLE_FIELDS = ("symbol", "direction", "closed_on", "result_amount",
                   "result_pct", "note")


def _money_or_none(value):
    return None if value is None else navmod.money(value)


def _pct_or_none(value):
    return None if value is None else D(str(value)).quantize(D("0.0001"))


def _from_trade(trade: Trade) -> dict:
    """The as-is published values for a trade: what it would say unedited."""
    pct = None
    if trade.pnl is not None and trade.balance_before:
        pct = D(str(trade.pnl)) / D(str(trade.balance_before)) * D("100")
    # The WAT day, matching every other date on the investor side.
    closed = navmod.accounting_date(trade.exit_time) if trade.exit_time else None
    return {
        "symbol": trade.symbol,
        "direction": trade.direction,
        "closed_on": closed,
        "result_amount": _money_or_none(None if trade.pnl is None else str(trade.pnl)),
        "result_pct": _pct_or_none(pct),
        "note": None,
    }


def _normalise(field: str, value):
    if value is None:
        return None
    if field == "result_amount":
        return navmod.money(value)
    if field == "result_pct":
        return _pct_or_none(value)
    if field == "closed_on":
        return value if isinstance(value, date) else date.fromisoformat(str(value))
    if field == "direction":
        value = str(value).upper()
        if value not in ("BUY", "SELL"):
            raise fundmod.FundError(f"direction must be BUY or SELL, not {value}")
        return value
    return str(value)


def public_view(row: TradeDisclosure) -> dict:
    """The investor-facing shape. Whitelisted, stringified, nothing else."""
    out = {}
    for f in PUBLIC_FIELDS:
        v = getattr(row, f)
        out[f] = v.isoformat() if isinstance(v, date) else (None if v is None else
                                                            (navmod.text(v) if isinstance(v, D) else v))
    return out


def admin_view(trade: Trade, row: TradeDisclosure | None) -> dict:
    """What the admin sees in the queue: the raw trade beside what is published.

    Strategy identity is included here because the admin needs it to decide —
    this shape must never be returned by an investor route.
    """
    raw = _from_trade(trade)
    return {
        "trade_id": trade.id,
        "state": row.state if row else "undisclosed",
        "raw": {
            "symbol": trade.symbol,
            "direction": trade.direction,
            "strategy_id": trade.strategy_id,
            "entry_time": trade.entry_time.isoformat() if trade.entry_time else None,
            "exit_time": trade.exit_time.isoformat() if trade.exit_time else None,
            "pnl": None if trade.pnl is None else str(navmod.money(str(trade.pnl))),
            "exit_reason": trade.exit_reason,
            "closed_on": raw["closed_on"].isoformat() if raw["closed_on"] else None,
            "result_pct": None if raw["result_pct"] is None else navmod.text(raw["result_pct"]),
        },
        "published": public_view(row) if row and row.state == DISCLOSURE_PUBLISHED else None,
        "edited": bool(row and row.edited),
    }


async def queue(session, *, state: str = "undisclosed", limit: int = 100,
                offset: int = 0) -> list[dict]:
    """Closed live trades, newest first, filtered by disclosure state."""
    q = (select(Trade, TradeDisclosure)
         .outerjoin(TradeDisclosure, TradeDisclosure.trade_id == Trade.id)
         .where(Trade.status == "CLOSED"))
    if state == "undisclosed":
        q = q.where(TradeDisclosure.id.is_(None))
    elif state in (DISCLOSURE_PUBLISHED, DISCLOSURE_HIDDEN):
        q = q.where(TradeDisclosure.state == state)
    elif state != "all":
        raise fundmod.FundError(f"unknown disclosure state {state}")
    q = q.order_by(desc(Trade.exit_time), desc(Trade.id)).limit(limit).offset(offset)
    rows = (await session.execute(q)).all()
    return [admin_view(t, d) for t, d in rows]


async def undisclosed_count(session) -> int:
    return (await session.execute(
        select(func.count(Trade.id))
        .outerjoin(TradeDisclosure, TradeDisclosure.trade_id == Trade.id)
        .where(Trade.status == "CLOSED", TradeDisclosure.id.is_(None))
    )).scalar_one()


async def _trade(session, trade_id: int) -> Trade:
    trade = await session.get(Trade, trade_id)
    if trade is None:
        raise fundmod.FundError(f"no trade {trade_id}")
    if trade.status != "CLOSED":
        raise fundmod.FundError("only a closed trade can be disclosed")
    return trade


async def publish(session, *, trade_id: int, actor_id: str,
                  edits: dict | None = None, reason: str | None = None) -> TradeDisclosure:
    """Publish a trade, as-is or edited.

    Any field that differs from the trade's own value is an edit, needs a reason,
    and writes an `adjustments` row with the raw value as `old_value`. That is
    what makes "published edited" an adjustment and not a misrepresentation:
    the reconciliation can always show the raw figure next to the published one.

    Re-publishing an already published trade applies the new edits against what
    was published, so each change is its own recorded step.
    """
    edits = dict(edits or {})
    unknown = set(edits) - set(EDITABLE_FIELDS)
    if unknown:
        raise fundmod.FundError(f"not editable: {sorted(unknown)}")

    trade = await _trade(session, trade_id)
    raw = _from_trade(trade)
    row = (await session.execute(
        select(TradeDisclosure).where(TradeDisclosure.trade_id == trade_id)
    )).scalar_one_or_none()

    baseline = raw if row is None or row.state != DISCLOSURE_PUBLISHED else \
        {f: getattr(row, f) for f in EDITABLE_FIELDS}
    target = {f: (_normalise(f, edits[f]) if f in edits else baseline[f])
              for f in EDITABLE_FIELDS}
    changed = [f for f in EDITABLE_FIELDS if target[f] != baseline[f]]
    # A note is commentary, not a figure — adding one is not an edit to a number.
    figure_changes = [f for f in changed if f != "note"]
    if figure_changes and not (reason or "").strip():
        raise fundmod.FundError(
            f"publishing with {', '.join(figure_changes)} changed from the trade "
            f"needs a reason")

    if row is None:
        row = TradeDisclosure(trade_id=trade_id, decided_by=actor_id)
        session.add(row)
    row.state = DISCLOSURE_PUBLISHED
    for f in EDITABLE_FIELDS:
        setattr(row, f, target[f])
    # Edited means "differs from the trade", however many steps it took.
    row.edited = any(target[f] != raw[f] for f in EDITABLE_FIELDS if f != "note")
    row.decided_by = actor_id
    row.decided_at = datetime.now(timezone.utc)
    await session.flush()

    for f in figure_changes:
        session.add(Adjustment(
            entity_type="trade_disclosure", entity_id=str(trade_id), field=f,
            old_value=None if baseline[f] is None else
            (navmod.text(baseline[f]) if isinstance(baseline[f], D) else str(baseline[f])),
            new_value=None if target[f] is None else
            (navmod.text(target[f]) if isinstance(target[f], D) else str(target[f])),
            reason=reason, actor_id=actor_id))
    await fundmod.audit(session, actor_id=actor_id, action="disclosure.published",
                        entity_type="trade", entity_id=str(trade_id),
                        detail={"edited": figure_changes, "reason": reason})
    return row


async def hide(session, *, trade_id: int, actor_id: str,
               reason: str | None = None) -> TradeDisclosure:
    """Decide not to show a trade. Also how a published trade is withdrawn.

    Withdrawing something investors have already seen needs a reason — they may
    have screenshotted it, and the record should say why it disappeared.
    """
    await _trade(session, trade_id)
    row = (await session.execute(
        select(TradeDisclosure).where(TradeDisclosure.trade_id == trade_id)
    )).scalar_one_or_none()
    was_published = row is not None and row.state == DISCLOSURE_PUBLISHED
    if was_published and not (reason or "").strip():
        raise fundmod.FundError("withdrawing a published trade needs a reason")
    if row is None:
        row = TradeDisclosure(trade_id=trade_id, decided_by=actor_id)
        session.add(row)
    row.state = DISCLOSURE_HIDDEN
    row.decided_by = actor_id
    row.decided_at = datetime.now(timezone.utc)
    await session.flush()
    if was_published:
        session.add(Adjustment(entity_type="trade_disclosure", entity_id=str(trade_id),
                               field="state", old_value=DISCLOSURE_PUBLISHED,
                               new_value=DISCLOSURE_HIDDEN, reason=reason,
                               actor_id=actor_id))
    await fundmod.audit(session, actor_id=actor_id, action="disclosure.hidden",
                        entity_type="trade", entity_id=str(trade_id),
                        detail={"was_published": was_published, "reason": reason})
    return row


async def published(session, *, limit: int = 100, offset: int = 0) -> list[dict]:
    """The investor-facing list. Phase 3's investor route calls exactly this."""
    rows = (await session.execute(
        select(TradeDisclosure).where(TradeDisclosure.state == DISCLOSURE_PUBLISHED)
        .order_by(desc(TradeDisclosure.closed_on), desc(TradeDisclosure.trade_id))
        .limit(limit).offset(offset)
    )).scalars().all()
    return [public_view(r) for r in rows]
