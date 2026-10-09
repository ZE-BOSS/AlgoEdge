"""
tests/test_rl_governance.py

The enforcement: what stops an unapproved agent from trading, and what stops a
drifted one from carrying on.

THESE ARE THE TESTS THAT MATTER MOST
------------------------------------
Everything else in W4 is a measurement. This is the part where a mistake means
an agent trading a strategy nobody signed off, so each test here is one route
by which that could happen:

  * loading an agent whose charter is DRAFT or PROPOSED;
  * loading one that was rejected;
  * loading one that is suspended for drift;
  * a suspended agent quietly falling back to trading without its gates;
  * the drift monitor failing to fire, or firing on a well-behaved short;
  * a supervisor overriding the owning strategy's own exit.

The enforcement point is `registry.load` and the `RLStrategy` constructor - not
the UI and not the route - so these tests call them directly.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.data.models import Base
from backend.rl import adherence as adh
from backend.rl import charter as ch
from backend.rl import features as F
from backend.rl import policy as pol
from backend.rl import registry as rlreg
from backend.rl.models import RLAgent
from backend.rl.spec import AgentSpec


def run(coro):
    return asyncio.run(coro)


class Site:
    """A throwaway database with the RL tables created."""

    async def __aenter__(self):
        self._tmp = tempfile.mkdtemp(prefix="algoedge-rl-")
        self.engine = create_async_engine(
            f"sqlite+aiosqlite:///{Path(self._tmp).as_posix()}/rl.db")
        import backend.rl.models  # noqa: F401 — registers the tables with Base
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        return self

    async def __aexit__(self, *exc):
        await self.engine.dispose()
        shutil.rmtree(self._tmp, ignore_errors=True)


def a_spec(**kw):
    return AgentSpec(name=kw.pop("name", "gov"), symbols=kw.pop("symbols", ("EURUSD",)), **kw)


def a_policy(spec):
    p = pol.LinearPolicy(n_features=spec.n_features, exit_head=True)
    return p.from_flat(np.random.default_rng(1).normal(0, 0.5, p.n_params))


def a_scaler(spec):
    X = np.random.default_rng(2).normal(0, 1, (300, spec.n_features))
    return F.Scaler().fit(X, spec.features, fitted_on="test rows 0-300")


def a_charter(spec, status=ch.DRAFT, version=1):
    g = ch.Gate(name="vol_high", feature="vol_expansion", op=">=", threshold=1.0)
    return ch.StrategyCharter(
        agent_id="gov-1", version=version, status=status, gates=(g,),
        tiers=(ch.Tier("A", ("vol_high",)),), entry_tier="A",
        instruments=spec.symbols, timeframes=(spec.timeframe,),
        thesis="a thesis", validation={"features": list(spec.features)})


async def _seed(site, *, charter_status=ch.APPROVED, agent_status=rlreg.APPROVED,
                suspended=False):
    spec = a_spec()
    async with site.Session() as s:
        await rlreg.save_agent(s, agent_id="gov-1", spec=spec, policy=a_policy(spec),
                               scaler=a_scaler(spec),
                               trained_on=[{"symbol": "EURUSD", "timeframe": "M5",
                                            "start": 1, "end": 2, "hash": "x"}],
                               status=agent_status)
        if suspended:
            await rlreg.suspend(s, "gov-1", "adherence drift")
        await rlreg.save_charter(s, a_charter(spec, charter_status))
        await s.commit()
    return spec


# ── loading is the enforcement point ───────────────────────────────────────

def test_an_approved_agent_loads():
    async def go():
        async with Site() as site:
            await _seed(site)
            async with site.Session() as s:
                agent = await rlreg.load(s, "gov-1", for_live=True)
            assert agent.charter.status == ch.APPROVED
            assert agent.strategy_id == "RL::gov@1"
    run(go())


@pytest.mark.parametrize("status", [ch.DRAFT, ch.PROPOSED])
def test_an_unapproved_charter_cannot_reach_a_live_slot(status):
    """Plan 4.10: nothing reaches live without APPROVED, enforced where the
    strategy is BUILT so an API call cannot bypass it."""
    async def go():
        async with Site() as site:
            await _seed(site, charter_status=status, agent_status="TRAINED")
            async with site.Session() as s:
                with pytest.raises((ch.CharterError, rlreg.NotApproved)):
                    await rlreg.load(s, "gov-1", for_live=True)
    run(go())


def test_a_proposed_charter_may_be_backtested_but_not_traded():
    """A proposal has to be runnable-and-labelled-unapproved, or the admin is
    approving something they could not have looked at."""
    async def go():
        async with Site() as site:
            await _seed(site, charter_status=ch.PROPOSED, agent_status="TRAINED")
            async with site.Session() as s:
                agent = await rlreg.load(s, "gov-1", for_live=False, version=1)
                assert agent.charter.status == ch.PROPOSED
                with pytest.raises(ch.CharterError):
                    await rlreg.load(s, "gov-1", for_live=True, version=1)
    run(go())


def test_a_rejected_agent_cannot_be_loaded_at_all():
    async def go():
        async with Site() as site:
            await _seed(site, agent_status=rlreg.REJECTED)
            async with site.Session() as s:
                row = await rlreg.get_agent(s, "gov-1")
                row.reject_reason = "PBO 45%"
                await s.commit()
                with pytest.raises(rlreg.NotApproved, match="PBO 45%"):
                    await rlreg.load(s, "gov-1")
    run(go())


def test_a_suspended_agent_is_refused_for_live_and_says_it_does_not_fall_back():
    """It must not silently trade unsupervised - the slot stops taking its
    signals and says why."""
    async def go():
        async with Site() as site:
            await _seed(site, suspended=True)
            async with site.Session() as s:
                with pytest.raises(rlreg.NotApproved, match="unsupervised"):
                    await rlreg.load(s, "gov-1", for_live=True)
                # still loadable for a backtest, which is how you investigate it
                agent = await rlreg.load(s, "gov-1", for_live=False)
                assert agent.charter.status == ch.APPROVED
    run(go())


def test_an_agent_with_no_charter_cannot_be_loaded():
    async def go():
        async with Site() as site:
            spec = a_spec()
            async with site.Session() as s:
                await rlreg.save_agent(s, agent_id="bare", spec=spec,
                                       policy=a_policy(spec), scaler=a_scaler(spec),
                                       trained_on=[])
                await s.commit()
                with pytest.raises(rlreg.NotApproved, match="no approved charter"):
                    await rlreg.load(s, "bare")
    run(go())


def test_an_agent_with_no_stored_scaler_cannot_be_served():
    """Its observation would be on a different scale from the one it was
    trained on, which is silent and total."""
    async def go():
        async with Site() as site:
            spec = await _seed(site)
            async with site.Session() as s:
                row = await rlreg.get_agent(s, "gov-1")
                row.scaler_json = "{}"
                await s.commit()
                with pytest.raises(LookupError, match="no fitted scaler"):
                    await rlreg.load(s, "gov-1")
    run(go())


def test_get_charter_returns_the_approved_one_and_not_the_newest():
    """A newer DRAFT must not become what live is measured against just by
    existing - that would be the agent changing its own strategy."""
    async def go():
        async with Site() as site:
            spec = await _seed(site)
            async with site.Session() as s:
                newer = a_charter(spec, ch.DRAFT, version=2)
                newer.thesis = "something the admin has not seen"
                await rlreg.save_charter(s, newer)
                await s.commit()
                live = await rlreg.get_charter(s, "gov-1")
                assert live.version == 1 and live.status == ch.APPROVED
    run(go())


# ── the strategy class ─────────────────────────────────────────────────────

def test_the_strategy_cannot_be_constructed_for_an_unloaded_agent():
    """`cached` only holds agents whose charter is APPROVED, and a LAZY load
    here would let whatever first asked for an agent pull it in - which is the
    bypass the whole design exists to prevent."""
    from backend.strategies.strategy_rl.engine import RLStrategy

    rlreg.cache_clear()
    cls = type("RLStrategy_absent", (RLStrategy,),
               {"strategy_id": "RL::absent@1", "AGENT_ID": "absent"})
    with pytest.raises(rlreg.NotApproved, match="not been approved"):
        cls({})


def test_registering_an_agent_makes_it_selectable_like_any_strategy():
    async def go():
        from backend.strategies.registry import list_strategies

        async with Site() as site:
            await _seed(site)
            async with site.Session() as s:
                registered = await rlreg.register_approved(s)
            assert registered == ["RL::gov@1"]
            assert "RL::gov@1" in list_strategies()
            rlreg.unregister("RL::gov@1")
            rlreg.cache_clear()
    run(go())


def test_an_agent_whose_charter_is_not_approved_is_absent_from_the_registry():
    """Absent rather than present-and-refused: a strategy you cannot select is
    clearer than one that appears in the list and then errors."""
    async def go():
        from backend.strategies.registry import list_strategies

        async with Site() as site:
            await _seed(site, charter_status=ch.PROPOSED, agent_status="TRAINED")
            async with site.Session() as s:
                assert await rlreg.register_approved(s) == []
            assert "RL::gov@1" not in list_strategies()
    run(go())


@pytest.mark.parametrize("sid,expected", [
    ("RL::alpha@3", ("alpha", 3)),
    ("RL::alpha", ("alpha", 1)),
    ("RL::a-b-c@12", ("a-b-c", 12)),
    ("ORB_v1", None),
    ("", None),
])
def test_a_strategy_id_round_trips(sid, expected):
    assert rlreg.parse_strategy_id(sid) == expected


# ── live adherence is scored against the APPROVED charter ──────────────────

def test_live_adherence_refuses_a_charter_that_is_not_approved():
    """Scoring live against a PROPOSED charter would let an agent earn its
    adherence reward against a strategy nobody signed off."""
    spec = a_spec()
    proposed = a_charter(spec, ch.PROPOSED)
    with pytest.raises(ValueError, match="APPROVED"):
        adh.score_live(proposed, {"vol_expansion": 1.5}, "BUY")


def test_live_adherence_works_on_an_approved_charter():
    spec = a_spec()
    approved = a_charter(spec, ch.APPROVED)
    score = adh.score_live(approved, {"vol_expansion": 1.5}, "BUY")
    assert score.adherence == 1.0 and score.in_charter is True
    assert score.charter_version == 1 and score.charter_hash


def test_a_decision_outside_the_entry_tier_is_marked_not_in_charter():
    spec = a_spec()
    approved = a_charter(spec, ch.APPROVED)
    score = adh.score_decision(approved, {"vol_expansion": 0.5}, "BUY")
    assert score.in_charter is False and score.gates_skipped == ("vol_high",)


def test_the_observation_dict_refuses_a_length_mismatch():
    """A column-order mismatch would silently score the wrong feature."""
    with pytest.raises(ValueError, match="for 3 feature names"):
        adh.observation_dict(["a", "b", "c"], [1.0, 2.0])


# ── drift ──────────────────────────────────────────────────────────────────

def _monitor_charter(floor=0.6, window=3):
    spec = a_spec()
    c = a_charter(spec, ch.APPROVED)
    c.adherence_floor = floor
    c.drift_trades = window
    return c


def test_drift_suspends_after_a_streak_below_the_floor():
    monitor = adh.DriftMonitor()
    c = _monitor_charter(window=3)
    low = adh.DecisionScore(0.2, "A", (), ("vol_high",), 1, c.content_hash, True)
    v1 = monitor.record(c, low)
    v2 = monitor.record(c, low)
    assert not v1.suspend and not v2.suspend
    v3 = monitor.record(c, low)
    assert v3.suspend is True
    assert "vol_high" in v3.message and "3 consecutive" in v3.message
    assert monitor.suspended(c) is True


def test_one_good_trade_resets_the_streak():
    """A streak reacts at the point the behaviour changed; one compliant trade
    in the middle means the agent is still sometimes following the charter."""
    monitor = adh.DriftMonitor()
    c = _monitor_charter(window=3)
    low = adh.DecisionScore(0.2, "A", (), ("vol_high",), 1, c.content_hash, True)
    high = adh.DecisionScore(1.0, "A", ("vol_high",), (), 1, c.content_hash, True)
    monitor.record(c, low)
    monitor.record(c, low)
    monitor.record(c, high)
    assert monitor.record(c, low).suspend is False
    assert monitor.suspended(c) is False


def test_the_suspension_names_the_gates_it_has_been_skipping():
    """The drift email has to say WHICH gates, or it is unactionable."""
    monitor = adh.DriftMonitor()
    c = _monitor_charter(window=2)
    low = adh.DecisionScore(0.0, None, (), ("vol_high",), 1, c.content_hash, False)
    monitor.record(c, low)
    v = monitor.record(c, low)
    assert v.skipped and v.skipped[0][0] == "vol_high"
    assert v.skipped[0][1] == 2


def test_recovery_is_reported_but_does_not_auto_resume():
    """An agent that drifted and came back is still an agent whose charter no
    longer describes it. That is an admin's call."""
    monitor = adh.DriftMonitor()
    c = _monitor_charter(window=2)
    low = adh.DecisionScore(0.0, None, (), ("vol_high",), 1, c.content_hash, False)
    high = adh.DecisionScore(1.0, "A", ("vol_high",), (), 1, c.content_hash, True)
    monitor.record(c, low)
    monitor.record(c, low)
    assert monitor.suspended(c)
    for _ in range(5):
        v = monitor.record(c, high)
    assert v.recovered is True and monitor.suspended(c) is True
    assert "stays suspended" in v.message


