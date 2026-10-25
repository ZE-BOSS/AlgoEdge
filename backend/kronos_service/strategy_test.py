"""
backend/kronos_service/strategy_test.py

Kronos's direction call traded as a STRATEGY, in app form - the test that
decides whether a close-to-close forecast edge survives real trading:

  entry     next bar's open after the decision bar (the engine's own fill)
  stop      2 x ATR(14) from the window, so R is comparable across trades
  target    far (6R): the trade is meant to end on time, not on target
  exit      after `hold` bars, through the engine's `on_position_bar` hook
  costs     the engine's measured spread, slippage, commission AND swap

Legs, all on the SAME entry bars: Kronos-confident, Kronos-all, always-long,
and a random-direction control repeated over many seeds. If Kronos does not
beat the random-direction distribution, the timing - not the forecast - made
whatever money there was.

    python -m backend.kronos_service.strategy_test --symbol SPX500 --tf H1
"""

from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd

from backend.kronos_service import evaluate as ev
from backend.kronos_service import precompute as kp
from backend.kronos_service.config import KronosConfig


class TimeExit:
    """Closes every position after `hold` bars. Nothing else."""

    POSITION_BAR_WINDOW = 64

    def __init__(self, hold: int):
        self.hold = int(hold)

    def notify_outcome(self, *args, **kwargs):          # the engine calls it on every close
        return None

    def on_position_bar(self, symbol, timeframe, candles, position):
        from backend.strategies.base_strategy import TradeAction
        entry = position.get("entry_time")
        try:
            ts = int(entry) if isinstance(entry, (int, float)) else int(pd.Timestamp(entry).timestamp())
        except Exception:                               # noqa: BLE001
            return None
        times = (candles["time"].to_numpy(dtype="int64") if "time" in candles.columns
                 else pd.DatetimeIndex(candles.index).as_unit("s").asi8)
        held = int((times >= ts).sum()) - 1
        if held >= self.hold:
            return TradeAction(ticket=int(position.get("ticket") or 0), action="CLOSE",
                               close_reason="TIME_EXIT")
        return None


def _signal(sym, tf, t, direction, close, atr, rr=6.0, stop_atr=2.0):
    risk = atr * stop_atr
    s = 1 if direction == "BUY" else -1
    return {"symbol": sym, "direction": direction, "time": int(t), "entry_price": float(close),
            "stop_loss": float(close - s * risk), "take_profit": float(close + s * rr * risk),
            "timeframe": tf, "confluence_score": 80, "metadata": {}, "strategy_name": "KRONOS_TEST"}


def run_legs(frame: pd.DataFrame, rows: dict[int, dict], sym: str, tf: str, rc: dict,
             hold: int, seeds: int = 30) -> dict:
    from backend.analytics.metrics import compute_trade_metrics
    from backend.backtester.engine import BacktestEngine

    idx = frame.set_index(pd.to_datetime(frame["time"], unit="s"))

    def go(signals):
        if not signals:
            return []
        eng = BacktestEngine(dict(rc))
        eng.group_chart_data = False
        res = eng.run(idx, signals, 10_000.0, None, None, TimeExit(hold), None, None)
        out = []
        for g in res.get("grouped_trades") or []:
            try:
                r = compute_trade_metrics(g).get("realized_rr")
            except Exception:                           # noqa: BLE001
                r = None
            if r is not None:
                out.append(float(r))
        return out

    # Time order. The store is in the order rows were COMPUTED - a later run
    # over an earlier period is appended after - and the engine walks signals
    # in time, so an unsorted list silently traded only one of the periods.
    pts = sorted((int(r["asof"]), r) for r in rows.values()
                 if np.isfinite(r.get("atr", np.nan)) and r["atr"] > 0)
    close = dict(zip(frame["time"].astype("int64"), frame["close"].astype(float)))
    conf, allk, longs = [], [], []
    for t, r in pts:
        c = close.get(t)
        if c is None:
            continue
        d = "BUY" if r["exp_return_pct"] > 0 else "SELL"
        allk.append(_signal(sym, tf, t, d, c, r["atr"]))
        longs.append(_signal(sym, tf, t, "BUY", c, r["atr"]))
        if r["p_up"] >= 0.8 or r["p_up"] <= 0.2:
            conf.append(_signal(sym, tf, t, "BUY" if r["p_up"] >= 0.8 else "SELL", c, r["atr"]))
    out = {"kronos_confident": ev._summarise(go(conf)), "kronos_all": ev._summarise(go(allk)),
           "always_long": ev._summarise(go(longs))}
    # Random direction on the CONFIDENT bars: same timing, no forecast.
    rnd = []
    rng = np.random.default_rng(7)
    for _ in range(seeds):
        sig = [dict(s, direction=("BUY" if rng.random() < 0.5 else "SELL")) for s in conf]
        sig = [_signal(sym, tf, s["time"], s["direction"], s["entry_price"],
                       abs(s["entry_price"] - s["stop_loss"]) / 2.0) for s in sig]
        r = ev._summarise(go(sig))
        if r.get("trades"):
            rnd.append(r["mean_r"])
    if rnd:
        k = out["kronos_confident"].get("mean_r", float("nan"))
        out["random_on_confident_bars"] = {
            "seeds": len(rnd), "mean_r_mean": round(float(np.mean(rnd)), 4),
            "mean_r_p95": round(float(np.percentile(rnd, 95)), 4),
            "kronos_beats_share": round(float(np.mean([k > x for x in rnd])), 3)}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--tf", default="H1")
    ap.add_argument("--start", default="2024-07-01")
    ap.add_argument("--end", default="2026-10-16")
    ap.add_argument("--hold", type=int, default=11)
    ap.add_argument("--model", default="Kronos-mini")
    ap.add_argument("--lookback", type=int, default=512)
    a = ap.parse_args()
    from backend.kronos_service.research import load_bars
    from backend.rl import env as E

    cfg = KronosConfig(model=a.model, lookback=a.lookback, pred_len=12, sample_count=5)
    rows = kp.load(a.symbol, a.tf, cfg)
    f = load_bars(a.symbol, a.tf, a.start, a.end)
    rc = E.default_risk_config(a.symbol)
    res = run_legs(f, rows, a.symbol, a.tf, rc, a.hold)
    print(json.dumps({a.symbol: res}), flush=True)


if __name__ == "__main__":
    main()
