"""A finished run is delivered in pieces: a summary, then trade groups in pages,
then each trade's chart one timeframe at a time.

2026-09-15: portfolio runs (1,000-3,000 trades) never finished loading on the
user's remote server; the live chart and progress stream made the page heavy.
Later the same day a 1,025-trade portfolio run loaded its summary but its first
page of trades timed out at 60 s: each in-memory group carried the strategy's
signal payload six times (leg `metadata`, `original_signal`, and whole-leg
copies in `best_exit`/`worst_exit`), none of which the page reads.
"""

import asyncio
import json
from types import SimpleNamespace

from backend.api.routes import backtest as bt
from backend.services.replay_stream import MAX_SERIES_BARS, ReplayStreamer

HEAVY_SIGNAL = {"markings": [{"type": "FVG", "top": 1.0 + k, "bottom": 0.5 + k, "label": "H4 FVG"} for k in range(400)]}


def _candles(n, t0=1_767_225_600, step=300):
    return [{"time": t0 + i * step, "open": 1.0 + i, "high": 2.0 + i, "low": 0.5 + i, "close": 1.5 + i} for i in range(n)]


def _group(i):
    t0 = 1_767_225_600 + i * 3000
    sig = {"direction": "BUY", "stop_loss": 1.0, "metadata": HEAVY_SIGNAL}
    leg = {"id": f"l{i}", "entry_confirmations": ["✓ trend"], "chart_data": _candles(10, t0), "smc_data": {"b": 1},
           "metadata": HEAVY_SIGNAL, "original_signal": sig, "commission": 1.5, "sizing_diagnostics": {"lots": 0.1},
           "entry_snapshot_b64": "iVBORw0KGgo" * 500}
    return {"group_id": f"g{i}", "symbol": "EURUSD", "entry_time": t0 + 900 * 300, "exit_time": t0 + 910 * 300,
            "combined_pnl": 1.0, "initial_stop_loss": 1.0, "chart_data": _candles(60, t0),
            "chart_data_m5": _candles(1200, t0), "chart_data_m15": _candles(80, t0, 900), "chart_data_h1": [],
            "smc_data": {"boxes": [1] * 10}, "original_signal": sig, "entry_confirmations": ["✓ trend"],
            "entry_snapshot_b64": "iVBORw0KGgo" * 500, "best_exit": dict(leg), "worst_exit": dict(leg),
            "sub_trades": [leg]}


def _run(n=1100):
    groups = [_group(i) for i in range(n)]
    return {"backtest_id": "paged-1", "total_trades": n, "report": {"win_rate": 0.5}, "grouped_trades": groups,
            "trades": [g["sub_trades"][0] for g in groups], "equity_curve": [10_000.0 + i for i in range(50_000)],
            "run_logs": [{"message": "done"}], "replay": {"series": {"a": [1]}}}


def _json(resp):
    return json.loads(resp.body)


def _all_pages(user, page_size=100):
    got, offset, pages, largest = [], 0, 0, 0
    while True:
        resp = asyncio.run(bt.get_latest_result_trades(offset=offset, limit=page_size, current_user=user))
        largest = max(largest, len(resp.body))
        page = json.loads(resp.body)
        pages += 1
        if not page["groups"]:
            break
        got.extend(page["groups"])
        offset = page["next_offset"]
        if offset >= page["total"]:
            break
    return got, pages, largest


def _cleanup(user):
    for store in (bt.USER_BACKTEST_STATE, bt._SUMMARY_JSON, bt._GROUP_INDEX):
        store.pop(user.id, None)


