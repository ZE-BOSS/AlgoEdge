#!/usr/bin/env bash
# Checks the group bot from the server: who it is, and which chats it has
# been added to. Reads GROUP_BOT_TOKEN from the live instance; never prints it.
# Does not consume updates (no offset is sent).
set -uo pipefail
T="$(grep -hE '^(export )?GROUP_BOT_TOKEN=' /opt/algoedge/live/instance.env /opt/algoedge/live/app/.env 2>/dev/null | head -1 | sed -E 's/^(export )?GROUP_BOT_TOKEN=//' | tr -d "\"' \r")"
echo "token: ${#T} chars, id ${T%%:*}"
[ -n "$T" ] || { echo "NO GROUP_BOT_TOKEN on live"; exit 1; }
GROUP_BOT_TOKEN="$T" python3 - <<'PY'
import json, os, urllib.request, urllib.error
T = os.environ["GROUP_BOT_TOKEN"]
def api(method, **q):
    url = f"https://api.telegram.org/bot{T}/{method}"
    if q:
        url += "?" + urllib.parse.urlencode({k: json.dumps(v) if isinstance(v, list) else v for k, v in q.items()})
    try:
        with urllib.request.urlopen(url, timeout=25) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        body = e.read()
        try: return json.loads(body)
        except Exception: return {"ok": False, "description": f"HTTP {e.code} {body[:120]!r}"}
    except Exception as e:
        return {"ok": False, "description": f"{type(e).__name__}: {e}"}
import urllib.parse
me = api("getMe"); print("getMe:", me.get("ok"), me.get("description", ""), (me.get("result") or {}).get("username", ""),
                          (me.get("parameters") or {}))
wh = api("getWebhookInfo"); r = wh.get("result") or {}
print("webhook:", wh.get("ok"), wh.get("description", ""), "SET" if r.get("url") else "none", "pending", r.get("pending_update_count"))
up = api("getUpdates", allowed_updates=["my_chat_member", "message", "callback_query"])
print("updates:", up.get("ok"), up.get("description", ""), len(up.get("result") or []))
seen = {}
for u in up.get("result") or []:
    m = u.get("my_chat_member")
    if m:
        nc = m["new_chat_member"]
        seen[m["chat"]["id"]] = (m["chat"].get("title"), m["chat"].get("type"), nc.get("status"),
                                 sorted(k for k, v in nc.items() if k.startswith("can_") and v))
    msg = u.get("message") or {}
    if msg.get("chat", {}).get("type") == "private":
        print("DM from", msg["from"].get("id"), msg["from"].get("username"), repr((msg.get("text") or "")[:40]))
for k, v in seen.items():
    print("CHAT", k, *v)
for c in (-1003622850842, -5228203057):
    g = api("getChat", chat_id=c); r = g.get("result") or {}
    print("getChat", c, g.get("ok"), g.get("description", ""), r.get("title", ""), r.get("type", ""))
PY
