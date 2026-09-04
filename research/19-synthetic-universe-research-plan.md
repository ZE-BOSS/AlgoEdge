19 — Synthetic universe: strategy-creation research plan
========================================================

**2026-09-04** · feasibility measured live today against Deriv-MT5 (798 symbols,
167 synthetic) and Deriv's public WebSocket API.

Goal: repeat what DriftJumpAlpha did on Crash for **Boom, Volatility, Step,
Jump and Range Break** — a per-asset statistical edge that survives out of
sample, arrived at by measurement rather than by search.

This document is the plan only. Nothing here is a result.

---

## 0. What I measured today (facts, not proposals)

### 0.1 Data is available, in two complementary shapes

| source | resolution | depth | notes |
|---|---|---|---|
| **Deriv WS API** | **raw ticks** | **rolling 365 days, exactly** | no auth needed (`app_id=1089`), 5,000 ticks/request, **3,560 ticks/s** measured |
| MT5 M1 | 1 min | 99,999 bars ≈ **69 days** | capped by terminal `maxbars=100000` |
| MT5 M5 | 5 min | 99,999 bars ≈ **347 days** | |
| MT5 M15 | 15 min | 99,999 bars ≈ **2.85 years** | |
| **MT5 H1** | 1 hour | 44,677–64,716 bars = **5.1–7.4 years** | full history since inception |
| MT5 D1 | 1 day | inception → now | Boom/Crash 1000 from **2019-04-17**, 500 from 2019-07-12, 300 from 2021-07-30, Volatility 10 from 2019-01-01 |

**Yes, we can get real tick data** — this was the open question. Tick cadence is
exactly regular: **1.00 s** for Boom, Crash, Jump, Step, Range Break and the
(1s) volatility variants; **2.00 s** for standard Volatility indices. One year
of a 1-second symbol is **31.5 M ticks ≈ 2.5 h** to harvest on a single
connection.

**Trap worth recording:** requests older than 365 days do **not** error — they
silently return the *latest* data instead. A naive backward scraper would
happily collect duplicates and believe it had two years. Every harvest must
assert that the returned first-timestamp is where it was asked for.

**One free win available:** MT5's M1 cap is the terminal's *Max bars in chart*
setting (Tools → Options → Charts). Raising it to Unlimited would deepen M1
well past 69 days. Worth doing before Phase 0, it costs one click.

### 0.2 The universe splits into three classes, and the class decides the strategy

Measured from 40,000 real ticks per symbol pulled today:

| symbol | up-ticks | down-ticks | skew | kurtosis | tail events | inter-arrival CV |
|---|---:|---:|---:|---:|---|---:|
| **BOOM1000** | 0.11% @ **+0.114%** | 97.90% @ −0.0001% | +40.5 | 1815 | 1 per 1,053 ticks, **up** | 1.08 |
| **BOOM500** | 0.18% @ +0.089% | 96.80% @ −0.0002% | +37.4 | 1658 | 1 per 656, up | 0.85 |
| **CRASH1000** | 95.38% @ +0.0001% | 0.12% @ **−0.106%** | −40.5 | 1764 | 1 per 1,111, **down** | 0.92 |
| **CRASH500** | 95.17% @ +0.0002% | 0.23% @ −0.083% | −32.7 | 1218 | 1 per 656, down | 1.03 |
| **R_75** | 49.74% | 50.26% | **−0.02** | **3.0** | none | — |
| **stpRNG** | 49.31% | 50.69% | +0.03 | **1.0** | none | — |
| **JD100** | 43.84% | 43.96% | −10.9 | 814 | 1 per 2,105, down | 0.69 |
| **RB100** | 50.00% | 50.00% | +33.5 | 4732 | 1 per 5,714, up | 0.85 |

**Class A — exact Brownian motion (Volatility indices).** R_75 came back with
skew −0.02 and kurtosis **3.000**. That is not "approximately normal", that is
Gaussian to the decimal. Per-tick sd 0.01886% over 2 s annualises to **74.9%** —
i.e. Volatility 75 *is* geometric Brownian motion at exactly 75% annual
volatility, as advertised. **A driftless GBM admits no directional edge.** This
is a theorem, not a research finding, and no amount of pattern search changes
it. Report 08 already measured the empirical version of this on real markets
(random-entry expectancy = −cost, to within 0.01 R).

**Class B — asymmetric jump processes (Boom, Crash, Jump).** A compound Poisson
process: a dense grind one way, rare violent jumps the other. Inter-arrival
CV ≈ 0.85–1.08 means **memoryless** — waiting buys you nothing, which is exactly
what your own `crash_hazard.py` concluded. Any strategy here must be built on
*asymmetry*, never on *timing*.

