"""
backend/risk/position_sizer.py

Position sizing engine: Fixed % and Kelly Criterion.
Source: RiskManagement_Spec.md Section 5
"""

import contextvars
import math
import time
from backend.utils.logger import get_logger

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

logger = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# EXECUTION-VIABILITY CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

# A stop must clear the round-trip cost of crossing the spread by this multiple
# before it is worth taking. At 1.0x the trade is stopped out by the spread
# alone; 2.0x is the smallest multiple that leaves the position any room to
# breathe. Raise it to be stricter about scalp-width stops.
# (Forensic case: APA emitted 0.45-pip stops on USDCHF, whose spread alone is
# 1.5-2.5 pips. Those stops are physically untradeable and, because lot size is
# inversely proportional to stop distance, they produced enormous lots.)
MIN_STOP_SPREAD_MULTIPLE = 2.0

# Absolute per-asset-class stop floor in PIPS, independent of ATR and of the
# current spread. This is the backstop for the case where the spread reading is
# itself wrong/zero (stale quote, no MT5). Deliberately small — it should only
# ever catch pathological sub-pip stops, not shape normal strategy behaviour.
MIN_STOP_FLOOR_PIPS: dict[str, float] = {
    "FOREX": 2.0,       # ~2x a major's typical 1.0-1.2 pip spread
    "INDEX": 3.0,       # index pips are coarse; spread x2 normally dominates
    # Crypto pip sizes span five orders of magnitude of price (BTC pip = $1,
    # DOGE pip = $0.0001), so a large flat floor would be meaningless on one and
    # punitive on the other. Kept small: the spread x2 term (which IS price-
    # scaled, see broker_costs.CRYPTO_SPREAD_PCT_OF_PRICE) is the real driver.
    "CRYPTO": 5.0,
    "COMMODITY": 5.0,   # gold pip = 0.10 → a $0.50/oz minimum stop
    "SYNTHETIC": 5.0,   # Deriv feeds are coarse relative to their volatility
}
_MIN_STOP_FLOOR_DEFAULT_PIPS = 2.0

# Ceiling on required margin as a percentage of account equity for a SINGLE
# position. MT5 rejects an order it cannot margin with retcode 10019 (No money),
# so a lot size above this is not "aggressive", it is un-fillable.
# (Forensic case: APA sized up to 40 lots on a $25,000 account = $4,000,000
# notional = 160x account leverage — rejected live, but filled in backtest,
# which is exactly the backtest/live divergence this guard closes.)
# 30% leaves headroom for the other concurrent positions the portfolio may hold.
MAX_MARGIN_UTILISATION_PCT = 30.0

# Account leverage assumed when MT5 cannot tell us the real figure (backtests,
# terminal offline). 1:100 is the common retail/prop default; if the real
# account is lower-leverage the estimate is optimistic, which is why the live
# path always prefers mt5.order_calc_margin().
FALLBACK_ACCOUNT_LEVERAGE = 100.0

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
                    # Execution-constraint fields (see get_symbol_info docstring).
                    # Cached alongside the sizing fields so a mid-session MT5
                    # disconnect doesn't strip the min-stop-distance guard.
                    "stops_level_points": int(getattr(info, "trade_stops_level", 0) or 0),
                    "digits": int(getattr(info, "digits", 5) or 5),
                    "point": float(getattr(info, "point", 0.0) or 0.0) or float(info.trade_tick_size),
                    "spread_points": float(getattr(info, "spread", 0.0) or 0.0),
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
    # FIX (2.5): GBPJPY/EURJPY/AUDJPY/CADJPY's InstrumentProfile already defines
    # point_size=0.01 as a full pip (unlike the pipette convention, e.g. 0.00001,
    # used for the rest of the FOREX table — see USDJPY's point_size=0.001, which
    # IS pipette-scale and correctly becomes 0.01 pip after the *10.0 below). Applying
    # the blanket *10.0 to these 4 already-pip-scale profiles produced a 10x-too-large
    # pip size (0.1 instead of 0.01), corrupting breakeven/trailing pip-based distances.
    _JPY_PIP_ALREADY_FULL_PIP = {"GBPJPY", "EURJPY", "AUDJPY", "CADJPY"}
    try:
        from backend.risk.compounding import get_instrument_profile
        profile = get_instrument_profile(symbol)
        if profile and profile.point_size:
            if profile.instrument_type == "FOREX":
                if symbol_upper in _JPY_PIP_ALREADY_FULL_PIP:
                    size = profile.point_size  # already a full pip, do not re-scale
                else:
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

