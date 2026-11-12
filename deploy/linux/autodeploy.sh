#!/usr/bin/env bash
# deploy/linux/autodeploy.sh - let the server deploy itself from GitHub.
#
# The server checks the `deploy` branch every 5 minutes. When that branch has
# moved, it runs update.sh for it (demo, live, flip and the admin site), the
# same as pasting the update command into the Hostinger terminal. Only a push to
# `deploy` does this - ordinary work on `dev` never restarts anything.
#
#   bash /opt/algoedge/src/deploy/linux/autodeploy.sh install     # turn on
#   bash /opt/algoedge/src/deploy/linux/autodeploy.sh status      # last runs
#   bash /opt/algoedge/src/deploy/linux/autodeploy.sh uninstall   # turn off
#
# Log: /var/log/algoedge-autodeploy.log
set -euo pipefail

REPO="https://github.com/ZE-BOSS/AlgoEdge.git"
BRANCH="${AUTODEPLOY_BRANCH:-deploy}"
STATE=/var/lib/algoedge/autodeploy.sha
LOG=/var/log/algoedge-autodeploy.log
LOCK=/run/algoedge-autodeploy.lock
SELF=/usr/local/bin/algoedge-autodeploy

[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }

case "${1:-}" in
install)
    install -m 755 "$(readlink -f "$0")" "$SELF"
    mkdir -p "$(dirname "$STATE")"
    # don't redeploy what is already running: start from the branch as it is now
    git ls-remote "$REPO" "refs/heads/$BRANCH" | cut -f1 > "$STATE" || true
    cat > /etc/systemd/system/algoedge-autodeploy.service <<EOF
[Unit]
Description=AlgoEdge: deploy the $BRANCH branch when it changes
After=network-online.target

[Service]
Type=oneshot
ExecStart=$SELF run
TimeoutStartSec=1800
EOF
    cat > /etc/systemd/system/algoedge-autodeploy.timer <<EOF
[Unit]
Description=AlgoEdge: check GitHub for a new $BRANCH every 5 minutes

[Timer]
OnBootSec=3min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
EOF
    systemctl daemon-reload
    systemctl enable --now algoedge-autodeploy.timer
    echo "auto-deploy is on: a push to '$BRANCH' is deployed within ~5 minutes (log: $LOG)"
    ;;
uninstall)
    systemctl disable --now algoedge-autodeploy.timer 2>/dev/null || true
    rm -f /etc/systemd/system/algoedge-autodeploy.{service,timer} "$SELF"
    systemctl daemon-reload
    echo "auto-deploy is off"
    ;;
status)
    systemctl list-timers algoedge-autodeploy.timer --no-pager || true
    echo "deployed: $(cat "$STATE" 2>/dev/null || echo none)"
    tail -n 15 "$LOG" 2>/dev/null || true
    ;;
run)
    exec 9>"$LOCK"
    flock -n 9 || exit 0                                   # a deploy is still running
    remote="$(git ls-remote "$REPO" "refs/heads/$BRANCH" | cut -f1)"
    [ -n "$remote" ] || exit 0                             # no such branch (yet)
    [ "$remote" != "$(cat "$STATE" 2>/dev/null)" ] || exit 0
    # a deploy that keeps failing would restart the instances every 5 minutes: give up after 3 tries of one commit
    fails="$(grep -c "^$remote\$" "$STATE.failed" 2>/dev/null || true)"
    [ "${fails:-0}" -lt 3 ] || exit 0
    {
        echo "== $(date -u '+%F %T') UTC: $BRANCH is at ${remote:0:7} - deploying"
        if curl -fsSL "https://raw.githubusercontent.com/ZE-BOSS/AlgoEdge/$remote/deploy/linux/update.sh" \
                | bash -s -- "$BRANCH"; then
            echo "$remote" > "$STATE"
            echo "== $(date -u '+%F %T') UTC: deployed ${remote:0:7}"
        else
            echo "$remote" >> "$STATE.failed"
            echo "== $(date -u '+%F %T') UTC: deploy of ${remote:0:7} FAILED (try $(( ${fails:-0} + 1 )) of 3)"
        fi
    } >> "$LOG" 2>&1
    ;;
*)
    echo "usage: $0 install|status|uninstall|run"; exit 1 ;;
esac
