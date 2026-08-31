"""Per-strategy gate audit: what is blocking, and which gates are dead?

Runs every strategy over real MT5 bars with the gate recorder enabled (the B7
fix) and reports, for each gate:

  evaluated  - how many candidates reached this gate
  pass rate  - how many it let through
  blocked    - how many it was the FIRST gate to stop

Answers two questions directly:
  1. Why does HTFFVGFlip take ~10 trades in 8 months? Which gate eats them?
  2. Which gates never block anything (dead config) and which block nearly
     everything (the real constraint)?
"""
from __future__ import annotations
import asyncio, sys
from pathlib import Path
import numpy as np, pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from backend.strategies.registry import get_strategy
from backend.core.config_schema import UserConfigV2, InstrumentSettings

CACHE = REPO / "research/data/cache"

STRATS = ["HTFFVGFlip_v1", "APA_v1", "BiasIFVG_v1", "CRT_v1",
          "NYOpenRetest_v1", "VWAP_v1"]
SYMS = ["XAUUSD", "US Tech 100", "EURUSD"]
TF = "M15"
NBARS = 12000


def load(sym, tf=TF, n=NBARS):
    z = np.load(CACHE / f"{sym.replace(' ', '_')}__{tf}.npz")
    df = pd.DataFrame({k: z[k][-n:] for k in
                       ("open", "high", "low", "close", "volume")})
    df.index = pd.to_datetime(z["time"][-n:], unit="s", utc=True)
    return df


async def audit(sid, sym):
    df = load(sym)
    cfg = UserConfigV2()
    cfg.instrument_settings = [InstrumentSettings(symbol=sym, strategy_id=sid)]
    e = get_strategy(sid)(cfg)
    e.is_backtesting = True
    e.gates.enabled = True
    sigs = 0
    for i in range(400, len(df)):
        try:
            if await e.on_bar(sym, TF, df.iloc[max(0, i - 400):i + 1]):
                sigs += 1
        except Exception:
            pass
    e.gates.finish()
    return sigs, e.gates.summary(), e.gates.strategy_rejections()


async def main():
    for sid in STRATS:
        print(f"\n{'=' * 72}\n{sid}")
        for sym in SYMS:
            try:
                sigs, su, rej = await audit(sid, sym)
            except Exception as ex:
                print(f"  {sym}: FAILED {type(ex).__name__}: {str(ex)[:60]}")
                continue
            gates = su.get("gates", {})
            print(f"  --- {sym}: {sigs} signals from {NBARS - 400} bars, "
                  f"{su['candidates_recorded']} candidates")
            if not gates:
                print("      (no gate reached — strategy returns before its "
                      "first instrumented gate)")
                continue
            print(f"      {'gate':30s}{'eval':>8s}{'pass':>8s}{'pass%':>8s}{'blocked':>9s}")
            for g, v in gates.items():
                ev = v.get("evaluated", 0)
                ps = v.get("passed", 0)
                bl = rej.get(g, 0)
                flag = ""
                if ev and ps == ev:
                    flag = "  <- never blocks (dead)"
                elif ev and ps / ev < 0.02:
                    flag = "  <- blocks ~everything"
                print(f"      {g:30s}{ev:8d}{ps:8d}"
                      f"{(ps / ev if ev else 0):8.1%}{bl:9d}{flag}")


if __name__ == "__main__":
    asyncio.run(main())
