"""
backend/strategies/strategy_roboquant/params.py

Parameters for the five strategies replicated from roboquant.dev's template
library (read 2026-10-19). Defaults are the SHOWCASE presets each template's
published results were produced with - which in two cases (T-Mean Reversion,
Crazy ORB) differ from the defaults in the template's own source code; the
showcase preset is what the claimed numbers belong to.

Position sizing is NOT copied: roboquant runs fixed futures contracts or a
fixed dollar risk; here the app's risk engine sizes every trade from the stop
(risk % of balance), so results are compared in R and win rate, not dollars.
"""

from dataclasses import dataclass


@dataclass
class RQNQPrecisionParams:
    """NQ Precision Bot - NQ 1m preset. Claimed: +1,377%, 94.0% win, DD 32.8%, PF 2.48."""
    timeframe: str = "M1"
    range_start: int = 930
    """Opening range from 09:30 ET (inclusive) ..."""
    range_end: int = 945
    """... to 09:45 ET (exclusive), built from 1-minute bars."""
    trigger_hhmm: int = 959
    """The 09:59:55 ET snapshot, taken here at the close of the 09:59 bar."""
    atr_period: int = 14
    atr_sl_mult: float = 2.2
    atr_tp_mult: float = 0.3
    close_after_min: int = 8
    """Time close: flatten after this many minutes if neither SL nor TP filled."""
    use_filters: bool = True
    rvol_len: int = 14
    rvol_min: float = 0.5
    rvol_max: float = 1.5
    atr_regime_len: int = 50
    regime_min: float = 1.0
    regime_max: float = 2.5


@dataclass
class RQNYORBParams:
    """NY Open Range Breakout - MNQ 15m preset. Claimed: +1,909%, 74.0% win, DD 42.1%, PF 1.22."""
    timeframe: str = "M15"
    range_bars: int = 1
    """Opening range = this many bars from 09:30 ET (1 x 15m = 09:30-09:45)."""
    atr_period: int = 14
    atr_stop_mult: float = 0.4
    rr_target: float = 0.4
    max_stop_pts: float = 0.0
    """0 = no cap on the stop distance in points."""
    use_rvol: bool = True
    min_rvol: float = 1.0
    rvol_period: int = 20
    use_atr_regime: bool = True
    min_atr_pct: float = 0.10
    max_atr_pct: float = 0.60
    cutoff_hhmm: int = 1500
    """No new entries from this ET time."""
    flat_hhmm: int = 1555
    """Flatten any open position from this ET time."""
    allow_long: bool = True
    allow_short: bool = True


@dataclass
class RQCrazyORBParams:
    """Crazy ORB - NQ 15m preset. Claimed: +594%, 81.3% win, DD 19.0%, PF 1.57."""
    timeframe: str = "M15"
    range_start_hhmm: int = 915
    range_end_hhmm: int = 930
    atr_period: int = 14
    atr_mult: float = 1.0
    rr: float = 0.35
    max_trades: int = 2
    """Per day; each side (long / short) at most once."""
    trade_start_hhmm: int = 930
    trade_end_hhmm: int = 1545
    flatten_hhmm: int = 1555


@dataclass
class RQTMeanReversionParams:
    """T-Mean Reversion - MNQ 1h preset. Claimed: +708%, 63.4% win, DD 27.7%, PF 2.58."""
    timeframe: str = "H1"
    entry_day: int = 5
    """ISO weekday (Mon=1 ... Fri=5), UTC calendar."""
    entry_hour: int = 18
    entry_minute: int = 0
    exit_day: int = 3
    exit_hour: int = 10
    exit_minute: int = 0
    disaster_stop_pct: float = 5.0
    """The template has NO stop. The app's risk engine sizes from a stop, so a
    far one is placed to size the trade and protect against a crash; in the
    measured period it was never the exit."""


