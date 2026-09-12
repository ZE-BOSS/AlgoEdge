"""The strategy search must not flatter a strategy: next-bar entry, ties lose,
gaps fill at the open, spread is charged, and settings are picked in-sample only."""

from datetime import datetime, timezone

import numpy as np
import pytz

from backend.analytics.strategy_search import (
    Bars, Family, Signal, _orb, evaluate_family, simulate, summarize,
)


def _bars(o, h, l, c, *, tf="H1", t0=1_700_000_000, spread=0.0, vol=None):
    n = len(c)
    step = {"H1": 3600, "M15": 900}[tf]
    return Bars("TEST", tf, np.arange(n, dtype=np.int64) * step + t0,
                np.asarray(o, float), np.asarray(h, float), np.asarray(l, float), np.asarray(c, float),
                np.full(n, spread), None if vol is None else np.asarray(vol, float))


def test_entry_is_next_bar_open_not_signal_close():
    b = _bars([100, 105, 106], [101, 112, 107], [99, 104, 105], [100, 106, 106])
    t = simulate(b, [Signal(0, 1, 5.0, target_rr=1.0)], {})[0]
    assert t.entry == 105 and t.i_entry == 1
    assert t.reason == "TARGET" and t.exit == 110


def test_bar_touching_stop_and_target_counts_as_loss():
    b = _bars([100, 100], [100, 120], [100, 80], [100, 100])
    t = simulate(b, [Signal(0, 1, 10.0, target_rr=1.0)], {})[0]
    assert t.reason == "STOP" and t.r == -1.0


def test_gap_through_stop_fills_at_open():
    b = _bars([100, 100, 85], [100, 101, 86], [100, 99, 84], [100, 100, 85])
    t = simulate(b, [Signal(0, 1, 10.0, target_rr=3.0)], {})[0]
    assert t.reason == "STOP" and t.exit == 85 and t.r == -1.5


def test_spread_is_charged():
    b = _bars([100, 100], [100, 110], [100, 100], [100, 100], spread=1.0)
    t = simulate(b, [Signal(0, 1, 10.0, target_rr=1.0)], {})[0]
    assert abs(t.r - 0.9) < 1e-12


def test_positions_do_not_overlap():
    b = _bars([100] * 6, [100.5] * 6, [99.5] * 6, [100] * 6)
    trades = simulate(b, [Signal(0, 1, 10.0, target_rr=5.0, max_hold=3), Signal(1, 1, 10.0, max_hold=1)], {})
    assert len(trades) == 1


def test_settings_are_chosen_without_seeing_out_of_sample():
    rng = np.random.default_rng(1)
    c = 100 + np.cumsum(rng.normal(0, 1, 800))
    b = _bars(c, c + 1, c - 1, c)
    split = int(b.time[500])

    def build(bars, p):
        return [Signal(i, p["d"], 2.0, target_rr=1.0) for i in range(0, len(bars) - 1, 5)], {}

    fam = Family("t", "H1", "", ({"d": 1}, {"d": -1}), build, 5)
    first = evaluate_family(b, fam, oos_start=split)
    c2 = c.copy()
    c2[520:] = c2[519] - np.arange(280) * 3   # rewrite only the out-of-sample future
    second = evaluate_family(_bars(c2, c2 + 1, c2 - 1, c2), fam, oos_start=split)
    assert first.get("chosen") == second.get("chosen")


def test_orb_uses_dst_aware_new_york_open():
    ny = pytz.timezone("America/New_York")
    start = int(ny.localize(datetime(2025, 7, 1, 8, 0)).timestamp())   # summer: 13:30 UTC open
    n = 40
    c = np.full(n, 100.0)
    h, lo = c + 0.5, c - 0.5
    b = _bars(c, h, lo, c, tf="M15", t0=start)
    k = 10                                  # 08:00 + 10*15min = 10:30 NY — first bar after a 60-min range
    b.close[k] = b.high[k] = 105.0
    sigs, _ = _orb(b, {"session": "ny", "range_min": 60, "exit": 2.0, "side": "both"})
    assert [s.i for s in sigs] == [k] and sigs[0].direction == 1
    assert datetime.fromtimestamp(int(b.time[6]), tz=timezone.utc).hour == 13  # 09:30 NY in UTC


def test_summary_compounds_one_percent_risk():
    b = _bars([100, 100, 100], [100, 110, 100], [100, 100, 100], [100, 100, 100])
    s = summarize(simulate(b, [Signal(0, 1, 10.0, target_rr=1.0)], {}))
    assert s["final_balance"] == 353.5
