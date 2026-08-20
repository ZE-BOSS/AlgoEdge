# Strategy Parameter Audit — Cost-Realism Pass

**Date:** 2026-08-20
**Scope:** all 7 strategy parameter sets + global `RiskParams`
**Files changed:** `backend/strategies/*/params.py` (5 files), `backend/core/config_schema.py`,
and revision notes in `docs/RiskManagement_Spec.md`, `docs/apa_strategy_implementation_plan.md`,
`docs/vwap_strategy_implementation_plan.md`, `docs/strategy-1-htf-fvg-flip.md`,
`docs/strategy-2-bias-keylevel-ifvg.md`, `docs/strategy-3-nyopen-break-retest.md`.
**Out of scope (not edited):** all `engine.py` files, `backtester/`, `risk/position_sizer.py`,
`risk/compounding.py`, `risk/broker_costs.py`. Engine defects found during the audit are
catalogued in §10 for hand-back.

---

## 1. The cost model every number below is derived from

Nothing in this audit is a preference. Every changed default follows from one arithmetic
fact, so it is worth stating precisely once.

**USDCHF round-trip friction, per unit traded:**

| Component | Cost |
|---|---|
| Spread | 2.0 pips |
| Slippage | 0.4 pips |
| Commission ($6/lot, $10/pip on a standard lot) | 0.6 pips |
| **Total** | **≈ 3.0 pips** |

Friction is a **fixed subtraction from every trade**, win or lose. Expressed as a fraction
of R, it depends entirely on the stop distance:

| Stop distance | Friction as % of R | Net win at 1R | Net loss at 1R | Break-even hit rate |
|---|---|---|---|---|
| 3.4 pips *(observed VWAP median)* | 88% | 0.12R | 1.88R | **94.0%** |
| 3.47 pips *(observed APA median)* | 86% | 0.14R | 1.86R | **93.0%** |
| 8 pips | 38% | 0.62R | 1.38R | **69.0%** |
| 12 pips | 25% | 0.75R | 1.25R | **62.5%** |
| 15 pips | 20% | 0.80R | 1.20R | **60.0%** |

Two conclusions drive everything that follows:

1. **A stop must be several multiples of friction.** At a 3.5-pip stop the strategy needs a
   93% hit rate to break even at 1R. No self-reported claim in these docs (64–75%) comes
   remotely close. **12 pips (≈4× friction, ≈0.25R cost) is the working FX-major floor**,
   with 8 pips as an absolute minimum for genuine scalpers that cannot amortise more.
2. **TP1 at 1R is not defensible.** Even at a healthy 12-pip stop, a 1R first target demands
   62.5% just to break even. Moving TP1 to **1.5R** makes net win = net loss = 1.25R, i.e.
   a symmetric 50% requirement. That is the honest minimum.

**Instrument-scaling note.** All pip floors are applied through `get_pip_size()`, which makes
them roughly instrument-class neutral: 12 "pips" is 12 index points on NAS100/UK100
(spread 1–2 pts), $1.20 on XAUUSD (spread ~$0.25), 0.12 on JPY pairs. All land in a
5–10× spread band. This is why a single constant is workable across the book.

**Sample-size caveat, stated once and applying to all seven strategies.** The forensic
sample is 43 trades for APA and 28 for VWAP, on one symbol, over 7 months. That is far too
small to infer an edge in either direction. **Every change below is justified by cost
arithmetic — which is deterministic — and not by measured performance, which is not.**
Nothing here should be read as "these parameters will make the strategies profitable."
It should be read as: the previous parameters made profitability *arithmetically impossible*,
and these ones do not.

---

## 2. Strategy APA — `backend/strategies/strategy_apa/params.py`

**Docs:** `apa_strategy_implementation_plan.md`, `SMC_Strategy_Spec.md`
**Forensic status:** worst offender. Median stop 3.47 pips, min 0.45 pips. 30% of legs
exited within one bar. Reached 40 lots on $25k (~160× leverage). Expectancy **−0.367R**.
Under real costs **+$6,283 → −$8,597**.

| Param | Old | New | Doc says | Rationale |
|---|---|---|---|---|
| `structure_timeframe` | M15 | M15 | 15-min (range 15m–1H) | Agrees. Unchanged. |
| `entry_timeframe` | M5 | M5 | 5-min | Agrees. Unchanged. |
| `minor_fractal_m` | 3 | 3 | 3 (range 2–5) | Agrees. Unchanged. |
| `major_fractal_m` | 8 | 8 | 8 (range 6–15) | Agrees. Unchanged. |
| `shoulder_symmetry_tolerance_atr` | 0.3 | 0.3 | 0.3×ATR | Agrees. Unchanged. |
| `tight_level_threshold_atr` | 0.2 | **0.35** | 0.2×ATR (range 0.1–0.4) | Deviation *within* the doc's range. The tight-levels branch selects the **Head** wick, always further from entry than the Right Shoulder wick — it is the **wider-stop** branch. Raising the threshold biases toward it. |
| `sl_buffer_atr` | 0.05 | 0.05 | 0.05×ATR (range 0–0.15) | Agrees. Left at doc value so the deviation is isolated to one field. |
| `sl_buffer_atr_mult` | **0.0** | **0.5** | not in doc | 0.05×ATR ≈ **0.35 pips** on USDCHF M15 — under a quarter of the spread. 0.5×ATR adds ~3–4 pips ≈ 1.5–2× spread. Proportional cushion. **Engine reads this.** |
| `min_sl_pips` | *(new)* | **12.0** | not in doc | Absolute backstop. ≈4× friction → cost ~0.25R instead of ~0.86R. ⚠ engine does not read. |
| `min_sl_atr_mult` | *(new)* | **1.0** | not in doc | Volatility-relative backstop for high-vol regimes where 12 pips is itself noise. Larger floor wins. ⚠ engine does not read. |
| `invalidation_zone_source` | right_shoulder | right_shoulder | Right Shoulder body | Agrees. Unchanged. |
| `session_filter_enabled` | **False** | **True** | doc specifies none | Deliberate deviation — see below. |
| `session_start` | 07:00 | 07:00 | — | London open (UTC; `engine.py:68` converts to UTC). |
| `session_cutoff` | **20:00** | **16:00** | — | 07:00–16:00 UTC = London open through the London/NY overlap, where USDCHF depth is best. 16:00–20:00 is NY-only flow on a European cross. |
| `atr_lookback` | 14 | 14 | — | Unchanged. |

### Doc-vs-implementation discrepancies

- **§5 has no cost model.** It is correct as geometry and silent on economics. §6 bounds the
  Invalidation Zone by shoulder **bodies** while §5 places the stop at the shoulder **wick** —
  the retest entry and the stop can be a fraction of a pip apart. This is not a bug in the
  implementation of the doc; it is a gap *in the doc*, and it is the root cause of the
  0.45-pip stops.
