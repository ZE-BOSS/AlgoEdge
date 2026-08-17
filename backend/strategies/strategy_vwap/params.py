"""
backend/strategies/strategy_vwap/params.py

VWAP Drift Pullback Strategy Parameters
========================================
Implements Matteo Conti's "Drift VWAP Pullback" (Golden Ticket VWAP).
Source: docs/vwap_strategy_implementation_plan.md

SL is 40pt (user-adjustable) so TP1 at 1R = 40pt matches the source's actual TP.
"""

from dataclasses import dataclass


@dataclass
class VWAPParams:
    """
    Tunable parameters for Strategy VWAP: Drift VWAP Pullback.
    """

    # ── VWAP Calculation ─────────────────────────────────────────────────
    vwap_anchor_minutes: int = 15
    """
    VWAP anchor window in minutes. A 15-min anchored VWAP overlaid on 5-min bars.
    Shorter = more responsive (more false signals). Range: 5–30.
    """

    entry_timeframe: str = "M5"
    """Timeframe for pullback trigger candle detection."""

    # ── Direction & Momentum Filters ─────────────────────────────────────
    momentum_lookback_bars: int = 4
    """
    How many anchor-timeframe bars to look back for momentum calculation.
    Default 4 × 15-min = 1 hour. Range: 2–16.
    """

    momentum_threshold_pct: float = 0.1
    """
    Required price movement (%) over the lookback window to qualify as trending.
    E.g. 0.1 = price must have moved ≥ 0.1% up (longs) or ≥ 0.1% down (shorts).
    Range: 0.05–0.3.
    """

    # ── Stop Loss ────────────────────────────────────────────────────────
    sl_points: float = 80.0
    """
    Fixed SL in price points (instrument-specific).
    Default 80pt per original strategy specification (vwap_strategy_implementation_plan.md).
    With TP1 at 1R, this gives TP1=80pt; wider than 40pt to survive spread and slippage.
    Adjustable from frontend (Backtester strategy params or live Settings). For non-NQ
    instruments where fixed points don't apply, sl_atr_multiplier takes over instead.
    """

    sl_atr_multiplier: float = 1.0
    """
    ATR-based SL floor. When > 0, SL distance = max(sl_points, ATR × this value).
    ATR can only WIDEN the SL beyond sl_points, never narrow it.
    Set to 0 to use pure fixed-point SL from sl_points with no ATR floor.
    """

    # ── Session Rules ─────────────────────────────────────────────────────
    session_open: str = "09:30"
    """Start of US session (Eastern Time). First-hour exclusion begins here."""

    session_exclude_end: str = "10:30"
    """End of first-hour exclusion. No entries between session_open and this time."""

    entry_cutoff: str = "15:30"
    """No new entries after this time (ET). Positions opened before can still run."""

    hard_close: str = "15:55"
    """Force-flatten ALL open positions at this time (ET) to avoid overnight gap risk."""

    # ── Daily Guardrails ─────────────────────────────────────────────────
    max_trades_per_day: int = 4
    """Maximum number of trades per session. Range: 2–6."""

    max_losses_per_day: int = 2
    """Stop trading for the rest of the session after this many losses. Range: 1–3."""

    drawdown_kill_pct: float = 10.0
    """
    Deactivate the strategy if realized drawdown exceeds this % of allocated capital.
    Substitutes for the source's Monte Carlo drawdown boundary.
    """
