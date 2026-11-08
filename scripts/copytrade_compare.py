"""
scripts/copytrade_compare.py

Compares two channels from the same provider (e.g. his VIP and his public
channel) over the period both cover: which signals appear in both, and how the
signals each one has but the other doesn't would have traded. A public channel
whose missing signals are mostly the losers suggests losing posts get deleted.

    python scripts/copytrade_compare.py /tmp/gts_vip.tsv data/copytrade/goldtradersunny_public.tsv \
        --bars /tmp/ct_XAUUSD_M1_0.npz
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

from copytrade_backtest import load_bars_npz, load_channel, simulate  # noqa: E402

from backend.copytrade.rules import merge_rules  # noqa: E402


def zone(p):
    lo = p.get("entry_low") or p.get("entry_high")
    hi = p.get("entry_high") or lo
    return lo, hi


def same(a, b, max_dt=2 * 3600, max_gap=3.0):
    if a["p"]["side"] != b["p"]["side"] or abs(a["t"] - b["t"]) > max_dt:
        return False
    (al, ah), (bl, bh) = zone(a["p"]), zone(b["p"])
    if None in (al, ah, bl, bh):
        return False
    return max(al, bl) - min(ah, bh) <= max_gap      # zones overlap or nearly


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("a", help="channel A tsv (e.g. VIP)")
    ap.add_argument("b", help="channel B tsv (e.g. public)")
    ap.add_argument("--bars", required=True, help=".npz bars")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    bars = load_bars_npz(a.bars)
    A, fa = load_channel(a.a)
    B, fb = load_channel(a.b)
    lo = max(A[0]["t"], B[0]["t"], int(bars["time"][0]) + 3600)
    hi = min(A[-1]["t"], B[-1]["t"], int(bars["time"][-1]) - 3600)
    A = [s for s in A if lo <= s["t"] <= hi]
    B = [s for s in B if lo <= s["t"] <= hi]
    rules = merge_rules({"risk_pct": 1.0})

    def run(sigs, fups):
        return {s["id"]: simulate(s, fups.get(s["id"], []), bars, rules, follow=True) for s in sigs}

    ra, rb = run(A, fa), run(B, fb)

    def summary(label, sigs, res):
        f = [res[s["id"]] for s in sigs if res[s["id"]]["status"] == "FILLED"]
        R = [r["R"] for r in f]
        w = sum(1 for x in R if x > 0.02)
        lo_ = sum(1 for x in R if x < -0.02)
        print(f"{label:34} signals {len(sigs):3}  filled {len(f):3}  total {sum(R):+6.2f}R  "
              f"win {w} / loss {lo_} / flat {len(R) - w - lo_}")

    d = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")  # noqa: E731
    print(f"period {d(lo)} .. {d(hi)}  (A = {Path(a.a).name}, B = {Path(a.b).name})")
    a_in_b = [s for s in A if any(same(s, x) for x in B)]
    a_only = [s for s in A if s not in a_in_b]
    b_in_a = [s for s in B if any(same(s, x) for x in A)]
    b_only = [s for s in B if s not in b_in_a]
    summary("A, all", A, ra)
    summary("A, also posted in B", a_in_b, ra)
    summary("A, NOT in B", a_only, ra)
    summary("B, all", B, rb)
    summary("B, also posted in A", b_in_a, rb)
    summary("B, NOT in A", b_only, rb)
    if a.list:
        print("\nA signals not in B:")
        for s in a_only:
            r = ra[s["id"]]
            print(f"  {s['id']} {datetime.fromtimestamp(s['t'], timezone.utc):%m-%d %H:%M} {s['p']['side']:4} "
                  f"{zone(s['p'])} {r['status']:8} {r.get('R', 0):+.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
