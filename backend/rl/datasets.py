"""
backend/rl/datasets.py

Where an agent's bars come from, and what is recorded about them.

THE THREE THINGS THIS HAS TO GET RIGHT
--------------------------------------
1. **Timezone.** A CSV whose times are local, or whose "time" is the bar CLOSE
   rather than the bar OPEN, shifts every feature by one bar in a way nothing
   downstream can detect. `BarFeed` and the whole engine treat the index as the
   bar's OPEN in UTC. So the loader takes an explicit `time_is` and
   `timezone`, defaults to the convention MT5 uses, and REFUSES a file whose
   spacing does not match its declared timeframe rather than resampling it
   quietly.

2. **Overlap with training.** Every dataset gets a content hash and a
   `(symbol, timeframe, start, end)` coverage range, and every agent records
   exactly what it trained on. `overlap()` is what the Backtester calls before
   a run, and a full overlap disables Run - a warning you can click past is a
   warning that gets clicked past.

3. **Higher-timeframe alignment.** An H1 bar built from M5 bars that include
   the forming one is up to 55 minutes of lookahead. `assert_htf_alignment`
   checks every HTF bar closed at or before the primary bar's open, and
   `resample` builds HTF bars only from COMPLETE periods.

WHAT IS STILL OPEN (plan question 10)
-------------------------------------
No real sample file has been supplied for the user's own CSV/JSON exports, so
the column mapping here covers the shapes this codebase already produces (MT5
frames, the `data/` .npz archives and the research CSVs) plus the common
MetaTrader export dialects. It does not guess: an unrecognised header is
reported by name, with the headers that WERE found, so one look at the error
says what to add. Nothing is inferred silently.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from backend.utils.logger import get_logger

logger = get_logger(__name__)

TF_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}

# Column name -> canonical name. Lower-cased and stripped of punctuation before
# lookup, so "Open", "<OPEN>" and "open_price" all land on "open".
COLUMN_ALIASES = {
    "time": "time", "date": "time", "datetime": "time", "timestamp": "time",
    "bartime": "time", "opentime": "time", "dateтime": "time",
    "open": "open", "o": "open", "openprice": "open",
    "high": "high", "h": "high", "highprice": "high",
    "low": "low", "l": "low", "lowprice": "low",
    "close": "close", "c": "close", "closeprice": "close", "last": "close",
    "volume": "volume", "vol": "volume", "tickvol": "volume",
    "tickvolume": "volume", "realvolume": "volume", "v": "volume",
    "spread": "spread",
}

REQUIRED = ("time", "open", "high", "low", "close")


class DatasetError(ValueError):
    """A file that cannot be trusted as bars."""


def _canon(name: str) -> str:
    out = "".join(ch for ch in str(name).lower() if ch.isalnum())
    return COLUMN_ALIASES.get(out, out)


# -- provenance -------------------------------------------------------------


@dataclass
class Dataset:
    """Bars, plus everything needed to say what they are and where they came from."""
    symbol: str
    timeframe: str
    frame: pd.DataFrame
    source: str = ""
    broker: str = ""
    content_hash: str = ""
    rows: int = 0
    start: int = 0
    end: int = 0
    gaps: int = 0
    notes: list = field(default_factory=list)

    @property
    def start_iso(self) -> str:
        return datetime.fromtimestamp(self.start, tz=timezone.utc).isoformat() if self.start else ""

    @property
    def end_iso(self) -> str:
        return datetime.fromtimestamp(self.end, tz=timezone.utc).isoformat() if self.end else ""

    def to_dict(self) -> dict:
        return {"symbol": self.symbol, "timeframe": self.timeframe, "source": self.source,
                "broker": self.broker, "content_hash": self.content_hash, "rows": self.rows,
                "start": self.start, "end": self.end, "start_iso": self.start_iso,
                "end_iso": self.end_iso, "gaps": self.gaps, "notes": list(self.notes)}

    def coverage(self) -> dict:
        return {"symbol": self.symbol, "timeframe": self.timeframe,
                "start": self.start, "end": self.end, "hash": self.content_hash}


def content_hash(frame: pd.DataFrame) -> str:
    """Hash the BARS, not the file.

    Two exports of the same bars with different headers, column order or line
    endings are the same data and must hash the same - otherwise the overlap
    check is defeated by re-exporting.
    """
    cols = [c for c in ("time", "open", "high", "low", "close") if c in frame.columns]
    buf = np.ascontiguousarray(frame[cols].to_numpy(dtype="float64"))
    return hashlib.sha256(buf.tobytes()).hexdigest()[:16]


# -- loading ----------------------------------------------------------------


def normalise(frame: pd.DataFrame, symbol: str, timeframe: str, *,
              time_is: str = "open", tz: str = "UTC", source: str = "",
              broker: str = "", strict_spacing: bool = True) -> Dataset:
    """Rename, sort, validate. The one door every source comes through.

    `time_is="close"` shifts the stamps back by one bar interval, because the
    rest of this system indexes bars by their OPEN. Getting that wrong moves
    every feature one bar into the future, which no probe catches - the feature
    is still prefix-stable, it is just stable over the wrong prefix.
    """
    if frame is None or not len(frame):
        raise DatasetError(f"{symbol} {timeframe}: no rows")
    df = frame.copy()
    if not isinstance(df.index, pd.RangeIndex) and "time" not in df.columns:
        df = df.reset_index()

    mapping = {c: _canon(c) for c in df.columns}
    df = df.rename(columns=mapping)
    df = df.loc[:, ~df.columns.duplicated()]

    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise DatasetError(
            f"{symbol} {timeframe}: missing {missing}. Found columns: "
            f"{sorted(set(df.columns))}. Add the header to COLUMN_ALIASES in "
            f"backend/rl/datasets.py rather than renaming the file.")

    # -- time --------------------------------------------------------------
    t = df["time"]
    if not pd.api.types.is_numeric_dtype(t):
        # Epoch seconds exported as TEXT - a CSV round-trip produces exactly
        # this, and `to_datetime` reads "1735689600" as a nanosecond count and
        # coerces the whole column to NaT. Measured: 50 of 50 rows "unparseable"
        # on a file that was perfectly good. Tried as a number first.
        as_number = pd.to_numeric(t, errors="coerce")
        if as_number.notna().all():
            t = as_number
    if pd.api.types.is_numeric_dtype(t):
        secs = t.astype("int64")
        # Milliseconds are common in JSON exports and look like a date in the
        # year 56000 if taken as seconds.
        if secs.max() > 10_000_000_000:
            secs = secs // 1000
    else:
        parsed = pd.to_datetime(t, errors="coerce", utc=(tz.upper() == "UTC"))
        if parsed.isna().any():
            bad = int(parsed.isna().sum())
            raise DatasetError(f"{symbol} {timeframe}: {bad} unparseable timestamps")
        if parsed.dt.tz is None:
            parsed = parsed.dt.tz_localize(tz).dt.tz_convert("UTC")
        else:
            parsed = parsed.dt.tz_convert("UTC")
        # `as_unit("s")` rather than dividing nanoseconds: `asi8` is in the
        # index's own unit, which is nanoseconds on pandas 2 and may be
        # microseconds or seconds on pandas 3 - the same trap `core/daily_atr`
        # already documents.
        secs = pd.Series(pd.DatetimeIndex(parsed).as_unit("s").asi8, index=df.index)

    minutes = TF_MINUTES.get(str(timeframe).upper())
    if time_is == "close":
        if not minutes:
            raise DatasetError(f"cannot shift a close-stamped frame without a known "
                               f"timeframe (got {timeframe!r})")
        secs = secs - minutes * 60

    df["time"] = secs.to_numpy(dtype="int64")
    for c in ("open", "high", "low", "close"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    if "volume" in df.columns:
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0).astype(float)

    notes = []
    before = len(df)
    df = df.dropna(subset=["open", "high", "low", "close"])
    if len(df) != before:
        notes.append(f"dropped {before - len(df)} rows with a missing price")

    df = df.sort_values("time", kind="stable")
    dup = int(df["time"].duplicated().sum())
    if dup:
        # Keep the LAST of a duplicated stamp: in an MT5 export the later row
        # is the corrected one. Silently keeping the first is how a stale bar
        # survives a re-export.
        df = df.drop_duplicates(subset=["time"], keep="last")
        notes.append(f"{dup} duplicate timestamps - kept the last of each")

    bad = df[(df["high"] < df["low"]) | (df["high"] < df["open"])
             | (df["high"] < df["close"]) | (df["low"] > df["open"])
             | (df["low"] > df["close"])]
    if len(bad):
        raise DatasetError(
            f"{symbol} {timeframe}: {len(bad)} bars where the high/low do not contain "
            f"the open/close - the first is at "
            f"{datetime.fromtimestamp(int(bad['time'].iloc[0]), tz=timezone.utc)}. "
            f"That is a broken export, not a market event.")

    gaps = 0
    if minutes and len(df) > 2:
        diffs = np.diff(df["time"].to_numpy(dtype="int64"))
        step = minutes * 60
        unexpected = diffs[(diffs % step) != 0]
        gaps = int((diffs > step).sum())
        if strict_spacing and len(unexpected):
            raise DatasetError(
                f"{symbol} {timeframe}: {len(unexpected)} intervals are not a multiple of "
                f"{minutes} minutes (e.g. {int(unexpected[0])}s). Either the timeframe is "
                f"wrong or the times are stamped differently from `time_is={time_is!r}`. "
                f"Resampling this quietly would move every feature.")
        if gaps:
            notes.append(f"{gaps} gaps (weekends and holidays look like this)")

    df = df.reset_index(drop=True)
    ds = Dataset(symbol=symbol, timeframe=str(timeframe).upper(), frame=df, source=source,
                 broker=broker, content_hash=content_hash(df), rows=len(df),
                 start=int(df["time"].iloc[0]), end=int(df["time"].iloc[-1]),
                 gaps=gaps, notes=notes)
    return ds


def load_csv(path: str | Path, symbol: str, timeframe: str, **kw) -> Dataset:
    p = Path(path)
    # sep=None with the python engine sniffs tab- and semicolon-separated
    # exports, which MetaTrader produces, without a flag per dialect.
    frame = pd.read_csv(p, sep=None, engine="python")
    return normalise(frame, symbol, timeframe, source=str(p), **kw)


def load_json(path: str | Path, symbol: str, timeframe: str, **kw) -> Dataset:
    p = Path(path)
    blob = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(blob, dict):
        # Either {"bars": [...]} / {"candles": [...]} or columns-of-arrays.
        for key in ("bars", "candles", "data", "ohlc", "history"):
            if isinstance(blob.get(key), list):
                blob = blob[key]
                break
    frame = pd.DataFrame(blob)
    return normalise(frame, symbol, timeframe, source=str(p), **kw)


def load_npz(path: str | Path, symbol: str, timeframe: str, **kw) -> Dataset:
    """The `data/` archives this project already keeps."""
    p = Path(path)
    with np.load(p, allow_pickle=False) as z:
        cols = {k: z[k] for k in z.files}
    frame = pd.DataFrame({_canon(k): v for k, v in cols.items()})
    return normalise(frame, symbol, timeframe, source=str(p), **kw)


def load_file(path: str | Path, symbol: str, timeframe: str, **kw) -> Dataset:
    suffix = Path(path).suffix.lower()
    if suffix in (".csv", ".tsv", ".txt"):
        return load_csv(path, symbol, timeframe, **kw)
    if suffix == ".json":
        return load_json(path, symbol, timeframe, **kw)
    if suffix == ".npz":
        return load_npz(path, symbol, timeframe, **kw)
    if suffix in (".parquet", ".pq"):
        return normalise(pd.read_parquet(path), symbol, timeframe,
                         source=str(path), **kw)
    raise DatasetError(f"{path}: unsupported file type {suffix!r}")


def load_mt5(symbol: str, timeframe: str, bars: int = 200_000, **kw) -> Dataset:
    """Straight from the terminal, which is the primary source on this machine.

    Goes through `DataFetcher.get_historical_data` rather than calling MT5
    directly: that module owns the single-threaded executor rule, and calling
    MT5 from two threads is how the terminal starts answering None to
    everything. It is `async`, and this function is called from the TRAINING
    PROCESS where there is no running loop, so `asyncio.run` is correct here -
    it would not be in the API process, which is why the API never calls this.
    """
    import asyncio

    from backend.mt5.data_fetcher import DataFetcher

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise DatasetError(
            "load_mt5 must not be called from the event loop - building MT5 frames on "
            "the loop has already stalled this API once. Call it in the training process.")

    frame = asyncio.run(DataFetcher.get_historical_data(symbol, timeframe, int(bars)))
    if frame is None or not len(frame):
        raise DatasetError(f"{symbol} {timeframe}: MT5 returned no bars")
    return normalise(frame, symbol, timeframe, source="mt5", **kw)


# -- higher timeframes ------------------------------------------------------


def resample(primary: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Build HTF bars from COMPLETE periods only.

    The bar still forming is dropped, which is the whole point: an H1 bar built
    from the M5 bars available at 10:20 is not the 10:00 H1 bar, and feeding it
    to a strategy as if it were is up to 55 minutes of lookahead. Nothing
    downstream can detect that, so it is prevented here.
    """
    df = primary.copy()
    if "time" not in df.columns:
        df["time"] = pd.DatetimeIndex(df.index).as_unit("s").asi8
    step = int(minutes) * 60
    df["_bucket"] = (df["time"].to_numpy(dtype="int64") // step) * step
    agg = df.groupby("_bucket").agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"),
        **({"volume": ("volume", "sum")} if "volume" in df.columns else {}))
    agg = agg.reset_index().rename(columns={"_bucket": "time"})
    counts = df.groupby("_bucket").size()
    # The last bucket is complete only if it holds a full period of primary
    # bars. A short final bucket is the forming bar.
    if len(agg):
        expected = int(counts.iloc[:-1].max()) if len(counts) > 1 else int(counts.iloc[0])
        if int(counts.iloc[-1]) < expected:
            agg = agg.iloc[:-1]
    return agg.reset_index(drop=True)


