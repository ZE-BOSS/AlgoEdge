"""SpikeResumption_v1 must trade the rule that was measured, on all three paths.

The rule: on BOOM (spikes UP, drifts DOWN) an established downward drift, ONE
bar breaking it with a sharp buy, the next bar closing bearish, then SELL. CRASH
is the mirror. Out at the target, the stop, or `max_hold_bars` bars.

Two things here are easy to get wrong in a way no run would reveal:

  * THE DIRECTION. Boom sells and Crash buys. Inverting it turns a strategy that
    does not work into one that loses at speed, and nothing in a backtest's shape
    would announce it.
  * WHICH BAR'S ATR sets the stop. The measured rule uses the SPIKE bar's ATR,
    not the entry bar's. They differ most exactly when the spike was large, which
    is every trade this strategy takes.
"""

import asyncio

import numpy as np
import pandas as pd
import pytest

from backend.core.config_schema import UserConfigV2
from backend.strategies.registry import get_strategy
from backend.strategies.strategy_spike_resumption.engine import atr, spike_size
from backend.strategies.strategy_spike_resumption.params import SpikeResumptionParams
from backend.strategies.windows import window_bars

T0 = 1_767_571_200          # 2026-01-05 00:00 UTC, a Monday
STEP = 900                  # M15


def _engine(**kw):
    cfg = UserConfigV2()
    cfg.spike_resumption = SpikeResumptionParams(**kw)
    return get_strategy("SpikeResumption_v1")(cfg)


def _frame(close, open_=None, high=None, low=None):
    n = len(close)
    close = np.asarray(close, dtype=float)
    open_ = np.asarray(open_, dtype=float) if open_ is not None else np.r_[close[0], close[:-1]]
    high = np.asarray(high, dtype=float) if high is not None else np.maximum(open_, close) + 0.05
    low = np.asarray(low, dtype=float) if low is not None else np.minimum(open_, close) - 0.05
    t = T0 + STEP * np.arange(n)
    return pd.DataFrame({"time": t, "open": open_, "high": high, "low": low,
                         "close": close, "spread": np.zeros(n),
                         "tick_volume": np.full(n, 100.0)},
                        index=pd.to_datetime(t, unit="s"))


def _boom_setup(n=400, drift=-1.0, spike=20.0, seed=1):
    """A long downward drift, ONE big up bar, then a red bar. A Boom chart.

    The drift has to be steep relative to the spike, because the trend test is
    evaluated AT the spike bar and therefore sees the spike's own close — see
    test_the_trend_test_sees_the_spike_bar_itself. That is how the research
    measured it, so the engine reproduces it rather than quietly improving on it.
    """
    rng = np.random.default_rng(seed)
    close = 9000 + np.cumsum(np.full(n, drift) + rng.normal(0, 0.05, n))
    open_ = np.r_[close[0], close[:-1]]
    i = n - 2                                   # the spike bar
    open_[i] = close[i - 1]
    close[i] = open_[i] + spike                 # a huge up bar
    open_[i + 1] = close[i]
    close[i + 1] = open_[i + 1] - 6.0           # the confirming red bar
    return _frame(close, open_), i


def _crash_setup(n=400, drift=1.0, spike=20.0, seed=1):
    """A long UPWARD drift, ONE big down bar, then a green bar. A Crash chart."""
    rng = np.random.default_rng(seed)
    close = 9000 + np.cumsum(np.full(n, drift) + rng.normal(0, 0.05, n))
    open_ = np.r_[close[0], close[:-1]]
    i = n - 2
    open_[i] = close[i - 1]
    close[i] = open_[i] - spike                 # a huge DOWN bar
    open_[i + 1] = close[i]
    close[i + 1] = open_[i + 1] + 6.0           # the confirming GREEN bar
    return _frame(close, open_), i


def _scan(eng, df, symbol, window):
    out = []
    for k in range(60, len(df)):
        sig = asyncio.run(eng.on_bar(symbol, "M15", df.iloc[max(0, k + 1 - window):k + 1]))
        if sig is not None:
            out.append((k, sig))
    return out


