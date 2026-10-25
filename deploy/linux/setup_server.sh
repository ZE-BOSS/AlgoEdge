#!/usr/bin/env bash
# deploy/linux/setup_server.sh - ONE-TIME setup of an Ubuntu 22.04/24.04 VPS
# for AlgoEdge backends that reach MetaTrader 5 through Wine.
#
#   sudo bash setup_server.sh
#
# Installs: WineHQ (stable), Xvfb (a virtual screen for the MT5 window), x11vnc
# (to log into MT5 once, over an SSH tunnel), Python 3 + venv, Redis, Caddy
# (HTTPS), and the systemd units. Then run add_instance.sh once per account.
# Safe to run again.
set -euo pipefail

[ "$(id -u)" = 0 ] || { echo "run as root (sudo)"; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
. /etc/os-release

echo "== packages"
dpkg --add-architecture i386
apt-get update
apt-get install -y ca-certificates curl wget gnupg rsync unzip cabextract \
    xvfb x11vnc python3 python3-venv python3-dev build-essential redis-server ufw

echo "== WineHQ STAGING"
# Which Wine runs MetaTrader 5 (measured 2026-10-22 on Ubuntu 26.04, MT5 build 6274):
#   WineHQ stable 11.0 / Ubuntu wine 10.0 -> "A debugger has been found running in
#     your system" - MT5 refuses to start (mql5.com/en/forum/503128).
#   WineHQ STAGING 11.3+ carries a patch for that check, active only when the
#     prefix reports Windows 11 (add_instance.sh sets it). 11.20 runs MT5.
mkdir -pm755 /etc/apt/keyrings
# Binary keyring: apt 3 (Ubuntu 25.10+) ignores an ASCII-armoured .key file.
wget -qO- https://dl.winehq.org/wine-builds/winehq.key | gpg --dearmor --yes -o /etc/apt/keyrings/winehq-archive.key
wget -qNP /etc/apt/sources.list.d/     "https://dl.winehq.org/wine-builds/ubuntu/dists/${VERSION_CODENAME}/winehq-${VERSION_CODENAME}.sources"
apt-get update
apt-get remove -y wine wine64 2>/dev/null || true
apt-get install -y --install-recommends winehq-staging
wine --version

echo "== uv (gives every instance Python 3.12 - the version the backend is pinned
#    and tested on - whatever the distro ships; Ubuntu 26.04 ships 3.14)"
if ! command -v uv >/dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh
fi

echo "== Caddy"
if ! command -v caddy >/dev/null; then
    apt-get install -y debian-keyring debian-archive-keyring apt-transport-https
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
        | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
        > /etc/apt/sources.list.d/caddy-stable.list
    apt-get update && apt-get install -y caddy
fi

echo "== service user and folders"
id algoedge >/dev/null 2>&1 || useradd -m -s /bin/bash algoedge
mkdir -p /opt/algoedge/downloads /opt/algoedge/src
chown -R algoedge:algoedge /opt/algoedge

echo "== installers (Windows Python for the bridge, MetaTrader 5)"
cd /opt/algoedge/downloads
# The EMBEDDABLE Windows Python (a plain zip): the full installer is an MSI
# that is unreliable under Wine; a zip cannot fail to install.
[ -s python-3.11.9-embed-amd64.zip ] || sudo -u algoedge wget -q     https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip
[ -s get-pip.py ] || sudo -u algoedge wget -q https://bootstrap.pypa.io/get-pip.py
[ -s mt5setup.exe ] || sudo -u algoedge wget -q \
    https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe

echo "== systemd units"
install -m644 "$HERE"/units/*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now redis-server
# Virtual screens are per instance (algoedge-xvfb@<name>), started by add_instance.sh.

echo "== firewall: SSH + HTTPS only (the APIs and bridges listen on localhost)"
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo
echo "Done. Next:"
echo "  1. Copy the AlgoEdge code to /opt/algoedge/src (see README.md)."
echo "  2. sudo bash $HERE/add_instance.sh demo 8001 18811 1"
