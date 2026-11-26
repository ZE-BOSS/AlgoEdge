"""
scripts/pa_study.py - advanced price action study: key levels + structure + candle patterns,
optionally with order flow, broken down confluence by confluence.

Levels (causal)
  - H1 swing highs/lows (fractal, 2 bars each side, usable 2 H1 bars after the swing),
    merged when within 0.25 H1-ATR; "strong" once tested twice.
  - previous UTC day's high and low.
Setups at a level, on the setup timeframe (M15 by default)
  - reversal: price trades into the level from one side -> trade away from it
  - sweep: the wick goes through the level and the bar closes back (stop hunt)
  - retest: the level was broken by a close in the last 24 bars, price comes back to it from
    the broken side -> trade in the break's direction
Confluences recorded per candidate: pin / engulfing / doji / double top-bottom, sweep, big move
into the level, strong level, previous-day level, with/against the H1 structure, session; and
when 1-minute flow is present (futures): absorption, delta flip, big trades with the trade.

Inputs
  --source mt5      research_data/mt5/<asset>_m5.parquet (scripts/mt5_export.py), Deriv costs
  --source futures  research_data/<market>_m1.parquet + _of.parquet (of_fetch.py), futures costs

    python scripts/pa_study.py --source mt5 --assets xauusd,nas100,eurusd --tf M15
"""
from __future__ import annotations

import argparse
import json
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

import of_zones as oz

warnings.filterwarnings("ignore")
DERIV_COST = {"xauusd": 0.3, "xagusd": 0.03, "nas100": 1.5, "us30": 3.0, "sp500": 0.6, "ger40": 2.0,
              "uk100": 1.5, "jp225": 10.0, "usoil": 0.04, "eurusd": 0.00012, "gbpusd": 0.00015,
              "usdjpy": 0.015, "gbpjpy": 0.03, "audusd": 0.00012, "usdcad": 0.00018, "eurjpy": 0.02,
              "usdchf": 0.00015}
TARGETS = ["r1", "r1_5", "r2", "r3", "level"]
R_OF = {"r1": 1.0, "r1_5": 1.5, "r2": 2.0, "r3": 3.0}


def load_mt5(data: Path, asset: str) -> pd.DataFrame:
    df = pd.read_parquet(data / "mt5" / f"{asset}_m5.parquet")
    df.index = pd.to_datetime(df["time"], unit="s")
    return df[["open", "high", "low", "close"]].astype(float).sort_index()


def resample(x: pd.DataFrame, rule: str) -> pd.DataFrame:
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    for k in ("vol", "delta", "big_buy", "big_sell"):
        if k in x.columns:
            agg[k] = "sum"
    b = x.resample(rule, label="left", closed="left").agg(agg).dropna(subset=["open"])
    tr = np.maximum(b["high"] - b["low"], np.maximum((b["high"] - b["close"].shift()).abs(),
                                                     (b["low"] - b["close"].shift()).abs()))
    b["atr"] = tr.rolling(14).mean()
    if "vol" in b.columns:
        b["vol_avg"] = b["vol"].rolling(50).mean()
    return b


def h1_context(base: pd.DataFrame):
    """H1 swing levels (with the time they become known) and H1 structure per hour."""
    h = resample(base, "1h")
    hi, lo, t, atr = h["high"].values, h["low"].values, h.index, h["atr"].values
    levels = []                                   # (known_at, price, kind)
    for i in range(2, len(h) - 2):
        if hi[i] >= max(hi[i - 2:i]) and hi[i] >= max(hi[i + 1:i + 3]):
            levels.append((t[i + 2] + pd.Timedelta(hours=1), float(hi[i]), "high"))
        if lo[i] <= min(lo[i - 2:i]) and lo[i] <= min(lo[i + 1:i + 3]):
            levels.append((t[i + 2] + pd.Timedelta(hours=1), float(lo[i]), "low"))
    # structure: last two known swing highs and lows
    trend = pd.Series(0, index=t)
    sh, sl = [], []
    j = 0
    lv = sorted(levels)
    for k, tt in enumerate(t):
        while j < len(lv) and lv[j][0] <= tt:
            (sh if lv[j][2] == "high" else sl).append(lv[j][1])
            j += 1
        if len(sh) >= 2 and len(sl) >= 2:
            if sh[-1] > sh[-2] and sl[-1] > sl[-2]:
                trend.iloc[k] = 1
            elif sh[-1] < sh[-2] and sl[-1] < sl[-2]:
                trend.iloc[k] = -1
    return sorted(levels), trend, pd.Series(atr, index=t)


