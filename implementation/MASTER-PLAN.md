# AlgoEdge — Master Plan

**The single source of truth for what is planned, what is done, and what is next.**
Last reconciled: **2026-08-30**

---

## How to read this file

There is **one unit of work here: a Stage.** Stages are numbered 0–17 and each has a flat
list of numbered tasks. That is the whole scheme.

It used to be three schemes. `MASTER-IMPLEMENTATION-PLAN.md` Part 3 and Part 13 defined
"Phase 0–7", `TASKS.md` defined "Phase 0–14", and `implementation_plan.md` defined its own
"Phase 4–7" — all using the same word for different things, with the same numbers meaning
different work:

| Number | Master Plan Part 3/13 said | TASKS.md said |
|---|---|---|
| 1 | Correctness bugs | Units & instrument profiles |
| 2 | Filter re-disposition | Sizing truth |
| 3 | **Exit architecture** | **One config, no plumbing drift** |
| 4 | UI | Backtest ↔ live parity |
| 5 | New strategies / data | Exit architecture |

Reading either document alone gave a coherent but wrong picture of the other. **Stage numbers
below follow the `TASKS.md` numbering**, because every `[A1]`/`[D4]`/`[G8]` cross-reference,
every commit message and every code comment in the repo already points at it. The coarse
Part 3/13 scheme is retired; anything it carried that the fine-grained list never scheduled
has been recovered and is marked **`[RECOVERED]`** below.

**Status marks:**

| Mark | Meaning |
|---|---|
| `[x]` | Done and verified against the code on 2026-08-30 |
| `[~]` | Partially done — what is missing is stated |
| `[ ]` | Open |
| `[-]` | Deliberately not doing — the reason is stated |
| `[?]` | Needs a decision from you before it can be scheduled |

**Other documents in `implementation/` are now reference material, not plans.** They hold
detail this file summarises — the 70-entry defect register, the conformance and look-ahead
audits, the per-phase specs. None of them should be read as a to-do list. `TASKS.md` is
superseded by this file outright.

---

## Where things stand

**190 line items across 18 stages: 136 done, 9 partial, 39 open, 3 not doing, 3 awaiting a
decision.** (Some lines cover a contiguous range — `1.1–1.4`, `2.1–2.3` — where the original
tasks share one outcome, so the line count is lower than the 215 checkboxes `TASKS.md` carried.)

The four stages that matter right now:

- **Stage 15 (new)** — the corpus you ran says the book is **cost-limited, not signal-limited**.
  Three strategies are profitable on price and lose money in cash. A stop-distance floor and a
  trailing-first exit are worth more than any strategy change on the list.
- **Stage 16 (new)** — the frontend defects you reported. Diagnosed and fixed this session.
- **Stage 0 / 2 validation** — your backtests unblocked these, but the artifacts you shared do
  not carry the diagnostic fields the validation reads. One fresh run closes both.
- **Stage 14.12** — `FundamentalGate` is fully built and wired into **nothing**. Same
  "implemented but not wired" gap that hid the order-flow failure in Stage 10.

### Corrections made during this reconciliation

The task list had drifted from the code in both directions.

**Marked open, actually built** (verified in the repo on 2026-08-30):

| Was | Now | Evidence |
|---|---|---|
| 14.9 Symbol identity across brokers | **done** | `backend/core/instruments.py` — canonical instruments, per-broker maps, `_broker_maps`, `/api/broker` route |
| 14.10 Fundamentals chart tabs | **done** | `components/FundamentalsChart.jsx` + the `[Phase 14 E.1]` chart/data toggle in `pages/Fundamentals.jsx` |
| 14.12 `FundamentalGate` | **built, not wired** | `strategies/core/fundamental_gate.py` — four gates incl. `EconCalendarGate`; only `base_strategy.py` references it |
| 14.13 Strategy Factory | **done** | `api/routes/strategy_factory.py` + Strategy Lab's create/preview/promote |
| 14.14 Git branch + PR automation | **done** | the `activate` endpoint commits to `dev` and opens a PR |
| 13.13 Strategy Lab | **partial** | create/preview/promote shipped; *optimize* never wired (`backtester/optimizer.py` has no route) |

**Recorded as decided, since overtaken:** D-6 ("skip gamma exposure, no vendor commitment")
no longer describes the code. `CBOEOptionsProvider`, `YahooOptionsProvider`, `_compute_gex()`
and `GexRegimeGate` all exist and are free-tier. Gamma was built without a vendor. Stage 11 is
re-opened as partially done rather than skipped.

**Never scheduled anywhere** — carried by the coarse plan, dropped when the fine-grained list
was generated. All six are restored below as `[RECOVERED]`: cost sanity assertions (15.1),
the shadow-replay parity harness (4.10), same-candle ambiguity's M1 upgrade (4.11), APA's
in-trade head invalidation (6.15), drawdown-type configuration (17.1) and multi-prop-firm
infrastructure (17.2).

---

## Decisions

### Answered

- **D-1** `max_margin_utilisation_pct` stays at 30%, as a real editable field. *(2026-08-22)*
- **D-2** `max_positions_per_symbol` stays at 1; `allow_pyramiding` added as an opt-in. *(2026-08-22)*
- **D-3** Outright rejection below the confluence floor stays the default. *(2026-08-22)*
- **D-4** HTF FVG Flip stays independent; its displacement gate was fixed in place. *(2026-08-22)*
- **D-5** Bias-IFVG scans the APPROACH leg (spec default), with REACTION and BOTH available. *(2026-08-23)*
- **D-6** ~~Skip gamma exposure~~ — **superseded by the code.** Free CBOE/Yahoo GEX was built anyway. See Stage 11.
- **D-7** `phase7.txt` is not empty and was not deleted. *(2026-08-22)*
- **D-8** `lookahead_audit.md` restored from git. *(2026-08-22)*

### Open — these gate Stage 15, and Stage 15 is the valuable one

Full evidence for each is in [`RESEARCH-2026-08-30-CORPUS.md`](RESEARCH-2026-08-30-CORPUS.md).

