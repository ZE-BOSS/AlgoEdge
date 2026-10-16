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
    `_is_worker_process` for what happened when a child opened them too."""
    logger.add(
        _LOG_DIR / "backend.log",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function} | {message}",
        level="INFO",
        rotation="10 MB",
        retention="30 days",
        enqueue=True,
        encoding="utf-8",
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
    )


_install_shared_file_sinks()
_install_ws_sink()