# When set, symbol info is PINNED for the lifetime of a run: the first resolution
# per symbol is reused for every subsequent call, so sizing at entry and P&L at exit
# can never disagree, and a run is reproducible regardless of wall-clock timing or
# MT5 connectivity. See get_symbol_info() for the failure this prevents.
#
# This is a ContextVar, NOT a module global, deliberately: backtests run in the same
# process as the live bot, and a global freeze would leak into live trading — which
# must always see fresh broker data. A ContextVar confines the pin to the async task
# (or thread) running the backtest. Unset = normal live behaviour with the 60s TTL.
_frozen_symbol_info_var: contextvars.ContextVar[dict[tuple[str, bool], dict] | None] = (
    contextvars.ContextVar("frozen_symbol_info", default=None)
)


def freeze_symbol_info() -> None:
    """Pin symbol info for the duration of a backtest run (idempotent, task-scoped)."""
    if _frozen_symbol_info_var.get() is None:
        _frozen_symbol_info_var.set({})
        logger.info("[SIZER] Symbol info FROZEN for this run — sizing and PnL will use identical values.")


def unfreeze_symbol_info() -> None:
    """Release the pin and return to normal live TTL behaviour."""
    _frozen_symbol_info_var.set(None)


def _infer_digits(point_size: float) -> int:
    """
    Infer a symbol's price digits from its point/tick size (0.00001 → 5,
    0.01 → 2, 1.0 → 0). Used by the non-MT5 branches of get_symbol_info, where
    the broker's real `digits` is unavailable.
    """
    try:
        if point_size and point_size > 0:
            return max(0, int(round(-math.log10(point_size))))
    except (ValueError, OverflowError):
        pass
    return 5


