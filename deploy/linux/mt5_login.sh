#!/usr/bin/env bash
# deploy/linux/mt5_login.sh - log an instance's MT5 terminal into its account,
# from the server itself (no VNC needed). Run ON the VPS:
#
#   bash /opt/algoedge/src/deploy/linux/mt5_login.sh demo
#   bash /opt/algoedge/src/deploy/linux/mt5_login.sh live
#
# It goes through the instance's own MT5 bridge, so it reaches exactly that
# instance's terminal. The password is typed at a hidden prompt and passed
# straight to MetaTrader5.login(): it is not echoed, logged or written anywhere.
# MT5 remembers the login itself, as when "Save password" is ticked.
set -euo pipefail

NAME="${1:?which instance? demo or live}"
BASE="/opt/algoedge/$NAME"
[ -f "$BASE/instance.env" ] || { echo "no instance '$NAME' (missing $BASE/instance.env)"; exit 1; }

case "$NAME" in
    demo) DEF_ACCOUNT=32283616;  DEF_SERVER="Deriv-Demo" ;;
    live) DEF_ACCOUNT=140747450; DEF_SERVER="DerivSVG-Server-03" ;;
    *)    DEF_ACCOUNT="";        DEF_SERVER="" ;;
esac

read -r -p "MT5 account [$DEF_ACCOUNT]: " ACCOUNT; ACCOUNT="${ACCOUNT:-$DEF_ACCOUNT}"
read -r -p "MT5 server  [$DEF_SERVER]: " SERVER;  SERVER="${SERVER:-$DEF_SERVER}"

set -a; . "$BASE/instance.env"; set +a
cd "$BASE/app"
ACCOUNT="$ACCOUNT" SERVER="$SERVER" "$BASE/venv/bin/python" - <<'PY'
import getpass, os, sys
import backend                      # installs the MT5 bridge for this instance
import MetaTrader5 as mt5

account, server = int(os.environ["ACCOUNT"]), os.environ["SERVER"]
if not mt5.initialize():
    sys.exit(f"Cannot reach this instance's MT5 terminal: {mt5.last_error()}")
pw = getpass.getpass(f"Password for {account} on {server} (hidden): ")
ok = mt5.login(account, password=pw, server=server)
del pw
if not ok:
    sys.exit(f"Login FAILED: {mt5.last_error()} - check the password (the MT5 TRADING "
             f"password from Deriv's Trader's Hub) and the server name.")
a = mt5.account_info()
print(f"Logged in: account {a.login} on {a.server}, balance {a.balance:,.2f} {a.currency}")
PY
echo "Restarting the $NAME API so the bot picks the account up..."
systemctl restart "algoedge-api@$NAME"
echo "Done. Start the bot again from the admin site (Dashboard -> Start bot)."
