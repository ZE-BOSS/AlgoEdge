"""The APA strip-down must find the same patterns the live engine finds, and its
scale-out accounting must be arithmetic, not optimism."""

import numpy as np
import pandas as pd
import pytest

from backend.analytics.apa_research import (
    CONFLUENCES, APAConfig, apply_combination, build_candidates, detect_hs, detect_swings,
    variant_key,
)
from backend.analytics.strategy_search import Bars
from backend.strategies.core.swing_structure import detect_hs_pattern
from backend.strategies.core.swing_structure import detect_swings as engine_detect_swings


def _bars(symbol="GBPJPY", n=1500, tf=900, seed=11):
    rng = np.random.default_rng(seed)
    t = np.arange(n, dtype=np.int64) * tf + 1_767_225_600
    close = 150 + np.cumsum(rng.normal(0, 0.08, n))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + np.abs(rng.normal(0.05, 0.03, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0.05, 0.03, n))
    vol = rng.integers(20, 300, n).astype(float)
    return Bars(symbol, "M15" if tf == 900 else "M5", t, open_, high, low, close, np.full(n, 0.01), vol)


def _df(b):
    return pd.DataFrame({"time": b.time, "open": b.open, "high": b.high, "low": b.low,
                         "close": b.close, "volume": b.volume},
                        index=pd.to_datetime(b.time, unit="s"))


def test_swings_match_the_engine():
    b = _bars()
    mine = detect_swings(b, 3)
    theirs = engine_detect_swings(_df(b), 3)
    assert len(mine) == len(theirs)
    for a, c in zip(mine, theirs):
        assert a["type"] == c["type"] and a["price"] == pytest.approx(c["price"])


def test_head_and_shoulders_matches_the_engine():
    b = _bars()
    swings = detect_swings(b, 3)
    theirs = detect_hs_pattern(engine_detect_swings(_df(b), 3), 0.5, 0.3)
    mine = detect_hs(swings, 0.5, 0.3)
    assert (mine is None) == (theirs is None)
    if mine and theirs:
        assert mine["type"] == theirs["type"]
        assert mine["neck"]["price"] == pytest.approx(theirs["neckline_price"])


def test_candidates_carry_every_confluence_and_variant():
    sb, eb = _bars(), _bars(tf=300, n=4500, seed=12)
    cands = build_candidates(sb, eb)
    assert cands, "fixture produced no APA candidates"
    for c in cands:
        assert set(c.features) == set(CONFLUENCES)
        assert variant_key(5.0, True) in c.outcomes and variant_key("scaleout10", True) in c.outcomes


def test_entry_is_an_entry_timeframe_bar_after_the_break():
    sb, eb = _bars(), _bars(tf=300, n=4500, seed=12)
    for c in build_candidates(sb, eb):
        assert c.t_entry > c.t_bos
        assert c.entry == pytest.approx(float(eb.open[c.i_entry]))
        assert c.stop_dist > 0


def test_scaleout_books_a_third_at_2r_then_runs_risk_free():
    """Price runs to +2R, then reverses through entry: a fixed 1:10 target loses
    1R, while the scale-out keeps 1/3 x 2R and stops the rest at breakeven."""
    n, tf = 60, 300
    t = np.arange(n, dtype=np.int64) * tf + 1_767_225_600
    close = np.full(n, 100.0)
    close[1:6] = [100.5, 101.0, 101.5, 102.0, 102.5]     # +2R at a 1.0 stop distance
    close[6:] = 99.0                                      # back through entry
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + 0.01
    low = np.minimum(open_, close) - 0.01
    eb = Bars("T", "M5", t, open_, high, low, close, np.zeros(n))
    from backend.analytics.apa_research import Candidate, _resolve, _lists
    c = Candidate("T", 1, int(t[0]), 1, int(t[1]), 100.0, 1.0, 99.0, 105.0, 100.2, 99.8,
                  {f: True for f in CONFLUENCES}, {})
    bl = _lists(eb)
    fixed = _resolve(bl, c, 10.0, False)[0]
    scaled = _resolve(bl, c, "scaleout10", False)[0]
    assert fixed == pytest.approx(-1.0, abs=0.02)
    assert scaled == pytest.approx(2.0 / 3.0, abs=0.02)
    assert scaled > fixed


def test_filtering_is_exact_and_respects_a_daily_cap():
    sb, eb = _bars(), _bars(tf=300, n=4500, seed=12)
    cands = build_candidates(sb, eb)
    for f in ("session_ok", "trend_align", "head_not_breached"):
        kept = apply_combination(cands, use=(f,))
        assert all(c.features[f] for c in kept)
    capped = apply_combination(cands, max_per_day=1)
    per_day = {}
    for c in capped:
        per_day[c.t_entry // 86400] = per_day.get(c.t_entry // 86400, 0) + 1
    assert not per_day or max(per_day.values()) == 1


def test_retest_is_observed_not_required():
    """The live default enters without waiting for a retest; the research list must
    therefore contain candidates that never retested, so the flag can be ablated."""
    sb, eb = _bars(), _bars(tf=300, n=4500, seed=12)
    cands = build_candidates(sb, eb)
    assert any(not c.features["retest_occurred"] for c in cands)
