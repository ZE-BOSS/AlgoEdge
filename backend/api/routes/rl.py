"""
backend/api/routes/rl.py

The Agents page's backend. Admin-only, enforced on the router.

WHAT THESE ROUTES MAY AND MAY NOT DO
------------------------------------
They may create a spec, start a training job, read progress, run the validation
gate, read a charter and record the admin's approval or rejection.

They may NOT approve anything on the agent's behalf, and they cannot bypass the
charter: approval goes through `charter.approve_charter` / `charter.approve`,
which refuse an illegal status transition, and every path that ends in a running
agent goes through `registry.load`, which refuses a charter that does not permit
what is being asked. The enforcement is in those modules, not here, so an API
call cannot route around the Charter tab - which is the whole point of plan 4.10.

TRAINING NEVER RUNS HERE
------------------------
`POST /train` puts a job on a bounded queue that runs in a separate process and
returns immediately. This project has already stalled the entire API by doing
MT5 work on the event loop, 0.4 seconds at a time; training is minutes to hours.
"""

from __future__ import annotations

import asyncio
import json
import math
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import require_admin
from backend.data.database import get_db
from backend.rl import charter as ch
from backend.rl import datasets as dsmod
from backend.rl import registry as rlreg
from backend.rl import train as trainmod
from backend.rl.features import FEATURES
from backend.rl.models import RLAdherence, RLAgent, RLCharter, RLProposal, naive_utc
from backend.rl.spec import AgentSpec
from backend.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/rl", tags=["rl"], dependencies=[Depends(require_admin)])

UPLOADS = Path("data/rl/uploads")
# An upload is bars, and bars are small per row. 400,000 M5 bars of OHLCV CSV is
# roughly 25 MB; 200 MB is generous and still bounded, because an unbounded
# upload is a way to fill the VPS disk from a browser.
MAX_UPLOAD_BYTES = 200 * 1024 * 1024


# -- the feature catalogue --------------------------------------------------


@router.get("/features")
async def list_features():
    """What an agent may be allowed to see, with the English the charter uses."""
    return {
        "features": [
            {"name": f.name, "group": f.group, "plain": f.plain, "prescaled": f.prescaled}
            for f in FEATURES.values()
        ],
        "note": ("Every feature here is prefix-stable: its value at a bar does not "
                 "change when later bars arrive. Nothing here carries a balance, a "
                 "lot size, a stop distance or a risk percentage - the agent has no "
                 "access to money management because there is nowhere to put it."),
    }


# The probe's answer, computed once. See `leakcheck` below for why.
_LEAKCHECK_CACHE: dict | None = None


def _run_leakcheck() -> dict:
    from backend.rl import leakguard as lg
    from backend.rl.features import assert_registry_causal

    try:
        findings = assert_registry_causal()
        return {"ok": True, "features": len(findings),
                "findings": [{"name": f.name, "causal": f.causal, "compared": f.compared}
                             for f in findings]}
    except lg.LookaheadError as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/leakcheck")
async def leakcheck(refresh: bool = False):
    """Run the prefix-stability probe over every registered feature.

    Exposed as an endpoint as well as a test so a VPS can be checked without a
    checkout, and so the Agents page can show the result rather than asserting
    it. The probe's own controls are checked first: a probe that has stopped
    detecting leaks is worse than no probe, because it is trusted.

    OFF THE EVENT LOOP, AND CACHED
    The probe recomputes every registered feature at several prefix lengths.
    That is five seconds of numpy, and run inline it blocked the whole API -
    the Agents page loads this beside the agent list, so every visit showed an
    empty list for five seconds while this finished. Same failure as building
    MT5 frames on the loop, which stalled this backend once already.

    Cached forever rather than for a while: the answer is a pure function of
    the feature registry, which is code, so it cannot change without a
    restart. `?refresh=1` forces a re-run for the paranoid.
    """
    global _LEAKCHECK_CACHE
    if _LEAKCHECK_CACHE is None or refresh:
        _LEAKCHECK_CACHE = await asyncio.to_thread(_run_leakcheck)
    return _LEAKCHECK_CACHE


# -- agents -----------------------------------------------------------------


