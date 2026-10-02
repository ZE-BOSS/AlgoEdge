# Running a demo box and a live box from the same branch

Both servers track **`dev`**. Both deploy with the **same five commands**. The only
thing that differs is each machine's `.env`, which is gitignored and therefore
already per-machine.

---

## The deploy command — identical on both servers

```powershell
cd C:\Users\Administrator\Documents\AlgoEdge
git pull origin dev
powershell -ExecutionPolicy Bypass -File scripts\build-sites.ps1
pm2 restart all
pm2 logs
```

No `&&` — Windows PowerShell 5.1 does not have it. These run in sequence; if one
fails you will see it before the next line does any damage.

---

## What makes a box "demo" or "live": only `.env`

### LIVE `.env`

```ini
API_URL=https://api.alphavantiqcapital.com
ALGOEDGE_CADDY=1
ACME_EMAIL=you@example.com
DATABASE_URL=sqlite+aiosqlite:///algoedge.db
# ... MT5 credentials for the live account
```

`ALGOEDGE_CADDY=1` puts the backend on `127.0.0.1:8000` behind Caddy, and Caddy
serves both sites on 80/443 with TLS.

### DEMO `.env`

```ini
API_URL=http://16.60.51.87:8000
# ALGOEDGE_CADDY deliberately NOT set — there is no Caddy on this box
INVESTOR_PORT=81
DATABASE_URL=sqlite+aiosqlite:///algoedge.db
# ... MT5 credentials for a SEPARATE demo account
```

Replace `16.60.51.87` with the demo server's own IP.

---

## Why `API_URL` is the one setting you cannot forget

**The API address is compiled into the front end at build time.** It used to
default to the live API, which meant a build on any other machine silently
produced a front end pointing at **production** — and nothing on screen would
say so. A demo box showing live trades is exactly the accident a demo box exists
to prevent.

`scripts/build-sites.ps1` now reads `API_URL` from `.env` and **refuses to build
without it** rather than guessing. If you see

```
API_URL is not set. Add it to .env on this machine
```

that is the guard doing its job, not a broken script.

---

## How you reach each one

| | Live (Caddy) | Demo (no Caddy) |
|---|---|---|
| Admin app | `https://admin.alphavantiqcapital.com` | `http://<demo-ip>` (port 80) |
| Investor app | `https://app.alphavantiqcapital.com` | `http://<demo-ip>:81` |
| API | `https://api.alphavantiqcapital.com` | `http://<demo-ip>:8000` |

No DNS and no certificate are needed for the demo box — it is reached by IP, the
way `16.60.51.87` already is. The browser will say "Not secure" because it is
plain HTTP; that is expected and fine for a demo, and is the reason **no real
investor data should ever live on it**.

### Open these ports on the demo VPS

`80` (admin), `81` (investor), `8000` (API). On Windows:

```powershell
New-NetFirewallRule -DisplayName "AlgoEdge demo" -Direction Inbound -Protocol TCP -LocalPort 80,81,8000 -Action Allow
```

---

## First-time setup of the demo box

```powershell
# 1. clone and check out dev
cd C:\Users\Administrator\Documents
git clone https://github.com/ZE-BOSS/AlgoEdge.git
cd AlgoEdge
git checkout dev

# 2. python environment
python -m venv venv_win
venv_win\Scripts\python.exe -m pip install -r requirements.txt

# 3. .env — copy the example and edit it (see DEMO .env above)
Copy-Item .env.example .env
notepad .env

# 4. first build and start
powershell -ExecutionPolicy Bypass -File scripts\build-sites.ps1
pm2 start ecosystem.config.js
pm2 save
```

`pm2 save` makes the apps come back after a reboot.

Without `ALGOEDGE_CADDY`, `ecosystem.config.js` starts three apps:
`algoedge-backend` (0.0.0.0:8000), `algoedge-frontend` (:80) and
`algoedge-investor` (:81).

---

## Keeping demo from touching live

Everything that matters is already gitignored, so a `git pull` cannot overwrite
it: `.env`, `*.db`, `logs/`, `dist/`.

What is left is yours to keep separate:

- **A different MT5 account.** Two bots on one account will fight over the same
  positions. The demo box should use its own demo login, or leave the bot
  stopped.
- **Its own database.** It is a separate file on a separate machine, so this is
  automatic — but never copy the live `algoedge.db` onto demo. It holds investor
  records.
- **No real investor data on demo.** It is plain HTTP.

---

## Both on `dev` — what that costs you

You asked for both boxes on `dev`, which is simplest and is what this document
describes. The trade-off worth knowing: **anything pushed to `dev` reaches live
the next time you deploy it.** There is no staging gate; `dev` is production the
moment you run the deploy command on the live box.

That is fine as long as the deploy is deliberate. If you later want a gate, the
change is small — point live at a `prod` branch and merge `dev` into it when
demo looks right. Nothing else in this document changes.

---

## After the 2026-10-02 strategy retirement

`IVW_v1`, `OvernightSession_v1`, `OpeningDrive_v1` and `HTFFVGFlip_v1` were
removed. Your saved config was checked against the new code and **loads cleanly,
all 23 slots intact** — the stale `ivw` and `htf_fvg_flip` blocks are discarded
silently.

One thing to tidy on each box: the **CADJPY slot still names `HTFFVGFlip_v1`**.
It is disabled so nothing crashes, but the slot editor will show an unknown
strategy. Delete it or point it at a surviving one.
