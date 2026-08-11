from dataclasses import dataclass

@dataclass
class BiasIFVGParams:
    session_start: str = "08:00"
    session_cutoff: str = "17:00"
    max_trades_per_day: int = 3
    target_rr: float = 2.0
