"""
backend/kronos_service/features.py

Sampled forecast paths -> one compact, named feature row. The ONLY place this
arithmetic lives: precompute, the evaluation harness, the gate and the RL
observation all read rows produced here, so a live feature and a backtest
feature cannot be computed two different ways.
"""

from __future__ import annotations

import math

import numpy as np

# The numeric features, in a fixed order (an RL observation reads them in it).
FEATURES = ("exp_return_pct", "exp_return_atr", "p_up", "forecast_range_atr", "dispersion_atr")
META = ("symbol", "timeframe", "asof", "model_id", "latency_ms")

CLOSE, HIGH, LOW = 3, 1, 2


def compute(paths: np.ndarray, last_close: float, atr: float) -> dict:
    """`paths` is (S, pred_len, 6) for one series, columns o, h, l, c, v, a.

    exp_return_pct      mean forecast close at the horizon vs the last close, %
    exp_return_atr      the same move in ATRs (comparable across instruments)
    p_up                share of paths ending above the last close
    forecast_range_atr  mean over paths of (highest high - lowest low) / ATR
    dispersion_atr      std of the paths' horizon closes / ATR (uncertainty)
    """
    paths = np.asarray(paths, dtype=float)
    if paths.ndim != 3 or not np.isfinite(last_close) or last_close <= 0:
        raise ValueError("paths must be (S, H, 6) and last_close positive")
    a = float(atr) if (atr is not None and np.isfinite(atr) and atr > 0) else float("nan")
    end = paths[:, -1, CLOSE]
    mean_end = float(end.mean())
    rng = paths[:, :, HIGH].max(axis=1) - paths[:, :, LOW].min(axis=1)
    return {
        "exp_return_pct": (mean_end / last_close - 1.0) * 100.0,
        "exp_return_atr": (mean_end - last_close) / a,
        "p_up": float((end > last_close).mean()),
        "forecast_range_atr": float(rng.mean()) / a,
        "dispersion_atr": float(end.std()) / a,
    }


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int = 14) -> float:
    """Wilder ATR at the LAST bar, from the window alone - nothing after it."""
    high, low, close = (np.asarray(v, dtype=float) for v in (high, low, close))
    if len(close) < n + 1:
        return float("nan")
    prev = np.r_[close[0], close[:-1]]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev), np.abs(low - prev)))
    a = tr[:n].mean()
    for v in tr[n:]:
        a = (a * (n - 1) + v) / n
    return float(a)


def vector(row: dict | None) -> tuple[np.ndarray, bool]:
    """The RL observation form: the features in FEATURES order plus an
    availability flag. Missing or non-finite values are ZERO with the flag
    off - never a stale value carried forward."""
    if not row:
        return np.zeros(len(FEATURES)), False
    vals = [row.get(k) for k in FEATURES]
    if any(v is None or not math.isfinite(float(v)) for v in vals):
        return np.zeros(len(FEATURES)), False
    return np.array(vals, dtype=float), True