**Class C — discrete-state processes (Step, Range Break).** Step Index has
**one single step size, 0.1**, up or down, kurtosis 1.0 — a pure Bernoulli
random walk. Range Break has **8 distinct step sizes**. These are the only
assets in the universe with genuine discrete state, and therefore the only ones
where a *transition-matrix* edge could exist that isn't ruled out a priori.

### 0.3 Cost and minimum-stop gates — what is worth researching at all

**This table was wrong in the first draft and has been replaced.** The original
read live spreads at about 00:30 local. Deriv widens synthetic spreads overnight,
by **6× to 20×** on the Boom/Crash family, so the live read was not
representative — the same trap report 08 fell into by reading spreads on a
Saturday, which I repeated after being warned by my own source. The corrected
basis is `MqlRates.spread`, recorded per bar, median over the last 30 days and
cross-checked against a full year and against seven years of H1.

Boom 1000 live read: **0.01003%**. Every H1 bar since 2019: **0.00100%**.

| symbol | hist spread | @1×ATR | @0.5×ATR | live/hist | min stop ÷ ATR | verdict |
|---|---:|---:|---:|---:|---:|---|
| **Crash 500** | 0.00138% | **0.012 R** | 0.025 R | 7.4× | 0.27 | cheapest in universe |
| **Boom 300** | 0.00489% | **0.013 R** | 0.026 R | 19.8× | — | cheap |
| Crash 300 | 0.00494% | 0.014 R | 0.028 R | 9.7× | — | cheap |
| **Boom 500** | 0.00139% | **0.016 R** | 0.031 R | 6.4× | 0.36 | cheap |
| **Boom 1000** | 0.00100% | **0.018 R** | 0.037 R | 10.1× | 0.35 | cheap |
| **Crash 1000** | 0.00099% | **0.026 R** | 0.052 R | 8.0× | 0.35 | *DJA profitable here* |
| Range Break 100 | 0.00185% | 0.034 R | 0.068 R | 2.6× | 0.31 | workable, discrete |
| Range Break 200 | 0.00150% | 0.036 R | 0.073 R | 2.0× | 0.16 | workable, discrete |
| Jump 100 | 0.02182% | 0.039 R | 0.077 R | 2.3× | **2.05** | **geometry impossible** |
| Step Index 500 | 0.01345% | 0.047 R | 0.095 R | 0.9× | 0.16 | workable, discrete |
| Jump 25 | 0.00588% | 0.053 R | 0.107 R | 1.6× | 0.08 | workable |
| Step Index | 0.00256% | 0.080 R | 0.160 R | **0.5×** | 0.35 | dearer than it looked |
| Volatility 25 | 0.00954% | 0.093 R | 0.186 R | 1.1× | — | Class A |
| Volatility 75 (1s) | 0.03302% | 0.094 R | 0.188 R | 1.0× | — | Class A |
| Volatility 100 | 0.04260% | 0.103 R | 0.206 R | 1.0× | 0.49 | Class A |
| Volatility 75 | 0.03467% | **0.115 R** | 0.231 R | 1.1× | **0.63** | dearest, and Class A |

**The ranking inverts.** Boom and Crash are the **cheapest** family in the
universe at 0.012–0.026 R, not the dearest. Volatility is the **most expensive**
at 0.093–0.115 R — so Class A is unattractive on cost as well as being
structurally edgeless. Step Index moves the other way: its live read was 2× *low*,
and at 0.080 R it is dearer than it first appeared.

Three constraints stand:

- **Jump 100's minimum stop is 2.05 × M5 ATR.** The tight-stop/far-target
  geometry that report 08 found optimal on synthetics is *not permitted by the
  broker* on this symbol. Jump 100 needs a different strategy shape or none.
- **Volatility 75's floor is 0.63 × ATR**, above the 0.5 × ATR report 08 found
  optimal — Class A is closed off from both directions.
- **Never measure or trade synthetics in the overnight widening window.** A
  strategy sized on live-read spreads at 01:00 would price Boom 300 twenty times
  too dear and reject it outright.

Volatility indices are cheap only in the (1s) variants and none of that helps if
the process is driftless GBM.

### 0.4 Deriv serves mid, MT5 serves bid — and it is symmetric

Sampling the API tick against MT5's live bid/ask simultaneously, 20+ rounds per
symbol: the API price sits at **exactly half the spread above the bid**
(frac 0.500, sd ≤ 0.05) on 11 of 12 symbols. So Deriv's WS API is the index's
**mid** and MT5 bars are **bid**.

Two consequences:

- **A backtest built on MT5 bars charges nothing for crossing the spread.** Bars
  are bid; a long really enters at ask. Entry and exit must be adjusted by
  ±½ spread explicitly. This is a candidate contributor to the §1 gap.
