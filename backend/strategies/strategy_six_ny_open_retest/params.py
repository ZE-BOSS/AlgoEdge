from dataclasses import dataclass


@dataclass
class NYOpenRetestParams:
    """
    Tunable parameters for Strategy 3: 8:00 AM Session-Range Break & Retest
    """
    range_window_start: str = "08:00"
    range_window_end: str = "08:15"
    earliest_valid_break_time: str = "09:30"
    session_end: str = "11:00"

    stop_buffer_points: float = 5.0
    fixed_target_points: float = 50.0
    dynamic_target_override: bool = True
    sl_buffer_atr_mult: float = 0.0
    """
    Additional SL buffer as a multiple of ATR(14), applied on top of stop_buffer_points.
    0.0 = disabled. Example: 0.5 widens SL by 0.5 x ATR to reduce tight-SL over-fitting.
    Configurable from the Settings panel (live) and Backtester strategy params section.
    """
