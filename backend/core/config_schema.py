"""
backend/core/config_schema.py

Global Root Configuration Schema
================================
Holds the combined UserConfig schema used by the database, live trading, and backtester.
Dynamically includes configuration blocks for all registered strategies.
"""

import uuid
from dataclasses import dataclass, field
from typing import Literal

from backend.strategies.strategy_five_bias_ifvg.params import BiasIFVGParams
from backend.strategies.strategy_four_htf_fvg_flip.params import HTFFVGFlipParams
from backend.strategies.strategy_six_ny_open_retest.params import NYOpenRetestParams
from backend.strategies.strategy_apa.params import APAParams
from backend.strategies.strategy_vwap.params import VWAPParams
# ─────────────────────────────────────────────────────────────────────────────
# RISK MANAGEMENT PARAMETERS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RiskParams:
    """
    Complete risk management configuration.
    Stored per-user in the database. Editable from the frontend Settings panel.
    Applied identically in live trading and backtesting.
    """
    # ── Core sizing ──────────────────────────────────────────────────────
    risk_per_trade_pct: float = 0.5
    """
    CHANGED 1.0 -> 0.5 (2026-08). RiskManagement_Spec §6.5 documents 1.0% with a
    0.25-3.0% user range, so 0.5 is well inside spec. Two reasons to sit at the low end
    as the OUT-OF-THE-BOX default:

    1. Interaction with the circuit breaker. max_concurrent_positions is 3. At 1.0% per
       trade, three simultaneous losers = 3.0% = EXACTLY max_daily_drawdown_pct, so the
       shipped configuration is one ordinary adverse hour away from halting itself for
       the day. At 0.5% the same cluster costs 1.5% and the breaker retains headroom for
       a second wave. A risk budget that can be exhausted by a single normal-variance
       sequence is not a budget.
    2. No demonstrated edge yet. Forensic review of 11 real USDCHF runs (1,272 trade
       groups) found every strategy negative under realistic costs, and APA's per-trade
       expectancy at -0.367R. Standard practice is 0.25-0.50% until an edge survives
       out-of-sample; raise this deliberately once it does, not before.
    """

    max_risk_hard_cap_pct: float = 2.0
    """
    Absolute position-sizer safety net - the hard ceiling on any single trade's risk.

    CHANGED 3.0 -> 2.0 (2026-08). At 3.0 a single trade could consume the entire
    max_daily_drawdown_pct (3.0%) budget, making the daily breaker unable to breach
    gradually - it would go from "fine" to "halted" on one fill. 2.0 also aligns with
    DriftJumpAlpha_Strategy_Spec_v2 §1's `max_risk_per_trade_pct: 2.0`, which is the
    only spec in the repo that states a per-trade ceiling explicitly.
    """

    min_rr: float = 3.0
    """
    Per RiskManagement_Spec §1.2/§6.5 (default 3.0). SEMANTIC NOTE: risk/engine.py:298
    compares this against the LAST active TP's RR (tp3_rr = 5.0), not against TP1, so at
    these defaults it never rejects anything. It is a ladder-shape sanity check, not a
    per-trade edge filter - do not read a passing signal as "RR >= 3 was verified".
    """

    sizing_method: Literal["fixed_pct", "kelly"] = "fixed_pct"
    """
    Stays fixed_pct. Kelly requires a stable, positive, well-estimated edge; the largest
    per-strategy sample in the forensic review was 43 trades on one symbol over 7 months.
    Kelly sizing on an edge estimated from 43 observations sizes the estimation error,
    not the edge.
    """

    kelly_fraction: float = 0.25
    kelly_lookback_trades: int = 100
    """
    CHANGED 50 -> 100. Matches DriftJumpAlpha_Strategy_Spec_v2 §1's
    `kelly_recalc_window_trades: 100`. A 50-trade window has a win-rate standard error of
    roughly +/-7pp, which propagates into a wildly unstable Kelly fraction.
    """

    multi_position_mode: bool = True
    sl_buffer_pips: float = 5.0  # DEPRECATED: now per-strategy (APAParams.sl_buffer_atr, NYOpenRetestParams.stop_buffer_points). Kept for DB compat.

    # ── Global stop / exposure floors (added 2026-08) ────────────────────
    # ENGINE WIRING REQUIRED for both: backend/risk/position_sizer.py has NO minimum-SL
    # guard and clamps only to the broker's volume_max (default fallback 100.0 lots).
    # That is how APA reached 40 lots on a $25,000 account - $4,000,000 notional, ~160x
    # account leverage, which MT5 rejects with retcode 10019 (No money). These two fields
    # are the portfolio-level backstop underneath the per-strategy min_sl_* floors.

    min_sl_pips: float = 10.0
    """
    Global minimum stop distance in pips, applied AFTER the strategy emits its signal and
    BEFORE sizing. 0 = disabled. Deliberately looser than the per-strategy floors
    (8-15 pips) so it acts as a last-resort backstop for strategies that have no floor of
    their own, not as the primary control. A signal whose stop cannot be widened to this
    without invalidating its own structure should be REJECTED, not silently re-priced.
    """

    max_account_leverage: float = 30.0
    """
    Hard ceiling on (total open notional / account equity). 0 = disabled.
    30:1 is the ESMA retail cap for FX majors and is the least arguable reference point
    available. This is the guard that would have caught the 160x case directly, and it
    catches it independently of whether the stop-distance bug is fixed - the two failure
    modes (tight stop -> huge lots) share a single observable symptom.

    [2.4] WIRED (2026-08-22): position_sizer.py::_margin_capped_lots now uses this
    value as the assumed leverage in its notional/leverage margin ESTIMATE
    (replacing the hardcoded FALLBACK_ACCOUNT_LEVERAGE=100.0) whenever MT5's own
    order_calc_margin is unavailable (backtests, terminal offline) — a lower
    leverage assumption makes the estimated required margin proportionally
    larger, which makes the margin-utilisation cap bind tighter, i.e. it
    functions as the leverage ceiling this field describes. 0 = defer to MT5's
    actual reported account leverage instead of this fixed figure. When MT5
    IS connected, order_calc_margin (the broker's real answer) is used
    directly and this field has no effect — enforcement then comes entirely
    from the broker's own margin requirements, which is the correct behaviour.
    """

    mt5_order_deviation_points: int = 20
    """
    [Task 1.9 / Part 11 §B6] Max allowed slippage for a live MT5 order, in MT5's own
    native `points` (the broker's `SYMBOL_POINT` — a raw price-granularity unit, NOT
    a pip or an "index point"; this is a different, MT5-idiomatic concept from the
    pip/point mismatches elsewhere in this file). 20 points is what mt5/order_manager.py
    hardcoded before this field existed — this makes it a real, user-editable setting
    rather than a constant, per your "stop deciding this for me" instruction. Left at
    20 (unchanged default) because whether that figure is too tight or too loose on any
    given instrument depends on your broker's actual reported SYMBOL_POINT for it, which
    this codebase cannot verify without a live MT5 connection — if you see orders
    rejected for "requote"/"off quotes" on a fast-moving symbol, raise this for that
    instrument (or globally) rather than assuming the default is wrong.
    """

    # ── Margin / sizing-truth guards (Phase 2, [A1]-[A12]) ────────────────
    max_margin_utilisation_pct: float = 30.0
    """
    [2.1/A1] Ceiling on required margin as % of account equity for a single
    position. Was a hardcoded module constant in position_sizer.py — this is the
    real, user-editable field. Default kept at 30% per D-1 (2026-08-22 decision):
    this is not being lowered, only made visible and editable. Every trade this
    truncates now emits sizing_diagnostics.margin_truncation_pct and
    binding_constraint="margin" so the clamp is never silent.
    """
    min_deployable_risk_pct: float = 0.0
    """
    [2.2/A1] When the margin cap (above) would truncate a trade's realised risk
    below this floor, REJECT the trade (reason margin_ceiling_below_min_risk)
    instead of sizing a token position at some tiny fraction of the requested
    risk. 0.0 = disabled (take whatever the margin cap leaves). This is what
    stops a crypto symbol from silently trading at 0.05% risk forever instead
    of surfacing "this account cannot express 1% risk on this symbol".
    """
    min_stop_spread_multiple: float = 2.0
    """
    [2.11/A8] A stop must clear the round-trip spread by this multiple before it
    is tradeable (MIN_STOP_SPREAD_MULTIPLE, formerly a module constant in
    position_sizer.py). At 1.0x the trade is stopped out by the spread alone.
    """
    confluence_risk_tiers: list[tuple[int, float]] = field(
        default_factory=lambda: [(80, 100.0), (65, 75.0), (55, 50.0)]
    )
    """
    [2.10/A7] The confluence-scaled-risk ladder (score_floor, pct_of_base_risk),
    evaluated highest-floor-first — formerly hardcoded in
    position_sizer.py:get_confluence_scaled_risk. A score below every floor here
    deploys 0% (see reject_below_confluence).
    """
    reject_below_confluence: bool = True
    """
    [2.10/D-3] Per D-3 (2026-08-22 decision): outright REJECT signals scoring
    below every tier in confluence_risk_tiers (the default, matching existing
    behaviour) rather than clamping them to some minimum size. Set False to
    instead deploy at the lowest tier's fraction for any score above 0.
    """
    post_split_risk_tolerance_pct: float = 5.0
    """
    [2.12/A9] Replaces the hardcoded 1.05/1.10/1.01 post-split risk-cap
    tolerances in risk/engine.py. actual_risk_dollars is allowed to exceed
    requested_risk_dollars by this percentage before being rejected outright as
    post_split_risk_overshoot; a smaller residual overshoot up to this same
    tolerance is logged as a warning, not rejected.
    """
    exit_slippage_pips: float | None = None
    """
    [2.9/A6] Adverse slippage applied to SL/BE_SL/TRAIL_SL/SESSION_END/TIME_LIMIT
    exits only — never TP (a limit fill does not slip against you). None =
    default to slippage_pips (the entry-side cost value), so realised risk on a
    stop-out is not systematically understated relative to what was sized.
    """
    open_risk_weight: float = 0.5
    """
    [2.21/R9] How much of a still-open position's initial risk counts as
    "already lost" against the daily/weekly drawdown budget in the predictive
    scaling guard (risk/engine.py). 1.0 = old behaviour (100% of open risk
    counted as realised loss, which let 2-3 open positions exhaust a week's
    budget by Wednesday); 0.0 = ignore open risk entirely. 0.5 keeps the
    pre-emptive size-down while only hard-blocking at true budget exhaustion.
    """
    allow_pyramiding: bool = False
    """
    [2.17/D-2/P2-R2] Per D-2 (2026-08-22): max_positions_per_symbol default
    STAYS at 1. This is the opt-in override — when True, circuit_breaker.py's
    per-symbol check is skipped and max_positions_per_symbol governs stacking
    directly instead of acting as an on/off gate. False = today's behaviour.
    """
    min_bars_between_entries: int = 0
    """
    [2.17/P2-R2] Opt-in companion to allow_pyramiding: minimum bars between two
    entries on the same symbol even when pyramiding is allowed. 0 = disabled.
    """

    # ── Portfolio governor (Phase 9 / Part 7 §7.4) ─────────────────────────
    max_cluster_risk_pct: float = 0.0
    """
    [9.5] Cap on aggregate OPEN risk (as % of balance) across every symbol in
    the same correlation cluster (e.g. XAUUSD+XAGUSD — see
    risk/portfolio_governor.py::SYMBOL_CLUSTERS). 0 = disabled (default —
    changes nothing until explicitly set). Prevents three strategies each
    independently sizing 1% risk on correlated symbols from silently
    stacking into 3% of correlated exposure.
    """
    max_net_direction_risk_pct: float = 0.0
    """
    [9.6] Cap on aggregate open risk (as % of balance) across every symbol in
    the same cluster taken in the SAME effective direction — e.g. XAUUSD
    long + XAGUSD long + EURUSD long is treated as one USD-short bet, not
    three independent ones (USDJPY/USDCHF/USDCAD's direction is inverted for
    this purpose — a USDJPY BUY is a long-USD bet, netting against an
    EURUSD SELL, not an EURUSD BUY). 0 = disabled (default).
    """
    symbol_cluster_overrides: dict[str, str] = field(default_factory=dict)
    """[9.5/9.6] Per-user overrides/additions to the default SYMBOL_CLUSTERS table. Empty = use the built-in defaults unchanged."""

    strategy_risk_budget_pct: dict[str, float] = field(default_factory=dict)
    """
    [9.7] Per-strategy share of `max_daily_drawdown_pct`/`max_weekly_drawdown_pct`,
    as a percentage OF THAT BUDGET (e.g. `{"VWAP_v1": 40.0}` means VWAP's realised
    losses today may not exceed 40% of the day's drawdown budget, leaving the rest
    for every other strategy). A strategy not listed is unrestricted (today's
    behaviour — empty dict changes nothing). This is the fix Part 7 §7.4 describes:
    "a high-frequency engine (VWAP) cannot consume the daily/weekly DD budget a
    low-frequency one (APA) needs." Scoped to REALISED P&L only — open
    (unrealised) risk remains globally shared across strategies, since
    attributing floating P&L per-strategy at guard-check time would need
    plumbing this codebase doesn't have yet (CircuitBreaker.get_open_risk() is
    account-wide, not per-strategy).
    """

    # ── Backtest <-> live parity (Phase 4) ─────────────────────────────────
    sizing_basis: Literal["STATIC", "BALANCE", "EQUITY"] = "STATIC"
    """
    [4.2/D1] What position sizing is computed against. `STATIC` (default) sizes
    every trade against `PropFirmParams.initial_balance`, never the live/running
    balance — this is the existing backtest behaviour (which always uses the
    run's fixed `initial_balance`) and matches the explicit prior decision
    recorded in risk/engine.py's evaluate_signal ("We have completely removed
    compounding per user request"). `BALANCE` sizes against the live closed
    balance (compounds as realised P&L accumulates). `EQUITY` sizes against
    live floating equity (balance + open P&L).

    BEFORE this field existed, live PERSONAL accounts silently used `BALANCE`
    (bot_service.py passed the live `account_balance` as `initial_balance` for
    any non-prop-firm account) while backtests and prop-firm accounts always
    used `STATIC` — a real backtest/live divergence for the majority
    (personal-account) case. Defaulting this to `STATIC` closes that gap by
    making personal-live match backtest, not by changing backtest. Set to
    `BALANCE`/`EQUITY` explicitly if you want live personal accounts to
    compound size with account growth.
    """

    be_spread_multiple: float = 2.0
    """
    [4.5/D6/F4] Live's break-even buffer used a hardcoded `2.0x` spread safety
    margin (`position_manager.py`); the backtest path
    (`backtester/engine.py::_breakeven_stop`) used spread directly (1x) as one
    of the `max(spread, atr_buffer, pip_buffer)` candidates — a real
    backtest/live divergence. Both now call the same
    `breakeven_manager.resolve_be_buffer()` with this shared multiplier.
    Default kept at `2.0` (live's prior, more conservative value) rather than
    backtest's looser `1.0x`: when forced to converge two diverging defaults,
    this codebase's standing policy is to pick the safer one, not silently
    thin an existing margin — see `min_sl_pips`'s docstring for the
    inverse case (where the safer choice was the OPPOSITE direction, because
    there the field was a rejection gate, not a buffer size). Net effect:
    backtest's BE buffer is now very slightly wider by default than before.
    Set to `1.0` to restore backtest's old, narrower spread term.
    """

    trail_require_be_first: bool = False
    """
    [4.6/D7/F3] Whether trailing may only begin after break-even has been
    applied. Backtest (`risk/engine.py::manage_open_position`) previously
    ALWAYS required BE first (hardcoded `if trail_method and be_applied`);
    live (`position_manager.py`) NEVER did (trailing ran independently of
    `be_applied`). Both now honour this single flag. Default `False` matches
    live's prior behaviour and is also required by Part 4's stated use case
    (trailing at 1R without waiting for break-even) — flip to `True` to
    restore backtest's old (stricter) behaviour.
    """

    # ── TP structure ─────────────────────────────────────────────────────
    tp_levels: int = 5
    tp_count: int = 3
    tp_splits: list[float] = field(default_factory=lambda: [50.0, 30.0, 20.0])
    """
    CHANGED [40,35,25] -> [50,30,20] (2026-08). RiskManagement_Spec is internally
    inconsistent here: §2.3 documents 30/25/20/15/10 across five tiers while §6.5
    documents [40,35,25] across three. Either way the change is a front-load.

    Reasoning: costs are a FIXED subtraction from every trade's R, so they are
    proportionally most damaging on the nearest target and least damaging on the runner -
    but the nearest target is also the only one with a hit rate high enough to be
    estimated from realistic sample sizes. Weighting 50% into TP1 reduces the strategy's
    dependence on a 5R tail that the forensic data shows is rarely reached, and cuts
    per-trade P&L variance. The user's own runs used 20/40/40, which does the opposite:
    it puts 80% of size on the two targets with the weakest evidence behind them.
    """

    tp1_rr: float = 1.5
    """
    CHANGED 1.0 -> 1.5 (2026-08). This is the single most consequential risk default.

    On an FX major, round-trip friction is ~3.0 pips (2.0 spread + 0.4 slippage + ~0.6
    commission at $6/lot). Against the 12-pip stops this configuration now produces, that
    is ~0.25R of dead cost on EVERY trade, win or lose.
        TP1 @ 1.0R -> win nets 0.75R, loss costs 1.25R -> break-even hit rate 62.5%
        TP1 @ 1.5R -> win nets 1.25R, loss costs 1.25R -> break-even hit rate 50.0%
    1.0R is not a target, it is a demand for a 62.5% hit rate before the strategy earns
    anything - and every self-reported win rate behind these strategy docs (64-75%) is an
    unverified marketing claim measured without costs. 1.5R restores a symmetric,
    honestly-statable requirement.

    DOC CONFLICT: RiskManagement_Spec §6.5 documents tp1_rr = 3.0 / tp2 5.0 / tp3 7.0.
    That grid is unusable for these strategies - a 3R first target on a 12-pip stop is 36
    pips, inside a 90-minute session window, on a pair whose whole daily range is ~55
    pips. The strategy docs' 1R/3R/5R grid is the operative one; 1.5/3/5 is that grid
    corrected for costs. RiskManagement_Spec.md §6.5 has been annotated accordingly.
    """

    tp2_rr: float = 3.0
    tp3_rr: float = 5.0
    tp4_rr: float = 10.0
    tp5_rr: float = 15.0

    tp_volume_pcts: list[float] | None = None
    """
    [5.1/5.2/Part4] Explicit per-TP volume allocation, replacing the
    string-ish `tp_splits`. `None` (default) keeps `tp_splits` as the active
    source — this is a deprecated ALIAS, not a removal: `MultiTPManager`
    normalises whichever one is set to the active `tp_count`, and forces
    `[100]` whenever `tp_count == 1` regardless of either field's contents
    (a single TP has nothing to split). Set this explicitly to stop using
    `tp_splits`.
    """

    # ── Break-even ───────────────────────────────────────────────────────
    be_trigger_rr: float = 2.0
    """
    CHANGED 1.5 -> 2.0 (2026-08-23), so break-even sits STRICTLY ABOVE tp1_rr.

    History: this was 1.0, then moved to 1.5 to "coincide exactly with tp1_rr" on
    the reasoning that BE should arm the moment 50% of the position is booked.
    That reasoning was right about the ordering and wrong about how to achieve it,
    because coinciding is not ordering — it is a race.

    Measured on XAUUSD/APA, 2026-06-01 to 2026-08-23 (7 signals, 21 legs):

        SL         17 legs   81%   -483.78
        TRAIL_SL    4 legs   19%    +12.34
        TP           0 legs    0%

    Zero take-profits across the whole run. With `be_trigger_rr == tp1_rr`, the
    bar that first touches 1.5R arms break-even, and TP1's limit only fills on a
    later bar — so any retracement inside the move scratches the leg at entry
    before the partial it was supposed to protect ever pays. BE was consistently
    winning the race against the event it exists to follow.

    2.0 restores the intended ordering under every `be_mode`: TP1 fills at 1.5R,
    BE cannot arm on RR until 2.0R, and under TP_HIT/EITHER the TP1 fill itself
    arms it first anyway. See `be_mode` below, which is the primary fix.
    """

    be_buffer_pips: float = 0.0
    """
    CHANGED 2.0 -> 0.0 (2026-08). A FIXED pip buffer is scale-blind, and at these stop
    distances it is actively dangerous.

    The user's runs used be_buffer_pips = 10 against APA stops with a 3.47-pip median.
    On BE the SL is set to entry + 10 pips = entry + ~2.9R - which is ABOVE the market at
    the moment BE fires (price is at ~1R). backtester/engine.py:434 sees an SL on the
    wrong side of the open, classifies it as a gap, and fills it. Net effect: every
    surviving sub-position is force-closed at a profit the strategy never earned, the
    bar after TP1. That is a mechanical profit generator, and it is a strong candidate
    for the artifact behind "winners sized 1.63x larger than losers" and a positive
    dollar P&L on a -0.367R expectancy.

    0.0 means BE moves the stop to exactly entry. Spread cover is supplied by
    be_buffer_atr_mult and by BreakevenManager's live-spread term - both scale-aware.

    ENGINE BUG: backtester/engine.py:478 and portfolio_engine.py:400 (the TP1-hit
    sibling-BE path) read ONLY be_buffer_pips - they ignore be_buffer_atr_mult and the
    live spread that BreakevenManager correctly uses via max(spread, atr, pips). With
    this default they now behave correctly by accident; they should be fixed to use the
    same max() so a user re-raising be_buffer_pips cannot resurrect the artifact.
    """

    be_offset_pips: float = 0.0  # legacy alias kept for db compat - tracks be_buffer_pips

    be_buffer_atr_mult: float = 0.10
    """
    CHANGED 0.0 -> 0.10 (2026-08). The scale-aware replacement for be_buffer_pips.
    BreakevenManager applies buffer = max(live_spread, atr_mult x ATR, pips x pip_size),
    so this guarantees the break-even stop clears at least a fraction of current
    volatility no matter the instrument. 0.10 x ATR is ~0.7 pips on USDCHF M15 and ~1.5
    NQ points - enough to cover the spread without ever landing above the market.
    """

    be_mode: Literal["RR", "TP_HIT", "EITHER", "NONE"] = "EITHER"
    """
    [5.1/5.3/Part4] `RR` ignores `tp1_hit` and fires purely off `be_trigger_rr`.
    `TP_HIT` fires only when `be_trigger_tp_level` actually CLOSES. `EITHER`
    fires on whichever comes first. `NONE` disables BE entirely.

    STAYS "EITHER" (2026-08-23), with the real fix applied to the THRESHOLD
    instead — see `be_trigger_rr` above.

    The zero-take-profit result was never caused by this mode. It was caused by
    `be_trigger_rr == tp1_rr`: two conditions on the same price, one a level
    TOUCH and one a limit FILL. The touch is true on the bar that reaches the
    price; the fill needs the next bar. So the touch always won, and the partial
    it was meant to follow never paid.

    Separating the thresholds (BE at 2.0R, TP1 at 1.5R) removes the race without
    removing a trigger — which matters, because `TP_HIT` alone has a real hole:
    if TP1 never fills (single-TP setups, a structural target the grid overrode,
    a gap straight through), break-even would never arm AT ALL and the position
    would ride to its original stop. `EITHER` keeps the R-multiple as a backstop
    for exactly that case.

    So the ladder is: TP1 fills at 1.5R and arms BE; if it somehow does not, the
    2.0R touch arms it anyway. Both triggers live, neither able to pre-empt the
    take-profit.
    """
    be_trigger_tp_level: int = 1
    """[5.1/5.3] Which TP level's close counts as the BE trigger under `TP_HIT`/`EITHER` modes. Default 1 = today's `be_on_tp1_hit` behaviour."""

    # ── Circuit breakers — portfolio-level (always active) ───────────────
    # All four retained at RiskManagement_Spec §6.5 values. They were not the problem:
    # the forensic failures came from stop sizing upstream, and a drawdown breaker cannot
    # protect an account from a strategy whose stops sit inside the spread — it can only
    # decide how fast the loss is booked. Verified against spec rather than re-tuned.
    max_daily_drawdown_pct: float = 3.0    # spec §6.3/§6.5 default 3.0 (range 1-10)
    max_weekly_drawdown_pct: float = 6.0   # spec §6.3/§6.5 default 6.0 (range 3-20)
    max_daily_trades: int = 5  # DEPRECATED: now per-strategy (VWAPParams.max_trades_per_day, etc.). Kept as fallback.
    max_concurrent_positions: int = 3
    """
    Spec §6.5 default 3 (range 1-10). Retained — but note it only became defensible
    once risk_per_trade_pct dropped to 0.5: 3 x 0.5% = 1.5% aggregate at risk against a
    3.0% daily breaker. At the old 1.0% it was 3.0%, i.e. no headroom at all.
    """
    max_positions_per_symbol: int = 1

    max_daily_trades_by_strategy: dict[str, int] = field(default_factory=dict)
    """
    [2.18/P2-R3] Per-strategy override of max_daily_trades, keyed by strategy_id
    (e.g. {"APA_v1": 8}). A strategy not listed here falls back to the global
    max_daily_trades. Empty dict = today's global-only behaviour.
    """
    max_concurrent_positions_by_strategy: dict[str, int] = field(default_factory=dict)
    """
    [2.19/P2-R4] Per-strategy override of max_concurrent_positions, keyed by
    strategy_id. A high-frequency engine (e.g. VWAP) can be given a smaller
    concurrent-position budget than a low-frequency one (e.g. APA) without
    lowering the global cap for everyone. Empty dict = global-only behaviour.
    """

    # Target profit halts
    target_profit_enabled: bool = False
    max_daily_profit: float = 500.0
    max_weekly_profit: float = 2000.0

    # Trailing stops
    trail_method_tp1: str = "NONE"
    """
    [5.1/5.2/F2] Did not exist before — `MultiTPManager.trail_methods[0]` was
    hardcoded `None`, so with `tp_count=1` trailing was structurally
    impossible no matter what was configured. `"NONE"` (default) preserves
    that prior behaviour exactly; set to `ATR_TRAIL`/`STRUCTURE_TRAIL`/
    `FIXED_PIPS`/`PCT_TRAIL` to enable trailing on a single-TP position.
    """
    trail_method_tp2: str = "ATR_TRAIL"
    trail_method_tp3: str = "STRUCTURE_TRAIL"
    trail_method_tp4: str = "ATR_TRAIL"
    trail_method_tp5: str = "STRUCTURE_TRAIL"
    atr_trail_multiplier: float = 1.5
    atr_trail_multiplier_tp1: float = 1.5
    atr_trail_multiplier_tp2: float = 1.5
    atr_trail_multiplier_tp3: float = 1.5
    atr_trail_multiplier_tp4: float = 1.5
    atr_trail_multiplier_tp5: float = 1.5
    trail_pips: float = 15.0
    trail_pct: float = 0.5
    trail_activation_rr: float = 2.0
    """
    CHANGED 1.5 -> 2.0 (2026-08-23), for the same reason as `be_trigger_rr`.

    The previous note here already identified the failure exactly — *"the trail
    could scratch the position out before the partial it was supposed to protect
    had been taken"* — and then set this value EQUAL to `tp1_rr`, which does not
    fix that. Equal is not "after": both conditions become true on the same bar,
    and a level-touch beats a limit-fill because the fill needs a subsequent bar.

    That is visible in the measured run: of 21 legs, 4 exited `TRAIL_SL` and 0
    exited at a take-profit. The trail was activating at the exact price TP1 was
    waiting at, then trailing the leg out of the move.

    2.0, plus `trail_mode = "TP_HIT"` below, puts the de-risking ladder in the
    only order that makes sense: TP1 fills -> break-even arms -> trail activates.
    """

    trail_mode: Literal["RR", "TP_HIT", "EITHER", "NONE"] = "EITHER"
    """
    [5.1/5.4/Part4] Same semantics as `be_mode`, for trailing's activation.
    `RR` activates once `unrealized_r >= trail_activation_rr`, independent of
    any TP fill. `TP_HIT` activates only once `trail_trigger_tp_level` has
    actually CLOSED. `EITHER` accepts both.

    CHANGED "RR" -> "EITHER" (2026-08-23), matching `be_mode`.

    Under the old `RR` + `trail_activation_rr == tp1_rr` pairing, the trail armed
    on a price touch at exactly the level TP1 was waiting at and trailed the
    position out before the partial filled — 4 of 21 legs in the measured run
    exited `TRAIL_SL` with 0 take-profits anywhere. The fix is the separated
    threshold (2.0R, above TP1's 1.5R), not the removal of the R-multiple
    trigger: `EITHER` keeps trailing reachable on a runner that blows through
    the grid without a clean fill.

    `trail_trigger_rr` below is the user-facing name for the threshold;
    `trail_activation_rr` is the value actually read, for backward compatibility
    (both must agree — see 5.4/5.5's engine wiring).
    """
    trail_trigger_rr: float = 2.0
    """[5.1/5.4] User-facing alias for `trail_activation_rr` under the new exit-ladder naming. Keep the two in sync — see `trail_mode`. CHANGED 1.5 -> 2.0 alongside it."""
    trail_trigger_tp_level: int = 1
    """[5.1/5.4] Which TP level's close activates trailing under `TP_HIT`/`EITHER` `trail_mode`."""

    trail_step_pips: float = 5.0
    trail_structure_bars: int = 3
    trailing_stop_activation_rr: float = 2.0  # legacy alias
    trailing_step_pips: float = 5.0           # legacy alias

    # DEPRECATED: moved to PropFirmParams.max_daily_loss_pct. Kept for db compat.
    max_daily_loss_pct: float = 5.0


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY TWO (CRASHBOOM) PARAMETERS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DriftJumpAlphaParams:
    """
    Tunable parameters for the Drift & Jump Alpha engine.
    Source: docs/DriftJumpAlpha_Strategy_Spec_v2.md

    AUDITED 2026-08 — NO DEFAULTS CHANGED. Every field below already matches the spec
    exactly, and DriftJumpAlpha is the only strategy in the book that shipped with an
    explicit exposure ceiling. It is also structurally exempt from the FX cost-floor
    problem that drove the rest of this audit: it trades Deriv synthetics, where §8
    measures round-trip spread at ~1,430 points on Crash 1000, and its stop is an
    ATR/structure stop (§7: `atr_multiple: 1.5`, `buffer_atr_multiple: 0.2`) rather than
    a wick-flush structural stop, so it cannot degenerate to a sub-spread distance.

    Spec §1 fields NOT yet exposed here (each is currently hard-coded or absent in
    strategy_two/engine.py — enumerated for the backlog, deliberately NOT added as
    parameters, because adding a config field the engine never reads is exactly the
    dead-parameter failure this audit found in strategies four and five):
      min_ema_separation_atr_multiple (0.2), adx_period (14),
      pullback_max_distance_atr_multiple (1.0), swing_lookback_bars (5),
      exit atr_multiple (2.0) + adaptive low/high vol variants (1.5/2.5),
      min_hold_bars_before_trailing (3), stop_loss buffer_atr_multiple (0.2),
      widen_multiple_at_hard_threshold (1.5), flatten_all_at_percentile,
      max_concurrent_positions (1), max_weekly_drawdown_pct (8.0).
    """
    drift_ema_fast: int = 20       # spec §1 fast_ema_period
    drift_ema_slow: int = 50       # spec §1 slow_ema_period
    min_adx_to_trade: int = 20     # spec §1 secondary_trend_strength_filter.min_adx_to_trade
    jump_entry_percentile_threshold: float = 95.0  # spec §1/§6

    trade_jumps_enabled: bool = False
    """
    Spec §6: Setup B is "experimental, opt-in, hard-gated" and §9 found no evidence for
    the timing edge it assumes on Crash 1000. Correctly False. Do not flip this to True
    as a convenience default — §1 states the gate must not "become a simple default-off
    flag", and §6 notes Setup B's deliberately tighter stop mechanically produces LARGER
    lots for the same risk %, which is the same arithmetic that produced the 40-lot APA
    positions elsewhere in this book.
    """

    control_test_passed: bool = False  # spec §8 gate — must stay False until a real pass is on record

    aggregate_max_lots_per_symbol: float = 6.0
    """
    Spec §1 challenge_account_lot_ceiling: 6.0, "matches the $25k Crash 1000 BloomFunded
    tier used in current backtests", enforced as "a hard clamp applied AFTER the risk-%
    sizing formula, before order submission — never a soft warning".

    This is the correct pattern and it is why the new RiskParams.max_account_leverage was
    added: the 160x-leverage APA case would have been caught by an equivalent ceiling.
    Re-check this value whenever account size or challenge provider changes — it is a
    broker rule, not a tuning knob, and §1 says it must "never be inferred".
    """

    # ── Previously dead config (referenced via getattr(..., 0) in engine.py but
    # never defined here, so UI-submitted values were silently dropped by the
    # backtest route's hasattr() filter before ever reaching the dataclass) ──
    spike_threshold_pips: float = 0.0
    """
    Jump-detection threshold in pips (via get_pip_size()). 0 = disabled, falls back
    to the engine's ATR-based jump threshold (4x ATR) instead.
    """
    recovery_target_pips: float = 0.0
    """
    Minimum post-drift recovery distance (in pips) folded into Setup A's take-profit
    floor. 0 = disabled, TP floor uses only the ATR/RR-based targets.
    """

    # ── Spec §1 risk guardrails (previously unimplemented as trading gates —
    # the generic circuit breaker enforces portfolio-level caps, but not these
    # strategy-specific values). Defaults per spec §1's risk_management block. ──
    max_trades_per_day: int = 6
    max_daily_risk_pct: float = 4.0
    max_consecutive_losses: int = 4
    cooldown_after_max_losses_hours: int = 12
    min_rrr_to_accept_trade: float = 1.5

    max_losses_per_day: int = 0
    """
    [12.2/Part14] Generic daily loss guardrail, standardised across every
    strategy (was only on VWAPParams). Distinct from `max_consecutive_losses`
    (which triggers a time-based cooldown regardless of how the day's other
    trades went) — this is a flat daily count, same semantic as VWAP's
    `max_losses_per_day`. 0 = disabled (this strategy's existing
    `max_consecutive_losses`/cooldown mechanism remains the operative guard
    by default).
    """

    adx_gate_mode: Literal["BLOCK", "REDUCED_SIZE"] = "REDUCED_SIZE"
    """
    [6.13/S18] What to do when ADX falls below `min_adx_to_trade`. CHANGED
    from a hard BLOCK (the entire Drift Regime was declared inactive and the
    setup discarded). `REDUCED_SIZE` (default) instead scales `size_modifier`
    by the current ADX's percentile rank within its own recent (100-bar)
    history — a weak-but-not-bottom-decile trend still trades, just smaller,
    rather than being treated identically to a dead-flat market. `BLOCK`
    restores the old behaviour exactly.
    """
    adx_gate_min_size_modifier: float = 0.1
    """[6.13/S18] Floor on the ADX-percentile size multiplier under REDUCED_SIZE mode — never scales a sub-threshold-ADX trade down to zero size, only to this minimum fraction."""

    tp1_rr_override: float | None = 0.5
    """
    [2.15] Replaces the strategy_id == "DriftJumpAlpha" branch that used to be
    hardcoded into risk/multi_tp.py::calculate_tp_levels. Kept at 0.5 (the
    prior hardcoded value) by default — crash-spike setups reverse fast, so a
    much tighter TP1 than the global RiskParams.tp1_rr is intentional. None =
    use the global tp1_rr like every other strategy. Threaded into
    MultiTPManager via risk_config["tp1_rr_overrides_by_strategy"], built by
    the caller (backtest.py routes / bot_service.py) — MultiTPManager is a
    single shared instance across every strategy in a portfolio run, so this
    cannot be a flat global override.
    """


