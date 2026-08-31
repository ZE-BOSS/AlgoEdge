"""
backend/strategies/strategy_vwap/engine.py

VWAP v2 — Fair Value / Confluence Framework
=============================================
[Phase 8] Implements the source material's stated framework (VWAP = fair
value, trend context, extremes via ±1σ/±2σ/±3σ bands, confluence — "let VWAP
guide your bias, not your entry"), which v1 only partially implemented (a
single trend-continuation pullback, no bands, VWAP itself as the entry
trigger rather than the bias).

Two setups, either or both active via `entry_mode`:
  Setup 1 — PULLBACK_TO_VALUE (trend continuation): price above/below VWAP,
            VWAP sloping the same way, momentum confirming, price pulling
            back TOWARD value (converging, inside ±1σ), volume-confirmed,
            first pullback since the session anchor only. Target +2σ/-2σ.
  Setup 2 — BAND_REVERSION (mean reversion, new): price closes beyond
            ±2σ, VWAP slope flat, a rejection wick against the extension,
            volume-confirmed, momentum NOT confirming a real trend (don't
            fade a strong trend). Target = VWAP (fair value).

Source: docs/vwap_strategy_implementation_plan.md (v1),
implementation/strategy_update/ TapeDragon VWAP carousel (v2 framework).
"""

import math

import numpy as np
import pandas as pd
import pytz
from datetime import date as date_type
from datetime import datetime as datetime_type
from functools import lru_cache

from backend.core.config_schema import UserConfigV2
from backend.risk.position_sizer import get_pip_size
from backend.strategies.base_strategy import BaseStrategy, TradeSignal
from backend.strategies.core.markings import (
    ROLE_CONFLUENCE,
    ROLE_CONTEXT,
    ROLE_INVALIDATION,
    ROLE_TRIGGER,
    MarkingCollector,
    ts,
)
from backend.strategies.core.swing_structure import calculate_atr
from backend.strategies.registry import register_strategy
from backend.utils.logger import get_logger

logger = get_logger(__name__)

# Synthetic indices — VWAP is explicitly not suitable for these
_SYNTHETIC_PATTERNS = (
    "volatility", "boom", "crash", "jump", "step index", "range break",
    "v10", "v25", "v50", "v75", "v100", "v150", "v250",
)


def _is_synthetic(symbol: str) -> bool:
    """Return True if the symbol is a Deriv synthetic (no institutional order flow)."""
    sl = symbol.lower()
    return any(p in sl for p in _SYNTHETIC_PATTERNS)


_ET_TZ = pytz.timezone("America/New_York")

# 09:30 ET, the session anchor, expressed as seconds past midnight.
_SESSION_ANCHOR_SECONDS = 9 * 3600 + 30 * 60


@lru_cache(maxsize=8192)
def _et_utc_offset(epoch: int) -> int:
    """
    New York's UTC offset, in seconds, at the given UTC epoch.

    Uses stdlib `datetime` rather than pandas deliberately. `pd.to_datetime(...)
    .dt.tz_convert(...)` costs ~25 ms regardless of how many timestamps it is
    given, because the expense is constructing the Series and DatetimeIndex, not
    the conversion — so the previous "two conversions instead of three hundred"
    shortcut saved nothing measurable. This form is ~2 us.

    Memoised on the exact epoch: consecutive `on_bar` calls slide a window by one
    bar, so the first/last epochs repeat constantly across calls.
    """
    return int(datetime_type.fromtimestamp(epoch, tz=_ET_TZ).utcoffset().total_seconds())


def _session_dates_cached(times) -> np.ndarray:
    """
    ET session day per bar (the 09:30 anchor day), as an int64 array of day
    numbers. Only equality between adjacent elements matters to callers, so the
    absolute epoch of day 0 is irrelevant.

    The ET offset is piecewise constant — it changes at the two DST boundaries a
    year — so for a 300-bar window (about a day of M5) it is virtually always a
    single constant. The first and last timestamps are converted and, if their
    offsets agree, every session day follows by integer arithmetic. When they
    differ (a slice straddling a DST change, roughly twice a year) each element
    is converted individually, so correctness does not depend on the shortcut.

    Returns a plain ndarray rather than a Series: the sole caller immediately
    calls `.to_numpy()` on it, and constructing the Series costs more than the
    arithmetic it wraps.
    """
    epochs = np.asarray(times, dtype="int64")
    if epochs.size == 0:
        return np.empty(0, dtype="int64")

    off0 = _et_utc_offset(int(epochs[0]))
    off1 = _et_utc_offset(int(epochs[-1]))

    if off0 == off1:
        return (epochs + off0 - _SESSION_ANCHOR_SECONDS) // 86400

    # DST straddle. Rare enough that a Python loop is the right trade.
    out = np.empty(epochs.size, dtype="int64")
    for i in range(epochs.size):
        e = int(epochs[i])
        out[i] = (e + _et_utc_offset(e) - _SESSION_ANCHOR_SECONDS) // 86400
    return out


