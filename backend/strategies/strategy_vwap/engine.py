"""
backend/strategies/strategy_vwap/engine.py

VWAP Drift Pullback Strategy Engine
=====================================
Implements Matteo Conti's "Golden Ticket VWAP" — fully rule-based.

Flow:
  Session gate → Bias (price vs VWAP) → VWAP slope → Momentum → Pullback trigger → Entry

Source: docs/vwap_strategy_implementation_plan.md
"""

import pandas as pd
import pytz
from datetime import date as date_type

from backend.core.config_schema import UserConfigV2
from backend.strategies.base_strategy import BaseStrategy, TradeSignal
from backend.strategies.core.swing_structure import calculate_atr
from backend.strategies.registry import register_strategy
from backend.utils.logger import get_logger

logger = get_logger(__name__)

# Synthetic indices — VWAP is explicitly not suitable for these
_SYNTHETIC_PATTERNS = (
    "volatility", "boom", "crash", "jump", "step index", "range break",
    "v10", "v25", "v50", "v75", "v100", "v150", "v250",
)


def _is_synthetic(symbol: str) -> bool:
    """Return True if the symbol is a Deriv synthetic (no institutional order flow)."""
    sl = symbol.lower()
    return any(p in sl for p in _SYNTHETIC_PATTERNS)


def _calculate_anchored_vwap(candles: pd.DataFrame, anchor_minutes: int) -> pd.Series:
    """
    Calculate anchored VWAP using a rolling anchor window of `anchor_minutes`.
    Uses typical price (H+L+C)/3 × Volume for the cumulative sum.
    If no Volume column, falls back to equal weighting.
    """
    typical_price = (candles["high"] + candles["low"] + candles["close"]) / 3

    if "volume" in candles.columns and candles["volume"].sum() > 0:
        tp_vol = typical_price * candles["volume"]
        vol = candles["volume"]
    else:
        tp_vol = typical_price
        vol = pd.Series(1.0, index=candles.index)

    # Rolling cumulative sums anchored to a fixed window
    cum_tp_vol = tp_vol.rolling(window=anchor_minutes, min_periods=1).sum()
    cum_vol = vol.rolling(window=anchor_minutes, min_periods=1).sum()

    vwap = cum_tp_vol / cum_vol
    return vwap