@dataclass
class CRTParams:
    """
    Tunable parameters for the Candle Range Theory engine.
    Spec: CRT_Strategy_Spec.md
    The SL formula (sl_dist = tp_dist / target_r_multiple) is per-spec (Section 6).
    min_sl_pips and sl_atr_mult are additional guards added above the spec to prevent
    microscopic SL values on small HTF candles causing huge lot sizes in live execution.
    """
    htf_timeframe: str = "H1"      # spec §2 default 1H ("highest-quality per source")
    ltf_timeframe: str = "M5"      # spec §2 default 5M

    target_r_multiple: float = 1.5
    """
    Spec §2/§6 default 1.5 (valid range 1.5-2.0). HELD at 1.5 — and note this parameter
    runs BACKWARDS relative to every other strategy's RR setting.

    CRT derives the stop from the target: `sl_distance = tp_distance / target_r_multiple`
    (spec §6), where tp_distance is fixed by structure (C1's opposite extreme). Raising
    target_r_multiple therefore TIGHTENS the stop rather than extending the target. At the
    top of the spec's range (2.0) the stop is 25% tighter than at 1.5 for the identical
    setup. Given that sub-spread stops were the dominant failure mode across the whole
    book, the correct default here is the BOTTOM of the doc's range, not the middle.
    Anyone raising this is buying a better headline R by moving the stop closer to noise.
    """

    max_trades_per_session: int = 1   # spec §2/§10 — one signal per session by design
    session_start: str = "09:30"      # spec §7 — NY open, "highest-quality setups"
    session_cutoff: str = "12:00"     # spec §7 — stop new searches ~12:00-13:00 ET
    bypass_session_synthetics: bool = True  # spec §8 — 24/7 synthetics have no session anchor

    max_losses_per_day: int = 0
    """[12.2/Part14] Generic daily loss guardrail, standardised across every strategy (was only on VWAPParams). 0 = disabled."""

    # Minimum SL floors — prevent spec-correct but tiny SLs causing extreme lot sizes.
    # REVIEWED 2026-08 and retained unchanged. CRT is the ONLY strategy in the book that
    # already had a cost floor, and it is the reason CRT does not appear in the forensic
    # review's list of degenerate-stop offenders. These two fields are the pattern that
    # has now been replicated into APAParams, HTFFVGFlipParams, BiasIFVGParams and
    # VWAPParams — and unlike those copies, THESE ARE ACTUALLY READ BY THE ENGINE
    # (strategy_three_crt/engine.py:222-250).
    min_sl_pips: float = 15.0     # Hard minimum SL distance in pips (~5x FX-major friction)
    sl_atr_mult: float = 1.0      # SL must be at least N × ATR (0 = disabled)

    bias_neutral_mode: Literal["BLOCK", "REDUCED_SIZE", "ALLOW"] = "REDUCED_SIZE"
    """
    [6.8/S9] What to do with a valid C2 sweep when the HTF bias is NEUTRAL
    (no confirmed trend). CHANGED from a hard BLOCK — measured on real CRT/NDX
    logs, this discarded 254 of ~900 evaluations outright, the single largest
    rejection category. `BLOCK` restores the old behaviour exactly. `ALLOW`
    trades a NEUTRAL-bias C2 sweep at full size (direction taken from the
    sweep itself, not HTF confirmation). `REDUCED_SIZE` (default) trades it
    at `bias_neutral_size_modifier` instead of skipping it — the setup is
    real, just less confirmed without a trend behind it.
    """
    bias_neutral_size_modifier: float = 0.5
    """[6.8/S9] Size multiplier applied when bias_neutral_mode == "REDUCED_SIZE" and the HTF bias was NEUTRAL at trigger time."""

    trigger_grace_bars: int = 2
    """
    [6.10/S12] Number of ADDITIONAL HTF candle closes a live c2_trigger may
    survive without the LTF trigger firing, before being invalidated. CHANGED
    from 0 (any trigger not fired before the very next HTF close was
    discarded) — the forensic review flagged this as a real cause of lost
    setups: "Trigger timeout — LTF did not fire before next HTF close", with
    no grace at all. 0 restores the old immediate-invalidation behaviour.
    """

    # ARCHITECTURAL DISCREPANCY (reported, not fixed — outside params scope):
    # The whole point of CRT's backward SL derivation is to make the structural TP land at
    # exactly target_r_multiple. But the live/backtest TP ladder is owned by RiskParams
    # (tp1_rr/tp2_rr/tp3_rr = 1.5/3/5), which overrides the strategy's take_profit. So the
    # SL is reverse-engineered to hit a 1.5R target that is then discarded and replaced by
    # a 1.5R/3R/5R grid whose upper tiers sit far beyond C1's extreme — the level the
    # entire setup thesis says price is travelling to. Either CRT should be exempted from
    # the grid, or its SL should be placed structurally (beyond the C2 sweep wick).


