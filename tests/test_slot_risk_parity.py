"""
A slot's trades must not depend on what the other slots are doing.

implementation/PER-SLOT-RISK-DESIGN-2026-09-19.md: every slot owns its risk
engine and circuit breaker, so "run alone" (the single-symbol backtester) and
"run in a basket" (the portfolio backtester) are the same run. These tests hold
both engines to that, and hold the per-slot drawdown brake to its measured shape.
"""

import numpy as np
import pandas as pd
import pytest

from backend.backtester.engine import BacktestEngine
from backend.backtester.portfolio_engine import PortfolioBacktestEngine
from backend.risk.slot_book import SlotBook, resolve_slot_risk_config

ZERO_COSTS = {"commission_per_lot": 0.0, "slippage_pips": 0.0, "exit_slippage_pips": 0.0, "spread_pips": 0.0,
              "stops_level_pips": 0.0, "swap_long_per_lot_per_day": 0.0, "swap_short_per_lot_per_day": 0.0,
              "stop_fill_model": "OFF"}
BASE = {**ZERO_COSTS, "risk_per_trade_pct": 1.0, "min_rr": 0.5, "max_risk_hard_cap_pct": 3.0,
        "tp_count": 1, "tp1_rr": 2.0, "tp_splits": [100], "be_mode": "NONE", "trail_method_tp1": "NONE",
        "trail_mode": "NONE", "max_daily_drawdown_pct": 100.0, "max_weekly_drawdown_pct": 100.0,
        "max_daily_trades": 50, "max_concurrent_positions": 20, "max_positions_per_symbol": 1,
        "slot_brake_r": 0.0}
T0 = 1_767_571_200  # 2026-01-05 00:00 UTC, a Monday


def _bars(n=900, seed=5, start=1.1000, scale=0.00018):
    """M5 bars that both win and lose, so stops and targets both fire."""
    rng = np.random.default_rng(seed)
    close = start + np.cumsum(rng.normal(0, scale, n)) + np.arange(n) * 1e-9
    open_ = np.r_[start, close[:-1]]
    wick = np.abs(rng.normal(0, scale * 0.6, n))
    t = T0 + 300 * np.arange(n)
    return pd.DataFrame({"time": t, "open": open_, "high": np.maximum(open_, close) + wick,
                         "low": np.minimum(open_, close) - wick, "close": close,
                         "spread": np.zeros(n), "tick_volume": np.full(n, 100.0)})


def _signals(df, symbol, strategy, every=40, first=30, stop=0.0020):
    """One signal every `every` bars, alternating direction."""
    out = []
    for k, i in enumerate(range(first, len(df) - 5, every)):
        long = k % 2 == 0
        price = float(df["close"][i])
        out.append({"symbol": symbol, "_cache_key": f"{symbol}|{strategy}", "strategy_name": strategy,
                    "direction": "BUY" if long else "SELL", "time": int(df["time"][i]),
                    "entry_price": price, "stop_loss": price - stop if long else price + stop,
                    "take_profit": price + 2 * stop if long else price - 2 * stop,
                    "timeframe": "M5", "confluence_score": 80,
                    "metadata": {"strategy_id": strategy, "slot_id": f"{symbol}|{strategy}"}})
    return out


def _fingerprint(trades, symbol=None):
    return [(t["symbol"], t["direction"], round(float(t["entry_time"]), 3), round(float(t["entry_price"]), 8),
             round(float(t["volume"]), 4), t["exit_reason"], round(float(t["pnl"]), 6))
            for t in trades if symbol is None or t["symbol"] == symbol]


def _single(df, signals, config, balance=10_000.0):
    return BacktestEngine(dict(config)).run(df, signals, balance, df, df, None, None, None)["trades"]


def _portfolio(frames, signals_by_slot, slot_configs, balance=10_000.0, base=None):
    symbol_map = {k: k.split("|")[0] for k in frames}
    res = PortfolioBacktestEngine(dict(base or BASE), slot_configs).run(
        frames, signals_by_slot, balance, None, None, symbol_map)
    return res


@pytest.fixture(scope="module")
def two_slots():
    a, b = _bars(seed=5), _bars(seed=11, start=1.3000)
    return {
        "EURUSD|A_v1": (a, _signals(a, "EURUSD", "A_v1")),
        "GBPUSD|B_v1": (b, _signals(b, "GBPUSD", "B_v1")),
    }


def test_a_slot_trades_the_same_alone_and_in_a_basket(two_slots):
    (df_a, sigs_a) = two_slots["EURUSD|A_v1"]
    alone = _single(df_a, sigs_a, BASE)
    assert len(alone) >= 6, f"fixture too quiet: {len(alone)}"

    frames = {k: v[0] for k, v in two_slots.items()}
    signals = {k: v[1] for k, v in two_slots.items()}
    res = _portfolio(frames, signals, {k: dict(BASE) for k in two_slots})
    assert _fingerprint(res["trades"], "EURUSD") == _fingerprint(alone), \
        "the same slot traded differently with another slot in the book"


