"""One supertrend-stack rule, fixed 1 oz per position, on a date range - to compare with TradingView.

    python scripts/stack_check.py BARS.npz --tf 5 --from 2026-09-01 --to 2026-11-09
"""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from trend_scalp import load, resample  # noqa: E402
from trend_stack import Prep, simulate  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("bars")
ap.add_argument("--tf", type=int, default=5)
ap.add_argument("--frm", "--from", dest="frm", required=True)
ap.add_argument("--to", required=True)
ap.add_argument("--htf", type=int, default=15)
ap.add_argument("--mult", type=float, default=3.0)
ap.add_argument("--add", type=float, default=1.0)
ap.add_argument("--max", type=int, default=3)
ap.add_argument("--session", action="store_true")
a = ap.parse_args()
b = load(a.bars)
if a.tf > 1:
    b = resample(b, a.tf)
P = Prep(b, a.htf)
ts = lambda s: datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()  # noqa: E731
lo, hi = int(np.searchsorted(b["time"], ts(a.frm))), int(np.searchsorted(b["time"], ts(a.to)))
bal, dd, tr = simulate(P, lo, hi, "supertrend", 10, a.mult, a.add, a.max, a.session, 1.0, 100000.0, fixed=0.01)
tr = tr / 1.0          # 0.01 lot = 1 oz -> $ per position = $ per oz
w = tr[tr > 0]
pf = w.sum() / -tr[tr < 0].sum() if (tr < 0).any() else float("inf")
print(f"{a.frm}..{a.to} {a.tf}m: {len(tr)} positions, win {100*len(w)/max(len(tr),1):.1f}%, PF {pf:.3f}, "
      f"net ${tr.sum():+.2f} per oz-position, avg win {w.mean() if len(w) else 0:.2f}, "
      f"avg loss {tr[tr<0].mean() if (tr<0).any() else 0:.2f}")
