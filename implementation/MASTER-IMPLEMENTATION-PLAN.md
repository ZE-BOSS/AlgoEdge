# AlgoEdge — Implementation Plan: Phases 3 and 5

**Author:** Claude (Opus 5) · **Revised:** 2026-09-10 · **Branch:** `dev`

Phases 1, 2 and 4 are shipped — see
[`PHASES-1-2-4-RESOLVED.md`](PHASES-1-2-4-RESOLVED.md). This document covers only
what remains:

- **Phase 3** — an LLM forecasting agent on real markets.
- **Phase 5** — higher-timeframe context and microstructure, and the unified
  comparison at $350.

---

## Status legend

| mark | meaning |
|---|---|
| ✅ | Done — verified by measurement, not by "the code looks right" |
| 🟡 | In progress |
| ⬜ | Not started |
| 🔴 | Blocked on a named gate |

---

# §0 — What the completed work changes about these two phases

Three things carried forward, and they matter more than the phase order.

**1. The harness now tells the truth, so measurement is possible at all.** Until
today, every Boom/Crash backtest booked 100% of its stops at exactly the stop
price, worth 0.35–0.40 R per stopped trade against a measured edge of +0.12 R.
Nothing measured before that fix is usable as evidence for anything in these two
phases. Any number quoted from `research/16` or `research/18` must be re-derived.

**2. The instrument universe moves.** research/24 measured every Deriv synthetic
to be a fair martingale with memoryless jump arrival — the processes are
*specified* to be fair, and that is measured rather than assumed. Phase 3 and
Phase 5 therefore target **real markets**, where the same study found the one
durable, uncompeted structure it could find:

| test | synthetics | live markets |
|---|---|---|
| volatility clustering, Ljung–Box Q(20) | 11 – 23 | **38 – 3,788** (null threshold 45) |
| return autocorrelation | absent | present in equity indices, unstable by decade |
| risk premium | absent by construction | unmeasurable on this history; absent in FX |

Clustering is not a directional edge, which is exactly why it cannot be
arbitraged away — it describes risk, not return. **It is the single highest
expected-value item in this document, and it is not a strategy** (§2.1).

**3. The standard of proof is higher than it was.** research/24 §7 lists six
measurement errors that produced confident, wrong answers, four of them in that
work. Two are structural and apply directly here:

- **Overlapping trades.** Random long entries at DriftJumpAlpha's exact geometry
  read as a 4σ edge when trades overlap (n=3,998, t=+3.99) and as fair when they
  do not (n=685, t=+0.56). Your own runs average 1.27 concurrent positions.
- **Data-mining deflation.** A candidate that passed *four* validation stages
  died on the fifth: across ~84 hypotheses searched, the null expects a maximum
  |t| of 2.64 and the observed was 2.30 — inside the band. On 57 years of data it
  lost 9.2%/yr and flipped sign by decade.

**Every result in these two phases must be reported with an overlap-aware
t-statistic and a deflation for the number of hypotheses searched.** A profit
factor without those is decoration.

---

# §1 — PHASE 3: An LLM forecasting agent on real markets

**Scope, as you specified it:** Claude Opus 5 via your API key, as a *forecasting*
component only. It never sizes, never places, never manages. It emits a
structured proposal; `RiskEngine` and `PositionManager` remain the sole authority
on risk and execution.

**Universe:** XAUUSD, XAGUSD, XPTUSD, EURUSD, GBPJPY, BTCUSD, NAS100 (US Tech
100), SPX500 (US SP 500).

## 1.1 The look-ahead problem, and why it decides the schedule

You raised this yourself and you were right to. A model with a knowledge cutoff,
asked what gold did on a date before that cutoff, is recalling, not forecasting.
Any backtest that ignores this is worthless — and unlike most backtest flaws,
this one *cannot* be fixed by better code.

Five mitigations, in descending order of how much they are worth:

1. **Forward testing is the primary evidence.** Paper-trade from day one:
   timestamped, append-only, hash-chained so a row cannot be revised after the
   fact. This is the only unimpeachable measurement, and it is why Phase 3 starts
   early — it needs calendar time, not compute.
2. **Blind the context.** Strip dates, symbol names (map to `INSTRUMENT_A`), and
   absolute price levels. Feed normalised returns, z-scored features, relative
   levels. **The accuracy gap between blinded and unblinded is a direct estimate
   of the leakage** — measure it, report it, and treat it as the error bar on
   every historical result.
3. **Post-cutoff-only backtesting.** Restrict historical evaluation to dates
   after the model's cutoff. Smaller window, honest window.
