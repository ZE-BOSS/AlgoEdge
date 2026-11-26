"""Pre-signal alerts (services/watch_service.py) and the strategies' watch() levels."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from backend.core.config_schema import UserConfigV2
from backend.services.watch_service import WatchService, WatchTap, attach_tap
from backend.strategies.gate_recorder import CandidateRecord, GateEvent
from backend.strategies.registry import get_strategy

ET = ZoneInfo("America/New_York")


# ── the tap never changes a signal ────────────────────────────────────────────

def _ibs_sessions(days_ohlc):
    rows = []
    for d, (o, h, lo, c) in zip(pd.bdate_range("2026-03-02", periods=len(days_ohlc)), days_ohlc):
        times = [pd.Timestamp(d).replace(hour=9, minute=30, tzinfo=ET) + pd.Timedelta(minutes=int(30 * k)) for k in range(14)]
        path = np.linspace(o, c, 13)
        for k, t in enumerate(times):
            if k < 13:
                p0 = path[k - 1] if k else o
                p1 = path[k]
                hi = max(p0, p1) + (h - max(o, c)) * (k == 3)
                lw = min(p0, p1) - (min(o, c) - lo) * (k == 6)
                rows.append((int(t.timestamp()), p0, hi, lw, p1))
            else:
                rows.append((int(t.timestamp()), c, c, c, c))
    return pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"])


DAYS = [(100, 101, 99, 100.5), (100.5, 102, 100, 101.8), (101.8, 103, 101, 102.6),
        (102.6, 104, 102.4, 103.6), (103.6, 106, 103.0, 103.2)]


def _ibs(sma=3):
    cfg = UserConfigV2()
    cfg.ql_ibs.sma_days = sma
    return get_strategy("QL_IBS_v1")(cfg)


def test_tap_leaves_the_signal_identical_and_learns_its_chain():
    df = _ibs_sessions(DAYS)
    win = df.iloc[: 4 * 14 + 13].reset_index(drop=True)
    plain = asyncio.run(_ibs().on_bar("US Tech 100", "M30", win))
    eng = _ibs()
    tap = attach_tap(eng)
    tapped = asyncio.run(eng.on_bar("US Tech 100", "M30", win))
    assert plain is not None and tapped is not None
    assert tapped.metadata == plain.metadata                 # no passed_gates etc. added
    assert (tapped.direction, tapped.entry_price, tapped.stop_loss) == (plain.direction, plain.entry_price, plain.stop_loss)
    assert tap.last_chain == ["ibs_signal"]
    assert attach_tap(eng) is tap                            # idempotent


def test_tap_is_not_put_over_a_recorder_already_in_use():
    eng = _ibs()
    eng.gates.enabled = True
    assert attach_tap(eng) is None and not isinstance(eng.gates, WatchTap)


# ── near signal ───────────────────────────────────────────────────────────────

def _rec(events, emitted=False):
    r = CandidateRecord("X", "M5", -1, None)
    r.events = [GateEvent(n, p, d) for n, p, d in events]
    r.emitted = emitted
    return r


def test_near_miss_needs_every_gate_but_one():
    chain = ["trend", "pullback", "volume_confirmation"]
    one = _rec([("trend", True, None), ("pullback", True, None), ("volume_confirmation", False, "rvol 0.56 < 0.8")])
    assert WatchService.near_miss(chain, one) == ("volume_confirmation", "rvol 0.56 < 0.8")
    two = _rec([("trend", True, None), ("pullback", False, None), ("volume_confirmation", False, None)])
    assert WatchService.near_miss(chain, two) is None
    early = _rec([("trend", False, None)])                   # short-circuited at the first gate
    assert WatchService.near_miss(chain, early) is None
    quiet = _rec([("in_session_window", False, None), ("trend", True, None), ("pullback", True, None)])
    assert WatchService.near_miss(["in_session_window", "trend", "pullback"], quiet) is None
    assert WatchService.near_miss(["only"], _rec([("only", False, None)])) is None


# ── the alert flow ────────────────────────────────────────────────────────────

def _candles(price=100.0, n=40):
    c = np.full(n, price)
    return pd.DataFrame({"open": c, "high": c + 1.0, "low": c - 1.0, "close": c})   # ATR 2.0


class _Eng:
    def __init__(self, levels):
        self.levels = levels
        self.gates = WatchTap()

    def watch(self, symbol, timeframe, candles):
        return self.levels


def _svc(tmp_path):
    return WatchService(chains_path=tmp_path / "chains.json")


def test_heads_up_once_then_withdrawn(tmp_path):
    svc = _svc(tmp_path)
    eng = _Eng([{"direction": "BUY", "level": 100.8, "cross": "above", "stop": 99.0, "note": "range 98-100.8"}])
    kw = dict(slot_id="s1", strategy_id="QL_IBS_v1", symbol="US_Tech_100", engine=eng, timeframe="M30",
              signal=None, instance="live")
    out = svc.evaluate(candles=_candles(100.0), now=1000.0, **kw)
    assert len(out) == 1 and "Heads-up" in out[0] and "BUY" in out[0] and "100.80" in out[0]
    assert "QL\\_IBS\\_v1" in out[0] and "US\\_Tech\\_100" in out[0]      # Markdown-safe names
    assert svc.evaluate(candles=_candles(100.0), now=1100.0, **kw) == []   # cooldown
    eng.levels = []                                                        # window over, no signal
    out = svc.evaluate(candles=_candles(100.0), now=1200.0, **kw)
    assert len(out) == 1 and "withdrawn" in out[0]


def test_far_level_is_quiet_and_a_signal_clears_the_heads_up(tmp_path):
    svc = _svc(tmp_path)
    eng = _Eng([{"direction": "SELL", "level": 95.0, "cross": "below"}])
    kw = dict(slot_id="s2", strategy_id="RQ_NYORB_v1", symbol="GOLD", engine=eng, timeframe="M15", instance="")
    assert svc.evaluate(candles=_candles(100.0), signal=None, now=0.0, **kw) == []      # 2.5 ATR away
    eng.levels = [{"direction": "SELL", "level": 99.5, "cross": "below"}]
    assert len(svc.evaluate(candles=_candles(100.0), signal=None, now=10.0, **kw)) == 1
    assert svc.evaluate(candles=_candles(100.0), signal=object(), now=20.0, **kw) == []
    assert svc.slots["s2"].headsup is None


def test_near_signal_alert_after_learning_the_chain(tmp_path):
    svc = _svc(tmp_path)
    eng = _Eng([])
    eng.gates.last_chain = ["trend", "volume_confirmation"]
    eng.gates.records = [_rec([("trend", True, None), ("volume_confirmation", False, "rvol 0.56")])]
    out = svc.evaluate(slot_id="s3", strategy_id="VWAP_v1", symbol="XAUUSD", engine=eng, candles=_candles(),
                       timeframe="M5", signal=None, now=0.0)
    assert len(out) == 1 and "Near signal" in out[0] and "volume confirmation" in out[0] and "rvol 0.56" in out[0]
    assert (tmp_path / "chains.json").exists()
    assert WatchService(chains_path=tmp_path / "chains.json").chains["VWAP_v1"] == ["trend", "volume_confirmation"]


def test_warm_up_learns_but_does_not_alert(tmp_path):
    svc = _svc(tmp_path)
    eng = _Eng([{"direction": "BUY", "level": 100.1, "cross": "above"}])
    eng.gates.last_chain = ["a", "b"]
    out = svc.evaluate(slot_id="s4", strategy_id="X", symbol="X", engine=eng, candles=_candles(), timeframe="M5",
                       signal=None, warming=True, now=0.0)
    assert out == [] and svc.chains["X"] == ["a", "b"]


def test_alerts_can_be_switched_off(tmp_path, monkeypatch):
    monkeypatch.setenv("WATCH_ALERTS", "0")
    svc = _svc(tmp_path)
    eng = _Eng([{"direction": "BUY", "level": 100.1, "cross": "above"}])
    assert svc.evaluate(slot_id="s", strategy_id="X", symbol="X", engine=eng, candles=_candles(), timeframe="M5",
                        signal=None, now=0.0) == []


# ── strategies' watch() ───────────────────────────────────────────────────────

def test_ibs_heads_up_half_an_hour_before_the_close():
    df = _ibs_sessions(DAYS)
    eng = _ibs()
    at_1500 = df.iloc[: 4 * 14 + 12].reset_index(drop=True)
    lv = eng.watch("US Tech 100", "M30", at_1500)
    assert len(lv) == 1 and lv[0]["direction"] == "BUY" and lv[0]["cross"] == "below"
    today = at_1500.iloc[4 * 14:]
    hi, lo = today["high"].max(), today["low"].min()
    assert lv[0]["level"] == pytest.approx(lo + 0.2 * (hi - lo))
    assert eng.watch("US Tech 100", "M30", df.iloc[: 4 * 14 + 10]) == []     # not the 15:00 bar
    assert eng.watch("US Tech 100", "M30", df.iloc[: 3 * 14 + 12]) == []     # day 3 does not qualify


def test_dipbuy_heads_up_names_its_reason():
    df = _ibs_sessions(DAYS)
    cfg = UserConfigV2()
    cfg.ql_dipbuy.sma_days = 3
    eng = get_strategy("QL_DipBuy_v1")(cfg)
    lv = eng.watch("US Tech 100", "M30", df.iloc[: 4 * 14 + 12].reset_index(drop=True))
    assert len(lv) == 1 and lv[0]["direction"] == "BUY" and "IBS" in lv[0]["note"]


def _bars(times_utc, o, h, lo, c):
    idx = pd.DatetimeIndex(times_utc)
    f = pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "tick_volume": np.full(len(idx), 100.0)}, index=idx)
    f["time"] = idx.as_unit("s").asi8
    return f


def _sigma_frame(seed=3):
    days = pd.bdate_range("2026-03-02", periods=6)
    times = pd.DatetimeIndex([pd.Timestamp(d) + pd.Timedelta(minutes=m) for d in days for m in range(0, 24 * 60, 30)])
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 0.3, len(times)))
    o = np.r_[close[0], close[:-1]]
    return times, o, close


def test_sigma_watch_bands_are_the_ones_the_next_bar_is_judged_against():
    checked = 0
    for seed in (3, 5, 11):
        times, o, close = _sigma_frame(seed)
        et = times.tz_localize("UTC").tz_convert(ET)
        hhmm = et.hour * 100 + et.minute
        last_day = et.normalize() == et.normalize()[-1]
        for i in np.flatnonzero(last_day & (hhmm >= 1000) & (hhmm < 1530))[::2]:
            f = _bars(times, o, np.maximum(o, close) + 0.05, np.minimum(o, close) - 0.05, close)
            levels = get_strategy("RQ_SigmaBreakout_v1")(UserConfigV2()).watch("NDX100", "M30", f.iloc[:i])
            for lv in levels:
                d, x = lv["direction"], lv["level"]
                s = 1 if d == "BUY" else -1
                for eps, want in ((0.01, True), (-0.01, False)):     # just through it / just short
                    eng = get_strategy("RQ_SigmaBreakout_v1")(UserConfigV2())
                    eng.watch("NDX100", "M30", f.iloc[:i])            # the alert runs first, as live
                    c2 = close.copy()
                    c2[i] = x + s * eps
                    f2 = _bars(times, o, np.maximum(o, c2) + 0.05, np.minimum(o, c2) - 0.05, c2)
                    sig = asyncio.run(eng.on_bar("NDX100", "M30", f2.iloc[:i + 1]))
                    got = sig is not None and sig.direction == d
                    assert got == want, f"seed {seed} bar {i}: {d} at {x:.3f}, close {c2[i]:.3f}"
                checked += 1
    assert checked >= 6



def test_tap_and_watch_change_no_signal_bar_by_bar():
    """Same strategy, same bars: with the tap attached and watch() called after
    every bar (as live does), every bar's signal is identical to a plain run."""
    times, o, close = _sigma_frame(7)
    f = _bars(times, o, np.maximum(o, close) + 0.05, np.minimum(o, close) - 0.05, close)
    for sid, tf in (("RQ_SigmaBreakout_v1", "M30"),):
        plain = get_strategy(sid)(UserConfigV2())
        live = get_strategy(sid)(UserConfigV2())
        attach_tap(live)
        n_sig = 0
        for i in range(60, len(f)):
            a = asyncio.run(plain.on_bar("NDX100", tf, f.iloc[:i]))
            b = asyncio.run(live.on_bar("NDX100", tf, f.iloc[:i]))
            live.watch("NDX100", tf, f.iloc[:i])
            assert (a is None) == (b is None), f"{sid} bar {i}"
            if a is not None:
                n_sig += 1
                assert (a.direction, a.entry_price, a.stop_loss, a.take_profit) == \
                       (b.direction, b.entry_price, b.stop_loss, b.take_profit)
                assert a.metadata == b.metadata
        if sid == "RQ_SigmaBreakout_v1":
            assert n_sig > 0
