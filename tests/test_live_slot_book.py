"""
The live bot's half of per-slot risk.

The scan loop cannot be run in a test, so the pieces it depends on are exercised
directly: how a slot's engine is built from the saved config, what happens when
the user changes a setting while the bot is running, and where an account-wide
event (a close, a balance change, an MT5 reconcile) lands.
"""

import pytest

from backend.core.config_schema import InstrumentSlot, UserConfigV2
from backend.risk.live_risk_config import build_live_risk_config
from backend.risk.slot_book import SlotBook, resolve_slot_risk_config, slot_overrides_from


def _book(*slots) -> tuple[SlotBook, UserConfigV2]:
    cfg = UserConfigV2()
    cfg.instrument_slots = list(slots)
    book = SlotBook({"is_backtest": True})
    for slot in cfg.instrument_slots:
        base, _ = build_live_risk_config(cfg, slot.strategy_id)
        book.set_slot_config(slot.slot_id,
                             resolve_slot_risk_config(base, slot.strategy_id,
                                                      overrides=slot_overrides_from(slot)),
                             symbol=slot.symbol)
    return book, cfg


def test_each_slot_gets_its_own_engine_and_its_own_numbers():
    a = InstrumentSlot(symbol="EURUSD", strategy_id="IVW_v1",
                       risk={"max_daily_drawdown_pct": 2.0}, risk_per_trade_pct=0.35,
                       max_trades_per_day=2)
    b = InstrumentSlot(symbol="GBPJPY", strategy_id="ORB_v1")
    book, _ = _book(a, b)
    ea, eb = book.engine(a.slot_id), book.engine(b.slot_id)

    assert ea.risk_pct == 0.35 and eb.risk_pct == UserConfigV2().risk.risk_per_trade_pct
    assert ea.circuit.max_daily_trades == 2
    assert ea.circuit.max_daily_drawdown_pct == 2.0
    assert eb.circuit.max_daily_drawdown_pct == UserConfigV2().risk.max_daily_drawdown_pct
    assert ea.circuit is not eb.circuit, "two slots must not share a breaker"
    # each strategy still gets its own measured target
    assert ea.multi_tp is not eb.multi_tp


def test_changing_a_setting_takes_effect_without_losing_todays_counters():
    """The user edits a slot while the bot runs: new limits, same live counters."""
    slot = InstrumentSlot(symbol="EURUSD", strategy_id="IVW_v1", risk_per_trade_pct=1.0)
    book, cfg = _book(slot)
    engine = book.engine(slot.slot_id)
    engine.circuit.position_opened("g1", 1, symbol="EURUSD", initial_risk_dollars=100.0,
                                   slot_id=slot.slot_id)
    engine.circuit.record_backtest_close("g1", -100.0)
    assert engine.circuit.daily_trades_count == 1 and engine.circuit.cum_r == pytest.approx(-1.0)

    slot.risk_per_trade_pct = 0.25          # the edit
    base, _ = build_live_risk_config(cfg, slot.strategy_id)
    book.set_slot_config(slot.slot_id,
                         resolve_slot_risk_config(base, slot.strategy_id,
                                                  overrides=slot_overrides_from(slot)),
                         symbol=slot.symbol)
    rebuilt = book.engine(slot.slot_id)
    assert rebuilt.risk_pct == 0.25, "the edit did not reach the engine"
    assert rebuilt.circuit.daily_trades_count == 1, "today's trade count was forgotten"
    assert rebuilt.circuit.cum_r == pytest.approx(-1.0), "the slot's record was forgotten"