4. **Permutation control.** Run the identical pipeline on surrogate series with
   matched volatility clustering. Accuracy above that control is the real signal;
   everything below it is the pipeline finding structure in noise.
5. **A non-LLM baseline it must beat.** Volatility-scaled momentum plus a
   session/time-of-day prior. If the agent cannot beat that, it is an expensive
   random number generator and should be switched off.

**Gate (3.14):** ≥200 **non-overlapping** blinded forward forecasts beating the
baseline on **expectancy**, not accuracy. No live capital before that clears.

## 1.2 Architecture

```
                 ┌───────────────────────────────────────────┐
   scheduler ───▶│  ContextBuilder   (deterministic, cached) │
   4h regime     │  • OHLCV M5/M15/H1/H4/D1 (normalised)     │
   15m trigger   │  • ATR, realised vol, vol-of-vol, ACF(r²) │
                 │  • VWAP + bands, session VWAP             │
                 │  • Volume profile: POC / VAH / VAL        │
                 │  • CVD, delta divergence, absorption      │
                 │  • DOM imbalance (MT5BookProvider)        │
                 │  • GEX / gamma flip / call + put walls    │
                 │  • Calendar: next events + blackout       │
                 │  • Cross-asset: DXY, US10Y, VIX proxy     │
                 └────────────────────┬──────────────────────┘
                                      ▼
                 ┌───────────────────────────────────────────┐
                 │  ForecastAgent  (claude-opus-5)           │
                 │  strict JSON, temperature 0,              │
                 │  extended thinking, prompt-cached         │
                 └────────────────────┬──────────────────────┘
                                      ▼
                 ┌───────────────────────────────────────────┐
                 │  ProposalValidator                        │
                 │  schema · sanity · staleness · dedupe     │
                 │  → TradeSignal (confluence_score set)     │
                 └────────────────────┬──────────────────────┘
                                      ▼
              EXISTING RiskEngine → PositionManager → MT5
```

**Most of the data layer already exists** and is the reason this is weeks rather
than months:

| capability | location |
|---|---|
| Anthropic client, `claude-opus-5` default, streaming, thinking | `services/llm_service.py` |
| Gamma exposure (GEX) | `data/providers.py:354` |
| CBOE / Yahoo / Polygon options chains | `data/providers.py:407-620` |
| Databento MBO (true tape, aggressor flags) | `data/providers.py:621` |
| MT5 depth of book | `data/providers.py:214` |
| CVD, delta divergence, absorption, volume profile | `data/orderflow.py` |
| ForexFactory calendar | `data/providers.py:328` |
| Session/anchored VWAP | `strategies/strategy_vwap/` |

**Output contract** — the agent may return this and nothing else:

```json
{
  "instrument": "INSTRUMENT_A",
  "horizon_bars": 48,
  "direction": "LONG | SHORT | FLAT",
  "conviction": 0.0,
  "entry_zone": [lo, hi],
  "invalidation": 0.0,
  "targets": [{"level": 0.0, "probability": 0.0}],
  "expected_move_atr": 0.0,
  "regime": "TRENDING | RANGING | EXPANDING | COMPRESSING",
  "primary_evidence": ["..."],
  "contradicting_evidence": ["..."],
  "abstain_reason": null
}
```

Two design commitments worth stating explicitly:

- **`conviction` is calibrated, not vibes.** Scored weekly with a Brier score and
  a reliability diagram. An agent whose 0.8s come in at 55% gets recalibrated by
  isotonic regression *before* it is allowed to influence size. An uncalibrated
  confidence number is worse than no confidence number, because position sizing
  will believe it.
- **Abstention is a first-class output and is rewarded.** Most 15-minute windows
  contain nothing. An agent that says FLAT 80% of the time and is right on the
  rest is the target; one that always has a view is pattern-matching on the
  prompt.

**`confluence_score` is now load-bearing.** Phase 1 fixed live to actually pass
it to the risk engine, so a calibrated conviction maps straight onto the existing
tier ladder and sizes the trade. Get the calibration wrong and it silently
mis-sizes every trade.

## 1.3 Cost control

8 instruments × every 15 min × 24 h = 768 calls/day, which is not viable with a
large context. Design for it from the start:

- **Tiered cadence.** An H4 "regime brief" per instrument every 4 h (8 × 6 = 48
  calls). The M15 trigger runs a **cheap local** precondition — vol expansion,
  level proximity, event proximity — and escalates to the model only when it
  fires. Expect 20–60 model calls/day.