- **D-9 — Exit architecture.** Retire the 3-tier ladder (TP 1.5/3/5 at 50/30/20, BE at 1.5R)
  in favour of a single TP with trailing armed at 0.75R and break-even off?
  *Measured: +0.15R/trade pooled even at the pessimistic 40% trail capture; +0.2R to +0.43R on
  the five low-frequency strategies. Break-even at 1.5R is insuring a 4.4% event and cost −$412
  on trades holding a median 1.69R.* **My recommendation: yes.**
- **D-10 — Stop-distance floor.** Replace `min_stop_spread_multiple = 2.0` with a round-trip-cost
  multiple. *Measured: 10× takes the book from −$26,844 to −$3,504 keeping 49% of trades; 15×
  reaches +$415 keeping 27%.* **My recommendation: 10×, and review at 15× after one re-run.**
- **D-11 — `min_rr` rebase.** `min_rr` now gates on `blended_rr`, and your saved config blends to
  2.41–2.65 against a `min_rr` of 3 — **every signal is currently rejected before sizing.** This
  needs a value whichever way D-9 goes. **My recommendation: 1.0 with a single TP, and surface
  `blended_rr` in the form so the interaction is visible before a run.**
- **D-12 — Instrument dispositions.** Drop CRT × NDX100 (−0.631R, n=19) and APA × NDX100
  (−0.749R, n=14)? Drop or re-stop VWAP × EURUSD (−$7,440 on a 16-pip stop paying a 1.2-pip
  spread)? **My recommendation: drop both NDX100 pairings; widen EURUSD's stop past 20 pips
  rather than dropping it, and re-measure.**
- **D-13 — Fundamentals scope.** Build fundamentals as a gate rather than a strategy, and park
  the order-flow track until the feed question is settled? *The broker sends a quote feed — no
  traded size, no aggressor — so "CVD" is a tick rule on the mid.* **My recommendation: yes;
  run `scripts/check_fundamentals.py` in session first to confirm on your own terminal.**
- **D-14 — Per-strategy exits.** The six strategies want targets from 0.5R to 3R. Move the TP/trail
  parameters into `strategy_params_override` instead of the global grid? **My recommendation: yes,
  but after D-9 — a trailing-first design needs far fewer per-strategy values than a fixed ladder.**

---

## Stage 0 — Observability

*Build first; nothing downstream can be validated without it.*

- [x] **0.1** `[I1]` `rejection_funnel` and `blocked_signals` returned at the top level on both routes.
- [x] **0.2** `[H1]` `Backtester.jsx` reads `result.rejection_funnel`, falling back to the old nested path.
- [x] **0.3** `[I3]` `sizing_diagnostics` per trade via a ContextVar, incl. `raw_lot` and `broker_volume_clamp`.
- [x] **0.4** `[I4]` `[P2-S1]` Every rejected signal recorded via `_record_blocked`, capped at 500, surfaced in a collapsible panel.
- [x] **0.5** `[I2]` `_filter_run_logs()` — all WARNING+, evenly-sampled INFO tail, capped at 2,000.
- [ ] **0.6** **UNBLOCKED — ready to run.** Re-run the six strategies and confirm the funnel renders
  and matches the log-derived counts. *This was blocked on a live MT5 run; you have now done the
  runs. But the 19 artifacts in `debug/` carry none of `rejection_funnel`, `blocked_signals`,
  `sizing_diagnostics` or `circuit_breaker_summary` — they either predate the Stage 0 additions
  (the APA set is from 2026-08-21, one day before 0.1 landed) or an export path is dropping them.
  One fresh run settles which, and closes this task.*

---

## Stage 1 — Units & instrument-profile correctness

*Re-verified 2026-08-22: 6 of the original 8 unit claims were false or overstated. Reported as
found rather than quietly dropped — a fast first-pass audit is not a verified one.*

- [-] **1.1–1.4** `[B1]` Explicit unit type for every distance parameter, a `resolve_distance()`
  resolver, ATR/PCT defaults, and the UI hint. **Descoped** — the premise did not hold up.
- [x] **1.5** `[B2]` **Claim retracted.** `get_pip_size` already resolves from the profile first.
- [x] **1.6** `[B3]` **Real, and fixed.** VWAP's `sl_points` was multiplied by `pip_size` on indices.
- [ ] **1.7** `[B5]` ATR-based stop floor to replace the flat per-asset-class pip floor. *Deferred; reframed as a design tension, not a defect.* **Now superseded in priority by Stage 15.2** — a cost-relative floor addresses the same problem with direct evidence behind it.
- [x] **1.8** `[B4]` **Claim retracted.** `stop_buffer_points`' pip conversion is the documented design.
- [x] **1.9** `[B6]` `RiskParams.mt5_order_deviation_points` added (behaviour unchanged).
- [ ] **1.10** `[B7]` Unify live SL-distance math with `minimum_stop_distance()`. *A DRY preference, not a correctness fix.*
- [x] **1.11** `[B8]` **Claim retracted.** `calculate_pips` already uses `get_pip_size`.
- [x] **1.12–1.13** `[C1]` **Framing corrected first.** The 13 FX profiles are stale, not mis-priced. `resolve_cross_rate_point_value()` prefers a live MT5 mid and falls back to the snapshot.
- [x] **1.14** GER40 unverifiable — the broker does not list it. Left untouched rather than guessed.
- [x] **1.15** XRPUSD/DOGUSD lot constraints corrected against the live terminal (were 10× and 90× out).
- [x] **1.16** `scripts/verify_instrument_profiles.py` — 2 matched, 57 mismatched, 22 unlisted on first run.
- [x] **1.17** **The architectural fix that result drove:** `get_instrument_profile()` overlays live `symbol_info` onto the static profile. `ALGOEDGE_DISABLE_LIVE_PROFILES=1` pins the old behaviour.
- [x] **1.18** Superseded by 1.17.
- [x] **1.19** `[C5]` Zero-lot rejections now name their specific cause.
- [x] **1.20** `[C6]` **Claim retracted** — the method name and purpose were both wrong in the original audit.
- [x] **1.21** `test_instrument_profiles.py` green: 80/81 consistent, 1 flagged for manual review.

