# 06 — DriftJumpAlpha: catching SELLS on Crash

**Stage F** · produced 2026-08-30
Scripts: `research/data/mt5_probe.py`, `tick_feasibility.py`,
`crash_spike_stats.py`, `crash_short_ev.py`
Live data: MT5 **Deriv-Demo, connected**, 798 symbols. Synthetics trade 24/7, so
the weekend was not a constraint — every measurement below is from live and
recent data, not a substitute.

---

## Verdict

**Do not build the sell side.** Three independent measurements each kill it on
their own, and together they close the question rather than deferring it.

---

## 1. The spike has no interior — it completes in a single tick

6 hours of `COPY_TICKS_ALL` per symbol:

| symbol | ticks / 6h | median tick interval | spike length | spike depth |
|---|---:|---:|---:|---:|
| Crash 300 | 21,319 | **1000 ms** | **1 tick** | −0.649% |
| Crash 500 | 20,840 | **1001 ms** | **1 tick** | −0.285% |
| Crash 1000 | 20,975 | **1001 ms** | **1 tick** | −0.330% |

The feed emits **exactly one tick per second** — a fixed synthetic cadence, not
a market tape. There are no sub-second ticks, no order arrivals to count, and no
aggressor or volume information.

And the spike is a single discontinuous jump from one tick to the next. There is
no "spike forming" to detect, because there is no interior to observe. This is
not a resolution problem that better data would solve; the instrument is
generated that way.

**This eliminates outright:** tick-based order-arrival detection, synthetic
10-second bar aggregation (a 1 Hz feed cannot produce sub-second structure), and
volatility-burst detection on tick volume.

## 2. Spike arrival is memoryless — waiting tells you nothing

50,000 M1 bars per symbol (≈35 days), spike = 99th-percentile open→low drop:

| symbol | spikes | mean gap | sd | **CV** | p25 / p50 / p75 / p95 (min) |
|---|---:|---:|---:|---:|---|
| Crash 300 | 500 | 100.0 min | 93.8 | **0.938** | 30 / 71 / 140 / 304 |
| Crash 500 | 500 | 100.0 min | 97.4 | **0.973** | 29 / 71 / 137 / 304 |
| Crash 1000 | 500 | 99.9 min | 98.7 | **0.988** | 32 / 68 / 135 / 302 |

A coefficient of variation of 1.0 is the signature of an exponential
inter-arrival distribution — a Poisson process. All three land on it.

For such a process the expected wait to the next event is **constant regardless
of how long you have already waited**. There is no "overdue" state. This is a
mathematical property, not a weak empirical result, and it disposes of the
`compute_gap_percentile` pre-positioning idea directly: the gap percentile is a
perfectly good *description* of the distribution and carries **zero** timing
information, because the distribution is memoryless.

## 3. Being short has no edge either

If you cannot time the spike, the fallback is to hold a short and collect
spikes while paying the drift. Over the same 35 days:

| symbol | net price move | gross up | gross down | top-1% spikes | spread |
|---|---:|---:|---:|---:|---:|
| Crash 300 | −18.09% | +1,885% | −1,903% | −384% (20% of downside) | 0.052% |
| Crash 500 | −7.30% | +519% | −526% | −129% (24%) | 0.010% |
| Crash 1000 | **+3.59%** | +287% | −283% | −108% (38%) | 0.008% |

Read the gross columns. On Crash 300, ±1,890% of gross movement nets to −18%
— the net is **1% of the flow**. That is a random walk, and the sign is noise.
Crash 1000 drifts the *other* way over the same window, which is the giveaway.

Short-and-hold, net of spread:

| symbol | held 60 bars | held 240 bars | held 1440 bars (n=34) |
|---|---:|---:|---:|
| Crash 300 | −0.033% | +0.022% | +0.569% |
| Crash 500 | −0.002% | +0.023% | +0.176% |
| Crash 1000 | −0.013% | −0.026% | −0.116% |

Signs disagree across sibling instruments and across holding periods, on 34
daily samples. There is no effect here.

**Why this is what we should expect.** Deriv prices these so drift and spike
offset. The instrument is a fair game by construction, and the broker's edge is
the spread. Nothing about the buy side's success contradicts that —
DriftJumpAlpha does not profit from direction, it profits from *structure within
the drift*, and that structure has no mirror image on the spike.

One number is worth keeping: a spike, **if you were already positioned**, pays
14× (Crash 300) to 25× (Crash 1000) the spread. The payoff is genuinely large.
It is the entry that is impossible, not the exit.

---

## What to do instead

DriftJumpAlpha's **buy** side is the single best thing in the book:
+0.249R/trade, PF 1.56, and it *improves* out-of-sample (+0.215 IS → +0.284
OOS). The productive move is to widen what works rather than build what cannot.

**Extend DriftJumpAlpha to the two untested Crash symbols.** Both were in §0.2
and neither was ever run. Both are confirmed live on the terminal:

| symbol | present | spread | spike depth | depth / spread |
|---|---|---:|---:|---:|
| **Crash 500 Index** | ✅ | 0.0104% | 0.244% | **23.5×** |
| **Jump 100 Index** | ✅ | — | — | different process — drift *and* jumps both ways |

Crash 500 sits between the two symbols DriftJumpAlpha already trades profitably,
on every measured property — arrival rate, depth, and depth-to-spread ratio. It
is the highest-confidence untested cell in the entire programme.

Jump 100 is a genuinely different process (two-sided jumps, not drift-plus-crash)
and should be treated as an unknown, not an extrapolation.

## Stage F checklist

| item | status |
|---|---|
| Baseline: spike frequency, size, speed, theoretical R | ✅ measured, all three symbols |
| Tick-data feasibility, verified against the live terminal | ✅ **1 Hz feed, single-tick spikes — infeasible** |
| Sub-minute bar aggregation | ✅ ruled out — a 1 Hz feed has no sub-second structure |
| `compute_gap_percentile` as a pre-positioning signal | ✅ ruled out — arrival is memoryless (CV ≈ 1.0) |
| Volatility-burst on tick volume | ✅ ruled out — no volume/aggressor data on the feed |
| Resting stop-entry that the spike fills | ✅ ruled out — a stop fills *after* a single-tick gap, i.e. at the bottom, into the rebound |
| Measure each candidate on the §0.3 metric set | ✅ each eliminated before it needed one |
| Report | ✅ this file |

**Negative result, recorded so it is not re-explored.** The Crash sell side is
not tradeable on this feed. Not "hard" — structurally impossible, for reasons
that are properties of the instrument rather than of our tooling.
