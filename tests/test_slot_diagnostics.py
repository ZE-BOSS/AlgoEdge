"""
tests/test_slot_diagnostics.py

"Why isn't this slot trading?" — the three answers, kept apart.

WHY THIS EXISTS
---------------
Demo and live were taking different trades from what looked like the same
settings, and the system could not say why. Three completely different
situations presented identically from outside:

  * the slot is not being scanned at all — a CONFIGURATION answer;
  * it is scanning and the strategy finds nothing — a MARKET answer;
  * it is finding setups and something refuses every one — a RISK answer,
    and the reasons were already recorded but never surfaced.

Telling them apart is the whole feature, so that is what these tests check.
The fourth case — an engine still running for a slot that has been edited out
of the config — is here too, because a stale engine is the one that looks most
like a ghost.
"""

from __future__ import annotations

import pytest

from backend.core.config_schema import InstrumentSlot, UserConfigV2


class Bot:
    """The parts of `bot_service` the diagnostic reads, and nothing else."""

    def __init__(self, running=True, account=123):
        self.running = running
        self.account_id = account
        self._slot_activity = {}
        self._suppression_funnel = {}
        self.slot_book = None

    # the two methods under test are taken from the real service
    from backend.services.bot_service import BotService  # noqa: N813
    _slot_seen = BotService._slot_seen
    slot_diagnostics = BotService.slot_diagnostics


def config(*pairs):
    cfg = UserConfigV2()
    cfg.instrument_slots = [
        InstrumentSlot(slot_id=f"slot{i}", symbol=sym, strategy_id=strat)
        for i, (sym, strat) in enumerate(pairs)
    ]
    return cfg


BOOM_FADE = ("Boom 900 Index", "SpikeFade_v1")
BOOM_DRIFT = ("Boom 900 Index", "BoomDriftJump_v1")


def test_a_slot_that_was_never_scanned_says_so():
    """The configuration answer. This is the one the dashboard could not show:
    a slot with no activity produced no rows anywhere, which looked exactly
    like a slot that was running and finding nothing."""
    rows = Bot().slot_diagnostics(config(BOOM_FADE))
    assert rows[0]["verdict"] == "not scanned"
    assert rows[0]["scans"] == 0


def test_a_slot_that_scans_and_finds_nothing_is_a_market_answer():
    bot = Bot()
    cfg = config(BOOM_FADE)
    for _ in range(12):
        bot._slot_seen(cfg.instrument_slots[0], "scan")
    rows = bot.slot_diagnostics(cfg)
    assert rows[0]["verdict"] == "no setup found"
    assert rows[0]["scans"] == 12 and rows[0]["signals"] == 0
    assert rows[0]["last_scan"]


def test_a_slot_whose_signals_are_all_refused_names_what_refused_them():
    """The case behind the live/demo difference: a small balance can make the
    minimum lot unreachable on one instrument while another keeps trading."""
    bot = Bot()
    cfg = config(BOOM_FADE)
    slot = cfg.instrument_slots[0]
    bot._slot_seen(slot, "scan")
    bot._slot_seen(slot, "signal")
    bot._slot_seen(slot, "signal")
    bot._suppression_funnel = {f"below_broker_min_lot|{slot.slot_id}": 2}
    row = bot.slot_diagnostics(cfg)[0]
    assert row["verdict"] == "every signal refused"
    assert row["dropped"] == {"below_broker_min_lot": 2}


def test_a_trading_slot_reads_as_trading():
    bot = Bot()
    cfg = config(BOOM_FADE)
    slot = cfg.instrument_slots[0]
    for what in ("scan", "signal", "trade"):
        bot._slot_seen(slot, what)
    assert bot.slot_diagnostics(cfg)[0]["verdict"] == "trading"


def test_two_slots_on_one_symbol_are_reported_separately():
    """The exact shape of the complaint: the same symbol on two strategies,
    one of them silent. A per-SYMBOL view cannot express this."""
    bot = Bot()
    cfg = config(BOOM_DRIFT, BOOM_FADE)
    drift, fade = cfg.instrument_slots
    for _ in range(9):
        bot._slot_seen(drift, "scan")
    bot._slot_seen(drift, "signal")
    bot._slot_seen(drift, "trade")

    rows = {(r["symbol"], r["strategy_id"]): r for r in bot.slot_diagnostics(cfg)}
    assert len(rows) == 2
    assert rows[BOOM_DRIFT]["verdict"] == "trading"
    assert rows[BOOM_FADE]["verdict"] == "not scanned"


