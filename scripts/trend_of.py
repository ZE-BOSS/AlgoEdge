"""
scripts/trend_of.py - Trend Breakout, Range Breakout, Range Revert and FibTrend, each with every
order-flow / order-book / candle confluence recorded, plus a new "institutional node" trend entry.

Outputs one candidate file per strategy x market (same columns as of_zones.py / pa_study.py), so
combo_eval.py can run the candle x flow x context x session x target grid with the January-June /
July-November split.

Strategies (base rules = the app's, on CME futures 1-minute bars + 1-minute trade flow)
  tb       TrendBreakout_v1: M15 close beyond the N-day channel; stop 2 x daily ATR; exits
           "trail" (chandelier 1.5 x daily ATR from the best close of the last 100 bars) or a
           fixed R. Variant setup "retest": after a break, the first bar within 12 that comes
           back to the broken level (0.3 ATR) and closes on the breakout side.
  rb       RangeBreakout_v1: M5 close beyond the prior 20-bar high/low; stop 5 x ATR (app) or
           1.5 x ATR ("tight"); targets 1.5R/2R/3R. Same "retest" variant.
  rr       RangeRevert_v1: M5 close >= 2 x ATR from the 50 EMA, enter back toward it; stop 5 x ATR
           (app) or beyond the bar + 0.5 ATR ("tight"); targets 1.5R/2R and "ema" (the EMA itself).
  ft       FibTrend_v1: H4 fractal legs (5 bars), >= 3 H4 ATR, trend = H4 SMA300 side + slope 30;
           entry on the first M15 bar that trades back to 0.5 (fill at the next open), stop 0.786,
           exit chandelier 3 x H4 ATR or fixed R; setups expire after 30 H4 bars.
  ti       Institutional-node trend entry (new): trend from H1 EMA50 vs EMA200 + slope; nodes are
           the peaks of the big-trade volume profile of the previous 3 sessions (where size was
           concentrated). A long is the first M15 bar that trades down into a node below price
           from above (a short: up into one from below); fill at the next open, stop 0.3 ATR
           beyond the bar/node, targets 2R/3R or trail.

Confluences recorded on the signal bar (side-signed): pin, engulf, doji, double (top/bottom),
sweep (took the 20-bar extreme and closed back), absorb, flip (second-half delta with the trade),
big_with, big_surge (big volume >= 2 x avg), vol_surge (>= 1.5 x avg), delta_with, cvd_with
(12-bar cumulative delta with the trade), appr_delta, at_node / node_strength (an institutional
node within 0.5 ATR of the fill), book_with (top-of-book size imbalance with the trade, when a
bbo-1m file exists), vol_expanding (20d/60d realised vol >= 1.15), trend (H1, +1 with / -1 against).

    python trend_of.py --data /opt/algoedge/research_data --markets gold,nasdaq --strategies tb,rb,rr,ft,ti \
        --out /opt/algoedge/research_data/trend
"""
from __future__ import annotations

import argparse
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from of_zones import COST, load  # noqa: E402

RULE = {"M5": "5min", "M15": "15min", "H1": "1h", "H4": "4h"}
MAX_HOLD = {"tb": 20, "rb": 2, "rr": 2, "ft": 20, "ti": 5}       # days


# ── bars and indicators ───────────────────────────────────────────────────────
def tf_bars(m1: pd.DataFrame, tf: str) -> pd.DataFrame:
    rule = RULE[tf]
    g = m1.resample(rule, label="left", closed="left")
    b = g.agg({"open": "first", "high": "max", "low": "min", "close": "last", "vol": "sum", "delta": "sum",
               "big_buy": "sum", "big_sell": "sum"}).dropna(subset=["open"])
    # delta of the second half of each bar (the "flip")
    start = m1.index.floor(rule)
    half = pd.Timedelta(rule) / 2
    second = (m1.index - start) >= half
    b["delta_h2"] = m1["delta"].where(second, 0.0).groupby(start).sum().reindex(b.index).fillna(0.0)
    tr = np.maximum(b["high"] - b["low"], np.maximum((b["high"] - b["close"].shift()).abs(),
                                                     (b["low"] - b["close"].shift()).abs()))
    b["atr"] = tr.rolling(14).mean()
    b["vol_avg"] = b["vol"].rolling(50).mean()
    b["big"] = b["big_buy"] + b["big_sell"]
    b["big_avg"] = b["big"].rolling(50).mean()
    b["ema50"] = b["close"].ewm(span=50, adjust=False).mean()
    return b


