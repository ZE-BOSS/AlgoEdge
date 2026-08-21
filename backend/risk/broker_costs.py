"""
backend/risk/broker_costs.py

Single source of truth for TRANSACTION COSTS (spread, commission, swap, slippage)
and the broker's minimum-stop distance.

Design principle (from the product requirement):
    Transaction costs used by the BACKTESTER must be the same costs the LIVE
    account actually pays. Where the live broker can tell us (MT5 terminal
    connected), we read them straight off the terminal. Where it cannot, we fall
    back to defensible per-asset-class averages, so that:

        "if it's profitable under average costs, it's profitable in both
         backtest and live."

Everything here is defensive: this module must NEVER raise. A cost lookup that
blows up would either kill a backtest or (worse) silently under-cost a live
sizing decision, so every MT5 interaction is wrapped and always degrades to the
asset-class default.

Public API (stable — other modules code against this exact contract):

    get_broker_costs(symbol, use_live_mt5=True) -> dict with keys:
        spread_pips, commission_per_lot, swap_long_per_lot_per_day,
        swap_short_per_lot_per_day, slippage_pips, stops_level_pips, source

    get_slippage_pips(symbol, use_live_mt5=True) -> float
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from backend.utils.logger import get_logger

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# TUNABLES
# ─────────────────────────────────────────────────────────────────────────────

# Cache TTL, seconds. Mirrors the 60s TTL used by position_sizer's
# _pip_size_cache / _symbol_info_cache so all three caches expire together and a
# reconnecting terminal is picked up within the same window everywhere.
_COST_CACHE_TTL_SEC = 60.0

# How far back to look for closed deals when deriving commission empirically.
# 30 days is long enough to catch a handful of round turns on a symbol that is
# traded a few times a week, short enough that a broker's commission schedule
# change is reflected reasonably quickly.
COMMISSION_HISTORY_DAYS = 30

# Minimum number of round turns before we trust the empirical commission figure.
# One deal can be an odd partial-close artefact; three is enough to be a pattern.
_MIN_COMMISSION_DEALS = 2

# Absolute sanity band for an empirically-derived commission (USD per lot, round
# turn). Retail/prop commission is essentially never outside this range; a value
# outside it means we mis-parsed the history (e.g. a swap-heavy carry deal), so
# we drop back to the asset-class default rather than pollute the backtest.
_COMMISSION_SANITY_MIN = 0.0
_COMMISSION_SANITY_MAX = 60.0


# ─────────────────────────────────────────────────────────────────────────────
# ASSET-CLASS COST DEFAULTS
# ─────────────────────────────────────────────────────────────────────────────
#
# Keyed by InstrumentProfile.instrument_type so callers can look up defaults
# directly from a profile without a translation table.
#
# All spreads/slippage are in PIPS (as defined by position_sizer.get_pip_size()
# for that symbol — i.e. 0.0001 for most FX, 0.01 for JPY pairs, 0.1 for gold,
# 1.0 index point for indices, 0.01 for synthetics).
#
# Figures are deliberately conservative-but-realistic retail/prop-firm averages
# (raw/ECN-style accounts, which is what prop firms hand out). Reasoning for
# every number is in the inline comments — these are what a backtest falls back
# to when MT5 is absent, so they have to be defensible.
#
# "spread_min_pips"/"spread_max_pips" are the CLAMP BAND applied to a live MT5
# spread reading. MT5's symbol_info().spread is the spread RIGHT NOW: sampled at
# 02:00 server time or during an NFP print it can be 10-40x the session average
# and would poison the cost model. The band keeps a live reading honest without
# discarding it.

ASSET_CLASS_COST_DEFAULTS: dict[str, dict[str, float]] = {

    # FX MAJORS baseline (EURUSD/GBPUSD/USDJPY/USDCHF/AUDUSD/USDCAD/NZDUSD).
    # spread 1.2 pips: raw-spread accounts average ~0.1-0.3 on EURUSD but a
    #   standard/prop account is ~1.0-1.5 during London/NY; 1.2 is the honest
    #   all-session blend including the thin Asia open.
    # commission $7/lot round turn: the near-universal ECN rate ($3.50/side).
    # swap: -2.0/-1.0 USD per lot per day is a mid-range carry cost; both sides
    #   modelled negative because on most retail books both sides pay.
    # slippage 0.4 pips: typical market-order slippage on a liquid major at
    #   normal size; entries and exits each pay roughly this.
    # stops_level 0.5 pips: most FX brokers set stops level to 0, but a 0.5-pip
    #   floor stops the backtester from modelling stops the broker would reject.
    "FOREX": {
        "spread_pips": 1.2,
        "commission_per_lot": 7.0,
        "swap_long_per_lot_per_day": -2.0,
        "swap_short_per_lot_per_day": -1.0,
        "slippage_pips": 0.4,
        "stops_level_pips": 0.5,
        "spread_min_pips": 0.1,
        "spread_max_pips": 5.0,
    },

    # INDEX CFDs (US30/US500/NAS100/GER40/UK100...). Spread quoted in INDEX
    # POINTS (pip size 1.0 index point for most; 0.25/0.1 tick instruments still
    # resolve through get_pip_size()).
    # spread 1.5 points: US500 ~0.4-0.8, NAS100/GER40 ~1.5-3.0 in session; 1.5
    #   is the cross-index blend.
    # commission $0: index CFDs are overwhelmingly commission-free with the cost
    #   in the spread. Prop firms almost never add index commission.
    # swap: index CFDs carry a financing charge (~SOFR/ESTR ± spread) that is
    #   meaningful on longs; -3.0 long / -1.0 short reflects that asymmetry.
    # slippage 0.5 points: index CFDs gap more than FX at the cash open.
    "INDEX": {
        "spread_pips": 1.5,
        "commission_per_lot": 0.0,
        "swap_long_per_lot_per_day": -3.0,
        "swap_short_per_lot_per_day": -1.0,
        "slippage_pips": 0.5,
        "stops_level_pips": 1.0,
        "spread_min_pips": 0.2,
        "spread_max_pips": 8.0,
    },

    # CRYPTO CFDs. Spread is proportional to price, not a fixed pip count, so
    # the real driver is CRYPTO_SPREAD_PCT_OF_PRICE below (~0.06% ≈ the middle
    # of the 0.05-0.10% retail band). The static pip figures here are only the
    # last-resort fallback when no price is available.
    # commission $0: crypto CFDs are spread-only at essentially every broker.
    # swap: crypto financing is brutal and symmetric-ish (both sides pay);
    #   -20/-20 per lot per day reflects the typical ~0.03%/day on a 1-coin lot
    #   at BTC-scale notionals — deliberately punitive so backtests don't
    #   "discover" an edge that is really just unmodelled overnight carry.
    # slippage: crypto books are thin; 0.15% of the spread-equivalent, modelled
    #   as a large pip count for BTC-scale prices.
    "CRYPTO": {
        "spread_pips": 30.0,
        "commission_per_lot": 0.0,
        "swap_long_per_lot_per_day": -20.0,
        "swap_short_per_lot_per_day": -20.0,
        "slippage_pips": 15.0,
        "stops_level_pips": 10.0,
        "spread_min_pips": 1.0,
        "spread_max_pips": 400.0,
    },

    # COMMODITY baseline = GOLD (the commodity that actually gets traded here).
    # spread 3.0 pips: a gold pip is 0.10 of price, so 3.0 pips = $0.30/oz —
    #   the standard retail XAUUSD in-session spread (25-35 cents).
    # commission $0 baseline (spread-only on most retail books); raw accounts
    #   charging ~$7/lot are captured by the live-MT5 path when connected.
    # swap -6/-2: gold carries a real financing cost, heavier on longs.
    # slippage 1.0 pip (= $0.10/oz): gold fills are noisier than FX.
    # Per-symbol overrides below refine oil/silver/platinum/gas/copper, each of
    # which has a different pip definition.
    "COMMODITY": {
        "spread_pips": 3.0,
        "commission_per_lot": 0.0,
        "swap_long_per_lot_per_day": -6.0,
        "swap_short_per_lot_per_day": -2.0,
        "slippage_pips": 1.0,
        "stops_level_pips": 1.0,
        "spread_min_pips": 0.5,
        "spread_max_pips": 30.0,
    },

    # SYNTHETIC (Deriv Volatility/Boom/Crash/Step/Jump/Range/DEX).
    # These are broker-generated instruments: there is NO commission and NO
    # swap — Deriv builds its entire margin into the quoted spread, and the
    # synthetic feed never rolls over (they trade 24/7, so there is no
    # overnight financing event at all). Modelling any commission here would
    # make a synthetic backtest strictly pessimistic vs live.
    # spread 2.0 pips (pip = 0.01 price units for most V-indices): Deriv's
    #   quoted spread on V75/V100 is a couple of points in normal conditions.
    # slippage 1.0 pip: synthetics fill fast but the tick granularity is coarse
    #   relative to their volatility, so a one-point adverse fill is realistic.
    "SYNTHETIC": {
        "spread_pips": 2.0,
        "commission_per_lot": 0.0,   # Deriv synthetics are commission-free — cost is in the spread
        "swap_long_per_lot_per_day": 0.0,   # no rollover: synthetics trade 24/7 with no financing
        "swap_short_per_lot_per_day": 0.0,
        "slippage_pips": 1.0,
        "stops_level_pips": 2.0,
        "spread_min_pips": 0.2,
        "spread_max_pips": 30.0,
    },
}

# FX CROSSES (no USD leg, or exotic-ish majors-crosses like GBPNZD/GBPAUD).
# Crosses are quoted through two legs, so their spread is roughly the sum of the
# two constituent majors' spreads — ~2.0 pips is the standard session average
# for GBPJPY/EURJPY/GBPAUD/GBPNZD/EURAUD. Commission is unchanged (it is charged
# per lot, not per pip). Slippage is wider for the same two-leg reason.
FOREX_CROSS_COST_DEFAULTS: dict[str, float] = {
    "spread_pips": 2.0,
    "commission_per_lot": 7.0,
    "swap_long_per_lot_per_day": -3.0,
    "swap_short_per_lot_per_day": -2.0,
    "slippage_pips": 0.7,
    "stops_level_pips": 0.5,
    "spread_min_pips": 0.3,
    "spread_max_pips": 10.0,
}

# The seven USD-legged majors. Anything else with instrument_type == "FOREX"
# is treated as a cross and gets the wider cross defaults above.
FOREX_MAJORS: frozenset[str] = frozenset({
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
})

# Crypto spread as a fraction of price (0.06% — midpoint of the 0.05-0.10%
# retail CFD band). Converted to pips at lookup time using the live/last price,
# because a fixed pip spread is meaningless when BTC can be $20k or $120k.
CRYPTO_SPREAD_PCT_OF_PRICE = 0.0006
# Crypto slippage as a fraction of price (0.03% — half the spread, which is the
# usual relationship on thin crypto books).
CRYPTO_SLIPPAGE_PCT_OF_PRICE = 0.0003

# Order-of-magnitude reference prices, used ONLY to turn the percentage spread
# above into a pip count when there is no live price (i.e. offline backtests).
# The percentage is the real spec; these just anchor it. They do not need to be
# current — being within ~2x is enough for a cost model, and a live MT5 price
# always takes precedence.
CRYPTO_REFERENCE_PRICE: dict[str, float] = {
    "BTCUSD": 100_000.0,
    "ETHUSD": 3_500.0,
    "SOLUSD": 150.0,
    "LTCUSD": 100.0,
    "XRPUSD": 2.5,
    "DOGUSD": 0.20,
}

# Per-symbol refinements over the asset-class row. Only the keys that differ
# from the class default need to be listed.
SYMBOL_COST_OVERRIDES: dict[str, dict[str, float]] = {
    # Oil: pip = 0.01 of price. Retail WTI/Brent spread is ~3-5 cents = 3-5
    # pips; slippage ~2 cents. Both sides pay swap on oil CFDs (contango roll).
    "USOIL": {"spread_pips": 4.0, "slippage_pips": 2.0, "spread_min_pips": 1.0,
              "spread_max_pips": 25.0, "swap_long_per_lot_per_day": -4.0,
              "swap_short_per_lot_per_day": -4.0},
    "UKOIL": {"spread_pips": 4.0, "slippage_pips": 2.0, "spread_min_pips": 1.0,
              "spread_max_pips": 25.0, "swap_long_per_lot_per_day": -4.0,
              "swap_short_per_lot_per_day": -4.0},
    # Gold: stated explicitly so it does not silently inherit a re-tuned
    # COMMODITY row. 3.0 pips × 0.10 pip size = $0.30/oz spread; brokers quote
    # 25-35 cents in session and several dollars over the rollover, hence the
    # wide clamp ceiling.
    "XAUUSD": {"spread_pips": 3.0, "slippage_pips": 1.0, "spread_min_pips": 1.0,
               "spread_max_pips": 20.0},
    # Silver: pip = 0.001 → typical 2-3 cent spread = 20-30 pips.
    "XAGUSD": {"spread_pips": 25.0, "slippage_pips": 10.0, "spread_min_pips": 5.0,
               "spread_max_pips": 150.0},
    "XPTUSD": {"spread_pips": 200.0, "slippage_pips": 50.0, "spread_min_pips": 20.0,
               "spread_max_pips": 900.0},
    # Natural gas: pip = 0.001 → typical 0.003-0.005 spread = 3-5 pips.
    "NG": {"spread_pips": 5.0, "slippage_pips": 2.0, "spread_min_pips": 1.0,
           "spread_max_pips": 40.0},
    # Copper: pip = 0.0001 → ~0.0008-0.0015 spread = 8-15 pips.
    "XCUUSD": {"spread_pips": 12.0, "slippage_pips": 5.0, "spread_min_pips": 3.0,
               "spread_max_pips": 60.0},
    # NAS100 pip resolves to its 0.25 tick, so a 2.0-index-point spread is
    # 8 "pips"; GER40 pip is 0.1 index points → a 1.5-point spread is 15 pips.
    "NAS100": {"spread_pips": 8.0, "slippage_pips": 4.0, "spread_min_pips": 2.0,
               "spread_max_pips": 60.0},
    "US500": {"spread_pips": 6.0, "slippage_pips": 3.0, "spread_min_pips": 1.0,
              "spread_max_pips": 50.0},
    "US2000": {"spread_pips": 10.0, "slippage_pips": 4.0, "spread_min_pips": 2.0,
               "spread_max_pips": 60.0},
    "GER40": {"spread_pips": 15.0, "slippage_pips": 6.0, "spread_min_pips": 3.0,
              "spread_max_pips": 90.0},
}

_COST_KEYS = (
    "spread_pips",
    "commission_per_lot",
    "swap_long_per_lot_per_day",
    "swap_short_per_lot_per_day",
    "slippage_pips",
    "stops_level_pips",
    "source",
)

# {(symbol, use_live_mt5): (timestamp, cost_dict)} — same shape/TTL pattern as
# position_sizer._symbol_info_cache.
_cost_cache: dict[tuple[str, bool], tuple[float, dict]] = {}


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _mt5_connected(use_live_mt5: bool) -> bool:
    """True only if the MT5 package is importable AND the terminal is connected."""
    if not use_live_mt5 or mt5 is None:
        return False
    try:
        terminal = mt5.terminal_info()
        return terminal is not None and bool(terminal.connected)
    except Exception:
        return False


def _instrument_type(symbol: str) -> str:
    """Resolve the InstrumentProfile instrument_type, defaulting to FOREX."""
    try:
        from backend.risk.compounding import get_instrument_profile
        profile = get_instrument_profile(symbol)
        if profile is not None:
            return profile.instrument_type
    except Exception:
        pass
    return "FOREX"


def _base_defaults(symbol: str) -> dict:
    """
    Asset-class default row for this symbol, with FX major/cross separation and
    per-symbol overrides applied. Always returns a fresh dict (never the shared
    module-level one), so callers can mutate freely.
    """
    itype = _instrument_type(symbol)
    if itype == "FOREX":
        clean = symbol.upper().replace("/", "")
        base = dict(ASSET_CLASS_COST_DEFAULTS["FOREX"]) if clean in FOREX_MAJORS \
            else dict(FOREX_CROSS_COST_DEFAULTS)
    else:
        base = dict(ASSET_CLASS_COST_DEFAULTS.get(itype, ASSET_CLASS_COST_DEFAULTS["FOREX"]))

    # Symbol-level refinement (resolve through the alias table so "GOLD"/"DAX"
    # pick up the XAUUSD/GER40 rows).
    key = symbol.upper().replace("/", "")
    override = SYMBOL_COST_OVERRIDES.get(key)
    if override is None:
        try:
            from backend.risk.compounding import get_instrument_profile
            profile = get_instrument_profile(symbol)
            if profile is not None:
                override = SYMBOL_COST_OVERRIDES.get(profile.symbol.upper())
        except Exception:
            override = None
    if override:
        base.update(override)

    # Crypto spread/slippage scale with price (a fixed pip spread is meaningless
    # when one coin is $100,000 and another is $0.20), so derive them from a
    # live price where possible and from the reference price otherwise.
    if itype == "CRYPTO":
        price = _last_price(symbol)
        if not price:
            price = CRYPTO_REFERENCE_PRICE.get(key, 0.0)
            if not price:
                try:
                    from backend.risk.compounding import get_instrument_profile
                    profile = get_instrument_profile(symbol)
                    if profile is not None:
                        price = CRYPTO_REFERENCE_PRICE.get(profile.symbol.upper(), 0.0)
                except Exception:
                    price = 0.0
        pip = _pip_size(symbol)
        if price and price > 0 and pip > 0:
            base["spread_pips"] = (price * CRYPTO_SPREAD_PCT_OF_PRICE) / pip
            base["slippage_pips"] = (price * CRYPTO_SLIPPAGE_PCT_OF_PRICE) / pip
            # Crypto brokers set their minimum stop distance around one spread;
            # scale it with price too, or a flat pip figure would be trivial on
            # BTC and punitive on a sub-dollar coin.
            base["stops_level_pips"] = base["spread_pips"]
            # Widen the clamp band around the price-derived figure rather than
            # keeping the static one, which would be meaningless at this price.
            base["spread_min_pips"] = base["spread_pips"] * 0.2
            base["spread_max_pips"] = base["spread_pips"] * 6.0
    return base


def _pip_size(symbol: str) -> float:
    """Pip size via position_sizer (the single definition of a pip in this codebase)."""
    try:
        from backend.risk.position_sizer import get_pip_size
        return float(get_pip_size(symbol)) or 0.0
    except Exception:
        return 0.0001


def _last_price(symbol: str) -> float:
    """
    Best-effort last price, used only to scale crypto spreads. Returns 0.0 when
    unavailable (MT5 absent/disconnected) — callers must handle that.
    """
    if mt5 is None:
        return 0.0
    try:
        tick = mt5.symbol_info_tick(symbol)
        if tick is not None:
            bid = float(getattr(tick, "bid", 0.0) or 0.0)
            ask = float(getattr(tick, "ask", 0.0) or 0.0)
            if bid > 0 and ask > 0:
                return (bid + ask) / 2.0
            return bid or ask
    except Exception:
        pass
    return 0.0


def _derive_commission_per_lot(symbol: str, defaults: dict) -> tuple[float, bool]:
    """
    Empirically derive round-turn commission per 1.0 lot from closed deal history.

    symbol_info() does NOT expose commission on any broker (MT5 reports it
    per-deal only), so the only honest source is what we were actually charged.

    Method:
      * pull deals for this symbol over the last COMMISSION_HISTORY_DAYS
      * sum abs(commission) across ALL deals (entry and exit legs both)
      * divide by the total volume of the ENTRY (DEAL_ENTRY_IN) deals, which
        gives cost per lot for a complete round turn
      * if the history shows commission ONLY on entry deals, the broker books
        the whole charge per side at open, so what we measured is one side —
        double it to express a round turn

    Returns (per_lot, ok). ok=False means "use the asset-class default".
    """
    if mt5 is None:
        return float(defaults.get("commission_per_lot", 0.0)), False
    try:
        to_dt = datetime.now(timezone.utc)
        from_dt = to_dt - timedelta(days=COMMISSION_HISTORY_DAYS)
        deals = None
        try:
            deals = mt5.history_deals_get(from_dt, to_dt, group=f"*{symbol}*")
        except Exception:
            deals = None
        if not deals:
            try:
                deals = mt5.history_deals_get(from_dt, to_dt)
            except Exception:
                deals = None

        if not deals:
            return float(defaults.get("commission_per_lot", 0.0)), False

        entry_in = getattr(mt5, "DEAL_ENTRY_IN", 0)
        entry_out = getattr(mt5, "DEAL_ENTRY_OUT", 1)

        total_commission = 0.0
        in_volume = 0.0
        in_deals_with_comm = 0
        out_deals_with_comm = 0
        round_turns = 0

        for d in deals:
            try:
                d_symbol = getattr(d, "symbol", "") or ""
                if d_symbol.upper() != symbol.upper():
                    continue
                volume = float(getattr(d, "volume", 0.0) or 0.0)
                commission = abs(float(getattr(d, "commission", 0.0) or 0.0))
                d_entry = getattr(d, "entry", None)
                total_commission += commission
                if d_entry == entry_in:
                    in_volume += volume
                    round_turns += 1
                    if commission > 0:
                        in_deals_with_comm += 1
                elif d_entry == entry_out:
                    if commission > 0:
                        out_deals_with_comm += 1
            except Exception:
                continue

        if in_volume <= 0 or round_turns < _MIN_COMMISSION_DEALS:
            return float(defaults.get("commission_per_lot", 0.0)), False

        per_lot = total_commission / in_volume

        # Commission booked on entry only → we've measured a single side.
        if in_deals_with_comm > 0 and out_deals_with_comm == 0:
            per_lot *= 2.0

        if not (_COMMISSION_SANITY_MIN <= per_lot <= _COMMISSION_SANITY_MAX):
            logger.warning(
                f"[COSTS] {symbol}: derived commission ${per_lot:.2f}/lot is outside the "
                f"sanity band [{_COMMISSION_SANITY_MIN}, {_COMMISSION_SANITY_MAX}] — "
                f"using asset-class default instead."
            )
            return float(defaults.get("commission_per_lot", 0.0)), False

        # A genuine zero (index/crypto/synthetic CFD) is a valid, informative
        # answer — the broker really charges nothing.
        return round(per_lot, 4), True
    except Exception as e:
        logger.debug(f"[COSTS] {symbol}: commission history derivation failed: {e}")
        return float(defaults.get("commission_per_lot", 0.0)), False


def _clamp_spread(symbol: str, live_pips: float, defaults: dict) -> tuple[float, bool]:
    """
    Clamp a live spread reading into the asset class's sane band.

    symbol_info().spread is an instantaneous reading. Sampled during the 21:00-
    22:00 rollover, an off-session hour, or a news spike, USDCHF can print 40
    pips where its session average is 1.5-2.5. Feeding that straight into a
    backtest would model a cost the strategy never actually pays (or, on the low
    side, a 0-pip spread from a stale quote would model a cost it always pays).

    Returns (clamped_pips, was_clamped).
    """
    lo = float(defaults.get("spread_min_pips", 0.0))
    hi = float(defaults.get("spread_max_pips", float("inf")))
    if live_pips < lo:
        logger.warning(
            f"[COSTS] {symbol}: live spread {live_pips:.2f} pips below sane floor "
            f"{lo:.2f} (stale/off-session quote?) — clamped up to {lo:.2f}."
        )
        return lo, True
    if live_pips > hi:
        logger.warning(
            f"[COSTS] {symbol}: live spread {live_pips:.2f} pips above sane ceiling "
            f"{hi:.2f} (spike/off-session sample?) — clamped down to {hi:.2f}."
        )
        return hi, True
    return live_pips, False


# MT5 SYMBOL_SWAP_MODE values (MQL5 ENUM_SYMBOL_SWAP_MODE).
_SWAP_MODE_DISABLED = 0
_SWAP_MODE_POINTS = 1
_SWAP_MODE_CURRENCY_SYMBOL = 2
_SWAP_MODE_CURRENCY_MARGIN = 3
_SWAP_MODE_CURRENCY_DEPOSIT = 4
_SWAP_MODE_INTEREST_CURRENT = 5
_SWAP_MODE_INTEREST_OPEN = 6
_SWAP_MODE_REOPEN_CURRENT = 7
_SWAP_MODE_REOPEN_BID = 8

# A single day's financing should never approach a meaningful fraction of the
# position's notional value. 0.5%/day is already extreme (≈180%/yr); anything
# above it means we mis-parsed the units, so we fall back to the asset-class
# default rather than model a cost that would dominate every result.
MAX_DAILY_SWAP_PCT_OF_NOTIONAL = 0.5


def _swaps_to_usd_per_lot_per_day(
    info, symbol: str, swap_long: float, swap_short: float, defaults: dict
) -> tuple[float, float, bool]:
    """
    Convert MT5's raw swap_long/swap_short into USD per lot per day.

    MT5 reports swap in units that depend on `symbol_info().swap_mode`; the raw
    number is NOT a USD amount. Reading it literally produced a -$178.09/lot/day
    financing charge on XRPUSD, which at ~55 lots is ~$9,800/day and accounted
    for 81-164% of the entire loss on those runs.

    Conversions by mode:
      POINTS            swap is in points  -> points * point_size * contract_size
      CURRENCY_*        already a currency amount per lot -> used as-is
      INTEREST_*        annual interest %  -> notional * pct/100 / 360
      REOPEN_*/DISABLED position is re-opened rather than financed, or no swap.

    Returns (long_usd, short_usd, ok). `ok=False` means the value failed the
    plausibility check and the caller should keep the asset-class default.
    """
    mode = int(getattr(info, "swap_mode", _SWAP_MODE_CURRENCY_DEPOSIT) or 0)

    if mode in (_SWAP_MODE_DISABLED, _SWAP_MODE_REOPEN_CURRENT, _SWAP_MODE_REOPEN_BID):
        return 0.0, 0.0, True

    point = float(getattr(info, "point", 0.0) or 0.0)
    contract = float(getattr(info, "trade_contract_size", 0.0) or 0.0)
    price = _last_price(symbol) or 0.0
    notional = price * contract if (price > 0 and contract > 0) else 0.0

    if mode == _SWAP_MODE_POINTS:
        if point <= 0 or contract <= 0:
            return 0.0, 0.0, False
        factor = point * contract
        long_usd, short_usd = swap_long * factor, swap_short * factor
    elif mode in (_SWAP_MODE_INTEREST_CURRENT, _SWAP_MODE_INTEREST_OPEN):
        if notional <= 0:
            return 0.0, 0.0, False
        long_usd = notional * (swap_long / 100.0) / 360.0
        short_usd = notional * (swap_short / 100.0) / 360.0
    else:  # CURRENCY_SYMBOL / CURRENCY_MARGIN / CURRENCY_DEPOSIT
        # Already per lot per day in a currency. Non-USD account/symbol currencies
        # are not converted here — an FX conversion would need the account
        # currency and a live cross rate; the plausibility check below catches
        # anything wildly off.
        long_usd, short_usd = swap_long, swap_short

    # Plausibility check against notional.
    if notional > 0:
        cap = notional * (MAX_DAILY_SWAP_PCT_OF_NOTIONAL / 100.0)
        worst = max(abs(long_usd), abs(short_usd))
        if worst > cap:
            logger.warning(
                f"[COSTS] {symbol}: derived swap ${worst:,.2f}/lot/day exceeds "
                f"{MAX_DAILY_SWAP_PCT_OF_NOTIONAL}% of notional (${cap:,.2f}) using "
                f"swap_mode={mode} (raw {swap_long}/{swap_short}) — implausible, "
                f"falling back to the asset-class default."
            )
            return 0.0, 0.0, False

    return long_usd, short_usd, True


def _shape(d: dict, source: str) -> dict:
    """Project an internal working dict onto the exact public contract."""
    out = {
        "spread_pips": float(d.get("spread_pips", 0.0)),
        "commission_per_lot": float(d.get("commission_per_lot", 0.0)),
        "swap_long_per_lot_per_day": float(d.get("swap_long_per_lot_per_day", 0.0)),
        "swap_short_per_lot_per_day": float(d.get("swap_short_per_lot_per_day", 0.0)),
        "slippage_pips": float(d.get("slippage_pips", 0.0)),
        "stops_level_pips": float(d.get("stops_level_pips", 0.0)),
        "source": source,
    }
    # Costs are never negative (swaps are the exception — negative == a cost).
    for k in ("spread_pips", "commission_per_lot", "slippage_pips", "stops_level_pips"):
        if out[k] < 0:
            out[k] = 0.0
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def get_broker_costs(symbol: str, use_live_mt5: bool = True) -> dict:
    """
    Resolve the full transaction-cost profile for `symbol`.

    Priority: live MT5 terminal → asset-class defaults. Individual fields degrade
    independently: a symbol whose spread/swap/stops came from MT5 but whose
    commission had to fall back is reported as source="MT5_PARTIAL".

    Returns:
    {
      "spread_pips": float,                  # typical spread in PIPS (not points)
      "commission_per_lot": float,           # round-turn USD per 1.0 lot
      "swap_long_per_lot_per_day": float,    # USD, negative = cost
      "swap_short_per_lot_per_day": float,
      "slippage_pips": float,                # modelled execution slippage, PIPS
      "stops_level_pips": float,             # broker min stop distance, PIPS
      "source": str,                         # "MT5" | "MT5_PARTIAL" | "ASSET_CLASS_DEFAULT"
    }

    Never raises.
    """
    now = time.time()
    cache_key = (symbol, use_live_mt5)
    cached = _cost_cache.get(cache_key)
    if cached is not None and (now - cached[0]) < _COST_CACHE_TTL_SEC:
        return cached[1]

    try:
        defaults = _base_defaults(symbol)
    except Exception as e:
        logger.warning(f"[COSTS] {symbol}: default resolution failed ({e}) — using FOREX row.")
        defaults = dict(ASSET_CLASS_COST_DEFAULTS["FOREX"])

    result = None

    # ── 1. Live MT5 ───────────────────────────────────────────────────────────
    if _mt5_connected(use_live_mt5):
        try:
            try:
                mt5.symbol_select(symbol, True)
            except Exception:
                pass
            info = mt5.symbol_info(symbol)
            if info is not None:
                working = dict(defaults)
                degraded = False

                point = float(getattr(info, "point", 0.0) or 0.0)
                pip = _pip_size(symbol)
                # points → price → pips. If either conversion factor is missing
                # we cannot trust any point-denominated field from MT5.
                points_to_pips = (point / pip) if (point > 0 and pip > 0) else 0.0

                # Spread (points → pips, then clamped into the sane band).
                raw_spread_points = float(getattr(info, "spread", 0.0) or 0.0)
                if points_to_pips > 0 and raw_spread_points > 0:
                    live_spread_pips = raw_spread_points * points_to_pips
                    working["spread_pips"], _ = _clamp_spread(symbol, live_spread_pips, defaults)
                else:
                    degraded = True

                # Swaps: MT5 reports these in UNITS THAT DEPEND ON swap_mode, so the
                # raw value must never be used directly as "USD per lot per day".
                #
                # This was a real, severe bug: XRPUSD returned swap_long = -178.09,
                # which was taken literally. At a median ~55 lots that is ~$9,800 per
                # day of financing, and swap alone accounted for 81-164% of the entire
                # loss on the XRPUSD runs (est. $173k of a $214k loss on HTF FVG Flip;
                # $299k on Bias IFVG). The number is not a USD amount at all.
                swap_long = getattr(info, "swap_long", None)
                swap_short = getattr(info, "swap_short", None)
                if swap_long is not None and swap_short is not None:
                    sl_usd, ss_usd, swap_ok = _swaps_to_usd_per_lot_per_day(
                        info, symbol, float(swap_long), float(swap_short), defaults
                    )
                    if swap_ok:
                        working["swap_long_per_lot_per_day"] = sl_usd
                        working["swap_short_per_lot_per_day"] = ss_usd
                    else:
                        degraded = True
                else:
                    degraded = True

                # Broker minimum stop distance (points → pips).
                stops_points = float(getattr(info, "trade_stops_level", 0.0) or 0.0)
                if points_to_pips > 0:
                    live_stops_pips = stops_points * points_to_pips
                    # A broker reporting 0 means "no minimum"; keep the small
                    # asset-class floor in that case so the backtester still
                    # refuses physically-untradeable stops.
                    working["stops_level_pips"] = max(
                        live_stops_pips, float(defaults.get("stops_level_pips", 0.0))
                    )
                else:
                    degraded = True

                # Commission — history-derived, never from symbol_info.
                comm, comm_ok = _derive_commission_per_lot(symbol, defaults)
                working["commission_per_lot"] = comm
                if not comm_ok:
                    degraded = True

                # Slippage is never observable from MT5 (it is a property of our
                # own execution, not of the symbol spec) — always modelled.
                working["slippage_pips"] = float(defaults.get("slippage_pips", 0.0))

                result = _shape(working, "MT5_PARTIAL" if degraded else "MT5")
        except Exception as e:
            logger.warning(f"[COSTS] {symbol}: MT5 cost lookup failed ({e}) — falling back to defaults.")
            result = None

    # ── 2. Asset-class defaults ───────────────────────────────────────────────
    if result is None:
        result = _shape(defaults, "ASSET_CLASS_DEFAULT")

    _cost_cache[cache_key] = (now, result)
    logger.debug(
        f"[COSTS] {symbol}: spread={result['spread_pips']:.2f}p "
        f"comm=${result['commission_per_lot']:.2f}/lot "
        f"slip={result['slippage_pips']:.2f}p "
        f"stops={result['stops_level_pips']:.2f}p source={result['source']}"
    )
    return result


def get_slippage_pips(symbol: str, use_live_mt5: bool = True) -> float:
    """Convenience wrapper for callers that only need the modelled slippage."""
    try:
        return float(get_broker_costs(symbol, use_live_mt5=use_live_mt5)["slippage_pips"])
    except Exception:
        return 0.0


def clear_cost_cache() -> None:
    """Drop the cost cache (used by tests and after an MT5 reconnect)."""
    _cost_cache.clear()