---

## Stage 2 — Sizing truth

*24 of 25 implemented and smoke-tested 2026-08-22.*

- [x] **2.1–2.3** `[A1]` `max_margin_utilisation_pct` and `min_deployable_risk_pct` are real fields; margin truncation is visible per trade.
- [x] **2.4** `[A2]` `max_account_leverage` threaded into the margin estimate.
- [x] **2.5** `[A3]` Lot rounding floors to the step and never clamps up to `volume_min`.
- [x] **2.6** `[A4]` Sizing includes the slippage and spread buffer. *(Verified: a 1% EURUSD request realises 0.96%.)*
- [x] **2.7–2.8** `[A5]` SL/TP re-anchored to the actual fill in both backtest and live.
- [x] **2.9** `[A6]` Exit slippage applied adversely on non-gapped stop-side exits.
- [x] **2.10** `[A7]` Confluence risk tiers moved into `RiskParams`. **See Stage 15.5 — the score is a constant in three of six engines, so this ladder is scaling by a constant on half the book.**
- [x] **2.11** `[A8]` `min_stop_spread_multiple` wired through. **Its default of 2.0 is the subject of D-10.**
- [x] **2.12** `[A9]` One `post_split_risk_tolerance_pct` replaces three hardcoded ratios.
- [x] **2.13–2.14** `[A10]` TP-level request/placement recorded; single-TP short-circuits the split machinery.
- [x] **2.15** `[A11]` The hardcoded `DriftJumpAlpha` TP1 branch replaced by a per-strategy override map.
- [x] **2.16** `[A12]` `kelly_lot_size` → `kelly_risk_fraction`, returns a fraction.
- [x] **2.17** `[P2-R2]` `allow_pyramiding` / `min_bars_between_entries` added; default behaviour unchanged.
- [x] **2.18–2.19** Per-strategy daily-trade and concurrent-position limits.
- [x] **2.20** `[D9]` Live order volume floors to the lot step and refuses rather than clamping up.
- [x] **2.21** `[P2-R9]` `open_risk_weight` scales open risk in the drawdown budget.
- [x] **2.22** `[P2-X4]` Per-leg rejection instead of group-atomic. *(Fixed a latent `sub_trade_count` bug this change made reachable.)*
- [x] **2.23** `[P2-R12]` `stop_below_min_viable` already flows into the funnel distinctly.
- [x] **2.24** `circuit_breaker_summary` surfaced in both engines and both routes.
- [ ] **2.25** **UNBLOCKED — ready to run.** Confirm median `realised_risk_pct` is within 10% of target on every asset class, crypto included. *Same artifact gap as 0.6: `sizing_diagnostics` is absent from the runs shared. One fresh run closes both.*

---

## Stage 3 — One config, no plumbing drift

*The weakest stage. Most of what is open here is open because it needs the frontend in front of
you, not because it is hard.*

- [~] **3.1** `[E1]` The 12 new Stage-2 fields are typed `| None` and merge only when set. The other ~40 `BacktestRequest` fields were not converted.
- [x] **3.2** `SchemaForm.jsx` renders from `/config/parameter_schema`.
- [ ] **3.3** `[E2]` Remove `**req.risk_config`. *Deliberately deferred — at least one key (`be_buffer_atr_mult`) is read from it with no named twin. Needs a frontend-aware pass.*
- [x] **3.4–3.5** `[E3]` `max_account_leverage` and `global_min_sl_pips` are wired. *Engine-level `min_sl_pips` fallback deliberately stays 0.*
- [ ] **3.6** `[E3]` The third dead UI control — implement or remove. *Needs a product decision.*
- [ ] **3.7** `[E4]` `trail_method_tp1` in the UI. **Now a Stage 15 dependency** — D-9 makes this field the primary exit control.
- [x] **3.8–3.9** Both surfaces render from the same generated schema, so they cannot diverge by hand.
- [x] **3.10–3.11** `[E7]` `[E8]` Prop-firm position limits and the trading-day rule are configurable.
- [ ] **3.12** `[E9]` Editable cost table. **Raised in priority by Stage 15** — the crypto slippage heuristic is 0.12R of CRT × BTCUSD's 0.367R friction and cannot currently be corrected.
- [ ] **3.13** `[E10]` `tp_splits` validation + live-normalise display.
- [ ] **3.14–3.15** `[D2]` Replace the two hand-maintained `risk_config` dict literals with `dataclasses.asdict(RiskParams())`. *Mitigated, not fixed: every new field has been threaded into both literals by hand. `Rule-2`'s drift report currently counts 21 missing and 14 extra keys.*
- [x] **3.16** `[D4]` The `RiskEngine` cache fingerprint is a full config hash. *Fixed a real staleness bug in live trading.*

---

## Stage 4 — Backtest ↔ live parity

- [x] **4.1** `[F1]` `original_sl` read correctly and written by both engines.
- [x] **4.2** `[D1]` `sizing_basis` added; personal-live's anchor is the observed balance, not the $10,000 default (which was Bug 12).
- [x] **4.3** `[D3]` `trail_pct` percent→fraction unified. *Found a real 100× trailing-distance bug.*
- [x] **4.4** `[D5]` The `risk_pips = 20.0` fallbacks replaced with skip-and-warn.
- [x] **4.5** `[D6]` One `resolve_be_buffer()` replaces four independent buffer computations.
- [x] **4.6** `[D7]` `trail_require_be_first` honoured in both engines.
- [x] **4.7** `[D8]` The TP1-sibling BE cascade is gated on `be_mode`. *It was running unconditionally live.*
- [x] **4.8** `[D10]` Live `group_id` is a UUID; `record_external_close` looks groups up by symbol.
- [ ] **4.9** A live/demo MT5 parity fixture: same signal → same lots, same stop, same exits in both engines. *Individual mechanics verified by hand; the end-to-end fixture needs a terminal.*
- [ ] **4.10** `[RECOVERED]` **Shadow-replay parity harness.** Run the live signal path over historical data bar-by-bar *as if live* and diff its output against the batch backtester for the same window. *From the coarse plan's §3.3 — permanent infrastructure for the whole "signals live but not in backtest" class of bug, and it never appeared on any task list.*
- [x] **4.11a** `[RECOVERED]` Same-candle SL/TP ambiguity resolved conservatively (SL wins) and tagged `same_bar_ambiguous`. *Built in both engines; was on no task list.*
- [ ] **4.11b** `[RECOVERED]` The M1 upgrade for ambiguous bars — replay the bar on the lowest timeframe available and walk the real path, rather than assuming.

