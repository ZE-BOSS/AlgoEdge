"""Harvest MT5 bars to inception for the LIVE-MARKET universe.

Phase 1 closed the synthetic universe: fair by construction, memoryless, no
clustering, nothing to trade. Real markets are different in two specific,
measurable ways, and both are worth testing rather than assuming:

* **a risk premium** - equities are compensated for bearing risk, so their drift
  is genuinely non-zero. Synthetics have none by design (report 20 section 2).
* **volatility clustering** - real returns have strongly autocorrelated squared
  returns. Synthetics measured within +-0.009 of zero at every lag, which is what
  made that test such a clean discriminator.

Same constraints as the synthetic harvest: `count` must be < 100000, and
copy_rates_range from an early date forces a slow full download, so use
copy_rates_from_pos with count=99999.
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

OUT = Path(__file__).resolve().parent / "bars_live"
# D1 at 5,000 bars reaches 2010 and returns in 1.6 s. The H1 equivalent hangs the
# terminal: a 99,999-bar H1 request blocked on I/O for ten minutes at 1 s of CPU,
# and 20,000 was no better. Daily is also the natural frequency for these three
# tests, and 16 years beats the 11.4 that H1 would have given.
CAP = 5000

SYMBOLS = [
    # FX majors and crosses
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
    "EURGBP", "EURJPY", "GBPJPY",
    # metals
    "XAUUSD", "XAGUSD", "XPTUSD",
    # equity indices
    "US Tech 100", "US SP 500", "US 30", "Germany 40", "UK 100",
    "Japan 225", "Hong Kong 50", "Netherlands 25", "France 40", "Australia 200",
    # crypto
    "BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "LTCUSD", "BCHUSD",
    # energy
    "USOUSD", "UKOUSD",
]
# H1 only. Requesting 99,999 D1 bars asks the server for 274 years of daily
# history; on EURUSD that hung the terminal indefinitely (process blocked on I/O
# at 1 s of CPU and no progress for ten minutes). H1 at 99,999 bars is 11.4 years,
# which is ample for drift, clustering and autocorrelation alike - and every test
# in live_structure.py is computed from H1 anyway.
TFS = [("D1", mt5.TIMEFRAME_D1)]


def main() -> None:
    # One symbol per invocation when --symbols is given. Some instruments block
    # the MT5 terminal indefinitely on their first history request (GBPJPY sat at
    # 1 s of CPU for minutes), and a blocking call inside the C extension cannot
    # be interrupted from Python. Running each symbol as its own short-lived
    # process lets an external timeout contain the hang instead of losing the
    # whole queue behind it.
    global SYMBOLS
    if len(sys.argv) > 1 and sys.argv[1].startswith("--symbols"):
        want = sys.argv[1].split("=", 1)[1] if "=" in sys.argv[1] else sys.argv[2]
        SYMBOLS = [x for x in want.split(",") if x]
    if not connect():
        raise SystemExit("no MT5")
    OUT.mkdir(exist_ok=True)
    print(f"{'symbol':20s}{'tf':>4s}{'bars':>9s}{'from':>13s}{'to':>13s}{'years':>7s}")
    missing = []
    for s in SYMBOLS:
        info = mt5.symbol_info(s)
        if info is None:
            missing.append(s)
            continue
        mt5.symbol_select(s, True)
        for name, tf in TFS:
            f = OUT / f"{s.replace(' ', '_')}_{name}.npz"
            if f.exists():
                z = np.load(f)
                print(f"{s:20s}{name:>4s}{len(z['t']):9,d}   (cached)", flush=True)
                continue
            r = None
            for _ in range(3):
                r = mt5.copy_rates_from_pos(s, tf, 0, CAP)
                if r is not None and len(r):
                    break
                time.sleep(2)
            if r is None or not len(r):
                print(f"{s:20s}{name:>4s}   FAILED {mt5.last_error()}", flush=True)
                continue
            a = datetime.fromtimestamp(r[0][0], tz=timezone.utc)
            b = datetime.fromtimestamp(r[-1][0], tz=timezone.utc)
            np.savez_compressed(
                f, t=r["time"].astype(np.int64), o=r["open"].astype(np.float64),
                h=r["high"].astype(np.float64), l=r["low"].astype(np.float64),
                c=r["close"].astype(np.float64), v=r["tick_volume"].astype(np.int64),
                spread=r["spread"].astype(np.int32),
                meta=np.array([s, name, str(info.point), str(info.digits)],
                              dtype=object))
            print(f"{s:20s}{name:>4s}{len(r):9,d}{a.strftime('%Y-%m-%d'):>13s}"
                  f"{b.strftime('%Y-%m-%d'):>13s}{(b - a).days / 365.25:7.1f}",
                  flush=True)
    if missing:
        print(f"\nnot on this server: {', '.join(missing)}")
    mt5.shutdown()


if __name__ == "__main__":
    main()
