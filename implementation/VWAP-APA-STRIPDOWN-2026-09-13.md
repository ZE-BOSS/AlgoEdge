# VWAP and APA, stripped to their confluences — 13 Sep 2026

You asked for the ORB treatment on both: take every confluence apart, optimise each one, test across all markets, and prove it over 2 years / 1 year / 6 months for forward testing. No restrictions, no markets excluded.

That is what was done. The answer is not the one either of us wanted, and it is worth more than a tuned number would have been.

---

## 1. The short answer

- **VWAP has no profitable configuration on any of 26 markets.** Across 460,000+ candidate setups, every single confluence leaves expectancy negative, and two of the four the live strategy enforces make it *worse*. Best case: -0.039R per trade.
- **APA has no profitable configuration either.** Across 10,429 structural patterns on all 26 markets, every target from 1:1 to 1:10 and both scale-outs are negative in all three windows — and the most recent six months are the worst of the three.
- **1:5 / 1:7 / 1:10 is the wrong question.** The wider the target, the worse APA does — monotonically. 1:10 is its *worst* exit in all three windows.
- **One finding is strong enough to act on:** APA's retest requirement costs **-0.56R per trade** (t = -27, helped in **0 of 26 markets**). It is off by default in your engine; it must stay off.
- **Two measurement bugs were found and fixed** that had been inflating results — including results for ORB. Details in §5.

Neither strategy gets new parameters wired into the backend, because there is nothing measured to wire. Recommending settings here would be inventing them.

---

## 2. How each was stripped

