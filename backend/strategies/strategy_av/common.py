"""
backend/strategies/strategy_av/common.py

Shared pieces of the two AlphaVantiq research engines: bar arrays with order
flow, candle patterns, the order-flow and session tests, and the trade plan.
The definitions are the research's (scripts/of_zones.py, scripts/pa_study.py),
so a preset trades what the report measured.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from backend.strategies.strategy_roboquant.replay import Bars, Ctx, ReplayStrategy

R_OF = {"r1": 1.0, "r1_5": 1.5, "r2": 2.0, "r3": 3.0}
FLOW_COLS = ("of_vol", "delta", "delta_h2", "big_buy", "big_sell", "vwap")


def attach_flow(bars: Bars, candles: pd.DataFrame) -> None:
    """Order-flow columns of the frame (marketdata/source.py) into bars.ind;
    NaN when the frame has none (MT5 data) - every flow test is then False."""
    n = len(bars)
    for k in FLOW_COLS:
        if k in candles.columns and len(candles) == n:
            bars.ind[k] = candles[k].to_numpy(dtype=float)
        else:
            bars.ind[k] = np.full(n, np.nan)
    # the prior UTC day's volume nodes from the minute profile (marketdata/source.py);
    # absent on MT5 bars, where the zone engine builds them from its own bars
    if all(f"pd_hvn{i}" in candles.columns for i in range(6)) and len(candles) == n:
        for i in range(6):
            bars.ind[f"pd_hvn{i}"] = candles[f"pd_hvn{i}"].to_numpy(dtype=float)
    if not np.isfinite(bars.ind["of_vol"]).any():
        bars.ind["of_vol"] = np.asarray(bars.v, dtype=float)       # tick volume: the profile still works


def sma_atr(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int = 14) -> np.ndarray:  # noqa: E741
    """The research's ATR: simple mean of the true range (first bar has none)."""
    prev = np.r_[np.nan, c[:-1]]
    tr = np.fmax(h - l, np.fmax(np.abs(h - prev), np.abs(l - prev)))
    tr[0] = np.nan
    return pd.Series(tr).rolling(n).mean().to_numpy()


def candle_ok(kind: str, side: int, o, h, l, c, i: int, double: bool, sweep: bool = False) -> bool:  # noqa: E741
    rng = h[i] - l[i]
    if rng <= 0:
        return False
    body = abs(c[i] - o[i])
    lw, uw = min(o[i], c[i]) - l[i], h[i] - max(o[i], c[i])
    pin = (lw >= 0.5 * rng and c[i] >= l[i] + 0.5 * rng) if side == 1 else (uw >= 0.5 * rng and c[i] <= h[i] - 0.5 * rng)
    engulf = (c[i] > o[i] and c[i - 1] < o[i - 1] and c[i] >= o[i - 1] and o[i] <= c[i - 1]) if side == 1 else \
             (c[i] < o[i] and c[i - 1] > o[i - 1] and c[i] <= o[i - 1] and o[i] >= c[i - 1])
    if kind == "any":
        return True
    if kind == "pin":
        return pin
    if kind == "engulf":
        return engulf
    if kind == "doji":
        return body <= 0.15 * rng
    if kind == "rejection":
        return pin or engulf
    if kind == "double":
        return double
    if kind == "rejection_double":
        return (pin or engulf) and double
    if kind == "sweep":
        return sweep
    return False


def double_at(side: int, h, l, i: int, a: float) -> bool:  # noqa: E741
    """An earlier swing at the same price (within 0.3 ATR) 3-36 bars back, with a
    pullback of at least 0.7 ATR between the two tests."""
    for j in range(max(1, i - 36), i - 2):
        if side == -1 and h[j] >= h[j - 1] and h[j] >= h[j + 1] and abs(h[j] - h[i]) <= 0.3 * a \
                and min(l[j:i]) <= h[i] - 0.7 * a:
            return True
        if side == 1 and l[j] <= l[j - 1] and l[j] <= l[j + 1] and abs(l[j] - l[i]) <= 0.3 * a \
                and max(h[j:i]) >= l[i] + 0.7 * a:
            return True
    return False


