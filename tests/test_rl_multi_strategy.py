"""
Mode B over several base strategies (2026-10-18).

One agent is offered every setup that ORB_v1 and TrendBreakout_v1 find on a
pair, and sees which of them fired recently and in which direction. What is
pinned here:

  * the confluence inputs are the same number in training and live, for the
    same history - computed by two functions from one definition;
  * a spec with several bases widens the observation by k + 1, and an old
    single-strategy spec keeps its content hash;
  * the env builds, slices and rolls out a multi-strategy episode at the width
    the spec promises;
  * the live engine runs the base strategies inside itself and finds the SAME
    setups on the SAME decision bars as training - the parity claim, measured.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from tests.test_rl_training_liveness import _tiny_frame

BASES = ("ORB_v1", "TrendBreakout_v1")


def _spec(**kw):
    from backend.rl.spec import AgentSpec
    base = dict(name="multi", symbols=("EURUSD",), timeframe="M5", iterations=1,
                eval_window_bars=1000, seeds=(1, 2, 3, 4, 5), max_cpcv_paths=8)
    base.update(kw)
    return AgentSpec(**base)


# -- the inputs ---------------------------------------------------------------


def test_live_flags_equal_training_flags_row_for_row():
    from backend.rl import confluence as cf

    rng = np.random.default_rng(7)
    n = 400
    by = {s: {int(r): {"direction": str(rng.choice(["BUY", "SELL"]))}
              for r in rng.choice(n, 25, replace=False)} for s in BASES}
    train = cf.setup_flags(by, BASES, n)
    latest: dict = {}
    for r in range(n):
        for s in BASES:
            if r in by[s]:
                latest[s] = (r, by[s][r]["direction"])
        assert np.allclose(cf.flags_now(latest, BASES, r), train[r]), r


def test_a_setup_fades_to_nothing_and_the_last_column_is_the_sum():
    from backend.rl import confluence as cf

    by = {"ORB_v1": {10: {"direction": "BUY"}}, "TrendBreakout_v1": {12: {"direction": "SELL"}}}
    f = cf.setup_flags(by, BASES, 60)
    assert f[9].tolist() == [0.0, 0.0, 0.0], "a flag before its setup is look-ahead"
    assert f[10, 0] == 1.0 and f[12, 1] == -1.0
    assert np.allclose(f[:, 2], f[:, 0] + f[:, 1])
    assert f[10 + cf.SETUP_DECAY_BARS, 0] == 0.0


def test_merge_offers_every_strategy_on_a_shared_row_in_spec_order():
    from backend.rl import confluence as cf

    m = cf.merge({"ORB_v1": {5: {"direction": "BUY"}},
                  "TrendBreakout_v1": {5: {"direction": "SELL"}, 9: {"direction": "BUY"}}})
    assert [s["strategy_name"] for s in m[5]] == list(BASES)
    assert len(m[9]) == 1


# -- the spec -----------------------------------------------------------------


def test_several_bases_widen_the_observation_by_k_plus_one():
    one = _spec(base_strategy="ORB_v1")
    many = _spec(base_strategies=BASES)
    assert many.mode == "B" and many.multi_base and not one.multi_base
    assert many.observation_width == one.observation_width + len(BASES) + 1
    assert many.base_label == "ORB_v1 + TrendBreakout_v1"


def test_an_old_single_strategy_spec_keeps_its_hash():
    s = _spec(base_strategy="ORB_v1")
    assert "base_strategies" not in s.to_dict()


def test_bad_base_lists_are_refused():
    with pytest.raises(ValueError):
        _spec(base_strategies=("ORB_v1", "ORB_v1"))
    with pytest.raises(ValueError):
        _spec(base_strategies=("ORB_v1", "NoSuch_v9"))


def test_the_api_stores_a_one_item_list_as_a_single_base():
    from backend.api.routes.rl import SpecIn

    one = SpecIn(name="x", symbols=["EURUSD"], base_strategies=["ORB_v1"]).to_spec()
    assert one.base_strategy == "ORB_v1" and not one.base_strategies
    many = SpecIn(name="x", symbols=["EURUSD"], base_strategies=list(BASES)).to_spec()
    assert many.base_strategies == BASES and many.base_strategy == ""


# -- training -----------------------------------------------------------------


@pytest.fixture(scope="module")
def episode():
    from backend.rl import train as T
    spec = _spec(base_strategies=BASES)
    return spec, T.build_episodes(spec, {"EURUSD": _tiny_frame(n=9000)})[0]


def test_the_episode_carries_setups_from_every_strategy(episode):
    spec, data = episode
    names = {s["strategy_name"] for sigs in data.base_multi.values() for s in sigs}
    assert names == set(BASES), names
    assert data.base_flags.shape == (len(data), len(BASES) + 1)
    sub = data.slice(1000, 5000)
    assert sub.base_flags.shape == (4000, len(BASES) + 1)
    for r, sigs in sub.base_multi.items():
        assert 0 <= r < 4000 and all(s["bar_index"] == r for s in sigs)


def test_a_multi_strategy_rollout_runs_at_the_promised_width(episode):
    from backend.rl import env as E
    from backend.rl import policy as pol
    from backend.rl import train as T

    spec, data = episode
    env = E.TradingEnv(spec, data, T.training_risk_config({}))
    env.fit_scaler(slice(0, 4000), label="rows 0-4000")
    assert env.Z.shape[1] == spec.observation_width
    p = pol.LinearPolicy(n_features=spec.observation_width, exit_head=True, seed=1,
                         n_stop=len(spec.stop_atr_choices), n_rr=len(spec.rr_choices))
    p = p.from_flat(np.random.default_rng(1).normal(0, 0.6, p.n_params))
    ep = env.rollout(p)
    for t in ep.trades:
        meta = ((t.get("original_signal") or {}).get("metadata") or {})
        assert meta.get("base_strategy") in BASES
    base = env.baseline_episode()
    assert base.n_signals == sum(len(v) for v in data.base_multi.values())


# -- live parity --------------------------------------------------------------


def _live_engine(monkeypatch, spec):
    from backend.rl import registry as rlreg
    from backend.rl import train as T
    from backend.strategies.strategy_rl import engine as rle

    agent = SimpleNamespace(
        spec=spec, director=None, charter=None,
        scaler=SimpleNamespace(transform=lambda X: np.asarray(X, dtype=float)),
        policy=SimpleNamespace(director_model=lambda: None,
                               act_entry=lambda z, m: (None, 0.0, None)))
    monkeypatch.setattr(rlreg, "cached", lambda _id: agent)
    # The market features are not what this measures (tests/test_rl_live_parity
    # does them); computing 25 of them on every bar made this test take minutes.
    monkeypatch.setattr(rle.feat, "matrix",
                        lambda c, names: np.zeros((len(c), len(names))))
    cls = type("RLTest", (rle.RLStrategy,), {"AGENT_ID": "t", "strategy_id": "RL::t@1"})
    eng = cls(T.strategy_config(spec, "EURUSD", {}, {}, BASES[0]))
    eng.is_backtesting = True
    return eng


def test_live_finds_the_same_setups_on_the_same_bars_as_training(monkeypatch, episode):
    """The parity claim: the RL engine, driven by `BarFeed` exactly as the
    Backtester and live drive it, must see each base strategy's setups on the
    decision bars training keyed them to, with the same confluence inputs."""
    from backend.rl import base_signals as bs
    from backend.strategies.bar_feed import BarFeed

    spec, data = episode
    eng = _live_engine(monkeypatch, spec)
    tfs = eng.get_required_timeframes()
    assert tfs[-1] == spec.timeframe
    frames = bs.derive_missing(bs.index_frames({spec.timeframe: data.frame}), tfs,
                               spec.timeframe)
    times = data.times
    live_setups: dict[int, list[str]] = {}
    live_flags: dict[int, np.ndarray] = {}
    real_run = eng._run_bases

    async def spy(symbol, decision_time):
        found = await real_run(symbol, decision_time)
        if found:
            live_setups[int(decision_time)] = [f["strategy_name"] for f in found]
        return found

    eng._run_bases = spy
    real_flags = eng._setup_flags

    def flag_spy(symbol, candles):
        out = real_flags(symbol, candles)
        live_flags[int(pd.Timestamp(candles.index[-1]).timestamp())] = out
        return out

    eng._setup_flags = flag_spy
    feed = BarFeed(eng, "EURUSD", tfs)
    feed.bind(frames)

    async def drive():
        for i in range(300, len(frames[spec.timeframe])):
            await feed.step(i)

    asyncio.run(drive())

    train_setups = {int(times[r]): [s["strategy_name"] for s in sigs]
                    for r, sigs in data.base_multi.items()}
    # Training skips each strategy's first 300 bars of ITS OWN primary
    # timeframe (`generate(start_index=300)`) - 900 M5 bars for ORB on M15 -
    # and live has no such warm-up, so live's earliest setups are not compared.
    from backend.strategies.bar_feed import primary_timeframe
    warm = max(int(pd.Timestamp(frames[primary_timeframe(e.get_required_timeframes())]
                                .index[300]).timestamp()) for e in eng.bases.values())
    live_setups = {t: v for t, v in live_setups.items() if t >= warm - 300}
    common = set(train_setups) & set(live_setups)
    assert train_setups, "training found no setups - the fixture is too short"
    assert set(train_setups) == set(live_setups), (
        f"live matched {len(common)} of {len(train_setups)} training setups; "
        f"live-only {sorted(set(live_setups) - set(train_setups))[:5]}, "
        f"training-only {sorted(set(train_setups) - set(live_setups))[:5]}")
    assert all(train_setups[t] == live_setups[t] for t in common)
    # Flags well after the start, where both have a full history.
    rows = [r for r in range(2000, len(data)) if int(times[r]) in live_flags]
    worst = max(float(np.max(np.abs(live_flags[int(times[r])] - data.base_flags[r])))
                for r in rows)
    assert worst < 1e-9, worst


# -- what each input contributes ----------------------------------------------


def test_contributions_cover_every_column_and_restore_the_observation(episode):
    from backend.rl import contributions as cb
    from backend.rl import env as E
    from backend.rl import policy as pol
    from backend.rl import train as T

    spec, data = episode
    sub = data.slice(0, 3000)
    env = E.TradingEnv(spec, sub, T.training_risk_config({}))
    env.fit_scaler(slice(0, 2000), label="rows 0-2000")
    p = pol.LinearPolicy(n_features=spec.observation_width, exit_head=True, seed=2,
                         n_stop=len(spec.stop_atr_choices), n_rr=len(spec.rr_choices))
    p = p.from_flat(np.random.default_rng(2).normal(0, 0.6, p.n_params))
    before = env.observations_for(p).copy()
    cols = cb.column_names(spec, sub.names)
    assert cols[-1] == "setup_agreement" and len(cols) == spec.observation_width
    out = cb.explain(env, p, spec, sub.names, only=["setup_agreement", cols[0]])
    assert {r["column"] for r in out["rows"]} == set(cols)
    measured = [r for r in out["rows"] if "r_contribution" in r]
    assert {r["column"] for r in measured} == {"setup_agreement", cols[0]}
    assert np.array_equal(env.observations_for(p), before), "a shuffle leaked"
