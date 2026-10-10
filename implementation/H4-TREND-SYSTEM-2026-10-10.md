# A 4-hour system that survives, and what it took to find it

**Date:** 2026-10-10
**Script:** `scripts/run_h4_lab.py` · **Raw:** `debug/h4_lab_deriv.json`
**Companion:** `CRT-AND-SWEEP-STUDY-2026-10-10.md` (the four marketed models, all rejected)

---

## The system

```
timeframe   4-hour candles
entry       break of the 6-candle (one day) high -> long, low -> short
            filled at the OPEN OF THE NEXT 4H CANDLE, not at the level
stop        2.5 x ATR(14) on 4H candles
exit        chandelier trail at 4.0 x ATR from the best close since entry
            NO TARGET
filter      none
sizing      0.5% of equity per trade, compounding
markets     XAUUSD, XAGUSD, EURUSD, GBPUSD  (and see "markets to avoid")
```

That is the whole thing. The parameters were chosen on data ending
**2025-04-01** and never touched again.

## What it does

| Sample | Trades | Expectancy | t | Win | Payoff | $10k → | Max DD | Sharpe | Sortino |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **Deriv, out-of-sample** (1.4y) | 281 | **+0.3023R** | **+2.17** | — | — | **$15,975 (+59.8%)** | 10.1% | **+1.93** | +7.90 |
| **FundedNext, whole history** (1.3y) | 175 | **+0.3155R** | +1.56 | — | — | **$12,900 (+29.0%)** | 8.4% | **+1.52** | +4.26 |
| Gold alone, Deriv OOS | 64 | +0.6819R | +1.47 | 42.2% | 3.70 | $12,937 (+29.4%) | 4.3% | +1.96 | +32.22 |
| Gold alone, FundedNext | 52 | +0.7762R | +1.29 | 36.5% | 4.57 | $12,139 (+21.4%) | 4.9% | +1.39 | +8.19 |
| Gold alone, Deriv full (4.9y) | 260 | +0.2411R | +1.63 | 35.8% | 2.66 | $15,415 (+54.2%) | 14.6% | +0.76 | +1.80 |

The shape is textbook trend following: **win rate 36–42%, payoff 2.7–4.6.**
Most of the money comes from a handful of trades. If you see a 50% win rate
and small winners, something is broken.

---

## Why I believe this one and not the four from the videos

The CRT study found 5 winning cells out of 446 where chance gives ~10 — a
search producing fewer winners than noise. This one is built so that cannot
happen:

1. **Split first.** Everything before 2025-04-01 picked the parameters.
   Everything after was touched once.
2. **A surrogate baseline.** The identical search, run on entries shuffled to
   random candles, 24 times. Its 95th percentile is what "best of 96 tries on
   noise" is worth.
   **Pooled in-sample +0.0918R against a surrogate p95 of +0.0182R**, and the
   out-of-sample +0.0740R still clears it.
3. **One configuration for the whole universe, not one per market.** A trend
   system is a portfolio system — the tail arrives in whichever market happens
   to trend and you cannot know which in advance. Picking per market is both
   the wrong model and sixteen times the selection. My first attempt did pick
   per market, and it collapsed out of sample on fifteen of sixteen; that
   failure is what pointed at the right design.
4. **A second broker that was never used to choose anything.** FundedNext
   independently reproduces it: +0.3155R against Deriv's +0.3023R.

### Two structural lessons this inherits

* **No fixed target.** A target on a trend system removes the right tail and
  keeps every loser. This is the mistake that wasted an earlier session here,
  and it is exactly the mistake the four marketed models all make.
* **Next-candle-open fills.** A level entry is not a fill you can get.

---

## Markets to avoid, which is a finding too

**Both feeds agree the indices lose on this system:**

| Market | Feed | Trades | Expectancy | t |
|---|---|---:|---:|---:|
| US30 | FundedNext | 94 | **−0.3136R** | **−2.95** |
| SPX500 | FundedNext | 74 | −0.1963R | −1.51 |
| US SP 500 | Deriv | 169 | −0.1338R | −1.16 |
| Germany 40 | Deriv | 161 | −0.1242R | −1.11 |
| US Tech 100 | Deriv | 159 | −0.0169R | −0.14 |

**You asked specifically about Nasdaq.** On a 4-hour breakout it does not
work — flat on Deriv over 159 trades, and its cash-index siblings are
negative on both feeds, US30 significantly so. Indices mean-revert intraday
far more than they trend on a 4-hour horizon. Gold is the opposite, which is
why it carries this system.

**GBPJPY** is also negative on both (−0.11R Deriv, −0.19R FundedNext). You
asked about it; this is not the system for it. The ORB walk-forward result
from this project's own history is the one that liked GBPJPY.

---

## Risk sizing

Gold alone, Deriv, full 4.9 years:

| Risk/trade | Final | Return | Max DD | Sharpe |
|---|---:|---:|---:|---:|
| 0.25% | $12,518 | +25.2% | 3.6% | +0.96 |
| **0.50%** | **$13,365** | **+33.7%** | **7.2%** | **+0.98** |
| 1.00% | $15,415 | +54.2% | 14.6% | +0.76 |
| 2.00% | $25,492 | +154.9% | 29.5% | +0.80 |

Sharpe peaks around 0.5% and falls as risk rises — the extra return at 2% is
bought entirely with drawdown. **0.5% is the setting.**

---

## Honest limits

* **Individually, no single market clears t = +2.** Gold is +1.47 (Deriv OOS)
  and +1.29 (FundedNext). The basket reaches +2.17 because diversification
  does what it is supposed to, not because any one market is proven.
