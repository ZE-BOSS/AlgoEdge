20 — Generative characterisation (Phase 1)
==========================================

**2026-09-04** · scripts `phase1_bar_stability.py`, `phase1_geometry_ev.py`,
`phase1_markov.py`, `phase1_overlap_rescore.py`, `phase1_be_policy.py`,
`phase1_selection_noise.py`, `phase1_jump_law.py`, `phase1_dja_tick_replay.py`,
`phase1_random_control.py`, `synth_spread_audit.py`, `synth_quote_asymmetry.py`,
`synth_reconcile.py`

Data: MT5 H1/M15/D1 to inception (**5.5–7.7 years**, 12 symbols) and Deriv raw
ticks — **Boom 1000/500, Crash 1000/500 and Step Index complete at 31.53 M ticks
each, 99.9949% coverage** (157 M ticks; remaining 7 symbols still harvesting).

**Phase 1 is complete, and its answer is negative.** Every family in the
synthetic universe is closed to directional strategies, on measurement rather
than on failure to find something:

- all 11 symbols are **fair arithmetic martingales** (§2), confirmed at tick
  resolution to six decimal places (§3.1, §3.2)
- jump arrival is **memoryless with a flat hazard**, jump size is uncorrelated
  with everything observable (§3.2)
- Step Index — the last candidate — is a **perfect fair coin**, no memory to
  Markov order 10 at a precision of 0.009% on P(up) (§1.1)
- the one apparent counter-example in the project's own results does not survive
  an overlap-aware standard error (§5.1, §5.1b)

One thing of value came out of it anyway: the measurement toolkit the project was
missing (§5.1b) — overlap-aware significance, gap-aware fills, and a tick replay
harness. A second candidate, the break-even stop, looks harmful at every setting
tested but does not reach significance on 340 trades (§5.2).

---

## 1. These are generated processes, and the proof needs no distributional assumption

The sharpest available discriminator between a market and a generator is
**volatility clustering**. Every real market has strongly positive
autocorrelation in squared returns, persisting for weeks. A constant-volatility
generated process has none at any lag.

ACF(r²) on H1 closes, 5.5–7.7 years per symbol:

| symbol | ann. vol | ACF(r²) lag 1 | lag 6 | lag 24 | lag 168 |
|---|---:|---:|---:|---:|---:|
| Boom 1000 | 21.52% | −0.0032 | 0.0036 | −0.0014 | 0.0044 |
| Crash 1000 | 21.62% | −0.0006 | −0.0088 | 0.0027 | −0.0022 |
| Volatility 75 | 74.73% | 0.0009 | 0.0021 | 0.0011 | −0.0068 |
| Jump 100 | 132.05% | −0.0020 | 0.0082 | 0.0026 | 0.0017 |
| Range Break 200 | 20.03% | −0.0000 | −0.0000 | −0.0000 | −0.0000 |
| **Step Index** | 6.40% | **0.0183** | **0.0113** | **0.0110** | **0.0127** |

**Every value is within ±0.009 of zero** — except Step Index, which is small but
*systematically positive at every lag*.

**That exception is now closed, and it was not memory** — see §1.1. Step's step
size is a fixed **0.1 in price**, so percentage volatility rises mechanically as
the price level falls. Its price fell 22.6% over seven years, which predicts a
%-vol scaling of **1.293×**; the measured scaling was **1.309×**. A slow secular
trend in %-vol is exactly what produces a small positive ACF(r²) that is *flat
across all lags*, which is the shape observed. Nothing in the generator changed.

Volatility 75 annualises to **74.73%** against a 75% nameplate. The instruments
are what they say they are.

### 1.1 Step Index is a perfect fair coin — the last candidate, closed

Step was the only symbol with any memory signature and the only genuinely
discrete process, so it got the sharpest test available. On **31,530,797 ticks**:
one step size (0.1), **zero** flat ticks, so it is a clean Bernoulli sequence.

| test | result |
|---|---|
| P(up) | **0.499910 ± 0.000089**, z = −1.01 vs 0.5 |
| 95% CI | [0.499735, 0.500084] |
| Markov order 1–10 | **no memory at any order**, p = 0.24–0.91 |
| order 10 detail | χ² = **1041.0** on df = **1023** |
| run lengths 1–8 | match geometric, ratios 0.9950–1.0015 |

