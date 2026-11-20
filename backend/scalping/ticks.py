"""Tick store: real bid/ask ticks from MT5, kept per broker/symbol/day.

    data/ticks/<broker>/<symbol>/<YYYY-MM-DD>.npz   arrays t (UTC ms), bid, ask

Ticks come from MT5 copy_ticks_range (COPY_TICKS_INFO: every bid/ask change),
fetched one hour at a time with a pause between hours. Every MT5 call of an
instance goes through one bridge, and a long download in one call would make
that instance's live order sends wait behind it.

Times: MT5 stamps ticks in broker SERVER time written as if it were UTC. The
server's offset is measured once (latest tick vs the clock, rounded to the
hour) and removed, so the store is true UTC.

Bars are built from ticks (MT5 has no 30-second timeframe): OHLC of the bid,
plus the bar's average and maximum spread and its tick count.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

UTC = timezone.utc
STORE = Path(os.getenv("TICK_STORE", "data/ticks"))


# ── storage ─────────────────────────────────────────────────────────────────
def safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)


def day_path(broker: str, symbol: str, day: date) -> Path:
    return STORE / safe(broker) / safe(symbol) / f"{day.isoformat()}.npz"


def save_day(broker: str, symbol: str, day: date, t: np.ndarray, bid: np.ndarray, ask: np.ndarray) -> Path:
    p = day_path(broker, symbol, day)
    p.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(p, t=t.astype(np.int64), bid=bid.astype(np.float64), ask=ask.astype(np.float64))
    return p


@dataclass
class Ticks:
    t: np.ndarray       # int64 UTC milliseconds, ascending
    bid: np.ndarray
    ask: np.ndarray

    def __len__(self) -> int:
        return len(self.t)

    @property
    def mid(self) -> np.ndarray:
        return (self.bid + self.ask) / 2

    def slice_ms(self, a: int, b: int) -> "Ticks":
        i, j = np.searchsorted(self.t, a, "left"), np.searchsorted(self.t, b, "left")
        return Ticks(self.t[i:j], self.bid[i:j], self.ask[i:j])


def stored_days(broker: str, symbol: str) -> list[date]:
    d = STORE / safe(broker) / safe(symbol)
    return sorted(date.fromisoformat(p.stem) for p in d.glob("*.npz")) if d.exists() else []


def load(broker: str, symbol: str, start: date | None = None, end: date | None = None) -> Ticks:
    """Ticks of [start, end] (inclusive days), concatenated and de-duplicated."""
    parts = []
    for day in stored_days(broker, symbol):
        if (start and day < start) or (end and day > end):
            continue
        z = np.load(day_path(broker, symbol, day))
        parts.append((z["t"], z["bid"], z["ask"]))
    if not parts:
        return Ticks(np.zeros(0, np.int64), np.zeros(0), np.zeros(0))
    t = np.concatenate([p[0] for p in parts])
    bid = np.concatenate([p[1] for p in parts])
    ask = np.concatenate([p[2] for p in parts])
    o = np.argsort(t, kind="stable")
    t, bid, ask = t[o], bid[o], ask[o]
    keep = np.ones(len(t), bool)
    keep[1:] = (t[1:] != t[:-1]) | (bid[1:] != bid[:-1]) | (ask[1:] != ask[:-1])
    return Ticks(t[keep], bid[keep], ask[keep])


def gaps(tk: Ticks, min_gap_s: float = 600) -> list[tuple[str, float]]:
    """Silences longer than min_gap_s (weekends, outages, missing history)."""
    if len(tk) < 2:
        return []
    d = np.diff(tk.t) / 1000.0
    idx = np.nonzero(d > min_gap_s)[0]
    return [(datetime.fromtimestamp(tk.t[i] / 1000, UTC).isoformat()[:16], round(float(d[i]) / 60, 1)) for i in idx]


# ── bars from ticks ─────────────────────────────────────────────────────────
@dataclass
class Bars:
    t: np.ndarray        # bar start, UTC seconds
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray        # noqa: E741
    c: np.ndarray
    spread: np.ndarray   # average spread in the bar
    spread_max: np.ndarray
    n: np.ndarray        # tick count (MT5's "tick volume")
    end_ms: np.ndarray   # time of the bar's last tick, ms - a signal on this bar is known from here
    seconds: int

    def __len__(self) -> int:
        return len(self.t)


def make_bars(tk: Ticks, seconds: int) -> Bars:
    """OHLC of the BID (MT5 charts the bid), one bar per `seconds` bucket that has ticks."""
    if len(tk) == 0:
        z = np.zeros(0)
        return Bars(z.astype(np.int64), z, z, z, z, z, z, z.astype(np.int64), z.astype(np.int64), seconds)
    bucket = (tk.t // 1000) // seconds * seconds
    starts = np.nonzero(np.r_[True, bucket[1:] != bucket[:-1]])[0]
    ends = np.r_[starts[1:], len(bucket)]
    bid, spr = tk.bid, tk.ask - tk.bid
    o = bid[starts]
    c = bid[ends - 1]
    h = np.maximum.reduceat(bid, starts)
    l = np.minimum.reduceat(bid, starts)  # noqa: E741
    n = ends - starts
    sp = np.add.reduceat(spr, starts) / n
    spm = np.maximum.reduceat(spr, starts)
    return Bars(bucket[starts].astype(np.int64), o, h, l, c, sp, spm, n.astype(np.int64),
                tk.t[ends - 1].astype(np.int64), seconds)


# ── fetching from MT5 (server only) ─────────────────────────────────────────
def server_offset_s(mt5, symbol: str) -> int:
    tick = mt5.symbol_info_tick(symbol)
    if tick is None or abs(tick.time - time.time()) > 14 * 3600:
        return 0
    return int(round((tick.time - time.time()) / 3600) * 3600)


def fetch_window(mt5, symbol: str, a: datetime, b: datetime, offset_s: int):
    """Ticks between UTC a and b. The request is made in server time."""
    r = mt5.copy_ticks_range(symbol, a + timedelta(seconds=offset_s), b + timedelta(seconds=offset_s),
                             mt5.COPY_TICKS_INFO)
    if r is None or len(r) == 0:
        return None
    t = np.asarray(r["time_msc"], dtype=np.int64) - offset_s * 1000
    bid = np.asarray(r["bid"], dtype=np.float64)
    ask = np.asarray(r["ask"], dtype=np.float64)
    ok = (bid > 0) & (ask > 0) & (ask >= bid)
    return t[ok], bid[ok], ask[ok]


def fetch_day(mt5, symbol: str, day: date, offset_s: int, pause: float = 0.05):
    parts = []
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    for h in range(24):
        a = start + timedelta(hours=h)
        if a > datetime.now(UTC):
            break
        got = fetch_window(mt5, symbol, a, a + timedelta(hours=1), offset_s)
        if got is not None:
            parts.append(got)
        time.sleep(pause)
    if not parts:
        return None
    return (np.concatenate([p[0] for p in parts]), np.concatenate([p[1] for p in parts]),
            np.concatenate([p[2] for p in parts]))


def probe(mt5, symbol: str, offset_s: int) -> dict:
    """How far back this broker serves ticks for `symbol` (1-hour samples)."""
    now = datetime.now(UTC)
    out = {"symbol": symbol, "deepest_day_with_ticks": None, "ticks_last_full_day": None}
    for d in (1, 3, 7, 14, 30, 60, 90, 120, 180, 270, 365, 540, 730):
        hits, day = 0, None
        for back in (0, 1, 2):              # a weekend has no ticks on most symbols
            day = (now - timedelta(days=d + back)).date()
            for hh in (9, 13, 15, 20):      # thin hours: try a few
                a = datetime(day.year, day.month, day.day, hh, tzinfo=UTC)
                got = fetch_window(mt5, symbol, a, a + timedelta(hours=1), offset_s)
                time.sleep(0.02)
                if got is not None:
                    hits = len(got[0])
                    break
            if hits:
                break
        if hits:
            out["deepest_day_with_ticks"] = day.isoformat()
        elif d > 7:
            break
    y = (now - timedelta(days=1)).date()
    full = fetch_day(mt5, symbol, y, offset_s, pause=0.02)
    out["ticks_last_full_day"] = int(len(full[0])) if full else 0
    return out
