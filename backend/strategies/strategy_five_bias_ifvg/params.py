from dataclasses import dataclass, field
from typing import List

@dataclass
class BiasIFVGParams:
    """
    Tunable parameters for Strategy 2: 4-Step Bias -> Key Level -> IFVG
    """
    bias_timeframes: List[str] = field(default_factory=lambda: ["D1", "H4", "H1", "M15"])
    key_level_timeframes: List[str] = field(default_factory=lambda: ["M5", "M15", "M30", "H1", "H4"])
    confirmation_timeframes: List[str] = field(default_factory=lambda: ["M1", "M2", "M3", "M4", "M5"])
    
    stop_method: str = "swing_high_low"
    target_rr_range_min: float = 1.0
    target_rr_range_max: float = 3.0
    
    max_trades_per_day: int = 2
    session_start: str = "09:30"
    session_cutoff: str = "11:00"
