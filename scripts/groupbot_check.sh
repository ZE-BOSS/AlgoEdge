#!/usr/bin/env bash
# Checks the group bot from the server: who it is, and which chats it has
# been added to. Reads GROUP_BOT_TOKEN from the live instance; never prints it.
# Does not consume updates (no offset is sent).
set -uo pipefail
T="$(grep -E '^GROUP_BOT_TOKEN=' /opt/algoedge/live/instance.env /opt/algoedge/live/app/.env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"'"'"' ')"
[ -n "$T" ] || { echo "NO GROUP_BOT_TOKEN on live"; exit 1; }
api() { curl -s -m 20 "https://api.telegram.org/bot$T/$1"; }
echo "== getMe"; api getMe | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d.get("ok"),d.get("description",""),(d.get("result") or {}).get("username",""))'
echo "== webhook"; api getWebhookInfo | python3 -c 'import sys,json;d=json.load(sys.stdin);r=d.get("result") or {};print(d.get("ok"),"url set" if r.get("url") else "no webhook",r.get("pending_update_count"))'
echo "== chats"
api 'getUpdates?allowed_updates=%5B%22my_chat_member%22%2C%22message%22%5D' | python3 -c '
import sys,json
d=json.load(sys.stdin); print("ok",d.get("ok"),d.get("description",""),"updates",len(d.get("result") or []))
seen={}
for u in d.get("result") or []:
    m=u.get("my_chat_member")
    if m: seen[m["chat"]["id"]]=(m["chat"].get("title"),m["chat"].get("type"),m["new_chat_member"].get("status"),
                                {k:v for k,v in m["new_chat_member"].items() if k.startswith("can_") and v})
    msg=u.get("message") or {}
    if msg.get("chat",{}).get("type")=="private": print("DM from",msg["from"].get("id"),msg["from"].get("username"),repr((msg.get("text") or "")[:40]))
for k,v in seen.items(): print("CHAT",k,*v)
'
for c in -1003622850842 -5228203057; do
  echo "== chat $c"; api "getChat?chat_id=$c" | python3 -c 'import sys,json;d=json.load(sys.stdin);r=d.get("result") or {};print(d.get("ok"),d.get("description",""),r.get("title",""),r.get("type",""))'
done