def test_one_slots_daily_loss_limit_does_not_stop_another(two_slots):
    """Slot A stops for the day at a 1% daily drawdown; B has no such limit."""
    frames = {k: v[0] for k, v in two_slots.items()}
    signals = {k: v[1] for k, v in two_slots.items()}
    configs = {
        "EURUSD|A_v1": {**BASE, "max_daily_drawdown_pct": 1.0, "max_daily_trades": 2},
        "GBPUSD|B_v1": dict(BASE),
    }
    res = _portfolio(frames, signals, configs)
    a_trades = [t for t in res["trades"] if t["symbol"] == "EURUSD"]
    b_trades = [t for t in res["trades"] if t["symbol"] == "GBPUSD"]

    b_alone = _single(two_slots["GBPUSD|B_v1"][0], two_slots["GBPUSD|B_v1"][1], BASE)
    a_unlimited = _single(two_slots["EURUSD|A_v1"][0], two_slots["EURUSD|A_v1"][1], BASE)
    assert _fingerprint(b_trades) == _fingerprint(b_alone), "slot A's limits reached slot B"
    assert len(a_trades) < len(a_unlimited), "slot A's own limits did not bind"
    # each slot's realised record is its own, and so is its drawdown
    by_slot = res["circuit_breaker_summary"]["by_slot"]
    assert by_slot["EURUSD|A_v1"]["realised_r"] != by_slot["GBPUSD|B_v1"]["realised_r"]
    assert by_slot["GBPUSD|B_v1"]["drawdown_r"] == 0.0


def test_a_daily_trade_cap_is_counted_per_slot(two_slots):
    """One trade a day EACH, not one a day between them."""
    frames = {k: v[0] for k, v in two_slots.items()}
    signals = {k: v[1] for k, v in two_slots.items()}
    res = _portfolio(frames, signals, {k: {**BASE, "max_daily_trades": 1} for k in two_slots})
    per_day: dict[str, set] = {}
    for t in res["trades"]:
        day = pd.Timestamp(float(t["entry_time"]), unit="s").strftime("%Y-%m-%d")
        per_day.setdefault(day, set()).add(t["symbol"])
    assert any(len(syms) == 2 for syms in per_day.values()), \
        f"both slots never traded on the same day, so the cap was shared: {per_day}"
    for day, syms in per_day.items():
        for sym in syms:
            same = [t for t in res["trades"]
                    if t["symbol"] == sym
                    and pd.Timestamp(float(t["entry_time"]), unit="s").strftime("%Y-%m-%d") == day]
            assert len(same) == 1, f"{sym} took {len(same)} trades on {day} under a cap of 1"


def test_each_slot_keeps_its_own_targets_and_size(two_slots):
    frames = {k: v[0] for k, v in two_slots.items()}
    signals = {k: v[1] for k, v in two_slots.items()}
    configs = {
        "EURUSD|A_v1": {**BASE, "tp1_rr": 2.0, "risk_per_trade_pct": 1.0},
        "GBPUSD|B_v1": {**BASE, "tp1_rr": 5.0, "risk_per_trade_pct": 0.5},
    }
    res = _portfolio(frames, signals, configs)
    a = [t for t in res["trades"] if t["symbol"] == "EURUSD"]
    b = [t for t in res["trades"] if t["symbol"] == "GBPUSD"]
    assert a and b
    # B risks half as much per trade, on a symbol of the same contract size
    assert b[0]["volume"] == pytest.approx(a[0]["volume"] / 2, rel=0.35)
    r_a = abs(a[0]["take_profit"] - a[0]["entry_price"]) / abs(a[0]["entry_price"] - a[0]["initial_stop_loss"])
    r_b = abs(b[0]["take_profit"] - b[0]["entry_price"]) / abs(b[0]["entry_price"] - b[0]["initial_stop_loss"])
    assert r_a == pytest.approx(2.0, abs=0.05) and r_b == pytest.approx(5.0, abs=0.05)


def test_the_drawdown_brake_shrinks_only_the_slot_that_is_losing():
    """A slot 15R below its own peak trades at half size; 30R takes it to the floor."""
    from backend.risk.engine import RiskEngine

    eng = RiskEngine({**BASE, "slot_brake_r": 30.0, "slot_brake_floor": 0.25})
    sig = {"symbol": "EURUSD", "direction": "BUY", "entry_price": 1.10, "stop_loss": 1.0980,
           "confluence_score": 80, "strategy_name": "A_v1", "metadata": {}}

    ok, _, base_levels = eng.evaluate_signal(dict(sig), 10_000, initial_balance=10_000)
    assert ok
    full = sum(lv.volume for lv in base_levels)

    eng.circuit.peak_r, eng.circuit.cum_r = 0.0, -15.0
    ok, _, half_levels = eng.evaluate_signal(dict(sig), 10_000, initial_balance=10_000)
    assert ok
    assert sum(lv.volume for lv in half_levels) == pytest.approx(full * 0.625, rel=0.05)

    eng.circuit.cum_r = -30.0
    ok, _, floor_levels = eng.evaluate_signal(dict(sig), 10_000, initial_balance=10_000)
    assert ok
    assert sum(lv.volume for lv in floor_levels) == pytest.approx(full * 0.25, rel=0.05)

    off = RiskEngine({**BASE, "slot_brake_r": 0.0})
    off.circuit.peak_r, off.circuit.cum_r = 0.0, -30.0
    ok, _, unbraked = off.evaluate_signal(dict(sig), 10_000, initial_balance=10_000)
    assert ok and sum(lv.volume for lv in unbraked) == pytest.approx(full, rel=1e-6)


