"""
scripts/combo_eval.py - hypothesis grid over the candidates of of_zones.py / pa_study.py.

Every combination of one candle pattern x one order-flow condition x one context x one session
x one target is run (one position at a time), on January-June and July-November separately.

Outputs per candidate file:
  - robust: combinations with >= MIN trades in both halves, ranked by the worse half's
    expectancy (so a combo must work in both halves to rank)
  - effects: for each confluence, what adding it to otherwise-identical combinations does to win
    rate and expectancy (trade-weighted average over all pairs that have enough trades) - the
    "piece by piece" breakdown

    python combo_eval.py /opt/algoedge/research_data/zones/cand_gold_M15.parquet ... --out combos.json
"""
from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd

SPLIT = pd.Timestamp("2026-07-01")


def col(d: pd.DataFrame, k: str, default=False):
    return d[k] if k in d.columns else pd.Series(default, index=d.index)


CANDLE = {
    "any_candle": lambda d: pd.Series(True, index=d.index),
    "pin": lambda d: col(d, "pin"), "engulf": lambda d: col(d, "engulf"), "doji": lambda d: col(d, "doji"),
    "rejection": lambda d: col(d, "pin") | col(d, "engulf"),
    "double_tb": lambda d: col(d, "double"),
    "rejection+double": lambda d: (col(d, "pin") | col(d, "engulf")) & col(d, "double"),
    "sweep": lambda d: col(d, "sweep"),
}
FLOW = {
    "no_flow": lambda d: pd.Series(True, index=d.index),
    "absorption": lambda d: col(d, "absorb"), "delta_flip": lambda d: col(d, "flip"),
    "big_trades_with": lambda d: col(d, "big_with"),
    "approach_exhausted": lambda d: col(d, "appr_delta", -1) > 0,
    "absorb_or_flip": lambda d: col(d, "absorb") | col(d, "flip"),
    "absorb_and_flip": lambda d: col(d, "absorb") & col(d, "flip"),
    # trend_of.py: flow with the trade on the signal bar / the last 12 bars, institutional size,
    # and the top-of-book imbalance (when a bbo file was available)
    "delta_with": lambda d: col(d, "delta_with"),
    "cvd_with": lambda d: col(d, "cvd_with"),
    "big_surge": lambda d: col(d, "big_surge"),
    "big_surge_with": lambda d: col(d, "big_surge") & col(d, "big_with"),
    "vol_surge": lambda d: col(d, "vol_surge"),
    "delta_and_big_with": lambda d: col(d, "delta_with") & col(d, "big_with"),
    "book_with": lambda d: col(d, "book_with"),
}
CONTEXT = {
    "no_context": lambda d: pd.Series(True, index=d.index),
    "big_move_in": lambda d: col(d, "big_move", 0) >= 3.0,
    "fresh_zone": lambda d: col(d, "touch", 9) <= 1,
    "strong_zone": lambda d: (col(d, "strength", 0).fillna(0) >= 1.5) | col(d, "strong"),
    "big_trades_in_zone": lambda d: col(d, "zone_bigs", 0).fillna(0) >= 1.5,
    "origin_zone": lambda d: col(d, "kind", "") == "origin",
    "hvn_zone": lambda d: col(d, "kind", "") == "hvn",
    "prev_day_level": lambda d: col(d, "pd_level"),
    "with_trend": lambda d: col(d, "trend", 0) > 0,
    "against_trend": lambda d: col(d, "trend", 0) < 0,
    "reversal": lambda d: col(d, "setup", "") == "reversal",
    "retest": lambda d: col(d, "setup", "") == "retest",
    # trend_of.py
    "break": lambda d: col(d, "setup", "") == "break",
    "at_inst_node": lambda d: col(d, "at_node"),
    "strong_inst_node": lambda d: col(d, "node_strength", 0) >= 4,
    "vol_expanding": lambda d: col(d, "vol_expanding"),
    "fib_trend": lambda d: col(d, "fib_trend"),
    "retest_with_trend": lambda d: (col(d, "setup", "") == "retest") & (col(d, "trend", 0) > 0),
    "break_with_trend": lambda d: (col(d, "setup", "") == "break") & (col(d, "trend", 0) > 0),
}
SESSION = {
    "all_day": lambda d: pd.Series(True, index=d.index),
    "london_ny": lambda d: (d["hour"] >= 7) & (d["hour"] < 20),
    "ny_only": lambda d: (d["hour"] >= 13.5) & (d["hour"] < 20),
}


def take(sub: pd.DataFrame, target: str) -> np.ndarray:
    r, ent, ex = sub[f"r_{target}"].values, sub["entry_t"].values, sub[f"x_{target}"].values
    keep, free = [], np.datetime64("1970-01-01")
    for k in range(len(sub)):
        if np.isnan(r[k]) or ent[k] < free:
            continue
        keep.append(k)
        free = ex[k] if ex[k] is not None and not pd.isna(ex[k]) else ent[k]
    return r[keep], ent[keep]


def st(r: np.ndarray) -> dict:
    if len(r) == 0:
        return {"n": 0}
    w, lo = r[r > 0], r[r <= 0]
    eq = np.cumsum(r)
    dd = float(np.max(np.maximum.accumulate(np.concatenate([[0], eq]))[1:] - eq))
    return {"n": int(len(r)), "win": round(float((r > 0).mean()), 3), "net": round(float(r.sum()), 2),
            "exp": round(float(r.mean()), 3), "pf": round(float(w.sum() / -lo.sum()), 2) if lo.sum() < 0 else None,
            "dd": round(dd, 2)}