class SpecIn(BaseModel):
    name: str
    symbols: list[str]
    timeframe: str = "M5"
    features: list[str] | None = None
    seeds: list[int] | None = None

    # ── what KIND of agent this is ────────────────────────────────────
    #
    # These three were in the AgentSpec and not in this model, which meant
    # the UI could not create anything but a plain mode-A agent - the
    # backend could do more than the front end could ask for.
    #
    # base_strategy  "" = mode A, the agent finds its own entries.
    #                A registered id = mode B: that strategy finds the
    #                setups and the agent decides which to take, where the
    #                stop goes and what to aim at.
    # director       a frozen SUPERVISED direction model on top of which
    #                the RL policy decides. Off = reinforcement only.
    # exit_timeframe a faster timeframe for the exit head alone.
    base_strategy: str = ""
    director: bool = False
    director_horizon: int = 12
    exit_head: bool = True
    exit_timeframe: str = ""

    # The two risk numbers the agent chooses, from a menu this admin sets.
    risk_head: bool = True
    stop_atr_choices: list[float] | None = None
    rr_choices: list[float] | None = None

    # Higher timeframes merged in as context columns. Blank means none, and
    # the merge is as-of the last CLOSED higher-timeframe bar, so an H1
    # column at 10:05 carries the 09:00 bar and not the one still forming.
    htf: list[str] | None = None
    supervises: list[str] = Field(default_factory=list)
    supervise_symbols: list[str] = Field(default_factory=list)
    money_weight: float = 0.70
    adherence_weight: float = 0.30
    iterations: int = 40
    population: int = 24
    cpcv_groups: int = 8
    cpcv_test_groups: int = 2
    max_cpcv_paths: int = 10
    embargo_bars: int = 288
    max_hold_bars: int = 288
    min_confidence: float = 0.40
    # How much more a loss counts than a gain of the same size. 1.0 is
    # symmetric; above it the reward is Sortino-flavoured.
    downside_penalty: float = 1.0
    # compute budget - the honest dials, exposed so the admin sets the
    # trade-off rather than discovering it
    eval_windows: int = 2
    eval_window_bars: int = 20_000
    exit_tries: int = 4
    # CEM internals. Not interesting most days, but they are what a run
    # that converges on nothing is usually diagnosed by.
    elite_frac: float = 0.25
    init_sigma: float = 0.5
    attempted_symbols: list[str] = Field(default_factory=list)
    notes: str = ""

    def to_spec(self) -> AgentSpec:
        d = self.model_dump()
        for key in ("symbols", "features", "seeds", "stop_atr_choices",
                    "rr_choices", "supervises", "supervise_symbols",
                    "attempted_symbols", "htf"):
            if d.get(key) is not None:
                d[key] = tuple(d[key])
        d = {k: v for k, v in d.items() if v is not None}
        try:
            return AgentSpec.from_dict(d)
        except ValueError as exc:
            # The spec's own validation is the pre-registration: fewer than 5
            # seeds, an unknown feature, weights that do not sum to 1, a
            # supervised symbol the agent was not trained on. All refused here
            # rather than silently corrected.
            raise HTTPException(400, str(exc)) from exc


def describe_learning(spec: AgentSpec) -> dict:
    """What this agent IS, in words, for the screen.

    The three choices compose, and the combination is not obvious from the
    field names - "mode B with a director" is a supervised direction model
    feeding an RL policy that selects among an existing strategy's setups.
    Spelling it out here means the UI and the charter say the same thing,
    rather than each inventing its own wording.
    """
    parts = []
    if spec.base_strategy:
        parts.append(
            f"It does not look for its own entries. {spec.base_strategy} finds "
            f"the setups and this agent decides which of them to take.")
    else:
        parts.append("It looks for its own entries, on every bar.")

    if spec.director:
        parts.append(
            f"A SUPERVISED direction model is fitted first on the training fold "
            f"and then frozen; its call and confidence become two extra inputs. "
            f"Supervised learning calls direction over the next "
            f"{spec.director_horizon} bars; the reinforcement policy decides "
            f"what to do about that call.")
    else:
        parts.append("Reinforcement learning only - no supervised direction "
                     "model. The policy learns direction and timing together.")

    if spec.risk_head:
        parts.append(
            f"It chooses its stop from "
            f"{{{', '.join(f'{v:g}x' for v in spec.stop_atr_choices)}}} ATR and "
            f"its target from "
            f"{{{', '.join(f'1:{v:g}' for v in spec.rr_choices)}}}.")
    else:
        parts.append("The slot's own stop and target apply; the agent does not "
                     "choose them.")

    if spec.exit_head:
        parts.append(
            "It may close, part-close or tighten a running trade"
            + (f", judged on {spec.exit_timeframe} bars." if spec.exit_timeframe
               else " on each closed bar."))
    else:
        parts.append("It has no exit head: the stop, target and max hold decide "
                     "when a trade ends.")

    if spec.supervises:
        parts.append(f"It may also supervise {', '.join(spec.supervises)}.")

    return {
        "mode": spec.mode,
        "mode_label": {"A": "Its own strategy", "B": f"Optimising {spec.base_strategy}",
                       "C": "Supervisor only"}.get(spec.mode, spec.mode),
        "reinforcement": True,
        "supervised": bool(spec.director),
        "base_strategy": spec.base_strategy,
        "sentences": parts,
        "never": ["size a position", "set a risk percentage",
                  "widen a stop", "override a circuit-breaker halt"],
    }


@router.post("/describe")
async def describe_spec(spec: SpecIn):
    """What the form currently in front of the admin would create, in words.

    An endpoint rather than the same sentences written again in JavaScript.
    The create screen needs to read a spec back BEFORE the agent exists, and
    the only two ways to do that are to duplicate the wording in the client -
    where it drifts the first time the backend's rules change and nobody
    notices - or to ask the server about a spec it has not saved. This is the
    second. It touches no database and creates nothing.
    """
    try:
        obj = spec.to_spec()
    except Exception as exc:                      # a half-filled form, mostly
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "mode": obj.mode, "learning": describe_learning(obj),
            "estimate": estimate_cost(obj)}