def daily_atr(m1: pd.DataFrame, n: int = 14) -> pd.Series:
    """Daily ATR known at the START of each UTC day (completed days only)."""
    d = m1.resample("1D").agg({"high": "max", "low": "min", "close": "last"}).dropna()
    tr = np.maximum(d["high"] - d["low"], np.maximum((d["high"] - d["close"].shift()).abs(),
                                                     (d["low"] - d["close"].shift()).abs()))
    return tr.rolling(n).mean().shift(1)


def vol_ratio(m1: pd.DataFrame) -> pd.Series:
    d = m1["close"].resample("1D").last().dropna()
    r = np.log(d).diff()
    return (r.rolling(20).std() / r.rolling(60).std()).shift(1)


def inst_nodes(m1: pd.DataFrame, days: int = 3, top: int = 6) -> dict[pd.Timestamp, list[tuple[float, float]]]:
    """For each UTC day: the price nodes where big trades concentrated over the previous `days` sessions,
    [(price, strength)] with strength = node big volume / mean bin."""
    big = (m1["big_buy"] + m1["big_sell"])
    px = m1["vwap"]
    day = m1.index.normalize()
    out = {}
    uniq = day.unique()
    for k in range(days, len(uniq)):
        d = uniq[k]
        sel = (day >= uniq[k - days]) & (day < d) & (big.values > 0)
        if sel.sum() < 20:
            continue
        p, v = px.values[sel], big.values[sel]
        step = max(float(np.median(p)) * 0.0005, 1e-9)
        prof = pd.Series(v).groupby(np.round(p / step) * step).sum().sort_index()
        sm = prof.rolling(3, center=True, min_periods=1).mean()
        vv = sm.values
        mean = float(vv.mean()) or 1.0
        peaks = [(float(prof.index[i]), float(vv[i] / mean)) for i in range(len(vv))
                 if (i == 0 or vv[i] >= vv[i - 1]) and (i == len(vv) - 1 or vv[i] >= vv[i + 1]) and vv[i] >= 2 * mean]
        peaks.sort(key=lambda x: -x[1])
        out[d] = peaks[:top]
    return out


def h1_trend(m1: pd.DataFrame) -> pd.Series:
    """+1 / -1 / 0 per H1 bar, known at the bar's CLOSE: EMA50 vs EMA200 and EMA50 rising/falling over 10."""
    h = m1["close"].resample("1h").last().dropna()
    e50, e200 = h.ewm(span=50, adjust=False).mean(), h.ewm(span=200, adjust=False).mean()
    s = np.sign(e50 - e200)
    slope = np.sign(e50 - e50.shift(10))
    t = (s * (s == slope)).fillna(0)
    t.iloc[:200] = 0
    t.index = t.index + pd.Timedelta(hours=1)          # usable from the bar's close
    return t


def load_book(data: Path, market: str) -> pd.Series | None:
    """Top-of-book size imbalance per minute (bid - ask) / (bid + ask), if a bbo-1m file exists."""
    p = data / f"{market}_bbo.parquet"
    if not p.exists():
        return None
    b = pd.read_parquet(p)
    b.index = pd.to_datetime(b["time"], unit="s")
    imb = (b["bid_sz"] - b["ask_sz"]) / (b["bid_sz"] + b["ask_sz"]).replace(0, np.nan)
    return imb.sort_index()


