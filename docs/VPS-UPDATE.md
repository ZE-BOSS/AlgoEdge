AlgoEdge — updating an existing VPS
====================================

For a box that is **already running**. For a fresh install see
[VPS-DEPLOYMENT.md](VPS-DEPLOYMENT.md).

PowerShell 5.1 has no `&&` — chain with `;`, or `; if ($?) { }` when the second
command must only run if the first succeeded.

---

## Step 0 — always start here

Three read-only commands. They decide which path you take and take ten seconds:

```powershell
git fetch origin dev; git status
```

```powershell
git --no-pager log --oneline origin/dev..HEAD
```

```powershell
git --no-pager diff origin/dev...HEAD --stat -- "*.py" "*.jsx" "*.js" ":(exclude)*__pycache__*"
```

Read the results:

| what you see | path |
|---|---|
| "Your branch is behind", nothing else | **[Path A](#path-a--routine-update)** |
| behind, and `requirements.txt` / `package.json` changed | **[Path B](#path-b--dependencies-changed)** |
| "diverged", commands 2–3 print **nothing** real | **[Path C](#path-c--the-box-has-drifted)** |
| "diverged", command 3 prints **real source** | **[Path D](#path-d--the-box-has-work-worth-keeping)** |

Check whether dependencies moved:

```powershell
git --no-pager diff HEAD origin/dev --stat -- requirements.txt frontend/package.json frontend/package-lock.json
```

Empty output means Path A. Anything listed means Path B.

---

## Path A — routine update

The common case. Dependencies unchanged, box is clean.

```powershell
git pull origin dev
```

```powershell
cd frontend; npm run build; cd ..
```

```powershell
pm2 restart all; pm2 logs --lines 30
```

**Do not run `npm ci` here.** It deletes `node_modules` and will fail with
`EPERM ... unlink ... rolldown-binding.win32-x64-msvc.node` while PM2 is serving.
`npm run build` does not touch `node_modules`, so it just works.

---

## Path B — dependencies changed

Stop first. This is the whole difference between B and A.

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
pm2 restart all; pm2 logs --lines 30
```

If `npm ci` still reports `EPERM`, a node process survived:

```powershell
Get-Process node -ErrorAction SilentlyContinue | Stop-Process -Force
```

Windows sometimes holds the handle a moment after the process dies. If it
persists, `npm install` patches in place instead of deleting the tree:

```powershell
cd frontend; npm install; if ($?) { npm run build }; cd ..
```

---

## Path C — the box has drifted

`git status` says "diverged" but Step 0 showed no real source changes — only
`.pyc`, `venv/`, or junk commits. A deploy box should mirror the remote, never
author commits.

**Back up runtime state first.** `reset --hard` leaves untracked files alone, but
confirm before a destructive step:

```powershell
Copy-Item backend\data\*.json, db_state.txt, Redis\dump.rdb -Destination $env:TEMP\algoedge-backup -Force
```

```powershell
pm2 stop all
```

```powershell
git reset --hard origin/dev
```

Then rebuild — a reset usually clobbers `venv/pyvenv.cfg` if it is still tracked:

```powershell
deactivate; Remove-Item -Recurse -Force venv; python -m venv venv
```

```powershell
.\venv\Scripts\Activate.ps1; pip install -r requirements.txt
```

```powershell
cd frontend; npm ci; if ($?) { npm run build }; cd ..; pm2 restart all
```

Discarded commits stay in the reflog for ~90 days:

```powershell
git --no-pager reflog -10
```

---

## Path D — the box has work worth keeping

Step 0 showed real source changes made directly on the VPS. **Do not reset.**

Capture it first:

```powershell
git --no-pager diff origin/dev...HEAD -- "*.py" "*.jsx" ":(exclude)*__pycache__*" > $env:TEMP\vps-changes.patch
```

```powershell
Get-Content $env:TEMP\vps-changes.patch
```

Read it. Then pick one:

**Small and still wanted** — apply it on a development machine, commit, push, and
take Path C here. The change reaches the VPS through the normal pull.

**A hotfix already superseded** — Path C discards it, and the patch file is your
record.

**Substantial** — commit it on a branch and push for a proper merge:

```powershell
git checkout -b vps-hotfix; git add -A; git commit -m "hotfix from VPS"; git push origin vps-hotfix
```

Then merge it on a development machine, where you can run the tests.

---

## Rollback

Note the current commit before you start:

```powershell
git --no-pager log --oneline -1
```

To go back:

```powershell
pm2 stop all; git reset --hard <that-commit>
```

```powershell
cd frontend; npm run build; cd ..; pm2 restart all
```

If you did not note it:

```powershell
git --no-pager reflog -15
```

---

## Verify

```powershell
pm2 status
```

```powershell
Invoke-RestMethod http://localhost:8000/api/health
```

```powershell
pm2 logs --lines 50 --nostream | Select-String -Pattern "ERROR|Traceback|CRITICAL"
```

```powershell
Get-Process terminal64 -ErrorAction SilentlyContinue | Select-Object Id, Path
```

Two `terminal64` processes = both brokers reachable. If a strategy trades a
broker whose terminal is closed, it produces no signals and no error.

Confirm the frontend actually rebuilt — a stale `dist` is easy to miss:

```powershell
Get-ChildItem frontend\dist\index.html | Select-Object LastWriteTime
```

---

## The two problems that keep recurring

Both come from files that are tracked but should not be. **`.gitignore` has no
effect on files git already tracks** — that is why they keep coming back.

| symptom | cause |
|---|---|
| `No Python at '"/usr/bin\python.exe'` | `venv/pyvenv.cfg` is tracked; a pull overwrote it with another machine's |
| `No Python at '"C:\Users\<someone-else>\...'` | `venv_win/` is tracked and PM2 picks it first — see [The interpreter trap](#the-interpreter-trap-hit-on-the-live-vps-2026-09-01) |
| every pull conflicts on `__pycache__` | compiled `.pyc` files are tracked |

**Fix once, on a development machine, then push:**

```powershell
git rm -r --cached venv venv_win --quiet
```

```powershell
git ls-files "*.pyc" | ForEach-Object { git rm --cached $_ --quiet }
```

```powershell
git commit -m "chore: stop tracking venv/ and .pyc - machine-specific"; git push origin dev
```

One pull on the VPS afterwards and neither recurs.

**Stopgap if you cannot push yet** — silences them on this host only, and does
not travel with a push:

```powershell
git ls-files "*.pyc" | ForEach-Object { git update-index --skip-worktree $_ }
```

---

## The interpreter trap (hit on the live VPS, 2026-09-01)

**Symptom.** Frontend `online`, backend `stopped` or crash-looping with a rising
restart count, and an error log full of:

```
No Python at '"C:\Users\ikchr\AppData\Local\Programs\Python\Python311\python.exe'
```

A path belonging to **someone else's machine**, on your box.

**Cause.** `ecosystem.config.js` resolves its interpreter from a candidate list
and checks **`venv_win` first**:

```
venv_win/Scripts/python.exe   <-  checked first
.venv/Scripts/python.exe
venv/Scripts/python.exe
```

`venv_win/` was tracked in git, so a pull delivered a virtual environment built
on a different machine. PM2 launched its `python.exe` shim, which points at a
Python install that does not exist on this host, and the backend crash-looped.
The frontend was unaffected, which makes it look like a backend fault rather
than a deployment one.

### Fix

```powershell
pm2 stop algoedge-backend
```

```powershell
Rename-Item venv_win venv_win.disabled
```

**`pm2 restart` is not enough here.** It reuses the *saved process definition*,
which still holds the old interpreter path — renaming the directory changes
nothing until PM2 re-reads the config file. Delete and re-create:

```powershell
pm2 delete algoedge-backend; pm2 start ecosystem.config.js --only algoedge-backend
```

```powershell
pm2 status; pm2 logs algoedge-backend --lines 20 --nostream
```

The error log is append-only, so ignore everything above the new start. Success
is `↺` no longer climbing and `pid` showing a real number instead of `N/A`.

If it still fails, force the interpreter — the config honours this override:

```powershell
$env:ALGOEDGE_PYTHON = "C:\Users\Administrator\Documents\AlgoEdge\venv\Scripts\python.exe"
```

```powershell
pm2 delete algoedge-backend; pm2 start ecosystem.config.js --only algoedge-backend
```

### Permanent fix

`venv/`, `venv_win/` and `*.pyc` were untracked in commit `fca6505`. Once a host
pulls that, `venv_win` can no longer be delivered by git and this cannot recur.
Verify after the next pull:

```powershell
git ls-files venv_win | Measure-Object -Line
```

Zero means the fix landed; delete `venv_win.disabled` at that point.

> **Rule of thumb.** Any change to `ecosystem.config.js` — or to what it
> *resolves* (interpreter path, script path, cwd) — needs `pm2 delete` +
> `pm2 start ecosystem.config.js`, never `pm2 restart`. Restart only re-runs the
> definition PM2 already holds in memory.

---

## Two production concerns worth fixing

Both were found on the live box and apply to any new one.

### The API is reachable from the open internet

The backend log showed continuous unsolicited scanning from many IPs:

```
POST /jsonrpc                             GET /api/kernels
GET /api/2.0/mlflow/experiments/list      GET /.well-known/security.txt
```

Bots probing for exposed Jupyter, MLflow and RPC services. Every one 404s, so
nothing leaks — but **this host holds live broker credentials and can place
trades.** It should not answer the public internet.

Restrict the security group (or Windows Firewall) to your own IP on ports 80 and
8000, or put the box behind a VPN. Check what is currently listening:

```powershell
Get-NetTCPConnection -State Listen | Where-Object LocalPort -in 80,8000 | Select-Object LocalAddress, LocalPort
```

`0.0.0.0` means every interface, i.e. the internet.

### The frontend serves the Vite dev server, not a build

`ecosystem.config.js` launches `vite/bin/vite.js` directly, so port 80 is the
**development** server:

```
->  Local:   http://localhost:80/
Server responded with status code 431
```

Two consequences: `npm run build` output is never actually served (the build
step does nothing for you), and the dev server is not hardened — that 431 is it
choking on oversized request headers, most likely from the bot traffic above.

For production, serve the static `frontend/dist` instead. Until that changes,
`npm run build` can be skipped on redeploy — the dev server compiles on demand.

---

## Pushing from the dev machine

The half this document assumed you had already done. Three standing rules for
this repository, and none of them is a style preference:

- **Never `git add -A`.** The tree holds ~1.2 GB of tick archives and 4,768
  `skip-worktree` files under `venv_win/`. Stage by explicit path.
- **Never commit `.env`.** It holds live MT5 credentials.
- **`dev` is the branch.** The boxes pull `origin dev`.

```powershell
git add backend/rl backend/risk/giveback.py backend/api/routes/rl.py tests/
```

```powershell
git --no-pager diff --cached --stat
```

Read that list before writing the message — it is the last point at which an
accidental 300 MB archive is cheap to remove.

```powershell
git commit -m "RL agents: modes A and B, charter governance, give-back rule"
git push origin dev
```

Then **demo first**, always. The two boxes run the same code against different
money.

---

## What THIS release needs on the box

*(Release of 2026-10-10: the Agents screen rebuild, the leakcheck fix, the
`trades.mt5_account` migration and the MetaTrader5 pin.)*

- **This is Path B, not Path A.** `requirements.txt` changed:
  `MetaTrader5` is now pinned to `==5.0.5120`. 5.0.6231 ships a
  `_core.cp312-win_amd64.pyd` that Windows Application Control refuses
  outright — *"DLL load failed while importing _core: An Application Control
  policy has blocked this file"* — which takes out every MT5 route at import.
  If MT5 works on this box today you could skip the `pip install` and nothing
  would break, but then the box and `requirements.txt` disagree and the next
  rebuild inherits the problem. Run it.
- **One new column, `trades.mt5_account`.** This is the
  `sqlite3.OperationalError: no such column: trades.mt5_account` that took the
  box down on 2026-10-09. `create_all` creates missing TABLES and never
  missing COLUMNS, so it is in `add_column_migrations` in
  `backend/data/database.py` and runs at startup: **restarting the backend is
  the migration.** `scripts/fix_trades_mt5_account.py` back-fills the values
  for existing rows, and is safe to re-run.
- **Four RL tables** (`rl_agents`, `rl_charters`, `rl_proposals`,
  `rl_adherence`) if this box has not had the previous release. Same story —
  the restart creates them.
- **Front-end changes**: the Agents page, the sidebar server switcher and
  `index.css`. This box serves through the Vite dev server, so `npm run build`
  is strictly optional — but the dev server only picks up the new source after
  `pm2 restart`, and building keeps `dist` honest. Do both.
- **`pytest.ini` is new** and affects nothing at runtime. It stops pytest
  collecting `backend/api/routes/mt5_test.py` — FastAPI route handlers named
  `test_mt5_*` — as if they were tests.

### Checking it worked

In this order, because each rules out a different failure:

```powershell
curl.exe -s localhost:8000/api/health/identity
```

Confirm `label`, `role` and `mt5_account` are the box you think you are on. A
picker row labelled "demo" pointing at live is the one mistake the client
cannot prevent.

```powershell
pm2 logs --lines 40
```

Look for `Database initialized`, and `[RL] registered N approved agent(s)` —
or nothing at all from `[RL]`, which is correct when no charter has been
approved yet.

Then open the Dashboard and read the new **"Why isn't this slot trading?"**
panel. Every slot you expect to be working should say `no setup found` or
`trading`. A slot that says **`not scanned`** is not running at all, and that
is a configuration answer rather than a market one.

That panel is also how to settle the demo-vs-live question directly: switch
boxes with the server picker and compare the two lists. If live shows
`every signal refused` with `below_broker_min_lot` against a slot that trades
on demo, the cause is the smaller balance, not the code.

## Quick reference

```powershell
# routine (no dependency changes)
git pull origin dev; cd frontend; npm run build; cd ..; pm2 restart all

# dependencies changed
pm2 stop all; git pull origin dev
.\venv\Scripts\Activate.ps1; pip install -r requirements.txt
cd frontend; npm ci; if ($?) { npm run build }; cd ..; pm2 restart all

# drifted, nothing worth keeping
pm2 stop all; git reset --hard origin/dev
deactivate; Remove-Item -Recurse -Force venv; python -m venv venv
.\venv\Scripts\Activate.ps1; pip install -r requirements.txt
cd frontend; npm ci; if ($?) { npm run build }; cd ..; pm2 restart all
```