At order 10 the likelihood-ratio statistic lands within 2% of its null
expectation across 1,024 states. **There is no memory here at any order, and the
precision is 0.009% on P(up)** — this is not "no evidence found", it is a
measured fair coin.

The economics were never favourable anyway: Step's round trip costs **2.04
steps**, so even a 0.5% directional bias would need to be held ~204 steps just to
break even, and the measured bias is 0.009%.

**With this, every family in the universe is closed to directional strategies.**

## 2. No directional drift edge exists anywhere — and the naive test says otherwise

Testing log drift against zero flags Jump 100 as significant (−116%/yr, t = −2.06).
That is a trap. A process with zero expected *price* return has log drift of
exactly **−σ²/2** by Itô; at 132% vol that is −87%/yr of pure convexity, not edge.
The correct null is `log drift = −σ²/2`:

| symbol | log drift | −σ²/2 | excess | SE | t | verdict |
|---|---:|---:|---:|---:|---:|---|
| Boom 1000 | +5.29% | −2.32% | +7.61% | 7.92 | 0.96 | fair |
| Crash 1000 | −6.57% | −2.34% | −4.23% | 7.96 | −0.53 | fair |
| Volatility 75 | −10.85% | −27.93% | +17.08% | 26.98 | 0.63 | fair |
| **Jump 100** | −116.07% | **−87.18%** | −28.89% | 56.35 | **−0.51** | **fair** |
| Step Index | −3.75% | −0.21% | −3.54% | 2.38 | −1.49 | fair |

**All 11 symbols are fair arithmetic martingales.** Jump 100's apparent edge
disappears entirely. There is no drift to harvest in this universe.

## 3. Parameter stability — and the two exceptions

Annualised vol per calendar year. Boom/Crash/Jump/Volatility are constant to
better than ±0.5% across seven years (Volatility 75: 74.2–75.4% every year).

**Three symbols looked unstable. Only Range Break actually is:**

| symbol | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---:|---:|---:|---:|---:|---:|---:|
| **Range Break 100** | 25.7 | 44.2 | 46.7 | 19.6 | 16.4 | 19.8 | 18.8 |
| **Range Break 200** | 12.0 | 10.6 | 38.8 | 12.8 | 12.5 | 16.9 | 19.1 |
| Step Index | 5.7 | 6.0 | 6.5 | 6.5 | 6.9 | 6.8 | 7.2 |

Range Break swings **3×** and remains a genuine instability.

**Step's +31% is not instability and I was wrong to list it as such.** Its step is
a fixed 0.1 in price, so %-vol is mechanically `0.1 / price`. The price fell
10,208 → 7,897 (−22.6%) over the period, which predicts a 1.293× rise in %-vol;
the measured rise was 1.309×. The generator never changed. The same arithmetic
explains the ACF(r²) signature in §1 — a slow %-vol trend, not memory.

## 3.1 The jump law, measured on a full year of ticks (Boom 1000)

**31,531,484 ticks, 365.0 days, 99.9949% coverage.** This is the Class B
characterisation the plan called for, and it closes the timing question for good.

**The nameplate is literal.** 31,394 jumps → **one per 1,004.4 ticks**, 95% CI
**[993.3, 1,015.5]**, which contains 1000. "Boom 1000" means exactly what it says.
The split needs no arbitrary threshold: 98.08% of ticks grind down, 1.82% are
flat, and **every one of the 0.10% up-ticks is a spike** — the two populations sit
three orders of magnitude apart (grind at 1e-5.9, jumps at 1e-2.9) with an empty
valley between them.

**Arrival is Poisson, and the hazard is flat.** Having already waited *w* ticks,
the probability of a jump in the next 251:

| waited *w* | n | P(jump in next 251) |
|---:|---:|---:|
| 0 | 31,393 | 0.2210 |
| 251 | 24,455 | 0.2218 |
| 502 | 19,031 | 0.2258 |
| 1,004 | 11,474 | 0.2170 |
| 2,008 | 4,256 | 0.2176 |
| *exponential predicts* | | *0.2211* |

Inter-arrival CV **1.0038**, Fano factor **0.9293**. **Waiting buys nothing, over
a 2,000-tick range, on 31,394 events.** No entry rule based on elapsed time,
bar count, or "it's due" can work on these instruments — this is now settled
rather than suspected.

**Magnitude is independent of everything observable at entry:**

| against | correlation | SE |
|---|---:|---:|
| ticks since previous jump | −0.0059 | 0.0056 |
| price level | −0.0145 | — |
| hour of day | 5.4% spread in mean | — |

