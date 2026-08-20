from dataclasses import dataclass

@dataclass
class HTFFVGFlipParams:
    session_filter_enabled: bool = True
    """
    CHANGED False → True (2026-08). The spec's own "Open Questions" section says:
    "No explicit session/time filter was given for this strategy (unlike Strategy 3) —
    consider adding one if false signals cluster outside RTH." Forensic review of the
    real runs found exactly that clustering: sub-spread stops and single-bar exits
    concentrated outside cash-session hours. An ICT order-flow-flip premise requires
    genuine two-sided participation; the 20:00–07:00 ET stretch does not supply it on
    any of the traded instruments. Window below is US RTH (America/New_York — see
    engine.py:48, which converts to NY time, NOT UTC).
    """
    session_start: str = "09:30"
    """
    CHANGED 08:00 → 09:30 ET. 08:00–09:30 is pre-market: the H1 FVG taps that arm the
    state machine happen there, but the M5 inversion confirmations that fire entries are
    unreliable on pre-market volume, and FX/index CFD spreads have not yet compressed.
    Arming is unaffected — only the entry gate moves.
    """
    session_cutoff: str = "16:00"
    """
    CHANGED 17:00 → 16:00 ET. 16:00 is the US cash close; 16:00–17:00 is after-hours
    with materially wider spreads and no institutional order flow for an inversion to
    represent.
    """
    htf_timeframe: str = "H1"
    entry_confirmation_tf: str = "M5"
    # target_rr: spec doc §"Configurable Parameters" documents 1.0. DELIBERATE DEVIATION,
    # retained and now justified quantitatively rather than by convention:
    # with a ~12-pip structural stop and ~3.0 pips of FX-major round-trip friction
    # (2.0 spread + 0.4 slippage + ~0.6 commission), friction is ~0.25R. A 1.0R target
    # therefore nets 0.75R while a loss costs 1.25R → break-even win rate 62.5%. The
    # spec's own self-reported reference is ~75% on 27 unverified trades; requiring 62.5%
    # to merely break even leaves no margin for out-of-sample decay. At 2.0R the same
    # trade nets 1.75R against 1.25R → break-even win rate 41.7%. 2.0 stands.
    target_rr: float = 2.0
    require_unfilled_htf_fvg: bool = True
    """
    "First tap only" rule. When True, an HTF FVG that has already been tapped once
    (regardless of whether the resulting setup completed) is marked consumed and can
    never generate a second AWAIT_INVERSION_FVG sequence, even if it wasn't fully
    filled by that first tap. Set False to allow repeated taps of the same gap.
    """
    sl_buffer_atr_mult: float = 0.5
    """
    Additional SL buffer as a multiple of ATR(14), applied after structural SL is computed.
    0.0 = disabled. Configurable from the Settings panel and the Backtester params section.

    CHANGED 0.0 → 0.5 (2026-08). The engine sets SL flush at `m5_swing_point` with NO
    buffer at all (engine.py:199) — the stop sits exactly on the swing wick that every
    other participant can see, so it is picked off by the ordinary overshoot that follows
    a swing test, before any spread is even accounted for. 0.5×ATR(M5) adds ~1.5–2 pips
    on USDCHF, ~1× the spread. The min_sl_* floors below supply the rest.

    ⚠ ENGINE BUG — THIS PARAM IS CURRENTLY DEAD. `sl_buffer_atr_mult` does not appear
    anywhere in strategy_four_htf_fvg_flip/engine.py. It is accepted by the config
    schema, surfaced in the UI, persisted to the DB, and then silently ignored. See the
    audit doc for the exact patch (mirror strategy_six/engine.py:127-136).
    """

    # ── Cost-Floor Guards (added 2026-08 — NOT in the spec doc) ──────────────
    # ENGINE WIRING REQUIRED: engine.py:199 computes
    #     sl = state.get("m5_swing_point", entry * 0.99 ...)
    # with no buffer, no floor, and a 1%-of-price fallback. Intended semantics:
    #     floor   = max(min_sl_pips * pip_size, min_sl_atr_mult * atr)
    #     sl_dist = max(abs(entry - m5_swing_point) + sl_buffer_atr_mult * atr, floor)

    min_sl_pips: float = 12.0
    """
    Absolute minimum stop distance in pips. 0 = disabled. ≈4× FX-major round-trip
    friction (~3.0 pips on USDCHF), keeping cost at ~0.25R instead of ~0.9R.
    An M5 inversion swing on a quiet USDCHF hour is routinely 2–4 pips from entry;
    without this floor the position sizer converts that into extreme leverage.
    """

    min_sl_atr_mult: float = 1.0
    """
    Volatility-relative minimum: SL distance must also be >= this × ATR(14) on the
    entry confirmation timeframe. 0 = disabled. Whichever floor is larger wins.
    """
