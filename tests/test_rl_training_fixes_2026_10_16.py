"""
Regression tests for the RL training failures found on the VPS on 2026-10-16.

Every one of these shipped silently: the runs "finished", the screen said the
policy had learned to stay flat, and the real causes were a shape mismatch, a
missing argument, a swallowed exception and a log file nobody could rotate.
"""

import json
import math

import numpy as np
import pytest

from tests.test_rl_training_liveness import _tiny_frame


def _spec(**kw):
    from backend.rl.spec import AgentSpec
    base = dict(name="t", symbols=("EURUSD",), timeframe="M5", iterations=1,
                eval_window_bars=1000, seeds=(1, 2, 3, 4, 5), max_cpcv_paths=8)
    base.update(kw)
    return AgentSpec(**base)


# -- the director -------------------------------------------------------------


def test_a_director_policy_trains_and_carries_its_director():
    """`observation_width` counts the director's two columns. Nothing used to
    fit a director in the trainer, so the first rollout died on
    `matmul ... (size 27 is different from 25)` - every job, every run."""
    from backend.rl import env as E
    from backend.rl import train as T

    spec = _spec(director=True)
    data = T.build_episodes(spec, {"EURUSD": _tiny_frame()})[0]
    fold = E.cpcv_folds(len(data), spec.cpcv_groups, spec.cpcv_test_groups,
                        spec.embargo_bars)[0]
    policy, scaler, _ = T.train_ppo(spec, data, E.default_risk_config("EURUSD"), fold, 1)

    assert policy.director, "the trained policy does not carry its director"
    assert policy.n_features == spec.observation_width
    # A FRESH env - the gate, distill, a test fold - must pick the director up
    # from the policy rather than crash on the width.
    env = E.TradingEnv(spec, data.slice(*fold.test[0]), E.default_risk_config("EURUSD"),
                       scaler=scaler)
    env.rollout(policy)
    assert env.Z.shape[1] == spec.observation_width
    # ...and WITHOUT a rollout first, the path the gate's fidelity check takes.
    fresh = E.TradingEnv(spec, data, E.default_risk_config("EURUSD"), scaler=scaler)
    Z = fresh.observations_for(policy)
    assert Z.shape[1] == spec.observation_width
    policy.entry_probs(Z)


def test_a_director_policy_runs_under_its_charter():
    """With a charter attached - the gate, live adherence - the env named the
    25 market features against a 27-column row and raised. The gate failed on
    it for every director agent (end-to-end audit, 2026-10-16)."""
    from backend.rl import distill
    from backend.rl import env as E
    from backend.rl import train as T

    spec = _spec(director=True)
    data = T.build_episodes(spec, {"EURUSD": _tiny_frame()})[0]
    fold = E.cpcv_folds(len(data), spec.cpcv_groups, spec.cpcv_test_groups,
                        spec.embargo_bars)[0]
    rc = E.default_risk_config("EURUSD")
    policy, scaler, _ = T.train_ppo(spec, data, rc, fold, 1)
    env = E.TradingEnv(spec, data, rc, scaler=scaler)
    ep = env.rollout(policy, transitions=True)
    charter = distill.distil(policy, env.Z, data.names, spec, agent_id="t",
                             trade_bars=distill.trade_bars_from([ep]),
                             n_trades=ep.breakdown.n_trades,
                             mean_r=ep.breakdown.mean_r or None, scaler_note="x")
    sub = data.slice(*fold.test[0])
    E.TradingEnv(spec, sub, rc, scaler=scaler, charter=charter).rollout(policy)


