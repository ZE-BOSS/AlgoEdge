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

# [2.11] Default only — the real, user-editable value is RiskParams.min_stop_spread_multiple.
# A stop must clear the round-trip cost of crossing the spread by this multiple
# before it is worth taking. At 1.0x the trade is stopped out by the spread
# alone; 2.0x is the smallest multiple that leaves the position any room to
# breathe. Raise it to be stricter about scalp-width stops.
# (Forensic case: APA emitted 0.45-pip stops on USDCHF, whose spread alone is
# 1.5-2.5 pips. Those stops are physically untradeable and, because lot size is
# inversely proportional to stop distance, they produced enormous lots.)
_DEFAULT_MIN_STOP_SPREAD_MULTIPLE = 2.0

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

# [T2.2] Per (symbol, driver) tally of minimum-stop refusals, so a run that
# rejects every signal logs once with a running count rather than once per
# signal. Reset per process; purely diagnostic.
_MIN_STOP_REFUSAL_COUNTS: dict[tuple[str, str], int] = {}


def resolve_global_min_sl_pips(value, symbol: str) -> float:
    """
    Resolve RiskParams.min_sl_pips for THIS symbol.

    A single scalar applied to every asset class is what produced two separate
    failures in the saved book:

      * EURGBP: ATR-derived stops sit near 8 pips against a global floor of 10,
        so the sizer refused 100% of its signals and the run returned 0 trades
        with no explanation.
      * Netherlands 25 / EURGBP / XPTUSD: median forward excursion of 0.47R,
        0.73R and 0.94R — the stop was wider than the instrument's typical move,
        so those setups could not reach 1R even when correct. They are the three
        worst instruments in the book.

    So `min_sl_pips` now also accepts a mapping keyed by asset class:

        {"FOREX": 4, "INDEX": 3, "CRYPTO": 20, "COMMODITY": 8, "SYNTHETIC": 5}

    with an optional "DEFAULT" key. A plain number keeps the old behaviour
    exactly, so nothing that passes a scalar changes.
    """
    if value is None:
        return 0.0
    if isinstance(value, dict):
        cls = _instrument_type_of(symbol)
        for key in (cls, "DEFAULT", "default"):
            if key in value:
                try:
                    return float(value[key] or 0.0)
                except (TypeError, ValueError):
                    return 0.0
        return 0.0
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0

# [2.1] Default only — the real, user-editable value is RiskParams.max_margin_utilisation_pct.
# Ceiling on required margin as a percentage of account equity for a SINGLE
# position. MT5 rejects an order it cannot margin with retcode 10019 (No money),
# so a lot size above this is not "aggressive", it is un-fillable.
# (Forensic case: APA sized up to 40 lots on a $25,000 account = $4,000,000
# notional = 160x account leverage — rejected live, but filled in backtest,
# which is exactly the backtest/live divergence this guard closes.)
# 30% leaves headroom for the other concurrent positions the portfolio may hold.
_DEFAULT_MAX_MARGIN_UTILISATION_PCT = 30.0

# [2.4] Fallback ONLY when MT5 cannot tell us the real leverage AND the caller
# did not supply RiskParams.max_account_leverage either. 1:100 is the common
# retail/prop default; if the real account is lower-leverage the estimate is
# optimistic, which is why the live path always prefers mt5.order_calc_margin()
# and the config path (max_account_leverage) is preferred over this constant.
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