def get_symbol_info(symbol: str, use_live_mt5: bool = True) -> dict:
    """
    Get lot constraints, tick values and execution constraints for a symbol.
    Priority: Live MT5 (if connected AND use_live_mt5=True) → InstrumentProfile → Standard defaults.

    Both live trading and backtesting use this same chain (use_live_mt5=True by default),
    so sizing and _calc_pnl always use the same data source — preventing lot-size/PnL drift.
    IMPORTANT: Always logs which source was used so sizing decisions are auditable.

    Every branch returns the same key set, so downstream code can rely on all of
    these unconditionally:
        volume_min / volume_max / volume_step / contract_size
        tick_value / tick_size
        stops_level_points  (int, broker minimum stop distance in POINTS; 0 = unknown)
        digits              (int, price digits)
        point               (float, one point of price)
        spread_points       (float, current spread in POINTS; 0 = unknown)
        source
    The last four are only truly known when MT5 is reachable; the fallback
    branches derive `point`/`digits` from the profile's point_size and report
    0 for the two broker-side fields (meaning "unknown", not "no restriction").
    """
    now = time.time()
    cache_key = (symbol, use_live_mt5)

    # ── Frozen mode (backtests) ───────────────────────────────────────────────
    # When frozen, the first resolution for a symbol is pinned for the whole run
    # and never re-resolved.
    #
    # Why this exists: the cache below expires on a 60-SECOND WALL-CLOCK TTL, but a
    # backtest processes tens of thousands of bars over several minutes. The entry
    # of a trade and its exit therefore routinely landed on opposite sides of a
    # cache expiry, and if MT5 connectivity flickered in between, sizing resolved
    # against one source (e.g. live MT5) while _calc_pnl resolved against another
    # (InstrumentProfile, or the DEFAULT fallback at tick_value/tick_size =
    # 1.0/0.00001). Position size and P&L were then computed with DIFFERENT
    # instrument values.
    #
    # Observed on XRPUSD: within a single run, implied value-per-unit-move varied
    # between 135 and 21,672 — a 160x spread on the same symbol — producing single
    # trades that lost 190% of the account. It also made backtests NON-DETERMINISTIC:
    # the same run could return different results depending on wall-clock timing and
    # MT5 connection state, which disqualifies them for research use.
    _frozen = _frozen_symbol_info_var.get()
    if _frozen is not None and cache_key in _frozen:
        return _frozen[cache_key]

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
                    "stops_level_points": int(getattr(info, "trade_stops_level", 0) or 0),
                    "digits": int(getattr(info, "digits", 5) or 5),
                    "point": float(getattr(info, "point", 0.0) or 0.0) or float(info.trade_tick_size),
                    "spread_points": float(getattr(info, "spread", 0.0) or 0.0),
                    "source": "MT5",
                }
                _symbol_info_cache[cache_key] = (now, res)
                if _frozen is not None:
                    _frozen[cache_key] = res
                return res
        except Exception as e:
            logger.warning(f"[SIZER] {symbol}: MT5 symbol_info error: {e}. Falling back.")

    # ── 1.5. Cached MT5 Data ──────────────────────────────────────────────────
    if symbol in _MT5_SYMBOL_CACHE:
        cached_res = dict(_MT5_SYMBOL_CACHE[symbol])
        # Backfill the execution-constraint keys for any cache entry written
        # before they existed, so the contract holds on every branch.
        cached_res.setdefault("stops_level_points", 0)
        cached_res.setdefault("point", float(cached_res.get("tick_size", 0.00001)))
        cached_res.setdefault("digits", _infer_digits(cached_res.get("point", 0.00001)))
        cached_res.setdefault("spread_points", 0.0)
        _symbol_info_cache[cache_key] = (now, cached_res)
        if _frozen is not None:
            _frozen[cache_key] = cached_res
        return cached_res

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
                # No broker to ask: `point` is the profile's point_size and
                # digits follows from it. stops_level/spread are unknown (0) —
                # callers substitute a modelled figure (see broker_costs.py).
                "stops_level_points": 0,
                "digits": _infer_digits(profile.point_size),
                "point": float(profile.point_size),
                "spread_points": 0.0,
                "source": "InstrumentProfile",
            }
            _symbol_info_cache[cache_key] = (now, res)
            if _frozen is not None:
                _frozen[cache_key] = res
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
        "stops_level_points": 0,
        "digits": 5,
        "point": 0.00001,
        "spread_points": 0.0,
        "source": "DEFAULT",
    }
    _symbol_info_cache[cache_key] = (now, res)
    if _frozen is not None:
        _frozen[cache_key] = res
    return res


def _instrument_type_of(symbol: str) -> str:
    """InstrumentProfile.instrument_type for the symbol, defaulting to FOREX."""
    try:
        from backend.risk.compounding import get_instrument_profile
        profile = get_instrument_profile(symbol)
        if profile is not None:
            return profile.instrument_type
    except Exception:
        pass
    return "FOREX"


