"""
tests/test_fill_model.py — [P1.2]

The defect these lock down: both backtest engines resolved a breached stop by
asking ONLY whether the bar's `open` was already through the level, and filling
at the exact stop price otherwise. On Boom/Crash — instruments whose defining
property is a single-tick intrabar jump — that booked a perfect fill on every
spike. Measured on the user's own runs: 65/65 hard-SL exits at exactly the stop,
`gap_fill` set on 0 of 132 trades.

The regression that matters most is `test_intrabar_spike_is_not_a_perfect_fill`.
"""

import pytest

from backend.backtester.fill_model import (
    MODE_CONSERVATIVE,
    MODE_EMPIRICAL,
    MODE_OFF,
    StopFillModel,
    get_overshoot_profile,
)


def _model(mode=MODE_CONSERVATIVE):
    return StopFillModel(mode=mode, seed="test")


# ── the headline regression ──────────────────────────────────────────────────

def test_intrabar_spike_is_not_a_perfect_fill():
    """A bar that opens above the stop and then spikes far through it must not
    fill at the stop. This is the exact shape of a Boom/Crash spike."""
    m = _model()
    # SELL on Boom: stop above entry. Bar opens below the stop, then jumps
    # violently through it — high is 60 points past a 40-point stop distance.
    fill, gapped, ov = m.resolve_stop_fill(
        direction="SELL",
        open_p=14_700.0, high=14_800.0, low=14_695.0,
        stop_level=14_740.0,
        stop_distance=40.0,
        symbol="Boom 1000 Index",
        slippage_pips=0.0,
        position_key="p1",
    )
    assert gapped is True
    assert fill > 14_740.0, "SELL stop must fill ABOVE the stop when price jumps through it"
    assert ov == pytest.approx(get_overshoot_profile("Boom 1000 Index").mean, rel=1e-6)


def test_buy_side_mirror():
    m = _model()
    fill, gapped, ov = m.resolve_stop_fill(
        direction="BUY",
        open_p=6_000.0, high=6_005.0, low=5_900.0,
        stop_level=5_960.0,
        stop_distance=40.0,
        symbol="Crash 1000 Index",
        slippage_pips=0.0,
        position_key="p2",
    )
    assert gapped is True
    assert fill < 5_960.0
    assert ov == pytest.approx(get_overshoot_profile("Crash 1000 Index").mean, rel=1e-6)


# ── the clamp is what stops the model inventing prices ───────────────────────

def test_fill_never_beyond_the_bar_extreme():
    """A bar that merely grazed the stop fills AT the stop — the model may only
    charge overshoot that the bar's own range can support."""
    m = _model()
    fill, gapped, ov = m.resolve_stop_fill(
        direction="BUY",
        open_p=6_000.0, high=6_002.0,
        low=5_959.99,          # dipped 0.01 below the stop and no further
        stop_level=5_960.0,
        stop_distance=40.0,
        symbol="Crash 1000 Index",
        slippage_pips=0.0,
        position_key="p3",
    )
    assert fill == pytest.approx(5_959.99)
    assert ov == pytest.approx(0.01 / 40.0, rel=1e-6)
    assert gapped is True


def test_fill_is_never_better_than_the_stop():
    """This is an ADVERSE fill model. It must never hand back a profit."""
    m = _model()
    for direction, low, high in (("BUY", 5_959.0, 6_002.0), ("SELL", 14_695.0, 14_745.0)):
        stop = 5_960.0 if direction == "BUY" else 14_740.0
        fill, _, ov = m.resolve_stop_fill(
            direction=direction, open_p=6_000.0 if direction == "BUY" else 14_700.0,
            high=high, low=low, stop_level=stop, stop_distance=40.0,
            symbol="Crash 1000 Index", slippage_pips=0.0, position_key="k",
        )
        assert ov >= 0.0
        if direction == "BUY":
            assert fill <= stop
        else:
            assert fill >= stop


# ── the pre-existing bar-open branch must be preserved ───────────────────────

def test_bar_opening_through_the_stop_still_fills_at_the_open():
    m = _model()
    fill, gapped, _ = m.resolve_stop_fill(
        direction="BUY",
        open_p=5_900.0, high=5_910.0, low=5_890.0,
        stop_level=5_960.0,
        stop_distance=40.0,
        symbol="Crash 1000 Index",
        slippage_pips=0.0,
        position_key="p4",
    )
    assert gapped is True
    assert fill == pytest.approx(5_900.0)


# ── modes ────────────────────────────────────────────────────────────────────

def test_off_mode_reproduces_the_old_behaviour():
    """Kept so historical runs replay bit-for-bit — and only for that."""
    m = _model(MODE_OFF)
    fill, gapped, ov = m.resolve_stop_fill(
        direction="SELL",
        open_p=14_700.0, high=14_800.0, low=14_695.0,
        stop_level=14_740.0, stop_distance=40.0,
        symbol="Boom 1000 Index", slippage_pips=0.0, position_key="p5",
    )
    assert fill == pytest.approx(14_740.0)
    assert gapped is False
    assert ov == 0.0


