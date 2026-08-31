"""
backend/strategies/strategy_vwap/params.py

VWAP Drift Pullback Strategy Parameters
========================================
Implements Matteo Conti's "Drift VWAP Pullback" (Golden Ticket VWAP).
Source: docs/vwap_strategy_implementation_plan.md

STOP-LOSS CONVENTION — RESOLVED 2026-08
=======================================
Three mutually incompatible conventions for `sl_points` were in circulation:

  A) engine.py: `sl_dist = sl_points * get_pip_size(symbol)`
     → 80 * 0.0001 = 80 PIPS on USDCHF. Absurd for an M5 scalper: it turned a
       ~10-minute intraday trade into a ~6.9-DAY swing hold in the real runs.
  B) frontend hint (Backtester.jsx): "170 for USDCHF = 17 pips"
     → implies sl_points are PIPETTES (point_size 0.00001), i.e. 80pt = 8 pips.
  C) doc §5 (vwap_strategy_implementation_plan.md:92-95): "On NQ/MNQ: SL = 80
     points, as specified. On any other instrument: convert to an ATR-multiple
     instead of a fixed point value."

RESOLUTION: (C) is authoritative, with a calibrated k and an absolute floor.
`sl_points` is an INDEX-POINT figure native to NQ/MNQ and is meaningless on FX;
it is therefore used ONLY for the index/futures instrument class. Everything else
uses an ATR multiple, per the doc's explicit instruction.

Why not (B): 8 pips lands in roughly the right neighbourhood for USDCHF today, but
it is a fixed constant — it does not adapt across instruments or across volatility
regimes, which is precisely the failure the doc is warning about. It corroborates the
target range; it is not a method.

Why k is NOT 1.0: the doc says calibrate k such that k × ATR ≈ 80 points under NQ's
own volatility — it never specified 1.0. The old default of 1.0 was an unexamined
placeholder. Measured on the user's real USDCHF runs (where sl_atr_multiplier=1.0
won over the broken fixed path, so these ARE 1.0×ATR distances):
    min 1.64 | p25 2.83 | MEDIAN 3.40 | p75 4.22 | max 9.21 pips
So 1.0×ATR ≈ 3.4 pips ≈ 1.7× the USDCHF spread — under-stopped, not a stop.

NOTE ON WHICH ATR: engine.py calls `calculate_atr(candles)` where `candles` are the
ENTRY-timeframe (M5) bars, not the 15-minute anchor bars. All k values below are
therefore calibrated against ATR(M5). If the engine is ever changed to feed M15 bars,
ATR is ~1.7× larger and k must be divided by ~1.7 (i.e. k≈1.75 instead of 3.0).
"""

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class VWAPParams:
    """
    Tunable parameters for Strategy VWAP: Drift VWAP Pullback.
    """

    # ── VWAP v2 (Phase 8) ────────────────────────────────────────────────
    # Source material (implementation/strategy_update/ TapeDragon carousels):
    # VWAP is a FRAMEWORK — fair value (VWAP line), trend context (slope),
    # extremes (±1σ/±2σ/±3σ bands), and confluence. "Let VWAP guide your
    # bias, not your entry." The v1 engine implemented only the trend-context
    # half (a single Setup 1-like pullback, no bands at all, VWAP itself as
    # the entry trigger rather than the bias). v2 adds the missing band
    # framework and the mean-reversion setup the source material treats as
    # equally central.
    vwap_bands_enabled: bool = True
    """[8.1/8.2] Compute ±1σ/±2σ/±3σ VWAP bands. False reproduces v1 (VWAP line only, entry_mode forced to PULLBACK_TO_VALUE with pullback_max_distance_sigma/reversion checks skipped)."""

    vwap_band_sigmas: list[float] = field(default_factory=lambda: [1.0, 2.0, 3.0])
    """[8.1/8.2] Which sigma multiples to compute bands at. First three entries are used as ±1σ/±2σ/±3σ by the setups below regardless of list length."""

    vwap_band_lookback: int = 0
    """[8.1] 0 = bands computed since the session anchor (matching the VWAP line itself); >0 = a rolling N-bar lookback instead. 0 is the doc-faithful default."""

    entry_mode: Literal["PULLBACK_TO_VALUE", "BAND_REVERSION", "BOTH"] = "PULLBACK_TO_VALUE"
    """
    CHANGED "BOTH" -> "PULLBACK_TO_VALUE" (2026-08, Phase 3).

    BAND_REVERSION (Setup 2) is measurably dead code. Across the 46-cell sweep
    `reversion_beyond_band` passed **296 of 14,793 evaluations (2.0%)** — price
    almost never closes beyond the configured sigma on these instruments — and
    the three confluences inside the branch (`reversion_slope_flat`,
    `reversion_trend_neutral`, `wick_rejection`) therefore blocked **zero**
    candidates between them. `wick_rejection` was evaluated just 110 times in
    the entire study.

    So the setup is not weak, it is UNREACHABLE, and it costs a per-bar ATR
    computation, band arithmetic and wick-ratio maths on every one of those
    bars to produce almost nothing.

    Set to "BOTH" (and widen `reversion_min_sigma`) if you want to study it —
    but it needs to fire often enough to be measurable first.
    """
    """[8.1] Which setup(s) are active. PULLBACK_TO_VALUE = trend continuation (v1's setup, now with band/convergence/volume gates). BAND_REVERSION = new mean-reversion-to-VWAP setup (8.4). BOTH = either may fire."""

    pullback_requires_convergence: bool = True
    """[8.1/8.3] Setup 1 gate: distance from close to VWAP must be SHRINKING vs the prior bar (price actually pulling back toward value), not just a red/green candle — v1 only checked candle colour, which admits a candle accelerating AWAY from VWAP as long as it happens to be red/green."""

    pullback_max_distance_sigma: float = 1.0
    """[8.1/8.3] Setup 1 only triggers while price is inside this many sigma of VWAP — a "pullback" beyond ±1σ is already most of the way to a band-reversion setup, not a shallow retracement to value."""

    first_pullback_only: bool = True
    """[8.1/8.3] Only the FIRST Setup-1 trigger since the session anchor is taken; later ones in the same session are ignored. Resets with the daily/session reset."""

    reversion_min_sigma: float = 2.0
    """[8.1/8.4] Setup 2 (Band Reversion) only triggers when price closes beyond this many sigma — "never fade inside 2σ" per the source material."""

    reversion_requires_rejection: bool = True
    """[8.1/8.4] Setup 2 gate: the trigger candle must show a rejection wick (see reversion_min_rejection_wick_pct) against the extension, not just a close beyond the band."""

    reversion_min_rejection_wick_pct: float = 0.50
    """[8.4] Minimum rejection-wick fraction of the trigger candle's total range, when reversion_requires_rejection is True."""

    reversion_requires_trend_neutral: bool = True
    """[8.1/8.4] Setup 2 gate: do not fade a band extension while momentum (the same momentum_threshold_pct test Setup 1 uses) confirms a real trend in the extension's direction — "do not fade a strong trend"."""

    reversion_max_vwap_slope_atr_pct: float = 0.10
    """[8.4] Setup 2 gate: |VWAP(now) - VWAP(prev anchor bar)| must be below this fraction of ATR — "flat VWAP slope"."""

    volume_confirmation_mult: float = 0.0
    """
    [8.1] Both setups require trigger-bar volume >= this multiple of the rolling
    mean over `volume_confirmation_lookback_bars`. 0 = disabled.

    CHANGED 1.2 -> 0.0 (2026-08-25). At 1.2 this produced ZERO signals on gold
    across 7.5 months. Gate-by-gate on 6,000 XAUUSD M5 bars:

        tradeable bars                      1239
        + price above vwap                   718
        + vwap rising                        681
        + momentum up                        404
        + inside 1-sigma band                101
        + converging                          55
        + volume >= 1.2x                       0   <-- every candidate gone

    Two reasons it cannot work here, and they compound:

    1. MT5 CFD feeds report TICK volume (a count of price updates), not traded
       size. The docstring already flagged 0 as the right value for such feeds —
       the default simply never matched the platform this runs on.
    2. More fundamentally, the pullback setup selects for a CONVERGING,
       low-momentum bar — the quietest bar of the move — and then demanded it
       also be a 1.2x volume spike. The two conditions pull against each other
       by construction, so the filter was close to self-contradictory even on a
       feed with real volume.

    Set it back above 0 only on a venue that reports genuine traded volume, and
    check the gate funnel above still leaves candidates before trusting a run.
    """

    volume_confirmation_lookback_bars: int = 20
    """[8.1] Rolling window (entry-timeframe bars) for the volume_confirmation_mult rolling mean."""

    target_mode: Literal["SIGMA_BAND", "R_GRID"] = "SIGMA_BAND"
    """
    [8.6/P2-X5] SIGMA_BAND (default): Setup 1 targets +2σ/-2σ, Setup 2 targets
    VWAP itself — both declared via metadata.structural_tp/structural_tp_rr
    (same pattern CRT uses) so the RiskParams TP grid can be exempted rather
    than silently overriding a target with no relationship to either setup's
    thesis. R_GRID restores v1 behaviour (advisory tp = entry ± sl_dist,
    fully overridden by the grid).
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
    sl_method: str = "auto"
    """
    ADDED 2026-08. Explicit selector that resolves the points/pips/ATR conflict
    documented in this module's header. One of:

      "auto"          — instrument-class aware (DEFAULT, and the doc-faithful option):
                        index CFDs / index futures (NQ, MNQ, NAS100, US30, US500,
                        UK100, GER40, JP225, …) → "fixed_points";
                        everything else (FX, metals, crypto, synthetics) → "atr_multiple".
      "fixed_points"  — always use sl_points. Only meaningful where 1 "point" is a
                        native instrument unit, i.e. index CFDs and futures.
      "atr_multiple"  — always use sl_atr_multiplier × ATR, floored per min_sl_* below.

    Rationale for "auto": doc §5 says the 80-point figure is "calibrated to NQ's typical
    volatility ... it won't transfer literally to other instruments (a Deriv synthetic or
    a forex pair doesn't share NQ's point scale)". Making that instruction machine-readable
    is the only way to keep the spec-faithful NQ behaviour AND a sane FX behaviour in one
    default configuration.

    WIRED 2026-08: engine.py's `_resolve_sl_distance` reads this field and branches
    on `_is_index(symbol)` — the note that used to be here ("engine ignores this
    field") predates that fix and no longer applies.
    """

    sl_points: float = 80.0
    """
    Fixed SL in native INDEX PRICE UNITS. Per doc §5 this is an NQ/MNQ figure (80
    points = an 80.00 price move), and is only applied when the resolved sl_method
    is "fixed_points" — i.e. for index CFDs / index futures. Left at the doc's 80.0.

    CORRECTED 2026-08-22 (Part 11 §B3): engine.py used to multiply this by
    get_pip_size(symbol), on the assumption that get_pip_size() == 1.0 for every
    index — verifiably false for NAS100/USTEC/NDX (0.25) and US500/SPX/US2000/
    GER40/DAX (0.1), which silently shrank an 80-point stop to 20 or 8 points on
    exactly the most-traded indices in this book. sl_points is now applied directly
    with no conversion, matching what this docstring always said it should do.

    DO NOT read this as pips on FX. `sl_method="auto"` never selects "fixed_points"
    for FX — see `_is_index()` — so this field has no effect there regardless.
    """

    sl_atr_multiplier: float = 3.0
    """
    ATR multiple used when the resolved sl_method is "atr_multiple" (all non-index
    instruments under "auto"). 0 = disable the ATR path entirely.

    CHANGED 1.0 → 3.0 (2026-08). 1.0 was never a calibration — doc §5 asks for a k such
    that k × ATR ≈ 80 NQ points at the source's own volatility. NQ ATR(M15) at the time
    was ≈ 45 points, giving k ≈ 1.8 on M15 — but this engine feeds M5 bars to
    calculate_atr(), and M5 ATR is roughly 1/1.7 of M15 ATR, so the equivalent M5 k is
    ≈ 3.0. The two calibrations agree; 3.0 is the one that matches the code as written.

    Sanity check against measured USDCHF ATR(M5) distances (see module header):
        p25  2.83 × 3.0 =  8.5 pips
        med  3.40 × 3.0 = 10.2 pips   ← ~5× the 2.0-pip spread; friction ≈ 0.3R
        p75  4.22 × 3.0 = 12.7 pips
        min  1.64 × 3.0 =  4.9 pips   ← caught by min_sl_pips floor below
    That distribution sits squarely in the 8–14 pip band an FX major can actually
    support, and it scales automatically with the volatility regime.
    """

    # ── Absolute Cost Floors (added 2026-08 — NOT in the source doc) ─────────
    # ENGINE WIRING REQUIRED: neither field is read by strategy_vwap/engine.py yet.
    # Intended semantics:
    #   floor = max(min_sl_pips * pip_size, min_sl_spread_mult * current_spread)
    #   sl_dist = max(sl_dist_from_method, floor)

    min_sl_pips: float = 8.0
    """
    Absolute minimum stop distance in pips, applied under BOTH sl_methods. 0 = disabled.

    Derivation: 4 × the ~2.0-pip USDCHF round-trip spread. This is the guard that
    actually prevents the failure mode — an ATR multiple alone still collapses in a
    dead low-volatility session (the measured ATR(M5) minimum of 1.64 pips would give a
    4.9-pip stop even at k=3.0, which is only ~2.5× spread).

    Set lower than APA's 12.0 deliberately: VWAP is an explicit intraday scalper with a
    15:55 hard close, so it cannot amortise a 12-pip stop the way a structural reversal
    strategy can. 8 pips is the floor; the k=3.0 ATR term supplies the normal ~10 pips.
    """

    min_sl_spread_mult: float = 4.0
    """
    Minimum stop distance expressed as a multiple of the CURRENT quoted spread.
    0 = disabled. This is the instrument-neutral form of min_sl_pips and should take
    precedence wherever a live/modelled spread is available (it adapts to news-time
    spread blowouts, which a fixed pip figure cannot). 4× is the conventional retail-FX
    minimum: below it, the spread alone is >25% of R.
    """

    target_rr: float = 2.0
    """
    ADDED 2026-08. R-multiple for the strategy's own take_profit field.

    Resolves doc §6's flagged conflict ("This is a real conflict, not a cosmetic one")
    in favour of the R-grid, and rejects the source's fixed 40pt/50pt targets outright.
    The doc's own arithmetic at line 111 already shows why: at the claimed 64.5% win rate,
    the long side is ≈ 0.645×40 − 0.355×80 = −2.6 points per trade BEFORE spread,
    commission and slippage. A target that is negative-expectancy before costs cannot be
    rescued by cost modelling — so the source's 64–65% win-rate claim is treated as void,
    per the doc's second option, and the strategy is run as an R-multiple system.

    2.0R rather than the engine's hard-coded 1.0R: with a ~10-pip stop and ~3.0 pips of
    USDCHF round-trip friction, friction is ~0.30R. A 1R target nets 0.70R while a loss
    costs 1.30R — break-even hit rate 65%, i.e. the entire claimed edge with zero margin.
    At 2R the same trade nets 1.70R against 1.30R — break-even hit rate 43%, which leaves
    real room for the win rate to decay out-of-sample.

    ENGINE WIRING REQUIRED: engine.py hard-codes `tp = entry ± sl_dist` (1R). Note the
    live/backtest TP ladder is owned by RiskParams (tp1_rr/tp2_rr/tp3_rr) regardless, so
    this field is currently only the signal's advisory target.
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
