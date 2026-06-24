"""
backend/strategies/smc/params.py

SMC Strategy + Risk Management Parameters — Complete Settings
=============================================================
Single source of truth for ALL configurable parameters.
Every parameter here applies identically to live trading AND backtesting.
What you backtest is exactly what runs live.

Sources:
  - SMC_Strategy.md Section 15.2
  - RiskManagement_Spec.md Sections 1–6
"""

from dataclasses import dataclass, field
from typing import List, Literal, Tuple, Optional


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

    # ── Position Sizing ───────────────────────────────────────────────
    risk_per_trade_pct: float = 1.0
    """Percentage of account balance to risk per trade. Range: 0.25–3.0"""

    sizing_method: Literal["fixed_pct", "kelly"] = "fixed_pct"
    """
    fixed_pct: risk_per_trade_pct of balance always.
    kelly: Kelly Criterion based on rolling win rate / avg R (advanced).
    """

    kelly_fraction: float = 0.25
    """Fraction of full Kelly to use (0.25 = quarter-Kelly). Reduces variance."""

    kelly_lookback_trades: int = 50
    """Number of recent trades used to calculate Kelly fraction."""

    # ── Multi-Position Mode ───────────────────────────────────────────
    multi_position_mode: bool = True
    """
    True  = split into TP1/TP2/TP3 sub-positions (recommended).
    False = single position, single TP (simpler).
    """

    tp_levels: int = 5
    """Maximum number of TP levels supported (infrastructure). Range: 1–5."""

    tp_count: int = 3
    """How many TP levels to actively use. User-configurable from frontend.
    If tp_count <= 2: all TPs open at entry.
    If tp_count > 2: TP1+TP2 at entry, TP3–5 deferred (conviction-based).
    Range: 1–5."""

    tp_splits: List[float] = field(default_factory=lambda: [30.0, 25.0, 20.0, 15.0, 10.0])
    """
    Percentage of total lot allocated to each TP level.
    Must sum to 100. Length should match tp_count.
    Default for 5 TPs: [30, 25, 20, 15, 10].
    """

    # ── Take Profit Levels (RR Multipliers) ──────────────────────────
    tp1_rr: float = 1.0
    """TP1 Risk:Reward multiplier. Default: 1.0 (1:1 RR per spec v2.0)."""

    tp2_rr: float = 5.0
    """TP2 Risk:Reward multiplier. Standard: 5.0 (1:5 RR)."""

    tp3_rr: float = 7.0
    """TP3 Risk:Reward multiplier. Extended: 7.0 or 10.0 (1:7 / 1:10 RR)."""

    tp4_rr: float = 10.0
    """TP4 Risk:Reward multiplier. High-conviction runner. (1:10 RR)."""

    tp5_rr: float = 15.0
    """TP5 Risk:Reward multiplier. Maximum swing target. (1:15 RR)."""

    tp3_use_liquidity_target: bool = True
    """
    If True, TP3 is set at the next external liquidity pool (SMC target)
    rather than a fixed RR multiplier. Preferred for SMC strategies.
    """

    min_rr: float = 3.0
    """
    Minimum RR ratio to accept a trade signal at all.
    Signals where TP1 RR < min_rr are rejected outright.
    Range: 3.0–10.0.
    """

    max_rr: float = 10.0
    """Maximum RR cap for TP3. Targets beyond this are capped."""

    # ── Stop Loss Configuration ───────────────────────────────────────
    sl_method: Literal[
        "OB_EXTREME", "SWING_POINT", "FVG_EDGE", "ATR_BASED"
    ] = "OB_EXTREME"
    """
    OB_EXTREME  : SL beyond the Order Block's extreme (high/low).
    SWING_POINT : SL beyond the manipulation candle's wick.
    FVG_EDGE    : SL beyond the Fair Value Gap boundary.
    ATR_BASED   : SL = entry ± (ATR × atr_sl_multiplier).
    """

    sl_buffer_pips: float = 5.0
    """Extra pips added beyond the SL method level. Prevents stop hunting."""

    atr_sl_multiplier: float = 1.5
    """ATR multiplier when sl_method == 'ATR_BASED'. SL = entry ± (ATR × this)."""

    # ── Break-Even (BE) ───────────────────────────────────────────────
    be_enabled: bool = True
    """Enable break-even SL movement."""

    be_trigger_rr: float = 1.0
    """
    Move SL to break-even when trade reaches this many R in profit.
    Default: 1.0 = move to BE at 1:1 RR.
    """

    be_buffer_pips: float = 2.0
    """
    Move SL to (entry + this many pips) rather than exactly entry.
    Covers spread cost on BE close.
    """

    be_on_tp1_hit: bool = True
    """Automatically move remaining positions to BE when TP1 is hit."""

    # ── Trailing Stop Loss ────────────────────────────────────────────
    trail_method_tp2: Literal[
        "NONE", "FIXED_PIPS", "ATR_TRAIL", "STRUCTURE_TRAIL", "PCT_TRAIL"
    ] = "ATR_TRAIL"
    """Trailing method applied to TP2 sub-position after TP1 is hit."""

    trail_method_tp3: Literal[
        "NONE", "FIXED_PIPS", "ATR_TRAIL", "STRUCTURE_TRAIL", "PCT_TRAIL"
    ] = "STRUCTURE_TRAIL"
    """Trailing method applied to TP3 sub-position after TP2 is hit."""

    trail_pips: float = 15.0
    """Fixed pip distance for FIXED_PIPS trailing method."""

    atr_trail_multiplier: float = 1.5
    """ATR multiplier for ATR_TRAIL method. trail_distance = ATR × this."""

    atr_trail_period: int = 14
    """ATR period used in ATR_TRAIL calculations."""

    trail_pct: float = 0.005
    """Percentage for PCT_TRAIL method. 0.005 = 0.5% of current price."""

    trail_activation_rr: float = 1.0
    """
    Trailing only activates after price reaches this many R in profit.
    Prevents trailing from activating too early (before TP1 level).
    """

    structure_trail_timeframe: str = "M15"
    """Timeframe for STRUCTURE_TRAIL swing detection."""

    structure_trail_swing_length: int = 3
    """Swing lookback for STRUCTURE_TRAIL method."""

    # ── Portfolio Circuit Breakers ────────────────────────────────────
    max_daily_consecutive_losses: int = 3
    """
    Strategy pauses when daily consecutive losses hit this count.
    Resets at 00:00 GMT. Range: 1–10.
    """

    max_weekly_consecutive_losses: int = 5
    """
    Strategy pauses for the week when weekly consecutive losses hit this count.
    Resets Monday 00:01 GMT. Range: 2–15.
    """

    max_consecutive_losses: int = 5
    """
    Strategy pauses (requires manual re-enable) after this many
    consecutive losing trades. Range: 3–10.
    """

    max_concurrent_positions: int = 3
    """Maximum simultaneously open positions per user. Range: 1–10."""

    max_trades_per_session: int = 2
    """Max new trades allowed per London or NY session."""

    max_correlated_risk_pct: float = 4.0
    """Max combined risk % on highly correlated pairs (correlation > 0.80)."""

    # ── Spread Filter ─────────────────────────────────────────────────
    max_spread_multiplier: float = 2.0
    """
    Skip entry if current spread > (avg_spread_20bar × this).
    Prevents entering during news spikes or low-liquidity periods.
    """

    max_spread_pips: float = 3.0
    """Absolute maximum spread in pips (hard cap regardless of multiplier)."""

    # ── Stale Trade Management ────────────────────────────────────────
    stale_trade_sessions: int = 3
    """
    Close trades that haven't hit TP1 after this many full sessions.
    3 sessions ≈ 72 hours.
    """

    friday_close_hour_gmt: int = 20
    """Close all open positions before this GMT hour on Fridays. Gap risk."""

    sunday_open_wait_minutes: int = 60
    """Do not place trades for this many minutes after Sunday market open."""


