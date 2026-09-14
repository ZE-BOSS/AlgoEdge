"""Interest-mode swap (MT5 swap_mode 5) is charged on the lot's money value,
price x tick value / tick size — not price x contract size."""

from types import SimpleNamespace

import pytest

from backend.risk import broker_costs as bc


def _info(tick_value, tick_size, contract=1.0, point=None):
    return SimpleNamespace(swap_mode=5, point=point or tick_size, trade_contract_size=contract,
                           trade_tick_value=tick_value, trade_tick_size=tick_size, currency_profit="USD")


def _swap(monkeypatch, info, price, rate):
    monkeypatch.setattr(bc, "_last_price", lambda symbol: price)
    monkeypatch.setattr(bc, "_fx_to_account", lambda info, symbol: 1.0)
    long_usd, short_usd, ok = bc._convert_swap_impl(info, "X", rate, rate, {})
    assert ok
    return long_usd


def test_volatility_75_is_financed_on_its_money_value(monkeypatch):
    # MT5 2026-09-14: tick value 0.0001 on a 0.01 tick, contract 1, -7.5 %/yr.
    per_lot = _swap(monkeypatch, _info(0.0001, 0.01), 47571.1, -7.5)
    assert per_lot == pytest.approx(-47571.1 * 0.01 * 0.075 / 360)
    # 15 lots: ~$1.49 a night, not the ~$149 price x contract gave
    assert 15 * per_lot == pytest.approx(-1.4866, abs=1e-3)


def test_crash_1000_matches_a_measured_rollover(monkeypatch):
    # This account's fill: 2.56 lots at 5556.53, one rollover charged -7.11.
    per_lot = _swap(monkeypatch, _info(0.0001, 0.0001), 5556.53, -18.0)
    assert 2.56 * per_lot == pytest.approx(-7.11, abs=0.02)


@pytest.mark.parametrize("tick_value,tick_size,contract", [(0.001, 0.001, 1.0), (1.0, 0.1, 10.0)])
def test_unchanged_where_money_value_equals_price_times_contract(monkeypatch, tick_value, tick_size, contract):
    per_lot = _swap(monkeypatch, _info(tick_value, tick_size, contract), 7563.2, -1.0)
    assert per_lot == pytest.approx(-7563.2 * contract * 0.01 / 360)


def test_missing_tick_data_falls_back_to_price_times_contract(monkeypatch):
    info = SimpleNamespace(swap_mode=5, point=0.01, trade_contract_size=1.0, currency_profit="USD")
    per_lot = _swap(monkeypatch, info, 29238.0, -6.03)
    assert per_lot == pytest.approx(-29238.0 * 0.0603 / 360)