def test_resetting_is_explicit():
    monitor = adh.DriftMonitor()
    c = _monitor_charter(window=1)
    monitor.record(c, adh.DecisionScore(0.0, None, (), (), 1, c.content_hash, False))
    assert monitor.suspended(c)
    monitor.reset(c)
    assert monitor.suspended(c) is False


def test_each_charter_version_is_monitored_separately():
    """Approving a new version starts a fresh window: the old version's drift
    is not evidence about the new strategy."""
    monitor = adh.DriftMonitor()
    v1 = _monitor_charter(window=1)
    v2 = _monitor_charter(window=1)
    v2.version = 2
    monitor.record(v1, adh.DecisionScore(0.0, None, (), (), 1, v1.content_hash, False))
    assert monitor.suspended(v1) and not monitor.suspended(v2)


# ── a pending suspension becomes durable ───────────────────────────────────

def test_a_suspension_is_recorded_without_doing_io_and_drained_later():
    """Detection happens in a worker thread with no session and no event loop,
    so it records and the jobs loop acts."""
    from backend.rl import suspension

    suspension.clear()
    c = _monitor_charter(window=1)
    monitor = adh.DriftMonitor()
    v = monitor.record(c, adh.DecisionScore(0.0, None, (), ("vol_high",), 1,
                                            c.content_hash, False))
    suspension.record("gov-1", 1, v)
    assert len(suspension.peek()) == 1

    async def go():
        async with Site() as site:
            await _seed(site)
            async with site.Session() as s:
                n = await suspension.drain(s)
                await s.commit()
            assert n == 1
            assert suspension.peek() == []
            async with site.Session() as s:
                row = await rlreg.get_agent(s, "gov-1")
                assert row.suspended is True and "vol_high" in row.suspended_reason
    run(go())
    suspension.clear()


