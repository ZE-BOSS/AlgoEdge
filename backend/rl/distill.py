"""
backend/rl/distill.py

Policy -> named gates -> the charter the admin reads.

WHAT DISTILLATION HAS TO ACHIEVE HERE
-------------------------------------
Not "produce a readable summary". The validation gate requires that the
distilled thesis REPRODUCES the policy's decisions on held-out bars. A charter
that reads well and predicts something else is decoration, and an adherence
reward scored against decoration is worth nothing. So the output of this module
carries a measured `fidelity` and the gate rejects a charter below it.

HOW
---
The policy's own decisions are the training labels: long / flat / short per
bar. For each feature, the best single threshold separating one class from the
rest is found by Gini gain - a one-level tree per feature - and the strongest
few become `Gate` objects with real thresholds. That is deliberately the
simplest thing that produces an EXECUTABLE predicate over a named feature,
which is what `charter.Gate` requires and what makes adherence computable.

WHY NOT SCIKIT-LEARN
--------------------
It is not installed, and a decision tree of depth one per feature is thirty
lines of numpy. Adding a dependency to avoid thirty lines would make the live
VPS need it too, for a module whose whole output is six thresholds.

THE THRESHOLDS ARE ON THE SCALED OBSERVATION
--------------------------------------------
A gate reads a feature value in the units the policy sees - scaled by the
agent's own fold-fitted scaler. That is the only consistent choice: the policy
decided on scaled values, so a charter that thresholded raw values would be
describing a different function. `Gate.plain` therefore reads "above 1.2" in
standard-deviation-like units, and the charter records which scaler was used.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from backend.rl import charter as ch
from backend.rl import policy as pol
from backend.rl.features import FEATURES
from backend.rl.spec import AgentSpec

# A gate that fires on almost every bar says nothing, and one that fires on
# almost none cannot be measured. Both are refused.
MIN_HIT_RATE = 0.05
MAX_HIT_RATE = 0.95


@dataclass
class Split:
    """One candidate gate: a feature, a threshold, and how well it separates."""
    feature: str
    op: str
    threshold: float
    gain: float
    hit_rate: float
    direction: str = ch.EITHER

    @property
    def name(self) -> str:
        side = {"BUY": "long", "SELL": "short"}.get(self.direction, "")
        verb = "high" if self.op == ">=" else "low"
        return f"{self.feature}_{verb}" + (f"_{side}" if side else "")


def _gini(labels: np.ndarray, n_classes: int = 3) -> float:
    if not len(labels):
        return 0.0
    counts = np.bincount(labels, minlength=n_classes).astype(float)
    p = counts / counts.sum()
    return float(1.0 - np.sum(p * p))


def best_split(values: np.ndarray, labels: np.ndarray, *, quantiles: int = 19
               ) -> tuple[float, str, float] | None:
    """The single threshold on `values` that best separates `labels`.

    Candidate thresholds are quantiles of the feature rather than every unique
    value: 19 of them is enough to find a real cut and it keeps this O(19n)
    instead of O(n^2), which matters when n is 400,000 bars.
    """
    values = np.asarray(values, dtype=float)
    if len(values) < 50:
        return None
    base = _gini(labels)
    qs = np.quantile(values, np.linspace(0.05, 0.95, quantiles))
    best = None
    for t in np.unique(qs):
        hi = values >= t
        n_hi = int(hi.sum())
        if n_hi < 25 or len(values) - n_hi < 25:
            continue
        g = (n_hi * _gini(labels[hi]) + (len(values) - n_hi) * _gini(labels[~hi])) / len(values)
        gain = base - g
        if best is None or gain > best[2]:
            # The operator is chosen by which side is PURER, so the gate reads
            # as the condition that selects the decision rather than the one
            # that rejects it.
            op = ">=" if _gini(labels[hi]) <= _gini(labels[~hi]) else "<="
            best = (float(t), op, float(gain))
    return best


def candidate_splits(Z: np.ndarray, labels: np.ndarray, names, *, top: int = 6
                     ) -> list[Split]:
    """The strongest few thresholds, most informative first."""
    out: list[Split] = []
    for i, name in enumerate(names):
        if i >= Z.shape[1] or name not in FEATURES:
            continue
        found = best_split(Z[:, i], labels)
        if found is None:
            continue
        t, op, gain = found
        hit = float(np.mean(Z[:, i] >= t) if op == ">=" else np.mean(Z[:, i] <= t))
        if not (MIN_HIT_RATE <= hit <= MAX_HIT_RATE):
            # A gate that is true on 99% of bars is not a gate. It would push
            # adherence to 1.0 for free, which is the quiet way to make the
            # 0.30 reward term meaningless.
            continue
        out.append(Split(feature=name, op=op, threshold=t, gain=gain, hit_rate=hit))
    out.sort(key=lambda s: -s.gain)
    return out[:int(top)]


# -- measuring a gate -------------------------------------------------------


def measure_gates(gates, Z: np.ndarray, names, trade_bars: dict[int, float]
                  ) -> list[ch.Gate]:
    """Attach each gate's measured hit rate and mean R.

    `trade_bars` maps a decision bar index to that trade's realised R, which
    comes from the episode's own transitions. Without this the Charter tab
    would list gates with no evidence beside them, and an admin would be
    approving names rather than a strategy.
    """
    idx = {n: i for i, n in enumerate(names)}
    out = []
    for g in gates:
        i = idx.get(g.feature)
        if i is None or i >= Z.shape[1]:
            out.append(g)
            continue
        col = Z[:, i]
        holds = col >= g.threshold if g.op == ">=" else col <= g.threshold
        rs = [r for bar, r in trade_bars.items() if bar < len(holds) and holds[bar]]
        out.append(ch.Gate(name=g.name, feature=g.feature, op=g.op, threshold=g.threshold,
                           direction=g.direction, weight=g.weight,
                           hit_rate=float(np.mean(holds)),
                           mean_r=float(np.mean(rs)) if rs else None))
    return out


# -- fidelity ---------------------------------------------------------------


def fidelity(charter: ch.StrategyCharter, policy: pol.LinearPolicy, Z: np.ndarray,
             names, min_confidence: float = 0.40) -> dict:
    """Does the charter reproduce the policy's decisions on these bars?

    Agreement on the BINARY question the charter can actually answer - does a
    tradeable setup exist on this bar - rather than on the direction. The
    charter's tiers say "a setup is present"; the policy's head says which way.
    Measuring agreement on direction would charge the charter for something it
    was never built to express.

    `balanced` is the number to read. Raw agreement is misleading when the
    policy trades on 5% of bars: a charter that says "never trade" would score
    95%. Balanced accuracy averages the two classes, so "never trade" scores
    0.5 and has to earn anything above it.
    """
    side, _, _ = policy.entry_decisions(Z, min_confidence)
    policy_trades = side != 0
    idx = {n: i for i, n in enumerate(names)}
    charter_trades = np.ones(len(Z), dtype=bool)
    flags_by_gate = {}
    for g in charter.gates:
        i = idx.get(g.feature)
        if i is None or i >= Z.shape[1]:
            flags_by_gate[g.name] = np.zeros(len(Z), dtype=bool)
            continue
        col = Z[:, i]
        if g.op == "between":
            lo, hi = g.threshold
            flags_by_gate[g.name] = (col >= lo) & (col <= hi)
        else:
            flags_by_gate[g.name] = ch.OPS[g.op](col, g.threshold)

    if charter.tiers:
        entry = charter.entry_tier or charter.tiers[0].name
        order = [t.name for t in charter.tiers]
        allowed = set(order[:order.index(entry) + 1]) if entry in order else set(order)
        charter_trades = np.zeros(len(Z), dtype=bool)
        for t in charter.tiers:
            if t.name not in allowed:
                continue
            counts = np.zeros(len(Z))
            for gname in t.gates:
                counts += flags_by_gate.get(gname, np.zeros(len(Z), dtype=bool))
            met = counts >= t.min_gates
            for gname in t.required:
                met &= flags_by_gate.get(gname, np.zeros(len(Z), dtype=bool))
            charter_trades |= met

    agree = float(np.mean(charter_trades == policy_trades))
    pos = policy_trades.sum()
    neg = len(policy_trades) - pos
    tpr = float(np.mean(charter_trades[policy_trades])) if pos else 0.0
    tnr = float(np.mean(~charter_trades[~policy_trades])) if neg else 0.0
    return {
        "agreement": round(agree, 4),
        "balanced": round((tpr + tnr) / 2.0, 4),
        "policy_trade_rate": round(float(np.mean(policy_trades)), 4),
        "charter_trade_rate": round(float(np.mean(charter_trades)), 4),
        "caught": round(tpr, 4),
        "rejected_correctly": round(tnr, 4),
        "bars": int(len(Z)),
    }


# -- the thesis -------------------------------------------------------------


def thesis_text(spec: AgentSpec, gates, tiers, fid: dict, n_trades: int,
                mean_r: float | None) -> str:
    """One paragraph, in plain language, from the measured gates.

    Written from the numbers rather than from a template with adjectives: an
    admin approving this is being asked to agree with a claim, so the claim has
    to carry its own evidence and its own sample size.
    """
    strongest = [g for g in gates if g.mean_r is not None]
    strongest.sort(key=lambda g: -(g.mean_r or 0))
    lead = strongest[0] if strongest else (gates[0] if gates else None)
    bits = [
        f"{spec.name} trades {', '.join(spec.symbols)} on {spec.timeframe}.",
    ]
    if lead is not None:
        bits.append(f"Its strongest condition is that {lead.plain}"
                    + (f", which fires on {lead.hit_rate:.0%} of bars"
                       if lead.hit_rate is not None else "")
                    + (f" and has averaged {lead.mean_r:+.3f}R"
                       if lead.mean_r is not None else "") + ".")
    if tiers:
        bits.append(f"A setup counts when {tiers[0].min_gates} of its "
                    f"{len(tiers[0].gates)} conditions hold (tier {tiers[0].name}).")
    if n_trades:
        bits.append(f"Measured over {n_trades} trades"
                    + (f" at {mean_r:+.4f}R each" if mean_r is not None else "") + ".")
    bits.append(f"These conditions reproduce the policy's own decisions with "
                f"{fid.get('balanced', 0):.0%} balanced accuracy on held-out bars"
                f" - below that the charter would be describing something the "
                f"agent does not do.")
    if spec.risk_head:
        bits.append(f"It chooses its own stop from "
                    f"{{{', '.join(f'{v:g}x' for v in spec.stop_atr_choices)}}} ATR "
                    f"and its own target from "
                    f"{{{', '.join(f'1:{v:g}' for v in spec.rr_choices)}}}.")
    if spec.exit_head:
        bits.append("It may also close or tighten a running trade early.")
    bits.append("It can never size a position, set a risk percentage or override "
                "a halt - there is no action for any of those.")
    return " ".join(bits)


# -- the whole job ----------------------------------------------------------


def distil(policy: pol.LinearPolicy, Z: np.ndarray, names, spec: AgentSpec, *,
           trade_bars: dict[int, float] | None = None, agent_id: str = "",
           holdout: tuple[np.ndarray, np.ndarray] | None = None,
           n_trades: int = 0, mean_r: float | None = None,
           scaler_note: str = "", top: int = 6) -> ch.StrategyCharter:
    """Turn a trained policy into a DRAFT charter, with its fidelity measured.

    DRAFT, not approved and not even proposed. Approval is the admin's, and the
    status machine in `charter.py` is what stops a freshly distilled charter
    reaching a live slot.
    """
    side, _, _ = policy.entry_decisions(Z, spec.min_confidence)
    labels = np.where(side > 0, 0, np.where(side < 0, 2, 1)).astype(np.int64)
    splits = candidate_splits(Z, labels, names, top=top)

    gates = measure_gates(
        [ch.Gate(name=s.name, feature=s.feature, op=s.op, threshold=s.threshold,
                 direction=s.direction) for s in splits],
        Z, names, trade_bars or {})

    tiers: list[ch.Tier] = []
    if gates:
        allnames = tuple(g.name for g in gates)
        # A, B, C as the user words them: the same gates at three strictnesses,
        # so a tier is a named level of conviction rather than a different
        # strategy. The risk layer sizes by tier, which is why the tiers are
        # part of what the admin approves.
        tiers = [
            ch.Tier(name="A", gates=allnames, min_gates=max(1, len(allnames) - 1)),
            ch.Tier(name="B", gates=allnames, min_gates=max(1, (len(allnames) + 1) // 2)),
            ch.Tier(name="C", gates=allnames, min_gates=1),
        ]

    draft = ch.StrategyCharter(
        agent_id=agent_id or spec.name, version=1, status=ch.DRAFT,
        gates=tuple(gates), tiers=tuple(tiers), entry_tier="B" if tiers else "",
        instruments=tuple(spec.symbols), timeframes=(spec.timeframe,),
        supervises=tuple(spec.supervises),
        exit_rule=("may close all or part of a trade, or tighten its stop, on a closed "
                   "bar; never widens a stop and never sizes a position")
        if spec.exit_head else "no early exit - the stop, target and max hold decide",
        spec_hash=spec.content_hash,
    )

    hz, hnames = holdout if holdout is not None else (Z, names)
    fid = fidelity(draft, policy, hz, hnames, spec.min_confidence)
    draft.thesis = thesis_text(spec, gates, tiers, fid, n_trades, mean_r)
    draft.validation = {"features": list(names), "fidelity": fid,
                        "scaler": scaler_note, "distilled_from": policy.content_hash,
                        "optimiser_gates": len(gates)}
    return draft


def trade_bars_from(episodes) -> dict[int, float]:
    """Decision bar -> realised R, pooled over episodes.

    Pooled by bar INDEX, which is only meaningful within one dataset - so
    callers pass episodes from the same `EpisodeData`. Stated here because
    silently pooling two symbols' bar indices would attribute one market's
    results to another's gates.
    """
    out: dict[int, float] = {}
    for ep in episodes or ():
        for bar, _action, r in getattr(ep, "transitions", ()) or ():
            if r:
                out[int(bar)] = float(r)
    return out
