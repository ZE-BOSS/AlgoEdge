#!/usr/bin/env bash
# deploy/linux/mt5_web_view.sh - see (and use) the demo and live MT5 terminals
# in a web browser, without SSH or a VNC app. Run ON the VPS:
#
#   bash /opt/algoedge/src/deploy/linux/mt5_web_view.sh on      # set up / turn on (asks for a page password)
#   bash /opt/algoedge/src/deploy/linux/mt5_web_view.sh off     # turn the pages off again
#
# Then open (any browser that can reach the server):
#   https://admin.alphavantiqcapital.com/mt5/demo/
#   https://admin.alphavantiqcapital.com/mt5/live/
#
# The screens are served only on localhost; the public way in is Caddy, behind
# the username and password chosen here (stored as a bcrypt hash only).
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
SNIP_DIR=/etc/caddy/mt5_view.d
SNIP="$SNIP_DIR/mt5.caddy"
INSTANCES="demo live"

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
on)
    echo "== packages"
    apt-get install -y -q novnc websockify x11vnc >/dev/null
    install -m644 "$HERE/units/algoedge-vncview@.service" "$HERE/units/algoedge-novnc@.service" /etc/systemd/system/
    systemctl daemon-reload

    echo "== the page password (you choose it; it is stored only as a hash)"
    read -r -p "Username for the MT5 page: " USER_NAME
    [ -n "$USER_NAME" ] || { echo "a username is required"; exit 1; }
    HASH="$(caddy hash-password)"          # prompts for the password twice, hidden
    [ -n "$HASH" ] || { echo "no password set"; exit 1; }

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
    install -m644 "$HERE/Caddyfile" /etc/caddy/Caddyfile
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
    echo "usage: $0 on|off"; exit 1 ;;
esac
