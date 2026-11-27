"""
backend/orderflow/feed.py

Streams CME futures trades from Databento (GLBX.MDP3, trades schema), replaying
the session so far on start (the live API replays up to 24 hours):

  * the ORDERFLOW_MAP markets into one SessionProfile each; every 15 seconds a
    snapshot - the levels in futures prices and translated to the broker's CFD
    prices with the live basis - goes to the shared folder, where the admin API
    and Elite clients' portal read it;
  * every market in marketdata/instruments.py into 1-minute bars with aggressor
    flow (marketdata/live.py), published to the shared market-data store that
    all instances' candles come from.

Runs only where ORDERFLOW=1 and DATABENTO_API_KEY are set (the live instance).

Environment
    DATABENTO_API_KEY   the key from the Databento portal
    ORDERFLOW           "1" to run
    ORDERFLOW_MAP       futures=CFD pairs, default "GC.v.0=XAUUSDm,NQ.v.0=USTECm"
    ORDERFLOW_DIR       default /opt/algoedge/shared/orderflow
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from pathlib import Path

from backend.marketdata.live import live_bars
from backend.orderflow.profile import SessionProfile, session_start_utc, to_cfd
from backend.utils.logger import get_logger

logger = get_logger(__name__)

SPECS = {  # root -> (tick size, large-print threshold in contracts, display name)
    "GC": (0.1, 20, "Gold futures (COMEX GC)"),
    "MGC": (0.1, 100, "Micro gold futures"),
    "NQ": (0.25, 15, "Nasdaq 100 futures (CME NQ)"),
    "MNQ": (0.25, 150, "Micro Nasdaq futures"),
    "ES": (0.25, 50, "S&P 500 futures (CME ES)"),
}


def out_dir() -> Path:
    return Path(os.getenv("ORDERFLOW_DIR", "/opt/algoedge/shared/orderflow"))


def pairs() -> dict[str, str]:
    raw = os.getenv("ORDERFLOW_MAP", "GC.v.0=XAUUSDm,NQ.v.0=USTECm")
    return {a.strip(): b.strip() for a, b in (p.split("=", 1) for p in raw.split(",") if "=" in p)}


def read_snapshot() -> dict:
    try:
        return json.loads((out_dir() / "levels.json").read_text())
    except Exception:  # noqa: BLE001
        return {}


class OrderFlowFeed:
    def __init__(self):
        self.profiles: dict[str, SessionProfile] = {}
        self.ids: dict[int, str] = {}          # instrument_id -> continuous symbol
        self.thread: threading.Thread | None = None
        self.task: asyncio.Task | None = None
        self.error = ""
        self.connected = False
        self.lock = threading.Lock()
        for sym in pairs():
            root = sym.split(".")[0]
            tick, big, _ = SPECS.get(root, (0.25, 50, root))
            self.profiles[sym] = SessionProfile(symbol=sym, tick=tick, big_size=big)

    def fresh_profiles(self) -> None:
        with self.lock:
            for sym, old in list(self.profiles.items()):
                p = SessionProfile(symbol=sym, tick=old.tick, big_size=old.big_size)
                p.prev = old.prev
                self.profiles[sym] = p
            self.ids.clear()

    @property
    def enabled(self) -> bool:
        return os.getenv("ORDERFLOW", "0") == "1" and bool(os.getenv("DATABENTO_API_KEY", "").strip())

    # -- stream (a thread: the databento client blocks) ------------------------
    def symbols(self) -> list[str]:
        return sorted(set(self.profiles) | set(live_bars.futures()))

    def _run(self) -> None:
        import databento as db
        backoff = 5
        if not live_bars.seeded:
            # in the background: seeding 13 markets can take minutes when the
            # historical API is slow, and the stream must not wait for it
            threading.Thread(target=live_bars.seed_history, name="marketdata-seed", daemon=True).start()
        while True:
            try:
                client = db.Live(key=os.getenv("DATABENTO_API_KEY"), reconnect_policy="reconnect")
                start = session_start_utc()
                client.subscribe(dataset="GLBX.MDP3", schema="trades", stype_in="continuous",
                                 symbols=self.symbols(), start=start)
                self.fresh_profiles()                # the replay rebuilds the session: never count it twice
                live_bars.replay_from(start)
                self.connected, self.error, backoff = True, "", 5
                logger.info(f"[ORDERFLOW] streaming {', '.join(self.symbols())} (session replay from the open)")
                for rec in client:                   # iterating starts the stream (no client.start())
                    self.on_record(rec, db)
                self.connected = False
            except Exception as e:  # noqa: BLE001
                self.connected = False
                self.error = f"{type(e).__name__}: {e}"[:300]
                logger.warning(f"[ORDERFLOW] stream stopped: {self.error}; retrying in {backoff}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)

    def on_record(self, rec, db) -> None:
        if isinstance(rec, db.SymbolMappingMsg):
            sym = getattr(rec, "stype_in_symbol", "") or ""
            if sym in self.profiles or sym in live_bars.books:
                self.ids[rec.instrument_id] = sym
            return
        if not isinstance(rec, db.TradeMsg):
            return
        sym = self.ids.get(rec.instrument_id)
        if sym is None and len(self.profiles) == 1 and not self.ids:
            sym = next(iter(self.profiles))          # one market and no mapping seen yet
        if sym is None:
            return
        side = str(getattr(rec.side, "value", rec.side) or "N")
        px, size, ts = rec.price / 1e9, float(rec.size), int(rec.ts_event)
        if sym in self.profiles:
            with self.lock:
                self.profiles[sym].add(px, size, side, ts)
        live_bars.add(sym, px, size, side, ts)

    # -- snapshots --------------------------------------------------------------
    def snapshot(self, cfd_price=None) -> dict:
        out = {"updated": time.time(), "connected": self.connected, "error": self.error, "markets": []}
        with self.lock:
            levels = {s: p.levels() for s, p in self.profiles.items()}
        for sym, lv in levels.items():
            cfd = pairs().get(sym, "")
            root = sym.split(".")[0]
            mid = cfd_price(cfd) if (cfd_price and cfd) else None
            offset = round(lv["last"] - mid, 4) if (lv.get("last") is not None and mid) else None
            out["markets"].append({"future": sym, "name": SPECS.get(root, (0, 0, root))[2], "cfd": cfd,
                                   "basis": offset, "futures": lv, "cfd_levels": to_cfd(lv, offset)})
        return out

    async def _writer(self) -> None:
        d = out_dir()
        while True:
            try:
                snap = self.snapshot(_mt5_mid)
                d.mkdir(parents=True, exist_ok=True)
                tmp = d / ".levels.tmp"
                tmp.write_text(json.dumps(snap, default=str))
                os.replace(tmp, d / "levels.json")
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[ORDERFLOW] snapshot: {e}")
            await asyncio.sleep(15)

    async def start(self) -> None:
        if not self.enabled or self.thread is not None:
            return
        self.thread = threading.Thread(target=self._run, name="orderflow", daemon=True)
        self.thread.start()
        threading.Thread(target=live_bars.run_flusher, name="marketdata-live", daemon=True).start()
        self.task = asyncio.create_task(self._writer())


def _mt5_mid(symbol: str) -> float | None:
    try:
        import MetaTrader5 as mt5
        t = mt5.symbol_info_tick(symbol)
        if t is None or not t.bid:
            mt5.symbol_select(symbol, True)          # not in Market Watch yet
            t = mt5.symbol_info_tick(symbol)
        if t is None or not t.bid:
            return None
        return (float(t.bid) + float(t.ask)) / 2
    except Exception:  # noqa: BLE001
        return None


orderflow_feed = OrderFlowFeed()
