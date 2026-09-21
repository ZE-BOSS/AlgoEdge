"""
backend/strategies/strategy_ivw/engine.py

IVW_v1 — Implied Volatility Walls, traded as a breakout.

THE RULE
--------
1. Each broker day (UTC), the walls sit at the day's open ± the `wall_percentile`
   of the previous `lookback_days` sessions' largest move away from their open
   (up moves for the upper wall, down moves for the lower).
2. The first M5 bar of the day that CLOSES beyond a wall is the only candidate —
   above the upper wall buy, below the lower sell. A later break the other way
   is ignored, as is any break on the day's last bar.
3. It is traded only if the day starts in the `regime_filter` cumulative-volatility
   regime (20-session summed range vs its last 250 sessions), and — with
   `require_bubble` — the breakout bar is a liquidation bubble: its extreme ≥ 2σ
   beyond an inverse-volume-weighted equilibrium of the previous 50 bars, with
   volume ≥ 2σ above theirs.
4. Stop `stop_width_frac` of the wall's width back; target one width on (1:2);
   flat at the end of the day.

WHAT WAS MEASURED (scratchpad ivw study, 2026-09-19; 16 markets, M5 2013-2026)
------------------------------------------------------------------------------
Fading the walls — what the indicator is sold for — lost on nearly every market
in every variant. Breakouts were break-even overall; the three filters above
were each weak alone and positive together. Chosen on 2013-2019, then judged on
2020-2026, which the choice never saw:

    rule B (these defaults)  2013-19 +0.045R | 2020-26 +0.152R (t 3.3, n 512)
    rule A (q70, forecast "contracting" instead of regime "low")
                             2013-19 +0.096R | 2020-26 +0.100R (t 2.6, n 931)

tests/test_ivw_strategy.py holds the engine, fed through BarFeed, to that
study's entry decision bar for bar.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from backend.strategies.base_strategy import BaseStrategy, TradeAction, TradeSignal
from backend.strategies.core.bars import _epoch_seconds
from backend.strategies.registry import register_strategy
from backend.strategies.strategy_ivw.params import IVWParams
from backend.utils.logger import get_logger

logger = get_logger(__name__)

DAY = 86400
MIN_SESSION_BARS = 100     # M5 bars; thinner days (holidays, weekend stubs) are not sessions
REGIME_SUM_DAYS = 20
REGIME_RANK_DAYS = 250
HAR_FIT_DAYS = 500
HAR_LAGS = 22
FORECAST_NORM_DAYS = 60


# ── pure calculations (the research study's, shared with the tests) ────────────

def walls(opens: np.ndarray, highs: np.ndarray, lows: np.ndarray, today_open: float,
          percentile: float, lookback: int) -> tuple[float, float] | None:
    """(upper, lower) for a day opening at `today_open`, from completed sessions."""
    if len(opens) < lookback or today_open <= 0:
        return None
    o, h, lo = opens[-lookback:], highs[-lookback:], lows[-lookback:]
    up = float(np.percentile((h - o) / o, percentile))
    dn = float(np.percentile((o - lo) / o, percentile))
    upper, lower = today_open * (1 + up), today_open * (1 - dn)
    if upper <= today_open or lower >= today_open:
        return None
    return upper, lower


def regime(ranges: np.ndarray) -> str | None:
    """"low" / "mid" / "high": the last `REGIME_SUM_DAYS` sessions' summed range,
    ranked against the same sum on each of the previous `REGIME_RANK_DAYS` days."""
    n = len(ranges)
    if n < REGIME_SUM_DAYS + REGIME_RANK_DAYS:
        return None
    cs = np.r_[0.0, np.cumsum(ranges)]
    now = cs[n] - cs[n - REGIME_SUM_DAYS]
    ends = np.arange(n - REGIME_RANK_DAYS, n)
    past = cs[ends] - cs[ends - REGIME_SUM_DAYS]
    pct = float((past < now).mean())
    return "low" if pct < 1 / 3 else ("high" if pct > 2 / 3 else "mid")


def har_ratio(ranges: np.ndarray) -> float:
    """HAR forecast of today's range over its recent mean; NaN without history.
    The study refit every 20 days; refitting every day uses the same data window."""
    n = len(ranges)
    if n < HAR_FIT_DAYS + HAR_LAGS:
        return float("nan")
    cs = np.r_[0.0, np.cumsum(ranges)]
    idx = np.arange(n - HAR_FIT_DAYS, n)
    X = np.column_stack([np.ones(len(idx)), ranges[idx - 1],
                         (cs[idx] - cs[idx - 5]) / 5, (cs[idx] - cs[idx - 22]) / 22])
    beta, *_ = np.linalg.lstsq(X, ranges[idx], rcond=None)
    f = float(beta @ np.array([1.0, ranges[n - 1], (cs[n] - cs[n - 5]) / 5, (cs[n] - cs[n - 22]) / 22]))
    norm = float(ranges[-FORECAST_NORM_DAYS:].mean())
    return f / norm if norm > 0 else float("nan")


def forecast_label(ratio: float) -> str:
    if not math.isfinite(ratio):
        return "n/a"
    return "contracting" if ratio < 0.9 else ("expanding" if ratio > 1.1 else "normal")


def bubble(close: np.ndarray, high: np.ndarray, low: np.ndarray, vol: np.ndarray, j: int, up: bool,
           lookback: int = 50, price_sigma: float = 2.0, volume_sigma: float = 2.0) -> bool:
    """OmegaTools' liquidation bubble on completed bar j, from the bars before it."""
    a = max(0, j - lookback)
    if j - a < 10:
        return False
    vv = vol[a:j] + 1e-9
    w = 1 / vv
    eq = float((close[a:j] * w).sum() / w.sum())
    sd = float(close[a:j].std())
    vsd = float(vv.std())
    if sd <= 0 or vsd <= 0:
        return False
    ps = ((high[j] - eq) if up else (eq - low[j])) / sd
    vs = (vol[j] - vv.mean()) / vsd
    return bool(ps >= price_sigma and vs >= volume_sigma)


