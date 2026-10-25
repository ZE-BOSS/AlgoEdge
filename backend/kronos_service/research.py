"""
backend/kronos_service/research.py

The measurement behind research/kronos/FINDINGS-*.md. Offline; reads MT5
through the app's own loader, never trades.

    python -m backend.kronos_service.research skill  --symbols AAPL,NVDA --tf H1
    python -m backend.kronos_service.research gate   --symbols XAUUSD --tf M5 \
           --strategies ORB_v1,TrendBreakout_v1

Every feature row is cached in data/kronos/ keyed by model_id, so re-runs and
the gate study reuse forecasts instead of paying for them again.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from backend.kronos_service import evaluate as ev
from backend.kronos_service import gate as kg
from backend.kronos_service import precompute as kp
from backend.kronos_service.config import KronosConfig

OUT = Path("research/kronos/results")


def load_bars(symbol: str, tf: str, start: str, end: str) -> pd.DataFrame:
    from backend.rl import datasets as ds
    from backend.rl.spec import AgentSpec

    spec = AgentSpec(name="kronos", symbols=(symbol,), timeframe=tf)
    frames = ds.load_frames({symbol: {"source": "mt5", "start": start, "end": end}}, spec)
    f = frames.get(symbol)
    if f is None or not len(f):
        raise ValueError(f"no {tf} bars for {symbol}")
    f = f.reset_index(drop=True) if "time" in f.columns else f.reset_index()
    if "time" not in f.columns:
        f["time"] = pd.DatetimeIndex(f.iloc[:, 0]).as_unit("s").asi8
    return f


def spread_bps(symbol: str, frame: pd.DataFrame) -> float:
    """The measured bar spread (MT5 'spread', in points) in basis points."""
    try:
        import MetaTrader5 as mt5
        mt5.initialize()
        point = float(mt5.symbol_info(symbol).point)
    except Exception:                                   # noqa: BLE001
        point = float("nan")
    if "spread" in frame.columns and np.isfinite(point):
        return float(np.nanmean(frame["spread"].to_numpy(dtype=float) * point
                                  / frame["close"].to_numpy(dtype=float)) * 1e4)
    return float("nan")


def _engine(cfg):
    from backend.kronos_service.predictor import KronosEngine
    return KronosEngine(cfg)


def cmd_skill(a, cfg, eng) -> dict:
    res = {}
    for sym in a.symbols.split(","):
        t0 = time.time()
        f = load_bars(sym, a.tf, a.start, a.end)
        n = len(f)
        # Non-overlapping horizons, newest last, capped at --points.
        rows = list(range(cfg.lookback - 1, n - cfg.pred_len - 1, max(1, a.stride or cfg.pred_len)))
        rows = rows[-a.points:]
        store = kp.extend(eng, f, rows, sym, a.tf, batch=a.batch)
        t = f["time"].to_numpy(dtype="int64")
        by_row = {i: store[int(t[i])] for i in rows if int(t[i]) in store}
        sk = ev.forecast_skill(f, by_row, cfg.pred_len, spread_bps(sym, f))
        sk.update({"bars": n, "first": str(pd.Timestamp(int(t[rows[0]]), unit="s")),
                   "last": str(pd.Timestamp(int(t[rows[-1]]), unit="s")),
                   "seconds": round(time.time() - t0)})
        res[sym] = sk
        print(json.dumps({sym: sk}), flush=True)
    return res


def cmd_gate(a, cfg, eng) -> dict:
    from backend.rl import base_signals as bs
    from backend.rl import env as E
    from backend.rl import train as T
    from backend.rl.spec import AgentSpec

    res = {}
    for sym in a.symbols.split(","):
        f = load_bars(sym, a.tf, a.start, a.end)
        frame_idx = f.set_index(pd.to_datetime(f["time"], unit="s"))
        t = f["time"].to_numpy(dtype="int64")
        pos = {int(x): k for k, x in enumerate(t)}
        spec = AgentSpec(name="kronos", symbols=(sym,), timeframe=a.tf)
        for sid in a.strategies.split(","):
            sigs = bs.generate(sym, a.tf, bs.index_frames({a.tf: frame_idx}), sid,
                               T.strategy_config(spec, sym, {}, {}, sid))
            sigs = [s for s in sigs if int(s["time"]) in pos]
            rows = [pos[int(s["time"])] for s in sigs]
            store = kp.extend(eng, f, rows, sym, a.tf, batch=a.batch)
            rc = E.default_risk_config(sym, strategy_id=sid)
            out = {}
            for thr in (0.6, 0.8):
                g = kg.GateConfig(enabled=True, min_agreement=thr, fail_mode="open")
                out[f"agree>={thr}"] = ev.gate_study(frame_idx, sigs, store, sym, rc, g)
            res[f"{sym}/{sid}"] = out
            print(json.dumps({f"{sym}/{sid}": out}), flush=True)
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("skill", "gate"))
    ap.add_argument("--symbols", required=True)
    ap.add_argument("--tf", default="H1")
    ap.add_argument("--start", default="2024-07-01")   # after Kronos' training cutoff
    ap.add_argument("--end", default="2026-10-16")
    ap.add_argument("--model", default="Kronos-mini")
    ap.add_argument("--lookback", type=int, default=512)
    ap.add_argument("--pred-len", type=int, default=12)
    ap.add_argument("--samples", type=int, default=5)
    ap.add_argument("--stride", type=int, default=0)
    ap.add_argument("--points", type=int, default=400)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--strategies", default="ORB_v1,TrendBreakout_v1")
    ap.add_argument("--tag", default="")
    a = ap.parse_args()
    cfg = KronosConfig(model=a.model, lookback=a.lookback, pred_len=a.pred_len,
                       sample_count=a.samples, threads=a.threads)
    eng = _engine(cfg)
    res = (cmd_skill if a.cmd == "skill" else cmd_gate)(a, cfg, eng)
    OUT.mkdir(parents=True, exist_ok=True)
    name = f"{a.cmd}_{a.tf}_{a.model}_{a.tag or a.symbols.replace(',', '-')[:40]}.json"
    (OUT / name).write_text(json.dumps({"config": cfg.__dict__, "model_id": cfg.model_id,
                                        "args": vars(a), "results": res}, indent=1, default=str))
    print(f"wrote {OUT / name}", flush=True)


if __name__ == "__main__":
    main()