def test_a_slots_record_is_its_own():
    """R is booked on the slot that traded, never on the book."""
    book = SlotBook({**BASE, "is_backtest": True}, {"X|S": dict(BASE), "Y|S": dict(BASE)})
    x, y = book.engine("X|S"), book.engine("Y|S")
    x.circuit.position_opened("g1", 1, symbol="X", initial_risk_dollars=100.0, slot_id="X|S")
    x.circuit.record_backtest_close("g1", -100.0)
    assert x.circuit.cum_r == pytest.approx(-1.0)
    assert y.circuit.cum_r == 0.0 and y.circuit.drawdown_r() == 0.0
    assert x.circuit.drawdown_r() == pytest.approx(1.0)


def test_resolver_layers_account_strategy_and_slot():
    base = {"tp1_rr": 9.9, "risk_per_trade_pct": 1.0, "min_rr": 3.0, "prop_firm": {"x": 1}}
    cfg = resolve_slot_risk_config(base, "IVW_v1", overrides={"risk_per_trade_pct": 0.4, "prop_firm": {"y": 2}})
    assert cfg["tp1_rr"] == 2.0                    # the strategy's measured target wins over the account default
    assert cfg["risk_per_trade_pct"] == 0.4        # the slot wins over both
    assert cfg["min_rr_by_strategy"] == {"IVW_v1": 2.0}
    assert cfg["prop_firm"] == {"x": 1}            # account-only: a slot cannot set it
    kept = resolve_slot_risk_config(base, "IVW_v1", overrides={"tp1_rr": 4.0})
    assert kept["tp1_rr"] == 4.0
    no_defaults = resolve_slot_risk_config(base, "IVW_v1", use_strategy_exit_defaults=False)
    assert no_defaults["tp1_rr"] == 9.9


def test_both_routes_resolve_one_slot_profile_the_same_way():
    """A slot sent to the single backtest and as a portfolio row must resolve to
    the same risk config — otherwise "same settings" is a claim, not a fact."""
    from backend.api.routes.backtest import BacktestRequest, build_merged_risk_config

    profile = {"risk_per_trade_pct": 0.4, "max_daily_trades": 3, "max_daily_drawdown_pct": 8.0,
               "be_mode": "RR", "be_trigger_rr": 1.0, "slot_brake_r": 30.0}
    single = build_merged_risk_config(
        BacktestRequest(strategy_id="IVW_v1", symbol="EURUSD", slot_risk=profile))
    row = resolve_slot_risk_config(
        build_merged_risk_config(BacktestRequest(strategy_id="IVW_v1", symbol="EURUSD")),
        "IVW_v1", overrides=profile, use_strategy_exit_defaults=True)
    differing = {k: (single.get(k), row.get(k)) for k in set(single) | set(row)
                 if k != "_strategy_defaults_applied" and single.get(k) != row.get(k)}
    assert not differing, differing
    for key, value in profile.items():
        assert single[key] == value, key


def test_live_resolves_a_slot_exactly_as_the_backtest_does():
    """The live bot and the Backtester must agree on what a slot's settings are."""
    from backend.api.routes.backtest import BacktestRequest, build_merged_risk_config
    from backend.core.config_schema import InstrumentSlot, UserConfigV2
    from backend.risk.live_risk_config import build_live_risk_config
    from backend.risk.slot_book import resolve_slot_risk_config, slot_overrides_from

    profile = {"max_daily_drawdown_pct": 7.0, "be_mode": "RR", "be_trigger_rr": 1.0, "slot_brake_r": 30.0}
    slot = InstrumentSlot(symbol="EURUSD", strategy_id="IVW_v1", risk=dict(profile),
                          risk_per_trade_pct=0.4, max_trades_per_day=3)
    cfg = UserConfigV2()
    cfg.instrument_slots = [slot]

    live_base, _ = build_live_risk_config(cfg, "IVW_v1")
    live = resolve_slot_risk_config(live_base, "IVW_v1", overrides=slot_overrides_from(slot))

    sent = {**profile, "risk_per_trade_pct": 0.4, "max_daily_trades": 3}
    bt = build_merged_risk_config(BacktestRequest(strategy_id="IVW_v1", symbol="EURUSD", slot_risk=sent))

    for key in sent:
        assert live[key] == bt[key] == sent[key], (key, live.get(key), bt.get(key))
    # and the fields that decide entries and exits agree too
    for key in ("tp_count", "tp1_rr", "min_rr_by_strategy", "trail_mode", "trail_method_tp1"):
        assert live[key] == bt[key], (key, live.get(key), bt.get(key))


def test_a_slot_saved_without_a_risk_block_still_means_what_it_meant():
    from backend.core.config_schema import InstrumentSlot, UserConfigV2
    from backend.risk.slot_book import slot_overrides_from

    cfg = UserConfigV2.from_dict({"instrument_slots": [
        {"symbol": "GBPJPY", "strategy_id": "ORB_v1", "risk_per_trade_pct": 0.75, "max_trades_per_day": 2},
    ]})
    slot = cfg.instrument_slots[0]
    assert slot.risk == {}
    assert slot_overrides_from(slot) == {"risk_per_trade_pct": 0.75, "max_daily_trades": 2}


