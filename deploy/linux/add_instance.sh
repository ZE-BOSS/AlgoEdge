#!/usr/bin/env bash
# deploy/linux/add_instance.sh - one AlgoEdge backend + its own MT5 terminal.
#
#   sudo bash add_instance.sh <name> <api_port> <bridge_port> <redis_db>
#   sudo bash add_instance.sh demo 8001 18811 1
#   sudo bash add_instance.sh live 8002 18812 2
#
# Each instance is fully separate - its own copy of the code (so logs, the
# SQLite database and state files never mix), its own Python venv, its own
# Wine prefix holding its own MT5 terminal and bridge, its own Redis database
# number and its own port. Restarting or breaking one never touches another.
#
# Re-run with the same arguments to UPDATE the code from /opt/algoedge/src;
# .env, the database and the MT5 login are kept.
set -euo pipefail

[ "$(id -u)" = 0 ] || { echo "run as root (sudo)"; exit 1; }
NAME="${1:?name}"; API_PORT="${2:?api port}"; BRIDGE_PORT="${3:?bridge port}"; REDIS_DB="${4:?redis db number}"
HERE="$(cd "$(dirname "$0")" && pwd)"
BASE="/opt/algoedge/$NAME"
SRC="/opt/algoedge/src"
DL="/opt/algoedge/downloads"
WINEPREFIX="$BASE/wine"
# Its own virtual screen: demo (8001) -> :91, live (8002) -> :92 ...
DISP=":$((90 + API_PORT - 8000))"
# WINEDLLOVERRIDES: no .NET (Mono) / HTML (Gecko) - MT5 needs neither, and Wine
# would otherwise stop on an "install wine-mono?" dialog nobody can see.
as_user() { sudo -u algoedge -H env WINEPREFIX="$WINEPREFIX" WINEARCH=win64 WINEDEBUG=-all DISPLAY="$DISP" WINEDLLOVERRIDES="mscoree=;mshtml=" "$@"; }

[ -f "$SRC/backend/main.py" ] || { echo "no code in $SRC - copy the repo there first"; exit 1; }
# Commands below run as the service user: start them from a folder it can
# read (uv looks for a uv.toml in the current folder - /root is off limits).
cd /opt/algoedge
export UV_NO_CONFIG=1
mkdir -p "$BASE"
chown algoedge:algoedge "$BASE"

echo "== [$NAME] code"
# A zip made on Windows can leave folders without the execute bit Linux needs
# to enter them (rsync: "Permission denied").
chown -R algoedge:algoedge "$SRC"; chmod -R u+rwX,go+rX "$SRC"
sudo -u algoedge rsync -a --delete \
    --exclude venv --exclude venv_win --exclude .venv --exclude node_modules \
    --exclude '.env' --exclude '*.db' --exclude logs --exclude errors --exclude debug \
    --exclude 'frontend' --exclude 'landing' --exclude 'investor-web' --exclude 'mobile' \
    --exclude 'research/*/cache' --exclude '__pycache__' \
    "$SRC/" "$BASE/app/"

echo "== [$NAME] Python venv"
# Python 3.12 through uv: the version the backend is pinned and tested on,
# independent of the distro's python3.
export UV_PYTHON_INSTALL_DIR=/opt/algoedge/python UV_CACHE_DIR=/opt/algoedge/.uv-cache
mkdir -p "$UV_PYTHON_INSTALL_DIR" "$UV_CACHE_DIR"; chown algoedge:algoedge "$UV_PYTHON_INSTALL_DIR" "$UV_CACHE_DIR"
[ -x "$BASE/venv/bin/python" ] || sudo -u algoedge -H env UV_NO_CONFIG=1 UV_PYTHON_INSTALL_DIR="$UV_PYTHON_INSTALL_DIR"     UV_CACHE_DIR="$UV_CACHE_DIR" uv venv --python 3.12 "$BASE/venv"
sudo -u algoedge -H env UV_NO_CONFIG=1 UV_CACHE_DIR="$UV_CACHE_DIR" VIRTUAL_ENV="$BASE/venv"     uv pip install -q -r "$BASE/app/requirements.txt"

