"""
backend/utils/timeutils.py

Session detection and timezone utilities for trading time management.
Source: SMC_Strategy.md Section 12
"""

import os
from datetime import datetime, timezone, timedelta
from typing import Optional, Literal


# ── Timezone Configuration ───────────────────────────────────────────────────
# Default to Nigerian Time (WAT / UTC+1) as requested.
# Can be overridden via environment variable TZ_OFFSET_HOURS.
TZ_OFFSET = int(os.getenv("TZ_OFFSET_HOURS", 1))
LOCAL_TZ = timezone(timedelta(hours=TZ_OFFSET))


# ── Session Windows (Local Time - Default UTC+1) ─────────────────────────────
# Adjusted from GMT to default UTC+1.
SESSIONS = {
    "LONDON": {"start": 8, "end": 16, "kill_start": 8, "kill_end": 10},
    "NY":     {"start": 13, "end": 21, "kill_start": 13, "kill_end": 15},
    "ASIAN":  {"start": 23, "end": 7},   # Blocked — no trades
}

BLOCKED_WINDOWS = [
    {"name": "ASIAN",          "start": 23, "end": 7},
    {"name": "PRE_LONDON",     "start": 7,  "end": 8},
    {"name": "FRIDAY_CLOSE",   "day": 4, "start": 21, "end": 24},  # Friday from 21:00 Local
    {"name": "SUNDAY_OPEN",    "day": 6, "start": 22, "end": 24},  # Sunday 22:00-24:00 Local
]


def get_utc_now() -> datetime:
    """Current time in UTC."""
    return datetime.now(timezone.utc)


def get_local_time(dt: Optional[datetime] = None) -> datetime:
    """Convert a UTC datetime to configured Local Time."""
    if dt is None:
        dt = get_utc_now()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(LOCAL_TZ)


def get_current_session(dt: Optional[datetime] = None) -> Optional[str]:
    """
    Returns the active session name ('LONDON', 'NY', 'LONDON/NY') or None.
    When London and NY overlap, returns 'LONDON/NY'.
    """
    dt = get_local_time(dt)
    hour = dt.hour

    in_london = SESSIONS["LONDON"]["start"] <= hour < SESSIONS["LONDON"]["end"]
    in_ny = SESSIONS["NY"]["start"] <= hour < SESSIONS["NY"]["end"]

    if in_london and in_ny:
        return "LONDON/NY"
    elif in_london:
        return "LONDON"
    elif in_ny:
        return "NY"
    return None


def is_kill_zone(dt: Optional[datetime] = None) -> bool:
    """Returns True if current time is in a London or NY kill zone."""
    dt = get_local_time(dt)
    hour = dt.hour

    london_kz = SESSIONS["LONDON"]["kill_start"] <= hour < SESSIONS["LONDON"]["kill_end"]
    ny_kz = SESSIONS["NY"]["kill_start"] <= hour < SESSIONS["NY"]["kill_end"]
    return london_kz or ny_kz


def is_session_blocked(dt: Optional[datetime] = None, instrument_type: str = "FOREX") -> tuple[bool, str]:
    """
    Returns (is_blocked, reason) if current time falls in a blocked window.
    Synthetics trade 24/7 and are never blocked.
    """
    if instrument_type == "SYNTHETIC":
        return False, ""

    dt = get_local_time(dt)
    hour = dt.hour
    weekday = dt.weekday()  # 0=Monday, 4=Friday, 6=Sunday

    # Asian session block
    asian = BLOCKED_WINDOWS[0]
    if hour >= asian["start"] or hour < asian["end"]:
        return True, "Asian session"

    # Pre-London
    pre = BLOCKED_WINDOWS[1]
    if pre["start"] <= hour < pre["end"]:
        return True, "Pre-London accumulation"

    # Friday close
    fri = BLOCKED_WINDOWS[2]
    if weekday == fri["day"] and hour >= fri["start"]:
        return True, "Friday close"

    # Sunday open
    sun = BLOCKED_WINDOWS[3]
    if weekday == sun["day"] and sun["start"] <= hour < sun["end"]:
        return True, "Sunday open gap risk"

    return False, ""


def is_news_blocked(
    current_time: datetime,
    news_events: list[dict],
    buffer_minutes: int = 30,
    instrument_type: str = "FOREX"
) -> tuple[bool, Optional[dict]]:
    """
    Returns (is_blocked, event) if within buffer_minutes of a HIGH-impact event.
    Synthetics are immune to news.
    """
    if instrument_type == "SYNTHETIC":
        return False, None

    for event in news_events:
        if event.get("impact") != "HIGH":
            continue
        # Assuming event["time"] is UTC
        delta = abs((event["time"] - current_time).total_seconds() / 60)
        if delta <= buffer_minutes:
            return True, event
    return False, None


def detect_session(timestamp) -> str:
    """
    Detect trading session from a timestamp (datetime or int/float/numpy).
    Returns 'LONDON', 'NY', 'OVERLAP', 'ASIAN', or '24/7'.
    Used by the backtester for session tagging on trades.
    """
    import pandas as pd
    import numpy as np
    
    if isinstance(timestamp, (int, float)):
        try:
            dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (OSError, ValueError):
            return "UNKNOWN"
    elif isinstance(timestamp, datetime):
        dt = timestamp
    elif isinstance(timestamp, pd.Timestamp):
        dt = timestamp.to_pydatetime()
    elif isinstance(timestamp, np.datetime64):
        dt = pd.to_datetime(timestamp).to_pydatetime()
    else:
        return "UNKNOWN"

    session = get_current_session(dt)
    if session:
        return session

    # Check Asian block
    dt_local = get_local_time(dt)
    hour = dt_local.hour
    if hour >= SESSIONS["ASIAN"]["start"] or hour < SESSIONS["ASIAN"]["end"]:
        return "ASIAN"

    return "UNKNOWN"

