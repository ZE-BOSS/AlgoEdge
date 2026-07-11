"""
backend/risk/compounding.py

AlgoEdge Compounding Plan Engine
==================================
Implements the stepped fixed-dollar risk compounding system.
Toggled per user via UserConfig.compounding.enabled.

Source: CompoundingPlan_Spec.md
"""

from dataclasses import dataclass, field
from typing import List, Optional, Literal
import math


# ─────────────────────────────────────────────────────────────────────────────
# DATA STRUCTURES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CompoundingStep:
    step_number:       int
    risk_amount:       float    # Fixed dollar risk for this step
    reward_at_3r:      float    # Expected reward at 3:1 RR
    entry_threshold:   float    # Minimum account balance to be at this step
    account_after_win: float    # Account balance after winning this step's trade

    @property
    def rr_ratio(self) -> float:
        return self.reward_at_3r / self.risk_amount if self.risk_amount > 0 else 0

    def __repr__(self):
        return (f"Step {self.step_number}: Risk=${self.risk_amount:.0f} "
                f"| Reward=${self.reward_at_3r:.0f} "
                f"| Entry≥${self.entry_threshold:.0f}")


@dataclass
class CompoundingState:
    """Live state of the compounding engine for one user. Stored in Redis."""
    current_step:              int   = 1
    risk_amount:               float = 20.0
    entry_balance:             float = 0.0    # Balance when this step was entered
    consecutive_wins:          int   = 0      # Wins at current risk level
    consecutive_losses:        int   = 0      # Losses at current risk level
    total_wins_at_level:       int   = 0
    total_losses_at_level:     int   = 0
    last_step_change_reason:   str   = "INIT"
    last_step_change_balance:  float = 0.0


# ─────────────────────────────────────────────────────────────────────────────
# DEFAULT 1:3 RR COMPOUNDING PLAN
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_1_3RR_STEPS: List[CompoundingStep] = [
    # Note: Step 4 in source image shows $230 (typo) — corrected to $320
    CompoundingStep(step_number=1,  risk_amount=20,  reward_at_3r=60,   entry_threshold=0,     account_after_win=80),
    CompoundingStep(step_number=2,  risk_amount=20,  reward_at_3r=60,   entry_threshold=80,    account_after_win=140),
    CompoundingStep(step_number=3,  risk_amount=30,  reward_at_3r=90,   entry_threshold=140,   account_after_win=230),
    CompoundingStep(step_number=4,  risk_amount=30,  reward_at_3r=90,   entry_threshold=230,   account_after_win=320),
    CompoundingStep(step_number=5,  risk_amount=50,  reward_at_3r=150,  entry_threshold=320,   account_after_win=470),
    CompoundingStep(step_number=6,  risk_amount=50,  reward_at_3r=150,  entry_threshold=470,   account_after_win=620),
    CompoundingStep(step_number=7,  risk_amount=100, reward_at_3r=300,  entry_threshold=620,   account_after_win=920),
    CompoundingStep(step_number=8,  risk_amount=100, reward_at_3r=300,  entry_threshold=920,   account_after_win=1220),
    CompoundingStep(step_number=9,  risk_amount=200, reward_at_3r=600,  entry_threshold=1220,  account_after_win=1820),
    CompoundingStep(step_number=10, risk_amount=200, reward_at_3r=600,  entry_threshold=1820,  account_after_win=2420),
    CompoundingStep(step_number=11, risk_amount=250, reward_at_3r=750,  entry_threshold=2420,  account_after_win=3170),
    CompoundingStep(step_number=12, risk_amount=250, reward_at_3r=750,  entry_threshold=3170,  account_after_win=3920),
    CompoundingStep(step_number=13, risk_amount=300, reward_at_3r=900,  entry_threshold=3920,  account_after_win=4820),
    CompoundingStep(step_number=14, risk_amount=300, reward_at_3r=900,  entry_threshold=4820,  account_after_win=5720),
    CompoundingStep(step_number=15, risk_amount=400, reward_at_3r=1200, entry_threshold=5720,  account_after_win=6920),
    CompoundingStep(step_number=16, risk_amount=400, reward_at_3r=1200, entry_threshold=6920,  account_after_win=8120),
    CompoundingStep(step_number=17, risk_amount=500, reward_at_3r=1500, entry_threshold=8120,  account_after_win=9620),
    CompoundingStep(step_number=18, risk_amount=500, reward_at_3r=1500, entry_threshold=9620,  account_after_win=11120),
]


