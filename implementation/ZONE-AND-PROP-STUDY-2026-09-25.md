# Round numbers, zones, and what actually passes a prop challenge

**2026-09-25** · M5 bars from the live Deriv terminal, 20 markets, 2021-10-01 → 2026-09-25
· scripts: `run_zone_study.py`, `run_zone_break.py`, `run_zone_money.py`,
`run_zone_robust.py`, `run_zone_report_stats.py`, `run_prop_challenge_sim.py`

---

## 0. The answer, before the evidence

| Question | Answer |
|---|---|
| Do real markets reverse at round numbers? | **No — the opposite.** Round levels are broken *more* often than ordinary prices. BTCUSD: 44.8% reversal vs 51.6% at control, z −10.6 on 6,997 events. |
| Is there any edge at round numbers? | **Yes, a small one, on the other side of the trade.** *Continuation* through a round level returns **+0.042R** per trade across three markets (11,143 trades, t +4.83) where the same trade at a non-round price returns **−0.02 to −0.07R**. |
| Do synthetic indices respect zones? | **No.** 23 cells, up to 25,063 events each: mean difference from control **+0.17 percentage points**, max \|z\| 1.92. Nothing. |
| Should it be a standalone strategy? | **No.** +0.042R at 1:1 is too thin to carry its own exits, and the edge only exists on a 1:1 geometry. |
| Should it be a reversal alert alongside other strategies? | **No.** That alert would be systematically wrong — fading a round number is *worse* than fading a random price (BTCUSD −0.103R vs control **+0.024R**). |
| Is it worth anything at all? | **As a direction bias, not as a level.** "Do not fade a round number; if anything, trade through it." Worth wiring as a *suppression* filter, not an entry trigger. |
| What passes a FundedNext challenge? | Expectancy **×** frequency ≈ **+20R/month** at **0.5–0.75% risk** → ~96% pass, <1% breach. Sobering control: **a pure coin-flip passes 33% of the time at 1% risk.** |

---

## 1. Why the control is the whole study

Price reverses at *some* level constantly — that is what price does. "It bounced at 1.1000" is not
evidence until you show it bounces there more than at 1.10237.

So every round level is measured against **control levels at the same spacing**, differing only in
roundness:

```
round grid     L = k · S                    S = 1.00, 0.50, 0.0100, 1000, …
control grid   L = (k + f) · S              f ∈ {0.17, 0.31, 0.43, 0.57, 0.69, 0.83}
```

The offsets deliberately avoid .25/.50/.75, which are themselves psychological. Same market, same
bars, same spacing, same event definition, same costs — only roundness differs.

**Event.** A clean first touch with a direction: the previous 12 bars are entirely on one side of
the level, and this bar reaches it. One candidate level per bar per direction, so chop around a
level cannot manufacture dozens of events.

**Outcome.** A race, which is how a trade actually resolves. From the level, over the next 48 bars
(4 hours), does price first retreat 0.25·S back the way it came (**reject**) or continue 0.25·S
through (**through**)? `reversal rate = rejects / (rejects + throughs)`; 50% is the null.

**Grids.** Chosen per market from the human ladder (…1, 2.5, 5, 10, 25, 50, 100, 250, 1000…),
keeping steps between 0.4× and 6× the market's own median daily range.

---

## 2. Question 1 — do real markets respect round numbers?

### 2.1 As reversal zones: no, and significantly so

Full history, M5, reversal rate at round vs control:

| Market | Grid | Events | Round | Control | Diff | z |
|---|---|---|---|---|---|---|
| EURUSD | 0.0025 | 7,187 | 49.7% | 50.9% | −1.2 pp | −1.83 |
| EURUSD | 0.0050 | 3,061 | 46.2% | 50.7% | **−4.6 pp** | **−4.71** |
| EURUSD | 0.0100 | 923 | 44.3% | 49.4% | **−5.1 pp** | **−2.85** |
| GBPJPY | 0.50 | 7,968 | 50.7% | 51.4% | −0.7 pp | −1.09 |
| GBPJPY | 1.00 | 3,523 | 51.4% | 51.0% | +0.4 pp | +0.48 |
| XAUUSD | 25 | 3,947 | 47.8% | 48.6% | −0.8 pp | −0.90 |
| XAUUSD | 50 | 1,509 | 45.1% | 48.9% | **−3.8 pp** | **−2.71** |
| XAUUSD | 100 | 488 | 41.6% | 49.7% | **−8.1 pp** | **−3.30** |
| BTCUSD | 1000 | 6,997 | 44.8% | 51.6% | **−6.8 pp** | **−10.56** |
| BTCUSD | 2500 | 1,937 | 47.8% | 49.4% | −1.6 pp | −1.32 |
| US Tech 100 | 250 | 1,727 | 47.4% | 48.1% | −0.8 pp | −0.60 |
| US Tech 100 | 500 | 569 | 48.3% | 48.6% | −0.2 pp | −0.10 |

Every significant cell has the **same sign, and it is negative**. Round numbers are not walls; they
are *thin spots*. The mechanism in the microstructure literature fits: stop orders cluster just
beyond round numbers, so arriving price is pulled through rather than repelled.

Note the control sits at 48–52% throughout — exactly where a null should sit. The instrument does
not have to be "efficient" for this to work: on Crash 1000 both arms read ~43%, because that
instrument's own downside spikes dominate *both* — which is precisely what a control is for.

### 2.2 As "big move" zones: no

The user's second framing — zones where markets make big moves — measured as the mean maximum
excursion from the level over the next 48 bars, in units of the grid step:

**39 cells, round 0.580 vs control 0.583 → −0.39%.** Individual cells scatter between −7.9% and
+6.4% with no pattern. Round numbers do not mark unusual movement in either direction.

### 2.3 As breakout triggers: no

The literature's second prediction is that price *trends rapidly once it crosses* a round number.
Tested directly (`run_zone_break.py`): enter on the close of the bar that breaks the level, target
0.5·S, stop 0.25·S, 48-bar horizon.

| Market | Grid | N | Round hit | Control hit | Diff |
|---|---|---|---|---|---|
| EURUSD | 0.0025 | 257 | 28.0% | 27.4% | +0.6 pp |
| GBPJPY | 0.50 | 258 | 25.2% | 28.1% | −2.9 pp |
| XAUUSD | 100 | 69 | 23.2% | 33.2% | −10.0 pp |
| BTCUSD | 1000 | 406 | 28.1% | 31.3% | −3.3 pp |
| US Tech 100 | 250 | 152 | 26.3% | 31.2% | −4.9 pp |

Null. And note every hit rate is ~28% against a 2:1 target that needs 33% to break even — this
geometry loses at round *and* control levels. Whatever the asymmetry is, it does not survive
entering after the break at a 2:1 payoff.

---

## 3. Where the edge actually is

The asymmetry is measured on a symmetric ±0.25·S race, so the trade that harvests it is the one
that matches that geometry exactly:

> **Enter at the level, in the direction price arrived. Stop 0.25·S back through the level.
> Target 0.25·S beyond it. 1:1. Flat within 4 hours.**

Costs are charged from each bar's own recorded spread plus a slippage allowance. Trades are
**non-overlapping** — one position at a time per market, which is both how you would trade it and
what stops the same market move being counted twice (overlap is the classic way this kind of study
flatters itself).

### 3.1 Results, five years, costs charged