def assert_htf_alignment(primary: pd.DataFrame, htf: pd.DataFrame, minutes: int) -> None:
    """Every HTF bar an engine could use had closed by the primary bar's open.

    WHY THE OBVIOUS VERSION OF THIS CHECK IS VACUOUS
    ------------------------------------------------
    The first version did an as-of search for the newest HTF bar whose close was
    at or before each primary bar's open, and then asserted that that bar's
    close was at or before the primary bar's open. `searchsorted` had already
    guaranteed it. The assertion could not fail - it passed a deliberately
    close-stamped frame - which is worse than no check, because it reads like
    one.

    What can actually be checked is the frame itself:

      * an HTF bar cannot have closed AFTER the last primary bar closed,
        because it was built from those primary bars. A frame stamped by close
        instead of open fails this by exactly one period, which is the mistake
        this function exists to catch.
      * consecutive HTF stamps must be at least one period apart. Closer than
        that means overlapping bars, i.e. a rolling window dressed up as bars.
    """
    from backend.rl.features import HTFAlignmentError

    if primary is None or htf is None or not len(primary) or not len(htf):
        return
    step = int(minutes) * 60
    p = (primary["time"].to_numpy(dtype="int64") if "time" in primary.columns
         else pd.DatetimeIndex(primary.index).as_unit("s").asi8)
    h = (htf["time"].to_numpy(dtype="int64") if "time" in htf.columns
         else pd.DatetimeIndex(htf.index).as_unit("s").asi8)

    primary_step = int(np.median(np.diff(p))) if len(p) > 2 else 0
    last_primary_close = int(p[-1]) + max(primary_step, 0)
    last_htf_close = int(h[-1]) + step
    if last_htf_close > last_primary_close:
        raise HTFAlignmentError(
            f"the last HTF bar closes at {last_htf_close} but the primary bars only "
            f"reach {last_primary_close} - the HTF frame is stamped by close rather "
            f"than open, or it was resampled including the bar still forming")

    if len(h) > 1:
        gaps = np.diff(h)
        if (gaps < step).any():
            raise HTFAlignmentError(
                f"HTF bars are closer together than {minutes} minutes - these are "
                f"overlapping windows, not bars")


