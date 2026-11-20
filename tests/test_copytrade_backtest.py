"""Copy-trading backtest: messages -> signals, rules -> settings, replay exit reasons, portfolio money."""

from datetime import datetime, timedelta, timezone

from backend.copytrade import backtest as bt
from backend.copytrade.replay import Sig, replay

T0 = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)


def _msg(i, minutes, text, reply_to=None, admin=True):
    return {"id": i, "date": (T0 + timedelta(minutes=minutes)).isoformat(), "text": text,
            "reply_to": reply_to, "from_admin": admin}


def _bars(path, start=None):
    """path: list of (open, high, low, close) per minute from T0."""
    t0 = int((start or T0).timestamp())
    return {"time": [t0 + 60 * k for k in range(len(path))], "open": [p[0] for p in path],
            "high": [p[1] for p in path], "low": [p[2] for p in path], "close": [p[3] for p in path]}


def test_messages_become_signals_with_follow_ups():
    msgs = [_msg(1, 0, "XAUUSD BUY NOW 4000\nSL 3990\nTP 4010\nTP 4020"),
            _msg(2, 5, "nice chart guys", admin=False),
            _msg(3, 30, "move SL to entry", reply_to=1)]
    sigs = bt.messages_to_signals(msgs)
    assert len(sigs) == 1 and sigs[0].side == "BUY" and sigs[0].sl == 3990
    assert [e[1] for e in sigs[0].events] == ["BREAKEVEN"]


def test_follow_ups_the_rules_ignore_are_dropped():
    msgs = [_msg(1, 0, "XAUUSD BUY NOW 4000\nSL 3990\nTP 4010"), _msg(3, 30, "move SL to entry", reply_to=1)]
    sigs = bt.messages_to_signals(msgs, follow={"BREAKEVEN": False})
    assert sigs[0].events == []


def test_member_messages_skipped_only_when_only_admins():
    msgs = [_msg(1, 0, "XAUUSD SELL NOW 4000\nSL 4010\nTP 3990", admin=False)]
    assert bt.messages_to_signals(msgs, only_admins=True) == []
    assert len(bt.messages_to_signals(msgs, only_admins=False)) == 1


def test_settings_follow_channel_rules_unless_overridden():
    rules = {"sl_fraction": 0.75, "split_weights": [20, 30, 50], "be_after_tp": 0, "follow_breakeven": True,
             "follow_move_sl": False, "max_open_signals": 3}
    cfg = bt.settings_from_rules(rules)
    assert cfg["sl_fraction"] == 0.75 and cfg["scheme"] == [20.0, 30.0, 50.0] and cfg["be_after_tp1"] is False
    assert cfg["follow"]["MOVE_SL"] is False and cfg["max_open"] == 3
    cfg = bt.settings_from_rules(rules, {"sl_fraction": 1.0, "scheme": "front", "be_after_tp1": True})
    assert cfg["sl_fraction"] == 1.0 and cfg["scheme"] == "front" and cfg["be_after_tp1"] is True


def test_replay_reports_how_it_ended():
    s = Sig(1, int(T0.timestamp()) - 60, "BUY", "MARKET", None, 3990.0, [4010.0, 4020.0])
    # fills ~4000, TP1 at minute 2 -> stop to BE, then back to entry
    bars = _bars([(4000, 4001, 3999, 4000), (4000, 4005, 3999, 4004), (4004, 4011, 4003, 4009),
                  (4009, 4009, 3998, 3999)])
    r = replay(s, bars, be_after_tp1=True)
    assert r.status == "FILLED" and r.tp1_hit and r.exit_reason == "breakeven stop"
    r2 = replay(s, _bars([(4000, 4001, 3999, 4000), (4000, 4000, 3985, 3986)]))
    assert r2.exit_reason == "SL" and round(r2.r_split, 2) == -1.0


def test_trade_row_compares_signal_with_market():
    s = Sig(7, int(T0.timestamp()) - 60, "SELL", "LIMIT", 4005.0, 4015.0, [3995.0])
    bars = _bars([(4000, 4006, 3999, 4004), (4004, 4004, 3994, 3995)])
    r = replay(s, bars)
    row = bt.trade_row("Willy", s, r, bars)
    assert row["market_at_signal"] == 4000 and row["fill_price"] == 4005.0
    assert row["slippage"] == 0 and row["exit_reason"] == "TP1" and row["r"] == 1.0


def _t(src, a, b, r):
    return {"source": src, "_entry_t": a, "_exit_t": b, "r": r, "status": "FILLED"}


def test_portfolio_compounds_and_measures_drawdown():
    trades = [_t("A", 0, 10, -1.0), _t("A", 20, 30, 2.0)]
    pf = bt.simulate_portfolio(trades, 10000, 1.0, compound=True)
    assert pf["end_balance"] == round(10000 - 100 + 9900 * 0.01 * 2, 2)
    assert pf["max_dd_usd"] == 100.0 and pf["max_dd_pct"] == 1.0
    fixed = bt.simulate_portfolio([dict(x) for x in trades], 10000, 1.0, compound=False)
    assert fixed["end_balance"] == 10100.0