# ── simulation on 1-minute bars ───────────────────────────────────────────────
class Sim:
    def __init__(self, m1: pd.DataFrame, cost: float):
        self.t = m1.index.values
        self.h, self.l, self.c = (m1[k].values for k in ("high", "low", "close"))
        self.cost = cost

    def fixed(self, entry_t, side, entry, stop, tps: dict, days: float):
        s0 = np.searchsorted(self.t, np.datetime64(entry_t))
        s1 = np.searchsorted(self.t, np.datetime64(entry_t + pd.Timedelta(days=days)))
        risk = (entry - stop) * side
        H, L, C = self.h[s0:s1], self.l[s0:s1], self.c[s0:s1]
        out = {}
        if risk <= 0 or len(C) == 0:
            return {k: (np.nan, None) for k in tps}
        hs = (L <= stop) if side == 1 else (H >= stop)
        ist = int(np.argmax(hs)) if hs.any() else None
        for k, tp in tps.items():
            if tp is None:
                out[k] = (np.nan, None)
                continue
            ht = (H >= tp) if side == 1 else (L <= tp)
            itp = int(np.argmax(ht)) if ht.any() else None
            if ist is not None and (itp is None or ist <= itp):
                r, xi = -1.0, ist
            elif itp is not None:
                r, xi = (tp - entry) * side / risk, itp
            else:
                r, xi = (C[-1] - entry) * side / risk, len(C) - 1
            out[k] = (r - self.cost / risk, self.t[s0 + xi])
        return out

    def trail(self, bars: pd.DataFrame, k_entry: int, side, entry, stop, dist: np.ndarray, lookback: int,
              days: float, update: np.ndarray | None = None):
        """Chandelier on bar closes: after a bar closes (only bars where `update` is True, e.g. the
        M15 bar that ends an H4 bar), stop = max(stop, best of those closes over the last
        `lookback` bars since entry - dist[k]); stops checked on later bars' high/low (gap fills at
        the open)."""
        o, h, l, c = (bars[x].values for x in ("open", "high", "low", "close"))
        t = bars.index
        risk = (entry - stop) * side
        if risk <= 0:
            return np.nan, None
        end_t = t[k_entry] + pd.Timedelta(days=days)
        cur = stop
        win: list[float] = []
        for k in range(k_entry, len(c)):
            if t[k] >= end_t:
                return (c[k - 1] - entry) * side / risk - self.cost / risk, t[k - 1].to_datetime64()
            hit = (l[k] <= cur) if side == 1 else (h[k] >= cur)
            if hit:
                px = min(cur, o[k]) if side == 1 else max(cur, o[k])
                return (px - entry) * side / risk - self.cost / risk, t[k].to_datetime64()
            if update is None or update[k]:
                win.append(c[k])
                if len(win) > lookback:
                    win.pop(0)
                if np.isfinite(dist[k]):
                    best = max(win) if side == 1 else min(win)
                    lvl = best - side * dist[k]
                    cur = max(cur, lvl) if side == 1 else min(cur, lvl)
        return np.nan, None


# ── features ──────────────────────────────────────────────────────────────────
def features(b, i: int, side: int, a: float, fill: float, nodes: list, book, trend: float, vr: float) -> dict:
    o, h, l, c = b["open"].values, b["high"].values, b["low"].values, b["close"].values
    dl, h2, bb, bs = b["delta"].values, b["delta_h2"].values, b["big_buy"].values, b["big_sell"].values
    vol, vavg, big, bavg = b["vol"].values, b["vol_avg"].values, b["big"].values, b["big_avg"].values
    rng = h[i] - l[i]
    body = abs(c[i] - o[i])
    lw, uw = min(o[i], c[i]) - l[i], h[i] - max(o[i], c[i])
    pin = rng > 0 and ((lw >= 0.5 * rng and c[i] >= l[i] + 0.5 * rng) if side == 1 else
                       (uw >= 0.5 * rng and c[i] <= h[i] - 0.5 * rng))
    engulf = (c[i] > o[i] and c[i - 1] < o[i - 1] and c[i] >= o[i - 1] and o[i] <= c[i - 1]) if side == 1 else \
             (c[i] < o[i] and c[i - 1] > o[i - 1] and c[i] <= o[i - 1] and o[i] >= c[i - 1])
    doji = rng > 0 and body <= 0.15 * rng
    double = False
    for j in range(max(1, i - 36), i - 2):
        if side == -1 and h[j] >= h[j - 1] and h[j] >= h[j + 1] and abs(h[j] - h[i]) <= 0.3 * a \
                and min(l[j:i]) <= h[i] - 0.7 * a:
            double = True
            break
        if side == 1 and l[j] <= l[j - 1] and l[j] <= l[j + 1] and abs(l[j] - l[i]) <= 0.3 * a \
                and max(h[j:i]) >= l[i] + 0.7 * a:
            double = True
            break
    lo20, hi20 = l[max(0, i - 20):i].min(), h[max(0, i - 20):i].max()
    sweep = (l[i] < lo20 and c[i] > lo20) if side == 1 else (h[i] > hi20 and c[i] < hi20)
    absorb = bool(np.isfinite(vavg[i]) and vol[i] >= 1.5 * vavg[i] and dl[i] * side < 0 and rng > 0 and
                  (((c[i] - l[i]) / rng >= 0.5) if side == 1 else ((h[i] - c[i]) / rng >= 0.5)))
    near = [(p, s) for p, s in nodes if abs(p - fill) <= 0.5 * a]
    bk = np.nan
    if book is not None:
        j = book.index.searchsorted(b.index[i] + (b.index[1] - b.index[0]), side="left") - 1
        if 0 <= j < len(book):
            bk = float(book.iloc[j]) * side
    return {"pin": bool(pin), "engulf": bool(engulf), "doji": bool(doji), "double": double, "sweep": bool(sweep),
            "absorb": absorb, "flip": bool(h2[i] * side > 0), "big_with": bool((bb[i] - bs[i]) * side > 0),
            "big_surge": bool(np.isfinite(bavg[i]) and bavg[i] > 0 and big[i] >= 2 * bavg[i]),
            "vol_surge": bool(np.isfinite(vavg[i]) and vol[i] >= 1.5 * vavg[i]),
            "delta_with": bool(dl[i] * side > 0), "cvd_with": bool(dl[max(0, i - 11):i + 1].sum() * side > 0),
            "appr_delta": float(dl[max(0, i - 6):i].sum() * side),
            "at_node": bool(near), "node_strength": float(max((s for _, s in near), default=0.0)),
            "book_with": bk > 0.1 if np.isfinite(bk) else False, "book_imb": bk,
            "trend": float(trend), "vol_expanding": bool(np.isfinite(vr) and vr >= 1.15),
            "hour": b.index[i].hour + b.index[i].minute / 60.0, "atr": float(a)}


