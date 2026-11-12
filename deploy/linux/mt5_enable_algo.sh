#!/usr/bin/env bash
# deploy/linux/mt5_enable_algo.sh - switch "Algo Trading" back on in one
# instance's MT5 terminal, and stop MT5 switching it off on an account change.
#
#   curl -fsSL https://raw.githubusercontent.com/ZE-BOSS/AlgoEdge/dev/deploy/linux/mt5_enable_algo.sh | bash -s live
#
# Why: MT5's "Disable algorithmic trading when the account has been changed" is
# on by default. When live's login moved from Deriv to Exness (11 Nov) MT5 turned
# Algo Trading off, and every order the copier sent was refused with
# "10027 AutoTrading disabled by client".
#
# What it does: stops that instance's API and MT5 terminal(s), sets in the
# terminal's Config/common.ini [Experts]: Enabled=1 (Algo Trading on), Account=0
# and Profile=0 (don't switch it off on an account / profile change), then starts
# the terminal, the bridge and the API again. Open positions are not touched -
# their stop loss and take profit live on the broker's server.
set -euo pipefail

INST="${1:-}"
case "$INST" in demo) PORT=18811;; live) PORT=18812;; flip) PORT=18813;; *) echo "usage: $0 demo|live|flip"; exit 1;; esac
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }
PREFIX="/opt/algoedge/$INST/wine"
# the install folder's Config (a /portable terminal) and any AppData data folder
mapfile -t CFGS < <(find "$PREFIX/drive_c" -iname common.ini -ipath '*config*' 2>/dev/null)
[ "${#CFGS[@]}" -gt 0 ] || { echo "no MT5 common.ini under $PREFIX"; exit 1; }

pids() {   # every terminal64.exe running in this instance's Wine prefix
    for p in $(pgrep -f terminal64.exe || true); do
        tr '\0' '\n' < "/proc/$p/environ" 2>/dev/null | grep -qx "WINEPREFIX=$PREFIX" && echo "$p"
    done
}

echo "== stopping the $INST API and MT5 terminal"
systemctl stop "algoedge-api@$INST"
systemctl stop "algoedge-mt5@$INST" || true
for p in $(pids); do kill "$p" 2>/dev/null || true; done   # also one the bridge started itself (/portable)
for _ in $(seq 1 30); do [ -z "$(pids)" ] && break; sleep 1; done
for p in $(pids); do kill -9 "$p" 2>/dev/null || true; done
sleep 2

edit_cfg() {
python3 - "$1" <<'EOF'
import re
import sys
p = sys.argv[1]
raw = open(p, "rb").read()
bom = raw.startswith(b"\xff\xfe")
t = raw.decode("utf-16")
want = {"Enabled": "1", "Account": "0", "Profile": "0"}
m = re.search(r"\[Experts\][^\[]*", t)
if m:
    sec = m.group(0)
    for k, v in want.items():
        sec = re.sub(rf"(?m)^{k}=.*$", f"{k}={v}", sec) if re.search(rf"(?m)^{k}=", sec) \
            else sec.replace("[Experts]", f"[Experts]\r\n{k}={v}", 1)
    t = t[:m.start()] + sec + t[m.end():]
else:
    t += "\r\n[Experts]\r\n" + "".join(f"{k}={v}\r\n" for k, v in want.items())
open(p, "wb").write((b"\xff\xfe" if bom else b"") + t.encode("utf-16-le"))
EOF
}

for CFG in "${CFGS[@]}"; do
    echo "== $CFG"
    cp "$CFG" "$CFG.bak.$(date +%Y%m%d%H%M%S)"
    edit_cfg "$CFG"
    iconv -f utf-16le -t utf-8 "$CFG" | tr -d '\r' | sed -n '/^\[Experts\]/,/^Profile/p'
done

echo "== starting the terminal, the bridge and the API"
systemctl start "algoedge-mt5@$INST"
sleep 15
systemctl restart "algoedge-bridge@$INST"
for _ in $(seq 1 60); do ss -ltn 2>/dev/null | grep -q ":$PORT " && break; sleep 2; done
systemctl start "algoedge-api@$INST"
sleep 20
journalctl -u "algoedge-api@$INST" --since "-1 min" --no-pager | grep -E "MT5 Connected|mock mode|connect failed" | tail -n 2 || true
echo "== done. In the app the $INST account should trade again; an order refused with 10027 means Algo Trading is still off."
