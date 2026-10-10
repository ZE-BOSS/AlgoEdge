"""
scripts/fix_trades_mt5_account.py

Add `trades.mt5_account` to whichever SQLite database actually holds the
trades, and say which one that was.

WHY THIS SCRIPT EXISTS RATHER THAN A ONE-LINER
-----------------------------------------------
`DATABASE_URL` defaults to `sqlite+aiosqlite:///algoedge.db` - a RELATIVE
path, resolved against the working directory of whatever started the process.
pm2 does not necessarily start the backend from the repository root, so the
file the app opens is not always the `algoedge.db` sitting next to this
script. The symptom is exactly the one that prompted this: the backend logs
`no such column: trades.mt5_account` while a hand-run ALTER on the obvious
file reports `duplicate column name`.

So this looks at every candidate, reports what it finds, and only touches the
ones that genuinely need it.

SAFE BY CONSTRUCTION
--------------------
It only ever runs `ADD COLUMN` and `CREATE INDEX IF NOT EXISTS`. It never
drops, never rewrites a row, and skips any database that already has the
column. Run it twice and the second run reports "already present" for
everything.

    py -3.12 scripts/fix_trades_mt5_account.py            # report only
    py -3.12 scripts/fix_trades_mt5_account.py --apply    # make the change
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

COLUMN = "mt5_account"
TABLE = "trades"


def configured_url() -> str | None:
    """What the app itself would open, read the same way the app reads it."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from backend.config import settings
        return settings.database.url
    except Exception:
        return os.getenv("DATABASE_URL")


def path_from_url(url: str) -> Path | None:
    """`sqlite+aiosqlite:///algoedge.db` -> Path('algoedge.db')."""
    if not url or not url.startswith("sqlite"):
        return None
    tail = url.split("///", 1)[-1]
    return Path(tail) if tail else None


def candidates(root: Path) -> list[Path]:
    found = []
    url_path = path_from_url(configured_url() or "")
    if url_path:
        # Resolved against the CURRENT working directory, exactly as the app
        # would resolve it - which is the whole point.
        found.append(Path.cwd() / url_path if not url_path.is_absolute() else url_path)
    for p in root.rglob("*.db"):
        if any(part in {"venv", "venv_win", "node_modules", ".git"} for part in p.parts):
            continue
        found.append(p)
    seen, out = set(), []
    for p in found:
        try:
            key = p.resolve()
        except OSError:
            continue
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def inspect(path: Path) -> dict:
    info = {"path": str(path), "exists": path.exists(), "bytes": 0,
            "has_table": False, "rows": None, "has_column": None}
    if not path.exists():
        return info
    info["bytes"] = path.stat().st_size
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return info
    try:
        cur = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (TABLE,))
        info["has_table"] = cur.fetchone() is not None
        if info["has_table"]:
            cols = {r[1] for r in con.execute(f"PRAGMA table_info({TABLE})")}
            info["has_column"] = COLUMN in cols
            info["rows"] = con.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
    except sqlite3.Error as exc:
        info["error"] = str(exc)
    finally:
        con.close()
    return info


def apply(path: Path) -> str:
    con = sqlite3.connect(path)
    try:
        con.execute(f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN} BIGINT")
        con.execute(f"CREATE INDEX IF NOT EXISTS ix_{TABLE}_{COLUMN} "
                    f"ON {TABLE}({COLUMN})")
        con.commit()
        return "added"
    except sqlite3.OperationalError as exc:
        if "duplicate column" in str(exc).lower():
            return "already present"
        return f"FAILED: {exc}"
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="actually add the column (default is report only)")
    args = ap.parse_args()

    url = configured_url()
    print(f"DATABASE_URL the app would use: {url}")
    print(f"working directory             : {Path.cwd()}")
    if url and not url.startswith("sqlite"):
        print("\nNot SQLite - this script only handles SQLite. For Postgres run:")
        print(f"  ALTER TABLE {TABLE} ADD COLUMN {COLUMN} BIGINT;")
        return 0
    print()

    rows = [inspect(p) for p in candidates(Path(__file__).resolve().parent.parent)]
    rows = [r for r in rows if r["exists"]]
    if not rows:
        print("no .db files found")
        return 1

    print(f"{'database':52s} {'MB':>8s} {'trades':>8s} {COLUMN:>14s}")
    for r in sorted(rows, key=lambda r: -(r["rows"] or 0)):
        state = ("-" if not r["has_table"]
                 else "present" if r["has_column"] else "MISSING")
        print(f"{r['path'][:52]:52s} {r['bytes'] / 1e6:8.1f} "
              f"{str(r['rows'] if r['rows'] is not None else '-'):>8s} {state:>14s}")

    need = [r for r in rows if r["has_table"] and r["has_column"] is False]
    print()
    if not need:
        print("Every database that has a `trades` table already has the column.")
        print("If the backend still reports `no such column`, it is opening a file "
              "that is not in this list - check pm2's working directory:")
        print("  pm2 describe algoedge-backend")
        return 0

    print(f"{len(need)} database(s) need the column:")
    for r in need:
        print(f"  {r['path']}  ({r['rows']} trades)")
    if not args.apply:
        print("\nReport only. Re-run with --apply to add it.")
        return 0

    print()
    for r in need:
        print(f"  {r['path']}: {apply(Path(r['path']))}")
    print("\nDone. Restart the backend:  pm2 restart algoedge-backend")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