# ── direction ───────────────────────────────────────────────────────────────
def test_boom_sells_after_an_up_spike():
    eng = _engine(trend_mode="ret", trend_lookback=50)
    df, i = _boom_setup()
    sig = asyncio.run(eng.on_bar("Boom 900 Index", "M15", df))
    assert sig is not None, "the setup did not fire at all"
    assert sig.direction == "SELL"
    assert sig.stop_loss > sig.entry_price and sig.take_profit < sig.entry_price


def test_crash_buys_after_a_down_spike():
    """The mirror. Inverting the direction would turn a strategy that does not
    work into one that loses fast, and no backtest shape would announce it."""
    eng = _engine(trend_mode="ret", trend_lookback=50)
    df, _ = _crash_setup()
    sig = asyncio.run(eng.on_bar("Crash 900 Index", "M15", df))
    assert sig is not None, "the mirrored setup did not fire"
    assert sig.direction == "BUY"
    assert sig.stop_loss < sig.entry_price and sig.take_profit > sig.entry_price
    assert sig.metadata["spike_side"] == -1


def test_a_market_that_does_not_one_sided_spike_never_fires():
    """Jump and Range Break spike BOTH ways, so "the drift" is not a thing, and
    a symbol absent from the fill table has no measured spike side at all."""
    df, _ = _boom_setup()
    for symbol in ("Jump 100 Index", "Volatility 75 Index", "EURUSD"):
        assert asyncio.run(_engine(trend_mode="ret").on_bar(symbol, "M15", df)) is None, symbol


# ── the stop ────────────────────────────────────────────────────────────────
def test_the_stop_uses_the_spike_bars_atr_not_the_entry_bars():
    eng = _engine(trend_mode="ret", stop_atr_multiple=2.0, atr_period=14)
    df, i = _boom_setup()
    sig = asyncio.run(eng.on_bar("Boom 900 Index", "M15", df))
    a = atr(df["high"].to_numpy(float), df["low"].to_numpy(float),
            df["close"].to_numpy(float), 14)
    assert sig.stop_loss - sig.entry_price == pytest.approx(2.0 * a[i], rel=1e-9)
    assert a[i] != pytest.approx(a[-1]), "fixture must have a different ATR on the two bars"
    assert sig.metadata["atr_val"] == pytest.approx(a[i], rel=1e-12)


def test_the_target_is_the_resolved_slot_rr():
    eng = _engine(trend_mode="ret", stop_atr_multiple=1.0)
    df, _ = _boom_setup()
    sig = asyncio.run(eng.on_bar("Boom 900 Index", "M15", df))
    risk = sig.stop_loss - sig.entry_price
    reward = sig.entry_price - sig.take_profit
    assert reward / risk == pytest.approx(sig.metadata["tp1_rr"], rel=1e-9)


# ── the gates ───────────────────────────────────────────────────────────────
def test_a_spike_that_is_too_small_does_not_fire():
    eng = _engine(trend_mode="ret", spike_atr_multiple=3.0)
    df, _ = _boom_setup(spike=1.0)
    assert asyncio.run(eng.on_bar("Boom 900 Index", "M15", df)) is None


def test_two_spikes_in_a_row_are_one_event_still_running():
    eng = _engine(trend_mode="ret")
    df, i = _boom_setup()
    o, c = df["open"].to_numpy(float).copy(), df["close"].to_numpy(float).copy()
    c[i + 1] = o[i + 1] + 40.0            # the confirming bar is itself a spike
    df2 = _frame(c, o)
    assert asyncio.run(eng.on_bar("Boom 900 Index", "M15", df2)) is None


def test_a_green_confirming_bar_does_not_confirm():
    eng = _engine(trend_mode="ret")
    df, i = _boom_setup()
    o, c = df["open"].to_numpy(float).copy(), df["close"].to_numpy(float).copy()
    c[i + 1] = o[i + 1] + 1.0             # closes UP, the wrong way
    assert asyncio.run(eng.on_bar("Boom 900 Index", "M15", _frame(c, o))) is None


