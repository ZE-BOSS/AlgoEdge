#!/usr/bin/env python
"""
scripts/summarize_confluence_study.py

Turns data/confluence_study/<market>.npz + <market>_rows.json (compact-v1) into:

  1. PER MARKET, PER STRATEGY — the setting chosen on 2023-2024 only (at least
     --min-select trades, positive average R), kept only if it was ALSO profitable
     in 2025, then reported unchanged on Jan-Sep 2026, with quarters won. Its
     select-window t-statistic sits next to the data-mining bar for the number of
     settings tried (significance.expected_max_abs_t).

  2. ONE SETTING PER GROUP — for each strategy, the single configuration with the
     best 2023-2024 result pooled over every market in the group (synthetic /
     real, at least 3 markets), reported on 2025 and 2026 pooled and per market.

  3. CONFLUENCE ABLATION — each confluence on its own against the same strategy,
     setting, side and REFERENCE exit without it (synth 2.5 x ATR stop 1:2, FVG
     strategies 1:2), pooled over the group, with the number of markets where it
     raised 2026 expectancy.

Streams one market at a time: the full row tables do not fit in memory together.

    python scripts/summarize_confluence_study.py [--in data/confluence_study] [--out .../summary.json]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.analytics.significance import expected_max_abs_t  # noqa: E402

SYNTHETIC = {"Crash 1000 Index", "Crash 500 Index", "Crash 300 Index", "Boom 1000 Index", "Boom 500 Index",
             "Boom 300 Index", "Volatility 25 Index", "Volatility 75 Index", "Volatility 100 Index",
             "Jump 25 Index", "Jump 100 Index", "Step Index", "Range Break 100 Index", "Range Break 200 Index"}
SYNTH = ("SpikeFade_v1", "RangeRevert_v1", "RangeBreakout_v1", "TrendDrift_v1")
NC = 6
REF_EXIT = {"synth": (2.5, 2.0), "fvg": (0.0, 2.0)}
SIDE_NAME = {0: "both", 1: "long", -1: "short"}


def win_stats(row: np.ndarray, w: int) -> dict:
    b = w * NC
    n, s, wins, gw, gl, s2 = (float(x) for x in row[b:b + NC])
    if n <= 0:
        return {"n": 0, "avg_r": None, "total_r": 0.0, "win_rate": None, "pf": None, "t": 0.0}
    mean = s / n
    var = (s2 - n * mean * mean) / (n - 1) if n > 1 else 0.0
    sd = math.sqrt(var) if var > 0 else 0.0
    return {"n": int(n), "avg_r": round(mean, 4), "total_r": round(s, 2), "win_rate": round(wins / n, 4),
            "pf": round(gw / gl, 3) if gl > 0 else None,
            "t": round(mean / (sd / math.sqrt(n)), 2) if sd > 0 and n > 2 else 0.0}


class Registry:
    """Global ids for strategy / setting / gate-set strings, shared across markets."""

    def __init__(self):
        self.strat: dict[str, int] = {}
        self.setting: dict[str, int] = {}
        self.gates: dict[str, int] = {}

    @staticmethod
    def _get(d, k):
        return d.setdefault(k, len(d))

    def names(self, d):
        return sorted(d, key=d.get)


STOPS = (0.0, 1.0, 2.5, 5.0)
RRS = (1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 8.0)


def pack(strat, setting, stop_code, rr_code, side, gate):
    return ((((strat * 4096 + setting) * 4 + stop_code) * 8 + rr_code) * 4 + (side + 1)) * 65536 + gate


def unpack(k):
    gate = k % 65536
    k //= 65536
    side = k % 4 - 1
    k //= 4
    rr_code = k % 8
    k //= 8
    stop_code = k % 4
    k //= 4
    return k // 4096, k % 4096, STOPS[stop_code], RRS[rr_code], side, gate


def load_market(folder: Path, stem: str, reg: Registry):
    meta = json.loads((folder / f"{stem}_rows.json").read_text(encoding="utf-8"))
    if meta.get("format") != "compact-v1":
        raise SystemExit(f"{stem}: legacy row file — run run_confluence_study.convert_legacy first")
    z = np.load(folder / f"{stem}.npz")
    s_map = np.array([reg._get(reg.strat, s) for s in meta["strategies"]], np.int64)
    g_map = np.array([reg._get(reg.setting, json.dumps(x, sort_keys=True)) for x in meta["settings"]], np.int64)
    k_map = np.array([reg._get(reg.gates, "|".join(x)) for x in meta["gatesets"]], np.int64)
    stop_code = np.searchsorted(np.array(STOPS, np.float32), z["stop_atr"])
    rr_code = np.searchsorted(np.array(RRS, np.float32), z["rr"])
    keys = pack(s_map[z["strategy"]], g_map[z["setting"]], stop_code.astype(np.int64), rr_code.astype(np.int64),
                z["side"].astype(np.int64), k_map[z["gates"]])
    return meta["symbol"], keys.astype(np.int64), z["stats"].astype(np.float64)


TIME_FLAGS = {"rth", "london", "newyork", "ny_open"}
# Flags recorded for analysis that no single engine parameter reproduces exactly:
# the trend-at-tap flags and the key-level-type flags describe what the PATH
# variants already choose, and a flag on the permissive variant is not the same
# machine as the gated variant.
NOT_A_PARAMETER = {"counter_trend", "with_trend", "key_fvg", "key_cisd", "key_rejection"}


def expressible(strategy: str, gates: list[str]) -> bool:
    """Can the live engine be configured to trade exactly this combination?"""
    if any(g in NOT_A_PARAMETER for g in gates):
        return False
    if len(TIME_FLAGS & set(gates)) > 1:
        return False
    if strategy == "HTFFVGFlip_v1" and "first_tap" in gates:
        return True
    return True


def describe(reg: Registry, key: int) -> dict:
    s, g, stop, rr, side, gate = unpack(int(key))
    strat = reg.names(reg.strat)[s]
    out = {"strategy": strat, "setting": json.loads(reg.names(reg.setting)[g]), "target_rr": rr,
           "side": SIDE_NAME[side], "confluences": [x for x in reg.names(reg.gates)[gate].split("|") if x] or ["none"]}
    if strat in SYNTH:
        out["stop_atr"] = stop
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default=str(ROOT / "data" / "confluence_study"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--min-select", type=int, default=60)
    ap.add_argument("--min-validate", type=int, default=20)
    args = ap.parse_args()
    folder = Path(args.inp)
    stems = [p.name[:-len("_rows.json")] for p in sorted(folder.glob("*_rows.json"))]
    reg = Registry()

    per_market = []
    pooled = {"synthetic": {"idx": {}, "sum": np.zeros((0, 20)), "count": np.zeros(0, np.int32)},
              "real": {"idx": {}, "sum": np.zeros((0, 20)), "count": np.zeros(0, np.int32)}}
    abl = {"synthetic": defaultdict(lambda: np.zeros(8)), "real": defaultdict(lambda: np.zeros(8))}
    abl_help = {"synthetic": defaultdict(lambda: [0, 0]), "real": defaultdict(lambda: [0, 0])}
    markets = []

    for stem in stems:
        sym, keys, st = load_market(folder, stem, reg)
        markets.append(sym)
        group = "synthetic" if sym in SYNTHETIC else "real"
        strat_of = keys // (65536 * 4 * 8 * 4 * 4096)

        # 1. per-market pick
        sel_n, sel_s = st[:, 0], st[:, 1]
        val_n, val_s = st[:, 6], st[:, 7]
        with np.errstate(invalid="ignore", divide="ignore"):
            sel_avg = np.where(sel_n > 0, sel_s / sel_n, -np.inf)
            val_avg = np.where(val_n > 0, val_s / val_n, -np.inf)
            score = np.where((sel_n >= args.min_select) & (sel_avg > 0) & (val_n >= args.min_validate) & (val_avg > 0),
                             sel_avg * np.sqrt(sel_n), -np.inf)
        gate_names = reg.names(reg.gates)
        strat_names = reg.names(reg.strat)
        gate_of = keys % 65536
        ok_gate = np.array([expressible(strat_names[int(s)], [x for x in gate_names[int(g)].split("|") if x])
                            for s, g in zip(strat_of, gate_of)], dtype=bool)
        score = np.where(ok_gate, score, -np.inf)
        for sid in np.unique(strat_of):
            m = strat_of == sid
            name = reg.names(reg.strat)[int(sid)]
            tried = int(m.sum())
            i = int(np.flatnonzero(m)[np.argmax(score[m])])
            if not np.isfinite(score[i]):
                per_market.append({"market": sym, "group": group, "strategy": name, "settings_tried": tried,
                                   "verdict": "nothing positive in both 2023-24 and 2025"})
                continue
            sel, val, uns = (win_stats(st[i], w) for w in range(3))
            bar = expected_max_abs_t(tried)
            per_market.append({"market": sym, "group": group, **describe(reg, keys[i]), "settings_tried": tried,
                               "data_mining_t_bar": round(bar, 2), "select_2023_24": sel, "validate_2025": val,
                               "unseen_2026": uns, "quarters_positive": f"{int(st[i, -2])}/{int(st[i, -1])}",
                               "clears_data_mining_bar": bool(sel["t"] >= bar),
                               "held_in_2026": bool(uns["n"] > 0 and (uns["avg_r"] or 0) > 0)})

        # 2. pooled accumulation
        P = pooled[group]
        new = [int(k) for k in keys if int(k) not in P["idx"]]
        if new:
            base = len(P["idx"])
            for off, k in enumerate(new):
                P["idx"][k] = base + off
            P["sum"] = np.vstack([P["sum"], np.zeros((len(new), 20))])
            P["count"] = np.concatenate([P["count"], np.zeros(len(new), np.int32)])
        rows_idx = np.fromiter((P["idx"][int(k)] for k in keys), np.int64, keys.size)
        np.add.at(P["sum"], rows_idx, st)
        np.add.at(P["count"], rows_idx, 1)

        # 3. ablation at the reference exit, side both
        local = {int(k): r for r, k in enumerate(keys)}
        empty_gate = reg.gates.get("", None)
        for r, k in enumerate(keys):
            s, g, stop, rr, side, gate = unpack(int(k))
            if side != 0 or gate == empty_gate:
                continue
            gname = reg.names(reg.gates)[gate]
            if "|" in gname:
                continue
            strat = reg.names(reg.strat)[s]
            if (stop, rr) != (REF_EXIT["synth"] if strat in SYNTH else REF_EXIT["fvg"]):
                continue
            if empty_gate is None:
                continue
            bk = pack(s, g, STOPS.index(stop), RRS.index(rr), 0, empty_gate)
            br = local.get(bk)
            if br is None:
                continue
            a = abl[group][(strat, gname)]
            a += np.array([st[r, 0], st[r, 1], st[br, 0], st[br, 1], st[r, 12], st[r, 13], st[br, 12], st[br, 13]])
        # markets helped: per (strategy, gate) pooled over settings within this market
        per_mkt = defaultdict(lambda: np.zeros(4))
        for r, k in enumerate(keys):
            s, g, stop, rr, side, gate = unpack(int(k))
            if side != 0 or gate == empty_gate or empty_gate is None:
                continue
            gname = reg.names(reg.gates)[gate]
            if "|" in gname:
                continue
            strat = reg.names(reg.strat)[s]
            if (stop, rr) != (REF_EXIT["synth"] if strat in SYNTH else REF_EXIT["fvg"]):
                continue
            br = local.get(pack(s, g, STOPS.index(stop), RRS.index(rr), 0, empty_gate))
            if br is None:
                continue
            per_mkt[(strat, gname)] += np.array([st[r, 12], st[r, 13], st[br, 12], st[br, 13]])
        for kk, v in per_mkt.items():
            if v[0] > 0 and v[2] > 0:
                h = abl_help[group][kk]
                h[1] += 1
                h[0] += int(v[1] / v[0] > v[3] / v[2])
        del keys, st, local
        print(f"  {sym}: loaded", flush=True)

    # pooled winners
    pooled_out = []
    winners = {}
    for group, P in pooled.items():
        if not P["idx"]:
            continue
        keys = np.fromiter(P["idx"].keys(), np.int64, len(P["idx"]))
        order = np.fromiter(P["idx"].values(), np.int64, len(P["idx"]))
        S = P["sum"][order]
        C = P["count"][order]
        strat_of = keys // (65536 * 4 * 8 * 4 * 4096)
        gate_names, strat_names = reg.names(reg.gates), reg.names(reg.strat)
        ok_gate = np.array([expressible(strat_names[int(s)], [x for x in gate_names[int(g)].split("|") if x])
                            for s, g in zip(strat_of, keys % 65536)], dtype=bool)
        with np.errstate(invalid="ignore", divide="ignore"):
            avg = np.where(S[:, 0] > 0, S[:, 1] / S[:, 0], -np.inf)
            score = np.where(ok_gate & (S[:, 0] >= args.min_select * 3) & (avg > 0) & (C >= 3),
                             avg * np.sqrt(S[:, 0]), -np.inf)
        for sid in np.unique(strat_of):
            m = strat_of == sid
            name = reg.names(reg.strat)[int(sid)]
            i = int(np.flatnonzero(m)[np.argmax(score[m])])
            if not np.isfinite(score[i]):
                pooled_out.append({"group": group, "strategy": name, "verdict": "no pooled setting positive in 2023-24"})
                continue
            winners[(group, int(keys[i]))] = len(pooled_out)
            bar = expected_max_abs_t(int(m.sum()))
            pooled_out.append({"group": group, **describe(reg, keys[i]), "settings_tried": int(m.sum()),
                               "markets_with_trades": int(C[i]), "data_mining_t_bar": round(bar, 2),
                               "select_2023_24": win_stats(S[i], 0), "validate_2025": win_stats(S[i], 1),
                               "unseen_2026": win_stats(S[i], 2), "markets": {}})

    # second pass: per-market detail of the pooled winners
    if winners:
        for stem in stems:
            sym, keys, st = load_market(folder, stem, reg)
            group = "synthetic" if sym in SYNTHETIC else "real"
            want = {k: i for (g, k), i in winners.items() if g == group}
            hit = np.flatnonzero(np.isin(keys, np.fromiter(want.keys(), np.int64, len(want))))
            for r in hit:
                entry = pooled_out[want[int(keys[r])]]
                entry["markets"][sym] = {"validate_2025": win_stats(st[r], 1), "unseen_2026": win_stats(st[r], 2),
                                         "quarters_positive": f"{int(st[r, -2])}/{int(st[r, -1])}"}
        for e in pooled_out:
            if "markets" in e:
                e["markets_positive_2026"] = sum(1 for v in e["markets"].values() if (v["unseen_2026"]["avg_r"] or 0) > 0)

    ablation_out = {}
    for group in abl:
        rows = defaultdict(list)
        for (strat, gname), a in abl[group].items():
            def avg(n, s):
                return round(s / n, 4) if n else None
            won, tot = abl_help[group][(strat, gname)]
            rows[strat].append({
                "confluence": gname,
                "select_avg_r_with": avg(a[0], a[1]), "select_avg_r_without": avg(a[2], a[3]),
                "select_edge_added": round(a[1] / a[0] - a[3] / a[2], 4) if a[0] and a[2] else None,
                "unseen_avg_r_with": avg(a[4], a[5]), "unseen_avg_r_without": avg(a[6], a[7]),
                "unseen_edge_added": round(a[5] / a[4] - a[7] / a[6], 4) if a[4] and a[6] else None,
                # Can exceed 1: a filter frees daily budget and open-position slots.
                "trades_vs_no_filter": round(a[0] / a[2], 3) if a[2] else None,
                "helped_in_2026": f"{won}/{tot}"})
        for strat in rows:
            rows[strat].sort(key=lambda x: -(x["select_edge_added"] if x["select_edge_added"] is not None else -9))
        ablation_out[group] = dict(rows)

    out = Path(args.out or (folder / "summary.json"))
    out.write_text(json.dumps({"markets": markets, "per_market": per_market, "pooled": pooled_out,
                               "ablation": ablation_out, "reference_exits": REF_EXIT}, indent=1, default=float),
                   encoding="utf-8")
    print(f"{len(markets)} markets -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
