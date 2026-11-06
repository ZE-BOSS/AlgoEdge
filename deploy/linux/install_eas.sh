#!/usr/bin/env bash
# deploy/linux/install_eas.sh - put the repo's MT5 Expert Advisors into an
# instance's MT5 terminal and compile them with MetaEditor (under Wine).
#
#   sudo bash /opt/algoedge/src/deploy/linux/install_eas.sh live
#   sudo bash /opt/algoedge/src/deploy/linux/install_eas.sh demo
#
# add_instance.sh (and so update.sh) runs this for every instance.
#
# Source: mql5/Experts/*.mq5 in the repo (/opt/algoedge/src).
# Target: <MT5 data folder>/MQL5/Experts/AlgoEdge/ inside /opt/algoedge/<name>/wine
# In MT5 they show under Navigator > Expert Advisors > AlgoEdge (right-click >
# Refresh if the terminal was already open). Attaching an EA to a chart and
# allowing Algo Trading stays a manual step in MT5 (over VNC).
#
# Never fails the caller: a missing data folder or a compile error is reported
# and the script exits 0, so a bad EA cannot stop a deploy.
set -uo pipefail

[ "$(id -u)" = 0 ] || { echo "run as root (sudo)"; exit 1; }
NAME="${1:?name}"
SRC="${SRC:-/opt/algoedge/src}"
BASE="/opt/algoedge/$NAME"
WINEPREFIX="$BASE/wine"
EA_SRC="$SRC/mql5/Experts"
MT5_DIR="$WINEPREFIX/drive_c/Program Files/MetaTrader 5"

shopt -s nullglob
files=("$EA_SRC"/*.mq5)
if [ ${#files[@]} -eq 0 ]; then
    echo "== [$NAME] EAs: none in $EA_SRC"
    exit 0
fi

DISP="$(sed -n 's/^DISPLAY=//p' "$BASE/instance.env" 2>/dev/null | head -1)"
AS=(sudo -u algoedge -H env WINEPREFIX="$WINEPREFIX" WINEARCH=win64 WINEDEBUG=-all
    DISPLAY="${DISP:-:99}" "WINEDLLOVERRIDES=mscoree=;mshtml=")

# MT5 data folders: the per-user one(s), plus the install folder in portable mode.
targets=()
for d in "$WINEPREFIX"/drive_c/users/*/AppData/Roaming/MetaQuotes/Terminal/*/MQL5; do
    targets+=("$d")
done
[ -d "$MT5_DIR/MQL5" ] && targets+=("$MT5_DIR/MQL5")
if [ ${#targets[@]} -eq 0 ]; then
    echo "!! [$NAME] EAs: MT5 has no data folder yet. Start it once"
    echo "   (sudo systemctl start algoedge-mt5@$NAME), wait a minute, then re-run this script."
    exit 0
fi

ME="$MT5_DIR/MetaEditor64.exe"
[ -f "$ME" ] || echo "!! [$NAME] EAs: MetaEditor64.exe not found - EAs copied but not compiled (MT5 compiles them when you open them in MetaEditor)."

for mql in "${targets[@]}"; do
    dst="$mql/Experts/AlgoEdge"
    sudo -u algoedge mkdir -p "$dst"
    for f in "${files[@]}"; do
        b="$(basename "$f" .mq5)"
        install -o algoedge -g algoedge -m644 "$f" "$dst/$b.mq5"
        [ -f "$ME" ] || { echo "== [$NAME] EA copied: $b.mq5"; continue; }

        rm -f "$dst/$b.log" "$dst/$b.ex5"
        winsrc="$("${AS[@]}" winepath -w "$dst/$b.mq5" 2>/dev/null)"
        winlog="$("${AS[@]}" winepath -w "$dst/$b.log" 2>/dev/null)"
        timeout 180 "${AS[@]}" wine "C:/Program Files/MetaTrader 5/MetaEditor64.exe" \
            "/compile:$winsrc" "/log:$winlog" >/dev/null 2>&1

        result=""
        if [ -f "$dst/$b.log" ]; then
            # MetaEditor writes its log as UTF-16.
            result="$(iconv -f UTF-16 -t UTF-8 "$dst/$b.log" 2>/dev/null | tr -d '\r')"
        fi
        if [ -f "$dst/$b.ex5" ]; then
            echo "== [$NAME] EA compiled: Experts/AlgoEdge/$b.ex5  ($(echo "$result" | grep -i 'result' | tail -1))"
        else
            echo "!! [$NAME] EA FAILED to compile: $b.mq5"
            echo "$result" | grep -iE 'error|warning|result' | head -25 | sed 's/^/   /'
        fi
    done
done
exit 0
