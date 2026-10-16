"""Regression tests for the Claude analysis failures found on 2026-10-16.

1. A blocking TLS handshake inside the Anthropic SDK froze the API's event loop
   for 12+ minutes - every endpoint, not just the analysis.
2. A missing SDK came back as HTTP 200 and was saved as an "analysis".
3. A saved backtest reached Claude with no metrics and no trades, so it said
   "zero trades executed" about a run with 81.
4. The funnel's named causes were dropped.
"""

import asyncio
import time

import pytest


def test_a_blocking_provider_call_does_not_freeze_the_event_loop(monkeypatch):
    from backend.services import llm_service as L

    async def blocking_call(self, model, prompt, **kw):
        time.sleep(1.5)            # a synchronous stall, like the cert-chain build
        self._last_refusal = False
        return "answer"

    monkeypatch.setattr(L, "HAS_ANTHROPIC", True)
    monkeypatch.setattr(L.LLMService, "_call_anthropic", blocking_call)

    async def main():
        ticks = 0

        async def heartbeat():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.05)
                ticks += 1

        hb = asyncio.create_task(heartbeat())
        out = await L.LLMService({})._call_provider("anthropic", "m", "p")
        hb.cancel()
        return out, ticks

    out, ticks = asyncio.run(main())
    assert out == "answer"
    # ~30 ticks if the loop stayed free; 0-1 if the call blocked it.
    assert ticks >= 10, f"the event loop was blocked ({ticks} heartbeats)"


def test_strict_mode_raises_instead_of_returning_the_error_text(monkeypatch):
    from backend.services import llm_service as L

    monkeypatch.setattr(L, "HAS_ANTHROPIC", False)
    monkeypatch.setitem(L.IMPORT_ERRORS, "anthropic", "ModuleNotFoundError: jiter.jiter")
    with pytest.raises(L.LLMUnavailable, match="jiter"):
        asyncio.run(L.LLMService({})._call_provider("anthropic", "m", "p", strict=True))
    # The old callers still get a sentence.
    msg = asyncio.run(L.LLMService({})._call_provider("anthropic", "m", "p"))
    assert "not available" in msg


def test_a_missing_key_is_an_error_in_strict_mode(monkeypatch):
    from backend.services import llm_service as L

    if not L.HAS_ANTHROPIC:
        pytest.skip("anthropic SDK not importable here")
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(L.LLMUnavailable, match="No Anthropic API key"):
        asyncio.run(L.LLMService({})._call_provider("anthropic", "claude-haiku-4-5", "p",
                                                    strict=True))


def test_the_backtest_context_names_the_funnels_causes():
    from backend.services.analysis_context import build_backtest_context

    text, _ = build_backtest_context({
        "report": {"total_pnl": 10},
        "grouped_trades": [],
        "rejection_funnel": {"raw_signals": 100, "approved": 20,
                             "risk_rejections": {"daily_drawdown_exhausted": 50,
                                                 "insufficient_rr": 30}},
    })
    assert "risk_rejections / daily_drawdown_exhausted: 50" in text
    assert "% of rejections" in text


@pytest.mark.asyncio
async def test_a_saved_run_reaches_claude_with_its_trades_and_metrics():
    """Through the real loader, on a throwaway database."""
    import tempfile
    import uuid
    from datetime import datetime, timezone
    from pathlib import Path
    from types import SimpleNamespace

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from backend.api.routes.analysis import _load_backtest
    from backend.data.database import Base
    from backend.data.models import BacktestRun, BacktestTrade
    from backend.services.analysis_context import build_backtest_context

    tmp = Path(tempfile.mkdtemp(prefix="algoedge-analysis-"))
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp.as_posix()}/a.db")
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    uid, rid = str(uuid.uuid4()), str(uuid.uuid4())
    async with Session() as s:
        s.add(BacktestRun(id=rid, user_id=uid, strategy_id="ORB_v1", symbol="GBPJPY",
                          total_trades=2, win_rate=0.5, profit_factor=1.4,
                          total_pnl=55.0, expectancy_r=0.2,
                          params_snapshot='{"initial_balance": 10000}',
                          start_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
                          end_date=datetime(2026, 6, 1, tzinfo=timezone.utc),
                          created_at=datetime.now(timezone.utc)))
        for i, pnl in enumerate((100.0, -45.0)):
            s.add(BacktestTrade(backtest_id=rid, symbol="GBPJPY", direction="BUY",
                                entry_price=190.0 + i, exit_price=191.0, pnl=pnl,
                                realized_rr=pnl / 45.0, exit_reason="TP1" if pnl > 0 else "SL",
                                balance_after=10000 + pnl,
                                entry_time=datetime(2026, 2, 1 + i, tzinfo=timezone.utc)))
        await s.commit()
        result = await _load_backtest(rid, SimpleNamespace(id=uid), s)
    await engine.dispose()

    assert len(result["grouped_trades"]) == 2
    assert result["report"]["profit_factor"] == 1.4
    assert result["initial_balance"] == 10000
    text, _ = build_backtest_context(result)
    assert "Total trades (groups): 2" in text
    assert "Profit factor: 1.40" in text or "Profit factor: 1.4" in text
    assert "TP1" in text and "SL" in text
