"""
backend/rl/train.py

Training: several seeds, purged folds, and a bounded queue that never runs on
the API event loop.

WHICH OPTIMISER, AND WHY THERE ARE TWO
--------------------------------------
PPO is the most stable algorithm in the comparative trading literature and it
is what the plan specifies. Both optimisers here are numpy-only and need
nothing installed, which is what lets the whole pipeline - env, reward,
validation, distillation, charter, registry, live engine - run and be tested
today rather than after a dependency install.

  * **PPO** (the default) - the clipped surrogate objective applied directly
    to the linear policy. The algorithm the plan specifies; NOT
    `stable_baselines3`, because sb3 drives a per-step Gymnasium env and the
    engine this wraps is a batch simulator whose realism is the entire reason
    for wrapping it. `train_ppo` says more about that trade-off, and the
    artefact records `ppo-direct` so nobody reads it as an sb3 result.
  * **CEM** (the cross-entropy method) - a population search: sample parameter
    vectors, keep the best quarter, re-fit a Gaussian. Gradient-free, so it is
    the honest comparison for whether the gradient is doing anything, and it
    handles the exit head's chained decisions without a bandit assumption.

Every run records which one produced it.

WHAT IS SAMPLED AND WHAT IS NOT
-------------------------------
A candidate is scored on `eval_windows` contiguous windows of the fold's
TRAINING rows. That is stochastic evaluation, which is ordinary, and it is the
only way 5 seeds x 10 paths x 960 candidate evaluations finishes - that is
48,000 runs of the real engine per asset. Every REPORTED number comes from a
whole test fold, never from a sample.

NEVER ON THE EVENT LOOP
-----------------------
This project has already stalled the entire API by building MT5 frames on the
loop, 0.4s at a time. Training is minutes to hours. `TrainingQueue` runs each
job in a separate process with a bounded backlog, and progress goes through a
JSON file the API polls - not a multiprocessing queue, because a queue that
nobody drains is a wedged worker.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from backend.rl import env as E
from backend.rl import features as feat
from backend.rl import policy as pol
from backend.rl.spec import AgentSpec
from backend.utils.logger import get_logger

logger = get_logger(__name__)

ARTEFACT_ROOT = Path("data/rl")

CEM = "cem"
PPO = "ppo"


def available_optimiser(prefer: str | None = None) -> str:
    """Which optimiser a run will use. Named so the artefact cannot mislead.

    `ppo` means `train_ppo` - PPO's clipped surrogate objective applied
    directly to the linear policy, which needs nothing installed. See that
    function for why `stable_baselines3` is not used: it drives a per-step
    Gymnasium env, and the engine this env wraps is a batch simulator whose
    realism is the whole reason for wrapping it.
    """
    return CEM if prefer == CEM else PPO


# -- what a run produces ----------------------------------------------------


@dataclass
class SeedResult:
    seed: int
    fold: int
    score: float = 0.0
    # The IN-SAMPLE score, kept beside the out-of-sample one because PBO is
    # built from the pair: which candidate looked best on the training rows,
    # and where that candidate then landed out of sample. Without it the
    # probability of backtest overfitting cannot be computed at all.
    train_score: float = 0.0
    test_mean_r: float = 0.0
    test_trades: int = 0
    test_adherence: float = 0.0
    sharpe: float = 0.0
    policy: dict = field(default_factory=dict)
    scaler: dict = field(default_factory=dict)
    history: list = field(default_factory=list)
    train_rows: int = 0
    test_rows: int = 0
    seconds: float = 0.0

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d.pop("policy", None)
        d.pop("scaler", None)
        return d


@dataclass
class TrainingRun:
    """Everything a training run produced, including what failed."""
    agent_id: str
    spec: dict = field(default_factory=dict)
    optimiser: str = CEM
    results: list = field(default_factory=list)
    best: SeedResult | None = None
    symbols_attempted: list = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""
    seconds: float = 0.0
    error: str = ""

    @property
    def sharpe_dispersion(self) -> float:
        """Spread of Sharpe across seeds. The gate rejects above 1.0.

        Standing advice in the literature and the reason the seed floor is five:
        if Sharpe moves by 2 across five seeds you do not have a result, you
        have a sample of one lucky initialisation.
        """
        by_seed: dict[int, list] = {}
        for r in self.results:
            by_seed.setdefault(r.seed, []).append(r.sharpe)
        if len(by_seed) < 2:
            return float("inf")
        means = [float(np.mean(v)) for v in by_seed.values()]
        return float(max(means) - min(means))

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id, "spec": self.spec, "optimiser": self.optimiser,
            "results": [r.to_dict() for r in self.results],
            "best": self.best.to_dict() if self.best else None,
            "symbols_attempted": self.symbols_attempted,
            "sharpe_dispersion": (None if self.sharpe_dispersion == float("inf")
                                  else round(self.sharpe_dispersion, 4)),
            "started_at": self.started_at, "finished_at": self.finished_at,
            "seconds": round(self.seconds, 1), "error": self.error,
        }


# -- progress ---------------------------------------------------------------


class Progress:
    """A JSON file per run. Deliberately boring.

    A multiprocessing queue is the obvious choice and the wrong one: if nothing
    drains it the worker blocks, and the thing that drains it is the API, which
    is the process this exists to keep free. A file is readable from any
    process, survives a restart, and a stale one is obvious.
    """

    def __init__(self, path: Path | None):
        self.path = Path(path) if path else None
        self.started = time.time()
        self.state: dict[str, Any] = {}

    def set(self, **kw) -> None:
        self.state.update(kw)
        self.state["elapsed_s"] = round(time.time() - self.started, 1)
        pct = self.state.get("pct")
        if pct is not None and pct > 0:
            done = max(1e-6, float(pct) / 100.0)
            self.state["eta_s"] = round(self.state["elapsed_s"] * (1 - done) / done, 1)
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.state), encoding="utf-8")
            os.replace(tmp, self.path)
        except Exception as exc:                        # noqa: BLE001
            logger.debug(f"[RL] progress write failed: {exc}")

    @property
    def cancelled(self) -> bool:
        """A cancel file beside the progress file. Checked between candidates,
        so a cancelled run stops within one episode rather than at the end."""
        if self.path is None:
            return False
        return self.path.with_name("cancel").exists()


def request_cancel(agent_id: str, root: Path | None = None) -> None:
    d = (root or ARTEFACT_ROOT) / agent_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "cancel").write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")


def clear_cancel(agent_id: str, root: Path | None = None) -> None:
    f = (root or ARTEFACT_ROOT) / agent_id / "cancel"
    if f.exists():
        f.unlink()


# -- the cross-entropy method ----------------------------------------------


def _sample_windows(spans, rng: np.random.Generator, count: int, width: int
                    ) -> list[tuple[int, int]]:
    """Contiguous windows drawn from the fold's training spans.

    Contiguous, not scattered rows: the engine simulates a bar sequence, and a
    set of disconnected rows is not a bar sequence. Picking scattered rows and
    running them as one frame would invent gaps the live feed never has.
    """
    usable = [(a, b) for a, b in spans if b - a >= 200]
    if not usable:
        return []
    out = []
    weights = np.array([b - a for a, b in usable], dtype=float)
    weights = weights / weights.sum()
    for _ in range(max(1, int(count))):
        a, b = usable[int(rng.choice(len(usable), p=weights))]
        w = min(int(width), b - a)
        start = int(rng.integers(a, max(a + 1, b - w + 1)))
        out.append((start, min(b, start + w)))
    return out


def _score(env: E.TradingEnv, policy: pol.LinearPolicy, *, exit_head: bool = True,
           seed: int = 0) -> E.Episode:
    return env.rollout(policy, exit_head=exit_head, seed=seed)


def train_cem(spec: AgentSpec, data: E.EpisodeData, risk_config: dict, fold: E.Fold,
              seed: int, *, progress: Progress | None = None,
              pct_from: float = 0.0, pct_to: float = 100.0,
              charter=None) -> tuple[pol.LinearPolicy, feat.Scaler, list]:
    """Search the parameter space on this fold's TRAINING rows only.

    The scaler is fitted on the training rows too, and then frozen - the single
    most common leak in applied ML, and the one that looks most innocent in a
    diff.
    """
    rng = np.random.default_rng(seed)
    train_rows = E.rows_of(fold.train)
    if not len(train_rows):
        raise ValueError("fold has no training rows")

    # Fit the scaler on the training ROWS, which are discontiguous - that is
    # fine for a scaler (it is a per-column statistic, not a sequence) and it
    # is exactly the rows the policy will be fitted on.
    scaler = feat.Scaler().fit(data.X[train_rows], data.names,
                               fitted_on=f"fold {fold.path} train rows ({len(train_rows)})")

    template = pol.LinearPolicy(n_features=spec.observation_width, hidden=0,
                                exit_head=spec.exit_head, seed=seed,
                                n_stop=len(spec.stop_atr_choices) if spec.risk_head else 0,
                                n_rr=len(spec.rr_choices) if spec.risk_head else 0)
    n = template.n_params
    mu = np.zeros(n)
    sigma = np.full(n, float(spec.init_sigma))
    n_elite = max(2, int(round(spec.population * spec.elite_frac)))

    history = []
    best_theta, best_score = mu.copy(), -np.inf

    for it in range(int(spec.iterations)):
        if progress is not None and progress.cancelled:
            logger.info(f"[RL] seed {seed} fold {fold.path}: cancelled at iteration {it}")
            break
        windows = _sample_windows(fold.train, rng, spec.eval_windows, spec.eval_window_bars)
        if not windows:
            break
        envs = []
        for a, b in windows:
            sub = data.slice(a, b)
            e = E.TradingEnv(spec, sub, risk_config, scaler=scaler, charter=charter)
            envs.append(e)

        population = rng.normal(mu, np.maximum(sigma, 1e-6), (int(spec.population), n))
        scores = np.zeros(len(population))
        for k, theta in enumerate(population):
            cand = template.copy().from_flat(theta)
            total, taken = 0.0, 0
            for e in envs:
                ep = _score(e, cand, seed=seed * 1000 + it)
                total += ep.score
                taken += ep.breakdown.n_trades
            # A candidate that takes no trades scores zero rather than being
            # excluded: "do nothing" is a legitimate policy and it must be
            # beatable, not unrepresented.
            scores[k] = total / max(1, len(envs))

        order = np.argsort(-scores)
        elite = population[order[:n_elite]]
        mu = elite.mean(axis=0)
        # A floor on sigma, so the search cannot collapse onto one point in the
        # first few iterations and then report that point as a result.
        sigma = np.maximum(elite.std(axis=0), 0.02)

        if scores[order[0]] > best_score:
            best_score = float(scores[order[0]])
            best_theta = population[order[0]].copy()

        history.append({"iteration": it, "best": round(float(scores[order[0]]), 6),
                        "mean": round(float(scores.mean()), 6),
                        "sigma": round(float(sigma.mean()), 4)})
        if progress is not None:
            frac = (it + 1) / max(1, int(spec.iterations))
            progress.set(stage="training", seed=seed, fold=fold.path, iteration=it + 1,
                         iterations=int(spec.iterations),
                         best=round(best_score, 6),
                         pct=round(pct_from + (pct_to - pct_from) * frac, 2))

    out = template.copy().from_flat(best_theta)
    out.seed = seed
    out.trained_iterations = len(history)
    return out, scaler, history


def train_ppo(spec: AgentSpec, data: E.EpisodeData, risk_config: dict, fold: E.Fold,
              seed: int, *, progress: Progress | None = None, charter=None,
              pct_from: float = 0.0, pct_to: float = 100.0,
              clip: float = 0.2, lr: float = 0.5, epochs: int = 4,
              entropy_bonus: float = 0.01
              ) -> tuple[pol.LinearPolicy, feat.Scaler, list]:
    """PPO's clipped surrogate objective, applied directly to the linear policy.

    WHY NOT `stable_baselines3`
    ---------------------------
    sb3 drives a Gymnasium env one `step()` at a time. The engine this env
    wraps is a BATCH simulator, and that is the entire point of section 4.4: a
    per-step simulator would not have the lambda spike fills, the measured
    per-symbol spreads, the minimum-stop floor or next-bar-open fills.
    Rewriting the engine to be steppable so a library can drive it is the tail
    wagging the dog, and wrapping it in a fake per-step shell would have the
    library optimise against a simulator that is not the one the result is
    reported from.

    So this is the ALGORITHM the plan specifies over the policy this project
    can actually serve, and the artefact records `ppo-direct` rather than `ppo`
    so nobody reads it as an sb3 result.

    The maths, for a softmax linear policy - short enough to state in full:

        pi(a|z)             = softmax(W z + b)[a]
        d log pi(a|z) / dW  = z outer (onehot(a) - pi)
        ratio               = pi_new(a|z) / pi_old(a|z)
        L                   = min(ratio * A, clip(ratio, 1-e, 1+e) * A)

    The advantage `A` is the decision's own reward minus the batch mean. No
    value network and no GAE, deliberately: an entry decision's reward arrives
    when its trade closes and does not depend on the NEXT entry decision, so
    this is a contextual bandit rather than a long-horizon MDP. Bootstrapping a
    value function across unrelated decisions would add variance and a second
    thing to overfit. The EXIT head is genuinely sequential, so it is improved
    by a small population search in the same loop.
    """
    rng = np.random.default_rng(seed)
    train_rows = E.rows_of(fold.train)
    if not len(train_rows):
        raise ValueError("fold has no training rows")
    scaler = feat.Scaler().fit(data.X[train_rows], data.names,
                               fitted_on=f"fold {fold.path} train rows ({len(train_rows)})")

    # A zero policy is uniform over the three actions, which is a good starting
    # distribution: it explores all three and prefers none.
    policy = pol.LinearPolicy(n_features=spec.observation_width, hidden=0,
                              exit_head=spec.exit_head, seed=seed,
                              n_stop=len(spec.stop_atr_choices) if spec.risk_head else 0,
                              n_rr=len(spec.rr_choices) if spec.risk_head else 0)
    history: list[dict] = []
    best_policy, best_score = policy.copy(), (-np.inf, -np.inf)

    for it in range(int(spec.iterations)):
        if progress is not None and progress.cancelled:
            logger.info(f"[RL] seed {seed} fold {fold.path}: cancelled at iteration {it}")
            break
        windows = _sample_windows(fold.train, rng, spec.eval_windows, spec.eval_window_bars)
        if not windows:
            break

        # -- collect, on-policy ---------------------------------------------
        Zs, acts, rewards = [], [], []
        score_sum, n_eps = 0.0, 0
        for a, b in windows:
            sub = data.slice(a, b)
            env = E.TradingEnv(spec, sub, risk_config, scaler=scaler, charter=charter)
            ep = env.rollout(policy, seed=seed * 1000 + it, explore=True, transitions=True)
            if not ep.transitions:
                continue
            idx = np.array([t[0] for t in ep.transitions], dtype=np.int64)
            Zs.append(env.Z[idx])
            acts.append(np.array([t[1] for t in ep.transitions], dtype=np.int64))
            rewards.append(np.array([t[2] for t in ep.transitions], dtype=float))
            score_sum += ep.score
            n_eps += 1
        if not Zs:
            history.append({"iteration": it, "note": "no decisions in this batch"})
            continue

        Zb = np.concatenate(Zs)
        ab = np.concatenate(acts)
        rb = np.concatenate(rewards)

        # Bars that produced nothing - flat, or a signal the risk layer refused -
        # are KEPT, with reward zero. A policy trained only on the bars it
        # traded learns that trading is always better than not trading.
        adv = rb - rb.mean()
        sd = float(adv.std())
        if sd <= 1e-12:
            history.append({"iteration": it, "decisions": int(len(rb)),
                            "note": "every decision scored the same - no gradient"})
            continue
        adv = adv / sd

        old_probs = policy.entry_probs(Zb)
        old_pa = old_probs[np.arange(len(ab)), ab]
        onehot = np.zeros_like(old_probs)
        onehot[np.arange(len(ab)), ab] = 1.0

        for _ in range(max(1, int(epochs))):
            P = policy.entry_probs(Zb)
            pa = P[np.arange(len(ab)), ab]
            ratio = pa / np.maximum(old_pa, 1e-12)
            # The clip is the whole mechanism: where the ratio has already moved
            # past the trust region in the direction that would keep increasing
            # the objective, this sample's gradient is switched off.
            inside = np.where(adv >= 0, ratio <= 1.0 + clip, ratio >= 1.0 - clip)
            w = np.where(inside, ratio * adv, 0.0)
            dlogits = (onehot - P) * w[:, None]
            # Entropy bonus, pushing back against collapsing onto one action in
            # the first few iterations and then reporting that as a result.
            dlogits += entropy_bonus * P * (_entropy(P)[:, None]
                                            + np.log(np.maximum(P, 1e-12)))
            policy.W_entry += lr * (Zb.T @ dlogits) / len(Zb)
            policy.b_entry += lr * dlogits.mean(axis=0)

        if spec.exit_head or spec.risk_head:
            policy = _improve_exit_head(policy, spec, data, risk_config, fold, rng,
                                        scaler, charter, seed * 7919 + it,
                                        tries=spec.exit_tries)

        eval_score = _evaluate(policy, spec, data, risk_config, fold, scaler, charter, seed)
        batch_score = score_sum / max(1, n_eps)
        # Ranked on the held-out evaluation first, with the on-policy batch as
        # the tie-break. Early iterations legitimately score 0.0 on evaluation
        # because the deterministic policy is not yet confident enough to take
        # a trade; without the tie-break the search would keep its FIRST
        # policy through every one of those iterations and learn nothing.
        rank = (eval_score, batch_score)
        if rank > best_score:
            best_score, best_policy = rank, policy.copy()
        history.append({"iteration": it, "decisions": int(len(rb)),
                        "batch_score": round(batch_score, 6),
                        "eval": round(eval_score, 6),
                        "entropy": round(float(_entropy(policy.entry_probs(Zb)).mean()), 4)})
        if progress is not None:
            frac = (it + 1) / max(1, int(spec.iterations))
            progress.set(stage="training", optimiser="ppo-direct", seed=seed,
                         fold=fold.path, iteration=it + 1, iterations=int(spec.iterations),
                         best=round(best_score[0], 6),
                         pct=round(pct_from + (pct_to - pct_from) * frac, 2))

    best_policy.seed = seed
    best_policy.trained_iterations = len(history)
    return best_policy, scaler, history


def _entropy(P: np.ndarray) -> np.ndarray:
    return -(P * np.log(np.maximum(P, 1e-12))).sum(axis=1)


MIN_EVAL_TRADES = 20


def _evaluate(policy: pol.LinearPolicy, spec: AgentSpec, data: E.EpisodeData,
              risk_config: dict, fold: E.Fold, scaler, charter, seed: int) -> float:
    """Deterministic score on a FIXED slice of the training rows.

    Fixed, so successive iterations are comparable - a moving evaluation window
    makes "it improved" indistinguishable from "it got an easier window". Still
    training rows: the test fold is untouched until the run is over.

    A score over fewer than `MIN_EVAL_TRADES` trades is returned as -inf, not
    as itself. Measured reason: an 18-iteration run picked its iteration-2
    policy on an evaluation score of +1.51 that came from three trades, and
    that policy then produced 12 trades on the test fold. Three trades is not
    an evaluation, and letting it win is how a trainer reports noise.
    """
    spans = [s for s in fold.train if s[1] - s[0] >= 500]
    if not spans:
        return float("-inf")
    a, b = max(spans, key=lambda s: s[1] - s[0])
    b = min(b, a + max(2000, spec.eval_window_bars // 2))
    env = E.TradingEnv(spec, data.slice(a, b), risk_config, scaler=scaler, charter=charter)
    ep = env.rollout(policy, seed=seed)
    if ep.breakdown.n_trades < MIN_EVAL_TRADES:
        return float("-inf")
    return ep.score


def _improve_exit_head(policy: pol.LinearPolicy, spec: AgentSpec, data: E.EpisodeData,
                       risk_config: dict, fold: E.Fold, rng, scaler, charter,
                       seed: int, tries: int = 6) -> pol.LinearPolicy:
    """A few perturbations of the exit and risk weights, kept only if they help.

    Only if they HELP, on the same window - an accept-if-not-worse rule drifts
    upward on noise. The exit head is the most overfit-prone component in W4
    and the real test is the ablation in `validate.py`, which runs the same
    agent with the head switched off.
    """
    # An unmeasurable base (-inf) means the first candidate that produces
    # enough trades wins, which is the right behaviour: it is the first one
    # that can be compared at all.
    best, best_score = policy, _evaluate(policy, spec, data, risk_config, fold,
                                         scaler, charter, seed)
    for _ in range(max(1, int(tries))):
        cand = policy.copy()
        if cand.exit_head:
            cand.W_exit = cand.W_exit + rng.normal(0, 0.15, cand.W_exit.shape)
            cand.b_exit = cand.b_exit + rng.normal(0, 0.05, cand.b_exit.shape)
        if cand.risk_head:
            # The stop and the target are chained with the exit in the same
            # way - where you put the stop decides what the exit head is
            # choosing between - so they are searched together rather than
            # given a gradient of their own.
            cand.W_stop = cand.W_stop + rng.normal(0, 0.15, cand.W_stop.shape)
            cand.b_stop = cand.b_stop + rng.normal(0, 0.05, cand.b_stop.shape)
            cand.W_rr = cand.W_rr + rng.normal(0, 0.15, cand.W_rr.shape)
            cand.b_rr = cand.b_rr + rng.normal(0, 0.05, cand.b_rr.shape)
        score = _evaluate(cand, spec, data, risk_config, fold, scaler, charter, seed)
        if score > best_score:
            best, best_score = cand, score
    return best


# -- the run ----------------------------------------------------------------


def train_agent(spec: AgentSpec, datasets: dict[str, pd.DataFrame], risk_config: dict,
                *, agent_id: str | None = None, root: Path | None = None,
                optimiser: str | None = None, charter=None,
                progress: Progress | None = None) -> TrainingRun:
    """Seeds x folds, on the pre-registered symbols.

    Every symbol in `spec.symbols` is trained and REPORTED, including the ones
    that turn out badly. Reporting only the ones that worked is survivorship
    bias in the experiment rather than in the code, which no probe catches.
    """
    root = Path(root or ARTEFACT_ROOT)
    agent_id = agent_id or f"{spec.name}-{uuid.uuid4().hex[:8]}"
    run = TrainingRun(agent_id=agent_id, spec=spec.to_dict(),
                      optimiser=available_optimiser(optimiser),
                      started_at=datetime.now(timezone.utc).isoformat())
    run.symbols_attempted = list(spec.attempted_symbols or spec.symbols)
    t0 = time.time()
    progress = progress or Progress(root / agent_id / "progress.json")
    clear_cancel(agent_id, root)

    trainer = train_ppo if run.optimiser == PPO else train_cem

    episodes: list[E.EpisodeData] = []
    for symbol in spec.symbols:
        frame = datasets.get(symbol)
        if frame is None or not len(frame):
            logger.warning(f"[RL] {symbol}: no data - skipped, and recorded as attempted")
            continue
        episodes.append(E.EpisodeData.build(symbol, spec.timeframe, frame, spec.features))
    if not episodes:
        run.error = "no data for any pre-registered symbol"
        run.finished_at = datetime.now(timezone.utc).isoformat()
        return run

    all_folds = E.cpcv_folds(min(len(d) for d in episodes), spec.cpcv_groups,
                             spec.cpcv_test_groups, spec.embargo_bars)
    folds = all_folds[:int(spec.max_cpcv_paths)]
    total = len(spec.seeds) * len(folds) * len(episodes)
    done = 0

    for data in episodes:
        for seed in spec.seeds:
            for fold in folds:
                if progress.cancelled:
                    run.error = "cancelled"
                    break
                span = (100.0 * done / total, 100.0 * (done + 1) / total)
                started = time.time()
                try:
                    policy, scaler, history = trainer(
                        spec, data, risk_config, fold, seed, progress=progress,
                        pct_from=span[0], pct_to=span[1], charter=charter)
                except Exception as exc:                # noqa: BLE001
                    logger.error(f"[RL] seed {seed} fold {fold.path} failed: {exc}")
                    done += 1
                    continue

                in_sample = [h.get("eval") for h in history
                             if isinstance(h.get("eval"), (int, float))]
                if not in_sample:
                    in_sample = [h.get("best") for h in history
                                 if isinstance(h.get("best"), (int, float))]
                res = SeedResult(seed=seed, fold=fold.path, policy=policy.to_dict(),
                                 scaler=scaler.to_dict(), history=history,
                                 train_score=float(max(in_sample)) if in_sample else 0.0,
                                 train_rows=fold.train_rows, test_rows=fold.test_rows,
                                 seconds=round(time.time() - started, 1))
                # The TEST fold, whole - never a sample.
                test_eps = []
                for a, b in fold.test:
                    sub = data.slice(a, b)
                    env = E.TradingEnv(spec, sub, risk_config, scaler=scaler, charter=charter)
                    test_eps.append(env.rollout(policy, seed=seed, label=f"test {a}-{b}"))
                rs = [t.r_net for ep in test_eps for t in ep.rewards]
                res.test_trades = len(rs)
                res.test_mean_r = float(np.mean(rs)) if rs else 0.0
                res.sharpe = sharpe_of(rs)
                res.test_adherence = float(np.mean(
                    [t.adherence for ep in test_eps for t in ep.rewards])) if rs else 0.0
                res.score = float(np.mean([ep.score for ep in test_eps])) if test_eps else 0.0
                run.results.append(res)
                if run.best is None or res.score > run.best.score:
                    run.best = res
                done += 1
                progress.set(stage="fold complete", pct=round(100.0 * done / total, 2),
                             seed=seed, fold=fold.path, trades=res.test_trades,
                             mean_r=round(res.test_mean_r, 5))
            if run.error == "cancelled":
                break
        if run.error == "cancelled":
            break

    run.seconds = time.time() - t0
    run.finished_at = datetime.now(timezone.utc).isoformat()
    save_run(run, root)
    progress.set(stage="complete", pct=100.0,
                 seeds=len(spec.seeds), folds=len(folds),
                 best=round(run.best.score, 6) if run.best else None)
    return run


def sharpe_of(rs, periods: float = 252.0) -> float:
    """Per-trade Sharpe, annualised by trade count rather than by calendar.

    Annualising a per-trade series by 252 assumes one trade a day, which is
    wrong for most of these strategies - so it is reported as a comparable
    number across runs, not as an annual figure, and `validate.py` deflates it
    for multiple testing before anybody reads it.
    """
    arr = np.asarray(list(rs or ()), dtype=float)
    if len(arr) < 2:
        return 0.0
    sd = float(arr.std(ddof=1))
    if sd <= 0:
        return 0.0
    return float(arr.mean() / sd * np.sqrt(periods))


def save_run(run: TrainingRun, root: Path | None = None) -> Path:
    d = Path(root or ARTEFACT_ROOT) / run.agent_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "training.json").write_text(json.dumps(run.to_dict(), indent=2), encoding="utf-8")
    if run.best:
        (d / "policy.json").write_text(json.dumps(run.best.policy), encoding="utf-8")
        (d / "scaler.json").write_text(json.dumps(run.best.scaler), encoding="utf-8")
    return d


def load_policy(agent_id: str, root: Path | None = None
                ) -> tuple[pol.LinearPolicy, feat.Scaler] | None:
    d = Path(root or ARTEFACT_ROOT) / agent_id
    try:
        p = pol.LinearPolicy.from_dict(json.loads((d / "policy.json").read_text(encoding="utf-8")))
        s = feat.Scaler.from_dict(json.loads((d / "scaler.json").read_text(encoding="utf-8")))
        return p, s
    except Exception:
        return None


def read_progress(agent_id: str, root: Path | None = None) -> dict:
    f = Path(root or ARTEFACT_ROOT) / agent_id / "progress.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {}


# -- the queue --------------------------------------------------------------


MAX_QUEUED = 4


class TrainingQueue:
    """One training job at a time, in a separate process, with a bounded backlog.

    Bounded because an unbounded queue is a way to run out of memory slowly.
    One at a time because training is CPU-bound and two jobs on the same cores
    finish later than two jobs in sequence - and because the API has to stay
    answerable, which this project has already lost once to work on the loop.
    """

    def __init__(self, max_queued: int = MAX_QUEUED):
        self._executor = None
        self._futures: dict[str, Any] = {}
        self.max_queued = int(max_queued)

    def _ensure(self):
        if self._executor is None:
            from concurrent.futures import ProcessPoolExecutor
            self._executor = ProcessPoolExecutor(max_workers=1)
        return self._executor

    @property
    def pending(self) -> list[str]:
        return [k for k, f in self._futures.items() if not f.done()]

    def submit(self, agent_id: str, payload: dict) -> str:
        if len(self.pending) >= self.max_queued:
            raise RuntimeError(
                f"{len(self.pending)} training jobs already queued - wait for one to finish; "
                "a deeper queue just moves the wait somewhere you cannot see it")
        if agent_id in self.pending:
            raise RuntimeError(f"{agent_id} is already training")
        self._futures[agent_id] = self._ensure().submit(_run_job, payload)
        return agent_id

    def status(self, agent_id: str) -> dict:
        f = self._futures.get(agent_id)
        state = "unknown"
        if f is not None:
            state = "done" if f.done() else "running" if f.running() else "queued"
        return {"agent_id": agent_id, "state": state, "progress": read_progress(agent_id)}

    def cancel(self, agent_id: str) -> bool:
        request_cancel(agent_id)
        f = self._futures.get(agent_id)
        # A queued job can be dropped outright; a running one is asked to stop
        # at the next candidate, which is where `Progress.cancelled` is read.
        return bool(f.cancel()) if f is not None and not f.running() else False

    def result(self, agent_id: str):
        f = self._futures.get(agent_id)
        return f.result() if f is not None and f.done() else None


queue = TrainingQueue()


def _run_job(payload: dict) -> dict:
    """The process entry point. Module level, because a closure cannot be pickled."""
    from backend.rl.datasets import load_frames

    spec = AgentSpec.from_dict(payload["spec"])
    risk_config = payload.get("risk_config") or {}
    frames = load_frames(payload.get("datasets") or {}, spec)
    charter = None
    if payload.get("charter"):
        from backend.rl.charter import StrategyCharter
        charter = StrategyCharter.from_dict(payload["charter"])
    run = train_agent(spec, frames, risk_config, agent_id=payload.get("agent_id"),
                      optimiser=payload.get("optimiser"), charter=charter)
    return run.to_dict()


def job_payload(spec: AgentSpec, datasets: dict, risk_config: dict, *,
                agent_id: str, optimiser: str | None = None, charter=None) -> dict:
    return {"spec": spec.to_dict(), "datasets": datasets, "risk_config": risk_config,
            "agent_id": agent_id, "optimiser": optimiser,
            "charter": charter.to_dict() if charter is not None else None}


def progress_callback(agent_id: str) -> Callable[[dict], None]:
    """For a caller that wants to forward progress over the existing WebSocket."""
    def push(state: dict) -> None:
        try:
            from backend.services.bot_service import bot_service
            bot_service.log_system_event(
                f"RL {agent_id}: {state.get('stage')} {state.get('pct')}%", "INFO", "RL")
        except Exception:
            pass
    return push