def test_mode_b_evaluation_can_score():
    """ORB raises a handful of setups a day; the 10,000-bar evaluation slice
    held fewer than 20 of them, so every mode-B evaluation was -inf."""
    from backend.rl import env as E
    from backend.rl import train as T

    from backend.rl import features as F

    spec = _spec(base_strategy="ORB_v1", eval_window_bars=1000)
    data = T.build_episodes(spec, {"EURUSD": _tiny_frame(n=12000)})[0]
    fold = E.cpcv_folds(len(data), spec.cpcv_groups, spec.cpcv_test_groups,
                        spec.embargo_bars)[0]
    spans = [s for s in fold.train if s[1] - s[0] >= 500]
    longest = max(spans, key=lambda s: s[1] - s[0])
    in_longest = sum(1 for k in data.base_signals if longest[0] <= k < longest[1])
    in_fold = sum(1 for k in data.base_signals if any(a <= k < b for a, b in spans))
    # The old evaluation looked at one span; the setups are spread over many.
    assert in_fold > in_longest

    rc = E.default_risk_config("EURUSD")
    scaler = F.Scaler().fit(data.X[E.rows_of(fold.train)], data.names)
    calls = []
    real = E.TradingEnv.rollout

    def counting(self, policy, **kw):
        calls.append(len(self.data.base_signals or {}))
        return real(self, policy, **kw)

    import pytest as _pt
    mp = _pt.MonkeyPatch()
    mp.setattr(E.TradingEnv, "rollout", counting)
    try:
        from backend.rl import policy as pol
        p = pol.LinearPolicy(n_features=spec.observation_width, hidden=0,
                             exit_head=spec.exit_head, n_stop=5, n_rr=5)
        T._evaluate(p, spec, data, rc, fold, scaler, None, 1)
    finally:
        mp.undo()
    assert sum(calls) > in_longest, "mode-B evaluation still covers only one span's setups"


def test_a_policy_without_a_director_serialises_exactly_as_before():
    """The content hash of every existing mode-A policy must not move."""
    from backend.rl import policy as pol

    p = pol.LinearPolicy(n_features=4, exit_head=True)
    assert "director" not in p.to_dict()
    assert pol.LinearPolicy.from_dict(p.to_dict()).content_hash == p.content_hash


def test_the_director_survives_a_round_trip_through_json():
    from backend.rl import policy as pol

    p = pol.LinearPolicy(n_features=6)
    p.director = {"names": ["a"], "w": [0.1, 0.2, 0.3, 0.4], "b": 0.0, "horizon": 12,
                  "cost_r": 0.0, "fitted_on": "x", "n_train": 99, "train_accuracy": 0.5}
    q = pol.LinearPolicy.from_dict(json.loads(json.dumps(p.to_dict())))
    assert q.director == p.director
    assert q.director_model().fitted


# -- crashed jobs are reported as crashes ------------------------------------


def test_a_crashing_trainer_is_reported_not_called_flat(monkeypatch, tmp_path):
    from backend.rl import train as T

    def boom(*a, **kw):
        raise RuntimeError("matmul: shape mismatch")

    monkeypatch.setattr(T, "train_ppo", boom)
    run = T.train_agent(_spec(), {"EURUSD": _tiny_frame()}, {}, agent_id="crash",
                        root=tmp_path)
    assert not run.results
    assert len(run.job_errors) == 40
    assert "matmul" in run.error and "all 40 jobs failed" in run.error
    prog = T.read_progress("crash", tmp_path)
    assert prog["stage"] == "failed"
    texts = " ".join(e["text"] for e in prog["events"])
    assert "CRASHED" in texts
    assert "learned to stay flat" not in texts
    assert json.loads((tmp_path / "crash" / "training.json").read_text())["job_errors"]


def test_a_cancelled_run_says_cancelled(monkeypatch, tmp_path):
    from backend.rl import train as T

    real = T.run_fold_job
    calls = []

    def first_then_cancel(*a, **kw):
        calls.append(1)
        out = real(*a, **kw)
        T.request_cancel("cx", tmp_path)
        return out

    monkeypatch.setattr(T, "run_fold_job", first_then_cancel)
    run = T.train_agent(_spec(), {"EURUSD": _tiny_frame()}, {}, agent_id="cx",
                        root=tmp_path)
    assert run.error == "cancelled"
    assert len(calls) == 1
    prog = T.read_progress("cx", tmp_path)
    assert prog["stage"] == "cancelled"
    assert prog["jobs_done"] == 1
    assert not prog.get("stale")


def test_the_job_progress_object_is_not_replaced(monkeypatch, tmp_path):
    """`_run_job` writes "starting" and "bars loaded"; `train_agent` used to
    build its own Progress on the same file and erase them."""
    from backend.rl import train as T

    monkeypatch.setattr(T, "ARTEFACT_ROOT", tmp_path)
    monkeypatch.setattr(T, "train_ppo", lambda *a, **kw: (_ for _ in ()).throw(
        RuntimeError("stop")))
    import backend.rl.datasets as ds
    monkeypatch.setattr(ds, "load_frames", lambda d, spec: {"EURUSD": _tiny_frame()})
    payload = T.job_payload(_spec(), {"EURUSD": "mt5"}, {}, agent_id="pp")
    payload["workers"] = 1
    T._run_job(payload)
    texts = [e["text"] for e in T.read_progress("pp", tmp_path)["events"]]
    assert any(t.startswith("starting") for t in texts), texts[:3]


