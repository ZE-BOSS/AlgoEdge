"""
backend/strategies/strategy_trend/engine.py

TrendBreakout_v1 — a Donchian breakout with a chandelier trailing stop.

THE RULE
--------
1. Channel = the highest high and lowest low of the previous `channel_days` days
   of bars, EXCLUDING the current bar.
2. A bar that CLOSES above the channel high is a buy; below the low, a sell.
3. Stop `stop_atr_multiple` x daily ATR from the entry. That is R, so it is also
   the position size.
4. No profit target. The position leaves on a chandelier trail:
   `trail_atr_multiple` x daily ATR back from the best close since entry,
   ratcheting, applied through `on_position_bar`.

WHY THIS ONE, AND WHAT IT IS NOT
--------------------------------
Every earlier screen in the 2026-09-25 session used a FIXED target, which is the
one exit a trend system cannot have: it makes its living in a handful of very
large winners, and capping them at 2R removes the whole right tail while keeping
every loser. Rebuilt with a trail, this was the only statistically significant
edge found on real CFDs.

Measured as the app runs it (close-beyond-channel signal, fill at the next bar's
open, real stops, costs charged — scripts/run_app_form_check.py), Deriv M15
2021-10 -> 2026-09, 10 markets:

    876 trades | +0.063R | 40.0% win | payoff 1.86 | t +2.17 | Sharpe 1.34
    $10,000 at 0.5% risk -> $13,262 (+32.6%), max drawdown 6.3% ($781)
    9 of 10 markets positive; XAUUSD +0.173R and USDJPY +0.122R carry it,
    US SP 500 -0.121R is the only negative one

Two things it is NOT:

  * It is NOT a way to pass a prop challenge in a month. +0.94 R/month at 40%
    wins is a slow grind; the FundedNext simulation put a 30-day pass at 3.2%
    with 1% risk and 8.2% breach. Its job is the funded account, where there is
    no clock.
  * It did NOT work on FundedNext's own bars over their available 16 months
    (170 trades, -0.092R, t -1.75, -11.6%). Part of that is a genuinely flat
    stretch for trend following and part is their wider spreads. Trend systems
    are judged in years, and 16 months is not one; but this is the most recent
    evidence and it is not encouraging.

Entry timing is the one liberty the research took that the engine cannot: it
entered AT the channel on a resting stop order. The app fills at the next bar's
open after a signal, so the break has to be a CLOSE beyond the channel. That
costs real edge and the cost scales with bar size — M15 +0.063R, H1 +0.052R,
D1 +0.026R, measured — which is why this runs on M15 rather than end-of-day.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from backend.strategies.base_strategy import BaseStrategy, TradeAction, TradeSignal
from backend.strategies.core.daily_atr import DailyATRCache, last_epoch
from backend.strategies.core.trail import chandelier_level
from backend.strategies.core.vol_regime import VolRegimeGate
from backend.strategies.registry import register_strategy
from backend.strategies.strategy_trend.params import TrendBreakoutParams
from backend.utils.logger import get_logger

logger = get_logger(__name__)

BARS_PER_DAY = 96          # M15
TIMEFRAME = "M15"


@register_strategy("TrendBreakout_v1")
class TrendBreakoutStrategy(VolRegimeGate, DailyATRCache, BaseStrategy):
    strategy_id = "TrendBreakout_v1"
    # the trail IS the exit — without this, a live position would only ever
    # leave at the initial stop or the placeholder target
    LIVE_POSITION_EXITS = True
    TIMEFRAME = TIMEFRAME

    def __init__(self, config: Any):
        super().__init__(config)
        self.params = getattr(config, "trend_breakout", None) or TrendBreakoutParams()

    def get_required_timeframes(self) -> list[str]:
        return [TIMEFRAME]

    @property
    def WINDOW_BARS(self) -> dict[str, int]:  # noqa: N802 — read by strategies.windows
        p = self.params
        # the channel needs `channel_days` of bars before the current one; the
        # daily ATR needs `atr_days` complete days plus the part-day the rolling
        # window starts on and the day in progress
        need = max(int(p.channel_days), int(p.atr_days) + 3) * BARS_PER_DAY
        # the volatility filter needs 60 days of its own; it costs nothing while
        # it is off, which is the default
        need = max(need, self.vol_regime_bars_needed(BARS_PER_DAY))
        return {TIMEFRAME: need + BARS_PER_DAY}

    @property
    def POSITION_BAR_WINDOW(self) -> int:  # noqa: N802 — backtesters and live
        p = self.params
        # on_position_bar recomputes the daily ATR itself: live builds a fresh
        # engine per cycle and hands it nothing but bars, so it cannot inherit
        # anything on_bar worked out
        return max(int(p.trail_lookback_bars), (int(p.atr_days) + 3) * BARS_PER_DAY) + 10

    async def initialize(self):
        return None

    async def on_tick(self, symbol: str, tick: dict[str, Any]) -> None:
        return None

    async def on_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame) -> TradeSignal | None:
        self.begin_candidate(
            symbol, timeframe,
            bar_time=candles.index[-1] if candles is not None and len(candles) else None,
        )
        if timeframe != TIMEFRAME or candles is None:
            return None
        p = self.params
        window = int(p.channel_days) * BARS_PER_DAY
        if len(candles) < window + 2:
            self.gate("channel_history", False, f"need {window + 2} bars, have {len(candles)}")
            return None

        high = candles["high"].to_numpy(dtype=float)
        low = candles["low"].to_numpy(dtype=float)
        close = candles["close"].to_numpy(dtype=float)
        # the channel the current bar has to beat excludes the current bar
        ch_high = float(high[-(window + 1):-1].max())
        ch_low = float(low[-(window + 1):-1].min())
        c = float(close[-1])

        atr = self.daily_atr(symbol, candles)
        if not self.gate("daily_atr", atr is not None, f"{p.atr_days}-day ATR unavailable"):
            return None

        want_long = p.side in ("both", "long")
        want_short = p.side in ("both", "short")
        long = bool(want_long and c > ch_high)
        short = bool(want_short and c < ch_low)
        if not self.gate("closed_beyond_channel", long != short,
                         f"close {c:.5f} inside the {p.channel_days}-day channel "
                         f"[{ch_low:.5f}, {ch_high:.5f}]"):
            return None

        stop_dist = float(p.stop_atr_multiple) * float(atr)
        if not self.gate("stop_positive", stop_dist > 0):
            return None

        # Last, so the gate breakdown shows how many real breakouts the regime
        # filter turned away rather than hiding them behind it.
        allowed, why = self.vol_regime_ok(candles)
        if not self.gate("vol_regime", allowed, why):
            return None

        from backend.strategies.strategy_defaults import SLOT_TP1_RR, get_strategy_defaults
        rr = float(SLOT_TP1_RR.get(f"{symbol.upper()}|{self.strategy_id}",
                                   get_strategy_defaults(self.strategy_id).get("tp1_rr", 20.0)))
        ti = last_epoch(candles)
        return self._tag_signal(TradeSignal(
            strategy_id=self.strategy_id,
            symbol=symbol,
            direction="BUY" if long else "SELL",
            signal_type="TREND_BREAKOUT",
            timeframe=TIMEFRAME,
            entry_price=c,
            entry_zone_top=ch_high,
            entry_zone_bottom=ch_low,
            stop_loss=c - stop_dist if long else c + stop_dist,
            take_profit=c + rr * stop_dist if long else c - rr * stop_dist,
            # a binary rule, measured at full risk — the top confluence tier
            confluence_score=80,
            timestamp=float(ti),
            metadata={
                "size_modifier": 1.0,
                "trail_method": "NONE",       # the strategy trails, not trailing_manager
                "setup": "donchian_trend",
                "channel_high": ch_high,
                "channel_low": ch_low,
                "channel_days": int(p.channel_days),
                "atr_val": float(atr),
                "tp1_rr": rr,
                "reason": (
                    f"close {'above' if long else 'below'} the {p.channel_days}-day "
                    f"{'high' if long else 'low'} "
                    f"({ch_high if long else ch_low:.5f}); stop "
                    f"{p.stop_atr_multiple:g}x daily ATR, trailing "
                    f"{p.trail_atr_multiple:g}x, no target"
                ),
            },
        ))

    def on_position_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame,
                        position: dict) -> TradeAction | None:
        """Move the stop up to the chandelier level. Both backtesters and live
        ignore a level that would loosen the stop, which is the ratchet and also
        what keeps the initial stop as a floor."""
        p = self.params
        if candles is None or len(candles) < 2:
            return None
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
