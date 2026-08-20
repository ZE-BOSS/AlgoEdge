"""
backend/core/config_schema.py

Global Root Configuration Schema
================================
Holds the combined UserConfig schema used by the database, live trading, and backtester.
Dynamically includes configuration blocks for all registered strategies.
"""

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
    modes (tight stop -> huge lots) share a single observable symptom. Enforce as a hard
    clamp after sizing and before order submission, per
    DriftJumpAlpha_Strategy_Spec_v2 §1: "never a soft warning".
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

    # ── Break-even ───────────────────────────────────────────────────────
    be_trigger_rr: float = 1.5
    """
    CHANGED 1.0 -> 1.5 (2026-08), to coincide exactly with tp1_rr.

    Triggering BE at 1.0R while TP1 sits at 1.5R is strictly harmful: it arms the
    scratch-out one third of the way BEFORE the first partial, so a normal retracement
    inside the move converts a trade that would have paid 1.25R net into a 0R scratch,
    while doing nothing at all for the losers (which never reach 1R). De-risking should
    never precede the event that de-risks you. Aligning the two means BE arms at the
    moment 50% of the position is already booked, which is the standard schedule.
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

    # Target profit halts
    target_profit_enabled: bool = False
    max_daily_profit: float = 500.0
    max_weekly_profit: float = 2000.0

    # Trailing stops
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
    trail_activation_rr: float = 1.5
    """
    CHANGED 1.0 -> 1.5 (2026-08). Kept in lockstep with tp1_rr and be_trigger_rr so that
    exactly one de-risking event happens at the first target rather than three staggered
    ones. Activating a trail at 1R while TP1 sits at 1.5R meant the trail could scratch
    the position out before the partial it was supposed to protect had been taken.
    """
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

    # Minimum SL floors — prevent spec-correct but tiny SLs causing extreme lot sizes.
    # REVIEWED 2026-08 and retained unchanged. CRT is the ONLY strategy in the book that
    # already had a cost floor, and it is the reason CRT does not appear in the forensic
    # review's list of degenerate-stop offenders. These two fields are the pattern that
    # has now been replicated into APAParams, HTFFVGFlipParams, BiasIFVGParams and
    # VWAPParams — and unlike those copies, THESE ARE ACTUALLY READ BY THE ENGINE
    # (strategy_three_crt/engine.py:222-250).
    min_sl_pips: float = 15.0     # Hard minimum SL distance in pips (~5x FX-major friction)
    sl_atr_mult: float = 1.0      # SL must be at least N × ATR (0 = disabled)

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
    Per-user instrument-level settings that override the global instrument profile.
    Stored in UserConfigV2. Allows users to customize behaviour per symbol.
    """
    symbol:          str
    strategy_id:     str   = "APA_v1"       # The registry ID of the strategy to run
    enabled:         bool  = True           # Trade this symbol at all
    max_lot_override: float | None = None # Cap lot size (safety)
    custom_sl_buffer: float | None = None # Override profile's sl_buffer_pips
    notes:           str  = ""              # User label (e.g. "V75 main account")


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

    # Challenge pass conditions
    profit_target_pct: float = 0.0           # 0 = no target (personal accounts)
    min_trading_days: int = 0                # 0 = no minimum


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
            
        return config

    def __post_init__(self):
        if self.instrument_settings is None:
            self.instrument_settings = []
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

DEFAULT_USER_CONFIG = UserConfigV2()
