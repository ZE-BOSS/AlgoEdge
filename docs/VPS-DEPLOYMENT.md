AlgoEdge — VPS deployment guide (Windows Server)
=================================================

Written against the live setup: **AWS EC2 Windows Server, PowerShell 5.1,
PM2, Redis, SQLite, and two MetaTrader 5 terminals.**

Two rules that cause most of the pain if ignored:

1. **PowerShell 5.1 has no `&&`.** Chain with `;`, or `; if ($?) { ... }` when
   the second command must only run on success.
2. **`venv/` and `node_modules/` are per-machine. Never take them from git.**
   Always build them on the box. See [Appendix A](#appendix-a--why-the-venv-keeps-breaking).

---

## 1. Prerequisites

Install once, as Administrator:

| what | why | check |
|---|---|---|
| **Python 3.11+** (tick "Add to PATH") | backend | `python --version` |
| **Node.js 20 LTS** | frontend build | `node --version` |
| **Git** | deploy | `git --version` |
| **Redis** | run state, backtest progress | `redis-cli ping` -> `PONG` |
| **MetaTrader 5** | market data + execution | terminal opens |
| **PM2** (`npm i -g pm2`) | process manager | `pm2 --version` |

Verify everything at once:

```powershell
python --version; node --version; git --version; pm2 --version
```

### MetaTrader 5 terminals

If you trade more than one broker you need **one terminal install per broker** —
the MT5 Python API binds to a terminal by executable path.

```
C:\Program Files\MetaTrader 5\terminal64.exe            <- FundedNext
C:\Program Files\MetaTrader 5 Terminal\terminal64.exe   <- Deriv
```

Log each terminal into its account manually once, tick **Tools > Options >
Expert Advisors > Allow algorithmic trading**, and leave both running.

> **The API allows one client per terminal.** Two processes cannot pull from the
> same terminal at once — a backtest sweep and the live bot will fight over it.

---

## 2. Clone

```powershell
cd C:\Users\Administrator\Documents
```

```powershell
git clone https://github.com/ZE-BOSS/AlgoEdge.git; cd AlgoEdge; git checkout dev
```

The repo carries ~82 MB of research data (MT5 caches, result JSON). If the box is
tight on disk or bandwidth:

```powershell
git clone --depth 1 --branch dev https://github.com/ZE-BOSS/AlgoEdge.git
```

Set a git identity, or commits and merges fail with *"Committer identity
unknown"*:

```powershell
git config user.email "you@example.com"; git config user.name "AlgoEdge VPS"
```

---

## 3. Environment

`.env` is **not** in git. Create it from the template:

```powershell
Copy-Item .env.example .env; notepad .env
```

Required:

```ini
MT5_ACCOUNT=...
MT5_PASSWORD=...
MT5_SERVER=FundedNext-Server
MT5_PATH=C:\Program Files\MetaTrader 5\terminal64.exe

DERIV_MT5_ACCOUNT=...
DERIV_MT5_PASSWORD=...
DERIV_MT5_SERVER=Deriv-Demo
DERIV_MT5_PATH=C:\Program Files\MetaTrader 5 Terminal\terminal64.exe

REDIS_URL=redis://localhost:6379
DATABASE_URL=sqlite+aiosqlite:///./algoedge.db
HOST=0.0.0.0
PORT=8000
ENCRYPTION_KEY=...        # generate, do not reuse another host's
JWT_ACCESS_EXPIRE_MINUTES=1440
```

**`MT5_PATH` is what pins each connection to the right terminal.** Omit it and
the API attaches to whichever it finds first — you end up silently comparing a
broker against itself.

---

## 4. Backend

```powershell
python -m venv venv
```

```powershell
.\venv\Scripts\Activate.ps1
```

```powershell
python -m pip install --upgrade pip; pip install -r requirements.txt
```

Verify MT5 reaches both terminals before going further:

```powershell
python research\data\dual_broker.py
```

Expect two different accounts, two different servers, and
`GENUINELY DIFFERENT FEEDS`. If it reports the same feed twice, `MT5_PATH` is
wrong or a terminal is closed.

---

## 5. Frontend

```powershell
cd frontend; npm ci; if ($?) { npm run build }; cd ..
```

Output lands in `frontend/dist` (gitignored — every host builds its own).

> `npm ci` **deletes `node_modules` and reinstalls**. It fails with `EPERM ...
> unlink ... rolldown-binding.win32-x64-msvc.node` if anything is serving the
> app. Always `pm2 stop all` first. On a redeploy where dependencies have not
> changed, `npm run build` alone is enough and avoids the problem entirely.

---

## 6. Start

```powershell
pm2 start ecosystem.config.js; pm2 save
```

```powershell
pm2 status; pm2 logs --lines 50
```

Survive reboots:

```powershell
npm i -g pm2-windows-startup; pm2-startup install; pm2 save
```

---

## 7. Redeploying

The everyday path — no dependency changes:

```powershell
git pull origin dev; cd frontend; npm run build; cd ..; pm2 restart all
```

When `requirements.txt` or `package.json` changed:

```powershell
pm2 stop all
```

```powershell
git pull origin dev
```

```powershell
.\venv\Scripts\Activate.ps1; pip install -r requirements.txt
```

```powershell
cd frontend; npm ci; if ($?) { npm run build }; cd ..
```

```powershell
pm2 restart all; pm2 logs --lines 50
```

Check whether dependencies actually moved before deciding:

```powershell
git --no-pager diff "HEAD@{1}" HEAD --stat -- requirements.txt frontend/package.json
```

Quote `"HEAD@{1}"` — PowerShell reads bare `@{...}` as hashtable syntax.

---

## 8. Health check

```powershell
pm2 status
```

```powershell
Invoke-RestMethod http://localhost:8000/api/health
```

```powershell
redis-cli ping
```

```powershell
Get-Process terminal64 -ErrorAction SilentlyContinue | Select-Object Id, Path
```

Two `terminal64` processes = both brokers reachable.

---

## Appendix A — why the venv keeps breaking

`venv/pyvenv.cfg` and compiled `.pyc` files were committed at some point. Git
tracks them regardless of `.gitignore` (**`.gitignore` only affects files git
does not already track**), so every pull overwrites the host's own environment.

The symptom is unmistakable:

```
No Python at '"/usr/bin\python.exe'
```

A Linux path on a Windows box — the committed `pyvenv.cfg` from another machine.

**Permanent fix, run once on a development machine and pushed:**

```powershell
git rm -r --cached venv --quiet
```

```powershell
git ls-files "*.pyc" | ForEach-Object { git rm --cached $_ --quiet }
```

```powershell
git commit -m "chore: stop tracking venv/ and .pyc - machine-specific"; git push origin dev
```

**Recovery on a host that has already been broken:**

```powershell
deactivate; Remove-Item -Recurse -Force venv; python -m venv venv
```

```powershell
.\venv\Scripts\Activate.ps1; pip install -r requirements.txt
```

If you cannot untrack them yet, silence them on this host only:

```powershell
git ls-files "*.pyc" | ForEach-Object { git update-index --skip-worktree $_ }
```

`skip-worktree` is local and does not travel with a push — set it on every host
that needs it.

---

## Appendix B — troubleshooting

| symptom | cause | fix |
|---|---|---|
| `The token '&&' is not a valid statement separator` | PowerShell 5.1 | use `;` or `; if ($?) { }` |
| `No Python at '"/usr/bin\python.exe'` | committed `pyvenv.cfg` from another OS | Appendix A |
| `EPERM ... unlink ... .node` | `npm ci` while the app is serving | `pm2 stop all` first, or just `npm run build` |
| `Committer identity unknown` | no git identity | `git config user.email/user.name` |
| `fatal: bad revision 'HEAD@'` | PowerShell ate `{1}` | quote it: `"HEAD@{1}"` |
| branches "diverged" on the VPS | commits authored on the deploy box | `git reset --hard origin/dev` — a deploy box should mirror, never author |
| `A backtest is already running for this user` | stale Redis flag from a killed run | **Clear stuck run** on the error, or `POST /api/backtest/stop` |
| MT5 returns nothing / both brokers identical | terminal closed, or `MT5_PATH` unset | check `terminal64` processes, run `dual_broker.py` |
| `logs/` growing fast | per-bar strategy logging | already demoted to DEBUG; confirm `LOG_LEVEL=INFO` |

### Before wiping anything

`git reset --hard` discards uncommitted work permanently. Check first:

```powershell
git --no-pager diff origin/dev...HEAD --stat -- "*.py" "*.jsx" ":(exclude)*__pycache__*"
```

That filters out compiled-file noise and shows only real source differences. If
it prints nothing, resetting is safe. If it prints something, that is work
existing only on this host — port it before discarding.

Untracked runtime state (`backend/data/*.json`, `Redis/dump.rdb`, `db_state.txt`)
is **not** touched by `reset --hard`, but back it up before any destructive step.
