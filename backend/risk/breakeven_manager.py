"""
backend/risk/breakeven_manager.py

Break-even system with configurable trigger and buffer.
Source: RiskManagement_Spec.md Section 3.2
"""

from typing import Any

from backend.utils.logger import get_logger

logger = get_logger(__name__)


def resolve_be_buffer(
    spread_price: float,
    atr: float,
    pip_size: float,
    be_buffer_pips: float,
    be_buffer_atr_mult: float,
    be_spread_multiple: float = 2.0,
) -> float:
    """
    [4.5/D6/F4] The ONE break-even buffer formula, shared by
    `backtester/engine.py::_breakeven_stop` and
    `position_manager.py`'s live BE blocks — previously each had its own
    independent implementation (backtest: `max(pip, atr, spread*1x)`;
    live: `max(pip, atr, spread*2x)`), a real backtest/live divergence.

        buffer = max(spread_price × be_spread_multiple, be_buffer_atr_mult × atr,
                      be_buffer_pips × pip_size)

    All three inputs are already in PRICE units (not pips) except
    `be_buffer_pips`/`pip_size`, which this function converts. Never raises;
    treats `None`/negative inputs as 0.
    """
    atr_buffer = float(be_buffer_atr_mult or 0.0) * float(atr or 0.0)
    pip_buffer = float(be_buffer_pips or 0.0) * float(pip_size or 0.0)
    spread_buffer = float(spread_price or 0.0) * float(be_spread_multiple or 0.0)
    return max(atr_buffer, pip_buffer, spread_buffer, 0.0)


class BreakevenManager:
    """Manages break-even SL adjustments for open positions."""

    def __init__(self, config: dict[str, Any]):
        self.be_trigger_rr = config.get("be_trigger_rr", 1.0)
        self.be_atr_multiplier = config.get("be_buffer_atr_mult", 0.0)
        self.be_buffer_pips = config.get("be_buffer_pips", 2.0)
        self.be_on_tp1_hit = config.get("be_on_tp1_hit", True)
        # [5.1/5.3/Part4] RR | TP_HIT | EITHER | NONE. Default EITHER
        # reproduces today's behaviour exactly (either condition fires it).
        self.be_mode = config.get("be_mode", "EITHER")
        # [4.5/D6/F4] shared buffer formula — see resolve_be_buffer() below.
        self.be_spread_multiple = config.get("be_spread_multiple", 2.0)

    def check_breakeven(
        self,
        entry_price: float,
        current_price: float,
        current_sl: float,
        stop_loss: float,
        direction: str,
        pip_value: float,
        live_spread: float,
        atr: float,
        be_already_applied: bool = False,
        tp1_hit: bool = False,
    ) -> float | None:
        """
        Returns the new SL price if break-even should be triggered, else None.
        SL only moves in the profitable direction — never backward.
        Source: RiskManagement_Spec.md Section 3.2
        """
        if be_already_applied:
            return None

        # [5.1/5.3/Part4] NONE disables BE regardless of every other setting.
        if self.be_mode == "NONE":
            return None

        risk = abs(entry_price - stop_loss)
        if risk == 0:
            return None

        from backend.risk.multi_tp import _is_buy
        is_buy = _is_buy(direction)

        # Calculate current R-multiple in profit
        if is_buy:
            unrealized_r = (current_price - entry_price) / risk
        else:
            unrealized_r = (entry_price - current_price) / risk

        # [5.1/5.3/Part4] be_mode selects which trigger(s) are live:
        #   RR      — unrealized_r only, ignores tp1_hit entirely.
        #   TP_HIT  — tp1_hit only, ignores be_trigger_rr entirely.
        #   EITHER  — today's behaviour: whichever condition fires first.
        rr_trigger = unrealized_r >= self.be_trigger_rr
        tp_hit_trigger = tp1_hit and self.be_on_tp1_hit

        if self.be_mode == "RR":
            should_trigger = rr_trigger
        elif self.be_mode == "TP_HIT":
            should_trigger = tp_hit_trigger
        else:  # EITHER
            should_trigger = rr_trigger or tp_hit_trigger

        if not should_trigger:
            return None

        # [4.5/D6/F4] Shared formula — was a bare max(live_spread, atr_buffer,
        # pip_buffer) with no spread multiplier (implicitly 1x), diverging
        # from position_manager.py's live inline computation (2x) and from
        # backtester/engine.py::_breakeven_stop's TP1-sibling cascade path.
        buffer = resolve_be_buffer(
            spread_price=live_spread,
            atr=atr,
            pip_size=pip_value,
            be_buffer_pips=self.be_buffer_pips,
            be_buffer_atr_mult=self.be_atr_multiplier,
            be_spread_multiple=self.be_spread_multiple,
        )

        # This line used to reference `atr_buffer` and `pip_buffer` — locals that
        # the resolve_be_buffer() refactor above deleted. It raised NameError on
        # its FIRST ever execution, which only happened on 2026-08-24: until the
        # APA retest fix, no trade in any backtest had ever reached break-even,
        # so this branch was unreachable and the broken line sat here undetected.
        # Now logs the inputs and the result, all of which are in scope.
        logger.debug(
            f"[BREAKEVEN] buffer inputs | spread={live_spread:.5f} atr={atr:.5f} "
            f"pip={pip_value:.5f} | be_pips={self.be_buffer_pips} "
            f"atr_mult={self.be_atr_multiplier} spread_mult={self.be_spread_multiple} "
            f"| resolved buffer={buffer:.5f}"
        )


        if is_buy:
            new_sl = entry_price + buffer
            # Only move SL in favorable direction
            if new_sl <= current_sl:
                return None
        else:
            new_sl = entry_price - buffer
            if new_sl >= current_sl:
                return None

        logger.info(f"Break-even triggered: SL {current_sl:.5f} -> {new_sl:.5f}")
        return new_sl
