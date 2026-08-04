"""
backend/risk/position_sizer.py

Position sizing engine: Fixed % and Kelly Criterion.
Source: RiskManagement_Spec.md Section 5
"""

from backend.utils.logger import get_logger

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

logger = get_logger(__name__)


def get_pip_size(symbol: str) -> float:
    """Return pip size for the symbol (e.g. 0.0001 for EURUSD, 0.01 for XAUUSD)."""
    try:
        from backend.risk.compounding import get_instrument_profile
        profile = get_instrument_profile(symbol)
        if profile and profile.point_size:
            return profile.point_size
    except ImportError:
        pass

    gold_like = ["XAUUSD", "GOLD", "XAU"]
    silver_platinum = ["XAGUSD", "SILVER", "XAG", "XPTUSD", "PLATINUM", "XPT"]
    jpy_pairs = ["USDJPY", "EURJPY", "GBPJPY", "AUDJPY", "CADJPY"]
    indices = ["US30", "US500", "NAS100", "US2000", "UK100", "FRA40", "EU50",
               "NTH25", "SWI20", "AUS200", "JP225", "GER40", "HK50", "USTEC", "NDX", "SPX"]
    oil_gas = ["USOIL", "UKOIL", "WTI", "BRENT", "OIL", "XTIUSD", "XBRUSD", "NG", "NATGAS", "XNGUSD"]
    crypto = ["BTC", "ETH", "DOGE", "SOL", "XRP", "LTC"]
    synthetics = ["V10", "V25", "V50", "V75", "V100", "BOOM", "CRASH", "STEP", "VOLATILITY", "JUMP", "DEX"]

    symbol_upper = symbol.upper()

    if any(s in symbol_upper for s in synthetics):
        return 1.0  # Points for synthetics
    if any(s in symbol_upper for s in gold_like):
        return 0.01
    if any(s in symbol_upper for s in silver_platinum):
        return 0.001 if "AG" in symbol_upper or "SILVER" in symbol_upper else 0.01
    if any(s in symbol_upper for s in jpy_pairs):
        return 0.01 if any(p in symbol_upper for p in ("GBPJPY", "EURJPY", "AUDJPY", "CADJPY")) else 0.001
    if any(s in symbol_upper for s in oil_gas):
        return 0.01
    if any(s in symbol_upper for s in crypto):
        return 1.0  # BTC/ETH-scale; badly wrong for DOGE/XRP but at least in the right order vs 0.0001
    if any(s in symbol_upper for s in indices):
        return 1.0
    return 0.0001  # Standard forex


def get_symbol_info(symbol: str) -> dict:
    """Get lot constraints from MT5, InstrumentProfile, or use sensible defaults."""
    if mt5:
        info = mt5.symbol_info(symbol)
        if info:
            return {
                "volume_min": info.volume_min,
                "volume_max": info.volume_max,
                "volume_step": info.volume_step,
                "contract_size": info.trade_contract_size,
                "tick_value": info.trade_tick_value,
                "tick_size": info.trade_tick_size,
            }
    # Use InstrumentProfile for accurate per-instrument defaults
    try:
        from backend.risk.compounding import get_instrument_profile
        profile = get_instrument_profile(symbol)
        if profile:
            return {
                "volume_min": profile.lot_min,
                "volume_max": profile.lot_max,
                "volume_step": profile.lot_step,
                "contract_size": profile.contract_size,
                "tick_value": profile.point_value_per_lot,
                "tick_size": profile.point_size,
            }
    except ImportError:
        pass
    # Last resort: standard forex defaults
    return {
        "volume_min": 0.01,
        "volume_max": 100.0,
        "volume_step": 0.01,
        "contract_size": 100000,
        "tick_value": 1.0,  # Fallback assumption
        "tick_size": 0.00001,
    }