@dataclass
class RQCharlesMongerParams:
    """Charles Monger Investment Strategy - ES 1h preset. Claimed: +1,116%, 24.9% win, DD 55.5%, PF 1.32."""
    timeframe: str = "H1"
    length: int = 200
    top_source: str = "high"
    bot_source: str = "close"
    long_only: bool = True
    stop_loss_percent: float = 1.0


# ── Added 2026-10-20: nine more templates, ported line for line through
#    strategy_roboquant/replay.py. Every default below is the template's
#    SHOWCASE preset, read from its published backtest's parameters
#    (research/roboquant/source/templates_dump.json). Point values (trail
#    trigger, target points) are in the instrument's price points, as upstream
#    on NQ/MNQ - US Tech 100 / NDX100 quote the same index in the same points.


@dataclass
class RQFlashTriggerORBParams:
    """The Flash Trigger ORB - MNQ 15m preset. Claimed: +195.1%, 61.6% win, DD 11.1%, PF 1.36."""
    timeframe: str = "M15"
    target_hour: int = 14
    target_minute: int = 30
    """The targeted candle, at this local time (UTC + utc_offset)."""
    utc_offset: int = 0
    risk_reward: float = 3.0
    eod_close_hour: int = 23
    eod_close_minute: int = 59
    """UTC. On 15-minute bars 23:59 is never reached, so - as upstream - trades
    are not flattened and can run into the next day."""
    activate_trailing: bool = True
    trail_activate_points: float = 70.0
    trail_buffer_points: float = 25.0
    day_filter: bool = False
    trade_monday: bool = True
    trade_tuesday: bool = True
    trade_wednesday: bool = True
    trade_thursday: bool = True
    trade_friday: bool = True


@dataclass
class RQScarfaceORBParams:
    """Scarface ORB - MNQ 1m preset. Claimed: +48.4%, 41.5% win, DD 7.7%, PF 1.35."""
    timeframe: str = "M1"
    duration_minutes: int = 5
    """Opening range from 09:30 ET for this many minutes."""
    wick_body_ratio: float = 0.09
    wick_ratio: float = 1.5
    sl_mode: int = 1
    """0 = beyond the momentum candle, 1 = ATR multiple."""
    atr_multiplier: float = 1.4
    atr_period: int = 14
    rr_ratio: float = 2.0
    tick_size: float = 0.25
    """Candle-stop buffer is a quarter of this (sl_mode 0 only)."""


@dataclass
class RQIVBParams:
    """IVB - Initial Volatility Breakout - MNQ 5m preset. Claimed: +309.8%, 52.0% win, DD 20.1%, PF 1.37."""
    timeframe: str = "M5"
    session_start_hhmm: int = 930
    session_end_hhmm: int = 1000
    """Initial balance 09:30-10:00 ET."""
    entry_window_end_hhmm: int = 1600
    direction: int = 0
    """0 = both, 1 = long only, 2 = short only."""
    max_contraction_ratio: float = 0.9
    min_contraction_ratio: float = 0.0
    ib_range_lookback: int = 10
    vol_surge_mult: float = 1.1
    vol_lookback: int = 5
    atr_period: int = 14
    sl_atr_mult: float = 3.0
    tp_atr_mult: float = 4.0
    flatten_eod: bool = False
    eod_flatten_hhmm: int = 1600
    max_trades_per_day: int = 2
    """Attempts: as upstream, every volume-surge bar inside the contraction band
    uses one, whether or not it closed beyond the IB."""


@dataclass
class RQGigaORBParams:
    """Giga ORB - MNQ 15m preset. Claimed: +477.1%, 55.9% win, DD 21.5%, PF 1.22."""
    timeframe: str = "M15"
    or_start_hhmm: int = 930
    or_minutes: int = 15
    last_entry_hhmm: int = 1400
    target_mode: int = 0
    """0 = Reward:Risk, 1 = fixed points."""
    rr: float = 1.0
    target_points: float = 40.0
    max_trades_per_day: int = 1
    allow_long: bool = True
    allow_short: bool = True
    use_wick_filter: bool = True
    max_wick_pct: float = 13.0
    close_at_end: bool = False
    use_daily_bias: bool = True
    bias_check_hhmm: int = 915


