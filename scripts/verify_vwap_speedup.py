"""
scripts/verify_vwap_speedup.py

Prove the VWAP hot-path rewrite changed the SPEED and not the NUMBERS.

`_calculate_anchored_vwap_with_bands` was 76% of VWAP's per-bar cost. It was
rewritten to stay in numpy and to stop paying pandas' fixed ~25 ms Series /
DatetimeIndex construction cost on every call. That is only a legitimate change
if the output is bit-for-bit what it was, so this compares it against an
independent reference written the slow, obvious way: full per-element timezone
conversion, pandas groupby per session, cumulative sums inside the group.

    venv_win/Scripts/python.exe scripts/verify_vwap_speedup.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytz  # noqa: E402

import MetaTrader5 as mt5  # noqa: E402

from backend.mt5.data_fetcher import DataFetcher  # noqa: E402
from backend.strategies.strategy_vwap.engine import (  # noqa: E402
    _calculate_anchored_vwap_with_bands,
)

ET = pytz.timezone("America/New_York")


def reference(candles: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """
    The slow, obvious implementation. Deliberately shares no code with the one
    under test — a reference that reuses the optimised helpers would validate
    nothing.
    """
    tp = (candles["high"] + candles["low"] + candles["close"]) / 3.0
    if "volume" in candles.columns and candles["volume"].sum() > 0:
        vol = candles["volume"].astype(float)
    else:
        vol = pd.Series(1.0, index=candles.index)

    et = pd.to_datetime(candles["time"], unit="s", utc=True).dt.tz_convert(ET)
    session = (et - pd.Timedelta(hours=9, minutes=30)).dt.normalize()

    vwap = pd.Series(index=candles.index, dtype=float)
    std = pd.Series(index=candles.index, dtype=float)
    for _d, idx in candles.groupby(session.to_numpy()).groups.items():
        g_tp, g_vol = tp.loc[idx], vol.loc[idx]
        cum_tpv = (g_tp * g_vol).cumsum()
        cum_v = g_vol.cumsum()
        running = cum_tpv / cum_v
        vwap.loc[idx] = running
        sq = g_vol * (g_tp - running) ** 2
        var = (sq.cumsum() / cum_v).clip(lower=0.0)
        std.loc[idx] = var.pow(0.5).fillna(0.0)

    return (
        vwap.ffill().bfill().to_numpy(dtype=float),
        std.fillna(0.0).to_numpy(dtype=float),
    )


async def main(symbol: str = "XAUUSD", count: int = 3000) -> int:
    mt5.initialize()
    df = await DataFetcher.get_historical_data(symbol, "M5", count=count)
    if df is None or df.empty:
        print("no data — is MT5 connected?")
        return 2
    df = df.set_index(pd.to_datetime(df["time"], unit="s")).sort_index()

    # Walk real 300-bar windows, exactly as on_bar sees them.
    windows = [df.iloc[i - 300:i] for i in range(300, len(df), 23)]
    print(f"{symbol}: {len(df)} bars -> {len(windows)} windows of 300", flush=True)

    worst_v = worst_s = 0.0
    for w in windows:
        gv, gs = _calculate_anchored_vwap_with_bands(w, 15, 0)
        rv, rs = reference(w)
        assert isinstance(gv, np.ndarray), "must return ndarray, not Series"
        assert gv.shape == rv.shape == (len(w),), f"shape {gv.shape} vs {rv.shape}"
        worst_v = max(worst_v, float(np.nanmax(np.abs(gv - rv))))
        worst_s = max(worst_s, float(np.nanmax(np.abs(gs - rs))))

    print(f"max |vwap difference| : {worst_v:.3e}")
    print(f"max |std  difference| : {worst_s:.3e}")

    # 1e-9 on gold prices near 4,500 is ~2e-13 relative: floating-point
    # association order, not a behaviour change.
    ok = worst_v < 1e-9 and worst_s < 1e-9
    print("EQUIVALENT" if ok else "*** DIVERGED — do not ship ***")

    n = 60
    t0 = time.perf_counter()
    for w in windows[:n]:
        _calculate_anchored_vwap_with_bands(w, 15, 0)
    fast = (time.perf_counter() - t0) / min(n, len(windows)) * 1000

    t0 = time.perf_counter()
    for w in windows[:n]:
        reference(w)
    slow = (time.perf_counter() - t0) / min(n, len(windows)) * 1000

    print(f"\nnew        : {fast:6.2f} ms/call")
    print(f"reference  : {slow:6.2f} ms/call   ({slow / fast:.1f}x slower)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