So neither *when* the next jump comes nor *how big* it is can be forecast.

**The generator is balanced to one part in a hundred:**

| component | per tick |
|---|---:|
| jump contribution | **+0.000099%** |
| grind contribution | **−0.000100%** |
| **net** | **−0.000001%** |

The grind is nearly deterministic — mean −0.000102% with sd 0.000068% — so
between spikes the path is close to a straight line. That short-horizon
predictability is real, and it is exactly cancelled by the spike risk. This is
§2's martingale result confirmed at tick resolution rather than inferred from
hourly bars.

### 3.2 The whole Boom/Crash family, and what the generator actually is

Repeating the measurement on all four, each on 31,530,797 ticks:

| symbol | jumps | measured rate | nameplate | ratio | CV | Fano | net drift/tick |
|---|---:|---:|---:|---:|---:|---:|---:|
| Boom 1000 | 31,394 | 1 per 1,004.4 | 1000 | **1.004** | 1.0038 | 0.9293 | −0.000001% |
| Boom 500 | 63,133 | 1 per 499.4 | 500 | **0.999** | 0.9993 | 1.0154 | −0.000000% |
| Crash 1000 | 31,229 | 1 per 1,009.7 | 1000 | **1.010** | 1.0010 | 0.9895 | −0.000000% |
| Crash 500 | 62,769 | 1 per 502.3 | 500 | **1.005** | 0.9977 | 0.9816 | −0.000000% |

**Every nameplate is literal to within 1%. All four are memoryless. All four are
fair to six decimal places.**

The jump *magnitude* distribution is the striking part — it is the **same
instrument to instrument**:

| symbol | mean | median | sd | p95 |
|---|---:|---:|---:|---:|
| Boom 1000 | 0.0999% | 0.0903% | 0.0682% | 0.2264% |
| Boom 500 | 0.1000% | 0.0901% | 0.0684% | 0.2264% |
| Crash 1000 | 0.1005% | 0.0907% | 0.0690% | 0.2286% |
| Crash 500 | 0.1004% | 0.0906% | 0.0687% | 0.2266% |

So the family is **two parameters: a direction and a rate N.** Spike size is
shared, drawn from one fixed distribution with a ~0.10% mean. The grind then
follows by arithmetic — it must be `−0.100%/N` per tick to keep the process fair,
and at N = 1000 that is −0.0001%, which is exactly the −0.000102% measured.

The generator is now fully specified, and there is nothing in it to trade:
arrival is memoryless, size is unpredictable, and the two halves cancel by
construction.

## 4. Gap slippage is the mechanism that makes these instruments fair

For a martingale, the optional stopping theorem gives a parameter-free
prediction: **gross expectancy is exactly zero for every stop/target pair**, so
net expectancy is exactly `−spread/stop`. The low win rate and the large payoff
must cancel.

