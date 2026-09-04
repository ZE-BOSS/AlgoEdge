"""
backend/strategies/strategy_synth/engine.py

Three synthetic-index strategies that share one entry/exit skeleton:

    SpikeFade_v1       — a bar moves >= k x ATR in one direction; enter AGAINST it.
    RangeRevert_v1     — price is >= k x ATR from the slow EMA; enter back toward it.
    RangeBreakout_v1   — close breaks the prior N-bar high/low; enter WITH it.

They live in one module rather than three packages because the only thing that
differs between them is the entry predicate — stop placement, target, daily caps
and signal construction are identical, and triplicating that was the larger risk.
Each is registered separately, so from the app's point of view they are three
ordinary strategies.

PARAMETERS, and where the shipped values come from
--------------------------------------------------
Every default here was selected by `research/data/run_strategy_search.py`, a
60-configuration grid per symbol over 1 Jan 2026 -> 4 Sep 2026, executed on raw
ticks with market-order stop fills and limit-order target fills. Per-symbol
overrides live in `strategy_defaults.py::SYNTH_SLOT_PARAMS`.

Stops are deliberately WIDE (>= 2.5 x ATR, usually 5 x). On jump instruments the
stop is what the spike gaps through, and research/24 §4.1 measured the unmodelled
slippage at ~1 R per trade at 0.5 x ATR against ~0.2 R at 5 x ATR. A tight stop on
these symbols is not a risk control, it is a way to convert a spike into a
four-fold loss.

WHAT THESE ARE NOT
------------------
research/24 measured every one of these instruments to be a fair martingale with
memoryless jump arrival, so none of these strategies has a demonstrated
statistical edge. They are configurations that performed best in a specific
eight-month window and are being forward-tested on that basis. Size accordingly.
"""

from typing import Any

import numpy as np
import pandas as pd

from backend.strategies.base_strategy import BaseStrategy, TradeSignal
from backend.strategies.registry import register_strategy
from backend.strategies.strategy_two.engine import calculate_adx, calculate_atr
from backend.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULTS = {
    "atr_period": 14,
    "ema_fast": 20,
    "ema_slow": 50,
    "breakout_lookback": 20,
    "max_trades_per_day": 6,
    "max_daily_risk_pct": 4.0,
}


class _SynthBase(BaseStrategy):
    """Shared skeleton. Subclasses implement `signal_for_bar` only."""

    strategy_id = "SynthBase"
    default_stop_atr = 5.0
    default_tp_rr = 1.5
    default_k = 3.0

    def __init__(self, config: Any):
        super().__init__(config)
        # BaseStrategy does not populate `params`; each strategy binds its own
        # config section (strategy_apa does `self.params = config.apa`).
        self.params = getattr(config, "synth", None)
        self.trades_today = 0
        self.daily_risk_used_pct = 0.0
        self.last_reset_date = None

    def get_required_timeframes(self) -> list[str]:
        return ["M5"]

    async def initialize(self):
        return None

    # -- parameter access ------------------------------------------------
    def _p(self, name: str, fallback):
        return getattr(self.params, name, fallback) if self.params else fallback

    def _frame(self, candles: pd.DataFrame) -> pd.DataFrame:
        d = candles.copy()
        d["ema_f"] = d["close"].ewm(span=self._p("ema_fast", DEFAULTS["ema_fast"]),
                                    adjust=False).mean()
        d["ema_s"] = d["close"].ewm(span=self._p("ema_slow", DEFAULTS["ema_slow"]),
                                    adjust=False).mean()
        d["atr"] = calculate_atr(d, DEFAULTS["atr_period"])
        d["adx"] = calculate_adx(d, 14)
        return d

    # -- subclasses override ---------------------------------------------
    def signal_for_bar(self, d: pd.DataFrame) -> int:
        """Return +1 long, -1 short, 0 none, for the LAST bar of `d`."""
        raise NotImplementedError

    # -- the shared machinery --------------------------------------------
    async def on_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame
                     ) -> TradeSignal | None:
        self.begin_candidate(
            symbol, timeframe,
            bar_time=candles.index[-1] if candles is not None and len(candles) else None,
        )
        if timeframe != "M5":
            return None
        lookback = self._p("breakout_lookback", DEFAULTS["breakout_lookback"])
        if len(candles) < max(60, lookback + 5):
            return None

        bar_date = candles.index[-1].date()
        if self.last_reset_date != bar_date:
            self.trades_today = 0
            self.daily_risk_used_pct = 0.0
            self.last_reset_date = bar_date

        max_trades = self._p("max_trades_per_day", DEFAULTS["max_trades_per_day"])
        if not self.gate("daily_trade_cap", self.trades_today < max_trades):
            return None
        risk_pct = getattr(getattr(self.config, "risk", None), "risk_per_trade_pct", 1.0)
        max_daily = self._p("max_daily_risk_pct", DEFAULTS["max_daily_risk_pct"])
        if not self.gate("daily_risk_cap",
                         self.daily_risk_used_pct + risk_pct <= max_daily):
            return None

        d = self._frame(candles)
        atr_val = float(d["atr"].iloc[-1]) if pd.notna(d["atr"].iloc[-1]) else 0.0
        if not self.gate("atr_valid", atr_val > 0):
            return None

        direction = self.signal_for_bar(d)
        if not self.gate("entry_predicate", direction != 0):
            return None

        entry = float(d["close"].iloc[-1])
        stop_atr = float(self._p("stop_atr_multiple", self.default_stop_atr))
        tp_rr = float(self._p("tp1_rr", self.default_tp_rr))
        risk = stop_atr * atr_val
        if not self.gate("risk_positive", risk > 0):
            return None

        long = direction > 0
        sl = entry - risk if long else entry + risk
        tp = entry + tp_rr * risk if long else entry - tp_rr * risk

        self.trades_today += 1
        self.daily_risk_used_pct += risk_pct
        return self._tag_signal(TradeSignal(
            strategy_id=self.strategy_id,
            symbol=symbol,
            direction="BUY" if long else "SELL",
            signal_type=self.signal_type,
            entry_price=entry, stop_loss=sl, take_profit=tp,
            confluence_score=70, timeframe=timeframe,
            timestamp=candles.index[-1].timestamp(),
            metadata={
                "size_modifier": 1.0,
                "trail_method": "NONE",
                "atr_val": atr_val,
                "stop_atr_multiple": stop_atr,
                "tp1_rr": tp_rr,
                "setup": self.signal_type.lower(),
                "reason": f"{self.strategy_id} {'BUY' if long else 'SELL'}",
            },
        ))

    async def on_tick(self, symbol: str, tick: dict[str, Any]) -> None:
        return None


