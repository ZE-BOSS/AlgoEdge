24 — Where a statistical edge exists, and where it does not
===========================================================

**Phase 5 · final report · 2026-09-04**

Scope: Deriv's 167 synthetic indices and 28 live-market instruments.
Data: **330 million ticks**, 166,076 index-days, and 5.5–56.7 years of bars.
Result: **no strategy is recommended, because none earned it.**

---

## Abstract

The programme set out to find, for each synthetic index, a statistical edge of the
kind `DriftJumpAlpha` appeared to have on Crash 1000 — and to do it without
overfitting.

It found instead that **the synthetic universe is closed by construction**. Every
instrument is a fair martingale, jump arrival is memoryless with a flat hazard,
jump size is unpredictable, and Step Index is a fair coin to a precision of 0.009%.
These are not failures to find something; they are measurements at a resolution
further searching cannot improve.

It also found that **`DriftJumpAlpha`'s edge does not survive an overlap-aware
standard error**, which resolves a contradiction the project had been building on
for months.

Turning to live markets, the same three tests found volatility clustering
(strongly present), a risk premium (unmeasurable on the available history, and
absent in FX), and one directional candidate — daily reversal in equity indices.
That candidate passed four validation stages and then **died on 57 years of data**,
where the effect it relies on flips sign by decade.

The durable outputs are a complete specification of the synthetic generators, a
map of where live-market structure exists, and a measurement toolkit that caught
six errors — four of them in this work.

---

## 1. The question, and why it was worth asking

The starting position was that live markets are too manipulated to trade
reliably, while synthetic indices are transparent: the rules are published, the
process is generated, and there is no order flow to be front-run. If an edge
exists there, it should be findable and durable.

That reasoning is sound. It is also, as it turns out, exactly backwards — the same
transparency that makes synthetics easy to analyse is what guarantees they contain
nothing to find. **A process you can fully specify is a process someone specified
to be fair.**

## 2. Method

Three measurements, applied identically to both universes.

**Volatility clustering — ACF(r²).** The sharpest available discriminator between
a generated process and a market, requiring no distributional assumption. Real
markets have strongly positive, persistent autocorrelation in squared returns; a
constant-volatility generator has none.

**Risk premium — Itô-corrected drift.** A process with zero expected *price*
return has log drift of exactly −σ²/2. Testing against zero instead of against
−σ²/2 produces false positives on high-volatility instruments and false negatives
on convex ones. Both traps occurred in this work (§7).

**Return autocorrelation.** What a directional entry rule actually needs, and the
one most likely to have been arbitraged away.

Two disciplines were fixed in advance (report 19 §2):

- **Only structural hypotheses.** A hypothesis had to be statable as a property of
  the generating process — *"Crash 1000 jumps once per N ticks"* — and verifiable
  to several significant figures. *"RSI < 30 works"* was not admissible.
- **Validate on simulated data.** Fit the generator, then test on freshly drawn
  years the strategy cannot have been fitted to. Anything working on history but
  not on simulation is discarded without appeal.

## 3. The synthetic universe, fully specified

### 3.1 Boom and Crash are a two-parameter family

Measured on 31,530,797 ticks per symbol, 365 days, 99.9949% coverage:

| symbol | jumps | measured rate | nameplate | ratio | CV | net drift/tick |
|---|---:|---:|---:|---:|---:|---:|
| Boom 1000 | 31,394 | 1 per 1,004.4 | 1000 | 1.004 | 1.0038 | −0.000001% |
| Boom 500 | 63,133 | 1 per 499.4 | 500 | 0.999 | 0.9993 | −0.000000% |
| Crash 1000 | 31,229 | 1 per 1,009.7 | 1000 | 1.010 | 1.0010 | −0.000000% |
| Crash 500 | 62,769 | 1 per 502.3 | 500 | 1.005 | 0.9977 | −0.000000% |

**Every nameplate is literal to within 1%.** The jump magnitude distribution is
*identical across all four* — mean 0.0999–0.1005%, p95 0.2264–0.2286%. So the
family is **a direction and a rate N**, with one shared spike-size distribution.
The grind then follows by arithmetic: it must be −0.100%/N per tick to keep the
process fair, and at N = 1000 that is −0.0001%, exactly the −0.000102% measured.

**Arrival is memoryless.** Having already waited *w* ticks, P(jump in next 251):

| w = 0 | 251 | 502 | 1,004 | 2,008 | exponential predicts |
|---:|---:|---:|---:|---:|---:|
| 0.2210 | 0.2218 | 0.2258 | 0.2170 | 0.2176 | 0.2211 |

Flat across a 2,000-tick range on 31,394 events. **No entry rule based on elapsed
time, bar count, or "it is due" can work.** Magnitude is likewise uncorrelated
with time since last jump (−0.0059, SE 0.0056), price level, or hour of day.

### 3.2 Volatility indices are exact Brownian motion

Volatility 75 returns skew −0.02 and kurtosis **3.000** — Gaussian to the decimal
— and annualises to **74.73%** against its 75% nameplate, holding between 74.2%
and 75.4% in every one of seven years.

