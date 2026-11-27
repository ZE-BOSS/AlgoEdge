"""
backend/strategies/strategy_av/params.py

Parameters of the AlphaVantiq research strategies (27 Nov 2026 report,
"Order-flow zones and price action"). Two engines, each named strategy a preset:

  zone_flow.py  zones where size traded before a move (impulse base / volume
                node) + a trigger candle + order flow on it
  level_pa.py   1-hour swing levels and the previous day's high/low + a candle
                pattern, with structure and session context

Every preset's defaults are the setup the research found working in both
Jan-Jun and Jul-Nov 2026; the comment above each gives its numbers.

Field values
    candle   any | pin | engulf | doji | rejection (pin or engulf) | double
             | rejection_double | sweep
    flow     none | absorption | delta_flip | big_trades_with | approach_exhausted
             | absorb_or_flip | absorb_and_flip
    context  none | big_move_in | fresh_zone | strong_zone | big_trades_in_zone
             | origin_zone | hvn_zone | prev_day_level | with_trend | against_trend
             | reversal | retest
    session  all_day | london_ny (07:00-20:00 UTC) | ny_only (13:30-20:00 UTC)
    target   r1 | r1_5 | r2 | r3 | zone (next opposing zone) | level (next level)
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AVZoneFlowParams:
    timeframe: str = "M5"
    candle: str = "rejection"
    flow: str = "delta_flip"
    context: str = "none"
    session: str = "all_day"
    target: str = "r2"
    impulse_atr: float = 2.0
    """An impulse: the close at least this many ATR beyond the base's next open, within 3 bars."""
    max_zone_width_atr: float = 1.2
    zone_max_age_days: float = 5.0
    max_hold_hours: float = 24.0
    """A trade still open after this long is closed (the research's one-day horizon)."""
    stop_buffer_atr: float = 0.1
    min_risk_atr: float = 0.3


# Golden Engulf Flip - gold M15: engulfing + delta flip at a high-volume zone, 3R.
# Jan-Nov 2026 (futures): 66 trades, 44% win, PF 2.28 (2.74 / 1.87), max DD 5.1R.
@dataclass
class AVGoldenEngulfFlipParams(AVZoneFlowParams):
    timeframe: str = "M15"
    candle: str = "engulf"
    flow: str = "delta_flip"
    context: str = "hvn_zone"
    session: str = "all_day"
    target: str = "r3"


# Golden Twin Absorb - gold M5: double top/bottom + absorption, London-NY, 1R.
# 132 trades, 63% win, PF 1.56 (1.53 / 1.60), max DD 5.9R.
@dataclass
class AVGoldenTwinAbsorbParams(AVZoneFlowParams):
    timeframe: str = "M5"
    candle: str = "double"
    flow: str = "absorption"
    context: str = "none"
    session: str = "london_ny"
    target: str = "r1"


# Nasdaq Rejection Flip - Nasdaq M5: pin/engulfing + delta flip at a strong zone, NY, 2R.
# 87 trades, 49% win, PF 2.03 (2.05 / 2.01), max DD 8.2R.
@dataclass
class AVNasdaqRejectionFlipParams(AVZoneFlowParams):
    timeframe: str = "M5"
    candle: str = "rejection"
    flow: str = "delta_flip"
    context: str = "strong_zone"
    session: str = "ny_only"
    target: str = "r2"


# S&P Fresh Doji - S&P M15: doji + absorption or delta flip at a first-touch zone, 1.5R.
# 66 trades, 55% win, PF 1.64 (1.65 / 1.63), max DD 5.2R.
@dataclass
class AVSPFreshDojiParams(AVZoneFlowParams):
    timeframe: str = "M15"
    candle: str = "doji"
    flow: str = "absorb_or_flip"
    context: str = "fresh_zone"
    session: str = "all_day"
    target: str = "r1_5"


# S&P Twin Rejection - S&P M15: double top/bottom + rejection, big trades with it, London-NY, 2R.
# 85 trades, 49% win, PF 1.59 (1.69 / 1.50), max DD 6.2R.
@dataclass
class AVSPTwinRejectionParams(AVZoneFlowParams):
    timeframe: str = "M15"
    candle: str = "rejection_double"
    flow: str = "big_trades_with"
    context: str = "none"
    session: str = "london_ny"
    target: str = "r2"


# Silver Absorb Flip - silver M5: absorption + delta flip at a zone, London-NY, next zone.
# 72 trades, 54% win, PF 1.89 (2.04 / 1.69), max DD 5.8R.
@dataclass
class AVSilverAbsorbFlipParams(AVZoneFlowParams):
    timeframe: str = "M5"
    candle: str = "any"
    flow: str = "absorb_and_flip"
    context: str = "none"
    session: str = "london_ny"
    target: str = "zone"


