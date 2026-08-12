"""
backend/strategies/strategy_apa/params.py

Advanced Price Action (APA) Strategy Parameters
================================================
Implements the Head & Shoulders / ABC structural reversal with
Invalidation Zone retest entry (Michael FX / A.P.A. framework).
Source: docs/apa_strategy_implementation_plan.md
"""

from dataclasses import dataclass


@dataclass
class APAParams:
    """
    Tunable parameters for Strategy APA: Advanced Price Action.
    All timeframes use AlgoEdge standard format (M5, M15, H1, H4, D1).
    """

    # ── Timeframes ──────────────────────────────────────────────────────
    structure_timeframe: str = "M15"
    """Timeframe for swing/structure analysis (Shoulder & Head detection)."""

    entry_timeframe: str = "M5"
    """Timeframe for entry trigger (retest + confirmation candle)."""

    # ── Swing Detection ──────────────────────────────────────────────────
    minor_fractal_m: int = 3
    """
    Half-window (in bars) for minor swing detection (Shoulders / Head).
    A swing high at bar[i] requires bar[i] > all bars in [i-M..i+M].
    Lower = more sensitive, higher = fewer but cleaner swings.
    Range: 2–5.
    """

    major_fractal_m: int = 8
    """
    Half-window for major swing detection (BOS validity filter).
    A BOS only counts if the neckline coincides with a major-fractal swing.
    Higher = stricter filter, fewer but more valid breaks. Range: 6–15.
    """

    # ── Pattern Validation ───────────────────────────────────────────────
    shoulder_symmetry_tolerance_atr: float = 0.3
    """
    Max distance between Left Shoulder price and Right Shoulder price,
    expressed as ATR multiples. Wider = more formations qualify. Range: 0.15–0.5.
    """

    tight_level_threshold_atr: float = 0.2
    """
    If |Head - Shoulder| < this × ATR, switch SL to cover both Head+Shoulder wicks.
    Prevents under-stopped entries when the two levels are very close. Range: 0.1–0.4.
    """

    sl_buffer_atr: float = 0.05
    """Extra room beyond the wick stop, in ATR multiples. Range: 0–0.15."""

    sl_buffer_atr_mult: float = 0.0
    """
    Additional SL buffer expressed as a multiple of ATR(14), added ON TOP of the
    structural SL distance after the signal is generated.
    0.0 = disabled (no extra buffer). Example: 0.5 widens the SL by 0.5 × ATR.
    Use to account for spread, slippage, and news wicks in live trading.
    Configurable from the Settings panel (live) and Backtester strategy params section.
    """

    invalidation_zone_source: str = "right_shoulder"
    """
    Which candle bodies define the retest Invalidation Zone.
    'right_shoulder' = only Right Shoulder candle bodies (default, more conservative).
    'both' = Left + Right Shoulder bodies (wider zone, more entries).
    """

    # ── Session Filter ────────────────────────────────────────────────────
    session_filter_enabled: bool = False
    """Restrict entries to the configured session window (disabled by default for APA)."""

    session_start: str = "07:00"
    """Session open (UTC). Only relevant if session_filter_enabled=True."""

    session_cutoff: str = "20:00"
    """Session close (UTC). Only relevant if session_filter_enabled=True."""

    # ── ATR Lookback ─────────────────────────────────────────────────────
    atr_lookback: int = 14
    """Number of bars used to calculate ATR for all multiplier calculations."""