def test_a_slots_break_even_setting_actually_moves_its_stops(two_slots):
    """The complaint this whole redesign started from: a trailing / break-even
    setting that the backend quietly ignored. Same slot, same bars, same signals
    — only the slot's exit settings differ, so the trades must differ."""
    df, sigs = two_slots["EURUSD|A_v1"]
    off = _single(df, sigs, {**BASE, "be_mode": "NONE", "trail_mode": "NONE"})
    on = _single(df, sigs, {**BASE, "be_mode": "RR", "be_trigger_rr": 0.5, "be_buffer_pips": 0.5,
                            "trail_mode": "RR", "trail_trigger_rr": 0.8,
                            "trail_method_tp1": "ATR_TRAIL", "atr_trail_multiplier_tp1": 1.0})
    assert any(t.get("be_applied") or t.get("trail_applied") for t in on), \
        "break-even / trailing never fired, so the setting is not reaching the engine"
    assert not any(t.get("be_applied") or t.get("trail_applied") for t in off)
    assert {t["exit_reason"] for t in on} != {t["exit_reason"] for t in off} or \
        _fingerprint(on) != _fingerprint(off), "the exits changed nothing"


def test_the_same_exit_settings_apply_to_that_slot_in_a_basket(two_slots):
    """And the slot's exits follow it into a portfolio, unchanged."""
    exits = {"be_mode": "RR", "be_trigger_rr": 0.5, "be_buffer_pips": 0.5,
             "trail_mode": "RR", "trail_trigger_rr": 0.8,
             "trail_method_tp1": "ATR_TRAIL", "atr_trail_multiplier_tp1": 1.0}
    df, sigs = two_slots["EURUSD|A_v1"]
    alone = _single(df, sigs, {**BASE, **exits})
    frames = {k: v[0] for k, v in two_slots.items()}
    signals = {k: v[1] for k, v in two_slots.items()}
    res = _portfolio(frames, signals,
                     {"EURUSD|A_v1": {**BASE, **exits}, "GBPUSD|B_v1": dict(BASE)})
    assert _fingerprint(res["trades"], "EURUSD") == _fingerprint(alone)


def test_a_slot_can_refuse_the_measured_per_symbol_settings():
    """The slot editor's "use the measured settings" switch has to reach both
    backtest paths — live has honoured it since InstrumentSlot gained it."""
    from backend.api.routes.backtest import apply_strategy_params
    from backend.core.config_schema import UserConfigV2
    from backend.strategies.strategy_defaults import get_synth_slot_params

    measured = get_synth_slot_params("GBPJPY", "ORB_v1")
    assert measured, "fixture needs a symbol with measured settings"

    on = UserConfigV2()
    apply_strategy_params(on, "ORB_v1", {}, "GBPJPY")
    for key, value in measured.items():
        assert getattr(on.orb, key) == value, key

    off = UserConfigV2()
    apply_strategy_params(off, "ORB_v1", {}, "GBPJPY", use_measured_params=False)
    differing = {k: (getattr(off.orb, k), v) for k, v in measured.items() if getattr(off.orb, k) != v}
    assert differing, "the switch changed nothing"

    # an explicit parameter still wins either way
    explicit = UserConfigV2()
    apply_strategy_params(explicit, "ORB_v1", {"range_minutes": 45}, "GBPJPY")
    assert explicit.orb.range_minutes == 45


# ── The app must be usable on a phone ───────────────────────────────────────
#
# Found 2026-09-20 at 375px: the sidebar is a drawer below 768px (translated
# off-canvas) but the ONLY control that moved it was the collapse chevron, which
# is pinned to the panel and therefore off-screen with it. Every page rendered,
# and none of them could be left — the app had no navigation at all on a phone.

def test_the_sidebar_drawer_has_an_opener_that_is_not_inside_it():
    from pathlib import Path

    css = Path("frontend/src/index.css").read_text(encoding="utf-8")
    app = Path("frontend/src/App.jsx").read_text(encoding="utf-8")

    assert ".sidebar.open {" in css, "the drawer needs an open state"
    # the opener lives in the layout, NOT in <aside>, or it slides away with it
    assert 'className="nav-open"' in app
    aside = app[app.index("<aside className={`sidebar"):app.index("</aside>")]
    assert "nav-open" not in aside, "the opener must sit outside the sidebar, or it goes off-screen with it"
    layout = app.split("function AppContent()")[1]
    assert '<button className="nav-open"' in layout, "the opener belongs to the layout, next to <main>"
    assert ".nav-open {\n    display: flex;" in css, \
        "the opener must be shown inside the phone breakpoint"
    # and tapping a link must close it, so the destination is not behind the panel
    assert "onClick={closeNav}" in app


def test_the_phone_drawer_closes_itself_on_navigation():
    from pathlib import Path

    app = Path("frontend/src/App.jsx").read_text(encoding="utf-8")
    assert "const { pathname } = useLocation();" in app
    # derived, not an effect: the drawer is open only for the route it opened on
    assert "const isNavOpen = navOpenAt === pathname;" in app, \
        "a route change from anywhere (a redirect, a card link) must close the drawer"


