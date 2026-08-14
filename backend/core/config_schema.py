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
    # Core sizing
    risk_per_trade_pct: float = 1.0
    max_risk_hard_cap_pct: float = 3.0       # Absolute position sizer safety net
    min_rr: float = 3.0
    sizing_method: Literal["fixed_pct", "kelly"] = "fixed_pct"
    kelly_fraction: float = 0.25
    kelly_lookback_trades: int = 50
    multi_position_mode: bool = True
    sl_buffer_pips: float = 5.0

    # TP structure
    tp_levels: int = 5
    tp_count: int = 3
    tp_splits: list[float] = field(default_factory=lambda: [40.0, 35.0, 25.0])
    tp1_rr: float = 1.0
    tp2_rr: float = 3.0
    tp3_rr: float = 5.0
    tp4_rr: float = 10.0
    tp5_rr: float = 15.0

    # Break-even
    be_trigger_rr: float = 1.0
    be_buffer_pips: float = 2.0
    be_offset_pips: float = 2.0  # legacy alias kept for db compat
    be_buffer_atr_mult: float = 0.0

    # Circuit breakers — trade-count-based (always active)
    max_daily_drawdown_pct: float = 3.0
    max_weekly_drawdown_pct: float = 6.0
    max_daily_trades: int = 5
    max_concurrent_positions: int = 3
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
    trail_activation_rr: float = 1.0
    trail_step_pips: float = 5.0
    trail_structure_bars: int = 3
    trailing_stop_activation_rr: float = 2.0  # legacy alias
    trailing_step_pips: float = 5.0           # legacy alias

    # Legacy field — kept for db compat
    max_daily_loss_pct: float = 5.0


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY TWO (CRASHBOOM) PARAMETERS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DriftJumpAlphaParams:
    """
    Tunable parameters for the Drift & Jump Alpha engine.
    """
    drift_ema_fast: int = 20
    drift_ema_slow: int = 50
    min_adx_to_trade: int = 20
    jump_entry_percentile_threshold: float = 95.0
    trade_jumps_enabled: bool = False
    control_test_passed: bool = False
    aggregate_max_lots_per_symbol: float = 6.0


@dataclass
class CRTParams:
    """
    Tunable parameters for the Candle Range Theory engine.
    Spec: CRT_Strategy_Spec.md
    The SL formula (sl_dist = tp_dist / target_r_multiple) is per-spec (Section 6).
    min_sl_pips and sl_atr_mult are additional guards added above the spec to prevent
    microscopic SL values on small HTF candles causing huge lot sizes in live execution.
    """
    htf_timeframe: str = "H1"
    ltf_timeframe: str = "M5"
    target_r_multiple: float = 1.5
    max_trades_per_session: int = 1
    session_start: str = "09:30"
    session_cutoff: str = "12:00"
    bypass_session_synthetics: bool = True
    # Minimum SL floor — prevents spec-correct but tiny SLs causing extreme lot sizes
    min_sl_pips: float = 15.0     # Hard minimum SL distance in pips
    sl_atr_mult: float = 1.0      # SL must be at least N × ATR (0 = disabled)


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
    Prop Firm (e.g. BloomFunded) challenge parameters.
    Drawdown fields here are HARD CIRCUIT BREAKERS when account_mode = 'prop_firm'.
    max_risk_hard_cap_pct is an absolute safety cap on the position sizer —
    active for ALL modes (personal and prop_firm).
    All fields are user-configurable and resettable from the Settings UI.
    """
    account_mode: Literal["personal", "prop_firm"] = "personal"
    challenge_type: Literal["none", "1-step", "2-step", "flex"] = "none"
    account_size: float = 10000.0
    initial_balance: float = 10000.0
    max_lot_sizes: dict[str, float] = field(default_factory=dict)
    # Drawdown hard blocks (active when account_mode = 'prop_firm')
    max_daily_loss_pct: float = 5.0          # Daily equity drawdown limit (%)
    max_total_drawdown_pct: float = 10.0     # Overall drawdown from peak/initial (%)
    drawdown_uses_equity: bool = True        # True = floating equity; False = closed balance only


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