def test_another_slots_refusals_are_not_attributed_to_this_one():
    """The funnel is keyed `reason|slot_id`. Reading it without the slot id
    would blame one slot for another's rejections - and with two slots on one
    symbol that is exactly the confusion to avoid."""
    bot = Bot()
    cfg = config(BOOM_DRIFT, BOOM_FADE)
    drift, fade = cfg.instrument_slots
    for slot in (drift, fade):
        bot._slot_seen(slot, "scan")
        bot._slot_seen(slot, "signal")
    bot._suppression_funnel = {
        f"below_broker_min_lot|{fade.slot_id}": 5,
        f"circuit_breaker:daily cap|{drift.slot_id}": 1,
    }
    rows = {r["strategy_id"]: r for r in bot.slot_diagnostics(cfg)}
    assert rows["SpikeFade_v1"]["dropped"] == {"below_broker_min_lot": 5}
    assert rows["BoomDriftJump_v1"]["dropped"] == {"circuit_breaker:daily cap": 1}


def test_an_engine_with_no_slot_in_the_config_is_surfaced():
    """A stale engine from before an edit. It is still trading and it is in no
    settings screen, which is the hardest state to notice."""
    bot = Bot()
    cfg = config(BOOM_DRIFT)
    ghost = InstrumentSlot(slot_id="gone", symbol="XAUUSD", strategy_id="ORB_v1")
    bot._slot_seen(ghost, "scan")
    rows = bot.slot_diagnostics(cfg)
    stale = [r for r in rows if r["slot_id"] == "gone"]
    assert stale and stale[0]["verdict"] == "running but not in the saved config"


def test_the_counters_reset_on_a_new_day():
    """Yesterday's scans must not make today's silent slot look healthy."""
    import datetime as dt

    bot = Bot()
    cfg = config(BOOM_FADE)
    bot._slot_seen(cfg.instrument_slots[0], "scan")
    assert bot.slot_diagnostics(cfg)[0]["scans"] == 1
    bot._slot_seen_day = dt.date(2000, 1, 1)
    bot._slot_seen(cfg.instrument_slots[0], "scan")
    assert bot.slot_diagnostics(cfg)[0]["scans"] == 1


def test_a_stopped_bot_is_reported_as_such():
    """Otherwise every slot reads "not scanned" and the obvious cause is the
    one thing the screen does not say."""
    rows = Bot(running=False).slot_diagnostics(config(BOOM_FADE))
    assert rows[0]["bot_running"] is False


def test_the_endpoint_is_mounted_and_signed_in_only():
    from backend.main import app

    paths = app.openapi()["paths"]
    assert "/api/slot-diagnostics" in paths
    body = paths["/api/slot-diagnostics"]["get"]
    assert body.get("security"), "the diagnostic names every instrument traded"


def test_the_dashboard_shows_it():
    """A diagnostic nobody can reach is a diagnostic nobody runs."""
    from pathlib import Path

    page = Path("frontend/src/pages/Dashboard.jsx").read_text(encoding="utf-8")
    assert "<SlotDiagnostics />" in page
    panel = Path("frontend/src/components/SlotDiagnostics.jsx").read_text(encoding="utf-8")
    for verdict in ("not scanned", "no setup found", "every signal refused"):
        assert verdict in panel, verdict
    assert "qk(" in panel, (
        "the query key must carry the server id, or switching boxes shows the "
        "previous one's answer under the new one's name")


def test_the_scan_loop_records_the_config_the_endpoint_reads():
    """/slot-diagnostics reads `bot_service.config`. The scan loop never set it,
    so the endpoint saw no slots and listed every SCANNING slot as "running but
    not in the saved config" (live dashboard, 2026-10-25). The loop must record
    the config it loaded each cycle."""
    import inspect
    from backend.services.bot_service import BotService
    src = inspect.getsource(BotService)
    loop = src[src.index("while self.running:"):]
    loaded = loop.index("config = UserConfigV2()")
    assert "self.config = config" in loop[loaded:loaded + 2000]
