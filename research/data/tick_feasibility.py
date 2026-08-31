"""Stage F: can MT5 tick data resolve a Crash-index spike as it forms?

The spike is the trade. The question is whether ticks arrive fast enough, and
carry enough information, to detect one starting rather than only after it has
finished.
"""
from datetime import datetime, timedelta, timezone
import numpy as np, MetaTrader5 as mt5
from mt5_probe import connect

SYMS = ["Crash 300 Index", "Crash 500 Index", "Crash 1000 Index"]

def ticks(sym, hours=6):
    to = datetime.now(timezone.utc)
    fr = to - timedelta(hours=hours)
    t = mt5.copy_ticks_range(sym, fr, to, mt5.COPY_TICKS_ALL)
    return t

if __name__ == "__main__":
    if not connect():
        raise SystemExit("no MT5")
    for sym in SYMS:
        mt5.symbol_select(sym, True)
        t = ticks(sym)
        if t is None or len(t) == 0:
            print(f"{sym}: no ticks"); continue
        ms = t['time_msc'].astype(np.int64)
        gaps = np.diff(ms)
        bid = t['bid'].astype(float)
        ret = np.diff(bid) / bid[:-1]
        # a "spike" = a drop far beyond normal tick-to-tick movement
        thr = np.percentile(ret[ret < 0], 0.5) if (ret < 0).any() else 0
        spikes = np.where(ret <= thr)[0]
        print(f"\n=== {sym}  ({len(t)} ticks over 6h)")
        print(f"  tick interval ms : median={np.median(gaps):.0f} p90={np.percentile(gaps,90):.0f} max={gaps.max():.0f}")
        print(f"  ticks per second : {len(t)/(6*3600):.2f}")
        print(f"  return  p50={np.percentile(np.abs(ret),50)*100:.4f}%  p99={np.percentile(np.abs(ret),99)*100:.4f}%")
        print(f"  drop threshold(0.5pct of negatives) = {thr*100:.4f}%  -> {len(spikes)} events in 6h")
        if len(spikes):
            # how many ticks does a spike take to complete?
            durs, mags = [], []
            for i in spikes[:40]:
                j = i
                while j + 1 < len(ret) and ret[j + 1] < 0:
                    j += 1
                durs.append(j - i + 1)
                mags.append((bid[j + 1] - bid[i]) / bid[i] * 100)
            print(f"  spike length     : median={np.median(durs):.0f} ticks, max={max(durs)} ticks")
            print(f"  spike magnitude  : median={np.median(mags):.3f}%  worst={min(mags):.3f}%")
            print(f"  -> {'DETECTABLE mid-move' if np.median(durs) >= 3 else 'SINGLE-TICK GAP — not detectable mid-move'}")
    mt5.shutdown()
