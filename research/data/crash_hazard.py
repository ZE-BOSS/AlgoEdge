"""Stage F, the plain-English version of the timing test.

CV ~= 1.0 is the statistician's way of saying "memoryless". This says the same
thing without asking anyone to trust a statistic:

  If you have already waited X minutes since the last spike, what is the chance
  a spike arrives in the next 10 minutes?

If waiting helps, that number rises with X and there is a tradeable timing edge:
wait until the odds improve, then position short. If it is flat, waiting tells
you nothing and no entry rule based on elapsed time can work.
"""
import numpy as np, MetaTrader5 as mt5
from mt5_probe import connect

SYMS = ["Crash 300 Index", "Crash 500 Index", "Crash 1000 Index"]
WINDOW = 10  # minutes ahead

if __name__ == "__main__":
    if not connect():
        raise SystemExit("no MT5")
    for sym in SYMS:
        mt5.symbol_select(sym, True)
        r = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M1, 0, 50000)
        o, l = r['open'].astype(float), r['low'].astype(float)
        drop = (o - l) / o
        is_spike = drop >= np.percentile(drop, 99.0)
        spike_idx = np.where(is_spike)[0]

        # for every minute, how long since the last spike?
        since = np.full(len(o), -1)
        last = -1
        for i in range(len(o)):
            since[i] = i - last if last >= 0 else -1
            if is_spike[i]:
                last = i

        print(f"\n=== {sym}  ({len(spike_idx)} spikes in 50,000 minutes)")
        print(f"  base rate: a spike in any given {WINDOW} min = "
              f"{len(spike_idx) / len(o) * WINDOW:.2%}")
        print(f"  {'minutes waited':>18s} {'n':>7s} {'P(spike in next 10m)':>22s}")
        for lo, hi in [(0, 15), (15, 30), (30, 60), (60, 90), (90, 120),
                       (120, 180), (180, 300), (300, 10 ** 9)]:
            sel = np.where((since >= lo) & (since < hi))[0]
            sel = sel[sel < len(o) - WINDOW]
            if len(sel) < 50:
                continue
            hit = np.array([is_spike[i + 1:i + 1 + WINDOW].any() for i in sel])
            label = f"{lo}-{hi} min" if hi < 10 ** 9 else f"{lo}+ min"
            print(f"  {label:>18s} {len(sel):7d} {hit.mean():21.2%}")
        print("  -> a FLAT column means waiting buys you nothing.")
    mt5.shutdown()
