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
| every pull conflicts on `__pycache__` | compiled `.pyc` files are tracked |

**Fix once, on a development machine, then push:**

```powershell
git rm -r --cached venv --quiet
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
