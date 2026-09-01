"""[18.3] The measured R:R defaults must actually be wired, not just declared.

Research/16 (285 cells, 23,989 trades) measured a best R:R per strategy and, on
top of that, a materially different best per symbol. Those numbers only matter
if they reach a real take-profit price on BOTH the live and backtest paths.

The failure this guards against is subtle: a default that is defined, exposed by
the API, displayed in the UI, and never applied — which is exactly what the
frontend was doing before this change.
"""
import pytest

from backend.risk.multi_tp import MultiTPManager
from backend.strategies.strategy_defaults import (
    STRATEGY_DEFAULTS,
    SLOT_TP1_RR,
    get_slot_tp1_rr_defaults,
    get_strategy_defaults,
    OVERRIDABLE,
)

STRATEGIES = [
    "APA_v1", "BiasIFVG_v1", "CRT_v1", "DriftJumpAlpha_v1",
    "HTFFVGFlip_v1", "NYOpenRetest_v1", "VWAP_v1",
]


@pytest.mark.parametrize("sid", STRATEGIES)
def test_every_strategy_has_a_measured_rr(sid):
    rr = get_strategy_defaults(sid).get("tp1_rr")
    assert rr is not None, f"{sid} has no measured tp1_rr"
    assert 1.0 <= rr <= 10.0, f"{sid} tp1_rr={rr} outside a sane range"


def test_tp1_rr_is_overridable():
    """A default a user cannot override is a constraint, not a default."""
    assert "tp1_rr" in OVERRIDABLE


def test_slot_keys_match_the_resolver_format():
    """SLOT_TP1_RR keys must match MultiTPManager.slot_key() exactly.

    A malformed key fails silently — resolution just falls through to the
    strategy default and nobody notices the measured value never applied.
    """
    for key in SLOT_TP1_RR:
        assert "|" in key, f"{key!r} is missing the SYMBOL|strategy separator"
        sym, sid = key.split("|", 1)
        assert sym == sym.upper(), f"{key!r} symbol must be upper-cased"
        assert sid in STRATEGIES, f"{key!r} names an unknown strategy {sid!r}"
        assert MultiTPManager.slot_key(sym, sid) == key


def _manager():
    return MultiTPManager({
        "tp1_rr": 1.5, "tp_count": 1, "tp_splits": "100",
        "tp1_rr_overrides_by_slot": get_slot_tp1_rr_defaults(),
        "tp1_rr_overrides_by_strategy": {
            s: get_strategy_defaults(s)["tp1_rr"] for s in STRATEGIES
        },
    })


@pytest.mark.parametrize("symbol,sid,expected", [
    ("Crash 1000 Index", "DriftJumpAlpha_v1", 5.0),
    ("Crash 300 Index", "DriftJumpAlpha_v1", 3.0),   # differs from its sibling
    ("US Tech 100", "NYOpenRetest_v1", 5.0),         # beats its 1:2 strategy default
    ("NDX100", "NYOpenRetest_v1", 5.0),              # FundedNext name, same value
    ("USOUSD", "BiasIFVG_v1", 5.0),
])
def test_per_symbol_defaults_resolve(symbol, sid, expected):
    assert _manager().resolve_tp1_rr(symbol, sid) == expected


@pytest.mark.parametrize("symbol,sid", [
    ("EURUSD", "NYOpenRetest_v1"),
    ("GBPUSD", "BiasIFVG_v1"),
    ("SOLUSD", "APA_v1"),
])
def test_unlisted_symbols_fall_back_to_the_strategy_default(symbol, sid):
    assert _manager().resolve_tp1_rr(symbol, sid) == get_strategy_defaults(sid)["tp1_rr"]


def test_measured_default_reaches_a_real_tp_price():
    """End to end: entry 100, stop 99 (risk 1.0) -> TP must sit at 100 + R:R."""
    m = _manager()
    for symbol, sid in (("Crash 1000 Index", "DriftJumpAlpha_v1"),
                        ("Crash 300 Index", "DriftJumpAlpha_v1"),
                        ("EURUSD", "NYOpenRetest_v1")):
        rr = m.resolve_tp1_rr(symbol, sid)
        levels = m.calculate_tp_levels(
            entry=100.0, sl=99.0, direction="BUY", total_volume=1.0,
            symbol=symbol, strategy_id=sid, use_live_mt5=False)
        assert levels, f"no TP levels produced for {symbol}/{sid}"
        assert levels[0].tp_price == pytest.approx(100.0 + rr, abs=1e-9)


def test_a_user_override_beats_the_measured_default():
    """These are defaults, not constraints."""
    m = MultiTPManager({
        "tp1_rr": 1.5, "tp_count": 1, "tp_splits": "100",
        "tp1_rr_overrides_by_slot": {
            **get_slot_tp1_rr_defaults(),
            "CRASH 1000 INDEX|DriftJumpAlpha_v1": 2.0,
        },
    })
    assert m.resolve_tp1_rr("Crash 1000 Index", "DriftJumpAlpha_v1") == 2.0


def test_every_strategy_default_carries_its_evidence():
    """A number with no measurement behind it should not be shipping."""
    for sid in STRATEGIES:
        assert STRATEGY_DEFAULTS[sid].get("evidence"), f"{sid} has no evidence string"
