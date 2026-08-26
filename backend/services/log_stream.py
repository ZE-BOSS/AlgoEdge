"""
backend/services/log_stream.py

[Phase 13 §G] Backend logs -> frontend.

The defect this closes (V2): `utils/logger.py` registers three loguru sinks —
stderr, `logs/backend.log`, `logs/trades.log` — and none of them is a WebSocket
sink. The frontend's Live Logs panel therefore only ever received what
`bot_service.log_system_event()` explicitly broadcast: a few dozen call sites
out of thousands of log statements. Everything else stayed in the terminal.

Three pieces here:

  1. A loguru sink that captures every record into a bounded ring buffer.
  2. A broadcaster that pushes new records over the existing WebSocket, batched
     so a debug-level firehose cannot saturate the socket.
  3. A session index, so "show me the logs from that run this morning" is a
     query rather than an ssh session.

Design constraint that shapes all of it: **the sink runs on whatever thread
logged**, including inside `asyncio.to_thread` worker threads where the
simulation runs. It therefore must not touch the event loop directly. It
appends to a deque (thread-safe for append/popleft) and the async broadcaster
drains it on its own schedule.
"""

from __future__ import annotations

import asyncio
import re
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ring buffer size. 5,000 records at ~200 bytes is ~1 MB resident — enough to
# backfill a freshly-opened page with real context, small enough to ignore.
RING_SIZE = 5000

# Broadcast cadence. Log lines are not frames; 250 ms batching is imperceptible
# to a reader and collapses a burst of hundreds of lines into one message.
BROADCAST_INTERVAL_S = 0.25
MAX_BATCH = 200

LEVELS = ("TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL")
LEVEL_ORDER = {name: i for i, name in enumerate(LEVELS)}

# `[SIZER]`, `[ENGINE]`, `[RISK]`, `[VWAP]` ... the codebase already tags its
# high-value lines this way, so category is recovered rather than invented.
_CATEGORY_RE = re.compile(r"\[([A-Z][A-Z0-9_]{1,24})\]")


class LogHub:
    """
    Process-wide log fan-out. One instance, created at import.

    Not per-user: the backend is single-tenant per process here, and a log line
    emitted deep inside the risk engine has no user context to attach anyway.
    Broadcasts go to every connected socket.
    """

    def __init__(self, ring_size: int = RING_SIZE):
        self._ring: deque[dict] = deque(maxlen=ring_size)
        self._pending: deque[dict] = deque(maxlen=ring_size)
        self._lock = threading.Lock()
        self._seq = 0
        self._task: asyncio.Task | None = None
        self._manager = None
        # session_id -> {"id", "label", "started_at", "ended_at", "counts"}
        self._sessions: dict[str, dict] = {}
        self._active_session: str | None = None

    # ── session tracking ────────────────────────────────────────────────
    def start_session(self, label: str, kind: str = "backtest") -> str:
        """
        Open a log session. Every record captured until `end_session` carries
        its id, which is what makes "the logs from that run" retrievable later.
        """
        sid = f"{kind}-{uuid.uuid4().hex[:8]}"
        with self._lock:
            self._sessions[sid] = {
                "id": sid,
                "kind": kind,
                "label": label,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "ended_at": None,
                "counts": {},
            }
            self._active_session = sid
            # Bound the session index the same way the ring is bounded — a
            # long-lived process should not accumulate sessions forever.
            if len(self._sessions) > 200:
                oldest = sorted(self._sessions.values(), key=lambda s: s["started_at"])[:50]
                for s in oldest:
                    self._sessions.pop(s["id"], None)
        return sid

    def end_session(self, session_id: str | None = None) -> None:
        with self._lock:
            sid = session_id or self._active_session
            if sid and sid in self._sessions:
                self._sessions[sid]["ended_at"] = datetime.now(timezone.utc).isoformat()
            if sid == self._active_session:
                self._active_session = None

    def sessions(self) -> list[dict]:
        with self._lock:
            return sorted(self._sessions.values(), key=lambda s: s["started_at"], reverse=True)

    # ── capture ─────────────────────────────────────────────────────────
    def sink(self, message: Any) -> None:
        """
        loguru sink. Called on the logging thread — including worker threads —
        so this does exactly one thing: append. No awaits, no loop access, no
        network. Anything heavier here would make logging a source of latency
        in the simulation loop.
        """
        try:
            rec = message.record
            text = rec["message"]
            level = rec["level"].name
            m = _CATEGORY_RE.search(text)

            self._seq += 1
            entry = {
                "seq": self._seq,
                "time": rec["time"].isoformat(),
                "ts": rec["time"].timestamp(),
                "level": level,
                "category": m.group(1) if m else rec["name"].split(".")[-1].upper(),
                "module": rec["name"],
                "function": rec["function"],
                "line": rec["line"],
                "message": text,
                "session_id": self._active_session,
            }
            self._ring.append(entry)
            self._pending.append(entry)

            sid = self._active_session
            if sid:
                sess = self._sessions.get(sid)
                if sess is not None:
                    sess["counts"][level] = sess["counts"].get(level, 0) + 1
        except Exception:
            # A logging sink that can raise turns every log call into a
            # potential crash site. Swallow unconditionally.
            pass

    # ── broadcast ───────────────────────────────────────────────────────
    def attach(self, manager: Any) -> None:
        """Register the WebSocket ConnectionManager and start the pump."""
        self._manager = manager
        if self._task is None or self._task.done():
            try:
                self._task = asyncio.create_task(self._pump())
            except RuntimeError:
                # No running loop yet (import-time). The FastAPI startup hook
                # calls attach() again once the loop exists.
                self._task = None

    async def _pump(self) -> None:
        while True:
            await asyncio.sleep(BROADCAST_INTERVAL_S)
            if self._manager is None or not self._pending:
                continue
            batch = []
            while self._pending and len(batch) < MAX_BATCH:
                batch.append(self._pending.popleft())
            if not batch:
                continue
            try:
                await self._manager.broadcast_all({"type": "log_batch", "logs": batch})
            except Exception:
                # Dropping a batch is strictly better than stalling the pump —
                # the records are still in the ring and still on disk.
                pass

    # ── query ───────────────────────────────────────────────────────────
    def query(
        self,
        level: str | None = None,
        category: str | None = None,
        session_id: str | None = None,
        search: str | None = None,
        since: float | None = None,
        limit: int = 500,
    ) -> list[dict]:
        """
        Filter the ring buffer. `level` is a FLOOR, not an exact match — asking
        for WARNING means "warnings and worse", which is what anyone hunting a
        bug actually wants.
        """
        floor = LEVEL_ORDER.get((level or "").upper())
        needle = search.lower() if search else None
        cat = category.upper() if category else None

        out = []
        for e in reversed(self._ring):
            if floor is not None and LEVEL_ORDER.get(e["level"], 0) < floor:
                continue
            if cat and e["category"] != cat:
                continue
            if session_id and e["session_id"] != session_id:
                continue
            if since is not None and e["ts"] < since:
                continue
            if needle and needle not in e["message"].lower():
                continue
            out.append(e)
            if len(out) >= limit:
                break
        out.reverse()   # chronological for reading
        return out

    @property
    def stats(self) -> dict:
        return {
            "buffered": len(self._ring),
            "pending": len(self._pending),
            "sessions": len(self._sessions),
            "active_session": self._active_session,
            "attached": self._manager is not None,
        }


