"""
tests/test_opposite_side.py — [P2]

"Catch sells on Crash and buys on Boom."

Two of the three strategies could already do it and were switched off; the third
(SpikeFade) had no counterpart at all until SpikeRide_v1. These tests lock down
that all three sides exist, are reachable from config, and pick the correct
direction from the data rather than from a symbol lookup table.
"""

import asyncio
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from backend.core.config_schema import SynthParams, UserConfigV2
from backend.strategies.registry import get_strategy


# ── fixtures: synthetic Boom / Crash bar series ──────────────────────────────

def _series(n=400, spike_dir=-1, price=5900.0, seed=3):
    """Grind one way, spike the other — the Boom/Crash generator in miniature.

    spike_dir=-1 => Crash: grinds UP, drops violently (negative return skew).
    spike_dir=+1 => Boom:  grinds DOWN, pops violently (positive skew).
    """
    rng = np.random.default_rng(seed)
    drift = -spike_dir * 0.45
    # The jump is a PERMANENT level change that the grind then slowly gives back
    # (research/24 §3.1: a jump of ~0.100% against a grind of −0.100%/N per tick,
    # which is what keeps the process fair). Bumping close[i] alone would make
    # the next bar's return an equal and opposite move, cancelling the skew the
    # strategy reads its direction from — so the jump goes into the increment.
    inc = rng.normal(drift, 1.2, n)
    for i in range(37, n, 41):
        inc[i] += spike_dir * 26.0
    close = price + np.cumsum(inc)
    high = close + np.abs(rng.normal(1.0, 0.6, n))
    low = close - np.abs(rng.normal(1.0, 0.6, n))
    for i in range(37, n, 41):            # the jump's own wick overshoots
        if spike_dir < 0:
            low[i] = close[i] - 4.0
        else:
            high[i] = close[i] + 4.0
    idx = pd.date_range("2026-09-01", periods=n, freq="5min", tz="UTC")
    return pd.DataFrame({"open": np.r_[close[0], close[:-1]], "high": high,
                         "low": low, "close": close}, index=idx)


def _cfg(**synth):
    c = UserConfigV2()
    c.synth = SynthParams(**synth)
    c.risk = SimpleNamespace(risk_per_trade_pct=1.0)
    return c


def _run(strategy_id, df, symbol, cfg=None):
    eng = get_strategy(strategy_id)(cfg or _cfg())
    return asyncio.run(eng.on_bar(symbol, "M5", df))


# ── SpikeRide exists and takes the side SpikeFade does not ───────────────────

def test_spike_ride_is_registered():
    assert get_strategy("SpikeRide_v1").strategy_id == "SpikeRide_v1"


def test_spike_ride_sells_a_crash_like_series():
    """Crash grinds up and drops. The un-traded side is SELL."""
    df = _series(spike_dir=-1)
    sig = None
    for cut in range(200, len(df)):
        sig = _run("SpikeRide_v1", df.iloc[:cut], "Crash 1000 Index")
        if sig:
            break
    assert sig is not None, "SpikeRide produced no signal on a Crash-like series"
    assert sig.direction == "SELL"
    assert sig.stop_loss > sig.entry_price and sig.take_profit < sig.entry_price


def test_spike_ride_buys_a_boom_like_series():
    df = _series(spike_dir=+1, price=14700.0)
    sig = None
    for cut in range(200, len(df)):
        sig = _run("SpikeRide_v1", df.iloc[:cut], "Boom 1000 Index")
        if sig:
            break
    assert sig is not None, "SpikeRide produced no signal on a Boom-like series"
    assert sig.direction == "BUY"
    assert sig.stop_loss < sig.entry_price and sig.take_profit > sig.entry_price


def test_direction_comes_from_the_data_not_the_symbol_name():
    """A symbol lookup table goes stale when a broker renames an instrument.
    Feed Crash-shaped data under a Boom name; the direction must follow the data."""
    df = _series(spike_dir=-1)
    sig = None
    for cut in range(200, len(df)):
        sig = _run("SpikeRide_v1", df.iloc[:cut], "Boom 1000 Index")
        if sig:
            break
    assert sig is not None and sig.direction == "SELL"


