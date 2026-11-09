"""Breakeven / TP-ladder trailing and first-fill handling in the copier's monitor loop."""

import pytest
from sqlalchemy import select

from backend.copytrade.models import CopyTradeOrder, CopyTradeSettings
from tests.test_copytrade import _run, env  # noqa: F401  (env is a fixture)


def _set_rules(env, **kw):  # noqa: F811
    async def go():
        async with env.Session() as s:
            st = await s.get(CopyTradeSettings, "u1")
            st.rules = {**(st.rules or {}), **kw}
            await s.commit()
    _run(go())


def _fill(env, ticket, price):  # noqa: F811
    o = env.fake.orders.pop(ticket)
    o.price_open = price
    env.fake.positions[ticket] = o
    env.fake.history.add(ticket)


def _tp_out(env, ticket):  # noqa: F811
    env.fake.positions.pop(ticket, None) or env.fake.orders.pop(ticket, None)
    env.fake.history.add(ticket)


def _orders(env):  # noqa: F811
    async def go():
        async with env.Session() as s:
            return list((await s.execute(select(CopyTradeOrder).order_by(CopyTradeOrder.leg))).scalars())
    return _run(go())


def test_breakeven_after_tp1_when_fill_and_tp_happen_between_checks(env):  # noqa: F811
    # 9 Nov: leg 1 filled and hit TP1 inside one round; the 2 % offset put the stop past the price
    _set_rules(env, zone_entry="layer", be_after_tp=1, be_offset_pct=2, cancel_pending_after_tp=1)
    env.fake.bid, env.fake.ask = 2352.8, 2353.0
    r = _run(env.service.handle_message("u1", env.ch, 70,
                                        "Gold buy now 2350 - 2346\nSL: 2340\nTP: 2352\nTP: 2354\nTP: 2356\nTP: open"))
    assert r["status"] == "EXECUTED", r
    t1, t2, t3 = (o.ticket for o in _orders(env)[:3])
    _tp_out(env, t1)                      # filled at 2350 and closed at TP1 2352 between two checks
    _fill(env, t2, 2348.0)
    env.fake.bid, env.fake.ask = 2350.2, 2350.4
    _run(env.service.monitor_once())
    st = {o.leg: o.status for o in _orders(env)}
    assert st[1] == "CLOSED"              # was marked CANCELLED before, so nothing after it fired
    assert st[3] == "CANCELLED"
    # 2348 + 0.1 % = 2350.35 is above the bid: falls back to plain breakeven
    assert env.fake.positions[t2].sl == pytest.approx(2348.0)


def test_refused_stop_is_retried_next_round(env):  # noqa: F811
    _set_rules(env, be_after_tp=1)
    _run(env.service.handle_message("u1", env.ch, 71, "XAUUSD BUY NOW\nSL 2340\nTP1 2355\nTP2 2360"))
    legs = _orders(env)
    _tp_out(env, legs[0].ticket)
    env.fake.bid, env.fake.ask = 2348.5, 2348.7       # back under the 2349 entry: breakeven doesn't fit yet
    _run(env.service.monitor_once())
    assert env.fake.positions[legs[1].ticket].sl == 2340
    env.fake.bid, env.fake.ask = 2351.0, 2351.2
    _run(env.service.monitor_once())
    assert env.fake.positions[legs[1].ticket].sl == pytest.approx(2349.0)


