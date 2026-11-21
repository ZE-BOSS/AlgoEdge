"""Demo <-> live signal sync (services/signal_sync.py, 21 Nov 2026)."""

from __future__ import annotations

import json
import os
import time

from backend.services.signal_sync import SignalSync
from backend.strategies.base_strategy import TradeSignal


def _pair(tmp_path):
    kw = dict(root=tmp_path, grace_s=0.0, max_age_s=240.0, enabled=True)
    return SignalSync(self_name="demo", peers=["live"], **kw), SignalSync(self_name="live", peers=["demo"], **kw)


def _sig(**kw):
    d = dict(strategy_id="VWAP_v1", symbol="US Tech 100", direction="SELL", signal_type="VWAP_SESSION_TREND",
             timeframe="M5", entry_price=26175.15, stop_loss=26342.60, take_profit=25505.35,
             confluence_score=80, timestamp=1795195800.0, metadata={"session": "ny", "slot_id": "s1"})
    d.update(kw)
    return TradeSignal(**d)


def test_peer_trade_is_placed_when_own_strategy_did_not_fire(tmp_path):
    demo, live = _pair(tmp_path)
    assert demo.publish(_sig()) is not None
    assert len(live.poll()) == 1
    rec = live.take("VWAP_v1", "US Tech 100", "M5")
    assert rec is not None
    s = live.to_signal(rec)
    assert (s.direction, s.stop_loss, s.take_profit, s.timestamp) == ("SELL", 26342.60, 25505.35, 1795195800.0)
    assert s.metadata["synced_from"] == "demo" and s.metadata["passed_gates"] is True
    assert "slot_id" not in s.metadata, "the receiving slot sets its own slot fields"
    assert live.take("VWAP_v1", "US Tech 100", "M5") is None, "taken once"


def test_no_mirror_when_own_strategy_fired_on_that_bar(tmp_path):
    demo, live = _pair(tmp_path)
    live.note_local("VWAP_v1", "US Tech 100", 1795195800.0)
    demo.publish(_sig())
    assert live.poll() == []
    assert live.take("VWAP_v1", "US Tech 100", "M5") is None


def test_own_signal_after_parking_cancels_the_mirror(tmp_path):
    demo, live = _pair(tmp_path)
    demo.publish(_sig())
    live.poll()
    live.note_local("VWAP_v1", "US Tech 100", 1795195800.0)
    assert live.take("VWAP_v1", "US Tech 100", "M5") is None


def test_synced_trades_are_not_republished(tmp_path):
    demo, live = _pair(tmp_path)
    demo.publish(_sig())
    live.poll()
    s = live.to_signal(live.take("VWAP_v1", "US Tech 100", "M5"))
    assert live.publish(s) is None
    assert demo.poll() == [], "no ping-pong"


def test_grace_period_and_staleness(tmp_path):
    demo, live = _pair(tmp_path)
    live.grace_s = 8.0
    p = demo.publish(_sig())
    assert live.poll() == [], "own strategy gets the grace period first"
    rec = json.loads(p.read_text())
    assert len(live.poll(now=rec["executed_at"] + 9)) == 1
    demo2, live2 = _pair(tmp_path / "b")
    p2 = demo2.publish(_sig(timestamp=1795196100.0))
    old = json.loads(p2.read_text())
    old["executed_at"] = time.time() - 600
    p2.write_text(json.dumps(old))
    assert live2.poll() == [], "too old to place"


def test_other_slot_or_timeframe_is_not_taken(tmp_path):
    demo, live = _pair(tmp_path)
    demo.publish(_sig())
    live.poll()
    assert live.take("VWAP_v1", "XAUUSD", "M5") is None
    assert live.take("VWAP_v1", "US Tech 100", "M30") is None


def test_disabled_does_nothing(tmp_path):
    demo, live = _pair(tmp_path)
    demo.enabled = live.enabled = False
    assert demo.publish(_sig()) is None
    assert live.poll() == [] and live.take("VWAP_v1", "US Tech 100") is None


def test_defaults_pair_demo_and_live(monkeypatch, tmp_path):
    monkeypatch.setenv("SIGNAL_SYNC_SELF", "live")
    monkeypatch.delenv("SIGNAL_SYNC_PEERS", raising=False)
    monkeypatch.delenv("SIGNAL_SYNC", raising=False)
    s = SignalSync.from_env()
    assert s.enabled and s.peers == ["demo"]
    monkeypatch.setenv("SIGNAL_SYNC_SELF", "flip")
    assert not SignalSync.from_env().enabled, "flip has no default peer"
    monkeypatch.setenv("SIGNAL_SYNC", "0")
    monkeypatch.setenv("SIGNAL_SYNC_SELF", "demo")
    assert not SignalSync.from_env().enabled


def test_cleanup_removes_old_own_files(tmp_path):
    demo, _ = _pair(tmp_path)
    p = demo.publish(_sig())
    os.utime(p, (time.time() - 3 * 86400,) * 2)
    assert demo.cleanup() == 1 and not p.exists()
