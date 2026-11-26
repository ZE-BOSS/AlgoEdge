"""
scripts/of_zones.py - order-flow zone reversal study (Chris's zones + Fabio Valentini-style
location/aggression), built to break every confluence down piece by piece.

Idea
  Zones are prices where size changed hands before a big move (the base of an impulse, or a
  high-volume node of the recent profile). When price comes back to a zone - ideally after a
  big move into it - look for a rejection candle (pin, engulfing, doji) or a double top/bottom and for order flow that says the move
  into the zone is being absorbed or has turned. Target the next zone on the other side.

How it works (all causal: a bar only sees what was known when it closed)
  1. Bars: futures 1-minute OHLC + 1-minute flow (buy/sell aggressor volume, big trades) from
     of_fetch.py, resampled to the setup timeframe (M5 by default).
  2. Zones
     - origin: the base candle before an impulse of >= IMPULSE x ATR within 3 bars; demand
       below an up-impulse, supply above a down-impulse. Strength = volume and big trades in the
       base + first impulse bar vs their 50-bar average ("orders sitting there").
     - hvn: peaks of the previous session's volume profile (minute VWAP x volume), +/- 0.25 ATR;
       support when price comes from above, resistance from below.
     A zone lives MAX_AGE_DAYS and dies when a bar closes through its far side.
  3. Candidates: every bar that trades into a live zone from the right side. For each we record
     every confluence as a feature and simulate the trade on 1-minute bars for several targets.
  4. Configurations are filters over the candidates (one position at a time per market).
     Each confluence is scored on its own, added to a base, and removed from the full set,
     choosing on January-June and judging on July-November.

    python scripts/of_zones.py --data /opt/algoedge/research_data --markets gold,nasdaq \
        --tf M5 --out /opt/algoedge/research_data/zones
"""
from __future__ import annotations

import argparse
import warnings
import itertools
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
SPLIT = pd.Timestamp("2026-07-01")
COST = {"gold": 0.25, "silver": 0.012, "nasdaq": 0.75, "sp500": 0.5, "us30": 3.0, "oil": 0.03,
        "eurusd": 0.0001, "gbpusd": 0.0001, "usdjpy": 0.0000010}
RULE = {"M1": "1min", "M5": "5min", "M15": "15min", "M30": "30min"}
TARGETS = ["r1", "r1_5", "r2", "r3", "zone"]
R_OF = {"r1": 1.0, "r1_5": 1.5, "r2": 2.0, "r3": 3.0}


# ── data ──────────────────────────────────────────────────────────────────────
def read(p: Path) -> pd.DataFrame:
    return pd.read_parquet(p.with_suffix(".parquet")) if p.with_suffix(".parquet").exists() \
        else pd.read_csv(p.with_suffix(".csv.gz"))


def load(data: Path, market: str, start: str, end: str) -> pd.DataFrame:
    """1-minute frame: OHLC + flow, indexed by UTC minute."""
    m1 = read(data / f"{market}_m1")
    m1 = m1.sort_values("time").drop_duplicates("time")
    m1.index = pd.to_datetime(m1["time"], unit="s")
    of = read(data / f"{market}_of").sort_values("time").drop_duplicates("time")
    of.index = pd.to_datetime(of["time"], unit="s")
    if "delta" not in of.columns:
        of["delta"] = of["buy"] - of["sell"]
    if "vwap" not in of.columns:
        of["vwap"] = of["pv"] / of["vol"].replace(0, np.nan)
    df = m1[["open", "high", "low", "close"]].join(of[["vol", "delta", "big_buy", "big_sell", "vwap"]], how="left")
    df[["vol", "delta", "big_buy", "big_sell"]] = df[["vol", "delta", "big_buy", "big_sell"]].fillna(0)
    df["vwap"] = df["vwap"].fillna(df["close"])
    return df[(df.index >= pd.Timestamp(start) - pd.Timedelta(days=10)) & (df.index < pd.Timestamp(end))]