Measured on 860,389 real BOOM1000 ticks, 0.5 × ATR stop, 5:1 target, short side
(the side Boom's up-spikes gap through):

| | value |
|---|---:|
| win rate | 67.3% |
| expectancy **if stops fill at the stop price** | **+1.0205 R** |
| expectancy **filling at the actual gapped price** | **+0.0369 R** |
| **slippage** | **−0.9836 R** |
| martingale prediction | −0.0370 R |

**The gap eats 96% of the apparent edge and restores the martingale exactly**
(z = +1.8). On the R_75 control — provably GBM — the same slippage term is only
**−0.02 R**, fifty times smaller.

The direction matters and is worth stating plainly: on Boom, **shorts** are
gapped through their stop (−0.98 R) while **longs** are gapped favourably through
their target (+0.47 R). Crash is the mirror: **longs** are the penalised side.

Control validation: R_75 at the same tight geometry returns a 33.7% win rate
against a 33.3% martingale prediction, and net expectancy matching theory to
z = +0.4. The measurement is sound.

### 4.1 Both harnesses under-charge this

`research/data/full_backtest.py:214` — the harness behind reports 16 and 18:

```python
if l[j] <= stop:
    r = -1.0; break
```

**A flat −1.0 R however far the bar gapped through the stop, with no slippage.**

`backend/backtester/engine.py:993` — the production engine is better but shares
the blind spot. It detects a gap only when the bar's **open** is already past the
stop (`open_p <= pos["stop_loss"]`). On a jump process the gap is *intrabar*: the
open is normal and the spike happens inside the bar, so the test never fires and
the exit fills at the stop price plus a fixed slippage in pips — a constant that
cannot represent a spike proportional to price.

Evidence from `algoedge.db`, DriftJumpAlpha on Crash 1000, SL exits (n = 149):

- booked `pnl_r` mean **−1.0246**
- actual adverse excursion `mae_r` mean **+1.2635**, max +2.58
- **83.9% of stopped trades went beyond 1.0 R against**

Charging every stop at its full adverse excursion moves DJA's expectancy
**+0.3597 → +0.2550 R**, an upper bound of **−0.105 R** on this artifact.
(`mae_r` is the trade-lifetime extreme while a real stop fills at the *first*
tick through the level, so the true correction is smaller. Tick replay settles it.)

**Severity scales inversely with stop width.** DJA's stop is **9.11 × M5 ATR**
(0.347% of price), so a median 0.106% spike moves only 0.30 R against it. That is
why the artifact is 0.1 R here and ~1 R in the 0.5 × ATR test above — and it is
why **report 08's recommendation of a 0.5 × ATR stop with a 5 R target on Crash
indices is dangerous**: at that geometry the unmodelled gap is worth about a full
R per trade and would fabricate a spectacular backtest that cannot exist live.

## 5. DriftJumpAlpha is statistically real and theoretically impossible

Two findings that have to be held together.

**It is not selection noise.** Across report 16's 285 cells (944 cell × R:R
combinations with n ≥ 30), DJA on Crash 1000 takes **ranks 1, 2, 3 and 4**:

| t | exp_r | n | R:R |
|---:|---:|---:|---:|
| **4.56** | 0.3856 | 514 | 3.0 |
| 4.39 | 0.4450 | 514 | 4.0 |
| 4.36 | 0.2861 | 514 | 2.0 |
| 3.97 | 0.4608 | 514 | 5.0 |

Under the null the largest t among 944 cells is typically 3.19 (95th pct 3.85,
largest in 500 simulated books 4.53). **P(chance) ≈ 0%**, and it is robust across
all four R:R settings, which a fluke would not be. The book's mean t is **−0.594**
— a fair game minus costs — so DJA stands out against a correctly-behaving
background.

**It also cannot be an edge.** §2 shows Crash 1000 is a fair martingale over 7.4
years, and optional stopping forbids *any* stopping-time strategy from positive
gross expectancy on a martingale. Entry cleverness cannot evade it; that is what
the theorem means.

Both cannot be true. **§5.1 resolves it: the significance is an artifact of
overlapping trades, and the edge does not survive an independent sample.**

### 5.1 RESOLVED — trade overlap, not edge

DJA's 514 trades (340 in the production run) are not independent draws. The
strategy runs concurrent positions and each trade lasts hours, so consecutive
trades share the same price path. A standard error computed as `sd/√n` across
them is badly optimistic, and every t-statistic built on it is inflated.

**The control quantifies exactly how much.** Random long entries on Crash 1000 at
DJA's own geometry (0.3472% stop, 5 R target, realistic tick fills):

| sampling | n | win rate | expectancy | t vs fair |
|---|---:|---:|---:|---:|
| overlapping | 3,998 | 21.56% | **+0.1577 R** | **+3.99** |
| **non-overlapping** | 685 | 19.85% | **+0.0499 R** | **+0.56** |

The *same strategy with no entry logic at all* reads as a 4-sigma edge when
trades overlap and as fair when they do not. Random shorts behave the same way
(−0.0785 R, t = −0.96). **The geometry is fair, exactly as optional stopping
predicts** — that prediction is now confirmed by measurement, not just asserted.

Applying the same correction to DJA's own trades, replayed tick-by-tick with
market-order stops and limit-order targets:

| | n | win rate | expectancy | SE | t vs zero |
|---|---:|---:|---:|---:|---:|
| all trades (overlapping) | 340 | 27.1% | +0.4850 R | 0.1493 | +3.25 |
| **independent subset** | **148** | 24.3% | **+0.3110 R** | **0.2188** | **+1.42** |

**t = +1.42. Not significant.** DriftJumpAlpha's Crash 1000 edge is not
statistically established once trade overlap is accounted for, and that is
consistent with §2 and §3.1: on a fair martingale with memoryless jumps, no edge
exists to find.