# ─────────────────────────────────────────────────────────────────────────────
# SMC STRATEGY PARAMETERS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SMCParams:
    """
    All tunable parameters for the SMC strategy detection engine.
    These control WHAT signals are generated (not how risk is managed).
    """

    # ── Market Structure Detection ────────────────────────────────────
    swing_length_htf: int = 5
    """H4 swing high/low detection lookback (candles on each side)."""

    swing_length_mtf: int = 3
    """H1 swing detection lookback."""

    swing_length_ltf: int = 3
    """M15/M5 swing detection lookback."""

    # ── Order Block Settings ──────────────────────────────────────────
    ob_impulse_min_ratio: float = 2.0
    """OB impulse move must be this many times the OB candle's size."""

    ob_max_touch_count: int = 2
    """Max OB touches before invalidation. 1=fresh only, 2=first+second."""

    ob_buffer_pips: float = 5.0
    """Pip buffer beyond OB extreme — used in SL placement."""

    ob_mitigation_type: Literal["body_close", "wick_pierce"] = "body_close"
    """How price mitigates an OB: full body close inside, or just a wick."""

    ob_lookback_bars: int = 20
    """How many bars back to search for the qualifying OB candle."""

    # ── FVG Settings ──────────────────────────────────────────────────
    fvg_min_gap_pips: float = 3.0
    """Minimum FVG size in pips to be considered valid."""

    fvg_entry_level: float = 0.50
    """Where within FVG to enter: 0.5 = 50% (CE level)."""

    fvg_max_age_bars: int = 50
    """Invalidate FVGs older than this many bars."""

    # ── Liquidity Detection ───────────────────────────────────────────
    liq_sweep_min_pips: float = 5.0
    """Minimum wick penetration beyond a liquidity level to qualify as sweep."""

    equal_highs_tolerance_pips: float = 10.0
    """Price tolerance for identifying equal highs/lows (liquidity pools)."""

    liq_lookback_bars: int = 100
    """How many bars back to scan for liquidity pools."""

    # ── Premium/Discount / OTE ────────────────────────────────────────
    ote_fib_min: float = 0.618
    """Lower Fibonacci level for Optimal Trade Entry zone."""

    ote_fib_max: float = 0.786
    """Upper Fibonacci level for Optimal Trade Entry zone."""

    discount_threshold: float = 0.50
    """Price below this Fibonacci level = discount zone (buy zone)."""

    # ── IPDM Phase Detection ──────────────────────────────────────────
    ipdm_accum_atr_ratio: float = 0.70
    """ATR below (avg_ATR × this) = Accumulation phase."""

    ipdm_expansion_atr_ratio: float = 1.20
    """ATR above (avg_ATR × this) = Expansion phase."""

    ipdm_atr_lookback: int = 20
    """Lookback period for average ATR calculation."""

    # ── Candlestick Detection ─────────────────────────────────────────
    candle_wick_min_ratio: float = 2.0
    """Minimum wick-to-body ratio for Hammer/Shooting Star detection."""

    candle_engulf_min_ratio: float = 1.0
    """Engulfing body must be >= prev body × this ratio."""

    candle_star_body_max_ratio: float = 0.50
    """Morning/Evening Star middle candle: body <= avg_body × this."""

    candle_displacement_min_ratio: float = 1.50
    """Displacement candle body must be >= avg_body × this."""

    candle_doji_max_body_pct: float = 0.10
    """Doji body must be <= this % of total range."""

    # ── Confluence Scoring Gate ───────────────────────────────────────
    min_signal_score: int = 65
    """Minimum confluence score (0–100) to execute a trade."""

    full_size_score: int = 85
    """Score threshold for full position size (below = 0.75× size)."""

    # ── Session & Time Filters ────────────────────────────────────────
    session_filter_enabled: bool = True
    """Enable/disable session time filter."""

    london_open_gmt: int = 7
    london_close_gmt: int = 15
    ny_open_gmt: int = 12
    ny_close_gmt: int = 20
    kill_zone_london_start: int = 7
    kill_zone_london_end: int = 9
    kill_zone_ny_start: int = 12
    kill_zone_ny_end: int = 14

    # ── News Filter ───────────────────────────────────────────────────
    news_filter_enabled: bool = True
    """Enable/disable high-impact news filter."""

    news_buffer_minutes: int = 30
    """Minutes before/after HIGH-impact news to block all entries."""

    # ── Symbols & Timeframes ──────────────────────────────────────────
    watched_symbols: List[str] = field(default_factory=lambda: [
        "EURUSD", "GBPUSD", "XAUUSD", "US30", "BTCUSD",
    ])

    timeframes: List[str] = field(default_factory=lambda: [
        "M5", "M15", "H1", "H4",
    ])

    # ── Snapshot Settings ─────────────────────────────────────────────
    snapshot_candle_count: int = 80
    """Number of candles to render in entry/exit chart snapshots."""

    snapshot_dpi: int = 120
    """DPI for saved PNG snapshot files."""


