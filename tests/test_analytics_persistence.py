"""
[T1.6] Regression guard for the analytics-persistence class of bug.

Every field checked here was, at some point, computed correctly and then
silently dropped one step short of the database — which is how the book ended up
with 3,528 saved trades carrying:

  * mae_pips / mfe_pips  NULL on 100% of rows (grouper never copied them up)
  * trail_method         NULL on 100% of rows (never configured, never recorded)
  * rejection_funnel     '{}' on 100% of runs (client-save path never passed it)
  * confluence_stats     '{}' on 100% of runs (buried in params_snapshot instead)
  * by_confirmation      one bucket, forever (read fields stripped before save)
  * pnl_r                positive on money-LOSING break-even exits

None of these raised. They just quietly produced empty analytics, and the whole
optimization effort was blocked on data that was never written. A unit test is
the only thing that makes that class of failure loud.
"""
import inspect

import pytest

from backend.data.models import BacktestRun, BacktestTrade
from backend.utils.trade_grouper import group_trades


# --- fields that MUST survive from the group dict into the DB row -------------
TRADE_FIELDS = [
    "mae_pips", "mfe_pips", "mae_r", "mfe_r", "risk_pips",
    "trail_method", "confluence_score", "pnl_r", "planned_rr", "realized_rr",
    "be_applied", "session", "strategy_id",
]
RUN_ANALYTICS_FIELDS = ["rejection_funnel", "bias_stats", "confluence_stats"]


def test_backtest_trade_model_has_analytics_columns():
    cols = set(BacktestTrade.__table__.columns.keys())
    missing = [f for f in TRADE_FIELDS if f not in cols]
    assert not missing, f"BacktestTrade is missing analytics columns: {missing}"


def test_backtest_run_model_has_analytics_columns():
    cols = set(BacktestRun.__table__.columns.keys())
    missing = [f for f in RUN_ANALYTICS_FIELDS if f not in cols]
    assert not missing, f"BacktestRun is missing analytics columns: {missing}"


@pytest.mark.parametrize("path", [
    "backend/backtester/runner.py",
    "backend/api/routes/backtest.py",
])
def test_both_save_paths_persist_excursion_fields(path):
    """
    Both save paths must pass the excursion fields to BacktestTrade.

    The client-save path in backtest.py is the one the Save button uses, and it
    is the path that produced every saved run — so a field wired only into
    runner.py is a field that never reaches the database in practice.
    """
    src = open(path, encoding="utf-8").read()
    for field in ("mae_pips", "mfe_pips", "mae_r", "mfe_r", "risk_pips"):
        assert f"{field}=" in src, f"{path} never assigns {field} on BacktestTrade"


def test_client_save_path_persists_run_analytics():
    """
    The client-save path must pass rejection_funnel / bias_stats /
    confluence_stats to BacktestRun.

    It previously buried two of them inside the params_snapshot JSON blob and
    dropped rejection_funnel entirely, so the model applied `default={}` and all
    57 saved runs recorded empty analytics.
    """
    src = open("backend/api/routes/backtest.py", encoding="utf-8").read()
    for field in RUN_ANALYTICS_FIELDS:
        assert f"{field}=" in src, (
            f"client-save path never assigns {field} on BacktestRun — "
            "this is exactly the bug that emptied every saved run"
        )


def _leg(**kw):
    base = dict(
        group_id="g1", symbol="EURUSD", direction="BUY", volume=0.1,
        entry_price=1.1000, exit_price=1.1010, stop_loss=1.0990,
        initial_stop_loss=1.0990, take_profit=1.1020,
        entry_time=1_700_000_000, exit_time=1_700_003_600,
        pnl=10.0, tp_level=1, exit_reason="TP1",
        mae_pips=3.0, mfe_pips=14.0, confluence_score=70,
        original_signal={"metadata": {
            "confluence_tags": ["fvg", "liquidity_sweep"],
            "gate_vector": {"fvg": True, "liquidity_sweep": True, "session": False},
        }},
    )
    base.update(kw)
    return base


