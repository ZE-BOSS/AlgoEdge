"""Telegram copy trading: parser, the open plan, and the service end to end
against a fake MT5 (no Telegram, no broker)."""

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.copytrade import mt5ops
from backend.copytrade import service as svc_mod
from backend.copytrade.models import CopyTradeChannel, CopyTradeOrder, CopyTradeSettings, CopyTradeSignal
from backend.copytrade.parser import parse_message
from backend.copytrade.rules import (
    DEFAULT_RULES, build_open_plan, legs_to_close, merge_rules, resolve_symbol, split_volume,
)
from backend.data.models import Base

GOLD = {"point": 0.01, "tick_size": 0.01, "tick_value": 1.0, "volume_min": 0.01, "volume_step": 0.01,
        "volume_max": 100, "digits": 2, "stops_level": 0, "trade_mode": 4}


# ── parser ──────────────────────────────────────────────────────────────────

def test_parse_multi_tp_signal():
    p = parse_message("XAUUSD BUY NOW @ 2350-2347\nSL 2340\nTP1 2355\nTP2 2360\nTP3 2365\nTP4 2370\n"
                      "TP5 2375\nTP6 2380\nTP7 2385\nTP8 2390\nTP9 2395\nTP10 2400")
    assert p.kind == "OPEN" and p.symbol == "XAUUSD" and p.side == "BUY"
    assert p.sl == 2340 and len(p.tps) == 10 and p.tps[0] == 2355 and p.tps[-1] == 2400
    assert (p.entry_low, p.entry_high) == (2347, 2350)


def test_parse_limit_and_follow_ups():
    p = parse_message("GOLD SELL LIMIT 2365\nSL: 2372\nTP 2350 / 2340 / 2330")
    assert (p.kind, p.side, p.order_type, p.entry, p.sl) == ("OPEN", "SELL", "LIMIT", 2365, 2372)
    assert p.tps == [2350, 2340, 2330]
    assert parse_message("Move SL to entry").kind == "BREAKEVEN"
    assert parse_message("SL to BE now").kind == "BREAKEVEN"
    m = parse_message("move SL to 2352")
    assert (m.kind, m.new_price) == ("MOVE_SL", 2352)
    c = parse_message("close half now")
    assert (c.kind, c.close_fraction) == ("CLOSE", 0.5)
    assert parse_message("close all trades").applies_to == "all"
    assert parse_message("cancel the buy limit").kind == "CANCEL"
    i = parse_message("TP2 hit ✅ 50 pips")
    assert (i.kind, i.tp_index) == ("INFO", 2)
    t = parse_message("change TP3 to 2390")
    assert (t.kind, t.tp_index, t.new_price) == ("MOVE_TP", 3, 2390)


def test_parse_synthetic_and_index_names():
    assert parse_message("Boom 1000 BUY\nSL 9800\nTP 10100").symbol == "Boom 1000 Index"
    assert parse_message("V75 sell now sl 410000 tp 400000").symbol == "Volatility 75 Index"
    p = parse_message("#US100 buy @ 21000 SL 20900 TP 21150")
    assert p.symbol == "NAS100" and p.entry == 21000 and p.tps == [21150]


# ── rules ───────────────────────────────────────────────────────────────────

def test_resolve_symbol_deriv_names():
    avail = ["US Tech 100", "XAUUSD", "Wall Street 30", "Boom 1000 Index"]
    r = merge_rules({})
    assert resolve_symbol("NAS100", avail, r) == "US Tech 100"
    assert resolve_symbol("US30", avail, r) == "Wall Street 30"
    assert resolve_symbol("Boom 1000 Index", avail, r) == "Boom 1000 Index"
    assert resolve_symbol("XAUUSD", ["XAUUSD.m", "EURUSD.m"], r) == "XAUUSD.m"
    assert resolve_symbol("NAS100", avail, merge_rules({"symbol_map": {"NAS100": "XAUUSD"}})) == "XAUUSD"