# ─────────────────────────────────────────────────────────────────────────────
# COMBINED USER CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class UserConfig:
    """
    Full user configuration: SMC strategy params + risk management params.
    Serialized to/from JSON in the database.
    One per user. Editable from the frontend Settings panel.
    """
    user_id:    str = ""
    smc:        SMCParams  = field(default_factory=SMCParams)
    risk:       RiskParams = field(default_factory=RiskParams)

    # ── MT5 Connection ────────────────────────────────────────────────
    mt5_account: int  = 0
    mt5_server:  str  = ""
    magic_base:  int  = 1001
    """
    Magic number base. Sub-positions use:
      TP1 = magic_base + 10
      TP2 = magic_base + 20
      TP3 = magic_base + 30
    """

    # ── LLM Configuration ─────────────────────────────────────────────
    llm_provider: Literal["claude", "openai", "gemini", "none"] = "none"
    llm_model:    str  = ""
    llm_auto_analyze_live:     bool = False
    llm_auto_analyze_backtest: bool = False

    # ── Notification Preferences ──────────────────────────────────────
    notify_trade_open:     bool = True
    notify_trade_close:    bool = True
    notify_sl_hit:         bool = True
    notify_be_applied:     bool = True
    notify_daily_limit:    bool = True
    notify_signal:         bool = False
    notify_daily_summary:  bool = True
    notify_llm_ready:      bool = True

    # ── Backtest Preferences ──────────────────────────────────────────
    backtest_auto_save:    bool = False
    """
    If False (default), user is prompted whether to save after each backtest.
    If True, saves automatically (user can still delete from history).
    """

    def to_dict(self) -> dict:
        import dataclasses
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "UserConfig":
        smc_data  = data.pop("smc",  {})
        risk_data = data.pop("risk", {})
        config = cls(**data)
        config.smc  = SMCParams(**smc_data)
        config.risk = RiskParams(**risk_data)
        return config


