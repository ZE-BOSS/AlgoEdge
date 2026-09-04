"""Phase 0: pull MT5 bars back to inception for the synthetic core.

Ticks reach 365 days. Bars reach 2019. The two answer different questions:
tick data measures the *generator*, bar data measures whether the generator's
parameters have *drifted* over the instrument's life - which one year cannot see.

`count` must be < 100000: copy_rates_from_pos(..., 100000) returns None with
(-2, 'Terminal: Invalid params'). That is the terminal's `maxbars` setting, and it
is why M1 stops at ~69 days. copy_rates_range from an early date is not a
workaround - it forces a full server download and takes minutes per symbol.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import MetaTrader5 as mt5                                    # noqa: E402
from mt5_probe import connect                                # noqa: E402

OUT = Path(__file__).resolve().parent / "bars"
CAP = 99999

SYMBOLS = [
    "Boom 1000 Index", "Boom 500 Index", "Crash 1000 Index", "Crash 500 Index",
    "Step Index", "Range Break 100 Index", "Range Break 200 Index",
    "Jump 25 Index", "Jump 100 Index",
    "Volatility 75 Index", "Volatility 25 Index", "Volatility 100 Index",
]
TFS = [("M15", mt5.TIMEFRAME_M15), ("H1", mt5.TIMEFRAME_H1), ("D1", mt5.TIMEFRAME_D1)]


def main() -> None:
    if not connect():
        raise SystemExit("no MT5")
    OUT.mkdir(exist_ok=True)
    print(f"{'symbol':24s}{'tf':>5s}{'bars':>10s}{'from':>13s}{'to':>13s}{'years':>7s}")
    for s in SYMBOLS:
        info = mt5.symbol_info(s)
        if info is None:
            print(f"{s:24s}  NOT FOUND", flush=True)
            continue
        mt5.symbol_select(s, True)
        for name, tf in TFS:
            f = OUT / f"{s.replace(' ', '_')}_{name}.npz"
            if f.exists():
                z = np.load(f)
                print(f"{s:24s}{name:>5s}{len(z['t']):10,d}   (cached)", flush=True)
                continue
            for attempt in range(3):
                r = mt5.copy_rates_from_pos(s, tf, 0, CAP)
                if r is not None and len(r):
                    break
                time.sleep(2)
            if r is None or not len(r):
                print(f"{s:24s}{name:>5s}   FAILED {mt5.last_error()}", flush=True)
                continue
            a = datetime.fromtimestamp(r[0][0], tz=timezone.utc)
            b = datetime.fromtimestamp(r[-1][0], tz=timezone.utc)
            np.savez_compressed(
                f, t=r["time"].astype(np.int64), o=r["open"].astype(np.float64),
                h=r["high"].astype(np.float64), l=r["low"].astype(np.float64),
                c=r["close"].astype(np.float64), v=r["tick_volume"].astype(np.int64),
                spread=r["spread"].astype(np.int32),
                meta=np.array([s, name, str(info.point), str(info.digits)], dtype=object))
            print(f"{s:24s}{name:>5s}{len(r):10,d}{a.strftime('%Y-%m-%d'):>13s}"
                  f"{b.strftime('%Y-%m-%d'):>13s}{(b - a).days / 365.25:7.1f}", flush=True)
    mt5.shutdown()


if __name__ == "__main__":
    main()
