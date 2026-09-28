"""The broker's UTC offset must not depend on whether the market is open.

MT5 reports bar times in SERVER time encoded as a pseudo-UTC epoch, so every
Eastern-Time session gate in the app is wrong by exactly the server's offset
unless that offset is subtracted first. It was measured by comparing a live tick
to real UTC — and a tick read while the market is SHUT is in the past, so
`tick.time - now` returns the offset MINUS however long the market has been
closed. It can only read LOW.

Measured on the FundedNext terminal on 2026-09-25, whose real offset is +3h: two
backends started 33 minutes apart, both after the Friday close, detected +2.0h
and +1.0h, and a third read 20 minutes later got +0.5h. Which hours a
session-anchored strategy traded therefore depended on when the backend started,
silently: VWAP, ORB, APA's session modes, IVW's day-end flat, and both session
strategies shipped on 2026-09-25.

The fix reads the offset off WHERE THE NEW YORK CASH OPEN SITS in the bars, which
does not care what time it is now, and trusts a tick only when it agrees.
"""

from datetime import datetime, timezone

import numpy as np
import pytest
import pytz

from backend.mt5 import data_fetcher as df_mod

NY = pytz.timezone("America/New_York")
RATE_DTYPE = np.dtype([("time", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"),
                       ("close", "<f8"), ("tick_volume", "<u8"), ("spread", "<i4"),
                       ("real_volume", "<u8")])


def _session_bars(true_offset_h: float, days: int = 14,
                  end: datetime | None = None) -> np.ndarray:
    """M5 bars in SERVER time for a market whose volume triples at 09:30 New York."""
    end = end or datetime(2026, 9, 25, tzinfo=timezone.utc)
    end_utc = int(end.timestamp())
    t_utc = np.arange(end_utc - days * 86400, end_utc, 300, dtype=np.int64)
    vol = np.full(len(t_utc), 40.0)
    for day in np.unique(t_utc // 86400):
        d = datetime.fromtimestamp(int(day) * 86400, timezone.utc).date()
        if d.weekday() >= 5:
            continue
        open_utc = int(NY.localize(datetime(d.year, d.month, d.day, 9, 30)).timestamp())
        bump = (t_utc >= open_utc) & (t_utc < open_utc + 1800)
        vol[bump] = 600.0
    out = np.zeros(len(t_utc), dtype=RATE_DTYPE)
    out["time"] = t_utc + int(true_offset_h * 3600)      # stored as SERVER time
    for f in ("open", "high", "low", "close"):
        out[f] = 100.0
    out["tick_volume"] = vol.astype(np.uint64)
    return out


class FakeMT5:
    TIMEFRAME_M5 = 5

    def __init__(self, true_offset_h, tick_lag_hours=0.0, bars=True, end=None):
        self.true_offset = true_offset_h
        self.tick_lag = tick_lag_hours
        self._bars = _session_bars(true_offset_h, end=end) if bars else None
        self.probed = []

    def symbol_info_tick(self, symbol):
        now = datetime.now(timezone.utc).timestamp()
        class T:
            pass
        t = T()
        # server time of a tick that last printed `tick_lag` hours ago
        t.time = int(now - self.tick_lag * 3600 + self.true_offset * 3600)
        return t

    def copy_rates_from_pos(self, symbol, tf, start, count):
        self.probed.append(symbol)
        return self._bars

    def terminal_info(self):
        class TI:
            connected = True
            maxbars = 100_000
        return TI()

    def last_error(self):
        return (1, "Success")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("ALGOEDGE_MT5_SERVER_UTC_OFFSET", raising=False)
    monkeypatch.setattr(df_mod, "_SERVER_OFFSET_CACHE", None)
    yield
    monkeypatch.setattr(df_mod, "_SERVER_OFFSET_CACHE", None)


@pytest.mark.parametrize("true_offset", [0.0, 1.0, 2.0, 3.0])
def test_the_cash_open_recovers_the_offset_with_no_live_tick_at_all(monkeypatch, true_offset):
    monkeypatch.setattr(df_mod, "mt5", FakeMT5(true_offset))
    assert df_mod._offset_from_volume_profile() == true_offset


def test_it_survives_a_dst_change(monkeypatch):
    """A fixed minute-of-day would be an hour out on one side of the boundary; the
    New York open is resolved per day through pytz instead. US DST ended
    2025-11-02, so a window ending 2025-11-08 straddles it."""
    monkeypatch.setattr(df_mod, "mt5", FakeMT5(3.0, end=datetime(2025, 11, 8, tzinfo=timezone.utc)))
    assert df_mod._offset_from_volume_profile() == 3.0


def test_a_stale_tick_does_not_win_against_the_bars(monkeypatch):
    """The exact 2026-09-25 failure: the market shut 2.5 hours ago, so the tick
    reads +0.5h when the truth is +3h."""
    fake = FakeMT5(3.0, tick_lag_hours=2.5)
    monkeypatch.setattr(df_mod, "mt5", fake)
    assert df_mod.detect_server_utc_offset_hours() == 3.0


def test_a_fresh_tick_agrees_and_is_used(monkeypatch):
    monkeypatch.setattr(df_mod, "mt5", FakeMT5(3.0, tick_lag_hours=0.0))
    assert df_mod.detect_server_utc_offset_hours() == 3.0


def test_the_tick_still_works_when_no_symbol_has_bars(monkeypatch):
    """No regression for a broker whose probe symbols are all missing."""
    monkeypatch.setattr(df_mod, "mt5", FakeMT5(2.0, tick_lag_hours=0.0, bars=False))
    assert df_mod.detect_server_utc_offset_hours() == 2.0


def test_the_env_override_still_beats_both(monkeypatch):
    monkeypatch.setenv("ALGOEDGE_MT5_SERVER_UTC_OFFSET", "3")
    monkeypatch.setattr(df_mod, "mt5", FakeMT5(0.0, tick_lag_hours=9.0))
    assert df_mod.detect_server_utc_offset_hours() == 3.0


def test_no_mt5_at_all_falls_back_to_zero(monkeypatch):
    monkeypatch.setattr(df_mod, "mt5", None)
    assert df_mod.detect_server_utc_offset_hours() == 0.0


def test_the_result_is_cached(monkeypatch):
    fake = FakeMT5(3.0, tick_lag_hours=4.0)
    monkeypatch.setattr(df_mod, "mt5", fake)
    assert df_mod.detect_server_utc_offset_hours() == 3.0
    probes = len(fake.probed)
    assert df_mod.detect_server_utc_offset_hours() == 3.0
    assert len(fake.probed) == probes, "a cached offset must not re-probe the terminal"


def test_bars_shift_to_true_utc_by_the_detected_offset(monkeypatch):
    """End to end: the point of all this is that _normalize_df lands on real UTC,
    because that is what every session gate assumes."""
    import pandas as pd
    fake = FakeMT5(3.0, tick_lag_hours=5.0)
    monkeypatch.setattr(df_mod, "mt5", fake)
    server = int(datetime(2026, 9, 22, 16, 30, tzinfo=timezone.utc).timestamp())  # 13:30 true UTC
    out = df_mod._normalize_df(pd.DataFrame({"time": [server], "open": [1.0], "high": [1.0],
                                             "low": [1.0], "close": [1.0]}))
    assert int(out["time"].iloc[0]) == server - 3 * 3600
    got = datetime.fromtimestamp(int(out["time"].iloc[0]), timezone.utc).astimezone(NY)
    assert got.strftime("%H:%M") == "09:30", f"the cash open landed at {got:%H:%M} New York"