def _ffill_bfill(a: np.ndarray) -> np.ndarray:
    """
    numpy equivalent of `Series.ffill().bfill()`.

    Measured at 18 ms/bar as pandas (`_pad_or_backfill` over a 300-bar window,
    twice per call); this form does not leave numpy.
    """
    valid = ~np.isnan(a)
    if valid.all():
        return a
    if not valid.any():
        return a

    idx = np.where(valid, np.arange(a.size), 0)
    np.maximum.accumulate(idx, out=idx)
    out = a[idx]

    # Leading NaNs have no earlier value to carry forward — back-fill them from
    # the first valid observation.
    first = int(np.argmax(valid))
    if first > 0:
        out[:first] = a[first]
    return out


def _calculate_anchored_vwap_with_bands(
    candles: pd.DataFrame, anchor_minutes: int, band_lookback: int = 0
) -> tuple[pd.Series, pd.Series]:
    """
    [8.2] Calculate true daily-anchored VWAP AND its running volume-weighted
    standard deviation, resetting to the session open each trading day.
    Extends the v1 `_calculate_anchored_vwap` (session-grouped cumulative
    VWAP) with the companion σ series needed for ±1σ/±2σ/±3σ bands.

    Spec says "15-min anchored VWAP" — this means a cumulative VWAP anchored to the
    start of each trading session (9:30 ET), not a rolling sliding-window VWAP.

    Returns `(vwap, std)` as numpy float arrays aligned to `candles`, NOT
    pandas Series — index with `[-1]`, not `.iloc[-1]`. Constructing the two
    output Series measured 92 ms/bar, more than the calculation itself, and
    every caller reads only the last element or two.

    std is all-zero when volume data is unavailable (bands collapse to the VWAP
    line itself in that case, which callers should treat as "bands disabled"
    rather than "±0 width").

    `band_lookback`: 0 = since the session anchor (matches the VWAP line);
    >0 = a rolling N-bar window for the std computation instead (the VWAP
    line itself always stays session-anchored regardless).
    """
    # numpy rather than Series arithmetic: the pandas form allocates three
    # intermediate Series with index alignment on every bar, and measured 10 ms
    # of a 40 ms call. The values are identical; only the wrapper differs.
    _h = candles["high"].to_numpy(dtype=float, copy=False)
    _l = candles["low"].to_numpy(dtype=float, copy=False)
    _c = candles["close"].to_numpy(dtype=float, copy=False)
    tp_a = (_h + _l + _c) / 3.0

    if "volume" in candles.columns:
        v_a = candles["volume"].to_numpy(dtype=float, copy=False)
        if not (v_a.sum() > 0):
            v_a = np.ones(tp_a.size, dtype=float)
    else:
        v_a = np.ones(tp_a.size, dtype=float)

    if "time" in candles.columns:
        try:
            sd_a = _session_dates_cached(candles["time"].to_numpy(dtype="int64", copy=False))

            # ── Vectorised path (band_lookback == 0, the default) ──────────
            #
            # The per-group loop below is correct but rebuilds four pandas
            # objects per session per call, with `.loc[group_idx]` label
            # indexing each time — and `on_bar` calls this once per bar over a
            # 300-bar window, so the same sums are recomputed ~300 times.
            # Measured cost: 43.7 ms/bar for VWAP against 1.15 ms/bar for APA,
            # i.e. ~12.5 minutes on a 17k-bar run.
            #
            # Session dates are sorted and contiguous (candles are time-ordered),
            # so each session is a slice, and a cumulative sum that resets per
            # session is `cumsum - cumsum_at_group_start`. That is the whole
            # calculation with no Python loop and no label lookups.
            if not band_lookback:
                starts = np.flatnonzero(np.concatenate(([True], sd_a[1:] != sd_a[:-1])))
                lengths = np.diff(np.concatenate((starts, [len(sd_a)])))
                gid = np.repeat(np.arange(len(starts)), lengths)

                def _reset_cumsum(a: np.ndarray) -> np.ndarray:
                    """Cumulative sum restarting at every session boundary."""
                    c = np.cumsum(a)
                    base = np.zeros(len(starts), dtype=float)
                    if len(starts) > 1:
                        base[1:] = c[starts[1:] - 1]
                    return c - base[gid]

                cum_tpv = _reset_cumsum(tp_a * v_a)
                cum_v = _reset_cumsum(v_a)
                # A zero cumulative volume can only happen on a session's first
                # bar with zero volume; the original ffill/bfill'd around it.
                safe_v = np.where(cum_v > 0, cum_v, np.nan)
                vwap_a = cum_tpv / safe_v

                sq_dev_a = v_a * (tp_a - vwap_a) ** 2
                cum_sq = _reset_cumsum(np.nan_to_num(sq_dev_a))
                var_a = np.maximum(cum_sq / safe_v, 0.0)
                std_a = np.nan_to_num(np.sqrt(var_a))

                if np.isnan(vwap_a).all():
                    raise ValueError("All-NaN VWAP — fallback to rolling")
                return _ffill_bfill(vwap_a), std_a

            typical_price = pd.Series(tp_a, index=candles.index)
            vol = pd.Series(v_a, index=candles.index)
            tp_vol = typical_price * vol
            session_dates = pd.Series(sd_a, index=candles.index)
            vwap_vals = pd.Series(index=candles.index, dtype=float)
            std_vals = pd.Series(index=candles.index, dtype=float)
            for _date, group_idx in candles.groupby(session_dates).groups.items():
                g_tp = typical_price.loc[group_idx]
                g_tp_vol = tp_vol.loc[group_idx]
                g_vol = vol.loc[group_idx]
                cum_tp_vol = g_tp_vol.cumsum()
                cum_vol = g_vol.cumsum()
                running_vwap = (cum_tp_vol / cum_vol).ffill().bfill()
                vwap_vals.loc[group_idx] = running_vwap

                # Running (or rolling, if band_lookback > 0) volume-weighted
                # variance around the RUNNING vwap at each point — using the
                # session's FINAL vwap would be forward-looking.
                sq_dev = g_vol * (g_tp - running_vwap) ** 2
                if band_lookback and band_lookback > 0:
                    cum_sq_dev = sq_dev.rolling(window=band_lookback, min_periods=1).sum()
                    cum_vol_w = g_vol.rolling(window=band_lookback, min_periods=1).sum()
                else:
                    cum_sq_dev = sq_dev.cumsum()
                    cum_vol_w = cum_vol
                variance = (cum_sq_dev / cum_vol_w).clip(lower=0.0)
                std_vals.loc[group_idx] = variance.pow(0.5).fillna(0.0)

            if vwap_vals.isna().all():
                raise ValueError("All-NaN VWAP — fallback to rolling")
            return vwap_vals.to_numpy(dtype=float), std_vals.to_numpy(dtype=float)

        except Exception:
            pass  # Fall through to rolling fallback

    # Fallback: rolling window (used when no 'time' column available)
    typical_price = pd.Series(tp_a, index=candles.index)
    vol = pd.Series(v_a, index=candles.index)
    tp_vol = typical_price * vol
    cum_tp_vol = tp_vol.rolling(window=anchor_minutes, min_periods=1).sum()
    cum_vol = vol.rolling(window=anchor_minutes, min_periods=1).sum()
    vwap_fallback = cum_tp_vol / cum_vol
    sq_dev = vol * (typical_price - vwap_fallback) ** 2
    cum_sq_dev = sq_dev.rolling(window=anchor_minutes, min_periods=1).sum()
    variance = (cum_sq_dev / cum_vol).clip(lower=0.0)
    std_fallback = variance.pow(0.5).fillna(0.0)
    return vwap_fallback.to_numpy(dtype=float), std_fallback.to_numpy(dtype=float)


