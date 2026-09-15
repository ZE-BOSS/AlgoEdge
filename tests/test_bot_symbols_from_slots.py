"""The bot trades exactly the symbols that have an ENABLED strategy slot.

2026-09-15: Settings had one enabled slot (Crash 1000 Index / DriftJumpAlpha_v1)
while the Dashboard's Bot Control showed — and Start Bot sent — a hardcoded
eight-symbol list. The scan loop then gave every started symbol without a slot
a default APA_v1 strategy, so the bot would have traded APA on XAUUSD, EURUSD…
and never scanned the slot the user had enabled.
"""

import inspect
from pathlib import Path

from backend.core.config_schema import UserConfigV2
from backend.services.bot_service import BotService


def test_only_enabled_slots_count():
    cfg = UserConfigV2.from_dict({"instrument_slots": [
        {"slot_id": "a", "symbol": "XRPUSD", "strategy_id": "APA_v1", "enabled": False},
        {"slot_id": "b", "symbol": "Crash 1000 Index", "strategy_id": "DriftJumpAlpha_v1", "enabled": True},
        {"slot_id": "c", "symbol": "Boom 1000 Index", "strategy_id": "SpikeFade_v1", "enabled": False},
        {"slot_id": "d", "symbol": "Crash 1000 Index", "strategy_id": "SpikeFade_v1", "enabled": True},
    ], "symbols": ["XAUUSD", "XAGUSD", "EURUSD"]})
    assert BotService.active_slot_symbols(cfg) == ["Crash 1000 Index"]


def test_legacy_instrument_settings_still_resolve():
    cfg = UserConfigV2.from_dict({"instrument_settings": [
        {"symbol": "EURUSD", "strategy_id": "APA_v1", "enabled": True},
        {"symbol": "GBPUSD", "strategy_id": "APA_v1", "enabled": False},
    ]})
    assert BotService.active_slot_symbols(cfg) == ["EURUSD"]


def test_start_route_and_scan_loop_do_not_trade_markets_without_a_slot():
    from backend.api.routes import bot as bot_routes
    from backend.services import bot_service

    start_src = inspect.getsource(bot_routes.start_bot)
    assert "bot_service.active_slot_symbols(" in start_src
    assert "symbols = slot_symbols" in start_src, "enabled slots must override any client-sent list"

    loop_src = inspect.getsource(bot_service.BotService._scan_loop)
    assert "if getattr(config, 'instrument_slots', None):" in loop_src
    assert "self.symbols = list(_slots_by_symbol)" in loop_src


def test_dashboard_reads_the_slots_not_a_hardcoded_list():
    js = Path("frontend/src/pages/Dashboard.jsx").read_text(encoding="utf-8")
    assert "'XAUUSD', 'XAGUSD', 'XPTUSD', 'EURUSD', 'GBPUSD', 'USOIL', 'ETHUSD', 'GBPJPY'" not in js
    assert "activeSymbolsFrom(userConfig?.config)" in js
    assert "startBot({ scan_interval: 60 })" in js
