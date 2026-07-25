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
    fixed_target_points: float = 15.0
    dynamic_target_override: bool = True
