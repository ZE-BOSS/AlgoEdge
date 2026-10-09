"""
backend/mt5/history.py

The only place MT5 deal history is read, classified and stored.

THE RULE THIS REPLACES
----------------------
Two places asked `getattr(d, 'type', 0) >= 2` and called the answer "net
deposits". That range also contains CHARGE, CORRECTION, BONUS, five kinds of
COMMISSION, INTEREST, two cancellations, DIVIDEND and TAX — so commission and
interest were counted as deposits, and `prop_firm_validator` re-baselines its
drawdown high-water mark when that total falls.

THE RULE THAT REPLACES IT IS NOT A RULE
---------------------------------------
Measured on both terminals, 2026-10-08:

    Deriv 6194570       capital as DEAL_BALANCE (2),  "Reset+Balance+for+Demo+Acount"
    FundedNext 11869564 capital as DEAL_CHARGE  (4),  "Initial+Deposit"

So `type == 2` — the obvious fix — misses FundedNext's entire $25,000, and
`type >= 2` sweeps in costs. Neither constant is right, which is why the map
lives on the account (`MT5Account.deal_class_overrides`) and anything it cannot
place lands in UNKNOWN, where it BLOCKS the reconciliation from reporting
healthy rather than being quietly absorbed.

Comment text is not a discriminator either: both Deriv balance deals carry the
SAME comment for a top-up (+4,121.81) and a deduction (-115.80). Sign carries
direction; the comment only says "this is an adjustment, not investor capital".

WHAT THIS MODULE MAY NOT DO
---------------------------
It may not import `backend.investor`. The investor ledger reads the tables this
writes, never the terminal — see backend/investor/__init__.py for why.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select

from backend.mt5.cash_models import (
    ADJUSTMENT,
    CAPITAL_IN,
    CAPITAL_OUT,
    COST,
    CREDIT_NOT_CAPITAL,
    DEAL_BALANCE,
    DEAL_TYPE_NAMES,
    DEFAULT_DEAL_CLASSES,
    TRADE_RESULT,
    UNKNOWN,
    MT5Account,
    MT5CashFlow,
    OpeningAnchor,
)
from backend.utils.logger import get_logger

logger = get_logger(__name__)


# ── classification ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Classified:
    flow_class: str
    amount: float
    by: str
    review_reason: str | None = None


def _overrides(account: MT5Account | None) -> dict[int, str]:
    if account is None or not account.deal_class_overrides:
        return {}
    try:
        raw = json.loads(account.deal_class_overrides)
    except (TypeError, ValueError):
        logger.warning(f"[CASH] {getattr(account, 'login', '?')}: unreadable deal_class_overrides")
        return {}
    out: dict[int, str] = {}
    for k, v in (raw or {}).items():
        try:
            out[int(k)] = str(v)
        except (TypeError, ValueError):
            continue
    return out


def _adjustment_patterns(account: MT5Account | None) -> list[str]:
    if account is None or not account.adjustment_comment_patterns:
        return []
    try:
        return [str(p).lower() for p in (json.loads(account.adjustment_comment_patterns) or [])]
    except (TypeError, ValueError):
        return []


def signed_amount(deal) -> float:
    """What this deal moved the balance by.

    profit + commission + swap + fee, because a trade deal carries its costs
    on the same row and summing only `profit` silently loses them. Measured:
    FundedNext's 171 deals reconcile to the cent this way (22,993.35) and do
    not without the commission term (23,453.33).
    """
    return (float(getattr(deal, "profit", 0.0) or 0.0)
            + float(getattr(deal, "commission", 0.0) or 0.0)
            + float(getattr(deal, "swap", 0.0) or 0.0)
            + float(getattr(deal, "fee", 0.0) or 0.0))


def classify(deal, account: MT5Account | None = None) -> Classified:
    """One deal to one class. Never guesses: it answers UNKNOWN instead."""
    deal_type = int(getattr(deal, "type", -1))
    amount = signed_amount(deal)
    comment = str(getattr(deal, "comment", "") or "").lower()
    overrides = _overrides(account)

    # 1. a comment the account has told us means "adjustment, not capital"
    if deal_type in (DEAL_BALANCE,) or deal_type in overrides:
        for pattern in _adjustment_patterns(account):
            if pattern and pattern in comment:
                return Classified(ADJUSTMENT, amount, "override",
                                  f"comment matches {pattern!r}")

    # 2. the account's own map, then the measured defaults
    chosen = overrides.get(deal_type)
    by = "override" if chosen else "default"
    if chosen is None:
        chosen = DEFAULT_DEAL_CLASSES.get(deal_type)

    if chosen is None:
        name = DEAL_TYPE_NAMES.get(deal_type, "?")
        return Classified(UNKNOWN, amount, "default",
                          f"deal type {deal_type} ({name}) has no classification")

    # 3. capital is directional, and the sign decides — not the comment, which
    #    on Deriv is identical for a top-up and a deduction.
    if chosen in (CAPITAL_IN, CAPITAL_OUT):
        if amount == 0:
            return Classified(UNKNOWN, amount, by,
                              "a capital movement of zero is not a movement")
        chosen = CAPITAL_IN if amount > 0 else CAPITAL_OUT

    if chosen == UNKNOWN:
        name = DEAL_TYPE_NAMES.get(deal_type, "?")
        return Classified(UNKNOWN, amount, by,
                          f"{name} must be reviewed: a broker correction can be "
                          f"capital or a cost, and only you know which")

    if chosen not in (CAPITAL_IN, CAPITAL_OUT, COST, CREDIT_NOT_CAPITAL,
                      TRADE_RESULT, ADJUSTMENT):
        return Classified(UNKNOWN, amount, by, f"{chosen!r} is not a known class")

    return Classified(chosen, amount, by)


# ── reading the terminal ────────────────────────────────────────────────────

async def fetch_deals(start: datetime, end: datetime | None = None) -> list:
    """Every deal in the window, from the terminal MT5 is attached to.

    On the single MT5 thread — the package is not thread-safe and its
    connection belongs to the thread that initialised it (see mt5/executor.py).
    """
    import MetaTrader5 as mt5

    from backend.mt5.executor import run_mt5

    end = end or datetime.now()
    deals = await run_mt5(mt5.history_deals_get, start, end)
    return list(deals or [])


async def account_snapshot() -> dict | None:
    """login, broker, server, currency, balance, equity — or None if detached."""
    import MetaTrader5 as mt5

    from backend.mt5.executor import run_mt5

    info = await run_mt5(mt5.account_info)
    if info is None:
        return None
    terminal = await run_mt5(mt5.terminal_info)
    return {
        "login": int(info.login), "server": getattr(info, "server", ""),
        "currency": getattr(info, "currency", "USD"),
        "balance": float(info.balance), "equity": float(info.equity),
        "broker": getattr(terminal, "company", "") if terminal else "",
    }


def _to_utc(epoch_seconds: int) -> datetime:
    """Broker-server time to UTC, using the offset data_fetcher already solves.

    Reused rather than re-answered: there is exactly one right answer to "what
    is this terminal's clock offset" and `data_fetcher` already has it.
    """
    from backend.mt5 import data_fetcher as df

    offset_hours = 0.0
    try:
        cached = getattr(df, "_SERVER_OFFSET_CACHE", None)
        if cached is not None:
            offset_hours = float(cached)
    except (TypeError, ValueError):
        offset_hours = 0.0
    return datetime.fromtimestamp(epoch_seconds - offset_hours * 3600, tz=timezone.utc).replace(tzinfo=None)


# ── storing ─────────────────────────────────────────────────────────────────

async def get_account(session, login: int) -> MT5Account | None:
    return (await session.execute(
        select(MT5Account).where(MT5Account.login == int(login)))).scalar_one_or_none()


async def upsert_account(session, snapshot: dict) -> MT5Account:
    """Record the account we are attached to, without overwriting its settings."""
    row = await get_account(session, snapshot["login"])
    if row is None:
        row = MT5Account(login=int(snapshot["login"]))
        session.add(row)
    # Facts from the terminal are refreshed; everything an admin chose is left
    # alone — re-attaching must not silently reset `holds_investor_capital`.
    row.broker = snapshot.get("broker") or row.broker
    row.server = snapshot.get("server") or row.server
    row.currency = snapshot.get("currency") or row.currency
    if not row.label:
        row.label = f"{row.broker or 'MT5'} {row.login}"
    await session.flush()
    return row


async def store_deals(session, login: int, deals: list) -> dict:
    """Classify and store, skipping deals already stored. Returns a summary.

    Idempotent through `UNIQUE(account_login, deal_ticket)`: this is safe to
    run on every start and as often as anyone presses the button.
    """
    account = await get_account(session, login)
    existing = {t for (t,) in (await session.execute(
        select(MT5CashFlow.deal_ticket).where(MT5CashFlow.account_login == int(login))))}

    added, by_class, unknown = 0, {}, 0
    for deal in deals:
        ticket = int(getattr(deal, "ticket", 0) or 0)
        if not ticket or ticket in existing:
            continue
        verdict = classify(deal, account)
        epoch = int(getattr(deal, "time", 0) or 0)
        session.add(MT5CashFlow(
            account_login=int(login), deal_ticket=ticket,
            deal_type=int(getattr(deal, "type", -1)),
            deal_entry=int(getattr(deal, "entry", 0) or 0),
            position_id=int(getattr(deal, "position_id", 0) or 0) or None,
            order_ticket=int(getattr(deal, "order", 0) or 0) or None,
            symbol=str(getattr(deal, "symbol", "") or "") or None,
            volume=float(getattr(deal, "volume", 0.0) or 0.0),
            price=float(getattr(deal, "price", 0.0) or 0.0),
            profit=float(getattr(deal, "profit", 0.0) or 0.0),
            commission=float(getattr(deal, "commission", 0.0) or 0.0),
            swap=float(getattr(deal, "swap", 0.0) or 0.0),
            fee=float(getattr(deal, "fee", 0.0) or 0.0),
            comment=str(getattr(deal, "comment", "") or "")[:255] or None,
            magic=int(getattr(deal, "magic", 0) or 0) or None,
            deal_time=datetime.fromtimestamp(epoch),
            deal_time_utc=_to_utc(epoch),
            flow_class=verdict.flow_class, amount=verdict.amount,
            classified_by=verdict.by, review_reason=verdict.review_reason,
        ))
        existing.add(ticket)
        added += 1
        by_class[verdict.flow_class] = by_class.get(verdict.flow_class, 0) + 1
        if verdict.flow_class == UNKNOWN:
            unknown += 1

    if account is not None:
        account.last_synced_at = datetime.now(timezone.utc)
    await session.flush()
    if unknown:
        logger.warning(
            f"[CASH] {login}: {unknown} deal(s) could not be classified and are "
            f"waiting for review — the reconciliation cannot report healthy until "
            f"they are resolved")
    return {"added": added, "by_class": by_class, "unknown": unknown,
            "total_seen": len(deals)}


async def sync(session, *, login: int | None = None, since: datetime | None = None) -> dict:
    """Read the attached terminal and bring this account's ledger up to date."""
    snapshot = await account_snapshot()
    if snapshot is None:
        return {"ok": False, "reason": "MT5 is not attached to a terminal"}
    if login is not None and int(login) != snapshot["login"]:
        # Refusing rather than syncing the wrong account into the wrong ledger.
        return {"ok": False, "reason": (
            f"MT5 is attached to {snapshot['login']}, not {login} — attach the "
            f"other terminal first")}

    await upsert_account(session, snapshot)
    start = since or datetime(2000, 1, 1)
    deals = await fetch_deals(start)
    result = await store_deals(session, snapshot["login"], deals)
    return {"ok": True, "login": snapshot["login"], "broker": snapshot["broker"],
            "balance": snapshot["balance"], "equity": snapshot["equity"], **result}