def test_an_upward_drift_blocks_a_boom_sell():
    """Boom only sells when the drift is already down. A rising Boom is not it."""
    eng = _engine(trend_mode="ret", trend_lookback=50)
    df, _ = _boom_setup(drift=+0.4)
    assert asyncio.run(eng.on_bar("Boom 900 Index", "M15", df)) is None


@pytest.mark.parametrize("mode", ["ema", "ret", "below"])
def test_every_trend_mode_agrees_this_is_a_downward_drift(mode):
    eng = _engine(trend_mode=mode, trend_lookback=50)
    df, _ = _boom_setup()
    assert asyncio.run(eng.on_bar("Boom 900 Index", "M15", df)) is not None, mode


def test_require_below_spike_waits_for_the_close_to_get_back_past_it():
    df, i = _boom_setup()
    o, c = df["open"].to_numpy(float).copy(), df["close"].to_numpy(float).copy()
    c[i + 1] = o[i + 1] - 1.0             # red, but still above where the spike began
    frame = _frame(c, o)
    assert asyncio.run(_engine(trend_mode="ret").on_bar("Boom 900 Index", "M15", frame)) is not None
    strict = _engine(trend_mode="ret", require_below_spike=True)
    assert asyncio.run(strict.on_bar("Boom 900 Index", "M15", frame)) is None


def test_min_bars_since_spike_rejects_a_cluster():
    df, i = _boom_setup()
    o, c = df["open"].to_numpy(float).copy(), df["close"].to_numpy(float).copy()
    j = i - 5
    o[j], c[j] = c[j - 1], c[j - 1] + 20.0        # a second spike five bars earlier
    frame = _frame(c, o)
    assert asyncio.run(_engine(trend_mode="ret").on_bar("Boom 900 Index", "M15", frame)) is not None
    gated = _engine(trend_mode="ret", min_bars_since_spike=20)
    assert asyncio.run(gated.on_bar("Boom 900 Index", "M15", frame)) is None


def test_confirm_bars_two_needs_two_red_bars():
    df, i = _boom_setup(n=401)
    o, c = df["open"].to_numpy(float).copy(), df["close"].to_numpy(float).copy()
    i = len(c) - 3
    o[i], c[i] = c[i - 1], c[i - 1] + 40.0
    o[i + 1], c[i + 1] = c[i], c[i] - 6.0
    o[i + 2], c[i + 2] = c[i + 1], c[i + 1] - 6.0
    frame = _frame(c, o)
    assert asyncio.run(_engine(trend_mode="ret", confirm_bars=2).on_bar(
        "Boom 900 Index", "M15", frame)) is not None
    o[i + 2], c[i + 2] = c[i + 1], c[i + 1] + 6.0     # second bar closes green
    assert asyncio.run(_engine(trend_mode="ret", confirm_bars=2).on_bar(
        "Boom 900 Index", "M15", _frame(c, o))) is None


# ── the spike measure ───────────────────────────────────────────────────────
def test_range_mode_sees_a_spike_that_was_faded_inside_its_own_bar():
    o = np.array([100.0]); c = np.array([100.5]); h = np.array([140.0]); lo = np.array([99.0])
    body = spike_size(o, h, lo, c, +1, "body")[0]
    rng = spike_size(o, h, lo, c, +1, "range")[0]
    either = spike_size(o, h, lo, c, +1, "either")[0]
    assert body == pytest.approx(0.5)
    assert rng == pytest.approx(40.0)
    assert either == pytest.approx(40.0)


# ── the exit ────────────────────────────────────────────────────────────────
def test_the_bar_count_exit_is_live_and_sized_for_it():
    eng = _engine(max_hold_bars=5)
    assert eng.LIVE_POSITION_EXITS is True
    assert eng.POSITION_BAR_WINDOW >= 5
    df, _ = _boom_setup()
    entry = pd.Timestamp(df.index[-8])
    pos = {"ticket": 1, "direction": "SELL", "entry_time": entry, "stop_loss": 9999.0}
    act = eng.on_position_bar("Boom 900 Index", "M15", df, pos)
    assert act is not None and act.action == "CLOSE" and act.close_reason == "MAX_HOLD"
    early = {**pos, "entry_time": pd.Timestamp(df.index[-2])}
    assert eng.on_position_bar("Boom 900 Index", "M15", df, early) is None


