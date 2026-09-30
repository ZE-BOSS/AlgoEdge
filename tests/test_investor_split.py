"""
A published trade's result divided between investors, and capital against
profit. See backend/investor/split.py.

  * shares follow units held at the end of the day before the close, and add
    up to the published result to the cent, for a gain and for a loss
  * an investor sees only their own share, never the fund-wide dollar figure
  * the admin sees every line
  * capital vs profit: profit on top, or a loss eating into capital
  * month-by-month results, settings, and a trade alert that respects them
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from decimal import Decimal

from backend.data.models import Trade
from backend.investor import split as splitmod
from backend.notify import outbox
from test_investor_portal import App

D = Decimal


def run(coro):
    return asyncio.run(coro)


def test_allocation_adds_up_to_the_cent_for_gains_and_losses():
    w = {"a": D("1"), "b": D("1"), "c": D("1")}
    gain = splitmod.allocate(D("100.00"), w)
    assert sum(gain.values()) == D("100.00") and sorted(gain.values()) == [D("33.33"), D("33.33"), D("33.34")]
    loss = splitmod.allocate(D("-100.00"), w)
    assert sum(loss.values()) == D("-100.00") and all(v < 0 for v in loss.values())
    odd = splitmod.allocate(D("0.05"), {"a": D("3"), "b": D("1")})
    assert odd == {"a": D("0.04"), "b": D("0.01")}
    assert splitmod.allocate(D("10"), {}) == {}


async def _publish(a, tid, pnl, closed: datetime):
    async with a.Session() as s:
        s.add(Trade(id=tid, user_id="op", strategy_id="SECRET_v9", symbol="XAUUSD", direction="BUY",
                    status="CLOSED", pnl=pnl, balance_before=10000.0,
                    entry_time=closed - timedelta(hours=2), exit_time=closed))
        await s.commit()
    r = await a.admin("POST", f"/disclosures/{tid}/publish", {})
    assert r.status_code == 200, r.text
    # the trade alert is delivered after commit on its own session; the test
    # database is ONE shared in-memory connection, so let it finish first
    await outbox.drain()


def test_each_investor_sees_their_own_share_and_the_admin_sees_all(monkeypatch):
    monkeypatch.setenv("EMAIL_MODE", "log")
    monkeypatch.setenv("PUSH_MODE", "log")

    async def go():
        async with App() as a:
            # Ada 1,000 and Bea 3,000 in before the trade: 25% / 75%
            _, ada = await a.onboard("Ada", deposit=D("1000"), on=date(2026, 9, 1))
            _, bea = await a.onboard("Bea", deposit=D("3000"), on=date(2026, 9, 1))
            # Cy arrives ON the close day: not in the fund while the trade ran
            _, cy = await a.onboard("Cy", deposit=D("4000"), on=date(2026, 9, 20))
            await _publish(a, 500, 200.0, datetime(2026, 9, 20, 15))
            await _publish(a, 501, -80.0, datetime(2026, 9, 21, 15))

            ada_t = (await a.inv("GET", "/trades", ada)).json()
            bea_t = (await a.inv("GET", "/trades", bea)).json()
            cy_t = (await a.inv("GET", "/trades", cy)).json()
            by = lambda rows: {r["id"]: r for r in rows["trades"]}
            assert by(ada_t)["500"]["your_amount"] == "50.00" and by(bea_t)["500"]["your_amount"] == "150.00"
            assert by(cy_t)["500"]["your_amount"] is None, "not invested when it closed"
            # the loss on the 21st is split three ways by units (Cy now 50%)
            losses = [by(t)["501"]["your_amount"] for t in (ada_t, bea_t, cy_t)]
            assert sum(D(x) for x in losses) == D("-80.00") and losses == ["-10.00", "-30.00", "-40.00"]
            assert ada_t["summary"]["your_total"] == "40.00" and ada_t["summary"]["wins"] == 1
            for t in (ada_t, bea_t, cy_t):
                assert all("result_amount" not in r for r in t["trades"])

            split = (await a.admin("GET", "/disclosures/501/split")).json()
            assert split["result_amount"] == "-80.00" and split["allocated"] == "-80.00"
            assert [ln["amount"] for ln in split["lines"]] == ["-40.00", "-30.00", "-10.00"]
            assert (await a.admin("GET", "/disclosures/999/split")).status_code == 404
    run(go())


def test_capital_versus_profit_says_when_a_loss_is_eating_into_capital(monkeypatch):
    monkeypatch.setenv("EMAIL_MODE", "log")

    async def go():
        async with App() as a:
            _, ada = await a.onboard("Ada", deposit=D("1000"))
            await a.admin("POST", "/nav/snapshot", {"pool_equity": "1200"})
            up = (await a.inv("GET", "/me", ada)).json()["capital"]
            assert up["state"] == "profit" and up["profit"] == "200.00" and up["capital_eroded"] == "0.00"
            assert up["profit_pct_of_capital"] == "20.00"
            await a.admin("POST", "/nav/snapshot", {"pool_equity": "900", "reason": "re-price",
                                                   "on": date.today().isoformat()})
            down = (await a.inv("GET", "/me", ada)).json()["capital"]
            assert down["state"] == "capital_loss" and down["profit"] == "-100.00"
            assert down["capital_eroded"] == "100.00" and down["capital_intact"] == "900.00"
            assert down["profit_pct_of_capital"] == "-10.00"
            nav = (await a.inv("GET", "/nav", ada)).json()
            assert nav[-1]["net_invested"] == "1000.00" and nav[-1]["value"] == "900.00"
    run(go())


def test_performance_is_month_by_month_from_the_first_deposit(monkeypatch):
    monkeypatch.setenv("EMAIL_MODE", "log")

    async def go():
        async with App() as a:
            _, ada = await a.onboard("Ada", deposit=D("1000"), on=date(2026, 7, 10))
            await a.admin("POST", "/nav/snapshot", {"pool_equity": "1100", "on": "2026-07-31"})
            await a.admin("POST", "/nav/snapshot", {"pool_equity": "1045", "on": "2026-08-31"})
            perf = (await a.inv("GET", "/performance", ada)).json()
            months = {m["month"]: m for m in perf["months"]}
            assert months["2026-07"]["profit"] == "100.00" and months["2026-07"]["money_in_out"] == "1000.00"
            assert months["2026-08"]["profit"] == "-55.00" and months["2026-08"]["fund_return_pct"] == "-5.00"
            assert perf["stats"]["best_month"]["month"] == "2026-07"
            assert perf["stats"]["largest_fall_pct"] == "-5.00"
            empty = await a.onboard("Bo")
            assert (await a.inv("GET", "/performance", empty[1])).json() == {"months": [], "stats": None}
    run(go())


def test_settings_are_validated_and_switch_off_the_trade_alert(monkeypatch):
    monkeypatch.setenv("EMAIL_MODE", "log")
    monkeypatch.setenv("PUSH_MODE", "log")
    sent = []

    async def fake_push(investor_id, title, body, data=None):
        sent.append((investor_id, title))
        return 1

    monkeypatch.setattr(outbox, "push", fake_push)

    async def go():
        async with App() as a:
            ada_id, ada = await a.onboard("Ada", deposit=D("1000"), on=date(2026, 9, 1))
            bea_id, bea = await a.onboard("Bea", deposit=D("1000"), on=date(2026, 9, 1))
            r = await a.inv("PUT", "/preferences", bea, {"push": {"trades": False}, "chart_range": "3M"})
            assert r.status_code == 200 and r.json()["push"]["trades"] is False and r.json()["chart_range"] == "3M"
            assert (await a.inv("PUT", "/preferences", bea, {"chart_range": "5Y"})).status_code == 400
            assert (await a.inv("PUT", "/preferences", bea, {"push": {"nope": True}})).status_code == 400
            assert (await a.inv("GET", "/me", bea)).json()["preferences"]["chart_range"] == "3M"

            await _publish(a, 600, 50.0, datetime(2026, 9, 10, 15))
            await outbox.drain()
            alerted = {iid for iid, _ in sent}
            assert ada_id in alerted and bea_id not in alerted, "Bea switched trade alerts off"
            assert all("XAUUSD buy closed" in t and "$" not in t for _, t in sent)
            # re-publishing an edit does not alert again
            sent.clear()
            await a.admin("POST", "/disclosures/600/publish", {"note": "clarified"})
            await outbox.drain()
            assert sent == []
    run(go())
