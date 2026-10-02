"""
A saved strategy-settings change must reach a running bot's engine.

Slot engines used to be built once and kept until the bot restarted, so
editing TrendBreakout / ORB (or any strategy's) parameters changed
the form and nothing else, and two bots showing identical settings could trade
different ones. bot_service now fingerprints each slot's parameter inputs every
scan and rebuilds the engine when they change. These pin the fingerprint: it
must move on a real change and stay put across reloads of the same settings.
"""

from backend.core.config_schema import UserConfigV2
from backend.services.bot_service import BotService
from backend.strategies.registry import get_strategy, list_strategies

fp = BotService._strategy_params_fingerprint


class Slot:
    def __init__(self, override=None, measured=True):
        self.strategy_params_override = override
        self.use_measured_params = measured


def _block(config, strategy_id):
    engine = get_strategy(strategy_id)(config)
    return next((n for n, v in vars(config).items() if v is not None and v is engine.params), None)


def test_every_strategy_reads_a_settings_block_the_bot_can_watch():
    config = UserConfigV2()
    for sid in list_strategies():
        assert _block(config, sid), f"{sid}: engine.params is not one of the config's blocks"


def test_the_fingerprint_is_stable_across_reloads_of_the_same_settings():
    saved = UserConfigV2().to_dict() if hasattr(UserConfigV2(), "to_dict") else None
    a = UserConfigV2.from_dict(saved) if saved else UserConfigV2()
    b = UserConfigV2.from_dict(saved) if saved else UserConfigV2()
    for sid in ("TrendBreakout_v1", "ORB_v1", "APA_v1", "TrendDrift_v1"):
        block = _block(a, sid)
        assert fp(a, Slot(), block) == fp(b, Slot(), block)


def test_a_global_setting_change_moves_the_fingerprint():
    config = UserConfigV2()
    block = _block(config, "TrendBreakout_v1")
    before = fp(config, Slot(), block)
    config.trend_breakout.stop_atr_multiple = 1.5
    assert fp(config, Slot(), block) != before

    config = UserConfigV2()
    block = _block(config, "ORB_v1")
    before = fp(config, Slot(), block)
    config.orb.range_minutes = 45
    assert fp(config, Slot(), block) != before


def test_a_slot_override_and_the_measured_switch_move_it_too():
    config = UserConfigV2()
    block = _block(config, "TrendBreakout_v1")
    base = fp(config, Slot(), block)
    assert fp(config, Slot({"stop_atr_multiple": 1.0}), block) != base
    assert fp(config, Slot(measured=False), block) != base
