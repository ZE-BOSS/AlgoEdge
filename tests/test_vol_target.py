"""
tests/test_vol_target.py — [P5.1]

Volatility-targeted sizing. Shipped OFF by default after measurement failed to
show a benefit in this codebase (see the module docstring), so the most
important tests here are the ones proving it is inert until explicitly enabled
and that it cannot blow up position size when volatility approaches zero.
"""

import math

import numpy as np
import pytest

from backend.risk.vol_target import (
    BARS_PER_YEAR,
    clustering_strength,
    realised_volatility,
    resolve_vol_scale,
)


# ── off by default, and genuinely inert ──────────────────────────────────────

@pytest.mark.parametrize("target", [None, 0, 0.0, -5.0])
def test_disabled_is_exactly_a_no_op(target):
    """Every existing run must reproduce bit-for-bit. Scale must be EXACTLY 1.0,
    not 0.9999 — it multiplies risk."""
    r = resolve_vol_scale(1.8, [100.0] * 50, target_vol_annual_pct=target)
    assert r.scale == 1.0
    assert r.scaled_risk_pct == 1.8
    assert r.binding == "disabled"
    assert "off" in r.explain()


def test_risk_engine_defaults_to_disabled():
    from backend.risk.engine import RiskEngine

    eng = RiskEngine({"risk_per_trade_pct": 1.8})
    assert eng.vol_target_annual_pct in (None, 0)


def test_config_default_is_none():
    from backend.core.config_schema import RiskParams

    assert RiskParams().vol_target_annual_pct is None


# ── the clamps are load-bearing ──────────────────────────────────────────────

def test_near_zero_volatility_cannot_explode_position_size():
    """Unclamped, target/realised diverges as realised vol -> 0, sizing many
    multiples of intended risk immediately before the regime that ends the calm."""
    flat = [100.0 + i * 1e-9 for i in range(50)]   # essentially no movement
    r = resolve_vol_scale(1.8, flat, target_vol_annual_pct=15.0, max_scale=2.0)
    assert r.scale <= 2.0
    assert r.scaled_risk_pct <= 3.6


def test_extreme_volatility_cannot_shrink_below_the_floor():
    rng = np.random.default_rng(3)
    wild = list(100 * np.exp(np.cumsum(rng.normal(0, 0.2, 60))))
    r = resolve_vol_scale(1.8, wild, target_vol_annual_pct=5.0, min_scale=0.5)
    assert r.scale >= 0.5
    assert r.binding in ("floor", "none")


def test_binding_is_reported_accurately():
    flat = [100.0 + i * 1e-9 for i in range(50)]
    assert resolve_vol_scale(1.0, flat, target_vol_annual_pct=15.0).binding == "ceiling"


# ── the arithmetic ───────────────────────────────────────────────────────────

def test_scale_is_target_over_realised():
    rng = np.random.default_rng(7)
    px = list(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 200))))
    rv = realised_volatility(px, lookback=20, timeframe="D1")
    assert rv is not None and rv > 0
    r = resolve_vol_scale(2.0, px, target_vol_annual_pct=rv * 100,
                          lookback=20, timeframe="D1")
    assert r.scale == pytest.approx(1.0, abs=1e-9), "target == realised must give scale 1"
    assert r.scaled_risk_pct == pytest.approx(2.0)

    half = resolve_vol_scale(2.0, px, target_vol_annual_pct=rv * 50,
                             lookback=20, timeframe="D1")
    assert half.scale == pytest.approx(0.5, abs=1e-9)


def test_annualisation_follows_the_timeframe():
    rng = np.random.default_rng(5)
    px = list(100 * np.exp(np.cumsum(rng.normal(0, 0.001, 100))))
    d1 = realised_volatility(px, 20, "D1")
    h1 = realised_volatility(px, 20, "H1")
    assert h1 > d1
    assert h1 / d1 == pytest.approx(
        math.sqrt(BARS_PER_YEAR["H1"] / BARS_PER_YEAR["D1"]), rel=1e-9)


# ── refuses to guess ─────────────────────────────────────────────────────────

def test_insufficient_history_sizes_at_the_fixed_fraction():
    """A volatility computed from three bars feeds straight into position size.
    Better to decline than to produce a plausible-looking number."""
    r = resolve_vol_scale(1.8, [100.0, 101.0, 100.5], target_vol_annual_pct=15.0)
    assert r.scale == 1.0
    assert r.binding == "insufficient_data"
    assert "not enough history" in r.explain()


def test_none_and_empty_closes_are_safe():
    for closes in (None, [], [100.0]):
        r = resolve_vol_scale(1.8, closes, target_vol_annual_pct=15.0)
        assert r.scale == 1.0 and r.binding == "insufficient_data"


def test_numpy_arrays_are_accepted():
    """Bars arrive as numpy from MT5 and the backtester; `closes or []` raised
    'truth value of an array is ambiguous' on every one of them."""
    px = np.linspace(100, 110, 60) * (1 + np.sin(np.arange(60)) * 0.001)
    assert realised_volatility(px, 20, "H1") is not None
    assert resolve_vol_scale(1.8, px, target_vol_annual_pct=15.0).scale > 0


def test_non_positive_prices_do_not_produce_a_number():
    assert realised_volatility([100.0, 0.0] + [100.0] * 50, 20, "D1") is None
    assert realised_volatility([-1.0] * 60, 20, "D1") is None


# ── clustering detection ─────────────────────────────────────────────────────

def test_clustering_is_detected_where_it_exists():
    """A GARCH-like series — volatility that begets volatility — must register."""
    rng = np.random.default_rng(1)
    n, vol, px, prices = 3000, 0.01, 100.0, []
    for _ in range(n):
        r = rng.normal(0, vol)
        vol = math.sqrt(0.000002 + 0.12 * r * r + 0.86 * vol * vol)
        px *= math.exp(r)
        prices.append(px)
    s = clustering_strength(prices)
    assert s["clustering"] is True
    assert s["acf_lag1"] > 0.05
    assert "has something to work with" in s["note"]


def test_no_clustering_in_iid_returns():
    """Constant-volatility GBM is what a synthetic index looks like."""
    rng = np.random.default_rng(2)
    px = list(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 3000))))
    s = clustering_strength(px)
    assert s["clustering"] is False
    assert "will do approximately nothing" in s["note"]


def test_clustering_declines_to_answer_on_short_series():
    s = clustering_strength([100.0] * 10)
    assert s["clustering"] is False and s["acf_lag1"] is None


# ── the engine plumbs it, and records what it did ────────────────────────────

def test_engine_records_vol_target_in_sizing_diagnostics():
    import inspect

    from backend.risk import engine as risk_engine

    src = inspect.getsource(risk_engine)
    assert '"vol_target_scale"' in src
    assert '"vol_target_binding"' in src
    assert '"realised_vol_annual_pct"' in src, \
        "a run's sizing must be reconstructable from its own output"


def test_vol_target_is_applied_after_confluence_scaling():
    """The two compose: confluence says how much conviction the setup carries,
    vol targeting says what that conviction is worth in the current regime."""
    import inspect

    from backend.risk import engine as risk_engine

    src = inspect.getsource(risk_engine.RiskEngine.evaluate_signal)
    i_conf = src.index("get_confluence_scaled_risk")
    i_vol = src.index("resolve_vol_scale")
    assert i_conf < i_vol
