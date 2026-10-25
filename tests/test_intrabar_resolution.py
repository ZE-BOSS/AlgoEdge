"""
A bar that reaches the stop AND the target is settled from one-minute bars
when they are available (backtester/intrabar.py), and by the conservative
same-bar guess otherwise. 2026-10-20: the guess booked the roboquant Crazy ORB
port (target 0.35x the stop) at 65% win; the minutes, at the template's ~80%.
"""

import pandas as pd

from backend.backtester.engine import _resolve_sl_tp_hit
from backend.backtester.intrabar import IntrabarResolver, resolve_with_minutes


def _m1(rows, start="2026-03-02 14:30"):
    idx = pd.date_range(start, periods=len(rows), freq="1min")
    f = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)
    f["time"] = idx.as_unit("s").asi8
    return f


BAR_T = int(pd.Timestamp("2026-03-02 14:30").timestamp())


def test_target_first_inside_the_bar_is_booked_as_target():
    # BUY at 100, stop 95, target 102: up to 102.5 in minute 2, down to 94 in minute 9.
    rows = [(100, 101, 99.5, 100.5), (100.5, 102.5, 100, 102)] + [(102, 102, 99, 99)] * 6 + [(99, 99, 94, 95)] * 7
    r = IntrabarResolver(_m1(rows))
    # the 15-minute bar: open 100, high 102.5, low 94 - the guess says stop
    assert _resolve_sl_tp_hit("BUY", 100, 102.5, 94, 95, 102, True) == (True, False)
    assert resolve_with_minutes(r, "BUY", 100, 102.5, 94, 95, 102, True, BAR_T, 900) == (False, True)


def test_stop_first_stays_a_stop_and_no_minutes_means_the_guess():
    rows = [(100, 100.2, 94.5, 95)] + [(95, 103, 95, 102)] * 14
    r = IntrabarResolver(_m1(rows))
    assert resolve_with_minutes(r, "BUY", 100, 103, 94.5, 95, 102, True, BAR_T, 900) == (True, False)
    # minutes for another day: falls back to the conservative guess
    assert resolve_with_minutes(r, "BUY", 100, 103, 94.5, 95, 102, True, BAR_T + 86400, 900) == \
        _resolve_sl_tp_hit("BUY", 100, 103, 94.5, 95, 102, True)


def test_a_minute_that_reaches_both_falls_back_to_the_guess_on_that_minute():
    rows = [(100, 103, 94, 100)] + [(100, 100, 100, 100)] * 14
    r = IntrabarResolver(_m1(rows))
    assert resolve_with_minutes(r, "SELL", 100, 103, 94, 102.5, 95, True, BAR_T, 900) == \
        _resolve_sl_tp_hit("SELL", 100, 103, 94, 102.5, 95, True)


def test_portfolio_entry_bar_exit_reads_its_bar_length_and_minutes_by_slot():
    """The entry-bar check is shared (CostModelMixin). A portfolio position's
    symbol can be a slot key and its bar length is kept per key - without the
    per-engine hooks every portfolio fill bar fell back to the guess."""
    from backend.backtester.portfolio_engine import PortfolioBacktestEngine
    eng = PortfolioBacktestEngine({}, {})
    eng._symbol_map = {"XAUUSD::RQ_CrazyORB_v1": "XAUUSD"}
    eng._bar_secs_by_key = {"XAUUSD::RQ_CrazyORB_v1": 900.0}
    eng.intrabar = {"XAUUSD": "resolver"}
    pos = {"symbol": "XAUUSD::RQ_CrazyORB_v1", "_cache_key": "XAUUSD::RQ_CrazyORB_v1"}
    assert eng._bar_secs_for(pos) == 900.0
    assert eng._intrabar_for(pos) == "resolver"