def resolve_cross_rate_point_value(profile, use_live_mt5: bool = True) -> tuple[float, str]:
    """
    [Task 1.12/1.13] For an FX pair whose quote currency is not USD (see
    FX_CROSS_CONVERSION), recompute point_value_per_lot from a LIVE MT5 quote
    on the conversion pair instead of the static rate baked into the profile.
    Falls back to that static value when MT5 is unreachable or the conversion
    pair isn't quoted — exactly the same fallback chain get_symbol_info already
    uses everywhere else, so a live-server outage degrades to "slightly stale"
    rather than "sizing breaks".

    Returns (point_value_per_lot, source) where source is "MT5_LIVE" or
    "STATIC_SNAPSHOT", so callers can log/audit which one was used.
    """
    from backend.risk.compounding import FX_CROSS_CONVERSION

    conversion = FX_CROSS_CONVERSION.get(profile.symbol.upper())
    if conversion is None:
        return profile.point_value_per_lot, "STATIC_SNAPSHOT"  # not a cross pair

    quote_pair, convention = conversion

    if use_live_mt5 and mt5 is not None:
        try:
            terminal = mt5.terminal_info()
            if terminal is not None and terminal.connected:
                tick = mt5.symbol_info_tick(quote_pair)
                if tick is not None and tick.bid > 0 and tick.ask > 0:
                    live_rate = (tick.bid + tick.ask) / 2.0
                    quote_amount = profile.contract_size * profile.point_size
                    if convention == "indirect":
                        value = quote_amount / live_rate
                    else:
                        value = quote_amount * live_rate
                    return value, "MT5_LIVE"
        except Exception as e:
            logger.debug(f"[SIZER] {profile.symbol}: live cross-rate lookup on {quote_pair} failed: {e}")

    return profile.point_value_per_lot, "STATIC_SNAPSHOT"


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

    The InstrumentProfile-fallback branch additionally sets
    `cross_rate_source` ("MT5_LIVE" | "STATIC_SNAPSHOT") when the symbol is a
    non-USD-quoted FX cross (see resolve_cross_rate_point_value) — absent on
    every other branch/symbol, so read it with `.get()`, not as a required key.
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
            # [Task 1.12] For a cross-currency FX pair, prefer a live MT5 quote
            # over the static rate baked into the profile — see
            # resolve_cross_rate_point_value. A no-op for every non-FX-cross
            # symbol (commodities/indices/crypto/synthetics/USD-quoted majors).
            tick_value, cross_rate_source = resolve_cross_rate_point_value(profile, use_live_mt5)
            res = {
                "volume_min": profile.lot_min,
                "volume_max": profile.lot_max,
                "volume_step": profile.lot_step,
                "contract_size": profile.contract_size,
                "tick_value": tick_value,
                "tick_size": profile.point_size,
                # No broker to ask: `point` is the profile's point_size and
                # digits follows from it. stops_level/spread are unknown (0) —
                # callers substitute a modelled figure (see broker_costs.py).
                "stops_level_points": 0,
                "digits": _infer_digits(profile.point_size),
                "point": float(profile.point_size),
                "spread_points": 0.0,
                "source": "InstrumentProfile",
                # [Task 1.12] auditability: was tick_value a live MT5 cross-rate
                # conversion or the static snapshot baked into the profile?
                "cross_rate_source": cross_rate_source,
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


def minimum_stop_distance(
    symbol: str,
    info: dict | None = None,
    use_live_mt5: bool = True,
    min_stop_spread_multiple: float | None = None,
    # [merge] Keep BOTH sides: the widened type (a per-symbol dict, resolved by
    # resolve_global_min_sl_pips) came from this branch; min_stop_cost_multiple
    # is origin/dev's Stage 15 economic stop floor. They are independent gates
    # and both are wanted.
    global_min_sl_pips: float | dict[str, float] | None = 0.0,
    min_stop_cost_multiple: float = 0.0,
) -> tuple[float, str]:
    """
    Smallest stop distance (in PRICE, not pips) that is physically tradeable for
    this symbol — and, optionally, that is worth trading at all.

        min = max(broker stops_level, spread x min_stop_spread_multiple,
                   asset-class floor, global_min_sl_pips,
                   round_trip_cost x min_stop_cost_multiple)

    [2.11] min_stop_spread_multiple defaults to _DEFAULT_MIN_STOP_SPREAD_MULTIPLE
    when not supplied — pass RiskParams.min_stop_spread_multiple through from the
    caller to make it the real, user-editable value.

    [3.5/E3] global_min_sl_pips is RiskParams.min_sl_pips — a portfolio-level
    backstop floor on top of the asset-class floor above, previously defined
    on RiskParams but never actually read by this function (0 = disabled,
    matching "no additional floor").

    [Stage 15.2] min_stop_cost_multiple is an ECONOMIC floor rather than a
    physical one, and it is the difference that matters. Every candidate above
    answers "will the broker accept this stop"; this one answers "can this stop
    pay for the round trip". It requires

        stop >= min_stop_cost_multiple x (spread + 2 x slippage)

    so the multiple is read directly as "the fraction of one R the broker takes
    is at most 1/N".

    Why it exists, measured on the Jan-Aug 2026 corpus (1,326 trade groups —
    see implementation/RESEARCH-2026-08-30-CORPUS.md §2): the round trip
    consumed 0.056R to 0.187R per trade by strategy, and up to 0.37R on
    individual pairings. Three strategies were PROFITABLE measured on price and
    lost money in cash; the whole gap was friction. Rejecting signals whose stop
    fell below 10x the round trip took the book from -$26,844 to -$3,504 while
    keeping 49% of the trades, and 15x reached +$415 — with no change to any
    strategy's logic.

    The existing `min_stop_spread_multiple` looks similar and is not: it
    defaults to 2.0, counts the spread only, and ignores slippage entirely. Two
    times the spread means the broker takes roughly half of every R, which is a
    physical-tradability check being mistaken for an economic one.

    **Defaults to 0.0 (disabled)** — the same convention as
    `max_cluster_risk_pct` and `global_min_sl_pips`. Turning it on rejects
    signals that are currently taken, so it is a deliberate choice, not a
    silent default change.

    Returns (distance_in_price, human_readable_reason). Never raises; on total
    failure it returns (0.0, "unknown") so sizing is not blocked by a cost-lookup
    problem.
    """
    spread_multiple = (
        min_stop_spread_multiple if min_stop_spread_multiple is not None
        else _DEFAULT_MIN_STOP_SPREAD_MULTIPLE
    )
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
        slippage_pips = 0.0
        try:
            from backend.risk.broker_costs import get_broker_costs
            costs = get_broker_costs(symbol, use_live_mt5=use_live_mt5)
            spread_pips = float(costs.get("spread_pips", 0.0) or 0.0)
            costs_stops_pips = float(costs.get("stops_level_pips", 0.0) or 0.0)
            slippage_pips = float(costs.get("slippage_pips", 0.0) or 0.0)
        except Exception as e:
            logger.debug(f"[SIZER] {symbol}: broker cost lookup failed in min-stop check: {e}")
            # Fall back to whatever the raw symbol info knows about the spread.
            if point > 0:
                spread_pips = (float(info.get("spread_points", 0.0) or 0.0) * point) / pip_size

        floor_pips = MIN_STOP_FLOOR_PIPS.get(_instrument_type_of(symbol), _MIN_STOP_FLOOR_DEFAULT_PIPS)

        spread_price = spread_pips * spread_multiple * pip_size
        floor_price = floor_pips * pip_size
        costs_stops_price = costs_stops_pips * pip_size
        # Resolve per asset class — a scalar still behaves exactly as before.
        _global_pips = resolve_global_min_sl_pips(global_min_sl_pips, symbol)
        global_floor_price = _global_pips * pip_size

        candidates = {
            "broker_stops_level": max(broker_price, costs_stops_price),
            f"spread x{spread_multiple:g}": spread_price,
            "asset_class_floor": floor_price,
            "global_min_sl_pips": global_floor_price,
        }

        # [Stage 15.2] The economic floor. Spread is paid once, slippage on both
        # entry and exit — so the round trip is spread + 2 x slippage, which is
        # exactly the quantity the corpus measured against each strategy's stop.
        cost_multiple = float(min_stop_cost_multiple or 0.0)
        if cost_multiple > 0:
            round_trip_pips = spread_pips + 2.0 * slippage_pips
            candidates[f"round_trip_cost x{cost_multiple:g}"] = (
                round_trip_pips * cost_multiple * pip_size
            )

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
    max_margin_utilisation_pct: float | None = None,
    max_account_leverage: float | None = None,
) -> tuple[float, float]:
    """
    Clamp `lots` so the position's required margin stays within
    max_margin_utilisation_pct of account equity.

    Prefers mt5.order_calc_margin() (the broker's own answer, including its
    per-symbol margin rate and any tiered-margin rules). Without MT5 it estimates
        notional = lots x contract_size x entry_price
        margin   = notional / leverage
    which is approximate — currency conversion of a non-USD-quoted notional is
    not attempted — but is enough to stop the pathological 100x-account-notional
    sizes that MT5 rejects live with retcode 10019 (No money).

    [2.1/2.4] max_margin_utilisation_pct and max_account_leverage come from
    RiskParams (real, user-editable settings) — None falls back to the module
    defaults (_DEFAULT_MAX_MARGIN_UTILISATION_PCT / FALLBACK_ACCOUNT_LEVERAGE)
    for callers that don't have a config yet. max_account_leverage=0 means
    "use MT5's own reported leverage" (never the hardcoded fallback).

    Returns (capped_lots, required_margin_dollars). Never raises; returns
    `(lots, 0.0)` if it cannot form an opinion.
    """
    margin_pct = (
        max_margin_utilisation_pct if max_margin_utilisation_pct is not None
        else _DEFAULT_MAX_MARGIN_UTILISATION_PCT
    )
    try:
        if lots <= 0 or account_balance <= 0 or entry_price <= 0:
            return lots, 0.0

        max_margin = account_balance * (margin_pct / 100.0)
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
                return lots, 0.0
            # [2.4] Config leverage wins when set (>0); 0 or unset means "ask
            # MT5"; only the hardcoded FALLBACK_ACCOUNT_LEVERAGE is used when
            # neither is available.
            leverage = max_account_leverage if (max_account_leverage or 0) > 0 else 0.0
            if leverage <= 0 and mt5 is not None:
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
            return lots, required

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
            f"{margin_pct:.0f}% cap of ${max_margin:,.2f} on a "
            f"${account_balance:,.2f} account. Clamped to {capped} lots."
        )
        return capped, required
    except Exception as e:
        logger.debug(f"[SIZER] {symbol}: margin cap check failed: {e}")
        return lots, 0.0