def run_file(path: Path, min_n: int) -> dict:
    d = pd.read_parquet(path).sort_values("entry_t").reset_index(drop=True)
    d["entry_t"] = pd.to_datetime(d["entry_t"])
    for k in [c for c in d.columns if c.startswith("x_")]:
        d[k] = pd.to_datetime(d[k])
    targets = [c[2:] for c in d.columns if c.startswith("r_")]
    has_flow = "absorb" in d.columns
    flows = FLOW if has_flow else {"no_flow": FLOW["no_flow"]}
    masks = {}
    need = {"at_inst_node": "at_node", "strong_inst_node": "node_strength", "vol_expanding": "vol_expanding",
            "fib_trend": "fib_trend", "prev_day_level": "pd_level", "fresh_zone": "touch", "strong_zone": "kind",
            "big_trades_in_zone": "zone_bigs", "origin_zone": "kind", "hvn_zone": "kind", "big_move_in": "big_move",
            "with_trend": "trend", "against_trend": "trend", "reversal": "setup", "retest": "setup", "break": "setup",
            "retest_with_trend": "setup", "break_with_trend": "setup", "book_with": "book_imb",
            "delta_with": "delta_with", "cvd_with": "cvd_with", "big_surge": "big_surge",
            "big_surge_with": "big_surge", "vol_surge": "vol_surge", "delta_and_big_with": "delta_with"}
    for grp in (CANDLE, flows, CONTEXT, SESSION):
        for k, f in grp.items():
            if k in need and need[k] not in d.columns:
                continue
            if k == "book_with" and not d["book_imb"].notna().any():
                continue
            try:
                masks[k] = pd.Series(f(d)).fillna(False).values.astype(bool)
            except Exception:  # noqa: BLE001
                pass
    train = (d["entry_t"] < SPLIT).values
    res = []
    for cn, fn, xn, sn in itertools.product(CANDLE, flows, CONTEXT, SESSION):
        if not all(k in masks for k in (cn, fn, xn, sn)):
            continue
        m = masks[cn] & masks[fn] & masks[xn] & masks[sn]
        if m.sum() < 2 * min_n:
            continue
        for tg in targets:
            a, _ = take(d[m & train], tg)
            b, _ = take(d[m & ~train], tg)
            full, ent = take(d[m], tg)
            res.append({"candle": cn, "flow": fn, "context": xn, "session": sn, "target": tg,
                        "train": st(a), "test": st(b), "full": st(full),
                        "_months": pd.Series(full, index=pd.DatetimeIndex(ent)).groupby(
                            pd.DatetimeIndex(ent).to_period("M")).sum().round(2).astype(float).to_dict() if len(full) else {}})
    ok = [x for x in res if x["train"]["n"] >= min_n and x["test"]["n"] >= min_n]
    for x in ok:
        x["score"] = min(x["train"]["exp"], x["test"]["exp"])
    robust = sorted(ok, key=lambda x: x["score"], reverse=True)[:25]
    for x in robust:
        x["months"] = {str(k): v for k, v in x.pop("_months").items()}
    # piece-by-piece effects: compare each combo with the same combo minus one confluence
    idx = {(x["candle"], x["flow"], x["context"], x["session"], x["target"]): x for x in res}
    effects = {}
    for slot, none in (("candle", "any_candle"), ("flow", "no_flow"), ("context", "no_context"), ("session", "all_day")):
        for x in res:
            v = x[slot]
            if v == none:
                continue
            key = tuple(none if s == slot else x[s] for s in ("candle", "flow", "context", "session")) + (x["target"],)
            base = idx.get(key)
            if not base or x["full"]["n"] < min_n or base["full"]["n"] < min_n:
                continue
            e = effects.setdefault(v, {"pairs": 0, "w": 0.0, "d_win": 0.0, "d_exp": 0.0, "better": 0})
            wgt = x["full"]["n"]
            e["pairs"] += 1
            e["w"] += wgt
            e["d_win"] += wgt * (x["full"]["win"] - base["full"]["win"])
            e["d_exp"] += wgt * (x["full"]["exp"] - base["full"]["exp"])
            e["better"] += int(x["full"]["exp"] > base["full"]["exp"])
    eff = {k: {"pairs": v["pairs"], "avg_win_change": round(v["d_win"] / v["w"], 3),
               "avg_exp_change_r": round(v["d_exp"] / v["w"], 3), "share_better": round(v["better"] / v["pairs"], 2)}
           for k, v in effects.items() if v["pairs"]}
    baseline = {tg: idx.get(("any_candle", "no_flow", "no_context", "all_day", tg), {}).get("full") for tg in targets}
    return {"file": path.name, "candidates": int(len(d)), "combos": len(res), "baseline": baseline,
            "robust": [{k: v for k, v in x.items() if not k.startswith("_")} for x in robust], "effects": eff}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--min-n", type=int, default=30)
    ap.add_argument("--out", default="combos.json")
    a = ap.parse_args()
    allr = []
    for f in a.files:
        r = run_file(Path(f), a.min_n)
        allr.append(r)
        top = r["robust"][0] if r["robust"] else None
        print(f"{r['file']:36s} cand {r['candidates']:6d} combos {r['combos']:5d} | top: " +
              (f"{top['candle']}/{top['flow']}/{top['context']}/{top['session']}->{top['target']} "
               f"train {top['train']['n']}tr w{top['train']['win']} pf{top['train']['pf']} | "
               f"test {top['test']['n']}tr w{top['test']['win']} pf{top['test']['pf']}" if top else "none"), flush=True)
        Path(a.out).write_text(json.dumps(allr, default=str))
    print("COMBOS_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