# ─────────────────────────────────────────────────────────────────────────────
# INSTRUMENT PROFILES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class InstrumentProfile:
    """
    Per-instrument configuration. Different for synthetics, gold, forex.
    Used for correct lot sizing and strategy parameter overrides.
    """
    symbol:              str
    instrument_type:     Literal["SYNTHETIC", "COMMODITY", "FOREX", "INDEX", "CRYPTO"]
    
    # Pip / point sizing
    point_size:          float   # Smallest price increment (pip size)
    point_value_per_lot: float   # USD value of 1 point move per 1.0 standard lot
    
    # Lot constraints (from broker symbol info — fetch from MT5)
    lot_min:             float
    lot_max:             float
    lot_step:            float
    contract_size:       float   # Standard lot size
    
    # Session & filter overrides
    session_filter:      bool    # Override global session filter
    news_filter:         bool    # Override global news filter
    trades_24_7:         bool    # True for synthetics
    
    # SMC parameter overrides (None = use global defaults)
    swing_length_htf_override:     Optional[int]   = None
    swing_length_ltf_override:     Optional[int]   = None
    ob_impulse_ratio_override:     Optional[float] = None
    liq_sweep_min_atr_mult_override:   Optional[float] = None
    fvg_min_gap_atr_mult_override:     Optional[float] = None
    sl_buffer_pips_override:       Optional[float] = None
    atr_trail_multiplier_override: Optional[float] = None
    max_spread_atr_mult_override:      Optional[float] = None

    def get_pip_value_per_mini_lot(self) -> float:
        """Value of 1 pip/point move on 0.01 lot."""
        return self.point_value_per_lot * 0.01


# ── Instrument Library ────────────────────────────────────────────────────────

