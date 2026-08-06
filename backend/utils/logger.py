"""
backend/utils/logger.py

Structured logging with loguru.
All modules import `logger` from here for consistent formatting.
"""

import sys
from pathlib import Path

from loguru import logger

# Remove default handler
logger.remove()

# Console output — colored, human-readable
logger.add(
    sys.stderr,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> | <level>{message}</level>",
    level="DEBUG",
    colorize=True,
)

# File output — captures all [SIZER], [ENGINE], [MultiTP] debug log lines
_LOG_DIR = Path(__file__).parent.parent.parent / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
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

# Trade-specific log (high-value events only) disabled as per request
# logger.add(
#     _LOG_DIR / "trades.log",
#     format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {message}",
#     level="INFO",
#     rotation="5 MB",
#     retention="90 days",
#     filter=lambda record: "trade" in record["extra"].get("category", ""),
#     enqueue=True,
# )


def get_logger(name: str):
    """Get a logger bound with a module name for context."""
    return logger.bind(module=name)