| Market | Grid | Trades | Expectancy | Win | Total | t | Max DD | **Control** |
|---|---|---|---|---|---|---|---|---|
| EURUSD | 0.0050 | 3,766 | **+0.034R** | 52.5% | +126.9R | +2.19 | 52.4R | −0.039R |
| EURUSD | 0.0100 | 1,670 | **+0.055R** | 52.8% | +92.2R | +2.93 | 13.9R | −0.020R |
| XAUUSD | 50 | 2,159 | **+0.046R** | 53.2% | +99.8R | +2.43 | 54.2R | −0.012R |
| XAUUSD | 100 | 991 | **+0.062R** | 51.9% | +61.2R | +2.61 | 16.1R | −0.014R |
| BTCUSD | 1000 | 8,482 | **+0.038R** | 52.0% | +319.6R | +3.56 | 29.9R | −0.068R |
| USDJPY | 2.50 | 1,089 | +0.033R | 50.0% | +35.8R | +1.73 | 10.6R | +0.004R |
| GBPJPY | 1.00 | 4,336 | **−0.074R** | 47.9% | −321.2R | −5.03 | 324.5R | −0.079R |

The control column is the finding. The same trade at a non-round price **loses** in six of seven
cells. The differential — round minus control — is **+0.075R on EURUSD, +0.075R on XAUUSD,
+0.106R on BTCUSD**.

**GBPJPY does not respect levels.** Both arms lose heavily; it is simply a market where this
geometry is eaten by cost and noise. It is carried here as the counter-example.

### 3.2 Per-year stability

Expectancy in R / trade count:

| Market | Grid | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|
| EURUSD | 0.005 | +0.037/170 | +0.030/1061 | +0.063/712 | +0.043/543 | +0.018/823 | +0.011/457 |
| EURUSD | 0.01 | +0.072/82 | +0.060/450 | +0.096/321 | +0.013/218 | +0.033/390 | +0.062/209 |
| XAUUSD | 50 | +0.008/39 | −0.010/218 | +0.137/139 | +0.130/257 | +0.046/557 | +0.025/949 |
| XAUUSD | 100 | −0.000/27 | −0.052/93 | +0.070/64 | +0.060/111 | +0.098/244 | +0.069/452 |
| BTCUSD | 1000 | +0.031/595 | −0.006/1065 | +0.041/640 | +0.045/2188 | +0.061/2604 | +0.016/1390 |
| GBPJPY | 1 | −0.017/150 | −0.068/1012 | −0.071/812 | −0.027/955 | −0.142/886 | −0.079/521 |

EURUSD is positive in **all six years** on both grids. BTCUSD in five of six. XAUUSD in four of six
(and its two negative years are its two thinnest). GBPJPY is negative in all six — consistently, not
randomly.

The decay on EURUSD 0.005 (+0.037 → +0.011) is worth noting: the finer grid is being arbitraged or
eaten by cost. The coarser 0.01 grid is not.

### 3.3 How much slippage kills it

Expectancy as the slippage allowance rises (bar spread is always charged on top):

| Market | Grid | 0 pts | 1 pt | 3 pts | 5 pts | 8 pts |
|---|---|---|---|---|---|---|
| EURUSD | 0.005 | +0.042 | +0.034 | +0.018 | **+0.002** | −0.022 |
| EURUSD | 0.01 | +0.059 | +0.055 | +0.047 | +0.039 | +0.027 |
| XAUUSD | 50 | +0.047 | +0.046 | +0.045 | +0.043 | +0.041 |
| XAUUSD | 100 | +0.062 | +0.062 | +0.061 | +0.060 | +0.059 |
| BTCUSD | 1000 | +0.038 | +0.038 | +0.038 | +0.038 | +0.038 |
| USDJPY | 2.5 | +0.034 | +0.033 | +0.030 | +0.026 | +0.022 |

Robustness tracks R size. On the coarse grids R is 25 pips (EURUSD 0.01), $25 (XAUUSD 100) or 250
points (BTCUSD 1000), so execution noise is a rounding error. **EURUSD 0.005 dies at ~5 points and
should not be traded.**

---

## 4. Full statistics for the three-market book

EURUSD @ 0.01 + XAUUSD @ 100 + BTCUSD @ 1000, non-overlapping, costs charged, 2021-10 → 2026-09.

### 4.1 Headline

