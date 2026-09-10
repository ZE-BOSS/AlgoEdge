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


@register_strategy("SpikeRide_v1")
class SpikeRideStrategy(_SynthBase):
    """Trade WITH the instrument's spike direction — the other side of SpikeFade.

    On Crash this is the SELL side; on Boom the BUY side. Those are the sides the
    book has never traded, and there is a specific, mechanical reason they are
    worth measuring rather than assumed to be the mirror image of the fade.

    WHY THIS IS NOT JUST "SPIKEFADE WITH THE SIGN FLIPPED"
    -----------------------------------------------------
    The two sides have opposite exposure to the one thing the backtester models
    worst. A jump on these instruments is intrabar and it gaps:

        SpikeFade (Crash BUY)  — the spike runs INTO the stop. The gap is a loss,
                                 and until fill_model.py shipped the harness
                                 booked every one of them at exactly the stop
                                 price. research/27 §1.2 measured the unbooked
                                 cost at 0.35-0.40 R per stopped trade, against a
                                 measured edge of +0.12 R.
        SpikeRide (Crash SELL) — the spike runs into the TARGET. A limit order
                                 fills at its price or better, so this side has
                                 no equivalent hidden cost, and a target the bar
                                 gaps through fills at the gapped open.

    So the harness systematically flattered one side and not the other. Whether
    the ranking between them survives a correct fill model is an open, testable
    question, and it is the question this strategy exists to answer.

    WHAT THIS IS NOT
    ----------------
    It is NOT a timing edge, and it must not be sold as one. research/24 §3.1
    measured jump arrival on 31,394 Crash 1000 events to be memoryless — P(jump
    in the next 251 ticks) is flat at 0.217-0.226 whether you have waited 0 or
    2,008 ticks — and magnitude is uncorrelated with elapsed time (-0.0059,
    SE 0.0056). No entry rule based on tick count, bar count or "a drop is due"
    can work. The stretch trigger below is a GEOMETRY choice (where to put the
    stop relative to a grind that has already run), not a forecast.

    GEOMETRY
    --------
    Deliberately different defaults from the fade, because the payoffs are not
    symmetric. The stop is tight and sits against the grind; the target is sized
    to the measured spike, which research/24 §3.1 puts at 0.0999-0.1005% of price
    on average and 0.2264-0.2286% at p95 — roughly 2x and 4.5x M5 ATR on the
    1000-variants. A 5x ATR stop against a 2x ATR target would be a 1:0.4
    proposition, which is why `default_stop_atr` is 1.0 here and 5.0 there.

    DIRECTION
    ---------
    Taken from the sign of return skew over the visible window, not from the
    symbol name. Crash grinds up and drops hard (negative skew) so this sells;
    Boom grinds down and pops (positive skew) so it buys. Measuring it means the
    strategy works on any instrument with a jump asymmetry without a lookup
    table that silently goes stale when a broker renames a symbol.
    """

    strategy_id = "SpikeRide_v1"
    signal_type = "SPIKE_RIDE"
    default_stop_atr = 1.0
    default_tp_rr = 2.0
    stop_param_name = "spike_ride_stop_atr"
    tp_param_name = "spike_ride_tp_rr"

    #: |skew| below this is treated as "no measurable jump asymmetry" — the
    #: instrument is symmetric and there is no spike side to take.
    min_abs_skew = 0.15

    def signal_for_bar(self, d: pd.DataFrame) -> int:
        stretch_k = float(self._p("spike_ride_stretch_atr", 1.5))

        bar = d.iloc[-1]
        atr_val = float(bar["atr"])
        if atr_val <= 0:
            return 0

        rets = d["close"].pct_change().dropna()
        if len(rets) < 60:
            return 0
        skew = float(rets.skew())
        if not np.isfinite(skew) or abs(skew) < self.min_abs_skew:
            return 0

        # Negative skew => rare violent DOWN moves => the spike side is short.
        spike_dir = -1 if skew < 0 else 1

        # Enter only once the grind has run against the spike side, so the stop
        # sits behind an extension rather than in the middle of one. This is
        # about stop placement, not about the spike being "due" (see above).
        gap_atr = (float(bar["close"]) - float(bar["ema_s"])) / atr_val
        if spike_dir < 0 and gap_atr < stretch_k:
            return 0
        if spike_dir > 0 and gap_atr > -stretch_k:
            return 0

        # Never enter into a bar that has ALREADY spiked our way — that move is
        # the one we were trying to capture and entering after it is chasing.
        spent = ((float(bar["open"]) - float(bar["low"])) / atr_val if spike_dir < 0
                 else (float(bar["high"]) - float(bar["open"])) / atr_val)
        if spent >= float(self._p("spike_k_atr", 3.0)):
            return 0

        return spike_dir


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