def estimate_cost(spec: AgentSpec) -> dict:
    """Roughly how much work the training run will be, before it is started.

    Not a wall-clock promise - that depends on the bar count, which is not
    known until data is attached - but the JOB COUNT is exact, and it is the
    number that surprises people: five seeds and eight purged paths is forty
    training runs per symbol, not one.

    The path count is combinatorial, C(groups, test_groups) capped by
    max_cpcv_paths, and needs no bars - which is why it can be shown on a
    form that has not been submitted yet.
    """
    paths = min(int(spec.max_cpcv_paths),
                math.comb(int(spec.cpcv_groups), int(spec.cpcv_test_groups)))
    symbols = max(1, len(spec.symbols))
    jobs = len(spec.seeds) * paths * symbols
    return {
        "seeds": len(spec.seeds), "paths": paths, "symbols": symbols,
        "jobs": jobs, "iterations": int(spec.iterations),
        "note": (f"{len(spec.seeds)} seeds x {paths} purged CPCV paths x "
                 f"{symbols} symbol(s) = {jobs} separate training runs, each "
                 f"{int(spec.iterations)} iterations. The seeds are not a "
                 f"formality: the gate rejects the result outright if they "
                 f"disagree by more than 1.0 Sharpe."),
    }


@router.get("/strategies")
async def base_strategy_options():
    """The rule-based strategies a mode-B agent can be built on.

    Agents are excluded: an agent optimising another agent is not a thing this
    design supports, and offering it would invite a loop nobody can reason
    about.
    """
    from backend.strategies.registry import get_all_strategies

    return {"strategies": sorted(sid for sid in get_all_strategies()
                                 if not sid.startswith("RL::"))}


@router.get("/agents")
async def list_agents(db: AsyncSession = Depends(get_db), status: str | None = None):
    rows = await rlreg.list_agents(db, status=status)
    out = []
    for r in rows:
        charters = (await db.execute(
            select(RLCharter).where(RLCharter.agent_id == r.agent_id)
            .order_by(desc(RLCharter.version)))).scalars().all()
        approved = next((c for c in charters if c.status == ch.APPROVED), None)
        open_props = await rlreg.open_proposals(db, r.agent_id)
        validation = json.loads(r.validation_json) if r.validation_json else None
        out.append({
            "agent_id": r.agent_id, "name": r.name, "status": r.status,
            "optimiser": r.optimiser, "spec_hash": r.spec_hash,
            "policy_hash": r.policy_hash,
            "suspended": bool(r.suspended), "suspended_reason": r.suspended_reason or "",
            "live_adherence": r.live_adherence,
            "charter_version": approved.version if approved else None,
            "charter_status": approved.status if approved else (
                charters[0].status if charters else None),
            "strategy_id": (rlreg.strategy_id_for(r.name, approved.version)
                            if approved else None),
            "validation_passed": (validation or {}).get("passed"),
            "expectancy_r": (validation or {}).get("expectancy_r"),
            "trades": (validation or {}).get("trades"),
            "open_proposals": len(open_props),
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })
    return {"agents": out, "loaded": sorted(rlreg.AGENT_CACHE.keys())}


@router.post("/agents")
async def create_agent(body: SpecIn, db: AsyncSession = Depends(get_db)):
    """Record the pre-registration. No training, no weights yet.

    Frozen from here: the spec is content-hashed and every later report prints
    that hash, so a run cannot quietly change its own terms - including which
    symbols were attempted, which is the leak no probe catches.
    """
    spec = body.to_spec()
    agent_id = f"{spec.name}-{uuid.uuid4().hex[:8]}"
    if await rlreg.get_agent(db, agent_id) is not None:
        raise HTTPException(409, f"{agent_id} already exists")
    row = RLAgent(agent_id=agent_id, name=spec.name, spec_json=json.dumps(spec.to_dict()),
                  spec_hash=spec.content_hash, status="DRAFT")
    db.add(row)
    await db.commit()
    return {"agent_id": agent_id, "spec_hash": spec.content_hash,
            "spec": spec.to_dict()}


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str, db: AsyncSession = Depends(get_db)):
    row = await rlreg.get_agent(db, agent_id)
    if row is None:
        raise HTTPException(404, f"no agent {agent_id}")
    charters = await rlreg.list_charters(db, agent_id)
    props = (await db.execute(
        select(RLProposal).where(RLProposal.agent_id == agent_id)
        .order_by(desc(RLProposal.created_at)))).scalars().all()
    spec_d = json.loads(row.spec_json)
    try:
        spec_obj = AgentSpec.from_dict(spec_d)
        mode, learning = spec_obj.mode, describe_learning(spec_obj)
    except Exception:
        mode, learning = "?", {}
    return {
        "agent_id": row.agent_id, "name": row.name, "status": row.status,
        "optimiser": row.optimiser, "spec": spec_d,
        "mode": mode, "learning": learning,
        "spec_hash": row.spec_hash, "policy_hash": row.policy_hash,
        "trained_on": json.loads(row.trained_on_json or "[]"),
        "training": json.loads(row.training_json) if row.training_json else None,
        "validation": json.loads(row.validation_json) if row.validation_json else None,
        "suspended": bool(row.suspended), "suspended_reason": row.suspended_reason or "",
        "live_adherence": row.live_adherence,
        "reject_reason": row.reject_reason or "",
        "charters": [c.to_dict() | {"english": c.english()} for c in charters],
        "proposals": [{
            "id": p.id, "from_version": p.from_version, "kind": p.kind,
            "status": p.status, "why": p.why, "diff_hash": p.diff_hash,
            "reason": p.reason, "decided_by": p.decided_by,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "evidence_approved": json.loads(p.evidence_approved_json or "{}"),
            "evidence_proposed": json.loads(p.evidence_proposed_json or "{}"),
            "proposed": json.loads(p.proposal_json),
        } for p in props],
        "progress": trainmod.read_progress(agent_id),
    }