def test_split_volume_weights_and_min_lot():
    assert split_volume(1.0, [40, 30, 20, 10], GOLD) == [0.4, 0.3, 0.2, 0.1]
    # 0.03 lots over 5 legs: only 3 legs fit at 0.01
    assert split_volume(0.03, [1] * 5, GOLD) == [0.01, 0.01, 0.01]
    assert split_volume(0.005, [1, 1], GOLD) == []
    # remainder of the floor goes to leg 1
    assert split_volume(0.10, [1, 1, 1], GOLD) == [0.04, 0.03, 0.03]


def _sig(**kw):
    base = {"side": "BUY", "order_type": "MARKET", "entry": 2350.0, "entry_low": 2347.0, "entry_high": 2350.0,
            "sl": 2340.0, "tps": [2355.0, 2360.0, 2365.0], "tp_open": False}
    base.update(kw)
    return base


def test_plan_market_inside_zone_risk_split():
    r = merge_rules({"risk_pct": 1.0, "split_weights": [50, 30, 20], "zone_entry": "market"})
    p = build_open_plan(_sig(), {"bid": 2348.8, "ask": 2349.0}, GOLD, 10_000, r, "XAUUSD")
    assert p.ok and p.order_type == "MARKET" and p.entry == 2349.0
    # $100 risk / ($9 per 0.01-unit... ) = 100 / (9.0/0.01*1) = 0.111 lots -> 0.05/0.03/0.02 (+0.01 remainder)
    assert [l["tp"] for l in p.legs] == [2355.0, 2360.0, 2365.0]
    assert sum(l["volume"] for l in p.legs) == pytest.approx(0.11)
    assert p.risk_dollars <= 100.0


def test_plan_price_ran_away_waits_with_limit():
    r = merge_rules({"zone_entry": "market"})
    p = build_open_plan(_sig(), {"bid": 2354.0, "ask": 2354.2}, GOLD, 10_000, r, "XAUUSD")
    assert p.ok and p.order_type == "LIMIT" and p.entry == 2350.0
    p2 = build_open_plan(_sig(), {"bid": 2354.0, "ask": 2354.2}, GOLD, 10_000,
                         merge_rules({"chase_rule": "skip", "zone_entry": "market"}), "XAUUSD")
    assert not p2.ok and "ran past" in p2.reason


def test_plan_limit_already_reached_fills_at_market():
    s = _sig(order_type="LIMIT", entry=2350.0, entry_low=2350.0, entry_high=2350.0)
    p = build_open_plan(s, {"bid": 2349.0, "ask": 2349.2}, GOLD, 10_000, merge_rules({}), "XAUUSD")
    assert p.ok and p.order_type == "MARKET"
    p = build_open_plan(s, {"bid": 2352.0, "ask": 2352.2}, GOLD, 10_000, merge_rules({}), "XAUUSD")
    assert p.ok and p.order_type == "LIMIT" and p.entry == 2350.0


def test_layered_zone_like_gold_trader_sunny():
    """His format: zone posted ahead of time, price above it for a buy, 7 TPs."""
    sig = parse_message("Buy Gold 4487 - 4495Stop Loss 4483TP1 4497TP2 4499TP3 4501TP4 Open (4504/4507/4512/4518)").to_dict()
    assert sig["tps"] == [4497, 4499, 4501, 4504, 4507, 4512, 4518]
    r = merge_rules({})
    p = build_open_plan(sig, {"bid": 4505.0, "ask": 4505.3}, GOLD, 10_000, r, "XAUUSD")
    assert p.ok and p.order_type == "LAYER" and len(p.legs) == 7
    assert [l["kind"] for l in p.legs] == ["LIMIT"] * 7
    assert p.legs[0]["entry"] == 4495 and p.legs[-1]["entry"] == 4487       # first fill -> TP1, deepest -> TP7
    assert p.risk_dollars <= 100.0
    # price inside the zone: the levels already reached fill now, the deeper ones wait
    p = build_open_plan(sig, {"bid": 4491.0, "ask": 4491.3}, GOLD, 10_000, r, "XAUUSD")
    assert [l["kind"] for l in p.legs].count("MARKET") == 3 and p.risk_dollars <= 100.0
    # price under the stop: stale
    assert not build_open_plan(sig, {"bid": 4482.0, "ask": 4482.3}, GOLD, 10_000, r, "XAUUSD").ok