- **No session guidance.** The doc was demonstrated on a chart, not a 24/5 FX feed. Left off,
  APA fires through the Asian session where USDCHF spread widens to 3–5 pips and the M15
  range collapses to 2–3 pips — exactly the regime that produced the sub-spread stops.
  This is the one deviation I would flag hardest for review, because it is a genuine
  departure rather than a range-internal tweak.
- **§6's TP philosophy conflict** (forward-looking structural target vs. fixed-R grid) remains
  unresolved in code; the doc's default option (keep the R-grid) is what ships. The doc's
  suggested diagnostic — logging the next untested structural swing as a reference field —
  is still not implemented and would be cheap and genuinely informative.

### Expected behavioural change

- **Stop distribution shifts from a 3.47-pip median to ~12–14 pips** (floor binding on the
  majority of signals; ~3.5× wider).
- **Position sizes shrink by roughly the same factor (~3.5×)**, since size = risk$/stop.
  Combined with `risk_per_trade_pct` 1.0 → 0.5, typical lot size drops **~7×**. The 40-lot
  case becomes ~5–6 lots — inside broker limits, no retcode 10019.
- **Trade count: expect a 30–45% drop.** The session filter alone removes the Asian session
  (~40% of the clock, but a smaller share of qualifying signals since structure is quieter
  there); the wider stop does not itself reduce signal count but the SL-on-wrong-side
  rejection at `engine.py:284` will fire more often once floors are wired.
- **Average hold lengthens substantially.** The 30% of legs that died within one bar were
  dying to spread, not to thesis invalidation; most should now survive into a multi-bar
  outcome. Expect median hold to roughly double.
- **Dollar P&L will very likely go DOWN and expectancy UP.** This is the important and
  counter-intuitive prediction: the previous positive dollar P&L was manufactured by the
  size asymmetry that tight stops created (winners sized 1.63× losers). Removing the
  artifact removes the phantom profit. **Judge the result on expectancy in R, not on the
  P&L line.** A move from −0.367R toward 0R is a genuine improvement even if the dollar
  figure gets worse.

---

## 3. Strategy VWAP — `backend/strategies/strategy_vwap/params.py`

**Doc:** `vwap_strategy_implementation_plan.md`
**Forensic status:** 28 trades in 7 months. `sl_points=80 × get_pip_size()` produced an
80-pip stop on USDCHF, turning a 10-minute M5 scalper into a **~6.9-day** swing hold.

### 3.1 The `sl_points` conflict — definitive resolution

Three mutually incompatible conventions were in circulation:

| # | Convention | Source | USDCHF result | Verdict |
|---|---|---|---|---|
| A | `sl_points × pip_size` (0.0001) | `engine.py:244` — what actually shipped | **80.0 pips** | **Wrong.** Absurd on M5; caused the 6.9-day holds. |
| B | `sl_points × point_size` (0.00001) | frontend hint, `Backtester.jsx:112`: "170 for USDCHF = 17 pips" | **8.0 pips** | Right neighbourhood, wrong mechanism. |
| C | ATR-multiple for non-index | doc §5 lines 92–95 | 3.4 pips at the old k=1.0 | **Right method, uncalibrated k.** |

**Chosen: C, with a calibrated k and an absolute floor. Implemented via a new explicit
`sl_method` selector defaulting to `"auto"`.**

**Why C over B.** B lands almost exactly where the correct answer lands for USDCHF *today* —
which is useful corroboration that ~8–10 pips is the right neighbourhood, and I take it as
such. But B is a fixed constant: it does not adapt across instruments (8 "pips" is
meaningless on NQ) and it does not adapt across volatility regimes. Adapting to the
instrument is precisely what doc §5 exists to demand — *"it won't transfer literally to
other instruments (a Deriv synthetic or a forex pair doesn't share NQ's point scale)."*
B is a coincidence, not a method. **I concur with the coordinator's recommendation.**

**Why k = 3.0 and not 1.0.** The doc never said 1.0; it said *calibrate k such that
k × ATR ≈ 80 points under NQ's own volatility*. 1.0 was an unexamined placeholder.

- **Which ATR the engine actually uses — stated explicitly, as requested:** `engine.py:235`
  calls `calculate_atr(candles)` where `candles` are the **entry-timeframe M5 bars**, *not*
  the 15-minute anchor bars. This is decisive for the calibration.
- NQ ATR(M15) at the source's filming era ≈ 45 points → k ≈ **1.8 on M15**.
- M5 ATR ≈ 1/1.7 of M15 ATR → the equivalent **M5 k ≈ 3.0**.
- The two calibrations agree. **3.0 is the one that matches the code as written.**

Validated against the measured USDCHF ATR(M5) distances (these are true 1.0×ATR distances,
since the old `sl_atr_multiplier=1.0` won over the broken fixed path):

| Percentile | ATR(M5) | × k=3.0 | vs 2.0-pip spread |
|---|---|---|---|
| min | 1.64 | 4.9 pips | 2.5× — **floor catches this** |
| p25 | 2.83 | 8.5 pips | 4.2× |
| **median** | **3.40** | **10.2 pips** | **5.1× — target zone** |
| p75 | 4.22 | 12.7 pips | 6.3× |
| max | 9.21 | 27.6 pips | 13.8× |

**Why the ATR multiple alone is insufficient.** Even at k=3.0 the low-volatility tail gives
a 4.9-pip stop — only ~2.5× spread. `min_sl_pips = 8.0` (= 4× the USDCHF spread) and
`min_sl_spread_mult = 4.0` are the guard that actually prevents the failure mode.
`min_sl_spread_mult` is the instrument-neutral form and should take precedence wherever a
live or modelled spread is available, because it also adapts to news-time spread blowouts,
which a fixed pip figure cannot.

**If the engine is ever changed to feed M15 bars to `calculate_atr()`, divide k by ~1.7
(→ ≈1.75).** This is recorded in the params docstring and in the doc.

### 3.2 Parameter table