# -- overlap ----------------------------------------------------------------


@dataclass
class Overlap:
    """How much of a requested window the agent has already seen."""
    state: str                      # "clean" | "partial" | "in_sample"
    fraction: float = 0.0
    ranges: list = field(default_factory=list)
    first_clean: int = 0

    @property
    def runnable(self) -> bool:
        """A full overlap disables Run. Not a warning: a warning gets clicked past."""
        return self.state != "in_sample"

    @property
    def message(self) -> str:
        if self.state == "clean":
            return "none of this window was used in training"
        if self.state == "in_sample":
            when = (datetime.fromtimestamp(self.first_clean, tz=timezone.utc).date().isoformat()
                    if self.first_clean else "after the training data ends")
            return (f"this window is entirely inside the agent's training data - "
                    f"pick a window after {when}")
        named = "; ".join(f"{datetime.fromtimestamp(a, tz=timezone.utc).date()} to "
                          f"{datetime.fromtimestamp(b, tz=timezone.utc).date()}"
                          for a, b in self.ranges)
        return f"{self.fraction:.0%} of this window was used in training ({named})"

    def to_dict(self) -> dict:
        return {"state": self.state, "fraction": round(self.fraction, 4),
                "ranges": [[int(a), int(b)] for a, b in self.ranges],
                "runnable": self.runnable, "first_clean": int(self.first_clean),
                "message": self.message}


