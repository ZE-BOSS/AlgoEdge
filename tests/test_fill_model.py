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
    SPIKE_FILLS,
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
    # tick-calibrated: a spike-side stop fills lam of the way to the bar's extreme
    lam = SPIKE_FILLS["BOOM 1000 INDEX"][1]
    assert fill == pytest.approx(14_740.0 + lam * (14_800.0 - 14_740.0))
    assert ov == pytest.approx(lam * 60.0 / 40.0, rel=1e-6)


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
    lam = SPIKE_FILLS["CRASH 1000 INDEX"][1]
    assert fill == pytest.approx(5_960.0 - lam * (5_960.0 - 5_900.0))
    assert ov == pytest.approx(lam * 60.0 / 40.0, rel=1e-6)


def test_grind_side_stops_fill_at_the_stop():
    """Ticks: a Crash short / Boom long is stopped by the slow drift, -1.002R on
    every stop exit — no overshoot to charge."""
    m = _model()
    fill, gapped, ov = m.resolve_stop_fill(
        direction="SELL", open_p=6_000.0, high=6_050.0, low=5_995.0,
        stop_level=6_040.0, stop_distance=40.0, symbol="Crash 1000 Index",
        slippage_pips=0.0, position_key="g1")
    assert fill == pytest.approx(6_040.0) and gapped is False and ov == 0.0
    fill, gapped, ov = m.resolve_stop_fill(
        direction="BUY", open_p=14_700.0, high=14_705.0, low=14_600.0,
        stop_level=14_660.0, stop_distance=40.0, symbol="Boom 1000 Index",
        slippage_pips=0.0, position_key="g2")
    assert fill == pytest.approx(14_660.0) and gapped is False


def test_jump_indices_charge_both_sides():
    m = _model()
    lam = SPIKE_FILLS["JUMP 25 INDEX"][1]
    for direction, hi, lo, stop in (("BUY", 1_005.0, 900.0, 960.0), ("SELL", 1_100.0, 995.0, 1_040.0)):
        fill, gapped, _ = m.resolve_stop_fill(
            direction=direction, open_p=1_000.0, high=hi, low=lo, stop_level=stop,
            stop_distance=40.0, symbol="Jump 25 Index", slippage_pips=0.0, position_key=direction)
        want = stop - lam * (stop - lo) if direction == "BUY" else stop + lam * (hi - stop)
        assert gapped is True and fill == pytest.approx(want)


def test_crash_overshoot_does_not_scale_with_the_stop():
    """The spike's landing price depends on the bar, not on how wide the stop was
    (146 live fills: corr(overshoot, stop distance) = -0.06)."""
    m = _model()
    fills = []
    for dist in (10.0, 60.0):
        fill, _, ov = m.resolve_stop_fill(
            direction="BUY", open_p=6_000.0, high=6_005.0, low=5_800.0,
            stop_level=5_960.0, stop_distance=dist, symbol="Crash 1000 Index",
            slippage_pips=0.0, position_key=f"d{dist}")
        fills.append((fill, ov))
    assert fills[0][0] == pytest.approx(fills[1][0])          # same price overshoot
    assert fills[0][1] == pytest.approx(6 * fills[1][1])      # 6x the R on a 6x tighter stop


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
    lam = SPIKE_FILLS["CRASH 1000 INDEX"][1]
    assert fill == pytest.approx(5_960.0 - lam * 0.01)
    assert fill >= 5_959.99
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
    # Crash 1000 is now ABSOLUTE index points from 146 live MT5 stop fills
    assert p.absolute
    assert p.sample(0.50) == pytest.approx(3.04)    # live median
    assert p.sample(0.90) == pytest.approx(6.85)    # live p90
    assert p.sample(0.99) == pytest.approx(12.41)   # live p99
    # Boom keeps the research/27 tick-study anchors (no live Boom fills exist)
    q = get_overshoot_profile("Boom 1000 Index")
    assert not q.absolute and q.sample(0.50) == pytest.approx(0.159) and q.sample(0.99) == pytest.approx(4.94)


# ── the economic claim ───────────────────────────────────────────────────────

def test_conservative_mode_charges_the_tick_calibrated_spike_fill_across_many_stops():
    """Aggregate check: every spike-side Boom stop through a 160-point bar pays
    lam x 160 points (40-point stop -> lam x 4 R)."""
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
    assert s["mean_overshoot_r"] == pytest.approx(SPIKE_FILLS["BOOM 1000 INDEX"][1] * 4.0, abs=1e-3)


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
    assert get_overshoot_profile("EURUSD").mean < 0.05 and not get_overshoot_profile("EURUSD").absolute
    assert get_overshoot_profile("GBPJPY").mean < 0.05
    assert get_overshoot_profile("Crash 1000 Index").absolute and get_overshoot_profile("Crash 1000 Index").mean > 3
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
    assert entry["profile_calibration"] == "measured"
    assert "tick replay" in entry["profile_source"]
    m.resolve_stop_fill(
        direction="BUY", open_p=1.1, high=1.11, low=1.08, stop_level=1.09, stop_distance=0.01,
        symbol="EURUSD", slippage_pips=0.0, position_key="fx")
    fx = m.summary()["by_symbol"]["EURUSD"]
    assert fx["profile_calibration"] in {"interpolated", "assumed", "measured"}


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
