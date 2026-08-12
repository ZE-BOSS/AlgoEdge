from dataclasses import dataclass

@dataclass
class BiasIFVGParams:
    session_start: str = "08:00"
    session_cutoff: str = "17:00"
    max_trades_per_day: int = 3
    target_rr: float = 2.0
    sl_buffer_atr_mult: float = 0.0
    """
    Additional SL buffer as a multiple of ATR(14), applied after structural SL is computed.
    0.0 = disabled. Example: 0.5 widens SL by 0.5 x ATR to reduce tight-SL over-fitting.
    Configurable from the Settings panel (live) and Backtester strategy params section.
    """