class CloneIn(BaseModel):
    symbols: list[str]
    name: str | None = None


@router.post("/agents/{agent_id}/clone")
async def clone_agent(agent_id: str, body: CloneIn, db: AsyncSession = Depends(get_db)):
    """Same spec, different instruments, fresh training.

    The WEIGHTS are not copied. An agent's policy is fitted to one instrument's
    scale and its scaler was fitted on that instrument's bars; carrying both to
    a new symbol would produce a confident agent that had never seen the market
    it was trading. Cloning copies the experiment, not the result.
    """
    src = await rlreg.get_agent(db, agent_id)
    if src is None:
        raise HTTPException(404, f"no agent {agent_id}")
    d = json.loads(src.spec_json)
    d["symbols"] = list(body.symbols)
    d["attempted_symbols"] = list(body.symbols)
    d["name"] = body.name or f"{src.name}-clone"
    try:
        spec = AgentSpec.from_dict(d)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    new_id = f"{spec.name}-{uuid.uuid4().hex[:8]}"
    db.add(RLAgent(agent_id=new_id, name=spec.name, spec_json=json.dumps(spec.to_dict()),
                   spec_hash=spec.content_hash, status="DRAFT"))
    await db.commit()
    return {"agent_id": new_id, "spec_hash": spec.content_hash,
            "note": "weights are not cloned - a policy fitted to one instrument's "
                    "scale has not seen the new one"}


# -- datasets ---------------------------------------------------------------


@router.post("/datasets/upload")
async def upload_dataset(symbol: str = Query(...), timeframe: str = Query("M5"),
                         time_is: str = Query("open"), timezone_name: str = Query("UTC"),
                         broker: str = Query(""), file: UploadFile = File(...)):
    """Take a CSV/JSON/NPZ of bars, validate it hard, and report what it is.

    Validated rather than accepted: a file whose intervals do not match its
    declared timeframe, or whose highs do not contain its closes, is REFUSED
    with the reason. Resampling or repairing it quietly would move every
    feature by an amount nothing downstream can detect.
    """
    UPLOADS.mkdir(parents=True, exist_ok=True)
    blob = await file.read()
    if len(blob) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"{len(blob) / 1e6:.0f} MB exceeds the "
                                 f"{MAX_UPLOAD_BYTES / 1e6:.0f} MB limit")
    safe = "".join(c for c in (file.filename or "upload") if c.isalnum() or c in "._-")
    path = UPLOADS / f"{symbol.replace(' ', '_')}_{timeframe}_{uuid.uuid4().hex[:6]}_{safe}"
    path.write_bytes(blob)
    try:
        ds = dsmod.load_file(path, symbol, timeframe, time_is=time_is,
                             tz=timezone_name, broker=broker)
    except dsmod.DatasetError as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(400, str(exc)) from exc
    return {"path": str(path), **ds.to_dict()}


class OverlapIn(BaseModel):
    agent_id: str
    symbol: str
    timeframe: str = "M5"
    start: int
    end: int


@router.post("/overlap")
async def check_overlap(body: OverlapIn, db: AsyncSession = Depends(get_db)):
    """Is this backtest window inside what the agent trained on?

    `runnable` false means the Backtester must DISABLE Run, not warn: a warning
    you can click past is a warning that gets clicked past, and an in-sample
    backtest of a trained agent is the most convincing wrong number this system
    can produce.
    """
    row = await rlreg.get_agent(db, body.agent_id)
    if row is None:
        raise HTTPException(404, f"no agent {body.agent_id}")
    trained_on = json.loads(row.trained_on_json or "[]")
    if not trained_on:
        # No coverage recorded is NOT "clean". It means we cannot tell, and
        # saying "clean" would be a guess presented as a fact.
        return {"state": "unknown", "runnable": False, "fraction": None,
                "message": "this agent has no recorded training coverage, so overlap "
                           "cannot be checked - retrain it so the coverage is stored"}
    return dsmod.overlap(trained_on, body.symbol, body.timeframe,
                         body.start, body.end).to_dict()


