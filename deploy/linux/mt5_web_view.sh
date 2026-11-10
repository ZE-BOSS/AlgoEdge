#!/usr/bin/env bash
# deploy/linux/mt5_web_view.sh - see (and use) every instance's MT5 terminal
# (demo, live, flip ...) in a web browser, without SSH or a VNC app. Run ON the VPS:
#
#   bash /opt/algoedge/src/deploy/linux/mt5_web_view.sh on           # set up / add new instances
#   bash /opt/algoedge/src/deploy/linux/mt5_web_view.sh password     # choose a new page password
#   bash /opt/algoedge/src/deploy/linux/mt5_web_view.sh off          # turn the pages off again
#
# "on" keeps the page login already set (it only asks the first time), so a new
# instance such as flip is added without anyone choosing a password again.
#
# Then open (any browser that can reach the server):
#   https://admin.alphavantiqcapital.com/mt5/<instance>/vnc.html?path=mt5/<instance>/websockify&autoconnect=1&resize=scale
#
# The screens are served only on localhost; the public way in is Caddy, behind
# the username and password chosen here (stored as a bcrypt hash only).
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
SNIP_DIR=/etc/caddy/mt5_view.d
SNIP="$SNIP_DIR/mt5.caddy"
INSTANCES="$(for f in /opt/algoedge/*/instance.env; do [ -f "$f" ] && basename "$(dirname "$f")"; done | xargs)"
[ -n "$INSTANCES" ] || { echo "no instances under /opt/algoedge"; exit 1; }

reload_caddy() {
    if caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
        systemctl reload caddy
    else
        echo "!! Caddy rejected the new config - restoring the previous one:"
        caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile 2>&1 | tail -5 || true
        cp "$1" /etc/caddy/Caddyfile
        systemctl reload caddy
        exit 1
    fi
}

case "${1:-on}" in
off)
    rm -f "$SNIP"
    systemctl reload caddy
    for n in $INSTANCES; do systemctl disable --now "algoedge-novnc@$n" "algoedge-vncview@$n" 2>/dev/null || true; done
    echo "MT5 browser view is OFF."
    ;;
on|password)
    echo "== instances: $INSTANCES"
    echo "== packages"
    apt-get install -y -q novnc websockify x11vnc >/dev/null
    install -m644 "$HERE/units/algoedge-vncview@.service" "$HERE/units/algoedge-novnc@.service" /etc/systemd/system/
    systemctl daemon-reload

    USER_NAME=""; HASH=""
    if [ "$1" = on ] && [ -f "$SNIP" ]; then
        # keep the login already chosen: the line after "basic_auth {" is "<user> <hash>"
        read -r USER_NAME HASH < <(awk '/basic_auth/ {getline; print $1, $2; exit}' "$SNIP")
        [ -n "$HASH" ] && echo "== keeping the existing page login ($USER_NAME)"
    fi
    if [ -z "$HASH" ]; then
        echo "== the page password (you choose it; it is stored only as a hash)"
        read -r -p "Username for the MT5 page: " USER_NAME
        [ -n "$USER_NAME" ] || { echo "a username is required"; exit 1; }
        HASH="$(caddy hash-password)"      # prompts for the password twice, hidden
        [ -n "$HASH" ] || { echo "no password set"; exit 1; }
    fi

    mkdir -p "$SNIP_DIR"
    {
        for n in $INSTANCES; do
            d="$(sed -n 's/^DISPLAY=://p' "/opt/algoedge/$n/instance.env")"
            cat <<EOF
handle_path /mt5/$n/* {
	basic_auth {
		$USER_NAME $HASH
	}
	reverse_proxy 127.0.0.1:$((6000 + d))
}
EOF
        done
    } > "$SNIP"
    chmod 640 "$SNIP"; chgrp caddy "$SNIP" 2>/dev/null || true

    echo "== Caddy"
    BAK="/etc/caddy/Caddyfile.bak.$(date +%Y%m%d%H%M%S)"
    cp /etc/caddy/Caddyfile "$BAK"
    # The server's Caddyfile may have local edits; only replace it when it does
    # not yet load the MT5 pages.
    grep -q "mt5_view.d" /etc/caddy/Caddyfile || install -m644 "$HERE/Caddyfile" /etc/caddy/Caddyfile
    reload_caddy "$BAK"

    echo "== screens"
    for n in $INSTANCES; do systemctl enable --now "algoedge-vncview@$n" "algoedge-novnc@$n"; done
    sleep 2
    for n in $INSTANCES; do
        printf "  %-5s %s %s\n" "$n" "$(systemctl is-active "algoedge-vncview@$n")" "$(systemctl is-active "algoedge-novnc@$n")"
    done
    echo
    echo "Open in your browser (sign in with the username and password you just chose):"
    for n in $INSTANCES; do
        echo "  https://admin.alphavantiqcapital.com/mt5/$n/vnc.html?path=mt5/$n/websockify&autoconnect=1&resize=scale"
    done
    ;;
*)
    echo "usage: $0 on|password|off"; exit 1 ;;
esac