| Param | Old | New | Doc says | Rationale |
|---|---|---|---|---|
| `sl_method` | *(new)* | **"auto"** | §5 prose only | Makes §5's instrument split machine-readable. `auto` → index CFDs/futures use fixed points; everything else uses ATR. ⚠ engine does not read. |
| `sl_points` | 80.0 | 80.0 | 80 pts on NQ | Value agrees; **meaning corrected** — native index points, index class only. |
| `sl_atr_multiplier` | **1.0** | **3.0** | "calibrate k" | See §3.1. Median stop 3.4 → 10.2 pips. |
| `min_sl_pips` | *(new)* | **8.0** | not in doc | 4× USDCHF spread. Lower than APA's 12 deliberately: VWAP is an explicit scalper with a 15:55 hard close and cannot amortise a 12-pip stop. ⚠ engine does not read. |
| `min_sl_spread_mult` | *(new)* | **4.0** | not in doc | Instrument-neutral form of the above; adapts to spread blowouts. ⚠ engine does not read. |
| `target_rr` | *(new)* | **2.0** | §6 conflict, unresolved | Resolves §6 — see §3.3. ⚠ engine hard-codes 1R. |
| `vwap_anchor_minutes` | 15 | 15 | 15-min | Agrees. |
| `entry_timeframe` | M5 | M5 | 5-min | Agrees. |
| `momentum_lookback_bars` | 4 | 4 | 4×15-min = 1hr | Agrees. |
| `momentum_threshold_pct` | 0.1 | **0.1** | ±0.1% | Agrees — **deliberately not tightened.** 0.15 was tempting to filter marginal setups, but at 28 trades over 7 months the binding constraint is already sample size, not signal quality. Respecting the doc here. |
| `session_open` / `session_exclude_end` | 09:30 / 10:30 | unchanged | 9:30–10:30 ET | Agrees. Engine converts to America/New_York (`engine.py:71`). |
| `entry_cutoff` | 15:30 | unchanged | 3:30 PM ET | Agrees. |
| `hard_close` | 15:55 | unchanged | 3:55 PM ET | Agrees. |
| `max_trades_per_day` | 4 | 4 | 4 | Agrees. |
| `max_losses_per_day` | 2 | 2 | 2 | Agrees. |
| `drawdown_kill_pct` | 10.0 | 10.0 | "5–15%, pick manually" | In range. Unchanged. |

### 3.3 The §6 TP conflict — resolved in favour of the R-grid

Doc §6 flags a real conflict and offers two honest options. **Option two is taken: use the
R-grid, treat the 64–65% win-rate claim as void.**

The doc's own arithmetic at line 111 settles it. At 64.5% win rate, the source's long-side
expectancy is `0.645×40 − 0.355×80 ≈ −2.6 points` — **negative before costs**. A target rule
that is negative-expectancy before costs cannot be rescued by better cost modelling, so
implementing the source's 40pt/50pt fixed targets would be implementing a known-losing exit.
This is a doc being commendably honest about its own source, and the defaults now reflect it.

`target_rr = 2.0` rather than the engine's hard-coded 1.0R: with a ~10-pip stop and ~3.0 pips
friction (≈0.30R), a 1R target nets 0.70R against a 1.30R loss → **65% break-even hit rate**,
i.e. the entire claimed edge consumed with zero margin. At 2R: 1.70R against 1.30R → **43%**.

### Expected behavioural change

- **Stop distribution: 80 pips → ~8–13 pips** (median ~10.2). This is the single largest
  correction in the audit.
- **Average hold collapses from ~6.9 days to intraday** — which is what the strategy *is*.
  The 15:55 hard close becomes the binding exit for stragglers rather than a formality.
- **Trade count roughly unchanged** (entry logic untouched), but each trade now completes
  within its session, so overlapping/blocked signals from the max-1-position rule should
  fall — a modest *increase* in realised trades is plausible.
- **Overnight gap risk eliminated.** A 6.9-day hold on an intraday scalper was carrying
  weekend gap exposure the strategy never priced.
- Sample remains 28 trades. **This strategy is not evaluable yet.** Treat post-change results
  as a smoke test that the mechanics are sane, not as evidence about edge.

---

## 4. Strategy CRT — `CRTParams` in `backend/core/config_schema.py`

**Doc:** `CRT_Strategy_Spec.md`
**Status: fully doc-compliant. No defaults changed.** This is the only strategy that shipped
with a cost floor, and it is correspondingly absent from the forensic list of degenerate-stop
offenders. Its `min_sl_pips`/`sl_atr_mult` pattern is what has now been replicated across the
book — and unlike the copies, **CRT's are actually read by its engine**
(`strategy_three_crt/engine.py:222-250`).

| Param | Old | New | Doc says | Rationale |
|---|---|---|---|---|
| `htf_timeframe` | H1 | H1 | 1H (§2) | Agrees. |
| `ltf_timeframe` | M5 | M5 | 5M (§2) | Agrees. |
| `target_r_multiple` | 1.5 | **1.5 (held)** | 1.5, range 1.5–2.0 (§2/§6) | Agrees — and see the inversion warning below. |
| `max_trades_per_session` | 1 | 1 | 1 (§2/§10) | Agrees. |
| `session_start` | 09:30 | 09:30 | 9:30 AM ET (§7) | Agrees. |
| `session_cutoff` | 12:00 | 12:00 | ~12:00–13:00 ET (§7) | Agrees (conservative end). |
| `bypass_session_synthetics` | True | True | §8 — synthetics have no session anchor | Agrees. |
| `min_sl_pips` | 15.0 | 15.0 | not in doc | ≈5× friction. Above-spec guard; retained. |
| `sl_atr_mult` | 1.0 | 1.0 | not in doc | Above-spec guard; retained. |

### ⚠ `target_r_multiple` runs BACKWARDS — the one trap in this file

