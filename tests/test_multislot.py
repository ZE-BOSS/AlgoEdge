"""
tests/test_multislot.py — [P2.x]

Running one symbol under two strategies at once. The backend has supported this
since InstrumentSlot shipped; the Settings UI was the only thing still writing
the symbol-keyed `instrument_settings` array, so the path was never exercised —
and two symbol-keyed structures on the live path would have misfired the moment
it was.
"""

import inspect

import pytest

from backend.core.config_schema import InstrumentSlot, UserConfigV2


def _cfg(**kw):
    return UserConfigV2.from_dict(kw)


# ── the model ────────────────────────────────────────────────────────────────

def test_one_symbol_can_carry_several_slots():
    cfg = _cfg(instrument_slots=[
        {"slot_id": "a1", "symbol": "Crash 1000 Index", "strategy_id": "APA_v1", "enabled": True},
        {"slot_id": "b2", "symbol": "Crash 1000 Index", "strategy_id": "DriftJumpAlpha_v1", "enabled": True},
    ])
    assert len(cfg.instrument_slots) == 2
    assert {s.strategy_id for s in cfg.instrument_slots} == {"APA_v1", "DriftJumpAlpha_v1"}
    assert len({s.slot_id for s in cfg.instrument_slots}) == 2, "slot ids must be distinct"


def test_slot_ids_are_not_derived_from_the_symbol():
    """The old `group_id = signal.symbol` bug is what happens when a natural key
    is used as an identity key and then stops being unique."""
    a, b = InstrumentSlot(symbol="X", strategy_id="S1"), InstrumentSlot(symbol="X", strategy_id="S1")
    assert a.slot_id != b.slot_id


def test_legacy_config_still_migrates_to_one_slot_per_symbol():
    cfg = _cfg(instrument_settings=[
        {"symbol": "EURUSD", "strategy_id": "APA_v1", "enabled": True},
        {"symbol": "XAUUSD", "strategy_id": "VWAP_v1", "enabled": True},
    ])
    assert [(s.symbol, s.strategy_id) for s in cfg.instrument_slots] == [
        ("EURUSD", "APA_v1"), ("XAUUSD", "VWAP_v1"),
    ]


def test_migration_is_stable_across_loads():
    """A fresh uuid4 per load would make circuit-breaker and UI state keyed by
    slot_id look like a brand-new slot on every config reload."""
    data = {"instrument_settings": [{"symbol": "EURUSD", "strategy_id": "APA_v1", "enabled": True}]}
    assert (_cfg(**data).instrument_slots[0].slot_id
            == _cfg(**data).instrument_slots[0].slot_id)


def test_explicit_slots_win_over_the_legacy_array():
    cfg = _cfg(
        instrument_settings=[{"symbol": "EURUSD", "strategy_id": "APA_v1", "enabled": True}],
        instrument_slots=[
            {"slot_id": "s1", "symbol": "Boom 1000 Index", "strategy_id": "APA_v1", "enabled": True},
            {"slot_id": "s2", "symbol": "Boom 1000 Index", "strategy_id": "VWAP_v1", "enabled": True},
        ],
    )
    assert len(cfg.instrument_slots) == 2
    assert all(s.symbol == "Boom 1000 Index" for s in cfg.instrument_slots)


# ── [P2.3] the live dedupe cell ──────────────────────────────────────────────
#
# `self._last_signal_time` was keyed by bare symbol. Two slots on one symbol
# then shared one cell, which failed in BOTH directions: slot B's fingerprint
# overwrote slot A's (so slot A's next re-scan of a bar it had already traded
# was no longer deduped — a duplicate entry), and two slots emitting the same
# (entry, SL, direction) on one bar silently dropped the second.

def test_live_signal_dedupe_is_keyed_by_slot():
    from backend.services import bot_service as bs

    src = inspect.getsource(bs)
    assert "_dedupe_key = slot.slot_id" in src
    assert "self._last_signal_time[symbol]" not in src, \
        "every read AND write of the dedupe cell must use the slot key"
    assert "self._last_signal_time.get(symbol)" not in src


def test_live_orders_are_stamped_with_the_slot():
    """[P2.7] With two slots on one symbol, symbol alone can no longer attribute
    a position to a strategy — position_manager would trail slot A's trade with
    slot B's ATR multiplier."""
    from backend.services import bot_service as bs

    src = inspect.getsource(bs)
    assert 'comment=f"AE_TP{tp.level}_{slot.slot_id[:8]}"' in src
    assert "magic=self._magic_base + (tp.level * 10)" in src, \
        "the magic must come from magic_base, or a customised base breaks ownership"


def test_order_comment_fits_the_mt5_field():
    slot_id = "abcdef0123456789"
    assert len(f"AE_TP5_{slot_id[:8]}") <= 31


# ── [P2.9] exposure ──────────────────────────────────────────────────────────
#
# Each slot sizes independently at risk_per_trade_pct, so stacking N slots on
# one symbol carries N x the intended risk while every per-slot cap still reads
# as satisfied. Both governor settings default to off, so nothing warned.