| | EURUSD 0.01 | XAUUSD 100 | BTCUSD 1000 | **BOOK** |
|---|---|---|---|---|
| Trades | 1,670 | 991 | 8,482 | **11,143** |
| Expectancy | +0.0552R | +0.0618R | +0.0377R | **+0.0424R** |
| Total | +92.2R | +61.2R | +319.6R | **+473.0R** |
| Win rate | 52.8% | 51.9% | 52.0% | **52.1%** |
| Profit factor | 1.177 | 1.214 | 1.082 | **1.100** |
| t-stat | +2.93 | +2.61 | +3.56 | **+4.83** |
| Sharpe / trade | 0.0716 | 0.0831 | 0.0387 | 0.0458 |
| Sortino / trade | 0.1479 | 0.1597 | 0.1970 | 0.1492 |
| Max drawdown | 13.9R | 16.1R | 29.9R | **38.0R** |
| Control expectancy | −0.0202R | −0.0136R | −0.0680R | — |

### 4.2 Streaks — trade level

| | EURUSD | XAUUSD | BTCUSD | BOOK |
|---|---|---|---|---|
| Longest winning streak | 10 | 8 | 10 | **12** |
| Longest losing streak | 11 | 7 | 13 | **13** |

At 52% win rate a 13-loss streak is entirely normal, and at 1% risk it is a 13% account drawdown
from streak alone. **This is the number that decides position size, not the expectancy.**

### 4.3 Monthly

| | EURUSD | XAUUSD | BTCUSD | BOOK |
|---|---|---|---|---|
| Months observed | 60 | 57 | 60 | 60 |
| Positive months | 62% | 53% | 78% | **78%** |
| Best month | +12.3R | +18.6R | +43.0R | +51.0R |
| Worst month | −7.6R | −9.4R | −18.7R | −28.2R |
| Longest winning-month streak | 5 | 5 | **18** | **18** |
| Longest losing-month streak | 4 | 6 | 2 | **3** |

### 4.4 Weekly

| | EURUSD | XAUUSD | BTCUSD | BOOK |
|---|---|---|---|---|
| Weeks observed | 251 | 179 | 263 | 264 |
| Positive weeks | 61% | 58% | 57% | **62%** |
| Best week | +6.5R | +7.7R | +21.3R | +21.7R |
| Worst week | −8.5R | −6.5R | −13.8R | −17.2R |
| Longest winning-week streak | 6 | 7 | 10 | **9** |
| Longest losing-week streak | 4 | 8 | 6 | **3** |

### 4.5 What this is worth in money

+473R over five years is **+94R/year**, ~7.9R/month across three markets. At 0.5% risk per trade
that is ~**+47% a year gross of compounding**, with a **38R (19%) peak-to-trough drawdown** and a
maximum 3-month losing run.

That is a real but *modest* edge with an uncomfortable drawdown-to-return ratio, and its
profit factor of 1.10 leaves very little margin: a 10% deterioration in execution erases it.

---

## 5. Question 2 — synthetic indices

**No zone effect, with overwhelming power.** 23 cells on Step, Jump 25/75/100, Crash 900/1000,
Boom 900/1000, Volatility 75/100, up to 25,063 events per cell:

> mean round-minus-control difference **+0.17 percentage points**, largest \|z\| **1.92**

Selected cells (full history):

| Market | Grid | Events | Round | Control | Diff | z |
|---|---|---|---|---|---|---|
| Jump 100 Index | 100 | 25,063 | 32.5% | 32.4% | +0.2 pp | +0.57 |
| Jump 100 Index | 250 | 10,602 | 38.5% | 38.6% | −0.1 pp | −0.28 |
| Volatility 100 Index | 100 | 14,900 | 41.5% | 41.4% | +0.1 pp | +0.31 |
| Boom 1000 Index | 100 | 9,328 | 42.0% | 42.4% | −0.4 pp | −0.71 |
| Crash 1000 Index | 50 | 7,848 | 43.3% | 43.3% | −0.1 pp | −0.13 |
| Volatility 75 Index | 10000 | 6,494 | 49.3% | 49.1% | +0.2 pp | +0.26 |

