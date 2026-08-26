"""
scripts/analyse_apa_corpus.py

Cross-symbol analysis of every saved APA backtest, for optimisation.

Reads `algoedge.db` directly — no MT5, no backend, no re-running anything.

Two disciplines this follows, because the point is to find a real edge rather
than a flattering slice:

  * **Integrity gate first.** A run whose account went negative, or whose `pnl`
    and `pnl_r` disagree about whether a trade won, cannot contribute to an
    expectancy ranking. Those runs are reported and then EXCLUDED, not quietly
    averaged in.
  * **Consistency over magnitude.** With ~800 trades and a dozen candidate
    splits, something will look profitable by luck. So every filter is judged on
    how many symbols it works on independently, not on the pooled number.
"""

from __future__ import annotations

import argparse
import sqlite3
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def load(db: str):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    runs = con.execute(
        "SELECT id, symbol, strategy_id FROM backtest_runs WHERE strategy_id LIKE 'APA%'"
    ).fetchall()
    out = {}
    for r in runs:
        rows = con.execute(
            "SELECT * FROM backtest_trades WHERE backtest_id=?", (r["id"],)
        ).fetchall()
        out[r["symbol"]] = [dict(t) for t in rows]
    return out


def integrity(trades: list[dict]) -> tuple[bool, list[str]]:
    """A run is usable only if its accounting is self-consistent."""
    problems = []
    bals = [t["balance_before"] for t in trades if t.get("balance_before") is not None]
    if bals and min(bals) < 0:
        problems.append(f"balance went negative ({min(bals):,.0f})")
    worst = 0.0
    for t in trades:
        p, b = t.get("pnl"), t.get("balance_before")
        if p is not None and b and b > 0:
            worst = max(worst, abs(p) / b)
    if worst > 0.5:
        problems.append(f"single trade risked {worst * 100:.0f}% of balance")
    flips = sum(
        1 for t in trades
        if t.get("pnl") is not None and t.get("pnl_r") is not None
        and t["pnl"] * t["pnl_r"] < 0
    )
    # A handful of flips is a rounding/BE artefact; a third of the book is not.
    if trades and flips / len(trades) > 0.15:
        problems.append(f"{flips}/{len(trades)} pnl vs pnl_r sign disagreements")
    return (not problems), problems


def stats(trades: list[dict]) -> dict:
    rs = [t["pnl_r"] for t in trades if t.get("pnl_r") is not None]
    pnls = [t["pnl"] for t in trades if t.get("pnl") is not None]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    return {
        "n": len(trades),
        "wr": (len(wins) / len(pnls)) if pnls else 0.0,
        "exp_r": (sum(rs) / len(rs)) if rs else float("nan"),
        "pnl": sum(pnls),
        "pf": (sum(wins) / abs(sum(losses))) if losses and sum(losses) else float("inf"),
    }


def line(label, trades, width=30):
    if not trades:
        return f"  {label:<{width}}{'—':>6}"
    s = stats(trades)
    return (f"  {label:<{width}}{s['n']:>6}{s['wr'] * 100:>7.0f}%"
            f"{s['exp_r']:>9.3f}{s['pf']:>8.2f}{s['pnl']:>12,.0f}")


def head(title, width=30):
    print(f"\n{title}")
    print(f"  {'':<{width}}{'n':>6}{'WR':>8}{'expR':>9}{'PF':>8}{'P&L':>12}")
    print("  " + "-" * (width + 43))