CRT derives the stop from the target: `sl_distance = tp_distance / target_r_multiple`
(spec §6), where `tp_distance` is fixed by structure (C1's opposite extreme). **Raising
`target_r_multiple` therefore TIGHTENS the stop**, it does not extend the target. At the top
of the doc's range (2.0) the stop is 25% tighter than at 1.5 for an identical setup. Given
that sub-spread stops were the dominant failure mode across the whole book, the correct
default is the **bottom** of the doc's range, not the middle or top. Anyone raising this is
buying a better headline R by moving the stop closer to noise. Now documented inline.

### Architectural discrepancy (reported, not fixable in params)

The entire point of CRT's backward SL derivation is to make the structural TP land at exactly
`target_r_multiple`. But the live/backtest TP ladder is owned by `RiskParams`
(`tp1_rr`/`tp2_rr`/`tp3_rr` = 1.5/3/5), which overrides the strategy's `take_profit`. So the
stop is reverse-engineered to hit a 1.5R target that is then **discarded** and replaced by a
grid whose upper tiers (3R, 5R) sit far beyond C1's extreme — the level the entire setup
thesis says price is travelling to. TP2 and TP3 are therefore structurally unreachable by
CRT's own hypothesis, and with `tp_splits` at 50/30/20 that strands 50% of every CRT position
on targets the strategy does not believe in. **Either CRT should be exempted from the grid,
or its SL should be placed structurally (beyond the C2 sweep wick) and the grid allowed to
own the targets.** This needs a product decision; I have not pre-empted it.

### Expected behavioural change

None — no defaults changed. Included for completeness and because the grid-override issue
above is a live problem regardless.

---

## 5. Strategy 1 / HTF FVG Flip — `strategy_four_htf_fvg_flip/params.py`

**Doc:** `strategy-1-htf-fvg-flip.md`

| Param | Old | New | Doc says | Rationale |
|---|---|---|---|---|
| `htf_timeframe` | H1 | H1 | 1H (alt 4H) | Agrees. |
| `entry_confirmation_tf` | M5 | M5 | 5m | Agrees. |
| `require_unfilled_htf_fvg` | True | True | true | Agrees. |
| `target_rr` | 2.0 | **2.0 (held)** | **1.0** | **Deliberate deviation, now justified quantitatively.** At 1.0R: nets 0.75R vs a 1.25R loss → **62.5%** break-even. Doc self-reports ~75% on 27 unverified trades — no margin. At 2.0R → **41.7%**. The previous inline comment justified 2.0 only by convention ("not changed without product-owner confirmation"); it now has arithmetic behind it. |
| `session_filter_enabled` | **False** | **True** | none given | Doc's own Open Questions: *"consider adding one if false signals cluster outside RTH."* Forensic review found exactly that clustering. |
| `session_start` | **08:00** | **09:30** | — | 08:00–09:30 is pre-market: H1 FVG taps arm the machine there, but M5 inversion confirmations are unreliable on pre-market volume and spreads have not compressed. **Arming is unaffected — only the entry gate moves.** Engine converts to America/New_York (`engine.py:48`). |
| `session_cutoff` | **17:00** | **16:00** | — | 16:00 ET is the US cash close. 16:00–17:00 is after-hours: wider spreads, no institutional flow for an "inversion" to represent. |
| `sl_buffer_atr_mult` | **0.0** | **0.5** | not in doc | Engine sets SL **flush at `m5_swing_point` with no buffer at all** (`engine.py:199`) — exactly on the wick every other participant can see. 0.5×ATR(M5) ≈ 1.5–2 pips. ⚠ **DEAD — engine does not read it.** |
| `min_sl_pips` | *(new)* | **12.0** | not in doc | ≈4× friction. An M5 inversion swing on a quiet hour is routinely 2–4 pips from entry. ⚠ engine does not read. |
| `min_sl_atr_mult` | *(new)* | **1.0** | not in doc | Volatility-relative floor. ⚠ engine does not read. |

### Discrepancies

- **`target_rr` is a genuine doc-vs-code disagreement** (doc 1.0, code 2.0). Resolved in
  favour of the code, with the doc annotated rather than the code reverted.
- **`sl_buffer_atr_mult` is documented in the params file as configurable and is silently
  discarded.** It is accepted by the config schema, surfaced in the Settings UI, persisted
  to the DB, and never read. See §10.
- **`entry * 0.99` fallback stop.** `engine.py:199` falls back to a 1%-of-price stop if
  `m5_swing_point` is missing — on USDCHF that is ~80 pips, on NAS100 ~200 points. A silent
  80× swing in stop distance driven by a missing dict key. See §10.

### Expected behavioural change

- **Trade count: expect a 25–40% drop**, almost entirely from the session filter narrowing
  09:30–16:00 from 08:00–17:00 (a 6.5h window vs 9h, and the removed hours are the thin ones).
- **Stop distance: no change until the engine is wired.** This is the honest position — three
  of the four risk-relevant changes here are currently inert. Once wired, expect the same
  ~3× widening and ~3× size reduction seen in the APA projection.
- Post-wiring: fewer, larger-R trades; longer average hold; lower dollar P&L with higher
  R-expectancy.

---

## 6. Strategy 2 / Bias→KeyLevel→IFVG — `strategy_five_bias_ifvg/params.py`

**Doc:** `strategy-2-bias-keylevel-ifvg.md`

| Param | Old | New | Doc says | Rationale |
|---|---|---|---|---|
| `session_start` / `session_cutoff` | 09:30 / 11:00 | unchanged | 09:30–11:00 ET | Agrees. Already corrected in a prior pass. |
| `max_trades_per_day` | 2 | 2 | 1–2, capped at 2 by the day-stop rule | Agrees. |
| `target_rr` | 2.0 | **2.0 (held)** | range 1.0–3.0 | Midpoint. **The bottom of the doc's range is explicitly rejected**: 1.0R → 62.5% break-even vs the doc's unverified ~70% claim. 2.0R → 41.7%. |
| `sl_buffer_atr_mult` | **0.0** | **0.5** | not in doc | Doc §Step 4 nominates "swing high/low (default — safest for beginners)"; `engine.py:345` implements it **flush, no buffer**. A stop exactly on a visible swing is the most-hunted price in the book. ⚠ **DEAD — engine does not read it.** |
| `min_sl_pips` | *(new)* | **12.0** | not in doc | ≈4× friction. **Most load-bearing here of anywhere:** the strategy is confined to 09:30–11:00 and enters on an M5 body-close *through* an IFVG, so entry and the swing that formed the IFVG are only a few pips apart **by construction**, not by accident. ⚠ engine does not read. |
| `min_sl_atr_mult` | *(new)* | **1.0** | not in doc | Volatility-relative floor. ⚠ engine does not read. |
| `a_plus_confluence_threshold` | **85** | **90** | "clearly A+ setup" (qualitative) | **Closes a silently-broken rule** — see below. |
| `rejection_min_body_atr_mult` | 0.15 | 0.15 | not in doc | Above-spec quality filter. No evidence either way; unchanged rather than tuned on a hunch. |

### The `a_plus_confluence_threshold` finding

The engine emits a **hard-coded `confluence_score` of 85 on every signal** (`engine.py:364`).
With the threshold also at 85, the gate was `85 >= 85` → **every setup qualified as A+**.
The doc's selective rule — *"After 1 loss, only take a second trade if it's a clearly A+
setup at a new key level"* — had silently degraded to *"always take a second trade after a
loss."* That is the opposite of the doc's intent, and it doubles daily loss exposure exactly
when the account is already down.

Raising to 90 makes the gate unsatisfiable until a real scoring function exists, so the
day-stop rule degrades to the doc's **safe** reading: stop after the first loss. Retune to
~85 once `confluence_score` is genuinely computed. This is a params-level workaround for an
engine defect (§10) and should be revisited when that defect is fixed.

### Expected behavioural change

- **Trade count: expect a 30–40% drop**, driven almost entirely by the A+ gate now actually
  gating. Days that previously produced a loss followed by a second trade will now stop at
  one. Effective max trades/day falls from 2 to ~1.2.
- **This should improve the loss-day distribution more than the win-day distribution** —
  it truncates the left tail specifically, which is the point of a day-stop rule.
- Stop distances unchanged until the engine is wired.

---

## 7. Strategy 3 / NY Open Break & Retest — `strategy_six_ny_open_retest/params.py`

**Doc:** `strategy-3-nyopen-break-retest.md`
**Forensic status:** expectancy **−0.006R** (exactly break-even gross, firmly negative net).
Under real costs **+$1,752 → −$7,250**.

| Param | Old | New | Doc says | Rationale |
|---|---|---|---|---|
| `range_window_start` / `_end` | 08:00 / 08:15 | unchanged | 08:00–08:15 ET | Agrees. |
| `earliest_valid_break_time` | 09:30 | unchanged | 09:30 ET | Agrees. |
| `session_end` | 11:00 | unchanged | 11:00 ET | Agrees. |
| `stop_buffer_points` | 5.0 | **5.0 (held)** | 5, "calibrate per market" | Held at doc value. A single constant cannot be right for both NQ points and USDCHF pips; the **ATR term** is asked to carry instrument-scaling instead, which is the scale-free way to do it. |
| `fixed_target_points` | 50.0 | **50.0 (held)** | 50, instrument-specific | Retained as the NQ-native ceiling, but **badly mis-scaled on FX** — see below. Superseded by `target_mode`. |
| `dynamic_target_override` | True | True | enabled | Agrees. Currently the only thing making the fixed target survivable on FX. |
| `sl_buffer_atr_mult` | **0.0** | **1.0** | 0.0 (disabled) | Structural stop = half-range + 5 pts. The 08:00–08:15 USDCHF candle is 6–10 pips, so half-range is 3–5 pips and the whole stop was ~8–10 pips — only 3–4× the ~2.5-pip cost. +1.0×ATR(M5) (~3 pips) → ~11–13 pips = 4–5× cost. **✅ Engine DOES read this** (`engine.py:129-136`) — the only non-CRT floor param that is live. |
| `target_mode` | *(new)* | **"rr"** | not in doc | Scale-free target. ⚠ engine does not read. |
| `target_rr` | *(new)* | **2.0** | not in doc | 1R → 61% break-even at these stops; 2R → 41%. A strategy measured at −0.006R has no margin to spend on a near target. ⚠ engine does not read. |

### The `fixed_target_points` mis-scaling

50 "points" → `× get_pip_size()` → **50 pips** on USDCHF, measured from `range_mid`, inside a
09:30–11:00 window, on a pair whose entire *average daily range* is ~55 pips. It is
effectively unreachable. In the real runs virtually no trade exited at the fixed target;
every exit came from the SL, the `dynamic_target_override` swing, or the session close —
meaning the documented target was decorative and an undocumented fallback was doing the work.

I did not simply lower the constant, because there is no single value that is right on both
NQ and USDCHF, and lowering it would just relocate the same bug. **An R-multiple is the only
target formulation simultaneously correct on NQ, USDCHF and XAUUSD**, so `target_mode="rr"`
is the fix and `fixed_target_points` is demoted to a legacy path.

### Instrument tension in `sl_buffer_atr_mult = 1.0` — stated explicitly

On NQ, ATR(M5) is ~15–20 points, so 1.0× adds 15–20 points to a ~5-point buffer. That is
proportionate to NQ's own noise, but it makes the legacy 50-point fixed target only
~1.1–1.4R. **This is precisely why `target_mode` defaults to `"rr"`.** Do not raise
`sl_buffer_atr_mult` above 1.0 without first confirming the target is R-based.

### Expected behavioural change

- **Stop distance: ~8–10 pips → ~11–13 pips (+30%), effective immediately** — this is the
  one strategy where the SL change is live rather than pending engine work.
- **Position sizes fall ~25%** from the stop change alone, ~62% including the
  `risk_per_trade_pct` halving.
- **Trade count roughly unchanged** — entry logic and session windows are untouched.
- **Win rate should fall slightly, expectancy should rise.** Wider stops convert some
  marginal losers into runners and some scratches into losers; the −0.006R expectancy has
  nowhere to go but up if the cost fraction drops from ~30% of R to ~22%.
- **Once `target_mode` is wired**, expect a *lower* win rate again (2R is harder to reach than
  a near swing) with materially better R per win. Do not evaluate the target change on win
  rate.

---

## 8. Strategy 2 / DriftJumpAlpha — `DriftJumpAlphaParams` in `config_schema.py`

**Doc:** `DriftJumpAlpha_Strategy_Spec_v2.md`
**Status: fully spec-compliant. No defaults changed.** Best-aligned strategy in the book.

| Param | Old | New | Doc says | Rationale |
|---|---|---|---|---|
| `drift_ema_fast` | 20 | 20 | `fast_ema_period: 20` (§1) | Agrees. |
| `drift_ema_slow` | 50 | 50 | `slow_ema_period: 50` (§1) | Agrees. |
| `min_adx_to_trade` | 20 | 20 | `min_adx_to_trade: 20` (§1) | Agrees. |
| `jump_entry_percentile_threshold` | 95.0 | 95.0 | 95.0 (§1/§6) | Agrees. |
| `trade_jumps_enabled` | False | False | §6 "opt-in, hard-gated"; §9 found no edge | Agrees — correctly off. |
| `control_test_passed` | False | False | §8 gate | Agrees. |
| `aggregate_max_lots_per_symbol` | 6.0 | 6.0 | 6.0, "$25k Crash 1000 BloomFunded tier" (§1) | Agrees. **Broker rule, not a tuning knob** — §1: "never inferred". |
| `spike_threshold_pips` | 0.0 | 0.0 | — | Disabled → ATR fallback. Fine. |
| `recovery_target_pips` | 0.0 | 0.0 | — | Disabled. Fine. |
| `max_trades_per_day` | 6 | 6 | 6 (§1) | Agrees. |
| `max_daily_risk_pct` | 4.0 | 4.0 | 4.0 (§1) | Agrees. |
| `max_consecutive_losses` | 4 | 4 | 4 (§1) | Agrees. |
| `cooldown_after_max_losses_hours` | 12 | 12 | 12 (§1) | Agrees. |
| `min_rrr_to_accept_trade` | 1.5 | 1.5 | 1.5 (§1) | Agrees. |

### Why it is exempt from this audit's central problem

DJA trades Deriv synthetics, where §8 measures round-trip spread at ~1,430 points on Crash
1000 — a completely different cost regime — and its stop is an **ATR/structure stop**
(§7: `atr_multiple: 1.5`, `buffer_atr_multiple: 0.2`) rather than a wick-flush structural
stop. It therefore *cannot* degenerate to a sub-spread distance the way APA and the two IFVG
strategies do. It is the existence proof that the pattern works.

Its `aggregate_max_lots_per_symbol` — a hard clamp applied *after* the risk-% formula and
*before* order submission, "never a soft warning" — is also the exact control that would have
caught the 160× APA leverage case. **That is why `RiskParams.max_account_leverage` was added
(§9), generalising DJA's ceiling to the whole book.**

### Spec §1 fields not yet exposed (backlog, deliberately NOT added)

`min_ema_separation_atr_multiple` (0.2), `adx_period` (14),
`pullback_max_distance_atr_multiple` (1.0), `swing_lookback_bars` (5), exit `atr_multiple`
(2.0) with adaptive low/high-vol variants (1.5/2.5), `min_hold_bars_before_trailing` (3),
`stop_loss.buffer_atr_multiple` (0.2), `widen_multiple_at_hard_threshold` (1.5),
`flatten_all_at_percentile`, `max_concurrent_positions` (1), `max_weekly_drawdown_pct` (8.0).

**These were deliberately not added as parameters.** Adding a config field the engine never
reads is exactly the dead-parameter failure this audit found in strategies four and five —
it creates the *appearance* of configurability while silently discarding user input, which
is worse than an honest hard-coded constant. Wire the engine first, then expose.

### Expected behavioural change

None.

---

## 9. Task C — Global risk defaults (`RiskParams`)

**Doc:** `RiskManagement_Spec.md` §6.5 (annotated with a new §6.5a recording these changes).

| Param | Old | New | Doc says | Rationale |
|---|---|---|---|---|
| `risk_per_trade_pct` | **1.0** | **0.5** | 1.0 (range 0.25–3.0) | **(a)** With `max_concurrent_positions=3`, 3 × 1.0% = 3.0% = *exactly* `max_daily_drawdown_pct`. The shipped config was one ordinary adverse hour from halting itself for the day. At 0.5% the same cluster costs 1.5%. **(b)** No demonstrated edge: every strategy is negative under real costs. 0.25–0.50% is standard until an edge survives out-of-sample. |
| `max_risk_hard_cap_pct` | **3.0** | **2.0** | not in this spec | At 3.0 a *single* trade could consume the entire daily drawdown budget — the breaker could only go from "fine" to "halted" in one fill, never gradually. Aligns with DJA spec §1 `max_risk_per_trade_pct: 2.0`, the only explicit per-trade ceiling in the repo. |
| `min_rr` | 3.0 | 3.0 | 3.0 | Agrees — **but semantically weaker than it reads.** `risk/engine.py:298` compares against the **last** active TP's RR (5.0), not TP1, so at these defaults it never rejects anything. It is a ladder-shape sanity check, not a per-trade edge filter. Documented inline so nobody reads a passing signal as "RR ≥ 3 verified". |
| `sizing_method` | fixed_pct | fixed_pct | — | Held. Kelly needs a stable, well-estimated edge; the largest sample here is 43 trades. Kelly on 43 observations sizes the *estimation error*, not the edge. |
| `kelly_fraction` | 0.25 | 0.25 | ¼–½ Kelly (DJA §9) | Agrees. |
| `kelly_lookback_trades` | **50** | **100** | DJA §1 `kelly_recalc_window_trades: 100` | 50 trades gives a win-rate standard error of ~±7pp, producing a wildly unstable Kelly fraction. Aligns with the only spec that states a window. |
| `min_sl_pips` | *(new)* | **10.0** | not in spec | Global backstop. Deliberately *looser* than the per-strategy floors (8–15) so it is a last resort for strategies with no floor of their own, not the primary control. **A signal whose stop cannot be widened without invalidating its own structure should be REJECTED, not silently re-priced.** ⚠ not wired. |
| `max_account_leverage` | *(new)* | **30.0** | not in spec | Total open notional / equity. **30:1 is the ESMA retail FX cap** — the least arguable reference available. Catches the 160× case *independently* of whether the stop bug is fixed, since tight-stop-→-huge-lots has one observable symptom. Enforce as a hard clamp after sizing, per DJA §1: "never a soft warning". ⚠ not wired. |
| `tp_splits` | **[40,35,25]** | **[50,30,20]** | §6.5 [40,35,25]; §2.3 30/25/20/15/10 (spec is self-inconsistent) | Front-load. Costs are a **fixed** subtraction from R, so proportionally most damaging on the nearest target — but the nearest target is also the only one with a hit rate estimable from realistic samples. 50% into TP1 cuts dependence on a 5R tail the data shows is rarely reached, and reduces per-trade variance. **The user's 20/40/40 does the opposite**, putting 80% of size on the two targets with the weakest evidence. |
| `tp1_rr` | **1.0** | **1.5** | §6.5 says 3.0; strategy docs say 1.0 | **The single most consequential change.** At 12-pip stops, friction ≈0.25R. 1.0R → net win 0.75R vs net loss 1.25R → **62.5% break-even hit rate**. 1.5R → 1.25R vs 1.25R → **50.0%**. 1.0R is not a target, it is a demand for 62.5% before the strategy earns anything. |
| `tp2_rr` | 3.0 | 3.0 | §6.5 says 5.0 | Held. §6.5's 3/5/7 grid is unreachable — a 3R *first* target on a 12-pip stop is 36 pips inside a 90-minute window on a pair with a ~55-pip daily range. Strategy docs' 1/3/5 is operative. |
| `tp3_rr` | 5.0 | 5.0 | §6.5 says 7.0 | Held, same reasoning. |
| `tp4_rr` / `tp5_rr` | 10 / 15 | unchanged | off by default | Unchanged. |
| `be_trigger_rr` | **1.0** | **1.5** | 1.0 (range 0.5–2.0) | Now coincides exactly with `tp1_rr`. BE at 1.0R while TP1 sits at 1.5R is **strictly harmful**: it arms the scratch-out one third of the way *before* the first partial, converting 1.25R-net winners into 0R scratches while doing nothing for losers (which never reach 1R). **De-risking must not precede the event that de-risks you.** |
| `be_buffer_pips` | **2.0** | **0.0** | 2.0 (range 0–10) | **Latent P&L bug, not just a bad value** — see below. |
| `be_offset_pips` | 2.0 | 0.0 | legacy alias | Kept consistent with the above. |
| `be_buffer_atr_mult` | **0.0** | **0.10** | not in spec | Scale-aware replacement. `BreakevenManager` applies `max(live_spread, atr_mult×ATR, pips×pip_size)`, so this guarantees the BE stop clears a fraction of current volatility on any instrument. ~0.7 pips on USDCHF M15, ~1.5 NQ points. |
| `max_daily_drawdown_pct` | 3.0 | 3.0 | 3.0 | Agrees. |
| `max_weekly_drawdown_pct` | 6.0 | 6.0 | 6.0 | Agrees. |
| `max_concurrent_positions` | 3 | 3 | 3 (range 1–10) | Agrees — **but only defensible now that risk is 0.5%.** 3 × 0.5% = 1.5% against a 3.0% breaker. At the old 1.0% it was 3.0%: zero headroom. |
| `max_positions_per_symbol` | 1 | 1 | — | Agrees. |
| `max_daily_trades` | 5 | 5 | — | Deprecated fallback; per-strategy caps govern. Unchanged. |
| `trail_activation_rr` | **1.0** | **1.5** | — | Lockstep with `tp1_rr` / `be_trigger_rr`, so there is **one** de-risking event at the first target rather than three staggered ones. At 1.0R the trail could scratch out the position before the partial it was meant to protect had been taken. |
| `atr_trail_multiplier*` | 1.5 | 1.5 | 1.5 (range 0.5–3.0) | Agrees. |
| `trail_pips` | 15.0 | 15.0 | 15.0 (range 5–50) | Agrees. |
| `trail_method_tp2/tp3` | ATR / STRUCTURE | unchanged | ATR / STRUCTURE | Agrees. |
| `max_daily_loss_pct`, `sl_buffer_pips` | 5.0, 5.0 | unchanged | — | Deprecated, kept for DB compat. |

### 9.1 The `be_buffer_pips` finding — probable source of the phantom profitability

This deserves its own treatment because it is a **P&L-fabricating bug**, not a tuning issue.

The audited runs used `be_buffer_pips = 10` against APA stops with a **3.47-pip median**.
When break-even fires:

1. `backtester/engine.py:478` sets `new_sl = entry_price + 10 pips` for a BUY.
2. 10 pips on a 3.47-pip stop is **≈ +2.9R**.
3. BE fires at ~1R — so the new stop is placed roughly **1.9R ABOVE the current market**.
4. On the next bar, `engine.py:434` evaluates `open_p <= stop_loss` → **true** (the stop is
   above the open), classifies it as a **gap**, and fills it.
5. Every surviving sub-position is force-closed at a profit, the bar after TP1.

Net effect: with `tp_splits` at 20/40/40, the 80% of the position that was supposed to run to
3R and 5R — and which in reality would sometimes retrace to a scratch — is instead **always**
booked at roughly +1R. Losers keep their full −1R. **This is a mechanical profit generator,
and it is the most plausible explanation on the table for the observed combination of
"winners sized 1.63× larger than losers" and a positive dollar P&L sitting on top of a
−0.367R expectancy.**

The condition for the bug is `be_buffer_pips × pip_size > current profit distance at BE
trigger`, i.e. **`be_buffer_pips > be_trigger_rr × stop_pips`**. It is dormant whenever stops
are healthy and armed whenever they are not — which is exactly backwards, since it fires
hardest on the strategies whose results are least trustworthy.

`be_buffer_pips = 0.0` moves the stop to exactly entry. Spread cover moves to
`be_buffer_atr_mult` and `BreakevenManager`'s live-spread term, both of which are scale-aware
and cannot land above the market. **The engine-side fix in §10 is still required** — the
current defaults are safe, but a user re-raising `be_buffer_pips` in the UI can resurrect the
artifact with no warning.

### 9.2 Aggregate effect of the risk changes

- Typical position size falls ~2× from `risk_per_trade_pct` alone, and ~7× for APA once
  combined with the wider stop.
- Aggregate at-risk exposure falls from 3.0% to 1.5% of equity, restoring real headroom
  under the 3.0% daily breaker.
- The BE/TP/trail alignment at 1.5R removes two spurious early-exit mechanisms.
- **Expect reported dollar P&L to fall across the board and R-expectancy to rise.**
  Backtests run after this change are not comparable to backtests run before it, and the
  comparison that matters is expectancy in R, not the P&L line.

---

## 10. Engine-level defects found — handed back, NOT fixed (out of scope)

Ordered by severity.

### 10.1 🔴 Break-even buffer can place the stop above the market and book unearned profit
- **Where:** `backtester/engine.py:478`, `backtester/portfolio_engine.py:400`
- **What:** The TP1-hit sibling-BE path reads **only** `be_buffer_pips`. It ignores
  `be_buffer_atr_mult` and the live spread that `BreakevenManager.check_breakeven()`
  correctly combines via `max(live_spread, atr_buffer, pip_buffer)` (`breakeven_manager.py:74-78`).
- **Impact:** See §9.1. Fabricates P&L whenever `be_buffer_pips > be_trigger_rr × stop_pips`.
- **Fix:** Make both backtester paths use the same `max(spread, atr, pips)` computation as
  `BreakevenManager`, **and** clamp the resulting SL to never exceed the current price (BUY)
  or fall below it (SELL). The clamp is the real guard — it makes the bug unreachable
  regardless of configuration.

### 10.2 🔴 No minimum-stop or leverage guard anywhere in sizing
- **Where:** `backend/risk/position_sizer.py` (not edited — owned by another agent this pass)
- **What:** No minimum-SL check. `size = risk$ / stop_distance` is clamped only to the
  broker's `volume_max`, whose fallback default is **100.0 lots** (`position_sizer.py:198`).
- **Impact:** A 0.45-pip stop produced 40 lots on a $25,000 account = $4,000,000 notional =
  ~160× account leverage. MT5 rejects with **retcode 10019 (No money)** — so this is a live
  execution failure, not merely a backtest artifact.
- **Fix:** Enforce the new `RiskParams.min_sl_pips` and `RiskParams.max_account_leverage`
  as hard clamps after sizing and before submission, following the pattern
  `DriftJumpAlpha_Strategy_Spec_v2` §1 already specifies for
  `aggregate_max_lots_per_symbol`. Prefer **rejecting** the signal to silently re-pricing it.

### 10.3 🟠 `sl_buffer_atr_mult` is dead in two engines
- **Where:** `strategy_four_htf_fvg_flip/engine.py`, `strategy_five_bias_ifvg/engine.py`
- **What:** The identifier appears **nowhere** in either file. Both accept it via the config
  schema, surface it in the Settings UI, persist it to the DB — and discard it.
- **Impact:** Users believe they have widened their stops and have not. Silent risk
  misrepresentation, which is worse than an honest hard-coded constant.
- **Fix:** Mirror `strategy_six_ny_open_retest/engine.py:127-136`, which implements it
  correctly.

### 10.4 🟠 Structural stops placed flush on the swing, with no buffer
- **Where:** `strategy_four/engine.py:199`, `strategy_five/engine.py:345`
- **What:** `sl = state.get("m5_swing_point", ...)` — the stop sits exactly on the swing wick,
  with no cushion for spread, slippage, or the ordinary overshoot that follows a swing test.
- **Fix:** `sl_dist = max(abs(entry − m5_swing_point) + sl_buffer_atr_mult × atr, floor)`
  where `floor = max(min_sl_pips × pip_size, min_sl_atr_mult × atr)`.

### 10.5 🟠 `entry * 0.99` fallback stop
- **Where:** `strategy_four/engine.py:199`, `strategy_five/engine.py:345`
- **What:** If `m5_swing_point` is missing from state, the stop silently becomes 1% of price —
  ~80 pips on USDCHF, ~200 points on NAS100.
- **Impact:** A missing dict key causes an ~80× swing in stop distance and therefore in
  position size, with no log line.
- **Fix:** Return `None` (no signal). A missing structural reference means there is no setup,
  not a setup with an arbitrary stop.

### 10.6 🟠 `confluence_score` is a hard-coded constant
- **Where:** `strategy_five/engine.py:364` (85), `strategy_four/engine.py:~216` (88)
- **What:** Emitted identically on every signal, so any threshold comparison against it is
  degenerate.
- **Impact:** Broke the doc's "only A+ setups after a loss" day-stop rule (§6). Worked around
  in params by raising the threshold to 90; the workaround should be reverted once a real
  score exists.
- **Fix:** Compute a genuine 0–100 confluence score, or remove the field and the rule that
  depends on it.

### 10.7 🟡 VWAP ignores `sl_method`, `min_sl_pips`, `min_sl_spread_mult`, `target_rr`
- **Where:** `strategy_vwap/engine.py:244-252`
- **What:** Unconditionally computes `sl_dist = sl_points × get_pip_size(symbol)` then takes
  `max(that, ATR × sl_atr_multiplier)`; TP is hard-coded to 1R at line 257.
- **Fix (precise):**
  ```python
  # 1. Resolve method
  method = self.params.sl_method
  if method == "auto":
      method = "fixed_points" if _is_index_or_futures(symbol) else "atr_multiple"

  # 2. Base distance
  if method == "fixed_points":
      sl_dist = self.params.sl_points * get_pip_size(symbol)   # pip_size == 1.0 for indices
  else:
      sl_dist = atr * self.params.sl_atr_multiplier            # atr is ATR(M5) here — see below

  # 3. Absolute floors (this is the guard that matters)
  floor = self.params.min_sl_pips * get_pip_size(symbol)
  if self.params.min_sl_spread_mult > 0 and current_spread > 0:
      floor = max(floor, self.params.min_sl_spread_mult * current_spread)
  sl_dist = max(sl_dist, floor)

  # 4. Target
  tp = entry + sl_dist * self.params.target_rr   # (mirror for SELL)
  ```
  `_is_index_or_futures()` can reuse the `indices` list already in
  `position_sizer.get_pip_size()` (lines 99–100), plus NQ/MNQ/ES/MES.
- **⚠ Calibration coupling:** `sl_atr_multiplier = 3.0` is calibrated against **ATR(M5)**
  because `engine.py:235` passes the M5 entry bars to `calculate_atr()`. **If that is ever
  changed to feed M15 anchor bars, k must be divided by ~1.7 (→ ≈1.75)** or every FX stop
  silently widens by 70%. Recorded in the params docstring and in the doc.

### 10.8 🟡 APA / strategies 4 & 5 ignore `min_sl_pips` and `min_sl_atr_mult`
- **Where:** `strategy_apa/engine.py:206`, plus 10.4 above
- **Fix:** After computing `state["sl_level"]`, widen so that
  `|entry − sl| >= max(min_sl_pips × pip_size, min_sl_atr_mult × atr)`.
  **Note the ordering subtlety in APA:** the SL is computed at BOS-confirmation time
  (`engine.py:206`) but `entry` is not known until the retest fires
  (`engine.py:279`). The floor must therefore be applied at signal-emission time, not at
  SL-computation time — otherwise it is measured against the wrong reference and will
  under-widen.

### 10.9 🟡 NY Retest ignores `target_mode` / `target_rr`
- **Where:** `strategy_six_ny_open_retest/engine.py:114`
- **What:** `target = self.params.fixed_target_points * pip_size`, unconditionally.
- **Fix:** When `target_mode == "rr"`, compute `target = |entry − stop_loss| × target_rr`
  **after** all buffers and floors have been applied to `stop_loss`; keep the points path for
  `target_mode == "points"`. Apply `dynamic_target_override` to whichever target results.

### 10.10 🟡 CRT's structural TP is discarded by the risk grid
- **Where:** `strategy_three_crt/engine.py` + `risk/multi_tp.py`
- **What:** CRT reverse-engineers its SL so the structural TP lands at exactly 1.5R; the grid
  then replaces that TP with 1.5R/3R/5R. TP2 and TP3 sit beyond C1's extreme — the level the
  setup thesis says price is travelling to — so 50% of every CRT position is stranded on
  targets the strategy does not believe in.
- **Fix:** Product decision. Either exempt CRT from the grid, or place its SL structurally
  (beyond the C2 sweep wick) and let the grid own targets.

### 10.11 🔵 Frontend hint is factually wrong
- **Where:** `frontend/src/pages/Backtester.jsx:112`
- **What:** *"Primary SL distance in points (e.g. 170 for USDCHF = 17 pips)"*. The engine
  multiplies by `get_pip_size()` = 0.0001, so 170 → **170 pips**, not 17.
- **Fix:** Once `sl_method` is wired, relabel to "SL in index points (index CFDs/futures
  only — FX uses the ATR multiple below)" and hide or disable the field for non-index symbols.