# -- training ---------------------------------------------------------------


class TrainIn(BaseModel):
    # {symbol: "mt5" | "<path>" | {path, timeframe, time_is, timezone, broker}}
    datasets: dict = Field(default_factory=dict)
    risk_config: dict = Field(default_factory=dict)
    optimiser: str | None = None


@router.post("/agents/{agent_id}/train")
async def start_training(agent_id: str, body: TrainIn, db: AsyncSession = Depends(get_db)):
    """Queue a training job. Returns immediately.

    The job runs in a separate process behind a queue that is bounded at four:
    a deeper queue just moves the wait somewhere nobody can see it, and two
    jobs on the same cores finish later than two jobs in sequence.
    """
    row = await rlreg.get_agent(db, agent_id)
    if row is None:
        raise HTTPException(404, f"no agent {agent_id}")
    spec = AgentSpec.from_dict(json.loads(row.spec_json))
    missing = [s for s in spec.symbols if s not in (body.datasets or {})]
    if missing:
        raise HTTPException(400, f"no data source for {missing} - every PRE-REGISTERED "
                                 f"symbol must be supplied, including the ones you expect "
                                 f"to fail, because the report names every asset attempted")
    payload = trainmod.job_payload(spec, body.datasets, body.risk_config,
                                   agent_id=agent_id, optimiser=body.optimiser)
    try:
        trainmod.queue.submit(agent_id, payload)
    except RuntimeError as exc:
        raise HTTPException(429, str(exc)) from exc
    row.status = "TRAINING"
    row.optimiser = trainmod.available_optimiser(body.optimiser)
    await db.commit()
    return {"agent_id": agent_id, "queued": True, "optimiser": row.optimiser,
            "pending": trainmod.queue.pending}


@router.get("/agents/{agent_id}/progress")
async def training_progress(agent_id: str):
    return trainmod.queue.status(agent_id)


@router.post("/agents/{agent_id}/cancel")
async def cancel_training(agent_id: str):
    """Ask a running job to stop at the next candidate.

    Not a kill: the worker checks for the cancel between candidate
    evaluations, so a cancelled run stops within one engine run and leaves its
    artefacts readable instead of half-written.
    """
    dropped = trainmod.queue.cancel(agent_id)
    return {"agent_id": agent_id, "dropped_from_queue": dropped,
            "note": "a running job stops at its next candidate"}


@router.post("/agents/{agent_id}/collect")
async def collect_training(agent_id: str, db: AsyncSession = Depends(get_db)):
    """Store a finished job's policy and scaler, and distil a DRAFT charter.

    Separate from `/train` because the training process cannot write to the
    API's session. Idempotent: calling it twice stores the same policy and
    rebuilds the same draft, because the distillation is deterministic given
    the policy and the bars.

    DRAFT, deliberately. A freshly distilled charter has been measured but not
    signed, and the status machine is what stops it reaching a live slot.
    """
    row = await rlreg.get_agent(db, agent_id)
    if row is None:
        raise HTTPException(404, f"no agent {agent_id}")
    loaded = trainmod.load_policy(agent_id)
    if loaded is None:
        raise HTTPException(409, "no trained policy on disk yet")
    policy, scaler = loaded
    spec = AgentSpec.from_dict(json.loads(row.spec_json))
    result = trainmod.queue.result(agent_id) or {}
    trained_on = result.get("trained_on") or json.loads(row.trained_on_json or "[]")
    await rlreg.save_agent(db, agent_id=agent_id, spec=spec, policy=policy, scaler=scaler,
                           trained_on=trained_on, training=result,
                           optimiser=row.optimiser or "", status="TRAINED")
    await db.commit()
    return {"agent_id": agent_id, "policy_hash": policy.content_hash,
            "scaler_fitted_on": scaler.fitted_on,
            "note": "call /distil to build the charter, then /validate to run the gate"}


class RunIn(BaseModel):
    """Which bars to distil or validate against."""
    datasets: dict = Field(default_factory=dict)
    risk_config: dict = Field(default_factory=dict)


