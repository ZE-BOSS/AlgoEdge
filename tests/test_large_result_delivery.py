"""A large finished run must reach the page without freezing the server.

2026-09-15: a 6-symbol portfolio run (3,001 trades) never loaded — "Loading
backtest details..." forever. Measured on a result of that size, /latest_result
froze the event loop 7 s for a 13.7 MB body (438,000 equity points the page
decimates to 500), and the portfolio route sanitised the run on the event loop
and pushed it, chart slices included, through one WebSocket frame.
"""

import asyncio
import inspect
import json
from types import SimpleNamespace

from backend.api.routes import backtest as bt


def _result(n_groups=400, n_curve=100_000):
    groups = [{"group_id": f"g{i}", "pnl": 1.0, "chart_data": [{"t": 1}] * 50, "chart_data_h1": [{"t": 1}] * 50,
               "smc_data": {"x": 1}, "entry_confirmations": ["a"],
               "sub_trades": [{"id": f"l{i}", "chart_data": [{"t": 1}] * 50}]} for i in range(n_groups)]
    curve = [10_000.0 + (i % 997) for i in range(n_curve)]
    if n_curve > 77_000:
        curve[31_337] = 5_000.0   # the worst drawdown point
        curve[77_000] = 25_000.0  # the peak
    return {"backtest_id": "bt1", "total_trades": n_groups, "final_balance": 1.0, "report": {"win_rate": 0.5},
            "grouped_trades": groups, "trades": [g["sub_trades"][0] for g in groups], "equity_curve": curve,
            "run_logs": [{"message": "m"}], "replay": {"series": {"a": [1] * 1000}}}


def test_downsampled_curve_keeps_extremes_and_ends():
    r = _result()
    out = bt._downsample_curve(r["equity_curve"])
    assert len(out) <= bt._EQUITY_POINTS_MAX + 2
    assert out[0] == r["equity_curve"][0] and out[-1] == r["equity_curve"][-1]
    assert min(out) == 5_000.0 and max(out) == 25_000.0
    assert bt._downsample_curve([1.0, 2.0]) == [1.0, 2.0]


def test_lean_view_is_small_and_leaves_the_state_untouched():
    r = _result()
    lean = bt._lean_result_view(r)
    assert "replay" not in lean and lean["trades"] == [] and lean["trades_omitted"] == 400
    assert lean["equity_curve_points"] == 100_000 and len(lean["equity_curve"]) <= bt._EQUITY_POINTS_MAX + 2
    g = lean["grouped_trades"][0]
    assert "chart_data" not in g and "chart_data_h1" not in g and "smc_data" not in g
    assert "chart_data" not in g["sub_trades"][0]
    assert lean["report"] == {"win_rate": 0.5} and lean["run_logs"] == [{"message": "m"}]
    # the full copy stays intact for the chart, replay and save endpoints
    assert "chart_data" in r["grouped_trades"][0] and len(r["equity_curve"]) == 100_000 and r["replay"]

    small = bt._lean_result_view(_result(n_groups=10, n_curve=50))
    assert len(small["trades"]) == 10 and len(small["equity_curve"]) == 50

    mirror = bt._redis_safe_state({"status": "complete", "result": r})["result"]
    assert mirror["run_logs"] == [] and mirror["trades"] == [] and "replay" not in mirror


def test_latest_result_is_serialised_once_and_returned_as_bytes():
    user = SimpleNamespace(id="u-large-result", email="x")
    bt.USER_BACKTEST_STATE[user.id] = {"status": "complete", "result": _result()}
    try:
        first = asyncio.run(bt.get_backtest_latest_result(user))
        second = asyncio.run(bt.get_backtest_latest_result(user))
        assert first.media_type == "application/json" and first.body is second.body
        body = json.loads(first.body)
        assert body["backtest_id"] == "bt1" and len(body["grouped_trades"]) == 400
        # a new run replaces the cached body
        bt.USER_BACKTEST_STATE[user.id] = {"status": "complete", "result": {**_result(n_groups=5), "backtest_id": "bt2"}}
        third = asyncio.run(bt.get_backtest_latest_result(user))
        assert json.loads(third.body)["backtest_id"] == "bt2"
    finally:
        bt.USER_BACKTEST_STATE.pop(user.id, None)
        bt._LATEST_RESULT_JSON.pop(user.id, None)


def test_both_routes_announce_completion_without_the_result():
    src = inspect.getsource(bt)
    assert '"result": ws_payload' not in src
    assert src.count("_completion_envelope(sanitized)") == 2
    assert "sanitized = await asyncio.to_thread(_sanitize, response)" in src
    env = bt._completion_envelope(_result())
    assert set(env) == {"type", "stage", "pct", "backtest_id", "total_trades", "final_balance"}


def test_save_uses_the_servers_copy_and_builds_rows_off_the_loop():
    src = inspect.getsource(bt.save_backtest_from_client)
    assert '_srv_result.get("backtest_id") == backtest_id' in src
    assert 'raw_data.get("save_mode") == "SERVER"' in src and "status_code=409" in src
    assert "await _asyncio.to_thread(_build_trade_rows)" in src
    assert 't["chart_data_h1"] = state_t.get("chart_data_h1"' in src
