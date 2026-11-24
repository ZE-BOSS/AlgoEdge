"""Monthly R and first-half / second-half split from copytrade_backtest.py --json output.

    python scripts/ct_monthly.py out.json [variant-substring ...]
"""
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone

d = json.load(open(sys.argv[1]))
want = sys.argv[2:] or ["AlgoEdge copier", "auto-BE after TP1, pending"]
for name, rows in d.items():
    if not any(w in name for w in want):
        continue
    f = [r for r in rows if r.get("status") == "FILLED"]
    by = defaultdict(float)
    for r in f:
        by[datetime.fromtimestamp(r["t"], timezone.utc).strftime("%Y-%m")] += r["R"]
    rs = [r["R"] for r in sorted(f, key=lambda r: r["t"])]
    half = len(rs) // 2
    eq = peak = dd = 0.0
    for x in rs:
        eq += x; peak = max(peak, eq); dd = max(dd, peak - eq)
    pos = sum(1 for v in by.values() if v > 0)
    print(f"{name[:60]}\n  filled {len(rs)}  total {sum(rs):+.1f}R  1st half {sum(rs[:half]):+.1f}R  2nd half {sum(rs[half:]):+.1f}R  "
          f"maxDD {dd:.1f}R  months up {pos}/{len(by)}")
    print("  " + "  ".join(f"{k[2:]} {v:+.1f}" for k, v in sorted(by.items())))
