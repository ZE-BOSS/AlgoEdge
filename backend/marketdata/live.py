"""
backend/marketdata/live.py

Turns the Databento trade stream (orderflow/feed.py, live instance) into
1-minute bars with aggressor flow, and publishes them to the shared store
(store.live_path) every FLUSH_S seconds, so every instance on the server reads
the same live candles.

On start the book is seeded with ohlcv-1m from the historical API for the last
SEED_DAYS (no flow), and the stream's session replay then rebuilds the current
session trade by trade. On a reconnect the replay starts again from the session
open, so minutes from that point are dropped first and never counted twice.

The file is rewritten every FLUSH_S even when nothing traded, so readers can
tell a quiet market (fresh file, no new minutes) from a dead feed (old file).
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

from backend.marketdata import history, store
from backend.marketdata.instruments import INSTRUMENTS
from backend.utils.logger import get_logger

logger = get_logger(__name__)
SEED_DAYS = 4
KEEP_MIN = 60 * 24 * 5
FLUSH_S = 20


class MinuteBook:
    def __init__(self, big: int):
        self.big = big
        self.rows: dict[int, list[float]] = {}     # minute epoch -> [o,h,l,c,v,buy,sell,bb,bs,pv]
        self.seed = store.empty()

    def add(self, px: float, size: float, side: str, ts_ns: int) -> None:
        m = (ts_ns // 1_000_000_000) // 60 * 60
        r = self.rows.get(m)
        buy, sell = side == "B", side == "A"
        if r is None:
            self.rows[m] = r = [px, px, px, px, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        else:
            r[1] = max(r[1], px)
            r[2] = min(r[2], px)
            r[3] = px
        r[4] += size
        r[5] += size if buy else 0.0
        r[6] += size if sell else 0.0
        r[7] += size if (buy and size >= self.big) else 0.0
        r[8] += size if (sell and size >= self.big) else 0.0
        r[9] += px * size

    def drop_from(self, epoch_s: int) -> None:
        for k in [k for k in self.rows if k >= epoch_s]:
            del self.rows[k]

    def frame(self) -> pd.DataFrame:
        if self.rows:
            keys = sorted(self.rows)[-KEEP_MIN:]
            if len(self.rows) > KEEP_MIN + 500:
                self.rows = {k: self.rows[k] for k in keys}
            df = pd.DataFrame([self.rows[k] for k in keys], columns=store.COLS,
                              index=pd.to_datetime(keys, unit="s"))
        else:
            df = store.empty()
        if len(self.seed):
            df = pd.concat([self.seed, df])
        return store.conform(df).iloc[-KEEP_MIN:]


class LiveBars:
    """Owned by the order-flow feed; thread-safe add() from the stream thread."""

    def __init__(self):
        self.books = {i.future: MinuteBook(i.big) for i in INSTRUMENTS.values()}
        self.lock = threading.Lock()
        self.seeded = False

    def futures(self) -> list[str]:
        return list(self.books)

    def add(self, future: str, px: float, size: float, side: str, ts_ns: int) -> None:
        b = self.books.get(future)
        if b is None:
            return
        with self.lock:
            b.add(px, size, side, ts_ns)

    def replay_from(self, start: datetime) -> None:
        epoch = int(start.timestamp())
        with self.lock:
            for b in self.books.values():
                b.drop_from(epoch)

    def seed_history(self) -> None:
        """ohlcv-1m for the last SEED_DAYS, so the live file is never just one session."""
        if not history.key():
            return
        end = history.available_end() or datetime.now(timezone.utc)
        start = end - timedelta(days=SEED_DAYS)
        for inst in INSTRUMENTS.values():
            try:
                df = history.fetch_ohlcv(inst, start, end)
                with self.lock:
                    self.books[inst.future].seed = df
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[MARKETDATA] seed {inst.future}: {type(e).__name__}: {e}")
        self.seeded = True

    def flush(self) -> None:
        for fut, b in self.books.items():
            with self.lock:
                df = b.frame()
            try:
                store.write_live(fut, df)
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[MARKETDATA] live write {fut}: {e}")

    def run_flusher(self, stop: threading.Event | None = None) -> None:
        last_backfill = time.time() - 6 * 3600 + 900      # first run 15 min after start, not during the seed
        while not (stop and stop.is_set()):
            self.flush()
            if time.time() - last_backfill > 6 * 3600:      # the daily job, every 6 h
                last_backfill = time.time()
                threading.Thread(target=self._backfill, name="marketdata-backfill", daemon=True).start()
            time.sleep(FLUSH_S)

    @staticmethod
    def _backfill() -> None:
        try:
            out = history.backfill_all()
            logger.info(f"[MARKETDATA] backfill: {out}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[MARKETDATA] backfill: {type(e).__name__}: {e}")


live_bars = LiveBars()
