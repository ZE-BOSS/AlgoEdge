"""Audit backend: the real app on port 8011 against a COPY of the dev database.

Used by the "algoedge-audit" launch configuration so an end-to-end check never
writes agents, trades or settings into the developer's own database.

Everything runs under `if __name__ == "__main__"`. The training pool starts
its workers with "spawn", which re-imports this file in every child: without
the guard each worker started a second server, re-copied the database over
the first one's agents, and took the run down with it.
"""
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
site = ROOT / "venv_win" / "Lib" / "site-packages"
if site.exists():
    sys.path.insert(1, str(site))

COPY = Path(os.environ.get("TEMP", ".")) / "algoedge_audit.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{COPY.as_posix()}"
os.environ.setdefault("RL_JOB_WORKERS", "4")
# No Telegram, no investor jobs from an audit server.
os.environ["ALGOEDGE_AUDIT"] = "1"

if __name__ == "__main__":
    if not COPY.exists() or "--fresh" in sys.argv:
        shutil.copy2(ROOT / "algoedge.db", COPY)
    import uvicorn

    uvicorn.run("backend.main:app", host="127.0.0.1", port=8011, log_level="warning")
