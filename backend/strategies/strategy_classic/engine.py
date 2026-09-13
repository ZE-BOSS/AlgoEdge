"""
backend/strategies/strategy_classic/engine.py

The six classic families from the 2026-09-11 strategy search, as live/backtest
strategies:

    Donchian_v1        H1  trend breakout, ATR stop, 3xATR trail or channel exit
    EMAPullback_v1     H1  pullback to the fast EMA with the EMA regime, slot R:R target
    RSI2_v1            H1  Connors RSI(2) with the 200-bar trend, exit at the 5-bar SMA
    BollingerFade_v1   H1  close back inside the 20-bar band, exit at the middle band
    VolBreakout_v1     H1  breakout on a tick-volume surge (order-flow proxy), 3xATR trail
    TSMOM_v1           D1  time-series momentum: hold the sign of the N-day return

HOW PARITY IS KEPT
------------------
Each engine calls the research builder itself (analytics/strategy_search) on
the window it is handed and trades only a signal on the LAST closed bar. The
builders stop one bar short of the end (the research enters at the next bar's
open), so the window is padded with one copy of the last bar — every indicator
is causal, so the pad cannot change a value at the real last bar.

Exits the research applied are reproduced through on_position_bar:
    trail    MODIFY_SL to close - 3xATR(14) (never loosened), from the next bar
    channel  CLOSE when the close breaks the opposite max(5, N/2)-bar channel
    sma      CLOSE when the close crosses back through the exit SMA
    flip     CLOSE when the N-day return's sign no longer matches
    time     CLOSE after the family's maximum holding period
tests/test_classic_strategies.py holds each engine to its builder.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from backend.analytics import strategy_search as ss
from backend.strategies.base_strategy import BaseStrategy, TradeAction, TradeSignal
from backend.strategies.registry import register_strategy
from backend.strategies.strategy_classic import params as P
from backend.utils.logger import get_logger

logger = get_logger(__name__)

_TF_SECONDS = {"M5": 300, "M15": 900, "H1": 3600, "H4": 14400, "D1": 86400}


def _epoch_seconds(candles: pd.DataFrame) -> np.ndarray:
    if "time" in candles.columns:
        return candles["time"].to_numpy(dtype=np.int64)
    return pd.DatetimeIndex(candles.index).asi8 // 10**9


def candles_to_bars(symbol: str, timeframe: str, candles: pd.DataFrame, pad: bool) -> ss.Bars:
    t = _epoch_seconds(candles).astype(np.int64)
    cols = [candles[c].to_numpy(dtype=float) for c in ("open", "high", "low", "close")]
    vol = None
    for name in ("tick_volume", "volume", "tickvol"):
        if name in candles.columns:
            vol = candles[name].to_numpy(dtype=float)
            break
    # The bar's recorded spread, in PRICE units — the research floors every stop
    # at 4x it (edge_lab.accept_stop). Passing zeros here removed that floor live
    # and in the app, so BTCUSD session-pullback stops came out tighter than the
    # stops that were measured.
    spread = np.zeros(len(t))
    if "spread" in candles.columns:
        try:
            from backend.risk.position_sizer import get_symbol_info
            point = float(get_symbol_info(symbol).get("point") or 0.0)
            if point > 0:
                spread = np.maximum(candles["spread"].to_numpy(dtype=float), 0.0) * point
        except Exception:
            pass
    if pad and len(t):
        t = np.r_[t, t[-1] + _TF_SECONDS.get(timeframe, 3600)]
        cols = [np.r_[c, c[-1]] for c in cols]
        vol = np.r_[vol, vol[-1]] if vol is not None else None
        spread = np.r_[spread, spread[-1]]
    return ss.Bars(symbol, timeframe, t, *cols, spread, vol)


def _entry_epoch(position: dict) -> int | None:
    et = position.get("entry_time")
    if et is None:
        return None
    try:
        if isinstance(et, (int, float, np.integer, np.floating)):
            return int(et)
        return int(pd.Timestamp(et).value // 10**9)
    except Exception:
        return None


class ClassicStrategy(BaseStrategy):
    strategy_id = ""
    TIMEFRAME = "H1"
    PARAM_SECTION = ""
    PARAMS_CLS: type = object
    SIGNAL_TYPE = "CLASSIC"
    WINDOW_BARS: dict[str, int] = {}
    POSITION_BAR_WINDOW = 50

    def __init__(self, config: Any):
        super().__init__(config)
        self.params = getattr(config, self.PARAM_SECTION, None) or self.PARAMS_CLS()

    # -- the research mapping -------------------------------------------------
    def research_params(self, symbol: str) -> dict:
        raise NotImplementedError

    def build(self, b: ss.Bars, p: dict):
        raise NotImplementedError

    def max_hold_bars(self) -> int:
        raise NotImplementedError

    def exit_action(self, b: ss.Bars, j: int, d: int, ticket: int) -> TradeAction | None:
        return None

    # -- BaseStrategy ---------------------------------------------------------
    def get_required_timeframes(self) -> list[str]:
        return [self.TIMEFRAME]

    async def initialize(self):
        return None

    async def on_tick(self, symbol: str, tick: dict[str, Any]) -> None:
        return None

    def _slot_rr(self, symbol: str) -> float:
        from backend.strategies.strategy_defaults import SLOT_TP1_RR, get_strategy_defaults
        return float(SLOT_TP1_RR.get(f"{symbol.upper()}|{self.strategy_id}",
                                     get_strategy_defaults(self.strategy_id).get("tp1_rr", 2.0)))

    async def on_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame) -> TradeSignal | None:
        self.begin_candidate(symbol, timeframe,
                             bar_time=candles.index[-1] if candles is not None and len(candles) else None)
        if timeframe != self.TIMEFRAME or candles is None or len(candles) < 30:
            return None
        b = candles_to_bars(symbol, timeframe, candles, pad=True)
        last = len(b) - 2
        sigs, _aux = self.build(b, self.research_params(symbol))
        sig = next((s for s in sigs if s.i == last), None)
        if not self.gate("setup", sig is not None):
            return None
        if not self.gate("stop_positive", bool(sig.stop_dist > 0 and np.isfinite(sig.stop_dist))):
            return None
        c = float(b.close[last])
        long = sig.direction > 0
        rr = self._slot_rr(symbol)
        stop = float(sig.stop_dist)
        return self._tag_signal(TradeSignal(
            strategy_id=self.strategy_id,
            symbol=symbol,
            direction="BUY" if long else "SELL",
            signal_type=self.SIGNAL_TYPE,
            timeframe=timeframe,
            entry_price=c,
            stop_loss=c - stop if long else c + stop,
            take_profit=c + rr * stop if long else c - rr * stop,
            # Measured at full risk. 60 fell in the default 50% tier of
            # RiskParams.confluence_risk_tiers, halving every position.
            confluence_score=80,
            timestamp=float(b.time[last]),
            metadata={
                "size_modifier": 1.0,
                "trail_method": "NONE",
                "setup": self.strategy_id,
                "exit": sig.exit,
                "max_hold_bars": self.max_hold_bars(),
                "tp1_rr": rr,
                "reason": f"{self.strategy_id} {'long' if long else 'short'} ({self.research_params(symbol)})",
            },
        ))

    def on_position_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame,
                        position: dict) -> TradeAction | None:
        if candles is None or len(candles) < 3:
            return None
        b = candles_to_bars(symbol, self.TIMEFRAME, candles, pad=False)
        j = len(b) - 1
        d = 1 if str(position.get("direction", "BUY")).upper() in ("BUY", "BULLISH") else -1
        ticket = int(position.get("ticket") or position.get("mt5_ticket") or 0)

        action = self.exit_action(b, j, d, ticket)
        if action is not None and action.action == "CLOSE":
            return action

        ets = _entry_epoch(position)
        if ets is not None:
            if b.time[0] < ets:
                # the window reaches back past the entry: count real bars (the
                # research's max_hold is in bars, and weekends are not bars)
                held = int((b.time >= ets).sum()) - 1
            else:
                held = int((b.time[j] - ets) // _TF_SECONDS.get(self.TIMEFRAME, 3600))
            if held >= self.max_hold_bars():
                return TradeAction(ticket=ticket, action="CLOSE", close_reason="TIME")
        return action


def _atr_last(b: ss.Bars, n: int = 14) -> float:
    a = ss.atr(b, n)[-1]
    return float(a) if np.isfinite(a) else float("nan")


@register_strategy("Donchian_v1")
class DonchianStrategy(ClassicStrategy):
    strategy_id = "Donchian_v1"
    PARAM_SECTION = "donchian"
    PARAMS_CLS = P.DonchianParams
    SIGNAL_TYPE = "DONCHIAN_BREAKOUT"

    def research_params(self, symbol: str) -> dict:
        p = self.params
        return {"n": int(p.channel_bars), "k": float(p.stop_atr), "exit": p.exit_mode, "side": p.side}

    def build(self, b, p):
        return ss._donchian(b, p)

    def max_hold_bars(self) -> int:
        return 1000

    def exit_action(self, b, j, d, ticket):
        if self.params.exit_mode == "trail":
            a = _atr_last(b)
            if np.isfinite(a):
                return TradeAction(ticket=ticket, action="MODIFY_SL",
                                   new_sl=float(b.close[j]) - d * 3.0 * a, close_reason="TRAIL")
            return None
        half = max(5, int(self.params.channel_bars) // 2)
        if j < half:
            return None
        lo, hi = float(b.low[j - half:j].min()), float(b.high[j - half:j].max())
        c = float(b.close[j])
        if (d > 0 and c < lo) or (d < 0 and c > hi):
            return TradeAction(ticket=ticket, action="CLOSE", close_reason="CHANNEL")
        return None


@register_strategy("EMAPullback_v1")
class EMAPullbackStrategy(ClassicStrategy):
    strategy_id = "EMAPullback_v1"
    PARAM_SECTION = "ema_pullback"
    PARAMS_CLS = P.EMAPullbackParams
    SIGNAL_TYPE = "EMA_PULLBACK"
    WINDOW_BARS = {"H1": 1500}      # a 200-EMA needs ~7x its span to forget its seed

    def research_params(self, symbol: str) -> dict:
        p = self.params
        return {"emas": (int(p.fast_ema), int(p.slow_ema)), "rr": self._slot_rr(symbol), "side": p.side}

    def build(self, b, p):
        return ss._ema_pullback(b, p)

    def max_hold_bars(self) -> int:
        return 200


@register_strategy("RSI2_v1")
class RSI2Strategy(ClassicStrategy):
    strategy_id = "RSI2_v1"
    PARAM_SECTION = "rsi2"
    PARAMS_CLS = P.RSI2Params
    SIGNAL_TYPE = "RSI2_REVERSION"
    WINDOW_BARS = {"H1": 400}       # SMA(200) plus RSI warm-up

    def research_params(self, symbol: str) -> dict:
        p = self.params
        return {"th": float(p.threshold), "hold": int(p.max_hold_bars), "side": p.side}

    def build(self, b, p):
        return ss._rsi2(b, p)

    def max_hold_bars(self) -> int:
        return int(self.params.max_hold_bars)

    def exit_action(self, b, j, d, ticket):
        if j < 4:
            return None
        m = float(b.close[j - 4:j + 1].mean())
        c = float(b.close[j])
        if (d > 0 and c >= m) or (d < 0 and c <= m):
            return TradeAction(ticket=ticket, action="CLOSE", close_reason="SMA")
        return None


@register_strategy("BollingerFade_v1")
class BollingerFadeStrategy(ClassicStrategy):
    strategy_id = "BollingerFade_v1"
    PARAM_SECTION = "bollinger_fade"
    PARAMS_CLS = P.BollingerFadeParams
    SIGNAL_TYPE = "BOLLINGER_FADE"

    def research_params(self, symbol: str) -> dict:
        p = self.params
        return {"k": float(p.band_sigma), "side": p.side}

    def build(self, b, p):
        return ss._bollinger(b, p)

    def max_hold_bars(self) -> int:
        return 100

    def exit_action(self, b, j, d, ticket):
        if j < 19:
            return None
        m = float(b.close[j - 19:j + 1].mean())
        c = float(b.close[j])
        if (d > 0 and c >= m) or (d < 0 and c <= m):
            return TradeAction(ticket=ticket, action="CLOSE", close_reason="SMA")
        return None


@register_strategy("VolBreakout_v1")
class VolBreakoutStrategy(ClassicStrategy):
    strategy_id = "VolBreakout_v1"
    PARAM_SECTION = "vol_breakout"
    PARAMS_CLS = P.VolBreakoutParams
    SIGNAL_TYPE = "VOLUME_BREAKOUT"

    def research_params(self, symbol: str) -> dict:
        p = self.params
        return {"n": int(p.channel_bars), "m": float(p.volume_mult), "side": p.side}

    def build(self, b, p):
        return ss._vol_breakout(b, p)

    def max_hold_bars(self) -> int:
        return 1000

    def exit_action(self, b, j, d, ticket):
        a = _atr_last(b)
        if not np.isfinite(a):
            return None
        return TradeAction(ticket=ticket, action="MODIFY_SL",
                           new_sl=float(b.close[j]) - d * 3.0 * a, close_reason="TRAIL")


@register_strategy("TSMOM_v1")
class TSMOMStrategy(ClassicStrategy):
    strategy_id = "TSMOM_v1"
    TIMEFRAME = "D1"
    PARAM_SECTION = "tsmom"
    PARAMS_CLS = P.TSMOMParams
    SIGNAL_TYPE = "TSMOM_FLIP"
    WINDOW_BARS = {"D1": 200}

    @property
    def POSITION_BAR_WINDOW(self) -> int:  # noqa: N802 — read by the backtest engine
        return int(self.params.lookback_days) + 5

    def research_params(self, symbol: str) -> dict:
        p = self.params
        return {"lookback": int(p.lookback_days), "side": p.side}

    def build(self, b, p):
        return ss._tsmom(b, p)

    def max_hold_bars(self) -> int:
        return 400

    def exit_action(self, b, j, d, ticket):
        L = int(self.params.lookback_days)
        if j < L:
            return None
        s = int(np.sign(b.close[j] / b.close[j - L] - 1.0))
        if self.params.side != "both" and s < 0:
            s = 0
        if s != d:
            return TradeAction(ticket=ticket, action="CLOSE", close_reason="FLIP")
        return None


CLASSIC_STRATEGIES = {cls.strategy_id: cls for cls in (
    DonchianStrategy, EMAPullbackStrategy, RSI2Strategy, BollingerFadeStrategy,
    VolBreakoutStrategy, TSMOMStrategy)}
