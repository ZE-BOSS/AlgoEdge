"""
backend/core/config_schema.py

Global Root Configuration Schema
================================
Holds the combined UserConfig schema used by the database, live trading, and backtester.
Dynamically includes configuration blocks for all registered strategies.
"""

from dataclasses import dataclass, field
from typing import List, Literal, Optional, Dict, Any

from backend.strategies.strategy_one.params import SMCParams, RiskParams
from backend.strategies.strategy_four_htf_fvg_flip.params import HTFFVGFlipParams
from backend.strategies.strategy_five_bias_ifvg.params import BiasIFVGParams
from backend.strategies.strategy_six_ny_open_retest.params import NYOpenRetestParams
from backend.risk.compounding import CompoundingParams

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
    """
    htf_timeframe: str = "1H"
    ltf_timeframe: str = "M1"
    target_r_multiple: float = 1.5
    max_trades_per_session: int = 1
    session_start: str = "09:30"
    session_cutoff: str = "12:00"
    bypass_session_synthetics: bool = True


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
    strategy_id:     str   = "SMC_v1"       # The registry ID of the strategy to run
    enabled:         bool  = True           # Trade this symbol at all
    max_lot_override: Optional[float] = None # Cap lot size (safety)
    custom_sl_buffer: Optional[float] = None # Override profile's sl_buffer_pips
    compounding_enabled: bool = True        # Allow compounding on this symbol
    notes:           str  = ""              # User label (e.g. "V75 main account")


# ─────────────────────────────────────────────────────────────────────────────
# PROP FIRM SETTINGS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PropFirmParams:
    """
    BloomFunded Synthetic Challenge Parameters
    """
    account_mode: Literal["personal", "prop_firm"] = "personal"
    challenge_type: Literal["none", "1-step", "2-step", "flex"] = "none"
    account_size: float = 10000.0
    initial_balance: float = 10000.0
    max_lot_sizes: Dict[str, float] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# FULL USER CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class UserConfig:
    """Legacy base structure."""
    user_id:    str = ""
    smc:        SMCParams  = field(default_factory=SMCParams)
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
        smc_data  = data.pop("smc",  {})
        risk_data = data.pop("risk", {})
        
        import dataclasses
        known_fields = {f.name for f in dataclasses.fields(cls)}
        filtered_data = {k: v for k, v in data.items() if k in known_fields}
        
        config = cls(**filtered_data)
        config.smc  = SMCParams(**smc_data)
        config.risk = RiskParams(**risk_data)
        return config


@dataclass 
class UserConfigV2(UserConfig):
    """
    Extended UserConfig with compounding, instrument settings, and multi-strategy support.
    """
    compounding: CompoundingParams = None
    instrument_settings: List[InstrumentSettings] = None
    drift_jump_alpha: DriftJumpAlphaParams = field(default_factory=DriftJumpAlphaParams)
    crt: CRTParams = field(default_factory=CRTParams)
    htf_fvg_flip: HTFFVGFlipParams = field(default_factory=HTFFVGFlipParams)
    bias_ifvg: BiasIFVGParams = field(default_factory=BiasIFVGParams)
    ny_open_retest: NYOpenRetestParams = field(default_factory=NYOpenRetestParams)
    prop_firm: PropFirmParams = field(default_factory=PropFirmParams)

    @classmethod
    def from_dict(cls, data: dict) -> "UserConfigV2":
        smc_data  = data.pop("smc",  {})
        risk_data = data.pop("risk", {})
        compounding_data = data.pop("compounding", None)
        instrument_data = data.pop("instrument_settings", None)
        drift_jump_alpha_data = data.pop("drift_jump_alpha", {})
        crt_data = data.pop("crt", {})
        htf_fvg_flip_data = data.pop("htf_fvg_flip", {})
        bias_ifvg_data = data.pop("bias_ifvg", {})
        ny_open_retest_data = data.pop("ny_open_retest", {})
        prop_firm_data = data.pop("prop_firm", {})
        import dataclasses
        known_fields = {f.name for f in dataclasses.fields(cls)}
        filtered_data = {k: v for k, v in data.items() if k in known_fields}
        
        config = cls(**filtered_data)
        
        def filter_kwargs(dataclass_type, data_dict):
            if not isinstance(data_dict, dict): return {}
            known = {f.name for f in dataclasses.fields(dataclass_type)}
            return {k: v for k, v in data_dict.items() if k in known}

        config.smc  = SMCParams(**filter_kwargs(SMCParams, smc_data))
        config.risk = RiskParams(**filter_kwargs(RiskParams, risk_data))
        config.drift_jump_alpha = DriftJumpAlphaParams(**filter_kwargs(DriftJumpAlphaParams, drift_jump_alpha_data))
        config.crt = CRTParams(**filter_kwargs(CRTParams, crt_data))
        config.htf_fvg_flip = HTFFVGFlipParams(**filter_kwargs(HTFFVGFlipParams, htf_fvg_flip_data))
        config.bias_ifvg = BiasIFVGParams(**filter_kwargs(BiasIFVGParams, bias_ifvg_data))
        config.ny_open_retest = NYOpenRetestParams(**filter_kwargs(NYOpenRetestParams, ny_open_retest_data))
        config.prop_firm = PropFirmParams(**filter_kwargs(PropFirmParams, prop_firm_data))
        
        if compounding_data:
            config.compounding = CompoundingParams(**filter_kwargs(CompoundingParams, compounding_data))
        else:
            config.compounding = CompoundingParams()
            
        if instrument_data:
            config.instrument_settings = [InstrumentSettings(**filter_kwargs(InstrumentSettings, i)) for i in instrument_data]
        else:
            config.instrument_settings = []
            
        return config

    def __post_init__(self):
        if self.compounding is None:
            self.compounding = CompoundingParams()
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
        if self.prop_firm is None:
            self.prop_firm = PropFirmParams()

    def get_risk_amount(self, account_balance: float, state=None) -> float:
        if self.compounding.enabled:
            engine = self.compounding.build_engine()
            return engine.get_risk_amount(account_balance, state)
        else:
            return account_balance * (self.risk.risk_per_trade_pct / 100)

    def is_compounding_active(self) -> bool:
        return self.compounding.enabled

DEFAULT_USER_CONFIG = UserConfigV2()