### 3.3 Step Index is a fair coin

31,530,797 ticks, one step size (0.1), zero flat ticks:

| test | result |
|---|---|
| P(up) | **0.499910 ± 0.000089** (z = −1.01) |
| Markov order 1–10 | **no memory at any order**, p = 0.24–0.91 |
| order 10 | χ² = 1041.0 on df = 1023 |
| run lengths 1–8 | geometric, ratios 0.9950–1.0015 |

### 3.4 Everything is fair

All 11 symbols pass an Itô-corrected martingale test over 5.5–7.7 years. Jump 100
appears to have a −116%/yr drift and does not: −87%/yr of that is pure convexity
at 132% volatility, and the excess is t = −0.51.

Volatility clustering across the whole universe: **ACF(r²) within ±0.009 of zero
at every lag**, Ljung–Box Q(20) of 11–23 against a null threshold of 45.

## 4. The DriftJumpAlpha contradiction, resolved

Three things were each independently supported and could not all be true:

1. Crash jump arrival is memoryless (§3.1)
2. The process is a fair martingale (§3.4)
3. `DriftJumpAlpha` earned **+0.2039 R out of sample** on Crash 1000 (report 18)

If (1) and (2) hold, optional stopping forbids *any* stop/target strategy from
positive gross expectancy. Entry cleverness cannot evade a theorem.

**The resolution is trade overlap.** DJA runs concurrent positions lasting hours,
so consecutive trades share price path and `sd/√n` is badly optimistic. The
control is unambiguous — *random* long entries at DJA's exact geometry:

| sampling | n | expectancy | t vs fair |
|---|---:|---:|---:|
| overlapping | 3,998 | +0.1577 R | **+3.99** |
| non-overlapping | 685 | +0.0499 R | **+0.56** |

**A strategy with no entry logic at all reads as a 4-sigma edge when trades
overlap, and as fair when they do not.** Applying the correction to DJA's own
trades, replayed tick-by-tick: **148 independent trades, +0.3110 R, t = +1.42.**

Re-scoring the whole book the same way: **87 cells, 7,143 trades, 2 significant at
t > 2 — exactly the 2.0 chance predicts.** Mean t across all cells is −1.43, a
fair game minus costs. The best survivor sits inside the best-of-87 noise band at
P = 77%.

**Report 18's walk-forward could not have caught this**, because overlap inflates
both halves of a split equally. That is why the out-of-sample result looked like
confirmation.

### 4.1 A second, independent problem: gap fills

On a jump process the gap is *intrabar*. `full_backtest.py:214` books a flat
−1.0 R however deep the gap; `engine.py:993` detects gaps only at the bar **open**,
so a spike inside the bar never triggers it.

Measured on real ticks at a 0.5 × ATR stop: expectancy is **+1.0205 R** if stops
fill at the stop price and **+0.0369 R** filling where the gap actually lands —
**−0.98 R of unmodelled slippage**, restoring the martingale exactly. On a GBM
control the same term is −0.02 R, fifty times smaller.

At DJA's much wider 9.11 × ATR stop the artifact is worth at most −0.105 R. But it
scales inversely with stop width, which makes **report 08's recommendation of a
0.5 × ATR stop on Crash indices actively dangerous** — at that geometry it would
fabricate about a full R per trade.

## 5. Live markets: where structure actually exists

| test | synthetics | live markets |
|---|---|---|
| **volatility clustering** LB Q(20) | 11 – 23 | **38 – 3,788** |
| **risk premium** | absent by construction | unmeasurable here; **absent in FX** over 16 yrs |
| **return autocorrelation** | absent | present in equity indices, **unstable** (§6) |

**Clustering is the clean line between the universes**, and it is the one durable
finding. It is not a directional edge, so it cannot be arbitraged away — it
describes risk, not return. It is what makes volatility targeting and position
sizing work at all, and on synthetics no such tool could ever have added value.

The FX null is genuine: 16 years, every major indistinguishable from fair. The
equity premium is a **data limitation** — Deriv's index CFDs begin in January 2024,
and 2.6 years cannot resolve it.

## 6. The one candidate, and its death

Daily reversal in equity indices: all eight negative at lag 1, Netherlands 25 at
−0.219 (t = 6.1). Unlike anything synthetic, **costs were not the obstacle** — the
S&P needs |ρ| > 0.006 to break even and measured −0.174, twenty-nine times over.

It survived four stages:

| stage | result |
|---|---|
| correct pooling (date-aligned, 0.37 correlation) | t = +2.30, Sharpe 1.35 |
| artifact test (execute off the close) | **passed and improved** — intraday leg carries it |
| IID bootstrap (autocorrelation destroyed) | p = 0.0000 — it harvests what it claims |
| simulated markets, φ on vs off | +23.3%/yr vs −3.2%/yr, P(>0) = 1.00 |

And failed the fifth: **data-mining deflation.** Across ~84 hypotheses searched,
the null expects a maximum |t| of 2.64. Observed: 2.30 — *inside* the band.

