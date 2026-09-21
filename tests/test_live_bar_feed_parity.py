"""
Live must hand every strategy engine the same bars, in the same order, as a
backtest over the same data — however often the bot scans, however long a scan
takes, and whether or not the bot was offline for a while.

The backtest side is the routes' loop (BarFeed.step over every primary bar); the
live side is LiveBarFeed.advance driven by a simulated scan clock. Both run a
fresh engine of every registered strategy and record each on_bar call.
"""

import asyncio

import numpy as np
import pandas as pd
import pytest

from backend.core.config_schema import UserConfigV2
from backend.strategies.bar_feed import FIRST_STEP_INDEX, BarFeed, LiveBarFeed
from backend.strategies.registry import get_strategy, list_strategies
from backend.strategies.windows import WARMUP_BASE_DAYS

N_BARS = 1500
T0 = pd.Timestamp("2026-03-02 00:00")  # a Monday


def _bars(seed: int = 7) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    steps = rng.normal(0, 1.0, N_BARS)
    steps[rng.random(N_BARS) < 0.01] -= 25  # occasional spikes
    close = 5000 + np.cumsum(steps)
    open_ = np.r_[close[0], close[:-1]]
    wick = np.abs(rng.normal(0, 0.8, N_BARS))
    idx = pd.date_range(T0, periods=N_BARS, freq="5min")
    m5 = pd.DataFrame({"open": open_, "high": np.maximum(open_, close) + wick,
                       "low": np.minimum(open_, close) - wick, "close": close,
                       "tick_volume": rng.integers(50, 500, N_BARS).astype(float),
                       "spread": np.full(N_BARS, 30.0)}, index=idx)
    out = {"M5": m5}
    for tf, rule in (("M15", "15min"), ("H1", "1h"), ("H4", "4h"), ("D1", "1D")):
        r = m5.resample(rule, label="left", closed="left")
        out[tf] = pd.DataFrame({"open": r["open"].first(), "high": r["high"].max(), "low": r["low"].min(),
                                "close": r["close"].last(), "tick_volume": r["tick_volume"].sum(),
                                "spread": r["spread"].last()}).dropna()
    return out


SYMBOL_FOR = {"DriftJumpAlpha_v1": "Crash 1000 Index", "BoomDriftJump_v1": "Boom 1000 Index",
              "SpikeFade_v1": "Crash 500 Index", "RangeRevert_v1": "Crash 500 Index",
              "RangeBreakout_v1": "Volatility 75 Index", "TrendDrift_v1": "Volatility 75 Index"}


def _recording_engine(sid: str, calls: list):
    eng = get_strategy(sid)(UserConfigV2())
    real = eng.on_bar

    async def on_bar(symbol, tf, candles):
        calls.append((tf, candles.index[0], candles.index[-1], len(candles)))
        return await real(symbol, tf, candles)

    eng.on_bar = on_bar
    return eng


def _key(sig):
    return (sig.direction, round(float(sig.entry_price), 6), round(float(sig.stop_loss), 6))


async def _backtest(sid: str, frames: dict):
    calls: list = []
    eng = _recording_engine(sid, calls)
    feed = BarFeed(eng, SYMBOL_FOR.get(sid, "EURUSD"))
    feed.bind({tf: frames[tf] for tf in feed.timeframes})
    times = frames[feed.primary].index
    signals = {}
    for i in range(FIRST_STEP_INDEX, len(times)):
        s = await feed.step(i)
        if s:
            signals[times[i]] = _key(s)
    return calls, signals


def _scan_clock(primary_minutes: int, start: pd.Timestamp, end: pd.Timestamp):
    """60 s scans with jitter, one 45-minute outage and one 7-minute slow cycle."""
    rng = np.random.default_rng(11)
    t, out = start + pd.Timedelta(seconds=3), []
    outage = (start + pd.Timedelta(hours=20), start + pd.Timedelta(hours=20, minutes=45))
    slow = (start + pd.Timedelta(hours=31), start + pd.Timedelta(hours=31, minutes=7))
    while t < end:
        if not (outage[0] <= t < outage[1]) and not (slow[0] <= t < slow[1]):
            out.append(t)
        t += pd.Timedelta(seconds=60 + int(rng.integers(0, 12)))
    return out, (outage, slow)


async def _live(sid: str, frames: dict):
    calls: list = []
    eng = _recording_engine(sid, calls)
    feed = LiveBarFeed(eng, SYMBOL_FOR.get(sid, "EURUSD"))
    primary = frames[feed.primary]
    start = primary.index[FIRST_STEP_INDEX]
    scans, windows = _scan_clock(int(feed.primary[1:]) if feed.primary[0] == "M" else 60, start, primary.index[-1])
    acted, missed = {}, {}
    for t in scans:
        visible = {tf: frames[tf].loc[:t] for tf in feed.timeframes}  # last primary row = the forming bar
        res = await feed.advance(visible, prime_days=10_000)
        assert not res.history_gap
        if res.signal:
            acted[visible[feed.primary].index[-1]] = _key(res.signal)
        for bar_time, s in res.missed:
            missed[pd.Timestamp(bar_time)] = _key(s)
    return calls, acted, missed, windows


