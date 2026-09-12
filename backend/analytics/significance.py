"""
backend/analytics/significance.py — [P5.9 / P5.10]

Is this result real, or is it the search that found it?

WHY THIS EXISTS
---------------
`research/24 §7` catalogues six measurement errors that each produced a
confident, wrong answer. Two of them are structural and neither is visible in a
profit factor:

**Overlapping trades.** DriftJumpAlpha appeared to earn +0.2039 R out of sample.
The control — RANDOM long entries at DJA's exact geometry, no entry logic at all
— read as a 4-sigma edge when trades overlapped and as fair when they did not:

    sampling         n       expectancy    t vs fair
    overlapping      3,998   +0.1577 R     +3.99
    non-overlapping    685   +0.0499 R     +0.56

A strategy with no logic whatsoever scores 4 sigma if you let its trades share
price path. Re-scoring the whole book overlap-aware: 87 cells, 7,143 trades,
2 significant at t>2 — exactly what chance predicts. Your own runs average 1.27
concurrent positions, so they inherit this.

**Data-mining deflation.** A daily-reversal candidate passed four validation
stages: correct pooling (t=+2.30), an artifact test it improved on, an IID
bootstrap at p=0.0000, and simulated markets at P(>0)=1.00. Then it failed the
fifth — across ~84 hypotheses searched, the null expects a maximum |t| of 2.64,
and 2.30 sits inside that band. On 57 years it lost 9.2%/yr and flipped sign by
decade. Had it shipped on the first four stages it would have been a losing
strategy in the most recent out-of-sample period.

So: a t-statistic that ignores overlap is inflated, and one that ignores how
many things you tried is not a t-statistic at all. Everything here computes the
corrected versions, and `assess()` refuses to call anything significant that
does not clear both.

WHAT THE NUMBERS MEAN
---------------------
- `t_naive`        what the old reports printed. Keep it only to show the gap.
- `t_nonoverlap`   the honest one: the same strategy on a non-overlapping subset.
- `deflated_sharpe`  P(true Sharpe > 0) after correcting for trials, skew and
                     kurtosis (Bailey & Lopez de Prado). Above 0.95 is the bar.
- `pbo`            probability of backtest overfitting via combinatorially
                   symmetric cross-validation. Above 0.5 means your selection
                   procedure is worse than a coin flip out of sample.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np
from scipy import stats as _st

from backend.utils.logger import get_logger

logger = get_logger(__name__)

EULER_MASCHERONI = 0.5772156649015329


# ── overlap ──────────────────────────────────────────────────────────────────

def non_overlapping_subset(
    entries: Sequence[float],
    exits: Sequence[float],
) -> list[int]:
    """Indices of a maximal set of trades whose holding periods do not overlap.

    Greedy earliest-exit-first, which is the classic interval-scheduling optimum:
    repeatedly take the trade that frees the resource soonest. That yields the
    LARGEST possible independent subset, so the honest t-statistic is computed on
    as much data as the overlap allows rather than an arbitrary thinning.
    """
    order = sorted(range(len(entries)), key=lambda i: (exits[i], entries[i]))
    picked: list[int] = []
    last_exit = -math.inf
    for i in order:
        if entries[i] >= last_exit:
            picked.append(i)
            last_exit = exits[i]
    return sorted(picked)


def effective_sample_size(
    entries: Sequence[float],
    exits: Sequence[float],
) -> float:
    """Average concurrency-adjusted count of independent observations.

    n / mean_concurrency. A book averaging 1.27 open positions has roughly 79% of
    its nominal sample size, so its standard errors are ~12% too small.
    """
    n = len(entries)
    if n == 0:
        return 0.0
    events: list[tuple[float, int]] = []
    for a, b in zip(entries, exits):
        events.append((float(a), 1))
        events.append((float(b), -1))
    events.sort()
    cur = 0
    area = 0.0
    prev = events[0][0]
    for t, d in events:
        area += cur * (t - prev)
        cur += d
        prev = t
    span = events[-1][0] - events[0][0]
    if span <= 0:
        return float(n)
    mean_conc = area / span
    # Concurrency BELOW 1 means the book sat idle between trades. Idle time adds
    # no information, so it must not inflate the sample: without the floor,
    # 50 well-spaced trades returned an effective n of 55, which would make every
    # standard error computed from it too small — the exact error this function
    # exists to prevent, in the opposite direction.
    return n / max(mean_conc, 1.0)


# ── data-mining deflation ────────────────────────────────────────────────────

def expected_max_abs_t(n_trials: int) -> float:
    """Expected maximum |t| across `n_trials` independent draws from the null.

    This is the bar a result SELECTED FROM A SEARCH must clear — not 2.0.
    Searching ~84 hypotheses moves it to ~2.69, which is exactly how the
    daily-reversal candidate's convincing-looking 2.30 turned out to be noise
    (research/24 §6).

    Bailey & Lopez de Prado's approximation for the expected maximum of N
    standard normals, in its TWO-SIDED form. The one-sided version — the one
    usually quoted — answers "expected max of N draws", but a t-statistic is
    significant in either direction, so the relevant quantity is the expected max
    of N ABSOLUTE draws and the tail probability is 1/(2N), not 1/N. Verified
    against 200,000-path Monte Carlo:

        N      simulated E[max|Z|]   two-sided   one-sided (wrong here)
        10          1.881              1.901        1.575
        50          2.509              2.531        2.276
        84          2.691              2.708        2.469
        500         3.241              3.255        3.053
    """
    n = max(int(n_trials), 1)
    if n == 1:
        return 0.0
    g = EULER_MASCHERONI
    return ((1 - g) * _st.norm.ppf(1 - 1.0 / (2 * n))
            + g * _st.norm.ppf(1 - 1.0 / (2 * n * math.e)))


# ── Sharpe ───────────────────────────────────────────────────────────────────

def probabilistic_sharpe_ratio(
    returns: Sequence[float],
    benchmark_sr: float = 0.0,
) -> float:
    """P(true Sharpe > benchmark), correcting for skew and kurtosis.

    A Sharpe computed on skewed, fat-tailed trade returns — which is every
    stop/target strategy — has a much wider confidence interval than the normal
    formula implies. This is the correction.
    """
    r = np.asarray(list(returns), dtype=float)
    n = len(r)
    if n < 3 or r.std(ddof=1) == 0:
        return float("nan")
    sr = r.mean() / r.std(ddof=1)
    skew = float(_st.skew(r))
    kurt = float(_st.kurtosis(r, fisher=False))
    denom = 1 - skew * sr + ((kurt - 1) / 4.0) * sr * sr
    if denom <= 0:
        return float("nan")
    z = (sr - benchmark_sr) * math.sqrt(n - 1) / math.sqrt(denom)
    return float(_st.norm.cdf(z))


def deflated_sharpe_ratio(
    returns: Sequence[float],
    n_trials: int,
    trial_sharpe_std: float | None = None,
) -> float:
    """Probabilistic Sharpe against the Sharpe the BEST OF `n_trials` would reach
    by chance alone.

    The benchmark is not zero. If you tried 60 configurations, the best of them
    has a positive Sharpe with near-certainty even when all 60 are worthless, and
    that expected maximum is what a selected result has to beat.
    """
    r = np.asarray(list(returns), dtype=float)
    n = len(r)
    if n < 3 or r.std(ddof=1) == 0:
        return float("nan")
    if trial_sharpe_std is None:
        # Absent the spread of the actual trial Sharpes, the null spread of a
        # Sharpe estimated on n observations is ~1/sqrt(n).
        trial_sharpe_std = 1.0 / math.sqrt(n)
    sr0 = trial_sharpe_std * expected_max_abs_t(n_trials)
    return probabilistic_sharpe_ratio(r, benchmark_sr=sr0)


# ── probability of backtest overfitting ──────────────────────────────────────

def probability_of_backtest_overfitting(
    trial_returns: np.ndarray,
    n_splits: int = 10,
    max_combinations: int = 1000,
) -> dict[str, Any]:
    """PBO via combinatorially symmetric cross-validation (Bailey et al.).

    `trial_returns` is (T observations x N configurations) — the per-period
    return of every configuration you compared. The procedure: carve T into
    `n_splits` blocks, take every way of splitting them half in-sample and half
    out-of-sample, pick the best configuration in-sample, and see where it ranks
    out-of-sample. If your selection is informative the winner keeps winning; if
    you were fitting noise it lands mid-pack.

    PBO is the fraction of splits where the in-sample winner falls in the BOTTOM
    half out of sample. Above 0.5 means selecting by backtest is worse than
    picking at random.
    """
    X = np.asarray(trial_returns, dtype=float)
    if X.ndim != 2 or X.shape[1] < 2:
        return {"pbo": float("nan"), "n_combinations": 0,
                "note": "need at least 2 configurations to measure selection"}
    T, N = X.shape
    if n_splits % 2 != 0:
        n_splits += 1
    if T < n_splits * 2:
        return {"pbo": float("nan"), "n_combinations": 0,
                "note": f"need at least {n_splits * 2} observations, got {T}"}

    blocks = np.array_split(np.arange(T), n_splits)
    half = n_splits // 2
    combos = list(itertools.combinations(range(n_splits), half))
    if len(combos) > max_combinations:
        rng = np.random.default_rng(0)
        combos = [combos[i] for i in rng.choice(len(combos), max_combinations, replace=False)]

    logits = []
    for c in combos:
        is_idx = np.concatenate([blocks[i] for i in c])
        oos_idx = np.concatenate([blocks[i] for i in range(n_splits) if i not in c])
        is_sr = _sharpe_cols(X[is_idx])
        oos_sr = _sharpe_cols(X[oos_idx])
        if not np.isfinite(is_sr).any() or not np.isfinite(oos_sr).any():
            continue
        best = int(np.nanargmax(is_sr))
        # Relative rank of the in-sample winner among OOS performance, in (0,1).
        order = np.argsort(np.argsort(oos_sr))
        w = (order[best] + 1) / (N + 1)
        w = min(max(w, 1e-9), 1 - 1e-9)
        logits.append(math.log(w / (1 - w)))

    if not logits:
        return {"pbo": float("nan"), "n_combinations": 0, "note": "no usable splits"}
    arr = np.array(logits)
    pbo = float((arr <= 0).mean())
    return {
        "pbo": pbo,
        "n_combinations": len(logits),
        "median_logit": float(np.median(arr)),
        "note": ("selection is worse than random out of sample — the result is the search"
                 if pbo > 0.5 else
                 "selection carries out of sample"),
    }


def _sharpe_cols(block: np.ndarray) -> np.ndarray:
    mu = block.mean(axis=0)
    sd = block.std(axis=0, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(sd > 0, mu / sd, np.nan)


# ── purged, embargoed cross-validation ───────────────────────────────────────

def purged_kfold_splits(
    entries: Sequence[float],
    exits: Sequence[float],
    n_splits: int = 5,
    embargo_pct: float = 0.01,
) -> list[tuple[list[int], list[int]]]:
    """K-fold splits with overlapping labels purged and an embargo applied.

    Plain k-fold leaks whenever a training trade's holding period overlaps a test
    trade's: the two share price path, so the model is scored on data it has
    partly seen. Purging drops those training samples; the embargo additionally
    drops a window immediately after the test fold, where serial correlation
    still links the two.

    This is what makes a walk-forward number mean anything. research/24 §8 says
    to re-score reports 16 and 18 before trusting their 14 "verified" slots, and
    this is the tool for it.
    """
    n = len(entries)
    if n == 0 or n_splits < 2:
        return []
    ent = np.asarray(entries, dtype=float)
    ext = np.asarray(exits, dtype=float)
    order = np.argsort(ent)
    folds = np.array_split(order, n_splits)
    span = float(ent.max() - ent.min()) if n > 1 else 0.0
    embargo = span * max(0.0, embargo_pct)

    out: list[tuple[list[int], list[int]]] = []
    for f in folds:
        if len(f) == 0:
            continue
        test = sorted(int(i) for i in f)
        t0 = float(ent[test].min())
        t1 = float(ext[test].max())
        train = []
        for i in range(n):
            if i in set(test):
                continue
            # purge: any training label whose life overlaps the test window
            if ext[i] >= t0 and ent[i] <= t1:
                continue
            # embargo: and anything starting just after the test window closes
            if t1 < ent[i] <= t1 + embargo:
                continue
            train.append(i)
        out.append((train, test))
    return out


# ── the verdict ──────────────────────────────────────────────────────────────

@dataclass
class SignificanceReport:
    n: int
    n_nonoverlap: int
    effective_n: float
    mean_r: float
    t_naive: float
    t_nonoverlap: float
    deflation_threshold: float
    n_trials: int
    deflated_sharpe: float
    pbo: float | None
    verdict: str
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "n_nonoverlap": self.n_nonoverlap,
            "effective_n": round(self.effective_n, 1),
            "mean_r": round(self.mean_r, 4),
            "t_naive": round(self.t_naive, 3),
            "t_nonoverlap": round(self.t_nonoverlap, 3),
            "deflation_threshold": round(self.deflation_threshold, 3),
            "n_trials": self.n_trials,
            "deflated_sharpe": (None if self.deflated_sharpe != self.deflated_sharpe
                                else round(self.deflated_sharpe, 4)),
            "pbo": self.pbo,
            "verdict": self.verdict,
            "reasons": self.reasons,
        }


def assess(
    r_multiples: Sequence[float],
    entries: Sequence[float] | None = None,
    exits: Sequence[float] | None = None,
    n_trials: int = 1,
    trial_returns: np.ndarray | None = None,
) -> SignificanceReport:
    """The one call a report should make. Returns SIGNIFICANT only if every
    correction agrees — and names the reason when it does not."""
    r = np.asarray(list(r_multiples), dtype=float)
    n = len(r)
    # The bar a |t| must clear. expected_max_abs_t(N) is the expected MAXIMUM
    # |t| across N null trials, and for small families it is BELOW a single
    # test's 2.0 (N=3 gives ~1.30). Used raw, it judged a result selected from
    # three configurations more leniently than one from a single configuration —
    # backwards. Floored at 2.0 so searching can only ever raise the bar.
    threshold = max(2.0, expected_max_abs_t(n_trials))

    if n < 2:
        return SignificanceReport(n, 0, float(n), float(r.mean()) if n else 0.0,
                                  0.0, 0.0, threshold, n_trials,
                                  float("nan"), None, "INSUFFICIENT",
                                  ["fewer than 2 trades"])

    def _t(x: np.ndarray) -> float:
        if len(x) < 2 or x.std(ddof=1) == 0:
            return 0.0
        return float(x.mean() / (x.std(ddof=1) / math.sqrt(len(x))))

    t_naive = _t(r)

    if entries is not None and exits is not None and len(entries) == n:
        keep = non_overlapping_subset(entries, exits)
        r_no = r[keep]
        eff_n = effective_sample_size(entries, exits)
    else:
        keep = list(range(n))
        r_no = r
        eff_n = float(n)

    t_no = _t(r_no)
    dsr = deflated_sharpe_ratio(r_no, n_trials) if len(r_no) >= 3 else float("nan")

    pbo = None
    if trial_returns is not None:
        pbo = probability_of_backtest_overfitting(trial_returns).get("pbo")

    reasons: list[str] = []
    if r_no.mean() <= 0:
        reasons.append(f"expectancy is not positive on the non-overlapping subset "
                       f"({r_no.mean():+.4f} R)")
    if abs(t_no) < threshold:
        reasons.append(f"t={t_no:+.2f} does not clear the {threshold:.2f} bar for "
                       f"{n_trials} trial{'s' if n_trials != 1 else ''}")
    if len(r_no) < 30:
        reasons.append(f"only {len(r_no)} independent trades — too few to conclude anything")
    if dsr == dsr and dsr < 0.95:
        reasons.append(f"deflated Sharpe {dsr:.3f} is below 0.95")
    if pbo is not None and pbo > 0.5:
        reasons.append(f"PBO {pbo:.2f} — selecting by backtest is worse than random here")

    verdict = "SIGNIFICANT" if not reasons else (
        "INSUFFICIENT" if len(r_no) < 30 else "NOT SIGNIFICANT")
    return SignificanceReport(n, len(r_no), eff_n, float(r.mean()), t_naive, t_no,
                              threshold, n_trials, dsr, pbo, verdict, reasons)
