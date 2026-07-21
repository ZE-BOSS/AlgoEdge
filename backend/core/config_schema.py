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
from backend.risk.compounding import CompoundingParams

# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY TWO (CRASHBOOM) PARAMETERS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CrashBoomParams:
    """
    Tunable parameters for the CrashBoom strategy engine.
    """
    spike_lookback_bars: int = 50
    """Bars to look back to define a spike extreme."""
    
    drift_ema_fast: int = 5
    """Fast EMA for continuous drift detection."""
    
    drift_ema_slow: int = 15
    """Slow EMA for continuous drift detection."""
    
    spike_threshold_pips: float = 20.0
    """Minimum size of a spike (in pips) to trigger an entry signal."""
    
    recovery_target_pips: float = 10.0
    """Expected recovery distance (TP)."""


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
    crashboom: CrashBoomParams = field(default_factory=CrashBoomParams)
    prop_firm: PropFirmParams = field(default_factory=PropFirmParams)

    @classmethod
    def from_dict(cls, data: dict) -> "UserConfigV2":
        smc_data  = data.pop("smc",  {})
        risk_data = data.pop("risk", {})
        compounding_data = data.pop("compounding", None)
        instrument_data = data.pop("instrument_settings", None)
        crashboom_data = data.pop("crashboom", {})
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
        config.crashboom = CrashBoomParams(**filter_kwargs(CrashBoomParams, crashboom_data))
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
        if self.crashboom is None:
            self.crashboom = CrashBoomParams()
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
