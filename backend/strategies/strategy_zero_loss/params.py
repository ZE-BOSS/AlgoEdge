"""
backend/strategies/strategy_zero_loss/params.py

Zero Loss Boom & Crash v3.2 - the TradingView strategy
("Zero Loss Boom & Crash — strategy v3.2") and its MT5 EA, same defaults.
"""

from dataclasses import dataclass


@dataclass
class ZeroLossBoomCrashParams:
    """Zero Loss Boom & Crash v3.2 - M5. Every default is the Pine script's."""
    timeframe: str = "M5"
    red_len: int = 30
    """Red MA period (EMA of close)."""
    black_len: int = 5
    """Black MA period (EMA of close), scaled 0-100 to the last n_bars' high/low."""
    lvl_sell: float = 80.0
    lvl_buy: float = 20.0
    n_bars: int = 100
    boom_buy: bool = True
    """Boom BUY  (black at 20, closes above red - spike side)."""
    boom_sell: bool = True
    """Boom SELL (black at 80, closes below red - video trade)."""
    crash_buy: bool = True
    """Crash BUY (black at 20, closes above red - video trade)."""
    crash_sell: bool = True
    """Crash SELL (black at 80, closes below red - spike side)."""
    zone_mem: int = 5
    """The black line touched the zone within the last N candles."""
    turn_n: int = 3
    """The black line moving back out of the zone for N candles in a row (0 = off)."""
    cross_mode: str = "body"
    """"body": the candle opens on one side of the red line and closes on the other;
    "close": the close crosses the red line."""
    exit_mode: str = "zone"
    """"zone": hold until the black line reaches the opposite zone (max hold_bars);
    "fixed": hold hold_bars candles; "red": exit on a close back across the red line."""
    hold_bars: int = 100
    min_hold: int = 1
    """Red-line exit: earliest after N candles."""
    sl_mode: str = "atr"
    """"atr" (sl_atr x ATR(atr_len)), "points" (sl_points) or "none" (a far crash
    stop at 3x the ATR distance is sent; the ATR distance still sizes)."""
    atr_len: int = 14
    sl_atr: float = 8.0
    sl_points: float = 20.0
    tp_r: float = 0.0
    """Take profit at this R multiple (0 = off, the Pine default)."""
    trade_min_lot: bool = True
    """Pine "If the risk-based lot is below the minimum: Trade the minimum lot"."""
