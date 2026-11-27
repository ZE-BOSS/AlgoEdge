"""
scripts/combo_null.py - how good does the best combo look when the confluences mean nothing?

Shuffles the confluence columns across candidates (outcomes, times and sides stay), reruns the
combo_eval grid, and prints the top "robust" combo's worse-half expectancy - the score combo_eval
ranks by. Done a few times per file, that is the bar a real combo has to clear: with thousands of
combinations, the best of pure noise can look good.

    python combo_null.py trend/cand_rb_gold.parquet ... --runs 3 --min-n 30
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import combo_eval as ce  # noqa: E402

KEEP = {"t", "entry_t", "side", "setup"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--min-n", type=int, default=30)
    a = ap.parse_args()
    out = []
    for f in a.files:
        real = ce.run_file(Path(f), a.min_n)
        top = real["robust"][0] if real["robust"] else None
        line = {"file": Path(f).name, "real": top and top["score"], "real_pf": top and top["full"]["pf"], "null": []}
        d = pd.read_parquet(f)
        feat = [c for c in d.columns if not c.startswith(("r_", "x_")) and c not in KEEP]
        rng = np.random.default_rng(7)
        for _ in range(a.runs):
            p = d.copy()
            perm = rng.permutation(len(p))
            p[feat] = d[feat].iloc[perm].to_numpy()
            with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
                p.to_parquet(tmp.name)
                r = ce.run_file(Path(tmp.name), a.min_n)
            nt = r["robust"][0] if r["robust"] else None
            line["null"].append(nt and (round(nt["score"], 3), nt["full"]["pf"]))
        print(json.dumps(line), flush=True)
        out.append(line)
    print("NULL_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