def test_stacked_slots_warn_when_no_direction_cap_is_set():
    cfg = _cfg(
        risk={"risk_per_trade_pct": 1.8, "max_concurrent_positions": 15,
              "max_positions_per_symbol": 15},
        instrument_slots=[
            {"slot_id": "s1", "symbol": "Crash 1000 Index", "strategy_id": "APA_v1", "enabled": True},
            {"slot_id": "s2", "symbol": "Crash 1000 Index", "strategy_id": "VWAP_v1", "enabled": True},
        ],
    )
    warnings = cfg.validate_slot_position_caps()
    # "carries up to" is unique to the stacked-exposure warning; the older
    # position-cap warning also mentions "enabled slots".
    stacked = [w for w in warnings if "carries up to" in w]
    assert stacked, f"expected a stacked-exposure warning, got {warnings}"
    assert "3.60%" in stacked[0], "the warning must state the actual aggregate risk"


def test_no_stacked_warning_once_a_governor_is_set():
    cfg = _cfg(
        risk={"risk_per_trade_pct": 1.8, "max_concurrent_positions": 15,
              "max_positions_per_symbol": 15, "max_net_direction_risk_pct": 3.6},
        instrument_slots=[
            {"slot_id": "s1", "symbol": "Crash 1000 Index", "strategy_id": "APA_v1", "enabled": True},
            {"slot_id": "s2", "symbol": "Crash 1000 Index", "strategy_id": "VWAP_v1", "enabled": True},
        ],
    )
    assert not [w for w in cfg.validate_slot_position_caps() if "carries up to" in w]


def test_one_slot_per_symbol_never_warns_about_stacking():
    cfg = _cfg(
        risk={"risk_per_trade_pct": 1.8, "max_concurrent_positions": 15,
              "max_positions_per_symbol": 15},
        instrument_slots=[
            {"slot_id": "s1", "symbol": "Crash 1000 Index", "strategy_id": "APA_v1", "enabled": True},
            {"slot_id": "s2", "symbol": "Boom 1000 Index", "strategy_id": "APA_v1", "enabled": True},
        ],
    )
    assert not [w for w in cfg.validate_slot_position_caps() if "carries up to" in w]


def test_disabled_slots_do_not_count_toward_stacking():
    cfg = _cfg(
        risk={"risk_per_trade_pct": 1.8, "max_concurrent_positions": 15,
              "max_positions_per_symbol": 15},
        instrument_slots=[
            {"slot_id": "s1", "symbol": "Crash 1000 Index", "strategy_id": "APA_v1", "enabled": True},
            {"slot_id": "s2", "symbol": "Crash 1000 Index", "strategy_id": "VWAP_v1", "enabled": False},
        ],
    )
    assert not [w for w in cfg.validate_slot_position_caps() if "carries up to" in w]


# ── [P1.1] resolved-parameter transparency ───────────────────────────────────

def test_resolved_params_expose_every_gate_that_can_stop_trading():
    from backend.services.bot_service import bot_service

    class _Params:
        max_trades_per_day = 20
        max_daily_risk_pct = 20.0
        stop_atr_multiple = 5.0
        spike_k_atr = 3.0

    class _Engine:
        strategy_id = "APA_v1"
        params = _Params()

    cfg = _cfg(risk={"risk_per_trade_pct": 1.8, "max_daily_trades": 20})
    slot = InstrumentSlot(slot_id="s1", symbol="Crash 1000 Index", strategy_id="APA_v1")

    got = bot_service._resolved_slot_params(slot, cfg, _Engine())
    for key in ("risk_per_trade_pct", "max_daily_trades", "allow_pyramiding",
                "sizing_basis", "strategy.max_trades_per_day",
                "strategy.max_daily_risk_pct", "strategy.spike_k_atr"):
        assert key in got, f"{key} missing — it can silently stop the bot trading"
    assert got["risk_per_trade_pct"] == 1.8
    assert got["strategy.max_trades_per_day"] == 20


def test_per_slot_override_shows_through_in_resolved_params():
    from backend.services.bot_service import bot_service

    class _Engine:
        strategy_id = "APA_v1"
        params = None

    cfg = _cfg(risk={"risk_per_trade_pct": 1.8})
    slot = InstrumentSlot(slot_id="s1", symbol="X", strategy_id="APA_v1",
                          risk_per_trade_pct=0.5)
    assert bot_service._resolved_slot_params(slot, cfg, _Engine())["risk_per_trade_pct"] == 0.5


def test_suppression_funnel_names_every_dropped_signal():
    from backend.services.bot_service import bot_service

    bot_service._suppression_day = None
    bot_service._suppression_funnel = {}
    bot_service._suppressed("news_filter", "slot1")
    bot_service._suppressed("news_filter", "slot1")
    bot_service._suppressed("risk_engine:min lot", "slot2")

    f = bot_service._suppression_funnel
    assert f["news_filter|slot1"] == 2
    assert f["risk_engine:min lot|slot2"] == 1


def test_primary_tf_seconds_picks_the_fastest_engine():
    from backend.services.bot_service import bot_service

    class _E:
        def __init__(self, tfs):
            self._t = tfs

        def get_required_timeframes(self):
            return self._t

    bot_service.engines = {"a": _E(["H4", "M15"]), "b": _E(["M5"])}
    assert bot_service._primary_tf_seconds() == 300
    bot_service.engines = {}
    assert bot_service._primary_tf_seconds() is None
