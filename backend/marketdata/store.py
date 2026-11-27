"""
backend/marketdata/store.py

The shared 1-minute store every instance reads (demo, live, flip).

    <MARKETDATA_DIR>/<future>/<YYYY-MM-DD>.parquet   one UTC day of minutes (history)
    <MARKETDATA_DIR>/live/<future>.parquet           the streamed minutes, last few days

Columns (index: UTC minute, naive DatetimeIndex)
    open high low close volume     from ohlcv-1m or built from trades
    buy sell big_buy big_sell pv   aggressor flow from the trades schema;
                                   NaN where only ohlcv-1m was fetched

Days are written by the live instance's backfill (history.py) and by the live
feed (live.py); readers never write. Every write is atomic (temp + rename) so a
reader never sees half a file.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

_ONE_DAY = timedelta(days=1)
COLS = ["open", "high", "low", "close", "volume", "buy", "sell", "big_buy", "big_sell", "pv"]
FLOW = ["buy", "sell", "big_buy", "big_sell", "pv"]

_mem: dict[tuple[str, str], tuple[float, pd.DataFrame]] = {}
_lock = threading.Lock()


def root() -> Path:
    return Path(os.getenv("MARKETDATA_DIR", "/opt/algoedge/shared/marketdata"))


def _safe(future: str) -> str:
    return future.replace("/", "_")


def day_path(future: str, day: date) -> Path:
    return root() / _safe(future) / f"{day.isoformat()}.parquet"


def live_path(future: str) -> Path:
    return root() / "live" / f"{_safe(future)}.parquet"


def empty() -> pd.DataFrame:
    return pd.DataFrame({k: pd.Series(dtype=float) for k in COLS}, index=pd.DatetimeIndex([], name="minute"))


def _atomic(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o775)
    except OSError:
        pass
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    df.to_parquet(tmp)
    os.replace(tmp, path)


def conform(df: pd.DataFrame) -> pd.DataFrame:
    """Store shape: the COLS, float, sorted unique naive-UTC minute index."""
    if df is None or not len(df):
        return empty()
    out = df.copy()
    if not isinstance(out.index, pd.DatetimeIndex):
        out.index = pd.to_datetime(out.index, utc=True)
    if out.index.tz is not None:
        out.index = out.index.tz_convert("UTC").tz_localize(None)
    out.index = out.index.floor("1min")
    for k in COLS:
        if k not in out.columns:
            out[k] = np.nan
    out = out[COLS].astype(float)
    out = out[~out.index.duplicated(keep="last")].sort_index()
    out.index.name = "minute"
    return out


def write_day(future: str, day: date, df: pd.DataFrame) -> None:
    lo = pd.Timestamp(day)
    part = conform(df)
    part = part[(part.index >= lo) & (part.index < lo + pd.Timedelta(days=1))]
    _atomic(part, day_path(future, day))
    with _lock:
        _mem.pop((future, day.isoformat()), None)


def write_live(future: str, df: pd.DataFrame) -> None:
    _atomic(conform(df), live_path(future))


def has_day(future: str, day: date, flow: bool = False) -> bool:
    p = day_path(future, day)
    if not p.exists():
        return False
    if not flow:
        return True
    d = read_day(future, day)
    return bool(len(d)) and bool(d["buy"].notna().any())


def read_day(future: str, day: date) -> pd.DataFrame:
    p = day_path(future, day)
    key = (future, day.isoformat())
    try:
        mt = p.stat().st_mtime
    except OSError:
        return empty()
    with _lock:
        hit = _mem.get(key)
        if hit and hit[0] == mt:
            return hit[1]
    try:
        df = conform(pd.read_parquet(p))
    except Exception:  # noqa: BLE001  (a file being replaced, a corrupt file)
        return empty()
    with _lock:
        _mem[key] = (mt, df)
        if len(_mem) > 6000:
            for k in list(_mem)[:1000]:
                _mem.pop(k, None)
    return df


def read_live(future: str, max_age_s: float | None = None) -> pd.DataFrame:
    p = live_path(future)
    try:
        if max_age_s is not None and time.time() - p.stat().st_mtime > max_age_s:
            return empty()
        return conform(pd.read_parquet(p))
    except Exception:  # noqa: BLE001
        return empty()


def live_age(future: str) -> float | None:
    try:
        return time.time() - live_path(future).stat().st_mtime
    except OSError:
        return None


def read_range(future: str, start: datetime, end: datetime, live: bool = True) -> pd.DataFrame:
    """Minutes in [start, end) from the day files, the live file on top."""
    s, e = pd.Timestamp(start).tz_localize(None) if pd.Timestamp(start).tzinfo else pd.Timestamp(start), \
        pd.Timestamp(end).tz_localize(None) if pd.Timestamp(end).tzinfo else pd.Timestamp(end)
    parts = []
    d = s.normalize()
    while d < e:
        df = read_day(future, d.date())
        if len(df):
            parts.append(df)
        d = d + _ONE_DAY
    days = pd.concat(parts) if parts else empty()
    lv = read_live(future) if live else empty()
    if not len(lv):
        out = days
    elif not len(days):
        out = lv
    else:
        # one row per minute: a row with aggressor flow beats one without; between
        # equals the day file (complete, backfilled) beats the streamed minute
        both = pd.concat([lv.assign(_p=0), days.assign(_p=1)])
        both["_f"] = both["buy"].notna().astype(int)
        both = both.reset_index().sort_values(["minute", "_f", "_p"])
        out = both.drop_duplicates("minute", keep="last").set_index("minute").drop(columns=["_p", "_f"])
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out[(out.index >= s) & (out.index < e)]


def days_present(future: str) -> list[date]:
    d = root() / _safe(future)
    if not d.exists():
        return []
    out = []
    for p in d.glob("*.parquet"):
        try:
            out.append(date.fromisoformat(p.stem))
        except ValueError:
            pass
    return sorted(out)


def last_day(future: str) -> date | None:
    days = days_present(future)
    return days[-1] if days else None


def day_range(a: date, b: date) -> list[date]:
    out, d = [], a
    while d <= b:
        out.append(d)
        d += timedelta(days=1)
    return out
