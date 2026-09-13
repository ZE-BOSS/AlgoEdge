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

    breakout_timeframe: str = "M15"
    """"M15": the original ORB_v1 (first M15 close beyond the range).
    "M5": the edge-lab form (backend/analytics/edge_lab.gen_orb_break) — first
    M5 close beyond the range, no entries in the session's last 30 minutes, stop
    floored at min_stop_atr x ATR(14) of M5. Measured 2026-09-13 with
    require_trend on US Tech 100, XAUUSD, BTCUSD and GBPJPY: positive in 2022-23,
    2024-25 and 2026 on every one of them (data/edge_lab/)."""

    require_trend: bool = False
    """M5 form only: trade a break only WITH the higher-timeframe trend — close on
    the same side of a 600-bar M5 EMA (~50 hours) that is itself sloping that
    way over the last hour. The one confluence that helped on all four main
    markets in both the selection window and 2026."""