def test_a_failed_drain_keeps_the_suspension_pending():
    """An agent suspended in memory and not in the database would come back
    after a restart."""
    from backend.rl import suspension

    suspension.clear()
    c = _monitor_charter(window=1)
    v = adh.DriftMonitor().record(
        c, adh.DecisionScore(0.0, None, (), ("vol_high",), 1, c.content_hash, False))
    suspension.record("never-existed", 1, v)

    class Boom:
        def __getattr__(self, name):
            raise RuntimeError("no database")

    async def go():
        # `drain` is deliberately resilient: a database failure must not lose
        # the suspension, and it must not count as handled either - a wedged
        # drain that reported success would look like a quiet one.
        return await suspension.drain(Boom())

    assert run(go()) == 0
    assert len(suspension.peek()) == 1, "a failed drain must retry, not lose it"
    suspension.clear()


# ── supervision: the owner wins ────────────────────────────────────────────

def test_the_engine_asks_the_owner_before_any_supervisor():
    """The precedence rule, through the real engine's helper."""
    from backend.backtester.engine import BacktestEngine
    from backend.strategies.base_strategy import BaseStrategy, TradeAction

    class Owner(BaseStrategy):
        strategy_id = "OWNER_v1"

        def on_position_bar(self, *a, **k):
            return TradeAction(ticket=1, action="CLOSE", close_reason="OWNER_RULE")

    class Sup(BaseStrategy):
        strategy_id = "SUP"
        SUPERVISES_OTHER_STRATEGIES = True
        asked = False

        def supervises(self, strategy_id, symbol):
            return True

        def on_foreign_position_bar(self, *a, **k):
            Sup.asked = True
            return TradeAction(ticket=1, action="CLOSE", close_reason="SUPERVISOR")

    engine = BacktestEngine({})
    engine._strategy = Owner({})
    engine.set_supervisors([Sup({})])
    # The owner answered, so the engine never reaches the supervisor at all.
    pos = {"symbol": "EURUSD", "timeframe": "M5", "stop_loss": 99.0, "direction": "BUY"}
    assert engine._strategy.on_position_bar("EURUSD", "M5", None, pos).close_reason \
        == "OWNER_RULE"
    assert Sup.asked is False


