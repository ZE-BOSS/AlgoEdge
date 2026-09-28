"""
backend/strategies/strategy_spike_resumption/params.py

SpikeResumption_v1 — trade the drift resuming after a Boom/Crash spike is spent.

THE RULE
    On BOOM (spikes UP, drifts DOWN): an established downward drift, ONE bar
    breaks it with a sharp buy, the next bar closes bearish, SELL. On CRASH the
    exact mirror. Out on a target, a stop, or a bar count.

WHY IT IS A SEPARATE STRATEGY AND NOT A SETTING ON BoomDriftJump_v1
    That one's Setup A carries the line "Do not sell into a fresh up-spike" — it
    REFUSES this setup and takes EMA pullbacks anywhere in the drift instead.
    This one uses the spike as the trigger. Opposite premise, so: separate
    strategy.

WHAT THE MEASUREMENT SAYS — READ THIS BEFORE SIZING IT
    Implementation/SPIKE-RESUMPTION-2026-09-27.md has the full record. The short
    version, per instrument, January 2026 to date at 0.5% risk:

        Crash 1000   +11.9%   71.4% win   PF 3.51   t +2.68   <- carries it
        Crash 900     +1.0%   58.3% win   PF 1.23
        Boom 1000     -0.8%   37.5% win   PF 0.79
        Boom 900      -5.4%   23.5% win   PF 0.38   <- the worst of the eight

    And on three years the parameters were never fitted to (Boom/Crash 1000,
    2021-10 -> 2024-09) the rule loses: -0.098R per trade, -6.1%.

    So it is shipped enabled on NOTHING by default. A slot has to be created
    deliberately, and the evidence says Crash before Boom.
"""

from dataclasses import dataclass


@dataclass
class SpikeResumptionParams:
    spike_atr_multiple: float = 3.0
    """How big a bar has to be, in ATR, to count as the spike. Measured on the
    strategy's own M15 ATR(`atr_period`). 2.0 floods the strategy with ordinary
    bars and loses money (-0.026R over 2,859 trades); 5.0 almost never fires."""

    spike_metric: str = "body"
    """What "big" means. "body" is close minus open — a spike that ENDS the bar
    far from where it started, which is what the eye picks out on a Boom chart.
    "range" measures high minus open instead, so a spike that is faded inside its
    own bar still counts. "either" takes the larger of the two."""

    trend_mode: str = "below"
    """How "an established drift" is read, against the spike direction:
    "below" — the close has stayed the wrong side of the slow EMA for 20 straight
    bars. Strictest, fewest trades, measured best.
    "ema" — fast EMA the wrong side of slow. The usual regime test.
    "ret" — the last `trend_lookback` bars' return runs against the spike."""

    trend_lookback: int = 50
    """Bars in the trend test's EMA or return window."""

    confirm_bars: int = 1
    """Completed bars that must close AGAINST the spike before entering. Your
    rule is one. Two raises the win rate and lowers the payoff, netting out
    slightly worse."""

    require_below_spike: bool = False
    """Also require the confirming bar to close back past where the spike STARTED
    — the strict reading of "it must come below the last buy spike". Fewer,
    later entries."""

    min_bars_since_spike: int = 0
    """Ignore a spike that comes within this many bars of the previous one: a
    cluster of spikes is one event, not several setups. 0 = off."""

    stop_atr_multiple: float = 1.0
    """Stop distance in ATR, measured at the SPIKE bar rather than at entry —
    the spike is what set the volatility this trade is being sized against.
    This is R, so it also sets the position size."""

    max_hold_bars: int = 5
    """Out at this many bars after entry whatever the price. You observed the
    move running about 3 bars, sometimes 4 or 5; 10 measured clearly worse
    (-0.048R), which says the edge is over quickly if it was ever there."""

    atr_period: int = 14
    """Bars in the ATR that sets both the spike threshold and the stop."""
