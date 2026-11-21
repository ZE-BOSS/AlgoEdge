"""
backend/services/signal_sync.py

Demo <-> live signal sync (21 Nov 2026).

The demo and live instances run the same strategies on the same Deriv
instruments, but their feeds are not identical: on 20 Nov 2026 live's VWAP_v1
missed a US Tech 100 sell that demo took, because Deriv's real server had
halved the tick volume it reports (a volume gate failed on live only). Prices
agree to a few points; the metrics a strategy derives from them do not always.

So each instance publishes every trade its own strategies execute, and watches
its peer's. When the peer executed a strategy x symbol x bar that this instance
also runs, and this instance's strategy did not fire on that bar, this instance
places the same trade at once - no strategy re-check - with its OWN sizing,
risk limits and position management (the signal is fed into the normal
execution path). Trades placed by the sync are never re-published, so the two
cannot ping-pong.

Transport: one JSON file per executed signal in a directory both instances can
read (both run on the same server): <SIGNAL_SYNC_DIR>/<instance>/<file>.json.

Environment:
    SIGNAL_SYNC          "0" turns it off (default on for the demo/live pair)
    SIGNAL_SYNC_SELF     this instance's name (default: /opt/algoedge/<name>/app)
    SIGNAL_SYNC_PEERS    comma list (default: demo <-> live)
    SIGNAL_SYNC_DIR      default /opt/algoedge/shared/signal_sync
    SIGNAL_SYNC_GRACE_S  wait this long after the peer's fill for this
                         instance's own strategy to fire first (default 8)
    SIGNAL_SYNC_MAX_AGE_S  ignore a peer signal older than this (default 240)
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_PEERS = {"demo": ["live"], "live": ["demo"]}


def _instance_name() -> str:
    explicit = os.getenv("SIGNAL_SYNC_SELF") or os.getenv("ALGOEDGE_INSTANCE")
    if explicit:
        return explicit.strip()
    cwd = Path(os.getcwd()).resolve()
    # /opt/algoedge/<instance>/app
    if cwd.name == "app" and cwd.parent.parent.name == "algoedge":
        return cwd.parent.name
    return ""


def _json_safe(v: Any) -> Any:
    try:
        json.dumps(v)
        return v
    except (TypeError, ValueError):
        if isinstance(v, dict):
            return {str(k): _json_safe(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [_json_safe(x) for x in v]
        return str(v)


def bar_key(strategy_id: str, symbol: str, bar_time: float | None) -> tuple[str, str, int]:
    return (str(strategy_id), str(symbol), int(float(bar_time or 0)))


@dataclass
class SignalSync:
    self_name: str = ""
    peers: list[str] = field(default_factory=list)
    root: Path = Path("/opt/algoedge/shared/signal_sync")
    grace_s: float = 8.0
    max_age_s: float = 240.0
    enabled: bool = False
    # (strategy, symbol, bar) this instance's own strategies produced a signal for
    _local: dict[tuple[str, str, int], float] = field(default_factory=dict)
    # (strategy, symbol, bar) already taken from the peer or decided against
    _consumed: dict[tuple[str, str, int], float] = field(default_factory=dict)
    # (strategy, symbol) -> peer record waiting for that slot's next scan
    _pending: dict[tuple[str, str], dict] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> SignalSync:
        name = _instance_name()
        peers_env = os.getenv("SIGNAL_SYNC_PEERS")
        peers = [p.strip() for p in peers_env.split(",") if p.strip()] if peers_env else DEFAULT_PEERS.get(name, [])
        enabled = os.getenv("SIGNAL_SYNC", "1") != "0" and bool(name) and bool(peers)
        return cls(self_name=name, peers=[p for p in peers if p != name],
                   root=Path(os.getenv("SIGNAL_SYNC_DIR", "/opt/algoedge/shared/signal_sync")),
                   grace_s=float(os.getenv("SIGNAL_SYNC_GRACE_S", "8")),
                   max_age_s=float(os.getenv("SIGNAL_SYNC_MAX_AGE_S", "240")),
                   enabled=enabled)

    # -- this instance's own signals -------------------------------------------------
    def note_local(self, strategy_id: str, symbol: str, bar_time: float | None) -> None:
        """This instance's strategy fired on this bar (executed or not): never mirror it."""
        if not self.enabled:
            return
        self._local[bar_key(strategy_id, symbol, bar_time)] = time.time()
        self._pending.pop((str(strategy_id), str(symbol)), None)

    def publish(self, signal: Any) -> Path | None:
        """Write an executed signal for the peers. Sync-placed trades are not re-published."""
        if not self.enabled:
            return None
        meta = dict(getattr(signal, "metadata", None) or {})
        if meta.get("synced_from"):
            return None
        bar = getattr(signal, "timestamp", None) or meta.get("timestamp")
        rec = {
            "instance": self.self_name, "executed_at": time.time(), "bar_time": float(bar or 0),
            "strategy_id": getattr(signal, "strategy_id", ""), "symbol": signal.symbol,
            "timeframe": getattr(signal, "timeframe", ""), "direction": signal.direction,
            "signal_type": getattr(signal, "signal_type", ""), "entry_price": float(signal.entry_price),
            "stop_loss": float(signal.stop_loss), "take_profit": float(signal.take_profit),
            "confluence_score": int(getattr(signal, "confluence_score", 0) or 0),
            "metadata": _json_safe({k: v for k, v in meta.items()
                                    if k not in ("slot_id", "slot_max_positions", "slot_max_losses_per_day",
                                                 "slot_risk_per_trade_pct")}),
        }
        d = self.root / self.self_name
        try:
            d.mkdir(parents=True, exist_ok=True)
            k = bar_key(rec["strategy_id"], rec["symbol"], rec["bar_time"])
            name = f"{k[2]}_{k[0]}_{k[1]}_{rec['direction']}.json".replace(" ", "_").replace("/", "_")
            tmp = d / f".{name}.tmp"
            tmp.write_text(json.dumps(rec))
            os.replace(tmp, d / name)
            return d / name
        except OSError as e:
            logger.warning(f"[SYNC] could not publish {rec['strategy_id']} {rec['symbol']}: {e}")
            return None

    # -- the peers' signals ----------------------------------------------------------
    def poll(self, now: float | None = None) -> list[dict]:
        """New peer records that are due (past the grace period, not stale).
        Each is parked for its (strategy, symbol) slot; returns the newly parked."""
        if not self.enabled:
            return []
        now = now or time.time()
        fresh = []
        for peer in self.peers:
            d = self.root / peer
            if not d.is_dir():
                continue
            for f in d.glob("*.json"):
                try:
                    if now - f.stat().st_mtime > max(self.max_age_s, 3600):
                        continue
                    rec = json.loads(f.read_text())
                except (OSError, ValueError):
                    continue
                k = bar_key(rec.get("strategy_id"), rec.get("symbol"), rec.get("bar_time"))
                if k in self._consumed or k in self._local:
                    continue
                age = now - float(rec.get("executed_at") or 0)
                if age > self.max_age_s:
                    self._consumed[k] = now
                    continue
                if age < self.grace_s:
                    continue
                slot = (k[0], k[1])
                cur = self._pending.get(slot)
                if cur is None or float(cur.get("bar_time", 0)) < float(rec.get("bar_time", 0)):
                    self._pending[slot] = rec
                    fresh.append(rec)
        self._trim(now)
        return fresh

    def take(self, strategy_id: str, symbol: str, timeframe: str = "", now: float | None = None) -> dict | None:
        """The parked peer record for this slot, if it still applies (consumed either way)."""
        if not self.enabled:
            return None
        now = now or time.time()
        rec = self._pending.pop((str(strategy_id), str(symbol)), None)
        if rec is None:
            return None
        k = bar_key(rec["strategy_id"], rec["symbol"], rec["bar_time"])
        self._consumed[k] = now
        if k in self._local:
            return None
        if timeframe and rec.get("timeframe") and rec["timeframe"] != timeframe:
            return None
        if now - float(rec.get("executed_at") or 0) > self.max_age_s:
            return None
        return rec

    def has_pending(self) -> bool:
        return bool(self._pending)

    def to_signal(self, rec: dict):
        from backend.strategies.base_strategy import TradeSignal
        meta = dict(rec.get("metadata") or {})
        meta.update({"synced_from": rec.get("instance", "peer"), "passed_gates": True,
                     "synced_peer_executed_at": rec.get("executed_at")})
        return TradeSignal(
            strategy_id=rec["strategy_id"], symbol=rec["symbol"], direction=rec["direction"],
            signal_type=rec.get("signal_type") or "SYNC", timeframe=rec.get("timeframe") or "",
            entry_price=float(rec["entry_price"]), stop_loss=float(rec["stop_loss"]),
            take_profit=float(rec["take_profit"]), confluence_score=int(rec.get("confluence_score") or 0),
            timestamp=float(rec.get("bar_time") or 0) or None, metadata=meta,
        )

    def cleanup(self, older_than_s: float = 2 * 86400) -> int:
        """Delete this instance's own published files older than two days."""
        n = 0
        d = self.root / self.self_name
        if not d.is_dir():
            return 0
        cut = time.time() - older_than_s
        for f in d.glob("*.json"):
            try:
                if f.stat().st_mtime < cut:
                    f.unlink()
                    n += 1
            except OSError:
                pass
        return n

    def _trim(self, now: float) -> None:
        keep = max(self.max_age_s, 3600) * 2
        for dct in (self._local, self._consumed):
            for k in [k for k, t in dct.items() if now - t > keep]:
                del dct[k]


signal_sync = SignalSync.from_env()
