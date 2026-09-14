#!/usr/bin/env python
"""
scripts/run_confluence_study.py

The 2026-09-14 confluence study: SpikeFade, RangeRevert, RangeBreakout,
TrendDrift, HTF FVG Flip and Bias IFVG, every confluence taken apart, on the
major synthetic indices AND the major FX / metals / crypto / index markets.

For each market it builds every candidate once (research generators held to the
live engines by tests/test_synth_research.py and tests/test_fvg_research.py),
resolves every exit, and scores every (strategy setting, stop, target, side,
up-to-two confluences) combination in three windows:

    select      2023-01-01 -> 2025-01-01   settings are chosen here only
    validate    2025-01-01 -> 2026-01-01   must also be profitable here
    unseen      2026-01-01 -> 2026-09-12   never used for choosing

plus how many calendar quarters from 2023 Q1 to 2026 Q3 each made money.

    python scripts/run_confluence_study.py --bars <dir> --out data/confluence_study [--markets ...]

Writes <out>/<market>.npz (row table) and <out>/<market>_rows.json (row keys).
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.analytics import fvg_research as fr  # noqa: E402
from backend.analytics import synth_research as sr  # noqa: E402

T = lambda y, m, d: int(datetime(y, m, d, tzinfo=timezone.utc).timestamp())  # noqa: E731
WINDOWS = {"select": (T(2023, 1, 1), T(2025, 1, 1)),
           "validate": (T(2025, 1, 1), T(2026, 1, 1)),
           "unseen": (T(2026, 1, 1), T(2026, 9, 13))}
Q_LO, Q_HI = T(2023, 1, 1), T(2026, 9, 13)
SIDES = (0, 1, -1)
MAX_PER_DAY = 4
# stats columns per window: n, sum_r, wins, gross_win, gross_loss, sum_r2
COLS = ("n", "sum_r", "wins", "gw", "gl", "sum_r2")


def _pick_nb():
    from numba import njit

    @njit(cache=True)
    def pick(t_entry, t_exit, mask, max_per_day):
        out = np.empty(t_entry.size, dtype=np.int64)
        m = 0
        free_at = -1
        day = -1
        count = 0
        for a in range(t_entry.size):
            if not mask[a]:
                continue
            te = t_entry[a]
            dd = te // 86400
            if dd != day:
                day = dd
                count = 0
            if max_per_day > 0 and count >= max_per_day:
                continue
            count += 1  # budget is spent on emission, before the one-position rule
            if te < free_at:
                continue
            out[m] = a
            m += 1
            free_at = t_exit[a]
        return out[:m]

    return pick


PICK = _pick_nb()


def _score_nb():
    """Every (side, gate combo) of one exit key in a single compiled pass — the
    same mask -> PICK -> window/quarter sums as a numpy loop, without ~30 numpy
    calls per row (that loop was ~40% of a market's runtime)."""
    from numba import njit

    @njit(cache=True)
    def score(te, tx, r, finite, dirs, feats, combos, sides, q_all, lo, hi, q_lo, q_hi, max_per_day, out, ok):
        n = te.size
        n_combo = combos.shape[0]
        n_win = lo.size
        mask = np.empty(n, dtype=np.bool_)
        taken = np.empty(n, dtype=np.int64)
        for s in range(sides.size):
            side = sides[s]
            for c in range(n_combo):
                row = s * n_combo + c
                a0, a1 = combos[c, 0], combos[c, 1]
                cnt = 0
                for a in range(n):
                    m = finite[a] and (side == 0 or dirs[a] == side)
                    if m and a0 >= 0:
                        m = feats[a, a0]
                    if m and a1 >= 0:
                        m = feats[a, a1]
                    mask[a] = m
                    if m:
                        cnt += 1
                if cnt < 20:
                    continue
                k = 0
                free_at = -1
                day = -1
                count = 0
                for a in range(n):
                    if not mask[a]:
                        continue
                    dd = te[a] // 86400
                    if dd != day:
                        day = dd
                        count = 0
                    if max_per_day > 0 and count >= max_per_day:
                        continue
                    count += 1  # budget is spent on emission, before the one-position rule
                    if te[a] < free_at:
                        continue
                    taken[k] = a
                    k += 1
                    free_at = tx[a]
                if k < 20:
                    continue
                ok[row] = True
                for w in range(n_win):
                    cn = 0
                    sr_ = 0.0
                    wins = 0
                    gw = 0.0
                    gl = 0.0
                    s2 = 0.0
                    for p in range(k):
                        a = taken[p]
                        if te[a] >= lo[w] and te[a] < hi[w]:
                            x = r[a]
                            cn += 1
                            sr_ += x
                            s2 += x * x
                            if x > 0:
                                wins += 1
                                gw += x
                            elif x < 0:
                                gl -= x
                    b = w * 6
                    out[row, b] = cn
                    out[row, b + 1] = sr_
                    out[row, b + 2] = wins
                    out[row, b + 3] = gw
                    out[row, b + 4] = gl
                    out[row, b + 5] = s2
                qs = np.zeros(15)
                qn = np.zeros(15, dtype=np.int64)
                anyq = False
                for p in range(k):
                    a = taken[p]
                    if te[a] >= q_lo and te[a] < q_hi:
                        anyq = True
                        qs[q_all[a]] += r[a]
                        qn[q_all[a]] += 1
                if anyq:
                    pos = 0
                    tot = 0
                    for q in range(15):
                        if qn[q] > 0:
                            tot += 1
                            if qs[q] > 0:
                                pos += 1
                    out[row, n_win * 6] = pos
                    out[row, n_win * 6 + 1] = tot

    return score


SCORE = _score_nb()


def _quarter_ids(t: np.ndarray) -> np.ndarray:
    import pandas as pd
    idx = pd.to_datetime(t, unit="s", utc=True)
    return ((idx.year - 2023) * 4 + (idx.month - 1) // 3).to_numpy()


def load(bars_dir: Path, symbol: str, tf: str) -> dict[str, np.ndarray] | None:
    p = bars_dir / f"{symbol.replace(' ', '_')}_{tf}.npz"
    if not p.exists():
        return None
    z = np.load(p)
    return {k: z[k] for k in ("time", "open", "high", "low", "close", "spread", "tick_volume")} | {
        "point": float(z["point"])}


def score_candset(cs: sr.CandSet, flags: tuple[str, ...], rows: list, stats: list, label: dict) -> None:
    if cs.t_entry.size == 0:
        return
    order = np.argsort(cs.t_entry, kind="stable")
    te = np.ascontiguousarray(cs.t_entry[order], dtype=np.int64)
    q_all = np.asarray(_quarter_ids(te), dtype=np.int64)
    col = {f: k for k, f in enumerate(f for f in flags if f in cs.feats)}
    combos = [c for c in [()] + [(f,) for f in flags] + [c for c in itertools.combinations(flags, 2)
                                                        if not ({"adx_trend", "adx_range"} <= set(c))
                                                        and not ({"london", "newyork"} <= set(c))]
              if all(f in col for f in c)]
    cidx = np.full((len(combos), 2), -1, dtype=np.int64)
    for k, combo in enumerate(combos):
        for m, f in enumerate(combo):
            cidx[k, m] = col[f]
    fm = np.zeros((te.size, max(len(col), 1)), dtype=np.bool_)
    for f, k in col.items():
        fm[:, k] = cs.feats[f][order]
    dirs = np.ascontiguousarray(cs.direction[order], dtype=np.int64)
    sides = np.asarray(SIDES, dtype=np.int64)
    lo = np.array([w[0] for w in WINDOWS.values()], dtype=np.int64)
    hi = np.array([w[1] for w in WINDOWS.values()], dtype=np.int64)
    setting = json.dumps(label["setting"], sort_keys=True)
    gate_names = ["|".join(c) for c in combos]
    width = len(WINDOWS) * len(COLS) + 2
    for key, r_all in cs.r.items():
        r = np.ascontiguousarray(r_all[order], dtype=np.float64)
        tx = np.ascontiguousarray(cs.t_exit[key][order], dtype=np.int64)
        out = np.zeros((sides.size * len(combos), width), dtype=np.float32)
        ok = np.zeros(sides.size * len(combos), dtype=np.bool_)
        SCORE(te, tx, r, np.isfinite(r), dirs, fm, cidx, sides, q_all, lo, hi, Q_LO, Q_HI, MAX_PER_DAY, out, ok)
        for row in np.flatnonzero(ok):
            s, c = divmod(int(row), len(combos))
            rows.append((label["strategy"], setting, key[0], key[1], SIDES[s], gate_names[c]))
            stats.append(out[row])


def save_compact(out_dir: Path, stem: str, symbol: str, rows: list[tuple], stats: list, counts: dict) -> None:
    """Rows as integer/float columns plus lookup lists — a market is a few MB, not
    ~50 MB of repeated JSON dicts."""
    strategies, settings, gatesets = [], [], []
    si, gi, ki = {}, {}, {}
    cols = {"strategy": [], "setting": [], "stop_atr": [], "rr": [], "side": [], "gates": []}
    for strat, setting, stop, rr, side, gates in rows:
        cols["strategy"].append(si.setdefault(strat, len(si)))
        cols["setting"].append(gi.setdefault(setting, len(gi)))
        cols["gates"].append(ki.setdefault(gates, len(ki)))
        cols["stop_atr"].append(stop)
        cols["rr"].append(rr)
        cols["side"].append(side)
    np.savez_compressed(
        out_dir / f"{stem}.npz",
        stats=np.vstack(stats) if stats else np.zeros((0, 20), np.float32),
        strategy=np.asarray(cols["strategy"], np.int16), setting=np.asarray(cols["setting"], np.int32),
        gates=np.asarray(cols["gates"], np.int32), stop_atr=np.asarray(cols["stop_atr"], np.float32),
        rr=np.asarray(cols["rr"], np.float32), side=np.asarray(cols["side"], np.int8))
    (out_dir / f"{stem}_rows.json").write_text(json.dumps({
        "symbol": symbol, "format": "compact-v1",
        "strategies": sorted(si, key=si.get), "settings": [json.loads(k) for k in sorted(gi, key=gi.get)],
        "gatesets": [k.split("|") if k else [] for k in sorted(ki, key=ki.get)],
        "candidates": counts, "windows": WINDOWS, "cols": COLS}), encoding="utf-8")


def convert_legacy(out_dir: Path, stem: str) -> bool:
    """Rewrite a pre-compact <market>_rows.json (list of row dicts) in place."""
    jf = out_dir / f"{stem}_rows.json"
    meta = json.loads(jf.read_text(encoding="utf-8"))
    if meta.get("format") == "compact-v1":
        return False
    st = np.load(out_dir / f"{stem}.npz")["stats"]
    rows = [(r["strategy"], json.dumps(r["setting"], sort_keys=True), r["stop_atr"], r["rr"], r["side"],
             "|".join(r["gates"])) for r in meta["rows"]]
    del meta["rows"]
    save_compact(out_dir, stem, meta["symbol"], rows, list(st), meta.get("candidates", {}))
    return True


def run_market(symbol: str, bars_dir: Path, out_dir: Path, strategies: list[str]) -> str:
    from backend.risk.position_sizer import get_pip_size

    t0 = time.time()
    m5 = load(bars_dir, symbol, "M5")
    if m5 is None:
        return f"{symbol}: no M5 bars"
    frame = sr.build_frame(symbol, m5)
    rows: list[dict] = []
    stats: list[np.ndarray] = []
    counts: dict[str, int] = {}

    for sid in [s for s in strategies if s in sr.STRATEGIES]:
        grid = sr.SIGNAL_GRID[sid]
        names = list(grid)
        for values in itertools.product(*grid.values()):
            params = dict(zip(names, values))
            cs = sr.build_candidates(frame, sid, params)
            counts[f"{sid}{params}"] = int(cs.t_entry.size)
            score_candset(cs, sr.CONFLUENCES, rows, stats, {"strategy": sid, "setting": params})
            del cs

    if any(s in strategies for s in ("HTFFVGFlip_v1", "BiasIFVG_v1")):
        pip = float(get_pip_size(symbol))
        s5 = fr.from_arrays(m5)
        # Synthetic indices trade 24/7 with no session, so the session-window
        # variants are not a meaningful confluence there — only the session-off
        # machines run (London / New York time is still recorded as a flag).
        from scripts.harvest_study_bars import SYNTHETIC
        is_synth = symbol in SYNTHETIC
        htf_variants = [v for v in fr.all_htf_variants() if not (is_synth and v.session_rth)]
        bias_variants = [v for v in fr.all_bias_variants() if not (is_synth and v.session != "OFF")]
        if "HTFFVGFlip_v1" in strategies:
            h1 = load(bars_dir, symbol, "H1")
            if h1 is not None:
                out = fr.htf_candidates(s5, fr.from_arrays(h1), pip, htf_variants, frame=frame)
                for v in htf_variants:
                    cs = fr.to_candset(symbol, "HTFFVGFlip_v1", v.name, out[v.name], m5)
                    counts[f"HTFFVGFlip_v1|{v.name}"] = int(cs.t_entry.size)
                    score_candset(cs, fr.HTF_FLAGS, rows, stats, {"strategy": "HTFFVGFlip_v1", "setting": v.params()})
        if "BiasIFVG_v1" in strategies:
            m15, h4 = load(bars_dir, symbol, "M15"), load(bars_dir, symbol, "H4")
            if m15 is not None and h4 is not None:
                out = fr.bias_candidates(s5, fr.from_arrays(m15), fr.from_arrays(h4), pip, bias_variants, frame=frame)
                for v in bias_variants:
                    cs = fr.to_candset(symbol, "BiasIFVG_v1", v.name, out[v.name], m5)
                    counts[f"BiasIFVG_v1|{v.name}"] = int(cs.t_entry.size)
                    score_candset(cs, fr.BIAS_FLAGS, rows, stats, {"strategy": "BiasIFVG_v1", "setting": v.params()})

    stem = symbol.replace(" ", "_")
    save_compact(out_dir, stem, symbol, rows, stats, counts)
    return f"{symbol}: {len(rows):,} rows in {time.time() - t0:.0f}s"


def main() -> int:
    from scripts.harvest_study_bars import MARKETS

    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", required=True)
    ap.add_argument("--out", default=str(ROOT / "data" / "confluence_study"))
    ap.add_argument("--markets", nargs="*", default=MARKETS)
    ap.add_argument("--strategies", nargs="*",
                    default=list(sr.STRATEGIES) + ["HTFFVGFlip_v1", "BiasIFVG_v1"])
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for sym in args.markets:
        stem = sym.replace(" ", "_")
        if (out / f"{stem}_rows.json").exists():
            print(f"{sym}: cached", flush=True)
            continue
        try:
            print(run_market(sym, Path(args.bars), out, args.strategies), flush=True)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"{sym}: FAILED {type(e).__name__}: {e}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
