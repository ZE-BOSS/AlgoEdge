"""
backend/orderflow/profile.py

One futures session's order flow, built trade by trade:

  * volume profile at the contract's tick size: POC (most traded price) and the
    value area (the 70 % of volume around it: VAH / VAL);
  * VWAP;
  * delta: buyer-initiated minus seller-initiated volume (the aggressor side of
    each trade), for the session and for the last 30 minutes;
  * large prints: single trades at or above the size threshold, with side;
  * the previous session's POC / VAH / VAL / high / low, kept at rollover.

The CME session for gold and Nasdaq futures opens at 17:00 Chicago time; a
trade after that belongs to the next session.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

CHICAGO = ZoneInfo("America/Chicago")


def session_of(ts_ns: int) -> str:
    """The session date (Chicago calendar day it ENDS on) for a trade time."""
    t = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).astimezone(CHICAGO)
    if t.hour >= 17:
        t = t + timedelta(days=1)
    return t.strftime("%Y-%m-%d")


def session_start_utc(now: datetime | None = None) -> datetime:
    """When the current session opened (17:00 Chicago), in UTC."""
    t = (now or datetime.now(timezone.utc)).astimezone(CHICAGO)
    start = t.replace(hour=17, minute=0, second=0, microsecond=0)
    if t < start:
        start -= timedelta(days=1)
    return start.astimezone(timezone.utc)


def value_area(volume: dict[float, float], pct: float = 0.70) -> tuple[float | None, float | None, float | None]:
    """(POC, VAL, VAH): expand from the POC one price at a time toward the side with more volume."""
    if not volume:
        return None, None, None
    prices = sorted(volume)
    total = sum(volume.values())
    poc_i = max(range(len(prices)), key=lambda i: (volume[prices[i]], -abs(i - len(prices) / 2)))
    lo = hi = poc_i
    got = volume[prices[poc_i]]
    while got < total * pct and (lo > 0 or hi < len(prices) - 1):
        down = volume[prices[lo - 1]] if lo > 0 else -1
        up = volume[prices[hi + 1]] if hi < len(prices) - 1 else -1
        if up >= down:
            hi += 1
            got += up
        else:
            lo -= 1
            got += down
    return prices[poc_i], prices[lo], prices[hi]


@dataclass
class SessionProfile:
    symbol: str
    tick: float = 0.1
    big_size: int = 20
    session: str = ""
    volume: dict[float, float] = field(default_factory=dict)
    pv: float = 0.0
    vol: float = 0.0
    delta: float = 0.0
    high: float | None = None
    low: float | None = None
    last: float | None = None
    last_ts: int = 0
    trades: int = 0
    recent: deque = field(default_factory=lambda: deque(maxlen=200_000))     # (ts_ns, signed size)
    big: deque = field(default_factory=lambda: deque(maxlen=50))
    prev: dict = field(default_factory=dict)

    def add(self, price: float, size: float, side: str, ts_ns: int) -> None:
        sess = session_of(ts_ns)
        if sess != self.session:
            if self.session and self.vol:
                self.prev = self.levels(include_prev=False) | {"session": self.session}
            self._reset(sess)
        p = round(round(price / self.tick) * self.tick, 6)
        self.volume[p] = self.volume.get(p, 0.0) + size
        self.pv += price * size
        self.vol += size
        signed = size if side == "B" else -size if side == "A" else 0.0
        self.delta += signed
        self.recent.append((ts_ns, signed))
        self.high = price if self.high is None else max(self.high, price)
        self.low = price if self.low is None else min(self.low, price)
        self.last, self.last_ts = price, ts_ns
        self.trades += 1
        if size >= self.big_size:
            self.big.append({"price": price, "size": size, "side": {"B": "buy", "A": "sell"}.get(side, "?"),
                             "time": datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).isoformat()})

    def _reset(self, sess: str) -> None:
        self.session = sess
        self.volume, self.pv, self.vol, self.delta = {}, 0.0, 0.0, 0.0
        self.high = self.low = None
        self.trades = 0
        self.recent.clear()
        self.big.clear()

    def delta_since(self, seconds: int) -> float:
        cut = self.last_ts - seconds * 1_000_000_000
        return sum(s for t, s in self.recent if t >= cut)

    def levels(self, include_prev: bool = True) -> dict:
        poc, val, vah = value_area(self.volume)
        out = {"symbol": self.symbol, "session": self.session, "last": self.last, "poc": poc, "vah": vah, "val": val,
               "vwap": round(self.pv / self.vol, 4) if self.vol else None, "high": self.high, "low": self.low,
               "volume": self.vol, "trades": self.trades, "delta": self.delta,
               "delta_30m": self.delta_since(1800) if self.last_ts else 0.0,
               "big_prints": list(self.big)[-15:],
               "updated": datetime.fromtimestamp(self.last_ts / 1e9, tz=timezone.utc).isoformat() if self.last_ts else None}
        if include_prev and self.prev:
            out["prev"] = {k: self.prev.get(k) for k in ("session", "poc", "vah", "val", "high", "low", "vwap")}
        return out


def to_cfd(levels: dict, offset: float | None, digits: int = 2) -> dict:
    """The same levels in the CFD's prices: futures minus the basis (futures last - CFD mid)."""
    if offset is None:
        return {}
    keys = ("last", "poc", "vah", "val", "vwap", "high", "low")
    out = {k: round(levels[k] - offset, digits) for k in keys if levels.get(k) is not None}
    if levels.get("prev"):
        out["prev"] = {k: round(levels["prev"][k] - offset, digits) for k in keys if levels["prev"].get(k) is not None}
    out["big_prints"] = [dict(b, price=round(b["price"] - offset, digits)) for b in levels.get("big_prints") or []]
    return out
