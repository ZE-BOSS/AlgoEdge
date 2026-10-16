"""
tests/test_rl_training_liveness.py

"It says it is training. Is it?"

THE BUG THIS EXISTS FOR
-----------------------
`POST /agents/{id}/train` writes `status = "TRAINING"` to the database, and
the only thing that ever writes it back is `/collect`, which an admin presses
by hand after the run finishes. The worker itself is a `ProcessPoolExecutor`
child of the API process.

So restarting the backend - a deploy, a crash, a reboot - ends the run and
leaves the row saying TRAINING with nothing running. The Agents screen then
disables every control, shows a spinner, and reports the percentage the run
had reached hours earlier, forever. There was no button that cleared it and
no way to tell that state apart from a job that is merely slow.

WHY THE PROGRESS FILE'S MTIME IS THE ANSWER
-------------------------------------------
Whether the orphaned child died or kept running is not knowable from inside
the new process: process managers kill process trees on some platforms and not
others, and the future that used to track it died with the old interpreter.
What IS knowable is whether anything is still writing to the run's progress
file. A number that keeps rising means something is alive; one that stopped
means nothing is, whichever way the child went.
"""

from __future__ import annotations

import json
import os
import pathlib
import tempfile
import time

import pytest

from backend.rl import train as T


@pytest.fixture()
def run_dir():
    root = pathlib.Path(tempfile.mkdtemp(prefix="rl-liveness-"))
    (root / "ag").mkdir()
    return root


def write(root, state, age_s=0.0):
    f = root / "ag" / "progress.json"
    f.write_text(json.dumps(state), encoding="utf-8")
    if age_s:
        when = time.time() - age_s
        os.utime(f, (when, when))
    return f


def test_a_fresh_run_is_not_stale(run_dir):
    write(run_dir, {"stage": "training", "pct": 2.6})
    p = T.read_progress("ag", root=run_dir)
    assert p["age_s"] < 5
    assert p["stale"] is False


def test_a_run_that_stopped_writing_is_stale(run_dir):
    """The restart case, which is the whole point of the file."""
    write(run_dir, {"stage": "training", "pct": 2.6, "jobs_done": 2}, age_s=7200)
    p = T.read_progress("ag", root=run_dir)
    assert p["age_s"] == pytest.approx(7200, abs=30)
    assert p["stale"] is True, (
        "a run that has not written for two hours is not running, and saying "
        "otherwise is what left the screen spinning at 2.6% overnight")


def test_a_finished_run_is_never_stale_however_old(run_dir):
    """`complete` is a destination, not a silence.

    Without this a successful run from last week would be reported as a
    stuck one every time its agent was opened.
    """
    write(run_dir, {"stage": "complete", "pct": 100.0}, age_s=7 * 86400)
    assert T.read_progress("ag", root=run_dir)["stale"] is False


def test_the_staleness_threshold_is_several_times_the_slowest_step():
    """A threshold under a minute would cry wolf on a big fold.

    One candidate evaluation over a full fold is tens of seconds on the
    largest frames this project uses, and the progress file is written once
    per iteration.
    """
    assert T.STALE_AFTER_S >= 120


def test_a_missing_progress_file_is_empty_not_an_error(run_dir):
    assert T.read_progress("no-such-agent", root=run_dir) == {}


# -- what the queue admits it does not know --------------------------------


def test_the_queue_does_not_claim_a_job_it_never_submitted():
    """After a restart every future is gone, and the API must say so.

    The failure to avoid is the opposite one: reporting "not running" as
    though it were a finding, when it only means this process did not start
    it.
    """
    st = T.queue.status("never-submitted-anywhere")
    assert st["state"] == "unknown"
    assert st["owned_by_this_process"] is False
    assert st["looks_alive"] is False


def test_looks_alive_follows_the_file_when_the_future_is_gone(tmp_path, monkeypatch):
    """The case a restart actually produces: no future, but a live file.

    An orphaned child that survived the restart keeps writing. The API has no
    future for it and must not call it dead on that basis alone.
    """
    monkeypatch.setattr(T, "ARTEFACT_ROOT", tmp_path)
    (tmp_path / "orphan").mkdir()
    (tmp_path / "orphan" / "progress.json").write_text(
        json.dumps({"stage": "training", "pct": 40.0}), encoding="utf-8")

    st = T.queue.status("orphan")
    assert st["owned_by_this_process"] is False
    assert st["looks_alive"] is True, (
        "a child that outlived the restart is still training; calling it dead "
        "would invite a second run over the same files")