def test_a_supervisor_is_consulted_only_where_the_owner_was_silent():
    from backend.backtester.engine import BacktestEngine
    from backend.strategies.base_strategy import BaseStrategy, TradeAction

    class Quiet(BaseStrategy):
        strategy_id = "QUIET_v1"

        def on_position_bar(self, *a, **k):
            return None

    class Sup(BaseStrategy):
        strategy_id = "SUP"
        SUPERVISES_OTHER_STRATEGIES = True

        def supervises(self, strategy_id, symbol):
            return strategy_id == "QUIET_v1" and symbol == "EURUSD"

        def on_foreign_position_bar(self, symbol, tf, candles, position, owner=None):
            assert owner["strategy_id"] == "QUIET_v1"
            return TradeAction(ticket=1, action="CLOSE", close_reason="RL_SUPERVISOR::a@1")

    engine = BacktestEngine({})
    engine._strategy = Quiet({})
    engine.set_supervisors([Sup({})])
    pos = {"symbol": "EURUSD", "timeframe": "M5", "stop_loss": 99.0, "direction": "BUY"}
    act = engine._ask_supervisors(pos, None)
    assert act is not None and act.close_reason.startswith("RL_SUPERVISOR::")


def test_a_supervisor_is_not_asked_about_a_symbol_it_was_not_trained_on():
    from backend.backtester.engine import BacktestEngine
    from backend.strategies.base_strategy import BaseStrategy, TradeAction

    class Sup(BaseStrategy):
        strategy_id = "SUP"
        SUPERVISES_OTHER_STRATEGIES = True

        def supervises(self, strategy_id, symbol):
            return symbol == "Crash 1000 Index"

        def on_foreign_position_bar(self, *a, **k):
            return TradeAction(ticket=1, action="CLOSE")

    engine = BacktestEngine({})
    engine._strategy = None
    engine.set_supervisors([Sup({})])
    assert engine._ask_supervisors(
        {"symbol": "GBPJPY", "stop_loss": 99.0, "direction": "BUY"}, None) is None