This is the expected answer — these are RNG-driven price paths with no participants to remember a
number — and it is also the **methodological proof**: the same test that finds z = −10.6 on BTCUSD
finds nothing here. The instrument is calibrated.

### 5.1 The trap to avoid

The 2026 money run looked spectacular on synthetics:

| Market | Grid | N | Expectancy | Win | t | **Control** |
|---|---|---|---|---|---|---|
| Boom 1000 Index | 100 | 1,338 | **+0.150R** | 57.8% | +5.56 | **+0.143R** |
| Crash 1000 Index | 50 | 1,062 | **+0.119R** | 56.5% | +3.94 | **+0.108R** |
| Boom 900 Index | 100 | 901 | +0.118R | 56.4% | +3.62 | +0.107R |

Without a control this reads as "Boom 1000 respects zones, +0.15R, t 5.6". With the control it is
obvious: **the control earns the same.** This is the instrument's built-in drift/spike asymmetry —
a continuation trade captures it at *any* price. Nothing to do with round numbers.

If you want that edge, trade the drift directly; do not dress it up as zones.

---

## 6. Question 3 — alongside other strategies

### 6.1 As a reversal alert: actively harmful

This was the proposed use — flag a zone, alert "price will likely reverse here". Measured, the
reversal trade at round levels (full history, costs charged):

| Market | Grid | N | Expectancy | t | **Control** |
|---|---|---|---|---|---|
| EURUSD | 0.005 | 3,562 | −0.086R | −5.44 | −0.005R |
| EURUSD | 0.01 | 1,823 | −0.067R | −3.77 | −0.003R |
| XAUUSD | 50 | 2,131 | −0.078R | −4.16 | −0.020R |
| XAUUSD | 100 | 1,070 | −0.072R | −3.23 | −0.002R |
| BTCUSD | 1000 | 7,531 | **−0.103R** | −9.27 | **+0.024R** |
| USDJPY | 0.50 | 5,947 | −0.084R | −6.57 | −0.016R |
| GBPJPY | 0.50 | 8,021 | −0.089R | −7.65 | −0.077R |

Negative on **every market and every grid**, and worse than the control on every one. On BTCUSD the
differential is 12.7 percentage points of expectancy in the wrong direction.

An alert saying "expect a reversal at 110,000" would be pointing at the place a reversal is *least*
likely. Sending that to Telegram would make decisions worse, not better.

### 6.2 What it is legitimately worth

Invert it. The finding "round levels get broken" has one honest use:

- **Suppression, not triggering.** When another strategy wants to *fade* into a round level, that
  trade is measurably worse than average — a filter can veto it or halve its size.
- **Target placement.** Do not park a take-profit just beyond a round number expecting a wall;
  price goes through. Conversely a stop placed just beyond one sits exactly where the cascade runs.
- **Direction tie-break.** Where an existing signal is ambiguous, the round-level bias favours
  continuation.

None of these is a strategy. All three are cheap to implement as a flag on an existing signal.

**Not tested:** whether that veto measurably improves ORB/IVW/APA in the app. That needs the app's
own signal stream tagged with distance-to-level and re-run — a day's work, worth doing only if you
want the filter shipped.

---

## 7. Should it be a standalone strategy with its own entries and exits?

The request was for a strategy that defines its own entry zone and exit zone, with risk management
optionally stepping aside.

**The data does not support it**, for three specific reasons:

1. **The edge only exists at 1:1.** It was measured on a symmetric race; the break test at 2:1 is
   null. A strategy that picks its own exits at "the next zone" is choosing an asymmetric payoff
   the edge does not survive.
2. **+0.042R cannot carry discretion.** At a profit factor of 1.10 there is no room for a worse
   exit rule. The 1:1 stop *is* the edge.
3. **It would drop the control.** Standalone zone logic on Boom 1000 would show +0.15R and be
   entirely instrument drift (§5.1). Without a control in the loop, the strategy will "find" edges
   that are not there.