def test_zero_max_hold_turns_the_bar_exit_off():
    assert _engine(max_hold_bars=0).LIVE_POSITION_EXITS is False


# ── windows ─────────────────────────────────────────────────────────────────
def test_the_declared_window_is_long_enough_for_its_own_trend_test():
    """An EMA seeded on its own span still carries ~13% of the seed, so a window
    that only just fits the span computes a different number live than in the
    backtest — the failure this strategy's WINDOW_BARS exists to prevent."""
    for look in (30, 50, 100):
        eng = _engine(trend_lookback=look)
        assert window_bars("M15", eng) >= look * 5


# ── wiring ──────────────────────────────────────────────────────────────────
def test_it_is_wired_everywhere_a_strategy_has_to_be():
    import re
    from pathlib import Path

    from backend.api.routes.backtest import STRATEGY_PARAM_SECTION
    from backend.core.schema_introspection import build_full_schema

    assert STRATEGY_PARAM_SECTION["SpikeResumption_v1"] == "spike_resumption"
    assert {r["group"] for r in build_full_schema()} >= {"spike_resumption"}
    assert hasattr(UserConfigV2(), "spike_resumption")
    js = (Path(__file__).resolve().parents[1] / "frontend/src/components/slotSpec.js").read_text()
    block = re.search(r"STRATEGY_OPTIONS = \[(.*?)\];", js, re.S).group(1)
    assert "['SpikeResumption_v1'" in block and "'spike_resumption'" in block


def test_the_shipped_defaults_say_what_the_measurement_said():
    from backend.strategies.strategy_defaults import (
        SLOT_TP1_RR, SYNTH_SLOT_PARAMS, get_strategy_defaults, get_strategy_evidence,
    )
    d = get_strategy_defaults("SpikeResumption_v1")
    assert d["tp_count"] == 1 and d["be_mode"] == "NONE"
    assert d["trail_mode"] == "NONE" and d["trail_method_tp1"] == "NONE"
    evidence = get_strategy_evidence("SpikeResumption_v1")
    assert "NO RELIABLE EDGE" in evidence, "the evidence string must not oversell it"
    # No recommended symbol: recommending one would imply a measurement backing it.
    assert not [k for k in SLOT_TP1_RR if k.endswith("|SpikeResumption_v1")]
    assert not [k for k in SYNTH_SLOT_PARAMS if k.endswith("|SpikeResumption_v1")]


def test_the_trend_test_sees_the_spike_bar_itself():
    """A surprise worth pinning: the drift is measured AT the spike bar, so a
    spike big enough to undo the whole lookback flips the trend test and the
    setup is refused. The research harness does exactly this, so the engine must
    too — "fixing" it here would make the app trade something never measured."""
    eng = _engine(trend_mode="ret", trend_lookback=50)
    gentle, _ = _boom_setup(drift=-0.4, spike=40.0)      # 50 bars of drift = -20, spike = +40
    assert asyncio.run(eng.on_bar("Boom 900 Index", "M15", gentle)) is None
    steep, _ = _boom_setup(drift=-1.0, spike=20.0)       # -50 against +20
    assert asyncio.run(eng.on_bar("Boom 900 Index", "M15", steep)) is not None


# ── the three paths must agree ──────────────────────────────────────────────
#
# "What I get on single and portfolio backtest is what I get live" is the whole
# point of the per-slot work, and a strategy is only as parity-safe as its exit.
# SpikeResumption's exit is a BAR COUNT, which every path counts for itself, so
# a disagreement here would be silent: both runs would produce trades, just not
# the same ones.
PARITY_RISK = {
    "commission_per_lot": 0.0, "slippage_pips": 0.0, "exit_slippage_pips": 0.0,
    "spread_pips": 0.0, "stops_level_pips": 0.0, "swap_long_per_lot_per_day": 0.0,
    "swap_short_per_lot_per_day": 0.0, "stop_fill_model": "OFF",
    "risk_per_trade_pct": 1.0, "min_rr": 0.5, "max_risk_hard_cap_pct": 3.0,
    "tp_count": 1, "tp1_rr": 3.0, "tp_splits": "100",
    "be_mode": "NONE", "trail_mode": "NONE", "trail_method_tp1": "NONE",
    "max_daily_drawdown_pct": 100.0, "max_weekly_drawdown_pct": 100.0,
    "max_daily_trades": 50, "use_strategy_exit_defaults": False,
}


