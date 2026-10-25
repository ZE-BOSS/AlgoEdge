"""
Trade-by-trade comparison of a roboquant template's published backtest
(research/roboquant/source/templates_dump.json, NQ/MNQ futures, tick data)
against this app's RQ_* engine on MT5 NDX100 bars, on the overlapping days.

    py -3.12 research/roboquant/compare_trades.py <template_index> <strategy_id> <tf> [symbol]

Answers: do we trade the same days, in the same direction, and do the
matched trades end the same way (win/loss)? Bars are cached in cache/.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent
ET = "America/New_York"


def bars(symbol: str, tf: str, start="2020-01-01", end="2026-10-20") -> pd.DataFrame:
    p = HERE / "cache" / f"{symbol.replace(' ', '_')}_{tf}.pkl"
    if p.exists():
        return pd.read_pickle(p)
    import MetaTrader5 as mt5
    from backend.kronos_service.research import load_bars
    term = "C:/Program Files/MetaTrader 5 Terminal/terminal64.exe" if " " in symbol else "C:/Program Files/MetaTrader 5/terminal64.exe"
    assert mt5.initialize(path=term), mt5.last_error()
    f = load_bars(symbol, tf, start, end)
    f = f[["time", "open", "high", "low", "close"] + [c for c in ("tick_volume", "volume", "real_volume") if c in f.columns]]
    f.to_pickle(p)
    return f


def their_trades(k: int) -> pd.DataFrame:
    d = json.load(open(HERE / "source" / "templates_dump.json", encoding="utf-8"))[k]
    t = pd.DataFrame(d["trades"], columns=["entry_ms", "entry", "exit_ms", "exit", "dir", "qty", "pnl", "comm", "sl", "tp", "sym"])
    t["et"] = pd.to_datetime(t["entry_ms"], unit="ms", utc=True).dt.tz_convert(ET)
    t["day"] = t["et"].dt.strftime("%Y-%m-%d")
    t["long"] = t["dir"].str.lower().str.startswith("l")
    t["risk_pts"] = (t["entry"] - t["sl"]).abs()
    t["r"] = np.where(t["long"], t["exit"] - t["entry"], t["entry"] - t["exit"]) / t["risk_pts"]
    t["win"] = t["pnl"] > 0
    return t, d["title"]


def our_signals(sid: str, symbol: str, tf: str, f: pd.DataFrame) -> pd.DataFrame:
    p = HERE / "cache" / f"sig_{sid}_{symbol.replace(' ', '_')}_{tf}.pkl"
    if p.exists():
        return pd.read_pickle(p)
    from backend.core.config_schema import UserConfigV2
    from backend.rl import base_signals as bs
    idx = f.set_index(pd.to_datetime(f["time"], unit="s"))
    if "tick_volume" not in idx.columns:
        idx["tick_volume"] = idx["volume"]
    sigs = bs.generate(symbol, tf, bs.index_frames({tf: idx}), sid, UserConfigV2(), start_index=60)
    s = pd.DataFrame([{"bar_index": int(x["bar_index"]), "dir": x["direction"], "entry": float(x["entry_price"]),
                       "sl": float(x["stop_loss"]), "tp": float(x["take_profit"])} for x in sigs])
    s.to_pickle(p)
    return s


def resolve_m1(s: pd.DataFrame, f: pd.DataFrame, m1: pd.DataFrame, tf_sec: int, flat_hhmm: int | None,
               max_minutes: int | None = None) -> pd.DataFrame:
    """Outcome of each of our signals on M1 bars: entry = signal close, SL first
    only within one M1 bar, flat at flat_hhmm ET or after max_minutes."""
    t1 = m1["time"].to_numpy(np.int64)
    h1, l1, c1 = (m1[k].to_numpy(float) for k in ("high", "low", "close"))
    et1 = pd.to_datetime(t1, unit="s", utc=True).tz_convert(ET)
    hh1 = (et1.hour * 100 + et1.minute).to_numpy()
    tt = f["time"].to_numpy(np.int64)
    out = []
    for row in s.itertuples():
        i = row.bar_index
        long = str(row.dir).upper() == "BUY"
        e, sl, tp = row.entry, row.sl, row.tp
        risk = abs(e - sl)
        j0 = int(np.searchsorted(t1, tt[i] + tf_sec))
        r = np.nan
        j = j0
        while j < len(t1):
            if (long and l1[j] <= sl) or (not long and h1[j] >= sl):
                r = -1.0; break
            if (long and h1[j] >= tp) or (not long and l1[j] <= tp):
                r = abs(tp - e) / risk; break
            if (flat_hhmm and hh1[j] >= flat_hhmm) or (max_minutes and j - j0 + 1 >= max_minutes):
                r = ((c1[j] - e) if long else (e - c1[j])) / risk; break
            j += 1
        out.append(r)
    s = s.copy()
    s["r"] = out
    et = pd.to_datetime(tt[s["bar_index"].to_numpy()] + tf_sec, unit="s", utc=True).tz_convert(ET)
    s["et"] = et
    s["day"] = et.strftime("%Y-%m-%d")
    s["long"] = s["dir"].str.upper() == "BUY"
    s["risk_pts"] = (s["entry"] - s["sl"]).abs()
    return s


def stats(r) -> dict:
    r = np.asarray(r, float)
    r = r[np.isfinite(r)]
    if not len(r):
        return {"n": 0}
    pf = r[r > 0].sum() / -r[r < 0].sum() if (r < 0).any() else None
    return {"n": int(len(r)), "win": round(float((r > 0).mean()), 3), "mean_r": round(float(r.mean()), 4),
            "pf": round(float(pf), 3) if pf else None}


def main():
    k, sid, tf = int(sys.argv[1]), sys.argv[2], sys.argv[3]
    symbol = sys.argv[4] if len(sys.argv) > 4 else "NDX100"
    flat = int(sys.argv[5]) if len(sys.argv) > 5 and sys.argv[5] != "0" else None
    max_min = int(sys.argv[6]) if len(sys.argv) > 6 else None
    tf_sec = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600}[tf]
    th, title = their_trades(k)
    f = bars(symbol, tf)
    m1 = f if tf == "M1" else bars(symbol, "M1")
    s = resolve_m1(our_signals(sid, symbol, tf, f), f, m1, tf_sec, flat, max_min)
    lo = max(th["et"].min(), s["et"].min()); hi = min(th["et"].max(), s["et"].max())
    th = th[(th["et"] >= lo) & (th["et"] <= hi)]
    s = s[(s["et"] >= lo) & (s["et"] <= hi)]
    # nth trade of the day on each side, so a 2-a-day strategy matches pairwise
    th["n"] = th.groupby("day").cumcount(); s["n"] = s.groupby("day").cumcount()
    m = th.merge(s, on=["day", "n"], how="outer", suffixes=("_rq", "_us"), indicator=True)
    both = m[m["_merge"] == "both"]
    same_dir = both[both["long_rq"] == both["long_us"]]
    et_rq = pd.to_datetime(same_dir["et_rq"]); et_us = pd.to_datetime(same_dir["et_us"])
    same_time = ((et_rq - et_us).abs() <= pd.Timedelta(seconds=max(60, tf_sec))) if len(same_dir) else pd.Series(dtype=bool)
    sd = same_dir[same_time.values] if len(same_dir) else same_dir
    print(json.dumps({
        "template": title, "strategy": sid, "symbol": symbol, "window": [str(lo), str(hi)],
        "roboquant_on_window": stats(th["r"]), "roboquant_win_by_pnl": round(float(th["win"].mean()), 3),
        "ours_on_window": stats(s["r"]),
        "days_traded": {"roboquant": int(th["day"].nunique()), "ours": int(s["day"].nunique()),
                        "both": int(len(set(th["day"]) & set(s["day"])))},
        "trades_matched_by_day": int(len(both)), "same_direction": int(len(same_dir)),
        "same_direction_within_one_bar": int(len(sd)),
        "outcome_agreement_on_those": round(float(((sd["r_rq"] > 0) == (sd["r_us"] > 0)).mean()), 3) if len(sd) else None,
        "their_r_on_those": stats(sd["r_rq"]), "our_r_on_those": stats(sd["r_us"]),
        "only_roboquant": stats(m.loc[m["_merge"] == "left_only", "r_rq"]),
        "only_ours": stats(m.loc[m["_merge"] == "right_only", "r_us"]),
        "stop_pts_ratio_median": round(float((sd["risk_pts_us"] / sd["risk_pts_rq"]).median()), 3) if len(sd) else None,
        "their_by_year": {y: stats(g["r"]) for y, g in th.groupby(th["et"].dt.year)},
        "our_by_year": {y: stats(g["r"]) for y, g in s.groupby(s["et"].dt.year)},
    }, indent=1, default=str))


if __name__ == "__main__":
    main()
