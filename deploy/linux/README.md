# AlgoEdge backend on Linux (Hostinger / any Ubuntu VPS) with MT5 under Wine

The MetaTrader5 Python package exists only for Windows, so on Linux:

```
 Vercel frontend ──HTTPS──► Caddy (443) ──► AlgoEdge API (Linux Python, 127.0.0.1:8001)
                                                 │  `import MetaTrader5` = backend/mt5/bridge_client.py
                                                 ▼
                              MT5 bridge (Windows Python under Wine, 127.0.0.1:18811)
                                                 │  the real MetaTrader5 package
                                                 ▼
                              MT5 terminal (Wine, virtual screen :99) ──► broker
```

No backend code changes: with `ALGOEDGE_MT5_BRIDGE` set, `backend/__init__.py`
installs the bridge as `MetaTrader5`. Each **instance** (demo, live, …) has its
own code copy, venv, Wine prefix, MT5 terminal, bridge, port and Redis DB.

## 1. Order the VPS
Ubuntu 24.04, plain OS. 4 vCPU / 16 GB (Hostinger KVM 4) fits demo + live.
Log in with your SSH key: `ssh root@<VPS-IP>`.

## 2. Point a subdomain at it
At your DNS provider add an **A record**: `api-demo` → `<VPS-IP>`
(and later `api-live` → `<VPS-IP>`).

## 3. Upload the code (from this Windows PC)
```powershell
powershell -ExecutionPolicy Bypass -File deploy\linux\pack.ps1
scp algoedge-src.zip root@<VPS-IP>:/opt/
```
On the VPS:
```bash
apt-get install -y unzip && mkdir -p /opt/algoedge/src
unzip -o /opt/algoedge-src.zip -d /opt/algoedge/src
```

## 4. One-time server setup (about 10 minutes)
```bash
sudo bash /opt/algoedge/src/deploy/linux/setup_server.sh
```

## 5. Add the demo instance (about 10 minutes)
```bash
sudo bash /opt/algoedge/src/deploy/linux/add_instance.sh demo 8001 18811 1
```

## 6. Fill in its settings
```bash
sudo nano /opt/algoedge/demo/app/.env
```
Replace every `CHANGE_ME` with the values from your current server's `.env`
(MT5 account/password/server, JWT_SECRET_KEY, ENCRYPTION_KEY, ADMIN_EMAILS …).
`scripts/secrets.sh` can carry an encrypted `.env` across if you prefer.

## 7. Log MT5 into the account (once)
In a SECOND terminal on your PC, open a tunnel:
```bash
ssh -L 5900:localhost:5900 root@<VPS-IP>
```
then on the VPS run `bash /opt/algoedge/src/deploy/linux/vnc.sh`, and open a
VNC viewer (TightVNC / RealVNC Viewer) on `localhost:5900`. In the MT5 window:
**File → Login to Trade Account** → type the broker server (e.g. `Deriv-Demo`),
account, password, tick **Save password** → OK. Then switch **Algo Trading** ON
(toolbar button). Close the viewer and stop vnc.sh (Ctrl+C).

## 8. Start the API and HTTPS
```bash
sudo systemctl enable --now algoedge-api@demo
sudo cp /opt/algoedge/src/deploy/linux/Caddyfile /etc/caddy/Caddyfile
sudo systemctl reload caddy
bash /opt/algoedge/src/deploy/linux/status.sh
```
`https://api-demo.alphavantiqcapital.com/api/health` should answer, and
`/api/health/identity` should show the MT5 account you logged in.

## 9. Connect the frontend
In the admin app's server switcher add `https://api-demo.alphavantiqcapital.com`.
The `.env` already allows `https://admin.alphavantiqcapital.com`; add your Vercel
address to `FRONTEND_URL` if it differs, then `sudo systemctl restart algoedge-api@demo`.

## Live later
```bash
sudo bash /opt/algoedge/src/deploy/linux/add_instance.sh live 8002 18812 2
```
then steps 6–8 for `live` (uncomment the `api-live` block in the Caddyfile).

## Updating the code
Upload a new zip (step 3), then re-run `add_instance.sh` with the SAME arguments:
it syncs the code, keeps `.env`, the database and the MT5 login, and restarts.

## Day to day
| What | Command |
|---|---|
| Health of everything | `bash /opt/algoedge/src/deploy/linux/status.sh` |
| API log | `journalctl -u algoedge-api@demo -f` (also `/opt/algoedge/demo/app/logs/`) |
| Bridge log | `journalctl -u algoedge-bridge@demo -f` |
| Restart one instance | `sudo systemctl restart algoedge-mt5@demo algoedge-bridge@demo algoedge-api@demo` |

All three services restart themselves if they crash. A restart of `demo` never
touches `live`.

## If MT5 does not install
`add_instance.sh` stops with "MT5 did not install". Run the installer visibly
(vnc.sh + tunnel as in step 7):
```bash
sudo -u algoedge env WINEPREFIX=/opt/algoedge/demo/wine DISPLAY=:99 wine /opt/algoedge/downloads/mt5setup.exe
```
click through it in the VNC window, then re-run `add_instance.sh`.

## Known limits (Wine)
- MT5 under Wine is community-supported, not official. An MT5 auto-update can
  occasionally break it: `status.sh` shows the bridge failing; restart the
  instance, and if it persists re-run the installer as above.
- Bulk history (big M1 backtests) crosses the bridge: about 200,000 bars per
  second measured. Live trading is unaffected.
- `order_send` is never re-sent after a lost reply (it may have executed);
  the backend sees a failed order and its normal handling applies.
