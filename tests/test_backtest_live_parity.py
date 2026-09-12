"""
tests/test_backtest_live_parity.py

Regressions for the backtest<->live divergences found on 2026-09-10 by comparing
`Implementation/resources/backtest_run_cf9355c6.json` against the live journal
for the same days, symbols and strategy.

Each test names the divergence it locks down, and what it cost.
"""

import pytest


# ── [P1.4] Confluence-scaled risk reached the backtest and not live ──────────
#
# RiskEngine.evaluate_signal reads `confluence_score` off the signal dict
# (falling back to metadata). The backtest route put it there; bot_service did
# not, and the strategies carry it as a TradeSignal FIELD rather than a metadata
# key — so live resolved None and skipped the tier scaling entirely.
# Measured: backtest sized SpikeFade at 1.35% (score 70 -> 75% of 1.8%), live
# sized at the full 1.8%. A third more risk per trade than any backtest reported.

def test_live_signal_dict_carries_confluence_score():
    import inspect

    from backend.services import bot_service as bs

    src = inspect.getsource(bs)
    marker = '"confluence_score": getattr(signal, "confluence_score", None),'
    assert marker in src, (
        "bot_service must put confluence_score on the signal dict it hands "
        "RiskEngine.evaluate_signal, or live silently sizes larger than every backtest."
    )


def test_confluence_tier_ladder_scales_a_70_score_to_three_quarters():
    from backend.risk.position_sizer import get_confluence_scaled_risk

    # The shipped ladder is [(80, 100%), (65, 75%), (55, 50%)]. SpikeFade emits
    # a hardcoded 70 for every signal, which lands in the 65 tier.
    assert get_confluence_scaled_risk(1.8, 70) == pytest.approx(1.35)
    assert get_confluence_scaled_risk(1.8, 85) == pytest.approx(1.8)
    assert get_confluence_scaled_risk(1.8, 60) == pytest.approx(0.9)


# ── [P1.8] Strategy history window differed between the two paths ────────────
#
# The backtest sliced M5 to 500 bars; live handed the engine 5,000. Any
# indicator whose value depends on visible history (ADX, a long EMA, a lookback
# percentile) therefore computed something different live, on identical data.

def test_window_table_is_the_single_source_for_both_paths():
    import inspect

    from backend.api.routes import backtest as bt_routes
    from backend.services import bot_service as bs
    from backend.strategies.windows import window_bars

    assert window_bars("M5") == 500
    assert window_bars("M15") == 300
    assert window_bars("nonsense") == window_bars("M5")

    bt_src = inspect.getsource(bt_routes)
    assert '_window_bars("M5")' in bt_src, "backtest TF_META must read the shared table"
    assert "from backend.strategies.windows import window_bars" in inspect.getsource(bs), \
        "the live scan loop must trim to the shared window"


# ── [P1.6] allow_pyramiding behaved differently in three code paths ──────────
#
# portfolio_engine ignored the flag entirely (always blocking same-direction
# entries), engine.py honoured it, and bot_service forwarded it to RiskEngine.
# So the same slot produced different trade counts depending on which engine
# ran it, and "profitable standalone, unprofitable in a portfolio" had a
# mechanical cause that had nothing to do with the portfolio.

def test_both_backtest_engines_honour_allow_pyramiding():
    import inspect

    from backend.backtester import engine as single
    from backend.backtester import portfolio_engine as portfolio

    for mod, name in ((single, "engine.py"), (portfolio, "portfolio_engine.py")):
        src = inspect.getsource(mod)
        assert 'allow_pyramiding = bool(self.risk_config.get("allow_pyramiding", False))' in src, \
            f"{name} must read allow_pyramiding"
        assert "if not allow_pyramiding:" in src, \
            f"{name} must skip the same-direction block when pyramiding is on"
        assert 'min_bars_between_entries' in src, \
            f"{name} must enforce the companion throttle"


def test_pyramiding_throttle_is_slot_keyed_not_symbol_keyed():
    """[P2.4] Two slots on one symbol must throttle independently."""
    import inspect

    from backend.backtester import engine as single
    from backend.backtester import portfolio_engine as portfolio

    assert "_last_entry_bar_by_slot" in inspect.getsource(portfolio)
    src = inspect.getsource(single)
    assert "self._last_entry_bar_by_symbol[_slot_key] = i" in src, \
        "the single-symbol engine must key its throttle by slot, not bare symbol"


# ── [P1.11] The rejection funnel did not reconcile ──────────────────────────
#
# `total_evaluated` was incremented AFTER the concurrency gates had already
# dropped signals, so on the user's own run the funnel read 144 evaluated /
# 76 blocked / 132 approved and could not answer "how many setups did the
# strategy actually find?".

