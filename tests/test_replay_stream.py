"""
tests/test_replay_stream.py

[Phase 13 §C.3] Coverage for the replay event stream.

The behaviours worth pinning down are the ones that only show up under load —
batching, throttling, and ordering — because that is where a naive stream
either floods the socket or draws markers in the wrong place.
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.services.replay_stream import (  # noqa: E402
    MAX_SERIES_BARS,
    ReplayStreamer,
    downsample,
)


class FakeManager:
    """Records what would have gone over the socket."""

    def __init__(self):
        self.sent = []

    async def broadcast_to_user(self, user_id, payload):
        self.sent.append(payload)

    def of_type(self, t):
        return [m for m in self.sent if m["type"] == t]


def _drain(streamer, mgr):
    """
    Run the event loop so the fire-and-forget `create_task` sends actually
    execute. The streamer never awaits its own sends by design — coupling
    socket speed to simulation speed would let a slow client slow the backtest.
    """
    async def _run():
        await asyncio.sleep(0)
        await asyncio.sleep(0)
    asyncio.run(_run())


def _bar(t, price=100.0):
    return {"time": t, "open": price, "high": price + 1, "low": price - 1, "close": price}


def _make(mgr, **kw):
    """
    Build a streamer inside a live loop — `asyncio.create_task` needs one.
    Returns (streamer, runner) where runner executes a coroutine that drives it.
    """
    return ReplayStreamer(mgr, "u1", **kw)


def _run_with_loop(fn):
    """Execute `fn(streamer)` inside a running loop so sends are scheduled."""
    async def _main():
        result = fn()
        # Let every scheduled send task run to completion.
        await asyncio.sleep(0)
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if pending:
            await asyncio.gather(*pending)
        return result
    return asyncio.run(_main())


# ─────────────────────────────────────────────────────────────────────────
# Batching and throttling
# ─────────────────────────────────────────────────────────────────────────

def test_bars_are_batched_not_sent_individually():
    mgr = FakeManager()

    def body():
        s = _make(mgr, batch_size=10, min_interval_s=0.0)
        s.init([{"slot_id": "EURUSD", "symbol": "EURUSD", "strategy_id": "VWAP_v1"}])
        s.leg_start("EURUSD", total_bars=100)
        for i in range(100):
            s.bar("EURUSD", _bar(1_700_000_000 + i * 300))
        s.leg_done("EURUSD")
        return s

    _run_with_loop(body)
    batches = mgr.of_type("replay_bars")
    assert len(batches) == 10, f"expected 10 batches of 10, got {len(batches)}"
    assert sum(len(b["bars"]) for b in batches) == 100, "bars were lost"


def test_throttle_suppresses_flushes_without_dropping_bars():
    """
    A throttled flush must ACCUMULATE, never discard. A backtest emits bars far
    faster than a screen can show them; the fix is fewer, bigger messages — not
    fewer bars.
    """
    mgr = FakeManager()

    def body():
        # A 1-hour floor guarantees every flush after the first is throttled.
        s = _make(mgr, batch_size=10, min_interval_s=3600.0)
        s.init([{"slot_id": "EURUSD", "symbol": "EURUSD", "strategy_id": "VWAP_v1"}])
        s.leg_start("EURUSD", total_bars=100)
        for i in range(100):
            s.bar("EURUSD", _bar(1_700_000_000 + i * 300))
        s.leg_done("EURUSD")   # forced flush, ignores the throttle
        return s

    _run_with_loop(body)
    batches = mgr.of_type("replay_bars")
    assert len(batches) < 10, "throttle did not reduce message count"
    assert sum(len(b["bars"]) for b in batches) == 100, "throttling lost bars"


def test_cursor_lets_client_detect_a_gap():
    mgr = FakeManager()

    def body():
        s = _make(mgr, batch_size=10, min_interval_s=0.0)
        s.init([{"slot_id": "X", "symbol": "X", "strategy_id": "S"}])
        s.leg_start("X", total_bars=50)
        for i in range(50):
            s.bar("X", _bar(1_700_000_000 + i * 300))
        s.leg_done("X")
        return s

    _run_with_loop(body)
    cursors = [b["cursor"] for b in mgr.of_type("replay_bars")]
    assert cursors == sorted(cursors), "cursors must be monotonic"
    assert cursors[-1] == 50


# ─────────────────────────────────────────────────────────────────────────
# Ordering — the bug this prevents is a marker drawn in empty space
# ─────────────────────────────────────────────────────────────────────────

def test_signal_forces_a_bar_flush_first():
    """
    A signal must never overtake the bar it belongs on. If it did, the chart
    would briefly draw the marker past the end of the candle series.
    """
    mgr = FakeManager()

    def body():
        s = _make(mgr, batch_size=1000, min_interval_s=3600.0)  # nothing would flush on its own
        s.init([{"slot_id": "X", "symbol": "X", "strategy_id": "S"}])
        s.leg_start("X", total_bars=10)
        for i in range(5):
            s.bar("X", _bar(1_700_000_000 + i * 300))
        s.signal("X", {"time": 1_700_001_200, "direction": "BUY", "entry_price": 100.0,
                       "stop_loss": 99.0, "take_profit": 102.0,
                       "metadata": {"markings": [{"type": "FVG", "label": "H4 FVG"}]}})
        s.leg_done("X")
        return s

    _run_with_loop(body)
    kinds = [m["type"] for m in mgr.sent]
    assert kinds.index("replay_bars") < kinds.index("replay_signal"), \
        "signal was emitted before the bars it sits on"


def test_signal_carries_markings_through():
    mgr = FakeManager()

    def body():
        s = _make(mgr, min_interval_s=0.0)
        s.init([{"slot_id": "X", "symbol": "X", "strategy_id": "S"}])
        s.leg_start("X")
        s.signal("X", {
            "time": 1_700_000_000, "direction": "BUY", "entry_price": 100.0,
            "stop_loss": 99.0, "take_profit": 102.0, "confluence_score": 88,
            "metadata": {
                "markings": [{"type": "FVG", "label": "H4 FVG", "top": 101.0, "bottom": 100.5}],
                "confluence_summary": {"trigger": ["H4 FVG"]},
            },
        })
        return s

    _run_with_loop(body)
    sig = mgr.of_type("replay_signal")[0]
    assert sig["markings"][0]["label"] == "H4 FVG"
    assert sig["confluence_summary"] == {"trigger": ["H4 FVG"]}
    assert sig["confluence_score"] == 88
    json.dumps(sig)


def test_signal_accepts_object_or_dict():
    """The single-symbol route passes a dict; other call sites may pass a model."""
    mgr = FakeManager()

    class SignalObj:
        time = 1_700_000_000
        direction = "SELL"
        entry_price = 100.0
        stop_loss = 101.0
        take_profit = 98.0
        confluence_score = 70
        metadata = {"markings": []}

    def body():
        s = _make(mgr, min_interval_s=0.0)
        s.init([]); s.leg_start("X")
        s.signal("X", SignalObj())
        return s

    _run_with_loop(body)
    assert mgr.of_type("replay_signal")[0]["direction"] == "SELL"


# ─────────────────────────────────────────────────────────────────────────
# Lifecycle and the replay-mode series
# ─────────────────────────────────────────────────────────────────────────

def test_portfolio_legs_stay_independent_per_slot():
    """
    Two legs on the same symbol under different strategies must remain two
    separate series — merging them by symbol is the aliasing the design forbids.
    """
    mgr = FakeManager()

    def body():
        s = _make(mgr, batch_size=5, min_interval_s=0.0, mode="portfolio")
        s.init([
            {"slot_id": "GBPJPY::VWAP_v1", "symbol": "GBPJPY", "strategy_id": "VWAP_v1"},
            {"slot_id": "GBPJPY::APA_v1", "symbol": "GBPJPY", "strategy_id": "APA_v1"},
        ])
        for slot in ("GBPJPY::VWAP_v1", "GBPJPY::APA_v1"):
            s.leg_start(slot, total_bars=10)
            for i in range(10):
                s.bar(slot, _bar(1_700_000_000 + i * 300))
            s.leg_done(slot)
        s.done()
        return s

    streamer = _run_with_loop(body)
    payload = streamer.series_payload()
    assert set(payload["series"]) == {"GBPJPY::VWAP_v1", "GBPJPY::APA_v1"}
    assert len(payload["series"]["GBPJPY::VWAP_v1"]) == 10
    assert [m["slot_id"] for m in mgr.of_type("replay_leg_start")] == \
        ["GBPJPY::VWAP_v1", "GBPJPY::APA_v1"]


def test_lifecycle_events_are_emitted_in_order():
    mgr = FakeManager()

    def body():
        s = _make(mgr, min_interval_s=0.0)
        s.init([{"slot_id": "X", "symbol": "X", "strategy_id": "S"}])
        s.leg_start("X", total_bars=1)
        s.bar("X", _bar(1_700_000_000))
        s.leg_done("X")
        s.done()
        return s

    _run_with_loop(body)
    kinds = [m["type"] for m in mgr.sent]
    assert kinds[0] == "replay_init"
    assert kinds[1] == "replay_leg_start"
    assert kinds[-1] == "replay_done"


def test_disabled_streamer_emits_nothing():
    mgr = FakeManager()

    def body():
        s = _make(mgr, enabled=False)
        s.init([{"slot_id": "X", "symbol": "X", "strategy_id": "S"}])
        s.leg_start("X"); s.bar("X", _bar(1)); s.done()
        return s

    _run_with_loop(body)
    assert mgr.sent == []


# ─────────────────────────────────────────────────────────────────────────
# Downsampling — V4's fix
# ─────────────────────────────────────────────────────────────────────────

def test_downsample_preserves_span_and_extremes():
    """
    Decimation must aggregate, not truncate. Dropping bars outright would erase
    the highs and lows that trades actually resolved against.
    """
    bars = [{"time": i, "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}
            for i in range(20_000)]
    bars[7_777]["high"] = 999.0      # a spike that must survive
    bars[12_345]["low"] = 1.0

    out = downsample(bars, limit=1000)
    assert len(out) <= 1000
    assert out[0]["time"] == 0
    assert out[-1]["time"] >= 19_000, "the tail of the run was truncated"
    assert max(b["high"] for b in out) == 999.0, "a high was erased by decimation"
    assert min(b["low"] for b in out) == 1.0, "a low was erased by decimation"


def test_downsample_is_a_noop_under_the_limit():
    bars = [{"time": i, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0} for i in range(100)]
    assert downsample(bars, limit=MAX_SERIES_BARS) is bars


def test_broken_socket_does_not_raise_into_the_backtest():
    """A visualisation failure must cost the animation, not the run."""
    class ExplodingManager:
        async def broadcast_to_user(self, user_id, payload):
            raise RuntimeError("socket gone")

    def body():
        s = _make(ExplodingManager(), min_interval_s=0.0)
        s.init([{"slot_id": "X", "symbol": "X", "strategy_id": "S"}])
        s.leg_start("X")
        for i in range(10):
            s.bar("X", _bar(i))
        s.leg_done("X")
        s.done()
        return s

    streamer = _run_with_loop(body)          # must not raise
    assert len(streamer.series_payload()["series"]["X"]) == 10
    # Every failure must have been absorbed and counted inside the send task,
    # not left to surface as an unretrieved-task warning per bar batch.
    assert streamer.stats["dropped_sends"] > 0
