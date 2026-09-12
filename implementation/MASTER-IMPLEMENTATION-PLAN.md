# AlgoEdge — Implementation Plan: Phases 3 and 5

**Author:** Claude (Opus 5) · **Revised:** 2026-09-11 · **Branch:** `dev`

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
| 🟡 | Partly done — what remains is named |
| ⬜ | Not started |
| 🔴 | Blocked on a named gate |

---

# §0 — Results to date (2026-09-11)

Everything below was measured on live MT5 data from this machine. The headline:
**the measurement stack is built and validated; every edge tested so far is
inside noise.**

| what was tested | result | verdict |
|---|---|---|
| Volatility-targeted sizing (5.1), 6 instruments, A/B on identical trades | mean Sharpe +0.07 → −0.02 with ATR stops; improved 1 of 6 | **do not enable** — ATR stops already size as 1/vol, so it double-counts |
| Momentum baseline, full H1 history, 8 instruments, 901 forecasts | +0.011 R, t = +0.26 | inside noise |
| Momentum baseline, post-cutoff window (Jun–Sep 2026), 347 actionable | −0.294 R vs surrogate mean −0.089, p = 0.967 | **FAIL** |
| Momentum × 17 higher-timeframe regime cells (5.3), 907 trades | best \|t\| 2.17 vs family-wise bar 3.29, p = 0.695; global scope best 1.32, p = 1.000 | **no conditional edge** |
| LLM forecaster (3.14 gate) | not run — API console holds $0 | 🔴 pending $1.22 |

Two measurement lessons carried forward into the code:

1. **Zero is never the bar.** The harness's own conservatism (a bar touching both
   stop and target resolves to the stop; stops fill with overshoot) costs
   ~0.09 R per trade even on structureless data. Every result is compared to
   its surrogate distribution, not to zero.
2. **Two statistics bugs were caught and fixed**, both now regression-tested:
   the data-mining bar for small families sat *below* a single test's 2.0 (three
   configurations gave ~1.30), and a sign-only stability rule let an edge that
   decayed from +1.23 R to +0.03 R read as ACTIONABLE.

---

# §1 — PHASE 3: An LLM forecasting agent on real markets

**Scope, as you specified it:** Claude as a *forecasting* component only. It
never sizes, never places, never manages. It emits a structured proposal;
`RiskEngine` and `PositionManager` remain the sole authority on risk and
execution.

**Universe:** XAUUSD, XAGUSD, XPTUSD, EURUSD, GBPJPY, BTCUSD, US Tech 100,
US SP 500.

## 1.1 Look-ahead: the calendar first, blinding second

A model asked about a market it was trained on may be recalling, not
forecasting. The defences, strongest first:

1. **Post-cutoff decisions only.** A decision whose outcome lies after the model's
   knowledge cutoff cannot be recalled by any version of it. `run_forecast_test.py`
   decides only on bars from 2026-06-01 (Opus 5's cutoff is May 2026). ~600
   non-overlapping H1 forecasts exist in that window.
2. **Blinding.** No instrument name, no dates, relative levels only. Kept as a
   second line of defence — it makes recall harder, it cannot make it impossible.
3. **Forward testing** in calendar time, into the hash-chained journal.
4. **The surrogate control** — the identical pipeline on price series that keep
   the volatility path and destroy direction.
5. **The non-LLM baseline** it must beat.

The in-session alternative (the assistant producing the forecasts itself) was
withdrawn: the in-session model has read the backtests and knows what result
would be convenient, so it is more contaminated than a clean API call, not less.

## 1.2 Authentication — API key only

The Claude Agent SDK's own documentation rules out running this on a Claude.ai
subscription:

> "Unless previously approved, Anthropic does not allow third party developers to
> offer claude.ai login or rate limits for their products, including agents built
> on the Claude Agent SDK. Use the API key authentication methods described in
> the Quickstart instead."

So the forecaster authenticates with an API key (Anthropic console credits), or
not at all until Anthropic approves otherwise. For a single structured forecast
the client SDK already in `forecast_providers.AnthropicProvider` is also the right
engineering choice: the Agent SDK spawns a CLI subprocess per session, built for
multi-step tool loops.

## 1.3 What the gate run costs

Measured prompt: ~869 input tokens. The realistic feasibility run — 25 forecasts
× 8 instruments, plus a 3-series bootstrap surrogate control on 3 instruments —
is 425 calls and dry-runs at:

| model | cost |
|---|---|
| claude-haiku-4-5 | **$1.22** |
| claude-sonnet-5 | $10.67 |
| claude-opus-5 | $17.78 |

```bash
python scripts/run_forecast_test.py --provider anthropic:claude-haiku-4-5 --max-forecasts 25 --surrogates 3 --surrogate-instruments XAUUSD EURUSD BTCUSD --max-cost 2.00
```

The run refuses before its first call if the plan exceeds `--max-cost`, stops
after three consecutive operational failures (billing, auth) instead of scoring
the outage as caution, and never pays twice for a cached forecast.

**Pricing correction recorded:** a surrogate run re-forecasts every decision
point, so the control costs `surrogates × decisions`, not `surrogates`. An earlier
estimate priced it as the latter. `plan_run` prices it correctly.

## 1.4 Task board

| # | task | status | notes |
|---|---|---|---|
| 3.1 | `ContextBuilder` — deterministic, versioned, cached | ✅ | `analytics/forecast_context.py`; price-level-invariant hash |
| 3.2 | Feature set v1 | 🟡 | VWAP, volume profile, ATR/vol, clustering, range, session ✅; order flow is a tick-volume *proxy*, labelled as such; no historical calendar |
| 3.3 | GEX / gamma walls for the indices | ⬜ | needs historical options data |
| 3.4 | DOM / order-book features | ⬜ | MT5 book is thin on CFDs; true tape needs Databento |
| 3.5 | `ForecastAgent` + `ProposalValidator` | ✅ | every failure path abstains with an accurate reason |
| 3.6 | Blinding layer | ✅ | secondary to the post-cutoff harness (§1.1) |
| 3.7 | Non-LLM baseline | ✅ | measured: inside noise |
| 3.8 | Surrogate control | ✅ | `full` and `bootstrap` modes; validated on the baseline |
| 3.9 | Calibration: Brier, reliability, isotonic | ✅ | `analytics/forecast_scoring.py` |
| 3.10 | Forward test + hash-chained journal | 🟡 | journal ✅ (tamper and deletion detected); the calendar-time forward run has not started |
| 3.11 | Post-cutoff harness | ✅ | `analytics/forecast_harness.py`, `scripts/run_forecast_test.py` |
| 3.12 | Token budget + cost circuit breaker | ✅ | `services/llm_budget.py`, enforced inside the API call path |
| 3.13 | Provider seam + registry adapter | 🟡 | `services/forecast_providers.py` ✅; `LLMForecast_v1` registry strategy not yet |
| 3.14 | 🔴 **Gate** — post-cutoff test PASS | 🔴 | blocked on ~$2 of API console credit |

---

# §2 — PHASE 5: Higher timeframe context and microstructure

**Framing:** conditioning — finding states of the world in which an existing
signal has a *different* expectancy, and sizing accordingly. Expectancy and
profit factor, never win rate.

## 2.1 Volatility-targeted sizing — measured, and not worth enabling

research/24 §8 recommended it generically, and this plan originally called it the
highest expected-value item. Measurement against this codebase disagreed:

| stop style | mean Sharpe | mean max DD | improved |
|---|---|---|---|
| 2 × ATR (what AlgoEdge uses) | +0.07 → −0.02 | 17.0% → 20.3% | 1 of 6 |
| fixed price distance | +0.27 → +0.23 | 16.1% → 18.7% | 3 of 6 |

**Why:** with an ATR stop, `lots = risk$ / (k × ATR)` already scales position size
as 1/volatility; multiplying by `target_vol / realised_vol` corrects the same
exposure twice. The clustering itself is real on every live instrument (ACF(r²)
0.05–0.30) and absent on every synthetic — the structure holds, this lever does
not. Shipped off by default in `risk/vol_target.py`.

## 2.2 The conditional-expectancy study (5.3) — run, no edge

907 momentum trades across 8 instruments, labelled with the state observable at
entry (`analytics/htf_regime.py`), judged three ways at once:

1. against the complement, on non-overlapping trades (Welch's t);
2. against the family — the bar is the 95th percentile of the **maximum** |t|
   across all 17 cells under circularly-shifted labels;
3. against time — the edge must be present (≥1 SE, same sign) in both halves.

| overlap scope | independent | best \|t\| | family-wise bar | p | result |
|---|---|---|---|---|---|
| per instrument | 907 | 2.17 | 3.29 | 0.695 | NO CONDITIONAL EDGE |
| global | 313 | 1.32 | 3.25 | 1.000 | NO CONDITIONAL EDGE |

The best cell (+0.356 R off-hours, n = 57) sits below the scrambled-label null
mean of 2.45 — exactly what the search produces from noise. Conditioning a signal
with no base edge did not create one.

Two plan variables remain unlabelled: event proximity (no historical calendar)
and GEX regime (no historical options). Labelling past trades from today's feeds
would be look-ahead with extra steps.

## 2.3 Task board

| # | task | status | notes |
|---|---|---|---|
| 5.1 | Volatility-targeted sizing | ✅ | measured — **do not enable** (§2.1) |
| 5.2 | HTF regime layer | ✅ | no look-ahead: only bars CLOSED by the decision time |
| 5.3 | Conditional-expectancy study | ✅ | run on live data — no conditional edge (§2.2) |
| 5.4 | VWAP + std-dev bands as a shared feature | 🟡 | rolling VWAP ✅; session-anchored VWAP not yet |
| 5.5 | Volume profile | 🟡 | composite rolling profile + value area ✅; developing profile not yet |
| 5.6 | Order-flow features | 🟡 | tick-volume proxy only; true CVD needs aggressor-flagged tape |
| 5.7 | GEX / gamma-flip levels | ⬜ | needs historical options data |
| 5.8 | Real confluence score for SpikeFade | ⬜ | deprioritised — see §3 |
| 5.9 | Purged + embargoed CV | ✅ | `significance.purged_kfold_splits`; research/16 and /18 not yet re-scored with it |
| 5.10 | Deflated Sharpe / PBO | ✅ | plus the small-family floor fix |
| 5.11 | Unified $350 comparison report | ⬜ | must carry capital adequacy (49% of SpikeFade signals unexpressible at $350) |

---

# §3 — What changes because of the results

The plan's own stop rule (§4 below) said: if no conditioning cell survives, the
conditioning adds nothing and the correct output is vol-targeted sizing alone.
**Both halves of that have now been measured, and neither paid.** So:

- **Stop building conditioning features (5.4–5.8) on top of signals that have no
  base edge.** A feature only earns its keep by changing the expectancy of
  something; there is currently nothing for it to change.
- **The one open question worth money is 3.14** — whether an LLM reading the
  full context forecasts direction better than the surrogate distribution. It
  costs ~$2 to answer on post-cutoff data, and the honest prior is that it fails.
- **If 3.14 passes**, 5.3's study becomes the next step for it: condition the
  LLM's forecasts on regime, with the same three-way control.
- **If 3.14 fails**, Phases 3 and 5 have delivered their real output — a
  measurement stack that would have caught every inflated result this project
  has previously acted on — and the next move is a different hypothesis, not
  more features.

```
now          🔴 3.14 post-cutoff LLM test         ← blocked on ~$2 API credit
if PASS      3.13 registry adapter → 3.10 calendar forward test (8 weeks)
             5.3 study on the LLM's own forecasts
if FAIL      stop Phase 3; write up; choose the next hypothesis
either way   5.11 unified $350 report, re-scoring research/16 & /18 with 5.9
```

**Hard gates, unchanged:**

1. **No LLM capital before 3.14 passes.**
2. **Nothing from 5.3 ships without 5.9 and 5.10.**
3. **No size increase anywhere on an overlap-naive t-statistic.**
4. **Re-derive, do not cite** numbers from `research/16` or `research/18`.

---

# §4 — What would make me abandon a phase

Stated in advance, so the decision is not made under sunk cost:

- **Phase 3** — if the post-cutoff test lands inside its surrogate distribution,
  the agent has no edge and the honest move is to say so and stop paying for
  tokens.
- **Phase 5** — if no conditioning cell survives purged CV plus deflation, the
  conditioning adds nothing. *As of 2026-09-11 this has triggered for momentum
  on the 8 live instruments; see §3.*
