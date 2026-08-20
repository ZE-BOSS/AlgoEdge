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

    ENGINE WIRING REQUIRED: strategy_vwap/engine.py currently ignores this field and
    unconditionally computes `sl_dist = sl_points * get_pip_size(symbol)` then takes
    max(that, ATR × sl_atr_multiplier). See the audit doc for the precise change.
    """

    sl_points: float = 80.0
    """
    Fixed SL in native INDEX POINTS. Per doc §5 this is an NQ/MNQ figure (80 points),
    and is only applied when the resolved sl_method is "fixed_points" — i.e. for index
    CFDs / index futures, where get_pip_size() returns 1.0 and the multiplication is a
    genuine identity. Left at the doc's 80.0.

    DO NOT read this as pips on FX. Under the current engine code it is multiplied by
    get_pip_size() = 0.0001 for USDCHF, yielding an 80-PIP stop on an M5 pullback
    scalper — the direct cause of the ~6.9-day holds seen in the real backtests.
    The frontend hint "170 for USDCHF = 17 pips" (Backtester.jsx:112) describes a
    pipette convention the engine does not implement, and is wrong as written.
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
