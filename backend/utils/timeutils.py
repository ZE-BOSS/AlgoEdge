"""
backend/utils/timeutils.py

Session detection and timezone utilities for trading time management.
Source: SMC_Strategy.md Section 12
"""

from datetime import datetime, timezone, timedelta
from typing import Optional, Literal


# ── Session Windows (GMT) ────────────────────────────────────────────────────

SESSIONS = {
    "LONDON": {"start": 7, "end": 15, "kill_start": 7, "kill_end": 9},
    "NY":     {"start": 12, "end": 20, "kill_start": 12, "kill_end": 14},
    "ASIAN":  {"start": 22, "end": 6},   # Blocked — no trades
}

BLOCKED_WINDOWS = [
    {"name": "ASIAN",          "start": 22, "end": 6},
    {"name": "PRE_LONDON",     "start": 6,  "end": 7},
    {"name": "FRIDAY_CLOSE",   "day": 4, "start": 20, "end": 24},  # Friday from 20:00
    {"name": "SUNDAY_OPEN",    "day": 6, "start": 21, "end": 23},  # Sunday 21:00-23:00
]


def get_utc_now() -> datetime:
    """Current time in UTC."""
    return datetime.now(timezone.utc)


def get_current_session(dt: Optional[datetime] = None) -> Optional[str]:
    """
    Returns the active session name ('LONDON', 'NY', 'LONDON/NY') or None.
    When London and NY overlap (12:00-15:00 GMT), returns 'LONDON/NY'.
    """
    dt = dt or get_utc_now()
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
    dt = dt or get_utc_now()
    hour = dt.hour

    london_kz = SESSIONS["LONDON"]["kill_start"] <= hour < SESSIONS["LONDON"]["kill_end"]
    ny_kz = SESSIONS["NY"]["kill_start"] <= hour < SESSIONS["NY"]["kill_end"]
    return london_kz or ny_kz


def is_session_blocked(dt: Optional[datetime] = None) -> tuple[bool, str]:
    """
    Returns (is_blocked, reason) if current time falls in a blocked window.
    """
    dt = dt or get_utc_now()
    hour = dt.hour
    weekday = dt.weekday()  # 0=Monday, 4=Friday, 6=Sunday

    # Asian session block
    if hour >= 22 or hour < 6:
        return True, "Asian session (22:00–06:00 GMT)"

    # Pre-London
    if 6 <= hour < 7:
        return True, "Pre-London accumulation (06:00–07:00 GMT)"

    # Friday close
    if weekday == 4 and hour >= 20:
        return True, "Friday close (20:00+ GMT)"

    # Sunday open
    if weekday == 6 and 21 <= hour < 23:
        return True, "Sunday open gap risk (21:00–23:00 GMT)"

    return False, ""


def is_news_blocked(
    current_time: datetime,
    news_events: list[dict],
    buffer_minutes: int = 30,
) -> tuple[bool, Optional[dict]]:
    """
    Returns (is_blocked, event) if within buffer_minutes of a HIGH-impact event.
    news_events: list of {"time": datetime, "impact": "HIGH/MED/LOW", "title": str}
    """
    for event in news_events:
        if event.get("impact") != "HIGH":
            continue
        delta = abs((event["time"] - current_time).total_seconds() / 60)
        if delta <= buffer_minutes:
            return True, event
    return False, None


def detect_session(timestamp) -> str:
    """
    Detect trading session from a timestamp (datetime or int/float).
    Returns 'LONDON', 'NY', 'OVERLAP', 'ASIAN', or '24/7'.
    Used by the backtester for session tagging on trades.
    """
    if isinstance(timestamp, (int, float)):
        try:
            dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (OSError, ValueError):
            return "UNKNOWN"
    elif isinstance(timestamp, datetime):
        dt = timestamp
    else:
        return "UNKNOWN"

    session = get_current_session(dt)
    if session:
        return session

    hour = dt.hour
    if hour >= 22 or hour < 6:
        return "ASIAN"

    return "UNKNOWN"

