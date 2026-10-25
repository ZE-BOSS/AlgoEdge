"""
backend/strategies/strategy_roboquant/engine_more.py

Nine more roboquant.dev templates (2026-10-20), each a line-for-line port of
the template's Rust `on_bar` through `replay.ReplayStrategy`. The source of
every template is kept in research/roboquant/source/rs/, and each port names
the upstream line it mirrors where the behaviour is not obvious.

    RQ_FlashTriggerORB_v1   The Flash Trigger ORB              M15
    RQ_ScarfaceORB_v1       Scarface ORB                       M1
    RQ_IVB_v1               IVB - Initial Volatility Breakout  M5
    RQ_GigaORB_v1           Giga ORB                           M15
    RQ_SigmaBreakout_v1     Sigma Breakout                     M30
    RQ_DailyVWAP_v1         Daily Anchored VWAP Signal         3m (fed M1)
    RQ_FridayRevisit_v1     Friday Range Revisit - Monday Short M5
    RQ_M30ORB_v1            M30 ORB + M5 Breakout              M5
    RQ_SRSweep_v1           S&R Sweep                          M5

Not ported: "NY ORB Retest - PDH/PDL Filter" enters with a resting LIMIT order
at the broken level, and this app's backtester and order manager only place
market orders - a market fill at the next bar's open is a different strategy.

Daily values (`.timeframe("1D")` upstream) are read from the broker's D1 bars,
which the bar feed hands every engine the same way in a backtest and live; a
D1 bar is used only once it has closed.
"""

from __future__ import annotations

from collections import deque

import numpy as np
import pandas as pd

from backend.strategies.registry import register_strategy
from backend.strategies.strategy_roboquant import common as c
from backend.strategies.strategy_roboquant.params import (
    RQDailyAnchoredVWAPParams, RQFlashTriggerORBParams, RQFridayRevisitParams, RQGigaORBParams,
    RQIVBParams, RQM30ORBParams, RQScarfaceORBParams, RQSigmaBreakoutParams, RQSRSweepParams,
)
from backend.strategies.strategy_roboquant.replay import (
    Bars, Ctx, ReplayStrategy, atr_fast, daily_asof, ema_fast,
)


class _DailyMixin:
    """Engines that read completed daily bars: D1 is a second timeframe."""
    D1_WINDOW = 120

    def get_required_timeframes(self) -> list[str]:
        # D1 FIRST: the feed steps timeframes in this order, so a daily bar that
        # closed with this primary bar reaches the engine before the primary
        # bar does - in a backtest and live alike. Primary LAST: live position
        # management reads the last timeframe as the engine's own.
        return ["D1", self.params.timeframe]

    @property
    def WINDOW_BARS(self) -> dict[str, int]:  # noqa: N802
        return {self.params.timeframe: self.WINDOW, "D1": self.D1_WINDOW}

    def _d1_frame(self, symbol: str) -> pd.DataFrame | None:
        return getattr(self, "_d1", {}).get(str(symbol))

    async def on_bar(self, symbol, timeframe, candles):
        if timeframe == "D1":
            if candles is not None and len(candles):
                d = getattr(self, "_d1", None)
                if d is None:
                    d = self._d1 = {}
                d[str(symbol)] = candles
            return None
        self._symbol_now = str(symbol)
        return await super().on_bar(symbol, timeframe, candles)

    def on_position_bar(self, symbol, timeframe, candles, position):
        self._symbol_now = str(symbol)
        return super().on_position_bar(symbol, timeframe, candles, position)

    def prepare_backtest(self, symbol: str, frames_by_tf: dict) -> None:
        d1 = (frames_by_tf or {}).get("D1")
        if d1 is not None and len(d1):
            d = getattr(self, "_d1", None)
            if d is None:
                d = self._d1 = {}
            d[str(symbol)] = d1
        self._symbol_now = str(symbol)
        super().prepare_backtest(symbol, frames_by_tf)

    def _daily(self, bars: Bars, value: str, n: int = 14) -> np.ndarray:
        return daily_asof(self._d1_frame(getattr(self, "_symbol_now", "")), bars.t, value, n)


# ── The Flash Trigger ORB ───────────────────────────────────────────────────