Stated honestly: this does not *prove* the expectancy is zero — 148 trades give a
wide interval and the point estimate is still positive. It says the evidence for
an edge is far weaker than reports 16 and 18 concluded, and that the theory and
the data no longer contradict each other.

**Report 18's walk-forward could not have caught this.** Overlap inflates both
halves of a split equally, so an artifact of this kind survives out-of-sample
testing untouched — which is why the OOS result looked like confirmation.

### 5.1b The whole book, re-scored — and it behaves exactly like chance

`algoedge.db` carries entry and exit timestamps for **7,143 trades across 87
cells**, so the correction can be applied to everything without re-running a
backtest. Three columns: the naive `sd/√n` the reports used, an independent
(non-overlapping) subset, and an autocorrelation-deflated effective sample size
(capped at *n*, so the correction can only ever remove information).

| cell | n | exp R | t naive | n_ind | t_ind | t_eff |
|---|---:|---:|---:|---:|---:|---:|
| DriftJumpAlpha \| Crash 1000 | 340 | +0.360 | +3.39 | 207 | **+2.39** | +3.09 |
| BiasIFVG \| XAUUSD | 56 | +0.671 | +2.02 | 44 | +2.26 | +2.02 |
| DriftJumpAlpha \| Crash 300 | 356 | +0.144 | +1.65 | 204 | +1.12 | +1.52 |
| VWAP \| Volatility 75 | 189 | +0.135 | +1.12 | 189 | +1.12 | +1.12 |

**Cells significant at t > 2: 2 of 87. Expected by chance alone: 2.0.** The book
contains no excess of significant results whatsoever. Mean t across all 87 cells
is **−1.43** — a fair game minus costs, which is precisely what §2 and §3.1
predict the instruments to be.

And the survivors do not survive selection. These cells were chosen by looking at
results, so the bar is not a single-cell t but the maximum over the whole search:

- best corrected cell: DriftJumpAlpha Crash 1000, **t_ind = +2.39**
- null expectation for the largest |t| among 87 cells: **2.65** (95th pct 3.43)
- **P(some cell reaches +2.39 by chance) = 77%** — inside the noise band

The same reasoning rescales report 16's headline. Its naive t of 4.56 came from
514 overlapping trades; applying the deflation measured here (3.39 → 2.39, a
factor of 0.70) gives ≈3.2, against a null maximum of 3.19 for its 944 cell × R:R
combinations. Right at the median of the null.

**Caveat worth stating.** The engine's own P&L gives the independent subset
t = +2.39, while the tick replay of the same signals as a plain bracket gives
+1.42. They differ because the engine's break-even and time-limit exits shorten
trades, which reduces overlap and changes the P&L distribution. Both land in the
same place — inside the noise — but neither is a proof of zero, and the point
estimates stay positive. The honest summary is that **the evidence for an edge is
much weaker than reports 16 and 18 concluded, and no longer contradicts theory.**

### 5.2 The break-even stop looks harmful, but 340 trades cannot prove it

An earlier draft of this section claimed the break-even stop costs 0.125 R per
trade. **That was overstated** — it compared two unpaired expectancies. The
correct test is paired, since every policy trades the same signals and the shared
price path cancels. Replaying all 340 signals on ticks with entry, stop, target
and fills held identical, changing only the exit rule:

| policy | expectancy | paired diff vs no-BE | SE | t | trades touched | effect on those |
|---|---:|---:|---:|---:|---:|---:|
| no BE (plain bracket) | **+0.4850 R** | — | — | — | — | — |
| BE at 0.5 R | +0.3311 R | **−0.1539** | 0.1085 | −1.42 | 58% | −0.267 R |
| BE at 1.0 R | +0.3652 R | −0.1198 | 0.0903 | −1.33 | 39% | −0.306 R |
| BE at 1.5 R | +0.4237 R | −0.0614 | 0.0736 | −0.83 | 27% | −0.224 R |
| BE at 2.0 R | +0.4742 R | −0.0108 | 0.0610 | −0.18 | 21% | −0.052 R |
| BE at 3.0 R | +0.4791 R | −0.0059 | 0.0460 | −0.13 | 12% | −0.049 R |

**Every trigger is negative, and the cost shrinks monotonically as the trigger
widens** — precisely the shape you would expect if the stop is being hit by grind
noise rather than by genuine reversals. The engine's own booked +0.3597 R sits
between the 0.5 R and 1.0 R rows, consistent with its configured trigger.