def build(name: str, base: pd.DataFrame, tf: str, cost: float, start: str) -> pd.DataFrame:
    b = resample(base, oz.RULE[tf])
    levels, trend, _ = h1_context(base)
    days = base.resample("1D").agg({"high": "max", "low": "min"}).dropna()
    pd_levels = {d + pd.Timedelta(days=1): (float(r.high), float(r.low)) for d, r in days.iterrows()}
    o, h, l, c, atr = (b[k].values for k in ("open", "high", "low", "close", "atr"))
    has_of = "delta" in b.columns
    if has_of:
        vol, dl, bb, bs, vavg = (b[k].values for k in ("vol", "delta", "big_buy", "big_sell", "vol_avg"))
    t = b.index
    bt = base.index.values
    bh, bl, bc = base["high"].values, base["low"].values, base["close"].values
    bdl = base["delta"].values if has_of else None
    hour = t.hour + t.minute / 60.0
    t0 = pd.Timestamp(start)
    live: list[dict] = []
    li = 0
    rows = []
    step = pd.Timedelta(oz.RULE[tf])
    for i in range(40, len(b) - 1):
        a = atr[i]
        if not np.isfinite(a) or a <= 0:
            continue
        while li < len(levels) and levels[li][0] <= t[i]:
            kt, px, kind = levels[li]
            near = next((z for z in live if abs(z["px"] - px) <= 0.5 * a), None)  # ~0.25 H1-ATR
            if near:
                near["touches"] += 1
                near["px"] = (near["px"] + px) / 2
            else:
                live.append({"px": px, "kind": kind, "touches": 1, "born": kt, "pd": False, "broken_at": None,
                             "broken_dir": 0})
            li += 1
        day = t[i].normalize()
        if t[i - 1].normalize() != day and day in pd_levels:
            live = [z for z in live if not z["pd"]]
            ph, pl = pd_levels[day]
            live += [{"px": ph, "kind": "high", "touches": 1, "born": t[i], "pd": True, "broken_at": None, "broken_dir": 0},
                     {"px": pl, "kind": "low", "touches": 1, "born": t[i], "pd": True, "broken_at": None, "broken_dir": 0}]
        live = [z for z in live if t[i] - z["born"] <= pd.Timedelta(days=10)][-60:]
        # mark breaks by close
        for z in live:
            if z["broken_at"] is None:
                if c[i - 1] <= z["px"] < c[i] and c[i] - z["px"] >= 0.2 * a:
                    z["broken_at"], z["broken_dir"] = t[i], 1
                elif c[i - 1] >= z["px"] > c[i] and z["px"] - c[i] >= 0.2 * a:
                    z["broken_at"], z["broken_dir"] = t[i], -1
        if t[i] < t0:
            continue
        rng = h[i] - l[i]
        if rng <= 0:
            continue
        tol = 0.15 * a
        for z in live:
            px = z["px"]
            if not (l[i] - tol <= px <= h[i] + tol):
                continue
            setup = None
            if z["broken_at"] is not None and z["broken_at"] < t[i] and (t[i] - z["broken_at"]) <= 24 * step:
                d = z["broken_dir"]
                came_back = (l[i - 1] > px) if d == 1 else (h[i - 1] < px)
                if came_back:
                    setup, side = "retest", d
            if setup is None:
                if l[i - 1] > px + tol:
                    setup, side = "reversal", 1
                elif h[i - 1] < px - tol:
                    setup, side = "reversal", -1
                else:
                    continue
            body = abs(c[i] - o[i])
            lw, uw = min(o[i], c[i]) - l[i], h[i] - max(o[i], c[i])
            pin = (lw >= 0.5 * rng and c[i] >= l[i] + 0.5 * rng) if side == 1 else (uw >= 0.5 * rng and c[i] <= h[i] - 0.5 * rng)
            doji = body <= 0.15 * rng
            engulf = (c[i] > o[i] and c[i - 1] < o[i - 1] and c[i] >= o[i - 1] and o[i] <= c[i - 1]) if side == 1 else \
                     (c[i] < o[i] and c[i - 1] > o[i - 1] and c[i] <= o[i - 1] and o[i] >= c[i - 1])
            sweep = (l[i] < px - 0.05 * a and c[i] > px) if side == 1 else (h[i] > px + 0.05 * a and c[i] < px)
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
            ext = (max(h[i - 12:i]) - l[i]) if side == 1 else (h[i] - min(l[i - 12:i]))
            hk = t[i].floor("1h")
            tr_h1 = int(trend.get(hk, 0)) if hk in trend.index else 0
            feat = {"t": t[i], "side": side, "setup": setup, "pin": pin, "doji": doji, "engulf": engulf,
                    "double": double, "sweep": sweep, "big_move": ext / a, "strong": z["touches"] >= 2,
                    "pd_level": z["pd"], "trend": tr_h1 * side, "hour": hour[i]}
            m_lo = np.searchsorted(bt, t[i].to_datetime64())
            m_hi = np.searchsorted(bt, (t[i] + step).to_datetime64())
            if has_of:
                md = bdl[m_lo:m_hi]
                half = md[len(md) // 2:] if len(md) >= 2 else md
                feat["absorb"] = bool((vol[i] >= 1.5 * vavg[i]) and (dl[i] * side < 0) and
                                      (((c[i] - l[i]) / rng >= 0.5) if side == 1 else ((h[i] - c[i]) / rng >= 0.5)))
                feat["flip"] = float(half.sum()) * side > 0
                feat["big_with"] = (bb[i] - bs[i]) * side > 0
                feat["appr_delta"] = float(dl[i - 6:i].sum()) * side
            entry_t, entry = t[i + 1], o[i + 1]
            stop = (min(l[i], px) - 0.1 * a) if side == 1 else (max(h[i], px) + 0.1 * a)
            risk = (entry - stop) * side
            if risk < 0.3 * a:
                stop, risk = entry - side * 0.3 * a, 0.3 * a
            nxt = [zz["px"] for zz in live if zz is not z and (zz["px"] - entry) * side >= 0.8 * risk]
            tps = {k: entry + side * R_OF[k] * risk for k in R_OF}
            if nxt:
                tps["level"] = min(nxt) if side == 1 else max(nxt)
            s0 = np.searchsorted(bt, entry_t.to_datetime64())
            s1 = np.searchsorted(bt, (entry_t + pd.Timedelta(days=1)).to_datetime64())
            ph, pl, pc = bh[s0:s1], bl[s0:s1], bc[s0:s1]
            for k in TARGETS:
                tp = tps.get(k)
                if tp is None or not len(pc):
                    feat[f"r_{k}"], feat[f"x_{k}"] = np.nan, None
                    continue
                hs = (pl <= stop) if side == 1 else (ph >= stop)
                ht = (ph >= tp) if side == 1 else (pl <= tp)
                ist = int(np.argmax(hs)) if hs.any() else None
                itp = int(np.argmax(ht)) if ht.any() else None
                if ist is not None and (itp is None or ist <= itp):
                    r, xi = -1.0, ist
                elif itp is not None:
                    r, xi = (tp - entry) * side / risk, itp
                else:
                    r, xi = (pc[-1] - entry) * side / risk, len(pc) - 1
                feat[f"r_{k}"], feat[f"x_{k}"] = r - cost / risk, bt[s0 + xi]
            feat["entry_t"] = entry_t
            rows.append(feat)
    return pd.DataFrame(rows)


PA = {
    "reversal": lambda d: d["setup"] == "reversal",
    "retest": lambda d: d["setup"] == "retest",
    "pin": lambda d: d["pin"], "engulf": lambda d: d["engulf"], "doji": lambda d: d["doji"],
    "double_top_bottom": lambda d: d["double"],
    "rejection_any": lambda d: d["pin"] | d["engulf"] | d["doji"],
    "rejection_and_double": lambda d: (d["pin"] | d["engulf"]) & d["double"],
    "sweep": lambda d: d["sweep"],
    "big_move_in": lambda d: d["big_move"] >= 3.0,
    "strong_level": lambda d: d["strong"], "prev_day_level": lambda d: d["pd_level"],
    "with_h1_trend": lambda d: d["trend"] > 0, "against_h1_trend": lambda d: d["trend"] < 0,
    "london_ny": lambda d: (d["hour"] >= 7) & (d["hour"] < 20),
    "ny_only": lambda d: (d["hour"] >= 13.5) & (d["hour"] < 20),
}
OF = {
    "absorption": lambda d: d["absorb"], "delta_flip": lambda d: d["flip"],
    "big_trades_with": lambda d: d["big_with"], "approach_delta_exhausted": lambda d: d["appr_delta"] > 0,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="/opt/algoedge/research_data")
    ap.add_argument("--source", choices=["mt5", "futures"], default="mt5")
    ap.add_argument("--assets", default="xauusd,nas100,us30,sp500,eurusd,gbpusd,usdjpy,gbpjpy,usoil,ger40,uk100")
    ap.add_argument("--tf", default="M15")
    ap.add_argument("--start", default="2026-01-05")
    ap.add_argument("--end", default="2026-11-26")
    ap.add_argument("--out", default="/opt/algoedge/research_data/pa")
    a = ap.parse_args()
    data, out = Path(a.data), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for asset in [x for x in a.assets.split(",") if x]:
        t0 = time.time()
        try:
            if a.source == "mt5":
                base, cost = load_mt5(data, asset), DERIV_COST.get(asset, 0.0)
            else:
                base, cost = oz.load(data, asset, a.start, a.end), oz.COST.get(asset, 0.0)
            base = base[base.index < pd.Timestamp(a.end)]
            cand = build(asset, base, a.tf, cost, a.start)
            cand.to_parquet(out / f"cand_{a.source}_{asset}_{a.tf}.parquet")
            conf = dict(PA, **(OF if a.source == "futures" else {}))
            ev = oz.evaluate(cand, conf, TARGETS)
            (out / f"eval_{a.source}_{asset}_{a.tf}.json").write_text(json.dumps(ev, default=str))
            bst, bf = ev.get("best", {}), ev.get("best_full", {})
            print(f"{a.source:7s} {asset:7s} {a.tf} cand {len(cand):6d} | best {bst.get('confluences')} -> "
                  f"{bst.get('target')} | {bf.get('trades')}tr win {bf.get('win_rate')} net {bf.get('net_r')}R "
                  f"pf {bf.get('pf')} dd {bf.get('max_dd_r')}  {time.time() - t0:.0f}s", flush=True)
        except Exception as e:  # noqa: BLE001
            import traceback
            print(f"{a.source:7s} {asset:7s} ERROR {type(e).__name__}: {e}", flush=True)
            traceback.print_exc(limit=4)
    print("PA_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
