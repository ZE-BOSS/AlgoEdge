"""
backend/kronos_service/precompute.py

Walk-forward Kronos features over history, for the backtester and the
evaluation harness.

THE NO-LOOKAHEAD CONTRACT
-------------------------
The forecast "as of" row i uses bars i-lookback+1 .. i and nothing else - the
window is sliced, its ATR comes from the window, and the future bar times are
calendar arithmetic, not read from the frame. Row i is the DECISION bar: the
last bar that had closed when a strategy decided (`base_signals` keys setups
the same way), so a feature row and a setup on the same bar line up exactly.
`tests/test_kronos_service.py` proves it by changing every bar after i and
checking the features at i do not move.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pandas as pd

from backend.kronos_service import features as kf
from backend.kronos_service.config import KronosConfig
from backend.kronos_service.predictor import Window, future_times

TF_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}


def _epochs(frame: pd.DataFrame) -> np.ndarray:
    if "time" in frame.columns:
        return frame["time"].to_numpy(dtype="int64")
    return pd.DatetimeIndex(frame.index).as_unit("s").asi8


def window_at(frame: pd.DataFrame, i: int, cfg: KronosConfig, symbol: str,
              timeframe: str) -> Window | None:
    """The window ending at (and including) row i, or None without enough history."""
    L = cfg.lookback
    if i + 1 < L:
        return None
    win = frame.iloc[i + 1 - L: i + 1]
    t = _epochs(win)
    last = pd.Timestamp(int(t[-1]), unit="s")
    return Window(symbol=symbol, frame=win, asof=int(t[-1]),
                  future_times=future_times(last, TF_MINUTES[timeframe], cfg.pred_len))


def features_at(engine, frame: pd.DataFrame, rows: list[int], symbol: str,
                timeframe: str, *, batch: int = 4, on_progress=None) -> pd.DataFrame:
    """Feature rows for each decision row in `rows` (rows without enough
    history are skipped). `engine` is a `KronosEngine`."""
    cfg = engine.cfg
    out = []
    todo = [i for i in sorted(set(int(r) for r in rows)) if i + 1 >= cfg.lookback]
    for k in range(0, len(todo), batch):
        chunk = todo[k:k + batch]
        wins = [window_at(frame, i, cfg, symbol, timeframe) for i in chunk]
        paths, secs = engine.paths(wins)
        for w, i, p in zip(wins, chunk, paths):
            f = w.frame
            a = kf.atr(f["high"].to_numpy(), f["low"].to_numpy(), f["close"].to_numpy())
            row = kf.compute(p, float(f["close"].iloc[-1]), a)
            row.update({"symbol": symbol, "timeframe": timeframe, "asof": w.asof, "atr": a,
                        "row": i, "model_id": cfg.model_id,
                        "latency_ms": round(secs * 1000 / len(chunk), 1)})
            out.append(row)
        if on_progress is not None:
            on_progress(min(k + batch, len(todo)), len(todo))
    return pd.DataFrame(out)


def store_path(symbol: str, timeframe: str, cfg: KronosConfig,
               root: str = "data/kronos") -> Path:
    return Path(root) / f"{symbol}_{timeframe}_{cfg.model_id.replace('@', '_')}.csv"


def load(symbol: str, timeframe: str, cfg: KronosConfig,
         root: str = "data/kronos") -> dict[int, dict]:
    """{asof epoch: feature row} - what the backtest gate looks rows up in."""
    p = store_path(symbol, timeframe, cfg, root)
    if not p.exists():
        return {}
    df = pd.read_csv(p)
    return {int(r["asof"]): r for r in df.to_dict("records")}


def extend(engine, frame: pd.DataFrame, rows: list[int], symbol: str, timeframe: str,
           root: str = "data/kronos", **kw) -> dict[int, dict]:
    """Compute only the rows not already stored, append, return the whole store."""
    have = load(symbol, timeframe, engine.cfg, root)
    t = _epochs(frame)
    need = [i for i in rows if int(t[i]) not in have]
    if need:
        t0 = time.time()
        new = features_at(engine, frame, need, symbol, timeframe, **kw)
        p = store_path(symbol, timeframe, engine.cfg, root)
        p.parent.mkdir(parents=True, exist_ok=True)
        new.to_csv(p, mode="a", header=not p.exists(), index=False)
        have.update({int(r["asof"]): r for r in new.to_dict("records")})
        print(f"[kronos] {symbol} {timeframe}: +{len(new)} rows in {time.time() - t0:.0f}s",
              flush=True)
    return have