# ─────────────────────────────────────────────────────────────────────────────
# INSTRUMENT PROFILE SETTINGS (per-user overrides)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class InstrumentSettings:
    """
    DEPRECATED [12.1/12.3/Part14] — superseded by `InstrumentSlot` /
    `UserConfigV2.instrument_slots`. Kept (not removed) so an old saved
    config JSON still round-trips: `UserConfigV2.from_dict` auto-migrates any
    `instrument_settings` list into one `InstrumentSlot` per entry (12.4)
    whenever the incoming data has no `instrument_slots` of its own. Do not
    add new fields here — add them to `InstrumentSlot` instead.

    Per-user instrument-level settings that override the global instrument profile.
    Stored in UserConfigV2. Allows users to customize behaviour per symbol.
    """
    symbol:          str
    strategy_id:     str   = "APA_v1"       # The registry ID of the strategy to run
    enabled:         bool  = True           # Trade this symbol at all
    max_lot_override: float | None = None # Cap lot size (safety)
    custom_sl_buffer: float | None = None # Override profile's sl_buffer_pips
    notes:           str  = ""              # User label (e.g. "V75 main account")


@dataclass
class InstrumentSlot:
    """
    [12.1/Part14] One symbol+strategy pairing, with optional per-slot risk
    overrides. Replaces `InstrumentSettings`' "one strategy per symbol" model:
    the SAME symbol may now appear in more than one slot under different
    `strategy_id` values (e.g. USDCHF running VWAP_v1 and APA_v1
    concurrently) — something a symbol-keyed structure could never express.

    `slot_id` is a real UUID, deliberately NOT derived from `symbol` — the
    old code's `group_id = signal.symbol` bug (fixed at [4.8]) is exactly the
    failure mode of using a natural key as an identity key when that key
    later needs to not be unique. Two slots that happen to share a symbol
    must never collide.
    """
    slot_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    symbol: str = ""
    strategy_id: str = "APA_v1"
    enabled: bool = True

    # ── Per-slot overrides — None means "inherit the global RiskParams /
    # strategy default", exactly like every other None-defaulted override
    # field elsewhere in this codebase (see e.g. RiskParams.min_sl_pips's own
    # "activating an unrequested floor by default" reasoning — same principle
    # here: a slot with no explicit override must reproduce global behaviour
    # exactly, not silently apply some other default). ──
    risk_per_trade_pct: float | None = None
    max_trades_per_day: int | None = None
    max_positions_per_symbol: int | None = None
    max_losses_per_day: int | None = None
    strategy_params_override: dict = field(default_factory=dict)
    """
    Sparse per-field overrides merged onto the resolved strategy's own Params
    dataclass for THIS slot only (e.g. `{"tp1_rr_override": 0.5}` for a
    DriftJumpAlpha slot that wants a tighter target than every other
    DriftJumpAlpha slot). Empty dict = no overrides, use the strategy's
    global Params unchanged.
    """

    # Carried over from InstrumentSettings — same meaning, same field names,
    # so the 12.4 migration is a direct 1:1 copy for these two.
    max_lot_override: float | None = None
    custom_sl_buffer: float | None = None
    notes: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# PROP FIRM SETTINGS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PropFirmParams:
    """
    Prop Firm / Broker account configuration.
    Manually configured per firm (FundedNext, FIVR, FundedPeace, Exness, FBS, etc.).
    Drawdown fields are HARD CIRCUIT BREAKERS when account_mode = 'prop_firm'.
    max_risk_hard_cap_pct is in RiskParams — active for ALL modes.
    All fields are user-configurable from the Settings UI.
    """
    account_mode: Literal["personal", "prop_firm"] = "personal"
    firm_name: str = ""                    # e.g. "FundedNext", "FIVR", "Exness"
    challenge_type: str = "none"           # Free text: "1-step", "2-step", "express", etc.
    account_size: float = 10000.0
    initial_balance: float = 10000.0

    # Drawdown configuration
    drawdown_type: Literal["static", "trailing"] = "trailing"
    max_daily_loss_pct: float = 5.0          # Daily drawdown limit (%)
    max_total_drawdown_pct: float = 10.0     # Overall drawdown from peak/initial (%)
    drawdown_uses_equity: bool = True        # True = floating equity; False = closed balance

    # Trading rules
    overnight_holding_allowed: bool = True
    weekend_holding_allowed: bool = True
    news_trading_allowed: bool = True
    news_blackout_before_min: int = 15       # Minutes before high-impact event
    news_blackout_after_min: int = 45        # Minutes after high-impact event

    # Lot caps (per-symbol overrides)
    max_lot_sizes: dict[str, float] = field(default_factory=dict)
    default_max_lot: float | None = None
    """
    [3.10/E7] Replaces prop_firm_validator.py's hardcoded 999.0 default lot cap
    (applied to any symbol not listed in max_lot_sizes). None = no cap.
    """

    # Position limits — [3.10/E7] replace prop_firm_validator.py's hardcoded 5/13.
    max_positions_per_symbol: int = 5
    max_total_positions: int = 13

    # Challenge pass conditions
    profit_target_pct: float = 0.0           # 0 = no target (personal accounts)
    min_trading_days: int = 0                # 0 = no minimum
    trading_day_rule: Literal["ANY_TRADE", "ANY_CLOSED", "PROFIT_PCT"] = "ANY_TRADE"
    """
    [3.11/E8] Replaces prop_firm_validator.py's hardcoded 0.005 (0.5%) trading-day
    threshold. ANY_TRADE = a day counts once a trade opens (today's behaviour
    approximated). ANY_CLOSED = a day counts once a trade closes. PROFIT_PCT = a
    day counts only once realised profit that day exceeds trading_day_profit_pct.
    """
    trading_day_profit_pct: float = 0.5


