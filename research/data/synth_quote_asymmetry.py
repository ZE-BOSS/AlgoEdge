"""Where does the true index value sit inside the broker's bid/ask?

Deriv's WS API serves the index's own value; MT5 quotes the tradeable bid/ask
around it. On Volatility 75 the API price sits at exactly half the spread above
the bid - a symmetric quote. On Boom 1000 it sits 5% of the way up, meaning the
ask is ~19x further from fair value than the bid is.

That asymmetry decides which DIRECTION is affordable on each instrument, and it is
invisible to any backtest built on MT5 bars alone - MT5 bars are bid, so a long
that really enters at ask is charged nothing for the crossing. This is a candidate
explanation for the report-18 expectancy gap (research/19 section 1).

`frac` below is (api_mid - bid) / (ask - bid):
  0.50 -> symmetric, both directions cost the same
  ~0.0 -> bid is fair, the ASK is marked up: longs pay, shorts are cheap
  ~1.0 -> ask is fair, the BID is marked down: shorts pay, longs are cheap
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import numpy as np
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent))
import MetaTrader5 as mt5                                    # noqa: E402
from mt5_probe import connect                                # noqa: E402

URL = "wss://ws.derivws.com/websockets/v3?app_id=1089"
ROUNDS = 25

PAIRS = [
    ("BOOM1000",  "Boom 1000 Index"),
    ("BOOM500",   "Boom 500 Index"),
    ("CRASH1000", "Crash 1000 Index"),
    ("CRASH500",  "Crash 500 Index"),
    ("stpRNG",    "Step Index"),
    ("RB100",     "Range Break 100 Index"),
    ("RB200",     "Range Break 200 Index"),
    ("JD25",      "Jump 25 Index"),
    ("JD100",     "Jump 100 Index"),
    ("R_75",      "Volatility 75 Index"),
    ("R_25",      "Volatility 25 Index"),
    ("R_100",     "Volatility 100 Index"),
]


async def main() -> None:
    if not connect():
        raise SystemExit("no MT5")
    for _, name in PAIRS:
        mt5.symbol_select(name, True)

    samples: dict[str, list[float]] = {c: [] for c, _ in PAIRS}
    spreads: dict[str, list[float]] = {c: [] for c, _ in PAIRS}

    async with websockets.connect(URL, ping_interval=20) as ws:
        for _ in range(ROUNDS):
            for code, name in PAIRS:
                await ws.send(json.dumps({"ticks_history": code, "end": "latest",
                                          "count": 1, "style": "ticks"}))
                r = json.loads(await ws.recv())
                if "error" in r:
                    continue
                pr = r.get("history", {}).get("prices", [])
                t = mt5.symbol_info_tick(name)
                if not pr or t is None or t.ask <= t.bid:
                    continue
                mid = pr[-1]
                samples[code].append((mid - t.bid) / (t.ask - t.bid))
                spreads[code].append(t.ask - t.bid)
            await asyncio.sleep(0.4)

    print(f"{'symbol':24s}{'n':>4s}{'frac':>8s}{'sd':>7s}{'spread':>11s}"
          f"{'long pays':>11s}{'short pays':>11s}  reading")
    for code, name in PAIRS:
        v = np.asarray(samples[code], dtype=float)
        s = np.asarray(spreads[code], dtype=float)
        if len(v) < 3:
            print(f"{name:24s}  no data"); continue
        f = float(np.median(v))
        sp = float(np.median(s))
        long_cost, short_cost = (1 - f) * sp, f * sp
        if f < 0.25:
            reading = "ASK marked up - longs penalised"
        elif f > 0.75:
            reading = "BID marked down - shorts penalised"
        else:
            reading = "symmetric"
        print(f"{name:24s}{len(v):4d}{f:8.3f}{v.std():7.3f}{sp:11.4f}"
              f"{long_cost:11.4f}{short_cost:11.4f}  {reading}")
    mt5.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
