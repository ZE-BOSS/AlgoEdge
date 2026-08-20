"""
backend/risk/compounding.py

Instrument profile definitions for all supported trading instruments.
Used by position_sizer.py and backtester for correct lot sizing and PnL calculation.
All compounding step/plan logic has been removed — AlgoEdge uses fixed % risk per trade.
"""

from dataclasses import dataclass
from typing import Literal

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
    swing_length_htf_override:     int | None   = None
    swing_length_ltf_override:     int | None   = None
    ob_impulse_ratio_override:     float | None = None
    liq_sweep_min_atr_mult_override:   float | None = None
    fvg_min_gap_atr_mult_override:     float | None = None
    sl_buffer_pips_override:       float | None = None
    atr_trail_multiplier_override: float | None = None
    max_spread_atr_mult_override:      float | None = None

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
        # Verified from real trades: P&L = lots × price_move × $1.00/point
        # Universal formula: ratio = point_value_per_lot / point_size = 0.01/0.01 = 1.0
        # i.e. $1.00 per 1-unit price move per lot. Changing to 1.0 breaks this (ratio=100).
        point_value_per_lot=0.01,
        lot_min=0.01,
        lot_max=10.0,
        lot_step=0.001,
        contract_size=1,
        session_filter=False,
        news_filter=False,
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
        # ratio = 0.01/0.01 = 1.0 → $1 per 1-unit price move per lot (correct)
        point_value_per_lot=0.01,
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
        point_value_per_lot=0.01,  # ratio=1.0 → $1 per 1-unit move per lot
        lot_min=0.5,
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
        point_value_per_lot=0.01,  # ratio=1.0 → $1 per 1-unit move per lot
        lot_min=0.1,
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
        # Verified from real Crash 1000 trades: P&L = lots × points × $1.00
        # ratio = 0.01/0.01 = 1.0 → $1 per 1-unit price move per lot
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
        liq_sweep_min_atr_mult_override=2.0,
    ),

    "Crash 1000 Index": InstrumentProfile(
        symbol="Crash 1000 Index",
        instrument_type="SYNTHETIC",
        # Aligned to docs/DriftJumpAlpha_Strategy_Spec_v2.md's documented
        # pip_size: 0.001 for CRASH1000 (was 0.01 — 10x too large — which fed
        # directly into get_pip_size() for pip-distance params like
        # spike_threshold_pips/recovery_target_pips). point_value_per_lot is
        # scaled down by the same 10x so point_value_per_lot/point_size stays at
        # the real-trade-verified ratio of 1.0 ($1 per 1-unit price move per
        # lot) — PnL calc (backtester/engine.py's tick_value/tick_size ratio)
        # is unaffected by this change.
        point_size=0.001,
        point_value_per_lot=0.001,
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
        # ratio = 0.001/0.001 = 1.0 → $1 per 1-unit price move per lot
        point_value_per_lot=0.001,
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

    "Volatility 10 (1s) Index": InstrumentProfile(
        symbol="Volatility 10 (1s) Index", instrument_type="SYNTHETIC",
        point_size=0.01, point_value_per_lot=0.01, lot_min=0.5, lot_max=100.0,
        lot_step=0.001, contract_size=1, session_filter=False, news_filter=False, trades_24_7=True,
    ),
    "Volatility 25 (1s) Index": InstrumentProfile(
        symbol="Volatility 25 (1s) Index", instrument_type="SYNTHETIC",
        point_size=0.01, point_value_per_lot=0.01, lot_min=0.005, lot_max=50.0,
        lot_step=0.001, contract_size=1, session_filter=False, news_filter=False, trades_24_7=True,
    ),
    "Volatility 50 (1s) Index": InstrumentProfile(
        symbol="Volatility 50 (1s) Index", instrument_type="SYNTHETIC",
        point_size=0.01, point_value_per_lot=0.01, lot_min=0.005, lot_max=20.0,
        lot_step=0.001, contract_size=1, session_filter=False, news_filter=False, trades_24_7=True,
    ),
    "Volatility 75 (1s) Index": InstrumentProfile(
        symbol="Volatility 75 (1s) Index", instrument_type="SYNTHETIC",
        point_size=0.01, point_value_per_lot=0.01, lot_min=0.05, lot_max=10.0,
        lot_step=0.001, contract_size=1, session_filter=False, news_filter=False, trades_24_7=True,
    ),
    "Volatility 100 (1s) Index": InstrumentProfile(
        symbol="Volatility 100 (1s) Index", instrument_type="SYNTHETIC",
        point_size=0.01, point_value_per_lot=0.01, lot_min=0.1, lot_max=5.0,
        lot_step=0.001, contract_size=1, session_filter=False, news_filter=False, trades_24_7=True,
    ),
    "Volatility 150 (1s) Index": InstrumentProfile(
        symbol="Volatility 150 (1s) Index", instrument_type="SYNTHETIC",
        point_size=0.01, point_value_per_lot=0.01, lot_min=0.001, lot_max=3.0,
        lot_step=0.001, contract_size=1, session_filter=False, news_filter=False, trades_24_7=True,
    ),
    "Volatility 250 (1s) Index": InstrumentProfile(
        symbol="Volatility 250 (1s) Index", instrument_type="SYNTHETIC",
        point_size=0.01, point_value_per_lot=0.01, lot_min=0.001, lot_max=2.0,
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
    # point_size/point_value_per_lot aligned to spec's 0.001 Crash-index pip size
    # (was 0.01 — see "Crash 1000 Index" comment above); ratio kept at 1.0 so PnL
    # calc is unaffected.
    "Crash 300 Index": InstrumentProfile(
        symbol="Crash 300 Index", instrument_type="SYNTHETIC",
        point_size=0.001, point_value_per_lot=0.001, lot_min=0.5, lot_max=10.0,
        lot_step=0.001, contract_size=1, session_filter=False, news_filter=False,
        trades_24_7=True, swing_length_htf_override=5, ob_impulse_ratio_override=2.5,
    ),
    "Crash 500 Index": InstrumentProfile(
        symbol="Crash 500 Index", instrument_type="SYNTHETIC",
        point_size=0.001, point_value_per_lot=0.001, lot_min=0.2, lot_max=10.0,
        lot_step=0.001, contract_size=1, session_filter=False, news_filter=False,
        trades_24_7=True, swing_length_htf_override=5, ob_impulse_ratio_override=2.5,
    ),
    "Crash 600 Index": InstrumentProfile(
        symbol="Crash 600 Index", instrument_type="SYNTHETIC",
        point_size=0.001, point_value_per_lot=0.001, lot_min=0.001, lot_max=10.0,
        lot_step=0.001, contract_size=1, session_filter=False, news_filter=False,
        trades_24_7=True, swing_length_htf_override=5, ob_impulse_ratio_override=2.5,
    ),
    "Crash 900 Index": InstrumentProfile(
        symbol="Crash 900 Index", instrument_type="SYNTHETIC",
        point_size=0.001, point_value_per_lot=0.001, lot_min=0.001, lot_max=10.0,
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
    "USOIL": InstrumentProfile(
        symbol="USOIL",
        instrument_type="COMMODITY",
        point_size=0.01,
        point_value_per_lot=1.0,
        lot_min=0.01,
        lot_max=50.0,
        lot_step=0.01,
        contract_size=1000,
        session_filter=True,
        news_filter=True,
        trades_24_7=False,
    ),
    "UKOIL": InstrumentProfile(
        symbol="UKOIL",
        instrument_type="COMMODITY",
        point_size=0.01,
        point_value_per_lot=1.0,
        lot_min=0.01,
        lot_max=50.0,
        lot_step=0.01,
        contract_size=1000,
        session_filter=True,
        news_filter=True,
        trades_24_7=False,
    ),
    "XCUUSD": InstrumentProfile(
        symbol="XCUUSD",
        instrument_type="COMMODITY",
        point_size=0.0001,
        point_value_per_lot=2.5,  # 25,000 lb contract x $0.0001 — verify against your broker's actual copper contract spec
        lot_min=0.01,
        lot_max=50.0,
        lot_step=0.01,
        contract_size=25000,
        session_filter=True,
        news_filter=True,
        trades_24_7=False,
    ),
    "ETHUSD": InstrumentProfile(
        symbol="ETHUSD",
        instrument_type="CRYPTO",
        point_size=0.01,
        point_value_per_lot=0.01,
        lot_min=0.01,
        lot_max=100.0,
        lot_step=0.01,
        contract_size=1,
        session_filter=False,
        news_filter=False,
        trades_24_7=True,
    ),
    "GBPJPY": InstrumentProfile(
        symbol="GBPJPY",
        instrument_type="FOREX",
        point_size=0.01,
        # FIX: was 0.067 — off by exactly 100x. contract_size(100000) x
        # point_size(0.01) = 1000 JPY/point/lot; converted to USD at a
        # typical ~150 USDJPY rate, that's ~$6.67/point/lot, not $0.067.
        # The old value caused the sizer to open lots ~100x too large for
        # the same intended dollar risk on this pair specifically — this was
        # the single most severe instrument bug found in this audit.
        # Approximate — rates move; verify against your broker if precision matters.
        point_value_per_lot=6.7,
        lot_min=0.01,
        lot_max=100.0,
        lot_step=0.01,
        contract_size=100000,
        session_filter=True,
        news_filter=True,
        trades_24_7=False,
    ),
    "AUDUSD": InstrumentProfile(
        symbol="AUDUSD",
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
    ),
    "GBPNZD": InstrumentProfile(
        symbol="GBPNZD", instrument_type="FOREX", point_size=0.00001, point_value_per_lot=0.6, lot_min=0.01, lot_max=100.0, lot_step=0.01, contract_size=100000, session_filter=True, news_filter=True, trades_24_7=False,
    ),
    "GBPAUD": InstrumentProfile(
        symbol="GBPAUD", instrument_type="FOREX", point_size=0.00001, point_value_per_lot=0.65, lot_min=0.01, lot_max=100.0, lot_step=0.01, contract_size=100000, session_filter=True, news_filter=True, trades_24_7=False,
    ),
    "GBPCHF": InstrumentProfile(
        symbol="GBPCHF", instrument_type="FOREX", point_size=0.00001, point_value_per_lot=1.15, lot_min=0.01, lot_max=100.0, lot_step=0.01, contract_size=100000, session_filter=True, news_filter=True, trades_24_7=False,
    ),
    "EURJPY": InstrumentProfile(
        symbol="EURJPY", instrument_type="FOREX", point_size=0.01,
        point_value_per_lot=6.7,  # FIX: was 0.067 — same 100x error as GBPJPY, same corrected math
        lot_min=0.01, lot_max=100.0, lot_step=0.01, contract_size=100000, session_filter=True, news_filter=True, trades_24_7=False,
    ),
    "AUDJPY": InstrumentProfile(
        symbol="AUDJPY", instrument_type="FOREX", point_size=0.01, point_value_per_lot=6.7,
        lot_min=0.01, lot_max=100.0, lot_step=0.01, contract_size=100000, session_filter=True, news_filter=True, trades_24_7=False,
    ),
    "CADJPY": InstrumentProfile(
        symbol="CADJPY", instrument_type="FOREX", point_size=0.01, point_value_per_lot=6.7,
        lot_min=0.01, lot_max=100.0, lot_step=0.01, contract_size=100000, session_filter=True, news_filter=True, trades_24_7=False,
    ),
    "EURAUD": InstrumentProfile(
        symbol="EURAUD", instrument_type="FOREX", point_size=0.00001, point_value_per_lot=0.65, lot_min=0.01, lot_max=100.0, lot_step=0.01, contract_size=100000, session_filter=True, news_filter=True, trades_24_7=False,
    ),
    "GER40": InstrumentProfile(
        symbol="GER40", instrument_type="INDEX", point_size=0.1, point_value_per_lot=1.2, lot_min=0.01, lot_max=50.0, lot_step=0.01, contract_size=1, session_filter=True, news_filter=True, trades_24_7=False,
    ),
    "HK50": InstrumentProfile(
        symbol="HK50", instrument_type="INDEX", point_size=1.0, point_value_per_lot=1.0, lot_min=0.01, lot_max=50.0, lot_step=0.01, contract_size=1, session_filter=True, news_filter=True, trades_24_7=False,
    ),
    "NG": InstrumentProfile(
        symbol="NG", instrument_type="COMMODITY", point_size=0.001, point_value_per_lot=1.0, lot_min=0.01, lot_max=50.0, lot_step=0.01, contract_size=1000, session_filter=True, news_filter=True, trades_24_7=False,
    ),
    "XPTUSD": InstrumentProfile(
        symbol="XPTUSD", instrument_type="COMMODITY", point_size=0.01, point_value_per_lot=1.0, lot_min=0.01, lot_max=50.0, lot_step=0.01, contract_size=100, session_filter=True, news_filter=True, trades_24_7=False,
    ),
    "XAGUSD": InstrumentProfile(
        symbol="XAGUSD", instrument_type="COMMODITY", point_size=0.001, point_value_per_lot=5.0, lot_min=0.01, lot_max=50.0, lot_step=0.01, contract_size=5000, session_filter=True, news_filter=True, trades_24_7=False,
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
        # FIX: was 100.0. contract_size(100000) x point_size(0.001) = 100
        # JPY/point/lot — that raw JPY figure was being used directly as if
        # it were already USD, skipping the JPY->USD conversion entirely.
        # At a typical ~150 USDJPY rate the real value is 100/150 ≈ 0.67,
        # not 100. The old value was ~150x too large, causing the sizer to
        # open lots far too SMALL for the intended risk (under-risking,
        # opposite direction from the GBPJPY/EURJPY bug above).
        # Approximate — rates move; verify against your broker if precision matters.
        point_value_per_lot=0.67,
        lot_min=0.01,
        lot_max=100.0,
        lot_step=0.01,
        contract_size=100000,
        session_filter=True,
        news_filter=True,
        trades_24_7=False,
    ),
    "USDCHF": InstrumentProfile(
        symbol="USDCHF", instrument_type="FOREX", point_size=0.00001, point_value_per_lot=1.15,
        lot_min=0.01, lot_max=100.0, lot_step=0.01, contract_size=100000, session_filter=True, news_filter=True, trades_24_7=False,
    ),
    "USDCAD": InstrumentProfile(
        symbol="USDCAD", instrument_type="FOREX", point_size=0.00001, point_value_per_lot=0.735,
        lot_min=0.01, lot_max=100.0, lot_step=0.01, contract_size=100000, session_filter=True, news_filter=True, trades_24_7=False,
    ),
    "NZDUSD": InstrumentProfile(
        symbol="NZDUSD", instrument_type="FOREX", point_size=0.00001, point_value_per_lot=1.0,
        lot_min=0.01, lot_max=100.0, lot_step=0.01, contract_size=100000, session_filter=True, news_filter=True, trades_24_7=False,
    ),
    "GBPCAD": InstrumentProfile(
        symbol="GBPCAD", instrument_type="FOREX", point_size=0.00001, point_value_per_lot=0.735,
        lot_min=0.01, lot_max=100.0, lot_step=0.01, contract_size=100000, session_filter=True, news_filter=True, trades_24_7=False,
    ),
    "EURGBP": InstrumentProfile(
        symbol="EURGBP", instrument_type="FOREX", point_size=0.00001, point_value_per_lot=1.27,
        lot_min=0.01, lot_max=100.0, lot_step=0.01, contract_size=100000, session_filter=True, news_filter=True, trades_24_7=False,
    ),

    # ── Indices ───────────────────────────────────────────────────────────────

    "US30": InstrumentProfile(
        symbol="US30",
        instrument_type="INDEX",
        point_size=1.0,              # Dow Jones: minimum move = 1.0 point — correct
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

    "NAS100": InstrumentProfile(
        symbol="NAS100",
        instrument_type="INDEX",
        point_size=0.25,
        point_value_per_lot=2.5,
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

    "US500": InstrumentProfile(
        symbol="US500",
        instrument_type="INDEX",
        point_size=0.1,
        point_value_per_lot=1.0,
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

    # ── Additional Indices ───────────────────────────────────────────────────

    "US2000": InstrumentProfile(
        symbol="US2000", instrument_type="INDEX", point_size=0.1, point_value_per_lot=1.0,
        lot_min=0.01, lot_max=50.0, lot_step=0.01, contract_size=1, session_filter=True, news_filter=True, trades_24_7=False,
    ),
    "UK100": InstrumentProfile(
        symbol="UK100", instrument_type="INDEX", point_size=1.0, point_value_per_lot=1.0,
        lot_min=0.01, lot_max=50.0, lot_step=0.01, contract_size=1, session_filter=True, news_filter=True, trades_24_7=False,
    ),
    "FRA40": InstrumentProfile(
        symbol="FRA40", instrument_type="INDEX", point_size=1.0, point_value_per_lot=1.0,
        lot_min=0.01, lot_max=50.0, lot_step=0.01, contract_size=1, session_filter=True, news_filter=True, trades_24_7=False,
    ),
    "EU50": InstrumentProfile(
        symbol="EU50", instrument_type="INDEX", point_size=1.0, point_value_per_lot=1.0,
        lot_min=0.01, lot_max=50.0, lot_step=0.01, contract_size=1, session_filter=True, news_filter=True, trades_24_7=False,
    ),
    "NTH25": InstrumentProfile(
        symbol="NTH25", instrument_type="INDEX", point_size=1.0, point_value_per_lot=1.0,
        lot_min=0.01, lot_max=50.0, lot_step=0.01, contract_size=1, session_filter=True, news_filter=True, trades_24_7=False,
    ),
    "SWI20": InstrumentProfile(
        symbol="SWI20", instrument_type="INDEX", point_size=1.0, point_value_per_lot=1.0,
        lot_min=0.01, lot_max=50.0, lot_step=0.01, contract_size=1, session_filter=True, news_filter=True, trades_24_7=False,
    ),
    "AUS200": InstrumentProfile(
        symbol="AUS200", instrument_type="INDEX", point_size=1.0, point_value_per_lot=1.0,
        lot_min=0.01, lot_max=50.0, lot_step=0.01, contract_size=1, session_filter=True, news_filter=True, trades_24_7=False,
    ),
    "JP225": InstrumentProfile(
        symbol="JP225", instrument_type="INDEX", point_size=1.0, point_value_per_lot=1.0,
        lot_min=0.01, lot_max=50.0, lot_step=0.01, contract_size=1, session_filter=True, news_filter=True, trades_24_7=False,
    ),

    # ── Additional Crypto ────────────────────────────────────────────────────

    # NOTE: the four crypto profiles below are new additions, not audited
    # against a real broker spec the way the existing instruments above were —
    # verify contract_size/point_value_per_lot against your actual broker
    # before trading these live. lot_min is set for a rough ~$15-50 minimum
    # notional given typical DOGE/XRP/SOL/LTC prices, not a confirmed broker minimum.
    "DOGUSD": InstrumentProfile(
        symbol="DOGUSD", instrument_type="CRYPTO", point_size=0.0001, point_value_per_lot=0.0001,
        lot_min=100.0, lot_max=1000000.0, lot_step=10.0, contract_size=1, session_filter=False, news_filter=False, trades_24_7=True,
    ),
    "SOLUSD": InstrumentProfile(
        symbol="SOLUSD", instrument_type="CRYPTO", point_size=0.01, point_value_per_lot=0.01,
        lot_min=0.1, lot_max=1000.0, lot_step=0.1, contract_size=1, session_filter=False, news_filter=False, trades_24_7=True,
    ),
    "XRPUSD": InstrumentProfile(
        # FIX (2.4): was lot_max=50.0 == lot_min — a data-entry bug that pinned every
        # XRPUSD trade to exactly 50 lots regardless of computed risk-based sizing.
        # Widened to match sibling crypto profiles (SOLUSD/LTCUSD use lot_max=1000).
        symbol="XRPUSD", instrument_type="CRYPTO", point_size=0.0001, point_value_per_lot=0.01,
        lot_min=50.0, lot_max=1000.0, lot_step=10.0, contract_size=1, session_filter=False, news_filter=False, trades_24_7=True,
    ),
    "LTCUSD": InstrumentProfile(
        symbol="LTCUSD", instrument_type="CRYPTO", point_size=0.01, point_value_per_lot=0.01,
        lot_min=0.1, lot_max=1000.0, lot_step=0.1, contract_size=1, session_filter=False, news_filter=False, trades_24_7=True,
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
    "V10(1s)":  "Volatility 10 (1s) Index",  "V25(1s)":  "Volatility 25 (1s) Index",
    "V50(1s)":  "Volatility 50 (1s) Index",  "V75(1s)":  "Volatility 75 (1s) Index",
    "V100(1s)": "Volatility 100 (1s) Index", "V150(1s)": "Volatility 150 (1s) Index",
    "V250(1s)": "Volatility 250 (1s) Index",
    "Volatility 75 (1s) Index": "Volatility 75 (1s) Index",
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
    "Gold": "XAUUSD", "GOLD": "XAUUSD", "XAU": "XAUUSD",
    "Silver": "XAGUSD", "SILVER": "XAGUSD", "XAG": "XAGUSD",
    "Platinum": "XPTUSD", "XPT": "XPTUSD",
    "WTI": "USOIL", "OIL": "USOIL", "Crude": "USOIL", "Crude Oil": "USOIL", "XTIUSD": "USOIL", "WTICrude": "USOIL", "US Oil": "USOIL", "USOUSD": "USOIL",
    "NG": "NG", "XNGUSD": "NG", "Natural Gas": "NG", "NGAS": "NG",
    "ETH": "ETHUSD", "Ethereum": "ETHUSD",
    "DJI": "US30", "DOW": "US30", "Wall Street 30": "US30", "WS30": "US30", "YM": "US30", "US30.cash": "US30",
    "US100": "NAS100", "USTEC": "NAS100", "NDX": "NAS100",
    "NDX100": "NAS100", "US Tech100": "NAS100", "US Tech 100": "NAS100",
    "USTECH": "NAS100", "NQ100": "NAS100", "NQ": "NAS100",
    "SPX500": "US500", "SPX": "US500", "SP500": "US500", "S&P500": "US500",
    "S&P 500": "US500", "US 500": "US500", "INX": "US500", "ES": "US500", "US SP 500": "US500", "SP500.cash": "US500",
    "GER40": "GER40", "DAX": "GER40", "DE40": "GER40", "DAX40": "GER40", "GER30": "GER40",
    "HK50": "HK50", "Hang Seng": "HK50", "HSI": "HK50", "HSI50": "HK50",
    "BTC": "BTCUSD", "Bitcoin": "BTCUSD",
    "Aussie": "AUDUSD", "Geppy": "GBPJPY", "GJ": "GBPJPY",
    "GBPNZD": "GBPNZD", "GBPAUD": "GBPAUD", "GBPCHF": "GBPCHF",
    "EURJPY": "EURJPY", "EURAUD": "EURAUD",

    # ── Brent Crude / Copper ──────────────────────────────────────────────
    "UKOIL": "UKOIL", "UKOUSD": "UKOIL", "BRENT": "UKOIL", "XBRUSD": "UKOIL", "UK Brent Oil": "UKOIL", "UK OIL": "UKOIL",
    "COPPER": "XCUUSD", "HG": "XCUUSD", "XCUUSD": "XCUUSD",

    # ── Additional Indices (Deriv / FundedNext / common broker names) ──────
    "US2000": "US2000", "RUT": "US2000",
    "UK100": "UK100", "FTSE100": "UK100", "FTSE 100": "UK100", "UK 100": "UK100",
    "FRA40": "FRA40", "CAC40": "FRA40", "CAC 40": "FRA40", "France 40": "FRA40",
    "EU50": "EU50", "EUSTX50": "EU50", "ESTX50": "EU50", "Europe 50": "EU50",
    "NTH25": "NTH25", "NETH25": "NTH25", "AEX25": "NTH25", "Netherlands 25": "NTH25",
    "SWI20": "SWI20", "SMI20": "SWI20", "Switzerland 20": "SWI20",
    "AUS200": "AUS200", "ASX200": "AUS200", "Australia 200": "AUS200",
    "JP225": "JP225", "JPN225": "JP225", "NIK225": "JP225", "Japan 225": "JP225", "Nikkei": "JP225",

    # ── Additional Forex ─────────────────────────────────────────────────
    "USDCHF": "USDCHF", "USDCAD": "USDCAD", "NZDUSD": "NZDUSD", "Kiwi": "NZDUSD",
    "AUDJPY": "AUDJPY", "CADJPY": "CADJPY", "GBPCAD": "GBPCAD", "EURGBP": "EURGBP",

    # ── Additional Crypto ────────────────────────────────────────────────
    "DOGE": "DOGUSD", "Dogecoin": "DOGUSD", "DOGUSD": "DOGUSD",
    "SOL": "SOLUSD", "Solana": "SOLUSD", "SOLUSD": "SOLUSD",
    "XRP": "XRPUSD", "Ripple": "XRPUSD", "XRPUSD": "XRPUSD",
    "LTC": "LTCUSD", "Litecoin": "LTCUSD", "LTCUSD": "LTCUSD",
}

import re

def get_instrument_profile(symbol: str) -> InstrumentProfile | None:
    """Look up instrument profile with alias resolution and suffix stripping."""
    # Common suffixes added by brokers (.m, c, _i, #, .a)
    clean_symbol = re.sub(r'(\.m|c|_i|#|\.a|x)$', '', symbol, flags=re.IGNORECASE)
    clean_symbol = clean_symbol.replace('/', '')
    
    # Try the cleaned symbol first
    resolved = SYMBOL_ALIASES.get(clean_symbol, clean_symbol)
    profile = INSTRUMENT_PROFILES.get(resolved)
    if profile:
        return profile
        
    # Fallback to the exact raw symbol
    resolved_exact = SYMBOL_ALIASES.get(symbol, symbol)
    return INSTRUMENT_PROFILES.get(resolved_exact)

