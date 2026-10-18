"""
backend/rl/contributions.py

What each input - an EMA, an ATR ratio, the director's call, a base strategy's
setup flag - actually contributes to an agent.

WHY (2026-10-18)
----------------
Asked for directly: "all those indicators or additional confluences you put
there ... I can't tell how they contribute to the profitability of the agent."
The weights alone do not answer that. A large weight on a feature that is
almost always near zero does nothing, and a policy can lean on a feature that
helps it decide WHEN to trade while that feature has nothing to do with whether
the trade makes money. So two measurements, answering two different questions:

  sensitivity   How much does nudging this input move the DECISION? Each
                column is shifted by one standard deviation (the inputs are
                scaled, so that is +1) on real bars, and the change in "trade
                at all" and in "long rather than short" is averaged. Cheap, and
                works for a hidden layer as well as a linear policy.

  permutation   How much PROFIT does this input carry? The column is shuffled
                across bars - the values stay real, their link to the market is
                cut - and the whole episode is re-run through the real engine,
                costs and risk layer included. The drop in total R is what that
                input was worth. An input whose shuffle changes nothing is not
                being used, whatever its weight says; one whose shuffle IMPROVES
                the result is actively hurting, and a candidate to drop.

Permutation is the one to believe for profitability, and it is measured on the
bars you give it: on the training bars it shows what the agent learned to lean
on, and only on later bars does it show what still pays.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from backend.rl import policy as pol


def column_names(spec, names) -> list[str]:
    """Every column the policy reads, in order: market features, then the
    director's two, then one per base strategy and their agreement."""
    from backend.rl import confluence as cf
    from backend.rl.director import DIRECTOR_FEATURES

    out = list(names)
    if spec.director:
        out += list(DIRECTOR_FEATURES)
    if spec.multi_base:
        out += cf.column_names(spec.bases)
    return out


def _probs(policy: pol.LinearPolicy, Z: np.ndarray) -> np.ndarray:
    return policy.entry_probs(Z)


def sensitivity(policy: pol.LinearPolicy, Z: np.ndarray, columns: list[str],
                *, max_rows: int = 5000, seed: int = 0) -> list[dict]:
    """Per column: the mean change in P(trade) and in P(long) - P(short) when
    that column alone is raised by one (one standard deviation, scaled)."""
    Z = np.asarray(Z, dtype=float)
    if len(Z) > max_rows:
        idx = np.random.default_rng(seed).choice(len(Z), max_rows, replace=False)
        Z = Z[np.sort(idx)]
    i_long, i_flat, i_short = (pol.ENTRY_ACTIONS.index(a)
                               for a in (pol.LONG, pol.FLAT, pol.SHORT))
    base = _probs(policy, Z)
    out = []
    for c, name in enumerate(columns[:Z.shape[1]]):
        up = Z.copy()
        up[:, c] += 1.0
        p = _probs(policy, up)
        d_trade = float(np.mean((1 - p[:, i_flat]) - (1 - base[:, i_flat])))
        d_long = float(np.mean((p[:, i_long] - p[:, i_short])
                               - (base[:, i_long] - base[:, i_short])))
        out.append({"column": name, "d_trade": round(d_trade, 5),
                    "d_long": round(d_long, 5)})
    return out


def permutation(env, policy: pol.LinearPolicy, columns: list[str], *,
                seed: int = 7, only: list[str] | None = None) -> dict[str, Any]:
    """Re-run the episode once per column with that column shuffled.

    `env` is a `TradingEnv` with its scaler fitted. Its observation matrix is
    swapped column by column and restored afterwards, so nothing else - bars,
    fills, costs, the base strategies' setups - changes between runs.
    """
    Z0 = env.observations_for(policy).copy()
    base = env.rollout(policy, seed=seed)
    b = base.breakdown
    base_total = float(sum(t.r_net for t in base.rewards))
    rows = []
    rng = np.random.default_rng(seed)
    try:
        for c, name in enumerate(columns[:Z0.shape[1]]):
            if only and name not in only:
                continue
            Z = Z0.copy()
            Z[:, c] = Z0[rng.permutation(len(Z0)), c]
            env._Z = Z
            ep = env.rollout(policy, seed=seed)
            total = float(sum(t.r_net for t in ep.rewards))
            rows.append({
                "column": name,
                # Positive = the input was worth this much R; negative = the
                # agent does BETTER without it.
                "r_contribution": round(base_total - total, 4),
                "trades_without": int(ep.breakdown.n_trades),
                "mean_r_without": round(float(ep.breakdown.mean_r), 4)
                if ep.breakdown.n_trades else None,
            })
    finally:
        env._Z = Z0
    return {"baseline": {"trades": int(b.n_trades), "total_r": round(base_total, 4),
                         "mean_r": round(float(b.mean_r), 4) if b.n_trades else None},
            "rows": rows}


def explain(env, policy: pol.LinearPolicy, spec, names, *, permute: bool = True,
            only: list[str] | None = None) -> dict[str, Any]:
    """Both measurements, joined per column and ranked by what they are worth."""
    columns = column_names(spec, names)
    Z = env.observations_for(policy)
    sens = {r["column"]: r for r in sensitivity(policy, Z, columns)}
    perm = permutation(env, policy, columns, only=only) if permute else {"rows": []}
    by_perm = {r["column"]: r for r in perm["rows"]}
    rows = []
    for name in columns[:Z.shape[1]]:
        r = {"column": name, **{k: v for k, v in sens.get(name, {}).items()
                                if k != "column"}}
        r.update({k: v for k, v in by_perm.get(name, {}).items() if k != "column"})
        r["kind"] = ("director" if name.startswith("director_")
                     else "setup" if name.startswith("setup") else "market")
        rows.append(r)
    rows.sort(key=lambda r: (-(r.get("r_contribution") or 0.0),
                             -abs(r.get("d_trade") or 0.0)))
    helps = [r["column"] for r in rows if (r.get("r_contribution") or 0) > 0][:5]
    hurts = [r["column"] for r in rows if (r.get("r_contribution") or 0) < 0]
    unused = [r["column"] for r in rows
              if abs(r.get("d_trade") or 0) < 0.002 and abs(r.get("d_long") or 0) < 0.002]
    sentences = []
    if perm.get("baseline"):
        bl = perm["baseline"]
        sentences.append(f"Baseline on these bars: {bl['trades']} trades, "
                         f"{bl['total_r']:+.2f}R in total.")
    if helps:
        sentences.append("Most profit rides on: " + ", ".join(helps) + ".")
    if hurts:
        sentences.append("The agent does BETTER with these shuffled - they cost money "
                         "and are candidates to drop: " + ", ".join(hurts) + ".")
    if unused:
        sentences.append("Barely used by the decision at all: " + ", ".join(unused) + ".")
    return {"rows": rows, "baseline": perm.get("baseline"), "sentences": sentences,
            "method": {
                "d_trade": "change in P(take a trade) when the input rises by 1 std",
                "d_long": "change in P(long) - P(short) when the input rises by 1 std",
                "r_contribution": "total R lost when the input is shuffled across bars "
                                  "(re-run through the real engine; negative = it hurts)",
            }}