INSTRUMENT_PROFILES: dict[str, InstrumentProfile] = {

    # ── Deriv Synthetic Indices ───────────────────────────────────────────────

    "Volatility 75 Index": InstrumentProfile(
        symbol="Volatility 75 Index",
        instrument_type="SYNTHETIC",
        point_size=0.01,
        point_value_per_lot=0.01,    # $1 per point per standard lot (approx)
        lot_min=0.01,
        lot_max=10.0,
        lot_step=0.001,
        contract_size=1,
        session_filter=False,       # 24/7 — no session filter
        news_filter=False,          # Not affected by news
        trades_24_7=True,
        swing_length_htf_override=3,
        swing_length_ltf_override=2,
        ob_impulse_ratio_override=1.5,
        liq_sweep_min_atr_mult_override=1.0,
        fvg_min_gap_atr_mult_override=0.5,
        sl_buffer_pips_override=3.0,
    ),

    "Volatility 25 Index": InstrumentProfile(
        symbol="Volatility 25 Index",
        instrument_type="SYNTHETIC",
        point_size=0.01,
        point_value_per_lot=0.01,    # Lower volatility = lower point value
        lot_min=0.5,
        lot_max=50.0,
        lot_step=0.001,
        contract_size=1,
        session_filter=False,
        news_filter=False,
        trades_24_7=True,
        swing_length_htf_override=3,
        swing_length_ltf_override=2,
        ob_impulse_ratio_override=1.5,
        fvg_min_gap_atr_mult_override=0.3,
    ),

    "Volatility 50 Index": InstrumentProfile(
        symbol="Volatility 50 Index",
        instrument_type="SYNTHETIC",
        point_size=0.01,
        point_value_per_lot=0.01,
        lot_min=4.0,
        lot_max=20.0,
        lot_step=0.001,
        contract_size=1,
        session_filter=False,
        news_filter=False,
        trades_24_7=True,
        swing_length_htf_override=3,
        swing_length_ltf_override=2,
    ),

    "Volatility 100 Index": InstrumentProfile(
        symbol="Volatility 100 Index",
        instrument_type="SYNTHETIC",
        point_size=0.01,
        point_value_per_lot=0.01,    # Higher volatility = higher point value
        lot_min=0.5,
        lot_max=5.0,
        lot_step=0.001,
        contract_size=1,
        session_filter=False,
        news_filter=False,
        trades_24_7=True,
        swing_length_htf_override=3,
        swing_length_ltf_override=2,
        ob_impulse_ratio_override=2.0,
        liq_sweep_min_atr_mult_override=1.5,
        sl_buffer_pips_override=5.0,
        max_spread_atr_mult_override=0.5,
    ),

    "Boom 1000 Index": InstrumentProfile(
        symbol="Boom 1000 Index",
        instrument_type="SYNTHETIC",
        point_size=0.01,
        point_value_per_lot=0.01,
        lot_min=0.2,
        lot_max=10.0,
        lot_step=0.001,
        contract_size=1,
        session_filter=False,
        news_filter=False,
        trades_24_7=True,
        swing_length_htf_override=5,  # Longer for spike handling
        swing_length_ltf_override=3,
        ob_impulse_ratio_override=2.5,  # Stronger impulse required (spike-driven)
        liq_sweep_min_atr_mult_override=2.0,
    ),

    "Crash 1000 Index": InstrumentProfile(
        symbol="Crash 1000 Index",
        instrument_type="SYNTHETIC",
        point_size=0.01,
        point_value_per_lot=0.01,
        lot_min=0.2,
        lot_max=10.0,
        lot_step=0.001,
        contract_size=1,
        session_filter=False,
        news_filter=False,
        trades_24_7=True,
        swing_length_htf_override=5,
        swing_length_ltf_override=3,
        ob_impulse_ratio_override=2.5,
    ),

    "Volatility 10 Index": InstrumentProfile(
        symbol="Volatility 10 Index",
        instrument_type="SYNTHETIC",
        point_size=0.001,
        point_value_per_lot=0.001,   # Very low volatility
        lot_min=0.5,
        lot_max=100.0,
        lot_step=0.001,
        contract_size=1,
        session_filter=False,
        news_filter=False,
        trades_24_7=True,
        swing_length_htf_override=3,
        swing_length_ltf_override=2,
        fvg_min_gap_atr_mult_override=0.1,
    ),

    # ── Volatility Standard (New) ────────────────────────────────────────────

    "Volatility 150 Index": InstrumentProfile(
        symbol="Volatility 150 Index", instrument_type="SYNTHETIC",
        point_size=0.01, point_value_per_lot=0.01, lot_min=0.001, lot_max=3.0,
        lot_step=0.001, contract_size=1, session_filter=False, news_filter=False,
        trades_24_7=True, swing_length_htf_override=3, swing_length_ltf_override=2,
        ob_impulse_ratio_override=2.0, sl_buffer_pips_override=5.0,
    ),
    "Volatility 250 Index": InstrumentProfile(
        symbol="Volatility 250 Index", instrument_type="SYNTHETIC",
        point_size=0.01, point_value_per_lot=0.01, lot_min=0.001, lot_max=2.0,
        lot_step=0.001, contract_size=1, session_filter=False, news_filter=False,
        trades_24_7=True, swing_length_htf_override=3, swing_length_ltf_override=2,
        ob_impulse_ratio_override=2.5, sl_buffer_pips_override=8.0,
    ),

    # ── Volatility 1s Variants ───────────────────────────────────────────────

    "Volatility 10 Index (1s)": InstrumentProfile(
        symbol="Volatility 10 Index (1s)", instrument_type="SYNTHETIC",
        point_size=0.001, point_value_per_lot=0.25, lot_min=0.5, lot_max=100.0,
        lot_step=0.001, contract_size=1, session_filter=False, news_filter=False, trades_24_7=True,
    ),
    "Volatility 25 Index (1s)": InstrumentProfile(
        symbol="Volatility 25 Index (1s)", instrument_type="SYNTHETIC",
        point_size=0.01, point_value_per_lot=0.5, lot_min=0.005, lot_max=50.0,
        lot_step=0.001, contract_size=1, session_filter=False, news_filter=False, trades_24_7=True,
    ),
    "Volatility 50 Index (1s)": InstrumentProfile(
        symbol="Volatility 50 Index (1s)", instrument_type="SYNTHETIC",
        point_size=0.01, point_value_per_lot=0.75, lot_min=0.005, lot_max=20.0,
        lot_step=0.001, contract_size=1, session_filter=False, news_filter=False, trades_24_7=True,
    ),
    "Volatility 75 Index (1s)": InstrumentProfile(
        symbol="Volatility 75 Index (1s)", instrument_type="SYNTHETIC",
        point_size=0.01, point_value_per_lot=1.0, lot_min=0.05, lot_max=10.0,
        lot_step=0.001, contract_size=1, session_filter=False, news_filter=False, trades_24_7=True,
    ),
    "Volatility 100 Index (1s)": InstrumentProfile(
        symbol="Volatility 100 Index (1s)", instrument_type="SYNTHETIC",
        point_size=0.01, point_value_per_lot=1.5, lot_min=0.1, lot_max=5.0,
        lot_step=0.001, contract_size=1, session_filter=False, news_filter=False, trades_24_7=True,
    ),
    "Volatility 150 Index (1s)": InstrumentProfile(
        symbol="Volatility 150 Index (1s)", instrument_type="SYNTHETIC",
        point_size=0.01, point_value_per_lot=2.0, lot_min=0.001, lot_max=3.0,
        lot_step=0.001, contract_size=1, session_filter=False, news_filter=False, trades_24_7=True,
    ),
    "Volatility 250 Index (1s)": InstrumentProfile(
        symbol="Volatility 250 Index (1s)", instrument_type="SYNTHETIC",
        point_size=0.01, point_value_per_lot=3.0, lot_min=0.001, lot_max=2.0,
        lot_step=0.001, contract_size=1, session_filter=False, news_filter=False, trades_24_7=True,
    ),

    # ── Boom/Crash (New) ─────────────────────────────────────────────────────

    "Boom 300 Index": InstrumentProfile(
        symbol="Boom 300 Index", instrument_type="SYNTHETIC",
        point_size=0.01, point_value_per_lot=0.01, lot_min=1.0, lot_max=10.0,
        lot_step=0.001, contract_size=1, session_filter=False, news_filter=False,
        trades_24_7=True, swing_length_htf_override=5, ob_impulse_ratio_override=2.5,
    ),
    "Boom 500 Index": InstrumentProfile(
        symbol="Boom 500 Index", instrument_type="SYNTHETIC",
        point_size=0.01, point_value_per_lot=0.01, lot_min=0.2, lot_max=10.0,
        lot_step=0.001, contract_size=1, session_filter=False, news_filter=False,
        trades_24_7=True, swing_length_htf_override=5, ob_impulse_ratio_override=2.5,
    ),
    "Boom 600 Index": InstrumentProfile(
        symbol="Boom 600 Index", instrument_type="SYNTHETIC",
        point_size=0.01, point_value_per_lot=0.01, lot_min=0.001, lot_max=10.0,
        lot_step=0.001, contract_size=1, session_filter=False, news_filter=False,
        trades_24_7=True, swing_length_htf_override=5, ob_impulse_ratio_override=2.5,
    ),
    "Boom 900 Index": InstrumentProfile(
        symbol="Boom 900 Index", instrument_type="SYNTHETIC",
        point_size=0.01, point_value_per_lot=0.01, lot_min=0.001, lot_max=10.0,
        lot_step=0.001, contract_size=1, session_filter=False, news_filter=False,
        trades_24_7=True, swing_length_htf_override=5, ob_impulse_ratio_override=2.5,
    ),
    "Crash 300 Index": InstrumentProfile(
        symbol="Crash 300 Index", instrument_type="SYNTHETIC",
        point_size=0.01, point_value_per_lot=0.01, lot_min=0.5, lot_max=10.0,
        lot_step=0.001, contract_size=1, session_filter=False, news_filter=False,
        trades_24_7=True, swing_length_htf_override=5, ob_impulse_ratio_override=2.5,
    ),
    "Crash 500 Index": InstrumentProfile(
        symbol="Crash 500 Index", instrument_type="SYNTHETIC",
        point_size=0.01, point_value_per_lot=0.01, lot_min=0.2, lot_max=10.0,
        lot_step=0.001, contract_size=1, session_filter=False, news_filter=False,
        trades_24_7=True, swing_length_htf_override=5, ob_impulse_ratio_override=2.5,
    ),
    "Crash 600 Index": InstrumentProfile(
        symbol="Crash 600 Index", instrument_type="SYNTHETIC",
        point_size=0.01, point_value_per_lot=0.01, lot_min=0.001, lot_max=10.0,
        lot_step=0.001, contract_size=1, session_filter=False, news_filter=False,
        trades_24_7=True, swing_length_htf_override=5, ob_impulse_ratio_override=2.5,
    ),
    "Crash 900 Index": InstrumentProfile(
        symbol="Crash 900 Index", instrument_type="SYNTHETIC",
        point_size=0.01, point_value_per_lot=0.01, lot_min=0.001, lot_max=10.0,
        lot_step=0.001, contract_size=1, session_filter=False, news_filter=False,
        trades_24_7=True, swing_length_htf_override=5, ob_impulse_ratio_override=2.5,
    ),

    # ── Jump Indices ─────────────────────────────────────────────────────────

    "Jump 10 Index": InstrumentProfile(
        symbol="Jump 10 Index", instrument_type="SYNTHETIC",
        point_size=0.01, point_value_per_lot=0.01, lot_min=0.01, lot_max=50.0,
        lot_step=0.001, contract_size=1, session_filter=False, news_filter=False,
        trades_24_7=True, swing_length_htf_override=5, sl_buffer_pips_override=5.0,
    ),
    "Jump 25 Index": InstrumentProfile(
        symbol="Jump 25 Index", instrument_type="SYNTHETIC",
        point_size=0.01, point_value_per_lot=0.01, lot_min=0.01, lot_max=30.0,
        lot_step=0.001, contract_size=1, session_filter=False, news_filter=False,
        trades_24_7=True, swing_length_htf_override=5, sl_buffer_pips_override=5.0,
    ),
    "Jump 50 Index": InstrumentProfile(
        symbol="Jump 50 Index", instrument_type="SYNTHETIC",
        point_size=0.01, point_value_per_lot=0.01, lot_min=0.01, lot_max=20.0,
        lot_step=0.001, contract_size=1, session_filter=False, news_filter=False,
        trades_24_7=True, swing_length_htf_override=5, sl_buffer_pips_override=5.0,
    ),
    "Jump 75 Index": InstrumentProfile(
        symbol="Jump 75 Index", instrument_type="SYNTHETIC",
        point_size=0.01, point_value_per_lot=0.01, lot_min=0.01, lot_max=15.0,
        lot_step=0.001, contract_size=1, session_filter=False, news_filter=False,
        trades_24_7=True, swing_length_htf_override=5, sl_buffer_pips_override=5.0,
    ),
    "Jump 100 Index": InstrumentProfile(
        symbol="Jump 100 Index", instrument_type="SYNTHETIC",
        point_size=0.01, point_value_per_lot=0.01, lot_min=0.01, lot_max=10.0,
        lot_step=0.001, contract_size=1, session_filter=False, news_filter=False,
        trades_24_7=True, swing_length_htf_override=5, sl_buffer_pips_override=8.0,
    ),

    # ── Step Index ───────────────────────────────────────────────────────────

    "Step Index": InstrumentProfile(
        symbol="Step Index", instrument_type="SYNTHETIC",
        point_size=0.1, point_value_per_lot=0.1, lot_min=0.1, lot_max=500.0,
        lot_step=0.01, contract_size=1, session_filter=False, news_filter=False,
        trades_24_7=True,
    ),
    "Step Index 200": InstrumentProfile(
        symbol="Step Index 200", instrument_type="SYNTHETIC",
        point_size=0.01, point_value_per_lot=0.01, lot_min=0.01, lot_max=100.0,
        lot_step=0.01, contract_size=1, session_filter=False, news_filter=False,
        trades_24_7=True,
    ),
    "Step Index 500": InstrumentProfile(
        symbol="Step Index 500", instrument_type="SYNTHETIC",
        point_size=0.01, point_value_per_lot=0.01, lot_min=0.01, lot_max=50.0,
        lot_step=0.01, contract_size=1, session_filter=False, news_filter=False,
        trades_24_7=True,
    ),

    # ── Range Break ──────────────────────────────────────────────────────────

    "Range Break 100 Index": InstrumentProfile(
        symbol="Range Break 100 Index", instrument_type="SYNTHETIC",
        point_size=0.01, point_value_per_lot=0.01, lot_min=0.01, lot_max=50.0,
        lot_step=0.01, contract_size=1, session_filter=False, news_filter=False,
        trades_24_7=True,
    ),
    "Range Break 200 Index": InstrumentProfile(
        symbol="Range Break 200 Index", instrument_type="SYNTHETIC",
        point_size=0.01, point_value_per_lot=0.01, lot_min=0.01, lot_max=100.0,
        lot_step=0.01, contract_size=1, session_filter=False, news_filter=False,
        trades_24_7=True,
    ),

    # ── Drift Switch Index ───────────────────────────────────────────────────

    "DEX 600DN": InstrumentProfile(
        symbol="DEX 600DN", instrument_type="SYNTHETIC",
        point_size=0.01, point_value_per_lot=0.01, lot_min=0.01, lot_max=50.0,
        lot_step=0.01, contract_size=1, session_filter=False, news_filter=False,
        trades_24_7=True,
    ),
    "DEX 600UP": InstrumentProfile(
        symbol="DEX 600UP", instrument_type="SYNTHETIC",
        point_size=0.01, point_value_per_lot=0.01, lot_min=0.01, lot_max=50.0,
        lot_step=0.01, contract_size=1, session_filter=False, news_filter=False,
        trades_24_7=True,
    ),
    "DEX 900DN": InstrumentProfile(
        symbol="DEX 900DN", instrument_type="SYNTHETIC",
        point_size=0.01, point_value_per_lot=0.01, lot_min=0.01, lot_max=50.0,
        lot_step=0.01, contract_size=1, session_filter=False, news_filter=False,
        trades_24_7=True,
    ),
    "DEX 900UP": InstrumentProfile(
        symbol="DEX 900UP", instrument_type="SYNTHETIC",
        point_size=0.01, point_value_per_lot=0.01, lot_min=0.01, lot_max=50.0,
        lot_step=0.01, contract_size=1, session_filter=False, news_filter=False,
        trades_24_7=True,
    ),

    # ── Commodities ───────────────────────────────────────────────────────────

    "XAUUSD": InstrumentProfile(
        symbol="XAUUSD",
        instrument_type="COMMODITY",
        point_size=0.01,
        point_value_per_lot=1.0,  # $1 per 0.01 move per standard lot = $100/pip
        lot_min=0.01,
        lot_max=50.0,
        lot_step=0.01,
        contract_size=100,
        session_filter=True,
        news_filter=True,
        trades_24_7=False,
        swing_length_htf_override=5,
        ob_impulse_ratio_override=2.0,
        liq_sweep_min_atr_mult_override=5.0,   # Gold sweeps are larger
        fvg_min_gap_atr_mult_override=3.0,
        sl_buffer_pips_override=10.0,
        atr_trail_multiplier_override=2.0,  # Wider trail for gold
        max_spread_atr_mult_override=0.5,
    ),

    # ── Forex Majors ──────────────────────────────────────────────────────────

    "EURUSD": InstrumentProfile(
        symbol="EURUSD",
        instrument_type="FOREX",
        point_size=0.00001,
        point_value_per_lot=1.0,   # $10 per pip per standard lot
        lot_min=0.01,
        lot_max=100.0,
        lot_step=0.01,
        contract_size=100000,
        session_filter=True,
        news_filter=True,
        trades_24_7=False,
    ),

    "GBPUSD": InstrumentProfile(
        symbol="GBPUSD",
        instrument_type="FOREX",
        point_size=0.00001,
        point_value_per_lot=1.0,
        lot_min=0.01,
        lot_max=100.0,
        lot_step=0.01,
        contract_size=100000,
        session_filter=True,
        news_filter=True,
        trades_24_7=False,
        liq_sweep_min_atr_mult_override=0.8,  # GBP can have larger sweeps
        sl_buffer_pips_override=7.0,
    ),

    "USDJPY": InstrumentProfile(
        symbol="USDJPY",
        instrument_type="FOREX",
        point_size=0.001,
        point_value_per_lot=100.0,    # Approx, varies with JPY rate
        lot_min=0.01,
        lot_max=100.0,
        lot_step=0.01,
        contract_size=100000,
        session_filter=True,
        news_filter=True,
        trades_24_7=False,
    ),

    # ── Indices ───────────────────────────────────────────────────────────────

    "US30": InstrumentProfile(
        symbol="US30",
        instrument_type="INDEX",
        point_size=1.0,
        point_value_per_lot=1.0,    # $1 per point per standard lot
        lot_min=0.01,
        lot_max=50.0,
        lot_step=0.01,
        contract_size=1,
        session_filter=True,
        news_filter=True,
        trades_24_7=False,
        swing_length_htf_override=5,
        liq_sweep_min_atr_mult_override=2.0,
        fvg_min_gap_atr_mult_override=1.5,
        sl_buffer_pips_override=10.0,
        max_spread_atr_mult_override=0.8,
    ),

    "BTCUSD": InstrumentProfile(
        symbol="BTCUSD",
        instrument_type="CRYPTO",
        point_size=1.0,
        point_value_per_lot=1.0,
        lot_min=0.001,
        lot_max=5.0,
        lot_step=0.001,
        contract_size=1,
        session_filter=False,
        news_filter=True,
        trades_24_7=True,
        swing_length_htf_override=5,
        liq_sweep_min_atr_mult_override=20.0,
        fvg_min_gap_atr_mult_override=10.0,
        sl_buffer_pips_override=50.0,
        atr_trail_multiplier_override=2.5,
        max_spread_atr_mult_override=10.0,
    ),
}

