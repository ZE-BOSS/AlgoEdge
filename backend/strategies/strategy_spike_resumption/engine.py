"""
backend/strategies/strategy_spike_resumption/engine.py

SpikeResumption_v1 — the drift resumes after the spike is spent.

THE RULE
--------
On BOOM, which spikes UP and drifts DOWN:

  1. an established DOWNWARD drift;
  2. ONE M15 bar breaks it with a sharp buy — the spike. The bar after it must
     not also be a spike;
  3. the next bar closes bearish;
  4. SELL at that bar's close (the engine fills at the next bar's open);
  5. out at the target, the stop, or `max_hold_bars` bars, whichever comes first.

On CRASH, which spikes DOWN and drifts UP, every direction is mirrored.

WHICH WAY IS "THE SPIKE DIRECTION"
----------------------------------
Read from `fill_model.SPIKE_FILLS`, which already records it per instrument
because the fill model needs the same fact: +1 for Boom (up spikes), −1 for
Crash. A symbol that is not in that table, or that spikes both ways (Jump,
Range Break), gets no signal at all — "the drift" is not a thing there, and
guessing a direction from the symbol's name is how a strategy ends up trading
the wrong side of an instrument nobody measured.

WHAT THE MEASUREMENT SAYS
-------------------------
Fully written up in Implementation/SPIKE-RESUMPTION-2026-09-27.md. It does NOT
clear its own control on the longest history available: on Boom/Crash 1000 over
2021-10 → 2024-09, three years the parameters were never fitted to, it loses
0.098R per trade. The positive result lives in the window its parameters were
chosen on, and per instrument it is carried entirely by Crash 1000 (+11.9%, PF
3.51) while Boom 900 — the instrument the pattern was spotted on — is the worst
of eight (−5.4%, PF 0.38, 23.5% win).

It is shipped because it was asked for, wired the same as every other strategy
so it can be measured in the app on all three paths. It is enabled on nothing by
default and `strategy_defaults` carries that evidence string so the UI shows it.

THE FILL MODEL IS NOT OPTIONAL HERE
-----------------------------------
Every entry sits one bar away from a spike, which is exactly where a bar
backtest lies most: it fills a stop AT the stop, while on these instruments a
spike-side stop is taken out by a jump and fills far past it. Run this with
`stop_fill_model=EMPIRICAL`. With the naive model the numbers are fiction —
random Boom shorts booked +0.76R a trade against −0.03R on real ticks.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from backend.backtester.fill_model import get_spike_fill
from backend.strategies.base_strategy import BaseStrategy, TradeSignal
from backend.strategies.core.daily_atr import last_epoch
from backend.strategies.core.max_hold import MaxHoldExit
from backend.strategies.registry import register_strategy
from backend.strategies.strategy_spike_resumption.params import SpikeResumptionParams
from backend.utils.logger import get_logger

logger = get_logger(__name__)

TIMEFRAME = "M15"
BELOW_BARS = 20          # how long "below" mode wants the close on one side


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int) -> np.ndarray:
    prev = np.r_[close[0], close[:-1]]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev), np.abs(low - prev)))
    out = np.full(len(tr), np.nan)
    if len(tr) > n:
        cs = np.cumsum(tr)
        out[n:] = (cs[n:] - cs[:-n]) / n
    return out


def ema(x: np.ndarray, span: int) -> np.ndarray:
    return pd.Series(x).ewm(span=int(span), adjust=False).mean().to_numpy()


def spike_size(open_: np.ndarray, high: np.ndarray, low: np.ndarray,
               close: np.ndarray, side: int, metric: str) -> np.ndarray:
    """How far each bar moved IN THE SPIKE DIRECTION, by the chosen measure."""
    body = (close - open_) * side
    if metric == "body":
        return body
    reach = (high - open_) if side > 0 else (open_ - low)
    return np.maximum(body, reach) if metric == "either" else reach


@register_strategy("SpikeResumption_v1")
class SpikeResumptionStrategy(MaxHoldExit, BaseStrategy):
    strategy_id = "SpikeResumption_v1"
    TIMEFRAME = TIMEFRAME

    def __init__(self, config: Any):
        super().__init__(config)
        self.params = getattr(config, "spike_resumption", None) or SpikeResumptionParams()

    def get_required_timeframes(self) -> list[str]:
        return [TIMEFRAME]

    @property
    def WINDOW_BARS(self) -> dict[str, int]:  # noqa: N802 — read by strategies.windows
        p = self.params
        # An EMA seeded on its own span is still carrying ~13% of its seed, so the
        # trend test needs several spans of run-up before it means what it means.
        need = max(int(p.trend_lookback) * 5, int(p.atr_period) * 5, BELOW_BARS * 4)
        return {TIMEFRAME: need + 60}

    async def initialize(self):
        return None

    async def on_tick(self, symbol: str, tick: dict[str, Any]) -> None:
        return None

    def _spike_side(self, symbol: str) -> int:
        """+1 Boom (up spikes), −1 Crash (down spikes), 0 = not this strategy's market."""
        got = get_spike_fill(symbol)
        if not got:
            return 0
        side = int(got[0])
        return side if side in (1, -1) else 0

    async def on_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame) -> TradeSignal | None:
        self.begin_candidate(
            symbol, timeframe,
            bar_time=candles.index[-1] if candles is not None and len(candles) else None,
        )
        p = self.params
        if timeframe != TIMEFRAME or candles is None:
            return None

        side = self._spike_side(symbol)
        if not self.gate("spiking_instrument", side != 0,
                         f"{symbol} is not a one-sided spike market (Boom/Crash)"):
            return None

        confirm = max(1, int(p.confirm_bars))
        need = max(int(p.trend_lookback) * 4, int(p.atr_period) + 5, BELOW_BARS) + confirm + 5
        if not self.gate("history", len(candles) >= need,
                         f"need {need} bars, have {len(candles)}"):
            return None

        o = candles["open"].to_numpy(dtype=float)
        h = candles["high"].to_numpy(dtype=float)
        lo = candles["low"].to_numpy(dtype=float)
        c = candles["close"].to_numpy(dtype=float)
        last = len(c) - 1
        i = last - confirm                       # the spike bar

        a = atr(h, lo, c, int(p.atr_period))
        if not self.gate("atr", np.isfinite(a[i]) and a[i] > 0):
            return None

        size = spike_size(o, h, lo, c, side, str(p.spike_metric))
        is_spike = size >= float(p.spike_atr_multiple) * a
        if not self.gate("spike_bar", bool(is_spike[i]),
                         f"bar {confirm} back moved {size[i] / a[i]:.1f}xATR, "
                         f"needs {p.spike_atr_multiple:g}"):
            return None
        # A spike that runs into another spike is one event still in progress.
        if not self.gate("single_bar_spike", not bool(is_spike[i + 1])):
            return None

        if int(p.min_bars_since_spike) > 0:
            prev = np.flatnonzero(is_spike[:i])
            gap = i - int(prev[-1]) if len(prev) else 10_000
            if not self.gate("spike_gap", gap >= int(p.min_bars_since_spike),
                             f"previous spike only {gap} bars back"):
                return None

        if not self.gate("drifting", self._drifting(c, side, i)):
            return None

        # every confirming bar must close AGAINST the spike
        against = all((c[k] - o[k]) * side < 0 for k in range(i + 1, last + 1))
        if not self.gate("confirmed", against,
                         f"{confirm} bar(s) after the spike did not all close against it"):
            return None

        if p.require_below_spike:
            past = (c[last] < o[i]) if side > 0 else (c[last] > o[i])
            if not self.gate("back_past_spike", past,
                             "the confirming bar has not closed back past the spike's open"):
                return None

        stop_dist = float(p.stop_atr_multiple) * float(a[i])
        if not self.gate("stop_positive", stop_dist > 0):
            return None

        from backend.strategies.strategy_defaults import SLOT_TP1_RR, get_strategy_defaults
        rr = float(SLOT_TP1_RR.get(f"{symbol.upper()}|{self.strategy_id}",
                                   get_strategy_defaults(self.strategy_id).get("tp1_rr", 3.0)))
        long = side < 0                          # Crash spikes down, so we buy
        price = float(c[last])
        return self._tag_signal(TradeSignal(
            strategy_id=self.strategy_id,
            symbol=symbol,
            direction="BUY" if long else "SELL",
            signal_type="SPIKE_RESUMPTION",
            timeframe=TIMEFRAME,
            entry_price=price,
            entry_zone_top=float(h[i]),
            entry_zone_bottom=float(lo[i]),
            stop_loss=price - stop_dist if long else price + stop_dist,
            take_profit=price + rr * stop_dist if long else price - rr * stop_dist,
            # a binary rule with no graded confluence, measured at full risk
            confluence_score=80,
            timestamp=float(last_epoch(candles)),
            metadata={
                "size_modifier": 1.0,
                "trail_method": "NONE",
                "setup": "spike_resumption",
                "spike_side": side,
                "spike_atr": float(size[i] / a[i]),
                "spike_high": float(h[i]),
                "spike_low": float(lo[i]),
                "spike_open": float(o[i]),
                "atr_val": float(a[i]),
                "tp1_rr": rr,
                "max_hold_bars": int(p.max_hold_bars),
                "reason": (
                    f"{'Crash' if long else 'Boom'} drift resumed: a "
                    f"{size[i] / a[i]:.1f}xATR {'down' if side < 0 else 'up'}-spike, then "
                    f"{confirm} bar(s) closing back the other way"
                ),
            },
        ))

    def _drifting(self, close: np.ndarray, side: int, i: int) -> bool:
        """Is the drift running AGAINST the spike direction at bar `i`?"""
        p = self.params
        look = max(2, int(p.trend_lookback))
        mode = str(p.trend_mode)
        if mode == "ret":
            if i < look:
                return False
            return ((close[i] - close[i - look]) * side) < 0
        slow = ema(close[:i + 1], look)
        if mode == "below":
            win = min(look, BELOW_BARS)
            if i < win:
                return False
            return bool((((close[i - win:i + 1] - slow[i - win:i + 1]) * side) < 0).all())
        fast = ema(close[:i + 1], max(3, look // 4))
        return ((fast[i] - slow[i]) * side) < 0
