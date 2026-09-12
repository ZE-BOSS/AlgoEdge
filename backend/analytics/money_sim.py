"""
backend/analytics/money_sim.py

What a list of per-trade R outcomes is worth on a real account.

A strategy result in R says nothing about dollars until four account decisions
are made, and each can change the answer by more than the strategy does:

  * risk per trade, and whether it is taken from the STARTING balance (static)
    or the CURRENT balance (compounding) — the app's `sizing_basis`;
  * whether a winning position is added to (pyramiding);
  * a daily drawdown cap that stops new entries once the day's losses reach it;
  * the broker's lot grid: lots are FLOORED to the step and a trade whose
    minimum lot would risk more than the budget is REFUSED, exactly as
    risk/position_sizer.py does. On a small account this is the difference
    between a backtest and something that can be traded.

Legs are processed in time order across every symbol at once, so concurrent
positions share one balance and one daily budget, as they do live.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Sequence

import numpy as np


@dataclass(frozen=True)
class Contract:
    value_per_price_per_lot: float   # account currency per 1.0 price move per 1 lot
    min_lot: float
    lot_step: float
    max_lot: float = 1e9


@dataclass(frozen=True)
class Leg:
    symbol: str
    t_entry: int
    t_exit: int
    r: float                 # outcome in R of THIS leg's own stop, costs included
    stop_dist: float         # price distance entry -> stop
    group: str = ""          # a base trade and its pyramid add share a group
    is_add: bool = False


@dataclass(frozen=True)
class AccountRules:
    start_balance: float
    risk_pct: float
    compounding: bool = False
    pyramiding: bool = False
    daily_dd_cap_pct: float | None = None
    max_concurrent: int | None = None
    open_risk_weight: float = 0.5    # RiskParams.open_risk_weight default


def _day(t: int) -> int:
    return int(t) // 86400


def _month(t: int) -> str:
    return datetime.fromtimestamp(int(t), tz=timezone.utc).strftime("%Y-%m")


def simulate_account(legs: Sequence[Leg], contracts: dict[str, Contract],
                     rules: AccountRules) -> dict[str, Any]:
    events = []
    for k, leg in enumerate(legs):
        events.append((int(leg.t_exit), 0, k))   # exits first at equal timestamps
        events.append((int(leg.t_entry), 1, k))
    events.sort()

    balance = float(rules.start_balance)
    peak, max_dd, max_dd_usd = balance, 0.0, 0.0
    open_risk: dict[int, float] = {}
    open_groups: dict[str, int] = {}
    cur_day, day_pnl = None, 0.0
    taken: list[tuple[Leg, float]] = []
    month_start: dict[str, float] = {}
    month_pnl: dict[str, float] = {}
    counts = {"skipped_unsizable": 0, "blocked_daily_cap": 0, "blocked_concurrency": 0,
              "adds_taken": 0, "adds_skipped": 0}

    for t, kind, k in events:
        leg = legs[k]
        d = _day(t)
        if d != cur_day:
            cur_day, day_pnl = d, 0.0

        if kind == 0:
            risk = open_risk.pop(k, None)
            if risk is None:
                continue
            if not leg.is_add:
                open_groups.pop(leg.group, None)
            pnl = leg.r * risk
            m = _month(t)
            month_start.setdefault(m, balance)
            month_pnl[m] = month_pnl.get(m, 0.0) + pnl
            balance += pnl
            day_pnl += pnl
            taken.append((leg, pnl))
            peak = max(peak, balance)
            if peak > 0:
                max_dd = max(max_dd, (peak - balance) / peak)
                max_dd_usd = max(max_dd_usd, peak - balance)
            continue

        if leg.is_add and (not rules.pyramiding or leg.group not in open_groups):
            counts["adds_skipped"] += 1
            continue
        if balance <= 0:
            continue
        if rules.max_concurrent and len(open_risk) >= rules.max_concurrent:
            counts["blocked_concurrency"] += 1
            continue
        base = balance if rules.compounding else rules.start_balance
        requested = base * rules.risk_pct / 100.0
        if rules.daily_dd_cap_pct:
            remaining = (base * rules.daily_dd_cap_pct / 100.0 + day_pnl
                         - rules.open_risk_weight * sum(open_risk.values()))
            if remaining <= 0:
                counts["blocked_daily_cap"] += 1
                continue
            requested = min(requested, remaining)
        c = contracts[leg.symbol]
        per_lot = leg.stop_dist * c.value_per_price_per_lot
        if per_lot <= 0:
            continue
        lots = math.floor(requested / per_lot / c.lot_step + 1e-9) * c.lot_step
        lots = min(lots, c.max_lot)
        if lots < c.min_lot - 1e-12:
            counts["skipped_unsizable"] += 1
            continue
        open_risk[k] = lots * per_lot
        if leg.is_add:
            counts["adds_taken"] += 1
        else:
            open_groups[leg.group] = k

    return _summary(rules, balance, taken, max_dd, max_dd_usd, month_start, month_pnl, counts)


def _summary(rules, balance, taken, max_dd, max_dd_usd, month_start, month_pnl, counts):
    pnls = [p for _, p in taken]
    gw = sum(p for p in pnls if p > 0)
    gl = -sum(p for p in pnls if p < 0)
    months = {m: 100.0 * month_pnl[m] / month_start[m] for m in sorted(month_pnl) if month_start[m] > 0}
    mv = list(months.values())
    return {
        "start_balance": rules.start_balance,
        "final_balance": round(balance, 2),
        "net_pnl": round(balance - rules.start_balance, 2),
        "return_pct": round(100.0 * (balance / rules.start_balance - 1.0), 2),
        "trades": len(taken),
        "win_rate": round(sum(p > 0 for p in pnls) / len(pnls), 4) if pnls else None,
        "profit_factor": round(gw / gl, 3) if gl > 0 else None,
        "expectancy_usd": round(statistics.mean(pnls), 2) if pnls else None,
        "expectancy_r": round(statistics.mean(l.r for l, _ in taken), 4) if taken else None,
        "max_dd_pct": round(100.0 * max_dd, 2),
        "max_dd_usd": round(max_dd_usd, 2),
        "months": len(mv),
        "positive_months": sum(v > 0 for v in mv),
        "avg_month_pct": round(statistics.mean(mv), 2) if mv else None,
        "median_month_pct": round(statistics.median(mv), 2) if mv else None,
        "worst_month_pct": round(min(mv), 2) if mv else None,
        "best_month_pct": round(max(mv), 2) if mv else None,
        "monthly_pct": {m: round(v, 2) for m, v in months.items()},
        **counts,
    }


def shuffled_paths(legs: Sequence[Leg], contracts: dict[str, Contract], rules: AccountRules,
                   n: int = 300, seed: int = 0) -> dict[str, Any]:
    """Same trades and timing, outcomes reassigned at random between base trades
    (an add travels with its base). The spread of results is how much of the
    headline number was the ORDER the wins and losses happened to arrive in."""
    rng = np.random.default_rng(seed)
    groups: dict[str, list[int]] = {}
    for k, leg in enumerate(legs):
        groups.setdefault(leg.group or f"_{k}", []).append(k)
    keys = list(groups)
    finals, dds, worst_months = [], [], []
    for _ in range(int(n)):
        perm = rng.permutation(len(keys))
        new = list(legs)
        for dst, src in zip(keys, (keys[p] for p in perm)):
            src_base = next(legs[k] for k in groups[src] if not legs[k].is_add)
            src_add = next((legs[k] for k in groups[src] if legs[k].is_add), None)
            for k in groups[dst]:
                if legs[k].is_add:
                    new[k] = replace(legs[k], r=src_add.r if src_add else -1.0)
                else:
                    new[k] = replace(legs[k], r=src_base.r)
        s = simulate_account(new, contracts, rules)
        finals.append(s["return_pct"])
        dds.append(s["max_dd_pct"])
        if s["worst_month_pct"] is not None:
            worst_months.append(s["worst_month_pct"])
    q = lambda xs, p: round(float(np.quantile(xs, p)), 2) if xs else None
    return {
        "paths": int(n),
        "return_pct_p5": q(finals, 0.05), "return_pct_p50": q(finals, 0.5), "return_pct_p95": q(finals, 0.95),
        "max_dd_pct_p50": q(dds, 0.5), "max_dd_pct_p95": q(dds, 0.95),
        "worst_month_pct_p5": q(worst_months, 0.05),
        "prob_loss": round(float(np.mean(np.asarray(finals) < 0)), 3),
    }
