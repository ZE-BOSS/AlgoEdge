#!/usr/bin/env bash
# Show ONE instance's MT5 window - for logging that terminal into its account.
#   bash vnc.sh demo        (or: bash vnc.sh live)
# Listens on LOCALHOST only; reach it through an SSH tunnel from your PC:
#   ssh -L 5901:localhost:5900 root@<VPS-IP> "bash /opt/algoedge/src/deploy/linux/vnc.sh demo"
# then open a VNC viewer on localhost::5901. Stop with Ctrl+C when done.
NAME="${1:?which instance? e.g. demo or live}"
. "/opt/algoedge/$NAME/instance.env"
echo "Showing the $NAME MT5 terminal (display $DISPLAY)"
# A viewer session from before (closed SSH window) may still hold the port.
# Only THIS script's x11vnc (port 5900) - not the browser viewer's ones.
pkill -f "x11vnc.*-rfbport 5900" 2>/dev/null && sleep 1
exec sudo -u algoedge x11vnc -display "$DISPLAY" -localhost -rfbport 5900 -forever -shared -nopw
