"""Counterfactual take-profit sweep from observed stop-aware MFE.

Logic: a TP at k R would have been filled on any trade whose recorded maximum
favourable excursion reached k R (MFE is recorded up to the actual exit, so it is
already stop-aware and, for BE-stopped trades, a lower bound). Trades that never
reached k R keep their actual realised R. Costs are folded in by charging the
same average slippage the real TP1 fills paid.
"""
import sqlite3, collections, json, sys

DB = "algoedge.db"
LEVELS = [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0]

def load():
    c = sqlite3.connect(DB)
    return list(c.execute("""
        select strategy_id, symbol, pnl_r, mfe_r, mae_r, exit_reason, be_applied
        from backtest_trades"""))

def tp_cost(rows):
    """Realised slippage on actual 5R fills: planned 5.0 vs achieved."""
    fills = [r[2] for r in rows if r[5] == 'TP1']
    return 5.0 - (sum(fills) / len(fills)) if fills else 0.0

def sweep(rows, cost):
    out = {}
    for k in LEVELS:
        rs = []
        for _, _, pnl_r, mfe_r, _, _, _ in rows:
            rs.append(k - cost if mfe_r >= k else pnl_r)
        n = len(rs)
        wins = [x for x in rs if x > 0]
        losses = [x for x in rs if x <= 0]
        gp, gl = sum(wins), -sum(losses)
        out[k] = dict(
            n=n,
            hit=sum(1 for _, _, _, m, _, _, _ in rows if m >= k) / n,
            exp_r=sum(rs) / n,
            total_r=sum(rs),
            wr=len(wins) / n,
            pf=(gp / gl) if gl else float('inf'),
        )
    return out

if __name__ == "__main__":
    rows = load()
    cost = tp_cost(rows)
    print(f"realised TP slippage: {cost:.3f}R per fill\n")
    groups = {"ALL": rows}
    for r in rows:
        groups.setdefault(r[0], []).append(r)
    for name, g in groups.items():
        res = sweep(g, cost)
        best = max(res, key=lambda k: res[k]['exp_r'])
        print(f"=== {name}  (n={len(g)}) — best TP {best}R @ {res[best]['exp_r']:+.3f}R")
        print(f"{'TP':>5} {'P(hit)':>8} {'exp R':>8} {'total R':>9} {'WR':>7} {'PF':>7}")
        for k, v in res.items():
            mark = " <=" if k == best else ""
            print(f"{k:5.2f} {v['hit']:8.1%} {v['exp_r']:+8.3f} {v['total_r']:+9.1f} {v['wr']:7.1%} {v['pf']:7.2f}{mark}")
        print()
