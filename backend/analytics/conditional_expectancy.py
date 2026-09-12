"""
backend/analytics/conditional_expectancy.py — [P5.3]

Is there a market state in which this signal is worth taking — and would that
state have looked just as good if the labels meant nothing?

THE TRAP
--------
Cut any trade population into enough cells and one of them will look excellent.
research/24 §6 is the cautionary case: a candidate that passed four validation
stages died on the fifth, because the best of ~84 searched hypotheses is
expected to reach |t| ~ 2.69 by chance alone. A conditional-expectancy study is
that search by construction, so every cell here is judged three ways at once:

1. **Against its complement, on independent trades.** Welch's t between the
   cell and everything outside it, both reduced to non-overlapping subsets.
2. **Against the family.** The bar for that t is the 95th percentile of the
   MAXIMUM |t| across all cells when the labels are scrambled — a family-wise
   control, not a per-cell one. Labels are scrambled by circular shift within
   each instrument, which keeps each regime's persistence intact (a plain
   shuffle would destroy it and make every real result look more extreme than
   it is).
3. **Against time.** The cell's edge must be PRESENT in both the first and the
   second half of its trades: the same sign as the whole, and at least
   `half_min_t` standard errors from zero in each half. Sign alone is not
   enough — on 2026-09-11 a cell earning +1.23 R in its first half and +0.03 R
   in its second passed a sign-only rule and read ACTIONABLE. That is an edge
   that stopped working, which is precisely what this check exists to refuse.

A cell passing all three is still only a CANDIDATE. The next gate is purged,
embargoed walk-forward (plan task 5.9, `significance.purged_kfold_splits`), and
nothing ships as a live filter before it clears that.

OVERLAP SCOPE
-------------
`per_instrument` (default) treats trades on different instruments as separate
observations even when they overlap in time; `global` counts cross-instrument
overlap too. The first ignores cross-asset correlation (gold and silver move
together); the second over-corrects for uncorrelated pairs. The truth sits
between, and a result worth acting on should survive `global` as well.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np

from backend.analytics.significance import assess, expected_max_abs_t, non_overlapping_subset

_SCOPE_OFFSET = 1e12  # far beyond any epoch-second span, so instruments never overlap


@dataclass(frozen=True)
class TradeObs:
    instrument: str
    entry_time: float
    exit_time: float
    r: float


def welch_t(a: Sequence[float], b: Sequence[float]) -> float:
    """Welch's t for mean(a) - mean(b). Zero when either side is too small to vary."""
    if len(a) < 2 or len(b) < 2:
        return 0.0
    va, vb = statistics.variance(a), statistics.variance(b)
    den = math.sqrt(va / len(a) + vb / len(b))
    return (statistics.mean(a) - statistics.mean(b)) / den if den > 0 else 0.0


def one_sample_t(x: Sequence[float]) -> float:
    """t of the mean against zero. Zero when there is too little to vary."""
    if len(x) < 2:
        return 0.0
    sd = statistics.stdev(x)
    return statistics.mean(x) / (sd / math.sqrt(len(x))) if sd > 0 else 0.0


def _keyed(obs: Sequence[TradeObs], scope: str) -> tuple[list[float], list[float]]:
    if scope == "global":
        return [o.entry_time for o in obs], [o.exit_time for o in obs]
    idx = {s: i for i, s in enumerate(sorted({o.instrument for o in obs}))}
    return ([idx[o.instrument] * _SCOPE_OFFSET + o.entry_time for o in obs],
            [idx[o.instrument] * _SCOPE_OFFSET + o.exit_time for o in obs])


def _nonoverlap_r(obs: Sequence[TradeObs], scope: str) -> list[float]:
    if not obs:
        return []
    e, x = _keyed(obs, scope)
    return [obs[i].r for i in non_overlapping_subset(e, x)]


