"""
backend/analytics/fvg_research.py

HTFFVGFlip_v1 and BiasIFVG_v1, stripped to their confluences.

WHY A REBUILD INSTEAD OF RUNNING THE ENGINES
-------------------------------------------
Driving the live engines through the route's bar loop costs 3.6 ms (HTF FVG
Flip) and 7.7 ms (Bias IFVG) per M5 bar on this machine — ~26 hours for one
permissive pass over four years on 25 markets, before a single confluence is
varied. So, as with VWAP and APA, each engine is rebuilt here on plain arrays
and held to the engine by tests/test_fvg_research.py, signal for signal.

WHAT IS MIRRORED, EXACTLY
-------------------------
* The route's feeding: at M5 step i the engine sees M5 bars up to i-1, and a
  higher timeframe only when a new bar of it has CLOSED by time[i] (open <=
  time[i] - tf), handed as a window of window_bars(tf) bars — H1 200, H4 200,
  M15 300, M5 500. Higher timeframes are fed before M5, in the engine's
  get_required_timeframes order.
* core/fvg.FVGDetector: fills shrink a gap from the latest bar's wick and
  delete it when closed; a new gap needs gap >= mult x ATR(last 14 bars of the
  window) and, where configured, a displacement middle candle; at most 20 kept.
  A detector only sees the bars it is fed, so the M5 detectors are updated only
  in the states where each engine calls update() — which is why an FVG that
  formed while the engine was waiting elsewhere never exists for it.
* core/market_structure.MarketStructureDetector's trend (ChoCH on a close
  through >= 2 of the last 3 swings), with swings from an 11-bar centred window
  inside each 200-bar H1 window.
* Every state transition, including the same-bar fall-through, the session gate
  that waits WITHOUT resetting, the swing invalidation (wick for HTF, close for
  Bias), the age budget (30 / 40 entry bars), the leg windows, the stop
  (structural swing + 0.5 ATR, floored at max(12 pips, 1 ATR)) and the
  displacement measure.

WHAT IS VARIED
--------------
Gates that change the PATH of the state machine cannot be recorded as a flag —
turning them off changes which later setup the machine arms on — so each
combination is its own machine run ("variant"):

    HTF  htf_trend_filter  COUNTER (engine) | WITH | OFF
         first_tap_only    True (engine) | False
         require_retest    True (engine) | False
         displacement      True (1.5 ATR, 60% body — engine) | False
         session_rth       True (09:30-16:00 ET — engine) | False
    Bias bias_mode         H4 (engine) | OFF (direction from the tapped level)
         key_levels        ALL (engine) | FVG | CISD_REJ
         ifvg_leg_mode     APPROACH (engine default) | REACTION | BOTH
         session           09:30-11:00 ET (engine) | 08:00-16:00 ET | OFF

Gates that only test the trigger bar are recorded as flags on each candidate —
see HTF_FLAGS / BIAS_FLAGS. The Bias day-stop and max_trades_per_day are
portfolio rules applied at selection (synth_research.pick's daily cap).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np
import pandas as pd

from backend.analytics import synth_research as sr

BULL, BEAR = 1, -1
HTF_FLAGS = ("first_tap", "counter_trend", "with_trend", "inv_disp_025", "inv_disp_050", "stop_ok",
             "htf_trend", "adx_trend", "vol_high", "rth", "london", "newyork")
BIAS_FLAGS = ("levels_conf1", "levels_conf2", "inv_disp_025", "inv_disp_050", "stop_ok",
              "key_fvg", "key_cisd", "key_rejection", "h4_aligned",
              "htf_trend", "adx_trend", "vol_high", "ny_open", "london", "newyork")
RR = (1.0, 1.5, 2.0, 3.0, 4.0, 5.0)
TF_SECONDS = {"M5": 300, "M15": 900, "H1": 3600, "H4": 14400}


@dataclass
class Series_:
    time: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    atr14: np.ndarray = None

    def __post_init__(self):
        self.time = self.time.astype(np.int64)
        tr = sr.true_range(self.high, self.low, self.close)
        self.atr14 = sr.rolling_mean(tr, 14)
        # Plain Python lists for the per-bar state machines: indexing a list is
        # several times cheaper than boxing a numpy scalar on every access, and
        # these loops touch each bar dozens of times per variant.
        safe = np.where(np.isfinite(self.atr14) & (self.atr14 > 0), self.atr14, 0.0001)
        self.t_l = self.time.tolist()
        self.o_l, self.h_l = self.open.astype(float).tolist(), self.high.astype(float).tolist()
        self.l_l, self.c_l = self.low.astype(float).tolist(), self.close.astype(float).tolist()
        self.atr_l = safe.astype(float).tolist()


def from_arrays(d: dict[str, np.ndarray]) -> Series_:
    return Series_(d["time"], d["open"], d["high"], d["low"], d["close"])


def _atr(s: Series_, k: int) -> float:
    return s.atr_l[k] if k < len(s.atr_l) else 0.0001


# ── core/fvg.FVGDetector.update ──────────────────────────────────────────────

class Det:
    __slots__ = ("mult", "disp", "body", "active")

    def __init__(self, mult: float, disp: float = 0.0, body: float = 0.0):
        self.mult, self.disp, self.body, self.active = mult, disp, body, []

    def update(self, s: Series_, k: int) -> list:
        if k < 2:
            return self.active
        H, L, C, O = s.h_l, s.l_l, s.c_l, s.o_l
        lh, ll = H[k], L[k]
        if self.active:
            keep = []
            for f in self.active:
                if f["type"] == BULL:
                    if ll < f["top"]:
                        f["top"] = ll
                    if f["top"] <= f["bottom"]:
                        continue
                else:
                    if lh > f["bottom"]:
                        f["bottom"] = lh
                    if f["bottom"] >= f["top"]:
                        continue
                keep.append(f)
            self.active = keep
        low_k, high_k2 = L[k], H[k - 2]
        high_k, low_k2 = H[k], L[k - 2]
        bull = low_k > high_k2
        bear = (not bull) and high_k < low_k2
        if not (bull or bear):
            return self.active
        atr = s.atr_l[k]
        need = self.mult * atr
        ok = True
        if self.disp > 0 or self.body > 0:
            rng = H[k - 1] - L[k - 1]
            if self.disp > 0 and atr > 0:
                ok = rng >= self.disp * atr
            if ok and self.body > 0:
                ok = rng > 0 and abs(C[k - 1] - O[k - 1]) / rng >= self.body
        if bull:
            if low_k - high_k2 >= need and ok:
                self.active.append({"type": BULL, "top": low_k, "bottom": high_k2,
                                    "index": s.t_l[k - 1], "tapped": False})
        elif low_k2 - high_k >= need and ok:
            self.active.append({"type": BEAR, "top": low_k2, "bottom": high_k,
                                "index": s.t_l[k - 1], "tapped": False})
        if len(self.active) > 20:
            self.active = self.active[-20:]
        return self.active


# ── core/market_structure trend, fed per closed HTF window ───────────────────

class Structure:
    """MarketStructureDetector.get_bias() after each update(window)."""

    def __init__(self, s: Series_, window: int = 200, swing_length: int = 5):
        self.s, self.window, self.L = s, window, swing_length
        w = 2 * swing_length + 1
        hr = pd.Series(s.high).rolling(w, center=True).max().to_numpy()
        lr = pd.Series(s.low).rolling(w, center=True).min().to_numpy()
        self.hs = np.flatnonzero(np.isfinite(hr) & (s.high == hr))
        self.ls = np.flatnonzero(np.isfinite(lr) & (s.low == lr))
        self.trend = 0

    def update(self, k: int) -> int:
        start = max(0, k - self.window + 1)
        if k - start + 1 < 2 * self.L + 1:
            return self.trend
        lo, hi = start + self.L, k - self.L
        a, b = np.searchsorted(self.hs, lo), np.searchsorted(self.hs, hi, side="right")
        c, d = np.searchsorted(self.ls, lo), np.searchsorted(self.ls, hi, side="right")
        if b - a < 2 or d - c < 2:
            return self.trend
        close = self.s.close[k]
        sh = self.hs[max(a, b - 3):b]
        sl = self.ls[max(c, d - 3):d]
        if close > self.s.high[sh[-1]]:
            if self.trend != BULL and int((close > self.s.high[sh]).sum()) >= 2:
                self.trend = BULL
        elif close < self.s.low[sl[-1]]:
            if self.trend != BEAR and int((close < self.s.low[sl]).sum()) >= 2:
                self.trend = BEAR
        return self.trend


# ── shared ───────────────────────────────────────────────────────────────────

@dataclass
class Cand:
    variant: str
    t_signal: int
    i_entry: int
    direction: int
    stop_dist: float
    feats: dict[str, bool]
    detail: dict[str, float] = field(default_factory=dict)


def _et_minutes(t: np.ndarray) -> np.ndarray:
    idx = pd.to_datetime(t, unit="s", utc=True).tz_convert("America/New_York")
    return (idx.hour * 60 + idx.minute).to_numpy()


def _hhmm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def _htf_feed_points(m5_time: np.ndarray, tf_time: np.ndarray, tf_seconds: int) -> np.ndarray:
    """For each M5 step i: index of the last CLOSED htf bar fed (-1 if none new)."""
    ends = np.searchsorted(tf_time, m5_time - tf_seconds, side="right")
    out = np.full(m5_time.size, -1, dtype=np.int64)
    prev = -1
    for i in range(m5_time.size):
        e = int(ends[i])
        if e >= 20 and e != prev:
            out[i] = e - 1
            prev = e
    return out


def _stop(entry: float, swing: float, atr: float, pip: float) -> tuple[float, bool]:
    sl = abs(entry - swing) + 0.5 * atr
    floor = max(12.0 * pip if pip > 0 else 0.0, 1.0 * atr)
    if floor > 0 and sl < floor:
        return floor, True
    return sl, False


def _common_flags(frame: sr.Frame, j: int, d: int) -> dict[str, bool]:
    secs = int(frame.time[j]) % 86400
    eh, ehp = frame.ema_h[j], frame.ema_h[max(j - 12, 0)]
    return {
        "htf_trend": bool(j >= 12 and d * (frame.close[j] - eh) > 0 and d * (eh - ehp) > 0),
        "adx_trend": bool(np.nan_to_num(frame.adx[j], nan=0.0) >= 20),
        "vol_high": bool(np.nan_to_num(frame.atr[j] - frame.atr_med[j], nan=-1.0) >= 0),
        "london": 7 * 3600 <= secs < 16 * 3600,
        "newyork": 12 * 3600 + 1800 <= secs < 21 * 3600,
    }


# ── HTFFVGFlip_v1 ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class HTFVariant:
    trend: str = "COUNTER"
    first_tap_only: bool = True
    require_retest: bool = True
    displacement: bool = True
    session_rth: bool = True

    @property
    def name(self) -> str:
        return (f"trend={self.trend}|first_tap={int(self.first_tap_only)}|retest={int(self.require_retest)}"
                f"|disp={int(self.displacement)}|rth={int(self.session_rth)}")

    def params(self) -> dict[str, Any]:
        return {"htf_trend_filter": self.trend, "require_unfilled_htf_fvg": self.first_tap_only,
                "require_retest": self.require_retest,
                "fvg_displacement_atr_mult": 1.5 if self.displacement else 0.0,
                "fvg_displacement_body_pct": 0.60 if self.displacement else 0.0,
                "session_filter_enabled": self.session_rth}


def all_htf_variants() -> list[HTFVariant]:
    return [HTFVariant(t, f, r, dsp, s) for t in ("COUNTER", "WITH", "OFF") for f in (True, False)
            for r in (True, False) for dsp in (True, False) for s in (True, False)]


class _HTFState:
    __slots__ = ("v", "hdet", "mdet", "status", "bias", "tap_t", "tap_j", "tap_bar", "m5", "swing",
                 "first", "trend_at_tap", "bars")

    def __init__(self, v: HTFVariant):
        self.v = v
        self.hdet = Det(0.2, 1.5 if v.displacement else 0.0, 0.60 if v.displacement else 0.0)
        self.mdet = Det(0.1)
        self.status, self.bias, self.tap_t, self.tap_j, self.tap_bar = 0, 0, None, None, None
        self.m5, self.swing, self.first, self.trend_at_tap, self.bars = None, None, False, 0, 0


# status: 0 AWAIT_HTF_TAP, 1 AWAIT_INVERSION_FVG, 2 AWAIT_RETEST, 3 AWAIT_INVERSION_CLOSE

def htf_candidates(m5: Series_, h1: Series_, pip: float, variants: Iterable[HTFVariant] | None = None,
                   *, frame: sr.Frame | None = None, max_age: int = 30,
                   session: tuple[str, str] = ("09:30", "16:00")) -> dict[str, list[Cand]]:
    variants = list(variants or all_htf_variants())
    states = [_HTFState(v) for v in variants]
    out: dict[str, list[Cand]] = {v.name: [] for v in variants}
    struct = Structure(h1)
    feed = _htf_feed_points(m5.time, h1.time, 3600)
    etm = _et_minutes(m5.time)
    s0, s1 = _hhmm(session[0]), _hhmm(session[1])
    n = m5.time.size
    hi_, lo_, cl_, t_ = m5.h_l, m5.l_l, m5.c_l, m5.t_l
    feed = feed.tolist()
    etm = etm.tolist()
    trend = 0
    for i in range(20, n):
        j = i - 1
        k = feed[i]
        if k >= 0:
            trend = struct.update(int(k))
            for st in states:
                st.hdet.update(h1, int(k))
        hj, lj, cj, tj = hi_[j], lo_[j], cl_[j], t_[j]
        in_rth = s0 <= etm[j] <= s1
        for st in states:
            v = st.v
            st.bars += 1
            if st.status and st.tap_bar is not None and st.bars - st.tap_bar > max_age:
                st.status, st.bias, st.tap_t, st.tap_j, st.tap_bar = 0, 0, None, None, None
                st.m5, st.swing, st.first = None, None, False
            ltf = None
            if st.status in (1, 3):
                ltf = st.mdet.update(m5, j)
            if st.status == 0:
                for f in st.hdet.active:
                    if v.first_tap_only and f["tapped"]:
                        continue
                    if f["type"] == BULL and f["bottom"] <= lj <= f["top"]:
                        d = BULL
                    elif f["type"] == BEAR and f["bottom"] <= hj <= f["top"]:
                        d = BEAR
                    else:
                        continue
                    if v.trend == "COUNTER" and trend != -d:
                        continue
                    if v.trend == "WITH" and trend != d:
                        continue
                    st.first = not f["tapped"]
                    f["tapped"] = True
                    st.status, st.bias, st.tap_t, st.tap_j, st.tap_bar = 1, d, tj, j, st.bars
                    st.trend_at_tap = trend
                    break
            if st.status == 1 and ltf:
                for f in reversed(ltf):
                    if f["index"] >= st.tap_t:
                        if (st.bias == BULL and f["type"] == BEAR) or (st.bias == BEAR and f["type"] == BULL):
                            st.m5 = f
                            st.swing = min(lo_[st.tap_j:j + 1]) if st.bias == BULL else max(hi_[st.tap_j:j + 1])
                            st.status = 2 if v.require_retest else 3
                            break
            if st.status == 2:
                f = st.m5
                if (st.bias == BULL and hj >= f["bottom"]) or (st.bias == BEAR and lj <= f["top"]):
                    st.status = 3
            if st.status == 3:
                if v.session_rth and not in_rth:
                    continue
                f = st.m5
                trig = (st.bias == BULL and cj > f["top"]) or (st.bias == BEAR and cj < f["bottom"])
                if st.swing is not None:
                    if (st.bias == BULL and lj < st.swing) or (st.bias == BEAR and hj > st.swing):
                        st.status = 0
                        continue
                if trig:
                    atr = _atr(m5, j)
                    sd, floored = _stop(cj, st.swing, atr, pip)
                    boundary = f["top"] if st.bias == BULL else f["bottom"]
                    disp = abs(cj - boundary) / atr if atr > 0 else 0.0
                    d = st.bias
                    feats = {
                        "first_tap": bool(st.first),
                        "counter_trend": st.trend_at_tap == -d,
                        "with_trend": st.trend_at_tap == d,
                        "inv_disp_025": disp >= 0.25, "inv_disp_050": disp >= 0.50,
                        "stop_ok": not floored, "rth": bool(in_rth),
                    }
                    if frame is not None:
                        feats.update(_common_flags(frame, j, d))
                    out[v.name].append(Cand(v.name, tj, i, d, float(sd), feats,
                                            {"disp_atr": disp, "entry_signal": float(cj),
                                             "swing": float(st.swing)}))
                    st.status = 0
    return out


# ── BiasIFVG_v1 ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class BiasVariant:
    bias_mode: str = "H4"
    key_levels: str = "ALL"
    leg_mode: str = "APPROACH"
    session: str = "0930-1100"

    @property
    def name(self) -> str:
        return f"bias={self.bias_mode}|levels={self.key_levels}|leg={self.leg_mode}|session={self.session}"

    def params(self) -> dict[str, Any]:
        sess = {"0930-1100": ("09:30", "11:00", True), "0800-1600": ("08:00", "16:00", True),
                "OFF": ("00:00", "23:59", False)}[self.session]
        return {"bias_mode": self.bias_mode, "key_levels": self.key_levels, "ifvg_leg_mode": self.leg_mode,
                "session_start": sess[0], "session_cutoff": sess[1], "session_filter_enabled": sess[2]}


def all_bias_variants() -> list[BiasVariant]:
    return [BiasVariant(b, k, leg, s) for b in ("H4", "OFF") for k in ("ALL", "FVG", "CISD_REJ")
            for leg in ("APPROACH", "REACTION", "BOTH") for s in ("0930-1100", "0800-1600", "OFF")]


def _cisd_and_rejections(s: Series_, k: int, bias: int, window: int = 300,
                         min_body_mult: float = 0.15) -> list[dict]:
    start = max(0, k - window + 1)
    if k - start + 1 < 5:
        return []
    O, H, L, C, T = s.o_l, s.h_l, s.l_l, s.c_l, s.t_l
    o, h, lo, c = O[k], H[k], L[k], C[k]
    body, total = abs(c - o), h - lo
    min_body = min_body_mult * _atr(s, k) if min_body_mult > 0 else 0.0
    levels = []
    if total > 0 and body >= min_body:
        if bias == BULL and (min(c, o) - lo) > body * 2:
            levels.append({"type": "BULLISH_REJECTION", "top": float(min(c, o)), "bottom": float(lo),
                           "time": T[k]})
        elif bias == BEAR and (h - max(c, o)) > body * 2:
            levels.append({"type": "BEARISH_REJECTION", "top": float(h), "bottom": float(max(c, o)),
                           "time": T[k]})
    if k - start + 1 >= 4:
        i = k - 1
        run = []
        while i >= start:
            down = C[i] < O[i]
            up = C[i] > O[i]
            if (bias == BULL and not down) or (bias == BEAR and not up):
                break
            run.append(i)
            i -= 1
        if len(run) >= 2:
            line = O[run[-1]]
            if bias == BULL and c > line:
                buf = (c - line) * 0.1
                levels.append({"type": "BULLISH_CISD", "top": line + buf, "bottom": line - buf, "time": T[k]})
            elif bias == BEAR and c < line:
                buf = (line - c) * 0.1
                levels.append({"type": "BEARISH_CISD", "top": line + buf, "bottom": line - buf, "time": T[k]})
    return levels


class _BiasState:
    __slots__ = ("v", "mdet", "bias", "status", "key", "tap_t", "leg_t", "started", "inv", "swing",
                 "conf", "extra", "bars")

    def __init__(self, v: BiasVariant):
        self.v = v
        self.mdet = Det(0.05)
        self.bias = 0
        self.status = 1 if v.bias_mode == "OFF" else 0   # 0 AWAIT_BIAS 1 KEY 2 SETUP 3 CLOSE
        self.key = self.tap_t = self.leg_t = self.started = self.inv = self.swing = None
        self.conf, self.extra, self.bars = 0, [], 0

    def clear_setup(self):
        self.key = self.inv = self.swing = self.tap_t = self.leg_t = self.started = None


def bias_candidates(m5: Series_, m15: Series_, h4: Series_, pip: float,
                    variants: Iterable[BiasVariant] | None = None, *, frame: sr.Frame | None = None,
                    max_age: int = 40) -> dict[str, list[Cand]]:
    variants = list(variants or all_bias_variants())
    states = [_BiasState(v) for v in variants]
    out: dict[str, list[Cand]] = {v.name: [] for v in variants}
    h4det, m15det = Det(0.1), Det(0.1)
    feed_h4 = _htf_feed_points(m5.time, h4.time, 14400)
    feed_m15 = _htf_feed_points(m5.time, m15.time, 900)
    etm = _et_minutes(m5.time)
    windows = {"0930-1100": (_hhmm("09:30"), _hhmm("11:00")), "0800-1600": (_hhmm("08:00"), _hhmm("16:00"))}
    n = m5.time.size
    hi_, lo_, cl_, t_ = m5.h_l, m5.l_l, m5.c_l, m5.t_l
    feed_h4, feed_m15, etm = feed_h4.tolist(), feed_m15.tolist(), etm.tolist()
    h4_dir = 0
    for i in range(20, n):
        j = i - 1
        kh = feed_h4[i]
        if kh >= 0:
            act = h4det.update(h4, int(kh))
            if act:
                h4_dir = act[-1]["type"]
                for st in states:
                    if st.v.bias_mode != "H4":
                        continue
                    if h4_dir != st.bias:
                        st.bias = h4_dir
                        st.clear_setup()
                        st.status = 1
                    elif st.status == 0:
                        st.bias, st.status = h4_dir, 1
        km = feed_m15[i]
        if km >= 0:
            m15det.update(m15, int(km))
            both = None
            for st in states:
                if st.v.bias_mode == "H4":
                    if st.bias:
                        new = _cisd_and_rejections(m15, int(km), st.bias)
                    else:
                        new = []
                else:
                    if both is None:
                        both = _cisd_and_rejections(m15, int(km), BULL) + _cisd_and_rejections(m15, int(km), BEAR)
                    new = both
                if new:
                    st.extra = (st.extra + [dict(x) for x in new])[-10:]
        hj, lj, cj, tj = hi_[j], lo_[j], cl_[j], t_[j]
        for st in states:
            v = st.v
            st.bars += 1
            if st.status >= 2 and st.started is not None and st.bars - st.started > max_age:
                st.status = 1
                st.clear_setup()
                if v.bias_mode == "OFF":
                    st.bias = 0
            m5f = None
            if st.status in (2, 3):
                m5f = st.mdet.update(m5, j)
            if st.status == 1:
                chosen, d = None, 0
                if v.key_levels in ("ALL", "FVG"):
                    for f in reversed(m15det.active):
                        if f["type"] == BULL and f["bottom"] <= lj <= f["top"] and (v.bias_mode == "OFF" or st.bias == BULL):
                            chosen, d = f, BULL
                            break
                        if f["type"] == BEAR and f["bottom"] <= hj <= f["top"] and (v.bias_mode == "OFF" or st.bias == BEAR):
                            chosen, d = f, BEAR
                            break
                if chosen is None and v.key_levels in ("ALL", "CISD_REJ"):
                    for f in reversed(st.extra):
                        bull = f["type"] in ("BULLISH_CISD", "BULLISH_REJECTION")
                        if bull and f["bottom"] <= lj <= f["top"] and (v.bias_mode == "OFF" or st.bias == BULL):
                            chosen, d = f, BULL
                            break
                        if (not bull) and f["bottom"] <= hj <= f["top"] and (v.bias_mode == "OFF" or st.bias == BEAR):
                            chosen, d = f, BEAR
                            break
                if chosen is not None:
                    st.bias = d
                    st.key, st.status, st.tap_t = chosen, 2, tj
                    w0 = max(0, j - 39, j - 499)
                    vals = hi_[w0:j + 1] if d == BULL else lo_[w0:j + 1]
                    pos = vals.index(max(vals)) if d == BULL else vals.index(min(vals))
                    st.leg_t = t_[w0 + pos]
                    st.started = st.bars
                    top, bot = float(chosen["top"]), float(chosen["bottom"])
                    cnt = 0
                    for lvl in list(m15det.active) + st.extra:
                        if lvl is chosen:
                            continue
                        if lvl["bottom"] <= top and lvl["top"] >= bot:
                            cnt += 1
                    st.conf = cnt
            if st.status == 2 and m5f:
                if v.leg_mode == "REACTION":
                    ws, we = st.tap_t, 2**62
                elif v.leg_mode == "BOTH":
                    ws, we = st.leg_t, 2**62
                else:
                    ws, we = st.leg_t, st.tap_t
                for f in reversed(m5f):
                    if ws <= f["index"] <= we:
                        if (st.bias == BULL and f["type"] == BEAR) or (st.bias == BEAR and f["type"] == BULL):
                            st.inv = f
                            st.status = 3
                            j0 = max(0, j - 19)
                            st.swing = min(lo_[j0:j + 1]) if st.bias == BULL else max(hi_[j0:j + 1])
                            break
            if st.status == 3:
                if v.session != "OFF":
                    s0, s1 = windows[v.session]
                    if not (s0 <= etm[j] <= s1):
                        continue
                f = st.inv
                trig = (st.bias == BULL and cj > f["top"]) or (st.bias == BEAR and cj < f["bottom"])
                if (st.bias == BULL and cj < st.swing) or (st.bias == BEAR and cj > st.swing):
                    st.status = 1
                    if v.bias_mode == "OFF":
                        st.bias = 0
                    continue
                if trig:
                    atr = _atr(m5, j)
                    sd, floored = _stop(cj, st.swing, atr, pip)
                    boundary = f["top"] if st.bias == BULL else f["bottom"]
                    disp = abs(cj - boundary) / atr if atr > 0 else 0.0
                    d = st.bias
                    ktype = st.key.get("type") if isinstance(st.key.get("type"), str) else "FVG"
                    s0, s1 = windows["0930-1100"]
                    feats = {
                        "levels_conf1": st.conf >= 1, "levels_conf2": st.conf >= 2,
                        "inv_disp_025": disp >= 0.25, "inv_disp_050": disp >= 0.50,
                        "stop_ok": not floored,
                        "key_fvg": ktype == "FVG", "key_cisd": "CISD" in ktype, "key_rejection": "REJECTION" in ktype,
                        "h4_aligned": h4_dir == d,
                        "ny_open": bool(s0 <= etm[j] <= s1),
                    }
                    if frame is not None:
                        feats.update(_common_flags(frame, j, d))
                    out[v.name].append(Cand(v.name, tj, i, d, float(sd), feats,
                                            {"disp_atr": disp, "entry_signal": float(cj), "swing": float(st.swing),
                                             "conf": st.conf}))
                    st.status = 1
                    if v.bias_mode == "OFF":
                        st.bias = 0
    return out


# ── outcomes in synth_research's CandSet form, so pick/stats apply unchanged ─

def to_candset(symbol: str, strategy: str, variant: str, cands: list[Cand], m5_arrays: dict[str, np.ndarray],
               rr: Iterable[float] = RR) -> sr.CandSet:
    feats_names = sorted({k for c in cands for k in c.feats}) if cands else []
    cs = sr.CandSet(symbol, strategy, {"variant": variant},
                    np.array([m5_arrays["time"][c.i_entry] for c in cands], dtype=np.int64),
                    np.array([c.i_entry for c in cands], dtype=np.int64),
                    np.array([c.direction for c in cands], dtype=np.int64),
                    {f: np.array([bool(c.feats.get(f, False)) for c in cands], dtype=bool) for f in feats_names})
    rrs = np.asarray(list(rr), dtype=np.float64)
    if not cands:
        for q in rrs:
            cs.r[(0.0, float(q))] = np.zeros(0)
            cs.t_exit[(0.0, float(q))] = np.zeros(0, dtype=np.int64)
        return cs
    ov_abs, ov_frac, spike_side, lam = sr.overshoot_for(symbol)
    ent = cs.i_entry
    valid = ent < m5_arrays["time"].size
    sd = np.array([c.stop_dist for c in cands], dtype=np.float64)
    r_full = np.full((len(cands), rrs.size), np.nan)
    t_full = np.zeros((len(cands), rrs.size), dtype=np.int64)
    if valid.any():
        r_, ex, _ = sr.resolve(m5_arrays["open"], m5_arrays["high"], m5_arrays["low"], m5_arrays["close"],
                               m5_arrays["spread"], ent[valid], cs.direction[valid], sd[valid], rrs,
                               sr.MAX_HOLD, ov_abs, ov_frac, spike_side, lam)
        r_full[valid] = r_
        t_full[valid] = m5_arrays["time"][ex]
    for q, rv in enumerate(rrs):
        cs.r[(0.0, float(rv))] = r_full[:, q]
        cs.t_exit[(0.0, float(rv))] = t_full[:, q]
    return cs
