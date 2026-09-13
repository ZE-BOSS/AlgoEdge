"""
backend/strategies/windows.py

How many closed bars per timeframe a strategy engine is handed.

[P1.8] This existed twice, with different values. The backtest route sliced M5
to the last 500 bars (`TF_META[...]["window"]`, chosen so a multi-thousand-bar
run does not recompute EWM/ADX over 5,000 rows on every single bar), while the
live scan loop fetched and passed 5,000. Any indicator whose value depends on
how much history it can see — ADX, a long EMA, a percentile over a lookback —
therefore computed something different live than it did in the backtest, on
identical data, with no way to notice.

The numbers below are the backtest's, because they are the ones every published
result was produced with, and because trimming live to match is both the
parity-preserving direction and the faster one. Change them in one place or not
at all.
"""

# Bars of history handed to a strategy per timeframe.
STRATEGY_WINDOW_BARS: dict[str, int] = {
    "M1": 500,
    "M5": 500,
    "M15": 300,
    "M30": 200,
    "H1": 200,
    "H4": 200,
    "D1": 100,
}

DEFAULT_WINDOW_BARS = 500


def window_bars(timeframe: str, strategy: object | None = None) -> int:
    """Bars of history for `timeframe`; the M5 value for anything unrecognised.

    A strategy may declare `WINDOW_BARS = {"H1": 1500}` when its indicators
    need more history than the shared table gives — a 200-bar EMA seeded on a
    200-bar window still carries ~13% of its starting value, so it would not be
    the EMA the research measured. The larger of the two wins, on BOTH paths.
    """
    base = STRATEGY_WINDOW_BARS.get(str(timeframe).upper(), DEFAULT_WINDOW_BARS)
    extra = (getattr(strategy, "WINDOW_BARS", None) or {}).get(str(timeframe).upper(), 0)
    return max(base, int(extra))


_TF_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}


def warmup_days(timeframe: str, strategy: object | None, base_days: float) -> float:
    """Calendar days of history to fetch before a backtest starts so the first
    bar already has `window_bars(timeframe, strategy)` bars behind it. Markets
    close at weekends and some trade ~23h, hence the 1.6x slack."""
    bars = window_bars(timeframe, strategy)
    need = bars * _TF_MINUTES.get(str(timeframe).upper(), 5) / 1440.0 * 1.6 + 3
    return max(float(base_days), need)
