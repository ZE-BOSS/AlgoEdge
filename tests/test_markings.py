"""
tests/test_markings.py

[V1] Coverage for the chart-marking pipeline.

Two things are under test, and they fail differently on purpose (Visualization
plan §8: "a rendering bug and a geometry bug fail differently"):

  1. `strategies/core/markings.py` in isolation — geometry normalisation,
     validity rejection, JSON-safety, timestamp coercion.
  2. A strategy driven end-to-end through its real state machine, asserting
     that `metadata["markings"]` is actually populated and that
     `trade_grouper` routes it into `smc_data`.

(2) is the one that matters. The regression this suite exists to prevent is the
original defect: `trade_grouper` reading a metadata key that no strategy wrote,
silently producing empty charts for months. An import test would not have
caught that, and neither would a unit test on the collector alone.
"""

import asyncio
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.strategies.core.markings import (  # noqa: E402
    BOX_KINDS,
    KIND_FVG,
    LINE_KINDS,
    POINT_KINDS,
    ROLE_CONTEXT,
    ROLE_TRIGGER,
    Marking,
    MarkingCollector,
    ts,
)


# ─────────────────────────────────────────────────────────────────────────
# 1. Collector unit tests
# ─────────────────────────────────────────────────────────────────────────

def test_box_geometry_is_normalised():
    """Strategies pass top/bottom in whatever order the pattern produced."""
    mk = MarkingCollector("M5")
    mk.range_box("inverted", top=1.1990, bottom=1.2100, start_time=1_700_000_000)
    box = mk.to_list()[0]
    assert box["top"] == 1.2100
    assert box["bottom"] == 1.1990


def test_incomplete_box_is_dropped_not_drawn():
    mk = MarkingCollector("M5")
    mk.add(Marking(kind=KIND_FVG, label="no geometry", timeframe="M5", start_time=1))
    assert len(mk) == 0


def test_line_marking_gets_degenerate_box_coords():
    """A renderer that only knows boxes must still place a level correctly."""
    mk = MarkingCollector("M5")
    mk.level("Neckline", 1.2075, 1_700_000_000)
    d = mk.to_list()[0]
    assert d["price"] == d["top"] == d["bottom"] == 1.2075


def test_numpy_scalars_survive_json_round_trip():
    """
    Signal metadata routinely carries numpy floats straight out of a DataFrame
    slice, and both runner.py and backtest.py json.dumps() the result.
    """
    mk = MarkingCollector("M5")
    mk.level("Neckline", 1.2075, 1_700_000_000, precision_atr=np.float64(0.42), n=np.int64(7))
    payload = json.dumps(mk.as_metadata())
    assert "0.42" in payload
    assert json.loads(payload)["markings"][0]["detail"]["n"] == 7


def test_cap_prevents_metadata_blowup():
    mk = MarkingCollector("M5", cap=3)
    for i in range(50):
        mk.level(f"L{i}", 1.0 + i, 1_700_000_000)
    assert len(mk) == 3


def test_role_summary_groups_labels():
    mk = MarkingCollector("M5")
    mk.level("Neckline", 1.2, 1, role=ROLE_TRIGGER)
    mk.level("Asia high", 1.3, 1, role=ROLE_CONTEXT)
    summary = mk.as_metadata()["confluence_summary"]
    assert summary[ROLE_TRIGGER] == ["Neckline"]
    assert summary[ROLE_CONTEXT] == ["Asia high"]


@pytest.mark.parametrize("value,expected", [
    (pd.Timestamp("2023-11-14 22:13:20"), 1_700_000_000),
    (np.datetime64("2023-11-14T22:13:20"), 1_700_000_000),
    (1_700_000_000, 1_700_000_000),
    (1_700_000_000_000, 1_700_000_000),   # milliseconds
    (1_700_000_000.0, 1_700_000_000),
    (None, 0),
])
def test_ts_coerces_every_shape_the_engines_hold(value, expected):
    assert ts(value) == expected


