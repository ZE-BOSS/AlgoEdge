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

    max_sl_floor_atr_mult: float = 5.0
    """
    [6.5/S7] Ceiling on the cost floor `_sl_floor_distance` may produce,
    expressed as an ATR multiple. 0 = disabled. The floor's job is to rescue a
    structural stop that's TOO TIGHT (inside spread) — it has no business
    silently re-widening a stop that was already comfortably wide (observed:
    a stop floored from 465 to 550 pips with no upper bound at all). Default
    5.0×ATR sits well above `min_sl_atr_mult`'s default (1.0), so it only
    catches a genuinely pathological floor value, never normal floor activity.
    Lowering this below `min_sl_atr_mult` overrides that field's effect too —
    intentional, since this is meant to be the final word on how wide the
    floor may go.
    """

    neckline_major_atr_tolerance: float = 1.0
    """
    [6.4/S3] How far (in ATR multiples) a BOS neckline may sit from the
    nearest major-fractal swing and still count as "on a major level" rather
    than a liquidity sweep. CHANGED from a hardcoded 0.5 — a pattern between
    0.5x and this tolerance is now admitted (previously discarded outright)
    but scores 0 on the NECKLINE PRECISION confluence component (which still
    only awards points ≤0.30×ATR — see _confluence_score), so a looser match
    is admitted at lower confidence/size rather than never evaluated at all.
    Widen further to admit more borderline BOS levels; 0.5 restores the old
    hard cutoff exactly (nothing between 0.5x and 1.0x will ever pass).
    """

    max_concurrent_patterns: int = 3
    """
    [6.2/S4] APA used to track exactly one candidate pattern per symbol at a
    time — while it sat in AWAIT_BOS/AWAIT_RETEST/AWAIT_CONFIRMATION, no new
    pattern was scanned, so one slow-to-resolve setup blocked every other
    opportunity on that symbol. Candidates are now a bounded ring buffer of
    up to this many, each ageing and resolving independently (see
    pattern_max_age_bars/bos_max_age_bars/retest_max_age_bars below). Raise
    for more concurrent setups per symbol; 1 reproduces the old single-slot
    behaviour.
    """

    pattern_max_age_bars: int = 40
    """
    [6.1/S2] Max STRUCTURE-timeframe bars a candidate may sit in AWAIT_BOS
    (pattern detected, no break of structure yet) before it expires and is
    dropped. Replaces the old UTC-midnight calendar reset, which wiped an
    in-flight AWAIT_BOS pattern arbitrarily at day boundaries regardless of
    how fresh it actually was, while (per the old code's own gating) leaving
    a genuinely stale AWAIT_RETEST/AWAIT_CONFIRMATION setup untouched — 40
    bars on M15 is ~10 hours, generous for a pattern that's actively forming.
    0 = never expire on age (not recommended — this is exactly the leak the
    fix addresses).
    """

    bos_max_age_bars: int = 30
    """
    [6.1/S2] Max ENTRY-timeframe bars a candidate may sit in AWAIT_RETEST
    (BOS confirmed, waiting for price to retest the Invalidation Zone) before
    expiring. This is the timeout the old code was missing entirely — per the
    forensic review, "the only invalidation is checked inside
    AWAIT_CONFIRMATION, reached only after a retest touch. A setup whose
    retest never comes holds the slot indefinitely." 30 bars on M5 is 2.5
    hours. 0 = never expire.
    """

    retest_max_age_bars: int = 10
    """
    [6.1/S2] Max ENTRY-timeframe bars a candidate may sit in
    AWAIT_CONFIRMATION (retest touched the IZ, waiting for the confirmation
    rules to pass) before expiring. Shorter than bos_max_age_bars because a
    retest that has already touched the zone should confirm quickly if the
    setup is real. 0 = never expire.
    """

    session_filter_skip_24_7: bool = True
    """
    Exempt 24/7 instruments (crypto, synthetics) from the equity-session window.

    Measured on 691 saved trades: the session effect is strong in aggregate
    (NY +0.358R, ASIAN -0.112R) but reverses for instruments that do not follow
    an equity clock — XRPUSD is +0.56R in the Asian window and -0.20R in NY.
    Applying an equity session to crypto discards trades for no reason.

    Uses the `trades_24_7` flag already on each InstrumentProfile, so no second
    symbol list has to be kept in sync. Set False to apply the window to
    everything regardless.
    """

    require_retest: bool = False
    """
    Whether a retest into the Invalidation Zone is REQUIRED before entry.

    CHANGED to False (2026-08-24). Retest was previously mandatory and it was
    selecting against the strategy rather than for it. Measured on XAUUSD over
    one window:

        setup_expired_await_retest ..... 192   BOS fired, price ran, never returned
        setup_expired_await_bos ........  34
        became signals .................  15
        became trades ..................   5   MFE median 0.00R, 100% stopped

    The 192 are breakouts that WORKED — price broke structure and kept going, so
    the retest never came and APA never traded them. The 15 that did retest are,
    by selection, the breakouts that were failing. Requiring a retest therefore
    filtered out the winners and kept the losers.

    False = enter once BOS and the confirmation rules pass, without waiting for
    price to come back. True = the previous behaviour, for when you want the
    better fill and accept missing the runners.

    This is a genuine trade-off, not a bug fix: entering at the BOS close is a
    worse price than a successful retest would have given. It is exposed in the
    UI rather than decided here.
    """

    rejection_candle_confluence_points: int = 12
    """
    Confluence points awarded when the retest candle REJECTED the Invalidation
    Zone — wicked in and closed back out on the trade's side.

    A contributor, never a gate. A rejection is real evidence the zone held, so
    it should raise the score (and, through confluence-scaled risk, the size) —
    but demanding it would reintroduce the same filtering problem `require_retest`
    just removed, one level down.

    Scored 0 when no retest occurred at all, which is the honest reading: absence
    of a rejection is not evidence against the setup when nothing was tested.
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

    # ── Daily loss guardrail (added 2026-08, [12.2/Part14]) ──────────────
    max_losses_per_day: int = 0
    """
    Stop trading this strategy for the rest of the day after this many
    losses. 0 = disabled (unlimited). Was previously only a `VWAPParams`
    field — every strategy now gets the same guardrail available, generalised
    by `risk/circuit_breaker.py`'s per-slot loss tracking [12.6] rather than
    re-implemented per engine (VWAPEngine.notify_outcome's pattern).
    """

    # ── Phase 14 B3.4 — Rejection-candle entry gate ──────────────────────────
    require_rejection_candle: bool = True
    """
    [Phase 14 B3.4] When True, a signal only fires after a candle wicks into the
    Invalidation Zone AND closes back out on the trade's side — a close-out of the
    IZ on the correct side is what \"rejection\" means.

    This is the \"option 1\" fix from the plan: it waits for the zone to actively
    reject price rather than entering the moment the IZ is touched. This is the
    primary fix for the MFE≈0R signature (entering on the first touch at the worst
    point of the move).

    Set False to replicate pre-Phase-14 behaviour (enter as soon as the IZ is
    touched, without waiting for close-back-out). The old `require_retest` param
    stays separate — it controls whether a retest is required AT ALL before entry;
    this param controls what constitutes a valid retest entry when one occurs.

    The rejection confluence points (`rejection_candle_confluence_points`) are still
    awarded when a rejection candle fires — they are now also a gate condition when
    this param is True.
    """

    # ── Phase 14 B2.3 — In-trade hard invalidation ───────────────────────────
    hard_invalidation_exit: bool = True
    """
    [Phase 14 B2.3] When True, a trade is closed at the bar's close price
    whenever the bar's BODY (open-close range, not wick) closes beyond the pattern's
    Head level — the spec defines this as hard invalidation of the H&S thesis.

    Without this, a position that is structurally dead (head-close crossed) continues
    running toward SL/TP/trail, booking unnecessary MFE erosion and adverse excursions
    that the strategy's rules say should not be held.

    Set False to disable, which restores the old behaviour of running every position
    purely to its risk-engine stop/target.
    """
