#!/usr/bin/env bash
# deploy/linux/add_client.sh - give a VVIP client their own trading instance.
#
#   sudo bash /opt/algoedge/src/deploy/linux/add_client.sh <client_id>
#   sudo bash /opt/algoedge/src/deploy/linux/add_client.sh --remove <client_id>
#
# <client_id> is the id on the admin site's Signal Groups -> VVIP tab.
#
# What it does (all secrets stay inside one Python process - nothing is printed):
#   1. picks free ports and a Redis database, checks the server has room;
#   2. builds the instance with add_instance.sh (own MT5 terminal, bridge, venv);
#   3. logs the client's MT5 in with the password they entered on the portal,
#      copies live's strategy slots (symbols renamed for their broker) at the
#      client's risk, and makes it a follower of live: it trades only what live
#      trades, sized from the client's own balance;
#   4. turns Algo Trading on in the terminal, starts the API (its guard starts
#      the bot and enforces the daily-loss and drawdown stops) and marks the
#      client ACTIVE.
# --remove stops the instance's services (files are kept), wipes the stored
# password and marks the client STOPPED. Open positions are not touched.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run as root (sudo)"; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
LIVE=/opt/algoedge/live
LPY="$LIVE/venv/bin/python"
cd "$LIVE/app"

if [ "${1:-}" = "--remove" ]; then
    CID="${2:?client id}"
    NAME="$(sudo -u algoedge "$LPY" -m backend.groups.provision remove "$CID")"
    if [ -n "$NAME" ]; then
        systemctl disable --now "algoedge-api@$NAME" "algoedge-bridge@$NAME" "algoedge-mt5@$NAME" "algoedge-xvfb@$NAME" 2>/dev/null || true
        rm -f "/opt/algoedge/shared/vvip/$NAME.control.json"
    fi
    echo "== client $CID removed${NAME:+ (instance $NAME stopped; files kept in /opt/algoedge/$NAME)}"
    exit 0
fi

CID="${1:?client id}"
AVAIL_MB=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)
[ "$AVAIL_MB" -ge 1200 ] || { echo "only ${AVAIL_MB} MB of memory free - a client instance needs about 1.2 GB. Upgrade the VPS first."; exit 1; }

read -r NAME PORT BRIDGE REDIS_DB < <(sudo -u algoedge "$LPY" -m backend.groups.provision plan "$CID")
echo "== client $CID -> instance $NAME (API $PORT, bridge $BRIDGE, Redis db $REDIS_DB)"
sudo -u algoedge "$LPY" -m backend.groups.provision finish "$CID" "$NAME" PROVISIONING

mkdir -p /opt/algoedge/shared/vvip /opt/algoedge/shared/signal_sync
chown algoedge:algoedge /opt/algoedge/shared /opt/algoedge/shared/vvip /opt/algoedge/shared/signal_sync

# 2. the instance (stops before the API on a first run: the .env is new)
bash "$HERE/add_instance.sh" "$NAME" "$PORT" "$BRIDGE" "$REDIS_DB"

# 3. log in, copy the slots, write .env and the database
BASE="/opt/algoedge/$NAME"
systemctl stop "algoedge-api@$NAME" 2>/dev/null || true
( cd "$BASE/app" && set -a && . "$BASE/instance.env" && set +a && \
  sudo -u algoedge -E env HOME=/home/algoedge "$BASE/venv/bin/python" -m backend.groups.provision configure "$CID" "$NAME" "$REDIS_DB" ) \
  || { sudo -u algoedge "$LPY" -m backend.groups.provision finish "$CID" "$NAME" NEW; echo "!! provisioning stopped - client set back to NEW"; exit 1; }

# 4. Algo Trading on, API up (the guard starts the bot), client ACTIVE
bash "$HERE/mt5_enable_algo.sh" "$NAME"
systemctl enable --now "algoedge-api@$NAME"
sudo -u algoedge "$LPY" -m backend.groups.provision finish "$CID" "$NAME" ACTIVE
sleep 45
echo "== status"
cat "/opt/algoedge/shared/vvip/$NAME.status.json" 2>/dev/null | python3 -m json.tool 2>/dev/null \
    || echo "(no status yet - the guard writes it within a minute: journalctl -u algoedge-api@$NAME -f)"
