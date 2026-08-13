"""
backend/risk/position_sizer.py

Position sizing engine: Fixed % and Kelly Criterion.
Source: RiskManagement_Spec.md Section 5
"""

import time
from backend.utils.logger import get_logger

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

logger = get_logger(__name__)

_MT5_SYMBOL_CACHE = {}

def update_mt5_cache(symbols: list[str]):
    """
    Fetch and cache MT5 symbol info (tick_value, tick_size, lot limits) for the given symbols.
    This ensures that if MT5 disconnects mid-session, we still have accurate live data
    to size positions and calculate PnL properly, preventing extreme over-sizing.
    """
    global _MT5_SYMBOL_CACHE
    if not mt5:
        return
        
    try:
        terminal = mt5.terminal_info()
        if not (terminal and terminal.connected):
            logger.warning("[SIZER] MT5 not connected, cannot update symbol cache.")
            return
    except Exception:
        return
        
    updated = 0
    for symbol in symbols:
        try:
            mt5.symbol_select(symbol, True)
            info = mt5.symbol_info(symbol)
            if info and info.trade_tick_value > 0 and info.trade_tick_size > 0:
                _MT5_SYMBOL_CACHE[symbol] = {
                    "volume_min": info.volume_min,
                    "volume_max": info.volume_max,
                    "volume_step": info.volume_step,
                    "contract_size": info.trade_contract_size,
                    "tick_value": info.trade_tick_value,
                    "tick_size": info.trade_tick_size,
                    "source": "MT5_CACHE",
                }
                updated += 1
        except Exception as e:
            logger.debug(f"[SIZER] Error caching {symbol}: {e}")
            
    if updated > 0:
        logger.info(f"[SIZER] MT5 symbol cache updated for {updated} symbols.")

_pip_size_cache: dict[str, tuple[float, float]] = {}  # {symbol: (timestamp, size)}

def get_pip_size(symbol: str) -> float:
    """Return pip size for the symbol (e.g. 0.0001 for EURUSD, 0.01 for JPY pairs, 0.1 for XAUUSD)."""
    now = time.time()
    if symbol in _pip_size_cache:
        cached_time, size = _pip_size_cache[symbol]
        if now - cached_time < 60:
            return size

    symbol_upper = symbol.upper()
    size = None
    try:
        from backend.risk.compounding import get_instrument_profile
        profile = get_instrument_profile(symbol)
        if profile and profile.point_size:
            if profile.instrument_type == "FOREX":
                size = profile.point_size * 10.0
            elif profile.instrument_type == "COMMODITY" and "XAU" in symbol_upper:
                size = profile.point_size * 10.0  # Gold standard pip is 10 points
            else:
                size = profile.point_size
    except ImportError:
        pass

    gold_like = ["XAUUSD", "GOLD", "XAU"]
    silver_platinum = ["XAGUSD", "SILVER", "XAG", "XPTUSD", "PLATINUM", "XPT"]
    jpy_pairs = ["USDJPY", "EURJPY", "GBPJPY", "AUDJPY", "CADJPY", "CHFJPY", "NZDJPY"]
    indices = ["US30", "US500", "NAS100", "US2000", "UK100", "FRA40", "EU50",
               "NTH25", "SWI20", "AUS200", "JP225", "GER40", "HK50", "USTEC", "NDX", "SPX"]
    oil_gas = ["USOIL", "UKOIL", "WTI", "BRENT", "OIL", "XTIUSD", "XBRUSD", "NG", "NATGAS", "XNGUSD"]
    crypto = ["BTC", "ETH", "DOGE", "SOL", "XRP", "LTC"]
    synthetics = ["V10", "V25", "V50", "V75", "V100", "BOOM", "CRASH", "STEP", "VOLATILITY", "JUMP", "DEX"]

    if size is None:
        if any(s in symbol_upper for s in synthetics):
            size = 0.01
        elif any(s in symbol_upper for s in gold_like):
            size = 0.1  # Pip is 0.1 for Gold
        elif any(s in symbol_upper for s in silver_platinum):
            size = 0.01
        elif any(s in symbol_upper for s in jpy_pairs):
            size = 0.01  # JPY pip is 0.01
        elif any(s in symbol_upper for s in oil_gas):
            size = 0.01
        elif any(s in symbol_upper for s in crypto):
            size = 1.0 
        elif any(s in symbol_upper for s in indices):
            size = 1.0
        else:
            size = 0.0001  # Standard forex
            
    _pip_size_cache[symbol] = (now, size)
    return size

_symbol_info_cache: dict[tuple[str, bool], tuple[float, dict]] = {}  # {(symbol, use_live_mt5): (timestamp, info)}

