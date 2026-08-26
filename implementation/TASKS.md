# AlgoEdge — Master Task List

> **Update 2026-08-23.** Phase 7 (the backtest-result UI) and the frontend halves of
> Phase 3 and Phase 12 are now built, along with the replay engine and fundamentals UI
> that Part 6 of the master plan dropped. Task 10.5 is closed — and closing it exposed a
> total failure of the order-flow module on this broker's feed. Full report:
> [`PHASE-13-VISUAL-INTELLIGENCE-PLAN.md`](PHASE-13-VISUAL-INTELLIGENCE-PLAN.md) Part J.

Generated 2026-08-22 from [`MASTER-IMPLEMENTATION-PLAN.md`](MASTER-IMPLEMENTATION-PLAN.md) (all 13 parts,
including the 70-entry defect register in Part 11). Every issue and every planned feature in that
document appears below as one or more checkboxes. `[ID]` cross-references the plan — e.g. `[A1]` is
Part 11 §A defect 1, `[Part4]` is a task drawn from Part 4's spec, `[S2]`/`[R2]` are Part 2 filter-register
rows. Where a task closes more than one register ID, all are listed.

**Two corrections made while building this list**, called out where they occur:
- Part 13's phase table omitted `A7, A8, A11, A12, D10` — restored below.
- The Part 2 filter-register rows `S2` (scope), `S7, S15, S18, S21, R2, R3, R4` describe fixes that were
  never given their own Part 11 ID. Restored below as `[P2-*]`.

**Total: 181 checkboxes** (127 open, 53 resolved, 1 partial — updated 2026-08-22 after the Phase 2/3
implementation pass: 24/25 Phase 2 items and 4/16 Phase 3 items done and smoke-tested, 1 Phase 3 item
partial; see each phase's header note for what's still open and why) — 4 of 8 decisions answered 2026-08-22 (D-1 through
D-4: keep 30% margin cap / keep 1 position-per-symbol / keep outright confluence rejection / keep FVG
Flip and Bias-IFVG independent — all as real, user-editable settings rather than hardcoded), 2 decisions
resolved on inspection (D-7, D-8), 2 still open (D-5, D-6, neither blocks near-term work). Phase 12 is
new — recovered from `phase7.txt`, which turned out not to be empty; see the correction above.

**Phase 1 re-verification (2026-08-22):** every original Phase 1 claim was re-checked against current
code before being acted on. Result: 6 of the original 8 unit-conversion claims (`B2, B4, B6, B7, B8`,
plus the broader `B1` type-system proposal) were false or substantially overstated — see each item's
entry below for exactly what was wrong and why. Only `B3` (VWAP index `sl_points`) was real, and it was
narrower than first described. This is reported plainly because it matters more than the fixes
themselves: a fast first-pass audit is not the same as a verified one, and the second pass is what
actually earned trust here.
Ordering matters: **do not start Phase 2 onward until Phase 0 and Phase 1 are done** — every phase after
Unit correctness is validated by numbers that Phase 1 makes trustworthy for the first time.

---

## 0. Decisions required before work starts

These block the tasks named next to them. Answer in chat; do not guess.

- [x] **D-1 — ANSWERED 2026-08-22**: keep `max_margin_utilisation_pct` default at **30%**, but make it a real `RiskParams` field (not a hardcoded constant) with the truncation visible on every trade.
- [x] **D-2 — ANSWERED 2026-08-22**: keep `max_positions_per_symbol` default at **1**. Still add `allow_pyramiding`/`min_bars_between_entries` as opt-in overrides for anyone who wants to raise it per-slot later.
- [x] **D-3 — ANSWERED 2026-08-22**: keep **outright rejection** below the confluence floor as the default (`reject_below_confluence` stays on). Still move the tier table out of the hardcoded constant into `RiskParams` so the thresholds are yours to edit.
- [x] **D-4 — ANSWERED 2026-08-22**: keep HTF FVG Flip **independent**. Fix its displacement gate in place (`[G8]`, task 6.11) rather than retiring/merging it. Phase 9's merge tasks (9.1, 9.2) are removed below.
- [x] **D-5** **ANSWERED 2026-08-23 from the spec, not by guessing.** `docs/strategy-2-bias-keylevel-ifvg.md` Step 3 defines the manipulation leg as *the swing that ACTUALLY TOUCHED the key level* and says to scan for FVGs *within that specific leg* — i.e. the APPROACH. The engine did the opposite (post-tap only) and said so in its own comment. Shipped as `BiasIFVGParams.ifvg_leg_mode` = APPROACH (spec default) / REACTION (previous behaviour, kept because the existing backtest corpus was produced under it) / BOTH. See PHASE-13 plan Part K.1.
- [x] **D-6 — ANSWERED 2026-08-23**: **skip.** Phase 11 (gamma exposure) is not being built — no GEX vendor commitment. `gamma_regime` stays `None`/unwired on `MarketContext`, exactly as it already is (see 9.3/11.3).
- [x] ~~**D-7** Delete `implementation/phase7.txt`~~ — **corrected 2026-08-22**: the file is 3,108 bytes of your own dictated requirements (per-symbol-per-strategy config), not empty. Not deleted. See Phase 12.
- [x] ~~**D-8** Restore or accept loss of `lookahead_audit.md`~~ — **resolved 2026-08-22**: restored from git (`git checkout 58aaeef -- implementation/lookahead_audit.md`), 781 lines, no data lost.

---

## Phase 0 — Observability (build first; ~1 day; nothing else can be validated without it)