def test_tp_ladder_trail(env):  # noqa: F811
    _set_rules(env, trail_ladder=True, split_weights=[], max_legs=4)
    _run(env.service.handle_message("u1", env.ch, 72,
                                    "XAUUSD BUY NOW\nSL 2340\nTP1 2355\nTP2 2360\nTP3 2365\nTP4 2370"))
    tk = [o.ticket for o in _orders(env)]
    assert len(tk) == 4
    _tp_out(env, tk[0])
    env.fake.bid, env.fake.ask = 2355.5, 2355.7
    _run(env.service.monitor_once())
    assert all(env.fake.positions[t].sl == pytest.approx(2349.0) for t in tk[1:])       # TP1 -> breakeven
    _tp_out(env, tk[1])
    env.fake.bid, env.fake.ask = 2360.5, 2360.7
    _run(env.service.monitor_once())
    assert all(env.fake.positions[t].sl == pytest.approx(2355.0) for t in tk[2:])       # TP2 -> TP1
    _tp_out(env, tk[2])
    env.fake.bid, env.fake.ask = 2365.5, 2365.7
    _run(env.service.monitor_once())
    assert env.fake.positions[tk[3]].sl == pytest.approx(2360.0)                        # TP3 -> TP2
    env.fake.bid, env.fake.ask = 2362.0, 2362.2                                         # pullback: never loosens
    _run(env.service.monitor_once())
    assert env.fake.positions[tk[3]].sl == pytest.approx(2360.0)


def test_ladder_follows_price_through_targets_without_legs(env):  # noqa: F811
    # one leg: the ladder still steps up as price passes the posted targets
    _set_rules(env, trail_ladder=True, max_legs=1)
    _run(env.service.handle_message("u1", env.ch, 73, "XAUUSD BUY NOW\nSL 2340\nTP1 2355\nTP2 2360\nTP3 2365"))
    t = _orders(env)[0].ticket
    env.fake.bid, env.fake.ask = 2356.0, 2356.2
    _run(env.service.monitor_once())
    assert env.fake.positions[t].sl == pytest.approx(2349.0)
    env.fake.positions[t].tp = 0          # pretend its own TP was removed so it keeps running
    env.fake.bid, env.fake.ask = 2361.0, 2361.2
    _run(env.service.monitor_once())
    assert env.fake.positions[t].sl == pytest.approx(2355.0)


def test_first_fill_takes_the_rest_of_the_zone_at_market(env):  # noqa: F811
    _set_rules(env, zone_entry="layer", on_first_fill="market")
    env.fake.bid, env.fake.ask = 2352.8, 2353.0
    _run(env.service.handle_message("u1", env.ch, 74, "Gold buy now 2350 - 2346\nSL: 2340\nTP: 2355\nTP: 2360\nTP: 2365"))
    legs = _orders(env)
    assert all(o.status == "PENDING" for o in legs)
    _fill(env, legs[0].ticket, 2350.0)
    env.fake.bid, env.fake.ask = 2350.6, 2350.8
    _run(env.service.monitor_once())
    legs = _orders(env)
    assert all(o.status == "OPEN" for o in legs)
    assert [o.kind for o in legs] == ["LIMIT", "MARKET", "MARKET"]
    assert not env.fake.orders


def test_dry_run_ladder(env):  # noqa: F811
    _set_rules(env, trail_ladder=True, max_legs=2)

    async def to_dry():
        async with env.Session() as s:
            st = await s.get(CopyTradeSettings, "u1")
            st.dry_run = True
            await s.commit()
    _run(to_dry())
    _run(env.service.handle_message("u1", env.ch, 75, "XAUUSD BUY NOW\nSL 2340\nTP1 2355\nTP2 2360\nTP3 2365"))
    env.fake.bid, env.fake.ask = 2355.5, 2355.7
    _run(env.service.monitor_once())
    legs = _orders(env)
    assert legs[0].status == "CLOSED" and legs[1].sl == pytest.approx(2349.0)


# ── groups, add-ons, default symbol ───────────────────────────────────────────
def test_sender_allowed_in_groups():
    from backend.copytrade.telegram_client import sender_allowed
    admins = {111}
    assert sender_allowed(True, -100, 999, None, None, {})                       # broadcast channel
    assert sender_allowed(False, -100, 111, "mo", admins, {"only_admins": True})  # group admin
    assert not sender_allowed(False, -100, 222, "member", admins, {"only_admins": True})
    assert sender_allowed(False, -100, -100, None, admins, {})                   # anonymous admin = the group
    assert sender_allowed(False, -100, 222, "Member", admins, {"allowed_senders": ["@member"]})
    assert not sender_allowed(False, -100, 111, "mo", admins, {"allowed_senders": ["someone"]})
    assert not sender_allowed(False, -100, 111, "mo", None, {"only_admins": True})   # admins unknown: nothing


