"""
tests/test_baseline_forecaster.py — [P3.7]

The bar the LLM agent must clear. Measured on live MT5 H1 data across 8
instruments, 901 actionable forecasts: expectancy +0.0110 R, t = +0.26,
NOT SIGNIFICANT. That is what a fair baseline looks like — momentum alone
earns nothing here, so beating it requires real forecasting rather than
inheriting the instrument's drift.
"""

import math

import numpy as np
import pytest

from backend.strategies.baseline_forecaster import (
    BaselineForecaster,
    Forecast,
    _realised_vol,
    _rolling_vol_history,
)


def _trend(n=400, slope=0.004, noise=0.002, seed=1, start=100.0):
    rng = np.random.default_rng(seed)
    c = start * np.exp(np.cumsum(rng.normal(slope, noise, n)))
    h = c * (1 + abs(rng.normal(0, noise, n)))
    l = c * (1 - abs(rng.normal(0, noise, n)))
    return list(h), list(l), list(c)


# ── direction ────────────────────────────────────────────────────────────────

def test_uptrend_is_long_and_downtrend_is_short():
    bf = BaselineForecaster()
    up = bf.forecast("X", *_trend(slope=+0.004))
    dn = bf.forecast("X", *_trend(slope=-0.004))
    assert up.direction == "LONG", up.abstain_reason
    assert dn.direction == "SHORT", dn.abstain_reason


def test_stop_and_target_sit_on_the_correct_sides():
    bf = BaselineForecaster()
    for slope, d in ((+0.004, "LONG"), (-0.004, "SHORT")):
        f = bf.forecast("X", *_trend(slope=slope))
        assert f.direction == d
        entry = f.entry_zone[0]
        tgt = f.targets[0]["level"]
        if d == "LONG":
            assert f.invalidation < entry < tgt
        else:
            assert tgt < entry < f.invalidation


def test_reward_to_risk_matches_the_configured_target():
    bf = BaselineForecaster(target_rr=3.0)
    f = bf.forecast("X", *_trend(slope=0.004))
    entry = f.entry_zone[0]
    rr = abs(f.targets[0]["level"] - entry) / abs(entry - f.invalidation)
    assert rr == pytest.approx(3.0, rel=1e-6)


# ── abstention is a real answer ──────────────────────────────────────────────

def test_driftless_market_abstains_often():
    """Most windows contain nothing, and a forecaster that always has a view is
    pattern-matching rather than reading the market.

    Asserted as a RATE across many seeds, not on one draw: a zero-drift random
    walk genuinely does produce 48-bar momentum sometimes, so a single series is
    a coin flip and testing it would test nothing. Measured live across 8
    instruments the abstention rate was 38-47%."""
    bf = BaselineForecaster()
    flat = sum(
        bf.forecast("X", *_trend(slope=0.0, noise=0.002, seed=s)).direction == "FLAT"
        for s in range(40)
    )
    assert flat >= 12, f"abstained on only {flat}/40 driftless series"


def test_an_abstention_always_carries_a_reason():
    bf = BaselineForecaster()
    for s in range(40):
        f = bf.forecast("X", *_trend(slope=0.0, noise=0.002, seed=s))
        if f.direction == "FLAT":
            assert f.abstain_reason and not f.is_actionable
            return
    pytest.fail("no abstention produced to inspect")


def test_top_decile_volatility_abstains():
    """Widest stops, worst spreads, and where measured edges decay."""
    rng = np.random.default_rng(3)
    c = list(100 * np.exp(np.cumsum(np.r_[rng.normal(0, 0.001, 360),
                                          rng.normal(0.01, 0.05, 40)])))
    h = [x * 1.001 for x in c]
    l = [x * 0.999 for x in c]
    f = BaselineForecaster().forecast("X", h, l, c)
    assert f.direction == "FLAT"
    assert "top decile" in (f.abstain_reason or "")
    assert f.regime == "EXPANDING"


def test_short_history_abstains_rather_than_guessing():
    bf = BaselineForecaster()
    f = bf.forecast("X", [1.0] * 10, [1.0] * 10, [1.0] * 10)
    assert f.direction == "FLAT"
    assert "need" in (f.abstain_reason or "")


def test_degenerate_prices_abstain():
    bf = BaselineForecaster()
    flat = [100.0] * 400
    f = bf.forecast("X", flat, flat, flat)
    assert f.direction == "FLAT" and not f.is_actionable


# ── conviction ───────────────────────────────────────────────────────────────

def test_conviction_is_bounded_and_rises_with_momentum():
    bf = BaselineForecaster()
    weak = bf.forecast("X", *_trend(slope=0.0015, seed=2))
    strong = bf.forecast("X", *_trend(slope=0.01, seed=2))
    assert 0.0 <= weak.conviction <= 1.0
    assert 0.0 <= strong.conviction <= 1.0
    assert strong.conviction >= weak.conviction


def test_conviction_saturates_rather_than_running_away():
    """An unbounded score would dominate every confluence risk tier."""
    bf = BaselineForecaster()
    f = bf.forecast("X", *_trend(slope=0.05, noise=0.001))
    assert f.conviction == 1.0


# ── the rolling-volatility fast path ─────────────────────────────────────────

def test_rolling_vol_matches_the_naive_computation():
    """The O(n) version must agree with the O(n·lookback) one it replaced —
    the naive one made a single 8-instrument scan take minutes, and the live
    scan loop would have paid that every cycle."""
    rng = np.random.default_rng(1)
    px = list(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 600))))
    fast = _rolling_vol_history(px, 24)
    slow = [v for i in range(25, len(px)) if (v := _realised_vol(px[: i + 1], 24))]
    m = min(len(fast), len(slow))
    assert m > 500
    assert max(abs(a - b) for a, b in zip(fast[-m:], slow[-m:])) < 1e-12


def test_rolling_vol_declines_on_unusable_input():
    assert _rolling_vol_history([1.0, 2.0], 24) == []
    assert _rolling_vol_history([100.0, 0.0] + [100.0] * 50, 24) == []


# ── the contract ─────────────────────────────────────────────────────────────

def test_forecast_serialises_to_the_agent_contract():
    """The LLM agent returns this exact shape, so both can be scored by one
    harness. A comparison where each side has its own scorer is not one."""
    f = BaselineForecaster().forecast("XAUUSD", *_trend(slope=0.004))
    d = f.to_dict()
    for k in ("instrument", "direction", "conviction", "horizon_bars", "entry_zone",
              "invalidation", "targets", "expected_move_atr", "regime",
              "primary_evidence", "contradicting_evidence", "abstain_reason", "source"):
        assert k in d, k
    assert d["direction"] in ("LONG", "SHORT", "FLAT")
    assert isinstance(d["entry_zone"], list)
    assert d["source"] == "baseline"


def test_baseline_declares_its_own_blind_spot():
    f = BaselineForecaster().forecast("X", *_trend(slope=0.004))
    assert f.contradicting_evidence, "a forecast must state what it did NOT consider"


def test_is_actionable_requires_a_direction_and_conviction():
    assert not Forecast("X").is_actionable
    assert not Forecast("X", direction="LONG", conviction=0.0).is_actionable
    assert Forecast("X", direction="LONG", conviction=0.4).is_actionable
