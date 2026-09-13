"""The VWAP strip-down must reproduce the live engine's own maths, keep each
setup's confluences separate, refuse degenerate stops, and filter exactly —
including the path-dependent rules."""

import numpy as np
import pandas as pd
import pytest

from backend.analytics.strategy_search import Bars, atr
from backend.analytics.vwap_research import (
    CONFLUENCES, VWAPConfig, ablate, anchored_vwap, apply_combination, build_candidates,
    session_ids, stats, took_trade, variant_key,
)
from backend.strategies.strategy_vwap.engine import _calculate_anchored_vwap_with_bands

PULLBACK_ONLY = {"slope_aligned", "momentum_aligned", "inside_1sigma", "converging"}
REVERSION_ONLY = {"wick_rejection", "slope_flat", "trend_neutral", "beyond_2sigma"}
SHARED = {"volume_ok", "in_session_window"}


def _bars(days=12, seed=4, tf=300, spread=0.01):
    rng = np.random.default_rng(seed)
    n = days * 288
    t = np.arange(n, dtype=np.int64) * tf + 1_767_225_600      # 2026-01-01 00:00 UTC
    close = 150 + np.cumsum(rng.normal(0, 0.05, n))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + np.abs(rng.normal(0.02, 0.01, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0.02, 0.01, n))
    vol = rng.integers(20, 400, n).astype(float)
    return Bars("GBPJPY", "M5", t, open_, high, low, close, np.full(n, spread), vol)


def test_vwap_and_sigma_match_the_live_engine():
    b = _bars()
    df = pd.DataFrame({"time": b.time, "open": b.open, "high": b.high, "low": b.low,
                       "close": b.close, "volume": b.volume},
                      index=pd.to_datetime(b.time, unit="s"))
    want_vwap, want_std = _calculate_anchored_vwap_with_bands(df, 15, 0)
    got_vwap, got_std = anchored_vwap(b, session_ids(b.time, "et_0930"), 0)
    assert np.allclose(got_vwap, want_vwap, rtol=1e-9, atol=1e-9)
    assert np.allclose(got_std, want_std, rtol=1e-9, atol=1e-9)


def test_sessions_break_at_the_0930_et_anchor():
    b = _bars(days=4)
    sess = session_ids(b.time, "et_0930")
    for k in np.flatnonzero(np.diff(sess) != 0):
        et = pd.Timestamp(int(b.time[k + 1]), unit="s", tz="UTC").tz_convert("America/New_York")
        assert (et.hour, et.minute) == (9, 30)


def test_features_are_scoped_to_the_setup_that_owns_them():
    """A reversion-only gate on a pullback candidate would block nothing and make
    its ablation row read 'no effect' — the defect this guards."""
    for c in build_candidates(_bars()):
        keys = set(c.features)
        if c.setup == "pullback":
            assert keys == PULLBACK_ONLY | SHARED
        else:
            assert keys == REVERSION_ONLY | SHARED
        assert keys <= set(CONFLUENCES)


def test_no_candidate_has_a_degenerate_stop():
    """Near-zero stops produced single trades worth thousands of R before the
    spread floor and the ATR discard existed."""
    b = _bars()
    a = atr(b, 14)
    cfg = VWAPConfig()
    for c in build_candidates(b, cfg):
        assert c.stop_dist >= cfg.min_sl_spread_mult * float(b.spread[c.i_entry]) - 1e-12
        assert c.stop_dist >= cfg.min_stop_atr_discard * float(a[c.i_entry - 1]) - 1e-12


def test_outcomes_stay_in_a_sane_r_range():
    key = variant_key(2.0, True, False)
    for c in build_candidates(_bars()):
        r = c.outcomes[key][0]
        assert -6.0 < r < 60.0, f"{r} R is an artefact, not a trade"


def test_every_candidate_has_every_variant():
    for c in build_candidates(_bars()):
        assert variant_key("sigma2", True, False) in c.outcomes
        r, t_exit, reason = c.outcomes[variant_key(2.0, True, False)]
        assert t_exit >= c.t_entry and reason