If built anyway, it must be: fixed 1:1 at 0.25 × grid step, coarse grids only, EURUSD/XAUUSD/BTCUSD
only, one position at a time, 4-hour maximum hold — i.e. exactly the tested form, with risk
management still sizing it.

---

## 8. Prop firm challenges

### 8.1 The rules being solved (FundedNext Stellar 2-step, published 2026-09)

| Rule | Phase 1 | Phase 2 |
|---|---|---|
| Profit target | **10%** | 5% |
| Max daily loss | **5%** (resets 00:00 ET) | 5% |
| Max overall drawdown | **10% STATIC** — floor locked at 90% of start, never trails | same |
| Minimum trading days | 5 | 5 |
| Time limit | 45 days | 90 days |
| Consistency | no single day > 40% of total profit *(some sources state 50% per trade — verify against your contract)* | same |
| EAs / bots | allowed | allowed |
| News trading | restricted | restricted |
| Weekend holding | not allowed | not allowed |

Static (not trailing) drawdown is the single most important feature: it means early profit
*buys room*, so sequencing matters enormously.

### 8.2 The simulation

4,000 Monte Carlo challenges per cell. Trades bootstrapped from a win/loss profile, laid onto
trading days at the observed frequency, sized on the starting balance, walked barrier by barrier
with the daily cap, the static floor, the minimum-days rule, the 45-day clock and the consistency
rule all enforced.

**A coin-flip with no edge whatsoever:**

| Risk/trade | P(pass) | P(drawdown breach) | P(timeout) |
|---|---|---|---|
| 0.50% | 7.0% | 7.8% | 85.2% |
| 1.00% | **33.3%** | 36.6% | 30.1% |
| 1.50% | 35.8% | 49.6% | 14.6% |
| 2.00% | 33.3% | 60.0% | 6.7% |

**A third of no-edge traders pass at 1% risk.** Passing proves close to nothing on its own — and it
explains the business model. It also explains why a trader who passed once often fails the funded
account.

**The round-number edge (52.8% at 1:1, 1.3 trades/day, +1.5R per month):**

| Risk/trade | P(pass) | P(breach) | P(timeout) | Median days |
|---|---|---|---|---|
| 1.00% | 30.1% | 9.7% | 60.2% | 21 |
| 2.00% | 49.0% | 34.7% | 16.3% | 13 |

Worse than the coin-flip at 1% risk — because at 1.3 trades/day it cannot reach +10% inside 45 days
without raising risk, and raising risk adds breach probability faster than pass probability.
**The zone edge is not a prop-challenge strategy.**

**The app's ORB profile (≈35% at 1:3, 0.6 trades/day, +5R per month):**

| Risk/trade | P(pass) | P(breach) | P(timeout) | Median days |
|---|---|---|---|---|
| 0.75% | 41.0% | 1.6% | 57.4% | 13 |
| 1.00% | 52.7% | 5.3% | 42.0% | 11 |
| 1.50% | **56.5%** | 13.9% | 29.7% | 10 |
| 2.00% | 53.8% | 25.2% | 21.0% | 10 |

Respectable and safe, but timeout — not blow-up — is what fails it. Frequency is the binding
constraint.

**What actually passes (45% at 1:2, 3 trades/day, +22R per month):**

| Risk/trade | P(pass) | P(breach) | P(timeout) | Median days |
|---|---|---|---|---|
| 0.25% | 69.1% | 0.0% | 30.9% | 31 |
| **0.50%** | **96.0%** | **0.1%** | 3.9% | 17 |
| **0.75%** | **96.8%** | 0.7% | 2.5% | 14 |
| 1.00% | 95.3% | 3.3% | 1.4% | 12 |
| 2.00% | 81.9% | 17.2% | 0.8% | 10 |

### 8.3 What the simulation says to do

1. **Target ≈ +20R per month of expectancy × frequency.** That is the threshold where passing stops
   being luck. Below ~+10R/month the clock beats you; the answer is more trades, not more risk.
2. **Risk 0.5–0.75% per trade.** P(pass) peaks there and then *falls* as risk rises, while breach
   probability keeps climbing. Every profile in the table agrees.
