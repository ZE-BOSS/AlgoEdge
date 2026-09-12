"""
backend/strategies/strategy_orb/params.py

Opening-range breakout parameters. Defaults are the GBPJPY configuration — the
one market where every setting tried was profitable before 2026 and the chosen
one stayed profitable in all three test periods (see strategy_orb/engine.py).
Per-symbol session/range values live in strategy_defaults.py::SYNTH_SLOT_PARAMS.

The TARGET is not a parameter here on purpose: the order's take-profit is the
slot's resolved tp1_rr (slot -> SLOT_TP1_RR -> strategy default), the same path
every other strategy uses. A second target field on this block would be a
setting the order never reads.
"""

from dataclasses import dataclass


@dataclass
class ORBParams:
    session: str = "london"
    """Which cash open anchors the range: "london" (08:00 Europe/London) or
    "ny" (09:30 America/New_York). Both are DST-aware."""

    range_minutes: int = 60
    """Length of the opening range, from the session open. A multiple of 15."""

    breakout_window_minutes: int = 180
    """How long after the range closes a breakout still counts. Only the FIRST
    close beyond the range inside this window is traded — one trade per session."""

    side: str = "both"
    """"both", "long" or "short"."""

    min_stop_atr: float = 0.25
    """Stop distance floor in M15 ATR(14), for days the range is unusually narrow."""

    close_at_session_end: bool = True
    """Flatten at the session close. Measured on BTCUSD and XAUUSD, holding past
    the close turned a profitable period flat — this is part of the strategy."""