# ─────────────────────────────────────────────────────────────────────────────
# BACKTESTING OPTIMIZATION GRID
# ─────────────────────────────────────────────────────────────────────────────

SMC_OPTIMIZATION_GRID = {
    # Strategy detection parameters
    "smc.swing_length_htf":         [3, 5, 7],
    "smc.ob_impulse_min_ratio":     [1.5, 2.0, 2.5],
    "smc.ob_max_touch_count":       [1, 2],
    "smc.fvg_min_gap_pips":         [2.0, 3.0, 5.0],
    "smc.liq_sweep_min_pips":       [3.0, 5.0, 8.0],
    "smc.candle_wick_min_ratio":    [1.5, 2.0, 2.5],
    "smc.min_signal_score":         [60, 65, 70, 75],
    "smc.max_spread_multiplier":    [1.5, 2.0, 2.5],
}

RISK_OPTIMIZATION_GRID = {
    # Risk management parameters
    "risk.tp1_rr":                  [2.5, 3.0, 3.5],
    "risk.tp2_rr":                  [4.0, 5.0, 6.0],
    "risk.tp3_rr":                  [6.0, 7.0, 10.0],
    "risk.tp_splits":               [[50,30,20], [40,35,25], [33,33,34]],
    "risk.be_trigger_rr":           [0.5, 1.0, 1.5],
    "risk.trail_method_tp2":        ["ATR_TRAIL", "STRUCTURE_TRAIL"],
    "risk.trail_method_tp3":        ["STRUCTURE_TRAIL", "FIXED_PIPS"],
    "risk.atr_trail_multiplier":    [1.0, 1.5, 2.0],
    "risk.risk_per_trade_pct":      [0.5, 1.0, 1.5],
    "risk.sl_method":               ["OB_EXTREME", "SWING_POINT"],
    "risk.sl_buffer_pips":          [3.0, 5.0, 8.0],
}