def test_an_episode_reports_its_coverage_like_a_dataset():
    """The distil route stores `data.coverage()`; EpisodeData had no such
    method, so every Distil died with AttributeError after loading bars."""
    from backend.rl import train as T
    from backend.rl.datasets import content_hash

    frame = _tiny_frame(n=2000)
    data = T.build_episodes(_spec(), {"EURUSD": frame})[0]
    cov = data.coverage()
    assert cov["symbol"] == "EURUSD" and cov["timeframe"] == "M5"
    assert cov["start"] == int(frame["time"].iloc[0]) and cov["end"] == int(frame["time"].iloc[-1])
    assert cov["hash"] == content_hash(data.frame)


# -- mode B -------------------------------------------------------------------


def test_mode_b_episodes_carry_the_base_strategys_setups():
    """`train_agent` built every episode without `base_strategy`, so every
    mode-B agent saw 0 setups on every symbol."""
    from backend.rl import train as T

    spec = _spec(base_strategy="ORB_v1")
    data = T.build_episodes(spec, {"EURUSD": _tiny_frame(n=12000)})[0]
    assert data.base_signals, "no base signals generated for a mode-B agent"


def test_base_signals_index_the_agents_bars_not_the_strategys():
    """ORB_v1 decides on M15; the agent runs on M5. Keying by the strategy's
    bar index put every setup on the wrong M5 bar."""
    from backend.rl import train as T

    spec = _spec(base_strategy="ORB_v1")
    data = T.build_episodes(spec, {"EURUSD": _tiny_frame(n=12000)})[0]
    times = data.times
    for j, sig in data.base_signals.items():
        assert 0 <= j < len(times) - 1
        # Keyed by the DECISION row, which is the bar the signal is stamped
        # with - the agent fills at bar j+1's open, the bar its base strategy
        # fills on. Keying one bar later made mode B fill one bar late.
        assert int(times[j]) == int(sig["time"]), (j, sig["time"])


# -- training sees its own decisions, the test fold sees the real limits ------


def test_training_rollouts_drop_only_the_account_brakes():
    from backend.rl import train as T

    rc = {"max_daily_trades": 5, "max_daily_drawdown_pct": 3.0,
          "max_weekly_drawdown_pct": 6.0, "min_rr": 3.0, "risk_per_trade_pct": 1.0}
    out = T.training_risk_config(rc)
    assert out["max_daily_trades"] == 0
    assert out["max_daily_drawdown_pct"] == 0 and out["max_weekly_drawdown_pct"] == 0
    assert out["min_rr"] == 3.0 and out["risk_per_trade_pct"] == 1.0
    assert rc["max_daily_trades"] == 5, "the caller's config was mutated"


# -- speed ------------------------------------------------------------------


def test_softmax_rows_matches_the_row_by_row_version():
    from backend.rl.policy import softmax, softmax_rows

    X = np.random.default_rng(3).normal(0, 50, (200, 5))
    assert np.allclose(softmax_rows(X), np.apply_along_axis(softmax, 1, X))


def test_grouping_without_chart_data_keeps_every_number():
    from backend.utils.trade_grouper import group_trades

    import pandas as pd
    t0 = 1735689600
    candles = pd.DataFrame({"time": [t0 + 300 * i for i in range(50)],
                            "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0})
    trades = [{"group_id": "g1", "symbol": "X", "direction": "BUY", "entry_price": 1.0,
               "entry_time": t0 + 3000, "exit_time": t0 + 6000, "exit_price": 1.05,
               "stop_loss": 0.95, "pnl": 5.0, "volume": 1.0}]
    full = group_trades([dict(t) for t in trades], candles)[0]
    lean = group_trades([dict(t) for t in trades], candles, chart_data=False)[0]
    assert full.get("chart_data")
    assert not lean.get("chart_data")
    for k, v in full.items():
        if not k.startswith(("chart_data", "smc_data")):
            assert lean.get(k) == v, k


# -- logging ------------------------------------------------------------------


def test_a_worker_never_holds_the_shared_log_files(tmp_path):
    """On Windows a file another process holds open cannot be renamed, so a
    worker holding backend.log/trades.log broke rotation for good."""
    import subprocess
    import sys

    code = (
        "from backend.utils.logger import logger, configure_worker\n"
        "configure_worker('probe')\n"
        "names = [str(getattr(h._sink, '_path', '')) for h in logger._core.handlers.values()]\n"
        "print('SHARED' if any(n.endswith(('backend.log','trades.log')) for n in names) else 'OK')\n")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=str(__import__("pathlib").Path(__file__).parent.parent),
                         timeout=120)
    assert out.stdout.strip().endswith("OK"), out.stdout + out.stderr


def test_importing_the_logger_does_not_open_the_shared_files():
    """A spawned worker re-imports the logger before `configure_worker` runs.
    If the shared sinks opened their files at import, a rotation in the API
    process failed during that window (seen 2026-10-17)."""
    import subprocess
    import sys

    code = (
        "from backend.utils.logger import logger\n"
        "open_ = [str(getattr(h._sink, '_path', '')) for h in logger._core.handlers.values()\n"
        "         if getattr(h._sink, '_file', None) is not None]\n"
        "print('OPEN' if any(p.endswith(('backend.log','trades.log')) for p in open_) else 'CLOSED')\n")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=str(__import__("pathlib").Path(__file__).parent.parent),
                         timeout=120)
    assert out.stdout.strip().endswith("CLOSED"), out.stdout + out.stderr