def test_his_follow_ups():
    k = lambda t: parse_message(t).kind
    assert k("Cancelling sell limit orders 💡Price tapped 3 pips below my zone") == "CANCEL"
    assert k("Closing buy limit orders. Price came 30 pips from entry and straight up to TP7 ✅") == "CANCEL"
    assert k("Adjusting buy limit orders 💡Update below 👇") == "CANCEL"
    assert k("Price sold just beneath my zone. Closing sell limit orders and will update with new zone") == "CANCEL"
    assert k("Managing risk by moving most stops to BE 💡if we don’t push up from here I’ll exit positions") == "BREAKEVEN"
    assert k("Price is above the zone. Setting all to BE to stay in risk free 🤝") == "BREAKEVEN"
    assert k("Set BE on all positions to stay in risk free 💯") == "BREAKEVEN"
    assert k("As price goes back above zone I’ll close worst entries for BE and partial profits") == "NONE"
    assert k("Over $11,000 USD 🤑TP1 ✅TP2 ✅✅BE 🤝") == "INFO"
    assert k("TP3 HIT ✅✅✅") == "INFO"
    assert k("Still holding my buy limit orders, price is only 20 pips away") == "INFO"
    assert k("Price has tapped me out this setup with SL") == "INFO"
    m = parse_message("US Session is open - adjusting SL to 4372.5 🎯")
    assert (m.kind, m.new_price) == ("MOVE_SL", 4372.5)


def test_whole_public_history_parses():
    rows = [l.rstrip("\n").split("\t") for l in open(Path(__file__).parents[1] / "data/copytrade/goldtradersunny_public.tsv")
            if l.strip() and not l.startswith("#")]
    sigs = [parse_message(r[4]) for r in rows if r[0] == "S"]
    assert len(sigs) == 96 and all(p.kind == "OPEN" and p.sl and len(p.tps) == 7 for p in sigs)
    kinds = [parse_message(r[4]).kind for r in rows if r[0] == "F"]
    assert "OPEN" not in kinds and kinds.count("CANCEL") >= 40 and kinds.count("BREAKEVEN") >= 50


def test_plan_refusals():
    r = merge_rules({})
    assert "no stop" in build_open_plan(_sig(sl=None), {"bid": 2349, "ask": 2349.2}, GOLD, 1e4, r, "X").reason
    d = build_open_plan(_sig(sl=None), {"bid": 2349, "ask": 2349.2}, GOLD, 1e4,
                        merge_rules({"missing_sl": "default", "default_sl_pct": 0.5}), "X")
    assert d.ok and d.sl < d.entry
    # price below the stop: stale
    assert "stale" in build_open_plan(_sig(order_type="MARKET", entry=None), {"bid": 2339, "ask": 2339.2},
                                      GOLD, 1e4, r, "X").reason
    # tiny account: min lot risks $9.20 > 2% of $100 -> refused
    assert not build_open_plan(_sig(), {"bid": 2349, "ask": 2349.2}, GOLD, 100, r, "X").ok


def test_legs_to_close_nearest_first():
    legs = [{"ticket": 1, "volume": 0.05, "leg": 1}, {"ticket": 2, "volume": 0.03, "leg": 2},
            {"ticket": 3, "volume": 0.02, "leg": 3}]
    assert legs_to_close(legs, 0.5) == [(1, 0.05)]
    assert legs_to_close(legs, 1.0) == [(1, 0.05), (2, 0.03), (3, 0.02)]


# ── service end to end, fake MT5 ────────────────────────────────────────────