def sessions_from_m5(t: np.ndarray, o: np.ndarray, h: np.ndarray, lo: np.ndarray):
    """(day, open, high, low) of each UTC day with at least MIN_SESSION_BARS M5 bars."""
    day = t // DAY
    bounds = np.flatnonzero(np.diff(day)) + 1
    starts, ends = np.r_[0, bounds], np.r_[bounds, len(day)]
    keep = (ends - starts) >= MIN_SESSION_BARS
    starts, ends = starts[keep], ends[keep]
    return (day[starts], o[starts], np.array([h[s:e].max() for s, e in zip(starts, ends)]),
            np.array([lo[s:e].min() for s, e in zip(starts, ends)]))


def _times(candles: pd.DataFrame) -> np.ndarray:
    """Epoch seconds per row. Same result as core.bars._epoch_seconds without
    building a new DatetimeIndex — this runs on every M5 bar."""
    if "time" in candles.columns:
        return candles["time"].to_numpy(dtype=np.int64)
    return candles.index.values.astype("datetime64[s]").astype(np.int64)


def _volume(candles: pd.DataFrame) -> np.ndarray | None:
    for name in ("tick_volume", "volume", "tickvol", "real_volume"):
        if name in candles.columns:
            return candles[name].to_numpy(dtype=float)
    return None