def test_add_on_entry_joins_the_running_trade(env):  # noqa: F811
    _run(env.service.handle_message("u1", env.ch, 80, "Take Entry XAUUSD BUY 2350/ 2347.00\nStop Loss\n2340\nTP 2355\nTP 2360"))
    first = [o.ticket for o in _orders(env)]
    env.fake.bid, env.fake.ask = 2345.0, 2345.2
    r = _run(env.service.handle_message("u1", env.ch, 81, "ADD MORE BUY 2345.00\n\nUPDATE SL 2338.00"))
    assert r["status"] == "EXECUTED", r
    assert all(env.fake.positions[t].sl == pytest.approx(2338.0) for t in first if t in env.fake.positions)
    added = [o for o in _orders(env) if o.ticket not in first]
    assert added and all(o.symbol == "XAUUSD" and o.sl == pytest.approx(2338.0) for o in added)
    assert {o.tp for o in added} <= {2355.0, 2360.0}


def test_default_symbol(env):  # noqa: F811
    r = _run(env.service.handle_message("u1", env.ch, 82, "Take Entry SELL 2349.00\nStop Loss\n2356\nTP 2345\nTP 2341"))
    assert r["status"] == "SKIPPED"
    _set_rules(env, default_symbol="XAUUSD")
    r = _run(env.service.handle_message("u1", env.ch, 83, "Take Entry SELL 2349.00\nStop Loss\n2356\nTP 2345\nTP 2341"))
    assert r["status"] == "EXECUTED", r


def test_first_fill_adds_market_legs_and_keeps_the_limits(env):  # noqa: F811
    _set_rules(env, zone_entry="layer", on_first_fill="market_add", cancel_pending_after_tp=1)
    env.fake.bid, env.fake.ask = 2352.8, 2353.0
    _run(env.service.handle_message("u1", env.ch, 76, "Gold buy now 2350 - 2346\nSL: 2340\nTP: 2355\nTP: 2360\nTP: 2365"))
    legs = _orders(env)
    _fill(env, legs[0].ticket, 2350.0)
    env.fake.bid, env.fake.ask = 2350.6, 2350.8
    _run(env.service.monitor_once())
    rows = _orders(env)
    assert len(rows) == 5                                         # 3 legs + 2 market adds
    assert sorted((o.leg, o.kind, o.status) for o in rows) == [
        (1, "LIMIT", "OPEN"), (2, "LIMIT", "PENDING"), (2, "MARKET", "OPEN"), (3, "LIMIT", "PENDING"), (3, "MARKET", "OPEN")]
    assert len(env.fake.orders) == 2                              # the limits still wait
    _run(env.service.monitor_once())                              # no second round of adds
    assert len(_orders(env)) == 5
    # TP1 fills: the waiting limits go, the market legs ride to TP2 / TP3
    _tp_out(env, legs[0].ticket)
    env.fake.bid, env.fake.ask = 2355.2, 2355.4
    _run(env.service.monitor_once())
    st = sorted((o.leg, o.kind, o.status) for o in _orders(env))
    assert (2, "LIMIT", "CANCELLED") in st and (3, "LIMIT", "CANCELLED") in st
    assert (2, "MARKET", "OPEN") in st and (3, "MARKET", "OPEN") in st


def test_first_fill_add_skipped_once_price_is_at_tp1(env):  # noqa: F811
    _set_rules(env, zone_entry="layer", on_first_fill="market_add")
    env.fake.bid, env.fake.ask = 2352.8, 2353.0
    _run(env.service.handle_message("u1", env.ch, 77, "Gold buy now 2350 - 2346\nSL: 2340\nTP: 2355\nTP: 2360\nTP: 2365"))
    legs = _orders(env)
    _fill(env, legs[0].ticket, 2350.0)
    env.fake.bid, env.fake.ask = 2355.4, 2355.6                   # already at TP1 when we notice the fill
    _run(env.service.monitor_once())
    assert len(_orders(env)) == 3