# ─────────────────────────────────────────────────────────────────────────────
# FULL USER CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class UserConfig:
    """Legacy base structure."""
    user_id:    str = ""
    risk:       RiskParams = field(default_factory=RiskParams)
    
    mt5_account: int  = 0
    mt5_server:  str  = ""
    magic_base:  int  = 1001

    llm_provider: Literal["claude", "openai", "gemini", "none"] = "none"
    llm_model:    str  = ""
    llm_auto_analyze_live:     bool = False
    llm_auto_analyze_backtest: bool = False

    notify_trade_open:     bool = True
    notify_trade_close:    bool = True
    notify_sl_hit:         bool = True
    notify_be_applied:     bool = True
    notify_daily_limit:    bool = True
    notify_signal:         bool = False
    notify_daily_summary:  bool = True
    notify_llm_ready:      bool = True

    telegram_bot_token:    str  = ""
    telegram_chat_id:      str  = ""

    backtest_auto_save:    bool = False

    def to_dict(self) -> dict:
        import dataclasses
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "UserConfig":
        risk_data = data.pop("risk", {})
        
        import dataclasses
        known_fields = {f.name for f in dataclasses.fields(cls)}
        filtered_data = {k: v for k, v in data.items() if k in known_fields}
        
        config = cls(**filtered_data)
        config.risk = RiskParams(**risk_data)
        return config



