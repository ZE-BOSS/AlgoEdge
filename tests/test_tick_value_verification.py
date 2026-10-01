"""
tests/test_tick_value_verification.py

A symbol's spec fields can lie about money. Check them against the broker.

FOUND 2026-10-01
----------------
Deriv's `Volatility 75 Index` reports `trade_tick_value` 0.0001 against
`trade_tick_size` 0.01, implying **$0.01** per unit of price. The terminal's own
`order_calc_profit` says one lot over a 1.00 move is **$1.00**. The spec field is
100x low, so every P&L the app computed from it was 100x low.

It only bit where MT5 is CONNECTED. Offline runs fall through to
`InstrumentProfile`, which carries 0.01/0.01 and is right — which is why the
whole historical research corpus looks sane and only the live VPS was wrong.
That asymmetry is the reason this went unnoticed, and the reason a test that
only runs offline would never have caught it.

Checked against 19 symbols on 2026-10-01; only V75 disagreed.
"""

from __future__ import annotations

import pytest

from backend.risk import position_sizer as ps


class FakeInfo:
    """Just the fields `_verified_tick_value` reads."""
    def __init__(self, tick_value, tick_size):
        self.trade_tick_value = tick_value
        self.trade_tick_size = tick_size


class FakeMT5:
    """A terminal whose spec fields and profit calculator can be set apart."""
    ORDER_TYPE_BUY = 0
    TIMEFRAME_M1 = 1

    def __init__(self, price=44_000.0, profit_for_one_unit=1.0, rates=True):
        self.price = price
        self.profit = profit_for_one_unit
        self._rates = rates
        self.calls = 0

    def copy_rates_from_pos(self, symbol, tf, start, count):
        if not self._rates:
            return None
        return [{"close": self.price}]

    def order_calc_profit(self, action, symbol, lots, p_open, p_close):
        self.calls += 1
        if self.profit is None:
            return None
        return self.profit * lots * (p_close - p_open)


@pytest.fixture(autouse=True)
def _clear_cache():
    ps._VERIFIED_TICK.clear()
    yield
    ps._VERIFIED_TICK.clear()


def test_the_v75_spec_is_corrected_to_what_the_broker_actually_pays(monkeypatch):
    """The real case. Spec implies $0.01/unit; the calculator says $1.00."""
    monkeypatch.setattr(ps, "mt5", FakeMT5(profit_for_one_unit=1.0))
    tick_value, tick_size = ps._verified_tick_value("Volatility 75 Index", 0.0001, 0.01)
    assert tick_value / tick_size == pytest.approx(1.0), "must match order_calc_profit"
    assert tick_size == 0.01, "tick_size is not the thing that was wrong"


def test_an_honest_spec_is_left_exactly_alone(monkeypatch):
    """XAUUSD: tick_value 1.0 / tick_size 0.01 = $100, and the broker agrees."""
    monkeypatch.setattr(ps, "mt5", FakeMT5(profit_for_one_unit=100.0))
    assert ps._verified_tick_value("XAUUSD", 1.0, 0.01) == (1.0, 0.01)


@pytest.mark.parametrize("claimed_tv,claimed_ts,truth", [
    (0.01, 0.01, 1.0),          # the new Vol over / DEX synthetics
    (1.0, 0.00001, 100_000.0),  # EURUSD
    (0.1, 0.01, 10.0),          # Step Index
])
def test_agreeing_symbols_are_untouched(monkeypatch, claimed_tv, claimed_ts, truth):
    monkeypatch.setattr(ps, "mt5", FakeMT5(profit_for_one_unit=truth))
    assert ps._verified_tick_value("X", claimed_tv, claimed_ts) == (claimed_tv, claimed_ts)


def test_a_one_percent_wobble_is_tolerated_not_corrected(monkeypatch):
    """Terminal rounding is not a unit error; only disagree about real factors."""
    monkeypatch.setattr(ps, "mt5", FakeMT5(profit_for_one_unit=100.4))
    assert ps._verified_tick_value("XAUUSD", 1.0, 0.01) == (1.0, 0.01)


def test_an_unreachable_calculator_leaves_the_spec_untouched(monkeypatch):
    """This must never be the thing that breaks sizing — a hint beats nothing."""
    monkeypatch.setattr(ps, "mt5", FakeMT5(profit_for_one_unit=None))
    assert ps._verified_tick_value("X", 0.0001, 0.01) == (0.0001, 0.01)

    ps._VERIFIED_TICK.clear()
    monkeypatch.setattr(ps, "mt5", FakeMT5(rates=False))
    assert ps._verified_tick_value("X", 0.0001, 0.01) == (0.0001, 0.01)


def test_a_raising_terminal_does_not_propagate(monkeypatch):
    class Boom:
        TIMEFRAME_M1 = 1
        ORDER_TYPE_BUY = 0

        def copy_rates_from_pos(self, *a, **k):
            raise RuntimeError("IPC timeout")

    monkeypatch.setattr(ps, "mt5", Boom())
    assert ps._verified_tick_value("X", 0.0001, 0.01) == (0.0001, 0.01)


def test_the_check_is_cached_so_it_is_not_an_ipc_call_per_bar(monkeypatch):
    """A backtest resolves a symbol tens of thousands of times."""
    fake = FakeMT5(profit_for_one_unit=1.0)
    monkeypatch.setattr(ps, "mt5", fake)
    for _ in range(50):
        ps._verified_tick_value("Volatility 75 Index", 0.0001, 0.01)
    assert fake.calls == 1


def test_a_zero_or_negative_profit_is_ignored(monkeypatch):
    """A dead market returning 0 must not rewrite the instrument to zero value,
    which would make every trade worth nothing and every P&L zero."""
    monkeypatch.setattr(ps, "mt5", FakeMT5(profit_for_one_unit=0.0))
    assert ps._verified_tick_value("X", 0.0001, 0.01) == (0.0001, 0.01)
