"""
backend/risk/position_sizer.py

Position sizing engine: Fixed % and Kelly Criterion.
Source: RiskManagement_Spec.md Section 5
"""

from typing import Optional
from backend.utils.logger import get_logger

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

logger = get_logger(__name__)


def get_pip_size(symbol: str) -> float:
    """Return pip size for the symbol (e.g. 0.0001 for EURUSD, 0.01 for XAUUSD)."""
    gold_like = ["XAUUSD", "GOLD"]
    jpy_pairs = ["USDJPY", "EURJPY", "GBPJPY"]
    indices = ["US30", "US500", "NAS100"]
    synthetics = ["V10", "V25", "V50", "V75", "V100", "Boom", "Crash", "Step"]

    symbol_upper = symbol.upper()

    if any(s in symbol_upper for s in synthetics):
        return 0.01  # Points for synthetics
    if any(s in symbol_upper for s in gold_like):
        return 0.01
    if any(s in symbol_upper for s in jpy_pairs):
        return 0.01
    if any(s in symbol_upper for s in indices):
        return 1.0
    return 0.0001  # Standard forex


def get_symbol_info(symbol: str) -> dict:
    """Get lot constraints from MT5 or use sensible defaults."""
    if mt5:
        info = mt5.symbol_info(symbol)
        if info:
            return {
                "volume_min": info.volume_min,
                "volume_max": info.volume_max,
                "volume_step": info.volume_step,
                "contract_size": info.trade_contract_size,
            }
    # Sensible defaults
    return {
        "volume_min": 0.01,
        "volume_max": 100.0,
        "volume_step": 0.01,
        "contract_size": 100000,
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
    Source: RiskManagement_Spec.md Section 5.1
    """
    risk_amount = account_balance * (risk_pct / 100)
    sl_distance = abs(entry_price - stop_loss_price)
    pip_size = get_pip_size(symbol)
    sl_pips = sl_distance / pip_size if pip_size > 0 else 0

    info = get_symbol_info(symbol)
    pip_value = pip_size * info["contract_size"]

    if sl_pips == 0 or pip_value == 0:
        return 0.0

    raw_lot = risk_amount / (sl_pips * pip_value)

    # Clamp and round to broker constraints
    clamped = max(info["volume_min"], min(info["volume_max"], raw_lot))
    step = info["volume_step"]
    rounded = round(clamped / step) * step if step > 0 else clamped
    return round(rounded, 2)


def calculate_lot_from_dollars(
    risk_dollars: float,
    entry_price: float,
    stop_loss_price: float,
    symbol: str,
) -> float:
    """
    Convert a fixed dollar risk amount to lots (for compounding plan).
    Source: CompoundingPlan_Spec.md Section 2.5
    """
    sl_distance = abs(entry_price - stop_loss_price)
    pip_size = get_pip_size(symbol)
    sl_pips = sl_distance / pip_size if pip_size > 0 else 0

    info = get_symbol_info(symbol)
    pip_value = pip_size * info["contract_size"]

    if sl_pips == 0 or pip_value == 0:
        return 0.0

    raw_lot = risk_dollars / (sl_pips * pip_value)

    clamped = max(info["volume_min"], min(info["volume_max"], raw_lot))
    step = info["volume_step"]
    rounded = round(clamped / step) * step if step > 0 else clamped
    return round(rounded, 2)


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
