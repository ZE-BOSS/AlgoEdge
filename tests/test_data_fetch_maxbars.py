"""A backtest window longer than the terminal's `maxbars` must still fetch.

MT5 caps every history request at `maxbars` (Tools > Options > Charts > "Max bars
in chart"), and `copy_rates_range` measures a request against the number of
PERIODS the span covers, not the number of bars that exist inside it. So an
11-month M5 range holding 68,617 real bars is refused with
(-2, 'Terminal: Invalid params') because the span is 101,952 five-minute slots
against a `maxbars` of 100,000 — and a request for exactly `maxbars` is refused
too.

`get_data_range` had a fallback for this that asked `copy_rates_from` for up to
200,000 bars, which is above every real `maxbars`, so it could only ever fail as
well. The consequence was that ANY M5 strategy failed outright over a window of a
year or more: measured 2026-09-25 on the FundedNext terminal, an
OvernightSession_v1 backtest over 2025-11 -> 2026-09 died in the fetch while the
same run over four months worked.

These tests drive the real fetch against a stub MT5 that enforces the same limit,
so they hold without a terminal.
"""

import asyncio
from datetime import datetime, timezone

import numpy as np
import pytest

from backend.mt5 import data_fetcher as df_mod

RATE_DTYPE = np.dtype([("time", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"),
                       ("close", "<f8"), ("tick_volume", "<u8"), ("spread", "<i4"),
                       ("real_volume", "<u8")])
MAXBARS = 100_000
STEP = 300          # M5


class _TerminalInfo:
    connected = True
    maxbars = MAXBARS


class FakeMT5:
    """Just enough MT5 to answer a history request the way the real one does."""

    TIMEFRAME_M1, TIMEFRAME_M5, TIMEFRAME_M15 = 1, 5, 15
    TIMEFRAME_M30, TIMEFRAME_H1, TIMEFRAME_H4, TIMEFRAME_D1 = 30, 16385, 16388, 16408
    TIMEFRAME_MN1, TIMEFRAME_W1 = 49153, 32769

    def __init__(self, first_bar: int, last_bar: int, step: int = STEP):
        self.first, self.last, self.step = first_bar, last_bar, step
        self.range_calls: list[tuple[int, int]] = []
        self.from_calls: list[int] = []
        self._err = (1, "Success")

    def terminal_info(self):
        return _TerminalInfo()

    def last_error(self):
        return self._err

    def initialize(self, *a, **k):
        return True

    def _bars(self, lo, hi):
        lo = max(lo, self.first)
        hi = min(hi, self.last)
        if hi <= lo:
            return np.empty(0, dtype=RATE_DTYPE)
        t = np.arange(lo - lo % self.step, hi, self.step, dtype=np.int64)
        out = np.zeros(len(t), dtype=RATE_DTYPE)
        out["time"] = t
        for f in ("open", "high", "low", "close"):
            out[f] = 100.0 + np.arange(len(t)) * 1e-6
        out["high"] += 0.01
        out["low"] -= 0.01
        out["tick_volume"] = 10
        return out

    def copy_rates_range(self, symbol, tf, start, end):
        self.range_calls.append((int(start), int(end)))
        # the real limit: periods spanned, not bars returned
        if (int(end) - int(start)) // self.step >= MAXBARS:
            self._err = (-2, "Terminal: Invalid params")
            return None
        self._err = (1, "Success")
        got = self._bars(int(start), int(end))
        return got if len(got) else None

    def copy_rates_from(self, symbol, tf, end, count):
        self.from_calls.append(int(count))
        if int(count) >= MAXBARS:
            self._err = (-2, "Terminal: Invalid params")
            return None
        self._err = (1, "Success")
        got = self._bars(int(end) - int(count) * self.step, int(end))
        return got if len(got) else None


@pytest.fixture
def stub(monkeypatch):
    """A terminal holding two years of M5 bars, read as already-UTC."""
    last = int(datetime(2026, 9, 20, tzinfo=timezone.utc).timestamp())
    fake = FakeMT5(last - 2 * 365 * 86400, last)
    monkeypatch.setattr(df_mod, "mt5", fake)
    monkeypatch.setattr(df_mod, "_SERVER_OFFSET_CACHE", 0.0)
    monkeypatch.setenv("ALGOEDGE_MT5_SERVER_UTC_OFFSET", "0")
    return fake


def _fetch(symbol, tf, start, end):
    return asyncio.run(df_mod.DataFetcher.get_data_range(symbol, tf, start, end))


def test_a_span_longer_than_maxbars_is_sliced_and_still_returns_every_bar(stub):
    start = datetime(2025, 10, 1, tzinfo=timezone.utc)
    end = datetime(2026, 9, 20, tzinfo=timezone.utc)
    out = _fetch("SPX500", "M5", start, end)

    assert len(stub.range_calls) > 1, "an over-long span must be sliced, not attempted whole"
    for lo, hi in stub.range_calls:
        assert (hi - lo) // STEP < MAXBARS, f"slice {lo}-{hi} still exceeds maxbars"
    assert not stub.from_calls, "the copy_rates_from fallback should not be needed"

    t = out["time"]
    assert t.is_monotonic_increasing and not t.duplicated().any()
    assert int(t.iloc[0]) >= int(start.timestamp())
    assert int(t.iloc[-1]) <= int(end.timestamp())
    # every M5 slot in the range the stub holds, with no gap at a slice boundary
    expected = (int(end.timestamp()) - int(start.timestamp())) // STEP
    assert len(out) == pytest.approx(expected, rel=0.01)


def test_a_short_span_is_still_exactly_one_request(stub):
    """The fix must not change the common case."""
    out = _fetch("SPX500", "M5", datetime(2026, 5, 1, tzinfo=timezone.utc),
                 datetime(2026, 9, 20, tzinfo=timezone.utc))
    assert len(stub.range_calls) == 1
    assert len(out) > 1000


def test_the_fallback_never_asks_for_more_bars_than_the_terminal_serves(stub, monkeypatch):
    """With every range request refused, the `copy_rates_from` fallback has to ask
    for a count the terminal will actually answer — 200,000 never was."""
    monkeypatch.setattr(stub, "copy_rates_range", lambda *a, **k: None)
    out = _fetch("SPX500", "M5", datetime(2026, 1, 1, tzinfo=timezone.utc),
                 datetime(2026, 9, 20, tzinfo=timezone.utc))
    assert stub.from_calls, "the fallback never ran"
    for count in stub.from_calls:
        assert count < MAXBARS, f"asked for {count} against maxbars {MAXBARS}"
    assert len(out) > 1000


def test_max_bars_budget_stays_under_the_terminals_limit(stub):
    """`maxbars` itself is refused, so the budget has to be strictly below it."""
    assert 1000 <= df_mod.terminal_max_bars() < MAXBARS


def test_max_bars_budget_falls_back_when_the_terminal_cannot_be_asked(monkeypatch):
    class Broken:
        def terminal_info(self):
            raise RuntimeError("no IPC")

    monkeypatch.setattr(df_mod, "mt5", Broken())
    assert 1000 <= df_mod.terminal_max_bars() < 100_000


def test_an_empty_slice_does_not_fail_the_whole_fetch(stub):
    """History that starts mid-range, or a slice that is all holiday, is normal —
    only every slice coming back empty is a failure."""
    out = _fetch("SPX500", "M5", datetime(2023, 1, 1, tzinfo=timezone.utc),
                 datetime(2026, 9, 20, tzinfo=timezone.utc))
    assert len(stub.range_calls) > 2
    assert len(out) > 1000
    assert int(out["time"].iloc[0]) >= stub.first


def test_a_range_with_no_history_at_all_still_raises(stub):
    with pytest.raises(df_mod.DataFetchError):
        _fetch("SPX500", "M5", datetime(2010, 1, 1, tzinfo=timezone.utc),
               datetime(2010, 6, 1, tzinfo=timezone.utc))