# [I3] Task 0.3 — per-trade sizing diagnostics. calculate_lot_size()'s signature
# and single-float return are relied on by every caller; rather than change the
# contract, the internal stages it already computes (pre-cap, post-hard-cap,
# post-margin-cap) are recorded here and read back by the caller (risk/engine.py)
# immediately after the call. ContextVar (not a module global) so this is safe
# under concurrent live + backtest execution in the same process — same pattern
# as _frozen_symbol_info_var above.
_last_sizing_diagnostics_var: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "last_sizing_diagnostics", default=None
)


def get_last_sizing_diagnostics() -> dict | None:
    """The internal stages of the most recent calculate_lot_size() call on this
    context (thread/async task). None if calculate_lot_size has not run yet."""
    return _last_sizing_diagnostics_var.get()


def _resolved_cost_pips(cost_config: dict | None, field: str, symbol: str, use_live_mt5: bool) -> float:
    """
    [2.6] The same explicit-value-wins-else-broker-default resolution
    backtester/engine.py::resolve_effective_costs uses, duplicated narrowly here
    so calculate_lot_size can size against the SAME slippage/spread figures that
    will actually be applied at fill — without importing the backtester module
    (which would create a circular import: backtester imports position_sizer).
    """
    raw = (cost_config or {}).get(field)
    _unset_tokens = {"", "auto", "default", "broker", "mt5", "none"}
    is_unset = raw is None or (isinstance(raw, str) and raw.strip().lower() in _unset_tokens)
    if not is_unset:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    try:
        from backend.risk.broker_costs import get_broker_costs
        return float(get_broker_costs(symbol, use_live_mt5=use_live_mt5).get(field, 0.0) or 0.0)
    except Exception:
        return 0.0


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
    max_margin_utilisation_pct: float | None = None,
    max_account_leverage: float | None = None,
    min_deployable_risk_pct: float = 0.0,
    min_stop_spread_multiple: float | None = None,
    cost_config: dict | None = None,
    global_min_sl_pips: float = 0.0,
    min_stop_cost_multiple: float = 0.0,
) -> float:
    """
    Calculate lot size so that if SL is hit, loss = risk_pct% of balance.
    Formula: Lot = (Balance × Risk%) / (Effective_SL_distance × (tick_value / tick_size))
    where Effective_SL_distance = SL_distance + (slippage_pips + spread_pips) × pip_size
    [2.6] — sizing against the raw stop distance alone understates realised risk,
    because the actual exit (and the cost model in _calc_pnl) always pays the
    spread/slippage on top of it.

    Four safety layers, applied in this order:
      1. MINIMUM STOP DISTANCE — refuse (return 0.0) if the stop is tighter than
         the broker's stops_level / spread × min_stop_spread_multiple / the
         asset-class floor. Such a stop is untradeable live and, being the
         denominator of the sizing formula, is also what produces absurd lots.
      2. HARD RISK CAP — clamp so realised risk can't exceed
         max_risk_hard_cap_pct% of balance even if profile/MT5 values are wrong.
      3. MARGIN CEILING — clamp so required margin stays inside
         max_margin_utilisation_pct of equity (MT5 would otherwise reject the
         order with retcode 10019, No money). [2.2] If that clamp would push
         realised risk below min_deployable_risk_pct, refuse instead of taking
         a token position.
      4. [2.5] Never round UP to volume_min — floor to the lot step and refuse
         (0.0, reason below_broker_min_lot) if that floors below volume_min.

    Source: RiskManagement_Spec.md Section 5.1
    """
    risk_amount = account_balance * (risk_pct / 100)
    sl_distance = abs(entry_price - stop_loss_price)

    # [I3] diag accumulates across the function; set on every return so a
    # caller reading get_last_sizing_diagnostics() right after this call always
    # sees THIS call's stages, never a stale value from a previous symbol.
    diag: dict = {
        "requested_risk_pct": risk_pct,
        "pre_cap_lots": None,
        "post_hardcap_lots": None,
        "post_margin_lots": None,
        "margin_truncation_pct": None,
        "refused_reason": None,
    }

    info = get_symbol_info(symbol, use_live_mt5=use_live_mt5)
    tick_value = info.get("tick_value", 1.0)
    tick_size = info.get("tick_size", 0.00001)
    source = info.get("source", "UNKNOWN")

    if tick_size == 0 or tick_value == 0 or sl_distance == 0:
        logger.error(f"[SIZER] {symbol}: Zero tick_size/tick_value/sl_distance — returning 0 lots.")
        diag["refused_reason"] = "zero_tick_or_distance"
        _last_sizing_diagnostics_var.set(diag)
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
            diag["refused_reason"] = "no_instrument_profile"
            _last_sizing_diagnostics_var.set(diag)
            return 0.0
        logger.warning(f"[SIZER] {symbol}: Sizing using DEFAULT fallback! Risk calculations may be inaccurate.")

    # ── GUARD 1: Minimum viable stop distance ─────────────────────────────────
    # A stop inside the broker's stops_level, or inside ~2x the spread, cannot be
    # traded: MT5 rejects it (retcode 10016, Invalid stops) or the spread alone
    # closes it. Refuse to size rather than emit a huge lot against a fictional
    # stop — the signal is then rejected upstream by the zero-lot check.
    min_stop_price, min_stop_reason = minimum_stop_distance(
        symbol, info, use_live_mt5=use_live_mt5, min_stop_spread_multiple=min_stop_spread_multiple,
        global_min_sl_pips=global_min_sl_pips,
        min_stop_cost_multiple=min_stop_cost_multiple,
    )
    # [15.1] Record what this trade's round trip costs as a fraction of its own
    # R, on EVERY trade, whether or not the economic floor above is switched on.
    #
    # This is the number the Jan-Aug 2026 corpus turned out to hinge on: three
    # strategies were profitable measured on price and lost money in cash, and
    # the entire gap was here. It was invisible at the time because nothing
    # recorded it — the cost model knew the spread, the sizer knew the stop, and
    # no one ever divided one by the other.
    #
    # Recording it unconditionally means `min_stop_cost_multiple` can be chosen
    # from a histogram of your own runs rather than from someone else's
    # threshold. RunReport's Cost Impact panel is where it surfaces.
    try:
        from backend.risk.broker_costs import get_broker_costs
        _c = get_broker_costs(symbol, use_live_mt5=use_live_mt5)
        _pip = get_pip_size(symbol) or 0.0
        _round_trip_price = (
            float(_c.get("spread_pips", 0.0) or 0.0)
            + 2.0 * float(_c.get("slippage_pips", 0.0) or 0.0)
        ) * _pip
        if sl_distance > 0 and _round_trip_price > 0:
            diag["round_trip_cost_price"] = _round_trip_price
            diag["round_trip_cost_R"] = _round_trip_price / sl_distance
            diag["stop_cost_cover"] = sl_distance / _round_trip_price
    except Exception as e:
        logger.debug(f"[SIZER] {symbol}: friction diagnostic unavailable: {e}")

    if min_stop_price > 0 and sl_distance < min_stop_price:
        pip_size = get_pip_size(symbol) or 0.0
        sl_pips = (sl_distance / pip_size) if pip_size > 0 else float("nan")
        # [T2.2] WARNING, not ERROR.
        #
        # Refusing to size is a normal, correct rejection — the signal is
        # dropped upstream by the zero-lot check and the run continues. Logging
        # it at ERROR made a routine filter look like a crash, which is exactly
        # why "the backtest just ends without producing a result" was
        # misdiagnosed for so long: an EURGBP run legitimately rejected 100% of
        # its signals against a 10-pip global floor and emitted a wall of red.
        #
        # Rate-limited per symbol+driver: one full explanation, then a periodic
        # count. A run that rejects every signal used to emit one line per
        # signal, which is its own denial-of-service on the log stream.
        _key = (symbol, min_stop_reason)
        _n = _MIN_STOP_REFUSAL_COUNTS.get(_key, 0) + 1
        _MIN_STOP_REFUSAL_COUNTS[_key] = _n
        if _n == 1 or _n % 100 == 0:
            logger.warning(
                f"[SIZER] {symbol}: SL distance {sl_distance:.6f} ({sl_pips:.2f} pips) is below "
                f"the minimum viable stop {min_stop_price:.6f} — driver: {min_stop_reason}. "
                f"This stop is untradeable live (spread/stops-level would consume it). "
                f"Refusing to size — returning 0 lots."
                + (f"  [{_n} refusals so far for this symbol/driver]" if _n > 1 else "")
            )
        diag["refused_reason"] = "stop_below_min_viable"
        _last_sizing_diagnostics_var.set(diag)
        return 0.0

    # MT5 tick_value = profit in account currency for 1 tick move of 1 standard lot.
    # value_per_unit_move = how many account-currency dollars 1 full price unit is worth per lot.
    value_per_unit_move = tick_value / tick_size

    # [2.6] Size against the stop distance PLUS the round-trip cost that will
    # actually be paid at entry/exit — sizing against the raw stop alone means
    # realised risk always runs above target by roughly the spread+slippage.
    pip_size_for_costs = get_pip_size(symbol) or 0.0
    slippage_pips = _resolved_cost_pips(cost_config, "slippage_pips", symbol, use_live_mt5)
    spread_pips = _resolved_cost_pips(cost_config, "spread_pips", symbol, use_live_mt5)
    cost_buffer_price = (slippage_pips + spread_pips) * pip_size_for_costs
    effective_sl_distance = sl_distance + cost_buffer_price
    diag["cost_buffer_price"] = cost_buffer_price

    raw_lot = risk_amount / (effective_sl_distance * value_per_unit_move)
    diag["raw_lot"] = raw_lot  # [I3] before ANY clamp — the pure risk-based answer
    # [I3] Which broker volume bound (if either) fired, so a caller can tell
    # "the account's risk% wanted more/less than this symbol's lot_min/lot_max
    # allows" apart from the two guards below, which only ever see the
    # already-clamped value and would otherwise misreport this as "none".
    if raw_lot > info["volume_max"]:
        diag["broker_volume_clamp"] = "lot_max"
    elif raw_lot < info["volume_min"]:
        diag["broker_volume_clamp"] = "lot_min"
    else:
        diag["broker_volume_clamp"] = None

    # [2.5] Clamp to volume_max only here — never round/clamp UP to volume_min.
    # An account whose risk-based lot floors below the broker's minimum cannot
    # actually take this trade at its intended risk; that must surface as a
    # refusal (below), not a silent size-up past the risk budget.
    step = info["volume_step"]
    capped_to_max = min(info["volume_max"], raw_lot)
    final_lot = math.floor(capped_to_max / step) * step if step > 0 else capped_to_max
    final_lot = round(final_lot, 3)
    diag["pre_cap_lots"] = final_lot  # [I3] risk-based size before either safety guard

    # ── GUARD 2: Hard Risk Cap (safety net) ───────────────────────────────────
    # Calculate actual dollar risk with the final lot, and clamp if it exceeds the cap.
    actual_risk = final_lot * effective_sl_distance * value_per_unit_move
    max_risk_dollars = account_balance * (max_risk_hard_cap_pct / 100)
    if actual_risk > max_risk_dollars:
        # Scale lots down to meet the cap — [2.5] floor, do not clamp up to volume_min.
        safe_lot = max_risk_dollars / (effective_sl_distance * value_per_unit_move)
        safe_lot = math.floor(safe_lot / step) * step if step > 0 else safe_lot
        safe_lot = round(safe_lot, 3)
        logger.warning(
            f"[SIZER] {symbol}: HARD CAP TRIGGERED! "
            f"Calculated lot {final_lot} would risk ${actual_risk:.2f} "
            f"({actual_risk/account_balance*100:.2f}% of balance). "
            f"Clamped to {safe_lot} lots (${max_risk_dollars:.2f} max). "
            f"Source was: {source}. Check your InstrumentProfile for this symbol!"
        )
        final_lot = safe_lot
    diag["post_hardcap_lots"] = final_lot  # [I3] size after Guard 2, before Guard 3

    # ── GUARD 3: Margin ceiling ───────────────────────────────────────────────
    # Applied last, on the risk-capped lot, because it can only ever reduce size:
    # a lot the account cannot margin is rejected by MT5 (retcode 10019) no
    # matter how small its stop-loss risk is.
    _pre_margin_lot = final_lot
    final_lot, required_margin = _margin_capped_lots(
        symbol, final_lot, entry_price, account_balance, info, use_live_mt5=use_live_mt5,
        max_margin_utilisation_pct=max_margin_utilisation_pct,
        max_account_leverage=max_account_leverage,
    )
    diag["post_margin_lots"] = final_lot  # [I3]
    diag["required_margin"] = required_margin
    if _pre_margin_lot > 0 and final_lot < _pre_margin_lot:
        diag["margin_truncation_pct"] = round((1.0 - final_lot / _pre_margin_lot) * 100.0, 2)

        # [2.2] If the margin cap truncated realised risk below the deployable
        # floor, refuse the trade outright rather than take a token position —
        # this is the fix for crypto pinning at ~$7,500 notional / 0.05-0.4%
        # risk on every trade regardless of the configured risk_pct.
        if min_deployable_risk_pct > 0:
            realised_risk_pct_if_taken = (
                (final_lot * effective_sl_distance * value_per_unit_move) / account_balance * 100.0
                if account_balance > 0 else 0.0
            )
            if final_lot <= 0 or realised_risk_pct_if_taken < min_deployable_risk_pct:
                logger.warning(
                    f"[SIZER] {symbol}: margin ceiling truncates realised risk to "
                    f"{realised_risk_pct_if_taken:.3f}%, below min_deployable_risk_pct="
                    f"{min_deployable_risk_pct:.3f}%. Refusing rather than taking a token position."
                )
                diag["refused_reason"] = "margin_ceiling_below_min_risk"
                _last_sizing_diagnostics_var.set(diag)
                return 0.0

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
        diag["refused_reason"] = "below_broker_min_lot"
        _last_sizing_diagnostics_var.set(diag)
        return 0.0

    _last_sizing_diagnostics_var.set(diag)  # [I3] success path
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

