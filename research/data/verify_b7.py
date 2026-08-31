"""B7 verification: does the gate recorder collect once enabled?

Drives real strategy engines over real MT5 bars and reads the telemetry the
backtest route now switches on. Kept to two strategies and a short slice so it
finishes quickly; the point is whether the counters move at all.
"""
import sys, asyncio
from pathlib import Path
import numpy as np, pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from backend.strategies.registry import get_strategy
from backend.core.config_schema import UserConfigV2, InstrumentSettings

N = 1500
z = np.load(REPO / "research/data/cache/XAUUSD__M15.npz")
df = pd.DataFrame({k: z[k][-N:] for k in
                   ["time", "open", "high", "low", "close", "volume"]})
df.index = pd.to_datetime(df["time"], unit="s", utc=True)


async def drive(sid):
    cfg = UserConfigV2()
    cfg.instrument_settings = [InstrumentSettings(symbol="XAUUSD", strategy_id=sid)]
    e = get_strategy(sid)(cfg)
    e.is_backtesting = True
    e.gates.enabled = True                     # what the route now does (B7)
    sigs = 0
    for i in range(300, N):
        try:
            if await e.on_bar("XAUUSD", "M15", df.iloc[:i + 1]):
                sigs += 1
        except Exception:
            pass
    e.gates.finish()
    return sigs, e.gates.summary(), e.gates.strategy_rejections()


async def main():
    for sid in ("NYOpenRetest_v1", "CRT_v1"):
        sigs, su, rej = await drive(sid)
        print(f"\n=== {sid}  ({N - 300} bars driven, {sigs} signals)")
        print(f"  candidates_recorded : {su['candidates_recorded']}")
        print(f"  candidates_blocked  : {su['candidates_blocked']}")
        print(f"  distinct gates seen : {len(su['gates'])}")
        for g, v in list(su["gates"].items())[:6]:
            print(f"    {g:26s} evaluated={v.get('evaluated'):5d} passed={v.get('passed'):5d}")
        print(f"  strategy_rejections : {dict(list(rej.items())[:6]) or '{}'}")
        print(f"  => {'PASS' if su['gates'] else 'FAIL - nothing recorded'}")

asyncio.run(main())