---

## Stage 5 — Exit architecture: RR-triggered break-even and trailing

*All plumbing exists. **Stage 15 is what finally uses it.***

- [x] **5.1** All eight fields on `RiskParams`: `trail_method_tp1`, `be_mode`, `be_trigger_tp_level`, `trail_mode`, `trail_trigger_rr`, `trail_trigger_tp_level`, `trail_require_be_first`, `tp_volume_pcts`.
- [x] **5.2** `[F2]` `MultiTPManager.trail_methods[0]` reads `trail_method_tp1`; `tp_volume_pcts` takes priority over `tp_splits`.
- [x] **5.3** `BreakevenManager` honours `be_mode` (`RR`/`TP_HIT`/`EITHER`/`NONE`).
- [x] **5.4** `manage_open_position`'s trailing gate honours `trail_mode`, computed against the fixed `original_sl`.
- [~] **5.5** Live mirrors `trail_method_tp1` and `trail_mode="RR"`. **`TP_HIT`/`EITHER` are not implemented live** — they log once and fall back to the RR condition.
- [~] **5.6** `blended_rr` / `blended_rr_be` surfaced in diagnostics. **`expected_rr` not built** (needs historical per-level hit rates); no frontend display.
- [x] **5.7** `[F6]` `min_rr` gates on `blended_rr`. **This is what currently rejects every signal under your saved config — see D-11.**
- [x] **5.8** Verified: single TP at 5R with BE and trail both at 1R produces a trailing stop at exactly 1R with no break-even applied.

---

## Stage 6 — Strategy engine fixes & filter re-disposition

- [x] **6.1 / 6.1b** `[G1]` UTC-midnight resets removed; per-candidate staleness budgets added to APA, HTF-FVG-Flip and Bias-IFVG.
- [x] **6.2** `[G2]` APA's state machine rewritten around a bounded candidate list; each ages independently.
- [x] **6.3** `[G3]` APA's BOS is a true body close.
- [x] **6.4** `[G4]` `neckline_major_atr_tolerance` replaces the hardcoded 0.5.
- [x] **6.5** `[P2-S7]` `max_sl_floor_atr_mult` caps the SL floor.
- [x] **6.6–6.7** `[G5]` `[G6]` VWAP's pullback condition and real confluence score — delivered via Stage 8.
- [x] **6.8** `[G7]` CRT `bias_neutral_mode` (`BLOCK`/`REDUCED_SIZE`/`ALLOW`).
- [~] **6.9** CRT session fields were already per-strategy. **Counting the rejection in the funnel is not done** — no strategy engine emits the phantom `TradeSignal` with `passed_gates=False` that the funnel reads. *Retrofitting that contract is its own task; it affects every engine, not just CRT.*
- [x] **6.10** `[G7]` CRT `trigger_grace_bars`.
- [x] **6.11** `[G8]` HTF-FVG-Flip displacement gate (1.5×ATR range, 60% body), opt-in so other callers are unaffected.
- [x] **6.12** `[P2-S15]` Bias-IFVG's second-trade rule clamps size instead of rejecting.
- [x] **6.13** `[P2-S18]` DriftJumpAlpha `adx_gate_mode` with percentile-ranked sizing.
- [x] **6.14** `[P2-S21]` `cooldown_after_max_losses_hours` already existed.
- [ ] **6.15** `[RECOVERED]` **APA head-level in-trade invalidation exit.** From the conformance audit §1.2-C; item 2 of the coarse plan's open-work register, never scheduled. The `on_position_bar` hook it needs now exists (Stage 14 B2.3).
- [?] **6.16** **CRT and NY Open Retest enter one bar early.** 56% of CRT's stop-outs die within 3 bars and 44% never tick in its favour at all; NY Retest is 37.5%/18.8%. *This is the mechanism `PHASE-14` Part B3 described — but B3 attributed it to APA on a 5-trade sample, and across all 52 APA groups it does not hold (median MFE 0.79R, 65% reach 0.5R). The fix B3 proposed — require a rejection candle rather than a zone touch — belongs to CRT's C2 trigger and NY Retest's retest.* **Needs your decision, as B3's did.**
- [ ] **6.17** NY-Retest M1 timeframe + `order_type` limit fills. *Requires an `order_type` field on `TradeSignal`. Flagged in the register since the first audit, scheduled by no stage until now — it changes entry mechanics materially, so it stays explicit rather than bundled.*
- [?] **6.18** CRT's structural target vs. the R grid. *CRT computes "price returns to C1's opposite extreme", then the grid discards it. VWAP v2 now has the same unresolved pattern (`target_mode="SIGMA_BAND"`). One decision covers both.*

---

## Stage 7 — Backtest-result UI

- [x] **7.1** `GET /api/config/parameter_schema` from `core/schema_introspection.py` — 213 fields across 9 groups, help text extracted from attribute docstrings via `ast`.
- [x] **7.2–7.7** `SchemaForm.jsx`: typed rows for nested values, docstring help, changed-vs-default flags, grouping, search, reset per field and per group.
- [x] **7.8** `[H3]` Entry-time risk resolved at its real source in `trade_grouper.py` (`initial_stop_loss` first), mirrored into `summaryEngine.js`.
- [x] **7.9–7.10** Strategy Lab reads the schema endpoint; dict parameters render as key/value rows.
- [x] **7.11–7.17** `<RunReport>`: Signal Funnel, Risk Deployment, Exit Attribution, Blocked Timeline, Cost Impact, metric formulas on hover. Verified live against a real XAUUSD/APA run and responsive to 375px.

---

## Stage 8 — VWAP v2

