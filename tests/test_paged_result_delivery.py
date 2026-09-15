"""A finished run is delivered in pieces: a summary, then trade groups in pages,
then each trade's chart one timeframe at a time.

2026-09-15: portfolio runs (1,000-3,000 trades) never finished loading on the
user's remote server; the live chart and progress stream made the page heavy.
"""

import asyncio
import json
from types import SimpleNamespace

from backend.api.routes import backtest as bt
from backend.services.replay_stream import MAX_SERIES_BARS, ReplayStreamer


def _candles(n, t0=1_767_225_600, step=300):
    return [{"time": t0 + i * step, "open": 1.0 + i, "high": 2.0 + i, "low": 0.5 + i, "close": 1.5 + i} for i in range(n)]


def _group(i):
    t0 = 1_767_225_600 + i * 3000
    return {"group_id": f"g{i}", "symbol": "EURUSD", "entry_time": t0 + 900 * 300, "exit_time": t0 + 910 * 300,
            "combined_pnl": 1.0, "chart_data": _candles(60, t0), "chart_data_m5": _candles(1200, t0),
            "chart_data_m15": _candles(80, t0, 900), "chart_data_h1": [], "smc_data": {"boxes": [1] * 10},
            "original_signal": {"x": 1}, "entry_confirmations": ["✓ trend"],
            "sub_trades": [{"id": f"l{i}", "entry_confirmations": ["✓ trend"], "chart_data": _candles(10, t0),
                            "smc_data": {"b": 1}}]}


def _run(n=1100):
    groups = [_group(i) for i in range(n)]
    return {"backtest_id": "paged-1", "total_trades": n, "report": {"win_rate": 0.5}, "grouped_trades": groups,
            "trades": [g["sub_trades"][0] for g in groups], "equity_curve": [10_000.0 + i for i in range(50_000)],
            "run_logs": [{"message": "done"}], "replay": {"series": {"a": [1]}}}


def _json(resp):
    return json.loads(resp.body)


def test_summary_then_pages_reassemble_the_run_in_order():
    user = SimpleNamespace(id="u-paged")
    run = _run()
    bt.USER_BACKTEST_STATE[user.id] = {"status": "complete", "result": run}
    try:
        summary = _json(asyncio.run(bt.get_latest_result_summary(user)))
        assert summary["trades_paged"] and summary["trade_groups_total"] == 1100 and summary["grouped_trades"] == []
        assert summary["report"] == {"win_rate": 0.5} and summary["run_logs"] == [{"message": "done"}]
        assert len(summary["equity_curve"]) <= bt._EQUITY_POINTS_MAX + 2 and "replay" not in summary

        got = []
        offset = 0
        while offset < summary["trade_groups_total"]:
            page = _json(asyncio.run(bt.get_latest_result_trades(offset=offset, limit=250, current_user=user)))
            assert page["total"] == 1100 and len(page["groups"]) <= 250
            got.extend(page["groups"])
            offset += 250
        assert [g["group_id"] for g in got] == [f"g{i}" for i in range(1100)]
        g = got[0]
        assert "chart_data" not in g and "smc_data" not in g and "original_signal" not in g
        assert g["entry_confirmations"] == ["✓ trend"] and g["sub_trades"][0]["entry_confirmations"] == ["✓ trend"]
        assert "chart_data" not in g["sub_trades"][0]
        # the kept result is untouched
        assert run["grouped_trades"][0]["chart_data_m5"] and run["replay"]

        capped = _json(asyncio.run(bt.get_latest_result_trades(offset=0, limit=10_000, current_user=user)))
        assert capped["limit"] == bt._TRADE_PAGE_MAX and len(capped["groups"]) == bt._TRADE_PAGE_MAX
    finally:
        for store in (bt.USER_BACKTEST_STATE, bt._SUMMARY_JSON, bt._PAGE_GROUPS, bt._GROUP_INDEX):
            store.pop(user.id, None)


def test_trade_chart_sends_one_timeframe_trimmed_around_the_trade():
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
        assert out["sub_trades_panel"][0]["entry_confirmations"] == ["✓ trend"]
        legacy = asyncio.run(bt.get_unsaved_trade_chart("g1", tf=None, current_user=user))
        assert {"chart_data", "chart_data_m15", "chart_data_m5", "chart_data_h1"} <= set(legacy)
    finally:
        for store in (bt.USER_BACKTEST_STATE, bt._GROUP_INDEX):
            store.pop(user.id, None)


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


def test_routes_stream_only_when_asked():
    import inspect
    src = inspect.getsource(bt)
    assert src.count('enabled=True, stream=bool(getattr(req, "replay_enabled", True)))') == 2
