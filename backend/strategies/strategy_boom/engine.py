"""
backend/strategies/strategy_boom/engine.py

BoomDriftJump — the mirror of DriftJumpAlpha for Boom indices.

Boom is Crash reflected: 98.1% of ticks grind DOWN in tiny steps and 0.10% are
violent UP spikes, one per N ticks where N is the nameplate (research/20 §3.2,
measured to within 1% on 31.5 M ticks per symbol). So every direction in
DriftJumpAlpha inverts:

    Crash                              Boom
    ------------------------------     ------------------------------
    Setup A: BUY the upward grind      Setup A: SELL the downward grind
    Setup B: SELL after a down-spike   Setup B: BUY after an up-spike
    EMA fast > EMA slow                EMA fast < EMA slow
    break of swing LOW confirms        break of swing HIGH confirms

**Read this before trading it.** research/24 measured Boom to be a fair
arithmetic martingale with memoryless jump arrival (hazard flat to 0.2210 vs an
exponential 0.2211 across a 2,000-tick wait) and jump size uncorrelated with
anything observable. Under those conditions the optional stopping theorem gives
every stop/target strategy a gross expectancy of exactly zero, so this strategy's
expected edge before costs is zero and after costs is negative. It is implemented
because it was asked for and because the honest way to settle a disagreement is a
measurement, not an assertion — `research/data/phase5_boom_backtest.py` runs that
measurement on tick data with gap-aware fills.

The one thing that is NOT symmetric between Crash and Boom, and matters:
on Boom the up-spike gaps through a SHORT's stop, so Setup A carries the gap risk
here, where on Crash it was Setup B's side. `trail_method` is NONE and the stop is
deliberately wide for that reason.
"""

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from backend.strategies.base_strategy import BaseStrategy, TradeSignal
from backend.strategies.core.market_structure import MarketStructureDetector
from backend.strategies.registry import register_strategy
from backend.strategies.strategy_two.engine import calculate_adx, calculate_atr
from backend.utils.logger import get_logger

logger = get_logger(__name__)

SPEC_DEFAULTS = {
    "min_ema_separation_atr_multiple": 0.2,
    "atr_period": 14,
    "min_adx_to_trade": 20,
    "jump_entry_percentile_threshold": 95.0,
    "drift_ema_fast": 20,
    "drift_ema_slow": 50,
    "setup_b_buffer_atr_multiple": 0.2,
    "min_rrr_to_accept_trade": 1.5,
    "max_trades_per_day": 6,
    "max_daily_risk_pct": 4.0,
}