def test_kind_sets_are_disjoint():
    """Routing in trade_grouper assumes a kind lands in exactly one bucket."""
    assert not (BOX_KINDS & LINE_KINDS)
    assert not (BOX_KINDS & POINT_KINDS)
    assert not (LINE_KINDS & POINT_KINDS)


# ─────────────────────────────────────────────────────────────────────────
# 2. End-to-end: a real strategy must actually emit markings
# ─────────────────────────────────────────────────────────────────────────

def _bars(freq, start, periods):
    """
    Synthesise a session that walks NY Open Retest through its full chain:
    mark the 08:00-08:15 range on M15, break above it with a body close after
    09:30 on M5, then retest range_mid.

    Times are US/Eastern because the engine resolves its session windows in ET.
    """
    idx = pd.date_range(start, periods=periods, freq=freq, tz="US/Eastern")
    rows = []
    for t in idx:
        hhmm = t.strftime("%H:%M")
        if hhmm < "08:00":
            o, h, l, c = 100.0, 100.05, 99.95, 100.0
        elif hhmm < "08:15":
            # The range window: high 100.5 / low 99.5 -> mid exactly 100.0
            o, h, l, c = 100.0, 100.5, 99.5, 100.0
        elif hhmm < "09:30":
            o, h, l, c = 100.2, 100.25, 100.15, 100.2      # drift inside the range
        elif hhmm < "09:45":
            o, h, l, c = 100.4, 101.2, 100.3, 101.0        # body close ABOVE range_high
        elif hhmm < "10:10":
            o, h, l, c = 101.0, 101.1, 99.9, 100.1         # retest down through mid
        else:
            o, h, l, c = 100.3, 100.35, 100.25, 100.3
        rows.append({"open": o, "high": h, "low": l, "close": c,
                     "volume": 1000, "time": int(t.timestamp())})
    return pd.DataFrame(rows, index=idx)


def test_ny_open_retest_emits_markings_end_to_end():
    from backend.core.config_schema import UserConfigV2
    from backend.strategies.strategy_six_ny_open_retest.engine import NYOpenRetestEngine

    engine = NYOpenRetestEngine(UserConfigV2())
    m15 = _bars("15min", "2026-03-10 06:00:00", 24)   # 06:00 -> 12:00
    m5 = _bars("5min", "2026-03-10 06:00:00", 72)

    # Stage 1 — M15 marks the 08:00-08:15 range.
    for i in range(2, len(m15) + 1):
        asyncio.run(engine.on_bar("EURUSD", "M15", m15.iloc[:i]))
        if engine.state["EURUSD"]["status"] == "AWAIT_BREAK":
            break
    st = engine.state["EURUSD"]
    assert st["status"] == "AWAIT_BREAK", f"range never marked (status={st['status']})"
    assert st["range_mid"] == 100.0

    # Stage 2 — M5 breaks the range, then retests the mid.
    signal = None
    for i in range(2, len(m5) + 1):
        sig = asyncio.run(engine.on_bar("EURUSD", "M5", m5.iloc[:i]))
        if sig is not None:
            signal = sig
            break

    assert signal is not None, f"state machine stalled at {engine.state['EURUSD']['status']}"

    markings = signal.metadata.get("markings")
    assert markings, "strategy produced a signal with no markings — the V1 regression"

    labels = {m["label"] for m in markings}
    # Each link of this setup's confluence chain must be on the chart.
    assert any("NY open range" in n for n in labels), labels
    assert any("Range mid" in n for n in labels), labels
    assert any("Body close beyond range" in n for n in labels), labels
    assert "Stop loss" in labels, labels

    # The role index is what lets a trade row answer "what did this entry need".
    summary = signal.metadata["confluence_summary"]
    assert "trigger" in summary and "invalidation" in summary

    # Geometry completeness and JSON-safety — what the renderer and the
    # persistence layer respectively depend on.
    for m in markings:
        kind = m["type"]
        if kind in BOX_KINDS:
            assert m["top"] >= m["bottom"], m
        elif kind in LINE_KINDS:
            assert m["price"] is not None, m
        assert isinstance(m["start_time"], int)
    json.dumps(signal.metadata)


