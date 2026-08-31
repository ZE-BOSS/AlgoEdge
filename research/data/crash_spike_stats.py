"""Stage F: is Crash-index spike timing predictable?

If the spike arrival process is memoryless (exponential inter-arrival times),
then time-since-last-spike carries no information and no amount of waiting
improves the odds of the next one. That is the difference between a tradeable
sell-side setup and a coin flip.
"""
import numpy as np, MetaTrader5 as mt5
from mt5_probe import connect

SYMS = ["Crash 300 Index", "Crash 500 Index", "Crash 1000 Index"]

if __name__ == "__main__":
    if not connect():
        raise SystemExit("no MT5")
    for sym in SYMS:
        mt5.symbol_select(sym, True)
        r = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M1, 0, 50000)
        if r is None or len(r) < 1000:
            print(f"{sym}: insufficient bars"); continue
        o, c, l = r['open'].astype(float), r['close'].astype(float), r['low'].astype(float)
        drop = (o - l) / o
        thr = np.percentile(drop, 99.0)
        idx = np.where(drop >= thr)[0]
        gaps = np.diff(idx).astype(float)
        span_days = (r['time'][-1] - r['time'][0]) / 86400
        sp = mt5.symbol_info(sym)
        spread_pct = sp.spread * sp.point / c[-1] * 100
        mag = drop[idx] * 100
        close_ret = (o[idx] - c[idx]) / o[idx] * 100
        print(f"\n=== {sym}: {len(r)} M1 bars over {span_days:.0f} days")
        print(f"  spike threshold (99th pct open->low drop) = {thr*100:.3f}%")
        print(f"  spikes = {len(idx)}, one per {gaps.mean():.1f} min")
        print(f"  inter-arrival mean={gaps.mean():.1f} sd={gaps.std():.1f} CV={gaps.std()/gaps.mean():.3f}")
        print(f"    CV near 1.0 => memoryless (exponential) => timing NOT predictable")
        qs = np.percentile(gaps, [25, 50, 75, 95])
        print(f"  inter-arrival p25/p50/p75/p95 = {qs[0]:.0f}/{qs[1]:.0f}/{qs[2]:.0f}/{qs[3]:.0f} min")
        print(f"  spike depth %: median={np.median(mag):.3f} p90={np.percentile(mag,90):.3f} max={mag.max():.3f}")
        print(f"  open->close on the spike bar %: median={np.median(close_ret):.3f} (short-at-open, hold to close)")
        print(f"  spread={spread_pct:.4f}% => spike depth is {np.median(mag)/spread_pct:.1f}x spread")
    mt5.shutdown()