def test_portfolio_respects_max_open_per_channel():
    trades = [_t("A", 0, 100, 1.0), _t("A", 10, 50, 1.0), _t("B", 10, 50, 1.0)]
    pf = bt.simulate_portfolio(trades, 10000, 1.0, compound=False, max_open={"A": 1})
    assert pf["trades"] == 2 and pf["skipped_max_open"] == {"A": 1}
    assert trades[1].get("portfolio_skipped") is True
    assert pf["max_concurrent_trades"] == 2


def test_portfolio_risk_is_fixed_at_entry():
    # B opens while A is open; A's loss must not change B's already-fixed risk
    trades = [_t("A", 0, 20, -1.0), _t("B", 10, 30, 1.0)]
    pf = bt.simulate_portfolio(trades, 10000, 1.0, compound=True)
    assert trades[1]["portfolio_risk_usd"] == 100.0
    assert pf["end_balance"] == 10000.0


def test_run_end_to_end_with_fake_telegram_and_prices(monkeypatch, tmp_path):
    """run(): history -> signals -> replay -> live comparison -> portfolio, against a real (temp) DB."""
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import backend.data.database as dbmod
    from backend.copytrade import mt5ops
    from backend.copytrade.models import CopyTradeChannel, CopyTradeOrder, CopyTradeSettings, CopyTradeSignal
    from backend.data.models import Base

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path.as_posix()}/bt.db")
    Session = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(days=2)

    async def setup():
        async with engine.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        async with Session() as s:
            s.add(CopyTradeSettings(user_id="u1", enabled=True, dry_run=False, rules={}))
            ch = CopyTradeChannel(user_id="u1", chat_id=-100, title="Willy", enabled=True,
                                  rules={"sl_fraction": 1.0, "max_open_signals": 2})
            s.add(ch)
            await s.flush()
            sig = CopyTradeSignal(user_id="u1", channel_id=ch.id, chat_id=-100, msg_id=1, kind="OPEN",
                                  status="CLOSED", parsed={}, actions=[], text="")
            s.add(sig)
            await s.flush()
            s.add(CopyTradeOrder(user_id="u1", signal_id=sig.id, channel_id=ch.id, leg=1, ticket=55,
                                 symbol="XAUUSD", side="BUY", status="CLOSED", entry=4000.0))
            await s.commit()
            return ch.id

    cid = asyncio.run(setup())
    monkeypatch.setattr(dbmod, "async_session", Session)

    msgs = [{"id": 1, "date": now.isoformat(), "text": "XAUUSD BUY NOW 4000\nSL 3990\nTP 4010", "from_admin": True},
            {"id": 2, "date": (now + timedelta(minutes=10)).isoformat(), "text": "XAUUSD SELL NOW 4012\nSL 4022\nTP 4002",
             "from_admin": True}]

    async def fake_history(user_id, chat_id, days, refresh=False):
        return {"messages": msgs, "first_message": "2025-01-01T00:00:00+00:00", "capped": False}

    t0 = int(now.timestamp())
    path = [(4000, 4001, 3999, 4000)] * 3 + [(4000, 4011, 3999, 4010)] + [(4010, 4012, 4009, 4012)] * 8 \
        + [(4012, 4013, 4001, 4002)] + [(4002, 4003, 4001, 4002)] * 5
    bars = {"time": [t0 + 60 * k for k in range(len(path))], "open": [p[0] for p in path],
            "high": [p[1] for p in path], "low": [p[2] for p in path], "close": [p[3] for p in path]}

    async def fake_m1(symbol, start):
        return bars

    async def fake_spread(symbol):
        return 0.0

    async def fake_syms():
        return ["XAUUSD"]

    async def fake_deal(ticket):
        return {"net": 42.0, "price": 4010.0, "reason": "take profit"}

    monkeypatch.setattr(bt, "channel_history", fake_history)
    monkeypatch.setattr(bt, "load_m1", fake_m1)
    monkeypatch.setattr(bt, "spread_price", fake_spread)
    monkeypatch.setattr(mt5ops, "available_symbols", fake_syms)
    monkeypatch.setattr(mt5ops, "closed_deal", fake_deal)

    res = asyncio.run(bt.run("u1", [cid], days=7, balance=10000, risk_pct=1.0, compound=False))
    ch = res["channels"][0]
    assert ch["title"] == "Willy" and ch["signals"] == 2 and ch["filled"] == 2
    assert ch["net_r_split"] == 1.67            # +1R, then +0.67R: the market sell filled 2 below its written 4012
    pf = res["portfolio"]
    assert pf["trades"] == 2 and pf["end_balance"] == 10166.67
    assert res["trades"][1]["slippage"] == 2.0 and res["trades"][1]["slippage_r"] == round(2 / 12, 3)
    first = res["trades"][0]
    assert first["exit_reason"] == "TP1" and first["live"]["net"] == 42.0
    assert "_entry_t" not in first