@register_strategy("VWAP_v1")
class VWAPEngine(BaseStrategy):
    """
    VWAP Drift Pullback strategy.
    Restricted to real markets; warns on synthetic indices.
    """

    def __init__(self, config: UserConfigV2):
        super().__init__(config)
        self.params = config.vwap
        self.state: dict = {}

    def _init_state(self, symbol: str):
        if symbol not in self.state:
            self.state[symbol] = {
                "trades_today": 0,
                "losses_today": 0,
                "last_trade_date": None,
                "trigger_bar_idx": None,  # Index of the pullback trigger candle
                "pending_entry": False,   # Entry on next bar open
                "entry_direction": None,
            }

    def _reset_daily(self, symbol: str, today: date_type):
        state = self.state[symbol]
        if state["last_trade_date"] != today:
            state["trades_today"] = 0
            state["losses_today"] = 0
            state["last_trade_date"] = today
            state["trigger_bar_idx"] = None
            state["pending_entry"] = False
            state["entry_direction"] = None

    def _et_time_str(self, ts: pd.Timestamp) -> str:
        """Convert timestamp to Eastern Time HH:MM string."""
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        et = ts.astimezone(pytz.timezone("America/New_York"))
        return et.strftime("%H:%M")

    def _is_in_exclusion(self, time_str: str) -> bool:
        """Return True if inside first-hour exclusion or after entry cutoff."""
        return (
            self.params.session_open <= time_str <= self.params.session_exclude_end
            or time_str >= self.params.entry_cutoff
        )

    def _is_hard_close_time(self, time_str: str) -> bool:
        return time_str >= self.params.hard_close

    def get_required_timeframes(self) -> list[str]:
        return [self.params.entry_timeframe]

    async def on_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame) -> TradeSignal | None:
        if timeframe != self.params.entry_timeframe:
            return None

        self._init_state(symbol)
        state = self.state[symbol]

        # Determine entry timeframe minutes (e.g., 'M5' -> 5)
        tf_str = self.params.entry_timeframe
        tf_mins = int(tf_str[1:]) if tf_str.startswith('M') else 5
        bar_multiplier = self.params.vwap_anchor_minutes // tf_mins
        actual_lookback = self.params.momentum_lookback_bars * bar_multiplier
        
        if len(candles) < actual_lookback + 5:
            return None

        current_time = candles.index[-1]
        today = current_time.date()
        self._reset_daily(symbol, today)

        # Synthetic check — warn but allow (user's choice)
        if _is_synthetic(symbol):
            logger.warning(
                f"[VWAP] {symbol} appears to be a synthetic index. "
                "VWAP is designed for real markets with institutional order flow. "
                "Results may be unreliable."
            )

        time_str = self._et_time_str(current_time)

        # Let the risk engine and backtester handle hard close via the metadata marker
        # if self._is_hard_close_time(time_str):
        #     return None

        # Guard rails
        if state["trades_today"] >= self.params.max_trades_per_day:
            return None
        if state["losses_today"] >= self.params.max_losses_per_day:
            return None

        latest = candles.iloc[-1]
        prev = candles.iloc[-2]

        # Calculate anchored VWAP
        vwap_series = _calculate_anchored_vwap(candles, self.params.vwap_anchor_minutes)
        vwap_now = vwap_series.iloc[-1]
        vwap_prev = vwap_series.iloc[-2] if len(vwap_series) > 1 else vwap_now

        # ── Entry from pending trigger (enter at next bar open after trigger) ──
        if state["pending_entry"] and state["trigger_bar_idx"] is not None:
            state["pending_entry"] = False
            direction = state["entry_direction"]
            state["entry_direction"] = None
            state["trigger_bar_idx"] = None

            if self._is_in_exclusion(time_str):
                return None

            entry = latest["open"]
            atr = calculate_atr(candles)
            if self.params.sl_atr_multiplier > 0:
                sl_dist = atr * self.params.sl_atr_multiplier
            else:
                sl_dist = self.params.sl_points

            if direction == "BUY":
                sl = entry - sl_dist
                tp = entry + sl_dist  # TP1 = 1R = 40pt = matches source's 40pt target
            else:
                sl = entry + sl_dist
                tp = entry - sl_dist

            state["trades_today"] += 1
            self.log_event(
                f"[{symbol}] VWAP {direction} ENTRY @ {entry:.5f} | SL: {sl:.5f} | TP1: {tp:.5f}",
                category="VWAP",
            )

            return TradeSignal(
                strategy_id="VWAP_v1",
                symbol=symbol,
                direction=direction,
                signal_type="VWAP_PULLBACK",
                timeframe=timeframe,
                entry_price=entry,
                stop_loss=sl,
                take_profit=tp,
                confluence_score=80,
                timestamp=float(latest.get("time", current_time.timestamp())),
                metadata={
                    "setup": "VWAP_PULLBACK",
                    "vwap": round(vwap_now, 5),
                    "slope": state.get("slope", 0.0),
                    "hard_close_time": self.params.hard_close,
                },
            )

        # ── Session exclusion check ──────────────────────────────────────
        if self._is_in_exclusion(time_str):
            return None

        # ── Bias: price vs VWAP ──────────────────────────────────────────
        price_above_vwap = latest["close"] > vwap_now
        price_below_vwap = latest["close"] < vwap_now

        # ── VWAP slope ───────────────────────────────────────────────────
        vwap_rising = vwap_now > vwap_prev
        vwap_falling = vwap_now < vwap_prev

        # ── Momentum (1hr price move) ─────────────────────────────────────
        # momentum_lookback_bars is defined in terms of anchor-timeframe bars (e.g. 15m)
        # We are on M5 candles, so we already computed actual_lookback above.
        lookback_close = candles["close"].iloc[-(actual_lookback + 1)]
        price_move_pct = (latest["close"] - lookback_close) / lookback_close * 100

        momentum_up = price_move_pct >= self.params.momentum_threshold_pct
        momentum_down = price_move_pct <= -self.params.momentum_threshold_pct

        # ── Check for pullback trigger candle ────────────────────────────
        # LONG: price above VWAP + VWAP rising + momentum up → look for a red (bearish) candle pulling back toward VWAP
        if price_above_vwap and vwap_rising and momentum_up:
            is_pullback_candle = latest["close"] < latest["open"]  # Red candle
            if is_pullback_candle:
                state["pending_entry"] = True
                state["trigger_bar_idx"] = current_time
                state["entry_direction"] = "BUY"
                self.log_event(
                    f"[{symbol}] VWAP LONG trigger: pullback candle detected. Entry next bar.",
                    category="VWAP",
                )

        # SHORT: price below VWAP + VWAP falling + momentum down → look for a green (bullish) candle pulling back toward VWAP
        elif price_below_vwap and vwap_falling and momentum_down:
            is_pullback_candle = latest["close"] > latest["open"]  # Green candle
            if is_pullback_candle:
                state["pending_entry"] = True
                state["trigger_bar_idx"] = current_time
                state["entry_direction"] = "SELL"
                self.log_event(
                    f"[{symbol}] VWAP SHORT trigger: pullback candle detected. Entry next bar.",
                    category="VWAP",
                )

        return None