class Ctx:
    """Everything a candidate needs, aligned to one timeframe's bars."""

    def __init__(self, m1, tf, data, market):
        self.b = tf_bars(m1, tf)
        idx = self.b.index
        self.datr = daily_atr(m1).reindex(idx.normalize()).values
        self.vr = vol_ratio(m1).reindex(idx.normalize()).values
        tr = h1_trend(m1)
        self.trend = tr.reindex(idx + (idx[1] - idx[0]), method="ffill").fillna(0).values
        self.nodes = inst_nodes(m1)
        self.days = idx.normalize()
        self.book = load_book(data, market)

    def feat(self, i, side, fill):
        a = float(self.b["atr"].values[i])
        return features(self.b, i, side, a, fill, self.nodes.get(self.days[i], []), self.book,
                        self.trend[i] * side, self.vr[i])


def row(base: dict, out: dict) -> dict:
    for k, (r, x) in out.items():
        base[f"r_{k}"], base[f"x_{k}"] = r, x
    return base


# ── strategies ────────────────────────────────────────────────────────────────
def gen_tb(m1, sim, data, market, start, channel_days=20):
    X = Ctx(m1, "M15", data, market)
    b = X.b
    o, h, l, c = (b[k].values for k in ("open", "high", "low", "close"))
    t = b.index
    w = channel_days * 96
    hi = pd.Series(h).rolling(w).max().shift(1).values
    lo = pd.Series(l).rolling(w).min().shift(1).values
    rows = []
    t0 = pd.Timestamp(start)
    dist = 1.5 * X.datr
    pend: list[dict] = []                               # breaks waiting for a retest
    for i in range(w + 2, len(b) - 1):
        if t[i] < t0 or not np.isfinite(X.datr[i]):
            continue
        side = 1 if c[i] > hi[i] else (-1 if c[i] < lo[i] else 0)
        setups = []
        if side:
            setups.append(("break", side, hi[i] if side == 1 else lo[i]))
            pend.append({"side": side, "lvl": hi[i] if side == 1 else lo[i], "until": i + 12, "from": i})
        a = b["atr"].values[i]
        keep = []
        for p in pend:
            if p["from"] == i:
                keep.append(p)
                continue
            if i > p["until"]:
                continue
            s = p["side"]
            if (s == 1 and c[i] < p["lvl"] - 0.5 * a) or (s == -1 and c[i] > p["lvl"] + 0.5 * a):
                continue                                # failed break
            touched = (l[i] <= p["lvl"] + 0.3 * a) if s == 1 else (h[i] >= p["lvl"] - 0.3 * a)
            held = (c[i] > p["lvl"]) if s == 1 else (c[i] < p["lvl"])
            if touched and held:
                setups.append(("retest", s, p["lvl"]))
                continue
            keep.append(p)
        pend = keep[-20:]
        for setup, s, lvl in setups:
            entry, et = o[i + 1], t[i + 1]
            stop = entry - s * 2.0 * X.datr[i]
            risk = 2.0 * X.datr[i]
            res = sim.fixed(et, s, entry, stop, {"r2": entry + s * 2 * risk, "r3": entry + s * 3 * risk,
                                                 "r5": entry + s * 5 * risk}, MAX_HOLD["tb"])
            res["trail"] = sim.trail(b, i + 1, s, entry, stop, dist, 100, MAX_HOLD["tb"])
            rows.append(row({"t": t[i], "entry_t": et, "side": s, "setup": setup, "level": lvl,
                             **X.feat(i, s, entry)}, res))
    return pd.DataFrame(rows)


