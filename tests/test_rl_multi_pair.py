"""
One agent, several pairs: each pair keeps ITS OWN policy and scaler (2026-10-18).

Before this, jobs trained per symbol but ONE best policy - with a scaler fitted
to that symbol's prices - was kept and run on every pair in the spec, so an
agent fitted to gold's scale traded EURUSD. Pinned here: training writes one
policy per pair, Collect stores them, the registry loads them, and the live
engine picks the right one for each symbol.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from tests.test_rl_governance import Site, a_charter, a_policy, a_scaler, a_spec, run
from tests.test_rl_training_liveness import _tiny_frame


def _res(symbol, score, seed):
    from backend.rl import train as T
    return T.SeedResult(seed=seed, fold=0, symbol=symbol, score=score,
                        policy={"seed": seed}, scaler={"s": seed})


def test_the_best_result_is_kept_per_symbol():
    from backend.rl import train as T
    per = T.best_per_symbol([_res("EURUSD", 0.1, 1), _res("GBPUSD", 0.5, 2),
                             _res("EURUSD", 0.3, 3), _res("GBPUSD", 0.2, 4)])
    assert per["EURUSD"].seed == 3 and per["GBPUSD"].seed == 2


def test_a_two_pair_run_writes_and_loads_one_policy_per_pair(tmp_path):
    from backend.rl import train as T
    from backend.rl.spec import AgentSpec

    spec = AgentSpec(name="pairs", symbols=("EURUSD", "GBPUSD"), timeframe="M5",
                     iterations=1, eval_window_bars=1000, seeds=(1, 2, 3, 4, 5),
                     max_cpcv_paths=8)
    gbp = _tiny_frame(n=4000, seed=9)
    gbp[["open", "high", "low", "close"]] *= 1.5     # a different price scale
    T.train_agent(spec, {"EURUSD": _tiny_frame(n=4000, seed=4), "GBPUSD": gbp},
                  {}, agent_id="pairs", root=tmp_path, workers=1)
    per = T.load_per_symbol("pairs", tmp_path)
    assert set(per) == {"EURUSD", "GBPUSD"}
    e_pol, e_sc = T.load_policy("pairs", tmp_path, symbol="EURUSD")
    g_pol, g_sc = T.load_policy("pairs", tmp_path, symbol="GBPUSD")
    # Each scaler was fitted on its own pair's bars.
    assert e_sc.to_dict() != g_sc.to_dict()


def test_the_registry_round_trips_per_pair_policies():
    from backend.rl import charter as ch
    from backend.rl import registry as rlreg

    async def go():
        async with Site() as site:
            spec = a_spec(symbols=("EURUSD", "GBPUSD"))
            main, sc = a_policy(spec), a_scaler(spec)
            other = a_policy(spec).from_flat(np.full(main.n_params, 0.01))
            async with site.Session() as s:
                row = await rlreg.save_agent(
                    s, agent_id="gov-1", spec=spec, policy=main, scaler=sc, trained_on=[],
                    status=rlreg.APPROVED,
                    per_symbol={"EURUSD": (main, sc), "GBPUSD": (other, sc)})
                assert row.policy_hash != main.content_hash, "hash must cover every pair"
                await rlreg.save_charter(s, a_charter(spec, ch.APPROVED))
                await s.commit()
            async with site.Session() as s:
                agent = await rlreg.load(s, "gov-1")
            assert agent.model_for("GBPUSD")[0].content_hash == other.content_hash
            assert agent.model_for("EURUSD")[0].content_hash == main.content_hash
            assert agent.model_for("USDJPY")[0] is agent.policy
    run(go())


def test_the_live_engine_uses_each_pairs_own_policy(monkeypatch):
    from backend.rl import registry as rlreg
    from backend.strategies.strategy_rl import engine as rle

    spec = a_spec(symbols=("EURUSD", "GBPUSD"))
    main, other = a_policy(spec), a_policy(spec).from_flat(np.zeros(a_policy(spec).n_params))
    sc_main, sc_other = a_scaler(spec), a_scaler(spec)
    agent = SimpleNamespace(
        spec=spec, policy=main, scaler=sc_main, charter=None, director=None,
        model_for=lambda sym: (other, sc_other) if sym == "GBPUSD" else (main, sc_main))
    monkeypatch.setattr(rlreg, "cached", lambda _id: agent)
    cls = type("RLPairs", (rle.RLStrategy,), {"AGENT_ID": "p", "strategy_id": "RL::p@1"})
    eng = cls(SimpleNamespace())
    assert eng._model("GBPUSD")[0] is other and eng._model("GBPUSD")[1] is sc_other
    assert eng._model("EURUSD")[0] is main
