"""Stage C, second attempt: is order flow backtestable after all?

Report 03 said fundamentals are blocked because nothing stores history. That is
true of the order BOOK (depth is a live snapshot, never saved). But MT5 serves
historical TICKS, and the project's own MT5OrderFlowProvider infers cumulative
volume delta from bid/ask movement rather than from a real tape.

If historical ticks reach back to January, that same inference can be run over
the past - which would make order flow measurable without waiting months to
collect it. This tests exactly that.
"""
from datetime import datetime, timezone, timedelta
import numpy as np, MetaTrader5 as mt5
from mt5_probe import connect

SYMS = ["XAUUSD", "EURUSD", "BTCUSD", "US Tech 100", "Crash 1000 Index"]
PROBES = [
    ("Jan 2026", datetime(2026, 1, 5, tzinfo=timezone.utc)),
    ("Apr 2026", datetime(2026, 4, 6, tzinfo=timezone.utc)),
    ("Jul 2026", datetime(2026, 7, 6, tzinfo=timezone.utc)),
    ("last week", datetime.now(timezone.utc) - timedelta(days=7)),
]

if __name__ == "__main__":
    if not connect():
        raise SystemExit("no MT5")
    print(f"{'symbol':20s}" + "".join(f"{n:>14s}" for n, _ in PROBES))
    for s in SYMS:
        info = mt5.symbol_info(s)
        if info is None:
            print(f"{s:20s} NOT FOUND"); continue
        if not info.visible:
            mt5.symbol_select(s, True)
        row = f"{s:20s}"
        for _, d in PROBES:
            t = mt5.copy_ticks_range(s, d, d + timedelta(days=1), mt5.COPY_TICKS_ALL)
            row += f"{(len(t) if t is not None else 0):14,d}"
        print(row)

    print("\n--- can we infer order flow from a historical tick block? ---")
    d = datetime(2026, 1, 5, tzinfo=timezone.utc)
    for s in ("XAUUSD", "EURUSD"):
        t = mt5.copy_ticks_range(s, d, d + timedelta(days=1), mt5.COPY_TICKS_ALL)
        if t is None or len(t) == 0:
            print(f"  {s}: no ticks in January"); continue
        flags = t["flags"]
        has_vol = np.any(t["volume"] > 0)
        bid, ask = t["bid"].astype(float), t["ask"].astype(float)
        mid = (bid + ask) / 2
        # tick-rule CVD: uptick = buy-initiated, downtick = sell-initiated
        dmid = np.diff(mid)
        cvd = np.sign(dmid)
        print(f"  {s}: {len(t):,} ticks | real volume field populated: {has_vol} "
              f"| flags present: {np.unique(flags)[:5]}")
        print(f"      tick-rule CVD computable: yes, "
              f"net={int(cvd.sum()):+,} over the day "
              f"({(cvd > 0).mean():.1%} upticks)")
    mt5.shutdown()