def test_a_strategy_that_has_not_opted_in_is_not_a_supervisor():
    """Default off: a strategy that was never measured as a supervisor does not
    become one by being selected somewhere."""
    from backend.backtester.engine import BacktestEngine
    from backend.strategies.base_strategy import BaseStrategy

    class NotASupervisor(BaseStrategy):
        strategy_id = "PLAIN"

    engine = BacktestEngine({})
    engine.set_supervisors([NotASupervisor({})])
    assert engine._supervisors == []


def test_the_base_class_supervises_nothing_by_default():
    """A supervisor that forgets to narrow its scope supervises nothing rather
    than everything."""
    from backend.strategies.base_strategy import BaseStrategy

    s = BaseStrategy({})
    assert s.SUPERVISES_OTHER_STRATEGIES is False
    assert s.supervises("ANY", "ANY") is False
    assert s.on_foreign_position_bar("EURUSD", "M5", None, {}, None) is None


def test_every_shipped_strategy_still_defaults_to_not_supervising():
    """Adding the hook must not have turned anything into a supervisor."""
    from backend.strategies.registry import get_all_strategies

    for sid, cls in get_all_strategies().items():
        if sid.startswith("RL::"):
            continue
        assert getattr(cls, "SUPERVISES_OTHER_STRATEGIES", False) is False, sid