# ── attributing old journal rows ────────────────────────────────────────────

async def backfill_trade_accounts(session, login: int) -> dict:
    """Give historical `Trade` rows an account, where the evidence allows it.

    `trades.mt5_account` is stamped at write from 2026-10-08. Rows written
    before that have nothing, and GUESSING is worse than leaving them blank:
    attributing a trade to the wrong account puts its P&L in the wrong
    reconciliation.

    So the only evidence accepted is the ticket. A `Trade.mt5_ticket` that
    appears in this account's own deal history belongs to this account and
    nothing else does. Measured on the VPS database: 23 of 94 rows carry a
    ticket, so most of the pre-bot block stays NULL — which is the honest
    answer and is shown as "account unknown" rather than filled in.
    """
    from backend.data.models import Trade

    tickets = {t for (t,) in (await session.execute(
        select(MT5CashFlow.position_id).where(
            MT5CashFlow.account_login == int(login),
            MT5CashFlow.position_id.isnot(None)))) if t}
    tickets |= {t for (t,) in (await session.execute(
        select(MT5CashFlow.order_ticket).where(
            MT5CashFlow.account_login == int(login),
            MT5CashFlow.order_ticket.isnot(None)))) if t}

    rows = (await session.execute(
        select(Trade).where(Trade.mt5_account.is_(None),
                            Trade.mt5_ticket.isnot(None)))).scalars().all()
    matched = 0
    for trade in rows:
        if int(trade.mt5_ticket or 0) in tickets:
            trade.mt5_account = int(login)
            matched += 1

    unattributed = int((await session.execute(
        select(func.count()).select_from(Trade).where(
            Trade.mt5_account.is_(None)))).scalar_one())
    await session.flush()
    if matched:
        logger.info(f"[CASH] {login}: attributed {matched} journal row(s) by ticket")
    return {"matched": matched, "considered": len(rows),
            "still_unattributed": unattributed}