# Aliases for Deriv MT5 symbol naming variations
SYMBOL_ALIASES: dict[str, str] = {
    # Volatility Standard
    "V10":  "Volatility 10 Index",  "V25":  "Volatility 25 Index",
    "V50":  "Volatility 50 Index",  "V75":  "Volatility 75 Index",
    "V100": "Volatility 100 Index", "V150": "Volatility 150 Index",
    "V250": "Volatility 250 Index",
    # Volatility 1s
    "V10(1s)":  "Volatility 10 Index (1s)",  "V25(1s)":  "Volatility 25 Index (1s)",
    "V50(1s)":  "Volatility 50 Index (1s)",  "V75(1s)":  "Volatility 75 Index (1s)",
    "V100(1s)": "Volatility 100 Index (1s)", "V150(1s)": "Volatility 150 Index (1s)",
    "V250(1s)": "Volatility 250 Index (1s)",
    "Volatility 75 Index (1s)": "Volatility 75 Index (1s)",
    # Boom
    "BOOM300": "Boom 300 Index", "BOOM500": "Boom 500 Index",
    "BOOM600": "Boom 600 Index", "BOOM900": "Boom 900 Index",
    "BOOM1000": "Boom 1000 Index", "B300": "Boom 300 Index",
    "B500": "Boom 500 Index", "B1000": "Boom 1000 Index",
    # Crash
    "CRASH300": "Crash 300 Index", "CRASH500": "Crash 500 Index",
    "CRASH600": "Crash 600 Index", "CRASH900": "Crash 900 Index",
    "CRASH1000": "Crash 1000 Index", "C300": "Crash 300 Index",
    "C500": "Crash 500 Index", "C1000": "Crash 1000 Index",
    # Jump
    "J10": "Jump 10 Index", "J25": "Jump 25 Index", "J50": "Jump 50 Index",
    "J75": "Jump 75 Index", "J100": "Jump 100 Index",
    "JUMP10": "Jump 10 Index", "JUMP25": "Jump 25 Index",
    "JUMP50": "Jump 50 Index", "JUMP75": "Jump 75 Index", "JUMP100": "Jump 100 Index",
    # Step
    "STEP": "Step Index", "STEP200": "Step Index 200", "STEP500": "Step Index 500",
    # Range Break
    "RB100": "Range Break 100 Index", "RB200": "Range Break 200 Index",
    # DSI / DEX
    "DSI600DN": "DEX 600DN", "DSI600UP": "DEX 600UP",
    "DSI900DN": "DEX 900DN", "DSI900UP": "DEX 900UP",
    # Commodities / Forex / Crypto
    "Gold": "XAUUSD", "GOLD": "XAUUSD",
    "DJI": "US30", "DOW": "US30",
    "BTC": "BTCUSD",
}