# ── One place per setting ───────────────────────────────────────────────────
#
# Found 2026-09-20 by the user: after risk moved to the slot, the Backtester
# still carried a global "Advanced Parameters" copy of the same fields, and
# Settings > Defaults still carried per-strategy blocks (one of them, CRT, for a
# strategy the backend no longer has at all). Both looked live. Neither was: a
# portfolio row resolves its slot's value over the global one, so editing the
# global copy changed nothing while appearing to.

def test_the_backtester_has_no_second_global_copy_of_slot_risk():
    from pathlib import Path

    js = Path("frontend/src/pages/Backtester.jsx").read_text(encoding="utf-8")
    assert "Advanced Parameters" not in js, \
        "risk, targets, break-even and trailing belong to the slot editor only"
    assert "showAdvanced" not in js
    assert "<SlotEditor" in js


def test_the_defaults_page_carries_no_strategy_parameters():
    from pathlib import Path

    js = Path("frontend/src/pages/Settings/Risk.jsx").read_text(encoding="utf-8")
    for block in ("crt", "vwap", "apa", "orb"):
        assert f"config.{block}" not in js, \
            f"{block} parameters belong to the slot that runs them, not to a global page"
    # it shows the slot's own sections, from the schema, so the two cannot drift
    assert "SLOT_RISK_SECTIONS" in js
    # and it saves only what it owns, so it cannot overwrite a slot or a strategy
    assert "mutation.mutate({ risk: config.risk, prop_firm: config.prop_firm })" in js


def test_crt_is_gone_from_the_backend_too():
    """The UI block configured a strategy that no longer exists."""
    from backend.core.config_schema import UserConfig
    from backend.strategies.registry import list_strategies

    assert not any("crt" in sid.lower() for sid in list_strategies())
    assert not hasattr(UserConfig(), "crt")


def test_every_symbol_field_accepts_a_symbol_the_list_does_not_have():
    """A broker lists names the app does not know (Deriv: "US Tech 100").

    A <select> made those untradable from the UI, so all three screens use the
    free-text picker and the list is a suggestion.
    """
    from pathlib import Path

    picker = Path("frontend/src/components/SymbolPicker.jsx").read_text(encoding="utf-8")
    assert 'type="text"' in picker, "the symbol field must be typable"
    # the only "<select" in the file is the comment explaining why there is none
    assert "<select " not in picker and "<select>" not in picker.split("*/", 1)[1]

    for path in ("frontend/src/components/SlotEditor.jsx",
                 "frontend/src/pages/Backtester.jsx",
                 "frontend/src/pages/Settings/Strategy.jsx"):
        js = Path(path).read_text(encoding="utf-8")
        assert "SymbolPicker" in js, f"{path} must pick symbols through the shared picker"

    # one list for all three, and it asks the broker before falling back
    hook = Path("frontend/src/hooks/useSymbolOptions.js").read_text(encoding="utf-8")
    assert "getInstrumentResolution" in hook
    assert "US Tech 100" in hook, "the fallback list must carry broker names, not only canonical ones"


# ── A strategy's own guardrails must read the SLOT's risk ───────────────────
#
# Found 2026-09-21 on a live run: DriftJumpAlpha on Crash 1000, 0 trades from
# 77,107 bars, funnel says `daily_risk_cap`. The strategy blocks a bar when
# risk_per_trade_pct alone would breach ITS max_daily_risk_pct (default 4%), and
# the run risked 5% -- so it refused every bar before forming a single signal.
#
# Two separate problems: the gate is invisible until you read the breakdown
# (now warned about in the slot editor), and it was reading the REQUEST's risk
# rather than the slot's, so a row that overrode risk was gated on a number it
# never traded with.

def test_the_strategy_config_takes_the_slots_resolved_risk():
    from backend.api.routes.backtest import apply_resolved_risk_to_strategy_config
    from backend.core.config_schema import UserConfigV2

    config = UserConfigV2()
    config.risk.risk_per_trade_pct = 5.0      # the page's value
    apply_resolved_risk_to_strategy_config(config, {
        "risk_per_trade_pct": 0.25,            # what this slot actually risks
        "max_daily_trades": 3,
        "not_a_risk_field": "ignored",
    })
    assert config.risk.risk_per_trade_pct == 0.25
    assert config.risk.max_daily_trades == 3
    assert not hasattr(config.risk, "not_a_risk_field")


def test_both_backtest_routes_hand_the_strategy_the_resolved_risk():
    import inspect

    from backend.api.routes import backtest as bt

    src = inspect.getsource(bt)
    calls = src.count("apply_resolved_risk_to_strategy_config(config,") - src.count(
        "def apply_resolved_risk_to_strategy_config(config,")
    assert calls == 2, "the single run and every portfolio row must both do it"
    assert "apply_resolved_risk_to_strategy_config(config, build_merged_risk_config(req))" in src
    assert "apply_resolved_risk_to_strategy_config(config, portfolio_slot_configs[slot_key])" in src