# Pre-built risk presets (selectable in UI as starting points)
RISK_PRESETS = {
    "conservative": RiskParams(
        risk_per_trade_pct=0.5,
        tp1_rr=1.0, tp2_rr=3.0, tp3_rr=5.0,
        tp_splits=[50, 35, 15],
        min_rr=3.0,
        be_trigger_rr=0.75,
        trail_method_tp2="ATR_TRAIL",
        trail_method_tp3="STRUCTURE_TRAIL",
        max_daily_consecutive_losses=2,
        max_concurrent_positions=2,
    ),
    "balanced": RiskParams(
        risk_per_trade_pct=1.0,
        tp1_rr=1.0, tp2_rr=3.0, tp3_rr=5.0,
        tp_splits=[40, 35, 25],
        min_rr=3.0,
        be_trigger_rr=1.0,
        trail_method_tp2="ATR_TRAIL",
        trail_method_tp3="STRUCTURE_TRAIL",
        max_daily_consecutive_losses=3,
        max_concurrent_positions=3,
    ),
    "aggressive": RiskParams(
        risk_per_trade_pct=1.5,
        tp1_rr=1.0, tp2_rr=5.0, tp3_rr=10.0,
        tp_splits=[30, 35, 35],
        min_rr=3.0,
        be_trigger_rr=1.5,
        trail_method_tp2="STRUCTURE_TRAIL",
        trail_method_tp3="STRUCTURE_TRAIL",
        atr_trail_multiplier=2.0,
        max_daily_consecutive_losses=5,
        max_concurrent_positions=5,
    ),
    "runner": RiskParams(
        risk_per_trade_pct=1.0,
        tp1_rr=1.0, tp2_rr=5.0, tp3_rr=10.0,
        tp_splits=[25, 35, 40],
        min_rr=3.0,
        tp3_use_liquidity_target=True,
        be_trigger_rr=1.0,
        trail_method_tp2="ATR_TRAIL",
        trail_method_tp3="STRUCTURE_TRAIL",
        max_daily_consecutive_losses=3,
        max_concurrent_positions=3,
    ),
}

# Default instances
DEFAULT_SMC_PARAMS  = SMCParams()
DEFAULT_RISK_PARAMS = RiskParams()
DEFAULT_USER_CONFIG = UserConfig()


# ─────────────────────────────────────────────────────────────────────────────
# COMPOUNDING PARAMETERS
# ─────────────────────────────────────────────────────────────────────────────

from typing import Literal as _Literal
from backend.risk.compounding import CompoundingParams  # noqa: F401 — re-exported for UserConfig


# ─────────────────────────────────────────────────────────────────────────────
# INSTRUMENT PROFILE SETTINGS (per-user overrides)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class InstrumentSettings:
    """
    Per-user instrument-level settings that override the global instrument profile.
    Stored in UserConfig. Allows users to customize behaviour per symbol.
    """
    symbol:          str
    enabled:         bool  = True           # Trade this symbol at all
    max_lot_override: Optional[float] = None # Cap lot size (safety)
    custom_sl_buffer: Optional[float] = None # Override profile's sl_buffer_pips
    compounding_enabled: bool = True        # Allow compounding on this symbol
    notes:           str  = ""              # User label (e.g. "V75 main account")


# ─────────────────────────────────────────────────────────────────────────────
# UPDATED FULL USER CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

@dataclass 
class UserConfigV2(UserConfig):
    """
    Extended UserConfig with compounding and instrument settings.
    Replaces UserConfig for all new installations.
    """
    compounding: CompoundingParams = None
    instrument_settings: List[InstrumentSettings] = None

    def __post_init__(self):
        if self.compounding is None:
            self.compounding = CompoundingParams()
        if self.instrument_settings is None:
            self.instrument_settings = []

    def get_risk_amount(self, account_balance: float, state=None) -> float:
        """
        Returns dollar risk for next trade.
        If compounding enabled: uses stepped plan.
        Otherwise: returns risk_pct% of balance.
        """
        if self.compounding.enabled:
            engine = self.compounding.build_engine()
            return engine.get_risk_amount(account_balance, state)
        else:
            return account_balance * (self.risk.risk_per_trade_pct / 100)

    def is_compounding_active(self) -> bool:
        return self.compounding.enabled


