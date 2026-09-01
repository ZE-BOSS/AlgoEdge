"""
backend/backtester/engine.py

Bar-by-bar backtesting engine using the same RiskEngine as live.
Source: TradingBot_MasterPlan-2.md Section 11
Source: RiskManagement_Spec.md Section 7

Core behavior:
  - ALL TP positions open at entry (no deferred stacking)
  - When TP1 hits → move ALL sibling positions to break-even
  - Trades grouped by signal entry (group_id) for combined P&L
  - Entry/exit confirmation arrays, ISO timestamps, duration metrics
"""

import uuid
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from backend.analytics.reports import generate_risk_report
from backend.backtester.report import apply_bar_level_drawdown, apply_leg_level_hit_rates
from backend.risk.engine import RiskEngine
from backend.risk.multi_tp import TPLevel, _is_buy
from backend.risk.position_sizer import get_pip_size, calculate_risk_dollars
from backend.risk.prop_firm_validator import PropFirmValidator
from backend.utils.logger import get_logger
from backend.utils.timeutils import detect_session
from backend.utils.trade_grouper import group_trades

logger = get_logger(__name__)


def _to_epoch_seconds(val) -> float | None:
    """
    Robustly convert an epoch number, python/pandas datetime, or numpy scalar
    to epoch seconds (float).

    This matters because portfolio_engine.py's global timeline is built from
    `df['time'].values` / `df.index.values`, which yields numpy scalars
    (np.int64, np.datetime64, ...) rather than plain python int/float.
    np.int64 does NOT satisfy `isinstance(x, int)` on most platforms and has
    no `.timestamp()` method, so the old int/float-only check silently fell
    through and returned 0 / a raw numeric string for every such value.
    """
    if val is None:
        return None
    if isinstance(val, (int, float, np.integer, np.floating)):
        return float(val)
    if isinstance(val, datetime):
        return val.timestamp()
    if hasattr(val, "timestamp"):
        try:
            return float(val.timestamp())
        except Exception:
            pass
    try:
        return pd.Timestamp(val).timestamp()
    except Exception:
        return None


def _epoch_to_iso(epoch_val) -> str:
    """Convert epoch timestamp (incl. numpy scalars) or datetime to ISO string."""
    if isinstance(epoch_val, str):
        return epoch_val
    secs = _to_epoch_seconds(epoch_val)
    if secs is None:
        return str(epoch_val)
    try:
        return datetime.fromtimestamp(secs, tz=timezone.utc).isoformat()
    except Exception:
        return str(epoch_val)


def _calc_duration_minutes(entry_time, exit_time) -> float:
    """Calculate trade duration in minutes, robust to numpy scalar / datetime / epoch inputs."""
    e = _to_epoch_seconds(entry_time)
    x = _to_epoch_seconds(exit_time)
    if e is None or x is None:
        return 0.0
    return (x - e) / 60.0


def _validate_position(direction: str, entry_price: float, stop_loss: float, take_profit: float) -> tuple:
    """
    Validate that SL/TP are on the correct side of entry for the given direction.
    Returns (is_valid, error_message).
    """
    if _is_buy(direction):
        if stop_loss >= entry_price:
            return False, f"BUY SL ({stop_loss:.5f}) must be below entry ({entry_price:.5f})"
        if take_profit <= entry_price:
            return False, f"BUY TP ({take_profit:.5f}) must be above entry ({entry_price:.5f})"
    else:
        if stop_loss <= entry_price:
            return False, f"SELL SL ({stop_loss:.5f}) must be above entry ({entry_price:.5f})"
        if take_profit >= entry_price:
            return False, f"SELL TP ({take_profit:.5f}) must be below entry ({entry_price:.5f})"
    return True, ""


def _breakeven_stop(
    direction: str,
    entry_price: float,
    current_price: float,
    pip_size: float,
    atr: float,
    risk_config: dict,
    spread_pips: float = 0.0,
) -> float:
    """
    Compute the break-even stop, clamped so it can never sit beyond the market.

    Why the clamp exists (this was a P&L-FABRICATING BUG)
    -----------------------------------------------------
    The old code was simply `entry_price +/- be_buffer_pips * pip_size`, with no
    reference to where price actually was. Whenever
        be_buffer_pips > be_trigger_rr * stop_pips
    the resulting "break-even" stop landed ABOVE the market (BUY). On the next bar
    the gap handler saw `open <= stop_loss`, treated it as a gapped stop, and filled
    it — booking a PROFIT on what was supposed to be a scratch exit.

    Measured impact on the real runs, before this fix:
      APA old  — 61/61 BE_SL legs exited at EXACTLY +1.00R; BE P&L $16,167 = 102%
                 of the entire strategy's reported profit.
      VWAP old — 429/429 legs at EXACTLY +1.00R; $128,101 = 137% of reported profit.
    Removing the artifact flips every previously-"profitable" old run negative
    (APA +15,888 -> -279; VWAP +93,725 -> -34,375). A real break-even stop returns
    ~0R minus costs and has variance; exiting at exactly +1.00R with zero variance
    every single time is not a market outcome.

    The bug armed itself precisely when stops were unhealthily tight, i.e. hardest
    on the strategies whose numbers were least trustworthy.

    [4.5/D6/F4] Buffer sizing now calls the SAME resolve_be_buffer() function
    used by BreakevenManager.check_breakeven() and position_manager.py's live
    BE blocks — previously this was a fourth, independently-drifting
    max(pip, atr, spread*1x) implementation (no be_spread_multiple term at
    all), which is exactly the kind of divergence Rule-5 exists to catch.
    """
    from backend.risk.breakeven_manager import resolve_be_buffer

    is_buy = _is_buy(direction)

    spread_price = float(spread_pips or 0.0) * pip_size
    buffer = resolve_be_buffer(
        spread_price=spread_price,
        atr=atr,
        pip_size=pip_size,
        be_buffer_pips=float(risk_config.get("be_buffer_pips", 0.0) or 0.0),
        be_buffer_atr_mult=float(risk_config.get("be_buffer_atr_mult", 0.0) or 0.0),
        be_spread_multiple=float(risk_config.get("be_spread_multiple", 2.0) or 0.0),
    )

    new_sl = entry_price + buffer if is_buy else entry_price - buffer

    # ── THE GUARD ──
    # Never let the stop cross the market. Keep it at least one spread (or one pip,
    # whichever is larger) away on the near side, so it cannot be filled on the very
    # next bar as a phantom "gap". This is an unconditional clamp — it makes the
    # fabrication unreachable no matter how be_buffer_pips is configured.
    #
    # If price has not yet travelled far enough for even a flat break-even to sit
    # safely behind the market, the clamp returns a level below entry (BUY). That is
    # intentional and safe: the caller only adopts the new stop when it is an
    # improvement on the existing one, so a too-close level is simply ignored.
    # Deliberately uses the RAW (1x) spread, not the be_spread_multiple-scaled
    # `buffer` above — this is a "don't get gap-filled this bar" floor, a
    # different concern from the BE cushion's own size.
    keep_away = max(spread_price, pip_size)
    if is_buy:
        new_sl = min(new_sl, current_price - keep_away)
    else:
        new_sl = max(new_sl, current_price + keep_away)

    return new_sl


def validate_at_fill_price(
    direction: str,
    fill_price: float,
    stop_loss: float,
    take_profit: float,
    symbol: str,
    stops_level_pips: float = 0.0,
    spread_pips: float = 0.0,
) -> tuple[bool, str, str]:
    """
    Re-validate SL/TP against the ACTUAL FILL price (Task 1 — critical).

    Returns (is_valid, rejection_key, detail_message).

    Background
    ----------
    The risk engine validates SL-vs-entry using the strategy's *theoretical*
    signal entry price, but positions fill at the NEXT BAR'S OPEN (realistic,
    and deliberately kept). Nothing re-checked the geometry once the real fill
    price was known. When a bar's open gaps across the stop, the position opened
    with its stop on the WRONG SIDE of entry, `abs(entry - sl)` collapsed to a
    fraction of a pip, and position sizing exploded inversely.

    Observed: BUY USDCHF, signal entry 0.76806 / SL 0.76798 (valid), actual fill
    0.76796 -> SL now ABOVE entry, risk distance 0.21 pips, sized 25.38 lots,
    -11.371R (-$760.66) in one bar. Frequency 8/43 APA groups (18.6%) and
    3/69 NY Open Retest groups.

    Crucially, LIVE TRADING ALREADY REJECTS THESE — backend/mt5/order_manager.py
    runs the same pre-execution check and returns {"stale": True} when the SL is
    already hit or sits inside the broker's stop level. Without this function the
    backtest books catastrophic losses that live trading structurally cannot
    incur, i.e. a pure backtest-vs-live divergence in the pessimistic direction.

    Checks (mirroring order_manager.py's stale-signal guard):
      1. SL strictly below (BUY) / above (SELL) the fill; TP the other side.
      2. SL distance from fill >= max(broker stops_level, ~2x spread). The 2x
         spread floor mirrors the sizing guard in the risk layer: a stop closer
         than the round-trip spread is not a tradeable stop at any size.
      3. Same minimum distance for TP (the broker enforces stops_level on both).
    """
    pip_size = get_pip_size(symbol)
    is_buy = _is_buy(direction)

    # ── 1. Side check against the real fill ──
    if is_buy:
        if stop_loss >= fill_price:
            return False, "stale_at_fill", (
                f"BUY SL ({stop_loss:.5f}) is at/above the actual fill ({fill_price:.5f}) — "
                f"the bar's open gapped across the stop; live would reject this as a stale signal"
            )
        if take_profit <= fill_price:
            return False, "stale_at_fill", (
                f"BUY TP ({take_profit:.5f}) is at/below the actual fill ({fill_price:.5f}) — "
                f"target already reached at fill; live would reject this as a stale signal"
            )
    else:
        if stop_loss <= fill_price:
            return False, "stale_at_fill", (
                f"SELL SL ({stop_loss:.5f}) is at/below the actual fill ({fill_price:.5f}) — "
                f"the bar's open gapped across the stop; live would reject this as a stale signal"
            )
        if take_profit >= fill_price:
            return False, "stale_at_fill", (
                f"SELL TP ({take_profit:.5f}) is at/above the actual fill ({fill_price:.5f}) — "
                f"target already reached at fill; live would reject this as a stale signal"
            )

    # ── 2/3. Minimum viable distance ──
    if pip_size <= 0:
        return True, "", ""

    min_pips = max(float(stops_level_pips or 0.0), 2.0 * float(spread_pips or 0.0))
    if min_pips <= 0:
        return True, "", ""

    min_distance = min_pips * pip_size
    sl_distance = abs(fill_price - stop_loss)
    if sl_distance < min_distance:
        return False, "sub_minimum_stop", (
            f"SL distance {sl_distance / pip_size:.2f} pips at fill {fill_price:.5f} is below the "
            f"minimum viable {min_pips:.2f} pips "
            f"(stops_level={stops_level_pips:.2f}p, 2x spread={2.0 * spread_pips:.2f}p) — "
            f"live would reject; sizing against this distance would explode lot size"
        )

    tp_distance = abs(take_profit - fill_price)
    if tp_distance < min_distance:
        return False, "sub_minimum_tp", (
            f"TP distance {tp_distance / pip_size:.2f} pips at fill {fill_price:.5f} is below the "
            f"broker minimum {min_pips:.2f} pips"
        )

    return True, "", ""