def test_risk_above_the_strategys_daily_cap_blocks_every_bar():
    """The interaction itself, so the cause stays documented in a test."""
    import asyncio

    import numpy as np
    import pandas as pd

    from backend.core.config_schema import UserConfigV2
    from backend.strategies.registry import get_strategy

    idx = pd.date_range("2026-01-01", periods=200, freq="5min", tz="UTC")
    close = 1000 + np.cumsum(np.random.default_rng(7).normal(0, 1, 200))
    bars = pd.DataFrame({"open": close, "high": close + 2, "low": close - 2,
                         "close": close, "tick_volume": 100}, index=idx)

    async def gate_stats(risk_pct: float) -> dict:
        config = UserConfigV2()
        config.risk.risk_per_trade_pct = risk_pct
        engine = get_strategy("DriftJumpAlpha_v1")(config)
        engine.is_backtesting = True
        engine.gates.enabled = True
        for i in range(80, 200):
            await engine.on_bar("Crash 1000 Index", "M5", bars.iloc[:i])
        summary = engine.gates.summary()
        return (summary.get("gates") or summary)["daily_risk_cap"]

    # default cap is 4%: 5% per trade cannot pass on any bar, ever
    blocked = asyncio.run(gate_stats(5.0))
    assert blocked["passed"] == 0 and blocked["failed"] == blocked["evaluated"] > 0

    allowed = asyncio.run(gate_stats(3.0))
    assert allowed["failed"] == 0, "3% is inside the 4% cap and must reach the entry logic"


def test_the_slot_editor_warns_before_a_run_that_cannot_signal():
    from pathlib import Path

    js = Path("frontend/src/components/SlotEditor.jsx").read_text(encoding="utf-8")
    assert "max_daily_risk_pct" in js and "max_trades_per_day" in js
    assert "slot-editor__blocked" in js, "the collapsed row must show it too"
    assert "slot-editor__warn" in js

    bt = Path("frontend/src/pages/Backtester.jsx").read_text(encoding="utf-8")
    assert "daily_risk_cap:" in bt, "the run report must explain this gate in words"


# ── The backtest must run the SAVED strategy parameters ─────────────────────
#
# Found 2026-09-21 on the same run: Settings had DriftJumpAlpha at
# max_daily_risk_pct = 20 and the slot risked 5%, which passes — but the engine
# used the DATACLASS default of 4% and blocked all 77,107 bars. Both backtest
# routes build a fresh UserConfigV2 and write only the slot's explicit
# overrides onto it, so anything set in Settings and not overridden on the slot
# reverted to a default. bot_service builds a live engine from the SAVED config,
# so the two paths ran different parameters for the same slot.

def test_the_backtest_starts_from_the_saved_strategy_block():
    from backend.api.routes.backtest import seed_strategy_blocks
    from backend.core.config_schema import UserConfigV2

    config = UserConfigV2()
    assert config.drift_jump_alpha.max_daily_risk_pct == 4.0, "the dataclass default"

    seed_strategy_blocks(config, {"drift_jump_alpha": {"max_daily_risk_pct": 20,
                                                       "max_trades_per_day": 20},
                                  "orb": {"range_minutes": 60}})
    assert config.drift_jump_alpha.max_daily_risk_pct == 20
    assert config.drift_jump_alpha.max_trades_per_day == 20
    assert config.orb.range_minutes == 60


def test_seeding_ignores_keys_a_block_does_not_have():
    from backend.api.routes.backtest import seed_strategy_blocks
    from backend.core.config_schema import UserConfigV2

    config = UserConfigV2()
    seed_strategy_blocks(config, {"drift_jump_alpha": {"not_a_field": 1, "max_daily_risk_pct": 9},
                                  "no_such_block": {"x": 1}})
    assert config.drift_jump_alpha.max_daily_risk_pct == 9
    assert not hasattr(config.drift_jump_alpha, "not_a_field")


def test_both_routes_seed_the_saved_blocks_before_the_slot_overrides():
    import inspect

    from backend.api.routes import backtest as bt

    src = inspect.getsource(bt)
    calls = src.count("seed_strategy_blocks(config,") - src.count("def seed_strategy_blocks(config,")
    assert calls == 2, "the single run and every portfolio row must both seed"
    # order matters: the slot's own parameters are applied AFTER the saved block
    for marker in ("seed_strategy_blocks(config, await load_saved_strategy_blocks(current_user.id))",
                   "seed_strategy_blocks(config, _saved_strategy_blocks)"):
        seed_at = src.index(marker)
        apply_at = src.index("apply_strategy_params(config", seed_at)
        assert seed_at < apply_at


# ── A run that fails must say so ────────────────────────────────────────────
#
# Found 2026-09-21 while reproducing the run above: the data fetch failed in
# under a second ("MT5 returned no data"), the handler broadcast over the
# websocket and returned, and the PERSISTED state stayed
# {"status": "running", "stage": "Fetching historical data...", "pct": 5}.
# The page showed a run that never finished — indistinguishable from a slow one
# — and every later run was refused with "A backtest is already running".

def test_a_failed_fetch_writes_an_error_state():
    import inspect

    from backend.api.routes import backtest as bt

    src = inspect.getsource(bt)
    start = src.index("except DataFetchError as e:")
    end = src.index("def _index_candles", start)
    block = src[start:end]
    assert block.count("await _fail(") == 2, \
        "both the fetch failure and the empty-data case must persist an error state"
    assert "broadcast_to_user(current_user.id, {\"type\": \"backtest_error\"" not in block, \
        "a websocket broadcast alone leaves the saved state at 'running'"


