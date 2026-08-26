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
        # FIX (audit): contract_size was 1000, contradicting the PnL ratio.
        # point_value_per_lot/point_size = 1.0/0.01 = 100 → $100 per $1.00 move
        # per lot, which is exactly a 100-BARREL contract ($0.01 move × 100
        # barrels = $1.00 per point). A 1000-barrel contract would make a $1.00
        # move worth $1000, i.e. point_value_per_lot = 10.0.
        # The 100-barrel spec is the standard retail/CFD WTI contract at
        # IC Markets/Pepperstone/Deriv-style brokers, and the ratio is the
        # load-bearing field (it drives both lot sizing AND PnL), so
        # contract_size is corrected to agree with it. PnL behaviour unchanged.
        contract_size=100,
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
        # FIX (audit): same as USOIL — Brent CFD is a 100-barrel contract here
        # ($1.00 per 0.01 move per lot). contract_size 1000 → 100. PnL unchanged.
        contract_size=100,
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
    # FIX (audit): contract_size was 1, contradicting the PnL ratio.
    # point_value_per_lot/point_size = 1.2/0.1 = 12 → $12 per 1.0 index point
    # per lot. GER40 is EUR-denominated, so USD/point = contract_size ×
    # point_size × EURUSD: 12 = cs × 0.1 × ~1.20 → cs = 10, i.e. the common
    # €10-per-index-point DAX CFD contract (10 index units per lot). Note this
    # is the one INDEX profile where ratio != contract_size legitimately —
    # point_value_per_lot is USD while contract_size is in EUR index units.
    # The ratio drives sizing and PnL, so it is preserved; only the cosmetic
    # contract_size is corrected. (If your broker quotes DAX at €1/point,
    # point_value_per_lot should become 0.12 and contract_size 1.)
    # [1.14/C2] UNRESOLVED, and now known to be unresolvable here. Checked
    # 2026-08-23: mt5.symbol_select("GER40") returns False on this Deriv-Demo
    # account — the broker does not offer it (nor NAS100 / US30). So the
    # ratio/contract_size mismatch flagged in the audit cannot be confirmed or
    # corrected against a live quote, and the profile is also unreachable in
    # practice: any run naming GER40 fails at data fetch, not at sizing.
    # Left EXACTLY as-is rather than adjusted on a guess. Re-verify against a
    # broker that actually lists it before trusting these numbers.
    "GER40": InstrumentProfile(
        symbol="GER40", instrument_type="INDEX", point_size=0.1, point_value_per_lot=1.2, lot_min=0.01, lot_max=50.0, lot_step=0.01, contract_size=10, session_filter=True, news_filter=True, trades_24_7=False,
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
        point_size=0.25,          # NAS100 trades in 0.25-point ticks
        point_value_per_lot=2.5,  # $2.50 per 0.25 tick
        lot_min=0.01,
        lot_max=50.0,
        lot_step=0.01,
        # FIX (audit): contract_size was 1, contradicting the PnL ratio.
        # point_value_per_lot/point_size = 2.5/0.25 = 10 → $10 per index point
        # per lot, the standard CFD spec (10 index units per lot; between the
        # $5 micro and $20 E-mini NQ futures multipliers). USD-quoted, so
        # contract_size must equal the ratio. The ratio is load-bearing and
        # matches live PnL, so contract_size is corrected: 1 → 10.
        contract_size=10,
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
        point_size=0.1,           # S&P CFD quoted to 0.1 index points
        point_value_per_lot=1.0,  # $1.00 per 0.1 point
        lot_min=0.01,
        lot_max=50.0,
        lot_step=0.01,
        # FIX (audit): contract_size was 1, contradicting the PnL ratio.
        # 1.0/0.1 = 10 → $10 per index point per lot (10 index units per lot —
        # the standard CFD spec, between the $5 micro and $50 E-mini ES
        # multipliers). USD-quoted, so contract_size must equal the ratio.
        contract_size=10,
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

    # FIX (audit): contract_size was 1, contradicting the PnL ratio.
    # 1.0/0.1 = 10 → $10 per Russell 2000 index point per lot (10 index units
    # per lot; the E-mini RTY futures multiplier is $50, the CFD is $10).
    # USD-quoted, so contract_size must equal the ratio. Ratio preserved.
    "US2000": InstrumentProfile(
        symbol="US2000", instrument_type="INDEX", point_size=0.1, point_value_per_lot=1.0,
        lot_min=0.01, lot_max=50.0, lot_step=0.01, contract_size=10, session_filter=True, news_filter=True, trades_24_7=False,
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
        # [C3] Verified 2026-08-23 against mt5.symbol_info("DOGUSD"):
        #     volume_min=1500.0  volume_max=100000.0  volume_step=100.0
        #     digits=5  point=1e-05  trade_contract_size=1.0
        # point_size corrected 0.0001 -> 1e-05 to match the broker's 5 digits;
        # lot_max was 10x too LARGE (1,000,000 vs. 100,000), so the sizer could
        # produce volumes the broker would reject at the top of the range as
        # well as the bottom.
        symbol="DOGUSD", instrument_type="CRYPTO", point_size=0.00001, point_value_per_lot=0.00001,
        lot_min=1500.0, lot_max=100000.0, lot_step=100.0, contract_size=1, session_filter=False, news_filter=False, trades_24_7=True,
    ),
    "SOLUSD": InstrumentProfile(
        symbol="SOLUSD", instrument_type="CRYPTO", point_size=0.01, point_value_per_lot=0.01,
        lot_min=0.1, lot_max=1000.0, lot_step=0.1, contract_size=1, session_filter=False, news_filter=False, trades_24_7=True,
    ),
    "XRPUSD": InstrumentProfile(
        # FIX (2.4): was lot_max=50.0 == lot_min — a data-entry bug that pinned every
        # XRPUSD trade to exactly 50 lots regardless of computed risk-based sizing.
        # Widened to match sibling crypto profiles (SOLUSD/LTCUSD use lot_max=1000).
        #
        # FIX (audit): point_value_per_lot was 0.01 (copy-pasted from SOLUSD,
        # whose point_size is 0.01) while XRP's point_size is 0.0001 to match
        # its sub-dollar price. That made the ratio 100 — i.e. $100 per $1.00
        # XRP move per lot, a 100-XRP contract — against contract_size=1.
        # Here the RATIO is the wrong field, not contract_size: every sibling
        # crypto profile (DOGUSD/SOLUSD/LTCUSD, and BTCUSD/ETHUSD) is 1 coin
        # per lot with ratio 1.0, and XRPUSD's lot_min=50/lot_step=10 were
        # chosen for a ~$25 minimum notional at 1 XRP per lot. Corrected to
        # 0.0001 → ratio 1.0, consistent with contract_size=1.
        # [1.15/1.16/C3] RESOLVED 2026-08-23 against the live terminal, not guessed.
        # mt5.symbol_info("XRPUSD") on Deriv-Demo returns:
        #     volume_min=500.0  volume_max=90000.0  volume_step=100.0
        #     digits=4  point=0.0001  trade_contract_size=1.0
        #     trade_tick_value=0.0001  trade_tick_size=0.0001
        # The previous lot_min=50 / lot_step=10 were BOTH 10x too small, and
        # lot_max=1000 was 90x too small. That is not a rounding difference: a
        # backtest sizing this symbol at, say, 19.22 lots (as the debug runs in
        # `debug/` did) would be rejected outright by the broker for sitting
        # below volume_min. Every XRPUSD backtest result predating this line was
        # therefore simulating trades that could not have been placed.
        symbol="XRPUSD", instrument_type="CRYPTO", point_size=0.0001, point_value_per_lot=0.0001,
        lot_min=500.0, lot_max=90000.0, lot_step=100.0, contract_size=1, session_filter=False, news_filter=False, trades_24_7=True,
    ),
    "LTCUSD": InstrumentProfile(
        symbol="LTCUSD", instrument_type="CRYPTO", point_size=0.01, point_value_per_lot=0.01,
        lot_min=0.1, lot_max=1000.0, lot_step=0.1, contract_size=1, session_filter=False, news_filter=False, trades_24_7=True,
    ),
}

