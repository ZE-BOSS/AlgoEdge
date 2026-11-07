"""
backend/strategies/strategy_zero_loss/engine.py

Zero Loss Boom & Crash v3.2, mirrored from the TradingView strategy
(zero_loss_boom_crash_strategy.pine) and its MT5 EA:

  black = 100 x (EMA5(close) - lowest(low, 100)) / (highest(high, 100) - lowest(low, 100))
  red   = EMA30(close)
  SELL: black touched 80 within the last 5 candles, has fallen 3 candles in a
        row, and the candle opened above the red line and closed below it.
  BUY : the mirror at 20.
  Boom: SELL = video trade, BUY = spike side. Crash: BUY = video, SELL = spike.
  Any other symbol: buys if Boom BUY or Crash BUY is on, sells if a SELL is on.
  Stop 8 x ATR(14) from the close; no target; exit when the black line reaches
  the opposite zone (80 for a buy, 20 for a sell) or after 100 candles.

STATELESS like the roboquant ports: every value is recomputed from the window,
so a backtest and the live feed agree. The one fill difference: Pine
(process_orders_on_close) fills at the signal candle's close; this app fills at
the next candle's open - the same price on a 24/7 synthetic.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from backend.strategies.registry import register_strategy
from backend.strategies.strategy_roboquant import common as c
from backend.strategies.strategy_roboquant.engine import _RQBase
from backend.strategies.strategy_zero_loss.params import ZeroLossBoomCrashParams


def pine_ema(x: np.ndarray, n: int) -> np.ndarray:
    """ta.ema (alpha 2/(n+1)). Seeded with the first value instead of Pine's
    SMA of the first n: over the 400-bar window the seed has decayed to
    nothing ((n-1)/(n+1)^370 < 1e-10 for n = 30), and pandas does it in C."""
    x = np.asarray(x, dtype=float)
    return pd.Series(x).ewm(alpha=2.0 / (n + 1.0), adjust=False).mean().to_numpy(copy=True)


def pine_atr(h: np.ndarray, l: np.ndarray, cl: np.ndarray, n: int) -> np.ndarray:  # noqa: E741
    """ta.atr: RMA (alpha 1/n) of the true range."""
    h, l, cl = (np.asarray(v, dtype=float) for v in (h, l, cl))  # noqa: E741
    pc = np.r_[np.nan, cl[:-1]]
    tr = np.where(np.isnan(pc), h - l, np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc))))
    return pd.Series(tr).ewm(alpha=1.0 / n, adjust=False).mean().to_numpy(copy=True)


def indicators(candles: pd.DataFrame, p: ZeroLossBoomCrashParams) -> dict[str, np.ndarray]:
    o = candles["open"].to_numpy(dtype=float)
    h = candles["high"].to_numpy(dtype=float)
    l = candles["low"].to_numpy(dtype=float)  # noqa: E741
    cl = candles["close"].to_numpy(dtype=float)
    red = pine_ema(cl, p.red_len)
    blk = pine_ema(cl, p.black_len)
    hh = pd.Series(h).rolling(p.n_bars, min_periods=p.n_bars).max().to_numpy()
    ll = pd.Series(l).rolling(p.n_bars, min_periods=p.n_bars).min().to_numpy()
    with np.errstate(invalid="ignore", divide="ignore"):
        black = np.where(hh > ll, 100.0 * (blk - ll) / (hh - ll), np.nan)
    return dict(o=o, h=h, l=l, c=cl, red=red, black=black, atr=pine_atr(h, l, cl, p.atr_len))


def kind_of(symbol: str) -> str:
    s = (symbol or "").upper()
    return "BOOM" if "BOOM" in s else "CRASH" if "CRASH" in s else "OTHER"


def signal_at(ind: dict[str, np.ndarray], i: int, p: ZeroLossBoomCrashParams, kind: str) -> tuple[str | None, str]:
    """('SELL'|'BUY'|None, tag) for bar i - the Pine script's sellSig / buySig."""
    o, cl, red, black = ind["o"], ind["c"], ind["red"], ind["black"]
    if i < max(p.n_bars, p.zone_mem + p.turn_n) or not np.isfinite(red[i]) or not np.isfinite(black[i]):
        return None, ""
    if p.cross_mode == "close":
        cross_dn = cl[i - 1] >= red[i - 1] and cl[i] < red[i]
        cross_up = cl[i - 1] <= red[i - 1] and cl[i] > red[i]
    else:
        cross_dn = o[i] > red[i] and cl[i] < red[i]
        cross_up = o[i] < red[i] and cl[i] > red[i]
    win = black[i - p.zone_mem + 1: i + 1]
    in_sell = bool(np.nanmax(win) >= p.lvl_sell) if np.isfinite(win).any() else False
    in_buy = bool(np.nanmin(win) <= p.lvl_buy) if np.isfinite(win).any() else False
    boom, crash, other = kind == "BOOM", kind == "CRASH", kind == "OTHER"
    video_sell = in_sell and cross_dn and ((boom and p.boom_sell) or (other and (p.boom_sell or p.crash_sell)))
    video_buy = in_buy and cross_up and ((crash and p.crash_buy) or (other and (p.boom_buy or p.crash_buy)))
    spike_sell = in_sell and cross_dn and crash and p.crash_sell
    spike_buy = in_buy and cross_up and boom and p.boom_buy
    turn_s = turn_b = True
    for k in range(p.turn_n):
        a, b = black[i - k], black[i - k - 1]
        if not (a < b):
            turn_s = False
        if not (a > b):
            turn_b = False
    if (video_sell or spike_sell) and turn_s:
        return "SELL", "SELL video" if video_sell else "SELL spike"
    if (video_buy or spike_buy) and turn_b:
        return "BUY", "BUY video" if video_buy else "BUY spike"
    return None, ""


