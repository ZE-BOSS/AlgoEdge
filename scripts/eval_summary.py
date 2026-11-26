"""
scripts/eval_summary.py - compact text summary of of_zones.py / pa_study.py eval JSON files.

    python eval_summary.py /opt/algoedge/research_data/zones/eval_*.json [--alone] [--full]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def s(x: dict) -> str:
    if not x or not x.get("trades"):
        return "0tr"
    return f"{x['trades']}tr w{x.get('win_rate')} {x.get('net_r')}R pf{x.get('pf')} dd{x.get('max_dd_r')}"


def main() -> int:
    files = [a for a in sys.argv[1:] if not a.startswith("--")]
    show_alone, show_full = "--alone" in sys.argv, "--full" in sys.argv
    for f in files:
        ev = json.loads(Path(f).read_text())
        name = Path(f).stem.replace("eval_", "")
        best = ev.get("best", {})
        tg = best.get("target")
        zo = ev.get("zone_only", {}).get(tg, {})
        print(f"## {name}  cand {ev.get('candidates')}  base[{tg}] train {s(zo.get('train'))} | test {s(zo.get('test'))}")
        for p in ev.get("forward", []):
            print(f"   +{p['add']:24s}->{p['target']:5s} train {s(p['train'])} | test {s(p['test'])}")
        for k, v in ev.get("remove_one", {}).items():
            print(f"   -{k:24s}         train {s(v['train'])} | test {s(v['test'])}")
        if show_full:
            bf = ev.get("best_full", {})
            print(f"   FULL {s(bf)} streak {bf.get('win_streak')}/{bf.get('loss_streak')} lm {bf.get('losing_months')} "
                  f"months {bf.get('months_pct_at_1pct_risk')}")
        if show_alone:
            rows = []
            for k, d in ev.get("alone", {}).items():
                for t, v in d.items():
                    te, tr = v.get("test", {}), v.get("train", {})
                    if te.get("trades", 0) >= 15 and tr.get("trades", 0) >= 15:
                        rows.append((min(tr.get("exp_r", -9), te.get("exp_r", -9)), k, t, tr, te))
            for sc, k, t, tr, te in sorted(rows, reverse=True)[:8]:
                print(f"   alone {k:24s}->{t:5s} train {s(tr)} | test {s(te)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