def test_a_dead_run_does_not_lock_the_user_out():
    import time

    from backend.api.routes.backtest import STALE_RUN_SECONDS, _is_live_run

    now = time.time()
    assert _is_live_run({"status": "running", "heartbeat": now}) is True
    assert _is_live_run({"status": "running", "heartbeat": now - STALE_RUN_SECONDS - 1}) is False
    assert _is_live_run({"status": "completed", "heartbeat": now}) is False
    assert _is_live_run(None) is False
    # a state written before heartbeats existed is taken at its word
    assert _is_live_run({"status": "running"}) is True


def test_both_run_endpoints_use_the_liveness_check():
    import inspect

    from backend.api.routes import backtest as bt

    src = inspect.getsource(bt)
    assert src.count("if _is_live_run(state):") == 2, \
        "the single and the portfolio endpoint must both allow a new run after a dead one"
    # `_time_mod`, the MODULE-level clock: both task bodies import `time as
    # _time` further down, which would make this closure read an unbound local
    # and kill the task between "queued" and "started".
    assert src.count('state["heartbeat"] = _time_mod.time()') == 2, "both tasks must stamp their state, or liveness cannot be judged"
    assert 'state["heartbeat"] = _time.time()' not in src


# ── The editor must show the exits that will actually run ───────────────────
#
# Found 2026-09-21 comparing two deployments with "the same settings": one ran
# 55% trailing exits, the other none. resolve_slot_risk_config lays a strategy's
# MEASURED exits over the account default for every field the slot has not set
# itself, and DriftJumpAlpha's are trail_mode=NONE, trail_method_tp1=NONE,
# be_mode=TP_HIT, tp1_rr=5. The slot editor was showing the account's
# "trail EITHER / ATR_TRAIL", so the run silently did something else.

def test_a_strategys_measured_exits_beat_the_account_default():
    from backend.risk.slot_book import resolve_slot_risk_config

    account = {"tp1_rr": 2.0, "trail_mode": "EITHER", "trail_method_tp1": "ATR_TRAIL",
               "be_mode": "EITHER"}

    inherited = resolve_slot_risk_config(account, "DriftJumpAlpha_v1", overrides={},
                                         use_strategy_exit_defaults=True)
    assert inherited["trail_mode"] == "NONE"
    assert inherited["trail_method_tp1"] == "NONE"
    assert inherited["be_mode"] == "TP_HIT"
    assert inherited["tp1_rr"] == 5.0

    # a slot that sets the field itself still wins
    explicit = resolve_slot_risk_config(account, "DriftJumpAlpha_v1",
                                        overrides={"trail_mode": "EITHER"},
                                        use_strategy_exit_defaults=True)
    assert explicit["trail_mode"] == "EITHER"

    # and so does turning the measured exits off
    off = resolve_slot_risk_config(account, "DriftJumpAlpha_v1", overrides={},
                                   use_strategy_exit_defaults=False)
    assert off["trail_mode"] == "EITHER"


def test_the_slot_editor_shows_the_measured_exit_that_will_run():
    from pathlib import Path

    js = Path("frontend/src/components/SlotEditor.jsx").read_text(encoding="utf-8")
    # the risk rows are rebased on the measured value, not only the account's
    assert "const measured = measuredExits?.[name];" in js
    assert "default: measured, measuredBy: slot.strategy_id" in js
    # and the panel says which fields the strategy decided
    assert "slot-editor__note--measured" in js

    for path in ("frontend/src/pages/Backtester.jsx",
                 "frontend/src/pages/Settings/Strategy.jsx"):
        page = Path(path).read_text(encoding="utf-8")
        assert "measuredExitsFor(" in page, f"{path} must pass the measured exits to the editor"
        assert "use_strategy_exit_defaults === false" in page, \
            f"{path} must stop showing them when the account has turned them off"


# ── Every setting the engine honours must be reachable ──────────────────────
#
# Audited 2026-09-21 against the pre-redesign screens (d7f3a08): 18 RiskParams
# fields the old UI wrote were unreachable in the new one and ALL 18 were still
# read by the engine — the exit ladder (tp4/tp5, tp_splits, trail methods 3-5,
# the per-TP ATR multipliers, trail_pips/pct/structure_bars, the BE spread and
# TP-level triggers) and `use_strategy_exit_defaults`, the switch that decides
# whether a strategy's measured exits replace them at all. A further 25 fields
# had never been exposed by either UI.
#
# So coverage is no longer a hand-kept list: the slot editor renders every risk
# field that is not account-owned, and this test holds that to the dataclass.

def _ui_risk_keys():
    import re
    from pathlib import Path

    spec = Path("frontend/src/components/slotSpec.js").read_text(encoding="utf-8")
    sections = spec.split("SLOT_RISK_SECTIONS")[1].split("];")[0]
    named = set(re.findall(r"'([a-z0-9_]+)'", sections))
    account = set(re.findall(r"'([a-z0-9_]+)'",
                             spec.split("ACCOUNT_ONLY_KEYS = [")[1].split("];")[0]))
    return named, account


