# The four marketed 4-hour models, tested

**Date:** 2026-10-10
**Scripts:** `scripts/run_crt_study.py`, `scripts/run_crt_addendum.py`
**Raw results:** `debug/crt_study_{deriv,fundednext}.json`, `debug/crt_addendum_*.json`

---

## Verdict first

**None of the four models is worth implementing.** Not one configuration, on
either feed, is both profitable and statistically distinguishable from noise.

The cleanest way to say it is with the multiple-testing arithmetic, because it
needs no judgement:

| | Measured | Expected from pure chance |
|---|---:|---:|
| Deriv market × config cells with expectancy > 0 **and** t > +2 | **5** of 446 | ~10 |
| Cells positive on **both** feeds | **52** of 289 (18%) | ~72 (25%) |

**The set of winning configurations is smaller than random noise would
produce.** Test 446 coin flips and more of them come out positive than these
rules did. That is not a marginal edge being eroded by costs — it is the
absence of an edge, plus the cost of trading.

One claim in the material is nevertheless **true**, and it is the interesting
part of this whole exercise. See "What is actually real" below.

---

## What was tested

| Model | Source | Rule as stated |
|---|---|---|
| **1** | Both "manipulation" videos | 4H candle sweeps the prior 4H low and closes bullish → long; stop at the low; enter the next candle. **V2** adds: must engulf the prior candle's body |
| **2** | CRT video 1 | CRT candle anchored 05:00 New York (your settings screenshot); whichever side is swept sets the bias; a lower-timeframe close back inside is the entry; stop below that candle; target the far side of the range |
| **3** | CRT video 2 | Model 2 gated by a higher-timeframe key level (previous week's high/low), a 15m CRT, a 15m order block and a 15m IFVG |

**Data.** Deriv MT5: 12 markets, 2021-10 → 2026-09 (5.0 years). FundedNext: 8
markets, 2025-05 → 2026-09 (1.4 years). 5-minute bars throughout, aggregated
to 15m and 4H.

**Not available on either feed: XRP.** Nasdaq is Deriv-only ("US Tech 100");
FundedNext carries US30 and SPX500 instead. Those absences are reported by the
script rather than silently dropped — an earlier run of this study quietly lost
three markets to a naming mismatch, which is exactly how a study ends up
describing a universe nobody chose.

### How it was priced

* **Entries are next-bar-open.** "Enter at the close of that candle" is not a
  fill that exists. This project has measured a level entry costing up to 60%
  of an edge.
* **Stops are checked before targets inside a bar.** When one 5-minute bar
  contains both, the loss is taken.
* **Costs** are the bar's own spread plus 2 points of slippage.
* **Stops tighter than 2× the spread are dropped**, because the live risk gate
  refuses them. Counting them produces a backtest the platform cannot
  reproduce.
* **Overlapping positions are removed** — one open trade per symbol, as a live
  slot would hold. This matters: on EURUSD at 1:2 the raw list is 2,433 trades
  at −0.059R and the non-overlapping one is 1,472 at **−0.089R**. The overlap
  was flattering the result by a third.
* **Sharpe and Sortino are annualised from monthly returns** on the $10,000
  equity curve, not per trade. Per-trade Sharpe is the number that flatters a
  strategy taking thousands of tiny trades.

---

## Model 1 — the 4H manipulation sweep

### Deriv, 12 markets, 5 years, $10,000 at 1% risk

| Target | Trades | Win rate | Expectancy | t | PF | Return | Max DD | Sharpe |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| range | 20,813 | **72.1%** | −0.0213R | **−4.57** | 0.91 | −98.9% | 98.9% | −1.73 |
| 1:1 | 15,108 | 50.0% | −0.0193R | −2.51 | 0.95 | −95.2% | 95.4% | — |
| 1:1.5 | 14,047 | 42.6% | −0.0175R | −1.86 | 0.97 | −95.6% | 97.0% | — |
| 1:2 | 13,527 | 39.0% | −0.0138R | −1.28 | 0.96 | −94.8% | 96.6% | — |
| 1:3 | 13,028 | 35.5% | −0.0125R | −1.00 | 0.97 | −92.5% | 97.5% | −0.29 |
| 1:5 | 12,701 | 33.8% | −0.0052R | −0.36 | 0.97 | −95.4% | 98.2% | — |

With video 2's engulf condition: 45% fewer trades, and the range target gets
*worse* (−0.0237R, t −4.57). Best cell is 1:5 at **+0.0056R, t +0.35** — noise.

### That 72% win rate is real, and it is the trap

The range target genuinely wins 72% of the time. It also **loses 98.9% of the
account.** The target is close, the stop is far, and the 28% of trades that
lose are each worth several winners. A high win rate bought with an
unfavourable payoff is not an edge — it is a different way of being wrong, and
it is the most persuasive number in the whole marketing pitch.

t = −4.57 means this is **significantly negative**, not merely unprofitable.

### FundedNext, 8 markets, 1.4 years — and why it does not rescue it

| Target | Trades | Win | Expectancy | t | Return | Sharpe |
|---|---:|---:|---:|---:|---:|---:|
| 1:3 | 2,899 | 36.3% | **+0.0227R** | +0.84 | +36.2% | +0.65 |
| 1:5 | 2,830 | 34.0% | **+0.0351R** | +1.11 | +73.2% | — |
| v2 1:3 | 1,875 | 38.1% | **+0.0308R** | +0.97 | +46.4% | +0.67 |

The high-R variants are positive here. They are also **not significant** (t
around 1.0), and the same configurations are **negative on Deriv over 5
years**. The two feeds disagree, on a 1.4-year sample versus a 5-year one.
This project has been caught by exactly that before — a book showing t +3.76
on Deriv and −8.2% on FundedNext. A result that depends on which broker's bars
you look at is not a result.

---

## Model 2 — 4H CRT with a lower-timeframe entry

### The 82% win rate claim

**It is not reachable at any target, and the closest you get loses everything.**

| Target | Win rate | Expectancy | PF | Return on $10k |
|---|---:|---:|---:|---:|
| 1:0.25 | **72.5%** | −0.1293R | 0.52 | −100.0% |
| 1:0.5 | 63.0% | −0.1091R | 0.74 | −99.9% |
| 1:1 | 48.9% | −0.0797R | 0.88 | −99.9% |
| 1:2 | 35.6% | −0.0458R | 0.94 | −99.4% |
| 1:5 | 26.0% | −0.0092R | 0.99 | −88.9% |

A win rate is not a free parameter — the target sets it. Squeezing the target
down to a quarter of the stop gets you to 72.5%, still short of 82%, at a
**profit factor of 0.52**: for every dollar won, two are lost.

### Every anchor and confirmation timeframe

| Feed | Anchor | Confirm | Win | Expectancy | t | Return | Sharpe |
|---|---|---|---:|---:|---:|---:|---:|
| Deriv | 01:00 NY | 5m | 15.3% | −0.1698R | −5.50 | −100% | — |
| Deriv | **05:00 NY** | 5m | 21.5% | −0.0063R | −0.20 | −98.5% | — |
| Deriv | **05:00 NY** | 15m | 31.7% | −0.0311R | −1.60 | −99.3% | −0.51 |
| Deriv | 09:00 NY | 15m | 26.0% | −0.0733R | −1.16 | −99.9% | — |
| FundedNext | 05:00 NY | 15m | 26.2% | −0.1105R | −2.48 | −96.0% | −1.78 |

The 05:00 anchor from your settings screenshot is the *least bad* of the
three, which is the only thing that can be said for it. **Claimed 82%,
measured 15–32%.** Every configuration reaches a ~100% drawdown.

### The "Purge Duration: 90" setting

Tested as a time filter — a sweep only counts within N minutes of the CRT
candle's close:

| Window | Trades | Win | Expectancy | t |
|---|---:|---:|---:|---:|
| 30 min | 3,978 | 30.5% | −0.0277R | −0.84 |
| 60 min | 5,894 | 31.6% | −0.0182R | −0.69 |
| **90 min** | 6,852 | 31.6% | **−0.0153R** | −0.63 |
| 240 min | 8,012 | 31.0% | −0.0294R | −1.32 |
| no limit | 8,446 | 30.7% | −0.0379R | −1.75 |

90 minutes is genuinely the best of the six on Deriv — credit where due, the
setting is not arbitrary. It reduces the loss by 60% and does not come close
to removing it. On FundedNext the same filter makes things *worse*
(−0.1233R vs −0.1105R).

---

## Model 3 — the three-step confluence stack

Deriv, cumulative filters, range target:

| Stack | Trades | Win | Expectancy | t | Return |
|---|---:|---:|---:|---:|---:|
| base, no filters | 10,748 | 30.9% | **+0.0129R** | +0.63 | −60.1% |
| + key level | 2,788 | 29.0% | **−0.0464R** | −1.14 | −86.9% |
| + 15m CRT | 806 | 37.2% | −0.0347R | −0.57 | −30.5% |
| + 15m FVG | 134 | 52.2% | −0.0627R | −1.12 | −7.6% |
| + 15m OB | 134 | 52.2% | −0.0627R | −1.12 | −7.6% |

Read that column downwards.

* **"Step one, the higher timeframe key level" is the single most harmful
  filter in the stack.** It takes the base from +0.0129R to −0.0464R and
  throws away three quarters of the trades to do it.
* **The 15m order block does nothing at all** — identical 134 trades, so it
  never once disqualified a setup the other three allowed. It is decoration.
* The full stack leaves **134 trades in five years across twelve markets** —
  about two a month — and still loses money.

Each filter alone, to see its own sign:

| Filter alone | Trades | Expectancy | t |
|---|---:|---:|---:|
| key level | 2,788 | −0.0464R | −1.14 |
| 15m CRT | 2,662 | +0.0110R | +0.33 |
| 15m FVG | 421 | −0.0247R | −0.77 |
| 15m OB | 8,767 | +0.0096R | +0.42 |

Nothing clears t = +1. On FundedNext the base itself is **−0.1098R, t −2.42**:
significantly negative before any filter is applied.

### About the 11.25 R example

That is one trade, not a statistic. A filter stack that admits two setups a
month will always contain a spectacular-looking instance — that is what
selecting 134 trades out of 10,748 does. The distribution behind it loses
money.

---

## What is actually real

**"This is hopefully going to give you the lowest point of the day" is TRUE,
and it is the strongest effect in the study.**

| Feed | Variant | Signal candle held the day's extreme | Base rate | Lift |
|---|---|---:|---:|---:|
| Deriv | v1 plain | **46.2%** (8,748 / 18,918) | 26.1% | **1.77×** |
| Deriv | v2 engulf | 44.3% | 26.1% | 1.70× |
| FundedNext | v1 plain | **41.1%** (2,089 / 5,079) | 16.8% | **2.45×** |
| FundedNext | v2 engulf | 39.7% | 16.8% | 2.37× |

A 4-hour candle that sweeps the previous candle's low and closes back up marks
the day's low **nearly twice as often as chance**, on both feeds, across 24,000
signals. The observation is sound.

**The trade built on it is not.** Knowing where the low probably is does not
pay when your stop sits at that low (so the 54% of the time it is not the low,
you lose a full R) and your target is the top of the same candle (so you win
small). The edge is in the *observation*; the videos spend it entirely on a
bad entry-stop-target construction.

That is the one genuinely useful thing here, and it is worth keeping as a
**feature**, not a strategy — "did this 4H candle sweep and reverse" is a
legitimate input to something else.

---

## The only cells worth a second look

Of 289 market × config cells shared by both feeds, 52 are positive on both.
Chance alone would give ~72. Still, the positives cluster rather than scatter:

| Market | Config | Deriv (5y) | FundedNext (1.4y) |
|---|---|---|---|
| **BTCUSD** | sweep v2-engulf 1:3 | +0.0885R, t +2.15 | +0.2051R, t +2.06 |
| **BTCUSD** | sweep v2-engulf 1:5 | +0.1237R, t +2.57 | +0.1781R, t +1.61 |
| **XAUUSD** | sweep v1-plain 1:1.5 | +0.0713R, t +2.39 | +0.0730R, t +1.27 |
| **XAUUSD** | sweep v2-engulf 1:3 | +0.0713R, t +1.52 | +0.1628R, t +1.63 |

BTC and gold on the sweep at high R:R are the only things that are positive on
both feeds with t > 2 on at least one. Both are high-volatility instruments
where a 1:3 target is reachable within a day — which is a plausible mechanism
rather than a coincidence.

**This is a hypothesis, not a finding.** It was selected from 446 cells, and
the honest test is the one this project already uses: walk it forward on a
period neither feed's sample covers, and require it to clear the surrogate
p95. Nothing here justifies putting it in a slot.

---

## Recommendation

**Do not implement any of the four models.** Specifically:

1. The **4H manipulation sweep** is significantly negative at the target the
   videos imply (t −4.57) and indistinguishable from zero everywhere else.
2. The **4H CRT with LTF entry** is the worst of the three — ~100% drawdown on
   every anchor, every timeframe, both feeds. The 82% win-rate claim is false;
   the measured range is 15–32%, and 82% is not reachable at any target.
3. The **confluence stack** fails, and its own first step is its most harmful
   component.
4. **Keep the sweep-and-reverse condition as a feature.** The "lowest point of
   the day" effect is real at 1.7–2.5× the base rate on both feeds. It belongs
   in the feature set the agents can condition on, where something else can
   decide what to do about it — not as a strategy with a stop at the swept low.

The existing book already contains better-evidenced edges than anything here:
the trailing-stop Donchian system measured t +2.50, and this study's best
pooled cell is t +0.35.