@register_strategy("RQ_FlashTriggerORB_v1")
class RQFlashTriggerORB(ReplayStrategy):
    strategy_id = "RQ_FlashTriggerORB_v1"
    PARAMS_ATTR = "rq_flash_trigger_orb"
    PARAMS_CLS = RQFlashTriggerORBParams
    WINDOW = 500
    ANCHOR_DAYS = 3

    def prepare(self, bars):
        lt = pd.DatetimeIndex(pd.to_datetime(bars.t + self.params.utc_offset * 3600, unit="s"))
        bars.ind["loc_day"] = np.asarray(lt.year * 10000 + lt.month * 100 + lt.day, dtype="int64")
        bars.ind["loc_hhmm"] = np.asarray(lt.hour * 100 + lt.minute, dtype="int64")
        bars.ind["loc_wd"] = np.asarray(lt.dayofweek + 1, dtype="int64")

    def init_state(self):
        return dict(session_date=0, utc_date=0, closed_for_day=False, range_active=False,
                    traded=False, range_t=0, hi=0.0, lo=0.0)

    def _day_allowed(self, wd: int) -> bool:
        p = self.params
        if not p.day_filter:
            return True
        return {1: p.trade_monday, 2: p.trade_tuesday, 3: p.trade_wednesday,
                4: p.trade_thursday, 5: p.trade_friday}.get(int(wd), False)

    def step(self, st, k, b, ctx):
        p = self.params
        ld, lh = int(b.ind["loc_day"][k]), int(b.ind["loc_hhmm"][k])
        if ld != st["session_date"]:
            st.update(session_date=ld, range_active=False, traded=False)
        if int(b.utc_day[k]) != st["utc_date"]:
            st.update(utc_date=int(b.utc_day[k]), closed_for_day=False)
        if not st["closed_for_day"] and b.utc_hhmm[k] >= p.eod_close_hour * 100 + p.eod_close_minute:
            if ctx.position():
                ctx.close_position("RQ_EOD_FLAT")
            st["closed_for_day"] = True
        target = p.target_hour * 100 + p.target_minute
        if not st["range_active"] and lh == target and self._day_allowed(b.ind["loc_wd"][k]):
            st.update(hi=float(b.h[k]), lo=float(b.l[k]), range_active=True, traded=False, range_t=int(b.t[k]))
            return
        if not st["range_active"] or st["traded"] or b.t[k] <= st["range_t"] or ctx.position():
            return
        kw = (dict(trail_offset=p.trail_buffer_points, trail_activate=p.trail_activate_points)
              if p.activate_trailing else {})
        close = float(b.c[k])
        if close > st["hi"]:
            d = close - st["lo"]
            if d > 0:
                ctx.buy(st["lo"], close + d * p.risk_reward, range_high=st["hi"], range_low=st["lo"], **kw)
                st["traded"] = True
        elif close < st["lo"]:
            d = st["hi"] - close
            if d > 0:
                ctx.sell(st["hi"], close - d * p.risk_reward, range_high=st["hi"], range_low=st["lo"], **kw)
                st["traded"] = True


# ── Scarface ORB ─────────────────────────────────────────────────────────────