Both strategies were rebuilt in the analytics layer, faithful to the live engines (tests hold them to the engines' own functions), then run in their most permissive form — every optional confluence **recorded as a boolean rather than enforced** — so any combination could be evaluated afterwards by filtering the same candidate list. That makes "what does this one gate add" exact instead of a separate backtest per gate per market per window.

- **VWAP** (`backend/analytics/vwap_research.py`): same 09:30-ET session anchor, same reset-per-session cumulative VWAP and volume-weighted running σ, same slope reference, momentum definition, volume ratio, convergence test, and the same stop construction (trigger extreme vs ±1σ for pullbacks, ±3σ for reversions) with the engine's pip/spread floors. 11 confluences, 11 exit variants, both setups.
- **APA** (`backend/analytics/apa_research.py`): same fractal swings (minor for shoulders, major for BOS validity), same most-recent-triplet H&S with ATR symmetry tolerance, same neckline, same BOS-on-a-major-level test, same invalidation zone from shoulder bodies, same tight-levels stop branch. Structure on M15, entries on M5, exactly as the engine splits them. 10 confluences, 1:1 → 1:10 plus two scale-outs.
- **Selection discipline:** configurations chosen on 2024-03 → 2026-03 only. The 6-month column (2026-03 → 2026-09) was never selected on.
- **Verdicts are pooled across markets**, because a per-market pick on 40–140 trades is noise.

---

## 3. VWAP: the numbers

Pooled pullback arm, 26 markets, exit 1:2. Baseline (no gates) is -0.032R per trade over 2 years:

| Confluence | Admits | With gate | Without | Edge added | Markets helped (6m) |
|---|---|---|---|---|---|
| momentum_aligned | 91,471 | -0.023R | -0.032R | +0.009R | 18 of 25 |
| slope_aligned | 295,142 | -0.029R | -0.032R | +0.004R | 22 of 25 |
| volume_ok | 94,784 | -0.031R | -0.032R | +0.001R | 13 of 25 |
| **converging** | 175,757 | -0.037R | -0.032R | **-0.005R** | 10 of 25 |
| **in_session_window** | 53,124 | -0.044R | -0.032R | **-0.012R** | 16 of 25 |
| **inside_1sigma** | 158,006 | -0.048R | -0.032R | **-0.016R** | **2 of 25** |

Every exit variant, pooled, all three windows:

| Exit | 2 years | 1 year | 6 months |
|---|---|---|---|
| +2σ band (the strategy's own target) | -0.052R | -0.057R | -0.057R |
| VWAP (reversion target) | -0.076R | -0.087R | -0.092R |
| 1:1 | -0.073R | -0.081R | -0.076R |
| 1:2 | -0.063R | -0.070R | -0.065R |
| 1:3 | -0.060R | -0.065R | -0.059R |
| 1:4 | -0.059R | -0.066R | -0.059R |
| 1:2, no session close | -0.108R | -0.111R | -0.117R |
| +2σ with break-even | -0.053R | -0.060R | -0.055R |

t-statistics run from -3 to -25. These are not small samples.

**What it means:** `inside_1sigma` and `converging` — two of the four gates VWAP_v1 actually enforces — are actively harmful. `slope_aligned` and `momentum_aligned` help in direction (22 and 18 of 25 markets) but by +0.004R and +0.009R against a -0.032R baseline: they shrink a loss, they do not make an edge. `beyond_2sigma` helped **0 of 25** markets, confirming your own docstring that the reversion setup is unreachable in practice.

The per-market winners are artefacts of searching: EURAUD +0.212R, XAUUSD +0.115R — and gold's own 6-month window already flipped to -0.084R.

**Recommendation: stop allocating to VWAP.** Not "tune it differently" — the framework costs 0.03–0.06R per trade on FX, metals, crypto and indices alike, and no combination of its own confluences crosses zero.

---

## 4. APA: the numbers

Pooled, **all 26 markets, 10,429 patterns**, exit 1:3. Baseline -0.047R over 2 years:

| Confluence | Admits | With gate | Blocked trades | Edge added | Markets helped (6m) |
|---|---|---|---|---|---|
| **retest_occurred** | 2,646 | **-0.605R** (t -27) | **+0.227R** | **-0.557R** | **0 of 26** |
| trend_align | 4,399 | -0.018R | -0.083R | +0.029R | 16 of 26 |
| symmetry_tight | 3,828 | -0.021R | -0.070R | +0.026R | 15 of 26 |
| tight_levels | 2,323 | -0.040R | -0.050R | +0.008R | 10 of 26 |
| session_ok | 3,911 | -0.041R | -0.053R | +0.006R | 13 of 26 |
| bos_decisive | — | — | — | -0.004R | 13 of 26 |
| entry_near_neckline | — | — | — | -0.007R | 11 of 26 |
| neckline_precise | — | — | — | +0.005R | 7 of 26 |
| zone_rejected | 10–39 | -0.287R to -0.739R | — | strongly negative | 2 of 8 |

Over the 6-month window the two best gates strengthen slightly (trend_align +0.048R, symmetry_tight +0.036R) but from a worse baseline (-0.102R), so neither lifts the population above zero.

The R:R ladder — your actual question:

| Exit | 2 years | 1 year | 6 months |
|---|---|---|---|
| 1:1 | -0.031R | -0.058R | -0.066R |
| 1:2 | -0.024R | -0.039R | -0.076R |
| 1:3 | -0.040R | -0.041R | -0.084R |
| 1:5 | -0.043R | -0.051R | -0.106R |
| 1:7 | -0.044R | -0.047R | -0.093R |
| **1:10** | **-0.044R** | **-0.044R** | **-0.102R** |
| scale-out ⅓ at 2R, run to 5R | -0.030R | -0.039R | -0.086R |
| scale-out ⅓ at 2R, run to 10R | -0.025R | -0.024R | -0.074R |

Every target is negative in every window, and the most recent six months are the worst of the three — the opposite of what a strategy you could forward-test would look like.

**What it means:**

1. **The retest gate is the headline.** Patterns that retested the invalidation zone returned **-0.605R**; the ones it rejects returned **+0.227R**. On 2,646 retested patterns, **t = -27**, and it helped in **zero of 26 markets**. Your engine's docstring reached this from one XAUUSD window; it now holds across every market and 2.5 years. Keep `require_retest=False` permanently.
2. **`zone_rejected` cannot be used at all** — 10 to 39 occurrences per market group in 2.5 years. Not weak: unmeasurable.
3. **Wider targets are worse.** 1:2 → 1:10 degrades, and the scale-out is merely the least bad. Nothing crosses zero.
4. **The two gates that genuinely help are `trend_align` (+0.029R, 16 of 26 markets) and `symmetry_tight` (+0.026R, 15 of 26)** — consistent in direction, but against a -0.047R baseline they only halve the loss. `session_ok`, which the strategy enforces, adds +0.006R.
5. **Every per-market pick disagreed with every other** — 1:7 on GBPJPY, 1:10 on USDCAD, 1:1 on EURUSD, scale-out on BTCUSD, `bare core` on gold and ETHUSD — and the five market-group pools contradict each other on every gate except the retest (batch 1: `tight_levels` +0.062R; batch 3: **-0.110R**; batch 4: `session_ok` **-0.027R** where batches 2 and 3 had +0.057R). Signature of selection noise, measured five independent times.

**The one market worth a second look:** US Tech 100 alone was strongly positive at high R:R (1:10 = +0.238R over 2 years, +0.583R over 6 months, 162 patterns). One market out of 21, chosen after the fact, is a hypothesis — not a result. It needs its own pre-registered test before any money goes near it.

---

## 5. Two measurement bugs found (they also affect past results)

| Bug | Effect | Status |
|---|---|---|
| **Biased session sampling.** The permissive candidate list kept the first 12 per session; the VWAP anchor is 09:30 ET and the tradeable window opens at 10:30, so the quota filled before trading was allowed. The pullback arm measured "in_session_window admits **0** of 7,620". | The entire pullback study was meaningless on the first pass | Fixed: candidates are now spaced in time, never truncated (`min_bars_between_candidates`) |
| **Phantom 0R trades.** Candidates whose structural target already sat behind price were counted as 0R trades. Markets came back reporting ~2,075 "trades" at ±0.00xR with meaningless profit factors. | Dragged every average toward zero and inflated trade counts | Fixed: `took_trade()` excludes them everywhere, including the account simulator |
| **Degenerate stops** (measured earlier the same day): a structural stop taken when σ is tiny, or entry sitting on the ±3σ band, gave near-zero denominators — single trades reporting **-6,566R** and **+30R**. | Any aggregate was dominated by division artefacts | Fixed: the engine's own 4× spread floor plus an ATR-relative discard, in both research modules |

All three are now covered by tests (`tests/test_vwap_research.py`, `tests/test_apa_research.py`).

---

## 6. What this leaves you with

| Strategy | Verdict | Action |
|---|---|---|
| **ORB_v1** (GBPJPY, London 60m, 1:3) | The only setup that survived walk-forward, an unseen 2022–23 holdout, and tick replay | Forward-test it; it is already wired and shipped |
| **VWAP_v1** | No configuration crosses zero on 26 markets | Stop allocating. Keep the code; do not fund it |
| **APA_v1** | No target or gate combination crosses zero on 26 markets | Stop allocating. Never enable `require_retest` |
| DriftJumpAlpha / BoomDriftJump | Unchanged, as you asked | Keep sizes small |

**Why this is worth more than a tuned number:** you asked for optimisation to the max. The optimisation was run — 11 confluences × 11 exits × 26 markets for VWAP, 10 × 8 × 26 for APA, all chosen out of sample — and the honest output is that neither strategy has an edge to optimise. Wiring a "best" configuration from tables where every row is negative would have handed you a live system guaranteed to lose, with a backtest that looked plausible because it was selected on the same data it was measured on.

---

## 7. TODO

- [ ] Pre-register a US Tech 100 APA test at 1:10 (the one positive market) — fixed rules, chosen window, tick replay — before any allocation.
- [ ] Remove VWAP and APA slots from the live profile, or set them disabled, once you have deployed.
- [ ] ORB forward test continues per `STRATEGY-OPTIMIZATION-2026-09-12.md` §11.
- [ ] The engine fixes from 2026-09-12 still stand: per-bar spread, slippage calibration, margin fallback currency, confluence risk tiers.

## 8. Files

- `backend/analytics/vwap_research.py`, `backend/analytics/apa_research.py` — the strip-downs.
- `scripts/run_vwap_research.py`, `scripts/run_apa_research.py` — per-market ablation and walk-forward.
- `scripts/run_confluence_pool.py` — the cross-market verdict (what this report is built on).
- `scripts/run_strategy_book_sims.py` — dollars for any configuration that ever earns them.
- Data: `data/vwap_research/`, `data/apa_research/`.
- Tests: `tests/test_vwap_research.py`, `tests/test_apa_research.py` (22 tests, all passing).
