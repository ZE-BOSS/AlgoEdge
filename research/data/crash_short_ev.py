"""Stage F: does a short position on a Crash index have any positive EV?

Spike timing is memoryless, so anticipation is out. The remaining question is
whether being short *continuously* pays: you collect the spikes but you pay the
upward drift while you wait. Deriv prices these instruments so those cancel;
this measures whether that is actually true on our feed, net of spread.
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
        c, o, l = r['close'].astype(float), r['open'].astype(float), r['low'].astype(float)
        sp = mt5.symbol_info(sym)
        spread_pct = sp.spread * sp.point / c[-1] * 100
        days = (r['time'][-1] - r['time'][0]) / 86400
        drop = (o - l) / o
        thr = np.percentile(drop, 99.0)
        idx = np.where(drop >= thr)[0]

        net = (c[-1] - c[0]) / c[0] * 100
        up = np.diff(c)
        gross_up = up[up > 0].sum() / c[0] * 100
        gross_dn = -up[up < 0].sum() / c[0] * 100
        spike_total = drop[idx].sum() * 100

        print(f"\n=== {sym} - {days:.0f} days, 50,000 M1 bars")
        print(f"  net price change      : {net:+.2f}%   (short-and-hold = {-net:+.2f}% before costs)")
        print(f"  gross up / gross down : {gross_up:+.2f}% / {gross_dn:+.2f}%")
        print(f"  of which top-1% spikes: {spike_total:.2f}%  ({spike_total/gross_dn*100:.0f}% of all downside)")
        print(f"  spread                : {spread_pct:.4f}% per round trip")
        for hold in (60, 240, 1440):
            e = np.arange(0, len(c) - hold, hold)
            rets = (c[e] - c[e + hold]) / c[e] - spread_pct / 100
            print(f"  short, held {hold:5d} bars: n={len(rets):4d} mean={rets.mean()*100:+.4f}% "
                  f"WR={(rets > 0).mean():.1%} total={rets.sum()*100:+.2f}%")
        med = np.median(drop[idx]) * 100
        print(f"  median spike {med:.3f}% => a filled spike pays {med/spread_pct:.0f}x spread")
    mt5.shutdown()
