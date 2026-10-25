"""
backend/strategies/strategy_roboquant/engine.py

Five strategies replicated from roboquant.dev's template library, read from
their source on 2026-10-19 (see research/roboquant/REPLICATION-2026-10-19.md):

    RQ_NQPrecision_v1      NQ Precision Bot                    M1
    RQ_NYORB_v1            NY Open Range Breakout              M15
    RQ_CrazyORB_v1         Crazy ORB                           M15
    RQ_TMeanReversion_v1   T-Mean Reversion                    H1
    RQ_CharlesMonger_v1    Charles Monger Investment Strategy  H1

STATELESS BY DESIGN
-------------------
roboquant's engines keep per-day state (range, "traded today", last regime).
These recompute it from the window on every call - today's range from today's
bars, "already traded" by replaying today's earlier bars - so the live feed and
a backtest, which hand the engine the same windows, cannot disagree.

WHAT DIFFERS FROM ROBOQUANT, ON PURPOSE
---------------------------------------
* Fills: roboquant fills a bar strategy's market order at the next bar's open;
  so does this app's engine. NQ Precision Bot trades on TICKS upstream (a
  09:59:55 snapshot); here it is the 09:59 one-minute bar's close, filled at
  10:00 - five seconds later.
* Sizing: roboquant uses fixed futures contracts / fixed dollars; here the
  risk engine sizes from the stop. Results are compared in R and win rate.
* Instruments: NQ/MNQ -> US Tech 100, ES/MES -> US SP 500 (Deriv), NDX100 /
  SPX500 (FundedNext). CFD spreads replace futures commissions.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from backend.strategies.base_strategy import BaseStrategy, TradeAction, TradeSignal
from backend.strategies.registry import register_strategy
from backend.strategies.strategy_roboquant import common as c
from backend.strategies.strategy_roboquant.params import (
    RQCharlesMongerParams, RQCrazyORBParams, RQNQPrecisionParams, RQNYORBParams,
    RQTMeanReversionParams,
)


class _RQBase(BaseStrategy):
    LIVE_POSITION_EXITS = True
    PARAMS_ATTR = ""
    PARAMS_CLS: type = object
    WINDOW = 300
    POSITION_BAR_WINDOW = 64

    def __init__(self, config: Any):
        super().__init__(config)
        self.params = getattr(config, self.PARAMS_ATTR, None) or self.PARAMS_CLS()

    def get_required_timeframes(self) -> list[str]:
        return [self.params.timeframe]

    @property
    def WINDOW_BARS(self) -> dict[str, int]:  # noqa: N802 — read by strategies.windows
        return {self.params.timeframe: self.WINDOW}

    async def initialize(self):
        return None

    async def on_tick(self, symbol: str, tick: dict[str, Any]) -> None:
        return None

    def notify_outcome(self, *args, **kwargs) -> None:
        return None

    def _signal(self, symbol: str, timeframe: str, candles: pd.DataFrame, direction: str,
                entry: float, sl: float, tp: float, **meta) -> TradeSignal:
        sig = TradeSignal(
            strategy_id=self.strategy_id, symbol=symbol, direction=direction,
            signal_type=self.strategy_id, timeframe=timeframe, entry_price=float(entry),
            stop_loss=float(sl), take_profit=float(tp), confluence_score=80,
            timestamp=float(c.epochs(candles)[-1]), metadata=meta)
        return self._tag_signal(sig)

    def _start(self, symbol: str, timeframe: str, candles: pd.DataFrame, min_bars: int = 20) -> bool:
        self.begin_candidate(symbol, timeframe,
                             bar_time=candles.index[-1] if candles is not None and len(candles) else None)
        return (timeframe == self.params.timeframe and candles is not None
                and len(candles) >= min_bars)

    @staticmethod
    def _close(position: dict, reason: str) -> TradeAction:
        return TradeAction(ticket=int(position.get("ticket") or 0), action="CLOSE",
                           close_reason=reason)

    @staticmethod
    def _bars_held(candles: pd.DataFrame, position: dict) -> int | None:
        entry = position.get("entry_time")
        if entry is None:
            return None
        try:
            ts = int(entry) if isinstance(entry, (int, float)) else int(pd.Timestamp(entry).timestamp())
        except Exception:                               # noqa: BLE001
            return None
        return int((c.epochs(candles) >= ts).sum()) - 1


# ── NQ Precision Bot ─────────────────────────────────────────────────────────


@register_strategy("RQ_NQPrecision_v1")
class RQNQPrecision(_RQBase):
    strategy_id = "RQ_NQPrecision_v1"
    PARAMS_ATTR = "rq_nq_precision"
    PARAMS_CLS = RQNQPrecisionParams
    WINDOW = 600

    async def on_bar(self, symbol, timeframe, candles):
        p = self.params
        if not self._start(symbol, timeframe, candles, 60):
            return None
        t = c.epochs(candles)
        # One bar a day can trade: test the last bar's clock before converting
        # the whole window (on M1 that conversion was most of the run time).
        if c.et_hhmm_one(t[-1]) != p.trigger_hhmm:
            return None
        day, hhmm = c.et_parts(t)
        i = len(t) - 1
        rng = (day == day[i]) & (hhmm >= p.range_start) & (hhmm < p.range_end)
        if not self.gate("opening_range", bool(rng.any()), "no 09:30-09:45 bars today"):
            return None
        hi = float(candles["high"].to_numpy()[rng].max())
        lo = float(candles["low"].to_numpy()[rng].min())
        price = float(candles["close"].iloc[-1])
        direction = "BUY" if price > hi else "SELL" if price < lo else None
        if not self.gate("outside_range", direction is not None,
                         f"price {price:.2f} inside [{lo:.2f}, {hi:.2f}]"):
            return None
        # Upstream decides on a TICK at 09:59:55, when the last COMPLETED bar
        # is 09:58 - so ATR, the ATR regime and relative volume are read there.
        h, l, cl = (candles[k].to_numpy(dtype=float)[:-1] for k in ("high", "low", "close"))
        a = c.atr_series(h, l, cl, p.atr_period)[-1]
        al = c.atr_series(h, l, cl, p.atr_regime_len)[-1]
        if not self.gate("atr_available", np.isfinite(a) and a > 0):
            return None
        vol = c.volume(candles)[:-1]
        win = vol[-p.rvol_len:]
        rvol = vol[-1] / win.mean() if len(win) == p.rvol_len and win.mean() > 0 else np.nan
        regime = a / al if np.isfinite(al) and al > 0 else np.nan
        if p.use_filters:
            if not self.gate("rvol_band", np.isfinite(rvol) and p.rvol_min <= rvol <= p.rvol_max,
                             f"rvol {rvol:.2f} outside [{p.rvol_min}, {p.rvol_max}]"):
                return None
            if not self.gate("atr_regime_band", np.isfinite(regime) and p.regime_min <= regime <= p.regime_max,
                             f"ATR regime {regime:.2f} outside [{p.regime_min}, {p.regime_max}]"):
                return None
        s = 1 if direction == "BUY" else -1
        return self._signal(symbol, timeframe, candles, direction, price,
                            price - s * p.atr_sl_mult * a, price + s * p.atr_tp_mult * a,
                            or_high=hi, or_low=lo, atr=a, rvol=rvol, atr_regime=regime)

    def on_position_bar(self, symbol, timeframe, candles, position):
        held = self._bars_held(candles, position)
        if held is not None and held >= int(self.params.close_after_min):
            return self._close(position, "RQ_TIME_CLOSE")
        return None


# ── NY Open Range Breakout ───────────────────────────────────────────────────


@register_strategy("RQ_NYORB_v1")
class RQNYORB(_RQBase):
    strategy_id = "RQ_NYORB_v1"
    PARAMS_ATTR = "rq_ny_orb"
    PARAMS_CLS = RQNYORBParams
    WINDOW = 300
    OR_START = 930

    def _entry_at(self, j, hi, lo, close, atr, vol, k0):
        """The entry the strategy takes on bar j, or None (filters as upstream)."""
        p = self.params
        long_sig = p.allow_long and close[j] > hi
        short_sig = p.allow_short and close[j] < lo
        if not (long_sig or short_sig):
            return None
        if p.use_rvol:
            prev = vol[max(0, j - p.rvol_period):j]
            if not len(prev) or prev.mean() <= 0 or vol[j] < p.min_rvol * prev.mean():
                return ("blocked", "rvol")
        a = atr[j]
        if p.use_atr_regime:
            pct = a / close[j] * 100.0 if np.isfinite(a) and close[j] > 0 else 0.0
            if not (p.min_atr_pct <= pct <= p.max_atr_pct):
                return ("blocked", "atr_pct")
        if not np.isfinite(a) or a <= 0:
            return ("blocked", "atr")
        dist = p.atr_stop_mult * a
        if p.max_stop_pts > 0 and dist > p.max_stop_pts:
            return ("blocked", "stop_cap")
        return ("BUY" if long_sig else "SELL", dist)

    async def on_bar(self, symbol, timeframe, candles):
        p = self.params
        if not self._start(symbol, timeframe, candles, 40):
            return None
        t = c.epochs(candles)
        day, hhmm = c.et_parts(t)
        i = len(t) - 1
        if hhmm[i] >= p.flat_hhmm or hhmm[i] >= p.cutoff_hhmm:
            return None
        today = np.where((day == day[i]) & (hhmm >= self.OR_START))[0]
        if len(today) <= p.range_bars or i < today[p.range_bars - 1]:
            return None
        rb = today[:p.range_bars]
        if i <= rb[-1]:
            return None
        hi = float(candles["high"].to_numpy()[rb].max())
        lo = float(candles["low"].to_numpy()[rb].min())
        if hi <= lo:
            return None
        close = candles["close"].to_numpy(dtype=float)
        atr = c.atr_series(candles["high"].to_numpy(), candles["low"].to_numpy(), close, p.atr_period)
        vol = c.volume(candles)
        # One trade per day: replay today's earlier post-range bars.
        for j in today[today > rb[-1]]:
            if j == i:
                break
            if hhmm[j] >= p.cutoff_hhmm:
                return None
            e = self._entry_at(j, hi, lo, close, atr, vol, rb[-1])
            if e is not None and e[0] != "blocked":
                return None                            # already traded today
        e = self._entry_at(i, hi, lo, close, atr, vol, rb[-1])
        if not self.gate("breakout", e is not None, "close inside the opening range"):
            return None
        if not self.gate("filters", e[0] != "blocked", f"blocked by {e[1] if e else ''}"):
            return None
        direction, dist = e
        s = 1 if direction == "BUY" else -1
        entry = close[i]
        return self._signal(symbol, timeframe, candles, direction, entry,
                            entry - s * dist, entry + s * p.rr_target * dist,
                            or_high=hi, or_low=lo, atr=float(atr[i]))

    def on_position_bar(self, symbol, timeframe, candles, position):
        if c.et_hhmm_one(c.epochs(candles)[-1]) >= self.params.flat_hhmm:
            return self._close(position, "RQ_SESSION_FLAT")
        return None


# ── Crazy ORB ────────────────────────────────────────────────────────────────


@register_strategy("RQ_CrazyORB_v1")
class RQCrazyORB(_RQBase):
    strategy_id = "RQ_CrazyORB_v1"
    PARAMS_ATTR = "rq_crazy_orb"
    PARAMS_CLS = RQCrazyORBParams
    WINDOW = 300

    async def on_bar(self, symbol, timeframe, candles):
        p = self.params
        if not self._start(symbol, timeframe, candles, 40):
            return None
        t = c.epochs(candles)
        day, hhmm = c.et_parts(t)
        i = len(t) - 1
        if hhmm[i] >= p.flatten_hhmm or not (p.trade_start_hhmm <= hhmm[i] < p.trade_end_hhmm):
            return None
        today = day == day[i]
        rng = np.where(today & (hhmm >= p.range_start_hhmm) & (hhmm < p.range_end_hhmm))[0]
        if not self.gate("opening_range", len(rng) > 0, "no range bars today"):
            return None
        high = candles["high"].to_numpy(dtype=float)
        low = candles["low"].to_numpy(dtype=float)
        close = candles["close"].to_numpy(dtype=float)
        hi, lo = float(high[rng].max()), float(low[rng].min())
        atr = c.atr_series(high, low, close, p.atr_period)
        # Replay the day: each side once, at most max_trades, and no entry while
        # an earlier one is still open (SL/TP replayed from the bars).
        long_used = short_used = False
        trades = 0
        open_until = -1          # bar index at which the open trade exits (or beyond i)
        cands = np.where(today & (hhmm >= p.range_end_hhmm) & (hhmm >= p.trade_start_hhmm)
                         & (hhmm < p.trade_end_hhmm))[0]
        for j in cands:
            if j > i:
                break
            if open_until > j or trades >= p.max_trades:
                if j == i:
                    return None
                continue
            a = atr[j]
            want = None
            if close[j] > hi and not long_used:
                want = "BUY"
            elif close[j] < lo and not short_used:
                want = "SELL"
            if want is None or not np.isfinite(a) or a <= 0:
                if j == i:
                    self.gate("breakout", False, "no unused-side close beyond the range")
                    return None
                continue
            dist = p.atr_mult * a
            s = 1 if want == "BUY" else -1
            sl, tp = close[j] - s * dist, close[j] + s * p.rr * dist
            if j == i:
                return self._signal(symbol, timeframe, candles, want, close[j], sl, tp,
                                    or_high=hi, or_low=lo, atr=float(a))
            trades += 1
            long_used |= want == "BUY"
            short_used |= want == "SELL"
            ex = c.simulate_exit(high, low, j + 1, i + 1, want == "BUY", sl, tp)
            open_until = (ex if ex is not None else i + 1)
        return None

    def on_position_bar(self, symbol, timeframe, candles, position):
        if c.et_hhmm_one(c.epochs(candles)[-1]) >= self.params.flatten_hhmm:
            return self._close(position, "RQ_SESSION_FLAT")
        return None


# ── T-Mean Reversion ─────────────────────────────────────────────────────────


@register_strategy("RQ_TMeanReversion_v1")
class RQTMeanReversion(_RQBase):
    strategy_id = "RQ_TMeanReversion_v1"
    PARAMS_ATTR = "rq_t_mean_reversion"
    PARAMS_CLS = RQTMeanReversionParams
    WINDOW = 400

    async def on_bar(self, symbol, timeframe, candles):
        p = self.params
        if not self._start(symbol, timeframe, candles, 60):
            return None
        idx = pd.DatetimeIndex(pd.to_datetime(c.epochs(candles), unit="s"))   # UTC
        last = idx[-1]
        if not (last.isoweekday() == p.entry_day and last.hour == p.entry_hour
                and last.minute >= p.entry_minute):
            return None
        # The last COMPLETED Friday on the UTC calendar: never today.
        dates = idx.normalize()
        fridays = sorted({d for d in dates if d.isoweekday() == 5 and d < last.normalize()})
        if not self.gate("friday_known", bool(fridays), "no completed Friday in the window"):
            return None
        fri = fridays[-1]
        m = dates == fri
        o = float(candles["open"].to_numpy()[m][0])
        cl = float(candles["close"].to_numpy()[m][-1])
        if not self.gate("friday_red", cl < o, f"last Friday {fri.date()} closed {cl:.2f} >= open {o:.2f}"):
            return None
        entry = float(candles["close"].iloc[-1])
        sl = entry * (1.0 - p.disaster_stop_pct / 100.0)
        return self._signal(symbol, timeframe, candles, "BUY", entry, sl,
                            entry + 40.0 * (entry - sl), friday=str(fri.date()))

    def on_position_bar(self, symbol, timeframe, candles, position):
        """Close at the first bar AT OR AFTER the scheduled exit following the
        entry. Upstream closes on the bar that IS Wed 10:00 UTC; on complete
        bars that is the same bar, but live, a bot that was offline for that
        one bar would otherwise hold another whole week."""
        p = self.params
        last = pd.Timestamp(int(c.epochs(candles)[-1]), unit="s")
        entry = position.get("entry_time")
        try:
            e = (pd.Timestamp(int(entry), unit="s") if isinstance(entry, (int, float))
                 else pd.Timestamp(entry).tz_localize(None) if entry is not None else None)
        except Exception:                               # noqa: BLE001
            e = None
        if e is None:
            due = (last.isoweekday() == p.exit_day and last.hour == p.exit_hour
                   and last.minute >= p.exit_minute)
        else:
            days = (p.exit_day - e.isoweekday()) % 7
            exit_at = (e.normalize() + pd.Timedelta(days=days)
                       + pd.Timedelta(hours=p.exit_hour, minutes=p.exit_minute))
            if exit_at <= e:
                exit_at += pd.Timedelta(days=7)
            due = last >= exit_at
        return self._close(position, "RQ_CALENDAR_EXIT") if due else None


# ── Charles Monger ───────────────────────────────────────────────────────────


@register_strategy("RQ_CharlesMonger_v1")
class RQCharlesMonger(_RQBase):
    strategy_id = "RQ_CharlesMonger_v1"
    PARAMS_ATTR = "rq_charles_monger"
    PARAMS_CLS = RQCharlesMongerParams
    # EMA(200) seeded at the window start forgets the seed by 2,400 bars
    # ((199/201)^2400 ~ 4e-11), so the band equals the full-history band.
    WINDOW = 2400
    POSITION_BAR_WINDOW = 2400

    def _regimes(self, candles) -> np.ndarray:
        p = self.params
        top = c.ema(candles[p.top_source].to_numpy(dtype=float), p.length)
        bot = c.ema(candles[p.bot_source].to_numpy(dtype=float), p.length)
        close = candles["close"].to_numpy(dtype=float)
        return np.where(close > top, 1, np.where(close < bot, -1, 0))

    async def on_bar(self, symbol, timeframe, candles):
        p = self.params
        if not self._start(symbol, timeframe, candles, 60):
            return None
        r = self._regimes(candles)
        now = int(r[-1])
        if now == 0:
            return None
        prior = r[:-1][r[:-1] != 0]
        prev = int(prior[-1]) if len(prior) else 0
        if not self.gate("regime_flip", now != prev, "no colour flip on this bar"):
            return None
        if now == -1 and p.long_only:
            return None                         # a red flip only exits (on_position_bar)
        entry = float(candles["close"].iloc[-1])
        d = p.stop_loss_percent / 100.0
        if now == 1:
            sl = entry * (1 - d)
            return self._signal(symbol, timeframe, candles, "BUY", entry, sl, entry + 50 * (entry - sl))
        sl = entry * (1 + d)
        return self._signal(symbol, timeframe, candles, "SELL", entry, sl, entry - 50 * (sl - entry))

    def on_position_bar(self, symbol, timeframe, candles, position):
        r = self._regimes(candles)
        long_pos = str(position.get("direction", "BUY")).upper() == "BUY"
        if (long_pos and r[-1] == -1) or (not long_pos and r[-1] == 1):
            return self._close(position, "RQ_REGIME_FLIP")
        return None
