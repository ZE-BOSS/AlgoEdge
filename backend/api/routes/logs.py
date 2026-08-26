"""
backend/api/routes/logs.py

[V3 / Phase 13 Part G] Log retrieval.

Before this, `logs/backend.log` rotated at 10 MB with 30-day retention and was
never exposed over HTTP — there was no way to pull a past session's logs from
the platform at all. Two sources are served here and the distinction is
deliberate:

  * **buffer** — the in-memory ring in `services/log_stream.py`. Structured,
    carries session ids, categories and formatted tracebacks. The live view.
  * **file**   — `logs/*.log`, parsed back out of loguru's format string. Goes
    further back than the ring (30-day retention) but is flatter: no session id,
    no traceback grouping.

The buffer is the default because it is the richer of the two. The file is what
you reach for when the thing you want happened yesterday.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from backend.api.deps import get_current_user
from backend.data.models import User
from backend.services.log_stream import (
    available_log_files,
    log_hub,
    read_log_file,
)
from backend.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["logs"])


@router.get("/logs")
async def get_logs(
    session_id: str | None = None,
    level: str | None = Query(None, description="Minimum level: DEBUG/INFO/WARNING/ERROR/CRITICAL"),
    category: str | None = None,
    search: str | None = None,
    since: float | None = Query(None, description="Epoch seconds — only records at or after this time"),
    source: str = Query("buffer", pattern="^(buffer|file)$"),
    file: str = Query("backend.log", description="Log file name when source=file"),
    limit: int = Query(500, le=5000),
    current_user: User = Depends(get_current_user),
):
    """
    Query backend logs.

    `level` is a FLOOR, not an exact match — asking for WARNING returns warnings
    and worse, which is what anyone hunting a bug actually wants.
    """
    if source == "file":
        records = read_log_file(name=file, level=level, search=search, limit=limit)
        return {
            "source": "file",
            "file": file,
            "total": len(records),
            "records": records,
            # Named so the client can say "showing the tail" rather than
            # implying it has the whole file.
            "truncated": len(records) >= limit,
        }

    records = log_hub.query(
        level=level,
        category=category,
        session_id=session_id,
        search=search,
        since=since,
        limit=limit,
    )
    return {
        "source": "buffer",
        "total": len(records),
        "records": records,
        "stats": log_hub.stats,
    }


@router.get("/logs/sessions")
async def get_log_sessions(current_user: User = Depends(get_current_user)):
    """
    Sessions the ring buffer still holds records for.

    A session is started per backtest/bot run, so this is the picker behind
    "show me the logs for the run that failed" — the question that motivated
    this endpoint existing at all.
    """
    return {"sessions": log_hub.sessions(), "stats": log_hub.stats}


@router.get("/logs/files")
async def get_log_files(current_user: User = Depends(get_current_user)):
    """What's on disk, so the UI can show size and age before pulling a tail."""
    return {"files": available_log_files()}


@router.get("/logs/stats")
async def get_log_stats(current_user: User = Depends(get_current_user)):
    """
    Buffer health.

    Surfaced rather than hidden because the sink is deliberately lossy under
    pressure — a logging path that can block can deadlock the thing it
    instruments — and a client showing logs should be able to say when it is not
    showing all of them.
    """
    return log_hub.stats
