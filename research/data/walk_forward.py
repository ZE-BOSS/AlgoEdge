"""Out-of-sample test of strategy x symbol selection.

Select cells on the first half of the window, trade them in the second half.
In-sample cherry-picking always looks good; the question is whether the choice
survives the split.
"""
import sqlite3, collections

DB = "../../algoedge.db"
SPLIT = "2026-05-01"

def run(min_n_is, label):
    c = sqlite3.connect(DB)
    IS, OOS = collections.defaultdict(list), collections.defaultdict(list)
    for sid, sym, pnl_r, et in c.execute(
            "select strategy_id, symbol, pnl_r, entry_time from backtest_trades"):
        (IS if str(et) < SPLIT else OOS)[(sid, sym)].append(pnl_r)

    sel = {k for k, v in IS.items() if len(v) >= min_n_is and sum(v) > 0}
    oos_sel = [r for k in sel for r in OOS.get(k, [])]
    oos_all = [r for v in OOS.values() for r in v]
    is_sel = [r for k in sel for r in IS[k]]

    print(f"--- {label} (select on IS n>={min_n_is} and positive)")
    print(f"  cells selected     : {len(sel)} of {len(IS)}")
    print(f"  IS  selected cells : n={len(is_sel):5d}  expR={sum(is_sel)/len(is_sel):+.3f}  totR={sum(is_sel):+.1f}")
    if oos_sel:
        print(f"  OOS selected cells : n={len(oos_sel):5d}  expR={sum(oos_sel)/len(oos_sel):+.3f}  totR={sum(oos_sel):+.1f}")
    print(f"  OOS whole book     : n={len(oos_all):5d}  expR={sum(oos_all)/len(oos_all):+.3f}  totR={sum(oos_all):+.1f}")
    if oos_sel:
        edge = sum(oos_sel)/len(oos_sel) - sum(oos_all)/len(oos_all)
        print(f"  selection edge OOS : {edge:+.3f}R/trade")
    print()
    return sel

if __name__ == "__main__":
    for m in (20, 30, 50):
        run(m, f"min IS trades = {m}")
    # strategy-level selection instead of cell-level
    c = sqlite3.connect(DB)
    IS, OOS = collections.defaultdict(list), collections.defaultdict(list)
    for sid, pnl_r, et in c.execute("select strategy_id, pnl_r, entry_time from backtest_trades"):
        (IS if str(et) < SPLIT else OOS)[sid].append(pnl_r)
    print("--- per strategy: in-sample vs out-of-sample expectancy")
    print(f"{'strategy':20s} {'IS n':>6s} {'IS expR':>9s} {'OOS n':>6s} {'OOS expR':>9s} {'drift':>8s}")
    for s in sorted(IS):
        i, o = IS[s], OOS.get(s, [])
        if not o: continue
        ei, eo = sum(i)/len(i), sum(o)/len(o)
        print(f"{s:20s} {len(i):6d} {ei:+9.3f} {len(o):6d} {eo:+9.3f} {eo-ei:+8.3f}")