def minimum_stop_distance(symbol: str, info: dict | None = None, use_live_mt5: bool = True) -> tuple[float, str]:
    """
    Smallest stop distance (in PRICE, not pips) that is physically tradeable for
    this symbol.

        min = max(broker stops_level, spread x MIN_STOP_SPREAD_MULTIPLE, asset-class floor)

    Returns (distance_in_price, human_readable_reason). Never raises; on total
    failure it returns (0.0, "unknown") so sizing is not blocked by a cost-lookup
    problem.
    """
    try:
        if info is None:
            info = get_symbol_info(symbol, use_live_mt5=use_live_mt5)
        pip_size = get_pip_size(symbol)
        if not pip_size or pip_size <= 0:
            return 0.0, "unknown (no pip size)"

        # 1. Broker's own minimum stop distance, straight from symbol_info.
        point = float(info.get("point", 0.0) or 0.0)
        stops_points = float(info.get("stops_level_points", 0) or 0)
        broker_price = stops_points * point if (point > 0 and stops_points > 0) else 0.0

        # 2. Spread-based floor and 3. asset-class floor, both via broker_costs
        #    (imported lazily — broker_costs imports get_pip_size from this
        #    module, so a module-level import here would be circular).
        spread_pips = 0.0
        costs_stops_pips = 0.0
        try:
            from backend.risk.broker_costs import get_broker_costs
            costs = get_broker_costs(symbol, use_live_mt5=use_live_mt5)
            spread_pips = float(costs.get("spread_pips", 0.0) or 0.0)
            costs_stops_pips = float(costs.get("stops_level_pips", 0.0) or 0.0)
        except Exception as e:
            logger.debug(f"[SIZER] {symbol}: broker cost lookup failed in min-stop check: {e}")
            # Fall back to whatever the raw symbol info knows about the spread.
            if point > 0:
                spread_pips = (float(info.get("spread_points", 0.0) or 0.0) * point) / pip_size

        floor_pips = MIN_STOP_FLOOR_PIPS.get(_instrument_type_of(symbol), _MIN_STOP_FLOOR_DEFAULT_PIPS)

        spread_price = spread_pips * MIN_STOP_SPREAD_MULTIPLE * pip_size
        floor_price = floor_pips * pip_size
        costs_stops_price = costs_stops_pips * pip_size

        candidates = {
            "broker_stops_level": max(broker_price, costs_stops_price),
            f"spread x{MIN_STOP_SPREAD_MULTIPLE:g}": spread_price,
            "asset_class_floor": floor_price,
        }
        reason, distance = max(candidates.items(), key=lambda kv: kv[1])
        return float(distance), f"{reason} ({distance / pip_size:.2f} pips)"
    except Exception as e:
        logger.debug(f"[SIZER] {symbol}: minimum_stop_distance failed: {e}")
        return 0.0, "unknown"