def _resolve_sl_tp_hit(
    direction: str,
    open_p: float,
    high: float,
    low: float,
    stop_loss: float,
    take_profit: float,
    simulate_wicks: bool = True,
) -> tuple:
    """
    Determine whether a position's SL and/or TP was touched on this bar, and
    resolve same-bar SL+TP ambiguity via the OHLC shadow-weighted path model.

    Conservative worst-case bias (item 3.3): SL wins the tie-break if EITHER
    the shadow-length heuristic OR the distance-from-open heuristic favors it
    (not requiring both to agree). This is shared by the main close-evaluation
    loop AND the TP1 pre-pass (item 3.7) so both use the identical resolved
    result instead of two independently-drifting implementations.
    """
    if _is_buy(direction):
        sl_hit = low <= stop_loss
        tp_hit = high >= take_profit
    else:
        sl_hit = high >= stop_loss
        tp_hit = low <= take_profit

    if sl_hit and tp_hit:
        if simulate_wicks:
            if _is_buy(direction):
                sl_shadow = open_p - low    # downward shadow towards SL
                tp_shadow = high - open_p   # upward shadow towards TP
            else:
                sl_shadow = high - open_p   # upward shadow towards SL
                tp_shadow = open_p - low    # downward shadow towards TP
            dist_to_sl = abs(stop_loss - open_p)
            dist_to_tp = abs(take_profit - open_p)
            # Item 3.3: SL wins if EITHER heuristic favors it (conservative).
            sl_wins = (sl_shadow >= tp_shadow) or (dist_to_sl <= dist_to_tp)
        else:
            # Fallback: distance-from-open tie-breaker only.
            dist_to_sl = abs(stop_loss - open_p)
            dist_to_tp = abs(take_profit - open_p)
            sl_wins = dist_to_sl <= dist_to_tp
        if sl_wins:
            tp_hit = False
        else:
            sl_hit = False

    return sl_hit, tp_hit


# ──────────────────────────────────────────────────────────────────────────
#  Transaction-cost resolution (Task 2)
# ──────────────────────────────────────────────────────────────────────────
#
# The cost machinery (slippage / spread / commission, and now swap) has always
# been applied correctly in _calc_pnl — the defect was purely that every knob
# defaulted to 0.0, so every historical run was executed with ZERO transaction
# costs and therefore overstated every strategy's edge.
#
# Resolution order for each cost parameter:
#   1. An explicit user-supplied numeric value in risk_config  -> ALWAYS wins.
#   2. Otherwise ("auto", or the key absent/None)              -> broker-sourced
#      value from backend.risk.broker_costs.get_broker_costs(), which itself
#      falls back from live MT5 to asset-class averages.
#   3. Otherwise (broker_costs unavailable)                    -> 0.0, logged.
#
# This is a DEFAULT-RESOLUTION change, never an override: a user who explicitly
# configures 0.0 still gets 0.0, which keeps every previously-saved
# params_snapshot replaying identically.

# Values that mean "not explicitly configured — source this from the broker".
_COST_AUTO_TOKENS = {"", "auto", "default", "broker", "mt5", "none"}

_COST_FIELDS = (
    "slippage_pips",
    "commission_per_lot",
    "spread_pips",
    "swap_long_per_lot_per_day",
    "swap_short_per_lot_per_day",
    "stops_level_pips",
)

_BROKER_COST_FALLBACK = {
    "spread_pips": 0.0,
    "commission_per_lot": 0.0,
    "swap_long_per_lot_per_day": 0.0,
    "swap_short_per_lot_per_day": 0.0,
    "slippage_pips": 0.0,
    "stops_level_pips": 0.0,
    "source": "UNAVAILABLE",
}

_BROKER_COST_CACHE: dict[str, dict] = {}


def _cost_is_unset(value) -> bool:
    """True when a risk_config cost entry means 'resolve this from the broker'."""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in _COST_AUTO_TOKENS
    return False


def fetch_broker_costs(symbol: str, use_live_mt5: bool = True) -> dict:
    """
    Fetch broker cost data for `symbol`, cached per-symbol for the process.

    The import is deliberately LAZY and the call is fully guarded: broker_costs
    is a newer module and must never be able to break import of the backtest
    engine (or introduce a circular import through backend.risk.*). If it is
    missing or raises, every field degrades to 0.0 with source="UNAVAILABLE",
    which reproduces the old zero-cost behaviour rather than crashing a run.
    """
    key = (symbol or "").upper()
    cached = _BROKER_COST_CACHE.get(key)
    if cached is not None:
        return cached

    resolved = dict(_BROKER_COST_FALLBACK)
    try:
        from backend.risk.broker_costs import get_broker_costs  # lazy: see docstring
        data = get_broker_costs(symbol, use_live_mt5=use_live_mt5) or {}
        for field in _BROKER_COST_FALLBACK:
            if field == "source":
                continue
            try:
                resolved[field] = float(data.get(field, 0.0) or 0.0)
            except (TypeError, ValueError):
                resolved[field] = 0.0
        resolved["source"] = str(data.get("source", "UNKNOWN"))
    except Exception as e:
        logger.warning(
            f"[COSTS] broker_costs unavailable for {symbol!r} ({type(e).__name__}: {e}) — "
            f"falling back to zero transaction costs for any parameter the user did not set explicitly."
        )

    _BROKER_COST_CACHE[key] = resolved
    return resolved


def resolve_effective_costs(symbol: str, risk_config: dict[str, Any]) -> dict[str, Any]:
    """
    Merge explicit user cost settings over broker-sourced defaults for `symbol`.

    Returns the resolved numeric values plus provenance:
        {..., "sources": {field: "USER"|"MT5"|"ASSET_CLASS_DEFAULT"|...},
              "broker_source": str, "symbol": str}
    """
    broker = fetch_broker_costs(symbol)
    broker_source = broker.get("source", "UNAVAILABLE")

    out: dict[str, Any] = {}
    sources: dict[str, str] = {}
    for field in _COST_FIELDS:
        raw = risk_config.get(field, None)
        if _cost_is_unset(raw):
            out[field] = float(broker.get(field, 0.0) or 0.0)
            sources[field] = broker_source
            continue
        try:
            out[field] = float(raw)
            sources[field] = "USER"
        except (TypeError, ValueError):
            out[field] = float(broker.get(field, 0.0) or 0.0)
            sources[field] = broker_source

    # [2.9/A6] exit_slippage_pips: adverse slippage applied ONLY on
    # SL/BE_SL/TRAIL_SL/SESSION_END/TIME_LIMIT exits, never TP. Not a broker-cost
    # field (no broker_costs.py entry) — defaults to the resolved entry-side
    # slippage_pips unless the user sets it explicitly.
    raw_exit_slip = risk_config.get("exit_slippage_pips", None)
    if _cost_is_unset(raw_exit_slip):
        out["exit_slippage_pips"] = out["slippage_pips"]
        sources["exit_slippage_pips"] = "DEFAULT_EQUALS_SLIPPAGE_PIPS"
    else:
        try:
            out["exit_slippage_pips"] = float(raw_exit_slip)
            sources["exit_slippage_pips"] = "USER"
        except (TypeError, ValueError):
            out["exit_slippage_pips"] = out["slippage_pips"]
            sources["exit_slippage_pips"] = "DEFAULT_EQUALS_SLIPPAGE_PIPS"

    out["sources"] = sources
    out["broker_source"] = broker_source
    out["symbol"] = symbol
    return out