# ── the anchor ──────────────────────────────────────────────────────────────

async def anchor_from_journal(session, login: int, *, user_id: str | None = None):
    """Anchor at the first trade the BOT took, with that row's balance_before.

    "When the bot started trading" is a journal question, not a broker one, and
    the answer is the first `Trade` carrying a real strategy_id. Rows written
    by the position-manager's import of hand-placed trades are tagged
    MANUAL_* and are NOT the bot starting: measured on the VPS database, the
    journal begins 2026-09-17 with 71 such rows, while the bot's own first
    trade is 2026-09-30 02:35:06 (DriftJumpAlpha_v1, balance_before 3266.92).
    """
    from backend.data.models import Trade

    q = (select(Trade)
         .where(Trade.strategy_id.notlike("MANUAL%"), Trade.balance_before.isnot(None))
         .order_by(Trade.entry_time.asc()).limit(1))
    if user_id:
        q = q.where(Trade.user_id == user_id)
    # Once `trades.mt5_account` is populated this narrows to the account; rows
    # written before it exists have NULL and are not excluded, because the
    # alternative is no anchor at all.
    scoped = await session.execute(q.where(
        (Trade.mt5_account == int(login)) | (Trade.mt5_account.is_(None))))
    first = scoped.scalar_one_or_none()
    if first is None:
        return None

    row = (await session.execute(select(OpeningAnchor).where(
        OpeningAnchor.account_login == int(login)))).scalar_one_or_none()
    if row is None:
        row = OpeningAnchor(account_login=int(login))
        session.add(row)
    elif row.source == "manual":
        # A figure somebody entered by hand beats one we derived: they know
        # something the journal does not.
        return row
    row.as_of = first.entry_time
    row.balance = float(first.balance_before)
    row.source = "journal"
    row.from_trade_id = first.id
    row.note = (f"first bot trade: {first.strategy_id} on {first.symbol} "
                f"at {first.entry_time}")
    await session.flush()
    return row


