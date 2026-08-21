# Strategy Optimization Research

**Date:** 2026-08-21
**Basis:** 61 parsable runs · 4,448 trade groups · 13,582 legs · 6 strategies · 10 instruments
**Companion docs:** `doc_conformance_audit.md` (does the code match the spec), `lookahead_audit.md` (is the data honest), `strategy_parameter_audit.md` (are the defaults sane)

All R-multiples here are **recomputed against entry-time risk**. The `pnl_r` / `expectancy_r`
fields in the run files are corrupted (divided by the breakeven-adjusted stop) and were not used.
Times are **true ET** — the stored feed is broker server time at UTC+3, so true ET = stored − 7h.

---

## 0. Framing — what problem are we actually solving?

Pooled true expectancy per strategy:

| Strategy | Trades | True expectancy | Win rate |
|---|---|---|---|
| APA | 306 | +0.136R | 46.4% |
| VWAP | 2,484 | +0.056R | 45.6% |
| Bias IFVG | 620 | +0.027R | 43.1% |
| CRT | 120 | +0.006R | 45.8% |
| NY Open Retest | 465 | −0.002R | 44.1% |
| HTF FVG Flip | 453 | −0.088R | 38.2% |

These are not broken strategies. They are **coin flips with a faint tilt, being eaten by
friction and by their own exit logic.** So the question is not "why is this losing 90%" — it is
"what moves a ~0R system to a positive-expectancy one." That reframing matters, because the
answers are different. A broken system needs rebuilding; a ~0R system needs a small number of
high-selectivity filters and a better exit.

Three findings below are worth more than everything else combined. They are §1, §2 and §3.

---

## 1. The exit logic is destroying the edge — highest-value finding in this document

Maximum favourable excursion, measured in R, grouped by how the leg actually exited:

| Exit reason | Legs | Median MFE | p75 | % that exceeded 2R |
|---|---|---|---|---|
| **BE_SL** | 1,046 | **2.31R** | 6.53R | **55%** |
| **TRAIL_SL** | 2,429 | **2.02R** | 2.83R | **51%** |
| TP1 | 1,815 | 1.74R | 2.94R | 38% |
| TP2 | 263 | 8.58R | 14.25R | 100% |
| TP3 | 244 | 6.00R | 20.57R | 100% |
| SL | 7,055 | 0.42R | 0.82R | 12% |
| SESSION_END | 714 | 0.82R | 1.11R | 11% |

Read the first row again. **1,046 legs ran to a median 2.31R of unrealized profit and were
closed for zero.** More than half of them exceeded 2R before being scratched. That is not risk
management; that is a mechanism for converting winners into nothing.

The second row is the same story: 2,429 trailed exits had median MFE of 2.02R and gave most of
it back.

Meanwhile the losers tell us the *entries are fine*: SL legs reached a median of only 0.42R
before failing. Bad entries would show high MFE on losers (trades that worked then reversed).
These don't. **The entries are not the problem. The exits are.**

### 1.1 Why this is happening

Three mechanisms compound:

1. **Break-even at 1.5R on a system whose median winner peaks near 2R.** With BE armed at 1.5R
   and MFE typically 2.0–2.3R, an enormous fraction of trades touch BE, drift back a few pips,
   and scratch. The stop is being tightened to zero exactly in the zone where the trade spends
   most of its life.
2. **The trailing gate was firing unconditionally.** `risk/engine.py` computed `unrealized_r`
   against the *mutated* stop. After BE moved the stop to entry, the denominator collapsed and
   `unrealized_r` read ~19R instead of ~1.7R — so `trail_activation_rr` always passed. Trailing
   armed on essentially every trade that reached BE, whether it deserved to or not. TRAIL_SL is
   the second-largest exit bucket in the corpus *because of a division bug*.
3. **TP2/TP3 almost never fill** — 3.7% of exits combined — yet the 50/30/20 split allocates
   **50% of every position** to them. Half of each winner is parked on targets the data says
   fill once in twenty-seven times. When they *do* fill, MFE is 6–8.6R, so the tail is real —
   but 50% allocation is far too much weight for a 3.7% hit rate.

### 1.2 What to change

