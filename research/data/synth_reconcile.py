"""Phase 0 gate: do Deriv's tick feed and MT5's bars describe the same instrument?

Everything downstream mixes the two - ticks measure the generator, bars measure
whether its parameters drifted over seven years. If they disagree, we need to know
here, not in Phase 4 when a strategy fitted on ticks is backtested on bars.

Method: rebuild M1 OHLC from raw ticks, align to MT5's own M1, and compare. The
server's clock offset is *detected*, not assumed - MT5 returns bar times in server
timezone as a Unix timestamp, which is the classic way to be silently an hour out.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import MetaTrader5 as mt5                                    # noqa: E402
from mt5_probe import connect                                # noqa: E402

TICKS = Path(__file__).resolve().parent / "ticks"
PAIRS = [("BOOM1000", "Boom 1000 Index"), ("R_75", "Volatility 75 Index")]


def bars_from_ticks(t: np.ndarray, p: np.ndarray) -> dict[int, tuple]:
    """Minute-bucket OHLC straight from the tick stream."""
    minute = (t // 60) * 60
    out: dict[int, tuple] = {}
    edges = np.flatnonzero(np.diff(minute)) + 1
    for lo, hi in zip(np.concatenate(([0], edges)),
                      np.concatenate((edges, [len(minute)]))):
        seg = p[lo:hi]
        out[int(minute[lo])] = (seg[0], seg.max(), seg.min(), seg[-1], hi - lo)
    return out


def main() -> None:
    if not connect():
        raise SystemExit("no MT5")
    for code, name in PAIRS:
        f = sorted(TICKS.glob(f"{code}_*d.npz"))
        if not f:
            print(f"{code}: no tick file yet - skipping"); continue
        f = max(f, key=lambda x: x.stat().st_size)
        z = np.load(f, allow_pickle=True)
        t, p = z["t"], z["p"]
        tb = bars_from_ticks(t, p)

        mt5.symbol_select(name, True)
        r = mt5.copy_rates_from_pos(name, mt5.TIMEFRAME_M1, 0, 99999)
        if r is None:
            print(f"{code}: no MT5 M1"); continue

        print(f"\n=== {code} / {name}   (ticks: {f.name}, {len(t):,} ticks)")
        # Detect the clock offset by PRICE agreement, not by key overlap. These
        # series are continuous 24/7, so every candidate offset shares almost the
        # same number of minute keys and an overlap-count search just returns
        # whichever offset it scanned first. Median |close diff| separates cleanly.
        best, best_err = None, np.inf
        for off_h in range(-24, 25):
            off = off_h * 3600
            idx = {int(x) - off: i for i, x in enumerate(r["time"])}
            common = list(idx.keys() & tb.keys())[:3000]
            if len(common) < 200:
                continue
            e = float(np.median([abs(tb[k][3] - r["close"][idx[k]]) for k in common]))
            if e < best_err:
                best_err, best = e, off_h
        print(f"  MT5 clock offset vs UTC: {best:+d}h "
              f"(median |close| diff {best_err:.6g} at best alignment)")

        off = best * 3600
        keys = sorted(set(int(x) - off for x in r["time"]) & tb.keys())
        if len(keys) < 100:
            print("  too little overlap to compare"); continue
        idx = {int(x) - off: i for i, x in enumerate(r["time"])}
        dc, dh, dl, dv = [], [], [], []
        for k in keys:
            o2, h2, l2, c2, n2 = tb[k]
            i = idx[k]
            dc.append(abs(c2 - r["close"][i]))
            dh.append(abs(h2 - r["high"][i]))
            dl.append(abs(l2 - r["low"][i]))
            dv.append(n2 - r["tick_volume"][i])
        dc, dh, dl, dv = map(np.asarray, (dc, dh, dl, dv))
        px = float(np.median(r["close"]))
        print(f"  compared {len(keys):,} minutes")
        print(f"  |close| diff: median {np.median(dc):.6g}  p99 {np.percentile(dc,99):.6g}"
              f"  ({np.median(dc)/px*100:.6f}% of price)")
        print(f"  |high|  diff: median {np.median(dh):.6g}   |low| diff: median {np.median(dl):.6g}")
        print(f"  exact close matches: {(dc == 0).mean():.2%}")
        print(f"  tick_volume delta (ours - MT5): median {np.median(dv):+.0f} "
              f"mean {dv.mean():+.2f}")
        verdict = ("SAME INSTRUMENT" if np.median(dc) / px < 1e-5
                   else "MISMATCH - investigate before using both sources")
        print(f"  -> {verdict}")
    mt5.shutdown()


if __name__ == "__main__":
    main()
