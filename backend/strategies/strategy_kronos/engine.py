"""
backend/strategies/strategy_kronos/engine.py

KRONOS_v1: Kronos's confident direction call as a strategy (see params.py).

WHERE THE FORECASTS COME FROM
-----------------------------
Never from torch in this process. The trading backend must start, and keep
its CPU, without a 200 MB deep-learning runtime, so this strategy READS
forecast rows that the Kronos job runner (backend/kronos_service/jobs.py, its
own low-priority process) wrote to data/kronos/ - the same store, the same
`model_id` and the same feature schema the research used. A bar with no
forecast row produces no signal and says so in the gate log: "precompute the
dates on the Kronos page" for a backtest, "the Kronos live runner is not
producing rows" live.

THE RULE (as measured)
----------------------
At a decision bar with a forecast: BUY when >= min_agreement of the paths end
higher, SELL when >= min_agreement end lower, else nothing. Entry at the next
open, stop stop_atr_multiple x ATR(14), target target_rr x risk, closed after
hold_bars bars by `on_position_bar`.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from backend.strategies.base_strategy import BaseStrategy, TradeAction, TradeSignal
from backend.strategies.core.daily_atr import last_epoch
from backend.strategies.registry import register_strategy
from backend.strategies.strategy_kronos.params import KronosStrategyParams
from backend.utils.logger import get_logger

logger = get_logger(__name__)


def _config(p: KronosStrategyParams):
    from backend.kronos_service.config import KronosConfig
    return KronosConfig(model=p.model, lookback=p.lookback, pred_len=p.pred_len,
                        sample_count=p.sample_count)


@register_strategy("KRONOS_v1")
class KronosStrategy(BaseStrategy):
    strategy_id = "KRONOS_v1"
    LIVE_POSITION_EXITS = True
    POSITION_BAR_WINDOW = 64

    def __init__(self, config: Any):
        super().__init__(config)
        self.params = getattr(config, "kronos_strategy", None) or KronosStrategyParams()
        self._rows: dict[str, dict[int, dict]] = {}
        self._loaded_at: dict[str, float] = {}

    def get_required_timeframes(self) -> list[str]:
        return [self.params.timeframe]

    async def initialize(self):
        return None

    async def on_tick(self, symbol: str, tick: dict[str, Any]) -> None:
        return None

    # -- the forecast store ----------------------------------------------------

    def _store(self, symbol: str) -> dict[int, dict]:
        """Rows for this symbol, re-read at most once a minute live (the runner
        appends) and once per backtest."""
        import time
        now = time.time()
        if symbol in self._rows and (self.is_backtesting or now - self._loaded_at[symbol] < 60):
            return self._rows[symbol]
        from backend.kronos_service import precompute as kp
        try:
            rows = kp.load(symbol, self.params.timeframe, _config(self.params))
        except Exception as exc:                        # noqa: BLE001
            logger.warning(f"[KRONOS] could not read forecasts for {symbol}: {exc}")
            rows = {}
        self._rows[symbol], self._loaded_at[symbol] = rows, now
        return rows

    def prepare_backtest(self, symbol: str, frames_by_tf: dict) -> None:
        self._rows.pop(symbol, None)
        self._store(symbol)

    # -- the entry -----------------------------------------------------------

    async def on_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame) -> TradeSignal | None:
        p = self.params
        self.begin_candidate(symbol, timeframe,
                             bar_time=candles.index[-1] if candles is not None and len(candles) else None)
        if timeframe != p.timeframe or candles is None or len(candles) < 20:
            return None
        t = int(last_epoch(candles))
        row = self._store(symbol).get(t)
        if not self.gate("kronos_forecast", row is not None,
                         "no Kronos forecast for this bar - precompute it on the Kronos page"
                         if self.is_backtesting else "the Kronos live runner has no row for this bar"):
            return None
        p_up = float(row.get("p_up", 0.5))
        thr = float(p.min_agreement)
        # 1e-9: p_up is k/5, and 1 - 0.8 is 0.19999999999999996 in floating point.
        buy, sell = p_up >= thr - 1e-9, p_up <= (1.0 - thr) + 1e-9
        if not self.gate("paths_agree", buy or sell,
                         f"{p_up:.0%} of paths end higher (need {thr:.0%} either way)"):
            return None
        atr = float(row.get("atr") or 0.0)
        if not self.gate("atr_available", np.isfinite(atr) and atr > 0):
            return None
        entry = float(candles["close"].iloc[-1])
        direction = "BUY" if buy else "SELL"
        sgn = 1.0 if buy else -1.0
        risk = atr * float(p.stop_atr_multiple)
        signal = TradeSignal(
            strategy_id=self.strategy_id, symbol=symbol, direction=direction,
            signal_type="KRONOS", timeframe=timeframe, entry_price=entry,
            stop_loss=entry - sgn * risk, take_profit=entry + sgn * float(p.target_rr) * risk,
            confluence_score=80, timestamp=float(t),
            metadata={"kronos_model_id": row.get("model_id"), "kronos_p_up": round(p_up, 3),
                      "kronos_exp_return_atr": row.get("exp_return_atr"),
                      "kronos_dispersion_atr": row.get("dispersion_atr"),
                      "hold_bars": int(p.hold_bars)})
        return self._tag_signal(signal)

    # -- the exit ------------------------------------------------------------

    def on_position_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame,
                        position: dict) -> TradeAction | None:
        entry = position.get("entry_time")
        if entry is None or candles is None or not len(candles):
            return None
        try:
            ts = int(entry) if isinstance(entry, (int, float)) else int(pd.Timestamp(entry).timestamp())
        except Exception:                               # noqa: BLE001
            return None
        times = (candles["time"].to_numpy(dtype="int64") if "time" in candles.columns
                 else pd.DatetimeIndex(candles.index).as_unit("s").asi8)
        if int((times >= ts).sum()) - 1 >= int(self.params.hold_bars):
            return TradeAction(ticket=int(position.get("ticket") or 0), action="CLOSE",
                               close_reason="KRONOS_TIME_EXIT")
        return None

    def notify_outcome(self, *args, **kwargs) -> None:
        return None