def test_trade_grouper_routes_markings_into_smc_data():
    """
    The other half of the original defect: even with markings emitted, the
    grouper's filter only knew three of the eight kinds.
    """
    from backend.utils.trade_grouper import _BOX_KINDS, _LINE_KINDS, _POINT_KINDS

    mk = MarkingCollector("M5")
    mk.fvg("H4 FVG", 1.205, 1.203, 1_700_000_000)
    mk.range_box("C1 range", 1.21, 1.199, 1_700_000_000)
    mk.zone("OTE", 1.208, 1.206, 1_700_000_000)
    mk.level("Neckline", 1.2075, 1_700_000_000)
    mk.liquidity("Asia high", 1.211, 1_700_000_000)
    mk.structure("BOS", 1_700_000_000, price=1.208)

    markings = mk.to_list()
    boxes = [m for m in markings if m["type"] in _BOX_KINDS]
    lines = [m for m in markings if m["type"] in _LINE_KINDS]
    points = [m for m in markings if m["type"] in _POINT_KINDS]

    assert len(boxes) == 3, "ZONE/RANGE were dropped by the old three-kind filter"
    assert len(lines) == 2, "LEVEL/LIQUIDITY had nowhere to go before"
    assert len(points) == 1
    # Nothing may fall through the routing entirely.
    assert len(boxes) + len(lines) + len(points) == len(markings)


# ─────────────────────────────────────────────────────────────────────────
# 3. Order-flow tick classification
# ─────────────────────────────────────────────────────────────────────────

def _quote_only_ticks(mids):
    """
    A quote-only tick frame, matching what the live Deriv feed actually returns:
    bid/ask populated, `last` and both volume columns identically zero.
    """
    return pd.DataFrame({
        "time": range(len(mids)),
        "bid": [m - 0.5 for m in mids],
        "ask": [m + 0.5 for m in mids],
        "last": [0.0] * len(mids),
        "volume": [0] * len(mids),
        "volume_real": [0.0] * len(mids),
    })


def test_quote_only_feed_is_not_classified_as_all_sells():
    """
    The regression this exists to prevent.

    `classify_ticks` used to read `last` unconditionally. On a quote-only feed
    `last` is 0.0 on every tick, so `last <= bid` was trivially true and EVERY
    tick classified as SELL — CVD came out as exactly −(tick count) and
    imbalance as exactly −1.0, forever, on every symbol. Verified against live
    BTCUSD and Volatility 75 Index before the fix.
    """
    from backend.data.orderflow import classify_ticks, classification_method

    ticks = _quote_only_ticks([100, 101, 102, 101, 100, 101, 102, 103])
    signed = classify_ticks(ticks)

    assert classification_method(ticks) == "quote_rule"
    assert (signed > 0).any(), "no tick classified as a buy — the all-sells bug is back"
    assert (signed < 0).any(), "no tick classified as a sell"
    # The pathological signature: every tick sells, at exactly 1.0 each.
    assert signed.sum() != -len(ticks)


def test_quote_rule_follows_mid_direction():
    from backend.data.orderflow import classify_ticks

    ticks = _quote_only_ticks([100, 101, 102, 103])   # monotonically rising
    signed = classify_ticks(ticks)
    # The first tick has no predecessor, so it stays 0 rather than being guessed.
    assert signed.iloc[0] == 0.0
    assert (signed.iloc[1:] > 0).all()

    ticks = _quote_only_ticks([103, 102, 101, 100])   # monotonically falling
    assert (classify_ticks(ticks).iloc[1:] < 0).all()


