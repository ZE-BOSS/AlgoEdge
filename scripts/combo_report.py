"""scripts/combo_report.py - terse text of combo_eval.py JSON files (for reading over a console)."""
import json
import sys
from pathlib import Path


def s(x):
    return f"{x['n']}/{x.get('win')}/{x.get('exp')}/{x.get('pf')}/{x.get('dd')}" if x and x.get("n") else "0"


top = int(next((a.split("=")[1] for a in sys.argv if a.startswith("--top=")), 5))
for f in [a for a in sys.argv[1:] if not a.startswith("--")]:
    for r in json.loads(Path(f).read_text()):
        name = r["file"].replace("cand_", "").replace(".parquet", "")
        b = " ".join(f"{k}:{s(v)}" for k, v in (r.get("baseline") or {}).items() if v)
        print(f"## {name} base(n/win/exp/pf/dd) {b}")
        if "--effects" in sys.argv:
            eff = sorted(r["effects"].items(), key=lambda kv: kv[1]["avg_exp_change_r"], reverse=True)
            print("   EFF " + " | ".join(f"{k} {v['avg_win_change']:+.3f}w {v['avg_exp_change_r']:+.3f}R {v['share_better']}"
                                       for k, v in eff))
        for x in r["robust"][:top]:
            fu = x["full"]
            mo = " ".join(f"{k[5:]}:{v:+.1f}" for k, v in sorted(x.get("months", {}).items()))
            print(f"   {x['candle']}/{x['flow']}/{x['context']}/{x['session']}->{x['target']} "
                  f"TR {s(x['train'])} TE {s(x['test'])} FULL {s(fu)} M {mo}")