log_hub = LogHub()


# ─────────────────────────────────────────────────────────────────────────
# On-disk history — beyond what the ring holds
# ─────────────────────────────────────────────────────────────────────────

_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"

# Matches utils/logger.py's file format:
#   2026-08-23 09:12:33.123 | INFO     | module:function | message
_FILE_LINE_RE = re.compile(
    r"^(?P<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)\s+\|\s+"
    r"(?P<level>\w+)\s+\|\s+(?P<loc>[^|]+?)\s+\|\s+(?P<message>.*)$"
)


def read_log_file(
    name: str = "backend.log",
    level: str | None = None,
    search: str | None = None,
    limit: int = 2000,
) -> list[dict]:
    """
    Read historical records straight from the rotated log files.

    This is what answers "show me yesterday's session": the ring buffer only
    survives as long as the process, but `logs/backend.log` is retained 30 days.

    Read from the END backwards, because the interesting lines are almost always
    the recent ones and these files reach 10 MB before rotating.
    """
    # Path traversal guard: `name` reaches here from a query parameter, and
    # without this it would happily read any file on the box.
    safe = Path(name).name
    path = _LOG_DIR / safe
    if not path.exists() or path.suffix not in (".log", ""):
        return []

    floor = LEVEL_ORDER.get((level or "").upper())
    needle = search.lower() if search else None

    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            lines = deque(fh, maxlen=50_000)
    except OSError:
        return []

    out: list[dict] = []
    for raw in reversed(lines):
        m = _FILE_LINE_RE.match(raw.rstrip("\n"))
        if not m:
            continue
        lvl = m.group("level")
        if floor is not None and LEVEL_ORDER.get(lvl, 0) < floor:
            continue
        msg = m.group("message")
        if needle and needle not in msg.lower():
            continue
        cat = _CATEGORY_RE.search(msg)
        loc = m.group("loc").strip()
        out.append({
            "time": m.group("time"),
            "level": lvl,
            "category": cat.group(1) if cat else loc.split(":")[0].split(".")[-1].upper(),
            "module": loc,
            "message": msg,
            "session_id": None,
        })
        if len(out) >= limit:
            break
    out.reverse()
    return out


def available_log_files() -> list[dict]:
    if not _LOG_DIR.exists():
        return []
    files = []
    for p in sorted(_LOG_DIR.glob("*.log"), key=lambda f: f.stat().st_mtime, reverse=True):
        st = p.stat()
        files.append({
            "name": p.name,
            "size_bytes": st.st_size,
            "modified": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
        })
    return files


def install_sink(min_level: str = "DEBUG") -> None:
    """
    Register the hub as a loguru sink.

    Called from utils/logger.py at import. Guarded against double-install,
    because module reloads under `--reload` would otherwise stack sinks and
    duplicate every line once per reload.
    """
    from loguru import logger as _logger

    if getattr(install_sink, "_installed", False):
        return
    _logger.add(log_hub.sink, level=min_level, enqueue=False, format="{message}")
    install_sink._installed = True
