from dataclasses import dataclass
from typing import Optional

@dataclass
class HTFFVGFlipParams:
    """
    Tunable parameters for Strategy 1: HTF Key Level -> 5M FVG -> Inversion Flip
    """
    htf_timeframe: str = "H1"
    entry_confirmation_tf: str = "M1"
    target_rr: float = 1.0
    require_unfilled_htf_fvg: bool = True
    
    # Session filter (disabled by default for this strategy, per spec)
    session_filter_enabled: bool = False
    session_start: str = "09:30"
    session_cutoff: str = "11:00"