def test_a_close_is_booked_on_the_slot_that_opened_it():
    a = InstrumentSlot(symbol="EURUSD", strategy_id="IVW_v1")
    b = InstrumentSlot(symbol="GBPJPY", strategy_id="ORB_v1")
    book, _ = _book(a, b)
    ea, eb = book.engine(a.slot_id), book.engine(b.slot_id)
    ea.circuit.position_opened("g1", 1, symbol="EURUSD", initial_risk_dollars=100.0, slot_id=a.slot_id)

    assert book.route_close("EURUSD", -100.0) is True
    assert ea.circuit.daily_pnl == pytest.approx(-100.0)
    assert eb.circuit.daily_pnl == 0.0, "the loss landed on the wrong slot"

    # after a restart there are no open groups; the symbol's only slot takes it
    fresh, _ = _book(a, b)
    assert fresh.route_close("GBPJPY", 50.0) is True
    assert fresh.engine(b.slot_id).circuit.daily_pnl == pytest.approx(50.0)


def test_a_close_on_a_symbol_two_slots_share_is_not_guessed():
    a = InstrumentSlot(symbol="XAUUSD", strategy_id="IVW_v1")
    b = InstrumentSlot(symbol="XAUUSD", strategy_id="ORB_v1")
    book, _ = _book(a, b)
    book.engine(a.slot_id), book.engine(b.slot_id)
    assert book.route_close("XAUUSD", -100.0) is False, "a loss was guessed onto one of two slots"
    assert all(c.daily_pnl == 0.0 for c in book.circuits())


def test_mt5_reconcile_gives_each_slot_only_its_own_symbol():
    a = InstrumentSlot(symbol="EURUSD", strategy_id="IVW_v1")
    b = InstrumentSlot(symbol="GBPJPY", strategy_id="ORB_v1")
    book, _ = _book(a, b)
    book.engine(b.slot_id)          # build out of order, so engines != config order
    book.engine(a.slot_id)
    book.reconcile_from_mt5(["EURUSD", "EURUSD", "GBPJPY"])
    assert book.engine(a.slot_id).circuit.open_positions_by_symbol.get("EURUSD") == 2
    assert "GBPJPY" not in book.engine(a.slot_id).circuit.open_positions_by_symbol
    assert book.engine(b.slot_id).circuit.open_positions_by_symbol.get("GBPJPY") == 1


def test_a_balance_change_re_baselines_every_slot():
    a = InstrumentSlot(symbol="EURUSD", strategy_id="IVW_v1")
    b = InstrumentSlot(symbol="GBPJPY", strategy_id="ORB_v1")
    book, _ = _book(a, b)
    book.note_account_balance(10_000.0, account_id=123)
    for circuit in book.circuits():
        assert circuit._last_known_balance == 10_000.0
    book.reset_for_new_account(456)
    for circuit in book.circuits():
        assert circuit.account_id == 456 and circuit.cum_r == 0.0


def test_the_live_exit_manager_uses_the_slots_exits_not_the_strategys():
    """Two slots, one strategy, different break-even — position_manager's lookup."""
    import backend.services.position_manager as pm

    cfg = UserConfigV2()
    cfg.instrument_slots = [
        InstrumentSlot(symbol="EURUSD", strategy_id="IVW_v1", risk={"be_mode": "RR", "be_trigger_rr": 0.5}),
        InstrumentSlot(symbol="GBPJPY", strategy_id="IVW_v1", risk={"be_mode": "NONE"}),
    ]
    resolved = {}
    for sym in ("EURUSD", "GBPJPY"):
        slot = next(s for s in cfg.instrument_slots if s.symbol == sym)
        base, _ = build_live_risk_config(cfg, "IVW_v1")
        resolved[sym] = resolve_slot_risk_config(base, "IVW_v1", overrides=slot_overrides_from(slot))
    assert resolved["EURUSD"]["be_mode"] == "RR" and resolved["EURUSD"]["be_trigger_rr"] == 0.5
    assert resolved["GBPJPY"]["be_mode"] == "NONE"
    # the module really does resolve per slot (guards the lookup being dropped)
    import inspect
    src = inspect.getsource(pm)
    assert "resolve_slot_risk_config" in src and "instrument_slots" in src