def bars(m1: pd.DataFrame, tf: str) -> pd.DataFrame:
    b = m1.resample(RULE[tf], label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "vol": "sum", "delta": "sum",
         "big_buy": "sum", "big_sell": "sum"}).dropna(subset=["open"])
    tr = np.maximum(b["high"] - b["low"], np.maximum((b["high"] - b["close"].shift()).abs(),
                                                     (b["low"] - b["close"].shift()).abs()))
    b["atr"] = tr.rolling(14).mean()
    b["vol_avg"] = b["vol"].rolling(50).mean()
    b["big_avg"] = (b["big_buy"] + b["big_sell"]).rolling(50).mean()
    return b


def session_hvns(m1: pd.DataFrame) -> dict[pd.Timestamp, list[float]]:
    """Previous UTC day's high-volume nodes (local peaks of the minute-VWAP volume profile)."""
    out: dict[pd.Timestamp, list[float]] = {}
    for day, g in m1.groupby(m1.index.normalize()):
        g = g[g["vol"] > 0]
        if len(g) < 60:
            continue
        step = max(float(g["vwap"].median()) * 0.0004, 1e-9)
        prof = g["vol"].groupby((g["vwap"] / step).round() * step).sum().sort_index()
        sm = prof.rolling(5, center=True, min_periods=1).mean()
        v = sm.values
        peaks = [prof.index[i] for i in range(1, len(v) - 1) if v[i] >= v[i - 1] and v[i] >= v[i + 1]
                 and v[i] >= 0.5 * v.max()]
        out[day + pd.Timedelta(days=1)] = [float(p) for p in peaks][:6]
    return out


