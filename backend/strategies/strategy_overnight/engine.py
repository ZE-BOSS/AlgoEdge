"""
backend/strategies/strategy_overnight/engine.py

OvernightSession_v1 — long an equity index from the cash close to the next cash
open.

THE RULE
--------
1. On the last M5 bar of the cash session, buy. (The fill is the next bar's open,
   minutes after the bell — the engine's normal fill.)
2. Stop `stop_atr_multiple` x daily ATR below the entry.
3. Close on the last bar before the NEXT cash open, whose close is the best
   available stand-in for that session's opening price. There is no target: the
   trade is a holding period, not a move.

One position per symbol per night, and nothing is held through a cash session.

WHY
---
The overnight (close-to-open) return is where equity index returns have
historically come from; the intraday (open-to-close) return has been close to
zero. That is a compensation-for-overnight-gap-risk story rather than a
mispricing, which is exactly why it should keep paying, and it is cheap to
harvest: one entry and one exit a night is one spread a night.

Measured as the app runs it (scripts/run_app_form_check.py), Deriv M5
2021-10 -> 2026-09:

    2,006 nights | +0.065R | 52.8% win | t +3.15 | Sharpe 1.28
    $10,000 at 0.5% risk -> $18,008 (+80.1%), max drawdown 21.7% ($4,156)
    US Tech 100 +0.090R (t +2.64), US SP 500 +0.074R (t +2.25),
    Germany 40 +0.031R (t +0.77)

And on FundedNext's own bars (2025-06 -> 2026-09, US30 and SPX500, 654 nights):
+0.056R, t +1.73, Sharpe 1.16, +13.8% at 0.5% risk with a 9.0% drawdown. It is
the only strategy in the 2026-09-25 book that survived their costs, which is why
it is the one to run on a prop account.

Read the drawdown before the return. 21.7% at 0.5% risk is a lot for an
expectancy this size, and it is structural: the losers are gap-downs, they are
correlated across indices, and they arrive together. On a prop account with a
static 10% limit, size accordingly.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
import pytz

from backend.strategies.base_strategy import BaseStrategy, TradeAction, TradeSignal
from backend.strategies.core.daily_atr import DailyATRCache, last_epoch
from backend.strategies.core.trail import to_epoch
from backend.strategies.registry import register_strategy
from backend.strategies.strategy_overnight.params import OvernightSessionParams
from backend.strategies.strategy_orb.engine import SESSIONS, session_bounds
from backend.utils.logger import get_logger

logger = get_logger(__name__)

TIMEFRAME = "M5"
BAR_SECONDS = 300


def next_session_open(session: str, after_ts: int) -> int | None:
    """The first `session` open strictly after `after_ts`, skipping weekends.

    A Friday-evening position is held over the weekend and leaves at Monday's
    open — the same pairing the research used, which took consecutive SESSIONS
    rather than consecutive days.
    """
    tz = pytz.timezone(SESSIONS[session][0])
    for day in range(1, 9):
        open_ts, _ = session_bounds(session, after_ts + day * 86400)
        if open_ts <= after_ts:
            continue
        if datetime.fromtimestamp(open_ts, tz).weekday() >= 5:
            continue
        return open_ts
    return None


@register_strategy("OvernightSession_v1")
class OvernightSessionStrategy(DailyATRCache, BaseStrategy):
    strategy_id = "OvernightSession_v1"
    # the exit is the next cash open and nothing else — without this a live
    # position would sit there until the stop or the placeholder target
    LIVE_POSITION_EXITS = True
    TIMEFRAME = TIMEFRAME

    def __init__(self, config: Any):
        super().__init__(config)
        self.params = getattr(config, "overnight_session", None) or OvernightSessionParams()

    def get_required_timeframes(self) -> list[str]:
        return [TIMEFRAME]

    @property
    def WINDOW_BARS(self) -> dict[str, int]:  # noqa: N802 — read by strategies.windows
        # the daily ATR needs `atr_days` complete days, plus the part-day a
        # rolling window opens on and the day in progress
        return {TIMEFRAME: (int(self.params.atr_days) + 3) * 288}

    async def initialize(self):
        return None

    async def on_tick(self, symbol: str, tick: dict[str, Any]) -> None:
        return None

    async def on_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame) -> TradeSignal | None:
        self.begin_candidate(
            symbol, timeframe,
            bar_time=candles.index[-1] if candles is not None and len(candles) else None,
        )
        p = self.params
        if timeframe != TIMEFRAME or candles is None or len(candles) < 20:
            return None
        if p.session not in SESSIONS:
            self.gate("session_known", False, f"unknown session {p.session!r}")
            return None

        ti = last_epoch(candles)
        open_ts, close_ts = session_bounds(p.session, ti)
        # the session's LAST bar: inside the session, and the next bar is not
        if not self.gate("session_last_bar",
                         open_ts <= ti < close_ts and ti + BAR_SECONDS >= close_ts,
                         "not the final bar of the cash session"):
            return None

        atr = self.daily_atr(symbol, candles)
        if not self.gate("daily_atr", atr is not None, f"{p.atr_days}-day ATR unavailable"):
            return None

        stop_dist = float(p.stop_atr_multiple) * float(atr)
        if not self.gate("stop_positive", stop_dist > 0):
            return None

        nxt = next_session_open(p.session, ti)
        if not self.gate("next_open_resolved", nxt is not None):
            return None

        from backend.strategies.strategy_defaults import SLOT_TP1_RR, get_strategy_defaults
        rr = float(SLOT_TP1_RR.get(f"{symbol.upper()}|{self.strategy_id}",
                                   get_strategy_defaults(self.strategy_id).get("tp1_rr", 20.0)))
        c = float(candles["close"].iloc[-1])
        return self._tag_signal(TradeSignal(
            strategy_id=self.strategy_id,
            symbol=symbol,
            direction="BUY",           # the premium is paid for holding, not for a view
            signal_type="OVERNIGHT_HOLD",
            timeframe=TIMEFRAME,
            entry_price=c,
            stop_loss=c - stop_dist,
            take_profit=c + rr * stop_dist,
            # a calendar rule with no discretion, measured at full risk
            confluence_score=80,
            timestamp=float(ti),
            metadata={
                "size_modifier": 1.0,
                "trail_method": "NONE",
                "setup": "overnight_session",
                "session": p.session,
                "session_close_ts": close_ts,
                "next_session_open_ts": int(nxt),
                "atr_val": float(atr),
                "tp1_rr": rr,
                "reason": (
                    f"long the {p.session} cash close -> next cash open; stop "
                    f"{p.stop_atr_multiple:g}x daily ATR, exit at the open"
                ),
            },
        ))

    def on_position_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame,
                        position: dict) -> TradeAction | None:
        """Out on the last bar before the next cash open — that bar's close is
        the nearest thing to the opening price the engine can fill at."""
        p = self.params
        if candles is None or not len(candles) or p.session not in SESSIONS:
            return None
        entry_ts = to_epoch(position.get("entry_time"))
        if entry_ts is None:
            return None
        nxt = next_session_open(p.session, entry_ts)
        if nxt is None:
            return None
        last = last_epoch(candles)
        # `>=` also covers a holiday or a data gap that skipped the open entirely
        if last + BAR_SECONDS >= nxt:
            return TradeAction(ticket=int(position.get("ticket") or 0), action="CLOSE",
                               close_reason="SESSION_OPEN")
        return None
