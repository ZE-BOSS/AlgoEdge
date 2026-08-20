from dataclasses import dataclass

@dataclass
class HTFFVGFlipParams:
    session_filter_enabled: bool = False
    session_start: str = "08:00"
    session_cutoff: str = "17:00"
    htf_timeframe: str = "H1"
    entry_confirmation_tf: str = "M5"
    # target_rr default intentionally left at 2.0 (spec doc originally documented 1.0).
    # Treated as a deliberately-tuned live value, same as strategy_six's fixed_target_points
    # override — not changed without explicit product-owner confirmation.
    target_rr: float = 2.0
    require_unfilled_htf_fvg: bool = True
    """
    "First tap only" rule. When True, an HTF FVG that has already been tapped once
    (regardless of whether the resulting setup completed) is marked consumed and can
    never generate a second AWAIT_INVERSION_FVG sequence, even if it wasn't fully
    filled by that first tap. Set False to allow repeated taps of the same gap.
    """
    sl_buffer_atr_mult: float = 0.0
    """
    Additional SL buffer as a multiple of ATR(14), applied after structural SL is computed.
    0.0 = disabled. Example: 0.5 widens SL by 0.5 x ATR to reduce tight-SL over-fitting.
    Configurable from the Settings panel (live) and Backtester strategy params section.
    """
