"""
scripts/of_optimise.py - score bt_futures.py trades and find order-flow filters and take-profit
levels that improve them, choosing on January-June and judging on July-November only.

Features at each entry (from minutes strictly before it), signed by the trade's side so
"+" always means "flow agrees with the trade":
  d15, d60     delta / volume over the last 15 and 60 minutes
  cvd6h        delta / volume over the last 6 hours
  big30        (large buys - large sells) over the last 30 minutes, per 100 contracts
  rvol30       volume of the last 30 minutes / its 20-day median at that hour (unsigned)
  va           price vs the previous UTC day's value area: +1 beyond it in the trade's
               direction, 0 inside, -1 beyond it against the trade
Take profit: a trade that reached k R of favourable excursion before it closed would have
hit a k R target (MFE is measured up to the exit), so any target below the run's is exact.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

SPLIT = pd.Timestamp("2026-07-01")
FEATS = ["d15", "d60", "cvd6h", "big30", "rvol30", "va"]
TPS = [0.75, 1.0, 1.5, 2.0, 2.5, 3.0]


def read(p: Path) -> pd.DataFrame:
    return pd.read_parquet(p.with_suffix(".parquet")) if p.with_suffix(".parquet").exists() \
        else pd.read_csv(p.with_suffix(".csv.gz"))


def ts(x) -> pd.Timestamp:
    if isinstance(x, (int, float)) and not pd.isna(x):
        return pd.Timestamp(int(x), unit="s")
    t = pd.Timestamp(x)
    return t.tz_convert(None) if t.tzinfo else t


def flow_frame(data: Path, market: str) -> pd.DataFrame:
    of = read(data / f"{market}_of")
    of.index = pd.to_datetime(of["time"], unit="s")
    of = of.sort_index()
    full = of.reindex(pd.date_range(of.index[0], of.index[-1], freq="1min"), fill_value=0)
    f = pd.DataFrame(index=full.index)
    for w, name in ((15, "d15"), (60, "d60"), (360, "cvd6h")):
        d, v = full["delta"].rolling(w).sum(), full["vol"].rolling(w).sum()
        f[name] = (d / v.replace(0, np.nan)).shift(1)
    f["big30"] = ((full["big_buy"] - full["big_sell"]).rolling(30).sum() / 100.0).shift(1)
    v30 = full["vol"].rolling(30).sum()
    hour_med = v30.groupby(v30.index.hour).transform(lambda s: s.rolling(20 * 60, min_periods=60).median())
    f["rvol30"] = (v30 / hour_med.replace(0, np.nan)).shift(1)
    # previous UTC day's value area from minute VWAP x volume
    vals = {}
    for day, g in of.groupby(of.index.normalize()):
        g = g[g["vol"] > 0]
        if g.empty:
            continue
        step = max(float(g["vwap"].median()) * 0.0005, 1e-9)
        prof = (g["vol"].groupby((g["vwap"] / step).round() * step).sum()).sort_index()
        poc = prof.idxmax()
        lo = hi = list(prof.index).index(poc)
        tot, got = prof.sum(), prof.iloc[lo]
        while got < 0.7 * tot and (lo > 0 or hi < len(prof) - 1):
            up = prof.iloc[hi + 1] if hi < len(prof) - 1 else -1
            dn = prof.iloc[lo - 1] if lo > 0 else -1
            if up >= dn:
                hi += 1; got += up
            else:
                lo -= 1; got += dn
        vals[day + pd.Timedelta(days=1)] = (prof.index[lo], prof.index[hi])
    f["val"] = f.index.normalize().map(lambda d: vals.get(d, (np.nan, np.nan))[0])
    f["vah"] = f.index.normalize().map(lambda d: vals.get(d, (np.nan, np.nan))[1])
    return f


def featurise(trades: list[dict], f: pd.DataFrame) -> pd.DataFrame:
    df = pd.DataFrame(trades)
    if df.empty:
        return df
    df["t"] = df["entry_time"].map(ts).dt.floor("1min")
    x = f.reindex(df["t"].values)
    for c in ["d15", "d60", "cvd6h", "big30"]:
        df[c] = x[c].values * df["side"].values
    df["rvol30"] = x["rvol30"].values
    above, below = df["entry"].values > x["vah"].values, df["entry"].values < x["val"].values
    df["va"] = np.where(above, 1, np.where(below, -1, 0)) * df["side"].values
    df.loc[x["val"].isna().values, "va"] = np.nan
    return df


def with_tp(df: pd.DataFrame, k: float | None) -> np.ndarray:
    r = df["r"].values.astype(float)
    if k is None:
        return r
    mfe = pd.to_numeric(df["mfe_r"], errors="coerce").fillna(-9).values
    cost = (df["r_gross"] - df["r"]).values
    return np.where(mfe >= k, k - cost, r)


def stats(r: np.ndarray, t: pd.Series) -> dict:
    if len(r) == 0:
        return {"trades": 0}
    eq = np.cumsum(r)
    dd = float(np.max(np.maximum.accumulate(np.concatenate([[0], eq]))[1:] - eq)) if len(eq) else 0.0
    wins, losses = r[r > 0], r[r <= 0]
    streak_w = streak_l = cw = cl = 0
    for v in r:
        cw, cl = (cw + 1, 0) if v > 0 else (0, cl + 1)
        streak_w, streak_l = max(streak_w, cw), max(streak_l, cl)
    months = pd.Series(r, index=pd.DatetimeIndex(t.values)).groupby(pd.DatetimeIndex(t.values).to_period("M")).sum()
    return {"trades": int(len(r)), "win_rate": round(float((r > 0).mean()), 3), "net_r": round(float(r.sum()), 2),
            "exp_r": round(float(r.mean()), 3),
            "pf": round(float(wins.sum() / -losses.sum()), 2) if losses.sum() < 0 else None,
            "max_dd_r": round(dd, 2), "max_win_streak": streak_w, "max_loss_streak": streak_l,
            "months_pct_at_1pct_risk": {str(k): round(float(v), 2) for k, v in months.items()},
            "losing_months": int((months < 0).sum()), "months": int(len(months))}


def choose(train: pd.DataFrame) -> dict:
    """Best take profit, then best single order-flow filter, by train net R (kept trades >= 40%, >= 20)."""
    best_tp, best_net = None, with_tp(train, None).sum()
    for k in TPS:
        n = with_tp(train, k).sum()
        if n > best_net + 1e-9:
            best_tp, best_net = k, n
    base = with_tp(train, best_tp)
    rule, rule_net = None, base.sum()
    for c in FEATS:
        col = train[c]
        if col.notna().sum() < 20:
            continue
        for q in (0.2, 0.4, 0.5, 0.6, 0.8):
            th = float(col.quantile(q))
            for op in (">", "<"):
                keep = (col > th) if op == ">" else (col < th)
                keep = keep.fillna(False).values
                if keep.sum() < max(20, 0.4 * len(train)):
                    continue
                net = base[keep].sum()
                if net > rule_net + 1e-9 and base[keep].mean() > base.mean():
                    rule, rule_net = (c, op, round(th, 4)), net
    return {"tp": best_tp, "filter": rule}


def apply(df: pd.DataFrame, ch: dict) -> tuple[np.ndarray, pd.Series]:
    r = with_tp(df, ch["tp"])
    if ch["filter"]:
        c, op, th = ch["filter"]
        keep = ((df[c] > th) if op == ">" else (df[c] < th)).fillna(False).values
        return r[keep], df["t"][keep]
    return r, df["t"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="/opt/algoedge/research_data")
    ap.add_argument("--out", default="/opt/algoedge/research_data/report.json")
    a = ap.parse_args()
    data = Path(a.data)
    flows: dict[str, pd.DataFrame] = {}
    rows = []
    for p in sorted((data / "bt").glob("*.json")):
        res = json.loads(p.read_text())
        m, s = res["market"], res["strategy"]
        if m not in flows:
            flows[m] = flow_frame(data, m)
        df = featurise(res["trades"], flows[m])
        if df.empty:
            rows.append({"market": m, "strategy": s, "baseline": {"trades": 0}})
            continue
        df = df.sort_values("t").reset_index(drop=True)
        tr, te = df[df["t"] < SPLIT], df[df["t"] >= SPLIT]
        ch = choose(tr) if len(tr) >= 20 else {"tp": None, "filter": None}
        r_te, t_te = apply(te, ch)
        r_all, t_all = apply(df, ch)
        rows.append({"market": m, "strategy": s, "choice": ch,
                     "baseline": stats(df["r"].values, df["t"]),
                     "baseline_test": stats(te["r"].values, te["t"]),
                     "optimised_test": stats(r_te, t_te),
                     "optimised_full": stats(r_all, t_all)})
        b, o = rows[-1]["baseline_test"], rows[-1]["optimised_test"]
        print(f"{m:7s} {s:24s} base {rows[-1]['baseline'].get('net_r', 0):7.1f}R | test base {b.get('net_r', 0):6.1f}R "
              f"-> opt {o.get('net_r', 0):6.1f}R ({o.get('trades', 0)} tr)  {ch}", flush=True)
    Path(a.out).write_text(json.dumps(rows, default=str))
    print("OPT_DONE", len(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