class FakeMT5:
    def __init__(self):
        self.bid, self.ask = 2348.8, 2349.0
        self.next = 1000
        self.positions: dict[int, SimpleNamespace] = {}
        self.orders: dict[int, SimpleNamespace] = {}
        self.calls: list[tuple] = []

    def install(self, mp):
        async def available_symbols():
            return ["XAUUSD", "US Tech 100"]

        async def symbol_spec(sym):
            return dict(GOLD)

        async def quote(sym):
            return {"bid": self.bid, "ask": self.ask}

        async def balance():
            return 10_000.0

        async def place(symbol, side, kind, volume, price, sl, tp, magic, comment, spec, expiry_hours=0):
            self.next += 1
            t = self.next
            ns = SimpleNamespace(ticket=t, symbol=symbol, volume=volume, price_open=price, sl=sl, tp=tp or 0,
                                 magic=magic, type=0 if side == "BUY" else 1)
            (self.positions if kind == "MARKET" else self.orders)[t] = ns
            self.calls.append(("place", kind, volume, tp, comment))
            return True, "10009 done", t

        async def get_position(t):
            return self.positions.get(t)

        async def modify_position(t, sl=None, tp=None, spec=None):
            self.calls.append(("modpos", t, sl, tp))
            p = self.positions[t]
            p.sl = sl if sl is not None else p.sl
            p.tp = tp if tp is not None else p.tp
            return True, "ok"

        async def modify_order(t, price=None, sl=None, tp=None, spec=None):
            self.calls.append(("modord", t, sl, tp))
            return True, "ok"

        async def cancel_order(t):
            self.calls.append(("cancel", t))
            self.orders.pop(t, None)
            return True, "ok"

        async def close_volume(t, volume=None):
            self.calls.append(("close", t, volume))
            p = self.positions[t]
            if volume is None or volume >= p.volume:
                self.positions.pop(t)
            else:
                p.volume -= volume
            return True, "ok"

        async def ticket_state(t):
            return "PENDING" if t in self.orders else "OPEN" if t in self.positions else "GONE"

        async def closed_by_tp(t):
            return True

        for name, fn in list(locals().items()):
            if callable(fn) and hasattr(mt5ops, name):
                mp.setattr(mt5ops, name, fn)


@pytest.fixture
def env(monkeypatch):
    tmp = tempfile.mkdtemp()
    engine = create_async_engine(f"sqlite+aiosqlite:///{Path(tmp).as_posix()}/ct.db")
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def setup():
        async with engine.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        async with Session() as s:
            s.add(CopyTradeSettings(user_id="u1", enabled=True, dry_run=False,
                                    rules={"split_weights": [50, 30, 20], "zone_entry": "market"}))
            ch = CopyTradeChannel(user_id="u1", chat_id=-1001, title="Gold VIP", enabled=True, rules={})
            s.add(ch)
            await s.commit()
            return ch.id

    ch_id = asyncio.run(setup())
    monkeypatch.setattr(svc_mod, "async_session", Session)
    fake = FakeMT5()
    fake.install(monkeypatch)
    service = svc_mod.CopyTradeService()
    return SimpleNamespace(service=service, fake=fake, ch=ch_id, Session=Session)


def _run(coro):
    return asyncio.run(coro)


def test_open_then_breakeven_by_reply(env):
    r = _run(env.service.handle_message("u1", env.ch, 10, "XAUUSD BUY NOW @ 2350-2347\nSL 2340\nTP1 2355\nTP2 2360\nTP3 2365"))
    assert r["status"] == "EXECUTED", r
    places = [c for c in env.fake.calls if c[0] == "place"]
    assert [c[3] for c in places] == [2355.0, 2360.0, 2365.0]
    assert places[0][2] >= places[1][2] >= places[2][2]        # 50/30/20 weights
    assert all(c[4].startswith("AE_CT") for c in places)
    r = _run(env.service.handle_message("u1", env.ch, 11, "move SL to entry", reply_to=10))
    assert r["status"] == "EXECUTED"
    mods = [c for c in env.fake.calls if c[0] == "modpos"]
    assert len(mods) == 3 and all(c[2] == pytest.approx(2349.0) for c in mods)


def test_follow_up_without_reply_uses_latest_signal_and_widen_rule(env):
    _run(env.service.handle_message("u1", env.ch, 20, "GOLD BUY now\nSL 2340\nTP 2360 / 2370"))
    r = _run(env.service.handle_message("u1", env.ch, 21, "new SL 2335"))      # wider
    assert r["status"] == "EXECUTED"
    assert sum(1 for c in env.fake.calls if c[0] == "modpos" and c[2] == 2335) == 2