def get_symbol_info(symbol: str, use_live_mt5: bool = True) -> dict:
    """
    Get lot constraints and tick values for a symbol.
    Priority: Live MT5 (if connected AND use_live_mt5=True) → InstrumentProfile → Standard defaults.

    Both live trading and backtesting use this same chain (use_live_mt5=True by default),
    so sizing and _calc_pnl always use the same data source — preventing lot-size/PnL drift.
    IMPORTANT: Always logs which source was used so sizing decisions are auditable.
    """
    now = time.time()
    cache_key = (symbol, use_live_mt5)
    if cache_key in _symbol_info_cache:
        cached_time, info = _symbol_info_cache[cache_key]
        if now - cached_time < 60:
            return info

    # ── 1. Live MT5 Data ──────────────────────────────────────────────────────
    mt5_connected = False
    if use_live_mt5 and mt5:
        try:
            terminal = mt5.terminal_info()
            mt5_connected = terminal is not None and terminal.connected
        except Exception:
            mt5_connected = False

    if mt5_connected:
        try:
            mt5.symbol_select(symbol, True)
            info = mt5.symbol_info(symbol)
            if info and info.trade_tick_value > 0 and info.trade_tick_size > 0:
                res = {
                    "volume_min": info.volume_min,
                    "volume_max": info.volume_max,
                    "volume_step": info.volume_step,
                    "contract_size": info.trade_contract_size,
                    "tick_value": info.trade_tick_value,
                    "tick_size": info.trade_tick_size,
                    "source": "MT5",
                }
                _symbol_info_cache[cache_key] = (now, res)
                return res
        except Exception as e:
            logger.warning(f"[SIZER] {symbol}: MT5 symbol_info error: {e}. Falling back.")

    # ── 1.5. Cached MT5 Data ──────────────────────────────────────────────────
    if symbol in _MT5_SYMBOL_CACHE:
        _symbol_info_cache[cache_key] = (now, _MT5_SYMBOL_CACHE[symbol])
        return _MT5_SYMBOL_CACHE[symbol]

    # ── 2. InstrumentProfile Fallback ─────────────────────────────────────────
    try:
        from backend.risk.compounding import get_instrument_profile
        profile = get_instrument_profile(symbol)
        if profile:
            res = {
                "volume_min": profile.lot_min,
                "volume_max": profile.lot_max,
                "volume_step": profile.lot_step,
                "contract_size": profile.contract_size,
                "tick_value": profile.point_value_per_lot,
                "tick_size": profile.point_size,
                "source": "InstrumentProfile",
            }
            _symbol_info_cache[cache_key] = (now, res)
            return res
    except ImportError:
        pass

    # ── 3. Last Resort: Standard Forex Defaults ───────────────────────────────
    res = {
        "volume_min": 0.01,
        "volume_max": 100.0,
        "volume_step": 0.01,
        "contract_size": 100000,
        "tick_value": 1.0,
        "tick_size": 0.00001,
        "source": "DEFAULT",
    }
    _symbol_info_cache[cache_key] = (now, res)
    return res


def calculate_lot_size(
    account_balance: float,
    risk_pct: float,
    entry_price: float,
    stop_loss_price: float,
    symbol: str,
    # Absolute safety cap: if calculated risk exceeds this % of balance, clamp down.
    # Protects against any future misconfiguration regardless of profile values.
    max_risk_hard_cap_pct: float = 3.0,
    use_live_mt5: bool = True,
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

    info = get_symbol_info(symbol, use_live_mt5=use_live_mt5)
    tick_value = info.get("tick_value", 1.0)
    tick_size = info.get("tick_size", 0.00001)
    source = info.get("source", "UNKNOWN")

    if tick_size == 0 or tick_value == 0 or sl_distance == 0:
        logger.error(f"[SIZER] {symbol}: Zero tick_size/tick_value/sl_distance — returning 0 lots.")
        return 0.0

    # CROSS-RATE FX SAFETY GUARD
    # If source is DEFAULT, it means we have no MT5 data, no cached data, and no InstrumentProfile.
    # The default vpum is 100,000 (1.0 / 0.00001). If this is a cross-rate pair on a synthetic
    # or obscure broker where vpum is actually ~70,000 to ~120,000, using 100,000 might be
    # safe or might be very risky depending on the pair.
    # We enforce a strict refusal to trade cross-rate pairs or indices if they hit DEFAULT.
    _SAFE_DEFAULTS = {"EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD"}
    if source == "DEFAULT":
        if symbol.upper() not in _SAFE_DEFAULTS:
            logger.error(f"[SIZER] {symbol}: No MT5 data and no profile. Refusing to size. source=DEFAULT")
            return 0.0
        logger.warning(f"[SIZER] {symbol}: Sizing using DEFAULT fallback! Risk calculations may be inaccurate.")


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




def calculate_risk_dollars(
    lots: float,
    entry_price: float,
    stop_loss_price: float,
    symbol: str,
    use_live_mt5: bool = True,
) -> float:
    """
    Calculates the actual risk in dollars for a given lot size.
    Used for the 15% Maximum Risk Circuit Breaker and MultiTP cap enforcement.
    """
    if lots == 0.0:
        return 0.0

    sl_distance = abs(entry_price - stop_loss_price)

    info = get_symbol_info(symbol, use_live_mt5=use_live_mt5)
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