- **Haiku 4.5 as the trigger filter, Opus 5 as the analyst.**
- **Prompt caching** on the static instrument brief and instructions (already
  supported in `llm_service.py`).
- **Hard daily token budget with a circuit breaker**, logged per instrument. A
  runaway loop must cost a bounded amount of money, not an unbounded one.

## 1.4 Task board

| # | task | status | effort |
|---|---|---|---|
| 3.1 | `ContextBuilder` — deterministic, versioned, cached, snapshot-able | ⬜ | 5 d |
| 3.2 | Feature set v1: VWAP, volume profile, CVD, ATR/vol, session, calendar | ⬜ | 4 d |
| 3.3 | GEX / gamma-wall integration for NAS100 + SPX500 | ⬜ | 2 d |
| 3.4 | DOM / order-book features (MT5 book, Databento where keyed) | ⬜ | 3 d |
| 3.5 | `ForecastAgent` + strict JSON schema + `ProposalValidator` | ⬜ | 3 d |
| 3.6 | Blinding layer (dates, names, absolute levels stripped) | ⬜ | 2 d |
| 3.7 | Non-LLM baseline (vol-scaled momentum + session prior) | ⬜ | 2 d |
| 3.8 | Permutation / surrogate control harness | ⬜ | 3 d |
| 3.9 | Calibration: Brier, reliability diagram, isotonic recalibration | ⬜ | 2 d |
| 3.10 | Paper-trade forward test, append-only hash-chained journal | ⬜ | 2 d + **8 wks calendar** |
| 3.11 | Post-cutoff-only backtest harness | ⬜ | 3 d |
| 3.12 | Token budget + cost circuit breaker | ⬜ | 1 d |
| 3.13 | `LLMForecast_v1` adapter into the existing strategy registry | ⬜ | 2 d |
| 3.14 | 🔴 **Gate** — 200 non-overlapping blinded forecasts beat the baseline | 🔴 | — |

## 1.5 Open questions for you

- **Databento / Polygon keys?** They decide how good the order-flow features can
  be. Without them, DOM comes from MT5 only (thin on CFDs) and tape aggressor
  flags are inferred rather than true.
- **Confirm the cadence** (4 h regime / 15 m gated trigger) and the 8-instrument
  universe before 3.1 starts, since `ContextBuilder`'s caching design follows
  from it.

---

# §2 — PHASE 5: Higher timeframe context and microstructure

**Framing, as you asked for it:** approach this the way a bank's systematic desk
would. That means the deliverable is not "more indicators" — it is
**conditioning**: identifying states of the world in which an existing signal has
a *different* expectancy, and sizing accordingly. Expectancy and profit factor,
not win rate.

## 2.1 Do this first — it is the highest expected-value item in the plan

research/24 §8, verbatim:

> **Put volatility clustering into position sizing.** It is the only measured,
> robust, uncompeted structure this programme found. It requires no entry signal,
> no directional forecast, and does not depend on a parameter that flips sign by
> decade.

**Volatility-targeted sizing** — scale risk by `target_vol / realised_vol` —
converts clustering into a smoother equity curve and a materially better Sharpe
**without predicting direction at all**. It works across the real-market universe
(Q(20) 38–3,788). It does nothing on synthetics (Q(20) 11–23), which is itself a
useful confirmation.

It is one field on `RiskParams` and one function in `position_sizer.py`. It is
also the only item in this document with prior measured support behind it, and
it should ship before anything in §2.3 is attempted.

## 2.2 Task board

| # | task | status | effort |
|---|---|---|---|
| 5.1 | **Volatility-targeted position sizing** (`RiskParams.vol_target_pct`) | ⬜ | 3 d |
| 5.2 | HTF regime layer: D1/H4 trend, vol regime, range vs expansion | ⬜ | 3 d |
| 5.3 | Conditional-expectancy study: existing signals × HTF regime cells | ⬜ | 4 d |
| 5.4 | Session / anchored VWAP + std-dev bands as a shared feature | ⬜ | 2 d |
| 5.5 | Volume profile (POC / VAH / VAL, composite + developing) | ⬜ | 3 d |
| 5.6 | Order-flow features: CVD, absorption, delta divergence | ⬜ | 2 d |
| 5.7 | GEX / gamma-flip levels for index products | ⬜ | 2 d |
| 5.8 | Real confluence score for SpikeFade (replace the hardcoded 70) | ⬜ | 2 d |
| 5.9 | Purged + embargoed cross-validation framework | ⬜ | 4 d |
| 5.10 | Deflated Sharpe / PBO across the whole strategy book | ⬜ | 3 d |
| 5.11 | Unified comparison report: all strategies × universe, $350, corrected | ⬜ | 2 d |

