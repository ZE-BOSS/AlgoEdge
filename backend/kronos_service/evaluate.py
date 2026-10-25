"""
backend/kronos_service/evaluate.py

Does Kronos know anything about THIS market, net of what it costs to trade?
Two questions, answered out of sample (every bar used here is after Kronos'
June-2024 pre-training cutoff when the data allows):

1. FORECAST SKILL. At sampled decision bars, take the direction Kronos
   forecasts over its horizon and trade it close-to-close, paying the measured
   spread. Compared with two baselines that need no model:
     momentum  the sign of the last `horizon` bars' move
     long      always long (the drift of the sample)
   plus the rank IC of the expected return, a calibration table for p_up, and
   the correlation of forecast range with realised range.

2. GATE VALUE. An existing strategy's setups through the app's own
   BacktestEngine three ways: all of them, only those the Kronos gate allows,
   and - the control - only those it REJECTS. A gate that selects nothing
   leaves the two halves alike; a gate worth having makes the rejected half
   clearly worse.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from backend.kronos_service import gate as kg


def _t(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=float)
    if len(x) < 3 or x.std(ddof=1) == 0:
        return float("nan")
    return float(x.mean() / (x.std(ddof=1) / math.sqrt(len(x))))


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 5:
        return float("nan")
    ra = pd.Series(a).rank().to_numpy()
    rb = pd.Series(b).rank().to_numpy()
    return float(np.corrcoef(ra, rb)[0, 1])


def forecast_skill(frame: pd.DataFrame, rows: dict[int, dict], horizon: int,
                   spread_bps: float) -> dict:
    """`rows` maps a decision row index to its feature row."""
    close = frame["close"].to_numpy(dtype=float)
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    recs = []
    for i, f in sorted(rows.items()):
        if i + horizon >= len(close) or i - horizon < 0:
            continue
        real = close[i + horizon] / close[i] - 1.0
        recs.append({
            "real_bps": real * 1e4,
            "exp": float(f["exp_return_pct"]),
            "p_up": float(f["p_up"]),
            "mom": np.sign(close[i] - close[i - horizon]),
            "range_atr_fc": float(f["forecast_range_atr"]),
            "range_real_atr": (high[i + 1:i + horizon + 1].max()
                               - low[i + 1:i + horizon + 1].min()) / float(f.get("atr") or np.nan),
        })
    if not recs:
        return {"n": 0}
    d = pd.DataFrame(recs)
    out = {"n": len(d), "spread_bps": round(spread_bps, 2),
           "mean_abs_move_bps": round(float(d.real_bps.abs().mean()), 2)}

    def leg(sign: np.ndarray, name: str):
        sign = np.asarray(sign, dtype=float)
        take = sign != 0
        pnl = sign[take] * d.real_bps.to_numpy()[take] - spread_bps
        out[name] = {"trades": int(take.sum()),
                     "hit_rate": round(float((sign[take] * d.real_bps.to_numpy()[take] > 0).mean()), 4)
                     if take.any() else None,
                     "net_bps": round(float(pnl.mean()), 3) if take.any() else None,
                     "t": round(_t(pnl), 2) if take.any() else None}

    leg(np.sign(d.exp.to_numpy()), "kronos")
    conf = (d.p_up - 0.5).abs() >= 0.3
    leg(np.where(conf, np.sign(d.p_up - 0.5), 0), "kronos_confident")
    leg(d.mom.to_numpy(), "momentum")
    leg(np.ones(len(d)), "long")
    out["rank_ic"] = round(_spearman(d.exp.to_numpy(), d.real_bps.to_numpy()), 4)
    bins = pd.cut(d.p_up, [-0.01, 0.2, 0.4, 0.6, 0.8, 1.01])
    out["calibration"] = [
        {"p_up": str(k), "n": int(len(g)), "actual_up": round(float((g.real_bps > 0).mean()), 3)}
        for k, g in d.groupby(bins, observed=True)]
    ok = np.isfinite(d.range_atr_fc) & np.isfinite(d.range_real_atr)
    out["range_corr"] = (round(float(np.corrcoef(d.range_atr_fc[ok], d.range_real_atr[ok])[0, 1]), 3)
                         if ok.sum() > 5 else None)
    return out


def _summarise(trades: list[float]) -> dict:
    r = np.asarray([x for x in trades if x is not None and np.isfinite(x)], dtype=float)
    if not len(r):
        return {"trades": 0}
    wins, losses = r[r > 0].sum(), -r[r < 0].sum()
    eq = np.cumsum(r)
    dd = float((np.maximum.accumulate(np.r_[0, eq]) - np.r_[0, eq]).max())
    return {"trades": int(len(r)), "mean_r": round(float(r.mean()), 4),
            "total_r": round(float(r.sum()), 2), "win_rate": round(float((r > 0).mean()), 4),
            "profit_factor": round(float(wins / losses), 3) if losses > 0 else None,
            "max_dd_r": round(dd, 2), "t": round(_t(r), 2)}


def run_signals(frame: pd.DataFrame, signals: list[dict], symbol: str,
                risk_config: dict) -> list[float]:
    """The app's own engine, costs and risk layer; R per closed trade."""
    from backend.analytics.metrics import compute_trade_metrics
    from backend.backtester.engine import BacktestEngine

    if not signals:
        return []
    eng = BacktestEngine(dict(risk_config))
    eng.group_chart_data = False
    res = eng.run(frame, [dict(s) for s in signals], 10_000.0, None, None, None, None, None)
    out = []
    for g in res.get("grouped_trades") or []:
        try:
            r = compute_trade_metrics(g).get("realized_rr")
        except Exception:                               # noqa: BLE001
            r = None
        if r is not None:
            out.append(float(r))
    return out


def gate_study(frame: pd.DataFrame, signals: list[dict], rows_by_asof: dict[int, dict],
               symbol: str, risk_config: dict, gate_cfg: kg.GateConfig) -> dict:
    """All setups vs gate-allowed vs gate-rejected, through the real engine.

    A signal's decision bar is the bar it is stamped with (`base_signals`
    stamps the last CLOSED bar), so the feature row to use is the one whose
    `asof` is that stamp - a forecast from exactly the bars the strategy saw."""
    allowed, rejected, missing = [], [], 0
    for s in signals:
        row = rows_by_asof.get(int(s["time"]))
        if row is None:
            missing += 1
        d = kg.decide(gate_cfg, s["direction"], row, int(s["time"]))
        (allowed if d.allow else rejected).append(s)
    return {
        "gate": gate_cfg.__dict__, "missing_features": missing,
        "all": _summarise(run_signals(frame, signals, symbol, risk_config)),
        "allowed": _summarise(run_signals(frame, allowed, symbol, risk_config)),
        "rejected": _summarise(run_signals(frame, rejected, symbol, risk_config)),
    }
