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

# A MetaEditor that is already open in this prefix swallows a command-line
# /compile (the new process hands it over and exits at once, nothing compiles).
# Close only this instance's MetaEditor (matched by WINEPREFIX); MT5 itself and
# the other instance are left alone.
stop_metaeditor() {
    local stopped=0 pid
    for pid in $(pgrep -u algoedge -f 'MetaEditor64' 2>/dev/null); do
        if tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null | grep -qx "WINEPREFIX=$WINEPREFIX"; then
            kill "$pid" 2>/dev/null && stopped=1
        fi
    done
    if [ "$stopped" = 1 ]; then
        echo "   [$NAME] closed the open MetaEditor so the compile can run (reopen it with F4 in MT5)"
        for _ in $(seq 1 10); do
            pgrep -u algoedge -f 'MetaEditor64' >/dev/null 2>&1 || break
            sleep 1
        done
        sleep 2
    fi
}
[ -f "$ME" ] && stop_metaeditor
[ -f "$ME" ] || echo "!! [$NAME] EAs: MetaEditor64.exe not found - EAs copied but not compiled (MT5 compiles them when you open them in MetaEditor)."

ti=0
for mql in "${targets[@]}"; do
    ti=$((ti + 1))
    dst="$mql/Experts/AlgoEdge"
    # MetaEditor misreads a /compile: path with spaces ("Program Files",
    # "MetaTrader 5") once Wine has quoted it: give it a path without spaces
    # through a link C:\ae_mt5_<n> -> the folder holding this MQL5.
    link="$WINEPREFIX/drive_c/ae_mt5_$ti"
    sudo -u algoedge ln -sfn "$(dirname "$mql")" "$link"
    sudo -u algoedge mkdir -p "$dst"
    for f in "${files[@]}"; do
        b="$(basename "$f" .mq5)"
        install -o algoedge -g algoedge -m644 "$f" "$dst/$b.mq5"
        [ -f "$ME" ] || { echo "== [$NAME] EA copied: $b.mq5"; continue; }

        rm -f "$dst/$b.log" "$dst/$b.ex5"
        winsrc="C:\\ae_mt5_$ti\\MQL5\\Experts\\AlgoEdge\\$b.mq5"
        out="$(mktemp)"; chmod 666 "$out"
        start=$(date +%s)
        # MetaEditor writes <file>.log next to the source when /log has no path.
        ( cd "$dst" && timeout 180 "${AS[@]}" wine "C:/Program Files/MetaTrader 5/MetaEditor64.exe" \
            "/compile:$winsrc" "/log" >"$out" 2>&1 )
        rc=$?
        # wine can return before MetaEditor has finished: give it up to 60 s.
        for _ in $(seq 1 60); do
            [ -f "$dst/$b.ex5" ] && break
            [ -f "$dst/$b.log" ] && [ -s "$dst/$b.log" ] && { sleep 1; break; }
            sleep 1
        done
        secs=$(( $(date +%s) - start ))

        result=""
        for lg in "$dst/$b.log" "$mql/Experts/$b.log"; do
            if [ -f "$lg" ]; then
                # MetaEditor writes its log as UTF-16.
                result="$(iconv -f UTF-16 -t UTF-8 "$lg" 2>/dev/null | tr -d '\r')"
                [ -n "$result" ] && break
            fi
        done
        # Otherwise MetaEditor's own log (portable install: <MT5>/logs/metaeditor.log):
        # keep only the lines about this EA, newest last.
        if [ -z "$result" ]; then
            for lg in "$mql/../logs/metaeditor.log" "$mql/../logs/"*.log; do
                [ -f "$lg" ] || continue
                r="$(iconv -f UTF-16 -t UTF-8 "$lg" 2>/dev/null | tr -d '\r' | grep -a "$b" | tail -30)"
                [ -n "$r" ] && { result="$r"; break; }
            done
        fi
        if [ -f "$dst/$b.ex5" ]; then
            echo "== [$NAME] EA compiled: Experts/AlgoEdge/$b.ex5  ($(echo "$result" | grep -i 'result' | tail -1))"
        else
            echo "!! [$NAME] EA FAILED to compile: $b.mq5"
            if [ -n "$result" ]; then
                echo "$result" | grep -iE 'error|warning|result|compile' | tail -25 | sed 's/^/   /'
                echo "   (exit code $rc after ${secs}s)"
            else
                echo "   no compiler log. exit code $rc after ${secs}s (124 = timed out)"
                echo "   source path given to MetaEditor: ${winsrc:-<winepath returned nothing>}"
                echo "   wine output:"; tail -15 "$out" | sed 's/^/     /'
                echo "   files in $dst:"; ls -la "$dst" | sed 's/^/     /'
                echo "   MT5 / MetaEditor processes:"
                ps -eo pid,user,etime,args | grep -iE 'metaeditor|terminal64' | grep -v grep | cut -c1-160 | sed 's/^/     /'
            fi
        fi
        rm -f "$out"
    done
done
exit 0