| Change | From | To | Reasoning |
|---|---|---|---|
| `be_trigger_rr` | 1.5R | **2.5R, or disable** | BE must sit *beyond* the median MFE (2.0–2.3R), not inside it. Inside it, BE is a scratch generator. |
| `be_buffer_atr_mult` | 0.10 | keep | Scale-aware; the pip buffer is already 0. |
| `trail_activation_rr` | 1.5R | **2.5R** | Same logic, and now measured against a correct denominator. |
| `tp_splits` | 50/30/20 | **70/30, two tiers** | Concentrate on the target that actually fills. |
| `tp1_rr` | 1.5R | **1.5R (keep)** | TP1's median MFE is 1.74R — it is well-calibrated. Do not touch it. |
| `tp2_rr` | 3.0R | **4.0R** | If only 3.7% get there, make them count. Observed MFE at TP2 is 8.58R median. |
| TP3 | 5.0R | **remove** | 1.8% hit rate does not justify a third allocation. |

**Expected effect:** fewer scratches, materially higher average win, modestly lower win rate.
This is the single change most likely to move the book from ~0R to positive, and it can be
tested immediately on existing data.

---

## 2. Crypto is structurally wrong for four of these six strategies

Win rate by strategy × asset class, all runs pooled:

| Strategy | CRYPTO | FX | INDEX | METAL |
|---|---|---|---|---|
| HTF FVG Flip | **26.0%** (n=77) | 39.4% | 35.7% | 42.2% |
| Bias IFVG | **30.3%** (n=142) | 44.5% | 45.5% | 43.6% |
| NY Open Retest | 38.2% (n=76) | 49.6% | 43.8% | 38.3% |
| VWAP | 44.3% (n=415) | 44.7% | 44.7% | 44.1% |

The two ICT/SMC strategies lose **13–19 percentage points of win rate** on crypto versus every
other asset class. That is not variance at n=77 and n=142; it is a structural mismatch.

**Why.** FVG, inversion and liquidity-sweep logic all presume an order book that leaves visible
footprints — that the gap represents unfilled institutional interest which price returns to.
A crypto CFD is a broker-synthesised book referencing a fragmented 24/7 spot market. The
geometric pattern still *appears*; the mechanism it is supposed to represent does not exist.

Note VWAP is uniformly ~44% across all four classes. That is its own diagnosis: a strategy with
no class preference at all is not reading anything class-specific. Consistent with the
conformance finding that its defining pullback condition is missing.

**Action:** stop running HTF FVG Flip and Bias IFVG on BTCUSD/XRPUSD. Those runs also produced
the two largest dollar losses in the corpus, both driven by the swap and lot-sizing defects.
Removing them removes noise, not signal.

---

## 3. Session discipline is carrying real signal — and it was broken

Win rate by true-ET hour, all strategies pooled (n ≥ 100):

| ET hour | n | Win rate |
|---|---|---|
| 06:00 | 627 | **37.6%** ← worst |
| 07:00 | 685 | 42.9% |
| 08:00 | 359 | 44.0% |
| **09:00** | 204 | **49.0%** ← best |
| 10:00 | 204 | 42.2% |
| 11:00 | 109 | 41.3% |
| 21:00 | 573 | 45.0% |
| 22:00 | 569 | 43.4% |

The best hour is 09:00 ET — the New York cash open. The worst by a wide margin is 06:00 ET,
which is **precisely where the broken VWAP session gate was dumping its largest cluster of
trades** (see conformance audit §2.2-A). 627 trades at 37.6% in a single hour that the strategy
was never supposed to trade.

Corroborating evidence from the controlled A/B runs the user set up:

| Comparison | Filter ON | Filter OFF |
|---|---|---|
| APA CADJPY | −$965 (n=27, 40.7%) | −$3,613 (n=36, 36.1%) |
| APA XAUUSD | +$986 (n=13, 53.8%) | −$24 (n=21, 47.6%) |

Session filtering helps on both count and quality, every time it was tested.

**Action:** the timezone fix (§8 of the conformance audit) plus the VWAP gate fix should both
concentrate trades into better hours automatically. Verify on the re-run that the 06:00 cluster
disappears. Consider an explicit 09:00–11:00 ET preference for the session-anchored strategies.

---

## 4. Per-strategy: what the source method requires that the code omits

### 4.1 HTF FVG Flip — the displacement gate *(highest-value single fix)*

The doc defines an FVG as requiring *"a strong displacement candle 2."* The engine detects the
geometric three-bar gap and never tests displacement. Displacement is the entire distinction
between an institutional imbalance and three bars that happen to have a hole in them.