def gen_rb(m1, sim, data, market, start, n=20):
    X = Ctx(m1, "M5", data, market)
    b = X.b
    o, h, l, c, atr = (b[k].values for k in ("open", "high", "low", "close", "atr"))
    t = b.index
    hi = pd.Series(h).rolling(n).max().shift(1).values
    lo = pd.Series(l).rolling(n).min().shift(1).values
    rows, pend = [], []
    t0 = pd.Timestamp(start)
    for i in range(n + 60, len(b) - 1):
        a = atr[i]
        if t[i] < t0 or not np.isfinite(a) or a <= 0:
            continue
        side = 1 if c[i] > hi[i] else (-1 if c[i] < lo[i] else 0)
        setups = []
        if side:
            setups.append(("break", side, hi[i] if side == 1 else lo[i]))
            pend.append({"side": side, "lvl": hi[i] if side == 1 else lo[i], "until": i + 12, "from": i})
        keep = []
        for p in pend:
            if p["from"] == i:
                keep.append(p)
                continue
            if i > p["until"]:
                continue
            s = p["side"]
            if (s == 1 and c[i] < p["lvl"] - 0.5 * a) or (s == -1 and c[i] > p["lvl"] + 0.5 * a):
                continue
            if ((l[i] <= p["lvl"] + 0.3 * a) if s == 1 else (h[i] >= p["lvl"] - 0.3 * a)) and \
                    ((c[i] > p["lvl"]) if s == 1 else (c[i] < p["lvl"])):
                setups.append(("retest", s, p["lvl"]))
                continue
            keep.append(p)
        pend = keep[-20:]
        for setup, s, lvl in setups:
            entry, et = o[i + 1], t[i + 1]
            res = {}
            for geo, k in (("app", 5.0), ("tight", 1.5)):
                stop = entry - s * k * a
                risk = k * a
                r = sim.fixed(et, s, entry, stop, {f"{geo}_r1_5": entry + s * 1.5 * risk,
                                                   f"{geo}_r2": entry + s * 2 * risk,
                                                   f"{geo}_r3": entry + s * 3 * risk}, MAX_HOLD["rb"])
                res.update(r)
            rows.append(row({"t": t[i], "entry_t": et, "side": s, "setup": setup, "level": lvl,
                             **X.feat(i, s, entry)}, res))
    return pd.DataFrame(rows)


def gen_rr(m1, sim, data, market, start, k_atr=2.0):
    X = Ctx(m1, "M5", data, market)
    b = X.b
    o, h, l, c, atr, ema = (b[k].values for k in ("open", "high", "low", "close", "atr", "ema50"))
    t = b.index
    rows = []
    t0 = pd.Timestamp(start)
    for i in range(120, len(b) - 1):
        a = atr[i]
        if t[i] < t0 or not np.isfinite(a) or a <= 0:
            continue
        gap = c[i] - ema[i]
        if abs(gap) <= k_atr * a:
            continue
        s = -1 if gap > 0 else 1
        entry, et = o[i + 1], t[i + 1]
        res = {}
        stop = entry - s * 5.0 * a
        risk = 5.0 * a
        res.update(sim.fixed(et, s, entry, stop, {"app_r1_5": entry + s * 1.5 * risk, "app_r2": entry + s * 2 * risk,
                                                  "app_ema": ema[i] if (ema[i] - entry) * s > 0 else None},
                             MAX_HOLD["rr"]))
        stop = (l[i] - 0.5 * a) if s == 1 else (h[i] + 0.5 * a)
        if (entry - stop) * s < 0.5 * a:
            stop = entry - s * 0.5 * a
        risk = (entry - stop) * s
        res.update(sim.fixed(et, s, entry, stop, {"tight_r1_5": entry + s * 1.5 * risk, "tight_r2": entry + s * 2 * risk,
                                                  "tight_ema": ema[i] if (ema[i] - entry) * s >= 0.8 * risk else None},
                             MAX_HOLD["rr"]))
        rows.append(row({"t": t[i], "entry_t": et, "side": s, "setup": "stretch", "stretch_atr": abs(gap) / a,
                         **X.feat(i, s, entry)}, res))
    return pd.DataFrame(rows)


