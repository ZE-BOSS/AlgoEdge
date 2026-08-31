"""Run Riptide across every asset class at 1:2, 1:3 and 1:5. $10,000 capital."""
import numpy as np
from pathlib import Path
from riptide import signals, backtest
from exit_lab import load

CLASSES = {
    "crypto":  ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"],
    "index":   ["US Tech 100", "US SP 500", "Germany 40", "Netherlands 25", "Hong Kong 50"],
    "fx":      ["EURUSD", "GBPUSD", "USDJPY", "GBPJPY", "EURGBP", "USDCHF", "AUDUSD"],
    "metal":   ["XAUUSD", "XAGUSD", "XPTUSD"],
    "synth":   ["Crash 300 Index", "Crash 500 Index", "Crash 1000 Index",
                "Jump 100 Index", "Volatility 75 Index"],
}
TARGETS = [2.0, 3.0, 5.0]
MIN_SCORE = 2


def main():
    print("RIPTIDE — liquidity sweep + premium/discount only")
    print(f"$10,000 per asset · 1% risk · min confluence {MIN_SCORE}/5 · M15\n")
    totals = {t: [] for t in TARGETS}
    rows = []
    for cls, syms in CLASSES.items():
        print(f"\n{'=' * 86}\n{cls.upper()}")
        print(f"{'symbol':20s}{'sigs':>6s}" +
              "".join(f"{'  1:%g P&L' % t:>12s}{'DD%':>7s}{'PF':>6s}" for t in TARGETS))
        for sym in syms:
            b = load(sym, "M15")
            if b is None:
                print(f"{sym:20s}  no data"); continue
            cost = (b["spread"] * b["point"])
            sigs = signals(b)
            if not sigs:
                print(f"{sym:20s}{0:6d}   no signals"); continue
            line = f"{sym:20s}{len(sigs):6d}"
            rec = {"symbol": sym, "class": cls, "sigs": len(sigs)}
            for t in TARGETS:
                med_risk = np.median([abs(e - s) for _, _, e, s, _ in sigs])
                cr = cost / med_risk if med_risk > 0 else 0.0
                r = backtest(b, sigs, t, cost_r=cr)
                if r is None:
                    line += f"{'-':>12s}{'-':>7s}{'-':>6s}"
                    continue
                totals[t].append(r["exp"])
                rec[t] = r
                line += f"{r['pnl']:+12.0f}{r['maxdd_pct']:7.1f}{r['pf']:6.2f}"
            rows.append(rec)
            print(line)

    print(f"\n{'=' * 86}\nSUMMARY — mean expectancy per target")
    for t in TARGETS:
        v = totals[t]
        if v:
            print(f"  1:{t:g}   mean expR {np.mean(v):+.4f}   "
                  f"positive on {sum(1 for x in v if x > 0)}/{len(v)} symbols")

    best_t = max(TARGETS, key=lambda t: np.mean(totals[t]) if totals[t] else -9)
    print(f"\nBest target overall: 1:{best_t:g}")
    print(f"\n{'=' * 86}\nTOP CELLS at 1:{best_t:g}")
    ranked = sorted([r for r in rows if best_t in r],
                    key=lambda r: -r[best_t]["pnl"])
    print(f"{'symbol':20s}{'class':8s}{'n':>6s}{'P&L':>11s}{'DD%':>7s}{'WR':>7s}{'PF':>6s}{'expR':>9s}")
    for r in ranked[:12]:
        x = r[best_t]
        print(f"{r['symbol']:20s}{r['class']:8s}{x['n']:6d}{x['pnl']:+11.0f}"
              f"{x['maxdd_pct']:7.1f}{x['wr']:7.1%}{x['pf']:6.2f}{x['exp']:+9.4f}")
    print("\nWORST")
    for r in ranked[-5:]:
        x = r[best_t]
        print(f"{r['symbol']:20s}{r['class']:8s}{x['n']:6d}{x['pnl']:+11.0f}"
              f"{x['maxdd_pct']:7.1f}{x['wr']:7.1%}{x['pf']:6.2f}{x['exp']:+9.4f}")


if __name__ == "__main__":
    main()
