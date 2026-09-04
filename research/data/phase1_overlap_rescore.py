"""Phase 1g: re-score every backtested cell with an overlap-aware standard error.

Report 20 section 5.1 showed that `sd/sqrt(n)` across overlapping trades is badly
optimistic - a random long strategy with no entry logic at all read t = +3.99
overlapping and t = +0.56 once trades were forced to be independent. Every
significance claim in reports 16 and 18 is built on the optimistic version.

This re-scores from `algoedge.db`, which carries entry_time and exit_time for
7,143 trades across 87 cells, so nothing needs re-running.

Two corrections, reported side by side:

* **naive**: sd/sqrt(n), what the reports used.
* **independent subset**: greedily keep only trades that opened after the
  previous kept trade closed. Unbiased, loses power, easy to defend.
* **effective sample size**: n_eff = n / (1 + 2*sum(rho_k)) using the
  autocorrelation of the P&L series, a standard Newey-West style deflation. Uses
  every trade rather than discarding, at the cost of assuming stationarity.

A cell whose t collapses between the first column and the last two was never
significant; the apparent edge was the same price path counted several times.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from math import sqrt
from pathlib import Path

import numpy as np

DB = Path(__file__).resolve().parents[2] / "algoedge.db"
MIN_N = 30


def to_epoch(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("T", " ").split("+")[0]
    for f in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, f).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    return None


def n_eff(x: np.ndarray, max_lag: int = 25) -> float:
    """Effective sample size after autocorrelation (Newey-West style deflation)."""
    x = x - x.mean()
    d = float((x * x).sum())
    if d <= 0:
        return float(len(x))
    tot = 0.0
    for k in range(1, min(max_lag, len(x) - 2) + 1):
        rho = float((x[:-k] * x[k:]).sum() / d)
        w = 1.0 - k / (max_lag + 1)                  # Bartlett taper
        tot += w * rho
    # Capped at n. Negatively autocorrelated P&L makes the raw formula return
    # n_eff > n, which would *shrink* the standard error and make a marginal cell
    # look stronger than its own trade count can support. The correction exists to
    # be conservative, so it may only ever remove information, never add it.
    return min(float(len(x)), len(x) / max(1.0 + 2.0 * tot, 1e-6))


def main() -> None:
    c = sqlite3.connect(DB)
    rows = c.execute(
        """SELECT strategy_id, symbol, entry_time, exit_time, pnl_r
           FROM backtest_trades
           WHERE pnl_r IS NOT NULL AND entry_time IS NOT NULL
           ORDER BY strategy_id, symbol, entry_time""").fetchall()
    cells: dict[tuple[str, str], list] = {}
    for st, sy, et, xt, pr in rows:
        cells.setdefault((st or "?", sy or "?"), []).append(
            (to_epoch(et), to_epoch(xt), float(pr)))

    out = []
    for (st, sy), v in cells.items():
        v = [x for x in v if x[0] is not None]
        if len(v) < MIN_N:
            continue
        v.sort(key=lambda x: x[0])
        r = np.array([x[2] for x in v])
        n = len(r)
        m = r.mean()
        se_naive = r.std(ddof=1) / sqrt(n)

        # independent subset
        keep, last = [], -np.inf
        for e, x, pr in v:
            if e > last:
                keep.append(pr)
                last = x if x is not None else e
        ind = np.asarray(keep)

        # effective-n deflation
        ne = n_eff(r)
        se_eff = r.std(ddof=1) / sqrt(max(ne, 1.0))

        out.append({
            "cell": f"{st.replace('_v1','')} | {sy}", "n": n, "exp": m,
            "t_naive": m / se_naive if se_naive else 0.0,
            "n_ind": len(ind),
            "exp_ind": ind.mean() if len(ind) > 2 else np.nan,
            "t_ind": (ind.mean() / (ind.std(ddof=1) / sqrt(len(ind))))
                     if len(ind) > 2 and ind.std(ddof=1) > 0 else np.nan,
            "n_eff": ne, "t_eff": m / se_eff if se_eff else 0.0,
        })

    out.sort(key=lambda d: -d["t_naive"])
    print(f"{len(out)} cells with n >= {MIN_N}, from {sum(d['n'] for d in out):,} trades\n")
    print(f"{'cell':44s}{'n':>5s}{'exp R':>8s}{'t naive':>9s}"
          f"{'n_ind':>7s}{'t_ind':>8s}{'n_eff':>8s}{'t_eff':>8s}  verdict")
    print("-" * 112)
    survived = 0
    for d in out:
        ti, te = d["t_ind"], d["t_eff"]
        strong = (ti == ti and ti > 2) and te > 2
        if d["t_naive"] > 2:
            verdict = "HOLDS" if strong else "was noise"
        else:
            verdict = ""
        survived += bool(strong and d["t_naive"] > 2)
        print(f"{d['cell'][:43]:44s}{d['n']:5d}{d['exp']:+8.3f}{d['t_naive']:+9.2f}"
              f"{d['n_ind']:7d}{ti:+8.2f}{d['n_eff']:8.0f}{te:+8.2f}  {verdict}")

    naive_sig = sum(1 for d in out if d["t_naive"] > 2)
    print("-" * 112)
    print(f"\ncells significant on the NAIVE standard error : {naive_sig}")
    print(f"cells still significant after both corrections: {survived}")
    print(f"expected by chance alone at t>2 over {len(out)} cells: "
          f"{len(out)*0.0228:.1f}")
    print(f"\nmean t (naive) {np.mean([d['t_naive'] for d in out]):+.3f}   "
          f"mean t (eff) {np.mean([d['t_eff'] for d in out]):+.3f}")
    print(f"median overlap deflation n -> n_eff: "
          f"{np.median([d['n']/max(d['n_eff'],1) for d in out]):.2f}x")

    # The cells were not chosen in advance, so a single-cell t is the wrong bar.
    import random
    random.seed(0)
    mx = sorted(max(abs(random.gauss(0, 1)) for _ in range(len(out)))
                for _ in range(4000))
    print(f"\nSelecting the best of {len(out)} cells, the null expects a maximum "
          f"|t| of {mx[len(mx)//2]:.2f}")
    print(f"  (95th percentile {mx[int(len(mx)*0.95)]:.2f})")
    best = max(out, key=lambda d: d["t_ind"] if d["t_ind"] == d["t_ind"] else -9)
    print(f"  best cell after correction: {best['cell']}  t_ind {best['t_ind']:+.2f}")
    frac = sum(1 for m in mx if m >= best["t_ind"]) / len(mx)
    print(f"  P(some cell reaches this by chance) = {frac:.0%}"
          f"  -> {'still notable' if frac < 0.05 else 'inside the noise band'}")


if __name__ == "__main__":
    main()