def h4_frame(m1):
    h = m1.resample("4h", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    return h


def gen_ft(m1, sim, data, market, start):
    from types import SimpleNamespace
    sys.path.insert(0, "/opt/algoedge/demo/app")
    try:
        from backend.strategies.strategy_fib.core import atr_wilder, pivots
    except Exception:  # noqa: BLE001
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from backend.strategies.strategy_fib.core import atr_wilder, pivots
    p = SimpleNamespace(swing_bars=5, min_leg_atr=3.0, entry_level=0.5, stop_level=0.786, order_expiry_bars=30,
                        trend_sma_bars=300, trend_slope_bars=30, trail_atr_multiple=3.0, atr_bars=14)
    X = Ctx(m1, "M15", data, market)
    b = X.b
    o, h, l, c = (b[k].values for k in ("open", "high", "low", "close"))
    t = b.index
    H = h4_frame(m1)
    hh, ll, cc = H["high"].values, H["low"].values, H["close"].values
    ht = H.index
    hatr = atr_wilder(hh, ll, cc, 14)
    sma = pd.Series(cc).rolling(300).mean().values
    # H4 ATR and trail distance as seen by each M15 bar (last COMPLETED H4 bar)
    pos = np.searchsorted(ht.values, (t - pd.Timedelta(hours=4)).values, side="right") - 1
    dist = np.where(pos >= 0, 3.0 * hatr[np.clip(pos, 0, None)], np.nan)
    # the M15 bar that completes an H4 bar: its close is the H4 close, and the trail moves then
    h4_end = ((t + pd.Timedelta(minutes=15)).floor("4h") == (t + pd.Timedelta(minutes=15)))
    # ATR of the H4 bar that has just completed at that close
    pos_c = np.searchsorted(ht.values, (t + pd.Timedelta(minutes=15) - pd.Timedelta(hours=4)).values, side="right") - 1
    dist = np.where(pos_c >= 0, 3.0 * hatr[np.clip(pos_c, 0, None)], dist)
    seq, legs = [], []
    k = 5
    for (i, typ, px) in pivots(hh, ll, k):
        if seq and seq[-1][1] == typ:
            if (typ == 1 and px > seq[-1][2]) or (typ == -1 and px < seq[-1][2]):
                seq[-1] = (i, typ, px)
            else:
                continue
        else:
            seq.append((i, typ, px))
        if len(seq) < 2:
            continue
        (a_, _, pa), (bi, tb, pb) = seq[-2], seq[-1]
        conf = bi + k
        if conf >= len(cc):
            continue
        d = 1 if tb == 1 else -1
        rng = abs(pb - pa)
        if rng < 3.0 * hatr[bi]:
            continue
        trend_ok = conf - 30 >= 0 and np.isfinite(sma[conf]) and np.isfinite(sma[conf - 30]) and \
            (cc[conf] - sma[conf]) * d > 0 and (sma[conf] - sma[conf - 30]) * d > 0
        entry, stop, end0 = pb - d * 0.5 * rng, pb - d * 0.786 * rng, pb
        missed = (ll[bi + 1:conf + 1].min() <= entry) if d > 0 else (hh[bi + 1:conf + 1].max() >= entry)
        legs.append(dict(d=d, entry=entry, stop=stop, end0=end0, trend_ok=bool(trend_ok), missed=bool(missed),
                         conf_time=ht[conf] + pd.Timedelta(hours=4),
                         expires=ht[conf] + pd.Timedelta(hours=4 * 31), leg=rng))
    rows = []
    t0 = pd.Timestamp(start)
    for g in legs:
        if g["missed"] or g["conf_time"] < t0:
            continue
        i0 = t.searchsorted(g["conf_time"])
        i1 = t.searchsorted(g["expires"])
        d = g["d"]
        for i in range(i0, min(i1, len(b) - 1)):
            beyond = (h[i] > g["end0"]) if d > 0 else (l[i] < g["end0"])
            touch = (l[i] <= g["entry"]) if d > 0 else (h[i] >= g["entry"])
            if touch and not beyond:
                entry, et = o[i + 1], t[i + 1]
                stop = g["stop"]
                if (entry - stop) * d <= 0:
                    break
                risk = (entry - stop) * d
                res = sim.fixed(et, d, entry, stop, {"r2": entry + d * 2 * risk, "r3": entry + d * 3 * risk,
                                                     "leg_end": g["end0"] if (g["end0"] - entry) * d > 0 else None},
                                MAX_HOLD["ft"])
                res["trail"] = sim.trail(b, i + 1, d, entry, stop, dist, 10 ** 6, MAX_HOLD["ft"], update=h4_end)
                rows.append(row({"t": t[i], "entry_t": et, "side": d, "setup": "fib", "fib_trend": g["trend_ok"],
                                 **X.feat(i, d, entry)}, res))
                break
            if beyond:
                break
    return pd.DataFrame(rows)


def gen_ti(m1, sim, data, market, start):
    X = Ctx(m1, "M15", data, market)
    b = X.b
    o, h, l, c, atr = (b[k].values for k in ("open", "high", "low", "close", "atr"))
    t = b.index
    dist = 1.5 * X.datr
    rows = []
    t0 = pd.Timestamp(start)
    for i in range(60, len(b) - 1):
        a = atr[i]
        tr = X.trend[i]
        if t[i] < t0 or not tr or not np.isfinite(a) or a <= 0:
            continue
        s = int(tr)
        nodes = X.nodes.get(X.days[i], [])
        hit = None
        for p, st in nodes:
            if s == 1 and l[i - 1] > p + 0.1 * a and l[i] <= p + 0.1 * a and c[i] >= p - 0.5 * a:
                hit = (p, st)
                break
            if s == -1 and h[i - 1] < p - 0.1 * a and h[i] >= p - 0.1 * a and c[i] <= p + 0.5 * a:
                hit = (p, st)
                break
        if hit is None:
            continue
        p, st = hit
        entry, et = o[i + 1], t[i + 1]
        stop = (min(l[i], p) - 0.3 * a) if s == 1 else (max(h[i], p) + 0.3 * a)
        if (entry - stop) * s < 0.5 * a:
            stop = entry - s * 0.5 * a
        risk = (entry - stop) * s
        res = sim.fixed(et, s, entry, stop, {"r1_5": entry + s * 1.5 * risk, "r2": entry + s * 2 * risk,
                                             "r3": entry + s * 3 * risk}, MAX_HOLD["ti"])
        res["trail"] = sim.trail(b, i + 1, s, entry, stop, dist, 100, MAX_HOLD["ti"])
        f = X.feat(i, s, p)
        f["node_strength"] = st
        f["strong_node"] = st >= 4
        rows.append(row({"t": t[i], "entry_t": et, "side": s, "setup": "inst_node", "node": p, **f}, res))
    return pd.DataFrame(rows)


GEN = {"tb": gen_tb, "rb": gen_rb, "rr": gen_rr, "ft": gen_ft, "ti": gen_ti}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="/opt/algoedge/research_data")
    ap.add_argument("--markets", default="gold,nasdaq,sp500,silver,us30,oil,eurusd,gbpusd,usdjpy")
    ap.add_argument("--strategies", default="tb,rb,rr,ft,ti")
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--end", default="2026-11-27")
    ap.add_argument("--out", default="/opt/algoedge/research_data/trend")
    a = ap.parse_args()
    data, out = Path(a.data), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for m in a.markets.split(","):
        try:
            m1 = load(data, m, "2025-01-01", a.end)
        except FileNotFoundError:
            print(f"{m}: no data", flush=True)
            continue
        sim = Sim(m1, COST.get(m, 0.0))
        for s in a.strategies.split(","):
            t0 = time.time()
            try:
                d = GEN[s](m1, sim, data, m, a.start)
            except Exception as e:  # noqa: BLE001
                print(f"{m} {s}: FAILED {type(e).__name__}: {e}", flush=True)
                continue
            if len(d):
                d.to_parquet(out / f"cand_{s}_{m}.parquet")
            print(f"{m} {s}: {len(d)} candidates in {time.time() - t0:.0f}s", flush=True)
    print("TREND_OF_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
