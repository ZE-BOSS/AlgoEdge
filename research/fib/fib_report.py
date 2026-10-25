"""Per-market tables from results/fib_results.json + win-rate-vs-break-even test."""
import json, math, sys
from pathlib import Path
import numpy as np
R = json.loads((Path(__file__).parent / "results" / "fib_results.json").read_text())

def be_z(trades):
    """Wins vs the win rate a driftless market gives each trade's own geometry, P(win)=1/(1+RR)."""
    if not trades: return float("nan")
    p = np.array([1 / (1 + x["rr"]) for x in trades]); w = np.array([x["how"].startswith("tp") for x in trades])
    return float((w.sum() - p.sum()) / math.sqrt((p * (1 - p)).sum()))

for name in sys.argv[1:]:
    rows = R[name]
    print(f"\n### {name}")
    print(f"{'market':26s} {'trades':>6s} {'/mo':>5s} {'win%':>6s} {'BE z':>6s} {'exp R':>7s} {'PF':>5s} {'t':>6s} {'maxDD R':>7s} {'DD%@1%':>6s} {'H1 exp':>7s} {'H2 exp':>7s} {'missed':>6s} {'ran':>4s}  full-history(n, exp, t)")
    for s, r in rows.items():
        if not r.get("n"):
            print(f"{s:26s} {'0':>6s}"); continue
        fh = r["full_history"]
        print(f"{s:26s} {r['n']:6d} {r['per_month']:5.1f} {r['win']:6.1f} {be_z(r['trades']):+6.2f} {r['exp']:+7.3f} {r['pf']:5.2f} {r['t']:+6.2f} {r['dd_r']:7.1f} {r['dd_pct']:6.1f} "
              f"{(r['first_half'] or 0):+7.3f} {(r['second_half'] or 0):+7.3f} {r['missed']:6d} {r['ran_away']:4d}  ({fh['n']}, {fh['exp']:+.3f}, {fh['t']:+.2f})")
    allt = [x for r in rows.values() for x in r["trades"]]
    print(f"pooled BE z {be_z(allt):+.2f}; markets positive {sum(1 for r in rows.values() if r.get('n') and r['exp']>0)}/{sum(1 for r in rows.values() if r.get('n'))}; "
          f"positive in BOTH halves {sum(1 for r in rows.values() if r.get('n') and (r['first_half'] or 0)>0 and (r['second_half'] or 0)>0)}")