@dataclass
class RQSigmaBreakoutParams:
    """Sigma Breakout - MNQ 30m preset. Claimed: +440.9%, 55.3% win, DD 19.7%, PF 1.39."""
    timeframe: str = "M30"
    n_sigma_days: int = 2
    band_mult: float = 1.5
    use_vwap: bool = False
    sl_pct: float = 0.0
    """0 = no stop (the showcase): exits are the opposite band and 16:00 ET."""
    disaster_stop_pct: float = 2.0
    """Only when sl_pct is 0: the stop the app sizes from and keeps as a crash
    guard. The template's own exits come first in practice."""


@dataclass
class RQDailyAnchoredVWAPParams:
    """Daily Anchored VWAP Signal Strategy - MES 3m preset. Claimed: +299.9%, 56.6% win, DD 22.0%, PF 1.84."""
    timeframe: str = "M1"
    """Fed one-minute bars; the template steps on bar_minutes bars built from them."""
    bar_minutes: int = 3
    use_typical_price: bool = True
    session_start_hhmm: int = 930
    session_end_hhmm: int = 1600
    ema_length: int = 11
    atr_period: int = 14
    atr_stop_multiplier: float = 2.0
    use_atr_stop: bool = False
    use_relative_volatility_filter: bool = True
    baseline_atr_period: int = 50
    max_relative_atr: float = 1.1
    additional_attempts: int = 2
    disaster_stop_pct: float = 2.0
    """Only when use_atr_stop is off: the stop the app sizes from."""


@dataclass
class RQFridayRevisitParams:
    """Friday Range Revisit - Monday Short - NQ 5m preset. Claimed: +573.3%, 22.6% win, DD 15.2%, PF 2.96."""
    timeframe: str = "M5"
    upper_frac: float = 0.1
    sl_mode: int = 1
    """0 = above Thursday's RTH high (+ buffer), 1 = daily ATR multiple."""
    sl_buffer_pts: float = 0.0
    atr_sl_mult: float = 0.1
    tp_mode: int = 1
    """0 = Friday's RTH low, 1 = daily ATR multiple."""
    atr_tp_mult: float = 1.0
    atr_period: int = 14
    eod_hhmm: int = 1600


@dataclass
class RQM30ORBParams:
    """M30 ORB + M5 Breakout - MNQ 5m preset. Claimed: +345.7%, 47.6% win, DD 33.7%, PF 1.22."""
    timeframe: str = "M5"
    or_hour: int = 9
    or_min: int = 30
    or_len: int = 30
    max_trades: int = 1
    stop_mode: int = 1
    """0 = the breakout candle's low/high, 1 = ATR multiple."""
    atr_period: int = 14
    atr_mult: float = 3.0
    rr: float = 1.5
    use_atr_regime: bool = True
    daily_atr_period: int = 14
    daily_atr_lookback: int = 20
    min_atr_ratio: float = 0.0
    min_atr_pts: float = 0.0
    use_rvol: bool = True
    rvol_lookback: int = 20
    min_rvol: float = 1.4
    allow_long: bool = True
    allow_short: bool = True
    flatten_eod: bool = True
    flatten_hour: int = 15
    flatten_min: int = 0


@dataclass
class RQSRSweepParams:
    """S&R Sweep - NQ 5m preset. Claimed: +57.0%, 52.1% win, DD 11.0%, PF 1.25."""
    timeframe: str = "M5"
    left_bars: int = 3
    right_bars: int = 3
    ema_len: int = 30
    rr_ratio: float = 1.0
    sl_buf_ticks: int = 0
    tick_size: float = 0.25
    allow_long: bool = True
    allow_short: bool = True
