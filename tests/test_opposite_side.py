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
    """A side you cannot switch on from the Backtester cannot be measured.

    The Backtester no longer hardcodes strategy fields: since 2026-09-20 each slot
    renders its strategy's whole schema group, so "reachable" now means the flags
    are IN that group and the editor renders the group for these strategies.
    """
    from pathlib import Path

    from backend.core.schema_introspection import build_full_schema

    schema = build_full_schema()
    keys = {f["key"] for f in schema}
    for key in ("drift_jump_alpha.trade_jumps_enabled",
                "drift_jump_alpha.control_test_passed",
                "boom_drift_jump.trade_jumps_enabled"):
        assert key in keys, f"{key} is not in the parameter schema, so no form can render it"

    spec = Path("frontend/src/components/slotSpec.js").read_text(encoding="utf-8")
    for strategy, group in (("DriftJumpAlpha_v1", "drift_jump_alpha"),
                            ("BoomDriftJump_v1", "boom_drift_jump")):
        assert f"'{strategy}'" in spec and f"'{group}'" in spec,             f"{strategy} must map to its schema group or its slot shows the wrong fields"

    editor = Path("frontend/src/components/SlotEditor.jsx").read_text(encoding="utf-8")
    assert "const group = STRATEGY_GROUP[slot.strategy_id] || 'apa';" in editor
    assert "group={group}" in editor, "the slot editor must render the strategy's own schema group"

    js = Path("frontend/src/pages/Backtester.jsx").read_text(encoding="utf-8")
    assert "<SlotEditor" in js, "the Backtester must edit its run through the slot editor"


def test_both_jump_strategies_are_wired_into_the_backtester_param_routing():
    from backend.api.routes.backtest import STRATEGY_PARAM_SECTION

    assert STRATEGY_PARAM_SECTION["DriftJumpAlpha_v1"] == "drift_jump_alpha"
    assert STRATEGY_PARAM_SECTION["BoomDriftJump_v1"] == "boom_drift_jump"