def test_symmetric_series_produces_nothing():
    """No measurable jump asymmetry means there is no spike side to take."""
    rng = np.random.default_rng(11)
    n = 400
    close = 100 + np.cumsum(rng.normal(0, 1.0, n))
    idx = pd.date_range("2026-09-01", periods=n, freq="5min", tz="UTC")
    df = pd.DataFrame({"open": np.r_[close[0], close[:-1]],
                       "high": close + 0.5, "low": close - 0.5, "close": close}, index=idx)
    hits = [s for cut in range(200, n)
            if (s := _run("SpikeRide_v1", df.iloc[:cut], "Volatility 75 Index"))]
    assert not hits, "a symmetric instrument has no spike side; SpikeRide must abstain"


# ── the geometry must NOT be the fade's ──────────────────────────────────────

def test_spike_ride_uses_its_own_stop_and_target_not_the_shared_ones():
    """The Settings page shares one params block across the synth templates. A
    5x ATR stop against a 2x ATR spike target is a 1:0.4 proposition — sharing
    the fade's geometry would make the measurement meaningless."""
    from backend.strategies.strategy_synth.engine import SpikeFadeStrategy, SpikeRideStrategy

    assert SpikeRideStrategy.stop_param_name == "spike_ride_stop_atr"
    assert SpikeRideStrategy.tp_param_name == "spike_ride_tp_rr"
    assert SpikeFadeStrategy.stop_param_name == "stop_atr_multiple"

    cfg = _cfg(stop_atr_multiple=5.0, tp1_rr=5.0,
               spike_ride_stop_atr=1.0, spike_ride_tp_rr=2.0)
    df = _series(spike_dir=-1)
    for cut in range(200, len(df)):
        sig = _run("SpikeRide_v1", df.iloc[:cut], "Crash 1000 Index", cfg)
        if sig:
            risk = abs(sig.entry_price - sig.stop_loss)
            reward = abs(sig.take_profit - sig.entry_price)
            assert reward / risk == pytest.approx(2.0, rel=1e-6), \
                "SpikeRide must use spike_ride_tp_rr, not the shared tp1_rr"
            return
    pytest.fail("no signal produced")


def test_stretch_gate_is_configurable_and_binding():
    df = _series(spike_dir=-1)
    loose = sum(1 for cut in range(200, len(df))
                if _run("SpikeRide_v1", df.iloc[:cut], "Crash 1000 Index",
                        _cfg(spike_ride_stretch_atr=0.0, max_trades_per_day=999,
                             max_daily_risk_pct=999.0)))
    tight = sum(1 for cut in range(200, len(df))
                if _run("SpikeRide_v1", df.iloc[:cut], "Crash 1000 Index",
                        _cfg(spike_ride_stretch_atr=6.0, max_trades_per_day=999,
                             max_daily_risk_pct=999.0)))
    assert loose > tight, "a larger stretch requirement must admit fewer entries"


# ── the two sides that already existed and were switched off ────────────────

def test_drift_jump_alpha_has_a_crash_sell_side():
    """Setup B. It exists; `trade_jumps_enabled` and `control_test_passed` are
    both off by default, which is why it has never fired."""
    import inspect

    from backend.strategies.strategy_two import engine as dja

    src = inspect.getsource(dja)
    assert "SETUP B: JUMP ENTRY (SELL)" in src
    assert 'direction="SELL"' in src
    assert "trade_jumps = getattr(self.params, 'trade_jumps_enabled', False)" in src
    assert "control_test_passed" in src

    from backend.core.config_schema import DriftJumpAlphaParams
    assert DriftJumpAlphaParams().trade_jumps_enabled is False
    assert DriftJumpAlphaParams().control_test_passed is False


def test_boom_drift_jump_has_a_boom_buy_side():
    import inspect

    from backend.core.config_schema import BoomDriftJumpParams
    from backend.strategies.strategy_boom import engine as boom

    src = inspect.getsource(boom)
    assert "SETUP B: JUMP ENTRY (BUY)" in src
    assert 'direction="BUY"' in src
    assert BoomDriftJumpParams().trade_jumps_enabled is False


def test_setup_b_flags_are_reachable_from_the_backtester_payload():
    """A side you cannot switch on from the Backtester cannot be measured."""
    from pathlib import Path

    js = Path("frontend/src/pages/Backtester.jsx").read_text(encoding="utf-8")
    assert "trade_jumps_enabled" in js
    assert "control_test_passed" in js


def test_spike_ride_is_wired_into_the_backtester_param_routing():
    from backend.api.routes.backtest import STRATEGY_PARAM_SECTION

    assert STRATEGY_PARAM_SECTION["SpikeRide_v1"] == "synth"
