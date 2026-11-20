"""Rank the sweep: pairs profitable in BOTH halves, by return over drawdown."""
import glob
import json
import sys
from collections import Counter, defaultdict

d = sys.argv[1] if len(sys.argv) > 1 else "/opt/algoedge/research_out/sweep"
min_trades = int(sys.argv[2]) if len(sys.argv) > 2 else 20
top = int(sys.argv[3]) if len(sys.argv) > 3 else 40
rows, errs = [], Counter()
for f in glob.glob(f"{d}/*__*.json"):
    r = json.load(open(f))
    if r.get("error"):
        errs[(r["strategy"], r["error"][:60])] += 1
        continue
    rows.append(r)
per = defaultdict(lambda: Counter())
good = []
for r in rows:
    y, a, b = r["year"], r["h1"], r["h2"]
    per[r["strategy"]]["run"] += 1
    if y.get("trades", 0) == 0:
        per[r["strategy"]]["no trades"] += 1
        continue
    if y["trades"] < min_trades:
        per[r["strategy"]]["too few"] += 1
        continue
    both = (a.get("net") or 0) > 0 and (b.get("net") or 0) > 0
    per[r["strategy"]]["both halves +" if both else ("year +" if y["net"] > 0 else "loses")] += 1
    if both:
        good.append(r)
print(f"pairs done {len(rows) + sum(errs.values())}  ok {len(rows)}  errors {sum(errs.values())}")
print("\nPER STRATEGY (pairs): both halves + | year + only | loses | too few | no trades")
for s, c in sorted(per.items(), key=lambda kv: -kv[1]["both halves +"]):
    print(f"  {s:24s} {c['both halves +']:3d} {c['year +']:3d} {c['loses']:3d} {c['too few']:3d} {c['no trades']:3d}  of {c['run']}")
print(f"\nTOP {top} (both halves profitable, >= {min_trades} trades/yr), by return/drawdown:")
good.sort(key=lambda r: -(r["year"].get("return_over_dd") or 0))
for r in good[:top]:
    y = r["year"]
    print(f"  {r['strategy']:22s} {r['symbol'][:22]:22s} {r.get('asset_class','')[:8]:8s} n={y['trades']:4d} {y['per_month']:5.1f}/mo "
          f"win={y['win_rate']:.2f} avgR={y['avg_r']} net=${y['net']:9.0f} DD={y['max_dd_pct']:5.1f}% ret/DD={y['return_over_dd']} "
          f"h1=${r['h1']['net']:.0f} h2=${r['h2']['net']:.0f}")
if errs:
    print("\nERRORS:")
    for (s, e), n in errs.most_common(15):
        print(f"  {s}: {e} x{n}")