- [x] **8.1–8.2** 13 new params; session-anchored VWAP with volume-weighted σ bands in one pass.
- [x] **8.3** Setup 1 rewritten with the convergence-based pullback condition. Verified end to end.
- [x] **8.4** Band-reversion setup added. Verified end to end.
- [x] **8.5** Real 5-component confluence score. *Documented simplification: trend agreement is slope+momentum only.*
- [x] **8.6** `target_mode` added; structural TP declared in metadata. **The grid-exemption decision is 6.18, still open.**
- [x] **8.7–8.8** Day-limit rules unchanged; `docs/vwap_strategy_v2.md` written.
- [ ] **8.9** **VWAP's horizon and its targets contradict each other.** It is force-flat at 61 M5 bars (5.1h) — 45.7% of its legs exit that way — while TP1 sits at 1.5R and its median 5-hour excursion is 0.69R. 389 of those force-closed legs had already passed 1.0R. *Either extend the horizon or bring the target to 0.5–0.75R; doing neither is what produced 1,034 trades at −0.027R. Folded into D-9/D-14.*

---

## Stage 9 — Portfolio governor

- [x] **9.1–9.2** Not applicable per D-4 — HTF FVG Flip stays independent.
- [x] **9.3** `services/market_context.py` — `htf_trend`, `session`, `volatility_regime`, `vwap_zone`, `news_proximity_minutes`. Verified against synthetic data.
- [~] **9.4** `market_context_size_modifier()` is wired into `evaluate_signal`. **No strategy engine populates `metadata.market_context`**, so it never fires. *Six repetitive per-engine edits. Same shape as 14.12 — see the note there.*
- [x] **9.5** `risk/portfolio_governor.py::SYMBOL_CLUSTERS` + `max_cluster_risk_pct`. *Static clustering, deliberately not rolling correlation.*
- [x] **9.6** `resolve_net_direction_key()` nets USD-side bets across pairs.
- [x] **9.7** `strategy_risk_budget_pct` per strategy, enforced alongside the account-wide budget.
- [ ] **9.8** Rolling-correlation portfolio construction (viz plan §7). *The static table is the pragmatic version; the dynamic one needs synchronized multi-symbol history that no call site currently has.*

---

## Stage 10 — Order flow (Tier 1)

- [x] **10.1–10.4** `data/orderflow.py`: tick classification, CVD, delta divergence, absorption, volume profile/VPOC. Each verified against synthetic data with a known answer.
- [x] **10.5** `compute_orderflow_snapshot()` composed and consumed by the Fundamentals panel.
- [x] **10.6** **The defect running it exposed.** `classify_ticks` read `ticks["last"]` unconditionally; on this broker's feed `last` is 0.0 and `volume`/`volume_real` are 0 on every tick, so *every tick classified as a sell* and `cvd == −tick_count` exactly, forever. Fixed with a quote rule on the mid, `classification` and `volume_is_tick_count` reported, five regression tests.
- [?] **10.7** **The consequence, which is bigger than the fix.** The broker sends a **quote feed, not a trade feed.** There is no traded size and no aggressor, so what this module computes is: CVD = quote up-ticks minus down-ticks; volume profile = *time*-at-price; absorption = a high-tick-count low-range bar. That is a momentum series derived from the same price the strategies already read — not institutional positioning. *Run `scripts/check_fundamentals.py` in session to confirm on your own terminal, then decide (D-13) whether this track is re-scoped or shelved.*

---

## Stage 11 — Gamma exposure

*D-6 said skip. The code says otherwise — this stage was built without a vendor.*

- [x] **11.1** Free GEX providers exist: `CBOEOptionsProvider`, `YahooOptionsProvider`, `_compute_gex()`.
- [x] **11.2** `GexRegimeGate` in `strategies/core/fundamental_gate.py`.
- [ ] **11.3** `MarketContext.gamma_regime` is still `None` — the providers exist but nothing populates the field.
- [ ] **11.4** `docs/strategy-orderflow-gex.md` (was Doc-8). *Only `docs/orderflow_data_layer.md` exists.*
- [ ] **11.5** **The ticker-mapping problem.** GEX exists for `SPX`/`SPY`, not for the `SPX500` CFD actually traded. 14.2 warns about it in the UI; nothing maps between them. *Read GEX as a regime input for the underlying, never as a level on the CFD's chart.*

---

## Stage 12 — Per-symbol-per-strategy configuration

*Recovered from `phase7.txt`, which turned out not to be empty.*

- [x] **12.1–12.4** `InstrumentSlot` with a real `uuid4` slot id; `max_losses_per_day` on all six strategies; `instrument_slots` alongside the deprecated `instrument_settings`, with deterministic `uuid5` migration. *Verified: migrated slot ids are reproducible across reloads.*
- [x] **12.5–12.6** `CircuitBreaker` and `RiskEngine` resolve limits and risk per slot; pre-12.x call sites unchanged.
- [x] **12.7** `bot_service.py` iterates slots, keyed by `slot_id`, so two slots on one symbol get independent engine instances and independent state. *Slot-resolution verified directly; the async scan loop needs a live terminal.*
- [x] **12.8** `PortfolioBacktestEngine.run()` keyed by cache key rather than bare symbol. *Verified with two slots sharing EURUSD under different strategies — no collision, correct per-symbol costs.*
- [x] **12.9** `validate_slot_position_caps()` returns non-blocking warnings on `PUT /api/config`.
- [x] **12.10** **A real bug, found by investigating the cache report.** `risk/engine.py`'s per-symbol cooldown read `metadata.timeframe`, which *no caller anywhere* populated — so every signal used the "M15" fallback (900s), making an M5 strategy's cooldown 3× too long. Fixed at all three call sites.
- [ ] **12.11** Slot picker in `Settings/Strategy.jsx`. *Verified still absent — zero `slot` references in that file.*
- [ ] **12.12** Slot-aware portfolio symbol picker in the Backtester. *Depends on 12.11; the backend half is ready.*

---

## Stage 13 — Visual intelligence

