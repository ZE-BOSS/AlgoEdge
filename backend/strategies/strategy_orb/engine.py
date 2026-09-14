"""
backend/strategies/strategy_orb/engine.py

ORB_v1 — opening-range breakout.

THE RULE
--------
1. Mark the high and low of the first `range_minutes` after the session open
   (London 08:00 UK time, or New York 09:30 ET — DST-aware).
2. The first M15 bar inside the next `breakout_window_minutes` that CLOSES beyond
   the range is the signal: above the high -> buy, below the low -> sell. One
   trade per session; a later break the other way is ignored.
3. Stop at the far side of the range (never tighter than `min_stop_atr` x ATR).
   Target = the slot's tp1_rr. Flatten at the session close.

WHY THIS ONE (measured 2026-09-11, data/strategy_search/)
--------------------------------------------------------
Eight classic strategy families were searched on eight markets with settings
chosen ONLY on 2024-01 -> 2026-01 and reported unchanged over the following eight
months, then re-checked on 2022-09 -> 2024-01, which the search never saw. With
fixed R:R exits and the session close:

    GBPJPY  London 60m 1:3     2022-23 +0.05R | 2024-25 +0.08R | last 8m +0.21R, 8/9 months up
    BTCUSD  NY 60m 1:1.5       2022-23 +0.11R | 2024-25 +0.07R | last 8m +0.05R
    XAUUSD  NY 30m 1:2         2022-23 +0.08R | 2024-25 +0.02R | last 8m +0.04R

GBPJPY is the only one that is both consistent AND sizable on a $350 account at
1% risk (87% of its stops fit the broker's minimum lot); BTCUSD needs ~$1.5k and
XAUUSD far more. None of these clears a strict significance bar on its own —
they are the strongest candidates found, to be forward-tested, not proven alpha.

The live engine reproduces `analytics/strategy_search._orb` bar for bar;
tests/test_orb_strategy.py holds it to that.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
import pytz

from backend.strategies.base_strategy import BaseStrategy, TradeAction, TradeSignal
from backend.strategies.registry import register_strategy
from backend.strategies.strategy_orb.params import ORBParams
from backend.utils.logger import get_logger

logger = get_logger(__name__)

SESSIONS: dict[str, tuple[str, tuple[int, int], tuple[int, int]]] = {
    "london": ("Europe/London", (8, 0), (16, 30)),
    "ny": ("America/New_York", (9, 30), (16, 0)),
}
_TF_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600}


def session_bounds(session: str, ts: int) -> tuple[int, int]:
    """(open, close) epoch seconds of `session` on the local calendar day of `ts`."""
    tzname, (oh, om), (ch, cm) = SESSIONS[session]
    tz = pytz.timezone(tzname)
    day = datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone(tz).date()
    o = int(tz.localize(datetime(day.year, day.month, day.day, oh, om)).timestamp())
    c = int(tz.localize(datetime(day.year, day.month, day.day, ch, cm)).timestamp())
    return o, c


def _epoch_seconds(candles: pd.DataFrame) -> np.ndarray:
    if "time" in candles.columns:
        return candles["time"].to_numpy(dtype=np.int64)
    # A naive index is UTC (both the backtest route and the live loop build it
    # with pd.to_datetime(unit="s")). as_unit("s") keeps this right whatever unit
    # pandas stored the index in (asi8 is ns on pandas 2, us or s on pandas 3).
    return pd.DatetimeIndex(candles.index).as_unit("s").asi8


def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int = 14) -> np.ndarray:
    pc = np.r_[close[0], close[:-1]]
    tr = np.maximum(high - low, np.maximum(np.abs(high - pc), np.abs(low - pc)))
    return pd.Series(tr).rolling(n).mean().to_numpy()


@register_strategy("ORB_v1")
class ORBStrategy(BaseStrategy):
    strategy_id = "ORB_v1"

    def __init__(self, config: Any):
        super().__init__(config)
        self.params = getattr(config, "orb", None) or ORBParams()

    def get_required_timeframes(self) -> list[str]:
        return ["M5"] if self._m5 else ["M15"]

    @property
    def _m5(self) -> bool:
        return str(getattr(self.params, "breakout_timeframe", "M15")).upper() == "M5"

    @property
    def WINDOW_BARS(self) -> dict[str, int]:  # noqa: N802 — read by strategies.windows
        # the trend EMA spans 600 M5 bars; 5,000 lets it forget its seed and
        # gives the stop discard its 14 prior sessions
        return {"M5": 5000} if self._m5 else {}

    def _m5_signal(self, symbol: str, candles: pd.DataFrame) -> TradeSignal | None:
        from backend.analytics import edge_lab as lab
        from backend.strategies.core.bars import candles_to_bars

        p = self.params
        t = _epoch_seconds(candles)
        ti = int(t[-1])
        open_ts, close_ts = session_bounds(p.session, ti)
        range_end = open_ts + int(p.range_minutes) * 60
        window_end = min(range_end + int(p.breakout_window_minutes) * 60, close_ts - 1800)
        # cheap pre-check before building the session context
        if not self.gate("in_breakout_window", range_end <= ti < window_end):
            return None
        b = candles_to_bars(symbol, "M5", candles, pad=False)
        ctx = lab.build_ctx(b, p.session, live=True)
        res = lab.live_signal("orb_break", ctx, ("htf_trend",) if p.require_trend else (),
                              mins=int(p.range_minutes), window_bars=int(p.breakout_window_minutes) // 5,
                              min_stop_atr=float(p.min_stop_atr))
        if not self.gate("first_breakout_passes", res is not None):
            return None
        d = int(res["direction"])
        if not self.gate("side_allowed", p.side == "both" or (p.side == "long") == (d > 0)):
            return None
        from backend.strategies.strategy_defaults import SLOT_TP1_RR, get_strategy_defaults
        rr = float(SLOT_TP1_RR.get(f"{symbol.upper()}|{self.strategy_id}",
                                   get_strategy_defaults(self.strategy_id).get("tp1_rr", 2.0)))
        c = float(b.close[-1])
        stop = float(res["stop_dist"])
        long = d > 0
        f = res["features"]
        return self._tag_signal(TradeSignal(
            strategy_id=self.strategy_id, symbol=symbol, direction="BUY" if long else "SELL",
            signal_type="ORB_BREAKOUT", timeframe="M5", entry_price=c,
            entry_zone_top=float(f["range_high"]), entry_zone_bottom=float(f["range_low"]),
            stop_loss=c - stop if long else c + stop, take_profit=c + rr * stop if long else c - rr * stop,
            confluence_score=80, timestamp=float(ti),  # binary rule, measured at full risk: top tier of confluence_risk_tiers
            metadata={"size_modifier": 1.0, "trail_method": "NONE", "setup": "orb_m5",
                      "session": p.session, "range_minutes": int(p.range_minutes),
                      "range_high": float(f["range_high"]), "range_low": float(f["range_low"]),
                      "session_close_ts": close_ts, "tp1_rr": rr, "trend_aligned": bool(f["htf_trend"]),
                      "reason": f"ORB M5 {p.session} {p.range_minutes}m range broke {'up' if long else 'down'}"
                                f"{' with the H1 trend' if p.require_trend else ''}"},
        ))

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
        if p.session not in SESSIONS or candles is None or len(candles) < 20:
            return None
        if self._m5:
            return self._m5_signal(symbol, candles) if timeframe == "M5" else None
        if timeframe != "M15":
            return None

        t = _epoch_seconds(candles)
        high = candles["high"].to_numpy(dtype=float)
        low = candles["low"].to_numpy(dtype=float)
        close = candles["close"].to_numpy(dtype=float)
        i = len(t) - 1
        ti = int(t[i])

        open_ts, close_ts = session_bounds(p.session, ti)
        range_end = open_ts + int(p.range_minutes) * 60
        window_end = range_end + int(p.breakout_window_minutes) * 60
        if not self.gate("in_breakout_window", range_end <= ti < window_end):
            return None

        r0 = int(np.searchsorted(t, open_ts, side="left"))
        r1 = int(np.searchsorted(t, range_end, side="left"))
        complete = r0 < len(t) and int(t[r0]) == open_ts and (r1 - r0) >= max(1, int(p.range_minutes) // 15)
        if not self.gate("range_complete", complete, "opening range incomplete (holiday or data gap)"):
            return None
        rh, rl = float(high[r0:r1].max()), float(low[r0:r1].min())
        if not self.gate("range_valid", rh > rl):
            return None

        want_long = p.side in ("both", "long")
        want_short = p.side in ("both", "short")
        # One trade per session: stateless, so a live re-scan of the same bar and
        # a backtest reach the same answer without sharing any engine state.
        for j in range(r1, i):
            if (want_long and close[j] > rh) or (want_short and close[j] < rl):
                self.gate("first_breakout_of_session", False, "session already broke out")
                return None

        c = float(close[i])
        direction = 1 if (want_long and c > rh) else (-1 if (want_short and c < rl) else 0)
        if not self.gate("breakout_close", direction != 0):
            return None

        atr_i = _atr(high, low, close)[i]
        atr_i = float(atr_i) if np.isfinite(atr_i) else 0.0
        floor = float(p.min_stop_atr) * atr_i
        stop = max(c - rl, floor) if direction > 0 else max(rh - c, floor)
        if not self.gate("stop_positive", stop > 0):
            return None

        from backend.strategies.strategy_defaults import SLOT_TP1_RR, get_strategy_defaults
        rr = float(SLOT_TP1_RR.get(f"{symbol.upper()}|{self.strategy_id}",
                                   get_strategy_defaults(self.strategy_id).get("tp1_rr", 2.0)))
        long = direction > 0
        return self._tag_signal(TradeSignal(
            strategy_id=self.strategy_id,
            symbol=symbol,
            direction="BUY" if long else "SELL",
            signal_type="ORB_BREAKOUT",
            timeframe=timeframe,
            entry_price=c,
            entry_zone_top=rh,
            entry_zone_bottom=rl,
            stop_loss=c - stop if long else c + stop,
            take_profit=c + rr * stop if long else c - rr * stop,
            # ORB has no graded confluence: the rule either fires or it does not,
            # and it was measured at full risk. 70 fell in the default 75% tier.
            confluence_score=80,
            timestamp=float(ti),
            metadata={
                "size_modifier": 1.0,
                "trail_method": "NONE",
                "setup": "orb",
                "session": p.session,
                "range_minutes": int(p.range_minutes),
                "range_high": rh,
                "range_low": rl,
                "session_close_ts": close_ts,
                "atr_val": atr_i,
                "tp1_rr": rr,
                "reason": f"ORB {p.session} {p.range_minutes}m range broke {'up' if long else 'down'}",
            },
        ))

    def on_position_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame,
                        position: dict) -> TradeAction | None:
        """Flatten at the session close — part of the tested rule, not an add-on."""
        if not self.params.close_at_session_end or candles is None or not len(candles):
            return None
        t = _epoch_seconds(candles)
        last = int(t[-1])
        dur = _TF_SECONDS.get(str(timeframe).upper()) or (int(t[-1] - t[-2]) if len(t) > 1 else 900)
        open_ts, close_ts = session_bounds(self.params.session, last)
        # A position still open on a bar outside the session crossed a close:
        # ORB only ever enters between the range end and the session close.
        if last + dur >= close_ts or last < open_ts:
            return TradeAction(ticket=int(position.get("ticket") or 0), action="CLOSE",
                               close_reason="SESSION_END")
        return None
