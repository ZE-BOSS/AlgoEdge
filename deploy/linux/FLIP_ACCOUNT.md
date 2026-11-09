# Flip account: a third AlgoEdge instance

A flip account is a separate MT5 account with its own AlgoEdge instance next to demo (8001) and live (8002). It
has its own database, rules, copy-trading channels and MT5 terminal, so flip sizing never touches the normal
accounts.

| | demo | live | **flip** |
|---|---|---|---|
| API port | 8001 | 8002 | **8003** |
| MT5 bridge port | 18811 | 18812 | **18813** |
| Redis DB | 1 | 2 | **3** |
| API address | api-demo.… | api.… | **https://admin.alphavantiqcapital.com/s/flip** |

## Status (9 Nov 2026)
Done on the VPS: instance created (`/opt/algoedge/flip`); its MT5 terminal and bridge are running; the API is
running with its own JWT and encryption keys (other settings copied from demo), role *demo*, label *Flip*;
Caddy routes `admin.alphavantiqcapital.com/s/flip` to it, so no DNS is needed.
**Left for you:** steps 1, 4 (the MT5 login, which needs your password) and 6–7 below. Steps 2, 3 and 5 are done.

## Steps

1. **Open the MT5 account** at your broker (start on a demo account for the first weeks). Note the login, server
   and password. Never paste them into chat.
2. **DNS:** add an `A` record `api-flip.alphavantiqcapital.com → 72.62.213.241`.
3. **Create the instance** in the Hostinger web console:
   ```
   sudo bash /opt/algoedge/src/deploy/linux/add_instance.sh flip 8003 18813 3
   ```
4. **Log its MT5 terminal in.** In the web console, run the command below. The password is typed at a hidden
   prompt, never echoed or stored by AlgoEdge, and MT5 remembers the login itself.
   ```
   bash /opt/algoedge/src/deploy/linux/mt5_login.sh flip
   systemctl restart algoedge-api@flip
   ```
5. **Route it**: append this to `/etc/caddy/Caddyfile`, then run `systemctl reload caddy`:
   ```
   api-flip.alphavantiqcapital.com {
   	import api_proxy 127.0.0.1:8003
   }
   ```
6. **Add it to the admin site**: open the server switcher (bottom-left), add a server with URL
   `https://admin.alphavantiqcapital.com/s/flip` and label *Flip*, then register / sign in there with
   ikchristo19@gmail.com (the admin email on that server). Each server has its own users.
7. **Copy Trading on the flip server**: log Telegram in again there (separate session), add GTS VIP, Jamson and
   Gold Trader Sunny, and set the rules:
   - Total risk per signal: **5%** (the account study's best balance of speed and drop; 10% doubled the
     drawdown)
   - Trail the stop up the TP ladder: **on** · Breakeven offset: **0** · Cancel unfilled orders once TP 1 fills
   - When the first zone order fills: **Also take the rest at market (limits stay until TP1)**
   - Optional: Stack at TP1 = 1, Last leg runs without a target = on
   - Dry run for the first days.

After step 3, `update.sh` updates the flip instance along with demo and live automatically.

Profits are recorded separately because it is a separate account. Its balance *is* the flip record, and its
Journal shows every trade.