### 10.12 🔵 `min_rr` semantics are misleading
- **Where:** `risk/engine.py:298`
- **What:** Compares `min_rr` against the **last** active TP's RR (5.0), not TP1, so at default
  settings it never rejects a signal despite reading as a per-trade quality filter.
- **Fix:** Either rename to `min_last_tp_rr`, or add a genuine per-trade filter against TP1.
  Documented inline in `config_schema.py` in the meantime.

---

## 11. Verification

```
python -m py_compile  →  PASS on all 6 edited .py files
```

Additionally verified programmatically:
- All 8 dataclasses instantiate with no arguments.
- **No mutable defaults**: `tp_splits` uses `field(default_factory=...)`; two `RiskParams()`
  instances confirmed not to share list state (mutating one leaves the other at
  `[50.0, 30.0, 20.0]`).
- Every field has either a `default` or a `default_factory`.
- Types are consistent with prior values (`float`/`float`, `int`/`int`); the three new
  string-enum fields (`sl_method`, `target_mode`) are `str`, matching the existing
  convention used by `trail_method_tp2` etc. rather than `Literal`, so no schema-filter or
  DB-serialisation change is required.
- Full `UserConfigV2.to_dict()` → JSON → `from_dict()` round-trip preserves every new field,
  confirming the new params survive DB persistence and the `filter_kwargs()` allow-list.