def kelly_risk_fraction(
    win_rate: float,
    avg_win_r: float,
    avg_loss_r: float,
    max_kelly_fraction: float = 0.25,
) -> float:
    """
    [2.16] Renamed from kelly_lot_size, which never returned a lot size — it
    returned a dollar amount (balance * fraction) with no stop distance or
    instrument in its signature, so it could not have been a lot-sizing
    function. Still has zero callers; kept as the fraction Kelly's formula
    actually computes, for a future caller to turn into a risk_pct.

    Kelly Criterion risk fraction.
    f* = (W × B - L) / B
    Uses fractional Kelly (default 25% cap) to reduce volatility.
    Source: RiskManagement_Spec.md Section 5.2
    """
    loss_rate = 1 - win_rate
    b = avg_win_r / avg_loss_r if avg_loss_r > 0 else 1
    kelly = (win_rate * b - loss_rate) / b
    fraction = min(kelly, max_kelly_fraction)
    return max(fraction, 0.0)

def resolve_sizing_base_balance(
    sizing_basis: str,
    static_balance: float,
    live_balance: float,
    live_equity: float | None = None,
) -> float:
    """
    [4.2/D1] The ONE function that decides what a trade's position sizing is
    computed against — `RiskParams.sizing_basis`. Before this existed, live
    PERSONAL accounts silently used `live_balance` (bot_service.py passed the
    account's live, growing balance as `initial_balance` for any non-prop-firm
    account) while backtests and prop-firm accounts always used a fixed
    `static_balance` — a real backtest/live divergence for the majority
    (personal-account) case, and implicit compounding against the explicit
    "compounding removed per user request" decision recorded in
    risk/engine.py's evaluate_signal.

    `STATIC` (default) -> static_balance, never grows/shrinks with P&L.
    `BALANCE` -> live_balance (closed-trade balance; compounds with realised P&L).
    `EQUITY`  -> live_equity if supplied, else falls back to live_balance.
    """
    if sizing_basis == "BALANCE":
        return live_balance
    if sizing_basis == "EQUITY":
        return live_equity if live_equity is not None else live_balance
    return static_balance  # STATIC, or any unrecognised value


def get_confluence_scaled_risk(
    base_risk_pct: float,
    confluence_score: int,
    tiers: list[tuple[int, float]] | None = None,
    reject_below_confluence: bool = True,
) -> float:
    """
    Scales the base risk percentage down if the confluence score is lower than optimal.

    [2.10] `tiers` is a list of (score_floor, pct_of_base_risk), evaluated
    highest-floor-first — pass RiskParams.confluence_risk_tiers through from the
    caller to make this a real, user-editable setting rather than the hardcoded
    80/65/55 -> 100/75/50 ladder used when tiers is None.

    A score below every tier's floor deploys 0% when reject_below_confluence is
    True (the D-3 default: outright rejection). When False, it instead deploys
    at the LOWEST tier's fraction — i.e. never rejects on confluence alone.
    """
    active_tiers = tiers if tiers else [(80, 100.0), (65, 75.0), (55, 50.0)]
    ordered = sorted(active_tiers, key=lambda t: t[0], reverse=True)
    for floor, pct in ordered:
        if confluence_score >= floor:
            return base_risk_pct * (pct / 100.0)
    if reject_below_confluence or not ordered:
        return 0.0
    lowest_pct = ordered[-1][1]
    return base_risk_pct * (lowest_pct / 100.0)