def split_consistency(by_symbol, pred, label_a, label_b):
    """How many symbols independently agree that A beats B on expectancy?"""
    agree, total = 0, 0
    for sym, trades in by_symbol.items():
        a = [t for t in trades if pred(t)]
        b = [t for t in trades if not pred(t)]
        if len(a) < 8 or len(b) < 8:
            continue
        total += 1
        if stats(a)["exp_r"] > stats(b)["exp_r"]:
            agree += 1
    return agree, total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="algoedge.db")
    args = ap.parse_args()

    raw = load(args.db)
    print(f"Loaded {len(raw)} APA runs, {sum(len(v) for v in raw.values())} trades\n")

    print("INTEGRITY GATE")
    clean = {}
    for sym, trades in raw.items():
        ok, problems = integrity(trades)
        mark = "OK " if ok else "DROP"
        print(f"  [{mark}] {sym[:24]:<26} n={len(trades):<5} "
              + ("; ".join(problems) if problems else ""))
        if ok:
            clean[sym] = trades

    pool = [t for v in clean.values() for t in v]
    print(f"\n  {len(clean)} runs usable, {len(pool)} trades\n")
    if not pool:
        return 1

    head("BY SYMBOL")
    for sym in sorted(clean, key=lambda s: -stats(clean[s])["exp_r"]):
        print(line(sym[:29], clean[sym]))
    print(line("POOLED", pool))

    # ── Confluence ──
    scores = sorted({t["confluence_score"] for t in pool if t.get("confluence_score")})
    if scores:
        med = statistics.median(scores)
        head(f"BY CONFLUENCE (median {med:.0f}; scores {scores[:8]})")
        lo = [t for t in pool if (t.get("confluence_score") or 0) <= med]
        hi = [t for t in pool if (t.get("confluence_score") or 0) > med]
        print(line(f"score <= {med:.0f}", lo))
        print(line(f"score >  {med:.0f}", hi))
        a, n = split_consistency(clean, lambda t: (t.get("confluence_score") or 0) <= med, "lo", "hi")
        print(f"\n  low-score beats high-score on {a}/{n} symbols independently")

    # ── Direction ──
    head("BY DIRECTION")
    for d in ("BUY", "SELL"):
        print(line(d, [t for t in pool if str(t.get("direction", "")).upper() == d]))
    a, n = split_consistency(clean, lambda t: str(t.get("direction", "")).upper() == "BUY", "buy", "sell")
    print(f"\n  BUY beats SELL on {a}/{n} symbols independently")

    # ── Session ──
    head("BY SESSION")
    sess = defaultdict(list)
    for t in pool:
        sess[str(t.get("session") or "UNKNOWN")].append(t)
    for k in sorted(sess, key=lambda k: -len(sess[k])):
        print(line(k, sess[k]))

    # ── Exit reason ──
    head("BY EXIT REASON")
    ex = defaultdict(list)
    for t in pool:
        ex[str(t.get("exit_reason") or "?")].append(t)
    for k in sorted(ex, key=lambda k: -len(ex[k])):
        print(line(k, ex[k]))

    # ── Break-even ──
    head("BREAK-EVEN APPLIED?")
    print(line("BE applied", [t for t in pool if t.get("be_applied")]))
    print(line("BE not applied", [t for t in pool if not t.get("be_applied")]))

    # ── MFE: where should the target sit? ──
    mfe = [(t["mfe_pips"], t["mae_pips"]) for t in pool
           if t.get("mfe_pips") is not None and t.get("mae_pips")]
    if mfe:
        print("\nMFE / MAE (pips)")
        f = [m for m, _ in mfe]
        a = [x for _, x in mfe]
        print(f"  MFE median {statistics.median(f):>8.1f}   mean {statistics.mean(f):>8.1f}   max {max(f):>9.1f}")
        print(f"  MAE median {statistics.median(a):>8.1f}   mean {statistics.mean(a):>8.1f}   max {max(a):>9.1f}")
        ratio = [m / x for m, x in mfe if x]
        if ratio:
            print(f"  MFE/MAE median {statistics.median(ratio):.2f} "
                  f"(>1 means trades go further right than wrong before resolving)")

    # ── Confluence x direction, the combination worth checking ──
    if scores:
        head("CONFLUENCE x DIRECTION")
        for d in ("BUY", "SELL"):
            for lbl, keep in (("low", True), ("high", False)):
                sub = [t for t in pool
                       if str(t.get("direction", "")).upper() == d
                       and (((t.get("confluence_score") or 0) <= med) == keep)]
                print(line(f"{d} / {lbl} score", sub))

    print("\nExpectancy (expR) is the ranking column — it is comparable across")
    print("rows with different trade counts, where total P&L is not.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