- [x] **13.1–13.3** `strategies/core/markings.py` — the marking vocabulary. All six strategies emit markings; `trade_grouper` routes all 8 kinds. *`metadata["markings"]` had been read since it was written and produced by nothing, so `smc_data` was empty on every trade ever grouped.*
- [x] **13.4–13.6** `services/log_stream.py`, `api/routes/logs.py`, per-run log sessions. Backend logs reach the frontend for the first time.
- [x] **13.7–13.8** `services/replay_stream.py` + `ReplayChart.jsx` / `BacktestReplay.jsx`. 12 tests.
- [x] **13.9** `llm_service.py`: invalid model id corrected, default `claude-opus-5`, env-var key resolution (the key was previously always `""`), `max_tokens` 1024 → 16000, streaming.
- [x] **13.10** `services/analysis_context.py` + `/analysis`. A 4,469 KB result reduces to a 4.6 KB prompt.
- [x] **13.11–13.12** `<RunReport>` and the provider registry verified live against the terminal.
- [~] **13.13** Strategy Lab: **create / preview / promote shipped; *optimize* never wired.** `backtester/optimizer.py` exists with no route pointing at it.
- [x] **13.14–13.16** `SchemaForm` mounted; order-flow bubbles fed by real ticks; the full XAUUSD/APA verification run executed on your terminal.
- [x] **13.17** **Backtest starved the event loop.** `last_yield_time` was assigned and never read, so the loop yielded every 50 bars regardless of duration — >1s of solid blocking, which dropped the WebSocket. Now yields on >40ms elapsed, with three per-bar costs hoisted out.
- [x] **13.18** Marking lines ran to the chart edge — `end_time` now clamped to the group's exit.
- [x] **13.19** Replay chart teleported 400 bars at a time — now drains at 24 bars/frame with proportional catch-up.
- [x] **13.20** Layout overflowed the viewport — `min-width: 0` on flex children, in-box scrolling, breakpoints at 1280px and 1080px.

---

## Stage 14 — Strategy factory, fundamentals on chart, symbol identity

- [x] **14.1–14.2** Help accordions across Fundamentals; the GEX ticker warns on broker CFD names.
- [x] **14.3** Strategy Lab symbol is free text with suggestions; the preview window is a real date range.
- [x] **14.4** **VWAP produced zero signals on gold over 7.5 months.** `volume_confirmation_mult=1.2` eliminated every candidate (1,239 tradeable → 55 setups → 0 after the volume gate) because MT5 CFD feeds report tick volume, not size. Default → 0.0; now 20 signals on the same data.
- [x] **14.5** Anchored-VWAP vectorised: **12.2× faster, output bit-identical** across 27 windows.
- [x] **14.6** **Swap costs were spent in the wrong currency.** GBPJPY's −18.93 points became 1,893 **JPY**, spent as **USD** — ~150× too large; a +5.26R take-profit recorded as a −$1,770 loss. Now converted via the live cross, with an absolute per-lot bound. *Affected live P&L attribution, not only backtests.*
- [x] **14.7** `be_applied`/`trail_applied` now roll up from legs to the grouped trade. *All 691 analysed trades had recorded False despite 106 BE exits — what break-even cost was unmeasurable.*
- [x] **14.8** Session filter exempts 24/7 instruments. *XRPUSD: +0.56R in the Asian window vs −0.20R in NY.*
- [x] **14.9** **Done — was marked open.** `backend/core/instruments.py`: canonical instruments, per-broker symbol maps, `/api/broker` route.
- [x] **14.10** **Done — was marked open.** Fundamentals chart tabs with a per-tab chart/data toggle.
- [ ] **14.11** Strategy overlay on the fundamentals chart (Part E.2). *Genuinely not started.*
- [~] **14.12** **`FundamentalGate` is built and wired into nothing.** `strategies/core/fundamental_gate.py` has four gates — `EconCalendarGate`, `OrderFlowGate`, `CorrelationGate`, `GexRegimeGate` — with sensible `fail_loudly` and backtest-safety semantics. Only `base_strategy.py` mentions it; **no engine instantiates one.** *This is the third instance of the pattern Stage 10.6 warned about: "implemented but not wired" is not the same as working, and it is what hid the total order-flow failure for a whole phase.* **The calendar gate is the one to wire first — see Stage 15.7.**
- [x] **14.13** **Done — was marked open.** `api/routes/strategy_factory.py`: list / generate / activate / delete, with the review gate and path restriction.
- [x] **14.14** **Done — was marked open.** `activate` commits to `dev` and opens a PR via the GitHub API; advisory, never auto-merged.

---

## Stage 15 — Cost and exit re-architecture *(new, 2026-08-30)*

*Driven by the January–August corpus you ran. Full evidence:
[`RESEARCH-2026-08-30-CORPUS.md`](RESEARCH-2026-08-30-CORPUS.md); reproduce with
`python3 implementation/evidence/analyse_corpus.py`.*

**The finding this stage exists for:** measured on price alone, NY Open Retest (+0.103R),
HTF FVG Flip (+0.082R) and CRT (+0.023R) are profitable. Measured in cash, all six strategies
lose. The gap is friction — 0.056R to 0.187R per round trip, up to 0.37R on individual
instrument pairings. **The book is cost-limited, not signal-limited.** Nothing else on this
plan is worth as much as fixing that.

- [x] **15.1** `[RECOVERED]` **Per-trade friction diagnostic — shipped.** The coarse plan asked
  for a hard assertion (`spread_pips < 20` FX, `< 30` metals). On inspection that is already
  implemented, better, as `_clamp_spread`'s per-symbol sane bands — and XAGUSD (5–150) and
  XAUUSD (1–20) both pass theirs deliberately. Tightening them from here would be guessing at
  values only your terminal can settle. Shipped the useful half instead: `calculate_lot_size`
  now records `round_trip_cost_R` and `stop_cost_cover` into `sizing_diagnostics` on **every**
  trade. That is the number the whole corpus turned out to hinge on and nothing recorded it —
  the cost model knew the spread, the sizer knew the stop, and no one divided one by the other.
  It reaches the trade record and RunReport's Cost Impact panel, so 15.2's multiple can be
  chosen from your own histogram rather than my threshold.
