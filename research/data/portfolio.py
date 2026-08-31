"""Stage E: correlation and portfolio construction.

Correlation basis is daily R over the full window (per the plan's rule that
monthly P&L over a short window is noise-dominated). Two matrices: one across
strategies, one across the surviving strategy x symbol cells.
"""
import sqlite3, collections, math

DB = "../../algoedge.db"


def daily_series(key_fn):
    c = sqlite3.connect(DB)
    s = collections.defaultdict(lambda: collections.defaultdict(float))
    days = set()
    for sid, sym, r, et in c.execute(
            "select strategy_id, symbol, pnl_r, entry_time from backtest_trades"):
        d = str(et)[:10]
        s[key_fn(sid, sym)][d] += r
        days.add(d)
    return s, sorted(days)


def corr(a, b, days):
    xs = [a.get(d, 0.0) for d in days]
    ys = [b.get(d, 0.0) for d in days]
    n = len(days)
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return num / (dx * dy) if dx and dy else 0.0


def matrix(series, days, label, keys=None):
    keys = keys or sorted(series)
    print(f"\n=== {label} — daily-R correlation ({len(days)} trading days)")
    w = max(len(str(k)) for k in keys) + 1
    print(" " * w + "".join(f"{str(k)[:7]:>8s}" for k in keys))
    for a in keys:
        row = "".join(f"{corr(series[a], series[b], days):+8.2f}" for b in keys)
        print(f"{str(a):{w}s}{row}")


def portfolio(series, days, keys, label):
    tot = [sum(series[k].get(d, 0.0) for k in keys) for d in days]
    n = len(tot)
    mean = sum(tot) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in tot) / n)
    eq, peak, mdd = 0.0, 0.0, 0.0
    for x in tot:
        eq += x
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    print(f"\n{label}")
    print(f"  components   : {len(keys)}")
    print(f"  total R      : {sum(tot):+.1f} over {n} days")
    print(f"  daily mean R : {mean:+.3f}  sd {sd:.3f}")
    print(f"  Sharpe (ann) : {mean / sd * math.sqrt(252):.2f}" if sd else "")
    print(f"  max drawdown : {mdd:.1f}R")
    print(f"  R / maxDD    : {sum(tot) / mdd:.2f}" if mdd else "")


if __name__ == "__main__":
    s_strat, days = daily_series(lambda sid, sym: sid.replace("_v1", ""))
    matrix(s_strat, days, "Strategies")

    s_cell, _ = daily_series(lambda sid, sym: f"{sid.replace('_v1','')[:6]}/{sym[:10]}")
    survivors = ["DriftJ/Crash 1000", "DriftJ/Crash 300", "VWAP/Volatility"]
    keys = [k for k in s_cell if any(k.startswith(x[:12]) for x in survivors)]
    if keys:
        matrix(s_cell, days, "Surviving cells", keys)
        portfolio(s_cell, days, keys, "PORTFOLIO — surviving cells (equal weight)")

    portfolio(s_strat, days, list(s_strat), "PORTFOLIO — whole book (all 7 strategies)")
    portfolio(s_strat, days, ["DriftJumpAlpha"], "PORTFOLIO — DriftJumpAlpha alone")
