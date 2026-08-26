# Order Flow Data Layer — Tier 1 (AlgoEdge)

**Implements:** `backend/data/orderflow.py`, per Master Implementation Plan Phase 10 / Part 9 §9.2.

**Source:** `implementation/AlgoEdge-OrderFlow-Fundamental-Edge-Plan.md` (order-flow/DOM material) and
Master Plan Part 9's honest feasibility assessment.

---

## 1. Why "Tier 1" and not a full DOM/Bookmap clone

The execution venue is MT5 CFDs, not futures with a real limit order book. Feasibility table from the
master plan (§9.1):

| Data | Available on MT5 CFD? | Verdict |
|---|---|---|
| Level 2 / DOM depth | broker-synthesised, often 5–10 levels, often absent | partial, low fidelity |
| Time & sales / tape | `copy_ticks_range`, `COPY_TICKS_ALL` | available, **no aggressor flag** |
| Real volume | broker tick-count proxy, not exchange volume | proxy only |
| Bookmap-style heatmap | needs full-depth history | not reconstructable |

**Conclusion (unchanged from the master plan): do not build a Bookmap clone.** This module builds the
two primitives that ARE reconstructable from `copy_ticks_range` and DO transmit into price on CFD
instruments: Cumulative Volume Delta (CVD) and a tick-derived volume profile.

---

## 2. CVD — Cumulative Volume Delta (`classify_ticks`, `compute_cvd`, `aggregate_cvd_per_bar`)

Since MT5 ticks carry no aggressor flag, each tick is classified by proximity to the quoted bid/ask —
the standard proxy method:

```
last >= ask   -> BUY  (traded through/at the offer)
last <= bid   -> SELL (traded through/at the bid)
else          -> midpoint rule (closer to ask = BUY, closer to bid = SELL, exactly at mid = unclassified)
```

`aggregate_cvd_per_bar()` buckets signed tick volume into an existing OHLC bar index (e.g. a strategy's
own M5 candles) using the standard `[bar[i], bar[i+1])` convention — `.cumsum()` on its output gives a
per-bar cumulative CVD series.

**Verified** with synthetic ticks (70/30 buy/sell split, 2000 ticks): `classify_ticks` correctly
separated 1427 buy vs. 573 sell ticks (matches the generating distribution), `compute_cvd`'s final value
equalled the exact buy-minus-sell count, and `aggregate_cvd_per_bar`'s per-bar sum equalled the same
total CVD (aggregation is internally consistent).

## 3. Delta divergence (`detect_delta_divergence`)

Price makes a new high (low) over a lookback window while cumulative delta does NOT confirm it — buying
(selling) pressure isn't actually behind the extreme. **Verified**: a synthetic case where price made a
new high but CVD stayed below its own recent max correctly returned `BEARISH_DIVERGENCE`.

## 4. Absorption (`detect_absorption`)

Large one-sided delta on the latest bar (≥2 std. deviations above its own 20-bar mean) with minimal
price movement (bar range < 0.3×ATR) — volume was absorbed at a level rather than moving price.
**Verified**: a synthetic bar with delta=200 (vs. a ~5 std-dev background) and a 0.002-ATR range
correctly returned `ABSORPTION` / `BUY_ABSORBED`.

## 5. Volume profile / VPOC / value area (`compute_volume_profile`)

Standard volume-at-price histogram from tick data, with VPOC (the highest-volume price bin) and value
area (tightest contiguous range enclosing 70% of volume, expanding outward from VPOC — the conventional
algorithm). **Verified**: on a random-walk synthetic tick set centred at 100.0, VPOC and the value area
both landed correctly near 100.0.

## 6. Wiring status [10.5]

**Not wired into `MarketContext`** in this pass. The plan's task 10.5 says "wire all of the above into
MarketContext as confluence contributors only" — doing that properly needs a live tick fetch inside
`compute_market_context()`, adding latency and a network dependency to every single context computation,
and there is currently no strategy engine that reads an order-flow signal at all (wiring the *consumer*
side is a separate, larger task). The functions above are self-contained and ready to be called from
`market_context.py` or directly from a strategy engine once that wiring is undertaken — this is flagged
as follow-up work, not silently skipped.

**No live-data verification.** Every function above is verified against hand-constructed synthetic tick
DataFrames, not real MT5 tick history (no live MT5 connection is available in this environment). Before
trusting these signals on real data, fetch a real session's ticks via `fetch_ticks()` and sanity-check
the resulting CVD/profile against a known reference (e.g. a chart's own volume profile tool, if the
broker exposes one).