def test_empirical_is_deterministic_for_a_given_position():
    """Two runs must reprice the same trade identically, and the draw must not
    depend on how many OTHER positions closed first."""
    a = StopFillModel(mode=MODE_EMPIRICAL, seed="s")
    b = StopFillModel(mode=MODE_EMPIRICAL, seed="s")
    kw = dict(direction="SELL", open_p=14_700.0, high=14_900.0, low=14_695.0,
              stop_level=14_740.0, stop_distance=40.0,
              symbol="Boom 1000 Index", slippage_pips=0.0)
    # b resolves an unrelated position first — must not shift the shared one.
    b.resolve_stop_fill(position_key="unrelated", **kw)
    assert (a.resolve_stop_fill(position_key="shared", **kw)[0]
            == b.resolve_stop_fill(position_key="shared", **kw)[0])


def test_empirical_quantiles_are_monotonic_and_hit_the_measured_anchors():
    p = get_overshoot_profile("Crash 1000 Index")
    vals = [p.sample(u) for u in (0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99, 1.0)]
    assert vals == sorted(vals)
    assert p.sample(0.50) == pytest.approx(0.158)   # research/27 §1.2 median
    assert p.sample(0.99) == pytest.approx(7.360)   # research/27 §1.2 p99
    assert p.sample(1.00) == pytest.approx(18.540)  # research/27 §1.2 max


# ── the economic claim ───────────────────────────────────────────────────────

def test_conservative_mode_charges_the_measured_mean_across_many_stops():
    """Aggregate check: the run-level mean overshoot must land on the measured
    per-symbol mean, because that is the number §0.3's correction is built on."""
    m = _model()
    for n in range(200):
        m.resolve_stop_fill(
            direction="SELL", open_p=14_700.0, high=14_900.0, low=14_695.0,
            stop_level=14_740.0, stop_distance=40.0,
            symbol="Boom 1000 Index", slippage_pips=0.0, position_key=f"p{n}",
        )
    s = m.summary()
    assert s["stop_exits"] == 200
    assert s["gapped_pct"] == pytest.approx(100.0)
    assert s["mean_overshoot_r"] == pytest.approx(0.353, abs=1e-3)


def test_zero_stop_distance_degrades_safely():
    """No original risk to measure against = no guess. Fill at the stop."""
    m = _model()
    fill, gapped, ov = m.resolve_stop_fill(
        direction="BUY", open_p=6_000.0, high=6_002.0, low=5_900.0,
        stop_level=5_960.0, stop_distance=0.0,
        symbol="Crash 1000 Index", slippage_pips=0.0, position_key="p6",
    )
    assert fill == pytest.approx(5_960.0)
    assert gapped is False and ov == 0.0


def test_non_jump_instruments_get_a_small_default_not_a_jump_sized_one():
    assert get_overshoot_profile("EURUSD").mean < 0.05
    assert get_overshoot_profile("Crash 1000 Index").mean > 0.3
    assert get_overshoot_profile("Boom 500 Index").mean == pytest.approx(0.353)


def test_summary_reports_calibration_provenance():
    """A number that is interpolated must never be presented as measured."""
    m = _model()
    m.resolve_stop_fill(
        direction="SELL", open_p=14_700.0, high=14_900.0, low=14_695.0,
        stop_level=14_740.0, stop_distance=40.0,
        symbol="Boom 1000 Index", slippage_pips=0.0, position_key="p7",
    )
    entry = m.summary()["by_symbol"]["Boom 1000 Index"]
    assert entry["profile_calibration"] in {"interpolated", "assumed", "measured"}
    assert "research/27" in entry["profile_source"]


# ── [P1.2] the flags must survive grouping ───────────────────────────────────
#
# The engines set gap_fill / stop_overshoot_r on the LEG, but every consumer —
# the repricer, the frontend trade row, any audit — reads the GROUP. Without
# propagation a run whose stops ALL gapped reported "gap_fill on 0 of N trades",
# which is exactly the false negative that let the perfect-fill defect hide.

def test_group_trades_propagates_the_fill_flags():
    from backend.utils.trade_grouper import group_trades

    legs = [
        {"group_id": "g1", "symbol": "Crash 1000 Index", "strategy_id": "S",
         "direction": "BUY", "entry_price": 100.0, "stop_loss": 95.0,
         "initial_stop_loss": 95.0, "take_profit": 120.0, "volume": 0.2,
         "tp_level": 1, "entry_time": 0, "exit_time": 60, "exit_price": 94.0,
         "exit_reason": "SL", "pnl": -1.2, "gap_fill": True,
         "stop_overshoot_r": 0.2},
    ]
    g = group_trades(legs)[0]
    assert g["gap_fill"] is True, "a gapped leg must mark its group as gapped"
    assert g["stop_overshoot_r"] == pytest.approx(0.2)


def test_group_without_gapped_legs_reports_no_gap():
    from backend.utils.trade_grouper import group_trades

    legs = [
        {"group_id": "g2", "symbol": "EURUSD", "strategy_id": "S",
         "direction": "BUY", "entry_price": 1.10, "stop_loss": 1.09,
         "initial_stop_loss": 1.09, "take_profit": 1.13, "volume": 0.1,
         "tp_level": 1, "entry_time": 0, "exit_time": 60, "exit_price": 1.13,
         "exit_reason": "TP1", "pnl": 3.0},
    ]
    g = group_trades(legs)[0]
    assert g["gap_fill"] is False
    assert g["stop_overshoot_r"] is None