async def get_anchor(session, login: int) -> OpeningAnchor | None:
    return (await session.execute(select(OpeningAnchor).where(
        OpeningAnchor.account_login == int(login)))).scalar_one_or_none()


# ── the arithmetic ──────────────────────────────────────────────────────────

async def totals(session, login: int, *, start: datetime | None = None,
                 end: datetime | None = None) -> dict[str, float]:
    """Summed amount per class over the window."""
    q = select(MT5CashFlow.flow_class, func.coalesce(func.sum(MT5CashFlow.amount), 0.0)) \
        .where(MT5CashFlow.account_login == int(login))
    if start is not None:
        q = q.where(MT5CashFlow.deal_time_utc >= start)
    if end is not None:
        q = q.where(MT5CashFlow.deal_time_utc <= end)
    rows = (await session.execute(q.group_by(MT5CashFlow.flow_class))).all()
    return {cls: float(total) for cls, total in rows}


async def reconcile(session, login: int, *, actual_balance: float | None = None) -> dict:
    """anchor + everything since == the balance? Show the residual either way.

    The residual is reported WHEN IT IS ZERO TOO. A check that only speaks up
    when it is unhappy is one people stop believing, and a silent zero is
    indistinguishable from a check that did not run.
    """
    anchor = await get_anchor(session, login)
    start = anchor.as_of if anchor else None
    sums = await totals(session, login, start=start)

    capital_in = sums.get(CAPITAL_IN, 0.0)
    capital_out = sums.get(CAPITAL_OUT, 0.0)        # already negative
    trade_result = sums.get(TRADE_RESULT, 0.0)
    cost = sums.get(COST, 0.0)
    credit = sums.get(CREDIT_NOT_CAPITAL, 0.0)
    adjustment = sums.get(ADJUSTMENT, 0.0)
    unknown_total = sums.get(UNKNOWN, 0.0)

    opening = float(anchor.balance) if anchor else 0.0
    expected = (opening + capital_in + capital_out + trade_result + cost
                + credit + adjustment + unknown_total)

    unknown_count = (await session.execute(
        select(func.count()).select_from(MT5CashFlow).where(
            MT5CashFlow.account_login == int(login),
            MT5CashFlow.flow_class == UNKNOWN))).scalar_one()

    # An anchor later than every deal reports a perfect row of zeros, which
    # looks like a quiet, empty, healthy account rather than a window that
    # excludes the entire ledger. Caught on screen 2026-10-09, where the
    # summary tiles showed the real figures beside a reconciliation of 0.00
    # throughout and nothing said why.
    counted, newest = (await session.execute(
        select(func.count(), func.max(MT5CashFlow.deal_time_utc)).where(
            MT5CashFlow.account_login == int(login)))).one()
    anchor_excludes_everything = bool(
        anchor is not None and counted and newest is not None and anchor.as_of > newest)

    out = {
        "account_login": int(login),
        "anchored_at": anchor.as_of.isoformat() if anchor else None,
        "anchor_source": anchor.source if anchor else None,
        "anchor_note": anchor.note if anchor else None,
        "opening_balance": round(opening, 2),
        "capital_in": round(capital_in, 2),
        "capital_out": round(capital_out, 2),
        "net_capital": round(capital_in + capital_out, 2),
        "trade_result": round(trade_result, 2),
        "costs": round(cost, 2),
        "credit_not_capital": round(credit, 2),
        "adjustments": round(adjustment, 2),
        "unclassified_amount": round(unknown_total, 2),
        "unclassified_count": int(unknown_count),
        "expected_balance": round(expected, 2),
        "actual_balance": None if actual_balance is None else round(float(actual_balance), 2),
        "has_anchor": anchor is not None,
        "deals_in_window": int(counted),
        "anchor_excludes_everything": anchor_excludes_everything,
    }
    if actual_balance is not None:
        out["residual"] = round(float(actual_balance) - expected, 2)
    # Healthy means: it adds up AND nothing is waiting to be classified. A
    # ledger that balances because an unknown deal happens to net to zero is
    # not reconciled, it is lucky.
    out["healthy"] = bool(
        anchor is not None and unknown_count == 0
        and not anchor_excludes_everything
        and actual_balance is not None and abs(out.get("residual", 1.0)) < 0.01)
    out["blocked_by"] = (
        [] if out["healthy"] else
        ([] if anchor else ["no opening anchor"])
        + ([f"{unknown_count} deal(s) await review"] if unknown_count else [])
        + ([f"the opening anchor ({anchor.as_of:%Y-%m-%d}) is later than every "
            f"deal in this ledger (newest {newest:%Y-%m-%d}), so the figures "
            f"below cover nothing"] if anchor_excludes_everything else [])
        + (["no broker balance to compare against"] if actual_balance is None else [])
        + ([f"residual {out.get('residual')}"] if actual_balance is not None
           and abs(out.get("residual", 0.0)) >= 0.01 else []))
    return out