def calculate_lot_size(
    account_balance: float,
    risk_pct: float,
    entry_price: float,
    stop_loss_price: float,
    symbol: str,
) -> float:
    """
    Calculate lot size so that if SL is hit, loss = risk_pct% of balance.
    Formula: Lot = (Balance × Risk%) / (SL_distance_pips × pip_value_per_lot)
    Uses InstrumentProfile.point_value_per_lot for accurate cross-instrument sizing.
    Source: RiskManagement_Spec.md Section 5.1
    """
    risk_amount = account_balance * (risk_pct / 100)
    sl_distance = abs(entry_price - stop_loss_price)

    # Universal calculation: Lot = Risk / (SL_distance * (Tick_Value / Tick_Size))
    info = get_symbol_info(symbol)
    tick_value = info.get("tick_value", 1.0)
    tick_size = info.get("tick_size", 0.00001)

    if tick_size == 0 or tick_value == 0 or sl_distance == 0:
        return 0.0

    # MT5 tick_value is the profit in account currency for 1 tick move of 1 standard lot
    # Therefore, 1 unit of price move (e.g. 1.0) is worth tick_value / tick_size dollars.
    value_per_unit_move = tick_value / tick_size
    raw_lot = risk_amount / (sl_distance * value_per_unit_move)

    clamped = max(info["volume_min"], min(info["volume_max"], raw_lot))
    step = info["volume_step"]
    rounded = round(clamped / step) * step if step > 0 else clamped
    return round(rounded, 3)


def calculate_lot_from_dollars(
    risk_dollars: float,
    entry_price: float,
    stop_loss_price: float,
    symbol: str,
) -> float:
    """
    Convert a fixed dollar risk amount to lots (for compounding plan).
    Uses InstrumentProfile.point_value_per_lot for accurate sizing.
    Source: CompoundingPlan_Spec.md Section 2.5
    """
    sl_distance = abs(entry_price - stop_loss_price)

    # Universal calculation: Lot = Risk / (SL_distance * (Tick_Value / Tick_Size))
    info = get_symbol_info(symbol)
    tick_value = info.get("tick_value", 1.0)
    tick_size = info.get("tick_size", 0.00001)

    if tick_size == 0 or tick_value == 0 or sl_distance == 0:
        return 0.0

    value_per_unit_move = tick_value / tick_size
    raw_lot = risk_dollars / (sl_distance * value_per_unit_move)

    clamped = max(info["volume_min"], min(info["volume_max"], raw_lot))
    step = info["volume_step"]
    rounded = round(clamped / step) * step if step > 0 else clamped
    return round(rounded, 3)

def calculate_risk_dollars(lots: float, entry_price: float, stop_loss_price: float, symbol: str) -> float:
    """
    Calculates the actual risk in dollars for a given lot size.
    Used for the 15% Maximum Risk Circuit Breaker.
    """
    if lots == 0.0:
        return 0.0

    sl_distance = abs(entry_price - stop_loss_price)
    
    info = get_symbol_info(symbol)
    tick_value = info.get("tick_value", 1.0)
    tick_size = info.get("tick_size", 0.00001)
    
    if tick_size == 0:
        return 0.0
        
    value_per_unit_move = tick_value / tick_size
    return lots * sl_distance * value_per_unit_move

def kelly_lot_size(
    win_rate: float,
    avg_win_r: float,
    avg_loss_r: float,
    balance: float,
    max_kelly_fraction: float = 0.25,
) -> float:
    """
    Kelly Criterion lot sizing.
    f* = (W × B - L) / B
    Uses fractional Kelly (default 25% cap) to reduce volatility.
    Source: RiskManagement_Spec.md Section 5.2
    """
    loss_rate = 1 - win_rate
    b = avg_win_r / avg_loss_r if avg_loss_r > 0 else 1
    kelly = (win_rate * b - loss_rate) / b
    fraction = min(kelly, max_kelly_fraction)
    return balance * max(fraction, 0)

def get_confluence_scaled_risk(base_risk_pct: float, confluence_score: int) -> float:
    """
    Scales the base risk percentage down if the confluence score is lower than optimal.
    
    Tiers (configurable):
    - Score 80-100: Deploy 100% of base_risk_pct
    - Score 65-79:  Deploy 75% of base_risk_pct
    - Score 55-64:  Deploy 50% of base_risk_pct
    - Score <55:    Rejected (0%)
    """
    if confluence_score >= 80:
        return base_risk_pct
    elif confluence_score >= 65:
        return base_risk_pct * 0.75
    elif confluence_score >= 55:
        return base_risk_pct * 0.50
    else:
        return 0.0