PARITY_SYMBOL = "Boom 900 Index"


def _parity_case():
    """One real signal on the instrument the pattern was spotted on.

    It has to be a Boom or Crash symbol — the strategy refuses anything with no
    measured spike side — AND one position_sizer has a profile for, or the risk
    engine rejects every signal before an exit can be tested.
    """
    df, _ = _boom_setup()
    eng = _engine(trend_mode="ret", trend_lookback=50, max_hold_bars=5)
    sig = asyncio.run(eng.on_bar(PARITY_SYMBOL, "M15", df))
    return df, eng, sig


def test_the_bar_count_exit_books_the_trade_in_the_single_engine():
    from backend.backtester.engine import BacktestEngine
    df, eng, sig = _parity_case()
    assert sig is not None, "fixture produced no signal"
    signal = {"symbol": PARITY_SYMBOL, "_cache_key": PARITY_SYMBOL, "direction": sig.direction,
              "time": int(df["time"].iloc[-1]), "entry_price": sig.entry_price,
              "stop_loss": sig.stop_loss, "take_profit": sig.take_profit,
              "timeframe": "M15", "confluence_score": 80,
              "strategy_id": "SpikeResumption_v1", "metadata": dict(sig.metadata)}
    # the signal lands on the last bar, so give the run room to hold and exit
    longer = _frame(np.r_[df["close"].to_numpy(float), np.full(12, df["close"].iloc[-1])],
                    np.r_[df["open"].to_numpy(float), np.full(12, df["close"].iloc[-1])])
    res = BacktestEngine(dict(PARITY_RISK)).run(
        longer, [signal], 10_000.0, longer, longer, eng, None, None)
    trades = res.get("trades") or []
    assert len(trades) == 1, res.get("rejection_funnel")
    assert trades[0]["exit_reason"] == "MAX_HOLD", trades[0]["exit_reason"]


def test_single_and_portfolio_engines_book_it_identically():
    from backend.backtester.engine import BacktestEngine
    from backend.backtester.portfolio_engine import PortfolioBacktestEngine
    df, eng, sig = _parity_case()
    assert sig is not None
    signal = {"symbol": PARITY_SYMBOL, "_cache_key": PARITY_SYMBOL, "direction": sig.direction,
              "time": int(df["time"].iloc[-1]), "entry_price": sig.entry_price,
              "stop_loss": sig.stop_loss, "take_profit": sig.take_profit,
              "timeframe": "M15", "confluence_score": 80,
              "strategy_id": "SpikeResumption_v1", "metadata": dict(sig.metadata)}
    longer = _frame(np.r_[df["close"].to_numpy(float), np.full(12, df["close"].iloc[-1])],
                    np.r_[df["open"].to_numpy(float), np.full(12, df["close"].iloc[-1])])

    single = (BacktestEngine(dict(PARITY_RISK)).run(
        longer, [signal], 10_000.0, longer, longer,
        _engine(trend_mode="ret", trend_lookback=50, max_hold_bars=5),
        None, None).get("trades") or [])
    basket = (PortfolioBacktestEngine(dict(PARITY_RISK)).run(
        {PARITY_SYMBOL: longer}, {PARITY_SYMBOL: [signal]}, 10_000.0, None, None, None, None,
        {PARITY_SYMBOL: _engine(trend_mode="ret", trend_lookback=50, max_hold_bars=5)},
    ).get("trades") or [])

    assert len(single) == len(basket) == 1
    assert single[0]["exit_reason"] == basket[0]["exit_reason"] == "MAX_HOLD"
    for key in ("entry_price", "exit_price", "stop_loss"):
        assert single[0][key] == pytest.approx(basket[0][key], rel=1e-12), key