install -m644 "$HERE"/units/*.service /etc/systemd/system/
[ -f "$BASE/instance.env" ] || { echo "DISPLAY=$DISP" > "$BASE/instance.env"; chown algoedge:algoedge "$BASE/instance.env"; }
systemctl daemon-reload
systemctl enable --now "algoedge-xvfb@$NAME"
sleep 2
echo "== [$NAME] Wine prefix, Windows Python + MetaTrader5 package, MT5 terminal"
if [ ! -f "$WINEPREFIX/drive_c/windows/system32/kernel32.dll" ]; then
    as_user wineboot -i
    # MetaQuotes' own Linux installer sets this before installing MT5.
    # Windows 11: Wine Staging's fix for MT5's anti-debugger check applies only then.
    as_user wine reg add 'HKEY_CURRENT_USER\Software\Wine' /v Version /t REG_SZ /d win11 /f
    # MT5 also treats Wine's crash debugger (winedbg, registered here) as a debugger.
    as_user wine reg delete 'HKLM\Software\Microsoft\Windows NT\CurrentVersion\AeDebug' /v Debugger /f || true
fi
PY="$WINEPREFIX/drive_c/Python311"
if [ ! -f "$PY/python.exe" ]; then
    # The EMBEDDABLE Windows Python (a zip): the full installer is an MSI that
    # is unreliable under Wine. It ignores site-packages until `import site`
    # is enabled in its ._pth file.
    sudo -u algoedge mkdir -p "$PY"
    sudo -u algoedge unzip -q -o "$DL/python-3.11.9-embed-amd64.zip" -d "$PY"
    sudo -u algoedge sed -i 's/^#import site/import site/' "$PY/python311._pth"
    sudo -u algoedge cp "$DL/get-pip.py" "$PY/get-pip.py"
    as_user wine 'C:\Python311\python.exe' 'C:\Python311\get-pip.py' -q
fi
# numpy 1.26, not 2.x: numpy 2 calls ucrtbase.dll!crealf, which Wine 10 does not
# implement - the bridge aborts at import. Arrays cross the bridge as raw bytes
# + dtype, so this side's numpy need not match the backend's.
# The NEWEST MetaTrader5 package (the backend's 5.0.5120 pin is a Windows
# Application Control workaround on the workstation; it does not apply here).
as_user wine 'C:\Python311\python.exe' -m pip install -q --upgrade MetaTrader5 'numpy==1.26.4'
if [ ! -f "$WINEPREFIX/drive_c/Program Files/MetaTrader 5/terminal64.exe" ]; then
    as_user wine "$DL/mt5setup.exe" /auto || true
    for _ in $(seq 1 60); do
        [ -f "$WINEPREFIX/drive_c/Program Files/MetaTrader 5/terminal64.exe" ] && break
        sleep 5
    done
fi
[ -f "$WINEPREFIX/drive_c/Program Files/MetaTrader 5/terminal64.exe" ] \
    || { echo "MT5 did not install - see README 'If MT5 does not install'"; exit 1; }

echo "== [$NAME] bridge"
sudo -u algoedge mkdir -p "$WINEPREFIX/drive_c/algoedge"
install -o algoedge -g algoedge -m644 "$HERE/mt5_bridge_server.py" "$WINEPREFIX/drive_c/algoedge/"
if [ ! -s "$BASE/bridge.key" ]; then
    head -c 32 /dev/urandom | base64 | tr -d '\n' > "$BASE/bridge.key"
    chown algoedge:algoedge "$BASE/bridge.key"; chmod 600 "$BASE/bridge.key"
fi
install -o algoedge -g algoedge -m600 "$BASE/bridge.key" "$WINEPREFIX/drive_c/algoedge/bridge.key"

echo "== [$NAME] settings"
cat > "$BASE/instance.env" <<EOF
WINEPREFIX=$WINEPREFIX
DISPLAY=$DISP
API_PORT=$API_PORT
BRIDGE_PORT=$BRIDGE_PORT
ALGOEDGE_MT5_BRIDGE=127.0.0.1:$BRIDGE_PORT
ALGOEDGE_MT5_BRIDGE_KEY_FILE=$BASE/bridge.key
EOF
chown algoedge:algoedge "$BASE/instance.env"; chmod 640 "$BASE/instance.env"
if [ ! -f "$BASE/app/.env" ]; then
    sed -e "s|__NAME__|$NAME|g" -e "s|__API_PORT__|$API_PORT|g" -e "s|__REDIS_DB__|$REDIS_DB|g" \
        "$HERE/instance.env.example" > "$BASE/app/.env"
    chown algoedge:algoedge "$BASE/app/.env"; chmod 600 "$BASE/app/.env"
    NEW_ENV=1
fi

echo "== [$NAME] services"
systemctl daemon-reload
systemctl enable --now "algoedge-mt5@$NAME" "algoedge-bridge@$NAME"
if [ "${NEW_ENV:-0}" = 1 ]; then
    echo
    echo "!! $BASE/app/.env was created from the template. Fill in the secrets"
    echo "   (copy them from your current server's .env), log MT5 into the account"
    echo "   (README: 'Log MT5 in'), then:  sudo systemctl enable --now algoedge-api@$NAME"
else
    systemctl enable "algoedge-api@$NAME"
    systemctl restart "algoedge-api@$NAME"
    echo "== [$NAME] updated and restarted"
fi
