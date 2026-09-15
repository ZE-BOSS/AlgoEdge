"""Execute the save route itself, not just its source.

2026-09-15: a source-text test passed while every save raised UnboundLocalError
(a later `from ... import USER_BACKTEST_STATE` inside the function made the name
local), which the browser reported as "Network Error". These tests call the
route with a stub request and database.
"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.api.routes import backtest as bt


class _Result:
    def scalars(self):
        return self

    def first(self):
        return None


class _DB:
    def __init__(self):
        self.added = []
        self.committed = False

    async def execute(self, *_a, **_k):
        return _Result()

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True


def _request(body):
    async def _json():
        return body
    return SimpleNamespace(json=_json)


def _server_result(backtest_id="bt-save"):
    group = {"group_id": "g1", "symbol": "EURUSD", "direction": "BUY", "entry_price": 1.1, "exit_price": 1.2,
             "stop_loss": 1.0, "pnl": 10.0, "entry_time_iso": "2026-03-02T10:00:00+00:00",
             "exit_time_iso": "2026-03-02T11:00:00+00:00", "exit_reason": "TP1",
             "chart_data": [{"time": 1, "close": 1.1}], "chart_data_h1": [{"time": 1, "close": 1.1}],
             "sub_trades": [{"id": "l1", "pnl": 10.0}]}
    return {"backtest_id": backtest_id, "total_trades": 1, "report": {"win_rate": 1.0, "total_pnl": 10.0},
            "grouped_trades": [group], "trades": [dict(group)], "equity_curve": [10_000.0, 10_010.0],
            "run_logs": [{"message": "done"}], "replay": {"series": {"EURUSD": [1, 2, 3]}},
            "params_snapshot": {"start_date": "2026-03-01", "end_date": "2026-03-31"}}


def test_server_mode_saves_the_servers_full_copy():
    user = SimpleNamespace(id="u-save-1")
    bt.USER_BACKTEST_STATE[user.id] = {"status": "complete", "result": _server_result()}
    db = _DB()
    try:
        out = asyncio.run(bt.save_backtest_from_client(
            "bt-save", _request({"backtest_data": {"title": "My run", "notes": "n", "symbol": "EURUSD"},
                                 "save_mode": "SERVER"}), user, db))
    finally:
        bt.USER_BACKTEST_STATE.pop(user.id, None)
    assert out["status"] == "ok" and db.committed
    run, trade = db.added[0], db.added[1]
    assert run.title == "My run" and run.total_trades == 1 and run.replay_data and "done" in run.run_logs
    assert '"close": 1.1' in trade.chart_data and '"close": 1.1' in trade.chart_data_h1


def test_server_mode_without_the_run_asks_for_the_full_body():
    user = SimpleNamespace(id="u-save-2")
    bt.USER_BACKTEST_STATE.pop(user.id, None)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(bt.save_backtest_from_client(
            "bt-gone", _request({"backtest_data": {"title": "x"}, "save_mode": "SERVER"}), user, _DB()))
    assert exc.value.status_code == 409


def test_full_mode_still_saves_a_client_body():
    user = SimpleNamespace(id="u-save-3")
    bt.USER_BACKTEST_STATE.pop(user.id, None)
    db = _DB()
    body = {**_server_result("bt-client"), "title": "Uploaded"}
    out = asyncio.run(bt.save_backtest_from_client(
        "bt-client", _request({"backtest_data": body, "save_mode": "FULL"}), user, db))
    assert out["status"] == "ok" and db.added[0].title == "Uploaded" and len(db.added) == 2