def test_the_api_process_still_writes_the_shared_files():
    from backend.utils.logger import IS_WORKER, logger

    assert not IS_WORKER
    names = [str(getattr(h._sink, "_path", "")) for h in logger._core.handlers.values()]
    assert any(n.endswith("backend.log") for n in names)


# -- export -------------------------------------------------------------------


def test_the_export_survives_an_unscoreable_iteration():
    """History holds -inf for an evaluation with too few trades; a JSONResponse
    refuses it, and the export 500'd (VPS log, 2026-10-16)."""
    from fastapi.responses import JSONResponse

    from backend.api.routes.rl import _json_safe

    payload = {"training": {"results": [{"history": [{"eval": -math.inf},
                                                     {"eval": 0.25},
                                                     {"x": math.nan}]}]}}
    safe = _json_safe(payload)
    JSONResponse(safe)                                  # must not raise
    h = safe["training"]["results"][0]["history"]
    assert h[0]["eval"] == "-inf" and h[1]["eval"] == 0.25 and h[2]["x"] == "nan"


def test_every_rl_route_survives_non_finite_numbers():
    """A collected agent's training history holds -inf; GET /agents/{id} -
    what the Agents page polls - 500'd on it, so the page never loaded again."""
    from backend.api.routes import rl as R

    assert R.router.default_response_class is R._SafeJSONResponse
    body = json.loads(R._SafeJSONResponse({"history": [{"eval": -math.inf}]}).body)
    assert body["history"][0]["eval"] == "-inf"


# -- capacity -----------------------------------------------------------------


def test_capacity_reserves_cores_and_splits_between_agents(monkeypatch):
    from backend.rl import train as T

    monkeypatch.setattr(T.os, "cpu_count", lambda: 8)
    monkeypatch.delenv("RL_JOB_WORKERS", raising=False)
    monkeypatch.delenv("RL_MAX_AGENTS", raising=False)
    monkeypatch.delenv("RL_RESERVED_CORES", raising=False)
    assert T.capacity()["job_workers_per_agent"] == 6
    monkeypatch.setenv("RL_MAX_AGENTS", "2")
    assert T.capacity()["job_workers_per_agent"] == 3
    monkeypatch.setenv("RL_JOB_WORKERS", "1")
    assert T.capacity()["job_workers_per_agent"] == 1
    monkeypatch.setattr(T.os, "cpu_count", lambda: 2)
    monkeypatch.delenv("RL_JOB_WORKERS")
    monkeypatch.delenv("RL_MAX_AGENTS")
    assert T.capacity()["job_workers_per_agent"] == 1


@pytest.mark.slow
def test_parallel_jobs_give_the_same_run_as_sequential(tmp_path):
    from backend.rl import train as T

    spec = _spec(director=True)
    seq = T.train_agent(spec, {"EURUSD": _tiny_frame(n=4000)}, {}, agent_id="s",
                        root=tmp_path, workers=1)
    par = T.train_agent(spec, {"EURUSD": _tiny_frame(n=4000)}, {}, agent_id="p",
                        root=tmp_path, workers=3)
    key = lambda r: (r.symbol, r.seed, r.fold, round(r.score, 9), r.test_trades)  # noqa: E731
    assert [key(r) for r in seq.results] == [key(r) for r in par.results]
    assert not (tmp_path / "p" / "jobs").exists(), "job shards were left behind"