* **The market list is partly contaminated.** The *configuration* was chosen
  in-sample and is clean. The *market set* was narrowed by looking at which
  markets were positive — and for gold, silver, EURUSD and GBPUSD that
  narrowing is backed by FundedNext, which was never used to choose anything.
  The Deriv-only synthetics (Boom 1000, Step, Jump 100) cannot be
  cross-validated and are **not** in the recommended set, even though adding
  them lifts the Deriv number to +101.8% and t +2.96.
* **17 months out of sample.** Long enough to be meaningful, short enough
  that a regime change would not yet show.
* **A 4H trend system is slow.** About 200 trades a year across four markets.
  Long flat stretches are the normal state, not a malfunction.

This is a real, modest edge — Sharpe ~1.5–1.9 at 0.5% risk. It is not
"extremely profitable", and anything claiming to be is doing what the four
video models do.

## Recommended next step

Run it through the app's own backtester at `max_hold_bars` matching a 4H
system, confirm the app reproduces these numbers (this project has been
caught before by research-to-app drift), then forward test on demo. Do not
skip to live on a 17-month out-of-sample window.

---

# Deep pass — 2026-10-10 (later)

`scripts/run_h4_deep.py` · `debug/h4_deep_{deriv,fundednext}.json`

Three questions: does session alternation exist, does break-and-retest work,
and can the surviving system be optimised further. **Real markets only** — no
synthetics — because the request was for an edge on real markets.

## 1. "London bought, so New York sells" — not true

The claim is falsifiable without trading it: does the sign of one session's
return predict the next session's, negatively? 50% is the null.

| Pair | Deriv n | Opposite | FundedNext n | Opposite |
|---|---:|---:|---:|---:|
| Asia → London | 11,564 | 50.13% | 2,797 | 49.34% |
| London → New York | 11,567 | 50.70% | 2,798 | **47.07%** |
| New York → Asia | 9,567 | 51.81% | 2,220 | 53.51% |

Over 23,000 session pairs the effect is **half a percent**, and on FundedNext
London → New York goes the *other way*: sessions continue 53% of the time,
the opposite of the claim. The strongest single market reaches z = ±2.5,
which is what 30 tests (10 markets × 3 pairs) produce from noise.

**There is nothing here to trade.** Half a percent does not pay a spread.

## 2. Break and retest with a wide confirming candle — close, not there

Break a 12-candle extreme, price returns to the level, and a candle whose
range is ≥1.2× ATR closes beyond its own midpoint in the break's direction.

| | Trades | Expectancy | t |
|---|---:|---:|---:|
| in-sample | 842 | +0.1300R | +1.38 |
| **out-of-sample** | 453 | **+0.1355R** | +0.86 |

$10,000 at 0.5% → $11,283 (+12.8%), max DD 19.0%.

Positive and *stable* across the split, which is more than the four marketed
models managed — but t +0.86 is not an edge yet.

**The useful finding is in the failures.** Every `trail=2.5` variant is
strongly negative (−0.15R to −0.19R, t −3 to −4) and every `trail=4.0`
variant is positive. The trailing distance matters more than the entry
pattern does. That is the same lesson as the fixed-target mistake, arriving
from a different direction.

## 3. Optimising the trend system — more search did not help

A wider grid (entry 4/6/9/12 candles, stop 2.0/2.5/3.0, trail 3/4/5, filter
none/vol) on real markets picked **4 candles, stop 2.0, trail 5.0**:

* in-sample +0.1936R, n=1,368, **t +2.80**, surrogate p95 +0.1089R
* out-of-sample +0.1396R, n=701, t +1.42

Better in-sample than the original pick; **not better out-of-sample**, and
with a worse drawdown (25.1% vs 10.1% at 0.5% risk). The optimisation found
nothing the first pass had missed, which is the reassuring outcome — it means
the original was not overfit.

**The recommendation does not change.**

## The recommendation, in dollars

`6-candle breakout · stop 2.5× ATR · chandelier trail 4.0× ATR · no target`
`XAUUSD, XAGUSD, EURUSD, GBPUSD · 0.5% risk · $10,000`

| | Deriv out-of-sample (1.4y) | FundedNext, never used to choose (1.3y) |
|---|---|---|
| Trades | 281 | 175 |
| Expectancy | +0.3023R | +0.3155R |
| t | **+2.17** | +1.56 |
| Win rate | 38.8% | 38.9% |
| Payoff | 2.63 | 2.59 |
| **Final balance** | **$15,975.48** | **$12,900.31** |
| **Profit** | **+$5,975.48 (+59.8%)** | **+$2,900.31 (+29.0%)** |
| Max drawdown | $1,669.13 (10.1%) | $1,087.15 (8.4%) |
| Profit factor | 1.68 | 1.56 |
| Average win | +$140.77 | +$121.80 |
| Average loss | −$52.73 | −$48.48 |
| Best trade | +$976.21 | +$821.84 |
| Worst trade | −$201.95 | −$127.72 |
| Best month | +$1,877.39 | +$1,151.30 |
| Worst month | −$419.51 | −$482.04 |
| Profitable months | 67% | 65% |
| Longest winning streak | 6 | 7 |
| **Longest losing streak** | **14** | **10** |
| Sharpe | +1.93 | +1.52 |
| Sortino | +7.90 | +4.26 |

At 1% risk, Deriv out-of-sample is **+$8,954.58 (+89.5%)** with a $3,886
(19.6%) drawdown.

### The streak question, answered honestly

**The longest losing streak is 14 trades.** That is not a defect to be
engineered away — it is what a 38.8% win rate means. A system that wins 39%
of the time and makes 2.6× its losses *will* lose ten or more in a row
regularly, and two thirds of its months are profitable, not nine tenths.

Anything offering a high win rate and a long winning streak is selling the
72%-win-rate sweep from the other study, which loses 98.9% of the account.
Those two properties trade against each other and you cannot have both.
