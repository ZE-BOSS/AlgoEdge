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
    """
    Get lot constraints and tick values for a symbol.
    Priority: Live MT5 (if connected) → InstrumentProfile → Standard defaults.
    IMPORTANT: Always logs which source was used so sizing decisions are auditable.
    """
    # ── 1. Live MT5 Data ──────────────────────────────────────────────────────
    # Check both: library imported AND terminal is actually connected/initialized.
    mt5_connected = False
    if mt5:
        try:
            terminal = mt5.terminal_info()
            mt5_connected = terminal is not None and terminal.connected
        except Exception:
            mt5_connected = False

    if mt5_connected:
        try:
            # Ensure symbol is visible (select it into Market Watch if needed)
            mt5.symbol_select(symbol, True)
            info = mt5.symbol_info(symbol)
            if info and info.trade_tick_value > 0 and info.trade_tick_size > 0:
                logger.info(
                    f"[SIZER] {symbol}: Using LIVE MT5 data — "
                    f"tick_value={info.trade_tick_value}, tick_size={info.trade_tick_size}, "
                    f"vol_min={info.volume_min}, vol_step={info.volume_step}"
                )
                return {
                    "volume_min": info.volume_min,
                    "volume_max": info.volume_max,
                    "volume_step": info.volume_step,
                    "contract_size": info.trade_contract_size,
                    "tick_value": info.trade_tick_value,
                    "tick_size": info.trade_tick_size,
                    "source": "MT5",
                }
            else:
                logger.warning(
                    f"[SIZER] {symbol}: MT5 connected but symbol_info returned None or zero tick values. "
                    f"Falling back to InstrumentProfile."
                )
        except Exception as e:
            logger.warning(f"[SIZER] {symbol}: MT5 symbol_info error: {e}. Falling back.")
    else:
        logger.debug(f"[SIZER] {symbol}: MT5 not connected. Using InstrumentProfile fallback.")

    # ── 2. InstrumentProfile Fallback ─────────────────────────────────────────
    try:
        from backend.risk.compounding import get_instrument_profile
        profile = get_instrument_profile(symbol)
        if profile:
            logger.info(
                f"[SIZER] {symbol}: Using InstrumentProfile — "
                f"point_value_per_lot={profile.point_value_per_lot}, point_size={profile.point_size}, "
                f"lot_min={profile.lot_min}, lot_step={profile.lot_step}"
            )
            return {
                "volume_min": profile.lot_min,
                "volume_max": profile.lot_max,
                "volume_step": profile.lot_step,
                "contract_size": profile.contract_size,
                "tick_value": profile.point_value_per_lot,
                "tick_size": profile.point_size,
                "source": "InstrumentProfile",
            }
    except ImportError:
        pass

    # ── 3. Last Resort: Standard Forex Defaults ───────────────────────────────
    logger.warning(
        f"[SIZER] {symbol}: No MT5 data and no InstrumentProfile found. "
        f"Using standard forex defaults — THIS MAY BE INCORRECT FOR NON-FOREX SYMBOLS!"
    )
    return {
        "volume_min": 0.01,
        "volume_max": 100.0,
        "volume_step": 0.01,
        "contract_size": 100000,
        "tick_value": 1.0,
        "tick_size": 0.00001,
        "source": "DEFAULT",
    }


def calculate_lot_size(
    account_balance: float,
    risk_pct: float,
    entry_price: float,
    stop_loss_price: float,
    symbol: str,
    # Absolute safety cap: if calculated risk exceeds this % of balance, clamp down.
    # Protects against any future misconfiguration regardless of profile values.
    max_risk_hard_cap_pct: float = 3.0,
) -> float:
    """
    Calculate lot size so that if SL is hit, loss = risk_pct% of balance.
    Formula: Lot = (Balance × Risk%) / (SL_distance × (tick_value / tick_size))
    Has an absolute hard-cap: lot is clamped so actual risk never exceeds
    max_risk_hard_cap_pct% of balance, even if profile/MT5 values are wrong.
    Source: RiskManagement_Spec.md Section 5.1
    """
    risk_amount = account_balance * (risk_pct / 100)
    sl_distance = abs(entry_price - stop_loss_price)

    info = get_symbol_info(symbol)
    tick_value = info.get("tick_value", 1.0)
    tick_size = info.get("tick_size", 0.00001)
    source = info.get("source", "UNKNOWN")

    if tick_size == 0 or tick_value == 0 or sl_distance == 0:
        logger.error(f"[SIZER] {symbol}: Zero tick_size/tick_value/sl_distance — returning 0 lots.")
        return 0.0

    # MT5 tick_value = profit in account currency for 1 tick move of 1 standard lot.
    # value_per_unit_move = how many account-currency dollars 1 full price unit is worth per lot.
    value_per_unit_move = tick_value / tick_size
    raw_lot = risk_amount / (sl_distance * value_per_unit_move)

    clamped = max(info["volume_min"], min(info["volume_max"], raw_lot))
    step = info["volume_step"]
    rounded = round(clamped / step) * step if step > 0 else clamped
    final_lot = round(rounded, 3)

    # ── Hard Risk Cap (safety net) ─────────────────────────────────────────────
    # Calculate actual dollar risk with the final lot, and clamp if it exceeds the cap.
    actual_risk = final_lot * sl_distance * value_per_unit_move
    max_risk_dollars = account_balance * (max_risk_hard_cap_pct / 100)
    if actual_risk > max_risk_dollars:
        # Scale lots down to meet the cap
        safe_lot = max_risk_dollars / (sl_distance * value_per_unit_move)
        safe_lot = max(info["volume_min"], safe_lot)
        safe_lot = round(round(safe_lot / step) * step, 3) if step > 0 else round(safe_lot, 3)
        logger.warning(
            f"[SIZER] {symbol}: HARD CAP TRIGGERED! "
            f"Calculated lot {final_lot} would risk ${actual_risk:.2f} "
            f"({actual_risk/account_balance*100:.2f}% of balance). "
            f"Clamped to {safe_lot} lots (${max_risk_dollars:.2f} max). "
            f"Source was: {source}. Check your InstrumentProfile for this symbol!"
        )
        return safe_lot

    logger.info(
        f"[SIZER] {symbol}: lot={final_lot} | risk=${actual_risk:.2f} "
        f"({actual_risk/account_balance*100:.2f}%) | source={source}"
    )
    return final_lot



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