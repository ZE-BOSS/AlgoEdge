22 — Phase 3: Monte Carlo validation
====================================

**2026-09-04** · script `phase3_montecarlo.py`

Subject: the index intraday-reversal candidate from report 21 §3.
Verdict: **mechanism validated, significance not.** Three of four tests pass; the
one that fails is the one that asks how hard I searched.

---

## 0. Where this sits in the plan

Report 19 laid out Phases 0–5. Phase 1 closed the synthetic universe, so Phases 2
and 3 had nothing left to run on there. **Report 21 is Phase 2 relocated to live
markets**, and this is Phase 3 applied to its single surviving candidate.

Report 19 §2 defined Phase 3 as: *fit the generating process, then validate on
unlimited freshly simulated data.* That was written for instruments with a known
generator. Real markets have none, so the rule is kept and the generator replaced
by a **fitted model with a switch**:

```
r_t = φ·r_{t-1} + σ_t·ε_t ,   σ²_t = ω + a·u²_{t-1} + b·σ²_{t-1}
```

`φ` is the daily autocorrelation the strategy claims to harvest. Turning it off
gives a null world that is identical in every other respect — same volatility
clustering, same fat tails. That is as close to report 19's three-way test as a
real market allows.

---

## 1. Results

Observed, date-aligned, nine indices, 732 common days:
**+0.0563%/day, t = +2.30, Sharpe +1.35, +14.2%/yr.**

### Test 1 — IID bootstrap: does it harvest what it claims?

Resampling returns independently destroys autocorrelation while preserving the
exact return distribution. A strategy that claims to trade autocorrelation must
earn nothing here.

| | annualised |
|---|---:|
| null mean | **−4.44%** |
| null 95% range | [−10.6%, +2.3%] |
| observed | **+14.2%** |
| **p** | **0.0000** |

**Passes.** The edge is specifically autocorrelation, not some other artifact
riding along.

### Test 2 — Block bootstrap: an honest confidence interval

21-day blocks, preserving autocorrelation and clustering, so the interval does
not assume independent days.

| | value |
|---|---:|
| Sharpe | +1.35 |
| bootstrap 95% CI | **[+0.05, +2.37]** |
| P(Sharpe ≤ 0) | 0.014 |

**Passes, barely.** The lower bound clears zero by 0.05.

### Test 3 — Simulated markets, φ on versus φ off

Fitted φ across the nine: mean **−0.133**, range [−0.219, −0.078].

| | annualised | 95% range | P(>0) |
|---|---:|---:|---:|
| **φ = measured** | **+23.30%** | [+14.4, +32.1] | **1.00** |
| **φ = 0 (null)** | **−3.23%** | [−11.1, +5.1] | 0.27 |

**Passes, and this is report 19's three-way test working as designed:** the
strategy profits on freshly simulated data it has never seen when the parameter is
present, and earns nothing when it is switched off. The edge is structural *given
φ*.

One caveat on the magnitude: simulated +23.3% overstates the observed +14.2%,
because the model makes the tradeable intraday leg a perfectly scaled copy of the
daily return. In reality open-to-close and close-to-close are imperfectly
correlated, so less of the autocorrelation is capturable intraday than the model
assumes. The direction of the test is sound; its level is optimistic.

### Test 4 — Data-mining deflation: how hard did I search?

Across reports 21 and 22 I examined ~28 symbols against roughly three structural
hypotheses each — clustering, drift, autocorrelation.

| | value |
|---|---:|
| hypotheses effectively searched | ~84 |
| null expectation for max \|t\| | **2.64** (95th pct 3.42) |
| observed pooled t | **+2.30** |
| verdict | **INSIDE the search band** |

**Fails.** A t of 2.30 is below what the *best of 84 pure-noise tests* would be
expected to produce. On this evidence alone the finding cannot be separated from
the best result of a wide search.

---

## 2. Synthesis

The four tests are not in conflict; they answer different questions.

- Tests 1–3 establish the **mechanism**: the effect is autocorrelation, it is
  present in the data, and a strategy exploiting it behaves correctly on
  simulated markets where that parameter is switched on and off.
- Test 4 addresses the **evidence level**: 2.6 years of one market regime, found
  during a wide search, is not enough to claim it.

Both readings are correct simultaneously. The honest statement is that **this is a
well-specified hypothesis with a validated mechanism and insufficient evidence** —
which is a materially better position than anything the synthetic programme
produced, where the mechanism itself was proven absent.

## 3. A methodology note worth keeping

The first run of this script reported **t = +4.12 and Sharpe +2.37**. That was
wrong: it pooled the nine indices by array position rather than by calendar date.
The indices have different holiday calendars, so positional averaging pairs
different days together, which destroys their real 0.37 cross-correlation and
shrinks the portfolio variance. Correct alignment gives **t = +2.30**.

Two lessons, both general:

- an alignment bug flatters a portfolio result, because decorrelation always
  reduces variance
- it would have been invisible without an independent estimate to check against —
  the earlier `live_meanrev_validate.py` run, which aligned by date and gave
  +2.38/+2.52

## 4. What would settle it

Only one thing, and it is unchanged from report 21 §4: **more history.**

φ is estimated on 2.6 years because that is all Deriv's index CFDs have. Cash
index data with daily opens runs for decades and is freely available. Thirty years
would shrink the standard error by √(30/2.6) ≈ **3.4×**, taking t from 2.30 to
roughly **7.8 if the effect is real** — comfortably clear of the 3.42 search band —
or collapsing it toward zero if it is not.

That is a decisive experiment available for the cost of a data download, and it is
the only remaining step before this either becomes a strategy or gets discarded.

## 5. Phase status

| phase | status |
|---|---|
| 0 — data foundation | complete (reports 19–20) |
| 1 — generative characterisation | complete; synthetic universe closed (report 20) |
| 2 — hypothesis-driven edge search | complete, relocated to live markets (report 21) |
| **3 — Monte Carlo validation** | **complete; conditional pass (this report)** |
| 4 — walk-forward + strategy spec | **blocked** on the §4 data question |
| 5 — research paper | pending |

Phase 4 should not start until §4 is resolved. Writing a strategy spec against
t = 2.30 on 2.6 years would be exactly the mistake reports 16 and 18 made.