def test_funnel_has_a_census_and_a_pre_risk_bucket():
    from backend.backtester.engine import BacktestEngine
    from backend.backtester.portfolio_engine import PortfolioBacktestEngine

    for cls in (BacktestEngine, PortfolioBacktestEngine):
        eng = cls({"risk_per_trade_pct": 1.0})
        f = eng.rejection_funnel
        assert f["raw_signals"] == 0
        assert f["pre_risk_rejections"] == {}


def test_pre_risk_gates_are_counted_even_past_the_display_cap():
    """`blocked_signals` is a capped sample for the UI; the funnel is a census.
    Counting inside the cap would silently undercount a busy run."""
    from backend.backtester.portfolio_engine import PortfolioBacktestEngine

    eng = PortfolioBacktestEngine({"risk_per_trade_pct": 1.0})
    eng._blocked_signals_cap = 3
    sig = {"symbol": "Crash 1000 Index", "direction": "BUY",
           "entry_price": 1.0, "stop_loss": 0.9}
    for _ in range(50):
        eng._record_blocked(sig, 0, "same_direction_already_open")

    assert len(eng.blocked_signals) == 3, "the UI sample stays capped"
    assert eng.rejection_funnel["pre_risk_rejections"]["same_direction_already_open"] == 50, \
        "the census must count every drop"


def test_risk_engine_gates_do_not_land_in_the_pre_risk_bucket():
    from backend.backtester.portfolio_engine import PortfolioBacktestEngine

    eng = PortfolioBacktestEngine({"risk_per_trade_pct": 1.0})
    eng._record_blocked({"symbol": "X", "direction": "BUY"}, 0, "risk_engine", "min lot")
    assert eng.rejection_funnel["pre_risk_rejections"] == {}


# ── [P1.2] Both engines must build a stop-fill model ─────────────────────────

def test_both_engines_build_a_stop_fill_model_and_report_it():
    from backend.backtester.engine import BacktestEngine
    from backend.backtester.portfolio_engine import PortfolioBacktestEngine

    for cls in (BacktestEngine, PortfolioBacktestEngine):
        eng = cls({"risk_per_trade_pct": 1.0})
        assert eng._fill_model.mode == "CONSERVATIVE", \
            "the realistic fill model must be the DEFAULT, not opt-in"
        assert eng._fill_model.summary()["mode"] == "CONSERVATIVE"


def test_stop_fill_model_is_configurable_from_risk_config():
    from backend.backtester.portfolio_engine import PortfolioBacktestEngine

    eng = PortfolioBacktestEngine({"stop_fill_model": "OFF"})
    assert eng._fill_model.mode == "OFF"
    eng = PortfolioBacktestEngine({"stop_fill_model": "nonsense"})
    assert eng._fill_model.mode == "CONSERVATIVE", "an unknown mode must fail safe, not fail open"


# ── [P1.12] The Backtester ignored every saved strategy block ───────────────
#
# The seeding effect spread the component's own hardcoded initial state LAST,
# and that state is fully populated — so every key of the user's saved config
# was overwritten by a form default. Settings had the synth block at 20/20 and
# the live bot ran 20/20 while the Backtester silently ran 6/4.0, a daily risk
# cap that stops the engine after 2 trades a day and blinds it to the rest of
# the session. Observed in backtest_Boom_1000_Index_f8f2bb33: 0 trades.

def test_backtester_seeds_strategy_blocks_from_the_saved_config():
    from pathlib import Path

    js = Path("frontend/src/pages/Backtester.jsx").read_text(encoding="utf-8")
    for block in ("apa", "vwap", "orb", "drift_jump_alpha",
                  "boom_drift_jump"):
        good = f"merged.{block} = {{ ...(prev.{block} || {{}}), ...(c.{block} || {{}}) }};"
        bad = f"merged.{block} = {{ ...(c.{block} || {{}}), ...(prev.{block} || {{}}) }};"
        assert good in js, f"{block}: saved config must be spread last so it wins"
        assert bad not in js, f"{block}: form defaults must not overwrite the saved config"


def test_backtester_hard_cap_prefers_the_saved_config():
    from pathlib import Path

    js = Path("frontend/src/pages/Backtester.jsx").read_text(encoding="utf-8")
    assert ("merged.max_risk_hard_cap_pct = c.risk?.max_risk_hard_cap_pct "
            "?? prev.max_risk_hard_cap_pct ?? 3.0;") in js
