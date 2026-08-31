"""Split take-profit sweep.

Leg A takes `frac` of the position at `a` R; leg B runs to `b` R. A leg fills if
the trade's stop-aware MFE reached its level, otherwise it keeps the trade's
actual realised R (path unchanged — break-even stop still active on the runner).
Fills are charged the realised TP slippage measured on actual 5R fills.
"""
import sqlite3

DB = "algoedge.db"

def load(db=DB):
    c = sqlite3.connect(db)
    return list(c.execute(
        "select strategy_id, symbol, pnl_r, mfe_r, exit_reason from backtest_trades"))

def slip(rows):
    f = [r[2] for r in rows if r[4] == 'TP1']
    return 5.0 - sum(f) / len(f) if f else 0.0

def leg(mfe, level, pnl_r, cost):
    return level - cost if mfe >= level else pnl_r

def evaluate(rows, frac, a, b, cost):
    tot = 0.0
    wins = losses = 0.0
    for _, _, pnl_r, mfe, _ in rows:
        r = frac * leg(mfe, a, pnl_r, cost) + (1 - frac) * leg(mfe, b, pnl_r, cost)
        tot += r
        if r > 0: wins += r
        else: losses -= r
    n = len(rows)
    return tot / n, tot, (wins / losses if losses else float('inf'))

if __name__ == "__main__":
    rows = load()
    cost = slip(rows)
    groups = {"ALL": rows}
    for r in rows:
        groups.setdefault(r[0], []).append(r)

    fracs = [0.0, 0.25, 0.4, 0.5, 0.6, 0.75, 1.0]
    As = [1.0, 1.25, 1.5, 1.75, 2.0, 2.5]
    B = 5.0
    for name, g in groups.items():
        print(f"=== {name} (n={len(g)}) — leg B fixed at {B}R")
        print(f"{'frac@A':>7} " + " ".join(f"{a:>8.2f}R" for a in As))
        best = None
        for f in fracs:
            cells = []
            for a in As:
                e, tot, pf = evaluate(g, f, a, B, cost)
                cells.append(e)
                if best is None or e > best[0]: best = (e, f, a, pf, tot)
            print(f"{f:7.2f} " + " ".join(f"{e:+9.3f}" for e in cells))
        e, f, a, pf, tot = best
        print(f"  best: {f:.0%} at {a}R + {1-f:.0%} at {B}R -> {e:+.3f}R/trade, PF {pf:.2f}, total {tot:+.0f}R\n")