Phase 4 gated on that warning and went to get 57 years of cash-index data. It
killed the candidate:

- **The strategy loses on the S&P 500 over 56.7 years: −9.2%/yr, t = −4.29.**
  Negative on 12 of 18 indices.
- **The sign flips by decade.** ACF(1) was **+0.156** in the 1970s, +0.083 in the
  1980s, +0.062 in the 1990s, and only −0.068 in 2020–2026. Reversal loses in four
  decades of six.
- **Walk-forward returns nothing.** Six of seven cutoffs under 3%/yr; the most
  recent, trained on 135,952 days, is **−1.9%/yr**.

The 2.6-year Deriv window sat entirely inside the single recent regime where
reversal happens to hold, amplified by CFD-specific bar construction. Had it
shipped on that evidence — 9/9 indices positive, Sharpe 1.47, artifact test passed
— it would have been a **losing strategy in the most recent out-of-sample period.**

## 7. Six errors the method caught

Recorded because the method's value is mostly in this list. Four are mine.

1. **Live spreads read after midnight** inflated Boom/Crash costs 6–20×, inverting
   the cost ranking. Caught by comparing against seven years of per-bar spread.
   *(Report 08 had warned about exactly this and I repeated it.)*
2. **A tick harvester that counted errors instead of retrying** wrote files
   claiming "340.6d covered" holding 2M of 29M ticks. Caught by a coverage
   assertion, added after the fact.
3. **Positional instead of date-aligned pooling** read t = +4.12 where the truth
   was +2.30 — decorrelation always shrinks portfolio variance and flatters a
   result. Caught only because an earlier date-aligned run existed to compare to.
4. **The Itô correction applied at 200% volatility** turned XRP's actual 42.2%
   CAGR into a headline "+242%/yr excess". The same correction *hid* a false
   positive on Jump 100. One tool, both failure directions.
5. **Overlapping trades** (§4) — the project's central result.
6. **Intrabar gap fills** (§4.1) — present in both backtest harnesses.

## 8. Recommendations

**Do not build strategies on Deriv synthetics.** Not "we did not find one" —
the processes are specified to be fair, and that is now measured rather than
assumed. Any backtest showing otherwise is measuring the harness.

**Fix the measurement before trusting any further backtest.** Specifically:
overlap-aware standard errors, gap-aware stop fills, date-aligned pooling, and a
data-mining deflation on any result selected from a search. All six errors above
were invisible to the previous method and cheap to catch with this one.

**Do not size up `DriftJumpAlpha`**, and re-score reports 16 and 18 before
trusting any of their 14 walk-forward-"verified" slots.

**Put volatility clustering into position sizing.** It is the only measured,
robust, uncompeted structure this programme found. It requires no entry signal, no
directional forecast, and does not depend on a parameter that flips sign by decade.

**Re-examine the break-even stop.** It looks harmful at every trigger tested and
gives up 2.08 R of mean favourable excursion, though at t = −1.42 on 340 trades
this is a strong prior rather than a result.

## 9. Limits

- The synthetic conclusions rest on Deriv's *current* generators. They are
  contractual and stable over seven years, but not guaranteed forever.
- One year of ticks bounds drift only to about ±25%/yr; the martingale conclusion
  leans on the jump decomposition and seven years of bars, not on tick drift alone.
- The equity risk premium is unresolved here, not disproved. It is real; this
  server's 2.6-year history cannot see it.
- Cash and CFD series correlate only 0.565 close-to-close, so §6's cash results
  test the *phenomenon*, not CFD execution P&L day-by-day.
- Sixteen live symbols were tested against three hypotheses each; the deflation in
  §6 accounts for this, but a wider search would need a wider correction.

---

## Appendix — reports

| report | contents |
|---|---|
| 19 | research plan, feasibility, data sources |
| 20 | Phase 1 — generative characterisation, the DJA resolution |
| 21 | Phase 2 — live-market structure |
| 22 | Phase 3 — Monte Carlo validation |
| 23 | Phase 4 — walk-forward verdict |
| 24 | this paper |

Scripts in `research/data/`: `synth_tick_harvest.py`, `synth_bar_harvest.py`,
`synth_reconcile.py`, `synth_spread_audit.py`, `synth_quote_asymmetry.py`,
`phase1_bar_stability.py`, `phase1_jump_law.py`, `phase1_markov.py`,
`phase1_geometry_ev.py`, `phase1_dja_tick_replay.py`, `phase1_random_control.py`,
`phase1_overlap_rescore.py`, `phase1_be_policy.py`, `phase1_selection_noise.py`,
`live_bar_harvest.py`, `live_structure.py`, `live_meanrev_test.py`,
`live_meanrev_validate.py`, `live_meanrev_execution.py`, `phase3_montecarlo.py`,
`cash_index_harvest.py`, `cash_vs_cfd_check.py`, `phase4_walkforward.py`.

Data: `research/data/ticks/` (330 M ticks), `bars/`, `bars_live/`, `bars_cash/`.