def test_summary_then_pages_reassemble_the_run_in_order_without_the_signal_copies():
    user = SimpleNamespace(id="u-paged")
    run = _run()
    bt.USER_BACKTEST_STATE[user.id] = {"status": "complete", "result": run}
    try:
        summary = _json(asyncio.run(bt.get_latest_result_summary(user)))
        assert summary["trades_paged"] and summary["trade_groups_total"] == 1100 and summary["grouped_trades"] == []
        assert summary["report"] == {"win_rate": 0.5} and summary["run_logs"] == [{"message": "done"}]
        assert len(summary["equity_curve"]) <= bt._EQUITY_POINTS_MAX + 2 and "replay" not in summary

        got, _, largest = _all_pages(user)
        assert [g["group_id"] for g in got] == [f"g{i}" for i in range(1100)]
        g = got[0]
        for key in ("chart_data", "smc_data", "original_signal", "metadata", "best_exit", "worst_exit",
                    "entry_snapshot_b64"):
            assert key not in g, key
            assert key not in g["sub_trades"][0], key
        # what the page does read stays
        assert g["entry_confirmations"] == ["✓ trend"] and g["initial_stop_loss"] == 1.0
        assert g["sub_trades"][0]["commission"] == 1.5 and g["sub_trades"][0]["sizing_diagnostics"] == {"lots": 0.1}
        # 100 groups of a few hundred bytes each, not megabytes
        assert largest < 200_000, largest
        # the kept result is untouched
        full = run["grouped_trades"][0]
        assert full["chart_data_m5"] and full["best_exit"] and full["sub_trades"][0]["metadata"] and run["replay"]
    finally:
        _cleanup(user)


def test_a_page_is_cut_at_the_byte_budget_and_continues_from_next_offset():
    user = SimpleNamespace(id="u-budget")
    run = _run(30)
    for g in run["grouped_trades"]:
        g["some_future_field"] = "x" * 400_000      # anything a strategy might add later
    bt.USER_BACKTEST_STATE[user.id] = {"status": "complete", "result": run}
    try:
        first = _json(asyncio.run(bt.get_latest_result_trades(offset=0, limit=250, current_user=user)))
        assert 1 <= first["count"] < 30 and first["next_offset"] == first["count"]
        assert len(json.dumps(first)) <= bt._PAGE_BYTES_MAX + 450_000
        got, pages, _ = _all_pages(user, page_size=250)
        assert [g["group_id"] for g in got] == [f"g{i}" for i in range(30)] and pages > 1
    finally:
        _cleanup(user)


def test_lean_view_and_mirror_drop_the_signal_copies_too():
    run = _run(3)
    for view in (bt._lean_result_view(run), bt._lean_result_view(run, for_mirror=True)):
        g = view["grouped_trades"][0]
        assert "best_exit" not in g and "metadata" not in g["sub_trades"][0] and "original_signal" not in g
    small = bt._lean_result_view(run)
    assert small["trades"] and "metadata" not in small["trades"][0]


def test_trade_chart_sends_one_timeframe_trimmed_around_the_trade_with_its_snapshot():
    user = SimpleNamespace(id="u-chart")
    run = _run(3)
    bt.USER_BACKTEST_STATE[user.id] = {"status": "complete", "result": run}
    try:
        out = asyncio.run(bt.get_unsaved_trade_chart("g1", tf="M5", current_user=user))
        g = run["grouped_trades"][1]
        assert out["tf"] == "M5" and len(out["candles"]) <= bt._CHART_BARS_MAX
        times = [c["time"] for c in out["candles"]]
        assert times[0] <= g["entry_time"] and times[-1] >= g["exit_time"]
        assert set(out["available"]) == {"M5", "M15"}
        assert out["panel"]["entry_confirmations"] == ["✓ trend"] and out["panel"]["smc_data"]
        assert out["panel"]["entry_snapshot_b64"].startswith("iVBOR")
        assert out["sub_trades_panel"][0]["entry_confirmations"] == ["✓ trend"]
        legacy = asyncio.run(bt.get_unsaved_trade_chart("g1", tf=None, current_user=user))
        assert {"chart_data", "chart_data_m15", "chart_data_m5", "chart_data_h1"} <= set(legacy)
    finally:
        _cleanup(user)


