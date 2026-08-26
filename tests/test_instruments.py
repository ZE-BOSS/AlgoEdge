"""
tests/test_instruments.py

Canonical instrument identity and per-broker symbol resolution (task 14.9).

The case that motivates all of it: GER40 (FundedNext) and GER30 (Deriv) are one
instrument under two brokers, and a config naming GER40 must trade GER30 when
Deriv is the connected broker — without the config being rewritten.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.core.instruments import (  # noqa: E402
    broker_id_from_account,
    discover_broker_symbols,
    get_broker_map,
    get_instrument,
    reset_broker_maps,
    resolve_broker_symbol,
    resolve_canonical,
)


@dataclass
class FakeSymbol:
    """Stands in for an `mt5.symbols_get()` element."""

    name: str
    trade_contract_size: float = 0.0
    path: str = ""


DERIV = broker_id_from_account("Deriv Limited", "Deriv-Demo")
FUNDED = broker_id_from_account("FundedNext Ltd", "FundedNext-Server01")


@pytest.fixture(autouse=True)
def _clean():
    reset_broker_maps()
    yield
    reset_broker_maps()


# ── Canonical resolution (broker symbol -> canonical) ────────────────────────

@pytest.mark.parametrize(
    "spelling,expected",
    [
        ("GER30", "GER40"),
        ("GER40", "GER40"),
        ("DAX", "GER40"),
        ("DE40", "GER40"),
        ("US SP 500", "US500"),
        ("SPX500", "US500"),
        ("US Tech 100", "NAS100"),
        ("Gold", "XAUUSD"),
    ],
)
def test_spellings_fold_to_one_canonical(spelling, expected):
    assert resolve_canonical(spelling) == expected


def test_broker_suffixes_are_stripped():
    assert resolve_canonical("XAUUSD.m") == "XAUUSD"
    assert resolve_canonical("XAUUSD_i") == "XAUUSD"
    assert resolve_canonical("EUR/USD") == "EURUSD"


def test_unknown_symbol_passes_through_unchanged():
    # Safe to apply to anything: an unrecognised symbol is returned as-is
    # rather than mapped to a plausible-looking wrong instrument.
    assert resolve_canonical("NOTATHING123") == "NOTATHING123"


def test_loose_matching_ignores_spacing_and_punctuation():
    assert resolve_canonical("s&p 500") == "US500"


# ── Per-broker discovery (canonical -> broker symbol) ────────────────────────

def test_same_instrument_resolves_differently_per_broker():
    """The whole point of Part C."""
    discover_broker_symbols(DERIV, [FakeSymbol("GER30"), FakeSymbol("XAUUSD")])
    discover_broker_symbols(FUNDED, [FakeSymbol("GER40"), FakeSymbol("XAUUSD")])

    assert resolve_broker_symbol("GER40", DERIV) == "GER30"
    assert resolve_broker_symbol("GER40", FUNDED) == "GER40"
    # And a config written with Deriv's spelling still works on FundedNext.
    assert resolve_broker_symbol("GER30", FUNDED) == "GER40"


def test_unlisted_instrument_returns_none_with_a_reason():
    discover_broker_symbols(DERIV, [FakeSymbol("GER30")])

    assert resolve_broker_symbol("XAUUSD", DERIV) is None
    row = get_broker_map(DERIV)["XAUUSD"]
    assert row.available is False
    assert "not listed by Deriv Limited" in row.reason


def test_every_known_instrument_gets_a_row():
    """
    Absent keys would read as "unknown"; the pickers need "unavailable, because".
    """
    mapping = discover_broker_symbols(DERIV, [FakeSymbol("GER30")])
    assert get_instrument("XAUUSD").canonical in mapping
    assert all(r.reason for r in mapping.values())


def test_no_discovery_falls_back_to_canonical():
    # Pre-14.9 behaviour, so single-broker setups are untouched.
    assert resolve_broker_symbol("GER40", "never|discovered") == "GER40"
    assert resolve_broker_symbol("GER40", None) == "GER40"


# ── Refusing to guess ────────────────────────────────────────────────────────

def test_ambiguous_match_is_refused_not_guessed():
    """
    Two listings folding to one canonical, with no exact-name tiebreak, must
    resolve to nothing. Guessing here silently trades the wrong instrument.
    """
    discover_broker_symbols(DERIV, [FakeSymbol("DAX"), FakeSymbol("DE40")])

    row = get_broker_map(DERIV)["GER40"]
    assert row.available is False
    assert row.broker_symbol is None
    assert "ambiguous" in row.reason
    assert sorted(row.ambiguous_with) == ["DAX", "DE40"]
    assert resolve_broker_symbol("GER40", DERIV) is None


def test_exact_name_breaks_a_tie():
    discover_broker_symbols(DERIV, [FakeSymbol("DAX"), FakeSymbol("GER40")])

    row = get_broker_map(DERIV)["GER40"]
    assert row.available is True
    assert row.broker_symbol == "GER40"
    assert row.ambiguous_with == ["DAX"]


def test_contract_size_mismatch_is_reported_not_rejected():
    """
    Deriv lists every CFD with `trade_contract_size = 1` regardless of the
    instrument, so rejecting a name match on a contract-size disagreement makes
    genuinely-offered instruments permanently untradeable — measured against the
    live terminal, it lost `US Oil`, `UK Brent Oil` and `XCUUSD`.

    The discrepancy must therefore surface as a note on an AVAILABLE row. Sizing
    is unaffected either way: the live profile overlay uses the broker's value.
    """
    from backend.risk.compounding import INSTRUMENT_PROFILES

    real = INSTRUMENT_PROFILES["XAUUSD"].contract_size
    discover_broker_symbols(
        DERIV, [FakeSymbol("XAUUSD", trade_contract_size=real / 1000.0)]
    )

    row = get_broker_map(DERIV)["XAUUSD"]
    assert row.available is True
    assert resolve_broker_symbol("XAUUSD", DERIV) == "XAUUSD"
    assert "live overlay governs sizing" in row.reason


def test_derivs_long_form_index_names_resolve():
    """
    The case Part C was written for. Deriv writes indices out in full; without
    these spellings discovery reports GER40 as unlisted on a broker that offers
    it. Verified against the live Deriv-Demo terminal 2026-08-26.
    """
    assert resolve_canonical("Germany 40") == "GER40"
    assert resolve_canonical("Hong Kong 50") == "HK50"
    assert resolve_canonical("US Small Cap 2000") == "US2000"

    discover_broker_symbols(DERIV, [FakeSymbol("Germany 40")])
    assert resolve_broker_symbol("GER40", DERIV) == "Germany 40"
    assert resolve_broker_symbol("GER30", DERIV) == "Germany 40"


def test_plausible_contract_size_is_accepted():
    from backend.risk.compounding import INSTRUMENT_PROFILES

    real = INSTRUMENT_PROFILES["XAUUSD"].contract_size
    discover_broker_symbols(DERIV, [FakeSymbol("XAUUSD", trade_contract_size=real)])

    assert resolve_broker_symbol("XAUUSD", DERIV) == "XAUUSD"


def test_rediscovery_replaces_rather_than_accumulates():
    discover_broker_symbols(DERIV, [FakeSymbol("GER30"), FakeSymbol("XAUUSD")])
    assert resolve_broker_symbol("XAUUSD", DERIV) == "XAUUSD"

    discover_broker_symbols(DERIV, [FakeSymbol("GER30")])
    assert resolve_broker_symbol("XAUUSD", DERIV) is None