**Implement:** admit an FVG only if the middle candle's range ≥ **1.5 × ATR(14)** and its body ≥
**60% of its range**. Both thresholds are conventional in ICT practice and both are already
computable — the engine measures displacement today, but only to populate a score.

**Expected:** trade count −40 to −60%; win rate from 38.2% toward the mid-40s if the premise
holds. This is the only strategy in the book with clearly negative expectancy, and this is the
most likely reason.

### 4.2 VWAP — the pullback condition

`close < open` is not a pullback. Require the candle to actually move toward VWAP:
`abs(close − vwap) < abs(prev_close − vwap)`. Currently any red candle in a long setup fires,
including one accelerating away. Also enforce *first* pullback only, per the doc.

**Expected:** large reduction in trade count (VWAP is 2,484 of 4,448 groups — 56% of the entire
book), with the removed trades concentrated in the low-quality tail.

### 4.3 NY Open Retest — timeframe and order type

The doc specifies **M1** for break and retest; the engine uses **M5**. And the doc specifies a
**limit fill at `range_mid`**, while the backtester market-fills at the next bar's open. For a
mean-reversion-to-midpoint strategy, filling at market after the touch surrenders the entire
edge — the edge *is* the price level.

**Implement:** an `order_type` field on `TradeSignal`; fill limits at the limit price when the
bar's range contains it, markets at next open.

### 4.4 APA — body-close break of structure

`min(open, close)` is not a body close. A wick touch currently passes as a structural break,
which admits every liquidity sweep the strategy is explicitly designed to *avoid* trading.
Fix to a true body-close test. APA already has the best expectancy in the book (+0.136R) with
this defect present.

### 4.5 CRT — let it use its own target

CRT is the only faithful implementation. Its thesis is "price returns to C1's opposite extreme,"
and that target is computed and then discarded by the R grid. Exempt CRT from the grid, or clamp
the grid to `max_meaningful_rr`. Until then CRT is being judged on targets it does not believe in.

### 4.6 Bias IFVG — resolve the direction question

Doc describes the IFVG forming on the manipulation leg that *approached* the level; the code
scans *after* the tap. These select different setups. This needs a decision before the strategy
can be evaluated at all.

---

## 5. What is NOT worth doing

Stated explicitly, because the temptation will be there:

- **Do not tune entry thresholds on this data.** Every session-anchored result was measuring the
  wrong hours (timezone), VWAP was trading 24h, and R was inflated tenfold. Optimising against
  corrupted labels manufactures overfit.
- **Do not add more confluence factors.** The current `confluence_score` was a hard-coded
  constant in four engines — every trade in the APA dataset carries score 90. There is no
  evidence any scoring dimension predicts anything yet, because none was ever measured.
- **Do not chase the win rate.** At 44% with the exit fix in §1, the system does not need a
  higher win rate; it needs its winners to keep their gains.
- **Do not conclude anything from CRT.** 120 trades. Whatever it shows is noise.

---

## 6. Recommended sequence

1. **Re-run the book** with the fixes already applied (swap units, R denominator, timezone, VWAP
   session gate, frozen instrument data). Change nothing else. This establishes an honest baseline
   — the dollar figures will move enormously, the R figures should barely move. If R moves
   materially, something is still wrong.
2. **Test the exit change** (§1.2) against that baseline. Single highest-value experiment.
3. **Add the displacement gate** to HTF FVG Flip (§4.1) and the pullback condition to VWAP (§4.2).
4. **Drop crypto** for the two ICT strategies (§2).
5. **Fix NY Retest's timeframe and order type** (§4.3), which requires the `order_type` field.
6. Only then evaluate whether any of these has an edge.

---

## 7. The honest bottom line

Nothing in this document creates an edge. §1 recovers value the system is currently throwing
away; §2 removes markets where the premise does not apply; §3 and §4 restore rules the sources
specify and the code omits.

Whether any of these six methods has genuine edge **remains unknown and cannot be determined
from this dataset** — not because the sample is small (4,448 trades is respectable) but because
the labels were wrong. Corrupted R, three-hour session displacement, phantom costs and a
24-hour VWAP mean these runs measured something other than the strategies as documented.

The re-run in step 1 is not a formality. It is the first honest measurement this system will
have produced.
