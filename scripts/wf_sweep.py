"""Clean walk-forward on the full sweep: pick a portfolio from ALL pairs using
only Nov-May (first half) results, then report how those picks did Jun-Nov.
Uses the same caps as analyze_deep.select (no correlation rule: the sweep keeps
no trade lists)."""
import glob
import json
import sys

d = sys.argv[1]
cap = int(sys.argv[2]) if len(sys.argv) > 2 else 10
FAM = [{"US Tech 100", "Wall Street 30", "US SP 500", "US Small Cap 2000"},
       {"Germany 40", "France 40", "Europe 50", "Netherlands 25", "UK 100", "Swiss 20", "Spain 35"},
       {"XAUUSD", "XAGUSD", "XPTUSD", "XPDUSD"}]


def fam(s):
    return next((i for i, f in enumerate(FAM) if s in f), None)


rows = []
for f in glob.glob(f"{d}/*__*.json"):
    r = json.load(open(f))
    if r.get("error"):
        continue
    h1, h2 = r.get("h1") or {}, r.get("h2") or {}
    if h1.get("trades", 0) < 10 or (h1.get("net") or 0) <= 0 or (h1.get("pf") or 0) < 1.2:
        continue
    rows.append(r)
rows.sort(key=lambda r: -(r["h1"].get("return_over_dd") or 0))
out = []
for r in rows:
    if len(out) >= cap:
        break
    if any(c["symbol"] == r["symbol"] for c in out):
        continue
    f = fam(r["symbol"])
    if f is not None and any(c["strategy"] == r["strategy"] and fam(c["symbol"]) == f for c in out):
        continue
    if r["strategy"] == "RQ_TMeanReversion_v1" and any(c["strategy"] == r["strategy"] for c in out):
        continue
    if sum(c["strategy"] == r["strategy"] for c in out) >= 2:
        continue
    out.append(r)
print(f"{len(rows)} of all sweep pairs qualified on the first half alone (>=10 trades, profitable, PF>=1.2)")
tot1 = tot2 = 0
for r in out:
    a, b = r["h1"]["net"], (r["h2"].get("net") or 0)
    tot1 += a
    tot2 += b
    print(f"{r['strategy']} | {r['symbol']} | h1 {a:+.0f} | h2 {b:+.0f} | h2 trades {r['h2'].get('trades', 0)}")
print(f"TOTAL first half {tot1:+.0f} | second half {tot2:+.0f} | {sum((r['h2'].get('net') or 0) > 0 for r in out)}/{len(out)} profitable in the second half")
# baseline: every qualifying pair, equal weight
allb = [(r["h2"].get("net") or 0) for r in rows]
print(f"ALL {len(rows)} first-half qualifiers: second half average {sum(allb) / len(allb):+.0f} per pair, "
      f"{sum(x > 0 for x in allb)}/{len(allb)} profitable")