**But none of it reaches significance** (best t = −1.42). The honest position:
the break-even stop never once looks *helpful*, it looks harmful at every setting
tested, and the mechanism is corroborated by the excursion data — BE_SL exits
book +0.0531 R after reaching **+2.0807 R** of mean favourable excursion. That is
a strong prior, not a proven result. It deserves a properly powered test across
the whole book rather than a decision on 340 trades.

Candidates excluded by measurement along the way: look-ahead into the favourable
extreme (`pnl_r` vs `mfe_r` shows 0.026–0.685 for BE_SL and TIME_LIMIT — the
engine gives up profit, it does not steal it); entry timing (the DB entry price
sits −0.0014 R from the tick at its timestamp, exactly half a spread, so
alignment is correct); spread (0.00285 R against a 0.347% stop).

## 6. Corrections to report 19

- **Cost table (§0.3) was wrong and is replaced.** It read live spreads after
  midnight; Deriv widens synthetic spreads overnight by **6–20×** on Boom/Crash.
  On seven years of per-bar spread, **Boom/Crash are the cheapest family
  (0.012–0.026 R), not the dearest**, and Volatility is the most expensive
  (0.093–0.115 R). Boom 300, called "likely untradeable" at 0.320 R, is 0.013 R.
- **Deriv's API serves mid; MT5 bars are bid**, confirmed at frac 0.500 ± 0.05 on
  11 of 12 symbols. A backtest on MT5 bars charges nothing for crossing the
  spread. **Step Index is the exception at frac 0.000** — its API price *is* the
  bid, so longs pay the full spread and shorts pay none.
- Ticks and bars reconcile to a constant offset of exactly ½ spread, confirming
  both sources describe the same instrument.

## 7. What this means for the plan

This is the uncomfortable part, and it should be said plainly.

**There is no directional edge to find in Boom, Crash, Jump or Volatility.** Not
"we have not found one yet" — the processes are fair martingales (§2, confirmed
at tick resolution in §3.1), jump timing is memoryless with a flat hazard, jump
size is uncorrelated with everything observable, and optional stopping closes off
every stop/target geometry. The one apparent counter-example in the project's own
results does not survive an independent sample (§5.1).

That changes the plan:

- **Volatility indices: closed.** Exact GBM (§1), fair (§2), most expensive to
  trade (§6).
- **Boom: closed on the original thesis.** Mirroring DJA reproduces an artifact,
  not an edge. Boom is cheap and well-characterised, but §3.1 shows nothing to
  trade directionally.
- **Crash: the existing DJA result should not be sized up**, and report 16/18's
  numbers need re-scoring with an overlap-aware standard error before any of them
  are trusted.
- **Range Break: demoted.** Genuine parameter instability (§3) — vol swings 3×
  between years.
- **Step Index: closed.** It was the last candidate and it tested as a **perfect
  fair coin** — P(up) = 0.499910 ± 0.000089, no memory at any Markov order to 10
  (§1.1). Both reasons for promoting it turned out to be the same arithmetic
  artifact of a fixed absolute step size against a falling price.

**Every family is now closed.** There is no directional edge in this universe to
find, and that conclusion rests on measurement at a precision no amount of
further searching will improve on.

**What is worth building instead.** Two things came out of this that pay for
themselves regardless of strategy work:

1. **Fix the measurement.** Overlap-aware significance testing, gap-aware stop
   fills, and the tick replay harness now exist and are cheap to apply. Every
   past and future backtest number the project produces depends on them.
2. **§5.2: the break-even mechanism is destroying 0.125 R per trade** and giving
   up 2.08 R of mean favourable excursion. That is a real, immediate improvement
   available in the existing book, independent of everything above.

## 8. Next

1. ~~Markov-order test on Step Index~~ — **done, §1.1. Fair coin.**
2. ~~Re-score every backtested cell with an overlap-aware standard error~~ —
   **done, §5.1b. 2 of 87 cells significant, exactly the 2.0 chance predicts.**
3. ~~Jump law on the remaining Class B symbols~~ — **done, §3.2. All four
   nameplates literal, all memoryless, all fair; magnitude distribution shared.**
4. ~~Re-examine the break-even policy~~ — **done, §5.2. Harmful at every trigger
   tested, monotone in trigger width, but t = −1.42 at best. Needs a
   properly-powered test across the whole book, not a decision on 340 trades.**
5. Finish the tick harvest (~7 symbols remaining) for the archive.
