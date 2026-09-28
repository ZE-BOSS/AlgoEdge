"""
backend/strategies/strategy_opening_drive/params.py

OpeningDrive_v1 — the marketed "one 5-minute candle" rule, rebuilt and measured.

THE CLAIM THIS IMPLEMENTS
    "At the New York open, the algo watches just one thing: the first 5-minute
     candle. If it closes above the 12 EMA, it goes long. If it closes below, it
     goes short. The algo manages the position, trails the stop as momentum
     develops... Nasdaq 2019-2026, 1,448 trades, 982% return, 57% win rate, 1.29
     profit factor."

WHAT THE CLAIM IS WORTH
    The 57% win rate and 1.29 profit factor DO NOT REPRODUCE. Over 2021-10 ->
    2026-09 on seven markets (5,081 sessions, costs charged) the rule measures
    49.8% wins and a 1.07 profit factor. On US Tech 100 alone, the market the
    claim names: 54.5% wins, 1.07 profit factor, +0.052R per session. The 982%
    headline is not a comparable number at all without the risk per trade and
    whether it compounds, and neither was stated.

WHAT IS LEFT AFTER TESTING IT
    A small, real, fee-fragile edge on equity indices, and nothing anywhere else.
    It is shipped because it is measurable and positive on indices, with the
    per-market numbers in Implementation/STRATEGIES-SHIPPED-2026-09-25.md and
    NOT as the system it was sold as.

    US Tech 100  +0.052R  t +2.49    <- the only one that stands on its own
    US SP 500    +0.025R  t +1.17
    XAUUSD       +0.012R  t +0.92
    Germany 40   +0.007R  t +0.45
    BTCUSD       -0.005R  t -0.47    <- do not run it here
    every FX pair tested was negative

The rule leaves two things unspecified, and both are parameters here because
they had to be chosen by measurement: the initial stop (not mentioned at all)
and what "trails the stop as momentum develops" means.
"""

from dataclasses import dataclass


@dataclass
class OpeningDriveParams:
    session: str = "ny"
    """Which cash open to trade: "ny" (09:30 America/New_York) or "london"
    (08:00 Europe/London). DST-aware. The claim is about the New York open."""

    ema_span: int = 12
    """Span of the EMA the session's first bar is compared against, on the
    strategy's own M5 closes. 12 is the claim's number."""

    stop_atr_multiple: float = 1.0
    """Initial stop, in daily ATR. The claim specifies no stop; 1.0 was chosen
    from measurement and sets R, so it sets the position size."""

    trail_atr_multiple: float = 1.0
    """Chandelier trailing stop, in daily ATR back from the best close since
    entry — the testable reading of "trails the stop as momentum develops".
    0.5 / 1 / 2 / 3 were swept per market; 1.0 was the best pooled."""

    trail_lookback_bars: int = 100
    """Bars the trail's best-close anchor looks back over, never past the entry
    bar. A session is ~78 M5 bars, so 100 is "the whole session" while staying a
    bounded window that live and the backtester compute identically."""

    atr_days: int = 14
    """Days in the daily ATR the stop and trail are multiples of."""

    side: str = "both"
    """"both", "long" or "short". The rule is symmetric by construction."""

    close_at_session_end: bool = True
    """Flat at the cash close. Part of the measured rule — it is a session
    strategy, and every trade in the measurement was closed the same day."""
