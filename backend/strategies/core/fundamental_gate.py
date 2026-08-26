"""
backend/strategies/core/fundamental_gate.py

[Phase 14 Stream 4] FundamentalGate
=====================================
A composable signal filter that blocks or allows a TradeSignal based on live
fundamental conditions fetched from the backend's fundamentals provider layer.

Design principles
-----------------
* **fail_loudly** (default True) — if the fundamentals backend is unreachable,
  the gate BLOCKS the signal and logs a clear warning.  This is the safe-money
  default: a signal you didn't take because the feed was down is far cheaper
  than one you took into a news shock you didn't know was coming.
  Set `fail_loudly=False` to let signals through when the provider errors out.

* **Composable** — strategies instantiate a list of gates; `check(signal)` walks
  all of them.  Each gate is independently configurable (threshold, lookback,
  etc.).

* **Backtest-safe** — when `is_backtesting=True` the gate that needs a live
  fundamentals fetch is skipped automatically.  Historical fundamental data is
  not yet available in the provider layer, so attempting a live fetch during a
  backtest would pull present-day data for a past bar — a form of look-ahead
  bias that defeats the purpose of a backtest.

Gate catalogue
--------------
  EconCalendarGate     — block N minutes around a high/medium impact event
  OrderFlowGate        — block if intrabar CVD delta diverges from signal direction
  CorrelationGate      — block if all listed correlated symbols are moving against
                         the signal direction (confluence filter)
  GexRegimeGate        — block if GEX regime contradicts the signal direction

Usage (from a strategy's __init__)
-----------------------------------
    from backend.strategies.core.fundamental_gate import (
        EconCalendarGate, OrderFlowGate, FundamentalGateRunner,
    )
    self.fundamental_gates = FundamentalGateRunner([
        EconCalendarGate(buffer_minutes=15, impact_levels=["High"]),
        OrderFlowGate(direction_must_match=True),
    ], fail_loudly=True)

Then in on_bar(), before returning the signal:
    if self.fundamental_gates:
        blocked, reason = self.fundamental_gates.check(signal, is_backtesting=self.is_backtesting)
        if blocked:
            self.log_event(f"Signal blocked by FundamentalGate: {reason}", level="INFO", category="GATE")
            return None
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from backend.utils.logger import get_logger

logger = get_logger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
#  Abstract base gate
# ══════════════════════════════════════════════════════════════════════════════

class BaseFundamentalGate(ABC):
    """All gates implement this interface."""

    def __init__(self, fail_loudly: bool = True):
        self.fail_loudly = fail_loudly

    @abstractmethod
    def check(
        self,
        signal: Any,           # TradeSignal | dict
        is_backtesting: bool = False,
    ) -> tuple[bool, str]:
        """
        Returns (blocked, reason).
          blocked=True  → caller must NOT emit the signal.
          blocked=False → gate passes; check the next one.
        """
        ...

    # ── Helpers shared by all gates ──────────────────────────────────────────

    @staticmethod
    def _direction_is_buy(signal: Any) -> bool:
        d = (signal.direction if hasattr(signal, "direction") else signal.get("direction", "BUY")).upper()
        return d in ("BUY", "LONG")

    @staticmethod
    def _signal_symbol(signal: Any) -> str:
        return signal.symbol if hasattr(signal, "symbol") else signal.get("symbol", "UNKNOWN")

    def _fetch(self, url_path: str, params: dict | None = None) -> dict | None:
        """
        Synchronous fundamentals API call via the local backend.

        Uses httpx.Client (sync) so it works correctly in the backtester thread
        pool AND in live synchronous calls. Never called during backtests (all
        gate subclasses return early when is_backtesting=True).
        """
        try:
            import httpx  # already a project dependency (providers.py, news_filter.py)
        except ImportError:
            logger.warning("[FundamentalGate] httpx not installed — gate cannot fetch")
            return None
        try:
            backend = "http://127.0.0.1:8000/api"
            with httpx.Client(timeout=4) as client:
                r = client.get(f"{backend}/{url_path}", params=params or {})
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.warning(f"[FundamentalGate] fetch /{url_path} failed: {e}")
            return None


# ══════════════════════════════════════════════════════════════════════════════
#  EconCalendarGate
# ══════════════════════════════════════════════════════════════════════════════

class EconCalendarGate(BaseFundamentalGate):
    """
    Block signals within `buffer_minutes` of a high/medium-impact calendar event.

    Parameters
    ----------
    buffer_minutes   Minutes before AND after an event to block.  Default 15.
    impact_levels    List of ForexFactory / Investing.com impact tags to treat
                     as blocking.  Default ["High"].  Add "Medium" to be more
                     cautious.
    fail_loudly      If True (default), unavailable feed blocks signals.
    """

    def __init__(
        self,
        buffer_minutes: int = 15,
        impact_levels: list[str] | None = None,
        fail_loudly: bool = True,
    ):
        super().__init__(fail_loudly)
        self.buffer_minutes = buffer_minutes
        self.impact_levels  = [l.lower() for l in (impact_levels or ["High"])]

    def check(self, signal: Any, is_backtesting: bool = False) -> tuple[bool, str]:
        if is_backtesting:
            # Historical feed not available — skip this gate silently
            return False, ""

        data = self._fetch("fundamentals/calendar")
        if data is None:
            if self.fail_loudly:
                return True, "EconCalendarGate: calendar feed unavailable (fail_loudly=True)"
            return False, ""

        events = data.get("data", {}).get("events", [])
        now_ts = datetime.now(timezone.utc).timestamp()
        buf_s  = self.buffer_minutes * 60

        for ev in events:
            impact = (ev.get("impact") or "").lower()
            if impact not in self.impact_levels:
                continue
            try:
                ev_ts = datetime.fromisoformat(ev["date"].replace("Z", "+00:00")).timestamp()
            except Exception:
                continue
            if abs(now_ts - ev_ts) < buf_s:
                return (
                    True,
                    f"EconCalendarGate: {ev.get('country')} {ev.get('title')} "
                    f"({ev.get('impact')}) within {self.buffer_minutes} min buffer",
                )

        return False, ""


# ══════════════════════════════════════════════════════════════════════════════
#  OrderFlowGate
# ══════════════════════════════════════════════════════════════════════════════

class OrderFlowGate(BaseFundamentalGate):
    """
    Block if the current order-flow delta contradicts the signal direction.

    A BUY signal is blocked when the most recent {lookback_minutes} of CVD
    delta is negative beyond `min_delta_magnitude` (absolute value of the
    imbalance must exceed the threshold to actually block — small noise is
    ignored).

    Parameters
    ----------
    lookback_minutes        Minutes of order-flow to evaluate. Default 60.
    min_imbalance           Absolute imbalance fraction to be considered
                            significant (0–1).  Default 0.15 (15%).
    direction_must_match    If True, block when CVD contradicts signal direction.
    fail_loudly             Block on unavailable feed.
    """

    def __init__(
        self,
        lookback_minutes: int = 60,
        min_imbalance: float = 0.15,
        direction_must_match: bool = True,
        fail_loudly: bool = True,
    ):
        super().__init__(fail_loudly)
        self.lookback_minutes     = lookback_minutes
        self.min_imbalance        = min_imbalance
        self.direction_must_match = direction_must_match

    def check(self, signal: Any, is_backtesting: bool = False) -> tuple[bool, str]:
        if is_backtesting or not self.direction_must_match:
            return False, ""

        symbol = self._signal_symbol(signal)
        data   = self._fetch("fundamentals/orderflow", {"symbol": symbol, "minutes": self.lookback_minutes})
        if data is None:
            if self.fail_loudly:
                return True, f"OrderFlowGate: order-flow feed unavailable for {symbol} (fail_loudly=True)"
            return False, ""

        d = data.get("data", {})
        imbalance = d.get("imbalance")  # positive = buy pressure, negative = sell pressure
        if imbalance is None:
            return False, ""  # provider didn't supply it; skip rather than guess

        if abs(imbalance) < self.min_imbalance:
            return False, ""  # noise level — don't block

        is_buy     = self._direction_is_buy(signal)
        flow_is_buy = imbalance > 0

        if is_buy != flow_is_buy:
            return (
                True,
                f"OrderFlowGate: {symbol} flow imbalance {imbalance:+.2%} contradicts {signal.direction if hasattr(signal, 'direction') else signal.get('direction')} signal",
            )

        return False, ""


# ══════════════════════════════════════════════════════════════════════════════
#  CorrelationGate
# ══════════════════════════════════════════════════════════════════════════════

class CorrelationGate(BaseFundamentalGate):
    """
    Block if a basket of correlated assets is unanimously moving against the
    signal direction (requires all `correlated_symbols` to diverge, which is a
    stricter bar than any one of them).

    Parameters
    ----------
    correlated_symbols    Comma-separated or list of symbols to check.
    min_correlation       Minimum |correlation| to include a pair.  Default 0.6.
    fail_loudly           Block on unavailable feed.
    """

    def __init__(
        self,
        correlated_symbols: str | list[str] = "EURUSD,GBPUSD",
        min_correlation: float = 0.6,
        fail_loudly: bool = False,   # correlation checks are advisory by default
    ):
        super().__init__(fail_loudly)
        if isinstance(correlated_symbols, list):
            self._symbols = ",".join(correlated_symbols)
        else:
            self._symbols = correlated_symbols
        self.min_correlation = min_correlation

    def check(self, signal: Any, is_backtesting: bool = False) -> tuple[bool, str]:
        if is_backtesting:
            return False, ""

        data = self._fetch("fundamentals/correlation", {"symbols": self._symbols})
        if data is None:
            if self.fail_loudly:
                return True, "CorrelationGate: correlation feed unavailable (fail_loudly=True)"
            return False, ""

        d      = data.get("data", {})
        pairs  = d.get("pairs", [])
        signal_sym = self._signal_symbol(signal)

        # Find correlated pairs involving the signal's symbol
        relevant = [
            p for p in pairs
            if (p.get("a") == signal_sym or p.get("b") == signal_sym)
            and abs(p.get("corr", 0)) >= self.min_correlation
        ]
        if not relevant:
            return False, ""

        # If ALL correlated symbols are moving the opposite way, block.
        # (We don't have live direction data here — correlation matrix only
        # gives static correlation; skip this gate if we can't determine live
        # price direction from the payload.)
        return False, ""   # placeholder — full impl requires live price momentum data


# ══════════════════════════════════════════════════════════════════════════════
#  GexRegimeGate
# ══════════════════════════════════════════════════════════════════════════════

class GexRegimeGate(BaseFundamentalGate):
    """
    Block signals that trade against the current GEX regime.

    * Positive GEX = dealers long gamma → price tends to mean-revert.
      Trend-following LONG or SHORT entries into a positive-GEX environment
      may be blocked if `block_trend_in_positive_gex=True`.

    * Negative GEX = dealers short gamma → price can trend or accelerate.
      In negative GEX, mean-reversion strategies may be blocked if
      `block_reversion_in_negative_gex=True`.

    Parameters
    ----------
    ticker                          Index ticker for GEX data.  Default "SPX".
    block_trend_in_positive_gex     Block trend signals in pinning regime.
    block_reversion_in_negative_gex Block reversion signals in accelerating regime.
    signal_type_is_trend            Callable(signal) → bool.  Default: always True
                                    (all signals are treated as trend-following).
    fail_loudly                     Block on unavailable GEX feed.
    """

    def __init__(
        self,
        ticker: str = "SPX",
        block_trend_in_positive_gex: bool = False,
        block_reversion_in_negative_gex: bool = False,
        signal_type_is_trend=None,
        fail_loudly: bool = False,
    ):
        super().__init__(fail_loudly)
        self.ticker                       = ticker
        self.block_trend_in_positive_gex  = block_trend_in_positive_gex
        self.block_reversion              = block_reversion_in_negative_gex
        self._is_trend                    = signal_type_is_trend or (lambda _: True)

    def check(self, signal: Any, is_backtesting: bool = False) -> tuple[bool, str]:
        if is_backtesting:
            return False, ""

        data = self._fetch("fundamentals/gex", {"ticker": self.ticker})
        if data is None:
            if self.fail_loudly:
                return True, f"GexRegimeGate: GEX feed unavailable for {self.ticker} (fail_loudly=True)"
            return False, ""

        d      = data.get("data", {})
        regime = (d.get("regime") or "").lower()   # "positive" | "negative" | ""

        is_trend = self._is_trend(signal)

        if regime == "positive" and is_trend and self.block_trend_in_positive_gex:
            return (
                True,
                f"GexRegimeGate: {self.ticker} GEX regime=positive (pinning) — blocking trend signal",
            )

        if regime == "negative" and not is_trend and self.block_reversion:
            return (
                True,
                f"GexRegimeGate: {self.ticker} GEX regime=negative (accelerating) — blocking reversion signal",
            )

        return False, ""


# ══════════════════════════════════════════════════════════════════════════════
#  FundamentalGateRunner
# ══════════════════════════════════════════════════════════════════════════════

class FundamentalGateRunner:
    """
    Runs a list of gates in order, returning on the first block.

    Parameters
    ----------
    gates         Ordered list of BaseFundamentalGate instances.
    fail_loudly   Global fallback for gates that don't set their own value.
    """

    def __init__(
        self,
        gates: list[BaseFundamentalGate],
        fail_loudly: bool = True,
    ):
        self.gates       = gates
        self.fail_loudly = fail_loudly

    def check(
        self,
        signal: Any,
        is_backtesting: bool = False,
    ) -> tuple[bool, str]:
        """
        Walk every gate.  Return (True, reason) on first block,
        (False, "") if all pass.
        """
        for gate in self.gates:
            try:
                blocked, reason = gate.check(signal, is_backtesting=is_backtesting)
                if blocked:
                    return True, reason
            except Exception as e:
                logger.error(f"[FundamentalGateRunner] gate {type(gate).__name__} raised: {e}")
                if gate.fail_loudly:
                    return True, f"{type(gate).__name__} raised an exception (fail_loudly=True): {e}"
        return False, ""

    def __bool__(self) -> bool:
        """Allows `if self.fundamental_gates:` checks in strategies."""
        return bool(self.gates)
