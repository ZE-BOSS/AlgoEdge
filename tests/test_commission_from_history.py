"""Commission must come from what the account was actually charged.

A symbol with no deal history of its own fell through to the FX-cross average
($7/lot) even though this Deriv account's entire history books $0.00 — worth
~0.03 R per trade against an already conservative cost model.
"""

from types import SimpleNamespace

import pytest

import backend.risk.broker_costs as bc

DEFAULTS = {"commission_per_lot": 7.0}


class _FakeMT5:
    DEAL_ENTRY_IN = 0
    DEAL_ENTRY_OUT = 1

    def __init__(self, deals):
        self._deals = deals

    def history_deals_get(self, *args, **kwargs):
        group = kwargs.get("group")
        if group:      # per-symbol query: this account never traded the symbol
            return []
        return self._deals


def _deal(symbol, commission, entry, volume=1.0):
    return SimpleNamespace(symbol=symbol, commission=commission, entry=entry, volume=volume)


def _fx_history(commission_per_side):
    out = []
    for _ in range(3):
        out.append(_deal("CADJPY", commission_per_side, _FakeMT5.DEAL_ENTRY_IN))
        out.append(_deal("CADJPY", 0.0, _FakeMT5.DEAL_ENTRY_OUT))
    return out


def test_zero_commission_account_is_believed(monkeypatch):
    monkeypatch.setattr(bc, "mt5", _FakeMT5(_fx_history(0.0)))
    per_lot, ok = bc._derive_commission_per_lot("GBPJPY", DEFAULTS)
    assert ok is True and per_lot == 0.0


def test_entry_only_commission_is_doubled_for_the_round_turn(monkeypatch):
    monkeypatch.setattr(bc, "mt5", _FakeMT5(_fx_history(3.5)))
    per_lot, ok = bc._derive_commission_per_lot("GBPJPY", DEFAULTS)
    assert ok is True and per_lot == pytest.approx(7.0)


def test_non_fx_symbol_does_not_borrow_fx_history(monkeypatch):
    monkeypatch.setattr(bc, "mt5", _FakeMT5(_fx_history(0.0)))
    per_lot, ok = bc._derive_commission_per_lot("US SP 500", {"commission_per_lot": 2.5})
    assert ok is False and per_lot == pytest.approx(2.5)


def test_too_little_history_keeps_the_asset_class_default(monkeypatch):
    monkeypatch.setattr(bc, "mt5", _FakeMT5([_deal("CADJPY", 0.0, _FakeMT5.DEAL_ENTRY_IN)]))
    per_lot, ok = bc._derive_commission_per_lot("GBPJPY", DEFAULTS)
    assert ok is False and per_lot == pytest.approx(7.0)


def test_metals_are_not_treated_as_fx():
    assert bc._looks_like_fx("GBPJPY") and bc._looks_like_fx("EURUSD")
    assert not bc._looks_like_fx("XAUUSD") and not bc._looks_like_fx("US SP 500")