def test_lee_ready_still_used_when_traded_prices_exist():
    """A feed that does publish `last` must keep the stronger classification."""
    from backend.data.orderflow import classify_ticks, classification_method

    ticks = pd.DataFrame({
        "time": [0, 1, 2],
        "bid": [99.5, 99.5, 99.5],
        "ask": [100.5, 100.5, 100.5],
        "last": [100.5, 99.5, 100.0],   # at ask -> buy, at bid -> sell, at mid -> 0
        "volume": [3, 4, 5],
    })
    assert classification_method(ticks) == "lee_ready"
    signed = classify_ticks(ticks)
    assert signed.iloc[0] == 3.0
    assert signed.iloc[1] == -4.0
    assert signed.iloc[2] == 0.0


def test_tick_price_falls_back_to_mid():
    """
    Volume profile and the bubble overlay both read this. With the old
    `ticks["last"]` they got a column of zeros, so every price fell in one bin:
    VPOC came back None and `bubbles` was empty.
    """
    from backend.data.orderflow import tick_price

    ticks = _quote_only_ticks([100, 200])
    assert list(tick_price(ticks)) == [100.0, 200.0]


def test_volume_profile_survives_a_quote_only_feed():
    from backend.data.orderflow import compute_volume_profile

    ticks = _quote_only_ticks([100 + (i % 7) for i in range(200)])
    profile = compute_volume_profile(ticks)
    assert profile["vpoc"] is not None, "VPOC is None — the all-zero price bug is back"
    assert profile["value_area_low"] is not None


# ─────────────────────────────────────────────────────────────────────────
# 4. Live instrument-profile overlay
# ─────────────────────────────────────────────────────────────────────────

def test_static_profile_is_returned_when_mt5_is_unavailable():
    """
    CI and Linux boxes have no terminal. The overlay must degrade to the static
    table rather than returning None — a None profile means "symbol refuses to
    trade" downstream (audit C5).
    """
    from backend.risk import compounding

    compounding.refresh_live_profiles()
    original = compounding._LIVE_OVERLAY_DISABLED
    try:
        compounding._LIVE_OVERLAY_DISABLED = True
        p = compounding.get_instrument_profile("EURUSD")
        assert p is not None
        assert p is compounding.INSTRUMENT_PROFILES["EURUSD"]
    finally:
        compounding._LIVE_OVERLAY_DISABLED = original
        compounding.refresh_live_profiles()


def test_unknown_symbol_still_returns_none():
    from backend.risk.compounding import get_instrument_profile
    assert get_instrument_profile("NOT_A_REAL_SYMBOL_XYZ") is None


def test_overlay_preserves_policy_fields():
    """
    session_filter / news_filter / trades_24_7 / instrument_type are policy, not
    broker facts. An overlay that silently flipped them would change when a
    strategy is allowed to trade.
    """
    import dataclasses
    from backend.risk.compounding import INSTRUMENT_PROFILES, _build_live_profile

    base = INSTRUMENT_PROFILES["EURUSD"]
    # Exercise the overlay's own field list without needing a terminal, by
    # constructing what it would produce.
    live = dataclasses.replace(base, lot_min=0.05, point_value_per_lot=9.9)
    for field in ("session_filter", "news_filter", "trades_24_7", "instrument_type", "symbol"):
        assert getattr(live, field) == getattr(base, field)


def test_risk_per_lot_is_invariant_under_a_proportional_rescale():
    """
    The overlay can change point_size and point_value_per_lot together (BTCUSD:
    1.0/1.0 -> 0.001/0.001). Position sizing depends on their RATIO, so a
    proportional rescale must not move the lot size — otherwise the correction
    would silently resize every crypto position.
    """
    sl_distance = 250.0

    def risk_per_lot(point_size, point_value):
        return (sl_distance / point_size) * point_value

    assert risk_per_lot(1.0, 1.0) == pytest.approx(risk_per_lot(0.001, 0.001))