@register_strategy("BoomDriftJump_v1")
class BoomDriftJumpStrategy(BaseStrategy):
    """Drift continuation (SELL) + jump entry (BUY) for Boom indices."""

    def __init__(self, config: Any):
        super().__init__(config)
        self.strategy_id = "BoomDriftJump_v1"
        # BaseStrategy does not populate `params`; every strategy binds its own
        # config section here (strategy_apa does `self.params = config.apa`).
        self.params = getattr(config, "boom_drift_jump", None)
        self.ms = MarketStructureDetector()
        self.trades_today = 0
        self.daily_risk_used_pct = 0.0
        self.last_reset_date = None
        self.cooldown_until = None
        self.bars_since_jump = 0
        self.recent_gaps: list[float] = []

    def get_required_timeframes(self) -> list[str]:
        return ["M5"]

    async def initialize(self):
        return None

    def log_event(self, message: str, level: str = "INFO", category: str = "BOOM"):
        if getattr(self, "is_backtesting", False):
            return
        logger.log(level, f"[{category}] {message}")

    # ------------------------------------------------------------------
    def detect_jump(self, bar: pd.Series, atr_val: float) -> bool:
        """A Boom jump is a violent UP move — the mirror of Crash's down-spike."""
        if atr_val <= 0:
            return False
        up_move = float(bar["high"]) - float(bar["open"])
        return up_move >= 2.0 * atr_val and float(bar["close"]) > float(bar["open"])

    def compute_gap_percentile(self, up_move_atr: float) -> float:
        """Percentile rank of this bar's up-move within recent history."""
        self.recent_gaps.append(up_move_atr)
        if len(self.recent_gaps) > 500:
            self.recent_gaps.pop(0)
        if len(self.recent_gaps) < 30:
            return 0.0
        below = sum(1 for g in self.recent_gaps if g < up_move_atr)
        return below / len(self.recent_gaps) * 100.0

    # ------------------------------------------------------------------
    async def on_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame
                     ) -> TradeSignal | None:
        self.begin_candidate(
            symbol, timeframe,
            bar_time=candles.index[-1] if candles is not None and len(candles) else None,
        )
        if timeframe != "M5":
            return None
        if not self.gate("boom_symbol_only", "BOOM" in symbol.upper()):
            return None
        if len(candles) < 50:
            return None

        df = candles.copy()
        p = self.params
        fast = getattr(p, "drift_ema_fast", SPEC_DEFAULTS["drift_ema_fast"]) if p else 20
        slow = getattr(p, "drift_ema_slow", SPEC_DEFAULTS["drift_ema_slow"]) if p else 50
        min_adx = getattr(p, "min_adx_to_trade", SPEC_DEFAULTS["min_adx_to_trade"]) if p else 20
        jump_threshold = getattr(p, "jump_entry_percentile_threshold",
                                 SPEC_DEFAULTS["jump_entry_percentile_threshold"]) if p else 95.0
        trade_jumps = getattr(p, "trade_jumps_enabled", False) if p else False

        df["ema_fast"] = df["close"].ewm(span=fast, adjust=False).mean()
        df["ema_slow"] = df["close"].ewm(span=slow, adjust=False).mean()
        df["atr"] = calculate_atr(df, SPEC_DEFAULTS["atr_period"])
        df["adx"] = calculate_adx(df, 14)

        bar = df.iloc[-1]
        atr_val = float(bar["atr"]) if pd.notna(bar["atr"]) else 0.0
        adx_val = float(bar["adx"]) if pd.notna(bar["adx"]) else float("nan")
        if atr_val <= 0:
            return None

        # daily guardrails
        bar_date = df.index[-1].date()
        if self.last_reset_date != bar_date:
            self.trades_today = 0
            self.daily_risk_used_pct = 0.0
            self.last_reset_date = bar_date
        if self.cooldown_until is not None:
            if datetime.now(timezone.utc) < self.cooldown_until:
                return None
            self.cooldown_until = None
        max_trades = getattr(p, "max_trades_per_day", 6) if p else 6
        if not self.gate("daily_trade_cap", self.trades_today < max_trades):
            return None
        risk_pct = getattr(getattr(self.config, "risk", None), "risk_per_trade_pct", 1.0)
        max_daily = getattr(p, "max_daily_risk_pct", 4.0) if p else 4.0
        if not self.gate("daily_risk_cap", self.daily_risk_used_pct + risk_pct <= max_daily):
            return None

        up_move_atr = (float(bar["high"]) - float(bar["open"])) / atr_val
        gap_pct = self.compute_gap_percentile(up_move_atr)

        ms_state = self.ms.analyze(df) if hasattr(self.ms, "analyze") else {}
        swings = (ms_state or {}).get("swings", [])

        # ── SETUP B: JUMP ENTRY (BUY) — mirror of Crash's jump SELL ────────
        if trade_jumps and gap_pct >= jump_threshold and self.detect_jump(bar, atr_val):
            if swings and float(bar["close"]) > float(bar["open"]):
                highs = [s for s in swings
                         if s["type"] == "HIGH" and float(s["price"]) < float(bar["close"])]
                if highs:
                    entry = float(bar["close"])
                    lows = [s for s in swings
                            if s["type"] == "LOW" and float(s["price"]) < entry]
                    buffer = SPEC_DEFAULTS["setup_b_buffer_atr_multiple"] * atr_val
                    sl = (float(lows[-1]["price"]) - buffer if lows
                          else entry - atr_val * SPEC_DEFAULTS["setup_b_buffer_atr_multiple"])
                    risk = abs(entry - sl)
                    if risk <= 0:
                        return None
                    tp = entry + max(atr_val * 3, risk * 1.5)
                    rrr = abs(tp - entry) / risk
                    min_rrr = getattr(p, "min_rrr_to_accept_trade", 1.5) if p else 1.5
                    if not self.gate("min_rrr_jump", rrr >= min_rrr, detail=f"rrr={rrr:.2f}"):
                        return None
                    self.trades_today += 1
                    self.daily_risk_used_pct += risk_pct
                    return self._tag_signal(TradeSignal(
                        strategy_id=self.strategy_id, symbol=symbol,
                        direction="BUY", signal_type="JUMP_ENTRY",
                        entry_price=entry, stop_loss=sl, take_profit=tp,
                        confluence_score=95, timeframe=timeframe,
                        timestamp=df.index[-1].timestamp(),
                        metadata={"size_modifier": 1.0, "gap_pct": gap_pct,
                                  "trail_method": "NONE", "atr_val": atr_val,
                                  "setup": "setup_b_jump",
                                  "reason": f"Boom jump BUY. Gap: {gap_pct:.1f}%"},
                    ))

        # ── SETUP A: DRIFT CONTINUATION (SELL) — mirror of Crash's drift BUY ─
        if gap_pct >= jump_threshold:
            # Do not sell into a fresh up-spike; the mirror of DJA's block.
            return None

        ema_sep = abs(float(bar["ema_fast"]) - float(bar["ema_slow"]))
        min_sep = SPEC_DEFAULTS["min_ema_separation_atr_multiple"] * atr_val
        regime_active = float(bar["ema_fast"]) < float(bar["ema_slow"]) and ema_sep > min_sep

        size_modifier = 1.0
        if not pd.notna(adx_val) or adx_val < min_adx:
            mode = getattr(p, "adx_gate_mode", "REDUCED_SIZE") if p else "BLOCK"
            if mode == "BLOCK":
                regime_active = False
            else:
                recent = df["adx"].tail(100).dropna()
                pct = float((recent < adx_val).mean()) if len(recent) >= 10 and pd.notna(adx_val) else 0.0
                size_modifier = max(getattr(p, "adx_gate_min_size_modifier", 0.1) if p else 0.1, pct)

        if not self.gate("drift_regime_active", regime_active):
            return None
        # confirmation: price back at or below the fast EMA after a pullback up
        if not self.gate("drift_pullback", float(bar["close"]) <= float(bar["ema_fast"])):
            return None

        entry = float(bar["close"])
        # Stop ABOVE, which is the side Boom's up-spike gaps through. Kept wide
        # deliberately: research/24 §4.1 measured ~1 R of unmodelled slippage at a
        # 0.5 x ATR stop on this instrument class, and the artifact scales
        # inversely with stop width.
        sl = entry + max(atr_val * 2.5, ema_sep + atr_val)
        risk = abs(sl - entry)
        if risk <= 0:
            return None
        tp1_rr = getattr(p, "tp1_rr", 5.0) if p else 5.0
        tp = entry - risk * tp1_rr

        self.trades_today += 1
        self.daily_risk_used_pct += risk_pct
        return self._tag_signal(TradeSignal(
            strategy_id=self.strategy_id, symbol=symbol,
            direction="SELL", signal_type="DRIFT_ENTRY",
            entry_price=entry, stop_loss=sl, take_profit=tp,
            confluence_score=70, timeframe=timeframe,
            timestamp=df.index[-1].timestamp(),
            metadata={"size_modifier": size_modifier, "gap_pct": gap_pct,
                      "trail_method": "NONE", "atr_val": atr_val,
                      "setup": "setup_a_drift",
                      "reason": "Boom drift SELL (grind continuation)"},
        ))

    async def on_tick(self, symbol: str, tick: dict[str, Any]) -> None:
        return None