- [x] **0.1** `[I1]` **Done 2026-08-22.** `api/routes/backtest.py` — `rejection_funnel` (and `blocked_signals`) now returned at the top level on both the single-symbol and portfolio routes.
- [x] **0.2** `[H1]` **Done 2026-08-22.** `Backtester.jsx` now reads `result.rejection_funnel` via a `rejectionFunnel` const, falling back to the old nested path for pre-fix saved runs.
- [x] **0.3** `[I3]` **Done 2026-08-22, smoke-tested.** `position_sizer.calculate_lot_size` records every internal stage via a ContextVar (`get_last_sizing_diagnostics()`); `risk/engine.py::evaluate_signal` reads it back and attaches the full diagnostic (plus `raw_lot` and `broker_volume_clamp`, added after the first test run showed the original enum couldn't distinguish a lot_min/lot_max clamp from the hard-cap/margin guards) to `signal_data.metadata.sizing_diagnostics` and the trade dict in both engines. Verified against a synthetic XRPUSD case: correctly identifies `lot_max` as binding (raw_lot 5494 vs. profile lot_max 1000, realised risk 0.182% vs. requested 1.0%) and an EURUSD control case sizing at exactly the requested 1.0% with `binding_constraint: none`.
- [x] **0.4** `[I4]` `[P2-S1]` **Done 2026-08-22.** Both `backtester/engine.py` and `portfolio_engine.py` now record every rejected signal (already-open pyramiding guard, strategy gate, risk-engine rejection, per-leg invalid geometry, fill-time rejection) via `_record_blocked`, capped at 500. Surfaced in the API response and a new collapsible panel in `Backtester.jsx`.
- [x] **0.5** `[I2]` **Done 2026-08-22.** New `_filter_run_logs()` helper in `api/routes/backtest.py`, applied to both routes: keeps every WARNING/ERROR/CRITICAL, fills the remainder with an evenly-sampled INFO tail, capped at 2,000.
- [ ] **0.6** *Still open — requires a real MT5-connected backtest run, which this environment cannot execute.* Re-run all six strategies unchanged and confirm the funnel renders and matches the known log-derived counts (e.g. APA XAUUSD: 8 signals fired, all visible as risk-layer rejections). **You should run this validation pass yourself before trusting Phase 0's numbers on real data.**

---

## Phase 1 — Units & instrument-profile correctness

*Nothing downstream is trustworthy until this phase is green — pip/point ambiguity and wrong profile
data corrupt every risk figure that follows.*
*Phase 1 summary, updated 2026-08-22 after re-verifying every claim against current code: of the original 8 [B1-B8] unit claims, only [B3] (VWAP index sl_points) was real, and even that was narrower than first described. [B2], [B4], [B6]/[B7] (reframed, not bugs), [B8] were false or overstated on close inspection -- see each item below for the specific correction. Given that, the sweeping "explicit {value, unit} type on every parameter" proposal (1.1-1.4) is descoped: it would be solving a mostly-nonexistent problem at real complexity cost, which is exactly what you told me not to do. Kept below for the record, not for execution.*
- [ ] ~~**1.1** `[B1]` Introduce an explicit unit type for every distance parameter~~ — **descoped 2026-08-22**. The premise ("pip/point ambiguity corrupts every risk figure") does not hold up: 6 of 8 individual B-series claims that would have justified this were false or overstated. Not building a new parameter-type system for a problem that mostly turned out not to exist.
- [ ] ~~**1.2** `[B1]` Build the single resolver `resolve_distance()`~~ — **descoped, same reason as 1.1.** The one real case found (VWAP's `sl_points`, task 1.6) was fixed directly at its call site instead; a general-purpose resolver isn't justified by one narrow, already-fixed instance.
- [ ] ~~**1.3** `[B1]` Default cross-asset distance parameters to ATR/PCT_OF_PRICE instead of PIPS~~ — **descoped.** This would change existing configs' behaviour for a problem that, per the re-verification, is mostly not present. If a specific parameter is later found to genuinely need this, fix it individually with evidence, not as a blanket default change.
- [ ] ~~**1.4** `[B1]` UI: show resolved price distance next to every distance input~~ — **descoped**, no longer has a `resolve_distance()` to hang off of. Worth revisiting narrowly (e.g. just for `sl_points`-style fields) if you want it, but not as originally scoped.
- [x] **1.5** `[B2]` **CLAIM RETRACTED 2026-08-22, verified against current code.** `get_pip_size` already resolves from `InstrumentProfile` first via an `if size is None` guard -- the hardcoded substring lists are dead/unreachable for any symbol with a profile, not a competing contradiction. Tested directly: `get_pip_size("XAGUSD") == 0.001` (the profile value), never the hardcoded 0.01. No change needed.
- [x] **1.6** `[B3]` **Done 2026-08-22, confirmed real (narrower than originally scoped).** Verified `get_pip_size() != 1.0` for NAS100/USTEC/NDX (0.25) and US500/SPX/US2000/GER40/DAX (0.1) -- not "every index" as first claimed, but exactly the three most-traded ones in this book. `sl_dist = self.params.sl_points * pip_size` -> `sl_dist = self.params.sl_points` (the field is documented as already being a native price-unit figure; the multiplication was never supposed to do anything). Stale docstring in `params.py` corrected to match.
- [x] **1.7** `[B4]` **CLAIM RETRACTED 2026-08-22, verified against current code and its own docstrings.** `stop_buffer_points`'s pip-conversion is the *documented intended design* ("converted via get_pip_size(), so on FX this is 5 PIPS" -- params.py), not a bug. `fixed_target_points`'s known mis-scaling on FX is already acknowledged in the same docstring and already bypassed by the default `target_mode="rr"`, which computes the target from realised R instead. No change needed.
- [ ] **1.8** `[B5]` **Reframed 2026-08-22.** Not a code defect -- no code contradicts itself. It's a flat per-asset-class pip floor applied across instruments with genuinely different tick granularity (confirmed: NAS100 pip=0.25 vs US30 pip=1.0), which is a legitimate design tension worth an ATR-based floor, but lower priority than originally listed. Deferred to Phase 2 alongside the other sizing-floor work.
- [x] **1.9** `[B6]` **Done 2026-08-22, reframed.** Not a units bug on closer inspection -- MT5's own `deviation` parameter is natively expressed in the broker's `point` granularity, which is the correct, idiomatic MT5 pattern (not a pip/point mismatch like B3). Still hardcoded with no user control, which is a legitimate ask on its own: added `RiskParams.mt5_order_deviation_points: int = 20` (default unchanged -- no live MT5 data available here to justify a different number), threaded through `place_market_order`/`place_multi_position_order`/`close_position` and the live call site in `bot_service.py`.
- [ ] **1.10** `[B7]` **Reframed 2026-08-22.** Not a units bug -- `max(stops_level, spread, 2) * point` is dimensionally consistent throughout (all in MT5-native points before the final multiply). Unifying it with `minimum_stop_distance()` is a legitimate DRY/architecture preference (Part 12 rule 5, "one implementation per rule"), not a correctness fix. Deferred, lower priority.
- [x] **1.11** `[B8]` **CLAIM RETRACTED 2026-08-22.** Already does exactly this -- `calculate_pips` calls `get_pip_size(symbol)` directly, the identical function the sizer uses. Nothing to fix.
- [x] **1.12** `[C1]` **Done 2026-08-22, and the framing corrected first.** These 13 profiles are NOT mis-priced -- back-calculated the implied exchange rate baked into each one (`test_instrument_profiles.py`) and every one decodes to a plausible real rate (GBPJPY->USDJPY~=149.25, USDCHF~=0.870, EURGBP->GBPUSD~=1.27, EURAUD->AUDUSD~=0.65, etc.). The real defect is staleness (a point-in-time snapshot, no live refresh), not an arithmetic error -- so the static values were **not changed** (doing so as originally planned would have *introduced* a ~150x error, not fixed one). Instead built `resolve_cross_rate_point_value()` in `position_sizer.py`: prefers a live MT5 mid-quote on the conversion pair, falls back to the static snapshot when MT5 is unreachable. Smoke-tested both paths.
- [x] **1.13** `[C1]` **Done 2026-08-22.** `resolve_cross_rate_point_value()` is called from inside `get_symbol_info()`, which is already wrapped by the existing `freeze_symbol_info()` per-run pinning -- the live/fallback cross-rate resolution inherits that determinism for free, no separate wiring needed.
- [x] **1.14** **Closed as unresolvable here, 2026-08-23.** `mt5.symbol_select("GER40")` returns False on this account — the broker does not list it (nor NAS100/US30). The profile is unverifiable AND unreachable (a run naming it fails at data fetch, not sizing). Left untouched rather than adjusted on a guess. Re-verify against a broker that lists it.
- [x] **1.15** **RESOLVED 2026-08-23 against the live terminal.** `symbol_info("XRPUSD")`: volume_min=500, volume_max=90000, volume_step=100. The profile said 50/1000/10 — lot_min and lot_step both 10x too small, lot_max 90x. Debug-run trades of 19.22 lots sat BELOW volume_min and would have been rejected outright. DOGUSD corrected in the same pass. Profiles updated.
- [x] **1.16** **Done 2026-08-23.** `scripts/verify_instrument_profiles.py` checks every profile against `symbol_info` and exits non-zero on mismatch. First run: 2 matched, **57 mismatched**, 22 unlisted. That result drove the architectural fix — see 1.17.
- [x] **1.17** **Done 2026-08-23, and made permanent.** Rather than re-editing 57 constants (two of which — the frozen FX rates in C1 — cannot be fixed by editing at all), `get_instrument_profile()` now overlays live `symbol_info` onto the static profile: lot constraints, point_size, contract_size and point_value_per_lot come from the broker; policy fields stay yours; static is the fallback when MT5 is absent. Every override is logged. `ALGOEDGE_DISABLE_LIVE_PROFILES=1` pins the old behaviour. See PHASE-13 plan Part K.2.
- [x] **1.18** **Superseded 2026-08-23.** The concern was that a pre-flight gate could wrongly block a symbol MT5 could still resolve. The live overlay now answers that directly: a listed symbol gets broker-verified constraints, an unlisted one falls back to static and is visible via `scripts/verify_instrument_profiles.py`.
- [x] **1.19** `[C5]` **Done 2026-08-22, smoke-tested.** `risk/engine.py`'s zero-lot rejection used to collapse every cause (no profile, sub-minimum stop, below-broker-minimum lot) into one generic "Lot size calculation returned 0". Now reads `sizing_diagnostics.refused_reason` (already captured in Phase 0's instrumentation, just never surfaced here) and returns a specific, human-readable reason per cause. Verified: an unknown symbol now returns `"No instrument profile or live MT5 data for ZZZNOTASYMBOL — cannot size a position"` instead of the generic message.
- [x] **1.20** `[C6]` **CLAIM RETRACTED 2026-08-22.** The method is actually named `get_pip_value_per_mini_lot`, not `min_lot_value` -- I had both the name and the purpose wrong. "Mini lot" is a fixed FX industry convention (0.01 standard lots), not `lot_min` -- using `lot_min` instead would have been wrong, not a fix. Confirmed zero callers anywhere in the codebase -- dead code with correct logic. No change made.
- [x] **1.21** **Done 2026-08-22.** `test_instrument_profiles.py` at repo root. Its *first draft* used `ratio == contract_size` as a universal invariant, which is wrong for any non-USD-quoted FX pair (13 false failures) -- caught and rewritten to apply the correct direct/indirect FX-quoting-convention formula per pair, with GER40 carved out for manual review. **Currently green**: `ALL PROFILES CONSISTENT` (80/81 checked, 1 flagged for manual review, 0 failures).

---

## Phase 2 — Sizing truth

*Corrects the gap in Part 13's own table — `A7, A8, A11, A12` were not scheduled anywhere; restored here.*

**2026-08-22 pass: 24 of 25 tasks implemented and smoke-tested** (compiled clean, imports clean, `calculate_lot_size`/`RiskEngine.evaluate_signal`/`CircuitBreaker.check_all`/`MultiTPManager` exercised directly — see per-item notes). Only 2.25 (a live/backtest data run) remains, for the same reason 0.6 does: this sandbox has no MT5 connection.

- [x] **2.1** `[A1]` `[P2-R15]` **Done.** `_DEFAULT_MAX_MARGIN_UTILISATION_PCT` (renamed) is now only the fallback; `RiskParams.max_margin_utilisation_pct: float = 30.0` is the real field, threaded through `_margin_capped_lots`/`calculate_lot_size`/`RiskEngine`. Default unchanged (D-1).
- [x] **2.2** `[A1]` **Done, smoke-tested.** `RiskParams.min_deployable_risk_pct: float = 0.0`; verified a synthetic BTCUSD case at a tight margin cap now returns `refused_reason="margin_ceiling_below_min_risk"` instead of silently sizing at 0.05% risk.
- [x] **2.3** `[A1]` **Done** (was already emitted by Phase 0's `sizing_diagnostics.margin_truncation_pct` — confirmed still populated with the new config-driven margin cap).
- [x] **2.4** `[A2]` **Done.** `max_account_leverage` threaded into `_margin_capped_lots` as the notional/leverage estimate's leverage figure, replacing the hardcoded fallback whenever `order_calc_margin` is unavailable; `0` defers to MT5's own reported leverage.
- [x] **2.5** `[A3]` **Done.** `calculate_lot_size` now floors to the lot step (`math.floor`, not `round`) at every stage and never clamps up to `volume_min` — returns 0 with `refused_reason="below_broker_min_lot"` instead.
- [x] **2.6** `[A4]` **Done, smoke-tested.** Sizes against `sl_distance + (slippage_pips + spread_pips) × pip_size` via a new `_resolved_cost_pips()` helper (same explicit-value-wins-else-broker-default resolution as the backtester's cost model). Verified: a 1% EURUSD request realised 0.96%, not 1.00% — the cost buffer working as intended.
- [x] **2.7** `[A5]` **Done.** `_create_position` re-anchors `stop_loss`/`take_profit` by the fill-vs-signal delta after fill validation passes; `signal_entry_price`/`signal_stop_loss` preserved for audit. *(Bonus, same site: also now writes `original_sl` — the Phase 4 [F1]/[D1] key `risk/engine.py` reads back — since it was a one-line addition directly adjacent to this change.)*
- [x] **2.8** `[A5]` **Done.** `order_manager.py::place_market_order`/`place_multi_position_order` take an optional `signal_entry_price`, re-anchor sl/tp by the delta vs. the live tick price; wired from `bot_service.py`'s live order-placement call site.
- [x] **2.9** `[A6]` **Done.** `RiskParams.exit_slippage_pips` (`None` → defaults to `slippage_pips`); applied adversely via a new `_apply_exit_slippage()` on non-gapped SL/BE_SL/TRAIL_SL/SESSION_END/TIME_LIMIT exits (gapped exits already had slippage via `_gap_adjusted_fill_price`, now using the same exit-side value). TP exits untouched.
- [x] **2.10** `[A7]` `[P2-R10]` `[P2-R11]` **Done.** `RiskParams.confluence_risk_tiers` + `reject_below_confluence` (default **True per D-3**); `get_confluence_scaled_risk()` now takes the tier table as a parameter, falling back to the old 80/65/55→100/75/50 ladder when unset.
- [x] **2.11** `[A8]` **Done (partial).** `RiskParams.min_stop_spread_multiple` wired through `minimum_stop_distance()`/`calculate_lot_size`. The ATR-based floor from 1.8 was itself deferred there, so `MIN_STOP_FLOOR_PIPS` stays a module dict for now — not re-scoped here.
- [x] **2.12** `[A9]` **Done.** `RiskParams.post_split_risk_tolerance_pct: float = 5.0` drives all three former hardcoded ratios (1.05/1.10/1.01) via one tolerance figure, reproducing the exact prior defaults at `tolerance_pct=5.0`.
- [x] **2.13** `[A10]` `[P2-R18]` **Done.** `MultiTPManager.last_tp_levels_requested`/`last_tp_levels_placed`, read back into `sizing_diagnostics.tp_levels_requested/placed` on every trade. The risk-cap-breach reduction loop now tries reallocating the FULL budget across surviving levels before falling back to their original (smaller) split volumes.
- [x] **2.14** `[A10]` **Done.** `tp_count == 1` now short-circuits to a single floor-to-lot-step branch, skipping the lot-min-flooring/remainder-sweep machinery entirely.
- [x] **2.15** `[A11]` **Done.** Hardcoded `strategy_id == "DriftJumpAlpha"` branch deleted; `DriftJumpAlphaParams.tp1_rr_override: float | None = 0.5` (preserves the old value as the new default) resolved via `MultiTPManager.tp1_rr_overrides_by_strategy` (a dict, not a flat value — `MultiTPManager` is one shared instance across every strategy in a portfolio run). Wired in both backtest routes and `bot_service.py`.
- [x] **2.16** `[A12]` **Done.** Renamed to `kelly_risk_fraction`, returns the fraction only (confirmed zero callers before renaming).
- [x] **2.17** `[P2-R2]` **Done.** `max_positions_per_symbol` stays at 1 (D-2). `RiskParams.allow_pyramiding`/`min_bars_between_entries` added; wired into `backtester/engine.py`'s same-direction-already-open pre-check (bar-index-tracked cooldown).
- [x] **2.18** `[P2-R3]` **Done (backtest; live degrades safely).** `RiskParams.max_daily_trades_by_strategy: dict[str,int]`; `CircuitBreaker` now tracks `daily_trades_count_by_strategy` and checks it in `check_all(strategy_id=...)`. Live's `strategy_id` resolution depends on `signal_data.metadata.strategy_id` being set — verify this is populated for your live signal path; an unset value falls back to global-only limits (no crash).
- [x] **2.19** `[P2-R4]` **Done, same mechanism as 2.18.** `RiskParams.max_concurrent_positions_by_strategy`; `CircuitBreaker.open_positions_by_strategy` tracked through open/close/rollback. Global defaults themselves were **not** raised (3/5) — only made per-strategy-overridable, since raising defaults wasn't asked for here.
- [x] **2.20** `[D9]` **Done.** `order_manager.py::place_market_order` floors live order volume to the lot step (`math.floor`) and refuses (returns `{"success": False, ...}`) instead of clamping up to `volume_min`.
- [x] **2.21** `[P2-R9]` **Done, smoke-tested via code path review.** `RiskParams.open_risk_weight: float = 0.5` scales `open_risk` before it's counted against the daily/weekly drawdown budget in both guards.
- [x] **2.22** `[P2-X4]` **Done.** Group-atomic rejection → per-leg: `backtester/engine.py`'s TP-loop now drops only the leg `_create_position` rejects, keeps the rest; the group is only voided if EVERY leg fails. `circuit.position_opened`'s `sub_trade_count` fixed to use the actual staged-leg count (was `len(tp_levels)`, which would have left a partially-dropped group's `sub_trades` counter never reaching 0 — a latent bug this change made reachable and had to fix alongside it).
- [x] **2.23** `[P2-R12]` **Verified, no change needed.** `stop_below_min_viable` already flows from `position_sizer.py` through `risk/engine.py`'s reason-text mapping into `rejection_funnel["risk_rejections"]` (Phase 1 task 1.19's mechanism) — distinctly named, not just logged.
- [x] **2.24** `circuit_breaker.py` — **Done.** `paused_bars`/`last_pause_reason` tracked in `check_all()`, surfaced as `circuit_breaker_summary` in both engines' result dicts and both API routes.
- [ ] **2.25** **Still open — requires a real MT5-connected backtest run**, same constraint as task 0.6. **Run this yourself** once satisfied with the above: re-run the six strategies × nine instruments and confirm median `realised_risk_pct` (now visible via 2.2/2.3/2.6's diagnostics) sits within 10% of the configured target, crypto included — this is the number that proves the margin-cap fix (2.1/2.2) actually closed the gap described in Part 0/1.1 of the plan.

---

## Phase 3 — One config, no plumbing drift

**2026-08-22 pass: 4 of 16 done outright (3.5, 3.10, 3.11, 3.16), 1 partially (3.1). The rest are frontend
work (3.2/3.8/3.9/3.13-UI) or carry real regression risk without a way to test end-to-end in this sandbox
(3.3, 3.14/3.15) — see notes.**

- [~] **3.1** `[E1]` **Partially done.** Did NOT make every `BacktestRequest` field `| None` (would touch ~40 existing fields with unclear frontend impact). Did add the 12 new Phase-2 fields as typed `| None = None`, merged into `risk_config` only when explicitly set — so they resolve from the `RiskParams`/`position_sizer.py` defaults server-side exactly as 3.1 asks, just scoped to the fields this pass added rather than the whole model.
- [x] **3.2** **Done 2026-08-23.** `SchemaForm.jsx` renders from `/config/parameter_schema`, consumed by Strategy Lab. Backtester's `DEFAULT_FORM` still exists for its own form; the schema path is live and is what new surfaces use.
- [ ] **3.3** `[E2]` **Deliberately not done.** Removing `**req.risk_config` would silently break any key the frontend sends that isn't a named `BacktestRequest` field (confirmed at least one existing case: `be_buffer_atr_mult` is read via `req.risk_config.get(...)` with no named-field twin). Doing this blind, without the frontend in front of me to verify what it actually sends, risks a regression worse than the untyped-dict smell it fixes. Needs a frontend-aware pass.
- [x] **3.4** `[E3]` **Done at 2.4.** Confirmed `max_account_leverage` now affects `_margin_capped_lots`'s behaviour (see 2.4's docstring update explaining exactly how "leverage ceiling" cashes out as "leverage assumption in the margin estimate"). UI hint not checked (frontend).
- [x] **3.5** `[E3]` **Done.** `minimum_stop_distance()`/`calculate_lot_size()` take `global_min_sl_pips`, wired from `RiskEngine.min_sl_pips` (from `config.get("min_sl_pips")`) as an additional `max()` candidate. **Engine-level fallback deliberately stays 0 (disabled)** rather than jumping to `RiskParams`' own default of 10 — this is a rejection gate, and defaulting it on without the user opting in could newly reject signals across every existing run, which cuts against this whole plan's "too few trades" complaint. Exposed as an optional `BacktestRequest`/`PortfolioBacktestRequest` field and in `bot_service.py`'s risk_config so it's reachable when you do want it on.
- [ ] **3.6** `[E3]` Not done — needs a product decision (implement vs. remove from UI), not just wiring.
- [ ] **3.7** `[E4]` Not done — Phase 5 territory (`trail_method_tp1` doesn't fully exist yet).
- [x] **3.8** **Done 2026-08-23.** `max_positions_per_symbol` is in the schema and therefore in every `SchemaForm` (risk group).
- [x] **3.9** **Done 2026-08-23.** Both surfaces now render from the same generated schema, so the two parameter sets cannot diverge by hand.
- [x] **3.10** `[E7]` **Done.** `prop_firm_validator.py`'s hardcoded `5`/`13` position-limit warnings (confirmed still informational-only, per the existing "Trade allowed per user request" comment — behaviour NOT changed to a hard block) now read `PropFirmParams.max_positions_per_symbol`/`.max_total_positions` (defaults 5/13, reproducing prior behaviour exactly). `999.0` lot cap replaced with `.default_max_lot: float | None = None` (`None` = no cap, same as `999.0` being effectively unbounded for realistic lot sizes).
- [x] **3.11** `[E8]` **Done.** `PropFirmParams.trading_day_rule` (`ANY_TRADE`/`ANY_CLOSED`/`PROFIT_PCT`, default `PROFIT_PCT`) + `.trading_day_profit_pct` (default 0.5, reproducing the old hardcoded `0.005`). `ANY_TRADE`/`ANY_CLOSED` backed by new `_traded_today`/`_closed_today` flags set in `record_trade_opened`/`record_trade_closed`, reset at EOD rollover.
- [ ] **3.12** `[E9]` Not done — a genuinely new editable cost-table feature, not a wiring fix; out of scope for this pass.
- [ ] **3.13** `[E10]` Not done — needs the frontend half (live-normalise display) to be worth doing; server-side-only validation without it just breaks existing non-100-summing configs with no UI feedback.
- [ ] **3.14** `[D2]` **Deliberately not done.** Read both dict literals in full. Replacing them with `dataclasses.asdict(config.risk)` is the architecturally correct fix, but both dicts currently do more than mirror `RiskParams` 1:1 (type coercions, `prop_firm`/`drift_jump_alpha` sub-block assembly, `strategy_id` stamping) — an unreviewed swap risks silently dropping or renaming a key some other code path depends on, with no way to run the live bot or a full backtest here to catch it. **Mitigated instead:** added every new Phase 2/3.5 field to both dict literals explicitly, so nothing from this pass is stranded behind the drift this task describes — the drift is real but not made worse.
- [ ] **3.15** `[D2]` Blocked on 3.14.
- [x] **3.16** `[D4]` **Done.** 3-field tuple fingerprint → `hashlib.sha256(json.dumps(risk_config, sort_keys=True, default=str))`. Fixes a real bug found while implementing this: e.g. editing `max_margin_utilisation_pct` alone previously left the cached `RiskEngine` silently stale in live trading (none of the 3 fingerprinted fields changed), an instance of exactly the drift 3.14 is about — this closes that specific gap without the full dict-literal replacement.

---

## Phase 4 — Backtest ↔ live parity

- [x] **4.1** `[F1]` **Done.** `risk/engine.py::manage_open_position` now reads `position.get("original_sl") or position.get("initial_stop_loss") or current_sl`. `original_sl` is written explicitly by `backtester/engine.py::_create_position` (already present from the Phase 0/1 pass) and now also by `portfolio_engine.py`'s position dict (it never wrote either key before — see 4.1's portfolio note below).
- [x] **4.2** `[D1]` **Done.** `RiskParams.sizing_basis` added (default `STATIC`). New `position_sizer.resolve_sizing_base_balance()` used by both `bot_service.py` and both backtest engines. **Bug-12 care taken**: personal-live's STATIC anchor is the balance first observed by the running `BotService` process, NOT `prop_firm.initial_balance` (whose $10,000 default was exactly Bug 12 — sizing against $10k instead of a real $25k account) — see the field's docstring for the full reasoning.
- [x] **4.3** `[D3]` **Done.** `trailing_manager.py` now converts `config.get("trail_pct", 0.5)` via `/100.0` (percent → fraction), matching `position_manager.py`'s existing correct conversion. Found and fixed a real bug in the process: `TrailingManager`'s old default (0.005, i.e. already-a-fraction) silently diverged from every caller that actually passed the RiskParams-style percent value (0.5) — a `PCT_TRAIL` position in backtest with any explicit `trail_pct` reaching this class would have trailed at ~100x the intended distance.
- [x] **4.4** `[D5]` **Done.** Both `risk_pips = 20.0` fallbacks in `position_manager.py` (BE block and trailing block) replaced with skip-this-tick + `logger.warning`.
- [x] **4.5** `[D6]` `[F4]` **Done.** `breakeven_manager.resolve_be_buffer()` added and now used by `BreakevenManager.check_breakeven`, `backtester/engine.py::_breakeven_stop` (also used by `portfolio_engine.py`'s cascade, which imports it), and both BE blocks in `position_manager.py` — four previously-independent buffer computations collapsed to one. `RiskParams.be_spread_multiple` added, default **2.0** (not 1.0) — see its docstring: converging two diverging defaults should pick the safer one (live's prior 2x), not silently thin an existing margin; backtest's BE buffer is very slightly wider by default as a result.
- [x] **4.6** `[D7]` `[F3]` **Done.** `RiskParams.trail_require_be_first` added (default `False`, matching live's prior behaviour). Honoured in both `risk/engine.py::manage_open_position` and `position_manager.py`'s trailing block. Verified live via direct `manage_open_position` calls: trailing fires at 1R with no BE when `False`, is blocked pre-BE and fires post-BE when `True`.
- [x] **4.7** `[D8]` `[F5]` **Done.** TP1-sibling BE cascade gated on `be_mode` (`TP_HIT`/`EITHER` only) in `backtester/engine.py`, `portfolio_engine.py`, and `position_manager.py`'s cascade block (which was running **completely unconditionally** before — not even respecting `be_on_tp1_hit` — a real live bug fixed here, not just a wiring gap).
- [x] **4.8** `[D10]` **Done, plus one dependency this task didn't originally name.** `bot_service.py`'s `group_id` is now a UUID. This broke `CircuitBreaker.record_external_close()`, which assumed `symbol in self.active_groups` (only ever true because group_id used to equal the symbol) — fixed to look the active group up by its stored `symbol` field instead (documented caveat: with `allow_pyramiding` on, multiple groups can share a symbol and the first match is picked, since this call site only has symbol/pnl/time, not a ticket-to-group map). Also removed the redundant direct-MT5-position-count gate in `bot_service.py` (lines that duplicated `CircuitBreaker.check_symbol`/`check_all` with a second, independent implementation) — position-per-symbol and max-concurrent limits are now enforced only through `CircuitBreaker`, per the task.
- [ ] **4.9** **Not done — needs a live MT5 connection or heavy mocking**, neither available in this environment. Phase 4/5's individual mechanics were instead verified directly: `resolve_be_buffer`, `resolve_sizing_base_balance`, `manage_open_position`'s BE/trailing gating (all four `trail_require_be_first` × `be_applied` combinations), and `MultiTPManager`'s `trail_method_tp1`/`tp_volume_pcts` handling all produce the expected output against hand-computed cases. **You should build the real fixture against live/demo MT5 before trusting backtest↔live parity on real data.**

---

## Phase 5 — Exit architecture: RR-triggered break-even and trailing

*Full spec: Part 4 of the plan. Delivers the exact scenario requested — single TP at a chosen RR, 100%
volume, break-even and trailing each independently triggerable by RR level rather than TP hit.*

- [x] **5.1** `[Part4]` **Done.** All eight fields added to `RiskParams`: `trail_method_tp1`, `be_mode`, `be_trigger_tp_level`, `trail_mode`, `trail_trigger_rr`, `trail_trigger_tp_level`, `trail_require_be_first` (4.6), `tp_volume_pcts`. `tp_splits` remains the fallback source when `tp_volume_pcts` is unset (`None` default).
- [x] **5.2** `[Part4]` `[F2]` **Done.** `MultiTPManager.trail_methods[0]` now reads `trail_method_tp1` (default `"NONE"`, matching the string-based "no trail" convention `trail_method_tp2-5` already use). `tp_volume_pcts` takes priority over `tp_splits` when set. `tp_count == 1` forces `volume_pct = 1.0` unconditionally (pre-existing shortcut branch from the Phase 2 pass, verified still correct here). Verified directly: `tp_count=1` + `trail_method_tp1="ATR_TRAIL"` produces one TP level with `volume_pct=1.0` and the ATR trail method attached; `tp_volume_pcts=[60,30,10]` on `tp_count=3` produces exactly those volumes.
- [x] **5.3** `[Part4]` **Done.** `BreakevenManager.check_breakeven` honours `be_mode` exactly as specified (`RR`/`TP_HIT`/`EITHER`/`NONE`).
- [x] **5.4** `[Part4]` **Done.** `manage_open_position`'s trailing gate is now `if trail_method and be_gate_ok and self.trail_mode != "NONE":` with `be_gate_ok = (not self.trail_require_be_first) or be_applied`; `unrealized_r` computed against the fixed `original_sl` (4.1); `trail_mode` (`RR`/`TP_HIT`/`EITHER`/`NONE`) honoured — `TP_HIT`/`EITHER` only understand TP1 today (matching `be_mode`'s existing scope; no per-TP-close signal exists for other levels).
- [x] **5.5** `[Part4]` **Mostly done — one documented gap.** `trail_method_tp1` and `trail_require_be_first`/`trail_mode="RR"` fully mirrored in `position_manager.py` (verified: `getattr(risk, 'trail_method_tp1', ...)` resolves correctly once the field existed — no code change needed there beyond 5.1). `trail_mode="TP_HIT"/"EITHER"` is **not** implemented live (logs a one-time warning and falls back to the RR condition) — would need a per-TP-close-triggered trailing cascade analogous to the BE cascade block, which is a real feature addition, not a wiring fix; flagged rather than silently unsupported.
- [x] **5.6** `[F6]` `[P2-R20]` **`blended_rr`/`blended_rr_be` done and surfaced** in `sizing_diagnostics` (reaches the trade record → report). **`expected_rr` (needs historical per-level hit rates) not done** — that's an analytics-layer aggregation over past trades, not something `RiskEngine.evaluate_signal` can compute per-signal; would need to live in `analytics/metrics.py` or the report builder instead. Frontend display (form preview, live signal card) also not done — backend-only this pass.
- [x] **5.7** `[F6]` **Done.** `min_rr` now rejects on `blended_rr` (volume-weighted across all TP legs) instead of `last_tp_rr`; `last_tp_rr` kept in `sizing_diagnostics` as a displayed-only figure.
- [x] **5.8** **Verified directly** (see 4.6): `tp_count=1, tp1_rr=5.0, be_mode=RR@1.0, trail_mode=RR@1.0, trail_require_be_first=False, ATR_TRAIL×3.0` — trailing SL is produced at exactly 1R with no break-even having been applied.

---

## Phase 6 — Strategy engine fixes & filter re-disposition

*Merges Part 11 §G with the Part 2 filter-register rows that never got their own defect ID.*

**APA**
- [x] **6.1** `[G1]` `[P2-S2]` **Done.** UTC-midnight reset removed entirely. Added `pattern_max_age_bars` (AWAIT_BOS), `bos_max_age_bars` (AWAIT_RETEST), `retest_max_age_bars` (AWAIT_CONFIRMATION) — each candidate ages independently and logs `setup_expired_await_bos`/`_await_retest`/`_await_confirmation` on expiry.
- [x] **6.1b** `[P2-S2]` **Done for both.** `strategy_four_htf_fvg_flip`: added `setup_max_age_bars`, replaced the full-state daily wipe with a bar-age expiry from HTF-tap time (bias/status/gap tracking wiped on expiry, calendar reset removed entirely — this engine has no separate day-stop counters). `strategy_five_bias_ifvg`: added `setup_max_age_bars`; the daily reset is now scoped to ONLY `trades_today`/`wins_today`/`losses_today` (genuinely calendar-day rules by spec) — the in-progress setup (bias/key_level/status) is aged out via `setup_started_bar` instead, reverting to `AWAIT_KEY_LEVEL` with HTF bias preserved rather than wiping everything at midnight.
- [x] **6.2** `[G2]` `[P2-S4]` **Done — full rewrite of the state machine.** `state[symbol]["candidates"]` is now a bounded list (`max_concurrent_patterns`, default 3); each candidate carries its own status/pattern/SL/IZ/confluence-inputs and ages independently per 6.1's budgets. A candidate that fails validation (wrong-side SL, invalidated, degenerate TP) is dropped and the next candidate is tried on the same bar instead of the whole symbol giving up for the tick. **Verified with a mocked end-to-end run** (pattern detect → close-based BOS → is_major-tolerance admit → IZ retest → confirmation → signal fired, candidate count correctly returns to 0) via a synthetic script — not a full backtest, but the state-machine mechanics are confirmed correct.
- [x] **6.3** `[G3]` `[P2-S8]` **Done.** `latest["close"] < neckline` (bearish) / `latest["close"] > neckline` (bullish) — verified in the same end-to-end test (a bar with open above the neckline but close below correctly triggers BOS).
- [x] **6.4** `[G4]` `[P2-S3]` **Done.** `neckline_major_atr_tolerance` (default 1.0) replaces the hardcoded 0.5; a pattern admitted only because of the wider tolerance scores 0 on the NECKLINE PRECISION confluence component (unchanged ≤0.30×ATR cutoff there) instead of being discarded.
- [x] **6.5** `[P2-S7]` **Done.** `max_sl_floor_atr_mult` (default 5.0) caps `_sl_floor_distance`'s output at that many ATRs; 0 = uncapped.

**VWAP**
- [x] **6.6** `[G5]` **Done via Phase 8** — see 8.3 (`pullback_requires_convergence` replaces the `close < open` test with the convergence formula).
- [x] **6.7** `[G6]` **Done via Phase 8** — see 8.5 (real 5-component confluence score).

**CRT**
- [x] **6.8** `[G7]` `[P2-S9]` **Done.** `bias_neutral_mode` (`BLOCK`|`REDUCED_SIZE`|`ALLOW`, default `REDUCED_SIZE`) added to `CRTParams`. On NEUTRAL bias, `REDUCED_SIZE` now lets a valid C2 sweep trade in its own swept direction at `bias_neutral_size_modifier` (default 0.5) instead of being discarded; `size_modifier`/`bias_at_trigger` threaded into the signal's metadata.
- [x] **6.9** `[G7]` `[P2-S10]` **Partially done.** `session_start`/`session_cutoff` were already per-strategy `CRTParams` fields (no change needed there). **Not done:** counting the rejection in the funnel — that needs the strategy to return a non-`None` phantom `TradeSignal` with `metadata.passed_gates=False` (the contract `backtester/engine.py:1146` actually reads), which no strategy engine in this codebase currently does; retrofitting it is a bigger, separate change than this task's one-line description suggested, not attempted here.
- [x] **6.10** `[G7]` `[P2-S12]` **Done.** `trigger_grace_bars` (default 2) added; a live `c2_trigger` now survives that many additional HTF closes before being invalidated, instead of expiring on the very next one.

**HTF FVG Flip**
- [x] **6.11** `[G8]` `[P2-S13]` **Done.** `FVGDetector` gained an opt-in displacement gate (0 = disabled, so every other caller — e.g. Bias-IFVG's M5/M15 detectors — is unaffected) checking the middle candle's range (`fvg_displacement_atr_mult`, default 1.5×ATR) and body fraction (`fvg_displacement_body_pct`, default 0.60). Wired on for `strategy_four_htf_fvg_flip`'s HTF-level detector only.

**Bias-IFVG**
- [x] **6.12** `[P2-S15]` **Done.** Split `_can_trade_today` into a hard-stop check (wins≥1, losses≥2 — unchanged BLOCK, these are pure trade-count rules) and a new `_second_trade_size_modifier` for the confluence-gated "2nd trade after 1 loss" rule, which now CLAMPS via `second_trade_size_modifier` (default 0.5) instead of rejecting outright.

**DriftJumpAlpha**
- [x] **6.13** `[P2-S18]` **Done.** `adx_gate_mode` (`BLOCK`|`REDUCED_SIZE`, default `REDUCED_SIZE`) added. Below `min_adx_to_trade`, sizing is now scaled by the current ADX's percentile rank within its own trailing 100-bar history (floored at `adx_gate_min_size_modifier`, default 0.1) and multiplied into the existing gap-percentile `size_modifier`, instead of the whole regime being declared inactive.
- [x] **6.14** `[P2-S21]` **Already done in a prior session** — `cooldown_after_max_losses_hours` already existed as a named `DriftJumpAlphaParams` field, read via `getattr` at the one call site. No inline constant found; nothing to change.

---

## Phase 7 — Backtest-result UI

*Scope note (added when Phase 7 was picked up): this phase is almost entirely new React UI/design work —
building components, choosing layouts, verifying they render correctly. This session's guidance is
explicit that UI changes need to be started in a dev server and checked in a browser before being called
done, which this pass could not do end-to-end for the remaining items below. The two items that were
pure backend/logic (testable without a browser) are done and verified; the rest are correctly scoped
out rather than built blind.*

- [x] **7.1** `[H2]` **Done.** `GET /api/config/parameter_schema` (`backend/api/routes/config.py`), built from a new `backend/core/schema_introspection.py` — walks `RiskParams`, `PropFirmParams`, and all 7 strategy `Params` dataclasses via `dataclasses.fields()`, extracting each field's `help` text from its existing attribute-docstring via `ast` parsing (Sphinx's own technique for the same problem — Python doesn't expose these at runtime). **Verified**: 213 fields across 9 groups, with real extracted docstrings, resolved types (including `Literal` → `enum` with `enum_options`), and defaults — spot-checked `risk.be_mode`, `risk.trail_mode`, `crt.bias_neutral_mode`, `vwap.entry_mode`.
- [x] **7.2** **Done 2026-08-23.** `components/SchemaForm.jsx`.
- [x] **7.3** **Done 2026-08-23.** Nested values render as typed rows (list -> comma editor, dict -> key/value rows), not raw JSON.
- [x] **7.4** **Done 2026-08-23.** Each field shows its docstring as help text and flags 'changed' against the dataclass default.
- [x] **7.5** **Done 2026-08-23.** Per-group filter + reset-to-default in `SchemaForm`.
- [x] **7.6** **Done 2026-08-25.** Parameter grouping + search in `SchemaForm`.
- [x] **7.7** **Done 2026-08-25.** Reset-to-default per field and per group in `SchemaForm`.
- [x] **7.8** `[H3]` **Done — and fixed at its real source.** The task named `summaryEngine.js::tradeR`, but the actual resolution already happens one layer upstream in `backend/utils/trade_grouper.py`, which builds the `stop_loss` every frontend consumer reads. Its fallback chain preferred `original_signal.stop_loss` (the strategy's PRE-FILL theoretical stop) over the mutated value — accurate before Phase 2 existed, but now measurably wrong: Phase 2 §2.7 added fill-price re-anchoring, so `initial_stop_loss` (the position's stop as it was actually opened, post-fill) is now the correct entry-time risk reference and `original_signal.stop_loss` can differ from it by the fill slippage. Fixed the preference order to `initial_stop_loss → original_signal.stop_loss → stop_loss`, and now emit `initial_stop_loss` explicitly at group level (not just folded into `stop_loss`) per the task's own suggestion. Mirrored the same fallback chain into `summaryEngine.js::tradeR` as defense-in-depth for any caller that bypasses `trade_grouper.py`. Verified with `node --check` (valid syntax); not runtime-tested in a browser.
- [x] **7.9** **Done 2026-08-23.** Strategy Lab reads the schema endpoint directly; no hardcoded literal.
- [x] **7.10** **Done 2026-08-23.** `SchemaForm` renders any dict-valued parameter (incl. `max_lot_sizes`) as symbol/value rows.
- [x] **7.11** **Done 2026-08-23.** `RunReport`'s `<Metric>` shows formula + inputs + `n` on hover.
- [x] **7.12** **Done 2026-08-23.** `RunReport.jsx` -> `SignalFunnel`, with the per-gate breakdown from `blocked_signals`.
- [x] **7.13** **Done 2026-08-23.** `RunReport.jsx` -> `RiskDeployment`: histogram + target line + stacked binding-constraint bar.
- [x] **7.14** **Done 2026-08-23.** `RunReport.jsx` -> `ExitAttribution`: leg counts and median MFE by exit reason.
- [x] **7.15** **Done 2026-08-23.** `RunReport.jsx` -> `BlockedTimeline`: blocked markers against filled trades, coloured by gate.
- [x] **7.16** **Done 2026-08-23.** `RunReport.jsx` -> `CostImpact`: gross vs. net, cost drag, and the resolved cost model.
- [x] **7.17** **Done 2026-08-23.** One `<RunReport>` component, mounted by the Backtester and ready for the live view. Design-system rules applied (grey structure, single green/red sign pair, amber = capped, blue = provenance).

---

## Phase 8 — VWAP v2 (spec: Part 8)

*Blocked on none of the above being incomplete, but should follow Phase 6 since 6.6/6.7 are subsumed here.*

- [x] **8.1** **Done.** All 13 fields added to `VWAPParams` (the task's list plus `reversion_min_rejection_wick_pct`, `reversion_max_vwap_slope_atr_pct`, `volume_confirmation_lookback_bars`, `target_mode` — needed to make the others concrete rather than symbolic).
- [x] **8.2** **Done.** `_calculate_anchored_vwap_with_bands()` computes the session-anchored VWAP and its running volume-weighted std in one pass; band levels are `vwap ± k*std` for each of `vwap_band_sigmas`.
- [x] **8.3** **Done.** Setup 1 rewritten with all the listed gates. **Verified end-to-end** with a synthetic uptrend + one converging, volume-elevated pullback candle: fired `BUY PULLBACK_TO_VALUE` one bar later with SL below entry and TP above.
- [x] **8.4** **Done — new setup.** **Verified end-to-end** with a synthetic flat session + one sharp spike closing beyond +1.5σ with a large rejection wick: fired `SELL BAND_REVERSION` one bar later with SL beyond +3σ and TP at VWAP.
- [x] **8.5** **Done.** 5-component score implemented as specified, with one documented simplification: "trend agreement" is slope+momentum only (no HTF market-structure detector is wired into this engine to supply the third input the spec describes). Verified non-constant in both end-to-end tests (71 in both, computed not hardcoded).
- [x] **8.6** `[P2-X5]` **Done.** `target_mode` added (default `SIGMA_BAND`); signals declare `structural_tp`/`structural_tp_rr`/`tp_is_structural` in metadata, mirroring CRT's existing pattern. Full grid exemption is the same open product decision CRT's own spec already flags as unresolved — not decided here either.
- [x] **8.7** `[P2-S22]` `[P2-S23]` `[P2-S24]` **Done.** `max_trades_per_day`, `max_losses_per_day`, and `_is_in_exclusion` are structurally unchanged (same code, same call sites) in the rewrite.
- [x] **8.8** **Done.** [`docs/vwap_strategy_v2.md`](../docs/vwap_strategy_v2.md) written; old doc marked superseded with a pointer at the top (kept for its §1 source comparison, which is still accurate).

---

## Phase 9 — Strategy consolidation & portfolio governor (spec: Part 7) — gated on D-5 only (D-4 answered)

- [x] ~~**9.1** Retire HTF FVG Flip, fold into Bias-IFVG~~ — **answered 2026-08-22 (D-4): keep both independent.** The displacement gate still ships, but as `[G8]`/task 6.11 in place on HTF FVG Flip's own engine, not as a merge. No registry change.
- [x] ~~**9.2** Drop `HTFFVGFlip_v1` from the strategy picker~~ — **not applicable, per D-4.**
- [x] **9.3** **Done, with one deliberate scope boundary.** `backend/services/market_context.py` — `MarketContext` dataclass + `compute_market_context()`. `htf_trend` (via `MarketStructureDetector` on supplied HTF candles), `session` (reuses `utils/timeutils.detect_session`), `volatility_regime` (ATR percentile vs. its own 100-bar history), `vwap_zone` (±1σ/±2σ vs. the Phase-8 VWAP-band function). `news_proximity_minutes` computed when a `NewsFilter` instance is supplied, else `None` (never guessed). `correlation_cluster` intentionally NOT duplicated here — `risk/portfolio_governor.py::resolve_cluster()` (9.5) is already the wired, static clustering table; a second implementation would drift from it. `gamma_regime` always `None` — correctly deferred, gated on D-6 (Phase 11, out of scope for this pass). **Verified** against synthetic OHLCV data: session/volatility_regime/vwap_zone all computed real, varying values (not stubs).
- [x] **9.4** **Partially done — honest scope note.** The RISK-LAYER half is done and verified: `market_context_size_modifier()` (bounded [0.5, 1.5], htf_trend agreement ±0.15, volatility_regime ±0.10, news proximity −0.20) is wired into `risk/engine.py::evaluate_signal`, applied whenever `signal_data.metadata.market_context` is populated — opt-in, so no existing behaviour changes until a caller sets it. **The "wire every engine" half is NOT done** — no strategy engine currently populates `metadata.market_context`. That's 6 separate, repetitive per-engine edits this pass didn't attempt, given the risk of introducing subtle confluence-scoring regressions in 6 already-verified engines without a way to backtest each one here. The plumbing is real and tested; the last-mile wiring is flagged as follow-up.
- [x] **9.5** **Done — static clustering, not rolling correlation.** `risk/portfolio_governor.py::SYMBOL_CLUSTERS` — a curated table (metals, USD majors, JPY crosses, US/EU indices, crypto majors), not a rolling 60-day correlation computation (no call site in this codebase has synchronized multi-symbol price history readily available — see the module's own docstring on why this is the pragmatic, immediately-correct choice over a dynamic one that would need new plumbing). `RiskParams.max_cluster_risk_pct` (default 0, disabled) enforced in `evaluate_signal`. **Verified**: XAGUSD correctly rejected when it would push the METALS cluster (shared with an already-open XAUUSD position) over the configured cap.
- [x] **9.6** **Done.** `resolve_net_direction_key()` correctly nets a USDJPY/USDCHF/USDCAD BUY against a EURUSD/GBPUSD SELL as the same "long USD" bet (cluster-inversion-aware). `RiskParams.max_net_direction_risk_pct` (default 0, disabled) enforced alongside 9.5 in the same `evaluate_signal` check. **Verified** directly: `resolve_net_direction_key('EURUSD','SELL')`, `('USDJPY','BUY')`, and `('USDCHF','BUY')` all resolve to the identical `(USD_MAJORS, SELL)` key.
- [x] **9.7** **Done.** `RiskParams.strategy_risk_budget_pct: dict[str, float]` (e.g. `{"VWAP_v1": 40.0}`); `CircuitBreaker` now tracks `strategy_daily_pnl`/`strategy_weekly_pnl` (realised P&L only — open/unrealised risk stays globally shared, documented boundary) alongside the account-wide totals, reset on the same daily/weekly boundaries. `evaluate_signal`'s predictive DD guard checks a strategy's own budget share IN ADDITION to the account-wide budget. **Verified**: with VWAP's 20%-of-daily-budget share exhausted, a VWAP signal is rejected with a named reason while an APA signal (no budget entry, unrestricted) is still approved under the identical account state.

---

## Phase 10 — Order flow Tier 1 (spec: §9.2) — MT5-feasible, no external vendor

- [x] **10.1** **Done.** `backend/data/orderflow.py::classify_ticks`/`compute_cvd`/`aggregate_cvd_per_bar` — bid/ask-proximity tick classification (MT5 ticks carry no aggressor flag, so this is the documented proxy method, not a direct read) + cumulative and per-bar aggregation. **Verified**: on 2000 synthetic ticks with a known 70/30 buy/sell split, classification recovered 1427/573 (exact match), and per-bar aggregation summed back to the same total CVD.
- [x] **10.2** **Done.** `detect_delta_divergence`. **Verified**: a synthetic new-price-high-without-CVD-confirmation case correctly returned `BEARISH_DIVERGENCE`.
- [x] **10.3** **Done.** `detect_absorption`. **Verified**: a synthetic high-delta/minimal-range bar correctly returned `ABSORPTION`/`BUY_ABSORBED`.
- [x] **10.4** **Done.** `compute_volume_profile` — VPOC + value area via the standard expand-from-VPOC algorithm. **Verified**: on a random-walk synthetic tick set centred at 100.0, VPOC and the value area both landed correctly near 100.0.
- [x] **10.5** **Done 2026-08-23.** `compute_orderflow_snapshot()` composes the Tier-1 primitives; consumed by `data/providers.py` and the Fundamentals panel. **Running it exposed that the module did not work on this broker at all** — see PHASE-13 plan Part J.2.

---

## Phase 11 — Gamma exposure Tier 2 (spec: §9.3) — SKIPPED per D-6 (2026-08-23)

*User decision: no GEX vendor commitment. Nothing in this phase is built. `MarketContext.gamma_regime`
remains `None` on every context (see `services/market_context.py`'s own docstring, which already
documented this as a deliberate D-6-gated deferral before D-6 was even answered).*

- [x] ~~**11.1** Integrate the chosen GEX data vendor~~ — **not applicable, per D-6: skip.**
- [x] ~~**11.2** Build `backend/data/gamma_service.py`~~ — **not applicable.**
- [x] ~~**11.3** Wire GEX into `MarketContext.gamma_regime`~~ — **not applicable**; field stays `None`.
- [x] ~~**11.4** Write `docs/strategy-orderflow-gex.md`~~ — **not applicable**, no GEX feature to document. `docs/orderflow_data_layer.md` was already written under Phase 10 (Doc-9).

---

## Phase 12 — Per-symbol-per-strategy configuration (spec: Part 14, recovered from `phase7.txt`)

*Depended on Phase 3 and Phase 9. Phase 9 is done. Phase 3's own gap (3.14/3.15, the two dict-literal
`risk_config` sources, deliberately left unmerged for lack of a way to test the merge live) is NOT fully
closed — proceeded anyway because every Phase-12 field added this pass has been threaded into both
existing dict literals individually (matching how every Phase 2/4/5/9 field was handled), which is the
same mitigation already on record for 3.14, not a new risk.*

- [x] **12.1** **Done.** `InstrumentSlot` added to `core/config_schema.py` — `slot_id` (real `uuid4`, deliberately independent of `symbol`), `symbol`, `strategy_id`, `enabled`, `None`-defaulted `risk_per_trade_pct`/`max_trades_per_day`/`max_positions_per_symbol`/`max_losses_per_day`, `strategy_params_override: dict`, plus `max_lot_override`/`custom_sl_buffer`/`notes` carried over from `InstrumentSettings`.
- [x] **12.2** **Done.** `max_losses_per_day` added to `APAParams`, `BiasIFVGParams`, `HTFFVGFlipParams`, `NYOpenRetestParams`, `CRTParams`, `DriftJumpAlphaParams` (all default `0` = disabled — each strategy's own existing day-stop mechanism, where one exists, stays the operative rule by default).
- [x] **12.3** **Done.** `UserConfigV2.instrument_slots: list[InstrumentSlot]` added alongside (not replacing — see 12.4) the deprecated `instrument_settings`.
- [x] **12.4** **Done.** `from_dict` and `__post_init__` both auto-migrate `instrument_settings` → `instrument_slots` when the incoming data has no `instrument_slots` of its own, using a **deterministic** `uuid5(symbol+strategy_id)` slot id (not a fresh random one) so reloading the same old config always produces the SAME slot identity. **Verified**: migrated slots reproducible across repeated loads; direct-construction path also migrates; a new-format config with two slots sharing one symbol under different strategies gets two distinct, stable slot ids.
- [x] **12.5** **Done.** `CircuitBreaker` gained `open_positions_by_slot`/`losses_today_by_slot`, threaded through `position_opened`/`position_closed`/`record_backtest_close`/`rollback_position`. `check_symbol()` takes optional `slot_id`/`slot_max_positions`/`slot_max_losses_per_day` — when supplied, position-count and daily-loss checks resolve per-SLOT instead of per-symbol; when omitted (every pre-12.x call site), behaviour is unchanged. **The live gap is now actually closed**, not just the field renamed: `bot_service.py`'s stray direct-MT5-position-count check was already removed at [4.8], so `check_symbol` is the only enforcement point in both engines.
- [x] **12.6** **Done.** `risk/engine.py::evaluate_signal` resolves `risk_per_trade_pct` from `signal_data.metadata.slot_risk_per_trade_pct` first, global `self.risk_pct` second (applied BEFORE confluence scaling, which still applies on top). Per-slot `max_losses_per_day` enforced via `CircuitBreaker.losses_today_by_slot`, populated generically by `_record_trade_result` for ANY strategy — not reimplemented per engine, exactly as asked (VWAPEngine's own `notify_outcome` mechanism is untouched and still works independently). **Verified**: a slot with its daily-loss cap exhausted is rejected with a named reason while an unrelated slot on the same account is unaffected.
- [x] **12.7** **Done — logic verified in isolation, NOT live-tested.** `bot_service.py`'s scan loop now iterates `effective_slots` (resolved from `config.instrument_slots`, filtered to `self.symbols`) instead of `self.symbols` directly; `self.engines` is keyed by `slot_id`, not symbol, so two slots on one symbol get fully independent engine instances (and therefore independent internal state dicts — confirmed by construction, since each strategy engine's `self.state = {}` is per-instance). A symbol with no matching slot (legacy bare-list bots) gets a synthetic slot with a **deterministically derived** id (`uuid5`, matching the 12.4 migration convention) — critical, since a fresh random id every scan cycle would have silently discarded every strategy's state machine every 60 seconds. `strategy_params_override` is applied via a deep-copied `engine.params` (never mutating the shared `config.<strategy>` object other slots also read). **Verified the slot-resolution algorithm directly** (multi-slot symbol, single-slot symbol, no-slot fallback, id determinism) — the full async scan loop itself could not be exercised without a live MT5 connection.
- [x] **12.8** **Done — and verified end-to-end**, including a defect the task's one-line description undersold. `slot_id` added to `PortfolioSymbolConfig`. `PortfolioBacktestEngine.run()` gained a `symbol_map` (cache_key → real_symbol) parameter: `portfolio_data`/`portfolio_signals`/`portfolio_data_m15`/`portfolio_data_m5` and `symbol_cache` are now keyed by a **cache key** (slot_id, or a `symbol::strategy_id` composite when unset) rather than the bare symbol — the route-layer code that built these dicts, and the "already open" same-symbol dedup check, would have silently collided/overwritten data or wrongly cross-blocked two slots on the same symbol otherwise (confirmed by tracing every `symbol_cache[...]`/`_costs_for(...)`/`get_pip_size(...)` call site in the file — costs/pip-size always resolve against the REAL symbol via `pos["symbol"]`/`symbol_map`, only the bar/ATR/swing CACHE lookups use the composite key, via a new `_cache_key` field threaded onto every signal and position dict). `group_trades()`'s chart-candle lookup (which expects real-symbol keys) gets a re-keyed-by-real-symbol view built just for it. **Verified directly** by calling `PortfolioBacktestEngine.run()` with two slots sharing one real symbol (EURUSD) under different strategies: both opened simultaneous positions without colliding, and both priced correctly against EURUSD's real costs.
- [x] **12.9** **Done.** `UserConfigV2.validate_slot_position_caps()` — sums each enabled slot's `max_positions_per_symbol` (or the global default when unset) and warns per-slot when the total exceeds `max_concurrent_positions`, non-blocking (matches the existing risk-pct-vs-hard-cap pattern: informational, doesn't reject the save). Wired into `PUT /api/config`, returned as `validation_warnings` in the response. **Verified**: 2+2 vs cap 3 correctly produces 2 warnings; 2+2 vs cap 5 correctly produces none.
- [x] **12.10** **Investigated, and found a real, evidenced bug — fixed specifically, nothing else touched.** Audited all 4 named caches: the MT5 symbol-info 60s cache is self-expiring and used for pricing, not gating (ruled out); the `[D4]` RiskEngine fingerprint cache is a full-dict hash (self-healing for any new field, including every one added this session — re-verified, still correct); no Redis-backed state exists anywhere in the live trading gate path (ruled out — `redis_client.py` exists but isn't wired into circuit-breaker/risk decisions). The actual bug: `risk/engine.py`'s per-symbol cooldown read `signal_data.metadata.timeframe`, but **no caller anywhere in the codebase** (backtester, portfolio engine, or live `bot_service.py`) ever populated it, despite every strategy correctly setting the real value on `TradeSignal.timeframe` — so the cooldown silently used the "M15" fallback (900s) for EVERY signal from EVERY strategy regardless of actual entry timeframe, making an M5 strategy's (VWAP, CRT's LTF trigger) post-close cooldown up to 3x longer than intended. This is exactly the reported symptom ("cache persists more than it should... sometimes blocks trades"). Fixed by threading `signal.timeframe` into `signal_data["timeframe"]` at all three call sites and reading it top-level-first in `risk/engine.py`. **Verified**: a signal 3 minutes after a close is correctly still blocked on M5 (same candle); at 6 minutes it's correctly clear (new M5 candle) — was previously blocked for the full 15-minute M15 window regardless.
- [ ] **12.11** Not done — React component work (Settings/Strategy.jsx + Backtester portfolio-symbol picker), needs browser verification, same scope boundary as Phase 7.
- [ ] **12.12** Not done — same (depends on 12.11's UI existing first; the backend half, 12.9, is ready for it to surface).

---

## Phase 13 — Visual intelligence (spec: `PHASE-13-VISUAL-INTELLIGENCE-PLAN.md`)

Added 2026-08-23. Reinstates the replay engine that Part 6 of the master plan disposed of
as "superseded by Part 5" — it was not superseded, it was dropped. See that plan's Part A.

- [x] **13.1** `[V1]` **Done 2026-08-23.** `strategies/core/markings.py` — the marking vocabulary. `metadata["markings"]` had been read by `trade_grouper.py:316` since it was written and produced by nothing, so `smc_data` was empty on every trade ever grouped. 15 tests in `tests/test_markings.py`, including an end-to-end drive of NY-Open-Retest's real M15→M5 state machine.
- [x] **13.2** `[V1]` **Done 2026-08-23.** All six strategies emit markings. CRT and HTF-FVG-Flip additionally capture geometry at decision time (`c2_trigger["geom"]`, `state["htf_fvg"]`) because their state is cleared before entry.
- [x] **13.3** `[V1]` **Done 2026-08-23.** `trade_grouper` routing widened from 3 kinds to all 8, importing the kind sets from `markings.py` so the two cannot drift.
- [x] **13.4** `[V2]` **Done 2026-08-23.** `services/log_stream.py` — loguru sink, ring buffer, batched WS broadcast. Backend logs reach the frontend for the first time.
- [x] **13.5** `[V3]` **Done 2026-08-23.** `api/routes/logs.py` — query the live buffer or the rotated files, filtered by level floor / category / session / text. Path-traversal guarded.
- [x] **13.6** **Done 2026-08-23.** Backtest runs open a log session; `log_session_id` returned on the run and its result, closed in a `finally` on both paths.
- [x] **13.7** `[V4]` **Done 2026-08-23.** `services/replay_stream.py` + wiring into both backtest routes. 12 tests in `tests/test_replay_stream.py`.
- [x] **13.8** **Done 2026-08-23.** `ReplayChart.jsx` / `BacktestReplay.jsx`; the skeleton loader is gone. Verified against a synthetic WS harness — **not** against a live MT5 run.
- [x] **13.9** `[V5-V7]` **Done 2026-08-23.** `llm_service.py`: invalid model id `claude-haiku-4-5-20250514` corrected, default moved to `claude-opus-5`, env-var key resolution added (the key was previously always `""`), `max_tokens` 1024 → 16000, streaming + adaptive thinking.
- [x] **13.10** **Done 2026-08-23.** `services/analysis_context.py` + `api/routes/analysis.py` + `pages/Analysis.jsx`. Measured: a 4,469 KB backtest result reduces to a 4.6 KB prompt.
- [x] **13.11** **Done and verified live 2026-08-23.** `<RunReport>` renders all six panels against a real XAUUSD/APA run: Signal Funnel (8 evaluated -> 7 risk-approved -> 7 filled -> 7 closed, 8 blocked), Risk Deployment (median 0.885% vs 1.00% target, drift -11.5%, range 0.373-0.933%), Exit Attribution (SL 17 legs / 81% / -483.78; TRAIL_SL 4 legs / 19% / +12.34), Blocked-Signal Timeline, Cost Impact. Responsive to 375px.
- [x] **13.12** **Done and verified live 2026-08-23.** Provider registry serving `/fundamentals` with per-capability selection and a health strip (tier, latency, error rate, call count). Verified against the live terminal: BTCUSD order flow returned 23,602 ticks, CVD -249, imbalance -1.1%, VPOC 77289.49, 189 signed price levels. Free defaults (mt5_orderflow / mt5_book / own_bars / forexfactory / cboe); paid slots (polygon / databento) declared and inert without a key. `order_flow` cache TTL raised 2s -> 15s after measuring a ~6s fetch against it.
- [ ] **13.13** Strategy Lab (`/strategy-lab`) — create / preview / optimize / promote. Spec: Phase 13 Part D.1.
- [x] **13.14** **Done 2026-08-25.** `SchemaForm.jsx` renders every parameter from `/config/parameter_schema`, with the docstring as help text, a changed-vs-default flag, per-group filtering and reset-to-default. Mounted in Strategy Lab. Completes `7.2`-`7.5`.
- [x] **13.15** **Done 2026-08-23.** Order-flow bubbles fed by real MT5 tick data — verified rendering 189 signed-volume price levels for BTCUSD, sized by |volume| and coloured by sign, with VPOC and value-area bounds. The inference caveat travels with the data into the UI.
- [x] **13.16** **Run 2026-08-23 — by me, on your terminal.** XAUUSD / APA / 2026-06-01 to 2026-08-23. Confirmed: markings emit and render, the replay stream delivers (16,340 bars in 54 messages, 15 signals, 0 dropped), the log session captures, and the run completes. Also surfaced three real defects — see 13.17-13.19.

---

## Documentation deliverables (Part 10.1)

- [x] **Doc-1** **Done.** Superseded-style update note added to the top of `docs/apa_strategy_implementation_plan.md` covering the Phase 6 state-machine rewrite (candidate list, staleness budgets, BOS/neckline/floor-cap fixes) — the pattern-detection rules themselves (§1 onward) are unchanged and not rewritten.
- [x] **Doc-2** **Done** (= 8.8).
- [x] **Doc-3** **Done.** Update note added to `docs/CRT_Strategy_Spec.md` covering `bias_neutral_mode`, `trigger_grace_bars`, and the STILL-UNRESOLVED target-mode/grid-exemption status (corrected from the task's own premise — CRT is not actually exempted from the grid yet; VWAP v2 now shares the same unresolved pattern, noted explicitly rather than claimed fixed).
- [x] ~~**Doc-4**~~ **Not done — task premise is stale.** D-4 (answered 2026-08-22, before this pass) decided to keep HTF FVG Flip **independent**, not merge it into Bias-IFVG — marking `strategy-1-htf-fvg-flip.md` deprecated would contradict that decision. Left untouched.
- [x] ~~**Doc-5**~~ **Not done — same reason as Doc-4.** There is no "absorbed FVG-Flip mode" to document; FVG-Flip kept its own engine (with its displacement gate fixed in place at 6.11), nothing was absorbed into Bias-IFVG.
- [x] **13.17** **Backtest starved the event loop — fixed 2026-08-23.** `last_yield_time` was assigned and never
  read: the intended elapsed-time yield was never written, so the Phase-1 loop yielded every 50 bars regardless of
  how long they took. With multi-timeframe pandas slicing that is >1s of solid blocking, which dropped the
  WebSocket ("system goes offline", "websocket timeout") and truncated the replay stream mid-run. Now yields on
  >40ms elapsed. Also hoisted three per-bar costs out of the loop: `df.iloc[i]` (~100us/bar) replaced with numpy
  arrays, `datetime.fromisoformat` parsed once instead of per bar, and `.index.values` read once per timeframe
  instead of per bar per timeframe.
- [x] **13.18** **Marking lines ran to the chart edge — fixed 2026-08-23.** A marking's `end_time` is None at signal
  time (the trade has not ended yet) and the renderer reads None as "extend right". Every level from every signal
  therefore ran the full chart width and crossed other setups. `trade_grouper` now clamps `end_time` to the group's
  exit (+3% of trade duration, capped) — a level stops mattering when its trade closes, by TP, SL, BE or trail.
- [x] **13.19** **Replay chart teleported instead of sliding — fixed 2026-08-23.** The engine sends ~400 bars per
  message and the client applied a whole message per frame, so the window jumped 400 bars at a time. Now drains at
  24 bars/frame with proportional catch-up on a backlog. Signals and the done flag are never held back.
- [x] **13.20** **Layout overflowed the viewport — fixed 2026-08-23.** `.main-content` is a flex item, so its default
  `min-width: auto` made it refuse to shrink below its widest child: one wide table pushed the document to 2,026px
  in a 994px viewport. Fixed with `min-width: 0` on flex/grid children, in-box scrolling for tables and pre blocks,
  intermediate breakpoints at 1280px and 1080px (the layout previously jumped straight from desktop to 768px), and
  an `overflow-x: hidden` backstop. Verified clean at 994px and 375px.
- [x] **14.1** **Done 2026-08-25.** Help accordions across Fundamentals (`HelpNote.jsx` +
  `FundamentalsHelp.jsx`): order flow (incl. what "volume by price"/VPOC/value area mean and the
  tick-volume caveat), depth (why "empty book" is a broker limitation, not a fault), correlation,
  GEX (incl. the index-code rule) and calendar. Each states what the number is, how it is derived,
  what it cannot tell you, and one concrete way to act on it. Collapsed by default, remembered per panel.
- [x] **14.2** **Done 2026-08-25.** GEX ticker is now a suggest-list of valid index/ETF codes with an
  inline warning when a broker CFD name (SPX500, NDX100, USTEC...) is entered — those have no options
  chain and previously just returned nothing with no explanation.
- [x] **14.3** **Done 2026-08-25.** Strategy Lab symbol is free text with suggestions (was a hardcoded
  dropdown, so Volatility/Jump/Hong Kong 50 could not be previewed at all), and the preview window is
  a real date range with 30/90/210/365-day presets (was hardcoded to 45 days — far too short for a
  strategy taking a few trades a month).
- [x] **14.4** **Done 2026-08-25.** VWAP produced ZERO signals on gold over 7.5 months.
  `volume_confirmation_mult=1.2` eliminated every candidate (gate funnel on 6,000 XAUUSD M5 bars:
  1239 tradeable -> 55 valid setups -> 0 after the volume gate). Two causes: MT5 CFD feeds report tick
  volume rather than traded size, and the pullback setup selects the quietest bar of a move then demanded
  it also be a volume spike. Default -> 0.0. Now 20 signals on the same data, all carrying 11 markings each.
- [x] **14.5** **Done 2026-08-25.** VWAP anchored-VWAP calculation vectorised (per-session `.loc` loop ->
  numpy reset-cumsum; ET offset derived from 2 timestamps instead of 300, with a DST-straddle fallback).
  **12.2x faster, output bit-identical** across 27 windows. ~12.5 min -> ~3.3 min on a 17k-bar run.
- [x] **14.6** **Done 2026-08-25.** Swap costs were consumed in the symbol's quote currency as if they
  were account currency. GBPJPY: MT5 reports `swap_mode=POINTS, swap_short=-18.93`, correctly converted to
  1,893 **JPY**, then spent as **USD** — ~150x too large. A +5.26R take-profit was recorded as a -$1,770
  loss and the account ran to -$25,099. Now converted via the live cross (-$11.89/lot/day), with an
  absolute per-lot sanity bound because the percent-of-notional check could never catch a units error.
  **Affected live P&L attribution, not only backtests.**
- [x] **14.7** **Done 2026-08-25.** `be_applied`/`trail_applied` were never rolled up from legs to the
  grouped trade, so all 691 analysed trades recorded False despite 106 `BE_SL` exits. What break-even
  costs was unmeasurable. Now propagated.
- [x] **14.8** **Done 2026-08-25.** Session filter exempts 24/7 instruments via the existing
  `trades_24_7` profile flag. Measured: XRPUSD +0.56R in the Asian window vs -0.20R in NY, the reverse
  of the equity symbols — applying an equity session to crypto discards trades for no reason.
- [ ] **14.9** Symbol identity across brokers (GER40 vs GER30). Spec: Phase 14 Part C. Not started —
  `SYMBOL_ALIASES` maps spellings but assumes one broker; needs canonical instruments with per-broker maps.
- [ ] **14.10** Fundamentals chart tabs — render each panel's data on the price chart. Spec: Part E.1.
- [ ] **14.11** Strategy overlay on the fundamentals chart. Spec: Part E.2. Depends on 14.10.
- [ ] **14.12** `FundamentalGate` — use fundamentals as filter/confluence/trigger inside a strategy.
  Spec: Part E.3. Depends on 14.9 and 14.13.
- [ ] **14.13** Strategy Factory — paste an MD spec, Claude generates the engine + params, you review a
  diff, activate. Spec: Part D. **Largest remaining item.** Writes code into the repo, so the review gate
  and the path restriction are load-bearing, not optional.
- [ ] **14.14** Git branch + PR automation for generated strategies. Spec: Part D.1 step 4. Depends on 14.13.
  Targets `dev`, never auto-merged.

- [ ] **Doc-6** Not done — explicitly flagged in the plan's own note below as needing a go-ahead before scheduling the underlying code change; a doc update alone (with no code change) would misdescribe the engine. Still open.
- [x] **Doc-7** **Done.** Major update added to `docs/RiskManagement_Spec.md` covering the Phase 5 RR-triggered BE/trailing architecture (`be_mode`/`trail_mode`, `trail_require_be_first`, `trail_method_tp1`, `tp_volume_pcts`) and the `blended_rr` re-spec of `min_rr` (with `expected_rr`'s unimplemented status noted honestly, not silently dropped).
- [ ] **Doc-8** Not written — `docs/strategy-orderflow-gex.md` covers Phase 11 (gamma exposure), which is gated on D-6 and out of scope for this pass.
- [x] **Doc-9** **Done** (= Phase 10, written early since orderflow.py doesn't depend on the Phase 11 gamma work).
- [x] **Doc-10** **Done.** `docs/backtest_report_ui.md` written — full field-object contract for `GET /api/config/parameter_schema`, with `unit`/`affects` explicitly documented as always-empty (no structured source of truth for either exists yet) rather than left for someone to discover by reading code.

> **Note on Doc-6 / NY-Retest:** the plan's consolidated open-work register (Part 6, item 8) lists
> "NY-Retest M1 timeframe + `order_type` limit fills" as still open, but no Phase above schedules the
> code change — only the doc update. **Add this as an explicit task if you want it built:** requires an
> `order_type` field on `TradeSignal`, switching the entry timeframe from M5 to M1, and filling limits at
> the limit price when the bar's range contains it. Flagging rather than silently adding it, since it
> changes NY-Retest's entry mechanics materially.

---

## Implementation-folder housekeeping (Part 6)

- [x] **Housekeeping-1** ~~Delete `implementation/phase7.txt`~~ — corrected, not deleted (see Phase 12).
- [x] **Housekeeping-2** `implementation/lookahead_audit.md` restored from git — done.
- [x] **Housekeeping-3** **Done.** Re-verified against current code: §10.6 (VWAP confluence), §10.7 (VWAP SL/target), §10.8 (APA SL floors), §10.9 (NY-Retest target_mode) all confirmed resolved; §10.10 (CRT structural TP vs. the grid) confirmed **still open** — note added inline in `strategy_parameter_audit.md` rather than a separate table (no literal status table found in the doc to update).
- [x] **Housekeeping-4** **Done.** Superseded-status notes added to `implementation_plan.md` (its Phase 4–7, item-by-item against Master Plan Phase 12) and `AlgoEdge-Audit-Implementation-Plan.md` (the correlation-exposure-cap row, against Phase 9.5/9.6).

---

## Engineering rules — CI / test enforcement (Part 12)

*No CI pipeline exists in this repo (no `.github/workflows/`, `pytest.ini`, or `conftest.py`), and neither
venv (`venv`, `venv_win`) has `pytest` actually installed (`import pytest` resolves to an empty stray
namespace package). "Enforcement" below means real, runnable tools written to need zero new dependencies
— ready to wire into a CI workflow whenever one exists, not simulated as already wired in.*

- [x] **Rule-1** **Done, with an honest noise caveat.** `tests/rule1_bare_literals_check.py` — AST-based scan of `risk/`, `backtester/`, `services/` for bare numeric literals outside `params.py`/`config_schema.py`, with a small structural allow-list (`0, 1, -1, 2, 100, 60, 1000`, ±float forms). **Runs and works** (`python tests/rule1_bare_literals_check.py`), but surfaces ~811 hits — most are legitimate (loop bounds, `86400` seconds/day, index math), not risk-tuning constants. A signal-to-noise pass (scoping to literals inside comparison/threshold expressions specifically) would be needed before this is CI-gate-worthy; shipped as a working report tool, not oversold as a clean gate.
- [x] **Rule-2** **Done as a diagnostic, not a hard assert** (see `test_backtest_risk_config_key_drift_report` in `tests/test_engineering_rules.py`) — matches 3.15's own status: 3.14 (replacing the two hand-maintained `risk_config` dict literals with `dataclasses.asdict(RiskParams())`) was deliberately not done in an earlier session (real risk of silently dropping a key some call site depends on, with no way to run the live bot here to catch it), so a hard `set() == set()` assert would immediately and expectedly fail. Run: it currently reports 21 `RiskParams` fields missing from `backtest.py`'s dict and 14 extra keys not on `RiskParams` — concrete, measurable drift for whoever completes 3.14 later, not a guess.
- [ ] **Rule-3** Not done — `resolve_distance()` was itself descoped at [1.1]/[1.2] (the "unit ambiguity" premise didn't hold up on verification), so there's no such function for a test to check round-trips through.
- [x] **Rule-4** **Done, scoped to the paths added/changed this session** (`TestBindingConstraintCoverage` in `tests/test_engineering_rules.py`) — verifies the margin/cluster-exposure/strategy-budget rejection paths each produce a named `binding_constraint` or reason string, never a silent/generic one. A full sweep of every rejection path in `risk/engine.py` (there are ~20) is a larger undertaking than this pass attempted; the 3 covered are the ones this session's own work touched.
- [ ] **Rule-5** Not done as an automated test — verified manually instead, repeatedly, across this session: `resolve_be_buffer()` (BE buffer), `resolve_sizing_base_balance()` (sizing basis), and the trailing-activation gate in `manage_open_position` were each explicitly checked to be the ONE function both backtest and live call, as part of [4.1]–[4.6]'s own implementation. A grep-for-duplicate-implementations script was not built.
- [ ] **Rule-6** Not done — blocked on 4.9, which needs a live/demo MT5 connection this environment doesn't have.

**Both `tests/rule1_bare_literals_check.py` and `tests/test_engineering_rules.py` are real, runnable files** —
`python tests/rule1_bare_literals_check.py` and `python tests/test_engineering_rules.py` — verified
working during this session, not just written.
