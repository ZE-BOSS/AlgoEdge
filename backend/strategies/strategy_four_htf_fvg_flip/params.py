from dataclasses import dataclass

@dataclass
class HTFFVGFlipParams:
    session_filter_enabled: bool = False
    session_start: str = "08:00"
    session_cutoff: str = "17:00"
    htf_timeframe: str = "H1"
    entry_confirmation_tf: str = "M5"
    target_rr: float = 2.0