def test_grouper_propagates_excursion():
    """MAE/MFE are computed by the engine; the grouper has to carry them up."""
    g = group_trades([_leg()])[0]
    assert g.get("mae_pips") == 3.0, "grouper dropped mae_pips (was NULL on 3,528 trades)"
    assert g.get("mfe_pips") == 14.0, "grouper dropped mfe_pips"


def test_grouper_takes_widest_excursion_across_legs():
    """Legs share one entry and stop, so the group's excursion is the widest."""
    g = group_trades([
        _leg(mae_pips=3.0, mfe_pips=14.0),
        _leg(mae_pips=9.0, mfe_pips=6.0, tp_level=2, exit_reason="TP2"),
    ])[0]
    assert g["mae_pips"] == 9.0
    assert g["mfe_pips"] == 14.0


def test_grouper_emits_excursion_in_r():
    """
    R-normalised excursion is what makes setups comparable across symbols;
    pips are not poolable across a 14-symbol book.
    """
    g = group_trades([_leg()])[0]
    assert g.get("risk_pips"), "risk_pips not computed"
    assert g.get("mfe_r") is not None and g.get("mae_r") is not None
    # entry 1.1000, stop 1.0990 -> 10 pips of risk; mfe 14 pips -> 1.4R
    assert g["mfe_r"] == pytest.approx(1.4, abs=0.05)
    assert g["mae_r"] == pytest.approx(0.3, abs=0.05)


def test_grouper_captures_confluence_tags_before_they_are_stripped():
    """
    runner.py's _slim_sub_trades removes original_signal/metadata before saving,
    so the tags must be lifted onto the group first — otherwise
    `by_confirmation` collapses to the single "base_structure" bucket that all
    3,528 saved trades carried.
    """
    g = group_trades([_leg()])[0]
    assert g.get("confluence_tags") == ["fvg", "liquidity_sweep"]
    assert g.get("gate_vector", {}).get("session") is False


def test_pnl_r_sign_matches_cash_pnl():
    """
    pnl_r used to be aliased to realized_rr, which is pure PRICE movement and
    ignores spread/commission/slippage. At a break-even exit that made price-R
    slightly POSITIVE while the cash PnL was NEGATIVE: 186 such trades in the
    saved book, and BE_SL summed to -$1,632 of cash against +11.82 R.
    """
    g = group_trades([_leg(
        exit_price=1.10001, pnl=-2.5, exit_reason="BE_SL", stop_loss=1.0990,
    )])[0]
    if g.get("pnl_r") is None:
        pytest.skip("pnl_r unavailable without broker symbol info in this env")
    assert g["pnl_r"] < 0, (
        f"BE_SL lost ${g['pnl']} of cash but reported pnl_r={g['pnl_r']} — "
        "pnl_r must be derived from realised cash, not price movement"
    )


def test_gate_recorder_is_off_by_default():
    """Telemetry must cost live trading nothing."""
    from backend.strategies.gate_recorder import GateRecorder
    r = GateRecorder()
    assert r.enabled is False
    assert r.gate("x", True) is True
    assert r.gate("x", False) is False
    assert r.records == []


def test_gate_recorder_records_and_attributes_the_blocking_gate():
    from backend.strategies.gate_recorder import GateRecorder
    r = GateRecorder(enabled=True)
    r.begin("EURUSD", "M5", 1)
    r.gate("session", True)
    r.gate("momentum", False)
    r.gate("never_reached", True)
    r.finish()
    assert r.strategy_rejections() == {"momentum": 1}
    assert r.summary()["gates"]["session"]["pass_rate"] == 1.0


def test_gate_recorder_disabled_gates_force_pass_for_ablation():
    """`disabled_gates` is what makes a true ablation re-run possible."""
    from backend.strategies.gate_recorder import GateRecorder
    r = GateRecorder(enabled=True, disabled_gates={"session"})
    r.begin("EURUSD", "M5", 1)
    assert r.gate("session", False) is True      # forced through
    assert r.gate("momentum", False) is False    # untouched
    r.finish()
    assert "session" not in r.strategy_rejections()
    assert r.summary()["disabled_gates"] == ["session"]


