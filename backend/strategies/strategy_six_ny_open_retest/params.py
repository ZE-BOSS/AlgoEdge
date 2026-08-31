from dataclasses import dataclass


@dataclass
class NYOpenRetestParams:
    """
    Tunable parameters for Strategy 3: 8:00 AM Session-Range Break & Retest
    """
    range_window_start: str = "08:00"
    range_window_end: str = "08:15"
    earliest_valid_break_time: str = "09:00"
    """
    CHANGED "09:30" -> "09:00" (2026-08, Phase 3 ablation).

    Measured contribution **-0.013** across 8 cells (positive in only 4 of 8) —
    i.e. the gate is mildly HARMFUL — while blocking 7,306 of 8,185 candidates
    it saw (89.3%). Removing it entirely yields **+38% more signals** with no
    loss of expectancy.

    Not removed outright, because a break before the range window has finished
    forming is genuinely meaningless; relaxed by 30 minutes instead, which
    recovers most of the sample while keeping the structural guard. Revisit
    once there is a larger post-fix sample.
    """
    session_end: str = "11:00"

    max_losses_per_day: int = 0
    """[12.2/Part14] Generic daily loss guardrail, standardised across every strategy (was only on VWAPParams). 0 = disabled."""

    stop_buffer_points: float = 5.0
    """
    Buffer beyond the opposite side of the 08:00 range, in "points" -> converted via
    get_pip_size(), so on FX this is 5 PIPS. Matches the spec's documented default of 5
    (spec: "5 (instrument-specific) | Calibrate per market"). Left at the doc value: it
    is the ATR term below that is asked to carry the instrument-scaling job, because a
    single constant cannot be simultaneously right for NQ points and USDCHF pips.
    """

    fixed_target_points: float = 50.0
    """
    Fixed take-profit distance in "points" -> get_pip_size(), so 50 PIPS on USDCHF.
    Spec documents 50 as an implementation-tuned NQ default (original framework examples
    used ~15).

    KNOWN MIS-SCALING (not fixed here - see target_mode below): 50 pips from range_mid
    inside a 09:30-11:00 ET window is effectively unreachable on a CHF major, whose
    entire average DAY range is ~55 pips. In the real runs this meant essentially no
    trade ever exited at the fixed target; every exit came from the SL, the
    dynamic_target_override swing, or the session close. Retained as the NQ-native
    ceiling; `target_mode` is the scale-free replacement.
    """

    dynamic_target_override: bool = True
    """
    Per spec: use the nearer HTF swing level if it is closer than the fixed target.
    Enabled per doc, and currently the only thing making fixed_target_points survivable
    on FX (it is what actually supplies a reachable target).
    """

    # -- Scale-Free Target (added 2026-08 - NOT in the spec doc) --------------
    # ENGINE WIRING REQUIRED: engine.py:114 computes `target = fixed_target_points *
    # pip_size` unconditionally. Intended semantics when target_mode == "rr":
    #     sl_dist = abs(entry - stop_loss)          # after all buffers/floors
    #     target  = sl_dist * target_rr
    # falling back to the fixed-points path when target_mode == "points".

    target_mode: str = "rr"
    """
    "rr"     - target is target_rr x the realised stop distance (DEFAULT).
    "points" - legacy behaviour: target is fixed_target_points x pip_size.

    Rationale: an R-multiple is the only formulation of a target that is simultaneously
    correct on NQ, USDCHF and XAUUSD. A fixed point value has to be re-tuned per symbol
    and silently degenerates (unreachable on FX, trivial on an index) when it is not.
    """

    target_rr: float = 2.0
    """
    R-multiple target used when target_mode == "rr".

    2.0 rather than 1.0: with the ~12-15 pip stop this config now produces and ~3.0 pips
    of USDCHF round-trip friction, friction is ~0.22R. A 1R target nets 0.78R against a
    1.22R loss -> 61% break-even win rate. At 2R: 1.78R against 1.22R -> 41% break-even.
    The forensic review measured this strategy's realised expectancy at -0.006R, i.e.
    exactly break-even gross and firmly negative net - it has no margin to spend on a
    near target.
    """

    sl_buffer_atr_mult: float = 1.0
    """
    Additional SL buffer as a multiple of ATR(14) on the break-confirmation timeframe,
    applied on top of stop_buffer_points. 0.0 = disabled. This param IS read by the
    engine (engine.py:129-136) - unlike its namesakes in strategies four and five.

    CHANGED 0.0 -> 1.0 (2026-08). The structural stop here is (range_mid -> range
    extreme) + 5 points. The 08:00-08:15 ET candle on USDCHF is typically 6-10 pips tall,
    so half-range is 3-5 pips and the total stop was ~8-10 pips - only ~3-4x the ~2.5 pip
    all-in cost. Adding 1.0 x ATR(M5) (~3 pips on USDCHF) lifts it to ~11-13 pips, i.e.
    4-5x cost. Under realistic costs this strategy went +$1,752 -> -$7,250 in the
    forensic re-run; a stop that cost ~30% of R was the dominant term.

    Instrument tension, stated explicitly: on NQ, ATR(M5) is ~15-20 points, so 1.0x adds
    15-20 points to a ~5-point buffer. That is proportionate to NQ's own noise, but it
    does make the legacy 50-point fixed target only ~1.1-1.4R - which is precisely why
    target_mode defaults to "rr" above rather than to fixed points. Do not raise this
    above 1.0 without also confirming the target is R-based.
    """