def get_instrument_profile(symbol: str) -> Optional[InstrumentProfile]:
    """Look up instrument profile with alias resolution."""
    resolved = SYMBOL_ALIASES.get(symbol, symbol)
    return INSTRUMENT_PROFILES.get(resolved)


# ─────────────────────────────────────────────────────────────────────────────
# COMPOUNDING ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class CompoundingEngine:
    """
    Main compounding engine. One instance per user.
    Determines risk amount based on account balance and compounding step.
    """

    def __init__(self, config: "CompoundingParams", steps: Optional[List[CompoundingStep]] = None):
        self.config = config
        self.steps  = steps or DEFAULT_1_3RR_STEPS

    def get_step_for_balance(self, balance: float) -> CompoundingStep:
        """
        Returns the correct compounding step for the given balance.
        Finds the highest step whose entry_threshold <= balance.
        """
        current = self.steps[0]
        for step in self.steps:
            if balance >= step.entry_threshold:
                current = step
            else:
                break
        return current

    def get_risk_amount(self, balance: float, state: CompoundingState) -> float:
        """
        Returns the dollar risk amount for the next trade.
        
        In AUTO mode: directly from the step for current balance.
        In CONSERVATIVE mode: only advances if consecutive wins met.
        In MANUAL mode: uses whatever step the user has set.
        """
        if self.config.advance_mode == "MANUAL":
            # Respect the manually-set step
            step_idx = min(state.current_step - 1, len(self.steps) - 1)
            return self.steps[step_idx].risk_amount

        elif self.config.advance_mode == "CONSERVATIVE":
            # Only advance if enough consecutive wins at current level
            current_step = self.get_step_for_balance(balance)
            if (state.total_wins_at_level < self.config.conservative_wins_required and
                    state.current_step == current_step.step_number):
                # Stay at previous step even if balance qualifies for higher
                prev_step_idx = max(0, current_step.step_number - 2)
                return self.steps[prev_step_idx].risk_amount
            return current_step.risk_amount

        else:  # AUTO
            return self.get_step_for_balance(balance).risk_amount

    def update_state(
        self,
        state: CompoundingState,
        trade_won: bool,
        new_balance: float,
    ) -> CompoundingState:
        """
        Update compounding state after a trade closes.
        Handles step advances, downgrades, and streak tracking.
        """
        # Always update streaks first to prevent frozen state in LOSS_COUNT mode
        if trade_won:
            state.consecutive_wins   += 1
            state.consecutive_losses  = 0
            state.total_wins_at_level += 1
        else:
            state.consecutive_losses   += 1
            state.consecutive_wins      = 0
            state.total_losses_at_level += 1

        new_step = self.get_step_for_balance(new_balance)

        # Determine if we are allowed to step up based on advance_mode
        can_advance = False
        if self.config.advance_mode == "AUTO":
            can_advance = True
        elif self.config.advance_mode == "CONSERVATIVE":
            if state.total_wins_at_level >= self.config.conservative_wins_required:
                can_advance = True
        elif self.config.advance_mode == "MANUAL":
            can_advance = False # Never advance automatically

        if new_step.step_number > state.current_step and can_advance:
            # STEP UP
            state.current_step            = new_step.step_number
            state.risk_amount             = new_step.risk_amount
            state.entry_balance           = new_balance
            state.consecutive_wins        = 0
            state.consecutive_losses      = 0
            state.total_wins_at_level     = 0
            state.total_losses_at_level   = 0
            state.last_step_change_reason = "ADVANCE"
            state.last_step_change_balance = new_balance

        elif new_step.step_number < state.current_step and self.config.downgrade_mode == "THRESHOLD":
            # STEP DOWN (downgrade due to balance drop)
            state.current_step            = new_step.step_number
            state.risk_amount             = new_step.risk_amount
            state.entry_balance           = new_balance
            state.consecutive_wins        = 0
            state.consecutive_losses      = 0
            state.total_wins_at_level     = 0
            state.total_losses_at_level   = 0
            state.last_step_change_reason = "DOWNGRADE_THRESHOLD"
            state.last_step_change_balance = new_balance

        # LOSS COUNT downgrade
        if (self.config.downgrade_mode == "LOSS_COUNT" and
                state.consecutive_losses >= self.config.max_losses_before_downgrade):
            new_idx = max(0, state.current_step - 2)
            state.current_step             = self.steps[new_idx].step_number
            state.risk_amount              = self.steps[new_idx].risk_amount
            state.entry_balance            = new_balance
            state.consecutive_wins         = 0
            state.consecutive_losses       = 0
            state.total_wins_at_level      = 0
            state.total_losses_at_level    = 0
            state.last_step_change_reason  = "DOWNGRADE_LOSS_COUNT"
            state.last_step_change_balance = new_balance

        return state

    def get_progress_to_next_step(self, balance: float) -> dict:
        """Returns progress info for the UI progress bar."""
        current = self.get_step_for_balance(balance)
        step_idx = current.step_number - 1
        
        if step_idx >= len(self.steps) - 1:
            return {"at_max": True, "current_step": current.step_number, "pct": 100.0}

        next_step = self.steps[step_idx + 1]
        gap = next_step.entry_threshold - current.entry_threshold
        progress = balance - current.entry_threshold
        pct = min(100.0, (progress / gap * 100) if gap > 0 else 100.0)

        return {
            "at_max":            False,
            "current_step":      current.step_number,
            "current_risk":      current.risk_amount,
            "next_step":         next_step.step_number,
            "next_risk":         next_step.risk_amount,
            "next_threshold":    next_step.entry_threshold,
            "gap_remaining":     next_step.entry_threshold - balance,
            "progress_pct":      pct,
            "trades_to_next":    math.ceil((next_step.entry_threshold - balance) / current.reward_at_3r)
                                 if balance < next_step.entry_threshold else 0,
        }

    def project_growth(self, balance: float, win_rate: float, num_trades: int) -> list:
        """
        Project account growth over N trades at given win rate.
        Used in the analytics dashboard.
        Returns list of (trade_num, projected_balance, step).
        """
        state = CompoundingState(
            current_step=self.get_step_for_balance(balance).step_number,
            risk_amount=self.get_step_for_balance(balance).risk_amount,
        )
        projections = [(0, balance, state.current_step)]
        current_balance = balance
        win_accumulator = 0.0

        for i in range(1, num_trades + 1):
            step = self.get_step_for_balance(current_balance)
            risk = step.risk_amount
            reward = step.reward_at_3r

            # Deterministic alternating wins/losses based on win_rate
            win_accumulator += win_rate
            if win_accumulator >= 1.0:
                trade_won = True
                win_accumulator -= 1.0
                current_balance += reward
            else:
                trade_won = False
                current_balance -= risk
            
            current_balance = max(0, current_balance)

            state = self.update_state(state, trade_won, current_balance)
            projections.append((i, round(current_balance, 2), state.current_step))

        return projections