@register_strategy("SpikeFade_v1")
class SpikeFadeStrategy(_SynthBase):
    """Fade a spike: a bar moving >= k x ATR one way, entered the other way.

    On Boom the spike is UP and this goes short; on Crash it is DOWN and this goes
    long. The instrument decides the direction, so one strategy covers both.
    """

    strategy_id = "SpikeFade_v1"
    signal_type = "SPIKE_FADE"
    default_stop_atr = 5.0
    default_tp_rr = 1.5
    default_k = 3.0

    def signal_for_bar(self, d: pd.DataFrame) -> int:
        bar = d.iloc[-1]
        atr_val = float(bar["atr"])
        k = float(self._p("spike_k_atr", self.default_k))
        up = (float(bar["high"]) - float(bar["open"])) / atr_val
        dn = (float(bar["open"]) - float(bar["low"])) / atr_val
        if up >= k and up >= dn:
            return -1
        if dn >= k:
            return 1
        return 0


@register_strategy("RangeRevert_v1")
class RangeRevertStrategy(_SynthBase):
    """Enter back toward the slow EMA when price is stretched k x ATR from it."""

    strategy_id = "RangeRevert_v1"
    signal_type = "RANGE_REVERT"
    default_stop_atr = 5.0
    default_tp_rr = 1.5
    default_k = 2.0

    def signal_for_bar(self, d: pd.DataFrame) -> int:
        bar = d.iloc[-1]
        atr_val = float(bar["atr"])
        k = float(self._p("revert_k_atr", self.default_k))
        gap = float(bar["close"]) - float(bar["ema_s"])
        if gap > k * atr_val:
            return -1
        if -gap > k * atr_val:
            return 1
        return 0


@register_strategy("RangeBreakout_v1")
class RangeBreakoutStrategy(_SynthBase):
    """Enter with a close beyond the prior N-bar high or low."""

    strategy_id = "RangeBreakout_v1"
    signal_type = "RANGE_BREAKOUT"
    default_stop_atr = 5.0
    default_tp_rr = 1.5

    def signal_for_bar(self, d: pd.DataFrame) -> int:
        n = int(self._p("breakout_lookback", DEFAULTS["breakout_lookback"]))
        if len(d) < n + 2:
            return 0
        prior = d.iloc[-(n + 1):-1]
        close = float(d["close"].iloc[-1])
        if close > float(prior["high"].max()):
            return 1
        if close < float(prior["low"].min()):
            return -1
        return 0


@register_strategy("TrendDrift_v1")
class TrendDriftStrategy(_SynthBase):
    """EMA-regime continuation, entered on a pullback to the fast EMA.

    This is the `drift` template from `research/data/run_strategy_search.py`, and
    it exists as its own strategy for a specific reason: the search found the
    drift template best on Crash 1000, Volatility 75 and Jump 100, but
    DriftJumpAlpha_v1 hard-filters to CRASH symbols and BoomDriftJump_v1 to BOOM
    symbols, so neither could ever fire on Volatility or Jump. Shipping the
    measured logic under its own name keeps the backtest and the live strategy the
    same code, which is the only way the reported numbers are reproducible.

    Direction follows the regime, so it is long on an up-trending instrument and
    short on a down-trending one without any per-symbol configuration.
    """

    strategy_id = "TrendDrift_v1"
    signal_type = "TREND_DRIFT"
    default_stop_atr = 5.0
    default_tp_rr = 5.0

    def signal_for_bar(self, d: pd.DataFrame) -> int:
        bar = d.iloc[-1]
        atr_val = float(bar["atr"])
        ef, es = float(bar["ema_f"]), float(bar["ema_s"])
        sep = abs(ef - es)
        if sep <= 0.2 * atr_val:
            return 0
        if self._p("require_adx", True):
            adx = float(bar["adx"]) if pd.notna(bar["adx"]) else 0.0
            if adx < float(self._p("min_adx_to_trade", 20)):
                return 0
        close = float(bar["close"])
        if ef > es and close >= ef:
            return 1
        if ef < es and close <= ef:
            return -1
        return 0
