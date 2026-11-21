"""Build deep_runs jobs: every sweep pair that made money over the year with
>= 20 trades, plus Sigma take-profit / stop variants on US Tech 100."""
import glob
import json
import sys

sweep, out = sys.argv[1], sys.argv[2]
jobs = []
for f in sorted(glob.glob(f"{sweep}/*__*.json")):
    r = json.load(open(f))
    y = r.get("year") or {}
    if r.get("error") or y.get("trades", 0) < 20 or (y.get("net") or 0) <= 0:
        continue
    tag = f"{r['strategy']}__{r['symbol']}".replace(" ", "_").replace("/", "_")
    jobs.append({"strategy": r["strategy"], "symbol": r["symbol"], "params": {}, "tag": tag,
                 "asset_class": r.get("asset_class", "")})
for sl in (0.0, 0.5, 1.0):
    for tp in (0.0, 1.0, 2.0, 3.0):
        if sl == 0.0 and tp == 0.0:
            continue                      # the default: already in the list
        p = {"tp_r": tp}
        if sl:
            p["sl_pct"] = sl
        jobs.append({"strategy": "RQ_SigmaBreakout_v1", "symbol": "US Tech 100", "params": p,
                     "tag": f"SIGMA_VAR__sl{sl}_tp{tp}", "asset_class": "INDEX"})
json.dump(jobs, open(out, "w"), indent=0)
print(len(jobs), "jobs")
