"""Compact summary of scalp_bt.py outputs: per file, the setting chosen on the
tuning period and how it did on the unseen period, plus how many of ALL the
settings made money unseen (a strategy, not a lucky setting, shows up there)."""
import glob
import json
import statistics as st
import sys

MIN_TUNE = int(sys.argv[2]) if len(sys.argv) > 2 else 30
for f in sorted(glob.glob(sys.argv[1] if len(sys.argv) > 1 else "/opt/algoedge/research_out/*.txt")):
    rows, head, done = [], {}, False
    for line in open(f):
        if line.startswith("RES|"):
            rows.append(json.loads(line[4:]))
        elif line.startswith("HEAD|"):
            head = json.loads(line[5:])
        elif line.startswith("DONE|"):
            done = True
    name = f.split("/")[-1][:-4]
    if not rows:
        print(f"{name}: {'no settings' if done else 'running'}")
        continue
    ok = [r for r in rows if (r["tune"].get("trades") or 0) >= MIN_TUNE]
    un = [r["unseen"].get("avg_r") for r in rows if r["unseen"].get("trades")]
    pos = sum(1 for r in rows if (r["unseen"].get("net_r") or 0) > 0)
    line = f"{name} [{len(rows)} settings{'' if done else ', running'}] unseen>0: {pos}/{len(rows)}"
    if un:
        line += f" median unseen avgR {st.median(un):+.3f}"
    print(line)
    for r in sorted(ok, key=lambda r: -(r["tune"]["avg_r"] or -9))[:3]:
        t, u = r["tune"], r["unseen"]
        print(f"   {json.dumps(r['p'], separators=(',', ':'))[:90]}")
        print(f"     tune n={t['trades']} {t['per_day']}/d wr={t['win_rate']} avgR={t['avg_r']:+} net={t['net_r']:+} dd={t['max_dd_r']} cost={t['cost_share']}"
              f" | unseen n={u.get('trades')} wr={u.get('win_rate')} avgR={u.get('avg_r')} net={u.get('net_r')} dd={u.get('max_dd_r')}")