def test_the_frontends_account_only_list_matches_the_backends():
    from backend.risk.slot_book import ACCOUNT_ONLY_KEYS

    _, account = _ui_risk_keys()
    assert account == set(ACCOUNT_ONLY_KEYS), (
        "slotSpec.js mirrors risk/slot_book.py — a key in one and not the other "
        "means a setting is editable where it has no effect, or vice versa"
    )


def test_every_risk_field_is_reachable_somewhere_in_the_ui():
    from pathlib import Path

    from backend.core.config_schema import RiskParams
    from backend.risk.slot_book import ACCOUNT_ONLY_KEYS

    named, _ = _ui_risk_keys()
    editor = Path("frontend/src/components/SlotEditor.jsx").read_text(encoding="utf-8")
    page = Path("frontend/src/pages/Settings/Risk.jsx").read_text(encoding="utf-8")

    # the leftovers bucket: everything not named and not account-owned
    assert "sections.push(['Advanced', rest]);" in editor
    assert "!named.has(name) && !ACCOUNT_ONLY_KEYS.includes(name)" in editor
    # and the account's own fields are derived from the same list
    assert "const ACCOUNT_KEYS = ['use_strategy_exit_defaults', ...ACCOUNT_ONLY_KEYS];" in page

    # nothing in the dataclass can therefore be unreachable
    fields = set(vars(RiskParams()).keys())
    unreachable = fields - named - set(ACCOUNT_ONLY_KEYS) - {"use_strategy_exit_defaults"}
    # these land in the Advanced bucket; assert the mechanism covers them
    assert unreachable, "sanity: there should be fields relying on the bucket"
    for key in ("confluence_risk_tiers", "min_stop_spread_multiple", "multi_position_mode",
                "vol_target_annual_pct", "max_cluster_risk_pct"):
        assert key in unreachable and key in fields


def test_the_exit_ladder_is_editable_again():
    named, _ = _ui_risk_keys()
    for key in ("tp4_rr", "tp5_rr", "tp_splits", "trail_method_tp3", "trail_method_tp4",
                "trail_method_tp5", "atr_trail_multiplier", "atr_trail_multiplier_tp2",
                "atr_trail_multiplier_tp5", "trail_pips", "trail_pct",
                "trail_structure_bars", "trail_trigger_tp_level", "be_spread_multiple",
                "be_trigger_tp_level"):
        assert key in named, f"{key} is read by the engine and must be on the slot"


def test_a_slot_is_not_asked_about_targets_it_does_not_take():
    from pathlib import Path

    editor = Path("frontend/src/components/SlotEditor.jsx").read_text(encoding="utf-8")
    assert "const level = TP_LEVEL_OF(" in editor
    assert "return level === null || level <= tpCount;" in editor


# ── STATIC must mean a number you chose ─────────────────────────────────────
#
# `sizing_basis=STATIC` sizes every position against `static_balance`. A
# backtest passed the balance typed on the run; live passed
# prop_firm.initial_balance for a prop account and, for a PERSONAL account, the
# first balance the process happened to observe — so the anchor moved on every
# restart and could not be set at all. Same setting, two different numbers.

def test_a_stated_capital_is_what_static_sizes_against():
    from backend.core.config_schema import RiskParams
    from backend.risk.position_sizer import resolve_sizing_base_balance

    assert RiskParams().sizing_static_balance is None, "opt-in: unset changes nothing"

    # STATIC uses whatever the caller passes as the anchor...
    assert resolve_sizing_base_balance("STATIC", static_balance=5000.0,
                                       live_balance=12345.0, live_equity=9999.0) == 5000.0
    # ...and the other two are untouched by it
    assert resolve_sizing_base_balance("BALANCE", static_balance=5000.0,
                                       live_balance=12345.0, live_equity=9999.0) == 12345.0
    assert resolve_sizing_base_balance("EQUITY", static_balance=5000.0,
                                       live_balance=12345.0, live_equity=9999.0) == 9999.0


def test_every_path_anchors_static_on_the_stated_capital():
    import inspect

    from backend.backtester import engine as single
    from backend.backtester import portfolio_engine as portfolio
    from backend.risk import live_risk_config
    from backend.services import bot_service

    assert 'self.risk_config.get("sizing_static_balance")' in inspect.getsource(single)
    assert '_slot_cfg.get("sizing_static_balance")' in inspect.getsource(portfolio)
    live = inspect.getsource(bot_service)
    assert '_stated = getattr(config.risk, "sizing_static_balance", None)' in live
    assert "_static_balance = float(_stated)" in live, \
        "a stated capital must beat both the firm's figure and the restart anchor"
    assert "sizing_static_balance" in inspect.getsource(live_risk_config), \
        "it must reach the live risk config like every other risk field"


def test_the_stated_capital_is_editable_and_resolves_per_slot():
    from backend.risk.slot_book import resolve_slot_risk_config

    named, _ = _ui_risk_keys()
    assert "sizing_static_balance" in named, "it belongs beside the sizing basis it modifies"

    resolved = resolve_slot_risk_config({}, "ORB_v1", overrides={
        "sizing_basis": "STATIC", "sizing_static_balance": 5000.0})
    assert resolved["sizing_static_balance"] == 5000.0
