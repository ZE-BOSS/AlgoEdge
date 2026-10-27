"""
A slot's own settings must reach the live bot (2026-10-27, live $1k account).

1. Per-slot STRATEGY settings: the slot editor saves them as `strategy_params`,
   InstrumentSlot keeps `strategy_params_override`, and the loader dropped the
   unknown key - every per-symbol strategy setting silently ran the default
   (the live log: "max_daily_risk_pct (4%)" on a slot saved with 20).
2. Per-slot RISK limits: the editor saves them in the slot's `risk` block, the
   live circuit-breaker gate read only the legacy named fields and fell back to
   the account's 1 position per symbol - pyramiding never stacked.
3. A symbol the broker has in close-only mode is refused before any order is
   sent, with the broker's reason (Vol over Crash 550/750 on Deriv).
"""

import asyncio
import collections
import copy
import types

from backend.core.config_schema import UserConfigV2
from backend.services.bot_service import BotService


def _load(slot):
    return UserConfigV2.from_dict(copy.deepcopy({"instrument_slots": [slot]}))


def test_the_editors_strategy_params_become_the_slots_overrides():
    cfg = _load({"slot_id": "s1", "symbol": "Vol over Crash 750", "strategy_id": "DriftJumpAlpha_v1",
                 "strategy_params": {"max_daily_risk_pct": 20, "tp1_rr_override": 0.5}})
    assert cfg.instrument_slots[0].strategy_params_override == {"max_daily_risk_pct": 20, "tp1_rr_override": 0.5}


def test_the_editors_value_wins_over_an_older_override_key():
    cfg = _load({"slot_id": "s1", "symbol": "X", "strategy_id": "DriftJumpAlpha_v1",
                 "strategy_params_override": {"max_daily_risk_pct": 4, "max_trades_per_day": 9},
                 "strategy_params": {"max_daily_risk_pct": 20}})
    assert cfg.instrument_slots[0].strategy_params_override == {"max_daily_risk_pct": 20, "max_trades_per_day": 9}


def test_a_slot_without_editor_params_is_unchanged():
    cfg = _load({"slot_id": "s1", "symbol": "X", "strategy_id": "DriftJumpAlpha_v1",
                 "strategy_params_override": {"max_daily_risk_pct": 7}})
    assert cfg.instrument_slots[0].strategy_params_override == {"max_daily_risk_pct": 7}


def test_the_slots_risk_block_wins_over_the_account_default():
    cfg = UserConfigV2.from_dict(copy.deepcopy({
        "risk": {"max_positions_per_symbol": 1, "allow_pyramiding": False, "risk_per_trade_pct": 1.0},
        "instrument_slots": [{"slot_id": "s1", "symbol": "Boom 900 Index", "strategy_id": "BoomDriftJump_v1",
                              "risk": {"max_positions_per_symbol": 4, "allow_pyramiding": True,
                                       "risk_per_trade_pct": 4}}]}))
    slot = cfg.instrument_slots[0]
    assert BotService._slot_risk(slot, cfg, "max_positions_per_symbol", 1) == 4
    assert BotService._slot_risk(slot, cfg, "allow_pyramiding") is True
    assert BotService._slot_risk(slot, cfg, "risk_per_trade_pct") == 4
    # nothing set on the slot -> the account's value
    assert BotService._slot_risk(slot, cfg, "max_daily_trades") == cfg.risk.max_daily_trades


def test_a_legacy_named_slot_field_still_counts():
    cfg = UserConfigV2.from_dict(copy.deepcopy({
        "risk": {"max_positions_per_symbol": 1},
        "instrument_slots": [{"slot_id": "s1", "symbol": "X", "strategy_id": "VWAP_v1",
                              "max_positions_per_symbol": 3}]}))
    assert BotService._slot_risk(cfg.instrument_slots[0], cfg, "max_positions_per_symbol", 1) == 3


def test_the_live_gates_use_the_slot_values():
    import inspect
    src = inspect.getsource(BotService)
    gate = src[src.index("cb_ok, cb_reason = _slot_circuit.check_symbol(") - 200:]
    assert 'self._slot_risk(slot, config, "max_positions_per_symbol", 1)' in gate[:600]
    assert '_signal_metadata["slot_max_positions"] = self._slot_risk(' in src


Info = collections.namedtuple("Info", "trade_mode")


def _fake_mt5(mode):
    return types.SimpleNamespace(symbol_info=lambda s: Info(mode))


def test_close_only_and_disabled_symbols_are_named(monkeypatch):
    from backend.mt5 import order_manager as om
    monkeypatch.setattr(om, "mt5", _fake_mt5(3))
    assert "CLOSE-ONLY" in om.OrderManager.entry_block_reason("Vol over Crash 750", "BUY")
    monkeypatch.setattr(om, "mt5", _fake_mt5(0))
    assert "DISABLED" in om.OrderManager.entry_block_reason("X", "SELL")
    monkeypatch.setattr(om, "mt5", _fake_mt5(1))
    assert "LONG positions only" in om.OrderManager.entry_block_reason("X", "SELL")
    assert om.OrderManager.entry_block_reason("X", "BUY") is None
    monkeypatch.setattr(om, "mt5", _fake_mt5(4))
    assert om.OrderManager.entry_block_reason("X", "SELL") is None
