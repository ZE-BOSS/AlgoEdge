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
    _run(env.service.monitor_once())      # "gone" has to be seen twice before it counts
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
    _run(env.service.monitor_once())                              # gone twice -> TP1 counts
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


def test_runner_leg_and_stack_at_tp1(env):  # noqa: F811
    _set_rules(env, trail_ladder=True, runner_leg=True, stack_at_tp1=1.0, max_legs=2, split_weights=[])
    _run(env.service.handle_message("u1", env.ch, 78, "XAUUSD BUY NOW\nSL 2340\nTP1 2355\nTP2 2360\nTP3 2365\nTP4 2370"))
    legs = _orders(env)
    assert [o.tp for o in legs] == [2355.0, None]                     # the last leg runs
    t1, t2 = legs[0].ticket, legs[1].ticket
    _tp_out(env, t1)
    env.fake.bid, env.fake.ask = 2355.5, 2355.7
    _run(env.service.monitor_once())
    rows = _orders(env)
    stack = [o for o in rows if "stack at TP1" in (o.detail or "")]
    assert len(stack) == 1 and stack[0].sl == pytest.approx(2349.0) and stack[0].tp is None
    assert stack[0].volume == pytest.approx(env.fake.positions[t2].volume)
    _run(env.service.monitor_once())                                   # only once
    assert len([o for o in _orders(env) if "stack at TP1" in (o.detail or "")]) == 1
    env.fake.bid, env.fake.ask = 2366.0, 2366.2                        # TP3 reached: everything trails to TP2
    _run(env.service.monitor_once())
    assert env.fake.positions[t2].sl == pytest.approx(2360.0)
    assert env.fake.positions[stack[0].ticket].sl == pytest.approx(2360.0)


def test_mt5_disconnect_does_not_close_live_legs(env):  # noqa: F811
    """9 Nov 21:24: the terminal lost its login for 20 s; every leg of #51331 was
    marked CLOSED while it was still live, so nothing managed it afterwards."""
    _set_rules(env, zone_entry="layer", be_after_tp=1, cancel_pending_after_tp=1)
    env.fake.bid, env.fake.ask = 2349.8, 2350.0
    _run(env.service.handle_message("u1", env.ch, 80, "Gold buy now 2350 - 2346\nSL: 2340\nTP: 2352\nTP: 2354\nTP: 2356"))
    before = {o.id: o.status for o in _orders(env)}
    env.fake.down = True
    for _ in range(3):
        _run(env.service.monitor_once())
    assert {o.id: o.status for o in _orders(env)} == before
    env.fake.down = False
    _run(env.service.monitor_once())
    assert all(o.status in ("OPEN", "PENDING") for o in _orders(env))


def test_gone_must_be_seen_twice(env):  # noqa: F811
    _set_rules(env)
    _run(env.service.handle_message("u1", env.ch, 81, "XAUUSD BUY NOW\nSL 2340\nTP1 2355"))
    t1 = _orders(env)[0].ticket
    _tp_out(env, t1)
    _run(env.service.monitor_once())
    assert _orders(env)[0].status == "OPEN"
    _run(env.service.monitor_once())
    assert _orders(env)[0].status == "CLOSED"


def test_market_add_when_first_leg_went_in_at_market(env):  # noqa: F811
    """#51331: price was inside the zone, leg 1 was a market order at once - the
    old first-fill test waited for a LIMIT to turn OPEN and never fired."""
    _set_rules(env, zone_entry="layer", on_first_fill="market_add", cancel_pending_after_tp=1)
    env.fake.bid, env.fake.ask = 2349.0, 2349.2                     # inside 2350 - 2346
    _run(env.service.handle_message("u1", env.ch, 82, "Gold buy now 2350 - 2346\nSL: 2340\nTP: 2355\nTP: 2360\nTP: 2365"))
    legs = _orders(env)
    assert legs[0].kind == "MARKET" and legs[0].status == "OPEN"
    pending = [o for o in legs if o.status == "PENDING"]
    assert pending
    _run(env.service.monitor_once())
    rows = _orders(env)
    adds = [o for o in rows if "added at market" in (o.detail or "")]
    assert len(adds) == len(pending)
    assert all(o.status == "PENDING" for o in rows if o.kind == "LIMIT")   # limits still wait
    _run(env.service.monitor_once())
    assert len(_orders(env)) == len(rows)                                   # once only


def test_first_fill_market_mode_converts_limits_when_leg1_was_market(env):  # noqa: F811
    _set_rules(env, zone_entry="layer", on_first_fill="market")
    env.fake.bid, env.fake.ask = 2349.0, 2349.2
    _run(env.service.handle_message("u1", env.ch, 83, "Gold buy now 2350 - 2346\nSL: 2340\nTP: 2355\nTP: 2360\nTP: 2365"))
    _run(env.service.monitor_once())
    rows = _orders(env)
    assert not any(o.status == "PENDING" for o in rows)
    assert not env.fake.orders                                              # no limits left behind


def test_limit_already_at_the_price_is_taken_at_market(env):  # noqa: F811
    """10 Nov GBPJPY #4826: leg 2's buy limit sat on the ask, MT5 said "10015
    Invalid price" and the leg was lost - no position, no market add for it."""
    _set_rules(env, zone_entry="layer")
    env.fake.bid, env.fake.ask = 2348.0, 2348.2                      # leg 2's limit is 2348
    _run(env.service.handle_message("u1", env.ch, 84, "Gold buy now 2350 - 2346\nSL: 2340\nTP: 2355\nTP: 2360\nTP: 2365"))
    legs = {o.leg: o for o in _orders(env)}
    assert legs[2].kind == "MARKET" and legs[2].status == "OPEN" and "taken at market" in legs[2].detail
    assert legs[3].kind == "LIMIT" and legs[3].status == "PENDING"            # deeper one still waits


