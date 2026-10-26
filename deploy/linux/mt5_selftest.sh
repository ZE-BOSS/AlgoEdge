#!/usr/bin/env bash
# deploy/linux/mt5_selftest.sh - check that every MT5 call the app makes works
# on this server, through the same bridge and code the bot uses. Run ON the VPS:
#
#   bash /opt/algoedge/src/deploy/linux/mt5_selftest.sh demo            # read-only checks
#   bash /opt/algoedge/src/deploy/linux/mt5_selftest.sh demo --trade    # + one full trade (DEMO only, minimum lot)
#   bash /opt/algoedge/src/deploy/linux/mt5_selftest.sh live            # read-only checks on live
#
# Optional symbol (default "Boom 900 Index"):  ... demo --trade "Volatility 75 Index"
set -euo pipefail
NAME="${1:?which instance? demo or live}"; shift
BASE="/opt/algoedge/$NAME"
[ -f "$BASE/instance.env" ] || { echo "no instance '$NAME'"; exit 1; }
set -a; . "$BASE/instance.env"; set +a
cd "$BASE/app"
exec "$BASE/venv/bin/python" "$BASE/app/deploy/linux/mt5_selftest.py" "$@"