def test_partial_and_full_close(env):
    _run(env.service.handle_message("u1", env.ch, 30, "XAUUSD BUY\nSL 2340\nTP1 2355\nTP2 2360\nTP3 2365"))
    _run(env.service.handle_message("u1", env.ch, 31, "close half", reply_to=30))
    closes = [c for c in env.fake.calls if c[0] == "close"]
    # 0.11 lots open (0.06/0.03/0.02): half = 0.055, taken from leg 1 (nearest TP) first
    assert len(closes) == 1 and closes[0][2] == pytest.approx(0.055)
    _run(env.service.handle_message("u1", env.ch, 32, "close all now", reply_to=30))
    assert not env.fake.positions


def test_pending_cancel(env):
    env.fake.bid, env.fake.ask = 2369.0, 2369.2
    r = _run(env.service.handle_message("u1", env.ch, 40, "GOLD SELL LIMIT 2375\nSL 2382\nTP 2365 / 2355"))
    assert r["status"] == "EXECUTED" and len(env.fake.orders) == 2
    r = _run(env.service.handle_message("u1", env.ch, 41, "cancel the sell limit", reply_to=40))
    assert r["status"] == "EXECUTED" and not env.fake.orders


def test_dry_run_places_nothing_and_simulates(env):
    async def to_dry():
        async with env.Session() as s:
            st = await s.get(CopyTradeSettings, "u1")
            st.dry_run = True
            await s.commit()
    _run(to_dry())
    r = _run(env.service.handle_message("u1", env.ch, 50, "XAUUSD BUY\nSL 2340\nTP1 2355\nTP2 2360"))
    assert r["status"] == "DRY_RUN"
    assert not any(c[0] == "place" for c in env.fake.calls)
    env.fake.bid, env.fake.ask = 2356.0, 2356.2                 # TP1 reached
    _run(env.service.monitor_once())

    async def legs():
        async with env.Session() as s:
            from sqlalchemy import select
            return (await s.execute(select(CopyTradeOrder).order_by(CopyTradeOrder.leg))).scalars().all()
    rows = _run(legs())
    assert rows[0].status == "CLOSED" and "paper TP" in rows[0].detail and rows[1].status == "PAPER"


def test_edit_updates_stop_and_duplicate_ignored(env):
    _run(env.service.handle_message("u1", env.ch, 60, "XAUUSD BUY\nSL 2340\nTP1 2355"))
    r = _run(env.service.handle_message("u1", env.ch, 60, "XAUUSD BUY\nSL 2340\nTP1 2355"))
    assert r["status"] == "IGNORED"
    r = _run(env.service.handle_message("u1", env.ch, 60, "XAUUSD BUY\nSL 2342\nTP1 2355", edited=True))
    assert r["status"] == "EXECUTED"
    assert any(c[0] == "modpos" and c[2] == 2342 for c in env.fake.calls)


def test_max_open_signals_and_chatter(env):
    r = _run(env.service.handle_message("u1", env.ch, 70, "Good morning traders! Big week ahead"))
    assert r["status"] == "IGNORED"
    r = _run(env.service.handle_message("u1", env.ch, 71, "move SL to entry"))
    assert r["status"] == "IGNORED" and "no open signal" in r["detail"]


def test_layered_signal_end_to_end(env):
    async def to_layer():
        async with env.Session() as s:
            st = await s.get(CopyTradeSettings, "u1")
            st.rules = {**st.rules, "zone_entry": "layer"}
            await s.commit()
    _run(to_layer())
    env.fake.bid, env.fake.ask = 4505.0, 4505.3
    r = _run(env.service.handle_message("u1", env.ch, 80, "Buy Gold 4487 - 4495\nStop Loss 4483\nTP1 4497\nTP2 4499\n"
                                        "TP3 4501\nTP4 Open (4504/4507/4512/4518)"))
    assert r["status"] == "EXECUTED" and len(env.fake.orders) == 7 and not env.fake.positions
    r = _run(env.service.handle_message("u1", env.ch, 81, "Cancelling buy limit orders 💡", reply_to=80))
    assert r["status"] == "EXECUTED" and not env.fake.orders


def test_defaults_cover_every_rule_key():
    assert set(merge_rules({"bogus": 1})) == set(DEFAULT_RULES)
    assert DEFAULT_RULES["magic"] > 1101           # outside the bot's magic range
