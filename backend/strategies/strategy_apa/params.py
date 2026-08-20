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

    tight_level_threshold_atr: float = 0.35
    """
    If |Head - Shoulder| < this × ATR, switch SL to cover both Head+Shoulder wicks.
    Prevents under-stopped entries when the two levels are very close. Range: 0.1–0.4.

    CHANGED 0.2 → 0.35 (2026-08). The "tight levels" branch selects the *Head* wick,
    which is always further from entry than the Right Shoulder wick — i.e. this is the
    WIDER-stop branch. Raising the threshold makes the engine choose the wider stop more
    often. Forensic review of real USDCHF runs found APA's median structural stop was
    3.47 pips (min 0.45 pips) against a 1.5–2.5 pip spread — stops routinely placed
    INSIDE the cost of entry. 0.35 stays inside the doc's documented 0.1–0.4 range
    while biasing hard toward the survivable branch.
    """

    sl_buffer_atr: float = 0.05
    """Extra room beyond the wick stop, in ATR multiples. Range: 0–0.15 (doc default 0.05)."""

    sl_buffer_atr_mult: float = 0.5
    """
    Additional SL buffer expressed as a multiple of ATR(14), added ON TOP of the
    structural SL distance after the signal is generated (engine.py applies
    `(sl_buffer_atr + sl_buffer_atr_mult) * atr` as one combined buffer).

    CHANGED 0.0 → 0.5 (2026-08). At 0.0 the only cushion was sl_buffer_atr=0.05×ATR,
    which on USDCHF M15 (ATR ≈ 6–8 pips) is ~0.35 pips — less than a quarter of the
    spread. 0.5×ATR adds ~3–4 pips on USDCHF M15, i.e. roughly 1.5–2× the round-trip
    spread, so a normal-sized structural stop is no longer inside the cost floor.
    This is the *proportional* cushion; min_sl_pips below is the *absolute* backstop.
    """

    # ── Cost-Floor Guards (added 2026-08 — NOT in the source doc) ────────────
    # The APA doc specifies SL purely structurally (shoulder/head wick + 0.05×ATR).
    # That is correct as geometry but has no cost model behind it: the retest entry
    # sits inside the Invalidation Zone, which is bounded by shoulder BODIES, while
    # the stop sits at the shoulder WICK — the two can be a fraction of a pip apart.
    # A 0.45-pip stop on a 1.5–2.5 pip spread is not a stop, it is a coin flip on
    # bid/ask bounce, and it also mechanically produces untradeable position sizes
    # (risk$ / stop-distance → 40 lots on a $25k account = ~160× leverage, which MT5
    # rejects with retcode 10019). These two floors make the degenerate case impossible.
    #
    # ENGINE WIRING REQUIRED: strategy_apa/engine.py does not currently read either of
    # these. They are inert until the engine widens `state["sl_level"]` so that
    # |entry - sl| >= max(min_sl_pips * pip_size, min_sl_atr_mult * atr).

    min_sl_pips: float = 12.0
    """
    Absolute minimum stop distance in pips. 0 = disabled.

    Derivation: USDCHF round-trip friction ≈ 2.0 pip spread + 0.4 pip slippage +
    ~0.6 pip commission ($6/lot) ≈ 3.0 pips. A stop must be several multiples of
    friction or the cost eats the whole R. 12 pips ≈ 4× friction, so friction is
    ~0.25R rather than ~0.9R at the old 3.4-pip median.

    Scale note: this is applied via get_pip_size(), so it is broadly instrument-class
    neutral — 12 pips is 12 index points on NAS100/UK100 (spread 1–2 pts), $1.20 on
    XAUUSD (spread ~$0.25), 0.12 on JPY pairs. All land in the 5–10× spread band.
    """

    min_sl_atr_mult: float = 1.0
    """
    Volatility-relative minimum stop: SL distance must also be >= this × ATR(14) on
    the structure timeframe. 0 = disabled.

    Complements min_sl_pips: the pip floor protects against a broken spread model,
    the ATR floor protects against a high-volatility regime where 12 pips is itself
    noise. Whichever is larger wins.
    """

    invalidation_zone_source: str = "right_shoulder"
    """
    Which candle bodies define the retest Invalidation Zone.
    'right_shoulder' = only Right Shoulder candle bodies (default, more conservative).
    'both' = Left + Right Shoulder bodies (wider zone, more entries).
    """

    # ── Session Filter ────────────────────────────────────────────────────
    session_filter_enabled: bool = True
    """
    Restrict entries to the configured session window.

    CHANGED False → True (2026-08). The source doc specifies no session filter, but it
    was demonstrated on a chart, not on a 24/5 FX feed. Left off, APA fires through the
    Asian session, where USDCHF spread widens to 3–5 pips and the M15 range frequently
    collapses to 2–3 pips — i.e. exactly the regime that produced the sub-spread stops
    and the 30% of legs that exited within a single bar. A structural-reversal strategy
    needs real participation on both sides of the neckline break; the Asian session on a
    CHF cross does not supply it. This is a deliberate deviation from the doc.
    """

    session_start: str = "07:00"
    """Session open (UTC). London open. Only relevant if session_filter_enabled=True."""

    session_cutoff: str = "16:00"
    """
    Session close (UTC).

    CHANGED 20:00 → 16:00 (2026-08). 07:00–16:00 UTC is London open through the
    London/NY overlap — where USDCHF depth and therefore quoted spread are best.
    16:00–20:00 UTC is post-London-close: NY-only flow on a European cross, wider
    spreads and thinner structure. Cutting it removes signals whose stops must be
    widest exactly when the strategy's edge is weakest.
    """

    # ── ATR Lookback ─────────────────────────────────────────────────────
    atr_lookback: int = 14
    """Number of bars used to calculate ATR for all multiplier calculations."""
