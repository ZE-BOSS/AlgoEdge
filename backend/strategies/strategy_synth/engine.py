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
from backend.strategies.core.max_hold import MaxHoldExit
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


class _SynthBase(MaxHoldExit, BaseStrategy):
    """Shared skeleton. Subclasses implement `signal_for_bar` only."""

    strategy_id = "SynthBase"
    default_stop_atr = 5.0
    default_tp_rr = 1.5
    default_k = 3.0

    #: Which SynthParams field supplies this strategy's stop / target. All four
    #: original templates share `stop_atr_multiple` and `tp1_rr` — that is what
    #: the Settings page means by "a change here applies to all of them". A
    #: strategy whose geometry is genuinely different (SpikeRide: tight stop
    #: against the grind, target sized to the spike) names its own fields here
    #: instead of being silently forced onto the shared ones.
    stop_param_name = "stop_atr_multiple"
    tp_param_name = "tp1_rr"

    def __init__(self, config: Any):
        super().__init__(config)
        # BaseStrategy does not populate `params`; each strategy binds its own
        # config section (strategy_apa does `self.params = config.apa`).
        self.params = getattr(config, "synth", None)
        self.trades_today = 0
        self.daily_risk_used_pct = 0.0
        self.last_reset_date = None
        # Bar time of the last signal this engine emitted. The daily counters
        # below are a per-BAR budget, and on_bar() is called once per bar by the
        # backtester but once per SCAN CYCLE by the live bot — which re-feeds the
        # same closed bar every cycle until the next one closes. Without this,
        # one setup on a 5-minute bar burned the whole day's budget five times
        # over on a 60-second scan interval, and live took a fraction of the
        # backtest's trades. See _consume_daily_budget().
        self._last_emitted_bar = None

    def get_required_timeframes(self) -> list[str]:
        return ["M5"]

    async def initialize(self):
        return None

    # -- parameter access ------------------------------------------------
    def _p(self, name: str, fallback):
        return getattr(self.params, name, fallback) if self.params else fallback

    @property
    def WINDOW_BARS(self) -> dict[str, int]:  # noqa: N802 — read by strategies.windows
        # A 600-bar EMA needs a long window to be the EMA the study measured
        # (seed weight e^-10 at 3000 bars).
        return {"M5": 3000} if self._p("require_htf_trend", False) else {}

    def _confluences(self, d: pd.DataFrame, direction: int) -> tuple[bool, str]:
        """The optional confluences, exactly as synth_research.features computes them."""
        bar = d.iloc[-1]
        o, h, lo, c = float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"])
        side = str(self._p("side", "both")).lower()
        if side == "long" and direction < 0:
            return False, "side: long only"
        if side == "short" and direction > 0:
            return False, "side: short only"
        if self._p("require_trend_with", False) and not direction * (float(bar["ema_f"]) - float(bar["ema_s"])) > 0:
            return False, "trend_with"
        if self._p("require_htf_trend", False):
            eh = d["close"].ewm(span=600, adjust=False).mean().to_numpy()
            j = eh.size - 1
            if not (j >= 12 and direction * (c - eh[j]) > 0 and direction * (eh[j] - eh[j - 12]) > 0):
                return False, "htf_trend"
        adx_mode = str(self._p("adx_filter", "OFF")).upper()
        if adx_mode != "OFF":
            adx_v = float(bar["adx"]) if pd.notna(bar["adx"]) else 0.0
            if adx_mode == "TREND" and adx_v < 20:
                return False, "adx_trend"
            if adx_mode == "RANGE" and adx_v >= 20:
                return False, "adx_range"
        if self._p("require_vol_high", False):
            atr_hist = d["atr"].to_numpy()[-288:]
            if atr_hist.size < 288 or np.isnan(atr_hist).any() or float(bar["atr"]) < float(np.median(atr_hist)):
                return False, "vol_high"
        if self._p("require_candle_confirm", False) and not direction * (c - o) > 0:
            return False, "candle_confirm"
        if self._p("require_strong_close", False):
            rng = max(h - lo, 1e-12)
            if not (((c - lo) / rng if direction > 0 else (h - c) / rng) >= 0.7):
                return False, "strong_close"
        tf_ = str(self._p("time_filter", "ALL")).upper()
        if tf_ != "ALL":
            secs = int(pd.Timestamp(d.index[-1]).timestamp()) % 86400
            if tf_ == "LONDON" and not (7 * 3600 <= secs < 16 * 3600):
                return False, "london"
            if tf_ == "NEWYORK" and not (12 * 3600 + 1800 <= secs < 21 * 3600):
                return False, "newyork"
        return True, ""

    def _frame(self, candles: pd.DataFrame) -> pd.DataFrame:
        d = candles.copy()
        d["ema_f"] = d["close"].ewm(span=self._p("ema_fast", DEFAULTS["ema_fast"]),
                                    adjust=False).mean()
        d["ema_s"] = d["close"].ewm(span=self._p("ema_slow", DEFAULTS["ema_slow"]),
                                    adjust=False).mean()
        d["atr"] = calculate_atr(d, DEFAULTS["atr_period"])
        d["adx"] = calculate_adx(d, 14)
        return d

    # -- daily budget ------------------------------------------------------
    def _consume_daily_budget(self, bar_time, risk_pct: float) -> None:
        """Charge one trade against today's budget, ONCE per bar.

        `on_bar` is called at a different rate by the two code paths:

            backtester/engine.py   once per closed bar
            bot_service._scan_loop once every `scan_interval` seconds, always
                                   with the full closed history

        The live loop therefore hands the SAME final bar to this engine
        repeatedly — 5 times on a 60-second scan interval against an M5 bar.
        Charging the budget on every call meant a single setup consumed five
        trades' worth of the daily allowance, so live silently stopped trading
        long before the backtest did on identical data.

        Keying on the bar makes the charge idempotent: re-evaluating a bar the
        engine has already signalled on costs nothing, so both paths spend the
        budget at exactly the same rate.
        """
        if self._last_emitted_bar == bar_time:
            return
        self._last_emitted_bar = bar_time
        self.trades_today += 1
        self.daily_risk_used_pct += risk_pct

    def _budget_available(self, bar_time, risk_pct: float) -> tuple[bool, str]:
        """Is there room for another trade today? (allowed, reason-if-not).

        A bar already charged still counts as available, so a re-scan of that
        bar reaches the same verdict it did the first time instead of being
        rejected by the budget it itself consumed.
        """
        already = self._last_emitted_bar == bar_time
        max_trades = self._p("max_trades_per_day", DEFAULTS["max_trades_per_day"])
        used_trades = self.trades_today - (1 if already else 0)
        if used_trades >= max_trades:
            return False, f"daily trade cap reached ({used_trades}/{max_trades})"
        max_daily = self._p("max_daily_risk_pct", DEFAULTS["max_daily_risk_pct"])
        used_risk = self.daily_risk_used_pct - (risk_pct if already else 0.0)
        if used_risk + risk_pct > max_daily:
            return False, (
                f"daily risk cap reached ({used_risk:.1f}% used + {risk_pct:.1f}% "
                f"> {max_daily:.1f}% allowed)"
            )
        return True, ""

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

        risk_pct = getattr(getattr(self.config, "risk", None), "risk_per_trade_pct", 1.0)
        _ok, _why = self._budget_available(candles.index[-1], risk_pct)
        # Surfaced rather than returning a bare None: hitting the daily budget
        # is the single most likely reason this strategy stops trading part-way
        # through a session, and it used to be completely invisible — no log
        # line, no signal row, no alert.
        if not self.gate("daily_budget", _ok, _why):
            self._log_budget_block(symbol, _why)
            return None

        d = self._frame(candles)
        atr_val = float(d["atr"].iloc[-1]) if pd.notna(d["atr"].iloc[-1]) else 0.0
        if not self.gate("atr_valid", atr_val > 0):
            return None

        direction = self.signal_for_bar(d)
        if not self.gate("entry_predicate", direction != 0):
            return None
        _cf_ok, _cf_why = self._confluences(d, direction)
        if not self.gate("confluences", _cf_ok, _cf_why):
            return None

        entry = float(d["close"].iloc[-1])
        stop_atr = float(self._p(self.stop_param_name, self.default_stop_atr))
        tp_rr = float(self._p(self.tp_param_name, self.default_tp_rr))
        risk = stop_atr * atr_val
        if not self.gate("risk_positive", risk > 0):
            return None

        long = direction > 0
        sl = entry - risk if long else entry + risk
        tp = entry + tp_rr * risk if long else entry - tp_rr * risk

        self._consume_daily_budget(candles.index[-1], risk_pct)
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

    def _log_budget_block(self, symbol: str, reason: str) -> None:
        """Say once per symbol per day that the budget stopped trading."""
        key = (symbol, self.last_reset_date)
        if getattr(self, "_budget_logged", None) == key:
            return
        self._budget_logged = key
        msg = f"{self.strategy_id} on {symbol}: no more entries today — {reason}"
        logger.info(f"[SYNTH] {msg}")
        try:
            from backend.services.bot_service import bot_service
            bot_service.log_system_event(msg, "WARN", "RISK")
        except Exception:
            pass

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