def test_a_stale_file_with_no_future_is_dead(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "ARTEFACT_ROOT", tmp_path)
    (tmp_path / "dead").mkdir()
    f = tmp_path / "dead" / "progress.json"
    f.write_text(json.dumps({"stage": "training", "pct": 2.6}), encoding="utf-8")
    when = time.time() - 7200
    os.utime(f, (when, when))

    assert T.queue.status("dead")["looks_alive"] is False


# -- the reset, over HTTP ---------------------------------------------------


@pytest.mark.asyncio
async def test_reset_refuses_while_the_run_still_looks_alive(tmp_path, monkeypatch):
    """It must not become a way to stomp on a job that is merely slow."""
    import httpx
    from fastapi import FastAPI

    from backend.api.deps import require_admin
    from backend.api.routes import rl as api

    monkeypatch.setattr(T, "ARTEFACT_ROOT", tmp_path)
    (tmp_path / "live-one").mkdir()
    (tmp_path / "live-one" / "progress.json").write_text(
        json.dumps({"stage": "training", "pct": 12.0}), encoding="utf-8")

    class Row:
        agent_id, status = "live-one", "TRAINING"

    async def fake_get_agent(_db, agent_id):
        return Row()

    monkeypatch.setattr(api.rlreg, "get_agent", fake_get_agent)

    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[require_admin] = lambda: {"id": 1, "role": "admin"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        r = await client.post("/api/rl/agents/live-one/reset-training")

    assert r.status_code == 409
    assert "still looks alive" in r.json()["detail"]


# -- what Collect can still recover after the process that ran it died -----


def test_the_run_record_is_recoverable_from_disk(run_dir):
    """`/collect` must not overwrite a good run with an empty record.

    The worker writes `training.json` beside the policy; `/collect` used to
    read only `queue.result()`, which is `None` once the future's process is
    gone. Collecting after a restart therefore stored `{}` as the training
    record — losing the per-fold table and the seed dispersion, and leaving
    the gate's PBO and seed-agreement checks with nothing to read. The run
    looked fine right up until the gate could not explain itself.
    """
    (run_dir / "ag" / "training.json").write_text(json.dumps({
        "results": [{"seed": 1, "fold": 0, "test_mean_r": 0.061, "symbol": "Crash 1000 Index"},
                    {"seed": 2, "fold": 0, "test_mean_r": -0.004, "symbol": "Crash 1000 Index"}],
        "sharpe_dispersion": 0.42,
        "best": {"seed": 1, "fold": 0},
    }), encoding="utf-8")

    got = T.load_training("ag", root=run_dir)
    assert len(got["results"]) == 2
    assert got["sharpe_dispersion"] == 0.42
    assert got["best"]["seed"] == 1


def test_a_missing_run_record_is_empty_not_an_error(run_dir):
    assert T.load_training("never-ran", root=run_dir) == {}


# -- the four phases, and the gate narrating itself ------------------------


def test_each_phase_writes_its_own_file(run_dir):
    """Separate files, deliberately.

    The training worker is a child process and the gate runs in the API, so
    one shared file would have two processes writing it and a crash
    mid-write could take the training record down with the gate's.
    """
    names = {n: T.phase_path("ag", n, run_dir).name for n in T.PHASES}
    assert names["train"] == "progress.json", (
        "training keeps its original filename, or every agent trained before "
        "today loses its record")
    assert len(set(names.values())) == len(T.PHASES), "two phases share a file"


def test_the_pipeline_names_the_one_phase_that_is_running(run_dir):
    T.phase_progress("ag", "train", run_dir).set(stage="complete", pct=100.0)
    T.phase_progress("ag", "collect", run_dir).set(stage="complete", pct=100.0)
    T.phase_progress("ag", "validate", run_dir).set(stage="regime", pct=49.0)

    pipe = T.read_pipeline("ag", run_dir)
    assert pipe["running"] == "validate"
    assert pipe["phases"]["train"]["stage"] == "complete"
    assert pipe["phases"]["distil"] == {}, "a phase that never ran is empty, not absent"


def test_a_finished_pipeline_has_nothing_running(run_dir):
    for n in T.PHASES:
        T.phase_progress("ag", n, run_dir).set(stage="complete", pct=100.0)
    assert T.read_pipeline("ag", run_dir)["running"] is None


def test_scored_counts_as_finished(run_dir):
    """The gate has an answer but the caller has not stored it yet.

    Without this in `TERMINAL_STAGES`, a gate that finished would be reported
    as running until the database write landed, and as STALE if the write
    failed — which reads as "still going" for something that is not.
    """
    assert "scored" in T.TERMINAL_STAGES
    T.phase_progress("ag", "validate", run_dir).set(stage="scored", pct=100.0)
    assert T.read_pipeline("ag", run_dir)["running"] is None


def test_the_gate_reports_every_check_as_it_lands():
    """One callback on `Report.add` reaches all 25 checks.

    The alternative was a progress call beside each `rep.add(...)`, which is
    25 places to forget one.
    """
    from backend.rl.validate import Check, Report

    seen = []
    rep = Report(agent_id="x", on_check=lambda c: seen.append(c.name))
    rep.add(Check("sample size", True, 147.0, 100.0, "ok"))
    rep.add(Check("PBO", False, 0.37, 0.2, "too high"))
    assert seen == ["sample size", "PBO"]


def test_a_broken_watcher_cannot_fail_the_gate():
    """The report is the product; telling someone about it is not."""
    from backend.rl.validate import Check, Report

    def explode(_check):
        raise RuntimeError("the screen fell over")

    rep = Report(agent_id="x", on_check=explode)
    rep.add(Check("sample size", True, 147.0, 100.0, "ok"))
    assert len(rep.checks) == 1, "a failing watcher must not lose the check"


def test_the_watcher_is_not_part_of_what_a_report_means():
    """Two identical reports must compare equal whoever is watching them."""
    from backend.rl.validate import Check, Report

    a = Report(agent_id="x", on_check=lambda c: None)
    b = Report(agent_id="x")
    chk = Check("sample size", True, 147.0, 100.0, "ok")
    a.add(chk)
    b.add(Check("sample size", True, 147.0, 100.0, "ok"))
    assert a.checks[0] == b.checks[0]


def test_the_gate_stage_weights_cover_the_whole_run():
    """The percentage is built from these, so they have to sum to one."""
    from backend.rl.validate import GATE_STAGES

    assert sum(w for _, _, w in GATE_STAGES) == pytest.approx(1.0, abs=1e-9)
    assert len({k for k, _, _ in GATE_STAGES}) == len(GATE_STAGES), "duplicate stage key"


def test_a_background_gate_is_held_by_a_strong_reference():
    """A bare `create_task` can be collected mid-run.

    The event loop keeps only a weak reference to a task, so a 32-minute job
    whose result nobody holds is eligible for garbage collection the whole
    time it runs. CPython's docs say to keep the reference; this is that
    reference, and the check exists so it is not tidied away as unused.
    """
    from backend.api.routes import rl as api

    assert isinstance(api._BACKGROUND, set)
    src = pathlib.Path("backend/api/routes/rl.py").read_text(encoding="utf-8")
    assert "_BACKGROUND.add(task)" in src
    assert "add_done_callback(_BACKGROUND.discard)" in src, (
        "without the discard the set grows by one task per gate run, forever")


def test_the_gate_guard_uses_the_shared_terminal_list(run_dir, monkeypatch):
    """A finished gate must not block the next one.

    The guard originally listed "complete" and "failed" by hand, which left
    `scored` — the stage the gate sets for ITSELF, before the caller stores
    the result — reading as "still running". A process that died in that gap
    would have refused every later run on that agent, permanently.
    """
    from backend.api.routes import rl as api

    src = pathlib.Path("backend/api/routes/rl.py").read_text(encoding="utf-8")
    assert "trainmod.TERMINAL_STAGES" in src, (
        "the already-running guard keeps its own copy of the terminal stages "
        "again, and it will drift from TERMINAL_STAGES again")
    assert api.trainmod.TERMINAL_STAGES == T.TERMINAL_STAGES


# -- a job that dies must say why ------------------------------------------


def test_a_job_that_raises_records_the_reason(tmp_path, monkeypatch):
    """The failure mode reported on 2026-10-10.

    Three starts in a row produced the "nothing is running" banner and no
    explanation. The worker was raising on its first line; the exception went
    into the future, nobody called `.result()` or `.exception()`, and the
    future's "done" state was indistinguishable from a backend restart.

    The file matters more than the exception: the exception dies with the API
    process, the file survives it.
    """
    monkeypatch.setattr(T, "ARTEFACT_ROOT", tmp_path)
    payload = {"spec": {"name": "boom", "symbols": ["Nope 1"], "timeframe": "M5",
                        "seeds": [1, 2, 3, 4, 5]},
               "datasets": {}, "risk_config": {}, "agent_id": "boom"}

    with pytest.raises(Exception):
        T._run_job(payload)

    st = T.read_phase("boom", "train", tmp_path)
    assert st["stage"] == "failed"
    assert "no bars" in st["error"]
    assert st.get("traceback"), "a failure with no traceback cannot be diagnosed"


def test_the_failure_keeps_what_the_run_said_before_it_died(tmp_path, monkeypatch):
    """One Progress object for the whole job, not one per handler.

    Two of them each hold their own copy of the state, so the failure handler's
    write ERASED the events explaining what the run was doing when it failed —
    which is the only part anyone needs.
    """
    monkeypatch.setattr(T, "ARTEFACT_ROOT", tmp_path)
    payload = {"spec": {"name": "boom", "symbols": ["Nope 1"], "timeframe": "M5",
                        "seeds": [1, 2, 3, 4, 5]},
               "datasets": {}, "risk_config": {}, "agent_id": "boom"}
    with pytest.raises(Exception):
        T._run_job(payload)

    texts = [e["text"] for e in T.read_phase("boom", "train", tmp_path)["events"]]
    assert any("starting" in t for t in texts), "lost the line saying it began"
    assert any("bars loaded" in t for t in texts), "lost what it managed to load"
    assert any("FAILED" in t for t in texts)


def test_a_failed_run_is_not_alive_and_is_not_merely_quiet(tmp_path, monkeypatch):
    """`failed` and `looks_alive` have to disagree, or the banner is wrong.

    Without the distinction a crashed job reads as "the backend restarted
    under it", and the admin is told to press Reset and start again — which
    reproduces the crash.
    """
    monkeypatch.setattr(T, "ARTEFACT_ROOT", tmp_path)
    (tmp_path / "dead").mkdir()
    (tmp_path / "dead" / "progress.json").write_text(
        json.dumps({"stage": "failed", "error": "ValueError: no bars"}),
        encoding="utf-8")

    st = T.queue.status("dead")
    assert st["failed"] is True
    assert st["looks_alive"] is False
    assert "no bars" in st["error"]


def test_the_data_tab_choices_are_persisted(tmp_path):
    """Sources and the training window must survive a tab switch.

    They were `useState` inside `DataTab`, which only renders while its tab is
    selected — so switching to Training to watch the run and coming back threw
    both away, silently resetting the window to its default. Checked as text
    because the project has no JS test runner; it catches the guard being
    deleted, which is the realistic regression.
    """
    page = pathlib.Path("frontend/src/pages/Agents.jsx").read_text(encoding="utf-8")
    assert "loadChoices(agent.agent_id)" in page
    assert "saveChoices(agent.agent_id" in page
    assert "localStorage" in page


def test_an_agent_can_be_deleted_but_not_an_approved_one():
    """There was no delete on any screen, which is how the list fills up.

    Refused for an APPROVED charter: that agent may be in a live slot and the
    adherence record of something that traded is not disposable.
    """
    src = pathlib.Path("backend/api/routes/rl.py").read_text(encoding="utf-8")
    assert '@router.delete("/agents/{agent_id}")' in src
    body = src.split('@router.delete("/agents/{agent_id}")', 1)[1][:2000]
    assert "ch.APPROVED" in body, "an approved agent must not be deletable"
    assert "looks_alive" in body, "a running job must not be deleted under itself"
    assert "shutil.rmtree" in body, "the artefacts outlive the row otherwise"


# -- training must never run on an empty risk config -------------------------
# MEASURED 2026-10-11, one policy and one 2,938-bar slice of XAUUSD M15:
#     risk_config {}                        883 signals ->  0 trades
#     risk_config default_risk_config(...)  922 signals -> 43 trades
# With no config the engine refuses every signal. `_evaluate` returns -inf
# below MIN_EVAL_TRADES, so every candidate of every iteration tied at -inf
# (0 of 40 scoreable), no policy was ever selected on merit, and the run
# reported 0 trades / +0.0000R / dispersion 0.00 - which reads as agreement
# rather than as nothing having been measured. `POST /train` forwards
# `body.risk_config` untouched and the Agents screen sends none, so this was
# every run started from the UI.


def _tiny_frame(n=6000, seed=4):
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(seed)
    start = 1735689600
    t = np.arange(start, start + n * 300, 300, dtype="int64")
    close = 1.08 + np.cumsum(rng.normal(0, 0.00025, n))
    return pd.DataFrame({"time": t, "open": close - 0.00001, "high": close + 0.00015,
                         "low": close - 0.00015, "close": close,
                         "volume": rng.integers(50, 500, n).astype(float)})


def test_an_empty_risk_config_is_replaced_before_any_job_runs(monkeypatch, tmp_path):
    """The trainer must never be handed {}. Asserted at the trainer boundary
    rather than by running a real job, so this stays a unit test."""
    from backend.rl import train as T
    from backend.rl.spec import AgentSpec

    seen = []

    def fake_trainer(spec, data, risk_config, fold, seed, **kw):
        seen.append(dict(risk_config or {}))
        raise RuntimeError("stop here - the config is all this test wants")

    monkeypatch.setattr(T, "train_ppo", fake_trainer)
    monkeypatch.setattr(T, "train_cem", fake_trainer)

    spec = AgentSpec(name="t", symbols=("EURUSD",), timeframe="M5", iterations=1)
    T.train_agent(spec, {"EURUSD": _tiny_frame()}, {},      # <- the empty one
                  agent_id="riskcfg-probe", root=tmp_path)

    assert seen, "the trainer was never reached, so nothing was asserted"
    assert seen[0], (
        "train_agent handed the trainer an EMPTY risk config. The engine "
        "rejects every signal under one, so the run would report zero trades "
        "without ever saying why.")
    # Enough of a config to actually trade under, not a one-key placeholder.
    assert len(seen[0]) > 10, f"only {len(seen[0])} keys: {sorted(seen[0])}"


def test_a_supplied_risk_config_is_passed_through_untouched(monkeypatch, tmp_path):
    """The fallback must not override a caller that did supply one."""
    from backend.rl import train as T
    from backend.rl.spec import AgentSpec

    seen = []

    def fake_trainer(spec, data, risk_config, fold, seed, **kw):
        seen.append(dict(risk_config or {}))
        raise RuntimeError("stop here")

    monkeypatch.setattr(T, "train_ppo", fake_trainer)
    monkeypatch.setattr(T, "train_cem", fake_trainer)

    mine = {"risk_per_trade_pct": 0.37, "spread_pips": 0.2, "marker": "caller's"}
    spec = AgentSpec(name="t", symbols=("EURUSD",), timeframe="M5", iterations=1)
    T.train_agent(spec, {"EURUSD": _tiny_frame()}, mine,
                  agent_id="riskcfg-passthrough", root=tmp_path)

    assert seen and seen[0].get("marker") == "caller's"
    assert seen[0].get("risk_per_trade_pct") == 0.37


def test_the_run_record_on_disk_carries_the_config_and_the_funnel(monkeypatch, tmp_path):
    """`training.json` is what `/export` and `load_training` read, and it is
    written by `save_run`. Setting these fields AFTER that call left both
    empty on disk while looking perfectly correct in memory - so the one
    artefact a reviewer actually gets would not have said which risk config
    the run used."""
    import json

    from backend.rl import train as T
    from backend.rl.spec import AgentSpec

    def fake_trainer(spec, data, risk_config, fold, seed, **kw):
        raise RuntimeError("no policy wanted - only the saved record matters")

    monkeypatch.setattr(T, "train_ppo", fake_trainer)
    monkeypatch.setattr(T, "train_cem", fake_trainer)

    spec = AgentSpec(name="t", symbols=("EURUSD",), timeframe="M5", iterations=1)
    T.train_agent(spec, {"EURUSD": _tiny_frame()}, {},
                  agent_id="saved-record", root=tmp_path)

    saved = json.loads((tmp_path / "saved-record" / "training.json")
                       .read_text(encoding="utf-8"))
    assert "rejection_funnel" in saved, "the funnel never reached disk"
    assert saved.get("risk_config"), (
        "the risk config never reached disk, so the finished run cannot say "
        "what the engine was given")
    # And it is the REAL one the fallback built, not a token placeholder.
    assert len(saved["risk_config"]) > 10, sorted(saved["risk_config"])