# ─────────────────────────────────────────────────────────────────────────────
# [Task 1.12/1.13 — Part 11 §C1 of the master plan] CROSS-CURRENCY CONVERSION
# ─────────────────────────────────────────────────────────────────────────────
# Every FX pair below has a non-USD quote currency, so its `point_value_per_lot`
# above needs a quote-currency-to-USD conversion baked in to be correct — see
# each profile's inline comment. As stored, that conversion is a STATIC
# point-in-time rate snapshot with no live refresh: correct the day it was
# written, drifting as the real rate moves. This map is what lets
# position_sizer.py replace the snapshot with a live MT5 quote when one is
# available (falling back to the snapshot above when it is not) — see
# resolve_cross_rate_point_value() in position_sizer.py.
#
# CORRECTED 2026-08-22: an earlier pass of this audit flagged these 13 profiles
# as "internally inconsistent" using ratio == contract_size as the test — that
# invariant only holds when quote currency == USD. Back-calculating the
# implied rate from each one (test_instrument_profiles.py) shows they decode to
# plausible real exchange rates (GBPJPY -> USDJPY ~149.25, USDCHF -> ~0.870,
# EURGBP -> GBPUSD ~1.27, etc.) — i.e. they were never arithmetically wrong,
# only stale. Do not "fix" these numbers directly; fix staleness via the live
# resolver instead.
#
# convention: "indirect" currencies (JPY, CHF, CAD) are quoted as USD/XXX
# (USDJPY = JPY per 1 USD) -> converting requires DIVIDING by the live rate.
# "direct" currencies (GBP, AUD, NZD, EUR as quote) are quoted as XXX/USD ->
# converting is a direct MULTIPLY.
FX_CROSS_CONVERSION: dict[str, tuple[str, str]] = {
    # symbol: (MT5 quote pair to fetch a live rate from, "direct" | "indirect")
    "USDJPY": ("USDJPY", "indirect"),
    "GBPJPY": ("USDJPY", "indirect"),
    "EURJPY": ("USDJPY", "indirect"),
    "AUDJPY": ("USDJPY", "indirect"),
    "CADJPY": ("USDJPY", "indirect"),
    "USDCHF": ("USDCHF", "indirect"),
    "GBPCHF": ("USDCHF", "indirect"),
    "USDCAD": ("USDCAD", "indirect"),
    "GBPCAD": ("USDCAD", "indirect"),
    "EURGBP": ("GBPUSD", "direct"),
    "EURAUD": ("AUDUSD", "direct"),
    "GBPAUD": ("AUDUSD", "direct"),
    "GBPNZD": ("NZDUSD", "direct"),
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

    # ── Deriv's long-form CFD names ───────────────────────────────────────
    # Read off the live Deriv-Demo terminal (2026-08-26) while building the
    # per-broker symbol map (task 14.9). Deriv writes indices out in full
    # rather than using the four-letter codes, so without these the discovery
    # pass reports the instrument as "not listed" even though it is offered —
    # which is exactly the GER40/GER30 case that motivated Part C.
    "Germany 40": "GER40",
    "Hong Kong 50": "HK50",
    "Swiss 20": "SWI20",
    "US Small Cap 2000": "US2000",
    "UK Brent Oil": "UKOIL",
    "Australia 200": "AUS200",
    "Europe 50": "EU50",
    "France 40": "FRA40",
    "Netherlands 25": "NETH25",
    "Spain 35": "SPA35",
    "UK 100": "UK100",
    "Japan 225": "JP225",
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

import os
import re
import threading

from backend.utils.logger import get_logger

logger = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────
# [C1/C3/1.15/1.16] Live broker overlay
# ─────────────────────────────────────────────────────────────────────────
#
# The tables above are hand-maintained constants, and on 2026-08-23 they were
# checked against the live terminal for the first time:
#
#     57 of 59 verifiable profiles disagreed with the broker.
#     15 had a materially wrong point_value_per_lot.
#
# The failures are not random, and two of them cannot be fixed by editing
# constants at all:
#
#   * **Frozen FX rates (audit C1).** AUDJPY/CADJPY/EURJPY/GBPJPY/USDJPY all
#     carry point_value_per_lot = 6.7 against a live 6.29 — a USDJPY rate from
#     whenever the table was written. USDCHF/GBPCHF carry 1.15 against 1.2477.
#     These are not typos; they are a quote frozen into a constant, and they go
#     stale again the moment the rate moves. No edit fixes that permanently.
#   * **Lot constraints (C3).** XRPUSD's profile said lot_min=50/step=10; the
#     broker says 500/100. A backtest sizing at 19 lots was simulating an order
#     the broker rejects outright.
#
# So the fix is architectural rather than another round of hand-editing: when
# MT5 is connected, the BROKER is the source of truth, and the static profile is
# the fallback for when it is not (CI, a Linux box, an unlisted symbol).
#
# Overridden from symbol_info: lot_min/lot_max/lot_step, point_size,
# contract_size, and point_value_per_lot (derived as
# `trade_tick_value x point_size / trade_tick_size` — the broker's own
# account-currency value per point, which is exactly what the frozen constants
# were trying and failing to approximate).
#
# NOT overridden: session_filter / news_filter / trades_24_7 / instrument_type.
# Those are policy, not broker facts, and stay yours.
#
# Set ALGOEDGE_DISABLE_LIVE_PROFILES=1 to pin the static tables — useful when
# reproducing an old backtest exactly.

_LIVE_OVERLAY_DISABLED = os.environ.get("ALGOEDGE_DISABLE_LIVE_PROFILES", "").strip() in ("1", "true", "TRUE")

# Cached per resolved symbol. A backtest must see ONE profile for its whole run:
# re-reading mid-run would let position size drift as the FX rate moves, which
# would make the run unreproducible. Call `refresh_live_profiles()` between runs.
_live_cache: dict[str, "InstrumentProfile | None"] = {}
_live_cache_lock = threading.Lock()
_overlay_logged: set[str] = set()


def refresh_live_profiles() -> None:
    """Drop the overlay cache so the next lookup re-reads the terminal."""
    with _live_cache_lock:
        _live_cache.clear()
        _overlay_logged.clear()


def _build_live_profile(resolved: str, base: "InstrumentProfile") -> "InstrumentProfile | None":
    """Overlay live symbol_info onto `base`. Returns None when unavailable."""
    try:
        import MetaTrader5 as mt5
    except ImportError:
        return None

    try:
        if not mt5.symbol_select(resolved, True):
            return None
        info = mt5.symbol_info(resolved)
    except Exception:
        return None

    if info is None or not info.point or not info.trade_tick_size:
        return None

    point_size = float(info.point)
    # Broker's account-currency value of one `point_size` move, per lot.
    pv = float(info.trade_tick_value) * (point_size / float(info.trade_tick_size))
    if pv <= 0:
        # A zero/negative tick value means the terminal has not populated this
        # symbol yet (it happens right after symbol_select on a cold terminal).
        # Falling back is correct; guessing is not.
        return None

    import dataclasses
    live = dataclasses.replace(
        base,
        point_size=point_size,
        point_value_per_lot=pv,
        lot_min=float(info.volume_min),
        lot_max=float(info.volume_max),
        lot_step=float(info.volume_step),
        contract_size=float(info.trade_contract_size) or base.contract_size,
    )

    # Log once per symbol, and only when it actually changed something — a
    # silent override of position sizing is exactly the kind of thing that
    # should be visible in the run log.
    if resolved not in _overlay_logged:
        _overlay_logged.add(resolved)
        deltas = []
        for field in ("point_size", "point_value_per_lot", "lot_min", "lot_max", "lot_step", "contract_size"):
            was, now = getattr(base, field), getattr(live, field)
            if was and abs(float(now) - float(was)) / max(abs(float(was)), 1e-12) > 1e-6:
                deltas.append(f"{field} {was} -> {now}")
        if deltas:
            logger.info(
                f"[PROFILE] {resolved}: using live broker values ({'; '.join(deltas)})"
            )
    return live


def get_instrument_profile(symbol: str) -> InstrumentProfile | None:
    """
    Look up an instrument profile, preferring live broker values.

    Resolution order:
      1. alias/suffix-normalise the symbol
      2. if MT5 is connected and knows it -> static profile overlaid with
         symbol_info (cached for the process; see refresh_live_profiles)
      3. otherwise the static profile unchanged
    """
    # Common suffixes added by brokers (.m, c, _i, #, .a)
    clean_symbol = re.sub(r'(\.m|c|_i|#|\.a|x)$', '', symbol, flags=re.IGNORECASE)
    clean_symbol = clean_symbol.replace('/', '')

    resolved = SYMBOL_ALIASES.get(clean_symbol, clean_symbol)
    profile = INSTRUMENT_PROFILES.get(resolved)
    if profile is None:
        # Fallback to the exact raw symbol
        resolved = SYMBOL_ALIASES.get(symbol, symbol)
        profile = INSTRUMENT_PROFILES.get(resolved)
    if profile is None:
        return None

    if _LIVE_OVERLAY_DISABLED:
        return profile

    with _live_cache_lock:
        if resolved in _live_cache:
            cached = _live_cache[resolved]
            return cached if cached is not None else profile

    live = _build_live_profile(resolved, profile)
    with _live_cache_lock:
        _live_cache[resolved] = live
    return live if live is not None else profile