def test_entry_is_the_next_bar_open():
    b = _bars()
    for c in build_candidates(b)[:25]:
        assert c.entry == pytest.approx(float(b.open[c.i_entry]))


def test_filtering_only_removes_candidates_failing_the_gate():
    cands = build_candidates(_bars())
    for f in ("in_session_window", "volume_ok"):
        kept = apply_combination(cands, use=(f,))
        assert kept, f"{f} removed everything — fixture too quiet to measure"
        assert all(c.features.get(f, True) for c in kept)
    # a pullback gate must not silently drop reversion candidates
    pull_gate = apply_combination(cands, use=("converging",))
    assert any(c.setup == "reversion" for c in pull_gate)


def test_first_of_session_is_resolved_after_the_other_filters():
    cands = [c for c in build_candidates(_bars()) if c.setup == "pullback"]
    first_only = apply_combination(cands, use=("converging",), first_only=True)
    per_session = {}
    for c in first_only:
        per_session[c.session] = per_session.get(c.session, 0) + 1
    assert per_session and max(per_session.values()) == 1
    for c in first_only:
        assert not [x for x in cands if x.session == c.session
                    and x.t_entry < c.t_entry and x.features["converging"]]


def test_max_per_session_caps_trades():
    capped = apply_combination(build_candidates(_bars()), max_per_session=2)
    per = {}
    for c in capped:
        per[c.session] = per.get(c.session, 0) + 1
    assert per and max(per.values()) <= 2


def test_candidates_reach_the_tradeable_session_window():
    """The bound on the candidate list must not truncate each session before the
    window opens: the first attempt kept the first 12 candidates per session, and
    since the anchor is 09:30 ET and the window opens at 10:30, the study measured
    'in_session_window admits 0 of 7,620'."""
    cands = build_candidates(_bars())
    for setup in ("pullback", "reversion"):
        sub = [c for c in cands if c.setup == setup]
        assert sub, f"no {setup} candidates at all"
        assert any(c.features["in_session_window"] for c in sub), \
            f"every {setup} candidate falls outside the tradeable window"


def test_candidates_are_spaced_not_truncated():
    cfg = VWAPConfig()
    seen: dict[tuple[int, str], list[int]] = {}
    for c in build_candidates(_bars(), cfg):
        seen.setdefault((c.session, c.setup), []).append(c.i_entry)
    for idxs in seen.values():
        gaps = [b - a for a, b in zip(sorted(idxs), sorted(idxs)[1:])]
        assert all(g >= cfg.min_bars_between_candidates for g in gaps)


def test_ablation_skips_confluences_the_population_cannot_have():
    pull = [c for c in build_candidates(_bars()) if c.setup == "pullback"]
    names = {row["confluence"] for row in ablate(pull, variant_key(2.0, True, False))}
    assert names <= PULLBACK_ONLY | SHARED
    assert not (names & REVERSION_ONLY)


def test_a_target_already_behind_price_is_not_counted_as_a_trade():
    """`no_target` candidates are non-trades. Counting them as 0R produced markets
    reporting ~2,075 'trades' at +-0.00xR with a meaningless profit factor."""
    cands = build_candidates(_bars())
    key = variant_key("vwap", True, False)
    no_target = [c for c in cands if c.outcomes[key][2] == "no_target"]
    assert no_target, "fixture has no behind-price structural targets to check"
    assert not any(took_trade(c, key) for c in no_target)
    assert stats(no_target, key)["n"] == 0
    # and a real target is still counted
    real = [c for c in cands if c.outcomes[key][2] != "no_target"]
    assert stats(real, key)["n"] == len(real)


def test_ablation_partitions_the_population():
    pull = [c for c in build_candidates(_bars()) if c.setup == "pullback"]
    key = variant_key(2.0, True, False)
    total = stats(apply_combination(pull), key)["n"]
    for row in ablate(pull, key):
        if row["n_on"] and row["n_blocked"]:
            assert row["n_on"] + row["n_blocked"] == total
