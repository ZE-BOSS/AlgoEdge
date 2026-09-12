"""
tests/test_opposite_side.py — [P2]

"Catch sells on Crash and buys on Boom."

DriftJumpAlpha (Crash) and BoomDriftJump (Boom) both carry a jump-entry side
that is switched off by default. These tests lock down that both sides exist and
are reachable from the Backtester. (SpikeFade / SpikeRide were removed with the
other synthetic template strategies on 2026-09-11.)
"""


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


def test_both_jump_strategies_are_wired_into_the_backtester_param_routing():
    from backend.api.routes.backtest import STRATEGY_PARAM_SECTION

    assert STRATEGY_PARAM_SECTION["DriftJumpAlpha_v1"] == "drift_jump_alpha"
    assert STRATEGY_PARAM_SECTION["BoomDriftJump_v1"] == "boom_drift_jump"