@router.post("/agents/{agent_id}/distil")
async def distil_charter(agent_id: str, body: RunIn, db: AsyncSession = Depends(get_db)):
    """Turn the trained policy into a readable, executable charter.

    The gates are thresholds on named features, so "did it follow its strategy"
    is computed rather than asserted - and the charter carries the measured
    fidelity with which those gates reproduce the policy's own decisions. Below
    the gate's floor the charter is decoration and validation will reject it.
    """
    import asyncio

    row = await rlreg.get_agent(db, agent_id)
    if row is None:
        raise HTTPException(404, f"no agent {agent_id}")
    loaded = trainmod.load_policy(agent_id)
    if loaded is None:
        raise HTTPException(409, "train the agent first")
    policy, scaler = loaded
    spec = AgentSpec.from_dict(json.loads(row.spec_json))

    def work():
        from backend.rl import distill
        from backend.rl import env as E
        frames = dsmod.load_frames(body.datasets, spec)
        if not frames:
            raise ValueError("no bars - supply a dataset per pre-registered symbol")
        symbol = next(iter(frames))
        data = E.EpisodeData.build(symbol, spec.timeframe, frames[symbol], spec.features)
        env = E.TradingEnv(spec, data, body.risk_config or
                           E.default_risk_config(symbol), scaler=scaler)
        ep = env.rollout(policy, transitions=True)
        return distill.distil(
            policy, env.Z, data.names, spec, agent_id=agent_id,
            trade_bars=distill.trade_bars_from([ep]),
            n_trades=ep.breakdown.n_trades, mean_r=ep.breakdown.mean_r or None,
            scaler_note=scaler.fitted_on), [d for d in (data.coverage(),)]

    try:
        charter, coverage = await asyncio.to_thread(work)
    except Exception as exc:                            # noqa: BLE001
        raise HTTPException(400, f"{type(exc).__name__}: {exc}") from exc

    existing = await rlreg.list_charters(db, agent_id)
    if any(c.status == ch.APPROVED for c in existing):
        # An approved charter is not re-distilled over. A change to an approved
        # strategy is a PROPOSAL the admin decides on, which is the whole point
        # of the governance - so this refuses rather than overwriting.
        raise HTTPException(409, "this agent already has an approved charter - a change "
                                 "to it is a proposal, not a re-distillation")
    await rlreg.save_charter(db, charter)
    row.trained_on_json = json.dumps(coverage)
    await db.commit()
    return charter.to_dict() | {"english": charter.english()}


@router.post("/agents/{agent_id}/validate")
async def run_gate(agent_id: str, body: RunIn, db: AsyncSession = Depends(get_db)):
    """Run the whole validation gate and store the report, pass or fail.

    Runs in a worker thread, not on the event loop - it is dozens of engine
    runs. Rejections are STORED: the record of what did not work is worth more
    than another candidate, and it stops the same idea being retried as new.
    """
    import asyncio

    row = await rlreg.get_agent(db, agent_id)
    if row is None:
        raise HTTPException(404, f"no agent {agent_id}")
    loaded = trainmod.load_policy(agent_id)
    if loaded is None:
        raise HTTPException(409, "train the agent first")
    policy, scaler = loaded
    spec = AgentSpec.from_dict(json.loads(row.spec_json))
    charter = await rlreg.get_charter(db, agent_id, version=None)
    if charter is None:
        charters = await rlreg.list_charters(db, agent_id)
        charter = charters[0] if charters else None
    training = json.loads(row.training_json) if row.training_json else None

    def work():
        from backend.rl import env as E
        from backend.rl import validate as V
        frames = dsmod.load_frames(body.datasets, spec)
        if not frames:
            raise ValueError("no bars - supply a dataset per pre-registered symbol")
        symbol = next(iter(frames))
        data = E.EpisodeData.build(symbol, spec.timeframe, frames[symbol], spec.features)
        run = _rehydrate_run(training)
        rep = V.validate(spec, policy, scaler, data,
                         body.risk_config or E.default_risk_config(symbol),
                         agent_id=agent_id, training_run=run, charter=charter,
                         optimiser=row.optimiser or "")
        return rep

    try:
        report = await asyncio.to_thread(work)
    except Exception as exc:                            # noqa: BLE001
        raise HTTPException(400, f"{type(exc).__name__}: {exc}") from exc

    from backend.rl import notify as rl_notify
    rl_notify.validation_finished(agent_id, report.to_dict())
    row.validation_json = json.dumps(report.to_dict())
    row.status = "VALIDATED" if report.passed else "REJECTED"
    if not report.passed:
        row.reject_reason = ("failed the gate: "
                             + ", ".join(c.name for c in report.failures))
    await db.commit()
    return report.to_dict() | {"text": report.text()}


def _rehydrate_run(training: dict | None):
    """Rebuild just enough of a `TrainingRun` for the seed and PBO checks.

    Only the fields those two checks read. A fuller rehydration would need the
    policies back, which are on disk per agent rather than per seed - and the
    checks do not need them.
    """
    if not training:
        return None
    from backend.rl.train import SeedResult, TrainingRun

    run = TrainingRun(agent_id=training.get("agent_id", ""),
                      optimiser=training.get("optimiser", ""))
    for r in training.get("results") or []:
        run.results.append(SeedResult(
            seed=int(r.get("seed", 0)), fold=int(r.get("fold", 0)),
            score=float(r.get("score") or 0.0),
            train_score=float(r.get("train_score") or 0.0),
            test_mean_r=float(r.get("test_mean_r") or 0.0),
            test_trades=int(r.get("test_trades") or 0),
            sharpe=float(r.get("sharpe") or 0.0)))
    return run or None


