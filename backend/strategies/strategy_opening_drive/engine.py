"""
backend/strategies/strategy_opening_drive/engine.py

OpeningDrive_v1 — the first 5-minute candle of the cash session against the
12 EMA, with a chandelier trail.

THE RULE
--------
1. Take the FIRST M5 bar of the cash session. If it closes above the 12-period
   EMA of M5 closes, buy; below, sell. (The fill is the next bar's open — the
   engine's normal fill, and what the research measured.)
2. Stop `stop_atr_multiple` x daily ATR from the entry.
3. Trail `trail_atr_multiple` x daily ATR back from the best close since entry.
4. Flat at the cash close.

One trade per session, and the direction is decided by one bar. There is no
discretion in it at all, which is what makes it testable.

WHAT IT IS
----------
This is the marketed system the 2026-09-25 session was asked to verify, and the
verification FAILED on its headline claims: 49.8% wins and a 1.07 profit factor
pooled over 5,081 sessions against "57% and 1.29". See params.py for the full
statement of the claim and what it actually measures.

What survives is a small real edge on equity indices and nothing elsewhere.
Measured as the app runs it, Deriv M5 2021-10 -> 2026-09, seven markets:

    5,081 sessions | +0.012R | 49.8% win | t +1.89 | Sharpe 0.59
    $10,000 at 0.5% risk -> $12,652 (+26.5%), max drawdown 10.1%
    US Tech 100 +0.052R (t +2.49) is the only market that stands alone;
    BTCUSD -0.005R and every FX pair tested were negative

On FundedNext's own bars it is NEGATIVE (1,313 sessions, -0.008R, -7.8%). It is a
Deriv-cost strategy, not a prop-account one. Run it on US Tech 100 or not at all.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from backend.strategies.base_strategy import BaseStrategy, TradeAction, TradeSignal
from backend.strategies.core.daily_atr import DailyATRCache, last_epoch
from backend.strategies.core.trail import chandelier_level
from backend.strategies.registry import register_strategy
from backend.strategies.strategy_opening_drive.params import OpeningDriveParams
from backend.strategies.strategy_orb.engine import SESSIONS, session_bounds
from backend.utils.logger import get_logger

logger = get_logger(__name__)

TIMEFRAME = "M5"
BAR_SECONDS = 300


def ema_last(values: np.ndarray, span: int) -> float:
    """The final value of a span-`span` EMA, seeded on the first sample.

    `adjust=False` IS the recursion `scripts/run_claimed_strategy.ema` runs —
    out[0] = x[0], out[i] = a*x[i] + (1-a)*out[i-1] with a = 2/(span+1) — so the
    live engine's EMA is the one the claim was tested against. On a window of
    several thousand bars the seed has long since washed out.
    """
    return float(pd.Series(values).ewm(span=int(span), adjust=False).mean().iloc[-1])


@register_strategy("OpeningDrive_v1")
class OpeningDriveStrategy(DailyATRCache, BaseStrategy):
    strategy_id = "OpeningDrive_v1"
    # the trail and the session-close flat are both part of the measured rule
    LIVE_POSITION_EXITS = True
    TIMEFRAME = TIMEFRAME

    def __init__(self, config: Any):
        super().__init__(config)
        self.params = getattr(config, "opening_drive", None) or OpeningDriveParams()

    def get_required_timeframes(self) -> list[str]:
        return [TIMEFRAME]

    @property
    def WINDOW_BARS(self) -> dict[str, int]:  # noqa: N802 — read by strategies.windows
        # the daily ATR needs `atr_days` complete days, plus the part-day a
        # rolling window opens on and the day in progress
        return {TIMEFRAME: (int(self.params.atr_days) + 3) * 288}

    @property
    def POSITION_BAR_WINDOW(self) -> int:  # noqa: N802 — backtesters and live
        p = self.params
        # the trail recomputes the daily ATR itself, because live builds a fresh
        # engine per cycle and hands it nothing but bars
        return max(int(p.trail_lookback_bars), (int(p.atr_days) + 3) * 288) + 10

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
        if timeframe != TIMEFRAME or candles is None or len(candles) < 60:
            return None
        if p.session not in SESSIONS:
            self.gate("session_known", False, f"unknown session {p.session!r}")
            return None

        ti = last_epoch(candles)
        open_ts, close_ts = session_bounds(p.session, ti)
        # the session's FIRST bar, and only that one
        if not self.gate("session_first_bar", open_ts <= ti < open_ts + BAR_SECONDS,
                         "not the opening bar of the cash session"):
            return None

        close = candles["close"].to_numpy(dtype=float)
        c = float(close[-1])
        ema = ema_last(close, int(p.ema_span))
        long = c > ema
        if not self.gate("side_allowed", p.side == "both" or (p.side == "long") == long,
                         f"{'long' if long else 'short'} signal, side={p.side}"):
            return None
        if not self.gate("off_the_ema", c != ema, "opening bar closed exactly on the EMA"):
            return None

        atr = self.daily_atr(symbol, candles)
        if not self.gate("daily_atr", atr is not None, f"{p.atr_days}-day ATR unavailable"):
            return None
        stop_dist = float(p.stop_atr_multiple) * float(atr)
        if not self.gate("stop_positive", stop_dist > 0):
            return None

        from backend.strategies.strategy_defaults import SLOT_TP1_RR, get_strategy_defaults
        rr = float(SLOT_TP1_RR.get(f"{symbol.upper()}|{self.strategy_id}",
                                   get_strategy_defaults(self.strategy_id).get("tp1_rr", 20.0)))
        return self._tag_signal(TradeSignal(
            strategy_id=self.strategy_id,
            symbol=symbol,
            direction="BUY" if long else "SELL",
            signal_type="OPENING_DRIVE",
            timeframe=TIMEFRAME,
            entry_price=c,
            stop_loss=c - stop_dist if long else c + stop_dist,
            take_profit=c + rr * stop_dist if long else c - rr * stop_dist,
            # one bar decides it; measured at full risk
            confluence_score=80,
            timestamp=float(ti),
            metadata={
                "size_modifier": 1.0,
                "trail_method": "NONE",
                "setup": "opening_drive",
                "session": p.session,
                "session_close_ts": close_ts,
                "ema_val": float(ema),
                "atr_val": float(atr),
                "tp1_rr": rr,
                "reason": (
                    f"opening M5 bar closed {'above' if long else 'below'} the "
                    f"{p.ema_span} EMA ({ema:.5f}); stop {p.stop_atr_multiple:g}x "
                    f"daily ATR, trailing {p.trail_atr_multiple:g}x, flat at the close"
                ),
            },
        ))

    def on_position_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame,
                        position: dict) -> TradeAction | None:
        """Flat at the cash close; otherwise pull the stop up to the chandelier
        level. Both backtesters and live ignore a level that would loosen the
        stop, which is the ratchet and keeps the initial stop as a floor."""
        p = self.params
        if candles is None or not len(candles) or p.session not in SESSIONS:
            return None
        last = last_epoch(candles)
        open_ts, close_ts = session_bounds(p.session, last)
        if p.close_at_session_end and (last + BAR_SECONDS >= close_ts or last < open_ts):
            # a bar outside the session means a close was crossed: this strategy
            # only ever enters on the session's first bar
            return TradeAction(ticket=int(position.get("ticket") or 0), action="CLOSE",
                               close_reason="SESSION_END")
        atr = self.daily_atr(symbol, candles)
        if atr is None:
            return None
        level = chandelier_level(candles, position.get("entry_time"),
                                 position.get("direction", "BUY"), float(atr),
                                 float(p.trail_atr_multiple), int(p.trail_lookback_bars))
        if level is None or not np.isfinite(level):
            return None
        return TradeAction(ticket=int(position.get("ticket") or 0), action="MODIFY_SL",
                           new_sl=float(level), close_reason="TRAIL")