@pytest.mark.parametrize("sid", sorted(list_strategies()))
def test_live_feeds_every_engine_exactly_the_backtest_sequence(sid):
    frames = _bars()
    bt_calls, bt_signals = asyncio.run(_backtest(sid, frames))
    live_calls, acted, missed, _ = asyncio.run(_live(sid, frames))

    # The last primary bar is never stepped live (no scan after it forms), so
    # compare over the bars live reached.
    reached = max(c[2] for c in live_calls) if live_calls else None
    bt_upto = [c for c in bt_calls if reached is None or c[2] <= reached]
    assert live_calls == bt_upto, f"{sid}: live on_bar sequence differs from the backtest's"

    live_all = {**acted, **missed}
    bt_upto_sigs = {t: k for t, k in bt_signals.items() if t in live_all or t <= pd.Timestamp(reached) + pd.Timedelta(minutes=5)}
    assert live_all == bt_upto_sigs, f"{sid}: live found different signals than the backtest"


def test_a_missed_bar_is_reported_not_traded_late():
    """Bars that closed while the bot was offline are replayed in order, but a
    signal on one of them is reported as missed — only the newest bar is traded."""
    frames = _bars()

    class Every10th:
        def __init__(self):
            self.n = 0

        def get_required_timeframes(self):
            return ["M5"]

        async def on_bar(self, symbol, tf, candles):
            self.n += 1
            return type("S", (), {"direction": "BUY", "entry_price": 1.0, "stop_loss": 0.5})() \
                if candles.index[-1].minute % 50 == 0 else None

    eng = Every10th()
    feed = LiveBarFeed(eng, "X")
    idx = frames["M5"].index
    r1 = asyncio.run(feed.advance({"M5": frames["M5"].loc[:idx[FIRST_STEP_INDEX]]}, prime_days=10_000))
    assert r1.primed and r1.stepped == 1
    # offline for 12 bars
    r2 = asyncio.run(feed.advance({"M5": frames["M5"].loc[:idx[FIRST_STEP_INDEX + 12]]}))
    assert r2.stepped == 12 and not r2.primed
    assert all(pd.Timestamp(t) < idx[FIRST_STEP_INDEX + 12] for t, _ in r2.missed)
    # a re-scan inside the same bar feeds nothing
    r3 = asyncio.run(feed.advance({"M5": frames["M5"].loc[:idx[FIRST_STEP_INDEX + 12]]}))
    assert r3.stepped == 0 and r3.signal is None and eng.n == 13


def test_history_gap_is_flagged():
    frames = _bars()
    idx = frames["M5"].index

    class Quiet:
        def get_required_timeframes(self):
            return ["M5"]

        async def on_bar(self, symbol, tf, candles):
            return None

    feed = LiveBarFeed(Quiet(), "X")
    asyncio.run(feed.advance({"M5": frames["M5"].loc[:idx[400]]}, prime_days=10_000))
    later = frames["M5"].loc[idx[900]:idx[1200]]  # fetched history no longer reaches bar 400
    assert asyncio.run(feed.advance({"M5": later})).history_gap


def test_live_fetches_only_what_the_next_advance_needs():
    from backend.strategies.bar_feed import FETCH_MARGIN_BARS
    from backend.strategies.windows import warmup_days, window_bars

    class M5Only:
        def get_required_timeframes(self):
            return ["M5"]

        async def on_bar(self, symbol, tf, candles):
            return None

    eng = M5Only()
    feed = LiveBarFeed(eng, "X")
    fresh = feed.fetch_count("M5")
    span = int(warmup_days("M5", eng, WARMUP_BASE_DAYS["M5"]) * 86400 // 300)
    assert fresh >= span + window_bars("M5", eng) + FETCH_MARGIN_BARS
    assert fresh < 5000

    frames = _bars()
    idx = frames["M5"].index
    asyncio.run(feed.advance({"M5": frames["M5"].loc[:idx[900]]}))
    now = idx[900].timestamp() + 3 * 300 + 30  # three bars later
    steady = feed.fetch_count("M5", now_s=now)
    assert window_bars("M5", eng) + 3 <= steady <= window_bars("M5", eng) + 3 + FETCH_MARGIN_BARS + 250
    # enough history for the next advance to connect to the last stepped bar
    later = frames["M5"].loc[:idx[903]].iloc[-steady:]
    assert not asyncio.run(feed.advance({"M5": later})).history_gap


def test_every_path_uses_the_shared_feeder():
    """Four hand-copied loops drifted once; they must not come back."""
    import inspect
    from pathlib import Path

    from backend.api.routes import backtest as bt
    from backend.services import bot_service as bs

    route = inspect.getsource(bt)
    assert route.count("_feed = BarFeed(") == 2, "single and portfolio routes must both use BarFeed"
    assert route.count("sig = await _feed.step(i)") == 2
    assert "await engine.on_bar(" not in route and "await strategy_engine.on_bar(" not in route
    for tf in WARMUP_BASE_DAYS:
        assert f'"warmup_days": WARMUP_BASE_DAYS["{tf}"]' in route

    live = inspect.getsource(bs)
    assert "LiveBarFeed(current_engine, symbol, req_tfs)" in live
    assert "current_engine.on_bar(" not in live, "live must not feed the engine outside the feeder"

    script = Path("scripts/run_app_backtest.py").read_text(encoding="utf-8")
    assert "BarFeed(engine, req.symbol, required)" in script and "engine.on_bar(" not in script