def test_refused_limit_retried_at_market_when_price_reached_it(env):  # noqa: F811
    _set_rules(env, zone_entry="layer")
    env.fake.bid, env.fake.ask = 2349.8, 2350.0
    env.fake.refuse_pending_then_ask = 2347.9                         # price fell through 2348 as we sent it
    _run(env.service.handle_message("u1", env.ch, 85, "Gold buy now 2350 - 2346\nSL: 2340\nTP: 2355\nTP: 2360\nTP: 2365"))
    legs = {o.leg: o for o in _orders(env)}
    assert legs[2].kind == "MARKET" and legs[2].status == "OPEN" and "refused" in legs[2].detail
    assert not any(o.status == "FAILED" for o in legs.values())


def test_pending_at_market_rule():
    from backend.copytrade.rules import pending_at_market
    spec = {"point": 0.001, "stops_level": 0}
    q = {"bid": 212.48, "ask": 212.496}
    assert pending_at_market("LIMIT", "BUY", 212.495, q, spec)          # the #4826 leg
    assert not pending_at_market("LIMIT", "BUY", 212.44, q, spec)       # a real limit below
    assert pending_at_market("LIMIT", "SELL", 212.49, q, spec)
    assert not pending_at_market("LIMIT", "SELL", 212.60, q, spec)
    assert pending_at_market("STOP", "BUY", 212.50, q, spec)
    assert not pending_at_market("STOP", "BUY", 212.60, q, spec)
    assert not pending_at_market("MARKET", "BUY", 212.495, q, spec)


def test_channel_can_copy_gold_only(env):  # noqa: F811
    """'Only these symbols' set on one channel (its override), not globally."""
    from sqlalchemy import select

    from backend.copytrade.models import CopyTradeChannel, CopyTradeSignal

    async def go():
        async with env.Session() as s:
            ch = (await s.execute(select(CopyTradeChannel))).scalars().first()
            ch.rules = {"allowed_symbols": ["XAUUSD"]}
            await s.commit()
    _run(go())
    _run(env.service.handle_message("u1", env.ch, 90, "VOLATILITY 75 INDEX \n\nBUY NOW \n\nTP. 54800\n\nSL. 53400"))
    _run(env.service.handle_message("u1", env.ch, 91, "XAUUSD BUY NOW\nSL 2340\nTP1 2355"))

    async def sigs():
        async with env.Session() as s:
            return {r.msg_id: (r.status, r.detail) for r in (await s.execute(select(CopyTradeSignal))).scalars()}
    got = _run(sigs())
    assert got[90][0] == "SKIPPED" and "allowed symbols" in got[90][1]
    assert got[91][0] != "SKIPPED"


def test_trades_from_a_previous_mt5_account_free_their_slots(env, monkeypatch):  # noqa: F811
    """12 Nov: live switched from Deriv to Exness. The Deriv trades exist on no
    account the terminal can see, stayed 'open' for ever, filled the channel's
    max_open_signals, and every new signal on that channel was skipped."""
    from datetime import timedelta

    from backend.copytrade import service as svc

    _set_rules(env, max_open_signals=1)
    _run(env.service.handle_message("u1", env.ch, 95, "XAUUSD BUY NOW\nSL 2340\nTP1 2355"))
    tickets = [o.ticket for o in _orders(env)]
    for tk in tickets:                                      # the login moved to another broker
        env.fake.positions.pop(tk, None)
        env.fake.orders.pop(tk, None)
        env.fake.missing.add(tk)
    _run(env.service.monitor_once())
    assert all(o.status in ("OPEN", "PENDING") for o in _orders(env))          # not before the grace period
    _run(env.service.handle_message("u1", env.ch, 96, "XAUUSD SELL NOW\nSL 2360\nTP1 2345"))
    assert len(_orders(env)) == len(tickets)                                  # still blocked: slot taken

    real_now = svc._now
    monkeypatch.setattr(svc, "_now", lambda: real_now() + timedelta(minutes=20))
    _run(env.service.monitor_once())
    assert all(o.status in ("CLOSED", "CANCELLED") and "not on this MT5 account" in o.detail
               for o in _orders(env) if o.ticket in tickets)
    _run(env.service.handle_message("u1", env.ch, 97, "XAUUSD SELL NOW\nSL 2360\nTP1 2345"))
    assert len(_orders(env)) > len(tickets)                                   # the channel trades again


def test_a_disconnect_is_not_mistaken_for_another_account(env, monkeypatch):  # noqa: F811
    from datetime import timedelta

    from backend.copytrade import service as svc

    _set_rules(env)
    _run(env.service.handle_message("u1", env.ch, 98, "XAUUSD BUY NOW\nSL 2340\nTP1 2355"))
    env.fake.down = True
    real_now = svc._now
    monkeypatch.setattr(svc, "_now", lambda: real_now() + timedelta(hours=3))
    _run(env.service.monitor_once())
    _run(env.service.monitor_once())
    assert all(o.status in ("OPEN", "PENDING") for o in _orders(env))