# ── candidates ────────────────────────────────────────────────────────────────
def build(market: str, m1: pd.DataFrame, tf: str, impulse: float, max_age_days: float, start: str) -> pd.DataFrame:
    b = bars(m1, tf)
    hvn = session_hvns(m1)
    o, h, l, c = (b[k].values for k in ("open", "high", "low", "close"))
    vol, dl, bb, bs = (b[k].values for k in ("vol", "delta", "big_buy", "big_sell"))
    atr, vavg, bavg = b["atr"].values, b["vol_avg"].values, b["big_avg"].values
    t = b.index
    m1t = m1.index.values
    mh, ml, mc, mdl = (m1[k].values for k in ("high", "low", "close", "delta"))
    zones: list[dict] = []
    rows = []
    cost = COST.get(market, 0.0)
    max_age = pd.Timedelta(days=max_age_days)
    t0 = pd.Timestamp(start)
    hour = t.hour + t.minute / 60.0
    for i in range(60, len(b) - 1):
        a = atr[i]
        if not np.isfinite(a) or a <= 0:
            continue
        # -- new origin zones from an impulse that finished at bar i --------------
        for k in (1, 2, 3):
            j = i - k                           # base candle
            if j < 1:
                continue
            up = c[i] - o[j + 1]
            if up >= impulse * a and all(c[x] >= o[x] for x in range(j + 1, i + 1)):
                lo, hi = min(l[j], l[j + 1]), max(o[j], c[j])
                if hi - lo <= 1.2 * a:
                    strength = (vol[j] + vol[j + 1]) / (2 * vavg[i]) if vavg[i] else np.nan
                    bigs = (bb[j] + bs[j] + bb[j + 1] + bs[j + 1]) / (2 * bavg[i]) if bavg[i] else np.nan
                    zones.append({"kind": "origin", "side": 1, "lo": lo, "hi": hi, "born": t[i], "touches": 0,
                                  "strength": strength, "bigs": bigs, "imp": up / a})
                break
            dn = o[j + 1] - c[i]
            if dn >= impulse * a and all(c[x] <= o[x] for x in range(j + 1, i + 1)):
                lo, hi = min(o[j], c[j]), max(h[j], h[j + 1])
                if hi - lo <= 1.2 * a:
                    strength = (vol[j] + vol[j + 1]) / (2 * vavg[i]) if vavg[i] else np.nan
                    bigs = (bb[j] + bs[j] + bb[j + 1] + bs[j + 1]) / (2 * bavg[i]) if bavg[i] else np.nan
                    zones.append({"kind": "origin", "side": -1, "lo": lo, "hi": hi, "born": t[i], "touches": 0,
                                  "strength": strength, "bigs": bigs, "imp": dn / a})
                break
        # -- session HVN zones (side decided by approach) -----------------------
        day = t[i].normalize()
        if i == 0 or t[i - 1].normalize() != day:
            for p in hvn.get(day, []):
                zones.append({"kind": "hvn", "side": 0, "lo": p - 0.25 * a, "hi": p + 0.25 * a, "born": t[i],
                              "touches": 0, "strength": np.nan, "bigs": np.nan, "imp": np.nan})
        # -- expire / invalidate --------------------------------------------------
        live = []
        for z in zones:
            if t[i] - z["born"] > max_age:
                continue
            if z["side"] == 1 and c[i] < z["lo"] - 0.1 * a:
                continue
            if z["side"] == -1 and c[i] > z["hi"] + 0.1 * a:
                continue
            live.append(z)
        zones = live[-80:]
        if t[i] < t0:
            continue
        # -- candidates at bar i ----------------------------------------------------
        for z in zones:
            if z["born"] >= t[i - 1]:
                continue                        # a zone is not traded on the bar that made it
            if not (l[i] <= z["hi"] and h[i] >= z["lo"]):
                continue
            prev_above, prev_below = l[i - 1] > z["hi"], h[i - 1] < z["lo"]
            if z["side"] == 1 or (z["side"] == 0 and prev_above):
                side = 1
                if not prev_above:
                    continue
            elif z["side"] == -1 or (z["side"] == 0 and prev_below):
                side = -1
                if not prev_below:
                    continue
            else:
                continue
            z["touches"] += 1
            rng = h[i] - l[i]
            if rng <= 0:
                continue
            body = abs(c[i] - o[i])
            lw, uw = min(o[i], c[i]) - l[i], h[i] - max(o[i], c[i])
            pin = (lw >= 0.5 * rng and c[i] >= l[i] + 0.5 * rng) if side == 1 else (uw >= 0.5 * rng and c[i] <= h[i] - 0.5 * rng)
            doji = body <= 0.15 * rng
            engulf = (c[i] > o[i] and c[i - 1] < o[i - 1] and c[i] >= o[i - 1] and o[i] <= c[i - 1]) if side == 1 else \
                     (c[i] < o[i] and c[i - 1] > o[i - 1] and c[i] <= o[i - 1] and o[i] >= c[i - 1])
            closed_back = (c[i] >= z["lo"]) if side == 1 else (c[i] <= z["hi"])
            # double bottom / double top: an earlier swing at the same price (within 0.3 ATR) in the
            # last 3-36 bars, with a pullback of at least 0.7 ATR between the two tests
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
            n = 12
            ext = (max(h[i - n:i]) - l[i]) if side == 1 else (h[i] - min(l[i - n:i]))
            big_move = ext / a
            # order flow inside the trigger bar
            m_lo = np.searchsorted(m1t, t[i].to_datetime64())
            m_hi = np.searchsorted(m1t, (t[i] + pd.Timedelta(RULE[tf])).to_datetime64())
            md = mdl[m_lo:m_hi]
            half = md[len(md) // 2:] if len(md) >= 2 else md
            absorb = (vol[i] >= 1.5 * vavg[i]) and (dl[i] * side < 0) and ((c[i] - l[i]) / rng >= 0.5 if side == 1 else (h[i] - c[i]) / rng >= 0.5)
            flip = float(half.sum()) * side > 0
            big_with = (bb[i] - bs[i]) * side > 0
            big_against = (bb[i] - bs[i]) * side < 0
            appr_delta = float(dl[i - 6:i].sum()) * side          # delta of the approach, signed to the trade
            rvol = vol[i] / vavg[i] if vavg[i] else np.nan
            # trade: enter next bar's open, stop beyond zone/wick
            entry_t = t[i + 1]
            entry = o[i + 1]
            stop = (min(l[i], z["lo"]) - 0.1 * a) if side == 1 else (max(h[i], z["hi"]) + 0.1 * a)
            risk = (entry - stop) * side
            if risk < 0.3 * a:
                stop = entry - side * 0.3 * a
                risk = 0.3 * a
            # next opposing zone as target
            opp = [zz for zz in zones if zz is not z and ((side == 1 and zz["side"] in (-1, 0) and zz["lo"] > entry)
                                                         or (side == -1 and zz["side"] in (1, 0) and zz["hi"] < entry))]
            ztp = (min(zz["lo"] for zz in opp) if side == 1 else max(zz["hi"] for zz in opp)) if opp else None
            # simulate on minutes from entry, max 1 day
            s0 = np.searchsorted(m1t, entry_t.to_datetime64())
            s1 = np.searchsorted(m1t, (entry_t + pd.Timedelta(days=1)).to_datetime64())
            res = {}
            exit_t = {}
            path_h, path_l, path_c = mh[s0:s1], ml[s0:s1], mc[s0:s1]
            tps = {k: entry + side * R_OF[k] * risk for k in R_OF}
            if ztp is not None and (ztp - entry) * side >= 0.8 * risk:
                tps["zone"] = ztp
            for k in TARGETS:
                tp = tps.get(k)
                if tp is None:
                    res[k] = np.nan
                    exit_t[k] = None
                    continue
                hit_stop = (path_l <= stop) if side == 1 else (path_h >= stop)
                hit_tp = (path_h >= tp) if side == 1 else (path_l <= tp)
                ist = int(np.argmax(hit_stop)) if hit_stop.any() else None
                itp = int(np.argmax(hit_tp)) if hit_tp.any() else None
                if ist is not None and (itp is None or ist <= itp):
                    r, xi = -1.0, ist
                elif itp is not None:
                    r, xi = (tp - entry) * side / risk, itp
                else:
                    xi = len(path_c) - 1
                    r = (path_c[-1] - entry) * side / risk if len(path_c) else 0.0
                res[k] = r - cost / risk
                exit_t[k] = m1t[s0 + xi] if len(path_c) else None
            rows.append({"t": t[i], "entry_t": entry_t, "side": side, "kind": z["kind"], "touch": z["touches"],
                         "zone_age_h": (t[i] - z["born"]).total_seconds() / 3600, "strength": z["strength"],
                         "zone_bigs": z["bigs"], "imp": z["imp"], "pin": pin, "doji": doji, "engulf": engulf, "double": double,
                         "closed_back": closed_back, "big_move": big_move, "absorb": absorb, "flip": flip,
                         "big_with": big_with, "big_against": big_against, "appr_delta": appr_delta, "rvol": rvol,
                         "hour": hour[i], "risk_atr": risk / a, "has_zone_tp": "zone" in tps,
                         **{f"r_{k}": res[k] for k in TARGETS}, **{f"x_{k}": exit_t[k] for k in TARGETS}})
    return pd.DataFrame(rows)


# ── evaluation ────────────────────────────────────────────────────────────────
def take(cand: pd.DataFrame, mask: np.ndarray, target: str) -> pd.DataFrame:
    """One position at a time: walk the filtered candidates in time, skip while a trade is open."""
    sub = cand[mask & cand[f"r_{target}"].notna().values].sort_values("entry_t")
    keep, free = [], pd.Timestamp.min
    for idx, row in zip(sub.index, sub.itertuples(index=False)):
        if row.entry_t < free:
            continue
        keep.append(idx)
        x = getattr(row, f"x_{target}")
        free = pd.Timestamp(x) if x is not None else row.entry_t
    return sub.loc[keep]


def stats(tr: pd.DataFrame, target: str) -> dict:
    if tr.empty:
        return {"trades": 0}
    r = tr[f"r_{target}"].values.astype(float)
    eq = np.cumsum(r)
    dd = float(np.max(np.maximum.accumulate(np.concatenate([[0], eq]))[1:] - eq))
    wins, losses = r[r > 0], r[r <= 0]
    sw = sl = cw = cl = 0
    for v in r:
        cw, cl = (cw + 1, 0) if v > 0 else (0, cl + 1)
        sw, sl = max(sw, cw), max(sl, cl)
    months = pd.Series(r, index=pd.DatetimeIndex(tr["entry_t"])).groupby(pd.DatetimeIndex(tr["entry_t"]).to_period("M")).sum()
    days = max(1, (tr["entry_t"].max() - tr["entry_t"].min()).days)
    return {"trades": int(len(r)), "per_day": round(len(r) / days, 2), "win_rate": round(float((r > 0).mean()), 3),
            "net_r": round(float(r.sum()), 2), "exp_r": round(float(r.mean()), 3),
            "pf": round(float(wins.sum() / -losses.sum()), 2) if losses.sum() < 0 else None,
            "max_dd_r": round(dd, 2), "win_streak": sw, "loss_streak": sl,
            "months_pct_at_1pct_risk": {str(k): round(float(v), 2) for k, v in months.items()},
            "losing_months": int((months < 0).sum())}


CONFLUENCES = {
    "origin_zone": lambda d: d["kind"] == "origin",
    "hvn_zone": lambda d: d["kind"] == "hvn",
    "fresh_zone": lambda d: d["touch"] <= 1,
    "strong_zone": lambda d: d["strength"].fillna(0) >= 1.5,
    "big_trades_in_zone": lambda d: d["zone_bigs"].fillna(0) >= 1.5,
    "big_move_in": lambda d: d["big_move"] >= 3.0,
    "pin": lambda d: d["pin"],
    "doji": lambda d: d["doji"],
    "engulf": lambda d: d["engulf"],
    "rejection_any": lambda d: d["pin"] | d["doji"] | d["engulf"],
    "double_top_bottom": lambda d: d["double"],
    "rejection_and_double": lambda d: (d["pin"] | d["engulf"]) & d["double"],
    "closed_back": lambda d: d["closed_back"],
    "absorption": lambda d: d["absorb"],
    "delta_flip": lambda d: d["flip"],
    "big_trades_with": lambda d: d["big_with"],
    "no_big_against": lambda d: ~d["big_against"],
    "approach_delta_exhausted": lambda d: d["appr_delta"] > 0,
    "london_ny": lambda d: (d["hour"] >= 7) & (d["hour"] < 20),
    "ny_only": lambda d: (d["hour"] >= 13.5) & (d["hour"] < 20),
}


def evaluate(cand: pd.DataFrame, confluences: dict | None = None, targets: list | None = None) -> dict:
    out: dict = {"candidates": int(len(cand))}
    if cand.empty:
        return out
    train = (cand["entry_t"] < SPLIT).values
    test = ~train
    confluences = confluences or CONFLUENCES
    TARGETS = targets or globals()["TARGETS"]  # noqa: N806
    feats = {}
    for k, f in confluences.items():
        try:
            feats[k] = pd.Series(f(cand)).fillna(False).values.astype(bool)
        except KeyError:
            continue
    allm = np.ones(len(cand), bool)

    def run(mask, target):
        tr, te = take(cand, mask & train, target), take(cand, mask & test, target)
        return stats(tr, target), stats(te, target)

    # 1) every candidate, every target - the zone alone
    out["zone_only"] = {k: dict(zip(("train", "test"), run(allm, k))) for k in TARGETS}
    # 2) each confluence alone on top of "touch a zone"
    out["alone"] = {}
    for name, m in feats.items():
        out["alone"][name] = {k: dict(zip(("train", "test"), run(m, k))) for k in TARGETS if k != "r1"}
    # 3) greedy forward selection on TRAIN net R (target chosen too), then judged on test
    best = {"mask": allm, "names": [], "target": "r2", "net": -1e9}
    for k in TARGETS:
        s, _ = run(allm, k)
        if s.get("trades", 0) >= 30 and s["net_r"] > best["net"]:
            best = {"mask": allm, "names": [], "target": k, "net": s["net_r"]}
    path = []
    for _ in range(5):
        cand_best = None
        for name, m in feats.items():
            if name in best["names"]:
                continue
            mm = best["mask"] & m
            for k in TARGETS:
                s, _ = run(mm, k)
                if s.get("trades", 0) < 30:
                    continue
                score = s["net_r"]
                if score > best["net"] + 0.5 and (cand_best is None or score > cand_best[0]):
                    cand_best = (score, name, k, mm)
        if cand_best is None:
            break
        best = {"mask": cand_best[3], "names": best["names"] + [cand_best[1]], "target": cand_best[2],
                "net": cand_best[0]}
        tr, te = run(best["mask"], best["target"])
        path.append({"add": cand_best[1], "target": best["target"], "train": tr, "test": te})
    out["forward"] = path
    out["best"] = {"confluences": best["names"], "target": best["target"]}
    # 4) remove-one from the selected set, and add-one more, judged on both halves
    out["remove_one"] = {}
    for name in best["names"]:
        m = allm.copy()
        for nn in best["names"]:
            if nn != name:
                m &= feats[nn]
        out["remove_one"][name] = dict(zip(("train", "test"), run(m, best["target"])))
    out["add_one"] = {}
    for name, f in feats.items():
        if name in best["names"]:
            continue
        out["add_one"][name] = dict(zip(("train", "test"), run(best["mask"] & f, best["target"])))
    tr_all, te_all = take(cand, best["mask"], best["target"]), None
    out["best_full"] = stats(tr_all, best["target"])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="/opt/algoedge/research_data")
    ap.add_argument("--markets", default="gold,nasdaq,sp500,silver")
    ap.add_argument("--tf", default="M5")
    ap.add_argument("--impulse", type=float, default=2.0)
    ap.add_argument("--max-age-days", type=float, default=5)
    ap.add_argument("--start", default="2026-01-02")
    ap.add_argument("--end", default="2026-11-26")
    ap.add_argument("--out", default="/opt/algoedge/research_data/zones")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for mk in [m for m in a.markets.split(",") if m]:
        t0 = time.time()
        try:
            m1 = load(Path(a.data), mk, a.start, a.end)
            cand = build(mk, m1, a.tf, a.impulse, a.max_age_days, a.start)
            cand.to_parquet(out / f"cand_{mk}_{a.tf}.parquet")
            ev = evaluate(cand)
            (out / f"eval_{mk}_{a.tf}.json").write_text(json.dumps(ev, default=str))
            b = ev.get("best", {})
            bf = ev.get("best_full", {})
            print(f"{mk:7s} {a.tf} cand {len(cand):6d} | best {b.get('confluences')} -> {b.get('target')} | "
                  f"{bf.get('trades')}tr win {bf.get('win_rate')} net {bf.get('net_r')}R pf {bf.get('pf')} "
                  f"dd {bf.get('max_dd_r')}  {time.time() - t0:.0f}s", flush=True)
        except Exception as e:  # noqa: BLE001
            import traceback
            print(f"{mk:7s} ERROR {type(e).__name__}: {e}", flush=True)
            traceback.print_exc(limit=4)
    print("ZONES_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