- [ ] **15.1b** Verify XAGUSD's 96-pip and XAUUSD's 13.9-pip spreads against live `symbol_info`.
  *Between them they carry 524 of the corpus's 1,326 groups; if either is a points-read-as-pips
  error, the friction on those symbols is overstated.*
- [~] **15.2** `[D-10]` **Economic stop floor — mechanism shipped, default off.**
  `RiskParams.min_stop_cost_multiple` requires `stop >= N × (spread + 2 × slippage)`, so N reads
  as "the broker takes at most 1/N of one R". Threaded through `minimum_stop_distance`,
  `calculate_lot_size`, `RiskEngine`, both backtest request models and `bot_service`; it names
  itself in the rejection reason, so it shows in the funnel. **Defaults to 0.0 (disabled)** —
  turning it on rejects trades that are taken today, which is D-10's call, not a default to
  change underneath you. *Verified against the corpus's own XAGUSD numbers: its median stop
  covers 8× its round trip, so 6× accepts it and 10× rejects it, exactly as §2.1 measured.*
  **What remains is your decision on the value.**
- [ ] **15.3** `[D-9]` **Trailing-first exits.** `tp_count = 1`, `trail_method_tp1 = ATR_TRAIL`,
  `trail_mode = RR`, `trail_trigger_rr = 0.75`, `trail_require_be_first = False`, `be_mode = NONE`.
  *Every field already exists from Stage 5 — this is a default change plus a test, not new code.
  Measured: +0.15R/trade pooled at the pessimistic 40% capture, +0.2R to +0.43R on the five
  low-frequency strategies. Break-even at 1.5R is insuring a 4.4% event.*
- [~] **15.4** `[D-11]` **`min_rr` can no longer fail silently — the rebase itself is still yours.**
  `RiskEngine` now checks at construction whether the configured TP ladder can ever satisfy
  `min_rr`, and logs the arithmetic and the fix when it cannot. Your saved config —
  TP 1.5/3/5 at 50/30/20 with `min_rr = 3` — blends to **2.65** and therefore rejects every
  signal before sizing, with a funnel indistinguishable from a market that produced no setups.
  *Verified: fires on that exact config, silent at `min_rr = 2.0` and for the single-TP proposal,
  and honours `tp_volume_pcts` over `tp_splits` the way `MultiTPManager` does.* **Choosing the
  new value is D-11; surfacing `blended_rr` in the form is still open.**
- [ ] **15.5** **Make the confluence score real, or stop scaling risk by it.** CRT emits 90 on
  every trade, NY Open Retest 92, VWAP 80 — zero variance across 1,143 groups. `get_confluence_scaled_risk`
  is scaling by a constant on half the book. Where the score does vary it is inconsistent: APA's
  top bucket is its *worst* (−1.099R, n=8), HTF-FVG-Flip's top bucket is its best.
- [ ] **15.6** `[D-12]` **Instrument dispositions.** Drop CRT × NDX100 (−0.631R, n=19, −$2,428)
  and APA × NDX100 (−0.749R, n=14). Widen or drop VWAP × EURUSD (−$7,440; a 16-pip stop paying a
  1.2-pip spread). Prioritise CRT × XAUUSD (+0.459R) and CRT × BTCUSD (+0.406R).
- [ ] **15.7** `[D-13]` **Wire `EconCalendarGate` into the engines** (closes 14.12's gap for the
  one input whose data is real). *The argument is a cost argument, not an alpha one: at 0.10–0.19R
  of friction per trade, a gate that removes a third of VWAP's 1,034 trades hands back that cost
  before any selection benefit. Score it on trades removed per unit of expectancy retained, not
  on hit rate.*
- [ ] **15.8** **Re-run the book under 15.2 + 15.3 + 15.4 and re-measure.** *This is the gate for
  everything above. Between 4% and 64% of the exit grid is censored depending on strategy and
  target — the corpus cannot settle the margin, only point at it. One honest re-run can.*
- [ ] **15.9** **Investigate the long/short asymmetry.** SELLs beat BUYs on five of six strategies;
  VWAP's spread is 0.117R on n=1,034, which is large and well-sampled. Diagnose before ruling.

---

## Stage 16 — Frontend responsiveness and live run feedback *(new, 2026-08-30)*

*The defects you reported: the backend backtests fast but the frontend hangs, the progress bar
sits on a few numbers, the replay chart does not follow the backend, and results only appear
after a manual refresh. All four were the same three root causes.*

- [x] **16.1** **Two layers owned the same progress channel with incompatible scales.** The routes
  drove the bar 10 → 90 and then set 95; `run_backtest()` then broadcast its own 0 → 10 → 70 on
  the same channel — and on the `save_mode="DISCARD"` path both routes use, `stage="complete"`
  at `pct=100` **before the route had built the result**. The bar went backwards and the UI
  declared the run finished with nothing to show. New `services/backtest_progress.py` owns one
  monotonic, time-paced scale and hands 0.0–1.0 sub-ranges to each phase.
- [x] **16.2** **Progress resolution was 600 bars in phase 1 and nothing at all in phase 2.** Eight
  updates across a 5,000-bar signal pass, and the simulation — the longest part of a run —
  reported nothing, so the bar sat on 95%. Both engines now take a `progress_cb` and report ~200
  times across a run; phase 1 reports every bar and the reporter throttles.
- [x] **16.3** **The completion WebSocket frame carried the entire run** — a per-bar equity curve
  (45,923 points on one 15-trade run), every leg, every group, the log tail — after a
  `copy.deepcopy` of the same megabytes on the event loop. It froze the browser on parse and
  re-render, and when the frame was too large to send, `broadcast_to_user` swallowed the
  exception and dropped the socket, so the result never arrived. Completion now sends a headline
  envelope and the client fetches the body over the same REST route a refresh used. State is
  saved before the announcement to close the fetch race.
- [x] **16.4** **Failed WebSocket sends were silent.** `except Exception: pass` dropped the
  connection with nothing in the logs — which is why this took so long to find. Now logged.