# ─────────────────────────────────────────────────────────────────────────────
# SYNTHETIC-SPECIFIC PARAMETER PRESETS
# ─────────────────────────────────────────────────────────────────────────────

SYNTHETIC_SMC_PARAMS = SMCParams(
    # Shorter swing detection for faster-moving synthetics
    swing_length_htf=3,
    swing_length_mtf=2,
    swing_length_ltf=2,
    # Lower impulse threshold (synthetics create smaller relative impulses)
    ob_impulse_min_ratio=1.5,
    ob_max_touch_count=2,
    # FVG tuning for synthetics
    fvg_min_gap_pips=5.0,
    fvg_max_age_bars=30,
    # Liquidity settings
    liq_sweep_min_pips=10.0,
    equal_highs_tolerance_pips=5.0,
    # NO session or news filter for synthetics
    session_filter_enabled=False,
    news_filter_enabled=False,
    # All 24/7
    watched_symbols=["Volatility 75 Index", "Volatility 25 Index", "Volatility 50 Index"],
    timeframes=["M5", "M15", "H1", "H4"],
    # Scoring
    min_signal_score=60,  # Slightly lower threshold for synthetics cleaner structure
    full_size_score=80,
)

GOLD_SMC_PARAMS = SMCParams(
    swing_length_htf=5,
    swing_length_mtf=3,
    swing_length_ltf=3,
    ob_impulse_min_ratio=2.0,
    liq_sweep_min_pips=50.0,   # Gold sweeps are larger
    fvg_min_gap_pips=30.0,
    equal_highs_tolerance_pips=20.0,
    session_filter_enabled=True,
    news_filter_enabled=True,
    watched_symbols=["XAUUSD"],
    timeframes=["M5", "M15", "H1", "H4"],
    min_signal_score=70,       # Higher threshold for gold volatility
)

FOREX_SMC_PARAMS = SMCParams(
    swing_length_htf=5,
    swing_length_mtf=3,
    swing_length_ltf=3,
    ob_impulse_min_ratio=2.0,
    liq_sweep_min_pips=5.0,
    fvg_min_gap_pips=3.0,
    session_filter_enabled=True,
    news_filter_enabled=True,
    watched_symbols=["EURUSD", "GBPUSD", "USDJPY"],
    timeframes=["M5", "M15", "H1", "H4"],
    min_signal_score=65,
)

# ─────────────────────────────────────────────────────────────────────────────
# COMPOUNDING PRESETS
# ─────────────────────────────────────────────────────────────────────────────

COMPOUNDING_PRESETS = {
    "default_1_3rr": CompoundingParams(
        enabled=True,
        use_default_plan=True,
        advance_mode="AUTO",
        downgrade_mode="THRESHOLD",
    ),
    "conservative_1_3rr": CompoundingParams(
        enabled=True,
        use_default_plan=True,
        advance_mode="CONSERVATIVE",
        conservative_wins_required=3,
        downgrade_mode="LOSS_COUNT",
        max_losses_before_downgrade=3,
    ),
    "manual_control": CompoundingParams(
        enabled=True,
        use_default_plan=True,
        advance_mode="MANUAL",
        downgrade_mode="THRESHOLD",
    ),
    "disabled": CompoundingParams(
        enabled=False,
    ),
}

# Combined optimization grid (SMC + Risk + Compounding)
FULL_OPTIMIZATION_GRID = {
    **SMC_OPTIMIZATION_GRID,
    **RISK_OPTIMIZATION_GRID,
    "compounding.conservative_wins_required": [1, 2, 3],
    "compounding.max_losses_before_downgrade": [2, 3, 5],
    "compounding.advance_mode": ["AUTO", "CONSERVATIVE"],
}