# ─────────────────────────────────────────────────────────────────────────────
# LOT SIZE CALCULATOR (with instrument profile)
# ─────────────────────────────────────────────────────────────────────────────

def risk_dollars_to_lots(
    risk_dollars:     float,
    entry_price:      float,
    stop_loss_price:  float,
    profile:          InstrumentProfile,
) -> float:
    """
    Convert a dollar risk amount into lot size for a specific instrument.
    
    Formula: lots = risk_$ / (sl_distance_in_points × point_value_per_lot)
    
    Works for forex, synthetics, gold, and indices.
    """
    sl_distance = abs(entry_price - stop_loss_price)
    sl_points   = sl_distance / profile.point_size

    if sl_points == 0:
        return profile.lot_min

    raw_lots = risk_dollars / (sl_points * profile.point_value_per_lot)

    # Clamp to broker constraints
    clamped = max(profile.lot_min, min(profile.lot_max, raw_lots))

    # Round to lot step
    rounded = round(clamped / profile.lot_step) * profile.lot_step
    return round(rounded, 4)


def lots_to_risk_dollars(
    lots:            float,
    entry_price:     float,
    stop_loss_price: float,
    profile:         InstrumentProfile,
) -> float:
    """Inverse: calculate dollar risk for given lot size (for display/validation)."""
    sl_distance = abs(entry_price - stop_loss_price)
    sl_points   = sl_distance / profile.point_size
    return lots * sl_points * profile.point_value_per_lot


