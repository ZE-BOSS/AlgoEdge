"""
backend/services/watch_service.py

Pre-signal alerts for the live bot (Signal Groups plan, Phase 1).

Two kinds of alert, both sent to the admin Telegram:

1. Heads-up (exact). A strategy that knows its entry level in advance
   implements `watch(symbol, timeframe, candles)` and returns the levels it is
   waiting on - "BUY on a close above 4426". When price comes within
   WATCH_ATR x ATR of a level (or is already through it, for a rule decided at
   a later close such as IBS), the bot says so once: "Heads-up: GOLD BUY likely
   soon (Sigma, live)". If the level drops away without a signal, a short
   "setup withdrawn" follows.

2. Near signal (generic). Every strategy already passes its entry through
   named `gate()` checks. A read-only tap records those checks on each live bar
   without touching the signal (the strategy's own GateRecorder stays off, so
   no metadata changes and nothing about trading moves). The tap learns which
   gates a real signal passes - from signals the strategy gives, including the
   ones during warm-up - and when a bar meets every one of them but one, the
   bot says which condition is missing: "Near signal: US Tech 100 VWAP_v1 -
   everything met except volume_confirmation (rvol 0.56, needs 0.8)".

Signals that are found and then blocked (risk engine, news, broker, late bar)
already report themselves through bot_service._save_signal_state; this module
covers what happens before a signal exists.

Off with WATCH_ALERTS=0. WATCH_ATR (default 0.5) sets how close is "close".
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from backend.strategies.gate_recorder import GateRecorder
from backend.utils.logger import get_logger

logger = get_logger(__name__)

NEAR_COOLDOWN_S = 3600          # one "near signal" per slot and missing gate per hour
HEADSUP_COOLDOWN_S = 1800       # one heads-up per slot, direction and level per 30 min
MAX_ALERTS_PER_SLOT_HOUR = 4
# A missing gate that only says "outside the trading window" is not news.
QUIET_GATES = ("session", "window", "mode", "exclusion", "warmup", "warm_up", "timeframe", "cooldown")


# ── the read-only gate tap ─────────────────────────────────────────────────────

class WatchTap(GateRecorder):
    """A GateRecorder that records but never changes a signal.

    The live default is a disabled recorder, and an enabled one adds
    `passed_gates` / `rejection_reasons` to every signal, which bot_service
    reads (a signal with passed_gates False is skipped). This one returns no
    metadata, so the signals the strategy gives are byte-for-byte the same, and
    it keeps only the last few candidates."""

    __slots__ = ("last_chain",)
    KEEP = 64

    def __init__(self):
        super().__init__(enabled=True, max_records=10_000)
        self.last_chain: list[str] = []

    def emitted(self, signal) -> None:
        super().emitted(signal)
        cur = self._current
        if cur is not None and cur.events:
            # Recorded here, not read back later: warm-up steps through hundreds of
            # bars and the candidate list keeps only the last few.
            self.last_chain = [e.name for e in cur.events if e.passed]

    def metadata_for_signal(self) -> dict[str, Any]:
        return {}

    def begin(self, symbol, timeframe, bar_index=-1, bar_time=None):
        super().begin(symbol, timeframe, bar_index, bar_time)
        if len(self.records) > self.KEEP * 2:
            del self.records[: len(self.records) - self.KEEP]


def attach_tap(engine: Any) -> WatchTap | None:
    """Give a live engine a WatchTap unless its recorder is already in use."""
    g = getattr(engine, "gates", None)
    if isinstance(g, WatchTap):
        return g
    if g is not None and getattr(g, "enabled", False):
        return None                     # someone (an ablation) is using it - leave it alone
    tap = WatchTap()
    try:
        engine.gates = tap
    except Exception:                   # noqa: BLE001
        return None
    return tap


# ── state ─────────────────────────────────────────────────────────────────────

@dataclass
class _SlotState:
    headsup: dict | None = None             # the active heads-up: {direction, level, cross, sent}
    sent: dict = field(default_factory=dict)  # alert key -> last sent epoch
    recent: list = field(default_factory=list)  # epochs of alerts sent this hour


def _atr(candles, n: int = 14) -> float | None:
    try:
        h = candles["high"].to_numpy(dtype=float)
        lo = candles["low"].to_numpy(dtype=float)
        c = candles["close"].to_numpy(dtype=float)
    except Exception:                       # noqa: BLE001
        return None
    if len(c) < n + 1:
        return None
    tr = np.maximum(h[1:] - lo[1:], np.maximum(abs(h[1:] - c[:-1]), abs(lo[1:] - c[:-1])))
    a = float(tr[-n:].mean())
    return a if np.isfinite(a) and a > 0 else None


def _esc(text: Any) -> str:
    """Telegram Markdown V1: names like QL_IBS_v1 would otherwise start italics."""
    t = str(text or "")
    for ch in ("\\", "_", "*", "`", "["):
        t = t.replace(ch, "\\" + ch)
    return t


def _fmt(x: float) -> str:
    return f"{x:,.5f}".rstrip("0").rstrip(".") if abs(x) < 10 else f"{x:,.2f}"


class WatchService:
    def __init__(self, chains_path: str | os.PathLike | None = None):
        self.enabled = os.getenv("WATCH_ALERTS", "1") not in ("0", "false", "no", "off")
        self.atr_mult = float(os.getenv("WATCH_ATR", "0.5") or 0.5)
        self.chains: dict[str, list[str]] = {}
        self.slots: dict[str, _SlotState] = {}
        self._path = Path(chains_path) if chains_path else Path("data") / "watch_chains.json"
        self._load()
        self.sender = None                    # async callable(text) - set by bot_service / tests
        self.events: list[tuple] = []         # structured heads-ups for the group bot

    # -- chains: which gates a real signal passes, per strategy ------------------
    def _load(self) -> None:
        try:
            self.chains = {k: list(v) for k, v in json.loads(self._path.read_text()).items()}
        except Exception:                     # noqa: BLE001
            self.chains = {}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self.chains, indent=1))
        except Exception as e:                # noqa: BLE001
            logger.debug(f"watch chains not saved: {e}")

    def learn(self, strategy_id: str, tap: WatchTap) -> None:
        """Remember the gates passed by the latest signal the strategy gave."""
        chain = tap.last_chain
        if chain and chain != self.chains.get(strategy_id):
            self.chains[strategy_id] = list(chain)
            self._save()

    @staticmethod
    def near_miss(chain: list[str], rec) -> tuple[str, str | None] | None:
        """(missing gate, its detail) when the candidate met every gate of the
        chain but one, and that one was evaluated and failed."""
        if not chain or rec is None or rec.emitted or len(chain) < 2:
            return None
        passed = {e.name for e in rec.events if e.passed}
        failed = [e for e in rec.events if not e.passed]
        missing = [g for g in chain if g not in passed]
        if len(missing) != 1:
            return None
        hit = next((e for e in failed if e.name == missing[0]), None)
        if hit is None:
            return None
        if any(q in hit.name.lower() for q in QUIET_GATES):
            return None
        return hit.name, hit.detail

    # -- throttling ------------------------------------------------------------
    def _allow(self, st: _SlotState, key: str, cooldown: float, now: float) -> bool:
        st.recent = [t for t in st.recent if now - t < 3600]
        if len(st.recent) >= MAX_ALERTS_PER_SLOT_HOUR:
            return False
        if key in st.sent and now - st.sent[key] < cooldown:
            return False
        st.sent[key] = now
        st.recent.append(now)
        return True

    # -- the per-bar hook --------------------------------------------------------
    def evaluate(self, *, slot_id: str, strategy_id: str, symbol: str, engine: Any, candles,
                 timeframe: str, signal: Any, instance: str = "", now: float | None = None,
                 warming: bool = False) -> list[str]:
        """Called once per new closed bar of a live slot, after the engine ran.
        Returns the alert texts to send (plain text, Telegram Markdown V1)."""
        if not self.enabled:
            return []
        now = time.time() if now is None else now
        st = self.slots.setdefault(slot_id, _SlotState())
        tap = engine.gates if isinstance(getattr(engine, "gates", None), WatchTap) else None
        if tap is not None:
            self.learn(strategy_id, tap)
        if warming:
            return []
        out: list[str] = []
        acct = f", {_esc(instance)}" if instance else ""
        sym, sid = _esc(symbol), _esc(strategy_id)
        price = None
        try:
            price = float(candles["close"].iloc[-1])
        except Exception:                     # noqa: BLE001
            pass

        # A signal on this bar: whatever was pending is now a signal; the existing
        # EXECUTED / SKIPPED messages say what happened to it.
        if signal is not None:
            st.headsup = None
            return out

        # 1. exact heads-up
        levels = []
        watch = getattr(engine, "watch", None)
        if callable(watch):
            try:
                levels = watch(symbol, timeframe, candles) or []
            except Exception as e:            # noqa: BLE001
                logger.debug(f"[WATCH] {strategy_id} watch() failed: {e}")
                levels = []
        atr = _atr(candles)
        best = None
        if price is not None and atr:
            for lv in levels:
                lvl = float(lv["level"])
                cross = lv.get("cross") or ("above" if lv["direction"] == "BUY" else "below")
                gap = (lvl - price) if cross == "above" else (price - lvl)   # > 0: not reached yet
                if gap <= self.atr_mult * atr and (best is None or gap < best[0]):
                    best = (gap, lv, cross)
        if best is not None:
            gap, lv, cross = best
            key = f"hu:{lv['direction']}:{round(float(lv['level']), 2)}"
            if self._allow(st, key, HEADSUP_COOLDOWN_S, now):
                st.headsup = {"direction": lv["direction"], "level": float(lv["level"]), "cross": cross, "sent": now}
                self.events.append(("headsup", strategy_id, slot_id, symbol, dict(lv, cross=cross)))
                where = (f"price {_fmt(price)} is already {'above' if cross == 'above' else 'below'} it"
                         if gap <= 0 else f"price {_fmt(price)}, {gap / atr:.1f} ATR away")
                stop = f" · stop ~{_fmt(float(lv['stop']))}" if lv.get("stop") else ""
                note = f"\n{_esc(lv['note'])}" if lv.get("note") else ""
                out.append(f"👀 *Heads-up*: {sym} {lv['direction']} likely soon ({sid}{acct})\n"
                           f"Trigger: close {cross} {_fmt(float(lv['level']))}{stop} · {where}{note}")
        elif st.headsup and price is not None and atr:
            h = st.headsup
            gap = (h["level"] - price) if h["cross"] == "above" else (price - h["level"])
            still = any(lv["direction"] == h["direction"] and abs(float(lv["level"]) - h["level"]) < 1e-9
                        for lv in levels)
            if not still or gap > 3 * self.atr_mult * atr:
                why = ("its window closed or the setup changed" if not still
                       else f"price moved away ({_fmt(price)}, {gap / atr:.1f} ATR from the trigger)")
                out.append(f"↩️ *Setup withdrawn*: {sym} {h['direction']} ({sid}{acct}) - {why}. No trade.")
                self.events.append(("withdrawn", strategy_id, slot_id, symbol, h))
                st.headsup = None

        # 2. generic near signal
        if tap is not None and tap.records:
            nm = self.near_miss(self.chains.get(strategy_id, []), tap.records[-1])
            if nm is not None:
                gate, detail = nm
                if self._allow(st, f"near:{gate}", NEAR_COOLDOWN_S, now):
                    d = f" ({_esc(detail)})" if detail else ""
                    out.append(f"🟡 *Near signal*: {sym} {sid}{acct} - every condition met except "
                               f"*{_esc(gate.replace('_', ' '))}*{d}. No trade on this bar.")
        return out

    async def after_bar(self, **kw) -> None:
        texts = self.evaluate(**kw)
        evs, self.events = self.events, []
        if evs:
            try:
                from backend.groups import feeds as _gfeeds
                for kind, sid, slot, sym, lv in evs:
                    if kind == "headsup":
                        _gfeeds.headsup(sid, slot, sym, lv)
                    else:
                        _gfeeds.headsup_withdrawn(slot)
            except Exception as e:            # noqa: BLE001
                logger.debug(f"[WATCH] group feed: {e}")
        if not texts or self.sender is None:
            return
        for t in texts:
            try:
                await self.sender(t)
            except Exception as e:            # noqa: BLE001
                logger.debug(f"[WATCH] alert not sent: {e}")


watch_service = WatchService()