# -- charters ---------------------------------------------------------------


class ApproveIn(BaseModel):
    version: int | None = None


class RejectIn(BaseModel):
    reason: str
    version: int | None = None


@router.post("/agents/{agent_id}/charter/propose")
async def propose_charter(agent_id: str, db: AsyncSession = Depends(get_db)):
    """DRAFT -> PROPOSED. Freezes the charter and makes it backtestable.

    There is deliberately no DRAFT -> APPROVED path: a charter has to be
    runnable-and-labelled-unapproved before anyone signs it off, so the admin
    is approving something they could have looked at.
    """
    charter = await rlreg.get_charter(db, agent_id, version=None)
    if charter is None:
        rows = (await db.execute(
            select(RLCharter).where(RLCharter.agent_id == agent_id)
            .order_by(desc(RLCharter.version)))).scalars().first()
        if rows is None:
            raise HTTPException(404, "no charter - train the agent first, which distils one")
        charter = ch.StrategyCharter.from_dict(json.loads(rows.charter_json))
    try:
        ch.propose_draft(charter)
    except ch.CharterError as exc:
        raise HTTPException(409, str(exc)) from exc
    await rlreg.save_charter(db, charter)
    await db.commit()
    from backend.rl import notify as rl_notify
    rl_notify.approval_wanted(agent_id, "Charter waiting for approval",
                              charter.thesis[:300])
    return charter.to_dict()


@router.post("/agents/{agent_id}/charter/approve")
async def approve_charter(agent_id: str, body: ApproveIn,
                          db: AsyncSession = Depends(get_db),
                          admin=Depends(require_admin)):
    """The admin's signature. This is what lets an agent reach a live slot."""
    rows = (await db.execute(
        select(RLCharter).where(RLCharter.agent_id == agent_id,
                                RLCharter.status == ch.PROPOSED)
        .order_by(desc(RLCharter.version)))).scalars().first()
    if rows is None:
        raise HTTPException(409, "no PROPOSED charter to approve")
    charter = ch.StrategyCharter.from_dict(json.loads(rows.charter_json))
    agent_row = await rlreg.get_agent(db, agent_id)
    validation = json.loads(agent_row.validation_json) if (
        agent_row and agent_row.validation_json) else {}
    if not validation:
        raise HTTPException(409, "this charter has no validation report - run the gate "
                                 "first, so you are approving a measurement rather than "
                                 "an intention")
    try:
        ch.approve_charter(charter, by=str(getattr(admin, "email", "") or "admin"))
    except ch.CharterError as exc:
        raise HTTPException(409, str(exc)) from exc
    charter.validation = validation
    await rlreg.save_charter(db, charter)
    if agent_row is not None:
        agent_row.status = rlreg.APPROVED
    await db.commit()
    registered = await rlreg.register_approved(db)
    return {"charter": charter.to_dict(), "registered": registered,
            "strategy_id": rlreg.strategy_id_for(charter.agent_id, charter.version)}


@router.post("/agents/{agent_id}/charter/reject")
async def reject_charter(agent_id: str, body: RejectIn,
                         db: AsyncSession = Depends(get_db),
                         admin=Depends(require_admin)):
    """Rejections are kept, with the reason. The record of what did not work is
    worth more than another candidate."""
    if not body.reason.strip():
        raise HTTPException(400, "a rejection needs a reason - it is what blocks the "
                                 "same thing being resubmitted")
    q = select(RLCharter).where(RLCharter.agent_id == agent_id)
    if body.version is not None:
        q = q.where(RLCharter.version == body.version)
    row = (await db.execute(q.order_by(desc(RLCharter.version)))).scalars().first()
    if row is None:
        raise HTTPException(404, "no charter")
    charter = ch.StrategyCharter.from_dict(json.loads(row.charter_json))
    if not charter.can_transition_to(ch.REJECTED):
        raise HTTPException(409, f"a {charter.status} charter cannot be rejected")
    charter.status = ch.REJECTED
    charter.rejection_reason = body.reason.strip()
    await rlreg.save_charter(db, charter)
    agent_row = await rlreg.get_agent(db, agent_id)
    if agent_row is not None:
        agent_row.status = rlreg.REJECTED
        agent_row.reject_reason = body.reason.strip()
    rlreg.cache_clear(agent_id)
    rlreg.unregister(rlreg.strategy_id_for(agent_id, charter.version))
    await db.commit()
    return charter.to_dict()


# -- proposals --------------------------------------------------------------


