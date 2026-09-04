"""End-to-end check: do the new strategies actually emit signals via on_bar()?

The lab (`synth_strategy_lab.py`) reimplements each entry rule vectorised, which
is fast but proves nothing about the shipped engine. This drives the REAL strategy
classes through the REAL `on_bar` interface with real M5 bars, and asserts:

  * a signal is produced at all
  * direction, stop and target are on the correct sides of entry
  * the stop distance matches the configured ATR multiple
  * the risk:reward matches the configured tp1_rr

If this passes, the strategy works in the backend and the same code path runs in
the app's backtester and live loop.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend.core.config_schema import UserConfigV2          # noqa: E402
from backend.strategies.registry import get_strategy          # noqa: E402
from synth_strategy_lab import bars                           # noqa: E402

CASES = [
    ("BoomDriftJump_v1", "BOOM1000", "Boom 1000 Index"),
    ("SpikeFade_v1", "BOOM1000", "Boom 1000 Index"),
    ("SpikeFade_v1", "CRASH1000", "Crash 1000 Index"),
    ("RangeRevert_v1", "R_75", "Volatility 75 Index"),
    ("RangeBreakout_v1", "RB100", "Range Break 100 Index"),
    ("RangeBreakout_v1", "stpRNG", "Step Index"),
    ("TrendDrift_v1", "CRASH1000", "Crash 1000 Index"),
    ("TrendDrift_v1", "R_75", "Volatility 75 Index"),
    ("TrendDrift_v1", "JD100", "Jump 100 Index"),
]


async def drive(strategy_id: str, code: str, mt5_name: str, n_bars: int = 4000):
    _, _, df, _ = bars(code)
    df = df.iloc[:n_bars]
    cfg = UserConfigV2()
    eng = get_strategy(strategy_id)(cfg)
    eng.is_backtesting = True

    signals = []
    for i in range(200, len(df)):
        window = df.iloc[max(0, i - 300):i + 1]
        sig = await eng.on_bar(mt5_name, "M5", window)
        if sig is not None:
            signals.append(sig)
    return eng, signals


def check(strategy_id, code, sigs) -> list[str]:
    errs = []
    if not sigs:
        return [f"{strategy_id}/{code}: NO SIGNALS produced"]
    for s in sigs[:200]:
        long = s.direction == "BUY"
        if long and not (s.stop_loss < s.entry_price < s.take_profit):
            errs.append(f"{strategy_id}/{code}: BUY levels out of order "
                        f"sl={s.stop_loss} e={s.entry_price} tp={s.take_profit}")
            break
        if not long and not (s.take_profit < s.entry_price < s.stop_loss):
            errs.append(f"{strategy_id}/{code}: SELL levels out of order "
                        f"sl={s.stop_loss} e={s.entry_price} tp={s.take_profit}")
            break
        risk = abs(s.entry_price - s.stop_loss)
        reward = abs(s.take_profit - s.entry_price)
        want_rr = s.metadata.get("tp1_rr")
        if want_rr and abs(reward / risk - want_rr) > 0.02:
            errs.append(f"{strategy_id}/{code}: RR mismatch got {reward/risk:.3f} "
                        f"want {want_rr}")
            break
        atr = s.metadata.get("atr_val")
        mult = s.metadata.get("stop_atr_multiple")
        if atr and mult and abs(risk / atr - mult) > 0.02:
            errs.append(f"{strategy_id}/{code}: stop distance {risk/atr:.3f}xATR "
                        f"want {mult}xATR")
            break
    return errs


async def main() -> None:
    print(f"{'strategy':20s}{'symbol':12s}{'signals':>9s}{'BUY':>6s}{'SELL':>6s}"
          f"  first signal (entry / sl / tp)")
    print("-" * 104)
    all_errs = []
    for sid, code, name in CASES:
        eng, sigs = await drive(sid, code, name)
        buys = sum(1 for s in sigs if s.direction == "BUY")
        sells = len(sigs) - buys
        first = ""
        if sigs:
            s = sigs[0]
            first = (f"{s.direction} {s.entry_price:,.4f} / {s.stop_loss:,.4f} "
                     f"/ {s.take_profit:,.4f}")
        print(f"{sid:20s}{code:12s}{len(sigs):9d}{buys:6d}{sells:6d}  {first}")
        all_errs += check(sid, code, sigs)

    print("\n" + ("VALIDATION FAILURES:" if all_errs else "All checks passed."))
    for e in all_errs:
        print("  " + e)


if __name__ == "__main__":
    asyncio.run(main())