def flow_ok(kind: str, side: int, ind: dict, i: int, o, h, l, c) -> bool:  # noqa: E741
    if kind == "none":
        return True
    vol, dl = ind["of_vol"][i], ind["delta"][i]
    if not np.isfinite(dl):
        return False                                   # no order flow on this bar: never pass a flow test
    vavg = ind["vol_avg"][i]
    rng = h[i] - l[i]
    absorb = bool(np.isfinite(vavg) and vol >= 1.5 * vavg and dl * side < 0 and rng > 0 and
                  (((c[i] - l[i]) / rng >= 0.5) if side == 1 else ((h[i] - c[i]) / rng >= 0.5)))
    h2 = ind["delta_h2"][i]
    flip = bool(np.isfinite(h2) and h2 * side > 0)
    bb, bs = ind["big_buy"][i], ind["big_sell"][i]
    big_with = bool(np.isfinite(bb) and np.isfinite(bs) and (bb - bs) * side > 0)
    if kind == "absorption":
        return absorb
    if kind == "delta_flip":
        return flip
    if kind == "big_trades_with":
        return big_with
    if kind == "absorb_or_flip":
        return absorb or flip
    if kind == "absorb_and_flip":
        return absorb and flip
    if kind == "approach_exhausted":
        d = ind["delta"][max(0, i - 6):i]
        return bool(np.isfinite(d).all() and len(d) and float(np.sum(d)) * side > 0)
    return False


def session_ok(kind: str, utc_hhmm: int) -> bool:
    hr = (utc_hhmm // 100) + (utc_hhmm % 100) / 60.0
    if kind == "london_ny":
        return 7 <= hr < 20
    if kind == "ny_only":
        return 13.5 <= hr < 20
    return True


def plan(side: int, entry: float, stop: float, a: float, p: Any, targets: list[float],
         skip_close: bool = False) -> tuple[float, float] | None:
    """(stop, target) for a trade entered at `entry`: the stop at least min_risk_atr away;
    the target an R multiple, or (target "zone"/"level") the nearest of `targets`
    beyond the entry, which must be at least 0.8R away. skip_close=True takes the
    nearest one that is 0.8R+ away instead (the level engine's rule)."""
    risk = (entry - stop) * side
    if risk < p.min_risk_atr * a:
        stop, risk = entry - side * p.min_risk_atr * a, p.min_risk_atr * a
    if p.target in R_OF:
        return stop, entry + side * R_OF[p.target] * risk
    beyond = [x for x in targets if (x - entry) * side > 0]
    if skip_close:
        beyond = [x for x in beyond if (x - entry) * side >= 0.8 * risk]
    if not beyond:
        return None
    nearest = min(beyond) if side == 1 else max(beyond)
    if (nearest - entry) * side < 0.8 * risk:
        return None
    return stop, nearest


class AVReplayBase(ReplayStrategy):
    """Replay frame + flow columns + the time stop shared by both engines."""
    PREPARE_FLOW = True

    def _make_bars(self, candles: pd.DataFrame) -> Bars:
        bars = Bars.of(candles, self.TEMPLATE_MINUTES)
        attach_flow(bars, candles)
        self.prepare(bars)
        return bars

    def prepare(self, bars: Bars) -> None:
        bars.ind["atr"] = sma_atr(bars.h, bars.l, bars.c, 14)
        vol = bars.ind["of_vol"]
        bars.ind["vol_avg"] = pd.Series(vol).rolling(50).mean().to_numpy()
        big = bars.ind["big_buy"] + bars.ind["big_sell"]
        bars.ind["big_avg"] = pd.Series(big).rolling(50, min_periods=50).mean().to_numpy()

    def on_sim_fill(self, ctx: Ctx) -> None:
        """The app sets the target at metadata.rl_rr x the risk FROM ITS FILL
        (risk/multi_tp.py), not from the signal bar's close: the simulation does
        the same, so its 'flat or not' matches the account's."""
        k = ctx.k
        if k < 1:
            return
        o = ctx.orders.get(int(ctx.bars.t[k - 1])) or {}
        rr = (o.get("meta") or {}).get("rl_rr")
        p = ctx.pos
        if rr and p.sl:
            risk = (p.entry - p.sl) * p.side
            if risk > 0:
                p.tp = p.entry + p.side * float(rr) * risk

    def _time_stop(self, k: int, bars: Bars, ctx: Ctx) -> None:
        if ctx.position() and bars.t[k] - ctx.pos.entry_t >= self.params.max_hold_hours * 3600:
            ctx.close_position("AV_TIME_STOP")

    def forced_close(self, bars, k, position):
        entry = position.get("entry_time")
        if entry is None:
            return None
        try:
            ts = int(entry) if isinstance(entry, (int, float)) else int(pd.Timestamp(entry).timestamp())
        except Exception:  # noqa: BLE001
            return None
        return "AV_TIME_STOP" if bars.t[k] - ts >= self.params.max_hold_hours * 3600 else None