@dataclass 
class UserConfigV2(UserConfig):
    """
    Extended UserConfig with instrument settings and multi-strategy support.
    """
    instrument_settings: list[InstrumentSettings] = None
    """DEPRECATED [12.3] — see InstrumentSettings' own docstring. Use `instrument_slots` for anything new."""
    instrument_slots: list[InstrumentSlot] = None
    """[12.1/12.3/Part14] The authoritative symbol+strategy configuration — see InstrumentSlot."""
    drift_jump_alpha: DriftJumpAlphaParams = field(default_factory=DriftJumpAlphaParams)
    crt: CRTParams = field(default_factory=CRTParams)
    htf_fvg_flip: HTFFVGFlipParams = field(default_factory=HTFFVGFlipParams)
    bias_ifvg: BiasIFVGParams = field(default_factory=BiasIFVGParams)
    ny_open_retest: NYOpenRetestParams = field(default_factory=NYOpenRetestParams)
    apa: APAParams = field(default_factory=APAParams)
    vwap: VWAPParams = field(default_factory=VWAPParams)
    prop_firm: PropFirmParams = field(default_factory=PropFirmParams)

    @classmethod
    def from_dict(cls, data: dict) -> "UserConfigV2":
        risk_data = data.pop("risk", {})
        instrument_data = data.pop("instrument_settings", None)
        instrument_slots_data = data.pop("instrument_slots", None)
        drift_jump_alpha_data = data.pop("drift_jump_alpha", {})
        crt_data = data.pop("crt", {})
        htf_fvg_flip_data = data.pop("htf_fvg_flip", {})
        bias_ifvg_data = data.pop("bias_ifvg", {})
        ny_open_retest_data = data.pop("ny_open_retest", {})
        apa_data = data.pop("apa", {})
        vwap_data = data.pop("vwap", {})
        prop_firm_data = data.pop("prop_firm", {})
        import dataclasses
        known_fields = {f.name for f in dataclasses.fields(cls)}
        filtered_data = {k: v for k, v in data.items() if k in known_fields}
        
        config = cls(**filtered_data)
        
        def filter_kwargs(dataclass_type, data_dict):
            if not isinstance(data_dict, dict): return {}
            known = {f.name for f in dataclasses.fields(dataclass_type)}
            return {k: v for k, v in data_dict.items() if k in known}

        config.risk = RiskParams(**filter_kwargs(RiskParams, risk_data))
        config.drift_jump_alpha = DriftJumpAlphaParams(**filter_kwargs(DriftJumpAlphaParams, drift_jump_alpha_data))
        config.crt = CRTParams(**filter_kwargs(CRTParams, crt_data))
        config.htf_fvg_flip = HTFFVGFlipParams(**filter_kwargs(HTFFVGFlipParams, htf_fvg_flip_data))
        config.bias_ifvg = BiasIFVGParams(**filter_kwargs(BiasIFVGParams, bias_ifvg_data))
        config.ny_open_retest = NYOpenRetestParams(**filter_kwargs(NYOpenRetestParams, ny_open_retest_data))
        config.apa = APAParams(**filter_kwargs(APAParams, apa_data))
        config.vwap = VWAPParams(**filter_kwargs(VWAPParams, vwap_data))
        config.prop_firm = PropFirmParams(**filter_kwargs(PropFirmParams, prop_firm_data))
        

        if instrument_data:
            config.instrument_settings = [InstrumentSettings(**filter_kwargs(InstrumentSettings, i)) for i in instrument_data]
        else:
            config.instrument_settings = []

        # [12.4/Part14] Migration: a saved config from before instrument_slots
        # existed carries only instrument_settings. Auto-migrate it to one
        # slot per entry so upgrading never drops a running bot's settings —
        # this runs EVERY load, not just once, so it stays correct even if
        # the DB row is never re-saved. `slot_id` is derived deterministically
        # (uuid5 of symbol+strategy_id, not a fresh uuid4 each load) so the
        # same old-format config always migrates to the SAME slot identity —
        # a fresh random id on every load would make circuit-breaker/UI state
        # keyed by slot_id appear to be a "new slot" on every config reload.
        if instrument_slots_data:
            config.instrument_slots = [InstrumentSlot(**filter_kwargs(InstrumentSlot, s)) for s in instrument_slots_data]
        elif config.instrument_settings:
            config.instrument_slots = [
                InstrumentSlot(
                    slot_id=uuid.uuid5(uuid.NAMESPACE_OID, f"{s.symbol}:{s.strategy_id}").hex[:12],
                    symbol=s.symbol,
                    strategy_id=s.strategy_id,
                    enabled=s.enabled,
                    max_lot_override=s.max_lot_override,
                    custom_sl_buffer=s.custom_sl_buffer,
                    notes=s.notes,
                )
                for s in config.instrument_settings
            ]
        else:
            config.instrument_slots = []

        return config

    def __post_init__(self):
        if self.instrument_settings is None:
            self.instrument_settings = []
        if self.instrument_slots is None:
            # [12.4] Same migration as from_dict, for the direct-construction
            # path (e.g. `UserConfigV2(instrument_settings=[...])` in code/tests).
            self.instrument_slots = [
                InstrumentSlot(
                    slot_id=uuid.uuid5(uuid.NAMESPACE_OID, f"{s.symbol}:{s.strategy_id}").hex[:12],
                    symbol=s.symbol, strategy_id=s.strategy_id, enabled=s.enabled,
                    max_lot_override=s.max_lot_override, custom_sl_buffer=s.custom_sl_buffer,
                    notes=s.notes,
                )
                for s in self.instrument_settings
            ]
        if self.drift_jump_alpha is None:
            self.drift_jump_alpha = DriftJumpAlphaParams()
        if self.crt is None:
            self.crt = CRTParams()
        if self.htf_fvg_flip is None:
            self.htf_fvg_flip = HTFFVGFlipParams()
        if self.bias_ifvg is None:
            self.bias_ifvg = BiasIFVGParams()
        if self.ny_open_retest is None:
            self.ny_open_retest = NYOpenRetestParams()
        if self.apa is None:
            self.apa = APAParams()
        if self.vwap is None:
            self.vwap = VWAPParams()
        if self.prop_firm is None:
            self.prop_firm = PropFirmParams()

    def get_risk_amount(self, account_balance: float, state=None) -> float:
        return account_balance * (self.risk.risk_per_trade_pct / 100)

    def validate_exit_ladder(self) -> list[str]:
        """
        Warn when a de-risking trigger sits at or below the take-profit it is
        supposed to follow.

        This is the collision that produced 0 take-profits across a 21-leg
        XAUUSD/APA run: `be_trigger_rr == tp1_rr` puts a level TOUCH and a limit
        FILL on the same price, and the touch is true one bar earlier. Break-even
        then armed before the partial it exists to protect could pay, every time.

        Same pattern as `validate_slot_position_caps` — informational warnings
        for the caller to surface, never raises, never blocks a save. The point
        is that this specific mistake is silent in the results (it looks like a
        strategy with no edge), so it has to be loud in the config.
        """
        warnings: list[str] = []
        r = self.risk

        # Only the TP levels actually in play. A 5-level grid with tp_count=1
        # has exactly one real target; warning about tp3_rr would be noise.
        tp_levels = [
            (i, getattr(r, f"tp{i}_rr", None))
            for i in range(1, max(1, int(r.tp_count or 1)) + 1)
        ]
        tp_levels = [(i, v) for i, v in tp_levels if v]

        # The hazard is COINCIDENCE, not ordering. Break-even is SUPPOSED to sit
        # below the take-profit it protects — that is its entire job. What breaks
        # is when the two land on the same R-multiple: a level touch is true on
        # the bar that reaches the price, a limit fill needs the next one, so the
        # touch wins and the target it was meant to follow never pays.
        #
        # An earlier version of this check flagged `be_trigger_rr <= tp1_rr`,
        # which fires on every correctly-ordered ladder (BE 2R, TP 5R) and told
        # the user their working config was broken. Tolerance is relative so it
        # scales: 2% of the TP level.
        def _collides(trigger: float, tp: float) -> bool:
            return abs(trigger - tp) <= max(0.02 * abs(tp), 1e-9)

        if r.be_mode in ("RR", "EITHER"):
            for i, tp in tp_levels:
                if _collides(r.be_trigger_rr, tp):
                    warnings.append(
                        f"Break-even arms at {r.be_trigger_rr}R and TP{i} sits at {tp}R — "
                        f"the same level. A price touch beats a limit fill by one bar, so "
                        f"break-even will pre-empt TP{i} and it may never pay. Separate the "
                        f"two, or set be_mode to TP_HIT."
                    )
                    break

        trail_rr = r.trail_activation_rr
        if r.trail_mode in ("RR", "EITHER") and trail_rr:
            for i, tp in tp_levels:
                if _collides(trail_rr, tp):
                    warnings.append(
                        f"Trailing activates at {trail_rr}R and TP{i} sits at {tp}R — the "
                        f"same level, so the trail can take the position out before TP{i} "
                        f"fills. Separate the two, or set trail_mode to TP_HIT."
                    )
                    break

        # Break-even above the LAST target is inert: the position is closed by
        # then, so break-even can never arm. Different failure, same category —
        # a setting that silently does nothing.
        if tp_levels and r.be_mode in ("RR", "EITHER"):
            last_i, last_tp = tp_levels[-1]
            if r.be_trigger_rr > last_tp:
                warnings.append(
                    f"Break-even arms at {r.be_trigger_rr}R but the final target TP{last_i} "
                    f"is at {last_tp}R — the position is already closed by then, so "
                    f"break-even never fires."
                )

        # The user-facing alias must agree with the value actually read, or the
        # UI shows one number while the engine uses another.
        if abs(r.trail_trigger_rr - r.trail_activation_rr) > 1e-9:
            warnings.append(
                f"trail_trigger_rr ({r.trail_trigger_rr}) and trail_activation_rr "
                f"({r.trail_activation_rr}) disagree. The engine reads "
                f"trail_activation_rr; the UI shows trail_trigger_rr."
            )

        return warnings

    def validate_slot_position_caps(self) -> list[str]:
        """
        [12.9/Part14] Cross-validation: a slot's own `max_positions_per_symbol`
        override may not, in aggregate across every ENABLED slot, push total
        possible concurrent positions past the global
        `max_concurrent_positions` — same error PATTERN as the existing
        risk_per_trade_pct-vs-max_risk_hard_cap_pct warning (Settings/Risk.jsx
        / Backtester.jsx): informational, not a hard save-blocker — this
        returns warning strings for the caller to surface, it never raises.

        Returns one message per offending slot (empty list = no issue).
        """
        warnings: list[str] = []
        enabled_slots = [s for s in (self.instrument_slots or []) if s.enabled]
        if not enabled_slots:
            return warnings

        global_cap = self.risk.max_concurrent_positions
        default_per_slot = self.risk.max_positions_per_symbol
        total_possible = sum(
            (s.max_positions_per_symbol if s.max_positions_per_symbol is not None else default_per_slot)
            for s in enabled_slots
        )
        if total_possible > global_cap:
            for s in enabled_slots:
                per_slot = s.max_positions_per_symbol if s.max_positions_per_symbol is not None else default_per_slot
                warnings.append(
                    f"Slot {s.slot_id} ({s.symbol}/{s.strategy_id}): max_positions_per_symbol={per_slot} "
                    f"contributes to a total of {total_possible} possible concurrent positions across all "
                    f"{len(enabled_slots)} enabled slots, exceeding max_concurrent_positions={global_cap}. "
                    f"Not every slot could reach its own cap simultaneously without breaching the global one."
                )
        return warnings

DEFAULT_USER_CONFIG = UserConfigV2()