@register_strategy("VWAP_v1")
class VWAPEngine(BaseStrategy):
    """
    VWAP v2 — fair value / confluence framework strategy.
    Restricted to real markets; warns on synthetic indices.
    """

    def __init__(self, config: UserConfigV2):
        super().__init__(config)
        self.params = config.vwap
        self.state: dict = {}

    def _init_state(self, symbol: str):
        if symbol not in self.state:
            self.state[symbol] = {
                "trades_today": 0,
                "losses_today": 0,
                "last_trade_date": None,
                "trigger_bar_idx": None,  # Index of the pullback trigger candle
                "pending_entry": False,   # Entry on next bar open
                "entry_direction": None,
                "entry_setup": None,      # [8.1] "PULLBACK_TO_VALUE" | "BAND_REVERSION"
                "trigger_extreme": None,  # [8.3] trigger candle's low (BUY) / high (SELL)
                "trigger_vwap": None,
                "trigger_std": None,
                "pullback_taken_this_session": False,  # [8.3] first_pullback_only
                "prev_distance_to_vwap": None,          # [8.3] convergence tracking
            }

    def _reset_daily(self, symbol: str, today: date_type):
        state = self.state[symbol]
        if state["last_trade_date"] != today:
            state["trades_today"] = 0
            state["losses_today"] = 0
            state["last_trade_date"] = today
            state["trigger_bar_idx"] = None
            state["pending_entry"] = False
            state["entry_direction"] = None
            state["entry_setup"] = None
            state["pullback_taken_this_session"] = False
            state["prev_distance_to_vwap"] = None

    def _et_time_str(self, ts: pd.Timestamp) -> str:
        """Convert timestamp to Eastern Time HH:MM string."""
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        et = ts.astimezone(pytz.timezone("America/New_York"))
        return et.strftime("%H:%M")

    def _is_in_exclusion(self, time_str: str) -> bool:
        """
        Return True if this ET time is OUTSIDE the tradeable window.
        The tradeable window is (session_exclude_end, entry_cutoff).
        """
        return not (self.params.session_exclude_end < time_str < self.params.entry_cutoff)

    def _is_hard_close_time(self, time_str: str) -> bool:
        return time_str >= self.params.hard_close

    def get_required_timeframes(self) -> list[str]:
        return [self.params.entry_timeframe]

    # Index instruments where `sl_points` is a native unit and the fixed-point method
    # from the source strategy is meaningful. Everything else resolves to ATR.
    _INDEX_TOKENS = (
        "NQ", "MNQ", "ES", "MES", "YM", "MYM", "RTY", "M2K",
        "NAS100", "USTEC", "NDX", "US30", "US500", "SPX", "US2000",
        "UK100", "GER40", "DAX", "FRA40", "EU50", "JP225", "HK50",
        "AUS200", "SWI20", "NTH25",
    )

    def _is_index(self, symbol: str) -> bool:
        s = symbol.upper().replace(" ", "")
        return any(tok in s for tok in self._INDEX_TOKENS)

    def _apply_sl_floor(self, symbol: str, sl_dist: float, pip_size: float) -> float:
        """[8.6] Cost-floor application, factored out of _resolve_sl_distance so band-based stops (Setup 1/2) can share it."""
        floor = 0.0
        min_pips = getattr(self.params, "min_sl_pips", 0.0) or 0.0
        if min_pips > 0:
            floor = min_pips * pip_size

        spread_mult = getattr(self.params, "min_sl_spread_mult", 0.0) or 0.0
        if spread_mult > 0:
            try:
                from backend.risk.broker_costs import get_broker_costs
                spread_pips = get_broker_costs(symbol).get("spread_pips", 0.0) or 0.0
                if spread_pips > 0:
                    floor = max(floor, spread_mult * spread_pips * pip_size)
            except Exception:
                pass

        return max(sl_dist, floor) if floor > 0 else sl_dist

    def _resolve_sl_distance(self, symbol: str, atr: float, pip_size: float) -> float:
        """
        Resolve the stop distance in PRICE units per the configured sl_method, then
        apply the absolute cost floors. See params.py header for the derivation of
        every constant referenced here. Used as the ATR/fixed-points FALLBACK when
        vwap_bands_enabled is False (v1 behaviour).
        """
        method = getattr(self.params, "sl_method", "auto")
        if method == "auto":
            method = "fixed_points" if self._is_index(symbol) else "atr_multiple"

        if method == "fixed_points":
            sl_dist = self.params.sl_points
        else:
            k = getattr(self.params, "sl_atr_multiplier", 0.0) or 0.0
            sl_dist = atr * k if k > 0 else 0.0

        return self._apply_sl_floor(symbol, sl_dist, pip_size)

    def _confluence_score(
        self,
        setup: str,
        distance_sigma: float | None,
        direction: str,
        vwap_slope: float,
        momentum_pct: float,
        volume_ratio: float | None,
        time_str: str,
    ) -> int:
        """
        [8.5] Genuine 0-100 confluence score, replacing the hardcoded 80.

        Components (sum to 100):
          40  MANDATORY CHAIN — awarded whenever a signal fires, because the
              state machine already verified every required condition for
              the active setup to get here.
          0-15 VWAP DISTANCE — for PULLBACK_TO_VALUE, closer to VWAP is
              better (a shallow, controlled pullback); for BAND_REVERSION,
              FURTHER beyond the band threshold is better (a more decisive
              extension). Scored the same way either strategy reads
              distance_sigma: PULLBACK scores high near 0, REVERSION scores
              high well past reversion_min_sigma.
          0-15 TREND AGREEMENT — VWAP slope direction and momentum sign both
              agreeing with the trade direction (no HTF market-structure
              detector is wired into this engine, so this is slope+momentum
              only, not the full 3-input version the spec describes).
          0-15 VOLUME CONFIRMATION — trigger-bar volume vs. its rolling mean.
          0-15 SESSION QUALITY — 09:00-11:00 ET scores highest per the
              measured data cited in Part 8 of the plan.

        Range in practice: 40 (bare mandatory chain, everything else 0) -> 100.
        """
        score = 40

        if distance_sigma is not None:
            if setup == "PULLBACK_TO_VALUE":
                # Closer to VWAP (near 0σ) = better controlled pullback.
                if distance_sigma <= 0.25:
                    score += 15
                elif distance_sigma <= 0.5:
                    score += 8
            else:  # BAND_REVERSION
                min_sigma = getattr(self.params, "reversion_min_sigma", 2.0) or 2.0
                excess = distance_sigma - min_sigma
                if excess >= 0.5:
                    score += 15
                elif excess >= 0.15:
                    score += 8

        is_buy = direction == "BUY"
        slope_agrees = (vwap_slope > 0) == is_buy if setup == "PULLBACK_TO_VALUE" else True
        momentum_agrees = (momentum_pct > 0) == is_buy if setup == "PULLBACK_TO_VALUE" else (
            # Reversion WANTS momentum to NOT confirm the extension direction.
            (momentum_pct > 0) != is_buy
        )
        trend_points = 0
        if slope_agrees:
            trend_points += 8
        if momentum_agrees:
            trend_points += 7
        score += min(15, trend_points)

        if volume_ratio is not None:
            mult = getattr(self.params, "volume_confirmation_mult", 1.2) or 1.2
            if mult > 0:
                if volume_ratio >= mult * 1.5:
                    score += 15
                elif volume_ratio >= mult:
                    score += 8

        # Session quality: 09:00-11:00 ET best per the measured data.
        if "09:00" <= time_str <= "11:00":
            score += 15
        elif "08:00" <= time_str <= "14:00":
            score += 8

        return max(0, min(100, int(score)))

    def notify_outcome(self, symbol: str, group_id: str, is_win: bool, pnl: float) -> None:
        """
        Called by the backtester/live engine after a full trade group closes.
        Increments losses_today so the 2-loss-per-day cap (spec §7) is enforced.
        """
        self._init_state(symbol)
        state = self.state[symbol]
        if not is_win:
            state["losses_today"] += 1
            self.log_event(
                f"[{symbol}] VWAP loss recorded. losses_today={state['losses_today']} / max={self.params.max_losses_per_day}",
                category="VWAP",
            )

    async def on_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame) -> TradeSignal | None:
        if timeframe != self.params.entry_timeframe:
            return None

        # [T1.3] Open a confluence-telemetry record for this bar. No-op when
        # `self.gates.enabled` is False (the live default).
        self.begin_candidate(symbol, timeframe, bar_time=candles.index[-1] if len(candles) else None)

        self._init_state(symbol)
        state = self.state[symbol]

        # Determine entry timeframe minutes (e.g., 'M5' -> 5)
        tf_str = self.params.entry_timeframe
        tf_mins = int(tf_str[1:]) if tf_str.startswith('M') else 5
        bar_multiplier = self.params.vwap_anchor_minutes // tf_mins
        actual_lookback = self.params.momentum_lookback_bars * bar_multiplier

        vol_lookback = getattr(self.params, "volume_confirmation_lookback_bars", 20) or 20
        if len(candles) < max(actual_lookback + 5, vol_lookback + 1):
            return None

        current_time = candles.index[-1]
        today = current_time.date()
        self._reset_daily(symbol, today)

        # Synthetic check — warn but allow (user's choice)
        if _is_synthetic(symbol):
            logger.warning(
                f"[VWAP] {symbol} appears to be a synthetic index. "
                "VWAP is designed for real markets with institutional order flow. "
                "Results may be unreliable."
            )

        time_str = self._et_time_str(current_time)

        # Guard rails
        if not self.gate("daily_trade_cap", state["trades_today"] < self.params.max_trades_per_day):
            return None
        if not self.gate("daily_loss_cap", state["losses_today"] < self.params.max_losses_per_day):
            return None

        latest = candles.iloc[-1]
        prev = candles.iloc[-2]

        # [8.2] Calculate anchored VWAP + bands
        bands_enabled = getattr(self.params, "vwap_bands_enabled", True)
        vwap_series, std_series = _calculate_anchored_vwap_with_bands(
            candles, self.params.vwap_anchor_minutes,
            getattr(self.params, "vwap_band_lookback", 0) or 0,
        )
        vwap_now = vwap_series[-1]
        vwap_prev = vwap_series[-(bar_multiplier + 1)] if len(vwap_series) > bar_multiplier else vwap_now
        std_now = float(std_series[-1]) if bands_enabled and not np.isnan(std_series[-1]) else 0.0

        sigmas = getattr(self.params, "vwap_band_sigmas", [1.0, 2.0, 3.0]) or [1.0, 2.0, 3.0]
        sigma1 = sigmas[0] if len(sigmas) > 0 else 1.0
        sigma2 = sigmas[1] if len(sigmas) > 1 else 2.0
        sigma3 = sigmas[2] if len(sigmas) > 2 else 3.0

        # ── Entry from pending trigger (enter at next bar open after trigger) ──
        if state["pending_entry"] and state["trigger_bar_idx"] is not None:
            state["pending_entry"] = False
            direction = state["entry_direction"]
            setup = state["entry_setup"]
            trigger_extreme = state["trigger_extreme"]
            trigger_vwap = state["trigger_vwap"]
            trigger_std = state["trigger_std"] or 0.0
            state["entry_direction"] = None
            state["entry_setup"] = None
            state["trigger_bar_idx"] = None

            # Second exclusion check, on the ENTRY bar rather than the trigger
            # bar. Instrumented under the same gate name as the trigger-side
            # check — otherwise disabling `session_exclusion` for an ablation
            # left this one still enforcing, and the ablation silently measured
            # nothing (signal count unchanged at 14 -> 14).
            if not self.gate("session_exclusion", not self._is_in_exclusion(time_str)):
                return None

            entry = latest["open"]
            atr = calculate_atr(candles)
            pip_size = get_pip_size(symbol)
            target_mode = getattr(self.params, "target_mode", "SIGMA_BAND")
            structural_tp = None

            if bands_enabled and setup == "PULLBACK_TO_VALUE":
                # [8.3] Stop below the pullback swing low (trigger candle's
                # extreme) or -1σ, whichever is FURTHER from entry.
                band1_ref = trigger_vwap - sigma1 * trigger_std if direction == "BUY" else trigger_vwap + sigma1 * trigger_std
                if direction == "BUY":
                    sl = min(trigger_extreme, band1_ref)
                else:
                    sl = max(trigger_extreme, band1_ref)
                sl_dist = abs(entry - sl)
                sl_dist = self._apply_sl_floor(symbol, sl_dist, pip_size)
                sl = entry - sl_dist if direction == "BUY" else entry + sl_dist
                structural_tp = trigger_vwap + sigma2 * trigger_std if direction == "BUY" else trigger_vwap - sigma2 * trigger_std
                tp = structural_tp if target_mode == "SIGMA_BAND" else (entry + sl_dist if direction == "BUY" else entry - sl_dist)
            elif bands_enabled and setup == "BAND_REVERSION":
                # [8.4] Stop beyond ±3σ. Target = VWAP (fair value).
                band3_ref = trigger_vwap + sigma3 * trigger_std if direction == "SELL" else trigger_vwap - sigma3 * trigger_std
                sl_dist = abs(entry - band3_ref)
                sl_dist = self._apply_sl_floor(symbol, sl_dist, pip_size)
                sl = entry - sl_dist if direction == "BUY" else entry + sl_dist
                structural_tp = trigger_vwap
                tp = structural_tp if target_mode == "SIGMA_BAND" else (entry + sl_dist if direction == "BUY" else entry - sl_dist)
            else:
                # v1 fallback (bands disabled, or entry_mode somehow unset).
                sl_dist = self._resolve_sl_distance(symbol, atr, pip_size)
                sl = entry - sl_dist if direction == "BUY" else entry + sl_dist
                tp = entry + sl_dist if direction == "BUY" else entry - sl_dist

            if sl_dist <= 0 or abs(tp - entry) < 1e-10:
                self.log_event(f"[{symbol}] VWAP {setup} degenerate SL/TP — discarding.", category="VWAP")
                return None

            state["trades_today"] += 1
            if setup == "PULLBACK_TO_VALUE":
                state["pullback_taken_this_session"] = True
            state["slope"] = float(vwap_now - vwap_prev)

            confluence_score = self._confluence_score(
                setup=setup,
                distance_sigma=(abs(entry - trigger_vwap) / trigger_std) if trigger_std > 0 else None,
                direction=direction,
                vwap_slope=state["slope"],
                momentum_pct=state.get("trigger_momentum_pct", 0.0),
                volume_ratio=state.get("trigger_volume_ratio"),
                time_str=time_str,
            )

            self.log_event(
                f"[{symbol}] VWAP {setup} {direction} ENTRY @ {entry:.5f} | SL: {sl:.5f} | "
                f"TP: {tp:.5f} | confluence: {confluence_score}",
                category="VWAP",
            )

            structural_tp_rr = (abs(tp - entry) / sl_dist) if sl_dist > 0 else None

            # [V1 §C.6] Chart markings — every level this setup actually
            # measured against, emitted from the branch that measured it. The
            # trigger bar is `prev`: entry is by construction the open of the
            # bar AFTER the trigger candle (see the pending_entry mechanism).
            mk = MarkingCollector(timeframe)
            trigger_t = ts(candles.index[-2])
            entry_t = ts(current_time)

            mk.level(
                "VWAP (anchor)", trigger_vwap, trigger_t,
                role=ROLE_CONFLUENCE, color="rgba(59,130,246,0.9)",
                anchor_minutes=self.params.vwap_anchor_minutes,
                slope=round(state["slope"], 6),
                slope_direction="rising" if state["slope"] > 0 else ("falling" if state["slope"] < 0 else "flat"),
            )
            if bands_enabled and trigger_std > 0:
                # The band grid, drawn from the trigger bar's own sigma — not
                # the current bar's, so what you see is what the entry used.
                for mult, tag in ((sigma1, "1"), (sigma2, "2"), (sigma3, "3")):
                    for sign, side in ((1, "+"), (-1, "-")):
                        mk.level(
                            f"{side}{tag}σ", trigger_vwap + sign * mult * trigger_std, trigger_t,
                            role=ROLE_CONTEXT, color="rgba(148,163,184,0.45)",
                            sigma_multiple=mult, std=round(trigger_std, 6),
                        )
                mk.zone(
                    "Value area (±1σ)",
                    trigger_vwap + sigma1 * trigger_std,
                    trigger_vwap - sigma1 * trigger_std,
                    trigger_t, end_time=entry_t, role=ROLE_CONTEXT,
                    color="rgba(59,130,246,0.08)",
                )

            distance_sigma = (abs(entry - trigger_vwap) / trigger_std) if trigger_std > 0 else None
            mk.structure(
                f"{setup} trigger", trigger_t, price=trigger_extreme, role=ROLE_TRIGGER,
                setup=setup, direction=direction,
                trigger_extreme=round(float(trigger_extreme), 6) if trigger_extreme is not None else None,
                distance_sigma=round(distance_sigma, 3) if distance_sigma is not None else None,
                momentum_pct=round(float(state.get("trigger_momentum_pct", 0.0) or 0.0), 4),
                volume_ratio=state.get("trigger_volume_ratio"),
                session_time_et=time_str,
            )
            mk.level(
                "Stop loss", sl, entry_t, role=ROLE_INVALIDATION,
                color="rgba(239,68,68,0.9)",
                sl_distance=round(sl_dist, 6),
                sl_basis="pullback extreme vs ±1σ" if setup == "PULLBACK_TO_VALUE" else "±3σ band",
                sl_pips=round(sl_dist / pip_size, 2) if pip_size else None,
            )
            if structural_tp is not None:
                mk.level(
                    "Structural TP", structural_tp, entry_t, role=ROLE_CONTEXT,
                    color="rgba(16,185,129,0.9)",
                    target_mode=target_mode,
                    rr=round(structural_tp_rr, 3) if structural_tp_rr is not None else None,
                    basis="return to VWAP" if setup == "BAND_REVERSION" else f"+{sigma2}σ band",
                )

            return self._tag_signal(TradeSignal(
                strategy_id="VWAP_v1",
                symbol=symbol,
                direction=direction,
                signal_type=f"VWAP_{setup}",
                timeframe=timeframe,
                entry_price=entry,
                stop_loss=sl,
                take_profit=tp,
                confluence_score=confluence_score,
                timestamp=float(latest.get("time", current_time.timestamp())),
                metadata={
                    "setup": setup,
                    "vwap": round(vwap_now, 5),
                    "slope": state.get("slope", 0.0),
                    "hard_close_time": self.params.hard_close,
                    # [8.6/P2-X5] Same declared-structural-target pattern CRT
                    # uses, so the RiskParams TP grid can be exempted rather
                    # than silently overriding a target the setup doesn't
                    # believe in (a fixed R-grid has no relationship to
                    # "price returns to VWAP" or "+2σ").
                    "structural_tp": float(structural_tp) if structural_tp is not None else None,
                    "structural_tp_rr": round(structural_tp_rr, 3) if structural_tp_rr is not None else None,
                    "tp_is_structural": target_mode == "SIGMA_BAND",
                    "target_mode": target_mode,
                    # [V1] Chart geometry — consumed by trade_grouper.py's
                    # smc_data build and the replay chart's overlay layer.
                    **mk.as_metadata(),
                },
            ))

        # ── Session exclusion check ──────────────────────────────────────
        if not self.gate("session_exclusion", not self._is_in_exclusion(time_str)):
            return None

        # ── Bias: price vs VWAP ──────────────────────────────────────────
        price_above_vwap = latest["close"] > vwap_now
        price_below_vwap = latest["close"] < vwap_now

        # ── VWAP slope ───────────────────────────────────────────────────
        vwap_rising = vwap_now > vwap_prev
        vwap_falling = vwap_now < vwap_prev
        slope = vwap_now - vwap_prev

        # ── Momentum (lookback price move) ────────────────────────────────
        lookback_close = candles["close"].iloc[-(actual_lookback + 1)]
        price_move_pct = (latest["close"] - lookback_close) / lookback_close * 100 if lookback_close else 0.0

        momentum_up = price_move_pct >= self.params.momentum_threshold_pct
        momentum_down = price_move_pct <= -self.params.momentum_threshold_pct
        momentum_flat = not momentum_up and not momentum_down

        # ── Volume confirmation ───────────────────────────────────────────
        volume_ratio = None
        vol_mult = getattr(self.params, "volume_confirmation_mult", 0.0) or 0.0
        if "volume" in candles.columns and candles["volume"].sum() > 0:
            recent_mean_vol = candles["volume"].iloc[-(vol_lookback + 1):-1].mean()
            if recent_mean_vol and recent_mean_vol > 0:
                volume_ratio = float(latest["volume"]) / float(recent_mean_vol)
        volume_ok = (vol_mult <= 0) or (volume_ratio is not None and volume_ratio >= vol_mult)

        entry_mode = getattr(self.params, "entry_mode", "BOTH")
        if not bands_enabled:
            entry_mode = "PULLBACK_TO_VALUE"  # bands required for BAND_REVERSION

        distance_now = abs(latest["close"] - vwap_now)
        distance_prev = abs(prev["close"] - vwap_prev) if not pd.isna(vwap_prev) else distance_now
        converging = distance_now < distance_prev

        # ═══ SETUP 1: PULLBACK TO VALUE (trend continuation) ═══════════════
        if entry_mode in ("PULLBACK_TO_VALUE", "BOTH"):
            first_only = getattr(self.params, "first_pullback_only", True)
            already_taken = first_only and state.get("pullback_taken_this_session")
            max_dist_sigma = getattr(self.params, "pullback_max_distance_sigma", 1.0) or 1.0
            inside_band = (not bands_enabled) or std_now <= 0 or (distance_now <= max_dist_sigma * std_now)
            requires_convergence = getattr(self.params, "pullback_requires_convergence", True)

            # [T1.3] Each condition is recorded independently rather than as
            # one fused `and`, so the ablation study can rank them separately.
            _g_first   = self.gate("pullback_first_only", not already_taken)
            _g_band    = self.gate("pullback_inside_band", inside_band)
            _g_vol     = self.gate("volume_confirmation", volume_ok)
            if _g_first and _g_band and _g_vol:
                _g_bias  = self.gate("vwap_bias_side", price_above_vwap or price_below_vwap)
                _g_slope = self.gate("vwap_slope_aligned",
                                     (price_above_vwap and vwap_rising) or (price_below_vwap and vwap_falling))
                _g_mom   = self.gate("momentum_aligned",
                                     (price_above_vwap and momentum_up) or (price_below_vwap and momentum_down))
                if price_above_vwap and vwap_rising and momentum_up:
                    is_pullback_candle = (converging if requires_convergence else (latest["close"] < latest["open"]))
                    if self.gate("pullback_convergence", is_pullback_candle):
                        state["pending_entry"] = True
                        state["trigger_bar_idx"] = current_time
                        state["entry_direction"] = "BUY"
                        state["entry_setup"] = "PULLBACK_TO_VALUE"
                        state["trigger_extreme"] = float(latest["low"])
                        state["trigger_vwap"] = float(vwap_now)
                        state["trigger_std"] = std_now
                        state["trigger_momentum_pct"] = float(price_move_pct)
                        state["trigger_volume_ratio"] = volume_ratio
                        self.log_event(
                            f"[{symbol}] VWAP Setup1 LONG trigger: pullback converging to value. Entry next bar.",
                            category="VWAP",
                        )
                        return None
                elif price_below_vwap and vwap_falling and momentum_down:
                    is_pullback_candle = (converging if requires_convergence else (latest["close"] > latest["open"]))
                    if self.gate("pullback_convergence", is_pullback_candle):
                        state["pending_entry"] = True
                        state["trigger_bar_idx"] = current_time
                        state["entry_direction"] = "SELL"
                        state["entry_setup"] = "PULLBACK_TO_VALUE"
                        state["trigger_extreme"] = float(latest["high"])
                        state["trigger_vwap"] = float(vwap_now)
                        state["trigger_std"] = std_now
                        state["trigger_momentum_pct"] = float(price_move_pct)
                        state["trigger_volume_ratio"] = volume_ratio
                        self.log_event(
                            f"[{symbol}] VWAP Setup1 SHORT trigger: pullback converging to value. Entry next bar.",
                            category="VWAP",
                        )
                        return None

        # ═══ SETUP 2: BAND REVERSION (mean reversion, new — 8.4) ═══════════
        if bands_enabled and std_now > 0 and entry_mode in ("BAND_REVERSION", "BOTH"):
            min_sigma = getattr(self.params, "reversion_min_sigma", 2.0) or 2.0
            upper_band = vwap_now + min_sigma * std_now
            lower_band = vwap_now - min_sigma * std_now

            requires_flat_slope = True  # "flat VWAP slope" is unconditional per the spec
            max_slope_atr_pct = getattr(self.params, "reversion_max_vwap_slope_atr_pct", 0.10) or 0.10
            atr_for_slope = calculate_atr(candles)
            slope_is_flat = (not requires_flat_slope) or atr_for_slope <= 0 or (abs(slope) <= max_slope_atr_pct * atr_for_slope)

            requires_trend_neutral = getattr(self.params, "reversion_requires_trend_neutral", True)
            requires_rejection = getattr(self.params, "reversion_requires_rejection", True)
            min_wick_pct = getattr(self.params, "reversion_min_rejection_wick_pct", 0.50) or 0.50
            total_range = latest["high"] - latest["low"]

            _g_flat = self.gate("reversion_slope_flat", slope_is_flat)
            _g_rvol = self.gate("volume_confirmation", volume_ok)
            if _g_flat and _g_rvol and total_range > 0:
                # Closed beyond the UPPER band → expect reversion DOWN → SELL.
                # Recorded for its own sake — how often price even reaches the
                # band is the denominator for every other reversion stat.
                self.gate("reversion_beyond_band",
                          latest["close"] >= upper_band or latest["close"] <= lower_band)
                if latest["close"] >= upper_band and self.gate(
                    "reversion_trend_neutral",
                    (not requires_trend_neutral or momentum_flat or momentum_down),
                ):
                    upper_wick = latest["high"] - max(latest["open"], latest["close"])
                    rejection_ok = (not requires_rejection) or (upper_wick / total_range >= min_wick_pct)
                    # The "wick rejection" confluence, recorded with the actual
                    # wick fraction so its predictive value is measurable.
                    if self.gate("wick_rejection", rejection_ok,
                                 detail=f"wick_pct={upper_wick / total_range:.3f}"):
                        state["pending_entry"] = True
                        state["trigger_bar_idx"] = current_time
                        state["entry_direction"] = "SELL"
                        state["entry_setup"] = "BAND_REVERSION"
                        state["trigger_extreme"] = float(latest["high"])
                        state["trigger_vwap"] = float(vwap_now)
                        state["trigger_std"] = std_now
                        state["trigger_momentum_pct"] = float(price_move_pct)
                        state["trigger_volume_ratio"] = volume_ratio
                        self.log_event(
                            f"[{symbol}] VWAP Setup2 SHORT trigger: band reversion from +{min_sigma:g}σ. Entry next bar.",
                            category="VWAP",
                        )
                        return None
                # Closed beyond the LOWER band → expect reversion UP → BUY.
                elif latest["close"] <= lower_band and self.gate(
                    "reversion_trend_neutral",
                    (not requires_trend_neutral or momentum_flat or momentum_up),
                ):
                    lower_wick = min(latest["open"], latest["close"]) - latest["low"]
                    rejection_ok = (not requires_rejection) or (lower_wick / total_range >= min_wick_pct)
                    if self.gate("wick_rejection", rejection_ok,
                                 detail=f"wick_pct={lower_wick / total_range:.3f}"):
                        state["pending_entry"] = True
                        state["trigger_bar_idx"] = current_time
                        state["entry_direction"] = "BUY"
                        state["entry_setup"] = "BAND_REVERSION"
                        state["trigger_extreme"] = float(latest["low"])
                        state["trigger_vwap"] = float(vwap_now)
                        state["trigger_std"] = std_now
                        state["trigger_momentum_pct"] = float(price_move_pct)
                        state["trigger_volume_ratio"] = volume_ratio
                        self.log_event(
                            f"[{symbol}] VWAP Setup2 LONG trigger: band reversion from -{min_sigma:g}σ. Entry next bar.",
                            category="VWAP",
                        )
                        return None

        return None
