"""
backend/strategies/strategy_classic/params.py

Parameters for the six classic strategy families of the 2026-09-11 report
(backend/analytics/strategy_search.py). Each field maps one-to-one onto a
setting the research builder actually reads — nothing here that the engine
ignores. Targets are the slot's tp1_rr like every other strategy; families the
research ran WITHOUT a target carry tp1_rr 10 in strategy_defaults, so their
strategy exit (trail, channel, mean, flip) is what closes them.
"""

from dataclasses import dataclass


@dataclass
class DonchianParams:
    channel_bars: int = 20
    """Breakout of the prior N H1 bars' high (long) or low (short). Research grid: 20, 55."""
    stop_atr: float = 2.0
    """Initial stop in ATR(14). Research grid: 2.0, 3.0."""
    exit_mode: str = "trail"
    """"trail": 3xATR chandelier from each close. "channel": close beyond the
    opposite max(5, N/2)-bar channel."""
    side: str = "both"
    """"both" or "long"."""


@dataclass
class EMAPullbackParams:
    fast_ema: int = 20
    slow_ema: int = 50
    """Trade with fast>slow (long) on a bar that dips to the fast EMA and closes
    back above it. Stop 2xATR(14). Research grid: (20,50), (50,200)."""
    side: str = "both"


@dataclass
class RSI2Params:
    threshold: float = 10.0
    """Connors RSI(2): buy below this (sell above 100-this) with the 200-bar SMA
    trend; exit on a close back through the 5-bar SMA. Grid: 5, 10."""
    max_hold_bars: int = 24
    """Time exit in H1 bars. Grid: 24, 48."""
    side: str = "both"


@dataclass
class BollingerFadeParams:
    band_sigma: float = 2.0
    """Fade a close back inside the 20-bar band after a close outside it; exit at
    the middle band. Grid: 2.0, 2.5."""
    side: str = "both"


@dataclass
class VolBreakoutParams:
    channel_bars: int = 20
    """Close beyond the prior N-bar high/low ..."""
    volume_mult: float = 1.5
    """... on a bar whose tick volume is at least this x its 20-bar average (the
    only order-flow history MT5 keeps for CFDs). Stop 2xATR, 3xATR trail."""
    side: str = "both"


@dataclass
class TSMOMParams:
    lookback_days: int = 60
    """Hold the sign of the N-day return on D1; flip when it flips. Stop 3xATR(20).
    Grid: 20, 60, 120."""
    side: str = "both"
