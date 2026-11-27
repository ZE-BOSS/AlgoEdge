"""
backend/strategies/strategy_av/level_pa.py

Advanced price action at key levels (AlphaVantiq research, 27 Nov 2026).

Levels
  * 1-hour swing highs/lows (a fractal: higher/lower than the two hours either
    side), known once the second hour after it has closed; a new swing within
    0.5 ATR of a level merges into it and makes it "strong" (tested twice);
  * the previous UTC day's high and low, replaced each day.
  Levels older than level_max_age_days drop out.

Setups at a level (the bar's range within 0.15 ATR of it)
  retest    the level was broken by a close (0.2 ATR beyond) in the last
            retest_window_bars and price comes back to it from the broken side:
            trade in the break's direction
  reversal  otherwise, price comes into the level from one side (the previous
            bar entirely beyond it): trade away from it
Then the preset's candle (pin, engulfing, doji, double top/bottom, sweep),
context (1-hour structure, previous-day level, retest, ...), order flow (if
any) and session. Entry at the next bar, stop beyond the bar or the level plus
stop_buffer_atr (at least min_risk_atr), target an R multiple or the next
level; one trade at a time, closed after max_hold_hours.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.strategies.registry import register_strategy
from backend.strategies.strategy_av import params as P
from backend.strategies.strategy_av.common import AVReplayBase, candle_ok, double_at, flow_ok, plan, session_ok


def swing_events(bars) -> tuple[np.ndarray, np.ndarray]:
    """Per bar: the 1-hour swing high / low that becomes known at this bar (NaN = none)."""
    n = len(bars)
    sw_hi, sw_lo = np.full(n, np.nan), np.full(n, np.nan)
    if n < 10:
        return sw_hi, sw_lo
    t = pd.to_datetime(bars.t, unit="s")
    hr = t.floor("1h")
    g = pd.DataFrame({"h": bars.h, "l": bars.l}, index=t).groupby(hr)
    hh, ll = g["h"].max(), g["l"].min()
    hi, lo = hh.to_numpy(), ll.to_numpy()
    hour_s = np.asarray(hh.index.as_unit("s").asi8, dtype=np.int64)
    tsec = np.asarray(bars.t, dtype=np.int64)
    for i in range(2, len(hh) - 2):
        known = int(hour_s[i + 2]) + 3600                  # the confirming hour has closed
        k = int(np.searchsorted(tsec, known, side="left"))
        if k >= n:
            continue
        if hi[i] >= max(hi[i - 2:i]) and hi[i] >= max(hi[i + 1:i + 3]):
            sw_hi[k] = hi[i]
        if lo[i] <= min(lo[i - 2:i]) and lo[i] <= min(lo[i + 1:i + 3]):
            sw_lo[k] = lo[i]
    return sw_hi, sw_lo


class AVLevelPA(AVReplayBase):
    PARAMS_ATTR = ""
    PARAMS_CLS = P.AVLevelPAParams
    TEMPLATE_MINUTES = 1

    @property
    def ANCHOR_DAYS(self) -> int:  # noqa: N802
        return int(np.ceil(self.params.level_max_age_days)) + 1

    @property
    def WINDOW(self) -> int:  # noqa: N802
        per_day = {"M1": 1440, "M5": 288, "M15": 96, "M30": 48, "H1": 24}.get(self.params.timeframe, 96)
        return int(per_day * (self.ANCHOR_DAYS + 4))

    def prepare(self, bars) -> None:
        super().prepare(bars)
        bars.ind["sw_hi"], bars.ind["sw_lo"] = swing_events(bars)

    def init_state(self) -> dict:
        return {"levels": [], "sh": [], "sl": [], "day": 0, "dhi": None, "dlo": None}

    def _trend(self, st) -> int:
        sh, sl = st["sh"], st["sl"]
        if len(sh) >= 2 and len(sl) >= 2:
            if sh[-1] > sh[-2] and sl[-1] > sl[-2]:
                return 1
            if sh[-1] < sh[-2] and sl[-1] < sl[-2]:
                return -1
        return 0

    def step(self, st, k, b, ctx) -> None:
        p = self.params
        self._time_stop(k, b, ctx)
        o, h, l, c, t = b.o, b.h, b.l, b.c, b.t  # noqa: E741
        a = b.ind["atr"][k]
        levels = st["levels"]
        # -- the day's previous high/low ---------------------------------------------
        day = int(b.utc_day[k])
        if day != st["day"]:
            if st["day"] and st["dhi"] is not None:
                levels[:] = [z for z in levels if not z["pd"]]
                for px, kind in ((st["dhi"], "high"), (st["dlo"], "low")):
                    levels.append({"px": float(px), "kind": kind, "touches": 1, "born": int(t[k]), "pd": True,
                                   "broken_at": None, "broken_dir": 0})
            st.update(day=day, dhi=h[k], dlo=l[k])
        else:
            st["dhi"], st["dlo"] = max(st["dhi"], h[k]), min(st["dlo"], l[k])
        if not np.isfinite(a) or a <= 0:
            return
        # -- swings that became known on this bar --------------------------------------
        for col, kind in (("sw_hi", "high"), ("sw_lo", "low")):
            px = b.ind[col][k]
            if not np.isfinite(px):
                continue
            (st["sh"] if kind == "high" else st["sl"]).append(float(px))
            near = next((z for z in levels if abs(z["px"] - px) <= 0.5 * a), None)
            if near:
                near["touches"] += 1
                near["px"] = (near["px"] + px) / 2
            else:
                levels.append({"px": float(px), "kind": kind, "touches": 1, "born": int(t[k]), "pd": False,
                               "broken_at": None, "broken_dir": 0})
        st["sh"], st["sl"] = st["sh"][-4:], st["sl"][-4:]
        max_age = p.level_max_age_days * 86400
        levels[:] = [z for z in levels if t[k] - z["born"] <= max_age][-60:]
        if k < 1:
            return
        for z in levels:
            if z["broken_at"] is None:
                if c[k - 1] <= z["px"] < c[k] and c[k] - z["px"] >= 0.2 * a:
                    z["broken_at"], z["broken_dir"] = int(t[k]), 1
                elif c[k - 1] >= z["px"] > c[k] and z["px"] - c[k] >= 0.2 * a:
                    z["broken_at"], z["broken_dir"] = int(t[k]), -1
        if ctx.position() or ctx.pending is not None:
            return
        if h[k] - l[k] <= 0:
            return
        tol = 0.15 * a
        trend = self._trend(st)
        bar_s = int(np.median(np.diff(t[max(0, k - 20):k + 1]))) if k >= 2 else 0
        for z in levels:
            px = z["px"]
            if not (l[k] - tol <= px <= h[k] + tol):
                continue
            setup, side = None, 0
            if z["broken_at"] is not None and z["broken_at"] < t[k] and bar_s and \
                    (t[k] - z["broken_at"]) <= p.retest_window_bars * bar_s:
                d = z["broken_dir"]
                if (l[k - 1] > px) if d == 1 else (h[k - 1] < px):
                    setup, side = "retest", d
            if setup is None:
                if l[k - 1] > px + tol:
                    setup, side = "reversal", 1
                elif h[k - 1] < px - tol:
                    setup, side = "reversal", -1
                else:
                    continue
            if not self._passes(z, setup, side, trend, k, b, a):
                continue
            entry = float(c[k])
            stop = (min(l[k], px) - p.stop_buffer_atr * a) if side == 1 else (max(h[k], px) + p.stop_buffer_atr * a)
            nxt = [zz["px"] for zz in levels if zz is not z]
            pl = plan(side, entry, stop, a, p, nxt, skip_close=True)
            if pl is None:
                continue
            sl, tp = pl
            meta = {"setup": setup, "level": round(float(px), 6), "level_kind": "prev_day" if z["pd"] else z["kind"],
                    "trend_h1": int(trend), "rl_rr": round(float((tp - entry) / (entry - sl)), 4)}
            (ctx.buy if side == 1 else ctx.sell)(sl=sl, tp=tp, **meta)
            return

    def _passes(self, z, setup, side, trend, k, b, a) -> bool:
        p = self.params
        o, h, l, c = b.o, b.h, b.l, b.c  # noqa: E741
        px = z["px"]
        if not self.gate("session", session_ok(p.session, int(b.utc_hhmm[k]))):
            return False
        sweep = (l[k] < px - 0.05 * a and c[k] > px) if side == 1 else (h[k] > px + 0.05 * a and c[k] < px)
        dbl = double_at(side, h, l, k, a) if p.candle in ("double", "rejection_double") else False
        if not self.gate("candle", candle_ok(p.candle, side, o, h, l, c, k, dbl, sweep)):
            return False
        if not self.gate("order_flow", flow_ok(p.flow, side, b.ind, k, o, h, l, c)):
            return False
        ctxk = p.context
        if ctxk == "none":
            ok = True
        elif ctxk in ("reversal", "retest"):
            ok = setup == ctxk
        elif ctxk == "prev_day_level":
            ok = bool(z["pd"])
        elif ctxk == "strong_zone":
            ok = z["touches"] >= 2
        elif ctxk == "with_trend":
            ok = trend * side > 0
        elif ctxk == "against_trend":
            ok = trend * side < 0
        elif ctxk == "big_move_in":
            lo = max(0, k - 12)
            ext = (h[lo:k].max() - l[k]) if side == 1 else (h[k] - l[lo:k].min())
            ok = bool(ext / a >= 3.0)
        else:
            ok = False
        return self.gate("context", ok)


def _preset(sid: str, attr: str, cls):
    @register_strategy(sid)
    class _S(AVLevelPA):
        strategy_id = sid
        PARAMS_ATTR = attr
        PARAMS_CLS = cls
    _S.__name__ = _S.__qualname__ = sid.replace("_v1", "")
    return _S


AVNasdaqPrevDayDoji = _preset("AV_NasdaqPrevDayDoji_v1", "av_nasdaq_prev_day_doji", P.AVNasdaqPrevDayDojiParams)
AVDowDojiRetest = _preset("AV_DowDojiRetest_v1", "av_dow_doji_retest", P.AVDowDojiRetestParams)
AVOilTrendEngulf = _preset("AV_OilTrendEngulf_v1", "av_oil_trend_engulf", P.AVOilTrendEngulfParams)
AVOilRetestDoji = _preset("AV_OilRetestDoji_v1", "av_oil_retest_doji", P.AVOilRetestDojiParams)
AVNikkeiTrendEngulf = _preset("AV_NikkeiTrendEngulf_v1", "av_nikkei_trend_engulf", P.AVNikkeiTrendEngulfParams)
AVDAXRetestRejection = _preset("AV_DAXRetestRejection_v1", "av_dax_retest_rejection", P.AVDAXRetestRejectionParams)
AVCableLiquiditySweep = _preset("AV_CableLiquiditySweep_v1", "av_cable_liquidity_sweep", P.AVCableLiquiditySweepParams)

LEVEL_PRESETS = {
    "AV_NasdaqPrevDayDoji_v1": ("av_nasdaq_prev_day_doji", P.AVNasdaqPrevDayDojiParams, "Nasdaq Prev-Day Doji (price action)"),
    "AV_DowDojiRetest_v1": ("av_dow_doji_retest", P.AVDowDojiRetestParams, "Dow Doji Retest (US30, price action)"),
    "AV_OilTrendEngulf_v1": ("av_oil_trend_engulf", P.AVOilTrendEngulfParams, "Oil Trend Engulf (price action)"),
    "AV_OilRetestDoji_v1": ("av_oil_retest_doji", P.AVOilRetestDojiParams, "Oil Retest Doji (price action)"),
    "AV_NikkeiTrendEngulf_v1": ("av_nikkei_trend_engulf", P.AVNikkeiTrendEngulfParams, "Nikkei Trend Engulf (price action)"),
    "AV_DAXRetestRejection_v1": ("av_dax_retest_rejection", P.AVDAXRetestRejectionParams, "DAX Retest Rejection (price action)"),
    "AV_CableLiquiditySweep_v1": ("av_cable_liquidity_sweep", P.AVCableLiquiditySweepParams, "Cable Liquidity Sweep (GBP/USD, order flow)"),
}