3. **Frequency beats payoff.** 45% at 1:2 three times a day (+22R/mo) passes 96%; 35% at 1:3 once
   every two days (+5R/mo) passes 53% with the same edge quality. The 45-day clock is the enemy.
4. **Trade several uncorrelated markets** to buy frequency without raising per-trade risk — which is
   exactly what the per-slot book in this app is for.
5. **Watch the consistency rule.** One outsized day can invalidate a pass; cap daily risk so no
   single day can exceed 40% of the target.
6. **Do not use zones for this.** See above.

### 8.4 Honest framing

None of this is a guarantee, and no strategy "guarantees" a pass. The simulation assumes the
measured edge is real and stationary for 45 days. What it does show is the *shape* of the problem:
the challenge is a variance race against a clock, and the controllable variables are frequency and
risk, in that order.

---

## 9. What was not tested

- **Zone interaction with the app's live strategies** (§6.2) — no ORB/IVW/APA signal stream was
  tagged with distance-to-level.
- **Intraday session conditioning** — whether round levels matter more at London/NY open.
- **Level ages** — a level untouched for a month vs one touched yesterday.
- **Confluence with structure** — round numbers coinciding with a swing high/low or a prior day's
  extreme. This is the most likely place for a stronger effect and the obvious next study.
- **The FundedNext terminal itself** — rules were taken from published sources, not from the
  account. Contract specs and the exact consistency rule should be read off your own dashboard.
- **Tick data** — everything here is M5 bars; a 0.25·S race resolves inside a bar sometimes, and
  bar data cannot see which came first. This biases *both* arms equally, so the comparison holds,
  but absolute expectancies carry that caveat.

---

## 10. Reproduction

```bash
# 1. cache M5 bars from the live terminal (synthetics + real markets)
py -3.12 scripts/fetch_zone_bars.py
py -3.12 scripts/fetch_zone_bars.py --markets XAUUSD BTCUSD "US Tech 100" GBPJPY EURUSD USDJPY GBPUSD XAGUSD "Germany 40" "US SP 500"

# 2. does price react at round numbers more than at controls?
py -3.12 scripts/run_zone_study.py --since 2026-01-01 --tag ytd2026
py -3.12 scripts/run_zone_study.py --tag full5y

# 3. does it trend after breaking one?
py -3.12 scripts/run_zone_break.py --since 2026-01-01

# 4. what is it worth, with costs, in R?
py -3.12 scripts/run_zone_money.py --tag cont_full
py -3.12 scripts/run_zone_money.py --direction reversal --tag rev_full

# 5. overlap + per-year stability + cost sensitivity
py -3.12 scripts/run_zone_robust.py
py -3.12 scripts/run_zone_robust.py --slip-points 5

# 6. full trade statistics (monthly, weekly, streaks)
py -3.12 scripts/run_zone_report_stats.py

# 7. prop challenge probabilities
py -3.12 scripts/run_prop_challenge_sim.py
```

Outputs land in `data/zone_study/*.json`.

---

## 11. Recommendation

1. **Do not build the zone reversal strategy.** The premise is measurably false, and the alert
   version would be wrong in a way that costs money.
2. **Do not ship a standalone zone strategy.** +0.042R at 1:1 with PF 1.10 is too thin to own its
   exits, and it dies outside the tested geometry.
3. **Do consider a one-line suppression filter**: veto (or halve) any signal that fades into a
   round level on EURUSD / XAUUSD / BTCUSD. Cheap, grounded in z = −10.6 evidence, and it only ever
   removes trades.
4. **For the prop challenge, solve the right problem**: get expectancy × frequency to ~+20R/month
   by running several uncorrelated slots, then risk 0.5–0.75%. The book in this app already has the
   mechanism; the zone work does not contribute to it.
5. **Next study, if you want the zone thread continued**: round numbers *in confluence with*
   structure (prior day high/low, swing points, session opens). That is where the remaining
   hypothesis lives, and this harness now measures it in an afternoon.
