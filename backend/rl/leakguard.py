"""
backend/rl/leakguard.py

Can this feature see the future? Answered by equality, not by review.

WHY THIS IS THE FIRST THING BUILT
---------------------------------
Every lookahead leak makes the backtest better and live worse, and none of
them announces itself. A leaky feature produces a confident number, passes
review, survives cross-validation — CPCV purges label overlap, not a feature
that peeked — and only fails when money is on it. So the guard comes before
the agent: every result after it is worthless without it, and it costs a day.

THE PROPERTY
------------
A feature an agent may see must be PREFIX-STABLE:

    f(bars[:i])  is identical regardless of what comes after bar i

That is exactly what "causal" means for a time series, and unlike "I read the
code and it looks fine" it is an equality a machine can check. Run the feature
over a long frame, run it again over a truncated one, and compare the overlap.

WHAT IT CATCHES, AND WHAT IT DOES NOT
-------------------------------------
Catches: centred windows, whole-series normalisation, any `.bfill()` or
back-fill from a later value, `shift(-n)`, resampling that includes the
forming bar, a scaler fitted on everything.

Does NOT catch: leaks in the ENVIRONMENT rather than the feature — a reward
that reaches forward, a fill priced off a bar that had not closed, a per-symbol
table chosen on data that includes the test window. Those need the two
controls at the bottom of this file, which are about the pipeline as a whole.

MEASURED ON THIS CODEBASE, 2026-10-08
-------------------------------------
    anchored VWAP line     causal
    anchored VWAP sigma    causal
    rolling mean 20        causal      (control: must pass)
    centred mean 20        LEAKS       (control: must fail)
    whole-series z-score   LEAKS       (control: must fail)

The two failing controls are not decoration. A probe that passes everything is
indistinguishable from a probe that is broken, so `self_check()` asserts it
still catches the leaks it is known to catch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

import numpy as np
import pandas as pd

# A feature takes a frame of bars and returns a value per bar, or one value
# for the last bar. Both shapes occur in this codebase: `calculate_atr`
# returns a scalar, the VWAP helpers return arrays.
Feature = Callable[[pd.DataFrame], object]


@dataclass(frozen=True)
class Finding:
    """What the probe saw for one feature."""

    name: str
    causal: bool
    kind: str                       # "series" | "scalar" | "error"
    # How far back the contamination reaches, in bars before the cut. A
    # centred window of 20 leaks ~10; a whole-series statistic leaks all of
    # them. The number tells you WHICH mistake it is.
    reach: int = 0
    compared: int = 0
    detail: str = ""

    @property
    def summary(self) -> str:
        if self.kind == "error":
            return f"{self.name}: could not be probed — {self.detail}"
        if self.causal:
            return f"{self.name}: causal over {self.compared} shared bars"
        return (f"{self.name}: LEAKS — {self.detail or 'differs'}, reaching "
                f"{self.reach} bar(s) back from the cut")


def _as_array(value) -> tuple[np.ndarray, str]:
    """Normalise whatever a feature returned into a 1-D float array."""
    if isinstance(value, tuple):
        # several series at once (the VWAP helper returns line and sigma)
        parts = [np.asarray(v, dtype=float).ravel() for v in value]
        return np.concatenate(parts), "series"
    if isinstance(value, (pd.Series, pd.DataFrame)):
        return np.asarray(value, dtype=float).ravel(), "series"
    arr = np.asarray(value, dtype=float)
    if arr.ndim == 0:
        return arr.reshape(1), "scalar"
    return arr.ravel(), "series"


def _cuts(n: int, cut: int | None) -> list[int]:
    """Where to truncate. SEVERAL places, not one, and that is not belt-and-braces.

    A single cut misses any leak that only shows at a boundary. The case that
    taught this: `s.where(every 7th is NaN).bfill()` back-fills each gap from
    the next valid bar — a plain lookahead — but if the prefix happens to
    contain that next bar, the two runs agree and the probe passes. It only
    differs when a gap touches the END of the prefix.

    So the probe cuts at a spread of points and reports a leak if ANY of them
    disagrees. One-sided in cost: more cuts is slower, never wronger.
    """
    if cut is not None:
        return [cut]
    return sorted({max(2, int(n * f)) for f in (0.37, 0.5, 0.61, 0.74, 0.83, 0.91)
                   if int(n * f) < n - 1})


def probe(name: str, fn: Feature, frame: pd.DataFrame, *, cut: int | None = None) -> Finding:
    """Run `fn` over the whole frame and over prefixes, and compare the overlap.

    With no explicit `cut`, several are tried — see `_cuts` for why one is not
    enough. The first disagreement is reported; a feature is causal only if
    every cut agrees.
    """
    findings = [_probe_at(name, fn, frame, c) for c in _cuts(len(frame), cut)]
    bad = [f for f in findings if not f.causal]
    if not bad:
        # report the widest comparison that was made
        return max(findings, key=lambda f: f.compared)
    # the worst reach is the most informative: it says which mistake it is
    return max(bad, key=lambda f: f.reach)


def _probe_at(name: str, fn: Feature, frame: pd.DataFrame, cut: int) -> Finding:
    """One cut.

    A SCALAR feature is compared differently and deliberately so: it describes
    the LAST bar, and the last bar of a prefix is a different bar, so the two
    values are expected to differ. What can be checked is that the prefix's
    answer matches the same function run on that same prefix a second time —
    i.e. that it depends on nothing outside its input. Weak, but honest about
    what is knowable; a scalar feature should be wrapped as a series before it
    is trusted.
    """
    n = len(frame)
    if cut >= n:
        return Finding(name, False, "error", detail="cut must be inside the frame")

    try:
        full, kind = _as_array(fn(frame))
        short, _ = _as_array(fn(frame.iloc[:cut].copy()))
    except Exception as exc:                       # noqa: BLE001 — any failure is one answer
        return Finding(name, False, "error", detail=f"{type(exc).__name__}: {exc}")

    if kind == "scalar":
        again, _ = _as_array(fn(frame.iloc[:cut].copy()))
        same = bool(np.allclose(short, again, equal_nan=True))
        return Finding(name, same, "scalar", compared=1,
                       detail="" if same else "not reproducible on the same input")

    # A series feature must return one value per bar, or the comparison is
    # meaningless — line them up from the END, because a warm-up that drops
    # leading bars is normal and harmless.
    k = min(len(short), cut, len(full))
    if k == 0:
        return Finding(name, False, "error", detail="returned nothing")
    a, b = full[:k], short[:k]
    same = np.isclose(a, b, equal_nan=True)
    if same.all():
        return Finding(name, True, "series", compared=int(k))

    first_bad = int(np.flatnonzero(~same)[0])
    return Finding(name, False, "series", reach=int(k - first_bad), compared=int(k),
                   detail=f"{int((~same).sum())} of {k} values differ")


def scan(features: dict[str, Feature], frame: pd.DataFrame, *,
         cut: int | None = None) -> list[Finding]:
    return [probe(name, fn, frame, cut=cut) for name, fn in features.items()]


def leaking(findings: Iterable[Finding]) -> list[Finding]:
    return [f for f in findings if not f.causal]


def assert_causal(features: dict[str, Feature], frame: pd.DataFrame,
                  *, cut: int | None = None) -> None:
    """Raise unless every feature is prefix-stable. For CI and for `AgentSpec`.

    A feature that fails this cannot be added to an agent — not with a warning,
    not with a note in the report. The whole value of the guard is that it is
    not a judgement call.
    """
    bad = leaking(scan(features, frame, cut=cut))
    if bad:
        raise LookaheadError("\n".join(f.summary for f in bad))


class LookaheadError(AssertionError):
    """A feature can see the future."""


# ── the controls ────────────────────────────────────────────────────────────
#
# A probe that passes everything is indistinguishable from a probe that is
# broken. These three are run against the probe itself: two must fail and one
# must pass, every time.

def causal_control(frame: pd.DataFrame) -> np.ndarray:
    """A trailing mean. Looks only backwards; must pass."""
    return frame["close"].rolling(20, min_periods=1).mean().to_numpy()


def centred_control(frame: pd.DataFrame) -> np.ndarray:
    """A CENTRED window: each value is half made of bars that follow it."""
    return frame["close"].rolling(20, min_periods=1, center=True).mean().to_numpy()


def whole_series_control(frame: pd.DataFrame) -> np.ndarray:
    """Normalised against the whole series — the scaler mistake, in miniature.

    Every point carries the mean and standard deviation of bars that had not
    happened. This is the one that looks most innocent in a diff.
    """
    close = frame["close"]
    return ((close - close.mean()) / (close.std() or 1.0)).to_numpy()


CONTROLS: dict[str, Feature] = {
    "control: trailing mean (must pass)": causal_control,
    "control: centred mean (must fail)": centred_control,
    "control: whole-series z-score (must fail)": whole_series_control,
}

_MUST_FAIL = ("control: centred mean (must fail)",
              "control: whole-series z-score (must fail)")


def synthetic_bars(n: int = 400, *, seed: int = 11, step_seconds: int = 300) -> pd.DataFrame:
    """A frame to probe against. Random, because the probe tests a property of
    the FUNCTION rather than of any particular market."""
    rng = np.random.default_rng(seed)
    start = 1735689600
    t = np.arange(start, start + n * step_seconds, step_seconds, dtype="int64")
    close = 100 + np.cumsum(rng.normal(0, 0.3, n))
    return pd.DataFrame({
        "time": t, "open": close, "high": close + 0.2, "low": close - 0.2,
        "close": close, "volume": rng.integers(50, 500, n).astype(float),
    })


def self_check(frame: pd.DataFrame | None = None) -> list[Finding]:
    """Prove the probe still detects the leaks it is known to detect.

    Run this before trusting a clean scan. A silent change to `probe` that
    makes it always pass would otherwise turn the guard into a rubber stamp,
    which is worse than having no guard because it is believed.
    """
    frame = frame if frame is not None else synthetic_bars()
    findings = {f.name: f for f in scan(CONTROLS, frame)}
    broken = []
    for name, f in findings.items():
        should_fail = name in _MUST_FAIL
        if should_fail and f.causal:
            broken.append(f"{name} passed — the probe is no longer detecting leaks")
        if not should_fail and not f.causal:
            broken.append(f"{name} failed — the probe is rejecting causal features")
    if broken:
        raise LookaheadError("the leak probe is not working:\n" + "\n".join(broken))
    return list(findings.values())


# ── banned primitives ───────────────────────────────────────────────────────
#
# Some things are leaks wherever they appear, and catching them by name is
# cheaper than catching them by probe — a back-fill at the very start of an
# array contaminates only the first few bars, which a probe cut three quarters
# of the way in will never see.

BANNED_CALLS = (
    ".bfill(",              # back-fill: takes a value from a LATER bar
    ".backfill(",
    "method='bfill'",
    'method="bfill"',
    "center=True",          # a centred window straddles the present
    ".shift(-",             # a negative shift IS the future
    "_ffill_bfill",         # this codebase's own: see the note below
    ".interpolate(",        # fills a gap using the value after it
    "limit_direction='both'",
)

# `_ffill_bfill` in strategy_vwap back-fills leading NaNs from the first valid
# observation:
#     [nan nan nan 5 6 7]  ->  [5 5 5 5 6 7]
# Harmless where it is used (it starts at a session anchor and callers read
# [-1]) and a three-bar lookahead anywhere else. Measured 2026-10-08.


def scan_source(text: str) -> list[str]:
    """Banned primitives appearing in feature code."""
    return [c for c in BANNED_CALLS if c in text]


# ── the pipeline controls ───────────────────────────────────────────────────
#
# The probe above tests a FEATURE. These two test the whole pipeline, and they
# catch the leaks the probe cannot: a reward that reaches forward, a fill
# priced off a bar that had not closed, a per-symbol table chosen on data that
# includes the test window.
#
# Neither needs an agent to exist. They are verdicts over per-trade returns,
# so they are written now and called later by `validate.py`.


@dataclass(frozen=True)
class Verdict:
    passed: bool
    statistic: float
    detail: str

    @property
    def summary(self) -> str:
        return f"{'PASS' if self.passed else 'FAIL'} — {self.detail}"


def shuffled_label_verdict(returns_r, *, cost_r: float = 0.0,
                           alpha: float = 0.01) -> Verdict:
    """Train on shuffled forward returns. A clean pipeline cannot make money.

    Shuffling destroys every real relationship between features and outcome,
    so the only way the result can beat break-even-minus-costs is if the
    pipeline is reading the answer somewhere other than the features — the
    environment, the reward, a table fitted on the test window.

    `returns_r` is per-trade R. `cost_r` is the expected drag per trade, so a
    pipeline that correctly loses exactly its costs still passes. One-sided:
    losing MORE than costs on shuffled labels is not evidence of a leak.

    Fails on too few trades rather than passing: a verdict over 12 trades is
    not a verdict.
    """
    r = np.asarray(list(returns_r), dtype=float)
    r = r[np.isfinite(r)]
    if r.size < 100:
        return Verdict(False, float("nan"),
                       f"only {r.size} trades — not enough to conclude anything")
    from scipy import stats

    expected = -abs(cost_r)
    t, p_two = stats.ttest_1samp(r, expected)
    p = p_two / 2 if t > 0 else 1.0        # one-sided: only "better" matters
    mean = float(r.mean())
    if p < alpha:
        return Verdict(False, float(t),
                       f"shuffled labels still made {mean:+.4f}R against an expected "
                       f"{expected:+.4f}R (t {t:+.2f}, p {p:.4g}) — something other "
                       f"than the features is carrying the answer")
    return Verdict(True, float(t),
                   f"shuffled labels gave {mean:+.4f}R against an expected "
                   f"{expected:+.4f}R (t {t:+.2f}) — indistinguishable from noise")


def shift_verdict(base_r, shifted_r, *, collapse_ratio: float = 0.5) -> Verdict:
    """Delay every entry by one bar. A real edge degrades; a leak collapses.

    An edge that lives in the next bar's price is not an edge, and the
    signature is that it vanishes almost entirely for one bar of delay. A
    genuine one loses some of its value and keeps the rest.

    This project has the scar that calibrates it: a route that stamped entries
    one bar late cost 10R over 152 GBPJPY trades — real, measurable, and not a
    collapse. So `collapse_ratio` is the fraction of the original expectancy
    that must survive; below that, suspect the pipeline rather than the market.
    """
    base = np.asarray(list(base_r), dtype=float)
    shifted = np.asarray(list(shifted_r), dtype=float)
    base, shifted = base[np.isfinite(base)], shifted[np.isfinite(shifted)]
    if base.size < 100 or shifted.size < 100:
        return Verdict(False, float("nan"),
                       f"{base.size} and {shifted.size} trades — too few to compare")

    a, b = float(base.mean()), float(shifted.mean())
    if a <= 0:
        return Verdict(True, 0.0,
                       f"the unshifted run made {a:+.4f}R, so there is no edge to collapse")
    kept = b / a
    if kept < collapse_ratio:
        return Verdict(False, float(kept),
                       f"one bar of delay removed {(1 - kept) * 100:.0f}% of the edge "
                       f"({a:+.4f}R -> {b:+.4f}R) — an edge that lives in the next "
                       f"bar's price is not an edge")
    return Verdict(True, float(kept),
                   f"one bar of delay kept {kept * 100:.0f}% of the edge "
                   f"({a:+.4f}R -> {b:+.4f}R) — degrades rather than collapses")
