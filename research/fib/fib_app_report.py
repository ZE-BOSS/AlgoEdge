"""FibTrend_v1 app-backtest tables (research/fib/app/*.trades.json): per market and one combined
$10k account, last 9 months and last 3 years. R per trade = pnl / 1% of the balance before it
(the runs sized at 1% of balance)."""
import json, math
from pathlib import Path
import numpy as np
from fib_v2 import REAL, SYNTH, W9, W36

APP = Path(__file__).parent / "app"


def load(sym):
    f = APP / f"{sym.replace(' ', '_')}.trades.json"
    tr = json.loads(f.read_text()) if f.exists() else []
    return [dict(t=float(x["entry_time"]), tx=float(x["exit_time"]), r=x["pnl"] / (0.01 * x["balance_before"]),
                 sym=sym, how=x["exit_reason"]) for x in tr if x.get("balance_before")]


def account(tr, start, capital=10_000.0, risk=0.01):
    tr = sorted((x for x in tr if x["t"] >= start), key=lambda x: x["t"])
    if not tr:
        return dict(n=0)
    ev = sorted([(x["t"], 1, i) for i, x in enumerate(tr)] + [(x["tx"] + 1, 0, i) for i, x in enumerate(tr)])
    eq, peak, dd, stake, nopen, mx = capital, capital, 0.0, {}, 0, 0
    for _, kind, i in ev:
        if kind:
            stake[i] = eq * risk; nopen += 1; mx = max(mx, nopen)
        else:
            eq += tr[i]["r"] * stake.pop(i); nopen -= 1
            peak = max(peak, eq); dd = max(dd, (peak - eq) / peak)
    rs = np.array([x["r"] for x in tr]); w, l = rs[rs > 0], rs[rs <= 0]
    return dict(n=len(rs), win=100 * (rs > 0).mean(), exp=rs.mean(), rr=(w.mean() / -l.mean()) if len(w) and len(l) else math.nan,
                pf=w.sum() / -l.sum() if l.sum() < 0 else math.inf, t=rs.mean() / rs.std(ddof=1) * math.sqrt(len(rs)) if len(rs) > 2 else math.nan,
                final=eq, ret=100 * (eq / capital - 1), dd=100 * dd, max_open=mx)


def line(name, a):
    if not a.get("n"):
        return f"| {name} | 0 | | | | | |"
    return (f"| {name} | {a['n']} | {a['win']:.0f}% | {a['exp']:+.2f}R | {a['rr']:.2f} | "
            f"${a['final']:,.0f} ({a['ret']:+.1f}%) | {a['dd']:.1f}% |")


if __name__ == "__main__":
    data = {s: load(s) for s in REAL + SYNTH}
    for wname, ws in (("Last 9 months (2026-01-24 -> 2026-10-24)", W9), ("Last 3 years (2023-10-25 -> 2026-10-24)", W36)):
        print(f"\n### {wname}\n| Market | Trades | Win | Avg R | Realised RR | $10,000 -> | Max DD |\n|---|---|---|---|---|---|---|")
        for s in REAL + SYNTH:
            print(line(s, account(data[s], ws)))
        for book, syms in (("BOOK real CFDs (8)", REAL), ("BOOK synthetics (12)", SYNTH), ("BOOK all 20", REAL + SYNTH)):
            a = account([x for s in syms for x in data[s]], ws)
            print(line(book, a) + f"  t {a.get('t', float('nan')):+.2f}, max open {a.get('max_open')}")
