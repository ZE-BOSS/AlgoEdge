"""Run the exit lab across the symbols that matter, and report."""
import numpy as np
from exit_lab import LADDERS, load, simulate, summarise, hurst, atr

SYMS = ["Crash 1000 Index", "Crash 300 Index", "Crash 500 Index",
        "Volatility 75 Index", "XAUUSD", "US Tech 100", "US SP 500",
        "EURUSD", "GBPUSD", "BTCUSD", "XAGUSD", "Germany 40"]

STOP_MULT = 1.0
TARGET = 5.0


def main():
    print("=" * 78)
    print("PART 1 — regime: does this symbol trend? (Hurst on M15 close)")
    print("  >0.55 trending -> trailing should help")
    print("  <0.45 mean-reverting -> trailing should HURT; use a fixed target")
    print("=" * 78)
    H = {}
    for s in SYMS:
        b = load(s)
        if b is None:
            continue
        H[s] = hurst(b["close"])
        tag = ("TRENDING" if H[s] > 0.55 else
               "MEAN-REVERTING" if H[s] < 0.45 else "random walk")
        print(f"  {s:22s} H={H[s]:.3f}  {tag}")

    print("\n" + "=" * 78)
    print(f"PART 2 — exit method comparison  (stop {STOP_MULT}xATR, target {TARGET}R)")
    print("  every entry, both directions, real bar paths, stop-before-target")
    print("=" * 78)

    agg = {k: [] for k in LADDERS}
    for s in SYMS:
        b = load(s)
        if b is None:
            continue
        cost = (b["spread"] * b["point"]) / np.nanmedian(STOP_MULT * atr(
            b["high"], b["low"], b["close"]))
        print(f"\n--- {s}   (cost {cost:.3f} R/trade, H={H.get(s, float('nan')):.2f})")
        print(f"    {'method':18s}{'n':>7s}{'expR':>9s}{'totR':>9s}"
              f"{'WR':>8s}{'PF':>7s}{'maxDD':>9s}{'ret/DD':>8s}")
        rows = {}
        for name, lad in LADDERS.items():
            rs = np.concatenate([
                simulate(b, d, STOP_MULT, TARGET, lad, cost_r=cost)
                for d in ("BUY", "SELL")])
            st = summarise(rs)
            if not st:
                continue
            rows[name] = st
            agg[name].append(st["exp"])
            print(f"    {name:18s}{st['n']:7d}{st['exp']:+9.4f}{st['total']:+9.1f}"
                  f"{st['wr']:8.1%}{st['pf']:7.2f}{st['dd']:9.1f}{st['ret_dd']:8.2f}")
        if rows:
            best = max(rows, key=lambda k: rows[k]["exp"])
            print(f"    => best by expectancy: {best}")

    print("\n" + "=" * 78)
    print("PART 3 — averaged across symbols")
    print("=" * 78)
    print(f"  {'method':18s}{'mean expR':>12s}{'symbols won':>14s}")
    base = np.mean(agg["fixed_TP_only"])
    for name, v in agg.items():
        if not v:
            continue
        won = sum(1 for i, x in enumerate(v) if x > agg["fixed_TP_only"][i])
        print(f"  {name:18s}{np.mean(v):+12.4f}{won:>10d}/{len(v)}"
              f"{'   <-- baseline' if name == 'fixed_TP_only' else ''}")

    print("\n  trailing lift vs fixed TP, split by regime:")
    for name in LADDERS:
        if name == "fixed_TP_only" or not agg[name]:
            continue
        tr = [agg[name][i] - agg["fixed_TP_only"][i]
              for i, s in enumerate(SYMS[:len(agg[name])]) if H.get(s, 0.5) > 0.55]
        mr = [agg[name][i] - agg["fixed_TP_only"][i]
              for i, s in enumerate(SYMS[:len(agg[name])]) if H.get(s, 0.5) <= 0.55]
        print(f"    {name:18s} trending {np.mean(tr):+.4f} (n={len(tr)})   "
              f"non-trending {np.mean(mr):+.4f} (n={len(mr)})")


if __name__ == "__main__":
    main()