# Dow Big-Trade Pin - US30 M5: pin + big trades with it at a high-volume zone, NY, 3R.
# 85 trades, 39% win, PF 1.74 (1.88 / 1.56), max DD 11.5R.
@dataclass
class AVDowBigTradePinParams(AVZoneFlowParams):
    timeframe: str = "M5"
    candle: str = "pin"
    flow: str = "big_trades_with"
    context: str = "hvn_zone"
    session: str = "ny_only"
    target: str = "r3"


# Dow Absorption Zone - US30 M15: absorption at a high-volume zone, NY, 1.5R.
# 92 trades, 53% win, PF 1.52 (1.55 / 1.46), max DD 5.2R.
@dataclass
class AVDowAbsorptionZoneParams(AVZoneFlowParams):
    timeframe: str = "M15"
    candle: str = "any"
    flow: str = "absorption"
    context: str = "hvn_zone"
    session: str = "ny_only"
    target: str = "r1_5"


@dataclass
class AVLevelPAParams:
    timeframe: str = "M15"
    candle: str = "engulf"
    flow: str = "none"
    context: str = "none"
    session: str = "all_day"
    target: str = "r2"
    level_max_age_days: float = 10.0
    retest_window_bars: int = 24
    max_hold_hours: float = 24.0
    stop_buffer_atr: float = 0.1
    min_risk_atr: float = 0.3


# Nasdaq Prev-Day Doji - Nasdaq M15: doji at the previous day's high/low, 1R.
# Deriv prices: 111 trades, 61% win, PF 1.45 (1.47 / 1.43), max DD 4.4R.
@dataclass
class AVNasdaqPrevDayDojiParams(AVLevelPAParams):
    timeframe: str = "M15"
    candle: str = "doji"
    context: str = "prev_day_level"
    session: str = "all_day"
    target: str = "r1"


# Dow Doji Retest - US30 M5: doji on a break-and-retest of a level, next level.
# Deriv prices: 107 trades, 36% win, PF 1.51 (1.58 / 1.40), max DD 16.3R.
@dataclass
class AVDowDojiRetestParams(AVLevelPAParams):
    timeframe: str = "M5"
    candle: str = "doji"
    context: str = "retest"
    session: str = "all_day"
    target: str = "level"


# Oil Trend Engulf - US oil M5: engulfing at a level with the 1-hour trend, London-NY, 2R.
# Deriv prices: 84 trades, 55% win, PF 1.87 (1.83 / 1.91), max DD 4.6R.
@dataclass
class AVOilTrendEngulfParams(AVLevelPAParams):
    timeframe: str = "M5"
    candle: str = "engulf"
    context: str = "with_trend"
    session: str = "london_ny"
    target: str = "r2"


# Oil Retest Doji - US oil M15: doji on a break-and-retest, 3R.
# Futures prices: 104 trades, 40% win, PF 1.62 (1.26 / 2.14), max DD 11.2R.
@dataclass
class AVOilRetestDojiParams(AVLevelPAParams):
    timeframe: str = "M15"
    candle: str = "doji"
    context: str = "retest"
    session: str = "all_day"
    target: str = "r3"


# Nikkei Trend Engulf - Japan 225 M15: engulfing at a level with the 1-hour trend, 1R.
# Deriv prices: 60 trades, 63% win, PF 1.54 (1.55 / 1.53), max DD 3.3R.
@dataclass
class AVNikkeiTrendEngulfParams(AVLevelPAParams):
    timeframe: str = "M15"
    candle: str = "engulf"
    context: str = "with_trend"
    session: str = "all_day"
    target: str = "r1"


# DAX Retest Rejection - Germany 40 M15: pin/engulfing on a break-and-retest, London-NY, 3R.
# Deriv prices: 128 trades, 37% win, PF 1.38 (1.34 / 1.43), max DD 10.5R.
@dataclass
class AVDAXRetestRejectionParams(AVLevelPAParams):
    timeframe: str = "M15"
    candle: str = "rejection"
    context: str = "retest"
    session: str = "london_ny"
    target: str = "r3"


# Cable Liquidity Sweep - GBP/USD M15: sweep of the previous day's high/low + big trades with it, 2R.
# Futures prices and costs: 82 trades, 52% win, PF 1.67 (1.57 / 1.82), max DD 7.2R.
# Needs a broker with futures-like FX spreads; Deriv's spread eats the edge.
@dataclass
class AVCableLiquiditySweepParams(AVLevelPAParams):
    timeframe: str = "M15"
    candle: str = "sweep"
    flow: str = "big_trades_with"
    context: str = "prev_day_level"
    session: str = "all_day"
    target: str = "r2"
