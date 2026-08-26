"""
backend/services/market_context.py

[9.3/9.4] Market Context Service — Part 7 §7.3 of the master plan.

"Merging strategies is wrong; sharing CONTEXT is right." Every engine reads
this and scores a confluence contribution against it (never a veto); the
risk layer separately applies a bounded size_modifier from it. This module
computes the context; wiring it into a strategy's own confluence score is
per-engine (not done here — see the module docstring's "Not wired" note
below for what that would take, and TASKS.md 9.4 for the honest scope of
what shipped in this pass).

Fields, per Part 7 §7.3's `MarketContext(symbol, timestamp) -> {...}`:
    htf_trend            BULLISH | BEARISH | NEUTRAL   — MarketStructureDetector on H4
    session               ASIAN | LONDON | NY | OVERLAP | DEAD (via detect_session)
    volatility_regime     LOW | NORMAL | HIGH            — ATR(14) percentile vs. its own 100-bar history
    vwap_zone              BELOW_2SD | BELOW_1SD | AT_VALUE | ABOVE_1SD | ABOVE_2SD
    news_proximity_minutes  float | None — minutes to next HIGH-impact event, if a NewsFilter is supplied
    correlation_cluster    str — NOT computed here (needs synchronized multi-symbol
                             price history only the portfolio engine has); use
                             risk/portfolio_governor.py::resolve_cluster(symbol) instead,
                             which is the STATIC clustering table already wired into
                             the cluster-exposure-cap guard (9.5/9.6).
    gamma_regime           None — explicitly deferred, gated on D-6 (Phase 11, unresolved
                             GEX data vendor decision). Never guess this.

NOT WIRED into any strategy engine's confluence score in this pass — that is
a genuinely separate, repetitive task (touch all 6 engines' _confluence_score
methods to add a context-agreement component) which risks introducing subtle
per-strategy scoring regressions without the ability to backtest-verify them
here. What IS wired: `market_context_size_modifier()` below, called from
risk/engine.py::evaluate_signal when `signal_data.metadata.market_context`
is present — additive/opt-in, so no existing behaviour changes until a
caller actually populates that key.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from backend.strategies.core.swing_structure import calculate_atr
from backend.utils.timeutils import detect_session


@dataclass
class MarketContext:
    symbol: str
    timestamp: float
    htf_trend: str = "NEUTRAL"          # BULLISH | BEARISH | NEUTRAL
    session: str = "UNKNOWN"            # ASIAN | LONDON | NY | OVERLAP | 24/7 | UNKNOWN
    volatility_regime: str = "NORMAL"   # LOW | NORMAL | HIGH
    vwap_zone: str = "UNKNOWN"          # BELOW_2SD | BELOW_1SD | AT_VALUE | ABOVE_1SD | ABOVE_2SD | UNKNOWN
    news_proximity_minutes: float | None = None
    gamma_regime: str | None = None     # Always None — see module docstring (D-6 gated)


def _resolve_volatility_regime(candles: pd.DataFrame, lookback: int = 100) -> str:
    """ATR(14) percentile rank within its own recent history."""
    if len(candles) < 20:
        return "NORMAL"
    atr_now = calculate_atr(candles, 14)
    # Build a rolling ATR series cheaply: recompute ATR at each of the last
    # `lookback` points using an expanding tail — acceptable cost for a
    # per-signal context lookup (not a hot per-bar loop).
    tail = candles.iloc[-(lookback + 14):] if len(candles) > lookback + 14 else candles
    atr_series = []
    for i in range(14, len(tail)):
        atr_series.append(calculate_atr(tail.iloc[: i + 1], 14))
    if len(atr_series) < 10 or atr_now <= 0:
        return "NORMAL"
    percentile = sum(1 for a in atr_series if a < atr_now) / len(atr_series)
    if percentile >= 0.80:
        return "HIGH"
    if percentile <= 0.20:
        return "LOW"
    return "NORMAL"


def _resolve_vwap_zone(candles: pd.DataFrame) -> str:
    """Where the latest close sits relative to the session-anchored VWAP ±1σ/±2σ bands."""
    try:
        from backend.strategies.strategy_vwap.engine import _calculate_anchored_vwap_with_bands
    except Exception:
        return "UNKNOWN"
    if len(candles) < 10:
        return "UNKNOWN"
    # Returns numpy arrays, not Series — index with [-1].
    vwap_series, std_series = _calculate_anchored_vwap_with_bands(candles, 15, 0)
    if len(vwap_series) == 0:
        return "UNKNOWN"
    vwap_now = vwap_series[-1]
    std_now = float(std_series[-1]) if not pd.isna(std_series[-1]) else 0.0
    close = candles["close"].iloc[-1]
    if pd.isna(vwap_now) or std_now <= 0:
        return "AT_VALUE" if pd.isna(vwap_now) else "UNKNOWN"
    dist_sigma = (close - vwap_now) / std_now
    if dist_sigma >= 2.0:
        return "ABOVE_2SD"
    if dist_sigma >= 1.0:
        return "ABOVE_1SD"
    if dist_sigma <= -2.0:
        return "BELOW_2SD"
    if dist_sigma <= -1.0:
        return "BELOW_1SD"
    return "AT_VALUE"


def compute_market_context(
    symbol: str,
    entry_tf_candles: pd.DataFrame,
    htf_candles: pd.DataFrame | None = None,
    current_time: datetime | float | None = None,
    news_filter=None,
) -> MarketContext:
    """
    Build a MarketContext for `symbol` at the current bar.

    `entry_tf_candles`: the strategy's own entry-timeframe candles (used for
        volatility_regime and vwap_zone).
    `htf_candles`: optional higher-timeframe candles (e.g. H4) for htf_trend;
        falls back to NEUTRAL when not supplied (never guessed from LTF data).
    `news_filter`: optional `backend.risk.news_filter.NewsFilter` instance;
        news_proximity_minutes stays None when not supplied.
    """
    ts = current_time
    if ts is None and len(entry_tf_candles) > 0:
        ts = entry_tf_candles.index[-1]
    timestamp = ts.timestamp() if hasattr(ts, "timestamp") else float(ts or 0.0)

    session = detect_session(ts) if ts is not None else "UNKNOWN"
    volatility_regime = _resolve_volatility_regime(entry_tf_candles)
    vwap_zone = _resolve_vwap_zone(entry_tf_candles)

    htf_trend = "NEUTRAL"
    if htf_candles is not None and len(htf_candles) >= 15:
        try:
            from backend.strategies.core.market_structure import MarketStructureDetector
            detector = MarketStructureDetector(swing_length=5, min_bos_count=1)
            detector.update(htf_candles)
            htf_trend = detector.get_bias() if hasattr(detector, "get_bias") else detector.trend
        except Exception:
            pass

    news_proximity_minutes = None
    if news_filter is not None:
        try:
            upcoming = news_filter.get_upcoming_events(hours_ahead=24)
            relevant = [e for e in upcoming if not e.get("currency") or True]  # currency-symbol filtering left to the caller's own NewsFilter config
            if relevant and ts is not None:
                now_dt = ts if isinstance(ts, datetime) else datetime.fromtimestamp(timestamp)
                deltas = [
                    abs((e["time"] - now_dt).total_seconds() / 60.0)
                    for e in relevant if e.get("time") is not None
                ]
                if deltas:
                    news_proximity_minutes = min(deltas)
        except Exception:
            pass

    return MarketContext(
        symbol=symbol,
        timestamp=timestamp,
        htf_trend=htf_trend,
        session=session,
        volatility_regime=volatility_regime,
        vwap_zone=vwap_zone,
        news_proximity_minutes=news_proximity_minutes,
        gamma_regime=None,  # [D-6] never guessed
    )


def market_context_size_modifier(ctx: MarketContext, direction: str) -> float:
    """
    [9.4] Bounded size_modifier ∈ [0.5, 1.5] from MarketContext, per Part 7
    §7.3 — "never a veto." Called from risk/engine.py::evaluate_signal when a
    signal's metadata carries a serialized MarketContext (opt-in; no strategy
    currently populates this, so this function has no effect until one does).

    Simple, transparent scoring — not a black box:
      +0.15  htf_trend agrees with trade direction
      -0.15  htf_trend opposes trade direction
      +0.10  volatility_regime is NORMAL (HIGH/LOW both slightly penalised —
             HIGH means wider, less predictable moves; LOW means a
             directional trade has less room to develop)
      -0.10  volatility_regime is HIGH or LOW
      -0.20  news_proximity_minutes < 15 (about to trade into a news spike)
    Base 1.0, clamped to [0.5, 1.5].
    """
    modifier = 1.0
    is_buy = direction.upper() in ("BUY", "BULLISH")

    if ctx.htf_trend in ("BULLISH", "BEARISH"):
        trend_is_buy = ctx.htf_trend == "BULLISH"
        modifier += 0.15 if trend_is_buy == is_buy else -0.15

    if ctx.volatility_regime == "NORMAL":
        modifier += 0.10
    elif ctx.volatility_regime in ("HIGH", "LOW"):
        modifier -= 0.10

    if ctx.news_proximity_minutes is not None and ctx.news_proximity_minutes < 15:
        modifier -= 0.20

    return max(0.5, min(1.5, modifier))