- The quote is **symmetric**, so neither direction is structurally penalised —
  with one exception. **Step Index measures frac 0.000**: its API price *is* the
  bid, so the ask is marked up by the full spread, which on Step is exactly one
  0.1 step. Longs pay the whole toll there, shorts pay none.

---

## 1. The contradiction this research must resolve first

I want to flag this before proposing five new strategies on the same foundation.

Three things are each independently supported, and they cannot all be true:

1. Crash/Boom spike arrival is **memoryless** (CV ≈ 1, measured twice — your
   `crash_hazard.py` and again today).
2. These indices are constructed to be **fair** — drift near zero by design, and
   today's per-tick drift estimates are all within ~1 standard error of zero.
3. DriftJumpAlpha earns **+0.2039 R out of sample** on Crash 1000 (report 18),
   *stronger* out of sample than in — the signature of a real effect, not a fit.

If (1) and (2) hold, a directional strategy's expectancy should be **−spread**,
which for Crash 1000 is −0.23 R at a 1×ATR stop. Report 18 measures +0.20 R.
That is a 0.43 R gap that theory says should not exist.

Either the edge is real and comes from something not yet named — most plausibly
**path asymmetry** rather than drift (on Crash, price grinds up on 95.4% of
ticks, so a long with a far target reaches it by grinding and only loses to a
spike) — or some of it is harness artifact. Report 18's own Limits section
already notes the harness omits session-end exits, concurrency caps and the
circuit breaker.

**This is cheap to settle and everything else depends on it.** If the mechanism
is path asymmetry, it is a *structural* property that transfers to Boom by
mirror symmetry and gives us a principled generator for the other assets. If it
is partly artifact, we need to know that before scaling it across five more
symbols. Phase 1 settles it in about a day.

I am not asserting the edge is false — report 18's out-of-sample strengthening
is genuinely strong evidence. I am saying we should know *why* it works before
we replicate the method five more times.

---

## 2. The anti-overfitting method — the part that makes this different

You asked twice for this without overfitting. Synthetic indices allow something
real-market research fundamentally cannot, and I want to build the whole
programme around it:

> **Fit the generating process, then validate on unlimited freshly simulated
> data from that process.**

Because these instruments are *generated*, not traded, we can estimate the
generator's parameters from one year of ticks and then draw **as many
independent statistically-identical years as we like**. A strategy is then
tested on data that has never existed and cannot have been fitted to.

That gives a decisive three-way test:

| works on historical year | works on simulated years | conclusion |
|---|---|---|
| yes | yes | **structural edge** — trade it |
| yes | no | **fitted to noise** — discard, no appeal |
| no | yes | generator is mis-specified — refit |

No real-market research can do this, because you cannot draw new years of gold.
It converts "did we overfit?" from a judgement call into a measurement.

Two supporting rules:

- **Only structural hypotheses are admissible.** A hypothesis must be
  statable as a property of the generating process — *"Crash 1000 jumps once per
  N ticks with magnitude distribution D"* — and verifiable to several
  significant figures on 31.5 M ticks. *"RSI < 30 on M15 works"* is not
  admissible and will not be tested. This is precisely the discipline that would
  have caught APA before report 18 did.
- **Pre-register.** Hypothesis and pass/fail threshold written down before P&L
  is computed. Recorded in the report whether they pass or fail — including the
  failures, as reports 16 and 18 already do.

**One honest limit up front:** high-frequency data pins down *volatility* and
*jump structure* superbly, but it does **not** pin down *drift* — that is a
known result and no amount of tick resolution fixes it. One year gives roughly
±25% annualised precision on Crash 1000's drift. The fix is the decomposition in
Phase 1.2 plus MT5's 7.4 years of H1, which cuts the error ~2.7×. I would rather
state that now than have it surface as a surprise in Phase 3.

---

## 3. Phased plan

### Phase 0 — Data foundation · ~1 day

- Tick harvester against the Deriv WS API → **365 days × 1 s ticks**, Parquet,
  for a core 12: Boom 500/1000, Crash 500/1000, Volatility 25/75/100, Step,
  Step 500, Jump 25/100, Range Break 100/200.
  Budget ~2.5 h per 1 s symbol single-connection; parallel connections cut it to
  an overnight run. Roughly 30 GB raw, well under 10 GB as Parquet float32.
- Assert-on-drift guard against the 365-day silent-fallback trap.
- MT5 pull of H1 + M15 back to inception for the same symbols — the long
  context that tick data cannot reach.
- **Reconcile**: rebuild M1 bars from Deriv ticks and diff against MT5's M1. If
  they disagree we learn it here, not in Phase 4.

**Deliverable:** a cached, verified dataset + `research/data/` harvest scripts,
reusable for everything after.