def overlap(trained_on: list, symbol: str, timeframe: str, start: int, end: int) -> Overlap:
    """Does this backtest window intersect what the agent trained on?

    `trained_on` is a list of coverage dicts from `Dataset.coverage()`. Matching
    is by symbol AND timeframe: an agent trained on EURUSD M5 has seen nothing
    of GBPJPY, and the overlap must not claim otherwise.
    """
    start, end = int(start), int(end)
    if end <= start:
        return Overlap("clean")
    spans = []
    for cov in trained_on or ():
        if str(cov.get("symbol", "")).upper() != str(symbol).upper():
            continue
        if str(cov.get("timeframe", "")).upper() != str(timeframe).upper():
            continue
        a, b = int(cov.get("start") or 0), int(cov.get("end") or 0)
        if b <= a:
            continue
        lo, hi = max(a, start), min(b, end)
        if hi > lo:
            spans.append((lo, hi))
    if not spans:
        return Overlap("clean")
    spans.sort()
    merged = [list(spans[0])]
    for a, b in spans[1:]:
        if a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    covered = sum(b - a for a, b in merged)
    frac = covered / float(end - start)
    first_clean = max(int(c.get("end") or 0) for c in trained_on
                      if str(c.get("symbol", "")).upper() == str(symbol).upper()
                      and str(c.get("timeframe", "")).upper() == str(timeframe).upper())
    if frac >= 0.999:
        return Overlap("in_sample", frac, [tuple(m) for m in merged], first_clean)
    return Overlap("partial", frac, [tuple(m) for m in merged], first_clean)


