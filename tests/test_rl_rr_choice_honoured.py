"""The RL agent's reward:risk choice must reach the engine.

Until 2026-10-16 `MultiTPManager.calculate_tp_levels` always used the
slot's tp1_rr, so an agent that chose 1.5R traded the slot's 3R ladder in
training, in the gate and live - and its risk head learned from noise.
"""

from backend.risk.multi_tp import MultiTPManager


def _calc(**cfg):
    base = {"tp1_rr": 3.0, "tp_count": 3, "tp2_rr": 5.0, "tp3_rr": 10.0,
            "multi_position_mode": True, "tp_splits": "50,30,20"}
    base.update(cfg)
    return MultiTPManager(base)


def test_the_signals_own_target_replaces_the_slot_ladder():
    calc = _calc()
    levels = calc.calculate_tp_levels(100.0, 99.0, "BUY", 1.0, "EURUSD",
                                      strategy_id="RL::x", use_live_mt5=False,
                                      signal_tp1_rr=1.5)
    assert len(levels) == 1
    assert abs(levels[0].tp_price - 101.5) < 1e-9
    assert levels[0].rr_multiplier == 1.5


def test_without_a_signal_target_the_slot_ladder_is_unchanged():
    calc = _calc()
    levels = calc.calculate_tp_levels(100.0, 99.0, "BUY", 1.0, "EURUSD",
                                      strategy_id="ORB_v1", use_live_mt5=False)
    assert abs(levels[0].tp_price - 103.0) < 1e-9
    assert len(levels) > 1