def _split(obs: Sequence[TradeObs], labels: Sequence[dict[str, Any]],
           var: str, val: Any) -> tuple[list[TradeObs], list[TradeObs]]:
    ins, outs = [], []
    for o, lab in zip(obs, labels):
        v = lab.get(var)
        if v is None:
            continue  # unlabelled for this variable: in neither the cell nor its complement
        (ins if v == val else outs).append(o)
    return ins, outs


def enumerate_cells(labels: Sequence[dict[str, Any]], variables: Sequence[str]) -> list[tuple[str, Any]]:
    cells: list[tuple[str, Any]] = []
    for var in variables:
        vals = sorted({lab.get(var) for lab in labels if lab.get(var) is not None}, key=str)
        cells.extend((var, v) for v in vals)
    return cells


def _max_abs_welch(obs, labels, cells, scope, min_cell_n) -> float:
    best = 0.0
    for var, val in cells:
        ins, outs = _split(obs, labels, var, val)
        a, b = _nonoverlap_r(ins, scope), _nonoverlap_r(outs, scope)
        if len(a) < min_cell_n or len(b) < min_cell_n:
            continue
        best = max(best, abs(welch_t(a, b)))
    return best


def _shifted_labels(obs: Sequence[TradeObs], labels: Sequence[dict[str, Any]],
                    rng: np.random.Generator) -> list[dict[str, Any]]:
    """Circularly shift the label sequence within each instrument.

    Keeps every regime's persistence and its share of trades exactly; destroys
    only the ALIGNMENT between a trade and the state it was taken in.
    """
    out = list(labels)
    by_inst: dict[str, list[int]] = {}
    for i, o in enumerate(obs):
        by_inst.setdefault(o.instrument, []).append(i)
    for idxs in by_inst.values():
        idxs = sorted(idxs, key=lambda i: obs[i].entry_time)
        n = len(idxs)
        if n < 4:
            continue
        k = int(rng.integers(max(1, n // 10), max(2, n - n // 10)))
        for j, i in enumerate(idxs):
            out[i] = labels[idxs[(j + k) % n]]
    return out


def run_study(
    obs: Sequence[TradeObs],
    labels: Sequence[dict[str, Any]],
    *,
    variables: Sequence[str] | None = None,
    min_cell_n: int = 30,
    overlap_scope: str = "per_instrument",
    n_permutations: int = 200,
    half_min_t: float = 1.0,
    seed: int = 0,
) -> dict[str, Any]:
    if len(obs) != len(labels):
        raise ValueError(f"{len(obs)} trades but {len(labels)} label rows")
    if overlap_scope not in ("per_instrument", "global"):
        raise ValueError(f"unknown overlap_scope {overlap_scope!r}")

    variables = list(variables) if variables else sorted({k for lab in labels for k in lab})
    cells = enumerate_cells(labels, variables)
    n_cells = len(cells)

    e_all, x_all = _keyed(obs, overlap_scope)
    overall = assess([o.r for o in obs], e_all, x_all, n_trials=1).to_dict()

    # The family-wise bar. Never below a single test's 2.0, never below the
    # expected maximum across the family, and — when permutations run — never
    # below the 95th percentile of the maximum under scrambled labels.
    critical = max(2.0, expected_max_abs_t(n_cells)) if n_cells > 1 else 2.0
    observed_max = _max_abs_welch(obs, labels, cells, overlap_scope, min_cell_n)
    perm: dict[str, Any] | None = None
    if n_permutations > 0 and n_cells > 0:
        rng = np.random.default_rng(seed)
        null = np.asarray([
            _max_abs_welch(obs, _shifted_labels(obs, labels, rng), cells, overlap_scope, min_cell_n)
            for _ in range(int(n_permutations))
        ])
        p95 = float(np.quantile(null, 0.95))
        critical = max(critical, p95)
        perm = {
            "n_permutations": int(n_permutations),
            "method": "circular shift of labels within each instrument",
            "observed_max_abs_t": round(observed_max, 4),
            "null_mean_max_abs_t": round(float(null.mean()), 4),
            "null_p95_max_abs_t": round(p95, 4),
            "p_value": round(float((null >= observed_max).mean()), 4),
        }

    results: list[dict[str, Any]] = []
    for var, val in cells:
        ins, outs = _split(obs, labels, var, val)
        a, b = _nonoverlap_r(ins, overlap_scope), _nonoverlap_r(outs, overlap_scope)
        wt = welch_t(a, b)
        rs = [o.r for o in ins]
        mean = statistics.mean(rs) if rs else None

        srt = sorted(ins, key=lambda o: o.entry_time)
        h = len(srt) // 2
        first, second = [o.r for o in srt[:h]], [o.r for o in srt[h:]]
        m1 = statistics.mean(first) if first else None
        m2 = statistics.mean(second) if second else None
        t1, t2 = one_sample_t(first), one_sample_t(second)

        e, x = _keyed(ins, overlap_scope)
        sig = assess(rs, e, x, n_trials=max(1, n_cells)).to_dict()

        # A half only COUNTS as having a sign when it is at least `half_min_t`
        # standard errors from zero. np.sign() of a mean that is zero up to
        # floating-point noise (-5.2e-18 was measured) returns -1, which reported
        # an edge that had merely vanished as a sign flip — the right verdict for
        # the wrong reason, and the reason is what tells a reader whether the
        # regime reversed or simply stopped paying.
        present1 = m1 is not None and abs(t1) >= half_min_t
        present2 = m2 is not None and abs(t2) >= half_min_t
        flipped = present1 and present2 and np.sign(m1) != np.sign(m2)
        stable = (present1 and present2 and mean is not None and mean != 0
                  and np.sign(m1) == np.sign(m2) == np.sign(mean))

        note = ""
        if len(a) < min_cell_n or len(b) < min_cell_n:
            verdict = "TOO FEW"
        elif abs(wt) < critical:
            verdict = "NOT SIGNIFICANT"
        elif not stable:
            verdict = "UNSTABLE"
            note = ("clears the family-wise bar but changes sign between halves — a regime that reversed"
                    if flipped else
                    "clears the family-wise bar but is absent in one half — an edge that decayed")
        elif wt > 0 and sig.get("verdict") == "SIGNIFICANT":
            verdict = "ACTIONABLE"
            note = "candidate only — must clear purged walk-forward (5.9) before shipping"
        elif wt < 0 and mean < 0:
            verdict = "AVOID"
            note = "reliably worse than the rest — a filter candidate, not an edge"
        else:
            verdict = "NOT SIGNIFICANT"
            note = "different from its complement, but not profitable in itself"

        results.append({
            "variable": var, "value": val,
            "n": len(ins), "n_independent": len(a), "complement_n_independent": len(b),
            "mean_r": None if mean is None else round(mean, 4),
            "complement_mean_r": round(statistics.mean(b), 4) if b else None,
            "welch_t": round(wt, 4),
            "first_half_mean_r": None if m1 is None else round(m1, 4),
            "second_half_mean_r": None if m2 is None else round(m2, 4),
            "first_half_t": round(t1, 3),
            "second_half_t": round(t2, 3),
            "cell_significance": sig.get("verdict"),
            "verdict": verdict, "note": note,
        })
    results.sort(key=lambda c: -abs(c["welch_t"]))

    actionable = [c for c in results if c["verdict"] == "ACTIONABLE"]
    avoid = [c for c in results if c["verdict"] == "AVOID"]
    if actionable:
        headline = "CANDIDATES — survive the family-wise control; next gate is purged walk-forward (5.9)"
    elif avoid:
        headline = "HARMFUL REGIMES ONLY — worth filtering out; no regime adds an edge"
    else:
        headline = "NO CONDITIONAL EDGE"

    return {
        "n_trades": len(obs),
        "variables": variables,
        "n_cells_tested": n_cells,
        "critical_welch_t": round(critical, 4),
        "min_cell_n": min_cell_n,
        "half_min_t": half_min_t,
        "overlap_scope": overlap_scope,
        "overall": overall,
        "permutation_control": perm,
        "cells": results,
        "actionable": actionable,
        "avoid": avoid,
        "headline": headline,
    }
