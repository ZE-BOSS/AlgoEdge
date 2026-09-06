"""Who opened this MT5 position — the bot, or somebody else?

WHY THIS EXISTS
---------------
The bot used to treat *every* deal in the connected MT5 account's history as its
own. Three separate code paths did this:

    bot_service._trade_sync_loop      every OUT deal in the last 3 days ->
                                      circuit_breaker.record_external_close()
                                      -> daily_pnl -> max-daily-drawdown halt
    position_manager._manage_positions "God Sync" / "Historical Ghost Sync":
                                      every unrecorded live position and every
                                      unrecorded closed position in the last 14
                                      days inserted into the journal as a
                                      MANUAL / MANUAL_OFFLINE Trade
    api/routes/stats.get_user_stats   get_closed_positions_since(0) — the whole
                                      account history, unfiltered

So logging the bot into an account that already had a trading history made the
bot adopt that history: months of somebody else's (or the user's own manual)
trades were written into the journal and their cumulative P&L was booked as
*today's* realised P&L. A -$13,000 six-month history therefore tripped the
max-daily-drawdown circuit breaker on the first sync cycle and blocked all
trading, on a freshly reset demo account that had actually lost nothing.

OWNERSHIP TEST
--------------
A position is the bot's if EITHER holds:

  1. its position_id is a `TradePosition.mt5_ticket` we recorded when we opened
     it — the strongest evidence, because we wrote that row ourselves; or
  2. its `magic` is in the bot's magic range (see `bot_magic_range`) — covers
     positions opened by the bot whose DB write failed, and positions from a
     previous run against a DB that has since been reset.

Everything else is somebody else's trade. We leave it alone: not in the journal,
not in the P&L, not in the drawdown.

MAGIC NUMBERS THE BOT ACTUALLY USES
-----------------------------------
    bot_service.py       magic = 1001 + (tp.level * 10)   -> 1011,1021,...,1051
    order_manager.py     magic = magic_base + i           -> 1001,1002,...

Both derive from `UserConfig.magic_base` (default 1001), so the range is
[magic_base, magic_base + MAGIC_SPAN]. Manual trades placed in the MT5 terminal
by hand always carry magic 0, and another EA would have to be configured into
this exact 100-wide window to collide.
"""
from __future__ import annotations

from typing import Any, Iterable

from backend.utils.logger import get_logger

logger = get_logger(__name__)

# The bot never assigns a magic more than this far above magic_base:
# max is `magic_base + 5 * 10` for the 5-TP scheme. 100 leaves headroom without
# swallowing an unrelated EA that picked a round number like 2000.
MAGIC_SPAN = 100

DEFAULT_MAGIC_BASE = 1001


def bot_magic_range(magic_base: int | None = None) -> tuple[int, int]:
    """Inclusive [low, high] magic range that identifies a bot-placed order."""
    base = int(magic_base or DEFAULT_MAGIC_BASE)
    return base, base + MAGIC_SPAN


def is_bot_magic(magic: Any, magic_base: int | None = None) -> bool:
    """True if `magic` is one this bot assigns. magic 0 (manual) is never ours."""
    try:
        m = int(magic)
    except (TypeError, ValueError):
        return False
    if m == 0:
        return False
    low, high = bot_magic_range(magic_base)
    return low <= m <= high


def is_bot_deal(
    deal: Any,
    known_tickets: Iterable[int] | None = None,
    magic_base: int | None = None,
) -> bool:
    """Ownership test for one MT5 deal (a namedtuple from MetaTrader5, or a dict).

    `known_tickets` is the set of position ids the bot recorded in
    TradePosition.mt5_ticket. Pass it whenever it is cheaply available — it is
    the authoritative half of the test.
    """
    if deal is None:
        return False

    if isinstance(deal, dict):
        magic = deal.get("magic", 0)
        pos_id = deal.get("position_id") or deal.get("ticket")
    else:
        magic = getattr(deal, "magic", 0)
        pos_id = getattr(deal, "position_id", None) or getattr(deal, "ticket", None)

    if known_tickets and pos_id is not None:
        try:
            if int(pos_id) in known_tickets:
                return True
        except (TypeError, ValueError):
            pass

    return is_bot_magic(magic, magic_base)


def is_bot_position(position: Any, known_tickets: Iterable[int] | None = None,
                    magic_base: int | None = None) -> bool:
    """Ownership test for one OPEN MT5 position (`mt5.positions_get()` element)."""
    if position is None:
        return False
    magic = getattr(position, "magic", 0)
    ticket = getattr(position, "ticket", None)
    if known_tickets and ticket is not None:
        try:
            if int(ticket) in known_tickets:
                return True
        except (TypeError, ValueError):
            pass
    return is_bot_magic(magic, magic_base)


async def load_bot_tickets(db, user_id: str | None = None) -> set[int]:
    """Every mt5_ticket the bot has ever recorded, for the ownership test.

    Small by construction (one row per position the bot opened) and read once
    per sync cycle, not once per deal.
    """
    from sqlalchemy import select

    from backend.data.models import TradePosition

    try:
        q = select(TradePosition.mt5_ticket).where(TradePosition.mt5_ticket.isnot(None))
        if user_id:
            q = q.where(TradePosition.user_id == user_id)
        rows = await db.execute(q)
        return {int(t) for (t,) in rows.all() if t is not None}
    except Exception as e:
        logger.warning(f"[ownership] Could not load bot tickets: {e}")
        return set()