def test_trim_keeps_short_series_and_handles_iso_times():
    short = _candles(50)
    assert bt._trim_candles(short, None, None) is short
    long = _candles(2000)
    iso = "2026-01-04T12:00:00+00:00"
    out = bt._trim_candles(long, iso, iso, max_bars=300)
    e = bt._epoch_of(iso)
    assert len(out) == 300 and out[0]["time"] <= e <= out[-1]["time"]


def test_replay_without_the_live_stream_sends_nothing_and_keeps_a_bounded_series():
    sent = []

    class Mgr:
        async def broadcast_to_user(self, uid, payload):
            sent.append(payload)

    async def go():
        s = ReplayStreamer(Mgr(), "u1", stream=False)
        s.init([{"slot_id": "X"}])
        total = MAX_SERIES_BARS * 5 + 3
        s.leg_start("X", total_bars=total)
        for b in _candles(total):
            s.bar("X", b)
        s.leg_done("X")
        await asyncio.sleep(0)
        return s

    s = asyncio.run(go())
    assert sent == []
    assert len(s._series["X"]) <= MAX_SERIES_BARS + 1
    series = s.series_payload()["series"]["X"]
    assert len(series) <= MAX_SERIES_BARS
    first = series[0]
    assert first["open"] == 1.0 and first["low"] == 0.5 and first["high"] >= 2.0


def test_saved_run_pages_leave_the_snapshot_to_the_chart_endpoint():
    from datetime import datetime, timedelta

    snap = "iVBORw0KGgo" * 2000

    def row(i):
        t0 = datetime(2026, 3, 2, 10, 0) + timedelta(hours=i)
        return SimpleNamespace(
            id=i + 1, symbol="GBPJPY", direction="BUY", entry_price=190.0, exit_price=190.5, stop_loss=189.5,
            pnl=10.0, exit_reason="TP1", tp_level_hit=1, balance_before=10_000.0, balance_after=10_010.0,
            tp1_price=191.0, tp2_price=None, tp3_price=None, tp4_price=None, tp5_price=None, pnl_r=1.0,
            planned_rr=2.0, realized_rr=1.0, entry_time=t0, exit_time=t0 + timedelta(hours=1), session="LONDON",
            be_applied=False, trail_method="NONE", mae_pips=1.0, mfe_pips=5.0, confluence_score=70,
            strategy_id="ORB_v1", smc_data='{"boxes": []}',
            sub_trades=json.dumps([{"id": f"l{i}", "pnl": 10.0, "entry_snapshot_b64": snap,
                                    "entry_confirmations": ["✓ ORB"]}]))

    run = SimpleNamespace(strategy_id="ORB_v1", trades=[row(i) for i in range(7)])

    class Result:
        def scalar_one_or_none(self):
            return run

    class DB:
        async def execute(self, *_a, **_k):
            return Result()

    user = SimpleNamespace(id="u-saved")
    resp = asyncio.run(bt.get_backtest_trades_page("bt-1", offset=2, limit=3, current_user=user, db=DB()))
    page = json.loads(resp.body)
    assert page["total"] == 7 and page["count"] == 3 and page["next_offset"] == 5
    assert [g["group_id"] for g in page["groups"]] == ["3", "4", "5"]
    g = page["groups"][0]
    assert "entry_snapshot_b64" not in g and "entry_snapshot_b64" not in g["sub_trades"][0]
    assert g["sub_trades"][0]["entry_confirmations"] == ["✓ ORB"] and g["duration_minutes"] == 60
    assert len(resp.body) < 20_000
    # the full detail shape still carries it
    assert bt._saved_group_out(run.trades[0], run)["entry_snapshot_b64"] == snap


def test_routes_stream_only_when_asked():
    import inspect
    src = inspect.getsource(bt)
    assert src.count('enabled=True, stream=bool(getattr(req, "replay_enabled", True)))') == 2
