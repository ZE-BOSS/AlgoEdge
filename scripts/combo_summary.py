"""
scripts/combo_summary.py - one compact block per candidate file of a combo_eval.py JSON.

    python combo_summary.py trend_combos.json [--top 3] [--effects] [--match tb_]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def s(x: dict | None) -> str:
    if not x or not x.get("n"):
        return "0"
    return f"{x['n']}/{x['win']}/{x['exp']:+.3f}/pf{x['pf']}/dd{x.get('dd')}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--effects", action="store_true")
    ap.add_argument("--match", default="")
    a = ap.parse_args()
    for r in json.loads(Path(a.file).read_text()):
        name = r["file"].replace("cand_", "").replace(".parquet", "")
        if a.match and a.match not in name:
            continue
        base = " ".join(f"{k}:{s(v)}" for k, v in (r.get("baseline") or {}).items() if v)
        print(f"## {name} cand {r['candidates']} | base {base}")
        for x in r["robust"][:a.top]:
            m = x.get("months") or {}
            neg = sum(1 for v in m.values() if v < 0)
            print(f"  {x['candle']}/{x['flow']}/{x['context']}/{x['session']}->{x['target']} "
                  f"TR {s(x['train'])} TE {s(x['test'])} FULL {s(x['full'])} negM {neg}/{len(m)}")
        if a.effects:
            eff = sorted(r["effects"].items(), key=lambda kv: -kv[1]["avg_exp_change_r"])
            print("  EFF " + " | ".join(f"{k} {v['avg_exp_change_r']:+.3f}({v['share_better']})" for k, v in eff))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
