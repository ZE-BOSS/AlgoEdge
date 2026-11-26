"""Order-flow session profile and the Databento record handling (backend/orderflow)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.orderflow.profile import SessionProfile, session_of, session_start_utc, to_cfd, value_area


def ns(y, mo, d, h, mi=0):
    return int(datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp() * 1e9)


def test_session_rolls_at_17_chicago():
    # 22:59 UTC = 16:59 CST (Nov) -> session of that day; 23:00 UTC = 17:00 CST -> next day
    assert session_of(ns(2026, 11, 24, 22, 59)) == "2026-11-24"
    assert session_of(ns(2026, 11, 24, 23, 0)) == "2026-11-25"
    st = session_start_utc(datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc))
    assert st == datetime(2026, 11, 23, 23, 0, tzinfo=timezone.utc)


def test_value_area_holds_70_percent_around_poc():
    vol = {100.0: 5, 100.1: 10, 100.2: 40, 100.3: 20, 100.4: 15, 100.5: 10}
    poc, val, vah = value_area(vol)
    assert poc == 100.2
    inside = sum(v for p, v in vol.items() if val <= p <= vah)
    assert inside >= 0.7 * sum(vol.values()) and (val, vah) == (100.2, 100.4)
    assert value_area({}) == (None, None, None)


def test_profile_delta_vwap_big_prints_and_rollover():
    p = SessionProfile(symbol="GC.v.0", tick=0.1, big_size=20)
    t0 = ns(2026, 11, 24, 14)
    p.add(2650.04, 10, "B", t0)
    p.add(2650.11, 30, "A", t0 + 60 * 10**9)
    p.add(2650.2, 5, "B", t0 + 3600 * 10**9)
    lv = p.levels()
    assert lv["delta"] == -15 and lv["delta_30m"] == 5 and lv["volume"] == 45
    assert lv["poc"] == 2650.1 and lv["vwap"] == pytest.approx((2650.04 * 10 + 2650.11 * 30 + 2650.2 * 5) / 45, abs=1e-3)
    assert lv["big_prints"][0]["side"] == "sell" and lv["big_prints"][0]["size"] == 30
    assert lv["high"] == 2650.2 and lv["low"] == 2650.04
    p.add(2660.0, 1, "B", ns(2026, 11, 24, 23, 5))       # next session
    lv2 = p.levels()
    assert lv2["session"] == "2026-11-25" and lv2["volume"] == 1 and lv2["prev"]["poc"] == 2650.1


def test_to_cfd_subtracts_basis():
    lv = {"last": 2672.0, "poc": 2670.0, "vah": 2675.0, "val": 2665.0, "vwap": 2669.5, "high": 2680, "low": 2660,
          "prev": {"poc": 2650.0}, "big_prints": [{"price": 2671.0, "size": 40, "side": "buy"}]}
    c = to_cfd(lv, 22.0)
    assert c["poc"] == 2648.0 and c["last"] == 2650.0 and c["prev"]["poc"] == 2628.0
    assert c["big_prints"][0]["price"] == 2649.0
    assert to_cfd(lv, None) == {}


def test_feed_routes_records_by_symbol_mapping(monkeypatch, tmp_path):
    db = pytest.importorskip("databento")
    monkeypatch.setenv("ORDERFLOW_MAP", "GC.v.0=XAUUSDm,NQ.v.0=USTECm")
    from backend.orderflow.feed import OrderFlowFeed
    f = OrderFlowFeed()

    class Map:
        def __init__(self, iid, sym):
            self.instrument_id, self.stype_in_symbol = iid, sym
    monkeypatch.setattr(db, "SymbolMappingMsg", Map)
    f.on_record(Map(11, "GC.v.0"), db)
    f.on_record(Map(22, "NQ.v.0"), db)
    t0 = ns(2026, 11, 24, 15)
    f.on_record(db.TradeMsg(publisher_id=1, instrument_id=11, ts_event=t0, price=2672_100_000_000, size=3,
                            action=db.Action.TRADE, side=db.Side.BID, depth=0, ts_recv=t0, flags=0), db)
    f.on_record(db.TradeMsg(publisher_id=1, instrument_id=22, ts_event=t0, price=21000_250_000_000, size=2,
                            action=db.Action.TRADE, side=db.Side.ASK, depth=0, ts_recv=t0, flags=0), db)
    snap = f.snapshot(lambda cfd: {"XAUUSDm": 2650.0, "USTECm": 20990.0}[cfd])
    gc, nq = snap["markets"]
    assert gc["futures"]["last"] == pytest.approx(2672.1) and gc["futures"]["delta"] == 3
    assert gc["basis"] == pytest.approx(22.1) and gc["cfd_levels"]["last"] == pytest.approx(2650.0)
    assert nq["futures"]["delta"] == -2 and nq["cfd"] == "USTECm"
    assert not f.enabled


def test_reconnect_replay_does_not_double_count(monkeypatch):
    db = pytest.importorskip("databento")
    monkeypatch.setenv("ORDERFLOW_MAP", "GC.v.0=XAUUSDm")
    from backend.orderflow.feed import OrderFlowFeed
    f = OrderFlowFeed()
    t0 = ns(2026, 11, 24, 15)
    rec = db.TradeMsg(publisher_id=1, instrument_id=11, ts_event=t0, price=2672_100_000_000, size=3,
                      action=db.Action.TRADE, side=db.Side.BID, depth=0, ts_recv=t0, flags=0)
    f.on_record(rec, db)
    f.profiles["GC.v.0"].prev = {"poc": 2600.0}
    f.fresh_profiles()                                 # what a reconnect does before the replay
    f.on_record(rec, db)
    p = f.profiles["GC.v.0"]
    assert p.vol == 3 and p.delta == 3 and p.prev == {"poc": 2600.0}
