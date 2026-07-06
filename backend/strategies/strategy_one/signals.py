"""
backend/strategies/smc/signals.py

Trade signal generation and validation gates.
Source: SMC_Strategy-1.md Section 14
Source: RiskManagement_Spec.md Section 6
"""

from typing import Dict, Any, Optional
from backend.strategies.strategy_one.params import UserConfig
from backend.strategies.base_strategy import TradeSignal
from backend.utils.timeutils import is_kill_zone
from backend.utils.logger import get_logger

logger = get_logger(__name__)


class TradeGate:
    """All checks must return True. Any False = trade rejected."""

    def __init__(self, config: UserConfig):
        self.config = config

    def validate_all(self, context: Dict[str, Any]) -> tuple[bool, str]:
        """Run all safety gates. Returns (passed, rejection_reason)."""

        direction = context.get("signal_direction", "")

        # Gate 1: HTF bias must be confirmed (not NEUTRAL)
        htf_bias = context.get("htf_bias", "NEUTRAL")
        if htf_bias == "NEUTRAL":
            return False, "HTF bias is NEUTRAL — no directional conviction"
        if htf_bias != direction:
            return False, f"HTF bias {htf_bias} conflicts with signal {direction}"

        # Gate 3: Price must be at a POI (OB, FVG, Fib/OTE, or S&D)
        fresh_ob = context.get("fresh_ob")
        fvg_inside_ob = context.get("fvg_inside_ob", False)
        in_ote = context.get("in_ote_zone", False)
        in_sd = context.get("in_sd_zone", False)
        if fresh_ob is None and not fvg_inside_ob and not in_ote and not in_sd:
            return False, "Price is not at any POI (no OB, FVG, Fib, or S&D zone)"

        # Gate 4: Minimum RR must be met
        entry = context.get("entry_price", 0)
        sl = context.get("stop_loss", 0)
        tp1 = context.get("tp1_price", 0)
        if sl != 0 and entry != 0:
            risk = abs(entry - sl)
            reward = abs(tp1 - entry)
            rr = reward / risk if risk > 0 else 0
            if rr < self.config.risk.min_rr:
                return False, f"RR {rr:.1f} below minimum {self.config.risk.min_rr}"

        # Gate 5: Spread must be acceptable (Dynamic ATR Check)
        current_spread = context.get("current_spread_pips", 0)
        atr = context.get("atr", 0)
        
        # We repurpose max_spread_pips to act as an ATR multiplier (e.g. 0.1)
        max_allowed_spread = atr * self.config.risk.max_spread_pips if atr > 0 else self.config.risk.max_spread_pips
        
        if current_spread > max_allowed_spread:
            return False, f"Spread ({current_spread}) exceeds maximum dynamic limit ({max_allowed_spread:.2f} based on ATR)"

        # Gate 6: Must be in active session (if session filter enabled)
        if self.config.smc.session_filter_enabled:
            if not context.get("in_kill_zone", False):
                return False, "Outside active kill zone session"

        # Gate 7: Must not be blocked by high-impact news
        if self.config.smc.news_filter_enabled:
            if context.get("news_blocked", False):
                return False, "Blocked by high-impact news event"

        # Gate 8: Confluence score must meet minimum
        score = context.get("confluence_score", 0)
        if score < self.config.smc.min_signal_score:
            return False, f"Confluence score {score} below minimum {self.config.smc.min_signal_score}"

        return True, "ALL_GATES_PASSED"


class SignalGenerator:
    """Generates TradeSignal objects if validation passes."""

    def __init__(self, config: UserConfig):
        self.config = config
        self.gate = TradeGate(config)

    def generate(self, context: Dict[str, Any], score: int) -> Optional[TradeSignal]:
        """Attempt to generate a signal from current context."""

        context["confluence_score"] = score

        passed, reason = self.gate.validate_all(context)
        if not passed:
            is_bt = context.get("is_backtesting", False)
            prefix = "BT-" if is_bt else ""
            from backend.services.bot_service import bot_service
            bot_service.log_system_event(f"Signal rejected: {reason}", "DEBUG", f"{prefix}SIGNAL")
            return None

        direction = context.get("signal_direction", "")
        symbol = context.get("symbol", "")
        entry = context.get("entry_price", 0.0)
        sl = context.get("stop_loss", 0.0)
        tp1 = context.get("tp1_price", 0.0)

        signal = TradeSignal(
            symbol=symbol,
            direction=direction,
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
            }
        )

        logger.info(f"Signal generated: {direction} {symbol} @ {entry} | Score: {score}")
        return signal