class CostModelMixin:
    """
    Per-symbol cost resolution + swap/rollover financing, shared by
    BacktestEngine and PortfolioBacktestEngine so both price trades identically.

    Requires the host class to define `self.risk_config` and to call
    `self._init_cost_model()` from __init__.
    """

    # [18.2] Backtest-only exits that LIVE CANNOT PRODUCE.
    #
    # The engine can close a position with SESSION_END or TIME_LIMIT. Nothing in
    # backend/services/ implements either, so live simply holds those positions.
    # Measured on the 7,463-trade sweep: 925 trades (12.4%) exited this way,
    # carrying $36,076 of booked profit at averages of +0.471R (SESSION_END) and
    # +1.855R (TIME_LIMIT) — far better than the book's -0.180R. The backtest was
    # therefore systematically optimistic, and concentrated in its best non-TP
    # exits.
    #
    # Default False so a backtest describes what live will actually do. Set
    # `simulate_backtest_only_exits=True` to restore the old behaviour, and
    # expect the result to be unreachable in live until live implements them.
    @property
    def _backtest_only_exits(self) -> bool:
        return bool(self.risk_config.get("simulate_backtest_only_exits", False))

    def _init_cost_model(self) -> None:
        self._cost_cache: dict[str, dict[str, Any]] = {}
        # Snapshot of everything actually used, surfaced in the run results so a
        # saved backtest records the cost assumptions it was produced under.
        self.cost_model: dict[str, Any] = {}

    def _costs_for(self, symbol: str) -> dict[str, Any]:
        """Resolved cost parameters for `symbol` (cached; logged on first use)."""
        key = (symbol or "").upper()
        cached = self._cost_cache.get(key)
        if cached is not None:
            return cached

        resolved = resolve_effective_costs(symbol, self.risk_config)
        self._cost_cache[key] = resolved
        self.cost_model[key or "<default>"] = {
            k: resolved[k] for k in (*_COST_FIELDS, "exit_slippage_pips", "sources", "broker_source")
        }
        logger.info(
            f"[COSTS] {symbol or '<none>'} | spread={resolved['spread_pips']:.2f}p "
            f"slippage={resolved['slippage_pips']:.2f}p "
            f"commission=${resolved['commission_per_lot']:.2f}/lot "
            f"swap_long={resolved['swap_long_per_lot_per_day']:.4f}/lot/day "
            f"swap_short={resolved['swap_short_per_lot_per_day']:.4f}/lot/day "
            f"stops_level={resolved['stops_level_pips']:.2f}p "
            f"| broker_source={resolved['broker_source']} | per-field={resolved['sources']}"
        )
        return resolved

    def _swap_cost(
        self,
        direction: str,
        volume: float,
        symbol: str,
        entry_time: Any,
        exit_time: Any,
    ) -> float:
        """
        Signed overnight financing for a position held across rollovers (Task 3).

        Returns a value that is ADDED to PnL. `swap_*_per_lot_per_day` follows the
        MT5 sign convention (negative = a charge, positive = a credit), so the
        result is `rate * volume * weighted_days`.

        Rollover accounting:
          - One rollover is charged per calendar-day boundary crossed.
          - Crossing INTO Thursday is charged 3x — the Wednesday rollover carries
            the weekend's value date, the standard FX convention.
          - Crossing into Saturday/Sunday is charged 0x — the market is closed and
            no rollover occurs.

        SIMPLIFICATION (documented deliberately): the boundary used is UTC
        midnight rather than the broker's true 17:00 America/New_York rollover
        instant. Those differ by ~4-5 hours, so a hold that straddles that window
        can be off by exactly one rollover. Over the multi-day holds this exists
        to model (median 6.9 days for VWAP) the error is <15%, and using UTC
        avoids a per-bar timezone conversion in the hot close path.
        """
        if not volume:
            return 0.0
        costs = self._costs_for(symbol)
        rate = (
            costs["swap_long_per_lot_per_day"] if _is_buy(direction)
            else costs["swap_short_per_lot_per_day"]
        )
        if not rate:
            return 0.0

        start = _to_epoch_seconds(entry_time)
        end = _to_epoch_seconds(exit_time)
        if start is None or end is None or end <= start:
            return 0.0

        day = 86400.0
        first_boundary = (int(start // day) + 1) * day
        if first_boundary > end:
            return 0.0

        weighted_days = 0.0
        boundary = first_boundary
        # Cap the walk so a corrupt timestamp can't spin for millions of iterations.
        max_rollovers = 5000
        count = 0
        while boundary <= end and count < max_rollovers:
            # weekday of the day being rolled INTO (Mon=0 ... Sun=6)
            weekday = datetime.fromtimestamp(boundary, tz=timezone.utc).weekday()
            if weekday in (5, 6):        # Sat / Sun — market closed, no rollover
                pass
            elif weekday == 3:           # into Thursday — Wednesday triple swap
                weighted_days += 3.0
            else:
                weighted_days += 1.0
            boundary += day
            count += 1

        if weighted_days <= 0:
            return 0.0
        return rate * volume * weighted_days


def _gap_adjusted_fill_price(direction: str, open_p: float, level: float, symbol: str, slippage_pips: float) -> float:
    """
    Item 3.4: if a bar's open has already gapped past an SL/TP level before the
    level itself would be "hit" within the bar, a real fill cannot occur at the
    untouched level price — the realistic fill is the bar's open price,
    optionally shifted further against the trader by the configured slippage
    (mirrors how slippage is already applied to entries in _calc_pnl).
    """
    price = open_p
    pip_size = get_pip_size(symbol)
    if slippage_pips > 0 and pip_size > 0:
        slip = slippage_pips * pip_size
        if _is_buy(direction):
            price -= slip  # closing a BUY: worse = lower fill
        else:
            price += slip  # closing a SELL: worse = higher fill
    return price


def _apply_exit_slippage(direction: str, price: float, symbol: str, exit_slippage_pips: float) -> float:
    """
    [2.9/A6] Shift a non-gapped adverse-exit fill price (SL, BE_SL, TRAIL_SL,
    SESSION_END, TIME_LIMIT) against the trade direction by exit_slippage_pips.
    Never applied to TP fills — a limit order does not slip against you.
    Gapped exits already get slippage via _gap_adjusted_fill_price and are not
    passed through this function.
    """
    if exit_slippage_pips <= 0:
        return price
    pip_size = get_pip_size(symbol)
    if pip_size <= 0:
        return price
    slip = exit_slippage_pips * pip_size
    return price - slip if _is_buy(direction) else price + slip


class BacktestEngine(CostModelMixin):
    """
    Backtesting engine that uses the identical RiskEngine as live trading.
    What you backtest = what runs live.
    """

    def __init__(self, risk_config: dict[str, Any]):
        self.risk_config = risk_config.copy()
        self.risk_config["is_backtest"] = True  # CRITICAL: Prevent loading live bot state from disk
        
        self.risk_engine = RiskEngine(self.risk_config)
        # Mark as backtesting for informational purposes / future guards.
        self.risk_engine.is_backtesting = True
        prop_firm_config = self.risk_config.get("prop_firm", {})
        if isinstance(prop_firm_config, dict):
            prop_firm_config = prop_firm_config.copy()
            prop_firm_config["is_backtesting"] = True
        else:
            setattr(prop_firm_config, "is_backtesting", True)
            
        self.prop_firm_validator = PropFirmValidator(prop_firm_config)
        self.risk_engine.prop_firm_validator = self.prop_firm_validator
        self.trades: list[dict[str, Any]] = []
        self.open_positions: list[dict[str, Any]] = []
        self.equity_curve: list[float] = []
        self.invalid_signals: int = 0
        # [2.17] bar index of the last opened entry per symbol, used to enforce
        # min_bars_between_entries when allow_pyramiding is on.
        self._last_entry_bar_by_symbol: dict[str, int] = {}
        self.rejection_funnel: dict[str, Any] = {
            "total_evaluated": 0,
            "strategy_rejections": {},
            "risk_rejections": {},
            # Task 1: signals that passed every pre-trade gate but failed
            # re-validation against the ACTUAL FILL price (next-bar open).
            # Mirrors live's stale-signal rejection in mt5/order_manager.py.
            "fill_rejections": {},
            "errors": 0,
            "approved": 0
        }
        # [I4] Every signal that did NOT become a trade, with the gate that
        # stopped it — the funnel above has counts per gate; this has the actual
        # signals, so "was there a setup on that day" is answerable without
        # re-running. Capped so a hard-filtered run can't balloon the response.
        self.blocked_signals: list[dict[str, Any]] = []
        self._blocked_signals_cap = 500
        self.run_logs = []
        # ── Simulation costs (Task 2) ──
        # Resolved lazily PER SYMBOL via CostModelMixin._costs_for(), so an unset
        # parameter picks up live-MT5 / asset-class broker data instead of the old
        # silent 0.0. Explicit user values still win. See resolve_effective_costs().
        self._init_cost_model()
        # Pin instrument data for the whole run so position sizing (at entry) and
        # _calc_pnl (at exit) can never resolve against different sources. Without
        # this, get_symbol_info()'s 60-second wall-clock TTL expires mid-backtest and
        # a connectivity flicker silently changes tick_value/tick_size between the two
        # — observed producing a 160x spread in implied instrument value within a
        # single XRPUSD run, and making backtests non-reproducible.
        from backend.risk.position_sizer import freeze_symbol_info
        freeze_symbol_info()
        # Wick simulation: use OHLC shadow-weighted path model for same-bar SL+TP resolution
        self._simulate_wicks = bool(risk_config.get("simulate_wicks", True))
        # Strategy attribution for saved trades (Task 6): the sig dicts built by
        # the API route carry no strategy_id, so fall back to the run config.
        self._strategy_id = str(
            risk_config.get("strategy_id")
            or risk_config.get("strategy_name")
            or "UNKNOWN"
        )

    def run(
        self,
        candles: pd.DataFrame,
        signals: list[dict[str, Any]],
        initial_balance: float = 10000.0,
        candles_m15: pd.DataFrame = None,
        candles_m5: pd.DataFrame = None,
        strategy: Any = None,
        progress_cb: Any = None,
        # [merge] H1 context for the trade viewer's higher-timeframe pane
        # (bug B3). origin/dev never carried this parameter; dropping it would
        # silently reintroduce that bug. Sits AFTER progress_cb so the runner's
        # positional call binds correctly.
        candles_h1: pd.DataFrame = None,
    ) -> dict[str, Any]:
        """Run a backtest on historical candles with pre-generated signals.

        Args:
            strategy: Optional strategy instance. When supplied, its
                `on_position_bar` hook is called once per closed bar per open
                position, enabling strategy-side in-trade invalidation logic
                (e.g. APA's head-level hard exit).
            progress_cb: Optional `callable(fraction: float)` invoked
                periodically with this run's 0.0-1.0 completion. This method is
                executed under `asyncio.to_thread`, so the callback MUST NOT
                await or perform I/O — see `services/backtest_progress.py`,
                whose `_Phase.note` is written to be exactly that. Without it
                the simulation — the longest part of a run — reported nothing
                at all and the bar sat still from 95% to 100%.
        """
        self._strategy = strategy  # stored so close-path notify_outcome can call it
        balance = initial_balance
        self.trades = []
        self.open_positions = []
        self.equity_curve = [balance]
        self.invalid_signals = 0
        self.rejection_funnel = {
            "total_evaluated": 0,
            "strategy_rejections": {},
            "risk_rejections": {},
            # Task 1: signals that passed every pre-trade gate but failed
            # re-validation against the ACTUAL FILL price (next-bar open).
            # Mirrors live's stale-signal rejection in mt5/order_manager.py.
            "fill_rejections": {},
            "errors": 0,
            "approved": 0
        }
        self.blocked_signals = []

        logger.info("[ENGINE] ═══ Starting backtest engine ═══")
        logger.info(f"[ENGINE] Balance: ${initial_balance} | Signals: {len(signals)} | Candles: {len(candles)}")
        logger.info(f"[ENGINE] Risk config: risk_pct={self.risk_config.get('risk_per_trade_pct')}% | min_rr={self.risk_config.get('min_rr')} | tp_count={self.risk_config.get('tp_count')}")

        # ── Task 2: resolve transaction costs up-front so the run log states, at
        # the top, which costs were used and where each came from. _costs_for()
        # caches per symbol and logs on first resolution.
        for _sym in {s.get("symbol", "") for s in signals} or {""}:
            self._costs_for(_sym)

        # Reset prop firm state for a fresh backtest run
        self.prop_firm_validator.is_breached = False
        self.prop_firm_validator.breach_reason = ""
        self.prop_firm_validator._breach_logged = False
        self.prop_firm_validator._alerts_sent = set()


        signal_idx = 0

        # ── Pre-compute time arrays to avoid pd.to_datetime in the loop ──
        if 'time' in candles.columns:
            time_series = candles['time']
        else:
            time_series = pd.Series(candles.index)
            
        if pd.api.types.is_datetime64_any_dtype(time_series):
            time_arr = time_series.astype('int64').values / 10**9
        else:
            try:
                time_arr = time_series.astype(float).values
            except Exception:
                time_arr = pd.to_datetime(time_series).astype('int64').values / 10**9
                
        dt_series = pd.to_datetime(time_series, unit='s' if not pd.api.types.is_datetime64_any_dtype(time_series) else None, utc=True)
        dt_arr = dt_series.dt.to_pydatetime()
        
        # ── Pre-compute OHLC arrays (vectorized, O(n)) ──
        opens_arr = candles["open"].values.astype(float)
        highs_arr = candles["high"].values.astype(float)
        lows_arr = candles["low"].values.astype(float)
        closes_arr = candles["close"].values.astype(float)
        atr_period = 14

        prev_closes = np.roll(closes_arr, 1)
        prev_closes[0] = closes_arr[0]
        tr_all = np.maximum(
            highs_arr - lows_arr,
            np.maximum(np.abs(highs_arr - prev_closes), np.abs(lows_arr - prev_closes))
        )
        # Rolling mean ATR
        atr_array = np.zeros(len(candles))
        for i in range(atr_period, len(candles)):
            atr_array[i] = np.mean(tr_all[i - atr_period:i])

        # ── Pre-compute swing point cache ──
        sw_len = self.risk_config.get("trail_structure_bars", self.risk_config.get("swing_length", 5))
        swing_lookback = 20
        swing_cache = {}
        for i in range(swing_lookback, len(candles)):
            points = []
            for j in range(max(sw_len, i - swing_lookback), i - sw_len):
                if j - sw_len < 0:
                    continue
                window_h = highs_arr[j - sw_len:j + sw_len + 1]
                window_l = lows_arr[j - sw_len:j + sw_len + 1]
                if highs_arr[j] == window_h.max():
                    points.append({"type": "HIGH", "price": float(highs_arr[j])})
                if lows_arr[j] == window_l.min():
                    points.append({"type": "LOW", "price": float(lows_arr[j])})
            if points:
                swing_cache[i] = points


        # Progress is reported on a bar-count stride rather than elapsed time
        # because this loop cannot call time.monotonic() cheaply enough per bar
        # to be worth it; ~200 notes across a run is smooth and the stride
        # adapts to run length. The callback only assigns a float.
        _total_bars = len(candles)
        _progress_stride = max(1, _total_bars // 200)

        for i in range(_total_bars):
            if progress_cb is not None and i % _progress_stride == 0:
                try:
                    progress_cb(i / _total_bars)
                except Exception:
                    progress_cb = None  # a broken reporter must never stop a run

            current_time = time_arr[i]
            current_time_dt = dt_arr[i]
            current_price = closes_arr[i]
            high = highs_arr[i]
            low = lows_arr[i]
            open_p = opens_arr[i]

            # Look up pre-computed ATR and swing points
            current_atr = atr_array[i] if i < len(atr_array) else 0.0
            swing_points = swing_cache.get(i, [])

            # Calculate floating equity for Prop Firm tracking
            open_pnl = sum(self._calc_pnl(p["direction"], p["entry_price"], current_price, p["volume"], p.get("symbol", "")) for p in self.open_positions)
            self.prop_firm_validator.update_equity_balance(balance + open_pnl, balance, current_time_dt)
            if self.prop_firm_validator.is_breached and not getattr(self.prop_firm_validator, '_breach_logged', False):
                logger.warning(f"[PROP FIRM MONITOR] Drawdown breach detected: {self.prop_firm_validator.breach_reason} — continuing backtest (informational only)")
                self.prop_firm_validator._breach_logged = True  # log once, don't spam

            # 1. Manage existing open positions
            closed_this_bar = []
            tp1_hit_groups = set()  # Track which groups had TP1 hit this bar

            # Pre-pass: determine which groups will have TP1 close THIS bar so
            # that siblings (TP2/TP3) can defer their own SL/TP check to the
            # next bar. Without this, a TP2 position can be evaluated and closed
            # at its original SL/TP price on the SAME bar TP1 closes — BEFORE
            # the tp1_hit_groups BE block has moved the SL to entry+buffer.
            _tp1_closing_this_bar: set = set()
            for _p in self.open_positions:
                if _p.get("tp_level") != 1:
                    continue
                # Item 3.7: reuse the same ambiguity-resolved tp_hit determination
                # as the real close logic below, instead of a naive TP-touch-only
                # check that ignores whether the same bar's SL also fired and
                # which side the tie-break would actually pick.
                _sl_hit_pp, _tp_hit_pp = _resolve_sl_tp_hit(
                    _p["direction"], open_p, high, low,
                    _p["stop_loss"], _p["take_profit"], self._simulate_wicks,
                )
                if _tp_hit_pp:
                    _tp1_closing_this_bar.add(_p.get("group_id"))

            for pos in self.open_positions[:]:
                # Update highest/lowest price tracking for trailing
                if pos["direction"] == "BUY":
                    pos["highest_price"] = max(pos.get("highest_price", pos["entry_price"]), high)
                else:
                    pos["lowest_price"] = min(pos.get("lowest_price", pos["entry_price"]), low)

                # Check Max Holding Time (48 hours for Forex, 400 bars for CrashBoom)
                c_ts = current_time
                e_ts = pos["entry_time"].timestamp() if hasattr(pos.get("entry_time"), "timestamp") else pos.get("entry_time")
                
                pos["bars_held"] = pos.get("bars_held", 0) + 1
                symbol_upper = pos.get("symbol", "").upper()
                is_crashboom = "CRASH" in symbol_upper or "BOOM" in symbol_upper
                
                limit_hit = False
                if (self._backtest_only_exits
                        and is_crashboom and pos["bars_held"] >= 400):
                    limit_hit = True
                        
                if limit_hit:
                    pos["exit_price"] = _apply_exit_slippage(
                        pos["direction"], current_price, pos.get("symbol", ""),
                        self._costs_for(pos.get("symbol", ""))["exit_slippage_pips"],
                    )
                    pos["exit_reason"] = "TIME_LIMIT"
                    pos["pnl"] = self._calc_pnl(pos["direction"], pos["entry_price"], pos["exit_price"], pos["volume"], pos.get("symbol", ""), pos.get("entry_time"), current_time)
                    closed_this_bar.append(pos)
                    continue

                # Task 4: hard_close_time now actually reaches the position dict
                # (see _create_position) — before, this key was never copied out of
                # sig["metadata"], so the lookup always returned None and this whole
                # branch was dead code. That is why VWAP positions ran for ~7 days
                # despite a spec mandating flat by 15:55 ET.
                hard_close_time = pos.get("hard_close_time")
                if hard_close_time:
                    import pytz
                    _et_tz = pytz.timezone("America/New_York")
                    et = current_time_dt.astimezone(_et_tz)
                    time_str = et.strftime("%H:%M")
                    # A plain `time_str >= hard_close_time` compare is not enough on
                    # its own: after ET midnight the clock wraps ("02:00" < "15:55"),
                    # so a position that somehow survived the cutoff bar would run a
                    # further ~24h. Also force the close once the ET calendar date has
                    # advanced past the entry's, which is what "flat by end of session"
                    # actually means.
                    _entry_et_date = pos.get("_entry_et_date")
                    if _entry_et_date is None:
                        _entry_secs = _to_epoch_seconds(pos.get("entry_time"))
                        _entry_et_date = (
                            datetime.fromtimestamp(_entry_secs, tz=timezone.utc).astimezone(_et_tz).date()
                            if _entry_secs is not None else et.date()
                        )
                        pos["_entry_et_date"] = _entry_et_date
                    if self._backtest_only_exits and (
                            time_str >= hard_close_time or et.date() > _entry_et_date):
                        pos["exit_price"] = _apply_exit_slippage(
                            pos["direction"], current_price, pos.get("symbol", ""),
                            self._costs_for(pos.get("symbol", ""))["exit_slippage_pips"],
                        )
                        pos["exit_reason"] = "SESSION_END"
                        pos["pnl"] = self._calc_pnl(pos["direction"], pos["entry_price"], pos["exit_price"], pos["volume"], pos.get("symbol", ""), pos.get("entry_time"), current_time)
                        closed_this_bar.append(pos)
                        continue

                # Skip SL/TP evaluation for TP2/TP3 siblings whose TP1 leg
                # closes on THIS SAME BAR. Their BE stop has not been applied
                # yet (that happens in the tp1_hit_groups block after this loop).
                # Evaluating them now would close them at the wrong price.
                # They will be re-evaluated on the next bar with BE stop in place.
                if pos.get("tp_level", 1) != 1 and pos.get("group_id") in _tp1_closing_this_bar:
                    # Still update MAE/MFE for this bar.
                    pip_size = get_pip_size(pos.get("symbol", ""))
                    if pos["direction"] == "BUY":
                        adverse = pos["entry_price"] - low
                        favorable = high - pos["entry_price"]
                    else:
                        adverse = high - pos["entry_price"]
                        favorable = pos["entry_price"] - low
                    pos["mae_pips"] = max(pos.get("mae_pips", 0), adverse / pip_size if pip_size else 0)
                    pos["mfe_pips"] = max(pos.get("mfe_pips", 0), favorable / pip_size if pip_size else 0)
                    continue  # Defer SL/TP to next bar

                # Check SL and TP hits, resolving same-bar ambiguity via the
                # shared helper (items 3.2/3.3/3.7 — identical logic reused by
                # the TP1 pre-pass above, and matches portfolio_engine.py).
                _raw_sl_hit = (low <= pos["stop_loss"]) if pos["direction"] == "BUY" else (high >= pos["stop_loss"])
                _raw_tp_hit = (high >= pos["take_profit"]) if pos["direction"] == "BUY" else (low <= pos["take_profit"])
                if _raw_sl_hit and _raw_tp_hit:
                    pos["same_bar_ambiguous"] = True  # Tag for reporting
                sl_hit, tp_hit = _resolve_sl_tp_hit(
                    pos["direction"], open_p, high, low,
                    pos["stop_loss"], pos["take_profit"], self._simulate_wicks,
                )

                # Update MAE/MFE using this bar's high/low BEFORE the exit checks below,
                # so the excursion on the closing bar itself is captured — previously
                # this ran after the sl_hit/tp_hit `continue`s, so any trade that hit
                # its SL/TP on the same bar it was evaluated (i.e. most quick trades)
                # closed with mae_pips/mfe_pips frozen at their 0.0 initial value.
                if pos["direction"] == "BUY":
                    adverse = pos["entry_price"] - low
                    favorable = high - pos["entry_price"]
                else:
                    adverse = high - pos["entry_price"]
                    favorable = pos["entry_price"] - low

                pip_size = get_pip_size(pos.get("symbol", ""))
                pos["mae_pips"] = max(pos.get("mae_pips", 0), adverse / pip_size if pip_size else 0)
                pos["mfe_pips"] = max(pos.get("mfe_pips", 0), favorable / pip_size if pip_size else 0)

                if sl_hit:
                    # Item 3.4: if the bar's open already gapped past the SL level,
                    # a perfect fill at the exact SL price is unrealistic — fill at
                    # the gapped open price (± slippage) instead.
                    _sl_gapped = (open_p <= pos["stop_loss"]) if pos["direction"] == "BUY" else (open_p >= pos["stop_loss"])
                    _exit_slip = self._costs_for(pos.get("symbol", ""))["exit_slippage_pips"]
                    if _sl_gapped:
                        pos["exit_price"] = _gap_adjusted_fill_price(pos["direction"], open_p, pos["stop_loss"], pos.get("symbol", ""), _exit_slip)
                        pos["gap_fill"] = True
                    else:
                        # [2.9/A6] Non-gapped SL/BE_SL/TRAIL_SL exits used to fill at
                        # EXACTLY the stop price with zero slippage — understating
                        # realised risk relative to what live execution actually pays.
                        pos["exit_price"] = _apply_exit_slippage(
                            pos["direction"], pos["stop_loss"], pos.get("symbol", ""), _exit_slip
                        )
                    pos["exit_reason"] = "TRAIL_SL" if pos.get("trail_applied") else ("BE_SL" if pos.get("be_applied") else "SL")
                    pos["pnl"] = self._calc_pnl(pos["direction"], pos["entry_price"], pos["exit_price"], pos["volume"], pos.get("symbol", ""), pos.get("entry_time"), current_time)
                    closed_this_bar.append(pos)
                    continue
                elif tp_hit:
                    _tp_gapped = (open_p >= pos["take_profit"]) if pos["direction"] == "BUY" else (open_p <= pos["take_profit"])
                    if _tp_gapped:
                        pos["exit_price"] = _gap_adjusted_fill_price(pos["direction"], open_p, pos["take_profit"], pos.get("symbol", ""), self._costs_for(pos.get("symbol", ""))["slippage_pips"])
                        pos["gap_fill"] = True
                    else:
                        pos["exit_price"] = pos["take_profit"]
                    pos["exit_reason"] = f"TP{pos.get('tp_level', 1)}"
                    pos["pnl"] = self._calc_pnl(pos["direction"], pos["entry_price"], pos["exit_price"], pos["volume"], pos.get("symbol", ""), pos.get("entry_time"), current_time)
                    closed_this_bar.append(pos)
                    if pos.get("tp_level") == 1:
                        tp1_hit_groups.add(pos.get("group_id"))
                    continue

                # Run BE/trailing checks via RiskEngine — WITH ATR + swing data
                actions = self.risk_engine.manage_open_position(
                    pos, current_price,
                    atr_value=current_atr,
                    swing_points=swing_points,
                )
                for action in actions:
                    if action["action"] == "MODIFY_SL":
                        old_sl = pos["stop_loss"]
                        pos["stop_loss"] = action["new_sl"]
                        if action.get("reason") == "BREAKEVEN":
                            pos["be_applied"] = True
                        elif action.get("reason") == "TRAIL":
                            pos["trail_applied"] = True

                # ── Phase 14 B2.3: strategy-side in-trade invalidation ────────
                # Called AFTER BE/trailing so the strategy sees the current stop,
                # and BEFORE SL/TP so a strategy-requested close takes priority.
                if self._strategy is not None and hasattr(self._strategy, "on_position_bar"):
                    try:
                        # Build a minimal candle slice the strategy can inspect.
                        # `i` is the current bar index; pass the last 50 bars so
                        # the strategy has enough context without copying the full
                        # DataFrame on every position per bar.
                        _start = max(0, i - 49)
                        _candle_slice = candles.iloc[_start : i + 1]
                        _pos_action = self._strategy.on_position_bar(
                            pos.get("symbol", ""),
                            pos.get("timeframe", ""),
                            _candle_slice,
                            pos,
                        )
                        if _pos_action is not None and _pos_action.action == "CLOSE":
                            _exit_slip = self._costs_for(pos.get("symbol", ""))["exit_slippage_pips"]
                            pos["exit_price"] = _apply_exit_slippage(
                                pos["direction"], current_price,
                                pos.get("symbol", ""), _exit_slip,
                            )
                            pos["exit_reason"] = getattr(_pos_action, "close_reason", "STRATEGY_EXIT")
                            pos["pnl"] = self._calc_pnl(
                                pos["direction"], pos["entry_price"], pos["exit_price"],
                                pos["volume"], pos.get("symbol", ""),
                                pos.get("entry_time"), current_time,
                            )
                            closed_this_bar.append(pos)
                            continue
                    except Exception as _e:
                        logger.debug(f"[ENGINE] on_position_bar raised (non-fatal): {_e}")

            # ── CRITICAL: When TP1 hits, move ALL siblings to break-even ──
            # [4.7/D8/F5] Was unconditional — now gated on be_mode (only fires
            # under TP_HIT/EITHER, matching be_trigger_tp_level=1's default
            # scope; RR/NONE modes must not force a TP1-triggered BE cascade).
            _be_mode_cascade = self.risk_config.get("be_mode", "EITHER")
            if tp1_hit_groups and _be_mode_cascade in ("TP_HIT", "EITHER"):
                for pos in self.open_positions:
                    if pos.get("group_id") in tp1_hit_groups and pos not in closed_this_bar:
                        _sym = pos.get("symbol", "")
                        pip_size = get_pip_size(_sym)
                        new_sl = _breakeven_stop(
                            direction=pos["direction"],
                            entry_price=pos["entry_price"],
                            current_price=current_price,
                            pip_size=pip_size,
                            atr=current_atr,
                            risk_config=self.risk_config,
                            spread_pips=self._costs_for(_sym)["spread_pips"],
                        )
                        if pos["direction"] == "BUY":
                            if new_sl > pos["stop_loss"]:
                                pos["stop_loss"] = new_sl
                                pos["be_applied"] = True
                        else:
                            if new_sl < pos["stop_loss"]:
                                pos["stop_loss"] = new_sl
                                pos["be_applied"] = True

            # Close positions and build exit confirmations
            # FIX 2: Collect positions to remove after the loop (avoids O(n) list.remove in hot loop)
            positions_to_remove = []
            for pos in closed_this_bar:
                pos["exit_time"] = current_time
                # Task 6: leg records kept status="OPEN" forever, even once
                # exit_price/exit_reason/pnl/exit_time were all populated.
                pos["status"] = "CLOSED"
                try:
                    # current_time may be a numpy.int64/float64 (e.g. when it
                    # comes from bar['time']). numpy.int64 is NOT an instance
                    # of Python's int, so detect_session()'s isinstance checks
                    # silently fall through to "UNKNOWN" for it — even though
                    # no exception is raised. Normalize to a plain Python
                    # float first, same as sig_time already is at entry time.
                    pos["session"] = detect_session(_to_epoch_seconds(current_time))
                except Exception:
                    pos["session"] = "UNKNOWN"

                pos["duration_minutes"] = _calc_duration_minutes(
                    pos.get("entry_time"), pos.get("exit_time")
                )
                pos["entry_time_iso"] = _epoch_to_iso(pos.get("entry_time"))
                pos["exit_time_iso"] = _epoch_to_iso(pos.get("exit_time"))

                pos["exit_confirmations"] = [
                    f"Exit Reason: {pos.get('exit_reason', 'UNKNOWN')}",
                    f"Exit Price: {pos.get('exit_price', 0):.5f}",
                    f"PnL: ${pos.get('pnl', 0):.2f}",
                    f"Duration: {pos.get('duration_minutes', 0):.1f} min",
                    f"MAE: {pos.get('mae_pips', 0):.1f} pips",
                    f"MFE: {pos.get('mfe_pips', 0):.1f} pips",
                    f"BE Applied: {'Yes' if pos.get('be_applied') else 'No'}",
                    f"Trail Method: {pos.get('trail_method') or 'NONE'}",
                    f"Session: {pos.get('session', 'UNKNOWN')}",
                ]

                balance += pos.get("pnl", 0)
                pos["balance_after"] = balance
                is_win = pos.get("pnl", 0) > 0
                # If the group is now fully closed, notify strategy
                group_id_closed = pos.get("group_id", "unknown")
                # Fallback to simple sub-trade counting to know when group is done
                # Count remaining open positions for this group
                # Count remaining open positions for this group, ignoring those already queued for removal
                remaining_legs = sum(1 for p in self.open_positions if p.get("group_id") == group_id_closed and p not in positions_to_remove and p != pos)
                
                if remaining_legs == 0:
                    group_pnl = sum(
                        p.get("pnl", 0) for p in self.trades
                        if p.get("group_id") == group_id_closed
                    ) + pos.get("pnl", 0)
                    
                    group_lots = sum(
                        p.get("volume", 0.0) for p in self.trades
                        if p.get("group_id") == group_id_closed
                    ) + pos.get("volume", 0.0)
                    
                    # Safely feed PnL back to the Risk Engine's Circuit Breaker
                    if hasattr(self.risk_engine, "on_backtest_position_closed"):
                        self.risk_engine.on_backtest_position_closed(group_id_closed, group_pnl, current_time, pos.get("symbol", ""), group_lots)
                        
                    strategy = getattr(self, "_strategy", None)
                    if strategy is not None:
                        strategy.notify_outcome(
                            symbol=pos.get("symbol", ""),
                            group_id=group_id_closed,
                            is_win=group_pnl > 0,
                            pnl=group_pnl,
                        )
                self.trades.append(pos)
                positions_to_remove.append(pos)

                self.run_logs.append({
                    "time": _epoch_to_iso(current_time),
                    "level": "INFO",
                    "category": "BACKTEST_LOG",
                    "message": f"Closed {pos['direction']} {pos.get('symbol')} {pos.get('exit_reason')} | PnL: ${pos.get('pnl', 0):.2f}"
                })
            # FIX 2: Bulk removal after the loop — avoids O(n) list.remove per closed position
            for p in positions_to_remove:
                if p in self.open_positions:
                    self.open_positions.remove(p)

            current_timestamp = current_time.timestamp() if hasattr(current_time, "timestamp") else float(current_time)
            # 2. Check for new signals on this bar
            # FIX 1 (Lookahead bias): use >= so a signal is only actioned on the NEXT bar
            # after it was generated (sig_time must be strictly less than current_timestamp).
            while signal_idx < len(signals):
                sig = signals[signal_idx]
                sig_time = float(sig.get("time", float("inf")))
                if sig_time >= current_timestamp:
                    break
                signal_idx += 1

                # Prevent taking multiple positions on the same symbol in the same direction (pyramiding)
                symbol = sig.get("symbol")
                from backend.risk.multi_tp import _is_buy
                sig_is_buy = _is_buy(sig.get("direction", "BUY"))
                
                # [2.17/D-2/P2-R2] max_positions_per_symbol stays 1 by default (D-2);
                # allow_pyramiding is the explicit opt-in to stack same-direction
                # positions on one symbol, gated additionally by
                # min_bars_between_entries so pyramiding can't fire every bar.
                allow_pyramiding = bool(self.risk_config.get("allow_pyramiding", False))
                already_open = False
                if not allow_pyramiding:
                    for p in self.open_positions:
                        p_is_buy = _is_buy(p.get("direction", "BUY"))
                        if p.get("symbol") == symbol and p_is_buy == sig_is_buy:
                            already_open = True
                            break

                if already_open:
                    self._record_blocked(sig, current_time, "same_direction_already_open")
                    continue

                if allow_pyramiding:
                    min_bars = int(self.risk_config.get("min_bars_between_entries", 0) or 0)
                    if min_bars > 0:
                        last_bar = self._last_entry_bar_by_symbol.get(symbol)
                        if last_bar is not None and (i - last_bar) < min_bars:
                            self._record_blocked(sig, current_time, "min_bars_between_entries")
                            continue

                # Generate a group_id to link all sub-positions from this signal
                group_id = str(uuid.uuid4())[:8]
                sig["group_id"] = group_id

                current_time_dt = dt_arr[i]
                
                self.rejection_funnel["total_evaluated"] += 1
                
                # Check Strategy Rejections first
                passed_gates = sig.get("metadata", {}).get("passed_gates", True)
                if not passed_gates:
                    reasons = sig.get("metadata", {}).get("rejection_reasons", [])
                    for r in reasons:
                        gate = r.split(":")[0] if ":" in r else "Unknown Strategy Rule"
                        self.rejection_funnel["strategy_rejections"][gate] = self.rejection_funnel["strategy_rejections"].get(gate, 0) + 1
                    self._record_blocked(sig, current_time_dt, f"strategy:{reasons[0].split(':')[0] if reasons else 'unknown'}", "; ".join(reasons))
                    logger.trace(f"[ENGINE] ❌ Signal REJECTED (Strategy): {reasons}")
                    continue

                # Evaluate signal through RiskEngine
                approved, reason, tp_levels = False, "Error during evaluation", []

                # (Prop Firm validator is informational only in backtesting, we do not block signals here so the backtest can continue)

                try:
                    # [4.2/D1] Same resolver bot_service.py uses for live —
                    # was ALWAYS `initial_balance` (static) here regardless of
                    # RiskParams.sizing_basis, which didn't exist yet. Default
                    # STATIC reproduces this exactly; BALANCE/EQUITY let a
                    # backtest compound sizing with the run's own growing
                    # balance, for comparison against a live personal account
                    # actually running that way.
                    from backend.risk.position_sizer import resolve_sizing_base_balance
                    _sizing_base_balance = resolve_sizing_base_balance(
                        self.risk_config.get("sizing_basis", "STATIC"),
                        static_balance=initial_balance,
                        live_balance=balance,
                        live_equity=balance,  # no floating-PnL equity tracked mid-loop; balance is the closest available figure
                    )
                    approved, reason, tp_levels = self.risk_engine.evaluate_signal(
                        signal_data=sig,
                        account_balance=balance,
                        current_time=current_time_dt,
                        initial_balance=_sizing_base_balance
                    )
                except Exception as e:
                    import traceback
                    traceback.print_exc()
                    self.rejection_funnel["errors"] += 1
                    self._record_blocked(sig, current_time_dt, "engine_error", str(e))
                    logger.error(f"[ENGINE] ❌ Error evaluating signal: {e!s}")
                    continue

                if approved:
                    self.rejection_funnel["approved"] += 1
                    logger.trace(f"[ENGINE] ✅ Signal APPROVED at bar {i}: {sig.get('direction')} @ {sig.get('entry_price', current_price):.5f} | {len(tp_levels)} TP levels | balance=${balance:.2f}")

                    # The fill price for every leg of this group: the OPEN of the
                    # current bar (which is the bar AFTER the signal was generated —
                    # guaranteed by the sig_time >= current_timestamp guard above).
                    # This eliminates same-bar fill.
                    bar_open_price = float(open_p)

                    # Task 1 / [2.22/X4]: legs are staged, not appended directly.
                    # _create_position re-validates SL/TP against this REAL fill
                    # price and returns None when that specific leg's geometry is
                    # no longer tradeable. PER-LEG rejection: drop only the
                    # offending leg and keep the rest — the group as a whole is
                    # only rejected if EVERY leg fails. (Previously any single
                    # leg's rejection dropped the whole group, including legs
                    # that had already validated fine — a strictly worse outcome
                    # than trading the subset that is actually tradeable.)
                    staged_positions: list[dict[str, Any]] = []
                    dropped_legs = 0
                    for tp in tp_levels:
                        # Validate before opening
                        is_valid, err = _validate_position(
                            sig.get("direction", "BUY"),
                            sig.get("entry_price", current_price),
                            sig.get("stop_loss", 0),
                            tp.tp_price,
                        )
                        if not is_valid:
                            logger.warning(f"[ENGINE] ❌ Invalid position rejected: {err}")
                            self.invalid_signals += 1
                            dropped_legs += 1
                            self._record_blocked(sig, current_time_dt, "invalid_position_geometry", err)
                            continue

                        position = self._create_position(sig, tp, current_time, bar_open_price, group_id, balance)
                        if position is None:
                            # Rejection reason already recorded in rejection_funnel.
                            dropped_legs += 1
                            continue
                        staged_positions.append(position)
                        logger.debug(f"[ENGINE]   Position opened: TP{tp.level} @ {bar_open_price:.5f} (bar open) | vol={tp.volume:.4f}")

                    if not staged_positions:
                        # Undo the optimistic "approved" increment so the funnel still
                        # balances (total_evaluated == approved + all rejection buckets).
                        self.rejection_funnel["approved"] -= 1
                        self.invalid_signals += 1
                        self._record_blocked(sig, current_time_dt, "rejected_at_fill", "SL/TP invalid against actual fill price — see run_logs for the specific gate")
                        continue

                    self.open_positions.extend(staged_positions)
                    self._last_entry_bar_by_symbol[symbol] = i

                    self.run_logs.append({
                        "time": _epoch_to_iso(current_time),
                        "level": "INFO",
                        "category": "BACKTEST_LOG",
                        "message": (
                            f"Opened {sig.get('direction')} {sig.get('symbol', 'UNKNOWN')} @ {bar_open_price:.5f} | "
                            f"{len(staged_positions)}/{len(tp_levels)} TPs"
                            + (f" ({dropped_legs} leg(s) dropped at fill)" if dropped_legs else "")
                        )
                    })

                    # Notify CircuitBreaker of the new group. sub_trade_count must
                    # match len(staged_positions) — the legs actually opened —
                    # not len(tp_levels), or a group with a per-leg-dropped TP
                    # would never reach sub_trades<=0 and the circuit breaker
                    # would never record its PnL / release its symbol slot.
                    if hasattr(self.risk_engine, "circuit") and hasattr(self.risk_engine.circuit, "position_opened"):
                        actual_risk_dollars = sum(
                            calculate_risk_dollars(p["volume"], p["entry_price"], p["stop_loss"], p.get("symbol", ""))
                            for p in staged_positions
                        )
                        self.risk_engine.circuit.position_opened(
                            group_id,
                            len(staged_positions),
                            symbol=sig.get("symbol", ""),
                            initial_risk_dollars=actual_risk_dollars,
                            strategy_id=staged_positions[0].get("strategy_id", ""),
                            direction=sig.get("direction", ""),  # [9.6]
                            slot_id=sig.get("metadata", {}).get("slot_id", ""),  # [12.5/12.6]
                        )
                else:
                    self.rejection_funnel["risk_rejections"][reason] = self.rejection_funnel["risk_rejections"].get(reason, 0) + 1
                    self._record_blocked(sig, current_time_dt, "risk_engine", reason)
                    logger.trace(f"[ENGINE] ❌ Signal REJECTED (Risk): {reason}")

            # Track floating equity AFTER processing closes this bar:
            # Recompute open_pnl from positions that are still actually open
            # (not those just closed above). Prevents closed-trade PnL from
            # being double-counted in the equity curve on the bar they exit.
            post_close_pnl = sum(
                self._calc_pnl(p["direction"], p["entry_price"], current_price, p["volume"], p.get("symbol", ""))
                for p in self.open_positions
            )
            self.equity_curve.append(balance + post_close_pnl)

        # Close any remaining open positions at last price
        last_price = closes_arr[-1] if len(closes_arr) > 0 else 0
        last_time = time_arr[-1] if len(time_arr) > 0 else 0
        for pos in self.open_positions[:]:
            pos["pnl"] = self._calc_pnl(pos["direction"], pos["entry_price"], last_price, pos["volume"], pos.get("symbol", ""), pos.get("entry_time"), last_time)
            pos["exit_price"] = last_price
            pos["exit_reason"] = "END_OF_DATA"
            pos["exit_time"] = last_time
            pos["status"] = "CLOSED"  # Task 6
            pos["duration_minutes"] = _calc_duration_minutes(pos.get("entry_time"), last_time)
            pos["entry_time_iso"] = _epoch_to_iso(pos.get("entry_time"))
            pos["exit_time_iso"] = _epoch_to_iso(last_time)
            pos["exit_confirmations"] = [
                "Exit Reason: END_OF_DATA (forced close)",
                f"Exit Price: {last_price:.5f}",
                f"PnL: ${pos.get('pnl', 0):.2f}",
            ]
            balance += pos.get("pnl", 0)
            pos["balance_after"] = balance
            self.trades.append(pos)
        self.open_positions = []
        self.equity_curve.append(balance)

        # Group trades by group_id for combined P&L display
        grouped_trades = group_trades(self.trades, candles, candles_m15, candles_m5, candles_h1)

        # ── [T1.3] Merge strategy-side gate telemetry into the funnel ──────
        # `rejection_funnel["strategy_rejections"]` is populated from
        # `sig.metadata.passed_gates`, but a candidate the STRATEGY rejects
        # never becomes a signal and so never reaches this engine at all — the
        # branch that fills it could only ever fire for a signal that was
        # emitted while flagged as failed, which no strategy does. The result
        # was a permanently empty strategy-rejection breakdown.
        #
        # The GateRecorder on the strategy holds the real counts, so pull them
        # across here. Everything is guarded: a strategy without telemetry
        # enabled contributes nothing and behaves exactly as before.
        try:
            _gates = getattr(self._strategy, "gates", None)
            if _gates is not None and getattr(_gates, "enabled", False):
                _gates.finish()
                _blocks = _gates.strategy_rejections()
                if _blocks:
                    sr = self.rejection_funnel.setdefault("strategy_rejections", {})
                    for _name, _n in _blocks.items():
                        sr[_name] = sr.get(_name, 0) + _n
                _summary = _gates.summary()
                self.rejection_funnel["gate_stats"] = _summary.get("gates", {})
                self.rejection_funnel["candidates_evaluated"] = _summary.get("candidates_recorded", 0)
                self.rejection_funnel["candidates_blocked"] = _summary.get("candidates_blocked", 0)
                if _summary.get("disabled_gates"):
                    # Marks the run as an ABLATION run, so it can never be
                    # mistaken for a baseline in the saved results.
                    self.rejection_funnel["disabled_gates"] = _summary["disabled_gates"]
                logger.info(
                    f"[ENGINE] Gate telemetry: {_summary.get('candidates_recorded', 0)} candidates, "
                    f"{_summary.get('candidates_blocked', 0)} blocked. "
                    f"Top blockers: {list(_blocks.items())[:3]}"
                )
        except Exception as e:
            logger.warning(f"[ENGINE] Gate telemetry merge failed (run unaffected): {e}")

        report = generate_risk_report(
            grouped_trades,
            initial_balance=initial_balance,
            # Sharpe/Sortino need the same normaliser the sizer actually used.
            sizing_basis=self.risk_config.get("sizing_basis") or "STATIC",
        )
        report.rejection_funnel = self.rejection_funnel

        # ── Task 5: recompute TP/SL/BE/TRAIL hit rates from LEG-level exits ──
        # generate_risk_report only sees grouped trades, whose exit_reason is the
        # group's terminal reason — so a group that banked TP1 and later stopped
        # its remaining legs at break-even reported no TP1 hit at all.
        hit_rates = apply_leg_level_hit_rates(report, self.trades, grouped_trades)

        # ── Task 6: drawdown from the BAR-level equity curve ──
        dd = apply_bar_level_drawdown(report, self.equity_curve, initial_balance)

        # ── Engine completion summary ──
        total_pnl = balance - initial_balance
        wins = sum(1 for t in grouped_trades if t.get("pnl", 0) > 0)
        losses = sum(1 for t in grouped_trades if t.get("pnl", 0) <= 0)
        logger.info("[ENGINE] ═══ Backtest engine complete ═══")
        logger.info(f"[ENGINE] Trades: {len(grouped_trades)} ({wins}W / {losses}L) | Invalid: {self.invalid_signals}")
        logger.info(f"[ENGINE] P&L: ${total_pnl:.2f} | Final balance: ${balance:.2f}")
        if self.trades:
            best = max(t.get("pnl", 0) for t in self.trades)
            worst = min(t.get("pnl", 0) for t in self.trades)
            logger.info(f"[ENGINE] Best trade: ${best:.2f} | Worst trade: ${worst:.2f}")
        logger.info(
            f"[ENGINE] Leg-level hit rates: "
            f"TP1={hit_rates['tp1_hit_rate'] * 100:.1f}% TP2={hit_rates['tp2_hit_rate'] * 100:.1f}% "
            f"TP3={hit_rates['tp3_hit_rate'] * 100:.1f}% SL={hit_rates['sl_hit_rate'] * 100:.1f}% "
            f"BE={hit_rates['be_hit_rate'] * 100:.1f}% TRAIL={hit_rates['trail_hit_rate'] * 100:.1f}% "
            f"| max_dd={dd['max_drawdown_pct'] * 100:.2f}% (bar-level)"
        )
        if self.rejection_funnel.get("fill_rejections"):
            logger.info(f"[ENGINE] Fill-time rejections: {self.rejection_funnel['fill_rejections']}")
        logger.info(f"[ENGINE] Cost model applied: {self.cost_model}")

        return {
            "backtest_id": str(uuid.uuid4()),
            "initial_balance": initial_balance,
            "final_balance": balance,
            "total_pnl": total_pnl,
            "total_trades": len(self.trades),
            "total_signals": len(grouped_trades),
            "invalid_signals": self.invalid_signals,
            "trades": self.trades,
            "grouped_trades": grouped_trades,
            "equity_curve": self.equity_curve,
            "report": report,
            "rejection_funnel": self.rejection_funnel,
            "blocked_signals": self.blocked_signals,
            "run_logs": self.run_logs,
            # Task 2: record exactly which transaction costs this run assumed and
            # where each value came from, so a saved backtest is reproducible and
            # auditable ("was this run costed with live MT5 spreads or asset-class
            # averages, or was it another zero-cost run?").
            "cost_model": self.cost_model,
            # [2.24] How many risk-evaluation checks this run spent circuit-breaker
            # paused, and the last reason — makes a drawdown-latched stretch
            # visibly distinct from "the strategy found no setups" in the report.
            "circuit_breaker_summary": {
                "paused_checks": self.risk_engine.circuit.paused_bars,
                "last_pause_reason": self.risk_engine.circuit.last_pause_reason,
            },
        }

    def _record_blocked(self, sig: dict[str, Any], current_time: Any, gate: str, reason: str = "") -> None:
        """[I4] Append one rejected signal to self.blocked_signals, capped."""
        if len(self.blocked_signals) >= self._blocked_signals_cap:
            return
        try:
            time_iso = _epoch_to_iso(current_time)
        except Exception:
            time_iso = str(current_time)
        self.blocked_signals.append({
            "time": time_iso,
            "symbol": sig.get("symbol", ""),
            "direction": sig.get("direction", ""),
            "entry_price": sig.get("entry_price"),
            "stop_loss": sig.get("stop_loss"),
            "gate": gate,
            "reason": reason,
        })

    def _create_position(
        self,
        sig: dict[str, Any],
        tp: TPLevel,
        current_time: Any,
        current_price: float,
        group_id: str,
        balance: float,
    ) -> dict[str, Any] | None:
        """
        Create a position dict with entry confirmations and group_id.

        Returns None when the position must NOT be opened because SL/TP are no
        longer valid at the actual fill price (Task 1). Callers must treat None
        as "skip this signal group entirely".
        """
        # Item 3.1: the stored/fill entry_price must always be the realistic
        # next-bar-open price (`current_price` here is `bar_open_price` from
        # the caller), never the strategy's theoretical signal entry_price —
        # that value is preserved only in `original_signal`/logging below for
        # reference, not used as the fill price.
        entry_price = current_price
        symbol = sig.get("symbol", "")
        signal_entry_price = sig.get("entry_price", entry_price)
        stop_loss = sig.get("stop_loss", 0)
        take_profit = tp.tp_price

        # ── Task 1: re-validate SL/TP against the ACTUAL FILL price ──
        # The risk engine validated these against the strategy's theoretical
        # signal entry. Now that the real fill (next bar's open) is known, run
        # the SAME check live's order_manager.py runs before sending an order.
        # Validated against the ORIGINAL (un-reanchored) SL/TP first, so a fill
        # that gapped past the target or reversed the stop's side is still
        # caught here — re-anchoring below only runs once this passes.
        costs = self._costs_for(symbol)
        fill_ok, reject_key, reject_detail = validate_at_fill_price(
            sig.get("direction", "BUY"),
            entry_price,
            stop_loss,
            take_profit,
            symbol,
            stops_level_pips=costs["stops_level_pips"],
            spread_pips=costs["spread_pips"],
        )
        if not fill_ok:
            logger.warning(
                f"[ENGINE] ❌ Fill-time rejection ({reject_key}) {symbol} "
                f"TP{tp.level} signal_entry={sig.get('entry_price', entry_price):.5f} "
                f"fill={entry_price:.5f} sl={stop_loss:.5f}: {reject_detail}"
            )
            funnel = self.rejection_funnel.setdefault("fill_rejections", {})
            funnel[reject_key] = funnel.get(reject_key, 0) + 1
            # Also surface in risk_rejections so the existing rejection-funnel UI
            # shows it rather than the signal vanishing silently.
            self.rejection_funnel["risk_rejections"][reject_key] = (
                self.rejection_funnel["risk_rejections"].get(reject_key, 0) + 1
            )
            self.run_logs.append({
                "time": _epoch_to_iso(current_time),
                "level": "WARNING",
                "category": "BACKTEST_LOG",
                "message": f"Rejected at fill ({reject_key}) {sig.get('direction')} {symbol}: {reject_detail}",
            })
            return None

        # ── [2.7/A5] Re-anchor SL/TP to the ACTUAL FILL price ──
        # The signal's SL/TP were computed relative to the strategy's theoretical
        # entry_price. Once fill validation above has confirmed the real fill
        # (next bar's open) is not pathological (gapped past target/stop), shift
        # SL/TP by the same delta as the fill so every leg's realised risk and
        # RR matches what was actually sized — instead of leaving them pinned to
        # a signal price that may sit up to ~0.5R away from the real fill.
        # signal_entry_price/signal_stop_loss are preserved below for audit.
        fill_delta = entry_price - signal_entry_price
        if fill_delta != 0.0:
            stop_loss = stop_loss + fill_delta
            take_profit = take_profit + fill_delta

        # Detect entry session
        try:
            entry_session = detect_session(_to_epoch_seconds(current_time))
        except Exception:
            entry_session = "UNKNOWN"

        # Use detailed SMC confirmations from signal if available, plus position-specific info
        signal_confirmations = sig.get("confirmations", [])
        entry_confirmations = [
            f"Direction: {sig.get('direction', 'UNKNOWN')}",
            f"Entry Price: {entry_price:.5f}",
            f"Signal Entry Price (reference): {signal_entry_price:.5f}",
            f"Stop Loss (re-anchored to fill): {stop_loss:.5f}",
            f"Take Profit (TP{tp.level}, re-anchored to fill): {take_profit:.5f}",
            f"RR Multiplier: 1:{tp.rr_multiplier:.1f}",
            f"Volume: {tp.volume:.2f} lots ({tp.volume_pct * 100:.0f}%)",
            f"Entry Session: {entry_session}",
            "Entry Mode: ALL AT ENTRY",
            f"Pattern: {sig.get('pattern', 'N/A')}",
            f"FVG: {'Yes' if sig.get('has_fvg') else 'No'}",
            f"Liquidity Sweep: {'Yes' if sig.get('has_liquidity_sweep') else 'No'}",
        ]
        # Append full Structural analysis
        if "confluence_breakdown" in sig.get("metadata", {}):
            entry_confirmations.append("── Structural Analysis ──")
            entry_confirmations.extend(signal_confirmations)

        # Task 6: strategy attribution. sig dicts built by the API route carry no
        # strategy_id at all (see backtest.py's generate_signals_simulated), which
        # is why every saved grouped_trade reported strategy_id="UNKNOWN".
        # Fall back through signal -> metadata -> run config.
        strategy_id = (
            sig.get("strategy_id")
            or sig.get("strategy_name")
            or sig.get("metadata", {}).get("strategy_id")
            or self._strategy_id
        )

        # Task 4: hard_close_time was set correctly by strategies (e.g. VWAP's
        # 15:55 ET flat rule) but never copied out of sig["metadata"] into the
        # position dict — so the force-close branch in run() looked up a key that
        # was always absent and the entire session-end rule was dead code. That is
        # why VWAP positions ran for ~7 days against a spec mandating a same-day
        # flat. Read it the same way the other metadata keys are read below.
        hard_close_time = sig.get("metadata", {}).get("hard_close_time")

        return {
            "id": str(uuid.uuid4()),
            "group_id": group_id,
            "symbol": sig.get("symbol", ""),
            "strategy_id": strategy_id,
            "strategy": strategy_id,  # alias, matches portfolio_engine.py
            "direction": "BUY" if _is_buy(sig.get("direction", "BUY")) else "SELL",
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            # Immutable copy of the stop as it stood at entry. `stop_loss` above is
            # MUTATED by break-even and trailing, so it cannot be used to measure the
            # risk originally taken — see compute_trade_metrics() in analytics/metrics.py.
            "initial_stop_loss": stop_loss,
            "original_sl": stop_loss,  # risk/engine.py::manage_open_position reads this key
            # [2.7/A5] Theoretical (pre-fill) signal levels, kept for audit —
            # stop_loss/take_profit above are re-anchored to the actual fill.
            "signal_entry_price": signal_entry_price,
            "signal_stop_loss": sig.get("stop_loss", 0),
            "take_profit": take_profit,
            "volume": tp.volume,
            "tp_level": tp.level,
            "trail_method": tp.trail_method,
            "entry_time": current_time,
            "entry_time_iso": _epoch_to_iso(current_time),
            "entry_session": entry_session,
            "be_applied": False,
            "mae_pips": 0.0,
            "mfe_pips": 0.0,
            "confluence_score": sig.get("confluence_score", 0),
            "balance_before": balance,
            "status": "OPEN",
            "hard_close_time": hard_close_time,
            "entry_confirmations": entry_confirmations,
            "entry_snapshot_b64": sig.get("metadata", {}).get("entry_snapshot_b64", ""),
            # [I3] first-class field, not just nested in original_signal, so the
            # UI/report can read it without threading through metadata.
            "sizing_diagnostics": sig.get("metadata", {}).get("sizing_diagnostics"),
            "original_signal": sig,
        }

    def _calc_pnl(
        self,
        direction: str,
        entry: float,
        exit_price: float,
        volume: float,
        symbol: str,
        entry_time: Any = None,
        exit_time: Any = None,
    ) -> float:
        """
        Calculate P&L using the same data source chain as position sizing:
        MT5 live data (when connected) -> InstrumentProfile -> Standard defaults.

        Applies simulation costs, each resolved per-symbol from the user's explicit
        config or (when unset) live-MT5/asset-class broker data — see
        resolve_effective_costs():
          - slippage_pips: shifts effective entry price against the trade direction
          - spread_pips: pip cost of crossing bid/ask at entry (deducted from PnL)
          - commission_per_lot: round-turn broker commission (deducted from PnL)
          - swap_*_per_lot_per_day: overnight financing, charged per rollover
            crossed between entry_time and exit_time (Task 3). Only applied when
            BOTH timestamps are supplied, i.e. on real closes — floating-equity
            marks pass neither and are therefore unaffected.

        Formula: pnl = (price_diff / tick_size) * tick_value * volume - costs
        """
        from backend.risk.position_sizer import get_symbol_info
        costs = self._costs_for(symbol)
        info = get_symbol_info(symbol)
        tick_value = info.get("tick_value", 1.0)
        tick_size  = info.get("tick_size",  0.00001)
        source     = info.get("source", "UNKNOWN")
        pip_size   = get_pip_size(symbol)

        if source == "DEFAULT":
            logger.warning(f"[_calc_pnl] {symbol}: PnL computed with DEFAULT fallback — may be incorrect!")

        if tick_size == 0 or tick_value == 0:
            logger.warning(f"[_calc_pnl] {symbol}: tick_size or tick_value is zero — returning 0 PnL.")
            return 0.0

        value_per_unit_move = tick_value / tick_size

        # Apply slippage: shift effective entry against the trade direction
        slippage_pips = costs["slippage_pips"]
        if slippage_pips > 0 and pip_size > 0:
            slippage_price = slippage_pips * pip_size
            if _is_buy(direction):
                entry = entry + slippage_price  # BUY fills higher (worse)
            else:
                entry = entry - slippage_price  # SELL fills lower (worse)

        price_diff = exit_price - entry
        raw_pnl = price_diff * value_per_unit_move * volume
        if not _is_buy(direction):
            raw_pnl = -raw_pnl

        # Deduct spread cost (pip cost of crossing bid/ask at entry)
        spread_pips = costs["spread_pips"]
        if spread_pips > 0 and pip_size > 0:
            spread_cost = spread_pips * pip_size * value_per_unit_move * volume
            raw_pnl -= spread_cost

        # Deduct round-turn commission
        commission_per_lot = costs["commission_per_lot"]
        if commission_per_lot > 0:
            raw_pnl -= commission_per_lot * volume

        # Task 3: overnight financing on multi-day holds. Signed (MT5 convention:
        # negative = charge), so it is ADDED. Only charged on real closes, where
        # both timestamps are known.
        if entry_time is not None and exit_time is not None:
            raw_pnl += self._swap_cost(direction, volume, symbol, entry_time, exit_time)

        return raw_pnl