@register_strategy("RQ_ScarfaceORB_v1")
class RQScarfaceORB(ReplayStrategy):
    strategy_id = "RQ_ScarfaceORB_v1"
    PARAMS_ATTR = "rq_scarface_orb"
    PARAMS_CLS = RQScarfaceORBParams
    # No session cut-off upstream: a day's setup stays live until the next
    # 09:30, so the replay must reach back past yesterday's open.
    WINDOW = 4600
    ANCHOR_DAYS = 2

    def prepare(self, bars):
        bars.ind["atr"] = atr_fast(bars.h, bars.l, bars.c, self.params.atr_period)

    def init_state(self):
        return dict(n=0, in_or=False, or_open=0.0, or_bullish=False, candles=[], lines=False,
                    brk=False, brk_n=0, retest=False, momentum=False, invalid=False, traded=False,
                    bt=0.0, bt_low_green=0.0, has_bt=False, btl=0.0, bt_high_red=0.0, has_btl=False)

    def step(self, st, k, b, ctx):
        p = self.params
        st["n"] += 1
        hhmm = int(b.et_hhmm[k])
        end_m = 9 * 60 + 30 + p.duration_minutes
        start, end = 930, (end_m // 60) * 100 + end_m % 60
        o, h, lo, cl = float(b.o[k]), float(b.h[k]), float(b.l[k]), float(b.c[k])
        if start <= hhmm < end and not st["in_or"]:
            st.update(lines=False, brk=False, retest=False, momentum=False, invalid=False, traded=False,
                      brk_n=0, bt=0.0, btl=0.0, bt_low_green=0.0, bt_high_red=0.0, has_bt=False,
                      has_btl=False, candles=[])
            st.update(in_or=True, or_open=o)
        if st["in_or"] and hhmm < end:
            st["candles"].append((h, lo, cl, o))
        if st["in_or"] and hhmm >= end:
            st["in_or"] = False
            # Upstream reads the close of the FIRST bar after the range here.
            st["or_bullish"] = cl > st["or_open"]
            if not st["lines"]:
                st["lines"] = True
                if st["candles"]:
                    if st["or_bullish"]:
                        bear = [x for x in st["candles"] if x[2] < x[3]]
                        if bear:
                            top = max(bear, key=lambda x: x[0])        # first max wins, as upstream's `>`
                            for x in bear:
                                if x[0] == top[0]:
                                    top = x
                                    break
                            st.update(bt=top[0], bt_low_green=top[1], has_bt=True)
                    else:
                        bull = [x for x in st["candles"] if x[2] > x[3]]
                        if bull:
                            bot = min(bull, key=lambda x: x[1])
                            for x in bull:
                                if x[1] == bot[1]:
                                    bot = x
                                    break
                            st.update(btl=bot[1], bt_high_red=bot[0], has_btl=True)
        if not st["in_or"] and st["lines"] and not st["brk"]:
            if st["or_bullish"] and st["has_bt"]:
                if cl > o and cl > st["bt"]:
                    st.update(brk=True, brk_n=st["n"])
            elif not st["or_bullish"] and st["has_btl"]:
                if cl < o and cl < st["btl"]:
                    st.update(brk=True, brk_n=st["n"])
        if not st["in_or"] and st["brk"] and not st["retest"] and st["n"] > st["brk_n"] + 1:
            if st["or_bullish"] and st["has_bt"]:
                if cl <= st["bt"] or lo <= st["bt"]:
                    st["retest"] = True
            elif not st["or_bullish"] and st["has_btl"]:
                if cl >= st["btl"] or h >= st["btl"]:
                    st["retest"] = True
        if not st["in_or"] and st["retest"] and not st["momentum"] and not st["invalid"] and not st["traded"]:
            top_wick = h - cl if cl > o else h - o
            body = abs(cl - o)
            bottom_wick = o - lo if cl > o else cl - lo
            atr = b.ind["atr"][k]
            atr = float(atr) if np.isfinite(atr) else 0.0
            if st["or_bullish"] and st["has_bt"]:
                if cl < o and cl < st["bt_low_green"]:
                    st["invalid"] = True
                if (body > 0 and cl > o and cl > st["bt"] and bottom_wick >= p.wick_body_ratio * body
                        and top_wick <= bottom_wick / p.wick_ratio):
                    st.update(momentum=True, traded=True)
                    stop = cl - p.atr_multiplier * atr if (p.sl_mode == 1 and atr > 0) else lo - 0.25 * p.tick_size
                    d = cl - stop
                    if d > 0 and ctx.position() == 0:
                        ctx.buy(stop, cl + p.rr_ratio * d, breakout_target=st["bt"])
            elif not st["or_bullish"] and st["has_btl"]:
                if cl > o and cl > st["bt_high_red"]:
                    st["invalid"] = True
                if (body > 0 and cl < o and cl < st["btl"] and top_wick >= p.wick_body_ratio * body
                        and bottom_wick <= top_wick / p.wick_ratio):
                    st.update(momentum=True, traded=True)
                    stop = cl + p.atr_multiplier * atr if (p.sl_mode == 1 and atr > 0) else h + 0.25 * p.tick_size
                    d = stop - cl
                    if d > 0 and ctx.position() == 0:
                        ctx.sell(stop, cl - p.rr_ratio * d, breakout_target=st["btl"])


# ── IVB - Initial Volatility Breakout ───────────────────────────────────────


@register_strategy("RQ_IVB_v1")
class RQIVB(ReplayStrategy):
    strategy_id = "RQ_IVB_v1"
    PARAMS_ATTR = "rq_ivb"
    PARAMS_CLS = RQIVBParams
    # The contraction ratio averages the last 10 days' initial balances, so
    # the replay reaches back 12 trading days.
    WINDOW = 4200
    ANCHOR_DAYS = 12

    def prepare(self, bars):
        bars.ind["atr"] = atr_fast(bars.h, bars.l, bars.c, self.params.atr_period)

    def forced_close(self, bars, k, position):
        p = self.params
        return "RQ_EOD_FLAT" if p.flatten_eod and bars.et_hhmm[k] >= p.eod_flatten_hhmm else None

    def init_state(self):
        return dict(day=0, ib_high=-np.inf, ib_low=np.inf, ib_built=False, trades=0,
                    rth=deque(maxlen=200), ranges=deque(maxlen=400))

    def step(self, st, k, b, ctx):
        p = self.params
        hhmm, date = int(b.et_hhmm[k]), int(b.et_day[k])
        if date != st["day"]:
            if p.flatten_eod and st["day"] and ctx.position():
                ctx.close_position("RQ_EOD_FLAT")
            st.update(day=date, ib_high=-np.inf, ib_low=np.inf, ib_built=False, trades=0)
        in_ib = p.session_start_hhmm <= hhmm < p.session_end_hhmm
        in_entry = p.session_end_hhmm <= hhmm < p.entry_window_end_hhmm
        if p.flatten_eod and hhmm >= p.eod_flatten_hhmm and ctx.position():
            ctx.close_position("RQ_EOD_FLAT")
            return
        vol = float(b.v[k])
        if 930 <= hhmm < 1600:
            st["rth"].append(vol)
        atr = b.ind["atr"][k]
        if not np.isfinite(atr):
            return
        if in_ib:
            st["ib_high"] = max(st["ib_high"], float(b.h[k]))
            st["ib_low"] = min(st["ib_low"], float(b.l[k]))
            st["ib_built"] = True
        if in_entry and st["ib_built"] and ctx.position() == 0 and st["trades"] < p.max_trades_per_day:
            ib_range = st["ib_high"] - st["ib_low"]
            hist = list(st["ranges"])
            lb = min(p.ib_range_lookback, len(hist))
            avg_ib = sum(hist[len(hist) - lb:]) / lb if lb > 0 else ib_range
            norm = ib_range / avg_ib if avg_ib > 0 else 1.0
            range_ok = p.min_contraction_ratio <= norm <= p.max_contraction_ratio
            rth = list(st["rth"])
            n = min(p.vol_lookback, len(rth))
            avg_vol = sum(rth[len(rth) - n:]) / n if n > 0 else 0.0
            surge = vol / avg_vol if avg_vol > 0 else 1.0
            if range_ok and surge >= p.vol_surge_mult:
                st["trades"] += 1
                cl = float(b.c[k])
                if p.direction in (0, 1) and cl > st["ib_high"]:
                    ctx.buy(cl - p.sl_atr_mult * atr, cl + p.tp_atr_mult * atr,
                            ib_high=st["ib_high"], ib_low=st["ib_low"], ib_ratio=norm, vol_surge=surge)
                elif p.direction in (0, 2) and cl < st["ib_low"]:
                    ctx.sell(cl + p.sl_atr_mult * atr, cl - p.tp_atr_mult * atr,
                             ib_high=st["ib_high"], ib_low=st["ib_low"], ib_ratio=norm, vol_surge=surge)
        if p.session_end_hhmm <= hhmm < p.session_end_hhmm + 5 and st["ib_built"]:
            r = st["ib_high"] - st["ib_low"]
            if r > 0 and np.isfinite(r):
                st["ranges"].append(r)


# ── Giga ORB ─────────────────────────────────────────────────────────────────


@register_strategy("RQ_GigaORB_v1")
class RQGigaORB(_DailyMixin, ReplayStrategy):
    strategy_id = "RQ_GigaORB_v1"
    PARAMS_ATTR = "rq_giga_orb"
    PARAMS_CLS = RQGigaORBParams
    WINDOW = 500
    ANCHOR_DAYS = 4
    # Upstream sends no stop-loss-free orders; positions are held to SL/TP
    # (no flatten in the showcase), possibly across days.

    def prepare(self, bars):
        bars.ind["pdh"] = self._daily(bars, "high")
        bars.ind["pdl"] = self._daily(bars, "low")

    def init_state(self):
        return dict(day=0, or_high=-np.inf, or_low=np.inf, or_ready=False, trades=0,
                    bias_done=False, longs_blocked=False, shorts_blocked=False)

    def step(self, st, k, b, ctx):
        p = self.params
        hhmm, dnum = int(b.et_hhmm[k]), int(b.et_day[k])
        mins = (hhmm // 100) * 60 + hhmm % 100
        or_start = (p.or_start_hhmm // 100) * 60 + p.or_start_hhmm % 100
        or_end = or_start + p.or_minutes
        bias_check = (p.bias_check_hhmm // 100) * 60 + p.bias_check_hhmm % 100
        if dnum != st["day"]:
            st.update(day=dnum, or_high=-np.inf, or_low=np.inf, or_ready=False, trades=0,
                      bias_done=False, longs_blocked=False, shorts_blocked=False)
        if not st["or_ready"] and or_start <= mins < or_end:
            st["or_high"] = max(st["or_high"], float(b.h[k]))
            st["or_low"] = min(st["or_low"], float(b.l[k]))
            return
        if not st["or_ready"] and mins >= or_end and np.isfinite(st["or_high"]) and np.isfinite(st["or_low"]):
            st["or_ready"] = True
        if not st["or_ready"]:
            return
        cl = float(b.c[k])
        if p.use_daily_bias and not st["bias_done"] and mins >= bias_check:
            st["bias_done"] = True
            ph, pl = b.ind["pdh"][k], b.ind["pdl"][k]
            if not (np.isfinite(ph) and np.isfinite(pl)):
                return
            st["longs_blocked"] = cl < pl
            st["shorts_blocked"] = cl > ph
        if hhmm >= p.last_entry_hhmm:
            if p.close_at_end and ctx.position():
                ctx.close_position("RQ_LAST_ENTRY_FLAT")
            return
        if ctx.position() or st["trades"] >= p.max_trades_per_day:
            return
        o, h, lo = float(b.o[k]), float(b.h[k]), float(b.l[k])
        rng = h - lo
        upper_wick = h - max(o, cl)
        lower_wick = min(o, cl) - lo
        long_bad = p.use_wick_filter and rng > 0 and lower_wick / rng * 100.0 > p.max_wick_pct
        short_bad = p.use_wick_filter and rng > 0 and upper_wick / rng * 100.0 > p.max_wick_pct
        long_sig = p.allow_long and cl > st["or_high"] and not long_bad and not st["longs_blocked"]
        short_sig = p.allow_short and cl < st["or_low"] and not short_bad and not st["shorts_blocked"]
        if not long_sig and not short_sig:
            return
        stop_dist = cl - st["or_low"] if long_sig else st["or_high"] - cl
        if not np.isfinite(stop_dist) or stop_dist <= 0:
            return
        target_dist = p.target_points if p.target_mode == 1 else stop_dist * p.rr
        if long_sig:
            ctx.buy(st["or_low"], cl + target_dist, or_high=st["or_high"], or_low=st["or_low"])
        else:
            ctx.sell(st["or_high"], cl - target_dist, or_high=st["or_high"], or_low=st["or_low"])
        st["trades"] += 1


# ── Sigma Breakout ───────────────────────────────────────────────────────────


@register_strategy("RQ_SigmaBreakout_v1")
class RQSigmaBreakout(ReplayStrategy):
    strategy_id = "RQ_SigmaBreakout_v1"
    PARAMS_ATTR = "rq_sigma_breakout"
    PARAMS_CLS = RQSigmaBreakoutParams
    WINDOW = 500

    @property
    def ANCHOR_DAYS(self) -> int:  # noqa: N802
        return int(self.params.n_sigma_days) + 2

    @property
    def DISASTER_STOP_PCT(self) -> float:  # noqa: N802
        return float(self.params.disaster_stop_pct)

    def forced_close(self, bars, k, position):
        return "RQ_SESSION_CLOSE" if bars.et_hhmm[k] >= 1600 else None

    def init_state(self):
        return dict(day=0, day_open=0.0, have_open=False, enabled=False, pv=0.0, pvol=0.0,
                    vwap_prev=0.0, have_vwap=False, slots={})

    def step(self, st, k, b, ctx):
        p = self.params
        hhmm, date = int(b.et_hhmm[k]), int(b.et_day[k])
        if date != st["day"]:
            if ctx.position():
                ctx.close_position("RQ_DAY_CHANGE")
            st.update(day=date, have_open=False, pv=0.0, pvol=0.0, have_vwap=False)
        if hhmm >= 1600 and ctx.position():
            ctx.close_position("RQ_SESSION_CLOSE")
        if not (930 <= hhmm < 1600):
            return
        if not st["have_open"]:
            st.update(day_open=float(b.o[k]), have_open=True)
        ref = st["vwap_prev"] if (p.use_vwap and st["have_vwap"]) else st["day_open"]
        hist = st["slots"].setdefault(hhmm, deque(maxlen=max(2, int(p.n_sigma_days))))
        full = len(hist) == hist.maxlen
        sigma = sum(hist) / len(hist) if hist else 0.0
        upper, lower = ref + p.band_mult * sigma, ref - p.band_mult * sigma
        if not st["enabled"] and full:
            st["enabled"] = True
        cl = float(b.c[k])
        if st["enabled"]:
            pos = ctx.position()
            sl_long = cl * (1 - p.sl_pct / 100.0) if p.sl_pct > 0 else 0.0
            sl_short = cl * (1 + p.sl_pct / 100.0) if p.sl_pct > 0 else 0.0
            if cl > upper and pos <= 0:
                if pos < 0:
                    ctx.close_position("RQ_BAND_FLIP")
                ctx.buy(sl_long, 0.0, reference=ref, upper=upper, lower=lower)
            elif cl < lower and pos >= 0:
                if pos > 0:
                    ctx.close_position("RQ_BAND_FLIP")
                ctx.sell(sl_short, 0.0, reference=ref, upper=upper, lower=lower)
        typical = (float(b.h[k]) + float(b.l[k]) + cl) / 3.0
        st["pv"] += typical * float(b.v[k])
        st["pvol"] += float(b.v[k])
        if st["pvol"] > 0:
            st.update(vwap_prev=st["pv"] / st["pvol"], have_vwap=True)
        hist.append(abs(cl - st["day_open"]))


# ── Daily Anchored VWAP Signal Strategy ──────────────────────────────────────


@register_strategy("RQ_DailyVWAP_v1")
class RQDailyVWAP(ReplayStrategy):
    strategy_id = "RQ_DailyVWAP_v1"
    PARAMS_ATTR = "rq_daily_vwap"
    PARAMS_CLS = RQDailyAnchoredVWAPParams
    WINDOW = 6000
    ANCHOR_DAYS = 3

    @property
    def TEMPLATE_MINUTES(self) -> int:  # noqa: N802
        return max(1, int(self.params.bar_minutes))

    @property
    def DISASTER_STOP_PCT(self) -> float:  # noqa: N802
        return float(self.params.disaster_stop_pct)

    def prepare(self, bars):
        p = self.params
        bars.ind["ema"] = ema_fast(bars.c, max(1, p.ema_length))
        bars.ind["ema"][: max(1, p.ema_length) - 1] = np.nan
        bars.ind["atr"] = atr_fast(bars.h, bars.l, bars.c, max(1, p.atr_period))
        bars.ind["batr"] = atr_fast(bars.h, bars.l, bars.c, max(2, p.baseline_atr_period))

    def init_state(self):
        return dict(day=0, pv=0.0, vol=0.0, regime=0, waiting_red=False, waiting_green=False,
                    has_prev=False, prev_close=0.0, prev_vwap=0.0, first_closed=False,
                    first_won=False, attempts=0)

    def _in_session(self, hhmm: int) -> bool:
        a, z = self.params.session_start_hhmm, self.params.session_end_hhmm
        return a <= hhmm <= z if a <= z else (hhmm >= a or hhmm <= z)

    def _allowed(self, st) -> bool:
        if not st["first_closed"]:
            return True
        if st["first_won"]:
            return False
        return st["attempts"] < self.params.additional_attempts

    def on_sim_close(self, ctx: Ctx, why: str) -> None:
        st = ctx.st
        if not st["first_closed"]:
            st["first_closed"] = True
            st["first_won"] = ctx.closed[-1]["pnl"] > 0
        else:
            st["attempts"] += 1

    def step(self, st, k, b, ctx):
        p = self.params
        d = int(b.et_day[k])
        if d != st["day"]:
            st.update(day=d, pv=0.0, vol=0.0, regime=0, waiting_red=False, waiting_green=False,
                      has_prev=False, first_closed=False, first_won=False, attempts=0)
        h, lo, cl = float(b.h[k]), float(b.l[k]), float(b.c[k])
        px = (h + lo + cl) / 3.0 if p.use_typical_price else cl
        v = max(float(b.v[k]), 0.0)
        st["pv"] += px * v
        st["vol"] += v
        ema = b.ind["ema"][k]
        if not np.isfinite(ema) or st["vol"] <= 0:
            return
        vwap = st["pv"] / st["vol"]
        atr, batr = b.ind["atr"][k], b.ind["batr"][k]
        if not (np.isfinite(atr) and np.isfinite(batr)) or atr <= 0 or batr <= 0:
            return
        vol_ok = (not p.use_relative_volatility_filter) or atr / batr <= max(0.1, p.max_relative_atr)
        red_dot = green_dot = red_tri = green_tri = False
        if self._in_session(int(b.et_hhmm[k])):
            r = st["regime"]
            if st["waiting_red"] and cl > ema:
                st.update(waiting_red=False, regime=-2)
                red_tri = True
            elif st["waiting_green"] and cl < ema:
                st.update(waiting_green=False, regime=2)
                green_tri = True
            elif r == -2 and ema > vwap:
                st["regime"] = -3
            elif r == 2 and ema < vwap:
                st["regime"] = 3
            elif r == -3 and st["has_prev"] and st["prev_close"] >= st["prev_vwap"] and cl < vwap:
                st["regime"] = 0
                green_dot = True
            elif r == 3 and st["has_prev"] and st["prev_close"] <= st["prev_vwap"] and cl > vwap:
                st["regime"] = 0
                red_dot = True
            elif r == 0:
                if ema < vwap and cl < ema:
                    st.update(waiting_red=True, regime=-1)
                    red_dot = True
                elif ema > vwap and cl > ema:
                    st.update(waiting_green=True, regime=1)
                    green_dot = True
        if self._allowed(st) and vol_ok and ctx.position() == 0:
            dist = atr * max(0.1, p.atr_stop_multiplier)
            if green_dot:
                ctx.buy(cl - dist if p.use_atr_stop else 0.0, 0.0, vwap=vwap, ema=float(ema))
            elif red_dot:
                ctx.sell(cl + dist if p.use_atr_stop else 0.0, 0.0, vwap=vwap, ema=float(ema))
        if green_tri and ctx.position() > 0:
            ctx.close_position("RQ_EMA_EXIT")
        if red_tri and ctx.position() < 0:
            ctx.close_position("RQ_EMA_EXIT")
        st.update(prev_close=cl, prev_vwap=vwap, has_prev=True)


# ── Friday Range Revisit - Monday Short ──────────────────────────────────────


@register_strategy("RQ_FridayRevisit_v1")
class RQFridayRevisit(_DailyMixin, ReplayStrategy):
    strategy_id = "RQ_FridayRevisit_v1"
    PARAMS_ATTR = "rq_friday_revisit"
    PARAMS_CLS = RQFridayRevisitParams
    WINDOW = 1500
    ANCHOR_DAYS = 4                                  # Thursday and Friday before Monday

    def prepare(self, bars):
        bars.ind["datr"] = self._daily(bars, "atr", self.params.atr_period)

    def forced_close(self, bars, k, position):
        return "RQ_EOD_FLAT" if self.params.eod_hhmm <= bars.et_hhmm[k] < 1700 else None

    def init_state(self):
        return dict(cur=0, have_day=False, hi=0.0, lo=0.0, days=[], open_checked=False, traded=False)

    def step(self, st, k, b, ctx):
        p = self.params
        date, hhmm = int(b.et_day[k]), int(b.et_hhmm[k])
        if date != st["cur"]:
            if st["have_day"]:
                wd = int(pd.Timestamp(str(st["cur"])).isoweekday())
                st["days"].append((wd, st["hi"], st["lo"]))
                st["days"] = st["days"][-12:]
                st["have_day"] = False
            st.update(cur=date, open_checked=False, traded=False)
        in_rth = 930 <= hhmm < 1600
        if in_rth:
            if not st["have_day"]:
                st.update(have_day=True, hi=float(b.h[k]), lo=float(b.l[k]))
            else:
                st["hi"] = max(st["hi"], float(b.h[k]))
                st["lo"] = min(st["lo"], float(b.l[k]))
        if p.eod_hhmm <= hhmm < 1700 and ctx.position():
            ctx.close_position("RQ_EOD_FLAT")
        if ctx.position() < 0:
            st["traded"] = True
        if in_rth and not st["open_checked"]:
            st["open_checked"] = True
            if int(b.et_wd[k]) == 1:
                self._try_arm(st, k, b, ctx)

    def _try_arm(self, st, k, b, ctx):
        p = self.params
        if len(st["days"]) < 2:
            return
        fri, thu = st["days"][-1], st["days"][-2]
        if fri[0] != 5 or thu[0] != 4:
            return                                   # holiday week
        fh, fl = fri[1], fri[2]
        if fh <= fl or fh > thu[1]:
            return                                   # Friday broke Thursday's high: setup void
        o = float(b.o[k])
        if not (fl < o < fh):
            return                                   # Monday gapped outside Friday's range
        a = b.ind["datr"][k]
        need_atr = p.sl_mode == 1 or p.tp_mode == 1
        if need_atr and not (np.isfinite(a) and a > 0):
            return
        a = float(a) if np.isfinite(a) else 0.0
        stop = thu[1] + p.sl_buffer_pts if p.sl_mode == 0 else o + p.atr_sl_mult * a
        target = fl if p.tp_mode == 0 else o - p.atr_tp_mult * a
        if stop <= o or target >= o:
            return
        ctx.sell(stop, target, friday_high=fh, friday_low=fl, thursday_high=thu[1], monday_open=o)


# ── M30 ORB + M5 Breakout ────────────────────────────────────────────────────


@register_strategy("RQ_M30ORB_v1")
class RQM30ORB(_DailyMixin, ReplayStrategy):
    strategy_id = "RQ_M30ORB_v1"
    PARAMS_ATTR = "rq_m30_orb"
    PARAMS_CLS = RQM30ORBParams
    WINDOW = 1500
    ANCHOR_DAYS = 3

    def prepare(self, bars):
        p = self.params
        bars.ind["atr"] = atr_fast(bars.h, bars.l, bars.c, max(2, p.atr_period))
        bars.ind["datr"] = self._daily(bars, "atr", max(2, p.daily_atr_period))

    def forced_close(self, bars, k, position):
        p = self.params
        hhmm = int(bars.et_hhmm[k])
        m = (hhmm // 100) * 60 + hhmm % 100
        flat = max(0, p.flatten_hour) * 60 + max(0, p.flatten_min)
        return "RQ_EOD_FLAT" if p.flatten_eod and m >= flat else None

    def init_state(self):
        p = self.params
        return dict(day=0, have_start=False, or_high=-np.inf, or_low=np.inf, range_done=False,
                    or_ready=False, trades=0, vols=deque(maxlen=max(2, p.rvol_lookback)),
                    daily_atr=deque(maxlen=max(2, p.daily_atr_lookback)), atr_avg=None)

    def step(self, st, k, b, ctx):
        p = self.params
        day = int(b.et_day[k])
        if day != st["day"]:
            st.update(day=day, have_start=False, or_high=-np.inf, or_low=np.inf, range_done=False,
                      or_ready=False, trades=0)
            a = b.ind["datr"][k]
            if np.isfinite(a):
                dq = st["daily_atr"]
                st["atr_avg"] = sum(dq) / len(dq) if dq else None
                dq.append(float(a))
        vols = st["vols"]
        avg_vol = sum(vols) / len(vols) if vols else None
        rvol = float(b.v[k]) / avg_vol if avg_vol and avg_vol > 0 else None
        hhmm = int(b.et_hhmm[k])
        m = (hhmm // 100) * 60 + hhmm % 100
        start_min = max(0, p.or_hour) * 60 + max(0, p.or_min)
        end_min = start_min + max(5, p.or_len)
        if p.flatten_eod and m >= max(0, p.flatten_hour) * 60 + max(0, p.flatten_min) and ctx.position():
            ctx.close_position("RQ_EOD_FLAT")
        if start_min <= m < end_min:
            st["have_start"] = True
            st["or_high"] = max(st["or_high"], float(b.h[k]))
            st["or_low"] = min(st["or_low"], float(b.l[k]))
        elif m >= end_min and not st["range_done"]:
            st["range_done"] = True
            if st["have_start"] and np.isfinite(st["or_high"]) and np.isfinite(st["or_low"]):
                st["or_ready"] = True
        cl = float(b.c[k])
        if st["or_ready"] and ctx.position() == 0 and st["trades"] < p.max_trades:
            go_long = p.allow_long and cl > st["or_high"]
            go_short = p.allow_short and cl < st["or_low"]
            if go_long or go_short:
                if p.use_atr_regime:
                    a = b.ind["datr"][k]
                    avg = st["atr_avg"]
                    atr_ok = (np.isfinite(a) and avg is not None and avg > 0 and a >= p.min_atr_ratio * avg
                              and (p.min_atr_pts <= 0 or a >= p.min_atr_pts))
                else:
                    atr_ok = True
                rvol_ok = (rvol is not None and rvol >= p.min_rvol) if p.use_rvol else True
                if atr_ok and rvol_ok:
                    if p.stop_mode == 1:
                        a5 = b.ind["atr"][k]
                        stop_dist = float(a5) * p.atr_mult if np.isfinite(a5) else 0.0
                    elif go_long:
                        stop_dist = cl - float(b.l[k])
                    else:
                        stop_dist = float(b.h[k]) - cl
                    if np.isfinite(stop_dist) and stop_dist > 0:
                        if go_long:
                            ctx.buy(cl - stop_dist, cl + p.rr * stop_dist, or_high=st["or_high"],
                                    or_low=st["or_low"], rvol=rvol)
                        else:
                            ctx.sell(cl + stop_dist, cl - p.rr * stop_dist, or_high=st["or_high"],
                                     or_low=st["or_low"], rvol=rvol)
                        st["trades"] += 1
        vols.append(float(b.v[k]))


# ── S&R Sweep ────────────────────────────────────────────────────────────────


@register_strategy("RQ_SRSweep_v1")
class RQSRSweep(ReplayStrategy):
    strategy_id = "RQ_SRSweep_v1"
    PARAMS_ATTR = "rq_sr_sweep"
    PARAMS_CLS = RQSRSweepParams
    WINDOW = 1200
    ANCHOR_DAYS = 3

    def prepare(self, bars):
        n = max(2, self.params.ema_len)
        e = ema_fast(bars.c, n)
        e[: n - 1] = np.nan
        bars.ind["ema"] = e

    def init_state(self):
        p = self.params
        w = max(1, p.left_bars) + max(1, p.right_bars) + 1
        return dict(highs=deque(maxlen=w), lows=deque(maxlen=w), res=0.0, sup=0.0, prev_hp=0.0,
                    prev_lp=0.0, res_valid=False, sup_valid=False, res_tested=False, sup_tested=False)

    def step(self, st, k, b, ctx):
        p = self.params
        left, right = max(1, p.left_bars), max(1, p.right_bars)
        h, lo, cl, o = float(b.h[k]), float(b.l[k]), float(b.c[k]), float(b.o[k])
        st["highs"].append(h)
        st["lows"].append(lo)
        new_res = new_sup = None
        if len(st["highs"]) == left + right + 1:
            hs, ls = list(st["highs"]), list(st["lows"])
            mh, ml = hs[left], ls[left]
            if all(x < mh for x in hs[:left]) and all(x < mh for x in hs[left + 1:]):
                new_res = mh
            if all(x > ml for x in ls[:left]) and all(x > ml for x in ls[left + 1:]):
                new_sup = ml
        if new_res is not None and abs(new_res - st["prev_hp"]) > 0.0001:
            st.update(res=new_res, prev_hp=new_res, res_valid=True, res_tested=False)
        if new_sup is not None and abs(new_sup - st["prev_lp"]) > 0.0001:
            st.update(sup=new_sup, prev_lp=new_sup, sup_valid=True, sup_tested=False)
        ema = b.ind["ema"][k]
        if not np.isfinite(ema):
            return
        flat = ctx.position() == 0
        bull = (flat and p.allow_long and st["sup_valid"] and not st["sup_tested"] and st["sup"] > 0
                and cl > ema and lo < st["sup"] and cl > st["sup"] and cl > o)
        bear = (flat and p.allow_short and st["res_valid"] and not st["res_tested"] and st["res"] > 0
                and cl < ema and h > st["res"] and cl < st["res"] and cl < o)
        if st["res"] > 0 and h > st["res"]:
            st["res_tested"] = True
        if st["sup"] > 0 and lo < st["sup"]:
            st["sup_tested"] = True
        if st["res"] > 0 and cl > st["res"]:
            st["res_valid"] = False
        if st["sup"] > 0 and cl < st["sup"]:
            st["sup_valid"] = False
        buf = p.sl_buf_ticks * p.tick_size
        if bull:
            stop = lo - buf
            d = cl - stop
            if d > 0:
                ctx.buy(stop, cl + p.rr_ratio * d, level=st["sup"], ema=float(ema))
        if bear:
            stop = h + buf
            d = stop - cl
            if d > 0:
                ctx.sell(stop, cl - p.rr_ratio * d, level=st["res"], ema=float(ema))