- [x] **16.5** **The result was serialised to `localStorage` in full on every change** — 4–8 MB,
  synchronously, on the main thread — and re-serialised in full inside the quota-exceeded
  handler. Two multi-megabyte stringifies back to back, neither of which could succeed on a
  large run. Now stores a slim, decimated copy.
- [x] **16.6** The equity curve was mapped into 45,000 objects and filtered over again on every
  render, on every filter, sort, tab and group-by change. Memoised and decimated once.
- [x] **16.7** **The replay chart legitimately stops before the progress bar does.** Bars are
  streamed during signal generation only; the simulation phase has none to send. With 16.1's
  corrected stage labels the panel now says which phase is running, so the two no longer look
  like they disagree. *No further fix needed — the earlier `24 bars/frame` drain with
  proportional catch-up (13.19) keeps up with the backend's 8 flushes/second.*
- [ ] **16.8** Verify all of the above in a browser against a real MT5 run. *Everything here is
  verified by build, lint and code path; none of it has been watched in a browser from this
  environment.*

---

## Stage 17 — Prop-firm and account infrastructure *(recovered)*

*Both items are from the coarse plan's open-work register and were scheduled by no stage.*

- [ ] **17.1** `[RECOVERED]` **Drawdown-type configuration.** Static (absolute), trailing
  (peak-relative), daily (per-cutoff), max peak-to-trough, and consecutive-loss — independently
  selectable, because firms differ and some enforce more than one at once. *And the distinction
  that actually costs money: balance-based vs equity-based. Store both series separately so any
  rule can be evaluated against the correct one.*
- [ ] **17.2** `[RECOVERED]` **Multi-prop-firm infrastructure.** One control plane, N trading
  nodes, a node registry, a Redis event bus, and a stable outbound IP per isolation group.
  *P2 in the original priority ordering — parked, not forgotten.*

---

## Engineering rules

- [x] **Rule-1** `tests/rule1_bare_literals_check.py` — AST scan for bare numeric literals outside
  the params files. *Runs, but surfaces ~811 hits, most legitimate. A working report tool, not a
  CI gate; scoping it to comparison/threshold expressions would make it one.*
- [x] **Rule-2** Config key-drift report (`test_engineering_rules.py`). *A diagnostic rather than a
  hard assert, matching 3.14/3.15's status. Currently 21 missing, 14 extra.*
- [-] **Rule-3** `resolve_distance()` round-trip test — the function was descoped at 1.1/1.2.
- [x] **Rule-4** `binding_constraint` coverage for the margin, cluster-exposure and strategy-budget
  rejection paths. *3 of ~20 paths in `risk/engine.py`.*
- [ ] **Rule-5** Automated one-implementation-per-rule check. *Verified by hand across Stage 4/5;
  no script.*
- [ ] **Rule-6** Backtest↔live parity assertion. *Blocked on 4.9.*
- [ ] **Rule-7** **No CI pipeline exists.** No `.github/workflows/`, no `pytest.ini`, no
  `conftest.py`, and neither venv has pytest installed. Everything above is runnable; none of it
  runs automatically.

---

## Documentation

- [x] **Doc-1** APA plan updated for the Stage 6 state-machine rewrite.
- [x] **Doc-2** `docs/vwap_strategy_v2.md` (= 8.8).
- [x] **Doc-3** `docs/CRT_Strategy_Spec.md` updated, including the still-unresolved target-mode status.
- [-] **Doc-4 / Doc-5** Not applicable — D-4 kept HTF FVG Flip independent, so there is no deprecation to document and nothing was absorbed.
- [ ] **Doc-6** NY-Retest M1 + `order_type` — the doc update waits on the code change (6.17).
- [x] **Doc-7** `docs/RiskManagement_Spec.md` updated for the Stage 5 exit architecture.
- [ ] **Doc-8** `docs/strategy-orderflow-gex.md` — now genuinely needed, since Stage 11 shipped GEX.
- [x] **Doc-9** `docs/orderflow_data_layer.md`.
- [x] **Doc-10** `docs/backtest_report_ui.md` — the schema endpoint's field contract.
- [ ] **Doc-11** **This reconciliation.** `TASKS.md`, `MASTER-IMPLEMENTATION-PLAN.md` Part 3 and
  Part 13, and `implementation_plan.md`'s Phase 4–7 all need a superseded-by pointer at the top.

---

## Reference documents — what each is still for

| File | Keep for |
|---|---|
| `RESEARCH-2026-08-30-CORPUS.md` | **The evidence base for Stage 15 and D-9 through D-14.** Supersedes `strategy_optimization_research.md`. |
| `evidence/` | The scripts behind it. `analyse_corpus.py` regenerates every number. |
| `MASTER-IMPLEMENTATION-PLAN.md` | Part 11's 70-entry defect register, and Parts 4/5/8/9/14's specs. **Its Part 3 and Part 13 phase tables are retired.** |
| `PHASE-13-VISUAL-INTELLIGENCE-PLAN.md` | The replay protocol (Part C) and the order-flow field report (Part J.2). |
| `PHASE-14-STRATEGY-FACTORY-PLAN.md` | The factory contract (Part D) and symbol identity (Part C). **Part B3's "APA MFE is ~0R" is refuted at corpus scale — see 6.16.** |
| `doc_conformance_audit.md` | A live checklist of spec-vs-code divergence. |
| `lookahead_audit.md` | The look-ahead verdict on the single-symbol path. |
| `strategy_parameter_audit.md` | Per-parameter defaults with CHANGED annotations. |
| `AlgoEdge-Audit-Implementation-Plan.md` | §3.4 and §6, recovered as Stage 17. |
| `AlgoEdge-OrderFlow-Fundamental-Edge-Plan.md` | The transmission-mechanism reasoning behind Stage 10/11. |
| `AlgoEdge-Visualization-Portfolio-Architecture-Plan.md` | §7's portfolio construction, recovered as 9.8. |
| `phase7.txt` | Your dictated per-symbol-per-strategy requirement. Do not delete. |
| `implementation_plan.md`, `TASKS.md` | **Superseded by this file.** Kept for history only. |