def _margin_capped_lots(
    symbol: str,
    lots: float,
    entry_price: float,
    account_balance: float,
    info: dict,
    use_live_mt5: bool = True,
) -> float:
    """
    Clamp `lots` so the position's required margin stays within
    MAX_MARGIN_UTILISATION_PCT of account equity.

    Prefers mt5.order_calc_margin() (the broker's own answer, including its
    per-symbol margin rate and any tiered-margin rules). Without MT5 it estimates
        notional = lots x contract_size x entry_price
        margin   = notional / leverage
    which is approximate — currency conversion of a non-USD-quoted notional is
    not attempted — but is enough to stop the pathological 100x-account-notional
    sizes that MT5 rejects live with retcode 10019 (No money).

    Never raises; returns `lots` unchanged if it cannot form an opinion.
    """
    try:
        if lots <= 0 or account_balance <= 0 or entry_price <= 0:
            return lots

        max_margin = account_balance * (MAX_MARGIN_UTILISATION_PCT / 100.0)
        required = 0.0
        basis = ""

        # ── Preferred: ask the broker ─────────────────────────────────────────
        if use_live_mt5 and mt5 is not None:
            try:
                terminal = mt5.terminal_info()
                if terminal is not None and terminal.connected:
                    calc = mt5.order_calc_margin(mt5.ORDER_TYPE_BUY, symbol, lots, entry_price)
                    if calc is not None and float(calc) > 0:
                        required = float(calc)
                        basis = "mt5.order_calc_margin"
            except Exception as e:
                logger.debug(f"[SIZER] {symbol}: order_calc_margin failed: {e}")

        # ── Fallback: notional / leverage estimate ────────────────────────────
        if required <= 0:
            contract_size = float(info.get("contract_size", 0.0) or 0.0)
            if contract_size <= 0:
                return lots
            leverage = FALLBACK_ACCOUNT_LEVERAGE
            if mt5 is not None:
                try:
                    acct = mt5.account_info()
                    if acct is not None and getattr(acct, "leverage", 0):
                        leverage = float(acct.leverage)
                except Exception:
                    pass
            if leverage <= 0:
                leverage = FALLBACK_ACCOUNT_LEVERAGE
            notional = lots * contract_size * entry_price
            required = notional / leverage
            basis = f"estimated (notional/{leverage:g}x)"

        if required <= max_margin:
            return lots

        scale = max_margin / required
        capped = lots * scale
        step = float(info.get("volume_step", 0.01) or 0.01)
        if step > 0:
            # Floor (not round) to the step — rounding up could re-breach the cap.
            capped = math.floor(capped / step) * step
        capped = round(max(capped, 0.0), 3)

        logger.warning(
            f"[SIZER] {symbol}: MARGIN CEILING TRIGGERED! {lots} lots would require "
            f"${required:,.2f} margin [{basis}], above the "
            f"{MAX_MARGIN_UTILISATION_PCT:.0f}% cap of ${max_margin:,.2f} on a "
            f"${account_balance:,.2f} account. Clamped to {capped} lots."
        )
        return capped
    except Exception as e:
        logger.debug(f"[SIZER] {symbol}: margin cap check failed: {e}")
        return lots


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

    Three safety layers, applied in this order:
      1. MINIMUM STOP DISTANCE — refuse (return 0.0) if the stop is tighter than
         the broker's stops_level / spread × MIN_STOP_SPREAD_MULTIPLE / the
         asset-class floor. Such a stop is untradeable live and, being the
         denominator of the sizing formula, is also what produces absurd lots.
      2. HARD RISK CAP — clamp so realised risk can't exceed
         max_risk_hard_cap_pct% of balance even if profile/MT5 values are wrong.
      3. MARGIN CEILING — clamp so required margin stays inside
         MAX_MARGIN_UTILISATION_PCT of equity (MT5 would otherwise reject the
         order with retcode 10019, No money).

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

    # ── GUARD 1: Minimum viable stop distance ─────────────────────────────────
    # A stop inside the broker's stops_level, or inside ~2x the spread, cannot be
    # traded: MT5 rejects it (retcode 10016, Invalid stops) or the spread alone
    # closes it. Refuse to size rather than emit a huge lot against a fictional
    # stop — the signal is then rejected upstream by the zero-lot check.
    min_stop_price, min_stop_reason = minimum_stop_distance(symbol, info, use_live_mt5=use_live_mt5)
    if min_stop_price > 0 and sl_distance < min_stop_price:
        pip_size = get_pip_size(symbol) or 0.0
        sl_pips = (sl_distance / pip_size) if pip_size > 0 else float("nan")
        logger.error(
            f"[SIZER] {symbol}: SL distance {sl_distance:.6f} ({sl_pips:.2f} pips) is below the "
            f"minimum viable stop {min_stop_price:.6f} — driver: {min_stop_reason}. "
            f"This stop is untradeable live (spread/stops-level would consume it). "
            f"Refusing to size — returning 0 lots."
        )
        return 0.0

    # MT5 tick_value = profit in account currency for 1 tick move of 1 standard lot.
    # value_per_unit_move = how many account-currency dollars 1 full price unit is worth per lot.
    value_per_unit_move = tick_value / tick_size
    raw_lot = risk_amount / (sl_distance * value_per_unit_move)

    clamped = max(info["volume_min"], min(info["volume_max"], raw_lot))
    step = info["volume_step"]
    rounded = round(clamped / step) * step if step > 0 else clamped
    final_lot = round(rounded, 3)

    # ── GUARD 2: Hard Risk Cap (safety net) ───────────────────────────────────
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
        final_lot = safe_lot

    # ── GUARD 3: Margin ceiling ───────────────────────────────────────────────
    # Applied last, on the risk-capped lot, because it can only ever reduce size:
    # a lot the account cannot margin is rejected by MT5 (retcode 10019) no
    # matter how small its stop-loss risk is.
    final_lot = _margin_capped_lots(
        symbol, final_lot, entry_price, account_balance, info, use_live_mt5=use_live_mt5
    )

    # ── Final validity floor ──────────────────────────────────────────────────
    # Never return a negative lot, and never return one below the broker's
    # minimum volume — an un-fillable lot must be a refusal, not a silent
    # round-up back above whichever cap just bound.
    volume_min = float(info.get("volume_min", 0.01) or 0.01)
    if final_lot <= 0 or final_lot < volume_min:
        logger.warning(
            f"[SIZER] {symbol}: post-cap lot {final_lot} is below volume_min {volume_min} "
            f"(risk cap and/or margin ceiling left no tradeable size). Returning 0 lots."
        )
        return 0.0

    actual_risk = final_lot * sl_distance * value_per_unit_move
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