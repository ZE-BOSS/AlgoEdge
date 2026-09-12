"""
backend/analytics/forecast_scoring.py — [P3.8 / P3.9]

Scoring, calibration, and the control that says whether the pipeline is finding
structure or inventing it.

WHY CALIBRATION IS NOT OPTIONAL HERE
------------------------------------
The agent returns a `conviction` in [0,1], and Phase 1 wired `confluence_score`
through to the risk engine — so conviction will *size the trade*. An
uncalibrated confidence number is worse than no confidence number, because
position sizing will believe it. If the model's 0.8s come in at 55%, it is
systematically over-betting its best ideas.

So conviction is scored (Brier), inspected (reliability diagram), and corrected
(isotonic regression) BEFORE it is allowed to influence size.

WHY THE PERMUTATION CONTROL IS NOT OPTIONAL EITHER
--------------------------------------------------
Any pipeline this elaborate will produce a positive-looking number on something.
research/24 §7 catalogues six occasions where one did. The control is to run the
IDENTICAL pipeline on surrogate series that preserve the statistical properties
the forecaster claims to read — volatility clustering, fat tails, the lot — but
destroy the temporal structure any real forecast would depend on. Accuracy above
that control is signal. Accuracy at or below it is the pipeline finding shapes
in noise, and no amount of out-of-sample splitting will reveal that.

SCORING RULES
-------------
Expectancy in R, not accuracy. A forecaster right 70% of the time on trades
paying 0.3 R and wrong 30% on trades losing 1 R is worse than a coin flip, and
accuracy hides that completely. Abstention is free — a FLAT that would have been
wrong costs nothing and should not be penalised as if it were a loss.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np

from backend.utils.logger import get_logger

logger = get_logger(__name__)


# ── calibration ──────────────────────────────────────────────────────────────

def brier_score(probabilities: Sequence[float], outcomes: Sequence[bool]) -> float:
    """Mean squared error of a probabilistic forecast. Lower is better.

    0.25 is what you get by always saying 0.5. A forecaster scoring worse than
    that is actively misinforming the sizer.
    """
    p = np.asarray(list(probabilities), dtype=float)
    y = np.asarray([1.0 if o else 0.0 for o in outcomes], dtype=float)
    if len(p) == 0 or len(p) != len(y):
        return float("nan")
    return float(np.mean((p - y) ** 2))


def brier_skill_score(probabilities: Sequence[float], outcomes: Sequence[bool]) -> float:
    """Brier against the base-rate forecast. > 0 means the confidences add value.

    This is the honest version: a model that always predicts the base rate gets
    0, not a flattering-looking small Brier.
    """
    y = np.asarray([1.0 if o else 0.0 for o in outcomes], dtype=float)
    if len(y) == 0:
        return float("nan")
    base = float(y.mean())
    ref = float(np.mean((base - y) ** 2))
    if ref <= 0:
        return float("nan")
    return 1.0 - brier_score(probabilities, outcomes) / ref


def reliability_diagram(
    probabilities: Sequence[float],
    outcomes: Sequence[bool],
    bins: int = 10,
) -> list[dict[str, Any]]:
    """Stated confidence vs realised frequency, per bucket.

    The diagnostic that answers "when it says 0.8, how often is it right?".
    Buckets with fewer than a handful of observations are reported with their
    count so nobody reads a 100% hit rate off n=1.
    """
    p = np.asarray(list(probabilities), dtype=float)
    y = np.asarray([1.0 if o else 0.0 for o in outcomes], dtype=float)
    out: list[dict[str, Any]] = []
    if len(p) == 0:
        return out
    edges = np.linspace(0.0, 1.0, bins + 1)
    for i in range(bins):
        lo, hi = edges[i], edges[i + 1]
        sel = (p >= lo) & (p < hi) if i < bins - 1 else (p >= lo) & (p <= hi)
        n = int(sel.sum())
        out.append({
            "bin": f"{lo:.1f}-{hi:.1f}",
            "n": n,
            "mean_stated": float(p[sel].mean()) if n else None,
            "realised_rate": float(y[sel].mean()) if n else None,
            "gap": float(p[sel].mean() - y[sel].mean()) if n else None,
        })
    return out


def expected_calibration_error(
    probabilities: Sequence[float],
    outcomes: Sequence[bool],
    bins: int = 10,
) -> float:
    """One number for "how far off are the stated confidences", weighted by bucket
    population. 0 is perfect; above ~0.1 means conviction should not size."""
    rows = reliability_diagram(probabilities, outcomes, bins)
    n_total = sum(r["n"] for r in rows)
    if n_total == 0:
        return float("nan")
    return sum(r["n"] * abs(r["gap"]) for r in rows if r["n"]) / n_total


def isotonic_calibrate(
    probabilities: Sequence[float],
    outcomes: Sequence[bool],
) -> "IsotonicCalibrator":
    """Fit a monotone recalibration map with pool-adjacent-violators.

    Implemented directly rather than pulled from sklearn, which is not a
    dependency of this project — PAVA is a dozen lines and adding a large
    optional import to the live trading path for it would be a poor trade.

    Monotone by construction: a higher stated confidence can never map to a
    lower calibrated one, so the ORDERING the model produced is preserved and
    only the scale is corrected.
    """
    p = np.asarray(list(probabilities), dtype=float)
    y = np.asarray([1.0 if o else 0.0 for o in outcomes], dtype=float)
    if len(p) < 2:
        return IsotonicCalibrator(np.array([0.0, 1.0]), np.array([0.0, 1.0]), fitted=False)

    order = np.argsort(p, kind="mergesort")
    xs, ys = p[order], y[order]

    # PAVA: merge adjacent blocks until the fitted values are non-decreasing.
    vals = list(ys.astype(float))
    weights = [1.0] * len(vals)
    i = 0
    while i < len(vals) - 1:
        if vals[i] <= vals[i + 1]:
            i += 1
            continue
        w = weights[i] + weights[i + 1]
        merged = (vals[i] * weights[i] + vals[i + 1] * weights[i + 1]) / w
        vals[i:i + 2] = [merged]
        weights[i:i + 2] = [w]
        if i > 0:
            i -= 1
    fitted: list[float] = []
    for v, w in zip(vals, weights):
        fitted.extend([v] * int(round(w)))
    fitted_arr = np.asarray(fitted[:len(xs)], dtype=float)
    return IsotonicCalibrator(xs, fitted_arr, fitted=True)


@dataclass
class IsotonicCalibrator:
    x: np.ndarray
    y: np.ndarray
    fitted: bool = True

    def __call__(self, p: float) -> float:
        if not self.fitted or len(self.x) == 0:
            return float(p)
        return float(np.interp(float(p), self.x, self.y))

    def apply(self, probs: Sequence[float]) -> list[float]:
        return [self(p) for p in probs]


# ── scoring a forecaster ─────────────────────────────────────────────────────

@dataclass
class ForecastScore:
    n_forecasts: int
    n_actionable: int
    abstain_rate: float
    expectancy_r: float
    win_rate: float
    total_r: float
    brier: float
    brier_skill: float
    calibration_error: float
    reliability: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        def _c(v):
            return None if isinstance(v, float) and v != v else v
        return {
            "n_forecasts": self.n_forecasts,
            "n_actionable": self.n_actionable,
            "abstain_rate": round(self.abstain_rate, 4),
            "expectancy_r": round(self.expectancy_r, 4),
            "win_rate": round(self.win_rate, 4),
            "total_r": round(self.total_r, 3),
            "brier": _c(round(self.brier, 4) if self.brier == self.brier else self.brier),
            "brier_skill": _c(round(self.brier_skill, 4)
                              if self.brier_skill == self.brier_skill else self.brier_skill),
            "calibration_error": _c(round(self.calibration_error, 4)
                                    if self.calibration_error == self.calibration_error
                                    else self.calibration_error),
            "reliability": self.reliability,
        }


def score_forecasts(
    convictions: Sequence[float],
    r_multiples: Sequence[float],
    n_total_forecasts: int | None = None,
) -> ForecastScore:
    """Score the ACTIONABLE forecasts, with the abstention rate alongside.

    `convictions` and `r_multiples` cover only the forecasts that took a
    direction; `n_total_forecasts` is how many were made including abstentions.
    Abstaining is free — a FLAT that would have been wrong costs nothing, and
    scoring it as a loss would push the model toward always having a view, which
    is precisely the failure this design is trying to avoid.
    """
    conv = [float(c) for c in convictions]
    rs = [float(r) for r in r_multiples]
    n_act = len(rs)
    n_all = int(n_total_forecasts) if n_total_forecasts is not None else n_act
    if n_act == 0:
        return ForecastScore(n_all, 0, 1.0 if n_all else 0.0, 0.0, 0.0, 0.0,
                             float("nan"), float("nan"), float("nan"), [])
    wins = [r > 0 for r in rs]
    return ForecastScore(
        n_forecasts=n_all,
        n_actionable=n_act,
        abstain_rate=1.0 - (n_act / n_all) if n_all else 0.0,
        expectancy_r=sum(rs) / n_act,
        win_rate=sum(wins) / n_act,
        total_r=sum(rs),
        brier=brier_score(conv, wins),
        brier_skill=brier_skill_score(conv, wins),
        calibration_error=expected_calibration_error(conv, wins),
        reliability=reliability_diagram(conv, wins),
    )


# ── the surrogate control [P3.8] ─────────────────────────────────────────────

def surrogate_series(
    close: Sequence[float],
    method: str = "iid_shuffle",
    seed: int = 0,
) -> list[float]:
    """A price path with the same return DISTRIBUTION and no forecastable structure.

    Three methods, each destroying something different, so the control says
    which property a result actually depends on:

      `iid_shuffle`  — permute returns. Keeps the exact return distribution
                       (mean, variance, skew, fat tails); destroys ALL temporal
                       structure including volatility clustering.
      `block_shuffle`— permute contiguous blocks. Keeps clustering WITHIN a
                       block; destroys longer-range structure. The stricter
                       control for a forecaster that claims to read regimes.
      `sign_flip`    — randomly negate returns. Keeps the volatility path
                       exactly; destroys direction. The right control for a
                       DIRECTIONAL claim, because it leaves every volatility
                       feature intact.

    A forecaster that scores as well on a surrogate as on the real series is
    reading its own features, not the market.
    """
    px = [float(x) for x in close]
    if len(px) < 3:
        return px
    rng = np.random.default_rng(seed)
    rets = np.array([math.log(b / a) for a, b in zip(px, px[1:])
                     if a > 0 and b > 0], dtype=float)
    if len(rets) == 0:
        return px

    if method == "iid_shuffle":
        out = rng.permutation(rets)
    elif method == "block_shuffle":
        bs = max(5, len(rets) // 20)
        blocks = [rets[i:i + bs] for i in range(0, len(rets), bs)]
        rng.shuffle(blocks)
        out = np.concatenate(blocks)
    elif method == "sign_flip":
        out = rets * rng.choice([-1.0, 1.0], size=len(rets))
    else:
        raise ValueError(f"unknown surrogate method {method!r}")

    start = px[0]
    path = [start]
    for r in out:
        path.append(path[-1] * math.exp(float(r)))
    return path


def permutation_control(
    real_expectancy: float,
    surrogate_expectancies: Sequence[float],
) -> dict[str, Any]:
    """Where the real result sits in the surrogate distribution.

    `p_value` is the fraction of surrogates that did at least as well. Above
    0.05 means the pipeline produces this result on data with no structure to
    find, and the result is the pipeline.
    """
    sur = np.asarray(list(surrogate_expectancies), dtype=float)
    sur = sur[np.isfinite(sur)]
    if len(sur) < 10:
        return {"p_value": float("nan"), "n_surrogates": int(len(sur)),
                "note": "need at least 10 surrogate runs to say anything"}
    p = float((sur >= real_expectancy).mean())
    return {
        "p_value": p,
        "n_surrogates": int(len(sur)),
        "real_expectancy": float(real_expectancy),
        "surrogate_mean": float(sur.mean()),
        "surrogate_p95": float(np.quantile(sur, 0.95)),
        "beats_control": bool(p < 0.05),
        "note": ("clears the surrogate distribution" if p < 0.05 else
                 "the same pipeline produces this on structureless data — "
                 "the result is the pipeline, not the market"),
    }
