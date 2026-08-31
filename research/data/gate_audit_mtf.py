"""Multi-timeframe gate audit — mirrors how the backtest route drives a strategy.

The first attempt fed every strategy a single M15 series. Five of six then
returned before reaching any instrumented gate, because they ask for two or
three timeframes (`get_required_timeframes()`) and were being starved of the
higher one. Only CRT, which needs just M15, produced usable telemetry.

This version asks each engine which timeframes it wants, builds a closed-bar
slice for each on every step of the lowest one, and calls `on_bar` per timeframe
exactly as `backtest.py` does. That makes the block counts real.

Primary question: HTFFVGFlip produced ~10 trades in 8 months. Which gate eats
the candidates?
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
TF_MIN = {"M5": 5, "M15": 15, "H1": 60, "H4": 240, "D1": 1440}
AVAILABLE = ("M5", "M15", "H1", "H4", "D1")


def load(sym, tf, n=None):
    f = CACHE / f"{sym.replace(' ', '_')}__{tf}.npz"
    if not f.exists():
        return None
    z = np.load(f)
    df = pd.DataFrame({k: z[k] for k in
                       ("open", "high", "low", "close", "volume")})
    df.index = pd.to_datetime(z["time"], unit="s", utc=True)
    if n:
        df = df.iloc[-n:]
    return df


async def audit(sid, sym, bars_primary=6000):
    cfg = UserConfigV2()
    cfg.instrument_settings = [InstrumentSettings(symbol=sym, strategy_id=sid)]
    e = get_strategy(sid)(cfg)
    e.is_backtesting = True
    e.gates.enabled = True

    tfs = [t for t in e.get_required_timeframes() if t in AVAILABLE]
    if not tfs:
        return None, f"required TFs unavailable: {e.get_required_timeframes()}"
    tfs = sorted(set(tfs), key=lambda t: TF_MIN[t])
    data = {t: load(sym, t) for t in tfs}
    if any(v is None or len(v) < 300 for v in data.values()):
        return None, f"missing cached data for {tfs}"

    primary = tfs[0]
    pdf = data[primary].iloc[-bars_primary:]
    prev_seen = {t: None for t in tfs}
    sigs = 0

    for i in range(300, len(pdf)):
        now = pdf.index[i]
        for t in tfs:
            d = data[t]
            # last fully closed bar of this timeframe at `now`
            cutoff = now - pd.Timedelta(minutes=TF_MIN[t])
            end = int(np.searchsorted(d.index.values, np.datetime64(cutoff), "right"))
            if end < 60:
                continue
            stamp = d.index[end - 1]
            if prev_seen[t] == stamp:
                continue          # this TF has not produced a new bar yet
            prev_seen[t] = stamp
            sl = d.iloc[max(0, end - 400):end]
            try:
                if await e.on_bar(sym, t, sl):
                    sigs += 1
            except Exception:
                pass
    e.gates.finish()
    return (sigs, e.gates.summary(), e.gates.strategy_rejections(), tfs), None


async def main():
    targets = [("HTFFVGFlip_v1", "XAUUSD"), ("HTFFVGFlip_v1", "US Tech 100"),
               ("APA_v1", "XAUUSD"), ("BiasIFVG_v1", "XAUUSD"),
               ("NYOpenRetest_v1", "US Tech 100"), ("VWAP_v1", "XAUUSD")]
    for sid, sym in targets:
        print(f"\n{'=' * 74}\n{sid}  x  {sym}")
        try:
            res, err = await audit(sid, sym)
        except Exception as ex:
            print(f"  FAILED {type(ex).__name__}: {str(ex)[:70]}")
            continue
        if err:
            print(f"  {err}")
            continue
        sigs, su, rej, tfs = res
        print(f"  timeframes driven: {tfs}")
        print(f"  signals: {sigs}   candidates recorded: {su['candidates_recorded']}")
        gates = su.get("gates", {})
        if not gates:
            print("  no instrumented gate was ever reached")
            continue
        print(f"  {'gate':32s}{'eval':>8s}{'pass':>8s}{'pass%':>8s}{'blocked':>9s}")
        for g, v in gates.items():
            ev, ps = v.get("evaluated", 0), v.get("passed", 0)
            bl = rej.get(g, 0)
            note = ""
            if ev and ps == ev:
                note = "  <- never blocks (dead)"
            elif ev and ps / ev < 0.05:
                note = "  <- THE bottleneck"
            print(f"  {g:32s}{ev:8d}{ps:8d}{(ps/ev if ev else 0):8.1%}{bl:9d}{note}")


if __name__ == "__main__":
    asyncio.run(main())
