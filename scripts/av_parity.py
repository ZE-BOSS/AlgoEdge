"""
scripts/av_parity.py - do the AV_ presets take the research's trades on the research's own data?

Feeds the strategy engine (through BarFeed, the backtest path) the futures minutes of
research_data/<market>_m1 + _of, built into bars exactly as marketdata/source.py builds them,
and compares the signal bars with the trades the research builder (of_zones.py / pa_study.py)
takes for the same preset. Prints matched / research-only / engine-only per preset.

    cd <app> && PYTHONPATH=. python scripts/av_parity.py --data /opt/algoedge/research_data
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PRESETS = {  # strategy -> research market (futures)
    "AV_GoldenEngulfFlip_v1": "gold", "AV_GoldenTwinAbsorb_v1": "gold", "AV_NasdaqRejectionFlip_v1": "nasdaq",
    "AV_SPFreshDoji_v1": "sp500", "AV_SPTwinRejection_v1": "sp500", "AV_SilverAbsorbFlip_v1": "silver",
    "AV_DowBigTradePin_v1": "us30", "AV_DowAbsorptionZone_v1": "us30",
    "AV_NasdaqPrevDayDoji_v1": "nasdaq", "AV_DowDojiRetest_v1": "us30", "AV_OilTrendEngulf_v1": "oil",
    "AV_OilRetestDoji_v1": "oil", "AV_CableLiquiditySweep_v1": "gbpusd",
}


def minutes(data: Path, market: str) -> pd.DataFrame:
    m1 = pd.read_parquet(data / f"{market}_m1.parquet").drop_duplicates("time")
    of = pd.read_parquet(data / f"{market}_of.parquet").drop_duplicates("time")
    m1.index = pd.to_datetime(m1["time"], unit="s")
    of.index = pd.to_datetime(of["time"], unit="s")
    m = m1[["open", "high", "low", "close"]].astype(float)
    vol = of["vol"] if "vol" in of.columns else of["buy"] + of["sell"]
    m["volume"] = vol.reindex(m.index).fillna(0.0)
    for k in ("buy", "sell", "big_buy", "big_sell"):
        m[k] = of[k].reindex(m.index).fillna(0.0)
    pv = of["pv"] if "pv" in of.columns else of["vwap"] * vol
    m["pv"] = pv.reindex(m.index).fillna(0.0)
    return m[m.index >= pd.Timestamp("2025-12-20")]


def engine_signals(sid: str, m: pd.DataFrame) -> tuple[list, object]:
    from backend.core.config_schema import UserConfigV2
    from backend.marketdata.source import attach_nodes, resample
    from backend.strategies.bar_feed import BarFeed
    from backend.strategies.registry import get_strategy
    eng = get_strategy(sid)(UserConfigV2())
    eng.is_backtesting = True
    tf = eng.params.timeframe
    f = attach_nodes(resample(m, tf), m)
    eng.prepare_backtest("X", {tf: f})
    feed = BarFeed(eng, "X")
    feed.bind({tf: f})
    out = []

    async def go():
        for i in range(len(f)):
            s = await feed.step(i)
            if s is not None:
                out.append((f.index[i - 1], 1 if s.direction == "BUY" else -1))
    asyncio.run(go())
    return out, eng.params


def research_trades(sid: str, p, data: Path, market: str) -> set:
    import of_zones as Z
    import pa_study as PA
    tf = p.timeframe
    T = None
    if sid in ("AV_NasdaqPrevDayDoji_v1", "AV_DowDojiRetest_v1", "AV_OilTrendEngulf_v1", "AV_OilRetestDoji_v1",
               "AV_CableLiquiditySweep_v1"):
        base = Z.load(data, market, "2025-12-20", "2026-11-27")
        c = PA.build(market, base, tf, 0.0, "2026-01-01")
        T = pd.Series(True, index=c.index)
        X = {"none": T, "prev_day_level": c.pd_level, "retest": c.setup == "retest", "reversal": c.setup == "reversal",
             "with_trend": c.trend > 0, "against_trend": c.trend < 0, "strong_level": c.strong}
    else:
        d = Z.load(data, market, "2025-12-20", "2026-11-27")
        c = Z.build(market, d, tf, float(p.impulse_atr), float(p.zone_max_age_days), "2026-01-01")
        T = pd.Series(True, index=c.index)
        X = {"none": T, "hvn_zone": c.kind == "hvn", "origin_zone": c.kind == "origin", "fresh_zone": c.touch <= 1,
             "strong_zone": c.strength.fillna(0) >= 1.5, "big_trades_in_zone": c.zone_bigs.fillna(0) >= 1.5,
             "big_move_in": c.big_move >= 3.0}
    C = {"doji": c.doji, "engulf": c.engulf, "pin": c.pin, "rejection": c.pin | c.engulf, "double": c.double,
         "rejection_double": (c.pin | c.engulf) & c.double, "any": T, "sweep": c.get("sweep", T)}
    F = {"none": T, "big_trades_with": c.get("big_with", T), "delta_flip": c.get("flip", T),
         "absorption": c.get("absorb", T), "absorb_or_flip": c.get("absorb", T) | c.get("flip", T),
         "absorb_and_flip": c.get("absorb", T) & c.get("flip", T)}
    S = {"all_day": T, "london_ny": (c.hour >= 7) & (c.hour < 20), "ny_only": (c.hour >= 13.5) & (c.hour < 20)}
    mask = (C[p.candle] & F[p.flow] & X[p.context] & S[p.session]).values
    tk = Z.take(c, mask, p.target)
    return set(zip(pd.to_datetime(tk["t"]), tk["side"].astype(int)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="/opt/algoedge/research_data")
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    data = Path(a.data)
    sys.path.insert(0, str(data))
    sys.path.insert(0, str(ROOT / "scripts"))
    for sid, market in PRESETS.items():
        if a.only and a.only not in sid:
            continue
        try:
            m = minutes(data, market)
            sig, p = engine_signals(sid, m)
            es = set(sig)
            rs = research_trades(sid, p, data, market)
            warm = pd.Timestamp("2026-01-12")
            ro = sorted(x for x in rs - es if x[0] >= warm)
            eo = sorted(x for x in es - rs if x[0] >= warm)
            print(f"{sid:28s} {market:7s} engine {len(es):4d} research {len(rs):4d} both {len(es & rs):4d} "
                  f"research-only {len(ro):3d} engine-only {len(eo):3d} (after the warm-up) "
                  f"e.g. R {[str(x[0]) for x in ro[:2]]} E {[str(x[0]) for x in eo[:2]]}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"{sid:28s} {market:7s} FAILED {type(e).__name__}: {e}", flush=True)
    print("AV_PARITY_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
