"""
Agent backtests compute features once (`RLStrategy.prepare_backtest`), not
once per bar. The per-bar path cost ~80 ms of `feat.matrix` per bar - agent
backtests sat at "15%" for hours (2026-10-19). Pinned here: the fast path makes
the SAME observations and decisions as the per-bar path, and is much faster.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import numpy as np
import pandas as pd

from tests.test_rl_training_liveness import _tiny_frame


def _engine(monkeypatch):
    from backend.rl import features as feat
    from backend.rl import policy as pol
    from backend.rl import registry as rlreg
    from backend.rl.spec import AgentSpec
    from backend.strategies.strategy_rl import engine as rle

    spec = AgentSpec(name="speed", symbols=("EURUSD",), timeframe="M5")
    frame = _tiny_frame(n=2600)
    idx = frame.set_index(pd.to_datetime(frame["time"], unit="s"))
    scaler = feat.Scaler().fit(feat.matrix(idx, spec.features)[:1500], spec.features)
    p = pol.LinearPolicy(n_features=spec.observation_width, exit_head=True, seed=3,
                         n_stop=len(spec.stop_atr_choices), n_rr=len(spec.rr_choices))
    p = p.from_flat(np.zeros(p.n_params))      # never trades; only observations matter here
    agent = SimpleNamespace(spec=spec, policy=p, scaler=scaler, charter=None, director=None)
    monkeypatch.setattr(rlreg, "cached", lambda _id: agent)
    cls = type("RLSpeed", (rle.RLStrategy,), {"AGENT_ID": "s", "strategy_id": "RL::s@1"})
    eng = cls(SimpleNamespace())
    eng.is_backtesting = True
    return eng, idx


def _observations(eng, idx, *, fast: bool):
    from backend.strategies.bar_feed import BarFeed

    seen = []
    real = eng._observe

    def spy(candles, symbol=""):
        out = real(candles, symbol)
        if out is not None:
            seen.append((int(pd.Timestamp(candles.index[-1]).timestamp()), out[0].copy()))
        return out

    eng._observe = spy
    eng._feature_cache = {}
    if not fast:
        eng.prepare_backtest = lambda *a, **k: None           # the old per-bar path
    feed = BarFeed(eng, "EURUSD", ["M5"])
    feed.bind({"M5": idx})
    t0 = time.perf_counter()

    async def drive():
        for i in range(1300, 1700):
            await feed.step(i)

    asyncio.run(drive())
    return dict(seen), time.perf_counter() - t0


def test_the_fast_path_sees_the_same_observations_and_is_faster(monkeypatch):
    eng, idx = _engine(monkeypatch)
    fast, t_fast = _observations(eng, idx, fast=True)
    eng2, _ = _engine(monkeypatch)
    slow, t_slow = _observations(eng2, idx, fast=False)
    assert fast.keys() == slow.keys() and len(fast) > 300
    worst = max(float(np.max(np.abs(fast[k] - slow[k]))) for k in fast)
    assert worst < 1e-6, worst
    assert t_fast * 5 < t_slow, (t_fast, t_slow)


def test_live_never_takes_the_backtest_path(monkeypatch):
    from backend.strategies.bar_feed import LiveBarFeed

    eng, idx = _engine(monkeypatch)
    called = []
    eng.prepare_backtest = lambda *a, **k: called.append(1)
    LiveBarFeed(eng, "EURUSD", ["M5"]).bind({"M5": idx})
    assert not called