# -- what the trainer calls -------------------------------------------------


def load_frames(sources: dict, spec) -> dict:
    """`{symbol: frame}` for a training run, from whatever the UI recorded.

    `sources` maps a symbol to either a file path or `{"path": ..., "time_is":
    ..., "timezone": ...}`, or to the literal string "mt5". Called inside the
    training PROCESS, so the parent never has to pickle a 400,000-row frame.
    """
    out = {}
    for symbol in spec.symbols:
        src = (sources or {}).get(symbol)
        if src is None:
            logger.warning(f"[RL] {symbol}: no data source recorded - skipped")
            continue
        try:
            if src == "mt5":
                ds = load_mt5(symbol, spec.timeframe)
            elif isinstance(src, dict):
                ds = load_file(src["path"], symbol, src.get("timeframe", spec.timeframe),
                               time_is=src.get("time_is", "open"),
                               tz=src.get("timezone", "UTC"),
                               broker=src.get("broker", ""),
                               strict_spacing=bool(src.get("strict_spacing", True)))
            else:
                ds = load_file(src, symbol, spec.timeframe)
        except Exception as exc:                        # noqa: BLE001
            logger.error(f"[RL] {symbol}: {type(exc).__name__}: {exc}")
            continue
        out[symbol] = ds.frame
    return out