def test_every_strategy_exposes_the_gate_api():
    """
    All seven engines must inherit the recorder, or their confluences are
    invisible again and the ablation harness silently measures nothing.
    """
    from backend.strategies.registry import get_all_strategies
    strategies = get_all_strategies()
    assert strategies, "strategy registry is empty"
    for name, cls in strategies.items():
        assert hasattr(cls, "gate"), f"{name} has no .gate()"
        assert hasattr(cls, "begin_candidate"), f"{name} has no .begin_candidate()"
        src = inspect.getsource(cls)
        assert "self.gate(" in src or "begin_candidate" in src, (
            f"{name} inherits the gate API but never calls it — its confluences "
            "are unmeasurable"
        )


# ─── [B9] The two `_pct` conventions ─────────────────────────────────────────
# The codebase uses `_pct` for two different units, and both are internally
# consistent, so there is no bug to fix — only a trap to lock down:
#
#   RESULT metrics   fractions 0..1   win_rate=0.18, max_drawdown_pct=0.17
#                                     (frontend multiplies by 100 to display)
#   CONFIG limits    whole percents   max_daily_drawdown_pct=5.0, risk_per_trade_pct=1.0
#                                     (shown raw in the UI)
#
# Comparing one against the other without scaling would read 17% as 0.17 and
# silently never trip a 5% limit. circuit_breaker.py gets this right today
# (it multiplies by 100 before comparing); these tests keep it that way.

def test_result_metrics_are_fractions_not_percents():
    """generate_risk_report must emit win_rate / max_drawdown_pct in 0..1."""
    from backend.analytics.reports import generate_risk_report
    trades = [
        {"pnl": 100.0, "direction": "BUY", "confluence_score": 70,
         "balance_before": 10000.0, "balance_after": 10100.0, "exit_reason": "TP1"},
        {"pnl": -50.0, "direction": "SELL", "confluence_score": 70,
         "balance_before": 10100.0, "balance_after": 10050.0, "exit_reason": "SL"},
    ]
    rep = generate_risk_report(trades, initial_balance=10000.0)
    assert 0.0 <= rep.win_rate <= 1.0, (
        f"win_rate={rep.win_rate} is outside 0..1 — result metrics are fractions; "
        "something started emitting whole percents"
    )
    assert rep.win_rate == pytest.approx(0.5)
    assert 0.0 <= rep.max_drawdown_pct <= 1.0, (
        f"max_drawdown_pct={rep.max_drawdown_pct} is outside 0..1"
    )


def test_circuit_breaker_scales_before_comparing_to_percent_limits():
    """
    The drawdown check compares a computed drawdown against a whole-percent
    config limit, so it must scale. If this ever regresses, a 5% daily cap
    would compare 0.05 against 5.0 and never trigger — the breaker would look
    enabled and do nothing.
    """
    src = open("backend/risk/circuit_breaker.py", encoding="utf-8").read()
    assert "* 100" in src, (
        "circuit_breaker no longer scales drawdown to whole percent before "
        "comparing against max_daily_drawdown_pct / max_weekly_drawdown_pct"
    )


# ─── Per-strategy exit defaults: live and backtest must not diverge ──────────

def test_strategy_defaults_registry_is_well_formed():
    from backend.strategies.strategy_defaults import (
        STRATEGY_DEFAULTS, OVERRIDABLE, get_strategy_defaults,
    )
    from backend.strategies.registry import get_all_strategies

    registered = set(get_all_strategies().keys())
    for sid, cfg in STRATEGY_DEFAULTS.items():
        assert sid in registered or sid == "SMC_v1", f"{sid} is not a registered strategy"
        assert cfg.get("evidence"), f"{sid} has no evidence note — every default must trace to a measurement"
        for key in cfg:
            if key in ("evidence", "session_filter_enabled"):
                continue
            assert key in OVERRIDABLE, (
                f"{sid} overrides '{key}', which is not in OVERRIDABLE. Position sizing "
                "and drawdown caps are account-level and must stay global."
            )
    # evidence is never leaked into the applied config
    for sid in STRATEGY_DEFAULTS:
        assert "evidence" not in get_strategy_defaults(sid)