Field counts after edit: RiskParams 48, APAParams 15, VWAPParams 17, HTFFVGFlipParams 10,
BiasIFVGParams 9, NYOpenRetestParams 10, CRTParams 9, DriftJumpAlphaParams 14.

---

## 12. Summary of expected aggregate effects

| Strategy | Stop change | Trade count | Live now? |
|---|---|---|---|
| APA | 3.5 → ~12–14 pips (~3.5×) | **−30…−45%** | Buffer yes; floors **no** |
| VWAP | 80 → ~8–13 pips | ~flat (may rise slightly) | **No** — all pending |
| CRT | unchanged | unchanged | n/a |
| HTF FVG Flip | pending wiring | **−25…−40%** (session) | Session yes; stop **no** |
| Bias IFVG | pending wiring | **−30…−40%** (A+ gate) | Gate yes; stop **no** |
| NY Open Retest | ~9 → ~12 pips (+30%) | ~flat | **Yes** — SL buffer live |
| DriftJumpAlpha | unchanged | unchanged | n/a |

**Book-wide:** position sizes down ~2× from risk sizing alone (up to ~7× for APA);
aggregate at-risk exposure 3.0% → 1.5%; TP1 1.0R → 1.5R; BE/trail no longer fire before the
first partial.

**How to read the next backtest.** Dollar P&L will almost certainly get worse. That is the
expected and correct outcome: the previous figures were inflated by the BE-buffer artifact
(§9.1) and by the size asymmetry that sub-spread stops create. **Judge these changes on
expectancy in R and on whether the trades are executable at all — not on the P&L line.**
A move from −0.367R toward 0R is real progress even as the dollar figure falls.

**What this audit does not claim.** None of these changes creates an edge. They remove
arithmetic impossibilities and a P&L-fabricating bug. Whether any of these seven strategies
has an edge remains genuinely unknown, and cannot be established from 28–43 trades on one
symbol. The next step after the engine fixes in §10 is a materially larger sample across
multiple symbols, evaluated net of costs, before any of these strategies is considered for
live capital.
