"""
backend/strategies/strategy_vwap/engine.py

VWAP Drift Pullback Strategy Engine
=====================================
Implements Matteo Conti's "Golden Ticket VWAP" — fully rule-based.

Flow:
  Session gate → Bias (price vs VWAP) → VWAP slope → Momentum → Pullback trigger → Entry

Source: docs/vwap_strategy_implementation_plan.md
"""

import pandas as pd
import pytz
from datetime import date as date_type

from backend.core.config_schema import UserConfigV2
from backend.risk.position_sizer import get_pip_size
from backend.strategies.base_strategy import BaseStrategy, TradeSignal
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


def _calculate_anchored_vwap(candles: pd.DataFrame, anchor_minutes: int) -> pd.Series:
    """
    Calculate true daily-anchored VWAP, resetting to the session open each trading day.

    Spec says "15-min anchored VWAP" — this means a cumulative VWAP anchored to the
    start of each trading session (9:30 ET), not a rolling sliding-window VWAP.
    A rolling window never resets and produces a permanently drifting average that
    fundamentally misrepresents the institutional reference price.

    Algorithm:
      1. Determine the trading date for each bar (using 9:30 ET as the session start).
      2. Within each date group, compute cumulative sum(TP*Vol) / cumsum(Vol).
      3. This gives a proper intraday VWAP that starts fresh each session.

    `anchor_minutes` is kept as a parameter for API compatibility but is no longer used
    (the session-anchor supersedes it). If candles have no 'time' column, falls back to
    the old rolling window.
    """
    typical_price = (candles["high"] + candles["low"] + candles["close"]) / 3

    if "volume" in candles.columns and candles["volume"].sum() > 0:
        tp_vol = typical_price * candles["volume"]
        vol = candles["volume"]
    else:
        tp_vol = typical_price
        vol = pd.Series(1.0, index=candles.index)

    # Try to build a session-date column for grouping
    if "time" in candles.columns:
        try:
            import pytz
            et_tz = pytz.timezone("America/New_York")
            times_utc = pd.to_datetime(candles["time"], unit="s", utc=True)
            times_et = times_utc.dt.tz_convert(et_tz)

            # Session boundary: 09:30 ET.  Any bar before 09:30 belongs to the previous session.
            # Use the calendar date shifted so bars 00:00–09:29 are grouped with the prior day's session.
            session_open_offset = pd.Timedelta(hours=9, minutes=30)
            session_dates = (times_et - session_open_offset).dt.date

            vwap_vals = pd.Series(index=candles.index, dtype=float)
            for _date, group_idx in candles.groupby(session_dates).groups.items():
                g_tp_vol = tp_vol.loc[group_idx]
                g_vol = vol.loc[group_idx]
                cum_tp_vol = g_tp_vol.cumsum()
                cum_vol = g_vol.cumsum()
                # ffill/bfill WITHIN this session group only — a global ffill across the
                # whole concatenated series can leak the previous session's final VWAP
                # value into a NaN bar (e.g. zero-volume bar) at the start of the next
                # session, misrepresenting the fresh session-anchored VWAP.
                vwap_vals.loc[group_idx] = (cum_tp_vol / cum_vol).ffill().bfill()

            if vwap_vals.isna().all():
                raise ValueError("All-NaN VWAP — fallback to rolling")
            return vwap_vals

        except Exception:
            pass  # Fall through to rolling fallback

    # Fallback: rolling window (used when no 'time' column available)
    cum_tp_vol = tp_vol.rolling(window=anchor_minutes, min_periods=1).sum()
    cum_vol = vol.rolling(window=anchor_minutes, min_periods=1).sum()
    return cum_tp_vol / cum_vol


