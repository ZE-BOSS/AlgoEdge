from dataclasses import dataclass

@dataclass
class BiasIFVGParams:
    # ── [D-5] Where the IFVG is allowed to form ──────────────────────────
    ifvg_leg_mode: str = "APPROACH"
    """
    RESOLVED 2026-08-23 from the strategy spec, which was unambiguous where the
    engine was not.

    `docs/strategy-2-bias-keylevel-ifvg.md` Step 3:
        "Define the manipulation leg: the swing (high->low, or low->high) that
         ACTUALLY TOUCHED the Step 2 key level. Scan the 5m timeframe for FVGs
         that exist WITHIN THAT SPECIFIC LEG (not elsewhere on chart)."

    The leg that touched the level is the leg that APPROACHED it, so a
    spec-conformant scan looks at gaps formed on the way IN, which then invert
    when price closes back through them on the way out. The engine did the
    opposite — it only considered gaps forming AFTER the tap — and its own
    comment flagged the mismatch as an open product question.

      "APPROACH" - spec-conformant (DEFAULT): gaps within the leg that reached
                   the key level, i.e. formed at or before the tap.
      "REACTION" - the engine's previous behaviour: gaps forming after the tap,
                   inside the move away from the level. Retained because the
                   entire existing backtest corpus was produced under it, so it
                   is what a historical result must be reproduced against.
      "BOTH"     - either side of the tap. Widest, and the right setting for a
                   like-for-like comparison run.

    Which of the two is more profitable is an empirical question, not a
    doctrinal one. The default follows the spec; the flag exists so you can
    settle it with a backtest rather than an argument.
    """

    # Spec's documented primary NY-session edge is 09:30-11:00 ET. The previous
    # 08:00-17:00 default spanned nearly the whole trading day and effectively
    # disabled the "primary trading window" hypothesis the strategy is built on.
    session_start: str = "09:30"
    session_cutoff: str = "11:00"
    # Spec's mandatory day-stop rule (stop after 1 win; after 1 loss only take a
    # 2nd trade if it's A+; stop after 2 losses regardless) caps realistic same-day
    # trade count at 2 — see BiasIFVGEngine.notify_outcome/_can_trade_today.
    max_trades_per_day: int = 2

    max_losses_per_day: int = 0
    """
    [12.2/Part14] Generic daily loss guardrail, standardised across every
    strategy (was only on VWAPParams). Default 0 (disabled) here deliberately
    — this strategy already has its own, more specific day-stop rule
    (`_can_trade_today`: stop after 1 win, stop after 2 losses, 2nd-trade-
    after-1-loss confluence clamp — see engine.py). Set this to a nonzero
    value only if you want an ADDITIONAL, simpler slot-level cutoff on top of
    that existing rule.
    """

    target_rr: float = 2.0
    """
    Spec §Step 4 gives a take-profit RANGE of 1:1 to 1:3 and lists `target_rr_range`
    as 1.0-3.0; 2.0 is the midpoint and is retained. Note the low end of that range is
    not viable net of costs: at a 12-pip stop and ~3.0 pips of FX-major round-trip
    friction (~0.25R), a 1.0R target nets 0.75R against a 1.25R loss -> 62.5%
    break-even win rate, versus the spec's unverified ~70% claim. 2.0R needs only 41.7%.
    """

    sl_buffer_atr_mult: float = 0.5
    """
    Additional SL buffer as a multiple of ATR(14), applied after structural SL is computed.
    0.0 = disabled. Configurable from the Settings panel and the Backtester params section.

    CHANGED 0.0 -> 0.5 (2026-08). engine.py:345 places SL flush at `m5_swing_point` with
    no buffer whatsoever. Spec §Step 4 nominates "swing high/low (default - safest for
    beginners)" as the stop method, but a stop placed exactly ON a visible swing is the
    single most-hunted price in the book. 0.5 x ATR(M5) adds ~1.5-2 pips of USDCHF cushion.

    ENGINE BUG - THIS PARAM IS CURRENTLY DEAD. `sl_buffer_atr_mult` appears nowhere in
    strategy_five_bias_ifvg/engine.py. It is accepted by the config schema, surfaced in
    the UI, persisted to the DB, and then silently ignored. Patch shape: mirror
    strategy_six_ny_open_retest/engine.py:127-136.
    """

    # -- Cost-Floor Guards (added 2026-08 - NOT in the spec doc) --------------
    # ENGINE WIRING REQUIRED: engine.py:345 computes
    #     sl = state.get("m5_swing_point", entry * 0.99 ...)
    # with no buffer and no floor. Intended semantics:
    #     floor   = max(min_sl_pips * pip_size, min_sl_atr_mult * atr)
    #     sl_dist = max(abs(entry - m5_swing_point) + sl_buffer_atr_mult * atr, floor)

    min_sl_pips: float = 12.0
    """
    Absolute minimum stop distance in pips. 0 = disabled. ~4x FX-major round-trip
    friction (~3.0 pips on USDCHF), keeping cost at ~0.25R instead of ~0.9R.
    Especially load-bearing here: the strategy is confined to the 09:30-11:00 ET window
    and enters on an M5 body-close through an IFVG, so entry and the swing that formed
    the IFVG are frequently only a few pips apart by construction.
    """

    min_sl_atr_mult: float = 1.0
    """
    Volatility-relative minimum: SL distance must also be >= this x ATR(14) on M5.
    0 = disabled. Whichever floor is larger wins.
    """

    a_plus_confluence_threshold: int = 90
    """
    Minimum confluence_score a setup must have to qualify as a "clearly A+ setup"
    (spec §day-stop rule) - the only kind of setup allowed to be a 2nd trade after
    an opening loss.

    CHANGED 85 -> 90 (2026-08). The engine emits a HARD-CODED confluence_score of 85 on
    every signal (engine.py:364). With the threshold also at 85 the comparison is
    `85 >= 85`, so EVERY setup qualified as A+ and the spec's selective "only if it's
    clearly A+" carve-out silently became "always take a second trade after a loss".
    Raising to 90 makes the gate unsatisfiable until a real scoring function exists, so
    the day-stop rule degrades to the spec's safe reading: stop after the first loss.
    Retune to ~85 once the engine computes a genuine 0-100 confluence score.

    ENGINE BUG: confluence_score is a constant, not a computed quality measure.
    """

    setup_max_age_bars: int = 40
    """
    [6.1b/S2] Max M5 bars an in-progress setup (AWAIT_IFVG_SETUP or
    AWAIT_IFVG_CLOSE — i.e. a key level has been tapped and the engine is
    waiting on the M5 IFVG/inversion) may run before it's dropped, reverting
    to AWAIT_KEY_LEVEL with bias preserved. Replaces the old UTC-midnight
    calendar reset, which wiped the ENTIRE state (including HTF bias) at the
    day boundary regardless of freshness — even a setup one bar from firing.
    0 = never expire on age. Does not affect trades_today/wins_today/
    losses_today, which remain genuinely calendar-day counters by spec design.
    """

    second_trade_size_modifier: float = 0.5
    """
    [6.12/S15] Size multiplier applied to a 2nd trade taken after 1 loss when
    its confluence falls below `a_plus_confluence_threshold`. CHANGED from an
    outright rejection (BLOCK) to this CLAMP — the setup still fires, just at
    reduced size, rather than being discarded entirely for missing an
    inherently somewhat arbitrary confidence bar. 1.0 = no reduction (fire at
    full size even when sub-threshold, effectively disabling the clamp).
    """

    rejection_min_body_atr_mult: float = 0.15
    """
    Minimum candle body size, as a multiple of M15 ATR(14), for a rejection wick to
    qualify as a rejection-block key level. Filters out near-doji candles (body ~0)
    that would otherwise produce noisy, low-quality "rejection levels" purely from
    wick-to-body ratio. 0.0 disables the floor.
    """
