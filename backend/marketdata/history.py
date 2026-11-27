"""
backend/marketdata/history.py

Fills the shared store from Databento's historical API.

  * ohlcv-1m for any range a backtest or a strategy asks for (fast, included in
    the subscription): one request for the missing stretch, written per day;
  * trades (aggressor side) for the order-flow columns: day by day, by the live
    instance's daily backfill only - a year of Nasdaq trades is gigabytes.

A day is written even when it has no bars (a holiday), so it is not asked
for again; the day still in progress and anything after the dataset's
available end are never written.

Needs DATABENTO_API_KEY. Without it every call is a no-op and readers fall back
to MT5.
"""
from __future__ import annotations

import os
import tempfile
import threading
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from backend.marketdata import store
from backend.marketdata.instruments import INSTRUMENTS, Instrument
from backend.utils.logger import get_logger

logger = get_logger(__name__)
DATASET = "GLBX.MDP3"
_fetch_lock = threading.Lock()
_avail: tuple[float, datetime | None] = (0.0, None)


def key() -> str:
    return os.getenv("DATABENTO_API_KEY", "").strip()


def client():
    import databento as db
    return db.Historical(key())


def _retry(fn, *a, tries: int = 4, **kw):
    for i in range(tries):
        try:
            return fn(*a, **kw)
        except Exception as e:  # noqa: BLE001
            if i == tries - 1:
                raise
            msg = str(e)
            if "data_end_after_available_end" in msg or "data_start_after_available_end" in msg:
                raise
            time.sleep(3 * 2 ** i)


def available_end() -> datetime | None:
    """The last time the historical API serves (cached 10 minutes)."""
    global _avail
    if time.time() - _avail[0] < 600 and _avail[1] is not None:
        return _avail[1]
    try:
        r = _retry(client().metadata.get_dataset_range, dataset=DATASET)
        end = r.get("end") if isinstance(r, dict) else getattr(r, "end", None)
        ts = pd.Timestamp(end)
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        _avail = (time.time(), ts.to_pydatetime())
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[MARKETDATA] dataset range: {type(e).__name__}: {e}")
        return None
    return _avail[1]


def _to_utc(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def fetch_ohlcv(inst: Instrument, start, end) -> pd.DataFrame:
    s, e = _to_utc(start), _to_utc(end)
    if e <= s:
        return store.empty()
    df = _retry(client().timeseries.get_range, dataset=DATASET, schema="ohlcv-1m", symbols=[inst.future],
                stype_in="continuous", start=s.isoformat(), end=e.isoformat()).to_df()
    if df is None or not len(df):
        return store.empty()
    if "ts_event" in df.columns:
        df = df.set_index("ts_event")
    return store.conform(df[["open", "high", "low", "close", "volume"]])


def minute_flow(trades: pd.DataFrame, big: int) -> pd.DataFrame:
    """Minutes of OHLC + aggressor flow from a trades frame (side B = buyer lifted the offer)."""
    if trades is None or not len(trades):
        return store.empty()
    t = trades.reset_index() if "ts_event" not in trades.columns else trades
    ts = pd.to_datetime(t["ts_event"], utc=True).dt.tz_localize(None).dt.floor("1min")
    size = t["size"].astype(float).values
    side = t["side"].astype(str).values
    px = t["price"].astype(float).values
    buy, sell = (side == "B"), (side == "A")
    df = pd.DataFrame({"minute": ts.values, "px": px, "size": size, "buy": size * buy, "sell": size * sell,
                       "pv": px * size, "big_buy": size * (buy & (size >= big)), "big_sell": size * (sell & (size >= big))})
    g = df.groupby("minute")
    out = pd.DataFrame({"open": g["px"].first(), "high": g["px"].max(), "low": g["px"].min(),
                        "close": g["px"].last(), "volume": g["size"].sum(), "buy": g["buy"].sum(),
                        "sell": g["sell"].sum(), "big_buy": g["big_buy"].sum(), "big_sell": g["big_sell"].sum(),
                        "pv": g["pv"].sum()})
    return store.conform(out)


def fetch_flow_day(inst: Instrument, day: date, end_cap: datetime | None = None) -> pd.DataFrame:
    s = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    e = s + timedelta(days=1)
    if end_cap is not None:
        e = min(e, end_cap)
    if e <= s:
        return store.empty()
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "day.dbn.zst")
        _retry(client().timeseries.get_range, dataset=DATASET, schema="trades", symbols=[inst.future],
               stype_in="continuous", start=s.isoformat(), end=e.isoformat(), path=path)
        import databento as db
        tr = db.DBNStore.from_file(path).to_df()
    return minute_flow(tr, inst.big)


def complete_days(a: date, b: date) -> list[date]:
    """Days in [a, b] that have fully ended by the dataset's available end."""
    av = available_end()
    if av is None:
        return []
    last_full = (av - timedelta(days=1)).date() if av.hour or av.minute else (av.date() - timedelta(days=1))
    return [d for d in store.day_range(a, min(b, last_full)) if d.weekday() != 5]   # no CME trading Saturday


def ensure_ohlcv(inst: Instrument, start, end) -> int:
    """Fetch and store the days of [start, end] the store does not have. Returns days written."""
    if not key():
        return 0
    a, b = _to_utc(start).date(), _to_utc(end).date()
    want = [d for d in complete_days(a, b) if not store.has_day(inst.future, d)]
    if not want:
        return 0
    with _fetch_lock:
        want = [d for d in want if not store.has_day(inst.future, d)]
        if not want:
            return 0
        got = fetch_ohlcv(inst, datetime(want[0].year, want[0].month, want[0].day),
                          datetime(want[-1].year, want[-1].month, want[-1].day) + timedelta(days=1))
        for d in want:
            lo = pd.Timestamp(d)
            store.write_day(inst.future, d, got[(got.index >= lo) & (got.index < lo + pd.Timedelta(days=1))])
    logger.info(f"[MARKETDATA] {inst.future}: stored {len(want)} day(s) of 1-minute bars")
    return len(want)


def backfill_flow(inst: Instrument, days_back: int = 3) -> int:
    """Add aggressor flow for recent complete days that lack it (the daily job)."""
    if not key():
        return 0
    today = datetime.now(timezone.utc).date()
    n = 0
    av = available_end()
    for d in complete_days(today - timedelta(days=days_back), today):
        if store.has_day(inst.future, d, flow=True):
            continue
        try:
            df = fetch_flow_day(inst, d, av)
            store.write_day(inst.future, d, df)
            n += 1
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[MARKETDATA] flow backfill {inst.future} {d}: {type(e).__name__}: {e}")
    return n


def backfill_all(flow_days: int = 3) -> dict:
    """The live instance's daily job: ohlcv for the last 30 days everywhere, flow for recent days."""
    out = {}
    today = datetime.now(timezone.utc).date()
    for inst in INSTRUMENTS.values():
        try:
            n = ensure_ohlcv(inst, today - timedelta(days=30), today)
            f = backfill_flow(inst, flow_days)
            out[inst.future] = (n, f)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[MARKETDATA] backfill {inst.future}: {type(e).__name__}: {e}")
    return out
