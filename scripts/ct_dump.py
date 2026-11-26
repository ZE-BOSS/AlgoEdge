"""Dump recent copy-trading OPENs (and what was placed) from an instance DB, compactly.
python ct_dump.py <db> [days] [kind]   - one line per signal; '=', '?', '&' replaced so a terminal reader can show it."""
import json, sqlite3, sys
db, days = sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 4
kind = sys.argv[3] if len(sys.argv) > 3 else "OPEN"
c = sqlite3.connect(db)
ch = {r[0]: r[1] for r in c.execute("select id, title from copytrade_channels")}
rows = c.execute(
    "select id, channel_id, created_at, kind, status, text, parsed, detail from copytrade_signals "
    "where created_at >= datetime('now', ?) and kind = ? order by created_at", (f"-{days} days", kind)).fetchall()
def clean(s, n):
    s = " / ".join(x.strip() for x in (s or "").splitlines() if x.strip())
    return s.replace("=", ":").replace("?", "¿").replace("&", "+")[:n]
for sid, cid, t, k, st, text, parsed, detail in rows:
    p = json.loads(parsed or "{}")
    orders = c.execute("select kind, status, entry, tp from copytrade_orders where signal_id = ? order by leg", (sid,)).fetchall()
    o = ",".join(f"{a[0][0]}{a[1][:3]}" for a in orders)
    print(f"{t[5:16]}|{clean(ch.get(cid, '?'), 14)}|{st[:4]}|{p.get('order_type', '')[:3]}|e{p.get('entry')}|{o}|{clean(text, 150)}")
print("rows", len(rows))
