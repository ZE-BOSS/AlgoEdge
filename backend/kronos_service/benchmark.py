"""
backend/kronos_service/benchmark.py

    python -m backend.kronos_service.benchmark [--threads 1] [--repeats 5]

Latency (p50/p95) and peak memory for each model x batch size on THIS machine,
and the per-bar budget that follows: the prompt's rule is that inference for
all enabled symbols must finish inside 25% of the bar. Run it on the VPS
before enabling anything - numbers from a workstation do not transfer to a
burstable 2-vCPU instance.
"""

from __future__ import annotations

import argparse
import json
import os
import time

import numpy as np
import pandas as pd


def _rss_mb() -> float:
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss / 2**20
    except Exception:                                   # noqa: BLE001
        return float("nan")


def _frame(n: int) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    c = 2000 + np.cumsum(rng.normal(0, 1.0, n))
    t = 1_735_689_600 + 300 * np.arange(n)
    return pd.DataFrame({"time": t, "open": c, "high": c + 1, "low": c - 1, "close": c,
                         "volume": rng.integers(10, 999, n).astype(float)})


def run(models=("Kronos-mini", "Kronos-small"), batches=(1, 4), samples=(1, 5),
        pred_len=12, threads=1, repeats=5) -> list[dict]:
    from backend.kronos_service import precompute as kp
    from backend.kronos_service.config import KronosConfig
    from backend.kronos_service.predictor import KronosEngine

    out = []
    for m in models:
        lookback = 400 if m == "Kronos-small" else 1000
        for S in samples:
            cfg = KronosConfig(model=m, lookback=lookback, pred_len=pred_len,
                               sample_count=S, threads=threads)
            eng = KronosEngine(cfg)
            f = _frame(lookback + 64)
            for B in batches:
                wins = [kp.window_at(f, lookback - 1 + k, cfg, f"S{k}", "M5") for k in range(B)]
                eng.paths(wins)                                  # warm-up
                lat = []
                for _ in range(repeats):
                    t = time.perf_counter()
                    eng.paths(wins)
                    lat.append(time.perf_counter() - t)
                row = {"model": m, "lookback": lookback, "pred_len": pred_len, "samples": S,
                       "batch": B, "threads": threads, "p50_s": round(float(np.median(lat)), 3),
                       "p95_s": round(float(np.percentile(lat, 95)), 3),
                       "per_symbol_s": round(float(np.median(lat)) / B, 3),
                       "rss_mb": round(_rss_mb(), 0)}
                # Symbols that fit in 25% of an M5 / M15 bar at this setting.
                row["symbols_in_25pct_M5"] = int(75 // max(1e-9, row["per_symbol_s"]))
                row["symbols_in_25pct_M15"] = int(225 // max(1e-9, row["per_symbol_s"]))
                out.append(row)
                print(json.dumps(row), flush=True)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--threads", type=int, default=1)
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--models", default="Kronos-mini,Kronos-small")
    a = ap.parse_args()
    run(models=tuple(a.models.split(",")), threads=a.threads, repeats=a.repeats)
