"""A JPY pip is 0.01 whether the instrument profile came from the static table
(pip scale, 0.01) or from live MT5 (the broker's pipette, 0.001)."""

from types import SimpleNamespace

import pytest

import backend.risk.compounding as compounding
import backend.risk.position_sizer as ps


@pytest.fixture(autouse=True)
def _clear_cache():
    ps._pip_size_cache.clear()
    yield
    ps._pip_size_cache.clear()


def _profile(monkeypatch, point_size, kind="FOREX"):
    monkeypatch.setattr(compounding, "get_instrument_profile",
                        lambda s: SimpleNamespace(instrument_type=kind, point_size=point_size))


@pytest.mark.parametrize("symbol", ["GBPJPY", "EURJPY", "AUDJPY", "CADJPY", "USDJPY"])
@pytest.mark.parametrize("point_size", [0.001, 0.01])
def test_jpy_pip_is_one_hundredth_from_either_profile_source(monkeypatch, symbol, point_size):
    _profile(monkeypatch, point_size)
    assert ps.get_pip_size(symbol) == pytest.approx(0.01)


def test_non_jpy_forex_is_unchanged(monkeypatch):
    _profile(monkeypatch, 0.00001)
    assert ps.get_pip_size("EURUSD") == pytest.approx(0.0001)


def test_gold_is_unchanged(monkeypatch):
    _profile(monkeypatch, 0.01, kind="COMMODITY")
    assert ps.get_pip_size("XAUUSD") == pytest.approx(0.1)
