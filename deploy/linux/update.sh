#!/usr/bin/env bash
# deploy/linux/update.sh - update the VPS from GitHub, run ON the server.
#
# For when the PC cannot reach the server over SSH (some ISPs cannot route to
# the VPS at all): paste this into the Hostinger browser terminal -
#
#   curl -fsSL https://raw.githubusercontent.com/ZE-BOSS/AlgoEdge/dev/deploy/linux/update.sh | bash
#
# It fetches the branch (default dev), then re-runs add_instance.sh for demo
# and live - which keeps each instance's .env, database and MT5 login and
# restarts only its API - and rebuilds the admin site and the landing page.
# Usage with another branch:  ... | bash -s -- <branch>
set -euo pipefail

[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }
BRANCH="${1:-dev}"
REPO="https://github.com/ZE-BOSS/AlgoEdge.git"
GIT=/opt/algoedge/git
SRC=/opt/algoedge/src
API_URL="${API_URL:-https://api.alphavantiqcapital.com}"

command -v git >/dev/null && command -v rsync >/dev/null || apt-get install -y -q git rsync

echo "== fetching $BRANCH"
if [ -d "$GIT/.git" ]; then
    git -C "$GIT" fetch -q --depth 1 origin "$BRANCH"
    git -C "$GIT" checkout -q -f FETCH_HEAD
else
    git clone -q --depth 1 -b "$BRANCH" "$REPO" "$GIT"
fi
echo "   at $(git -C "$GIT" log -1 --format='%h %s')"

# The code the instances are built from. --delete so files removed in git
# (a retired strategy) do not linger; local-only state never lives in src.
rsync -a --delete --exclude .git --exclude node_modules --exclude '.env' "$GIT/" "$SRC/"

echo "== demo"
bash "$SRC/deploy/linux/add_instance.sh" demo 8001 18811 1
echo "== live"
bash "$SRC/deploy/linux/add_instance.sh" live 8002 18812 2

echo "== websites"
if ! command -v npm >/dev/null; then
    apt-get install -y -q nodejs npm
fi
mkdir -p /var/www/admin /var/www/landing
rsync -a --delete "$SRC/landing/" /var/www/landing/
# Vite 8 needs Node 20.19+; an older distro Node would fail the build after
# both instances were already updated, so check first and say so.
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
if [ "$NODE_MAJOR" -ge 20 ]; then
    (
        cd "$SRC/frontend"
        npm ci --no-audit --no-fund --loglevel=error
        VITE_DEFAULT_BACKEND_URL="$API_URL" npm run build --silent
    )
    rsync -a --delete "$SRC/frontend/dist/" /var/www/admin/
else
    echo "!! Node $(node -v 2>/dev/null || echo missing) is too old for the admin site build (needs 20+)."
    echo "   The APIs and the landing page are updated; the admin site was left as it was."
fi
systemctl reload caddy || true

echo
bash "$SRC/deploy/linux/status.sh" || true
echo "== done: $(git -C "$GIT" log -1 --format='%h %s')"
