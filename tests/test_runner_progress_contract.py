"""[18.4] Drive run_backtest for real, so a NameError in it cannot ship again.

A merge renamed `progress_phase` to `progress_cb` in the signature while the
body still referenced `progress_phase`. Every backtest died with:

    NameError: name 'progress_phase' is not defined
      runner.py, in run_backtest
        _owned_scale = progress_phase is None

122 tests passed through that. None of them called `run_backtest`, and the
route only fails at runtime — exactly the gap bugs B1 and B3 were logged
against ("verified at the layer I changed, never through the path a real run
takes"). These tests execute the function.
"""
import asyncio
import inspect

import pandas as pd
import pytest

from backend.backtester.runner import run_backtest
from backend.services.backtest_progress import BacktestProgress


def _candles(n=300):
    idx = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    base = 100.0
    return pd.DataFrame({
        "time": [int(t.timestamp()) for t in idx],
        "open": [base] * n,
        "high": [base + 0.5] * n,
        "low": [base - 0.5] * n,
        "close": [base] * n,
        "volume": [100] * n,
    }, index=idx)


def test_signature_is_a_phase_not_a_callback():
    """The name the body uses and the name the signature declares must agree."""
    params = inspect.signature(run_backtest).parameters
    assert "progress_phase" in params, (
        "run_backtest lost its progress_phase parameter — the body calls "
        "progress_phase.set() and tests `progress_phase is None`"
    )
    assert "progress_cb" not in params, (
        "progress_cb is a plain callable and cannot satisfy .set() / `is None` "
        "ownership checks — see the NameError this test exists for"
    )
    assert "candles_h1" in params, "candles_h1 dropped — reintroduces bug B3"


def test_body_references_only_declared_names():
    """Catch a rename that updates the signature but not the body."""
    src = inspect.getsource(run_backtest)
    declared = set(inspect.signature(run_backtest).parameters)
    for name in ("progress_phase", "progress_cb"):
        if f"{name} is None" in src or f"{name}.set(" in src or f"{name}.note" in src:
            assert name in declared, (
                f"run_backtest's body uses {name!r} but does not declare it — "
                f"this is the exact NameError that killed every run"
            )


@pytest.mark.parametrize("with_phase", [False, True])
def test_run_backtest_executes(with_phase):
    """Actually call it, both with and without a phase.

    `progress_phase=None` is the standalone path (`_owned_scale=True`);
    supplying one is the route path. Both must survive.
    """
    async def go():
        phase = None
        pump = None
        progress = None
        if with_phase:
            progress = BacktestProgress(
                user_id="test-user",
                broadcast=lambda uid, payload: asyncio.sleep(0),
                save_state=None,
            )
            phase = progress.phase(35, 90, "Simulating trades...")
            pump = asyncio.create_task(progress.pump())
        try:
            return await run_backtest(
                user_id="test-user",
                strategy_id="APA_v1",
                symbol="XAUUSD",
                candles=_candles(),
                signals=[],                 # no signals: exercises the plumbing
                risk_config={"risk_per_trade_pct": 1.0},
                initial_balance=10_000.0,
                save_mode="DISCARD",
                progress_phase=phase,
            )
        finally:
            if progress is not None:
                progress.close()
            if pump is not None:
                pump.cancel()

    results = asyncio.run(go())
    assert results is not None
    assert "total_trades" in results


def test_phase_reports_into_its_own_range():
    """A 35-90 phase must map 0.0-1.0 onto 35-90, not 0-100."""
    async def go():
        p = BacktestProgress("u", broadcast=lambda uid, pl: asyncio.sleep(0))
        ph = p.phase(35, 90, "Simulating trades...")
        await ph.set(0.0)
        lo = p._pct
        await ph.set(1.0)
        return lo, p._pct

    lo, hi = asyncio.run(go())
    assert lo == pytest.approx(35.0)
    assert hi == pytest.approx(90.0)


def test_progress_never_goes_backwards():
    """The bar must not jump back — record() drops any lower value."""
    async def go():
        p = BacktestProgress("u", broadcast=lambda uid, pl: asyncio.sleep(0))
        await p.set(70.0, "later")
        p.record(20.0, "earlier")
        return p._pct

    assert asyncio.run(go()) == pytest.approx(70.0)