@register_strategy("IVW_v1")
class IVWStrategy(BaseStrategy):
    strategy_id = "IVW_v1"
    LIVE_POSITION_EXITS = True     # the day-end flat is part of the measured rule
    # the bars live position management hands on_position_bar (position_manager);
    # D1 is only the walls' history
    TIMEFRAME = "M5"

    def __init__(self, config: Any):
        super().__init__(config)
        self.params = getattr(config, "ivw", None) or IVWParams()
        # per symbol: completed daily sessions (day number, open, high, low), from D1
        self._days: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = {}
        # per symbol: the day's walls, regime and forecast. Pure functions of the
        # completed sessions and the day's open, cached only because they are the
        # same on every M5 bar of the day — never a decision carried between bars.
        self._day_ctx: dict[str, tuple[tuple, Any]] = {}

    # D1 first: BarFeed hands timeframes in this order, so yesterday's session is
    # stored before the day's first M5 bar is judged.
    def get_required_timeframes(self) -> list[str]:
        return ["D1", "M5"]

    @property
    def WINDOW_BARS(self) -> dict[str, int]:  # noqa: N802 — read by strategies.windows
        p = self.params
        need = max(int(p.lookback_days), REGIME_SUM_DAYS + REGIME_RANK_DAYS)
        if p.forecast_filter == "contracting":
            need = max(need, HAR_FIT_DAYS + HAR_LAGS)
        # FX has a Sunday stub row a week that the session filter drops (6 rows per
        # 5 sessions); M5 covers a full UTC day plus the bubble's 50-bar look-back
        return {"D1": int(need * 1.25) + 20, "M5": 400}

    async def initialize(self):
        return None

    async def on_tick(self, symbol: str, tick: dict[str, Any]) -> None:
        return None

    def _store_days(self, symbol: str, candles: pd.DataFrame) -> None:
        t = _epoch_seconds(candles)
        o = candles["open"].to_numpy(dtype=float)
        h = candles["high"].to_numpy(dtype=float)
        lo = candles["low"].to_numpy(dtype=float)
        keep = session_mask(candles)
        self._days[symbol] = (t[keep] // DAY, o[keep], h[keep], lo[keep])

    def _day_context(self, symbol: str, today: int, today_open: float):
        """(walls or None, regime, forecast ratio) for `today`, or None without
        any completed session before it."""
        days = self._days.get(symbol)
        if days is None:
            return None
        d, o, h, lo = days
        n = int(np.searchsorted(d, today, side="left"))   # completed sessions before today
        if n == 0:
            return None
        key = (today, today_open, n, float(o[n - 1]))
        cached = self._day_ctx.get(symbol)
        if cached is not None and cached[0] == key:
            return cached[1]
        p = self.params
        o, h, lo = o[:n], h[:n], lo[:n]
        rng = (h - lo) / o
        ratio = har_ratio(rng) if p.forecast_filter == "contracting" else float("nan")
        ctx = (walls(o, h, lo, today_open, float(p.wall_percentile), int(p.lookback_days)), regime(rng), ratio)
        self._day_ctx[symbol] = (key, ctx)
        return ctx

    async def on_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame) -> TradeSignal | None:
        self.begin_candidate(
            symbol, timeframe,
            bar_time=candles.index[-1] if candles is not None and len(candles) else None,
        )
        if candles is None or not len(candles):
            return None
        if timeframe == "D1":
            self._store_days(symbol, candles)
            return None
        if timeframe != "M5" or len(candles) < 20:
            return None

        p = self.params
        t = _times(candles)
        c = candles["close"].to_numpy(dtype=float)
        j = len(t) - 1
        ti = int(t[j])
        today = ti // DAY
        day_end = (today + 1) * DAY
        # the entry is the next bar's open, which must still be today
        if not self.gate("entry_bar_same_day", ti + 300 < day_end):
            return None

        # the day's first bar must be in the window, with yesterday's last before it
        first = int(np.searchsorted(t, today * DAY, side="left"))
        if not self.gate("day_open_in_window", first > 0):
            return None
        # A day that opens too late to reach MIN_SESSION_BARS (the FX/index Sunday
        # stub) was never a session in the study, so it is not traded either.
        if not self.gate("full_session", int(t[first]) - today * DAY <= DAY - MIN_SESSION_BARS * 300,
                         "day opened too late to be a full session"):
            return None
        today_open = float(candles.iat[first, candles.columns.get_loc("open")])
        ctx = self._day_context(symbol, today, today_open)
        if not self.gate("daily_history", ctx is not None, "no completed daily sessions yet"):
            return None
        w, reg, fratio = ctx
        if not self.gate("walls", w is not None, "not enough sessions for the walls"):
            return None
        upper, lower = w

        # first close beyond either wall today — stateless, so live and backtest agree
        day_c = c[first:j]
        if day_c.size and bool(((day_c > upper) | (day_c < lower)).any()):
            self.gate("first_break_of_day", False, "a wall already broke today")
            return None
        direction = 1 if c[j] > upper else (-1 if c[j] < lower else 0)
        if not self.gate("wall_break_close", direction != 0):
            return None
        if not self.gate("side_allowed", p.side == "both" or (p.side == "long") == (direction > 0)):
            return None

        if p.regime_filter != "any":
            if not self.gate("regime", reg == p.regime_filter,
                             f"regime {reg or 'unknown'}, needs {p.regime_filter}"):
                return None
        if p.forecast_filter == "contracting":
            if not self.gate("forecast", forecast_label(fratio) == "contracting",
                             f"range forecast {forecast_label(fratio)}"):
                return None
        is_bubble = False
        vol = _volume(candles)
        if vol is not None:
            h = candles["high"].to_numpy(dtype=float)
            lo = candles["low"].to_numpy(dtype=float)
            is_bubble = bubble(c, h, lo, vol, j, direction > 0, int(p.bubble_lookback),
                               float(p.bubble_price_sigma), float(p.bubble_volume_sigma))
        if p.require_bubble and not self.gate("bubble", is_bubble, "breakout bar is not a liquidation bubble"):
            return None

        width = (upper - today_open) if direction > 0 else (today_open - lower)
        stop = float(p.stop_width_frac) * width
        if not self.gate("stop_positive", stop > 0):
            return None
        from backend.strategies.strategy_defaults import SLOT_TP1_RR, get_strategy_defaults
        rr = float(SLOT_TP1_RR.get(f"{symbol.upper()}|{self.strategy_id}",
                                   get_strategy_defaults(self.strategy_id).get("tp1_rr", 2.0)))
        entry = float(c[j])
        long = direction > 0
        return self._tag_signal(TradeSignal(
            strategy_id=self.strategy_id, symbol=symbol, direction="BUY" if long else "SELL",
            signal_type="IVW_BREAKOUT", timeframe="M5", entry_price=entry,
            entry_zone_top=upper, entry_zone_bottom=lower,
            stop_loss=entry - stop if long else entry + stop,
            take_profit=entry + rr * stop if long else entry - rr * stop,
            confluence_score=80, timestamp=float(ti),  # pass/fail gates, measured at full risk
            metadata={"size_modifier": 1.0, "trail_method": "NONE", "setup": "ivw_breakout",
                      "upper_wall": upper, "lower_wall": lower, "day_open": today_open,
                      "regime": reg, "forecast": forecast_label(fratio), "bubble": is_bubble,
                      "session_close_ts": day_end, "tp1_rr": rr,
                      "reason": f"IVW {p.wall_percentile:g}th-percentile {'upper' if long else 'lower'} wall "
                                f"broke {'up' if long else 'down'} ({reg or '?'} vol regime"
                                f"{', liquidation bubble' if is_bubble else ''})"},
        ))

    def on_position_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame,
                        position: dict) -> TradeAction | None:
        """Flat at the end of the UTC day — the study held no trade overnight."""
        if not self.params.close_at_day_end or candles is None or not len(candles):
            return None
        last = int(_times(candles)[-1])
        entry_day = _entry_day(position.get("entry_time"))
        # the day's last bar, or a bar already on a later day (weekend/holiday gap)
        if last + 300 >= (last // DAY + 1) * DAY or (entry_day is not None and last // DAY > entry_day):
            return TradeAction(ticket=int(position.get("ticket") or 0), action="CLOSE",
                               close_reason="DAY_END")
        return None


# A full UTC day is 288 M5 bars; the study kept days with >= 100 of them.
THIN_DAY_FRACTION = MIN_SESSION_BARS / 288 * 0.9


def session_mask(d1: pd.DataFrame) -> np.ndarray:
    """D1 rows that are real sessions. The study counted M5 bars (>= 100); a D1
    row cannot, so it counts when its tick volume reaches THIN_DAY_FRACTION of
    the median of it and the 20 rows before it. Checked against the M5 bar count
    on 2013-2026 MT5 data: the same days (Sunday stubs, holiday half-days) on 703
    of 704 FX days, and on every index, metal and oil day."""
    vol = _volume(d1)
    if vol is None or not len(vol):
        return np.ones(len(d1), dtype=bool)
    med = pd.Series(vol).rolling(21, min_periods=5).median().to_numpy()
    med = np.where(np.isfinite(med), med, np.nanmedian(vol))
    return vol >= med * THIN_DAY_FRACTION


def _entry_day(value: Any) -> int | None:
    if value is None:
        return None
    try:
        if isinstance(value, (int, float, np.integer, np.floating)):
            return int(value) // DAY
        return int(pd.Timestamp(value).timestamp()) // DAY
    except (TypeError, ValueError):
        return None
