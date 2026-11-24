#!/usr/bin/env bash
# After a deploy: are the APIs up, did the new tables appear, what is the group bot doing.
set -uo pipefail
tail -2 /var/log/algoedge-update-manual.log
for p in demo:8001 live:8002 flip:8003; do
  n=${p%%:*}; port=${p##*:}
  printf "%-5s api=%-8s health=%s\n" "$n" "$(systemctl is-active algoedge-api@$n)" \
    "$(curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:$port/api/health)"
done
echo "== live tables"
sqlite3 /opt/algoedge/live/app/algoedge.db ".tables" 2>/dev/null | tr -s ' ' '\n' | grep -E "^(signal_|group_|llm_|bot_kv|vvip)" | tr '\n' ' '; echo
echo "== group bot / errors since start (live)"
journalctl -u algoedge-api@live --since "-10 min" --no-pager -o cat 2>/dev/null \
  | grep -E "GROUP|Group bot|VVIP|ORDERFLOW|Traceback|ERROR|Error" | grep -v "GROUP_BOT_TOKEN=" | tail -15 \
  | sed -E 's/bot[0-9]+:[A-Za-z0-9_-]+/bot***/g'
