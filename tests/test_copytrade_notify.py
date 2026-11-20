"""Copy-trade Telegram alerts and the end-of-day report."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace as NS

from backend.copytrade import notify


def _sig(**kw):
    base = dict(kind="OPEN", status="EXECUTED", detail="3 legs placed", edited=False,
                parsed={"side": "SELL", "symbol": "XAUUSD", "sl": 4120.0}, text="SELL GOLD 4105-4110 SL 4120",
                actions=["leg 1 ...", "risk $98.40", "note: sized for the worst case"])
    base.update(kw)
    return NS(**base)


def _o(i, leg, status="OPEN", kind="LIMIT", sl=4120.0, tp=4095.0, entry=4108.0, vol=0.05, ticket=None, detail=""):
    return NS(id=i, leg=leg, status=status, kind=kind, sl=sl, tp=tp, entry=entry, volume=vol,
              ticket=ticket or 1000 + i, detail=detail, symbol="XAUUSD")


def test_placed_message_names_channel_legs_and_risk():
    m = notify.signal_message("Willy Gold Usd", _sig(), [_o(1, 1, "OPEN", "MARKET"), _o(2, 2, "PENDING", tp=None)])
    assert "Copy trade placed" in m and "Willy Gold Usd" in m
    assert "MARKET 0.05 lots" in m and "runner (no target)" in m
    assert "Risk if stopped: $98.40" in m
    assert "worst case" in m


def test_skipped_message_gives_reason_and_quote():
    m = notify.signal_message("VIP", _sig(status="SKIPPED", detail="3 signals already open on this channel (max 3)"), [])
    assert "Signal skipped" in m and "max 3" in m and "SELL GOLD" in m


def test_chatter_and_duplicates_are_silent():
    assert notify.signal_message("X", _sig(kind="NONE", status="IGNORED", detail="not a signal"), []) is None
    assert notify.signal_message("X", _sig(kind="INFO", status="IGNORED", detail="already handled"), []) is None


def test_follow_up_not_applied_is_reported():
    m = notify.signal_message("X", _sig(kind="MOVE_SL", status="IGNORED", detail="move is 5% from the entry - ignored"), [])
    assert "Stop moved not applied" in m and "5%" in m


def test_markdown_in_channel_names_is_escaped():
    m = notify.signal_message("Gold_VIP *pro*", _sig(status="SKIPPED", detail="x"), [])
    assert "Gold\\_VIP \\*pro\\*" in m


def test_order_events_fill_close_and_breakeven():
    rows_before = [_o(1, 1, "PENDING"), _o(2, 2, "OPEN"), _o(3, 3, "OPEN")]
    snap = notify.snapshot(rows_before)
    after = [_o(1, 1, "OPEN"), _o(2, 2, "CLOSED"), _o(3, 3, "OPEN", sl=4108.0)]
    lines = notify.order_events(snap, after, {2: {"net": 31.5, "price": 4095.0, "reason": "take profit"}})
    text = "\n".join(lines)
    assert "Leg 1 filled" in text
    assert "Leg 2 closed - take profit @ 4095.00, P&L +$31.50" in text
    assert "Leg 3 stop to breakeven: 4120.00 -> 4108.00" in text


def test_order_events_cancel_and_added_leg():
    snap = notify.snapshot([_o(1, 1, "PENDING")])
    after = [_o(1, 1, "CANCELLED", detail="placed | expired"), _o(9, 1, "OPEN", "MARKET")]
    text = "\n".join(notify.order_events(snap, after, {}))
    assert "Leg 1 order removed (expired)" in text and "Leg 1 added: MARKET" in text


def test_round_message_total_and_silence():
    root = NS(parsed={"side": "BUY", "symbol": "XAUUSD"})
    assert notify.round_message("C", root, [], None) is None
    m = notify.round_message("C", root, ["🛑 Leg 1 closed - stop loss, P&L -$40.00"], -40.0)
    assert "Signal finished: -$40.00" in m


def test_daily_report_groups_by_strategy_and_channel():
    text = notify.build_daily_report(
        "Fri 20 Nov 2026",
        [{"strategy": "SIGMA", "symbol": "NAS100", "pnl": 120.0}, {"strategy": "SIGMA", "symbol": "NAS100", "pnl": -50.0}],
        [{"channel": "Willy", "signal_id": 1, "pnl": 30.0}, {"channel": "Willy", "signal_id": 1, "pnl": 20.0},
         {"channel": "VIP", "signal_id": 2, "pnl": -60.0}],
        [{"channel": "Willy", "status": "CLOSED"}, {"channel": "VIP", "status": "SKIPPED", "detail": "low confidence 0.3"}],
        {"balance": 10000.0, "equity": 10010.0, "positions": 1},
    )
    assert "SIGMA NAS100: 2 trades, 1W/1L, +$70.00" in text
    assert "Willy: 1 trades, 1W/0L, +$50.00" in text       # legs summed per signal
    assert "VIP: 1 trades, 0W/1L, -$60.00" in text
    assert "1 skipped/failed (low confidence 0.3 x1)" in text
    assert "Day total: +$60.00" in text and "Balance 10,000.00" in text


def test_next_report_time_is_21_lagos():
    at = notify.next_report_at(datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc))
    assert at == datetime(2026, 11, 20, 20, 0, tzinfo=timezone.utc)      # 21:00 WAT = 20:00 UTC
    at = notify.next_report_at(datetime(2026, 11, 20, 20, 30, tzinfo=timezone.utc))
    assert at.day == 21


def test_queue_spaces_messages_and_retries_429():
    sent, calls = [], {"n": 0}

    async def fake_send(text):
        calls["n"] += 1
        if text == "b" and calls["n"] == 2:
            return {"ok": False, "results": [{"status": 429}]}
        sent.append((text, asyncio.get_running_loop().time()))
        return {"ok": True, "results": [{"status": 200}]}

    async def run():
        q = notify.TelegramQueue(send=fake_send, gap=0.05)
        notify_sleep = asyncio.sleep

        async def fast_sleep(s):          # the 5 s back-off after a 429, shortened for the test
            await notify_sleep(min(s, 0.05))
        notify.asyncio.sleep, orig = fast_sleep, asyncio.sleep
        try:
            for t in ("a", "b", "c"):
                q.post(t)
            await notify_sleep(0.5)
        finally:
            notify.asyncio.sleep = orig
        return q

    q = asyncio.run(run())
    assert [t for t, _ in sent] == ["a", "b", "c"]
    assert q.sent == 3 and q.failed == 0
    gaps = [b - a for (_, a), (_, b) in zip(sent, sent[1:])]
    assert all(g >= 0.04 for g in gaps)


# ── edited signals ──────────────────────────────────────────────────────────
def test_edit_that_updated_the_trade():
    sig = _sig(edited=True, status="EXECUTED", detail="edited SL 2335",
               actions=["edited SL 2335", "leg 1: SL 2340.0 -> 2335 (moved) ok",
                        "stop is further away than before: the $ risk of these lots went up"],
               text="XAUUSD BUY SL 2335 TP1 2355", parsed={"side": "BUY", "symbol": "XAUUSD"})
    m = notify.signal_message("Willy", sig, [])
    assert "Signal edited - trade updated" in m and "Willy" in m
    assert "SL 2340.0 -> 2335" in m and "risk" in m and "Now reads" in m
    assert "Copy trade placed" not in m


def test_edit_with_a_failed_change_is_flagged():
    sig = _sig(edited=True, status="EXECUTED", actions=["leg 2: entry 2340 -> 2338 FAILED 10015 Invalid price"])
    assert "partly FAILED" in notify.signal_message("X", sig, [])


def test_edit_not_applied_says_why():
    sig = _sig(edited=True, status="IGNORED", actions=[],
               detail="edit of a skipped signal, too late to take it")
    m = notify.signal_message("X", sig, [])
    assert "not applied" in m and "too late" in m


def test_cosmetic_edits_stay_silent():
    for d in ("edit changed nothing that is traded", "edit does not change a signal",
              "edit of a signal that is not open"):
        assert notify.signal_message("X", _sig(edited=True, status="IGNORED", actions=[], detail=d), []) is None


def test_trade_replaced_after_edit():
    sig = _sig(edited=False, status="EXECUTED",
               detail="replaced (SELL XAUUSD -> BUY XAUUSD): 1 legs placed",
               parsed={"side": "BUY", "symbol": "XAUUSD", "sl": 2340.0},
               actions=["edit changed the trade (SELL XAUUSD -> BUY XAUUSD): closing the old one, taking the corrected signal",
                        "leg 1: close all ok", "risk $98.00"])
    m = notify.signal_message("X", sig, [_o(1, 1, "OPEN", "MARKET")])
    assert "Trade replaced - signal edited" in m
    assert "SELL XAUUSD -> BUY XAUUSD" in m and "leg 1: close all ok" in m
