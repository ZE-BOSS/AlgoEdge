#!/usr/bin/env python
"""
scripts/run_spike_optimise.py

Can the spike-resumption rule be made to work, and on which instruments?

The first pass pooled eight symbols and reported one number, which hid that
Crash 1000 was carrying everything while Boom 900 -- the instrument the pattern
was spotted on -- was the worst of the eight. This pass:

  * scores every variant PER INSTRUMENT and reports the split, never the pool,
  * splits Boom from Crash, because they behaved oppositely,
  * chooses on 2024-09 -> 2026-01 and reports the choice unchanged on
    2026-01 -> today, so the out-of-sample number is never the chosen one,
  * runs the shifted-entry control on every finalist.

New knobs over the first pass: spike measured by BODY or RANGE, a requirement
that the confirming bar close back past where the spike started ("it must come
below the last buy spike"), a minimum gap between spikes so a cluster counts
once, and an optional chandelier trail instead of a fixed target.

    py -3.12 scripts/run_spike_optimise.py
    py -3.12 scripts/run_spike_optimise.py --stage finalists
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import zone_money_engine as eng  # noqa: E402
from scripts.run_published_strategies import FEEDS  # noqa: E402
from scripts.run_spike_resumption import MARKETS, load_m15, run_variant  # noqa: E402

OUT = ROOT / "data" / "spike_resumption"
SELECT_END = "2026-01-01"

BASE = dict(k_atr=3.0, trend_mode="below", trend_look=50, confirm_bars=1,
            stop_atr=1.0, target="spike", target_rr=3.0, max_hold=5,
            spike_metric="body", require_below_spike=False,
            min_bars_since_spike=0, trail_atr=0.0)


def score(trades: list[dict], risk: float = 0.5) -> dict | None:
    if len(trades) < 12:
        return None
    r = np.array([x["r"] for x in trades])
    rep = eng.report(eng.run_account(sorted(trades, key=lambda x: x["t"]), 10_000.0, risk), "x")
    if not rep.get("trades"):
        return None
    return {
        "n": len(r), "expectancy_r": float(r.mean()),
        "t": float(r.mean() / r.std(ddof=1) * np.sqrt(len(r))) if r.std() else 0.0,
        "win": rep["win_rate"], "pf": rep["profit_factor"],
        "return_pct": rep["return_pct"], "dd": rep["max_dd_pct"],
    }


def load_all(since: str, until: str | None):
    bars_dir, specs = FEEDS["deriv"]
    eng.SPECS = json.loads(specs.read_text())
    t0 = int(datetime.fromisoformat(since).replace(tzinfo=timezone.utc).timestamp())
    t1 = int(datetime.fromisoformat(until).replace(tzinfo=timezone.utc).timestamp()) if until else None
    data = {}
    for sym, side in MARKETS:
        b = load_m15(bars_dir, sym, t0)
        if b is None:
            continue
        if t1:
            m = b["time"] < t1
            b = {k: (v[m] if isinstance(v, np.ndarray) else v) for k, v in b.items()}
        data[sym] = (b, side)
    return data


def book(data, params, family=None, placebo=0) -> list[dict]:
    out = []
    for sym, (b, side) in data.items():
        if family and family.lower() not in sym.lower():
            continue
        out += run_variant(b, sym, side, slip_points=1.0, placebo_shift=placebo, **params)
    return out


ROW = f"  {'variant':46s} {'fam':>5s} {'N':>5s} {'expR':>7s} {'win%':>6s} {'PF':>5s} {'t':>6s} {'ret%':>8s} {'DD%':>6s}"


def line(label, family, st):
    if st is None:
        print(f"  {label:46s} {family:>5s}     -   too few")
        return
    print(f"  {label:46s} {family:>5s} {st['n']:>5d} {st['expectancy_r']:>+7.3f} "
          f"{st['win']:>5.1f}% {st['pf']:>5.2f} {st['t']:>+6.2f} {st['return_pct']:>+7.2f}% "
          f"{st['dd']:>5.2f}%")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="all", choices=["all", "knobs", "finalists"])
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    sel = load_all("2024-09-01", SELECT_END)
    oos = load_all(SELECT_END, None)
    results: dict = {}

    if args.stage in ("all", "knobs"):
        print(f"\nSELECTION WINDOW 2024-09 -> {SELECT_END} -- one knob at a time, split by family")
        print(ROW)
        print("  " + "-" * (len(ROW) - 2))
        grids = {
            "spike_metric": ["body", "range", "either"],
            "require_below_spike": [False, True],
            "min_bars_since_spike": [0, 10, 20],
            "k_atr": [2.0, 3.0, 4.0],
            "stop_atr": [0.5, 1.0, 2.0],
            "target": ["spike", "rr"],
            "target_rr": [2.0, 3.0],
            "max_hold": [3, 5, 8],
            "trail_atr": [0.0, 1.0, 2.0],
            "trend_look": [30, 50, 80],
        }
        for key, values in grids.items():
            for v in values:
                p = {**BASE, key: v}
                if key == "target_rr":
                    p["target"] = "rr"
                for fam in ("Boom", "Crash"):
                    line(f"{key}={v}", fam, score(book(sel, p, fam)))
                    results[f"{key}={v}|{fam}"] = score(book(sel, p, fam))
            print()

    if args.stage in ("all", "finalists"):
        # A small, deliberately short list of combinations, each motivated by the
        # knob results rather than by a full cross-product search -- a grid over
        # ten knobs would find something on 120 trades whatever the data said.
        finalists = {
            "base": BASE,
            "crash-only, below-spike": {**BASE, "require_below_spike": True},
            "range spike": {**BASE, "spike_metric": "range"},
            "range + below-spike": {**BASE, "spike_metric": "range", "require_below_spike": True},
            "trail 1xATR": {**BASE, "trail_atr": 1.0},
            "gap 20 bars": {**BASE, "min_bars_since_spike": 20},
            "tight stop 0.5": {**BASE, "stop_atr": 0.5},
            "hold 3": {**BASE, "max_hold": 3},
        }
        for window, data, tag in ((f"2024-09 -> {SELECT_END}", sel, "IN"),
                                  (f"{SELECT_END} -> today", oos, "OUT")):
            print(f"\nFINALISTS -- {window}  [{tag}-SAMPLE]")
            print(ROW)
            print("  " + "-" * (len(ROW) - 2))
            for label, p in finalists.items():
                for fam in ("Boom", "Crash"):
                    st = score(book(data, p, fam))
                    line(label, fam, st)
                    results[f"finalist:{label}|{fam}|{tag}"] = st
                print()

        print("\nCONTROL on the out-of-sample window -- entry shifted 5 bars later")
        print(ROW)
        print("  " + "-" * (len(ROW) - 2))
        for label, p in finalists.items():
            for fam in ("Boom", "Crash"):
                line(f"{label} +5", fam, score(book(oos, p, fam, placebo=5)))

    (OUT / "optimise.json").write_text(json.dumps(results, indent=1, default=float))
    print(f"\n-> {OUT / 'optimise.json'}")


if __name__ == "__main__":
    main()
