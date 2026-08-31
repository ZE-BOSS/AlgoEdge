"""Does raising the confluence bar rescue Riptide?

At min_score=2 the strategy fires on nearly every sweep (~700 signals per symbol
in 8 months) and loses on almost everything. The design intent was to trade only
the HIGHEST-confluence setups, so this sweeps the threshold 2..5 and reports what
selectivity buys.

If the atom edge is real, expectancy should climb monotonically with the score
even as the sample shrinks. If it does not, the atoms do not stack.
"""
import numpy as np
from riptide import signals, backtest
from exit_lab import load

SYMS = ["XAUUSD", "US Tech 100", "BTCUSD", "GBPJPY", "Crash 1000 Index",
        "Volatility 75 Index", "US SP 500", "EURUSD"]
TARGETS = [2.0, 3.0, 5.0]


def main():
    print("RIPTIDE — confluence threshold sweep (min score 2..5 of 5)\n")
    grand = {}
    for sym in SYMS:
        b = load(sym, "M15")
        if b is None:
            continue
        allsigs = signals(b, min_score=1)      # get everything, filter after
        if not allsigs:
            continue
        cost_px = b["spread"] * b["point"]
        print(f"\n=== {sym}")
        print(f"  {'minScore':>9s}{'n':>7s}" +
              "".join(f"{'  1:%g expR' % t:>12s}{'PF':>6s}{'P&L$':>9s}" for t in TARGETS))
        for ms in (2, 3, 4, 5):
            sub = [s for s in allsigs if s[4] >= ms]
            if len(sub) < 20:
                print(f"  {ms:9d}{len(sub):7d}   too few")
                continue
            med = np.median([abs(e - st) for _, _, e, st, _ in sub])
            cr = cost_px / med if med > 0 else 0.0
            line = f"  {ms:9d}{len(sub):7d}"
            for t in TARGETS:
                r = backtest(b, sub, t, cost_r=cr)
                if r is None:
                    line += f"{'-':>12s}{'-':>6s}{'-':>9s}"
                    continue
                grand.setdefault((ms, t), []).append(r["exp"])
                line += f"{r['exp']:+12.4f}{r['pf']:6.2f}{r['pnl']:+9.0f}"
            print(line)

    print(f"\n{'=' * 78}\nAVERAGED ACROSS SYMBOLS")
    print(f"  {'minScore':>9s}" + "".join(f"{'  1:%g expR' % t:>12s}{'pos/n':>9s}"
                                          for t in TARGETS))
    for ms in (2, 3, 4, 5):
        line = f"  {ms:9d}"
        for t in TARGETS:
            v = grand.get((ms, t), [])
            if not v:
                line += f"{'-':>12s}{'-':>9s}"
                continue
            line += f"{np.mean(v):+12.4f}{sum(1 for x in v if x > 0):>5d}/{len(v):<3d}"
        print(line)


if __name__ == "__main__":
    main()