@register_strategy("VWAP_v1")
class VWAPEngine(BaseStrategy):
    """
    VWAP Drift Pullback strategy.
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

    def _et_time_str(self, ts: pd.Timestamp) -> str:
        """Convert timestamp to Eastern Time HH:MM string."""
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        et = ts.astimezone(pytz.timezone("America/New_York"))
        return et.strftime("%H:%M")

    def _is_in_exclusion(self, time_str: str) -> bool:
        """
        Return True if this ET time is OUTSIDE the tradeable window.

        The tradeable window is (session_exclude_end, entry_cutoff) — i.e. after the
        first-hour exclusion has ended and before the entry cutoff.

        BUG FIXED 2026-08: the previous form was

            (session_open <= t <= session_exclude_end) or (t >= entry_cutoff)

        which blocked the first hour and the post-cutoff tail but NEVER blocked the
        PRE-SESSION hours. At t="03:00": "09:30" <= "03:00" is False and
        "03:00" >= "15:30" is False, so the bar was treated as tradeable. VWAP —
        a US-cash-session strategy whose entire premise is the session-anchored
        institutional reference price — was therefore trading right through the
        Asian and London sessions.

        Measured on the supplied runs (EURUSD, n=259): 54% of all entries fell
        outside the intended 10:30-15:30 ET window, clustering at 06:00-08:00 ET,
        with 20 entries labelled ASIAN and 92 labelled LONDON. Anchored VWAP has no
        institutional meaning in those hours — the session it is anchored to has
        not opened yet.
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

    def _resolve_sl_distance(self, symbol: str, atr: float, pip_size: float) -> float:
        """
        Resolve the stop distance in PRICE units per the configured sl_method, then
        apply the absolute cost floors. See params.py header for the derivation of
        every constant referenced here.
        """
        method = getattr(self.params, "sl_method", "auto")
        if method == "auto":
            method = "fixed_points" if self._is_index(symbol) else "atr_multiple"

        if method == "fixed_points":
            # get_pip_size() returns 1.0 for index CFDs, so this is a genuine identity.
            sl_dist = self.params.sl_points * pip_size
        else:
            k = getattr(self.params, "sl_atr_multiplier", 0.0) or 0.0
            sl_dist = atr * k if k > 0 else 0.0

        # ── Cost floors ──────────────────────────────────────────────────────
        # A stop tighter than a few multiples of spread is not a stop — it is hit by
        # the bid/ask bounce at the instant of entry. Floor is the larger of an
        # absolute pip figure and a spread multiple (the latter adapts to news-time
        # spread blowouts that a fixed pip figure cannot see).
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
                # Broker costs unavailable (no MT5, module missing) — the absolute
                # min_sl_pips floor above still applies.
                pass

        if floor > 0 and sl_dist < floor:
            logger.debug(
                f"[{symbol}] SL floored: {sl_dist / pip_size:.1f} → {floor / pip_size:.1f} pips "
                f"(method={method})"
            )
            sl_dist = floor

        return sl_dist

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

        self._init_state(symbol)
        state = self.state[symbol]

        # Determine entry timeframe minutes (e.g., 'M5' -> 5)
        tf_str = self.params.entry_timeframe
        tf_mins = int(tf_str[1:]) if tf_str.startswith('M') else 5
        bar_multiplier = self.params.vwap_anchor_minutes // tf_mins
        actual_lookback = self.params.momentum_lookback_bars * bar_multiplier
        
        if len(candles) < actual_lookback + 5:
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

        # Let the risk engine and backtester handle hard close via the metadata marker
        # if self._is_hard_close_time(time_str):
        #     return None

        # Guard rails
        if state["trades_today"] >= self.params.max_trades_per_day:
            return None
        if state["losses_today"] >= self.params.max_losses_per_day:
            return None

        latest = candles.iloc[-1]
        prev = candles.iloc[-2]

        # Calculate anchored VWAP
        vwap_series = _calculate_anchored_vwap(candles, self.params.vwap_anchor_minutes)
        vwap_now = vwap_series.iloc[-1]
        # Slope must compare against the prior 15-min ANCHOR bar, not the adjacent M5
        # bar — iloc[-2] compared two consecutive M5 bars, which is far noisier than
        # the spec's 15-min-anchor slope. bar_multiplier (M5-bars-per-anchor-window)
        # is already computed above for the momentum lookback; reuse it here.
        vwap_prev = vwap_series.iloc[-(bar_multiplier + 1)] if len(vwap_series) > bar_multiplier else vwap_now

        # ── Entry from pending trigger (enter at next bar open after trigger) ──
        if state["pending_entry"] and state["trigger_bar_idx"] is not None:
            state["pending_entry"] = False
            direction = state["entry_direction"]
            state["entry_direction"] = None
            state["trigger_bar_idx"] = None

            if self._is_in_exclusion(time_str):
                return None

            entry = latest["open"]
            atr = calculate_atr(candles)

            # Stop-loss method resolution — see params.py header for the full rationale.
            # `sl_points` is a native INDEX-POINT figure from the source strategy (80 on NQ)
            # and is meaningless on FX: multiplying it by get_pip_size() gave an 80-PIP stop
            # on USDCHF, which turned this M5 scalper into a ~6.9-day swing hold in the real
            # runs. Per doc §5 ("On any other instrument: convert to an ATR-multiple instead
            # of a fixed point value"), points are used ONLY for index instruments; everything
            # else uses ATR × k. A hard cost floor is then applied under both methods so a
            # dead low-volatility session can never produce a sub-spread stop.
            pip_size = get_pip_size(symbol)
            sl_dist = self._resolve_sl_distance(symbol, atr, pip_size)

            if direction == "BUY":
                sl = entry - sl_dist
                tp = entry + sl_dist  # TP1 = 1R = 40pt = matches source's 40pt target
            else:
                sl = entry + sl_dist
                tp = entry - sl_dist

            state["trades_today"] += 1
            # Record the VWAP slope at entry time (vwap_now/vwap_prev computed above,
            # at the top of this on_bar call) so metadata reflects the real slope
            # instead of always logging the placeholder 0.0.
            state["slope"] = float(vwap_now - vwap_prev)
            self.log_event(
                f"[{symbol}] VWAP {direction} ENTRY @ {entry:.5f} | SL: {sl:.5f} | TP1: {tp:.5f}",
                category="VWAP",
            )

            return TradeSignal(
                strategy_id="VWAP_v1",
                symbol=symbol,
                direction=direction,
                signal_type="VWAP_PULLBACK",
                timeframe=timeframe,
                entry_price=entry,
                stop_loss=sl,
                take_profit=tp,
                confluence_score=80,
                timestamp=float(latest.get("time", current_time.timestamp())),
                metadata={
                    "setup": "VWAP_PULLBACK",
                    "vwap": round(vwap_now, 5),
                    "slope": state.get("slope", 0.0),
                    "hard_close_time": self.params.hard_close,
                },
            )

        # ── Session exclusion check ──────────────────────────────────────
        if self._is_in_exclusion(time_str):
            return None

        # ── Bias: price vs VWAP ──────────────────────────────────────────
        price_above_vwap = latest["close"] > vwap_now
        price_below_vwap = latest["close"] < vwap_now

        # ── VWAP slope ───────────────────────────────────────────────────
        vwap_rising = vwap_now > vwap_prev
        vwap_falling = vwap_now < vwap_prev

        # ── Momentum (1hr price move) ─────────────────────────────────────
        # momentum_lookback_bars is defined in terms of anchor-timeframe bars (e.g. 15m)
        # We are on M5 candles, so we already computed actual_lookback above.
        lookback_close = candles["close"].iloc[-(actual_lookback + 1)]
        price_move_pct = (latest["close"] - lookback_close) / lookback_close * 100

        momentum_up = price_move_pct >= self.params.momentum_threshold_pct
        momentum_down = price_move_pct <= -self.params.momentum_threshold_pct

        # ── Check for pullback trigger candle ────────────────────────────
        # LONG: price above VWAP + VWAP rising + momentum up → look for a red (bearish) candle pulling back toward VWAP
        if price_above_vwap and vwap_rising and momentum_up:
            is_pullback_candle = latest["close"] < latest["open"]  # Red candle
            if is_pullback_candle:
                state["pending_entry"] = True
                state["trigger_bar_idx"] = current_time
                state["entry_direction"] = "BUY"
                self.log_event(
                    f"[{symbol}] VWAP LONG trigger: pullback candle detected. Entry next bar.",
                    category="VWAP",
                )

        # SHORT: price below VWAP + VWAP falling + momentum down → look for a green (bullish) candle pulling back toward VWAP
        elif price_below_vwap and vwap_falling and momentum_down:
            is_pullback_candle = latest["close"] > latest["open"]  # Green candle
            if is_pullback_candle:
                state["pending_entry"] = True
                state["trigger_bar_idx"] = current_time
                state["entry_direction"] = "SELL"
                self.log_event(
                    f"[{symbol}] VWAP SHORT trigger: pullback candle detected. Entry next bar.",
                    category="VWAP",
                )

        return None