# ─────────────────────────────────────────────────────────────────────────────
# COMPOUNDING PARAMS (part of params.py — also defined here for reference)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CompoundingParams:
    """User-configurable compounding plan settings."""

    enabled: bool = False
    """Enable stepped dollar-risk compounding. Default OFF — uses % risk."""

    use_default_plan: bool = True
    """Use the built-in 1:3 RR 18-step plan. False = use custom_steps."""

    custom_steps: Optional[List[dict]] = None
    """Custom step table. List of dicts with keys: step, risk, entry_threshold."""

    advance_mode: Literal["AUTO", "CONSERVATIVE", "MANUAL"] = "AUTO"
    """
    AUTO:         Advance when balance crosses next step threshold automatically.
    CONSERVATIVE: Require consecutive_wins_required wins at current level first.
    MANUAL:       User manually presses advance/retreat in dashboard.
    """

    conservative_wins_required: int = 2
    """In CONSERVATIVE mode: require this many wins at current level before advancing."""

    downgrade_mode: Literal["THRESHOLD", "LOSS_COUNT"] = "THRESHOLD"
    """
    THRESHOLD:  Step down automatically when balance drops below lower threshold.
    LOSS_COUNT: Step down after max_losses_before_downgrade consecutive losses.
    """

    max_losses_before_downgrade: int = 3
    """In LOSS_COUNT mode: step down after this many consecutive losses."""

    def get_steps(self) -> List[CompoundingStep]:
        if self.use_default_plan or not self.custom_steps:
            return DEFAULT_1_3RR_STEPS
            
        # Sort custom steps by entry_threshold ascending
        sorted_steps = sorted(self.custom_steps, key=lambda x: x.get("entry_threshold", 0))
        
        # Parse custom steps
        return [
            CompoundingStep(
                step_number=s["step"],
                risk_amount=s["risk"],
                reward_at_3r=s["risk"] * 3,
                entry_threshold=s["entry_threshold"],
                account_after_win=s["entry_threshold"] + s["risk"] * 3,
            )
            for s in sorted_steps
        ]

    def build_engine(self) -> CompoundingEngine:
        return CompoundingEngine(config=self, steps=self.get_steps())
