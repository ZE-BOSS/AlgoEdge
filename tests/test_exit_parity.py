"""[18.2] Guards the backtest against booking exits that live cannot produce.

The backtest engine can close a position with SESSION_END or TIME_LIMIT.
Nothing in backend/services/ implements either, so live holds those positions
instead. Measured on the 7,463-trade sweep, 925 trades (12.4%) exited this way
carrying $36,076 of profit, at averages of +0.471R and +1.855R — far better than
the book's -0.180R. The backtest was optimistic, and concentrated in exactly its
best non-TP exits.

`simulate_backtest_only_exits` now gates both and defaults to False, so a
backtest describes what live will do. These tests fail if that default flips, or
if a live implementation appears without the flag being reconsidered.
"""
import pathlib

import pytest

from backend.backtester.engine import BacktestEngine

BACKEND = pathlib.Path(__file__).resolve().parents[1] / "backend"


def test_backtest_only_exits_default_to_off():
    """Default must match live, i.e. these exits do not fire."""
    assert BacktestEngine({}). _backtest_only_exits is False
    assert BacktestEngine({"risk_per_trade_pct": 1.0})._backtest_only_exits is False


def test_flag_can_restore_old_behaviour():
    assert BacktestEngine({"simulate_backtest_only_exits": True})._backtest_only_exits is True


def test_live_still_does_not_implement_these_exits():
    """If live ever gains SESSION_END / TIME_LIMIT, revisit the default.

    This is the assumption the default rests on. When it stops being true the
    right answer is probably to flip the default back to True, so the test
    failing is the prompt to make that decision — not a nuisance.
    """
    services = (BACKEND / "services").rglob("*.py")
    offenders = []
    for f in services:
        text = f.read_text(encoding="utf-8", errors="ignore")
        for token in ("SESSION_END", "TIME_LIMIT"):
            if token in text:
                offenders.append(f"{f.name}: {token}")
    assert not offenders, (
        "live now references these exit reasons: "
        + ", ".join(offenders)
        + " — if live genuinely implements them, flip "
        "simulate_backtest_only_exits back to True so the backtest matches."
    )


@pytest.mark.parametrize("reason", ["SESSION_END", "TIME_LIMIT"])
def test_backtester_still_owns_these_reasons(reason):
    """Sanity: the gated code is still present, just disabled by default."""
    engine_src = (BACKEND / "backtester" / "engine.py").read_text(encoding="utf-8")
    assert reason in engine_src
    assert "_backtest_only_exits" in engine_src
