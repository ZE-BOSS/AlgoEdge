"""
backend/strategies/smc/signals.py

Trade signal generation and validation gates.
Source: SMC_Strategy-1.md Section 14
Source: RiskManagement_Spec.md Section 6
"""

from typing import Any

from backend.core.config_schema import UserConfig
from backend.strategies.base_strategy import TradeSignal
from backend.utils.logger import get_logger

logger = get_logger(__name__)


class TradeGate:
    """All checks must return True. Any False = trade rejected."""

    def __init__(self, config: UserConfig):
        self.config = config

    def validate_all(self, context: dict[str, Any]) -> tuple[bool, list[str]]:
        """Run all safety gates. Returns (passed, list_of_rejection_reasons)."""
        direction = context.get("signal_direction", "")
        reasons = []

        # Gate 1: HTF bias must be confirmed (not NEUTRAL)
        htf_bias = context.get("htf_bias", "NEUTRAL")
        if htf_bias == "NEUTRAL":
            reasons.append("Gate 1: HTF bias is NEUTRAL — no directional conviction")
        elif htf_bias != direction:
            reasons.append(f"Gate 1: HTF bias {htf_bias} conflicts with signal {direction}")

        # Gate 3: Price must be at a POI (OB, FVG, Fib/OTE, or S&D)
        fresh_ob = context.get("fresh_ob")
        fvg_inside_ob = context.get("fvg_inside_ob", False)
        in_ote = context.get("in_ote_zone", False)
        in_sd = context.get("in_sd_zone", False)
        if fresh_ob is None and not fvg_inside_ob and not in_ote and not in_sd:
            reasons.append("Gate 3: Price is not at any POI (no OB, FVG, Fib, or S&D zone)")

        # Gate 4: Minimum RR must be met
        entry = context.get("entry_price", 0)
        sl = context.get("stop_loss", 0)
        tp1 = context.get("tp1_price", 0)
        if sl != 0 and entry != 0:
            risk = abs(entry - sl)
            reward = abs(tp1 - entry)
            rr = reward / risk if risk > 0 else 0
            if rr < self.config.risk.min_rr:
                reasons.append(f"Gate 4: RR {rr:.1f} below minimum {self.config.risk.min_rr}")
        else:
            reasons.append("Gate 4: Missing entry/SL/TP for RR calculation")

        # Gate 5: Spread must be acceptable (Dynamic ATR Check)
        current_spread = context.get("current_spread_pips", 0)
        atr = context.get("atr", 0)
        
        atr_mult = getattr(self.config.risk, "max_spread_atr_mult", 0.5)
        max_allowed_spread = atr * atr_mult if atr > 0 else atr_mult
        
        if current_spread > max_allowed_spread:
            reasons.append(f"Gate 5: Spread ({current_spread}) exceeds maximum dynamic limit ({max_allowed_spread:.2f} based on ATR)")

        # Gate 6: Hard Filters (Strategy Optimization)
        if getattr(self.config.smc, "enforce_htf_pd", False):
            # Enforce buying in Discount, selling in Premium
            ipdm_phase = context.get("ipdm_phase", "")
            if direction == "BUY" and "PREMIUM" in ipdm_phase:
                reasons.append("Gate 6: Hard Filter — Buy signal in Premium zone rejected")
            elif direction == "SELL" and "DISCOUNT" in ipdm_phase:
                reasons.append("Gate 6: Hard Filter — Sell signal in Discount zone rejected")
                
        if getattr(self.config.smc, "enforce_fvg_displacement", False):
            if not context.get("active_fvgs", []):
                reasons.append("Gate 6: Hard Filter — Signal lacks FVG displacement")
                
        if getattr(self.config.smc, "enforce_asian_range_sweep", False):
            if not context.get("asian_range_swept", False):
                reasons.append("Gate 6: Hard Filter — Asian Range has not been swept")

        # Gate 7: Must be in active session (if session filter enabled)
        if getattr(self.config.smc, "session_filter_enabled", False):
            if not context.get("in_kill_zone", False):
                reasons.append("Gate 6: Outside active kill zone session")

        # Gate 8: Must not be blocked by high-impact news
        if getattr(self.config.smc, "news_filter_enabled", False):
            if context.get("news_blocked", False):
                reasons.append("Gate 8: Blocked by high-impact news event")

        # Gate 9: Confluence score must meet minimum
        score = context.get("confluence_score", 0)
        if score < self.config.smc.min_signal_score:
            reasons.append(f"Gate 9: Confluence score {score} below minimum {self.config.smc.min_signal_score}")

        return len(reasons) == 0, reasons


class SignalGenerator:
    """Generates TradeSignal objects if validation passes."""

    def __init__(self, config: UserConfig):
        self.config = config
        self.gate = TradeGate(config)

    def generate(self, context: dict[str, Any], score: int) -> TradeSignal | None:
        """Attempt to generate a signal from current context."""

        context["confluence_score"] = score

        passed, reasons = self.gate.validate_all(context)
        if not passed:
            for reason in reasons:
                logger.debug(f"[REJECTED] {reason}")
            # We no longer return None here so that the backtester can track the rejection funnel.
            # Live trading (bot_service) will safely ignore it by checking signal.metadata["passed_gates"].

        direction = context.get("signal_direction", "")
        trade_direction = "BUY" if direction == "BULLISH" else ("SELL" if direction == "BEARISH" else direction)
        symbol = context.get("symbol", "")
        entry = context.get("entry_price", 0.0)
        sl = context.get("stop_loss", 0.0)
        tp1 = context.get("tp1_price", 0.0)

        signal = TradeSignal(
            symbol=symbol,
            direction=trade_direction,
            entry_price=entry,
            stop_loss=sl,
            take_profit=tp1,
            timeframe=context.get("entry_timeframe", "M5"),
            confluence_score=score,
            signal_type=context.get("signal_type", "OB_ENTRY"),
            metadata={
                "htf_bias": context.get("htf_bias"),
                "ob": context.get("fresh_ob"),
                "markings": context.get("markings", []),
                "fvg": context.get("active_fvgs"),
                "sweep": context.get("liquidity_sweep"),
                "candle_tier": context.get("candle_tier"),
                "session": context.get("current_session"),
                "ipdm_phase": context.get("ipdm_phase"),
                "score_breakdown": context.get("score_breakdown", {}),
                "passed_gates": passed,
                "rejection_reasons": reasons,
            }
        )

        if passed:
            logger.info(f"Signal generated: {direction} {symbol} @ {entry} | Score: {score}")
        return signal