@register_strategy("ZeroLossBoomCrash_v32")
class ZeroLossBoomCrash(_RQBase):
    strategy_id = "ZeroLossBoomCrash_v32"
    PARAMS_ATTR = "zero_loss_boom_crash"
    PARAMS_CLS = ZeroLossBoomCrashParams
    WINDOW = 400
    POSITION_BAR_WINDOW = 400

    def __init__(self, config: Any):
        super().__init__(config)
        # Pine closes a zone / time exit at the candle's close and takes no new
        # entry on that same candle (the position is still open while the script
        # runs). Remember the candle a strategy exit was asked on, per symbol.
        self._exit_bar: dict[str, int] = {}

    async def on_bar(self, symbol, timeframe, candles):
        p = self.params
        if not self._start(symbol, timeframe, candles, p.n_bars + 40):
            return None
        if self._exit_bar.get(symbol) == int(c.epochs(candles)[-1]):
            self.gate("zl_exit_bar", False, "a position closed on this candle")
            return None
        ind = indicators(candles, p)
        i = len(candles) - 1
        side, tag = signal_at(ind, i, p, kind_of(symbol))
        if not self.gate("zl_signal", side is not None, "no Zero Loss signal on this candle"):
            return None
        atr = float(ind["atr"][i])
        if not np.isfinite(atr) or atr <= 0:
            return None
        dist = p.sl_points if p.sl_mode == "points" else p.sl_atr * atr
        s = -1.0 if side == "SELL" else 1.0
        entry = float(ind["c"][i])
        stop_dist = dist * (3.0 if p.sl_mode == "none" else 1.0)
        sl = entry - s * stop_dist
        # No target in the Pine default: a placeholder 40R away (never reached
        # before the zone / time exit), kept above zero on a deep sell.
        tp = entry + s * (p.tp_r * dist if p.tp_r > 0 else 40.0 * stop_dist)
        tp = max(tp, entry * 0.05)
        meta: dict[str, Any] = dict(tag=tag, black=float(ind["black"][i]), red=float(ind["red"][i]), atr=atr)
        if p.trade_min_lot:
            meta["round_up_to_min_lot"] = True
        return self._signal(symbol, timeframe, candles, side, entry, sl, tp, **meta)

    def on_position_bar(self, symbol, timeframe, candles, position):
        p = self.params
        if candles is None or len(candles) < p.n_bars + 5:
            return None
        ind = indicators(candles, p)
        i = len(candles) - 1
        held = self._bars_held(candles, position)
        # Pine counts from the signal candle (it filled on that close); here
        # the fill is the next candle's open, so add the one candle back.
        held = (held + 1) if held is not None else None
        long_pos = str(position.get("direction", "BUY")).upper() == "BUY"
        black = ind["black"][i]
        zone_x = bool(np.isfinite(black) and (black >= p.lvl_sell if long_pos else black <= p.lvl_buy))
        back_x = bool(ind["c"][i] < ind["red"][i] if long_pos else ind["c"][i] > ind["red"][i])
        timed = held is not None and held >= p.hold_bars
        if p.exit_mode == "fixed":
            why = "ZL_TIME_EXIT" if timed else None
        elif p.exit_mode == "red":
            why = "ZL_TIME_EXIT" if timed else ("ZL_RED_LINE" if held is not None and held >= p.min_hold and back_x else None)
        else:
            why = "ZL_OPPOSITE_ZONE" if zone_x else ("ZL_TIME_EXIT" if timed else None)
        if why:
            self._exit_bar[symbol] = int(c.epochs(candles)[-1])
            return self._close(position, why)
        return None
