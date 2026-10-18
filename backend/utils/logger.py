"""
backend/utils/logger.py

Structured logging with loguru.
All modules import `logger` from here for consistent formatting.
"""

import os
import sys
from pathlib import Path

from loguru import logger

# Remove default handler
logger.remove()

# Console output — colored, human-readable.
#
# [L1-opt] Level now comes from LOG_LEVEL (default INFO) instead of being
# hardcoded to DEBUG.
#
# The hardcoded DEBUG was a measured bottleneck, not just noise. Every
# `logger.debug(...)` was formatted, ANSI-colorized and written to stderr —
# and the backtest engine emits debug lines per position event
# ("[ENGINE] Position opened", "[BREAKEVEN] buffer inputs", one risk-decision
# JSON per signal). Over a 50,000-bar run that is hundreds of thousands of
# colorized writes to a captured stderr pipe, which dominated the run.
#
# The .env already said LOG_LEVEL=INFO; the sink simply ignored it. Set
# LOG_LEVEL=DEBUG to get the old behaviour back when actually debugging.
_CONSOLE_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logger.add(
    sys.stderr,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> | <level>{message}</level>",
    level=_CONSOLE_LEVEL,
    colorize=True,
)

# File output — captures all [SIZER], [ENGINE], [MultiTP] debug log lines
_LOG_DIR = Path(__file__).parent.parent.parent / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)


IS_WORKER = False


def _worker_filter(record) -> bool:
    # A training rollout runs the real risk engine and sizer on every decision,
    # and both narrate at INFO/WARNING ("Scaling down risk...", "post-cap lot
    # 0.0 is below volume_min") - correct behaviour, hundreds of lines a
    # second, and none of it actionable. Errors from them still come through.
    name = record["name"] or ""
    if name.startswith(("backend.risk", "backend.backtester")):
        return record["level"].no >= 40
    return True


def disable_wmi() -> None:
    """Stop `platform` querying WMI in this process.

    CPython 3.12 on Windows answers `platform.system()` / `uname()` with a WMI
    query (`_wmi`), and in a SPAWNED pool worker that query invalidates the
    pool's own queue semaphore: the worker's next `call_queue.get()` dies with
    "OSError: [WinError 6] The handle is invalid", the pool is marked broken,
    and every queued job fails with BrokenProcessPool. `truststore` calls
    `platform.system()` on import, and `aiohttp` / the Telegram service pull it
    in, so any training job that touched them took the run down - 38 of 40
    jobs lost (measured 2026-10-18; reproduced with a bare
    `platform.system()` in an initializer, no project code).

    `_wmi_query` raising OSError is the "WMI not available" path `platform`
    already handles: it falls back to the `ver` command, slower once and
    harmless. (Setting `platform._wmi = None` does NOT work - the query then
    raises AttributeError, which `platform` does not catch.) Called first in
    every worker initializer.
    """
    try:
        import platform

        def _no_wmi(*_args):
            raise OSError("WMI disabled in worker processes (see logger.disable_wmi)")

        platform._wmi_query = _no_wmi             # type: ignore[attr-defined]
    except Exception:                                 # noqa: BLE001
        pass


def configure_worker(role: str = "worker") -> None:
    """Re-point logging for a child process. The process pools pass this as
    their `initializer`, so it runs first in every child.

    A child process re-imports this module, and before this existed it kept
    its own handles on backend.log and trades.log. On Windows a file another
    process holds open cannot be renamed, so the first rotation after a child
    started failed - and every later write in BOTH processes retried it and
    printed a PermissionError traceback. trades.log sat stuck at its
    2026-09-22 name, growing without bound, and each training decision paid
    for a traceback. Now a worker writes only its own file.

    Explicit rather than "am I a child process?", because `uvicorn --reload`
    serves the API itself from a child process.
    """
    global IS_WORKER
    IS_WORKER = True
    disable_wmi()
    logger.remove()
    logger.add(sys.stderr, level="WARNING", filter=_worker_filter, colorize=True,
               format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | "
                      "<cyan>{name}</cyan>:<cyan>{function}</cyan> | <level>{message}</level>")
    logger.add(_LOG_DIR / f"{role}-{os.getpid()}.log", level="INFO", filter=_worker_filter,
               format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function} | {message}",
               # `delay`: a worker that logs nothing leaves no file. Every job
               # worker is a new pid, so without it an empty file per process
               # piled up in logs/ on every run.
               rotation="10 MB", retention=3, encoding="utf-8", delay=True)


def get_logger(name: str):
    """Get a logger bound with a module name for context."""
    return logger.bind(module=name)


# [Phase 13 section G / V2] WebSocket sink.
#
# Until this line existed, the three sinks above were the whole story: stderr
# and two files. Nothing carried a log record to the frontend, so the Live Logs
# panel only ever showed the handful of events that bot_service.log_system_event
# broadcast explicitly — which is why almost everything stayed in the terminal.
#
# Installed last so it captures records from every module that imports this one,
# and imported lazily inside the function to avoid a circular import
# (services.log_stream imports loguru, which is fine, but services/ generally
# imports utils/).
def _install_ws_sink() -> None:
    try:
        from backend.services.log_stream import install_sink
        install_sink(min_level="DEBUG")
    except Exception as e:  # never let logging setup break startup
        logger.warning(f"WebSocket log sink not installed: {e}")


def _install_shared_file_sinks() -> None:
    """backend.log and trades.log. The API process owns these - see
    `configure_worker` for what happened when a child held them too.

    `delay=True`: the file is opened on the first write, not when the sink is
    added. A spawned worker re-imports this module, and without the delay it
    opened both files for the moment between import and `configure_worker` -
    long enough for a rotation in the API process to fail (seen 2026-10-17
    while a training worker was starting). A worker never writes before
    `configure_worker` runs, so now it never opens them at all.
    """
    logger.add(
        _LOG_DIR / "backend.log",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function} | {message}",
        level="INFO",
        rotation="10 MB",
        retention="30 days",
        enqueue=True,
        encoding="utf-8",
        delay=True,
    )
    # High-value trade/risk events only
    logger.add(
        _LOG_DIR / "trades.log",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {message}",
        level="INFO",
        rotation="5 MB",
        retention="90 days",
        filter=lambda rec: any(k in rec["message"] for k in ("[SIZER]", "[ENGINE]", "[MultiTP]", "[RISK]", "Opened", "Closed", "TP", "SL")),
        enqueue=True,
        encoding="utf-8",
        delay=True,
    )


_install_shared_file_sinks()
_install_ws_sink()