def test_unknown_strategy_gets_empty_defaults():
    """An unregistered strategy must fall back to plain globals, not raise."""
    from backend.strategies.strategy_defaults import get_strategy_defaults
    assert get_strategy_defaults("NoSuchStrategy_v9") == {}


def test_merge_order_user_override_wins():
    from backend.strategies.strategy_defaults import merge_strategy_defaults
    merged = merge_strategy_defaults(
        "NYOpenRetest_v1",
        {"trail_method_tp1": "NONE", "risk_per_trade_pct": 0.5},
        {"trail_method_tp1": "FIXED_PIPS"},
    )
    assert merged["trail_method_tp1"] == "FIXED_PIPS", "explicit user override must win"
    assert merged["risk_per_trade_pct"] == 0.5, "global fields must survive untouched"
    # and without an override, the measured default applies
    assert merge_strategy_defaults(
        "NYOpenRetest_v1", {"trail_method_tp1": "NONE"}
    )["trail_method_tp1"] == "ATR_TRAIL"


def test_trailing_verdicts_match_the_measurement():
    """
    Guards the actual conclusions, so a well-meaning edit cannot silently
    re-enable trailing where it was measured to lose money.
    """
    from backend.strategies.strategy_defaults import get_strategy_defaults
    # Only NYOpenRetest gained from trailing (+1,765 PnL, +15.5pp WR).
    assert get_strategy_defaults("NYOpenRetest_v1")["trail_method_tp1"] == "ATR_TRAIL"
    # These three netted flat-to-negative; DriftJumpAlpha lost 1,299 on Crash 1000.
    for sid in ("DriftJumpAlpha_v1", "VWAP_v1", "CRT_v1"):
        assert get_strategy_defaults(sid)["trail_method_tp1"] == "NONE", (
            f"{sid} had trailing re-enabled — the sweep measured it as flat or harmful"
        )


def test_session_verdicts_match_the_measurement():
    from backend.strategies.strategy_defaults import get_strategy_defaults
    # -0.170 contribution: actively harmful, removing it gains signals AND expectancy.
    assert get_strategy_defaults("HTFFVGFlip_v1")["session_filter_enabled"] is False
    # +0.009 while discarding 88.2% of candidates — the most expensive no-op found.
    assert get_strategy_defaults("CRT_v1")["session_filter_enabled"] is False
    # +0.064 and +0.126: these earn their place, so they must NOT be disabled.
    for sid in ("VWAP_v1", "BiasIFVG_v1"):
        assert "session_filter_enabled" not in get_strategy_defaults(sid)


def test_live_and_backtest_both_apply_strategy_defaults():
    """
    Both paths must consult the registry. A strategy that trails in backtest but
    not live makes every backtest result useless as a prediction of live.
    """
    for path in ("backend/services/bot_service.py", "backend/api/routes/backtest.py"):
        src = open(path, encoding="utf-8").read()
        assert "strategy_defaults" in src, f"{path} never applies per-strategy exit defaults"
        assert "session_filter_enabled" in src, f"{path} never applies the session verdict"


def test_apa_rejection_gate_no_longer_depends_on_retest_state():
    """
    APA emitted 0 signals from 78,800 candidates because `retest_rejected` is
    only set inside the AWAIT_RETEST branch, which `require_retest=False` skips,
    while `require_rejection_candle=True` tested that flag.
    """
    from backend.strategies.strategy_apa.engine import APAEngine
    assert hasattr(APAEngine, "_zone_rejected_now"), (
        "the rejection test must be evaluable without the retest state machine"
    )
    fn = APAEngine._zone_rejected_now
    latest = {"high": 1.1050, "low": 1.0950, "close": 1.1040, "open": 1.1000}
    # Bullish: wick dipped into the zone, body closed back above it -> rejected.
    assert fn({"direction": "BUY", "invalidation_zone_top": 1.1000,
               "invalidation_zone_bottom": 1.0900}, latest) is True
    # No zone -> cannot be evaluated, must not block.
    assert fn({"direction": "BUY"}, latest) is True