## 2.3 The conditional-expectancy study (5.3) — the institutional core

For every existing strategy slot, cut the trade population by higher-timeframe
state and report expectancy per cell with overlap-aware errors:

| conditioning variable | buckets |
|---|---|
| D1 trend (close vs 20/50 EMA) | up / flat / down |
| H4 realised vol vs 60-day median | low / normal / high |
| session | Asia / London / NY / overlap |
| distance to session VWAP | <1σ / 1–2σ / >2σ |
| position in volume profile | above VAH / in value / below VAL |
| GEX regime (indices) | positive gamma / negative gamma |
| event proximity | <60 min / clear |

**The rule to hold yourself to.** A cell is actionable only if it survives all
three of:

1. an overlap-aware t-test (non-overlapping subsample, not `sd/√n`),
2. a data-mining deflation across the number of cells searched,
3. a walk-forward split.

research/24 §6 documents a candidate that passed **four** validation stages and
then died on the fifth — 57 years of data flipped its sign by decade and it lost
9.2%/yr on the S&P. **Assume every cell you find is that one until it proves
otherwise.** The 2.6-year window that made it look good sat entirely inside the
single recent regime where it happened to hold.

## 2.4 Purged, embargoed cross-validation (5.9)

Standard k-fold leaks badly when labels overlap. Implement purging plus embargo:
drop training samples whose label window overlaps the test window, and embargo a
further window after it.

Without this, **every walk-forward number in `research/16` and `research/18`
remains suspect** — research/24 §8 says explicitly to re-score those 14
"verified" slots before trusting any of them. 5.9 is what makes 5.11 meaningful.

## 2.5 The $350 report (5.11)

The unified comparison you asked for: every strategy × the tradeable universe, at
$350, with the corrected fill model, reported as expectancy in R, overlap-aware
t, deflated Sharpe, and dollars.

**One thing that report must carry prominently**, measured in Phase 1: at $350,
**49% of SpikeFade's signals cannot be expressed** because the position it wants
is below the broker's minimum lot. The $350 run took 2 Boom trades against the
$700 run's 65, from an identical 208-signal set. A $350 account does not trade a
smaller version of the strategy — it trades a biased subsample weighted toward
whichever setups happened to have cheap stops. Any per-strategy verdict at $350
has to state its own capital adequacy alongside its expectancy, or it is
comparing different strategies to each other.

---

# §3 — Sequencing and gates

```
week 1-2    ████ 5.1  volatility-targeted sizing        ← ship this first
week 1-4    ████ 3.1-3.6  context, agent, blinding
week 3-5    ████ 3.7-3.9  baseline, permutation control, calibration
week 4-12   ████ 3.10 forward test        (8 weeks of this is calendar time)
week 5-8    ████ 5.2-5.7  HTF regime + microstructure features
week 8-11   ████ 5.9-5.10 purged CV, deflated Sharpe, PBO
week 11-12  ████ 5.11 unified $350 comparison
            ────────── GATE 3.14: 200 forward forecasts beat baseline ──────────
week 12+         live capital on Phase 3
```

**Hard gates:**

1. **No LLM capital before 3.14.** 200 non-overlapping blinded forward forecasts
   beating the baseline on expectancy.
2. **Nothing from 5.3 ships without 5.9 and 5.10.** A conditional cell without
   purged CV and a deflation is the §6 candidate from research/24 wearing a
   different hat.
3. **No size increase anywhere on an overlap-naive t-statistic.** Random entries
   at a real strategy's geometry read as 4σ when trades overlap.
4. **Re-derive, do not cite.** Any number from `research/16` or `research/18`
   predates the fill-model fix and must be recomputed before it is used as
   evidence.

---

# §4 — What would make me abandon a phase

Stated in advance, so the decision is not made under sunk cost:

- **Phase 3** — if the blinded forward test cannot beat vol-scaled momentum plus
  a session prior over 200 non-overlapping forecasts, the agent has no edge and
  the honest move is to say so and stop paying for tokens. A large blinded-vs-
  unblinded gap on historical data means the historical results were recall, and
  only the forward number counts.
- **Phase 5** — if no conditioning cell survives purged CV plus deflation, then
  the conditioning adds nothing and the correct output is the vol-targeted sizing
  from 5.1 alone. That would still be a real improvement, and it would be worth
  more than a cell that looks good and flips sign in two years.