@router.post("/proposals/{proposal_id}/approve")
async def approve_proposal(proposal_id: int, db: AsyncSession = Depends(get_db),
                           admin=Depends(require_admin)):
    """Approving bumps the version and supersedes the old charter.

    The old one is kept, so every historical run can still be reproduced
    against the charter it actually ran under.
    """
    row = await db.get(RLProposal, proposal_id)
    if row is None:
        raise HTTPException(404, "no proposal")
    if row.status != ch.OPEN:
        raise HTTPException(409, f"proposal is {row.status}")
    approved = await rlreg.get_charter(db, row.agent_id, version=None)
    if approved is None:
        raise HTTPException(409, "there is no approved charter to supersede")
    proposal = ch.CharterProposal(
        agent_id=row.agent_id, from_version=row.from_version, kind=row.kind,
        proposed=ch.StrategyCharter.from_dict(json.loads(row.proposal_json)),
        why=row.why or "",
        evidence_approved=json.loads(row.evidence_approved_json or "{}"),
        evidence_proposed=json.loads(row.evidence_proposed_json or "{}"))
    try:
        new, old = ch.approve(approved, proposal, by=str(getattr(admin, "email", "") or "admin"))
    except (ch.CharterError, ch.ProposalError) as exc:
        raise HTTPException(409, str(exc)) from exc
    await rlreg.save_charter(db, old)
    await rlreg.save_charter(db, new)
    row.status = ch.ACCEPTED
    row.decided_by = str(getattr(admin, "email", "") or "admin")
    row.decided_at = naive_utc()
    rlreg.cache_clear(row.agent_id)
    rlreg.unregister(rlreg.strategy_id_for(row.agent_id, old.version))
    await db.commit()
    registered = await rlreg.register_approved(db)
    return {"charter": new.to_dict(), "superseded": old.version, "registered": registered}


@router.post("/proposals/{proposal_id}/reject")
async def reject_proposal(proposal_id: int, body: RejectIn,
                          db: AsyncSession = Depends(get_db),
                          admin=Depends(require_admin)):
    """Records the reason and blocks the same diff by hash.

    The agent may not re-propose an identical change without new evidence -
    compared by `diff_hash`, so rewording it does not get round the block.
    """
    row = await db.get(RLProposal, proposal_id)
    if row is None:
        raise HTTPException(404, "no proposal")
    if row.status != ch.OPEN:
        raise HTTPException(409, f"proposal is {row.status}")
    if not body.reason.strip():
        raise HTTPException(400, "a rejection needs a reason")
    row.status = ch.DECLINED
    row.reason = body.reason.strip()
    row.decided_by = str(getattr(admin, "email", "") or "admin")
    row.decided_at = naive_utc()
    await db.commit()
    return {"proposal_id": proposal_id, "status": row.status, "reason": row.reason}


# -- live adherence ---------------------------------------------------------


@router.get("/agents/{agent_id}/adherence")
async def adherence_history(agent_id: str, days: int = Query(90, ge=1, le=730),
                            db: AsyncSession = Depends(get_db)):
    """The live drift chart, per closed trade.

    Per trade rather than per bar, because adherence credited per bar makes
    "never trade" the highest-adherence policy and the drift monitor would then
    never fire on an agent that had quietly stopped trading its charter.
    """
    # Naive UTC, to match the column. `rl_adherence.created_at` is a plain
    # `DateTime` written by `server_default=func.now()`, which SQLite returns
    # naive - comparing it against an aware value raises.
    since = naive_utc() - timedelta(days=days)
    rows = (await db.execute(
        select(RLAdherence).where(RLAdherence.agent_id == agent_id,
                                  RLAdherence.created_at >= since)
        .order_by(RLAdherence.created_at))).scalars().all()
    agent = await rlreg.get_agent(db, agent_id)
    charter = await rlreg.get_charter(db, agent_id, version=None)
    return {
        "agent_id": agent_id,
        "floor": charter.adherence_floor if charter else None,
        "drift_trades": charter.drift_trades if charter else None,
        "suspended": bool(agent.suspended) if agent else False,
        "suspended_reason": (agent.suspended_reason or "") if agent else "",
        "points": [{
            "at": r.created_at.isoformat() if r.created_at else None,
            "adherence": r.adherence, "tier": r.tier, "symbol": r.symbol,
            "r_net": r.r_net, "version": r.charter_version,
            "skipped": json.loads(r.gates_skipped_json or "[]"),
            "tripped": bool(r.tripped_suspension),
        } for r in rows],
    }


@router.post("/agents/{agent_id}/unsuspend")
async def unsuspend(agent_id: str, db: AsyncSession = Depends(get_db)):
    """Re-enable by hand, after someone has looked at why it drifted.

    Deliberately manual. An agent that drifted and came back is still an agent
    whose charter no longer describes it, and auto-resuming would make the
    suspension a speed bump instead of a decision.
    """
    await rlreg.unsuspend(db, agent_id)
    charter = await rlreg.get_charter(db, agent_id, version=None)
    if charter is not None:
        from backend.rl.adherence import monitor
        monitor.reset(charter)
    await db.commit()
    return {"agent_id": agent_id, "suspended": False}


@router.get("/suspensions")
async def pending_suspensions():
    """What the drift monitor has flagged but the jobs loop has not stored yet.

    Visible because the in-process suspension takes effect immediately and the
    durable record follows on the next job tick; without this the gap between
    the two would be invisible.
    """
    from backend.rl import suspension
    return {"pending": suspension.peek()}
