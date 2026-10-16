"""
backend/rl/validate.py

The gate. Pre-registered, enforced in code, and designed to REJECT.

WHY THIS FILE IS THE PRODUCT
----------------------------
The agent is the easy part. Published RL trading results mostly die above
1-4 basis points of cost, and 1bp on EURUSD is about 0.1 pip - inside the
spread this project actually pays, before slippage. On Boom and Crash it is
worse: spike-side stops fill lambda toward the bar extreme, and a naive bar
model faked +0.46R on RANDOM entries. So cost realism and out-of-sample
discipline are the deliverable, and this module is where they are enforced.

Every check below returns a `Check` with its own verdict and its own number.
A failure does not stop the others from running: the full report is worth more
than the first reason it failed, and rejections are KEPT, because the record
of what did not work is worth more than another candidate.

THE CHECKS
----------
    seeds            >= 5, Sharpe dispersion <= 1.0
    cpcv             >= 8 paths, PBO < 20%
    deflated sharpe  positive after the multiple-testing haircut
    cost stress      still positive at 2x measured spread and slippage
    regime split     positive in >= 2 of 3 independent windows
    broker           positive on both brokers' bars, or scoped and labelled
    adherence        >= 0.6 mean against the APPROVED charter
    exit head        beats the same agent with the head disabled
    charter          gates executable AND the thesis reproduces the policy
    leak probe       every feature prefix-stable
    shuffled labels  break-even minus costs
    shifted entry    degrades by one bar rather than collapsing
    sample size      >= 100 trades, or there is nothing to conclude
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

import numpy as np

from typing import Any

from backend.rl import env as E
from backend.rl import leakguard as lg
from backend.rl import policy as pol
from backend.rl.spec import AgentSpec

# The bars, as data so the report can print them beside the measurements and a
# changed bar is visible in a diff rather than buried in an if.
BARS = {
    "min_seeds": 5,
    "max_sharpe_dispersion": 1.0,
    "min_cpcv_paths": 8,
    "max_pbo": 0.20,
    "min_deflated_sharpe": 0.0,
    "cost_stress_multiplier": 2.0,
    "min_regime_windows": 2,
    "regime_windows": 3,
    "min_adherence": 0.6,
    "min_trades": 100,
    "min_charter_fidelity": 0.65,
    # Mode B only: the agent must beat the rule it was built on, on the same
    # setups and the same bars. Zero rather than a margin - the comparison is
    # already paired, so any positive difference is a real one, and demanding
    # a margin on top would just be a second arbitrary number.
    "min_base_improvement": 0.0,
}


@dataclass
class Check:
    name: str
    passed: bool
    value: float | None = None
    bar: float | None = None
    detail: str = ""
    skipped: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def line(self) -> str:
        mark = "skip" if self.skipped else ("PASS" if self.passed else "FAIL")
        got = "" if self.value is None else f"  {self.value:+.4f}"
        bar = "" if self.bar is None else f" (bar {self.bar:+.4f})"
        return f"  {mark:4s} {self.name:22s}{got}{bar}  {self.detail}"


@dataclass
class Report:
    agent_id: str
    spec_hash: str = ""
    checks: list = field(default_factory=list)
    expectancy_r: float | None = None
    trades: int = 0
    sharpe: float | None = None
    deflated_sharpe: float | None = None
    pbo: float | None = None
    adherence: float | None = None
    optimiser: str = ""
    mode: str = "A"
    base_strategy: str = ""
    symbols_attempted: list = field(default_factory=list)
    cost_model: dict = field(default_factory=dict)
    created_at: str = ""
    # Not part of the report's content: excluded from compare and repr so a
    # watcher cannot change what two reports mean when held side by side.
    on_check: Any = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        self.created_at = self.created_at or datetime.now(timezone.utc).isoformat()

    @property
    def passed(self) -> bool:
        """Every non-skipped check passed, AND at least one check actually ran.

        The second half is not pedantry. `all()` over an empty sequence is
        True, so a report whose every check was skipped - no training run, no
        charter, no second broker, no trades - read as PASS. Measured: a report
        containing one skipped check and nothing else returned `passed=True`,
        which is the single most dangerous answer this class can give, because
        the whole point of the gate is to refuse what it cannot measure.
        """
        ran = [c for c in self.checks if not c.skipped]
        return bool(ran) and all(c.passed for c in ran)

    @property
    def skipped(self) -> list:
        """Named, because "could not be measured" has to be visible next to a
        verdict rather than absorbed into it."""
        return [c for c in self.checks if c.skipped]

    @property
    def failures(self) -> list:
        return [c for c in self.checks if not c.passed and not c.skipped]

    def add(self, check: Check) -> Check:
        self.checks.append(check)
        if self.on_check is not None:
            try:
                self.on_check(check)
            except Exception:          # noqa: BLE001
                # A broken watcher must never fail the gate. The report is
                # the product; telling someone about it is not.
                pass
        return check

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id, "spec_hash": self.spec_hash,
            "passed": self.passed, "optimiser": self.optimiser,
            "mode": self.mode, "base_strategy": self.base_strategy,
            "expectancy_r": self.expectancy_r, "trades": self.trades,
            "sharpe": self.sharpe, "deflated_sharpe": self.deflated_sharpe,
            "pbo": self.pbo, "adherence": self.adherence,
            "symbols_attempted": self.symbols_attempted,
            "cost_model": self.cost_model,
            "checks": [c.to_dict() for c in self.checks],
            "failures": [c.name for c in self.failures],
            "skipped": [c.name for c in self.skipped],
            "bars": BARS, "created_at": self.created_at,
        }

    def text(self) -> str:
        head = (f"{self.agent_id}  spec {self.spec_hash}  {self.optimiser}  "
                f"mode {self.mode}"
                + (f" on {self.base_strategy}" if self.base_strategy else "") + "\n"
                f"  {self.trades} trades | "
                f"{'n/a' if self.expectancy_r is None else f'{self.expectancy_r:+.4f}R'} | "
                f"verdict {'PASS' if self.passed else 'REJECTED'}")
        return head + "\n" + "\n".join(c.line for c in self.checks)


# -- the statistics ---------------------------------------------------------


def probability_of_backtest_overfitting(in_sample: np.ndarray,
                                        out_sample: np.ndarray) -> float | None:
    """PBO: how often the configuration that looked best IN SAMPLE lands below
    the median of the out-of-sample results.

    Both arguments are `(configurations, paths)` matrices - one row per
    candidate (here, per seed) and one column per CPCV path. For each path the
    in-sample winner is found, and PBO is the fraction of paths where that
    winner's own out-of-sample result sits below the median of what every
    candidate achieved out of sample on that path.

    This is the number the literature puts at the centre of backtest
    overfitting, and the discipline is to REJECT above 20% rather than ship the
    best-looking candidate. It needs at least two candidates and two paths: with
    one candidate there is no selection, and with no selection there is no
    selection bias to measure - which is why a single-seed run cannot produce
    this number at all rather than producing a flattering zero.
    """
    IS = np.asarray(in_sample, dtype=float)
    OOS = np.asarray(out_sample, dtype=float)
    if IS.ndim != 2 or IS.shape != OOS.shape:
        return None
    n_configs, n_paths = IS.shape
    if n_configs < 2 or n_paths < 2:
        return None
    worse = 0
    counted = 0
    for j in range(n_paths):
        col_is, col_oos = IS[:, j], OOS[:, j]
        if not np.isfinite(col_is).any() or not np.isfinite(col_oos).any():
            continue
        pick = int(np.nanargmax(col_is))
        median = float(np.nanmedian(col_oos))
        counted += 1
        if col_oos[pick] < median:
            worse += 1
    return float(worse / counted) if counted else None


def pbo_matrices(results) -> tuple[np.ndarray, np.ndarray] | None:
    """Build the (seed x path) matrices PBO needs from a training run.

    The seed is the candidate and the CPCV path is the path, which is the
    natural mapping here: seeds are what this trainer varies, and the plan's own
    seed floor exists because seed choice is a selection the result is sensitive
    to.
    """
    seeds = sorted({r.seed for r in results})
    paths = sorted({r.fold for r in results})
    if len(seeds) < 2 or len(paths) < 2:
        return None
    IS = np.full((len(seeds), len(paths)), np.nan)
    OOS = np.full((len(seeds), len(paths)), np.nan)
    si = {s: i for i, s in enumerate(seeds)}
    pi = {p: i for i, p in enumerate(paths)}
    for r in results:
        IS[si[r.seed], pi[r.fold]] = r.train_score
        OOS[si[r.seed], pi[r.fold]] = r.test_mean_r
    return IS, OOS


def _norm_ppf(q: float) -> float:
    """The inverse normal CDF. scipy when it is there, Acklam otherwise.

    Guarded because this module is imported by the API, and the API must not
    fail to start over a statistics helper.
    """
    try:
        from scipy import stats
        return float(stats.norm.ppf(q))
    except Exception:
        # Acklam's rational approximation, accurate to about 1.15e-9.
        a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
             1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
        b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
             6.680131188771972e+01, -1.328068155288572e+01]
        c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
             -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
        d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
             3.754408661907416e+00]
        plow, phigh = 0.02425, 1 - 0.02425
        if q < plow:
            r = math.sqrt(-2 * math.log(q))
            return (((((c[0] * r + c[1]) * r + c[2]) * r + c[3]) * r + c[4]) * r + c[5]) / \
                   ((((d[0] * r + d[1]) * r + d[2]) * r + d[3]) * r + 1)
        if q > phigh:
            r = math.sqrt(-2 * math.log(1 - q))
            return -(((((c[0] * r + c[1]) * r + c[2]) * r + c[3]) * r + c[4]) * r + c[5]) / \
                     ((((d[0] * r + d[1]) * r + d[2]) * r + d[3]) * r + 1)
        r = q - 0.5
        rr = r * r
        return (((((a[0] * rr + a[1]) * rr + a[2]) * rr + a[3]) * rr + a[4]) * rr + a[5]) * r / \
               (((((b[0] * rr + b[1]) * rr + b[2]) * rr + b[3]) * rr + b[4]) * rr + 1)


EULER = 0.5772156649015329


def expected_max_sharpe(n_trials: int, trial_sd: float) -> float:
    """What the BEST of `n_trials` Sharpe ratios looks like when none of them
    are real.

    This is the haircut. Try 140 configurations on noise and the best one has a
    perfectly respectable Sharpe; the question is whether the observed one
    beats what noise alone would have produced. `trial_sd` is the spread of the
    Sharpes actually tried - the more they disagree, the higher the bar, which
    is the right direction: wide disagreement across seeds is itself evidence
    that the thing being measured is noise.
    """
    n = max(2, int(n_trials))
    sd = max(float(trial_sd), 1e-9)
    return float(sd * ((1 - EULER) * _norm_ppf(1 - 1.0 / n)
                       + EULER * _norm_ppf(1 - 1.0 / (n * math.e))))


def deflated_sharpe(sharpe: float, n_trials: int, n_obs: int, *,
                    skew: float = 0.0, kurtosis: float = 3.0,
                    trial_sd: float | None = None) -> dict | None:
    """Sharpe after the multiple-testing haircut, and the probability it is real.

    Returns both numbers because they answer different questions and the plan's
    bar is the first one:

      `haircut`     observed Sharpe minus the expected maximum from noise.
                    Positive is the bar: the result beats what trying this many
                    times would have produced by chance.
      `probability` the deflated Sharpe ratio proper - the probability that the
                    true Sharpe is above zero, given the number of attempts and
                    the non-normality of trade returns.

    Skew and kurtosis are included because trade returns are not normal and
    pretending they are flatters every result: a long-tailed loss distribution
    makes the Sharpe estimator noisier than the normal formula admits.
    """
    if n_obs < 3 or n_trials < 1 or not np.isfinite(sharpe):
        return None
    sd = trial_sd if trial_sd is not None else max(abs(float(sharpe)) * 0.5, 1e-9)
    expected = expected_max_sharpe(n_trials, sd)
    # Variance of the Sharpe estimator under non-normal returns.
    var = (1.0 - skew * sharpe + (kurtosis - 1.0) / 4.0 * sharpe ** 2) / max(1, n_obs - 1)
    se = math.sqrt(max(var, 1e-12))
    z = (float(sharpe) - expected) / se
    try:
        from scipy import stats
        prob = float(stats.norm.cdf(z))
    except Exception:
        prob = float(0.5 * (1.0 + math.erf(z / math.sqrt(2.0))))
    return {"haircut": float(sharpe) - expected, "expected_max": expected,
            "probability": prob, "trials": int(n_trials), "observations": int(n_obs)}


def t_stat(rs) -> tuple[float, int]:
    arr = np.asarray(list(rs or ()), dtype=float)
    if len(arr) < 2:
        return 0.0, len(arr)
    sd = float(arr.std(ddof=1))
    if sd <= 0:
        return 0.0, len(arr)
    return float(arr.mean() / (sd / math.sqrt(len(arr)))), len(arr)


# -- the gate ---------------------------------------------------------------


# The gate's stages, in the order they run, with a rough share of the total
# time each one takes. The shares are only used to turn "stage 4 of 8" into a
# percentage that does not jump around; they came from timing the §6.9 run on
# 78,895 Crash 1000 bars.
GATE_STAGES = (
    ("rollouts", "running the policy over every purged test fold", 0.30),
    ("sample", "sample size, seeds and seed dispersion", 0.01),
    ("pbo", "probability of backtest overfitting", 0.02),
    ("dsr", "deflated Sharpe against the number of attempts", 0.01),
    ("cost", "re-running at twice the measured cost", 0.15),
    ("regime", "splitting the history into independent windows", 0.15),
    ("broker", "the same rule on a second broker's bars", 0.02),
    ("adherence", "scoring the policy against its own charter", 0.02),
    ("exit", "what the exit head is worth on its own", 0.10),
    ("base", "whether it beats the strategy it was built on", 0.05),
    ("leak", "prefix-stability of every feature, at six cuts", 0.02),
    ("shuffled", "the shuffled-feature control", 0.08),
    ("shifted", "the one-bar-late control", 0.07),
)
_STAGE_AT = {}
_acc = 0.0
for _k, _d, _w in GATE_STAGES:
    _STAGE_AT[_k] = (_acc, _d)
    _acc += _w


def _say(progress, key: str, extra: str = "") -> None:
    """Mark a gate stage as started, if anyone is listening."""
    if progress is None:
        return
    pct_from, desc = _STAGE_AT.get(key, (0.0, key))
    progress.set(stage=key, stage_detail=desc, pct=round(pct_from * 100, 1))
    progress.event(f"{desc}{(' - ' + extra) if extra else ''}")


def validate(spec: AgentSpec, policy: pol.LinearPolicy, scaler, data: E.EpisodeData,
             risk_config: dict, *, agent_id: str, training_run=None, charter=None,
             broker_data: dict | None = None, optimiser: str = "",
             progress=None) -> Report:
    """Run the whole gate. Nothing here stops early on a failure.

    `broker_data` maps a broker name to another `EpisodeData` for the same
    symbol, which is how the broker cross-check is done: the same rule on two
    brokers' bars. 8 of 15 paired cells in this project's own playbook flip
    SIGN between Deriv and FundedNext, so a result on one is not a result on
    the other and the report says which it is.
    """
    rep = Report(agent_id=agent_id, spec_hash=spec.content_hash, optimiser=optimiser,
                 mode=spec.mode, base_strategy=spec.base_strategy,
                 symbols_attempted=list(spec.attempted_symbols or spec.symbols))

    if progress is not None:
        done = []

        def _watch(check):
            done.append(check)
            verdict = "SKIP" if check.skipped else "PASS" if check.passed else "FAIL"
            shown = "" if check.value is None else f" {check.value:+.4f}"
            against = "" if check.bar is None else f" (bar {check.bar:g})"
            progress.set(checks_done=len(done),
                         checks=[c.to_dict() for c in done])
            progress.event(f"{verdict} {check.name}{shown}{against}"
                           + (f" - {check.detail}" if check.detail else ""))

        rep.on_check = _watch

    folds = E.cpcv_folds(len(data), spec.cpcv_groups, spec.cpcv_test_groups,
                         spec.embargo_bars)[:spec.max_cpcv_paths]
    _say(progress, "rollouts", f"{len(folds)} purged paths over {len(data):,} bars")

    # -- the headline run, on every test fold ------------------------------
    test_eps, rs = [], []
    for i, f in enumerate(folds):
        if progress is not None:
            progress.set(fold=f.path, folds_done=i, folds_total=len(folds),
                         pct=round(100 * _STAGE_AT["rollouts"][0]
                                   + 30.0 * i / max(1, len(folds)), 1))
        for a, b in f.test:
            env = E.TradingEnv(spec, data.slice(a, b), risk_config,
                               scaler=scaler, charter=charter)
            ep = env.rollout(policy, label=f"path {f.path} {a}-{b}", transitions=True)
            test_eps.append(ep)
            rs += [t.r_net for t in ep.rewards]
    rep.trades = len(rs)
    rep.expectancy_r = float(np.mean(rs)) if rs else None
    rep.cost_model = test_eps[0].cost_model if test_eps else {}
    from backend.rl.train import sharpe_of
    rep.sharpe = sharpe_of(rs)

    _say(progress, "sample", f"{rep.trades} trades at {rep.expectancy_r:+.4f}R" if rep.expectancy_r is not None else f"{rep.trades} trades")
    # -- sample size -------------------------------------------------------
    rep.add(Check("sample size", rep.trades >= BARS["min_trades"], float(rep.trades),
                  float(BARS["min_trades"]),
                  "a verdict over fewer trades than this is not a verdict"))

    # -- seeds -------------------------------------------------------------
    if training_run is not None:
        disp = training_run.sharpe_dispersion
        seeds = len({r.seed for r in training_run.results})
        rep.add(Check("seeds", seeds >= BARS["min_seeds"], float(seeds),
                      float(BARS["min_seeds"]), "one seed is one lucky initialisation"))
        ok = np.isfinite(disp) and disp <= BARS["max_sharpe_dispersion"]
        rep.add(Check("seed dispersion", bool(ok),
                      None if not np.isfinite(disp) else float(disp),
                      BARS["max_sharpe_dispersion"],
                      "Sharpe spread across seeds; wide means there is no result"))
    else:
        rep.add(Check("seeds", False, skipped=True,
                      detail="no training run supplied - cannot check seed dispersion"))

    # -- CPCV + PBO --------------------------------------------------------
    rep.add(Check("cpcv paths", len(folds) >= BARS["min_cpcv_paths"], float(len(folds)),
                  float(BARS["min_cpcv_paths"]), "purged and embargoed folds"))
    mats = pbo_matrices(training_run.results) if training_run is not None else None
    if mats is not None:
        rep.pbo = probability_of_backtest_overfitting(*mats)
        rep.add(Check("PBO", rep.pbo is not None and rep.pbo < BARS["max_pbo"],
                      rep.pbo, BARS["max_pbo"],
                      "how often the in-sample winner is below the out-of-sample median"))
    else:
        rep.add(Check("PBO", False, skipped=True,
                      detail="needs at least 2 seeds x 2 paths - with one candidate "
                             "there is no selection, so there is no selection bias"))

    # -- deflated Sharpe ---------------------------------------------------
    trials = (len(training_run.results) if training_run is not None else len(folds)) or 1
    arr = np.asarray(rs, dtype=float)
    sd_r = float(arr.std()) if len(arr) > 2 else 0.0
    skew = float(((arr - arr.mean()) ** 3).mean() / sd_r ** 3) if sd_r > 0 else 0.0
    kurt = float(((arr - arr.mean()) ** 4).mean() / sd_r ** 4) if sd_r > 0 else 3.0
    trial_sharpes = [r.sharpe for r in training_run.results] if training_run is not None else []
    trial_sd = float(np.std(trial_sharpes)) if len(trial_sharpes) > 1 else None
    dsr = deflated_sharpe(rep.sharpe or 0.0, trials, len(rs), skew=skew, kurtosis=kurt,
                          trial_sd=trial_sd)
    rep.deflated_sharpe = None if dsr is None else round(dsr["haircut"], 4)
    rep.add(Check("deflated Sharpe",
                  dsr is not None and dsr["haircut"] > BARS["min_deflated_sharpe"],
                  rep.deflated_sharpe, BARS["min_deflated_sharpe"],
                  "n/a" if dsr is None else
                  f"Sharpe {rep.sharpe:+.3f} against {dsr['expected_max']:+.3f} expected from "
                  f"{trials} attempts on noise; P(real) {dsr['probability']:.1%}"))

    _say(progress, "cost")
    # -- cost stress -------------------------------------------------------
    stressed = []
    for f in folds[:max(1, len(folds) // 2)]:
        for a, b in f.test:
            env = E.TradingEnv(spec, data.slice(a, b), risk_config,
                               scaler=scaler, charter=charter)
            ep = env.rollout(policy, cost_multiplier=BARS["cost_stress_multiplier"])
            stressed += [t.r_net for t in ep.rewards]
    stressed_mean = float(np.mean(stressed)) if stressed else None
    rep.add(Check("cost stress 2x", stressed_mean is not None and stressed_mean > 0,
                  stressed_mean, 0.0,
                  "the measured cost model priced twice as high"))

    _say(progress, "regime")
    # -- regime split ------------------------------------------------------
    n = len(data)
    wins = 0
    windows = int(BARS["regime_windows"])
    detail_bits = []
    for k in range(windows):
        a, b = int(k * n / windows), int((k + 1) * n / windows)
        env = E.TradingEnv(spec, data.slice(a, b), risk_config, scaler=scaler, charter=charter)
        ep = env.rollout(policy)
        m = ep.breakdown.mean_r if ep.breakdown.n_trades else 0.0
        detail_bits.append(f"{m:+.3f}R/{ep.breakdown.n_trades}")
        if ep.breakdown.n_trades >= 20 and m > 0:
            wins += 1
    rep.add(Check("regime split", wins >= BARS["min_regime_windows"], float(wins),
                  float(BARS["min_regime_windows"]),
                  "independent windows: " + ", ".join(detail_bits)))

    # -- broker cross-check ------------------------------------------------
    if broker_data:
        results = {}
        for broker, bdata in broker_data.items():
            env = E.TradingEnv(spec, bdata, risk_config, scaler=scaler, charter=charter)
            ep = env.rollout(policy)
            results[broker] = (ep.breakdown.mean_r, ep.breakdown.n_trades)
        positive = [b for b, (m, t) in results.items() if t >= 20 and m > 0]
        detail = ", ".join(f"{b} {m:+.3f}R/{t}" for b, (m, t) in results.items())
        rep.add(Check("broker cross-check", len(positive) == len(results),
                      float(len(positive)), float(len(results)),
                      detail + ("" if len(positive) == len(results)
                                else " - scope the agent to the broker it works on, and label it")))
    else:
        rep.add(Check("broker cross-check", False, skipped=True,
                      detail="only one broker's bars supplied; the result is scoped to it"))

    # -- adherence ---------------------------------------------------------
    if charter is not None:
        adh = [t.adherence for ep in test_eps for t in ep.rewards]
        rep.adherence = float(np.mean(adh)) if adh else 0.0
        rep.add(Check("adherence", rep.adherence >= BARS["min_adherence"], rep.adherence,
                      BARS["min_adherence"],
                      "against the charter it is measured on; below this the stated "
                      "strategy is fiction"))
    else:
        rep.add(Check("adherence", False, skipped=True, detail="no charter supplied"))

    # -- the exit-head ablation -------------------------------------------
    if spec.exit_head:
        off = []
        for f in folds:
            for a, b in f.test:
                env = E.TradingEnv(spec, data.slice(a, b), risk_config,
                                   scaler=scaler, charter=charter)
                ep = env.rollout(policy, exit_head=False)
                off += [t.r_net for t in ep.rewards]
        off_mean = float(np.mean(off)) if off else None
        delta = (rep.expectancy_r - off_mean) if (rep.expectancy_r is not None
                                                  and off_mean is not None) else None
        rep.add(Check("exit head", delta is not None and delta > 0, delta, 0.0,
                      f"with the head {rep.expectancy_r if rep.expectancy_r is None else f'{rep.expectancy_r:+.4f}'}R "
                      f"against {off_mean if off_mean is None else f'{off_mean:+.4f}'}R without it "
                      f"- the most overfit-prone part of this agent"))
    else:
        rep.add(Check("exit head", True, skipped=True, detail="no exit head on this agent"))

    # -- the charter reproduces the policy --------------------------------
    if charter is not None:
        from backend.rl.distill import fidelity
        env = E.TradingEnv(spec, data, risk_config, scaler=scaler, charter=charter)
        fid = fidelity(charter, policy, env.observations_for(policy), data.names,
                       spec.min_confidence)
        executable = all(g.feature in set(data.names) for g in charter.gates) and bool(charter.gates)
        rep.add(Check("charter fidelity",
                      executable and fid["balanced"] >= BARS["min_charter_fidelity"],
                      fid["balanced"], BARS["min_charter_fidelity"],
                      f"balanced accuracy reproducing the policy's own decisions on "
                      f"{fid['bars']} bars"
                      + ("" if executable else " - and some gates name features "
                                               "the agent cannot see")))
    else:
        rep.add(Check("charter fidelity", False, skipped=True, detail="no charter supplied"))

    # -- mode B: beat the rule you were built on ---------------------------
    if spec.base_strategy:
        base_rs, agent_rs = [], []
        for f in folds:
            for a, b in f.test:
                sub = data.slice(a, b)
                if not sub.base_signals:
                    continue
                e = E.TradingEnv(spec, sub, risk_config, scaler=scaler, charter=charter)
                base_rs += [t.r_net for t in e.baseline_episode().rewards]
                agent_rs += [t.r_net for t in e.rollout(policy).rewards]
        base_mean = float(np.mean(base_rs)) if base_rs else None
        agent_mean = float(np.mean(agent_rs)) if agent_rs else None
        delta = (agent_mean - base_mean) if (base_mean is not None
                                             and agent_mean is not None) else None
        rep.add(Check("beats its base strategy",
                      delta is not None and delta > BARS["min_base_improvement"],
                      delta, BARS["min_base_improvement"],
                      f"{spec.base_strategy} alone "
                      f"{'n/a' if base_mean is None else f'{base_mean:+.4f}'}R over "
                      f"{len(base_rs)} trades against the agent's "
                      f"{'n/a' if agent_mean is None else f'{agent_mean:+.4f}'}R over "
                      f"{len(agent_rs)} - the same setups, the same bars"))
        rep.add(Check("takes enough of its base's setups",
                      bool(agent_rs) and len(agent_rs) >= 0.1 * max(1, len(base_rs)),
                      float(len(agent_rs)), 0.1 * max(1, len(base_rs)),
                      "an agent that declines almost everything has not optimised "
                      "the strategy, it has switched it off"))
    else:
        rep.add(Check("beats its base strategy", True, skipped=True,
                      detail="mode A - this agent has no base strategy to beat"))

    _say(progress, "leak")
    # -- the leak probe ----------------------------------------------------
    try:
        from backend.rl.features import assert_registry_causal
        findings = assert_registry_causal(names=spec.features)
        rep.add(Check("leak probe", True, float(len(findings)), None,
                      f"{len(findings)} features prefix-stable over six cuts"))
    except lg.LookaheadError as exc:
        rep.add(Check("leak probe", False, None, None, str(exc)[:200]))

    _say(progress, "shuffled")
    # -- shuffled labels ---------------------------------------------------
    shuffled = []
    for f in folds[:max(1, len(folds) // 2)]:
        for a, b in f.test:
            env = E.TradingEnv(spec, data.slice(a, b), risk_config,
                               scaler=scaler, charter=charter)
            ep = env.rollout(policy, shuffle_features=True, seed=99)
            shuffled += [t.r_net for t in ep.rewards]
    v = lg.shuffled_label_verdict(shuffled, cost_r=0.0) if shuffled else None
    rep.add(Check("shuffled labels", bool(v.passed) if v else False,
                  float(np.mean(shuffled)) if shuffled else None, 0.0,
                  v.detail if v else "no trades on the shuffled control",
                  skipped=not shuffled))

    _say(progress, "shifted")
    # -- shifted entry -----------------------------------------------------
    shifted = []
    for f in folds:
        for a, b in f.test:
            env = E.TradingEnv(spec, data.slice(a, b), risk_config,
                               scaler=scaler, charter=charter)
            ep = env.rollout(policy, shift_entry=1)
            shifted += [t.r_net for t in ep.rewards]
    sv = lg.shift_verdict(rs, shifted) if (rs and shifted) else None
    rep.add(Check("shifted entry", bool(sv.passed) if sv else False,
                  float(np.mean(shifted)) if shifted else None, None,
                  sv.detail if sv else "not enough trades to compare",
                  skipped=sv is None))


    if progress is not None:
        # "scored" rather than "complete": the gate has an answer, but
        # storing it is the caller's job and has not happened yet. The route
        # overwrites this with "complete" once the row is written.
        progress.set(stage="scored", pct=100.0, passed=rep.passed,
                     trades=rep.trades, expectancy_r=rep.expectancy_r,
                     checks_done=len(rep.checks),
                     failures=[c.name for c in rep.failures],
                     skipped_checks=[c.name for c in rep.skipped])
        progress.event(
            f"{'PASSED' if rep.passed else 'REJECTED'}: {rep.trades} trades at "
            f"{(rep.expectancy_r or 0.0):+.4f}R, "
            f"{len(rep.failures)} failed / {len(rep.skipped)} skipped of "
            f"{len(rep.checks)} checks")

    return rep


# -- supervision's own gate -------------------------------------------------


def validate_supervisor(results: list, *, min_trades: int = 100) -> Report:
    """The supervisor bar, which is stricter because the baseline is sharper.

    `results` is a list of `SupervisionResult`, one per supervised strategy.
    The comparison is the same strategy, same seeds, same folds, same bars,
    unsupervised - not "did the agent make money", which is the wrong question
    for something that never opens a trade.
    """
    rep = Report(agent_id="supervisor")
    if not results:
        rep.add(Check("supervised vs unsupervised", False, skipped=True,
                      detail="no supervised runs"))
        return rep

    deltas = [r.delta_r for r in results]
    rep.expectancy_r = float(np.mean(deltas))
    rep.trades = sum(len(r.supervised.trades) for r in results)
    rep.add(Check("supervised vs unsupervised", all(d > 0 for d in deltas),
                  float(np.mean(deltas)), 0.0,
                  "; ".join(f"{r.strategy_id} {r.delta_r:+.4f}R" for r in results)))

    harmed = [r.strategy_id for r in results if r.delta_r <= 0]
    rep.add(Check("harms no strategy", not harmed, float(len(harmed)), 0.0,
                  "scope the agent to the strategies it helps: "
                  + ", ".join(harmed) if harmed else "none made worse"))

    cutters = [r.strategy_id for r in results if r.cutting_winners]
    rep.add(Check("winner protection", not cutters, float(len(cutters)), 0.0,
                  ("cutting winners on " + ", ".join(cutters)) if cutters
                  else "trades closed early averaged no better than trades let run"))

    thin = [r.strategy_id for r in results if len(r.supervised.trades) < min_trades]
    rep.add(Check("trade count", not thin, float(rep.trades), float(min_trades),
                  ("too few supervised trades on " + ", ".join(thin)) if thin
                  else "measurable on every strategy"))
    return rep