### Phase 1 — Generative characterisation & the contradiction · ~1–2 days

Per asset, estimate the process and publish parameters with confidence
intervals:

- **Class A (Volatility):** confirm GBM and *measure how exactly* — annualised
  vol vs nameplate, drift CI, and a formal test for any deviation from
  normality. Expected outcome: no directional edge exists. If that confirms,
  **say so plainly and stop spending time there** rather than manufacturing a
  strategy. That is the single largest time saving available in this programme.
- **Class B (Boom/Crash/Jump):** jump arrival law (Poisson test on 31.5 M
  ticks), magnitude distribution, and the drift **decomposition**:
  `E[r] = p·E[jump] + (1−p)·E[grind]` — each component estimable far more
  precisely than the aggregate, which is the way around the drift-precision
  limit. Then: does jump magnitude depend on price level, time since last jump,
  or time of day? Each is a structural, falsifiable question.
- **Class C (Step/Range Break):** build the discrete transition matrix and test
  for **Markov order > 0**. If the next step depends on history at all, that is
  a directly exploitable edge on the two cheapest assets in the universe. If it
  is order-0, they are fair coins and we say so.
- **Settle §1**: decompose DJA's Crash 1000 expectancy into drift, path
  asymmetry and harness effects.

**Deliverable:** `research/20-generative-characterisation.md` — a parameter
sheet for the universe, plus a verdict on §1.

### Phase 2 — Hypothesis-driven edge search · ~2 days

Only hypotheses that survived Phase 1. Scored in expectancy-R **net of measured
spread**, using the excursion machinery already in `research/data/`
(`excursion_engine.py`, `optimise_exits.py`) rather than new code.

Expected shape of the results, based on what is already measured:

- **Boom** — highest confidence, least work. Boom is Crash's mirror: 97.9% grind
  down, rare violent up-spikes. If DJA works on Crash for the reason §1
  establishes, the mirrored strategy works on Boom — and on the corrected cost
  basis Boom is **cheaper than the symbol DJA already profits on**: Boom 1000
  0.018 R and Boom 500 0.016 R against Crash 1000's 0.026 R. **This is the most
  likely win in the whole programme**, and the corrected §0.3 strengthens rather
  than weakens the case.
- **Step / Range Break** — the Markov test in Phase 1.3 is the whole question.
  Range Break is cheap (0.034–0.036 R); Step is dearer than the first draft
  claimed (0.080 R) and carries a full-spread toll on longs specifically (§0.4),
  so any Step edge must be short-side or large. Zero if the process is order-0.
- **Jump** — constrained: Jump 100's stop floor rules out the standard geometry.
  Jump 25 at 0.053 R with a 0.08 × ATR floor is the better candidate.
- **Volatility** — expected to be formally ruled out in Phase 1, and now also the
  most expensive family in the universe (0.093–0.115 R). Doubly unattractive.

### Phase 3 — Monte Carlo validation · ~1 day

Simulate ≥ 100 independent years per asset from the Phase 1 generators, run
every Phase 2 candidate, and apply the §2 three-way table. Anything that works
on the historical year but not on simulated years is discarded without appeal.

### Phase 4 — Walk-forward + strategy specs · ~2 days

Survivors get the report 18 treatment — the split it was *not* selected on —
then a per-asset spec in the existing `backend/strategies/` format with
`strategy_defaults.py` entries, ready for the app's own backtester.

### Phase 5 — The research paper

`research/21-synthetic-strategy-creation.md`: method, per-asset parameters,
edges found **and edges disproved**, strategy specs, and the Monte Carlo
validation record.

---

## 4. What would make me stop or change course

- Volatility indices confirm as exact GBM → **no strategy for them**, and I will
  say so rather than produce one. This is the likely outcome and it is a
  finding, not a failure.
- Step/Range Break come back order-0 Markov → they are fair coins; no strategy.
- §1 resolves toward "substantially harness artifact" → the priority becomes
  fixing the harness, not adding five strategies to it.
- Boom mirrors Crash as expected → ship that first, before the rest is finished.

## 5. Cost

About 7–9 working days end to end, with the overnight tick harvest as the only
wall-clock bottleneck. Phase 0 and Phase 1 alone (~3 days) settle the §1
contradiction and either confirm or kill Boom — which is most of the practical
value.

## 6. Open decisions for you

1. **Universe** — is the core 12 right, or do you want Boom/Crash 300 and the
   (1s) variants in from the start?
2. **Boom first, or characterise everything first?** Phase 1 on Boom alone could
   ship a tradeable strategy in ~2 days; the full sweep takes ~9.
3. **Am I authorised to raise MT5's Max-bars setting**, or would you rather do
   it yourself?
