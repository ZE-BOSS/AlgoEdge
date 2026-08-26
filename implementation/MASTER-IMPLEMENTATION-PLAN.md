# AlgoEdge — Master Diagnosis & Implementation Plan

**Date:** 2026-08-22
**Scope:** trade-frequency collapse · crypto risk under-deployment · hard-filter register · cost model ·
backtest-result UI · RR-triggered BE/trailing · implementation-folder reconciliation · VWAP upgrade ·
order-flow / gamma exposure · portfolio composition
**Evidence base:** 19 debug runs in `debug/` (Jan 1 – Aug 21 2026, $25,000, 1,214 signal groups,
3,592 legs, 6 strategies, 9 instruments) + two raw log captures + full codebase read + a programmatic
audit of all 81 instrument profiles.

> ### ▶ Start here — added 2026-08-22 (revision 2)
>
> **[PART 11](#part-11--full-codebase-defect-register) is the full-codebase defect register: 65 distinct
> defects across backend and frontend, each with `Where · Cost · Fix · Why`.** It opens with
> **§11.0, a one-screen index of every defect** — read that first.
> **PART 12** states the engineering rules every fix must satisfy (no hardcoded risk values, explicit
> units, one config contract, clamps must be visible). **PART 13** is the revised execution sequence.
>
> Parts 0–10 are the earlier strategic diagnosis and are unchanged.

---

## PART 0 — Executive summary

Five defects explain everything reported. None of them is "the market did not give a setup."

| # | Defect | Symptom | Confidence |
|---|---|---|---|
| **A** | `MAX_MARGIN_UTILISATION_PCT = 30%` binds on every crypto trade | XRP/BTC risking 0.05–0.4% instead of 1% | **Proven** |
| **B** | Signals fire but are silently killed by the risk layer; the diagnostic that would show it is never serialized | "6 trades in 7.5 months" | **Proven** |
| **C** | `max_positions_per_symbol = 1` + multi-day holds = symbol locked for days/weeks | 1 setup per 2–3 weeks | **Proven** |
| **D** | `risk/engine.py` reads `position["original_sl"]`, a key that does not exist → trailing arms on a collapsed denominator | winners scratched at BE/trail | **Proven** |
| **E** | Weekly-drawdown budget consumes *open* risk and latches `is_paused` | whole weeks with zero trades | **Proven in code** |

Costs (spread/slippage/commission/swap) **are** applied — but only on the entry side, and sizing ignores
them, so realised risk drifts. Details in §1.6.

The rejection funnel that would have shown all of this in five seconds **is computed by the engine and
then thrown away** before the API response is built. That is the single highest-leverage fix here.

---

# PART 1 — Root-cause findings, with evidence

## 1.1 A — The 30% margin ceiling is the real crypto position sizer  🔴

`backend/risk/position_sizer.py::_margin_capped_lots` clamps every position so required margin stays
under 30% of equity. It asks `mt5.order_calc_margin()` first. Crypto CFDs are margined at or near
**1:1** at this broker, so required margin ≈ full notional, and the cap becomes a hard notional ceiling
of `0.30 × $25,000 = $7,500`.

Measured notional (`Σ lots × entry × contract_size`) per signal group:

| Run | median notional | p10 | p90 |
|---|---|---|---|
| VWAP XRPUSD | **$7,496** | $7,455 | $7,531 |
| VWAP BTCUSD | **$7,134** | $6,886 | $7,432 |
| CRT BTCUSD | **$7,100** | $6,805 | $7,464 |
| APA BTCUSD | **$7,269** | $7,226 | $7,313 |
| VWAP XAUUSD | $45,639 | $24,403 | $64,812 |
| VWAP EURUSD | $177,291 | $110,651 | $277,700 |

Crypto is pinned to $7,500 with ±1% dispersion. That is not risk-based sizing; that is a constant.
FX/metals sit far above $7,500 because their margin rate is 1:30–1:500, so the cap never binds.

Resulting intended risk per group (recomputed as `volume × |entry − initial_stop_loss| × value-per-unit`):

| Run | min | p25 | **median** | max | target |
|---|---|---|---|---|---|
| VWAP XRPUSD | 0.068% | 0.321% | **0.471%** | 1.314% | 1.00% |
| VWAP BTCUSD | 0.020% | 0.169% | **0.249%** | 1.053% | 1.00% |
| CRT BTCUSD | 0.034% | 0.058% | **0.097%** | 0.414% | 1.00% |
| APA BTCUSD | — | — | **0.145%** | 0.224% | 1.00% |
| VWAP XAUUSD | 0.160% | 0.834% | **1.029%** | 2.199% | 1.00% |
| VWAP EURUSD | 0.427% | 0.943% | **1.144%** | 2.321% | 1.00% |

The guard itself is **correct** — MT5 rejects un-marginable orders with retcode 10019. The defects are:

1. It is **hard-coded**, not a user parameter.
2. It **silently truncates** instead of declaring "this account cannot express 1% risk on this symbol."
3. Nothing in the report or UI says it fired. It logs a WARNING; `run_logs` is truncated to the last
   100 entries, so the warning never reaches the saved result.
4. It runs **after** the TP split, so it also distorts the split ratios.

## 1.2 B — The rejection funnel is computed and then discarded  🔴

`backtester/engine.py` maintains `self.rejection_funnel` with `total_evaluated`, `approved`,
`strategy_rejections`, `risk_rejections`, `fill_rejections`, `errors`, and returns it at
`engine.py:1242`. Then:

- **Single-symbol route** (`api/routes/backtest.py:660-703`): the response dict **omits
  `rejection_funnel` entirely.**
- **Portfolio route** (`backtest.py:1119`): includes it at the **top level** of the response.
- **Frontend** (`Backtester.jsx:857`): reads `report.rejection_funnel` — a path neither route populates.

Verified: all 19 debug JSONs carry `report` keys `[bias_stats, confluence_stats]` and **no
`rejection_funnel` anywhere**. The panel has never rendered.

**Consequence:** for APA XAUUSD the run tail shows **8 `APA SIGNAL FIRED` events** while the last trade
in the entire run is dated **2026-04-08**. Every August signal was approved by the strategy and killed
downstream, with no way to see which gate did it.

Strategy-side funnel from the last-100 log window (APA XAUUSD): 24 H&S patterns detected → 12 killed by
the `is_major` neckline test → 9 BOS confirmed → 8 retests → **8 signals fired → 0 trades.**

## 1.3 C — `max_positions_per_symbol = 1` against multi-day holds  🔴

`risk/circuit_breaker.py::check_symbol` hard-rejects any signal while a group is open on that symbol.
`max_positions_per_symbol` defaults to **1**, and APA/CRT/FVG legs hold for days (sampled APA XAGUSD leg:
entry `2026-01-02 15:10` → exit `2026-01-04 22:00`, **3,290 minutes**; TP3 legs run until trailed out).
A single trade therefore blocks the symbol for the whole window.

For a one-symbol backtest this is the dominant frequency ceiling. `max_concurrent_positions = 3` adds a
second ceiling for portfolio runs.

## 1.4 D — `original_sl` key does not exist  🔴

`risk/engine.py:346`:

```python
original_sl = position.get("original_sl", current_sl)
```

The backtester writes `initial_stop_loss` (`engine.py:1369`); the portfolio engine writes
`initial_stop_loss` (`portfolio_engine.py:778`). **Nothing anywhere writes `original_sl`.** The fallback
`current_sl` is the *mutated* stop.

- Before BE fires, `current_sl == initial SL`, so break-even maths is accidentally correct.
- **After BE fires**, `current_sl == entry ± 0.10×ATR`, so `risk_distance = abs(entry − original_sl)`
  collapses to the BE buffer. `unrealized_r` then reads ~15–20R instead of ~1.5R and
  `trail_activation_rr` **always passes**.

This is exactly the mechanism `strategy_optimization_research.md §1.1(2)` identified. It is **still
present**. It explains why TRAIL_SL is the second-largest exit bucket in the corpus (2,429 legs, median
MFE 2.02R, most of it given back).

Same file, line 376: `atr=atr_value if atr_value > 0 else abs(entry - original_sl) * 0.1` — the same
collapsed denominator feeds the BE-buffer fallback.

## 1.5 E — Weekly drawdown budget counts open risk and latches  🟠

`risk/engine.py:180-200`:

```python
already_lost_weekly = -self.circuit.weekly_pnl + open_risk
remaining_weekly_risk = max_weekly_loss_dollars - already_lost_weekly
if remaining_weekly_risk <= 0:  return False, "Weekly drawdown limit ... fully exhausted."
```

With `max_weekly_drawdown_pct = 6` on $25,000 the weekly budget is $1,500. Three open groups at 1% =
$750 of `open_risk` counted as *already lost*, plus two realised losses = budget gone by Wednesday.
`circuit_breaker.check_all` then latches `is_paused = True` and returns `False` on its first line for
every remaining bar of the week.

Defensible for a prop challenge — but right now it is **invisible** and **indistinguishable from
"no setup".**

## 1.6 Cost model — working, but asymmetric  🟠

Verified against `backtester/engine.py::_calc_pnl` and the run data:

| Cost | Applied? | Where | Gap |
|---|---|---|---|
| Entry slippage | ✅ | shifts effective entry against direction | — |
| Spread | ✅ | `spread_pips × pip × vpum × vol` deduction | charged once, not per side |
| Commission | ✅ | `per_lot × volume` | — |
| Swap / rollover | ✅ | per rollover crossed, Wed triple | — |
| **Exit slippage** | ❌ | measured `exit_price − stop_loss == 0.0` on **216/216** non-gapped SL exits | stops fill exactly; live they do not |
| Gap fills | ✅ | `_gap_adjusted_fill_price` with slippage | — |

**Three real defects:**

1. **Sizing uses the un-slipped entry; PnL uses the slipped entry.** Realised risk is systematically
   `(sl_distance + slippage + spread) × lots`, i.e. above target.

2. **SL/TP are anchored to the *signal* price, not the *fill* price.** Measured drift between signal
   entry and actual fill, in R:

   | Run | p10 | median | p90 | max | groups > 0.25R adverse |
   |---|---|---|---|---|---|
   | VWAP XAUUSD | −0.656R | −0.025R | +0.268R | +0.520R | 28 / 222 |
   | APA XAGUSD | −0.204R | +0.131R | +0.396R | +0.408R | 6 / 16 |
   | NY Retest XAUUSD | −0.536R | −0.012R | +0.323R | +0.476R | 11 / 60 |

   A ±0.5R fill drift on a stop that is **not** re-anchored means the risk actually taken ranges from
   0.5× to 1.5× the risk sized. This is the main driver of the 0.4%–2.3% dispersion visible in every
   FX/metal run above, and it corrupts every R-based statistic downstream.

3. **Unit sanity:** `XAGUSD spread_pips = 94` and `BTCUSD stops_level_pips = 46.53` are almost certainly
   **points** consumed as **pips**. On XAGUSD this drove APA's cost floor to widen a structural stop from
   465 → **550 pips** (`sl_floored` on 5/16 signals). Needs a points-vs-pips audit in `broker_costs.py`.

## 1.7 VWAP is force-flat before its targets are reachable  🟠

VWAP XAUUSD leg exit mix: **`SESSION_END` 306**, `SL` 219, `TRAIL_SL` 63, `TP1` 40, `BE_SL` 17, `TP2` 3.

**47% of all legs are force-closed at 15:55 ET**, and per the prior research those legs carry a median
MFE of 0.82R. A 1.5R/3R/5R grid inside a same-day-flat window is not reachable. Either the grid or the
window has to change — see §8.

## 1.8 APA state-machine defects  🟡

`strategy_apa/engine.py`:

- **Single-slot state machine.** One pattern per symbol. While `AWAIT_BOS`/`AWAIT_RETEST` is occupied, no
  new pattern is scanned. There is **no timeout on `AWAIT_RETEST`** — the only invalidation
  (`body closes beyond Head`) is checked *inside* `AWAIT_CONFIRMATION`, reached only after a retest
  touch. A setup whose retest never comes holds the slot indefinitely.
- **The daily reset makes this worse.** Lines 190-212: the reset is skipped when
  `bos_confirmed and status in (AWAIT_RETEST, AWAIT_CONFIRMATION)` — i.e. precisely the deadlock state is
  protected from clearing, while `AWAIT_BOS` (a legitimately in-flight setup) **is** wiped every UTC
  midnight.
- **`is_major` neckline gate** (`abs(mp − neckline) <= atr × 0.5` against `major_fractal_m = 8` swings)
  destroys the pattern outright. Measured: **12 of 24** detected patterns in the log window.
- **Body-close test is wrong.** `min(open, close) < neckline` is a wick-inclusive test on one side —
  a still-open finding from `doc_conformance_audit.md §1.2-A`.

## 1.9 CRT hard-filter loss (from `debug/crt/ndx_logs.txt`)

| Log line | Count | Nature |
|---|---|---|
| `C2 did not match C1/bias criteria` | 451 | legitimate — pattern test |
| `HTF bias is NEUTRAL — skipping C2 evaluation` | **254** | **hard block** — no directional bias = no evaluation at all |
| `Ambiguous C2 (swept both sides)` | 114 | legitimate — genuinely ambiguous |
| `Valid C2 but out of session. Ignoring.` | **80** | **hard block** — 80 valid setups discarded on time-of-day alone |
| `Trigger timeout — LTF did not fire before next HTF close` | — | legitimate — but no grace bar |

CRT produced **19 trades in 7.5 months on NDX100** out of ~900 evaluations.

---

# PART 2 — Complete hard-filter register

Every gate in the pipeline, classified. **BLOCK** = returns/rejects. **CLAMP** = reduces size and
continues. **Disposition** = what it should become.

## 2.1 Strategy layer

| # | Gate | File | Now | Should be |
|---|---|---|---|---|
| S1 | Session window | all engines `_is_within_session` | BLOCK | **BLOCK (keep)** — measured +$986 vs −$24 on gold with/without. But log it. |
| S2 | Daily state reset | apa / fvg_flip / bias_ifvg | BLOCK (wipes setup) | **REMOVE** for `AWAIT_BOS`; replace with an explicit N-bar staleness timeout on every state |
| S3 | APA `is_major` neckline (0.5×ATR) | apa:266-277 | BLOCK | **CLAMP → confluence** — widen to `neckline_major_atr_tolerance` (default 1.0), score the precision instead of discarding |
| S4 | APA one-pattern slot | apa state dict | BLOCK | **REPLACE** with a ring buffer of up to N concurrent candidate patterns |
| S5 | APA head invalidation | apa:378-390 | BLOCK | **KEEP** — correct |
| S6 | APA SL-wrong-side | apa:400-420 | BLOCK | **KEEP** — correct, but should be impossible after S8 |
| S7 | APA cost floor `_sl_floor_distance` | apa:70-110 | CLAMP (widens SL) | **KEEP but cap** — add `max_sl_floor_atr_mult` so a 465→550-pip widening cannot happen silently |
| S8 | APA body-close BOS | apa:250-260 | wrong test | **FIX** to a true `close` test |
| S9 | CRT `HTF bias NEUTRAL` | crt:171 | BLOCK (254×) | **CLAMP** — allow with `size_modifier = 0.5` and a `bias_neutral` confluence penalty, OR make it a user toggle |
| S10 | CRT `out of session` | crt:183/190 | BLOCK (80×) | **KEEP as BLOCK**, but expose the window per-strategy and count it in the funnel |
| S11 | CRT ambiguous C2 | crt:161 | BLOCK | **KEEP** |
| S12 | CRT trigger timeout | crt:147 | BLOCK | **CLAMP** — add `trigger_grace_bars` (default 2) |
| S13 | FVG-Flip displacement | *absent* | — | **ADD as BLOCK**: middle candle range ≥ 1.5×ATR(14) and body ≥ 60% of range |
| S14 | FVG-Flip swing-broken invalidation | four:327-332 | BLOCK | **KEEP** |
| S15 | Bias-IFVG `a_plus_confluence` day-stop | five:572 | BLOCK | **CLAMP** — reduce size rather than skip |
| S16 | Bias-IFVG swing validity | five:534 | BLOCK | **KEEP** |
| S17 | NY-Retest session end | six:81 | BLOCK | **KEEP** |
| S18 | DJA ADX < min | two:363 | BLOCK | **CLAMP** — scale size by ADX percentile |
| S19 | DJA gap percentile ≥ threshold | two:288/351 | BLOCK | **KEEP** — regime kill-switch |
| S20 | DJA RRR < min_rrr | two:319/430 | BLOCK | **KEEP** |
| S21 | DJA post-loss cooldown | two:205 | BLOCK | **KEEP**, expose as param |
| S22 | VWAP `max_trades_per_day` (4) | vwap:288 | BLOCK | **KEEP** |
| S23 | VWAP `max_losses_per_day` (2) | vwap:290 | BLOCK | **KEEP** |
| S24 | VWAP exclusion windows | vwap `_is_in_exclusion` | BLOCK | **KEEP** |

## 2.2 Risk layer — `risk/engine.py::evaluate_signal`, in execution order

| # | Gate | Line | Now | Should be |
|---|---|---|---|---|
| R1 | `circuit.check_symbol` — TF cooldown | cb:170-181 | BLOCK | KEEP |
| R2 | `circuit.check_symbol` — `max_positions_per_symbol` | cb:187 | **BLOCK — dominant** | **RAISE default to 3** + add `allow_pyramiding` and a `min_bars_between_entries` instead of a hard 1 |
| R3 | `check_all` — `max_daily_trades` (5) | cb:110 | BLOCK + latch | KEEP, but move to per-strategy and surface the latch |
| R4 | `check_all` — `max_concurrent_positions` (3) | cb:117 | BLOCK | **RAISE to 5**, make per-strategy |
| R5 | `check_all` — daily DD % | cb:126 | BLOCK + latch | KEEP |
| R6 | `check_all` — weekly DD % | cb:141 | BLOCK + latch | KEEP but see R9 |
| R7 | `check_all` — target profit | cb:150-158 | BLOCK | KEEP (off by default) |
| R8 | Prop-firm drawdown block | engine:93 | BLOCK | KEEP |
| R9 | Predictive daily/weekly DD scaler | engine:170-200 | **CLAMP then BLOCK** | **Stop counting `open_risk` as realised.** Count it at 50% (or make the weight a param). Keep the clamp; only block at true exhaustion |
| R10 | `get_confluence_scaled_risk` < 55 | sizer:700 | **BLOCK** | **CLAMP to a floor** (e.g. 25% of base) unless `reject_below_confluence` is explicitly set |
| R11 | Confluence tiers 80/65/55 → 100/75/50% | sizer:690 | CLAMP | KEEP — but make the tier table a user parameter, and note VWAP hard-codes 80 so this never bites there |
| R12 | `minimum_stop_distance` guard | sizer:583 | **BLOCK (0 lots)** | **KEEP as BLOCK** — untradeable stops must not size. But it must appear in the funnel |
| R13 | `DEFAULT` source refusal | sizer:566 | BLOCK | KEEP |
| R14 | Hard risk cap `max_risk_hard_cap_pct` | sizer:604 | CLAMP | KEEP |
| R15 | **Margin ceiling 30%** | sizer:_margin_capped_lots | **CLAMP, hard-coded, silent** | **Parameterise** (`max_margin_utilisation_pct`), emit `margin_truncated_pct` per trade, and add a `min_deployable_risk_pct` below which the trade is *rejected with a named reason* rather than taken at 0.05% |
| R16 | Post-cap `< volume_min` | sizer:640 | BLOCK | KEEP |
| R17 | Prop-firm lot cap | engine:218 | BLOCK | KEEP |
| R18 | TP-split `lot_min` inflation → TP-count reduction | multi_tp:210-240 | CLAMP (drops TPs) | KEEP but report which TPs were dropped |
| R19 | `post_split_risk_overshoot` > 110% | engine:243 | BLOCK | KEEP |
| R20 | `insufficient_rr` (`last_tp_rr < min_rr`) | engine:295 | BLOCK | **RE-SPEC** — `min_rr` currently tests the *last* TP, so it is trivially satisfied by tp3=5R and means nothing. Test the **volume-weighted blended RR** instead (this is exactly the complaint about split volume not delivering the RR you asked for) |
| R21 | `_validate_tp` wrong-side | multi_tp:265 | BLOCK (whole group) | KEEP |

## 2.3 Execution layer — `backtester/engine.py`

| # | Gate | Line | Now | Should be |
|---|---|---|---|---|
| X1 | `_validate_position` | 86 | BLOCK | KEEP |
| X2 | `validate_at_fill_price` — stale signal (bar gapped past SL/TP) | 169-240 | BLOCK | KEEP — this is genuine live parity |
| X3 | `validate_at_fill_price` — SL closer than `max(stops_level, 2×spread)` | 241-252 | BLOCK | KEEP, but fix the points/pips unit bug first (§1.6.3) |
| X4 | Group-atomic rejection (any leg invalid → whole group dropped) | 1100-1132 | BLOCK | **CHANGE to per-leg** — drop the offending leg, keep the rest |
| X5 | `hard_close_time` force-flat | 801-824 | forced exit | KEEP, but see §8 |

**Summary of dispositions:** 6 gates change from BLOCK to CLAMP (S3, S9, S12, S15, S18, R10), 3 gates get
raised defaults (R2, R4, R9), 1 gate is re-specified (R20), 1 becomes per-leg (X4), 1 is added (S13),
and 3 are fixed outright (S2, S8, R15).

---

# PART 3 — Fix plan (phased)

## Phase 0 — Observability first (do this before touching any threshold)

Nothing below can be validated without it. Estimated 1 day.

1. **`api/routes/backtest.py:660`** — add `"rejection_funnel": results.get("rejection_funnel", {})` to
   the single-symbol response, at the top level, matching the portfolio route.
2. **`Backtester.jsx:857`** — read `result.rejection_funnel`, not `report.rejection_funnel`. Fall back to
   `report.rejection_funnel` so old saved runs still render.
3. **New: `sizing_diagnostics` per approved trade.** Emit for every sized position:
   `requested_risk_pct`, `confluence_scaled_pct`, `dd_scaled_pct`, `pre_cap_lots`, `post_hardcap_lots`,
   `post_margin_lots`, `final_lots`, `realised_risk_pct`, and a `binding_constraint` enum
   (`none | confluence | daily_dd | weekly_dd | hard_cap | margin | lot_min | lot_max`).
   Attach to the trade dict, aggregate into the report.
4. **New: `blocked_signals` list** — every signal the strategy emitted that did not become a trade, with
   timestamp, symbol, direction, entry/SL, and the gate name. Cap at 500, then summarise. This is the
   artefact that answers "why only 6 trades" directly.
5. **Raise `run_logs` retention** from 100 to a level-filtered 2,000 (keep all WARNING/ERROR, sample INFO).
6. Re-run the six strategies unchanged and publish the funnel. **Change nothing else in Phase 0.**

## Phase 1 — Correctness bugs (no behaviour tuning)

| Fix | File | Change |
|---|---|---|
| 1.1 | `risk/engine.py:346` | `position.get("original_sl") or position.get("initial_stop_loss") or current_sl`. Also write `original_sl` in both engines' position dicts for belt-and-braces. |
| 1.2 | `risk/engine.py:376` | same key fix for the ATR fallback |
| 1.3 | `position_sizer.py` | hoist `MAX_MARGIN_UTILISATION_PCT` and `MIN_STOP_SPREAD_MULTIPLE` into `RiskParams` |
| 1.4 | `position_sizer.py::_margin_capped_lots` | return `(lots, truncation_pct, basis)`; reject with reason `margin_ceiling_below_min_risk` when `realised_risk_pct < min_deployable_risk_pct` (new param, default 0.25%) |
| 1.5 | `backtester/engine.py::_create_position` | **re-anchor SL and TP to the actual fill**: `sl = fill ± sl_distance`, `tp_n = fill ± n·R`. Keep `signal_entry_price` for reference. Live path in `mt5/order_manager.py` must do the same. |
| 1.6 | `position_sizer.calculate_lot_size` | size against `sl_distance + slippage + spread` so realised risk matches target |
| 1.7 | `backtester/engine.py` | apply exit slippage on SL/TRAIL exits (adverse), none on TP limit fills |
| 1.8 | `risk/broker_costs.py` | points-vs-pips audit; assert `spread_pips < 20` for FX, `< 30` for metals, log LOUD on violation |
| 1.9 | `backtester/engine.py:1100-1132` | per-leg rejection instead of group-atomic (X4) |
| 1.10 | `risk/engine.py:180-200` | `open_risk_weight` param (default 0.5) in the DD budget |
| 1.11 | `circuit_breaker.py` | emit a `paused_bars` counter and the pause reason into the report |

## Phase 2 — Filter re-disposition (§2 table)

Apply BLOCK→CLAMP conversions, raise `max_positions_per_symbol` to 3 and `max_concurrent_positions` to 5,
add `min_bars_between_entries` per strategy, add the APA/CRT staleness timeouts, replace APA's single-slot
state with a candidate ring buffer, fix the APA body-close test, and add the FVG displacement gate.

**Every conversion must be gated behind a parameter with the old value as an option**, so Phase 2 can be
A/B'd against the Phase 0 baseline.

## Phase 3 — Exit architecture (§4 + the research doc's §1)

The BE/trail rework and the `min_rr` re-spec. Highest expected value once Phase 1 is honest.

## Phase 4 — UI (§5)

## Phase 5 — New strategies / data (§9)

---

# PART 4 — RR-triggered break-even and trailing, and single-TP mode

## 4.1 What exists today

- `be_trigger_rr` (default 1.5) — **works**, `BreakevenManager.check_breakeven` already triggers on
  `unrealized_r >= be_trigger_rr`.
- `be_on_tp1_hit` (default True) — a second, independent trigger.
- `trail_activation_rr` (default 1.5) — **exists but is broken** by the `original_sl` bug (§1.4).
- `trail_method_tp2..tp5` — exist.
- **`trail_method_tp1` does not exist.** `MultiTPManager.trail_methods[0]` is hard-coded `None`
  (`multi_tp.py:70`), and `position_manager.py:532` does `getattr(risk, 'trail_method_tp1', 'NONE')`.

**Net effect: with `tp_count = 1`, trailing is structurally impossible.** That is precisely the case
described.

Secondary blocker: `risk/engine.py:387` gates trailing behind `if trail_method and be_applied` — trailing
cannot start before break-even, even if the user wants an earlier trail.

## 4.2 Target model

A per-TP **exit ladder**, where each rung is independently triggerable by *either* an RR level *or* a TP
hit, and volume allocation is explicit.

```
tp_levels:
  - level: 1
    rr: 5.0
    volume_pct: 100          # explicit, replaces the tp_splits string
    trail_method: NONE
be:
  mode: RR                   # RR | TP_HIT | EITHER | NONE
  trigger_rr: 1.0            # break even at 1R
  trigger_tp_level: 1        # used when mode is TP_HIT or EITHER
  buffer_atr_mult: 0.10
  buffer_pips: 0.0
trail:
  mode: RR                   # RR | TP_HIT | EITHER | NONE
  trigger_rr: 1.0            # start trailing at 1R
  trigger_tp_level: 2
  require_be_first: false    # NEW — decouples trailing from break-even
  method: ATR_TRAIL
  atr_multiplier: 3.0
  step_pips: 5.0
```

This expresses the stated scenario exactly: one TP at 1:5, 100% volume, break even at 1:1, ATR(3.0)
trailing from 1:1 onward.

## 4.3 Backend changes

| File | Change |
|---|---|
| `core/config_schema.py` | Add `trail_method_tp1`, `atr_trail_multiplier_tp1` (already present), `be_mode`, `be_trigger_tp_level`, `trail_mode`, `trail_trigger_rr`, `trail_trigger_tp_level`, `trail_require_be_first`, `tp_volume_pcts: list[float]`. Keep `tp_splits` as a deprecated alias that populates `tp_volume_pcts`. |
| `risk/multi_tp.py` | `self.trail_methods[0] = config.get("trail_method_tp1", None)`. Replace the `tp_splits` string parse with `tp_volume_pcts`, normalised to sum 100 for the active `tp_count`. When `tp_count == 1`, `volume_pct = 100` unconditionally. |
| `risk/breakeven_manager.py` | Honour `be_mode`: `RR` ignores `tp1_hit`, `TP_HIT` ignores `be_trigger_rr`, `EITHER` is today's behaviour, `NONE` disables. |
| `risk/engine.py::manage_open_position` | Replace `if trail_method and be_applied:` with `if trail_method and (not trail_require_be_first or be_applied):`. Compute `unrealized_r` against `initial_stop_loss` (Phase 1.1). Honour `trail_mode`. |
| `services/position_manager.py` | Mirror all of the above so live matches backtest. `trail_method_tp1` must resolve. |
| `backtester/engine.py` + `portfolio_engine.py` | The `tp1_hit_groups` sibling-BE block must respect `be_mode`; today it force-BEs siblings on TP1 regardless. Use `_breakeven_stop` with `max(live_spread, atr_buffer, pip_buffer)` instead of `be_buffer_pips` alone. |

## 4.4 The blended-RR problem (`min_rr` re-spec)

The complaint that "TP3 might be 1:5 but the full split volume gives 1:3 or 1:1.5" is exactly right, and
the system currently has **no metric that measures it**. `min_rr` tests only `last_tp_rr`.

Add and surface everywhere (form preview, report, live signal card):

```
blended_rr        = Σ(volume_pct_i × rr_i)                      # if every TP fills
blended_rr_be     = Σ(volume_pct_i × rr_i) over TPs below BE trigger
                    + Σ(volume_pct_j × 0) over TPs above it     # realistic BE-scratch case
expected_rr       = Σ(volume_pct_i × rr_i × p_hit_i)            # p_hit from the run history
breakeven_winrate = 1 / (1 + blended_rr)
```

With `50/30/20` at `1.5/3/5`: `blended_rr = 0.5(1.5) + 0.3(3) + 0.2(5) = 2.65`. With the observed hit
rates (TP1 38%, TP2 1.9%, TP3 1.8%) `expected_rr ≈ 0.62`. **Show both numbers in the form, live, before
the user runs anything.** Then `min_rr` should gate on `blended_rr`, not `last_tp_rr`.

---

# PART 5 — Backtest-result UI: parameter display flow

## 5.1 The defect

`Backtester.jsx:845-851`:

```jsx
{Object.entries(result.params_snapshot).map(([k, v]) => (
  <span>{typeof v === 'object' && v !== null ? JSON.stringify(v) : String(v)}</span>
))}
```

Every nested object — `prop_firm`, `risk_config`, `strategy_params`, `resolved_cost_model`,
`manual_bias_overrides` — renders as a raw JSON blob on one line. The same flat map is used for new
results, saved results, and the saved-run detail view.

## 5.2 The fix: a schema-driven parameter renderer

Build one component, `<ParameterReport snapshot={...} />`, used by **all four** surfaces (live backtest
result, saved-run list detail, saved-run full view, live-bot config review).

**5.2.1 A single parameter schema, generated from the backend.**
Add `GET /api/config/parameter_schema` returning, for every field in `UserConfigV2`:

```json
{ "key": "prop_firm.max_lot_sizes",
  "group": "Prop Firm", "label": "Max Lot Size per Symbol",
  "type": "map<string,number>", "unit": "lots",
  "help": "Hard per-symbol lot ceiling enforced by PropFirmValidator.",
  "default": {}, "affects": ["sizing"] }
```

Generate it from the dataclass field metadata + docstrings that already exist in `config_schema.py`
(those docstrings are excellent and currently invisible to the user). This kills the whole class of
"frontend label drifted from backend meaning" bugs.

**5.2.2 Rendering rules by type.**

| Type | Render |
|---|---|
| scalar | label · value · unit · *(changed from default)* badge |
| bool | pill: ON / OFF |
| enum | pill with the selected option |
| `map<string,number>` (`max_lot_sizes`) | two-column table, empty state = "No per-symbol limits set" |
| nested object (`prop_firm`, `risk_config`) | collapsible section with its own grid — never `JSON.stringify` |
| `resolved_cost_model` | **dedicated table**: Symbol · Spread · Slippage · Commission · Swap L/S · Stops Level · **Source badge** (`USER` / `MT5` / `MT5_PARTIAL` / `ASSET_CLASS_DEFAULT`) — the provenance is already in the payload and is the single most decision-relevant thing in the snapshot |
| null / unset | italic grey "auto (broker)" — **not** `null` |

**5.2.3 Grouping.** Fixed order: Run · Account & Prop Firm · Risk & Sizing · Targets & Exits ·
Strategy Parameters · Costs (resolved) · Advanced. Collapsed by default except Run and Risk & Sizing.

**5.2.4 Diff mode.** Every parameter that differs from the schema default gets a coloured left border and
a tooltip showing the default. A "Show changed only" toggle at the top. This is how you compare two runs
without reading 45 fields.

**5.2.5 Compare drawer.** Select two saved runs → side-by-side parameter diff plus metric delta. This is
the A/B harness for Phase 2.

## 5.3 New diagnostic panels (fed by Phase 0)

1. **Signal Funnel** — a horizontal funnel: Evaluated → Strategy-approved → Risk-approved → Filled →
   Closed. Each drop segment is clickable and lists the gate names and counts. Replaces the dead
   rejection-funnel block.
2. **Risk Deployment** — histogram of `realised_risk_pct` with the target as a vertical line, plus a
   stacked bar of `binding_constraint`. Would have shown the crypto margin problem instantly.
3. **Exit Attribution** — leg counts and median MFE by exit reason, exactly the table in
   `strategy_optimization_research.md §1`. Makes "BE is a scratch generator" self-evident.
4. **Blocked-signal timeline** — signals overlaid on the equity curve as grey markers, coloured by gate.
   Answers "was there a setup on that day" visually.
5. **Cost Impact** — gross PnL vs net PnL, and R-per-trade before/after costs.

## 5.4 Design system

Institutional, dense, monochrome-plus-accent — not dashboard-toy.

- **Layout:** 12-column grid, 8px baseline. Left rail = run metadata + parameter report (sticky).
  Right = metrics, charts, trade table.
- **Type:** one sans (UI) + one tabular-figure mono for all numbers. Numbers right-aligned, fixed
  decimals per unit class.
- **Colour:** neutral greys carry structure; a single green/red pair carries sign; amber = "capped or
  clamped"; blue = "informational/provenance". No colour used decoratively.
- **Every derived number is hoverable** and shows its formula and inputs. Non-negotiable for a system
  where the numbers were previously wrong.
- **Provenance badges everywhere** a value could have come from more than one source (USER / MT5 /
  DEFAULT).
- Same components for backtest and live — one `<RunReport>` that takes a run object, whether historical
  or in-flight.

---

# PART 6 — Implementation-folder reconciliation

Verified file by file against the codebase.

| File | Verdict | Detail |
|---|---|---|
| `strategy_parameter_audit.md` | **~80% applied — keep** | All parameter defaults landed (`config_schema.py` carries the CHANGED annotations). §10.1 fixed (`be_buffer_pips = 0` + `_breakeven_stop` clamp). §10.2 fixed (min-stop + margin guards now exist). §10.6 fixed for APA and FVG-Flip (real confluence scores) but **NOT for VWAP** — `confluence_score=80` is still hard-coded at `strategy_vwap/engine.py:355`. §10.7/§10.8/§10.9/§10.10 unverified. **Do not delete.** |
| `AlgoEdge-Audit-Implementation-Plan.md` | **Partially applied — keep** | §2.3 news endpoint **fixed** (`news_filter.py:278` now uses `nfs.faireconomy.media/ff_calendar_thisweek.json`). §2.1 sizing overshoot **fixed** (`post_split_risk_overshoot` gate). §3.1 spread/slippage **fixed** (cost model live). §2.2 trailing-modify failures **not fixed** — root cause is §1.4 of this document. §3.2/§3.3/§3.4, §4, §5, §6 open. |
| `implementation_plan.md` (Phase 4–7) | **~25% applied — keep** | Phase 4.1 partially: only `VWAPParams` and `BiasIFVGParams` gained `max_trades_per_day`; APA/CRT/FVG-Flip/NY-Retest did not. Phase 7 `InstrumentSlot` — **grep returns zero hits, not started.** Phases 5 and 6 unverified. |
| `doc_conformance_audit.md` | **Findings still open — keep** | §1.2-A APA body-close: **still wrong**. §2.2 VWAP pullback: **still `close < open`**. §4.2 FVG displacement gate: **still absent**. §6.1 NY-Retest M1 + limit order: **still M15/M5, still market-fills**, and `TradeSignal` has no `order_type` field. This is a live checklist. |
| `strategy_optimization_research.md` | **0% applied — keep, it is the spine of Phase 3** | `be_trigger_rr` still 1.5 (not 2.5), `trail_activation_rr` still 1.5, `tp_splits` still 50/30/20, TP3 still present, crypto still enabled for both ICT strategies. The one item it flagged as a *bug* (§1.1-2, the division bug) is confirmed still present as §1.4 here. |
| `AlgoEdge-OrderFlow-Fundamental-Edge-Plan.md` | **Not started — keep** | Forward-looking. Reconciled into §9 below. |
| `AlgoEdge-Visualization-Portfolio-Architecture-Plan.md` | **Not started — keep** | Its §4 (replay engine) and §5 (fundamentals UI) are superseded in scope by Part 5 here; its §7 (portfolio engine) stands. |
| `phase7.txt` | **CORRECTED — DO NOT DELETE** | 3,108 bytes, not empty — my earlier claim it was zero bytes was wrong (verified 2026-08-22). It is your own dictated Phase 7 requirement: per-symbol-per-strategy configuration (the same symbol selectable more than once under different strategies, e.g. USDCHF+VWAP and USDCHF+APA concurrently), each with its own risk_per_trade_pct, max_trades_per_day, max_positions_per_symbol, and a new max_losses_per_day — while max_daily/weekly_drawdown_pct, overall max_concurrent_positions, the TP/RR grid and tp_splits stay global. Also flags that max_positions_per_symbol is enforced in backtest but **not in live trading**, a validation rule needed (per-symbol max positions must not exceed the global cap, mirroring the existing hard-cap-vs-risk-pct check), and a report that backend caching is inefficient and sometimes blocks live trades. Superset of the `InstrumentSlot` idea in `implementation_plan.md` Phase 7 — reconciled and expanded into the new Part 14 below.
| `request.txt` | **Keep as an archive note** | 2 lines, original request text. |
| `lookahead_audit.md` | **RESTORED** | Was deleted from the working tree but fully recoverable — 781 lines, added in commit `58aaeef` and never committed as removed. Restored via `git checkout 58aaeef -- implementation/lookahead_audit.md` (2026-08-22). Verdict inside: no material look-ahead bias found in the single-symbol backtest path. |

**Correction:** `phase7.txt` is not empty and must not be deleted — see above. `lookahead_audit.md` has
been restored. Nothing in `implementation/` is thoroughly complete and nothing further has been deleted.

**Consolidated open-work register** (everything not-done, deduplicated across all seven documents):

1. APA body-close BOS test *(conformance §1.2-A)*
2. APA head-level in-trade invalidation exit *(conformance §1.2-C)*
3. APA structure-freshness rule *(conformance §1.2-D)* — becomes the staleness timeout in §2.1-S2
4. VWAP pullback condition *(conformance §2.2, research §4.2)*
5. VWAP first-pullback-only rule
6. VWAP real confluence score *(param audit §10.6)*
7. HTF-FVG displacement gate *(research §4.1)* — highest single-strategy value
8. NY-Retest M1 timeframe + `order_type` limit fills *(research §4.3)*
9. CRT structural target exempt from the R-grid *(research §4.5)*
10. Bias-IFVG direction question *(research §4.6)* — needs a decision, not code
11. Exit architecture: BE/trail RR triggers, split reweighting *(research §1.2)* — Part 4 here
12. Per-strategy risk fields for the four strategies that lack them *(plan §4.1)*
13. `InstrumentSlot` per-symbol-per-strategy autonomy *(plan Phase 7)*
14. Trailing-modify failures live *(audit §2.2)* — fixed by §1.4 here
15. Drawdown-type configuration *(audit §3.4)*
16. Multi-prop-firm infrastructure *(audit §6)*
17. Portfolio correlation engine *(viz plan §7)*
18. Replay visualisation *(viz plan §4)*
19. Order-flow / gamma data layer *(orderflow plan)* — §9 here

---

# PART 7 — Should these strategies be merged or run independently?

Short answer: **run them independently, but under one portfolio risk governor.** Merging their signals
would destroy the only thing they currently have.

## 7.1 What the data says

Pooled true expectancy (from the 61-run corpus in `strategy_optimization_research.md`, R recomputed
against entry-time risk):

| Strategy | Trades | Expectancy | Win rate | Verdict |
|---|---|---|---|---|
| APA | 306 | +0.136R | 46.4% | best in book |
| VWAP | 2,484 | +0.056R | 45.6% | highest volume, thinnest edge |
| Bias IFVG | 620 | +0.027R | 43.1% | ~0R |
| CRT | 120 | +0.006R | 45.8% | n too small |
| NY Open Retest | 465 | −0.002R | 44.1% | ~0R |
| HTF FVG Flip | 453 | −0.088R | 38.2% | only clearly negative one |

## 7.2 Merge / independent, per pair

**Do NOT merge:**

- **APA + anything.** APA is a *structural reversal* method on a slow timeframe. Everything else is
  continuation or intraday mean-reversion. Merging halves its already-low frequency for no reason.
- **VWAP + ICT strategies.** VWAP's premise is *volume-weighted fair value*; ICT's is *liquidity
  geometry*. They are not the same claim about the market and requiring both agree is a filter, not a
  strategy.
- **DriftJumpAlpha + anything.** Different instrument universe entirely (Deriv synthetics). Structurally
  exempt.

**Genuine merge candidates — 2 of them:**

1. **HTF FVG Flip → fold into Bias→KeyLevel→IFVG.** These are the same ICT method at different
   resolutions: HTF-FVG-Flip is "HTF FVG tapped → inversion", Bias-IFVG is "HTF FVG sets bias → M15 key
   level tapped → M5 IFVG". Bias-IFVG is strictly the more specified of the two, and FVG-Flip is the only
   negative-expectancy engine in the book. **Recommendation: add the displacement gate to the shared
   `FVGDetector`, keep Bias-IFVG, retire FVG-Flip as a standalone strategy and preserve its logic as a
   `require_m5_inversion=false` mode of Bias-IFVG.** Saves an engine, removes the worst performer, keeps
   the option.

2. **NY Open Retest → becomes a *session module* of CRT.** Both are "mark a reference range, wait for a
   sweep/break, trade the return to the level." CRT's C1/C2 is the general form; NY-Open-Retest is the
   0800-ET-anchored special case. **Recommendation: keep them separate until CRT has >300 trades**, then
   re-evaluate. At n=120 for CRT any merge decision is noise.

**Keep fully independent:** APA, VWAP, CRT, Bias-IFVG (absorbing FVG-Flip), DriftJumpAlpha.
That is **five** engines, down from six.

## 7.3 What SHOULD be shared (not merged)

Merging strategies is wrong; sharing *context* is right. Build a single **Market Context Service** that
every engine reads and *scores* against — never a veto:

```
MarketContext(symbol, timestamp) -> {
  htf_trend:        BULLISH | BEARISH | NEUTRAL   (shared MarketStructure)
  session:          ASIAN | LONDON | NY | OVERLAP | DEAD
  volatility_regime: LOW | NORMAL | HIGH          (ATR percentile)
  news_proximity:   minutes to next high-impact event
  correlation_cluster: e.g. "USD_LONG", "RISK_ON"
  vwap_zone:        BELOW_2SD | BELOW_1SD | AT_VALUE | ABOVE_1SD | ABOVE_2SD
  gamma_regime:     LONG_GAMMA | SHORT_GAMMA | UNKNOWN   (§9)
}
```

Every engine adds a **confluence contribution** from this, and the risk layer applies
`size_modifier ∈ [0.5, 1.5]`. This gives you the cross-strategy fundamental/technical alignment you
want **without** the frequency collapse that comes from AND-ing independent systems together.

## 7.4 Portfolio governor

Independent engines need a shared exposure manager, or three strategies fire the same directional bet on
correlated symbols and you take 3% risk thinking you took 1%:

- **Cluster exposure caps** — group symbols by rolling 60-day return correlation (>0.7 = one cluster);
  cap aggregate risk per cluster, not per symbol.
- **Directional netting** — XAUUSD long + XAGUSD long + EURUSD long is one USD-short bet. Cap net
  currency exposure.
- **Per-strategy risk budget** — each engine gets a share of the daily/weekly DD budget so a
  high-frequency engine (VWAP, 2,484 trades) cannot consume the budget a low-frequency one (APA, 306)
  needs. **This alone will materially fix the APA frequency problem in portfolio runs.**

---

# PART 8 — VWAP upgrade, from `implementation/strategy_update/`

## 8.1 What is actually in that folder

27 PNGs: three TapeDragon carousels, no text documents.

- **Carousel A — Order Book / DOM** (slides 01–09): what the order book is, limit vs market orders, why
  price moves, reading liquidity depth, spoofing & iceberg orders.
- **Carousel B — Bookmap / liquidity heatmap** (01–09): reading the heatmap, liquidity as support &
  resistance, pulling & stacking, absorption, exhaustion.
- **Carousel C — VWAP** (01–06+): VWAP with ±1σ/±2σ/±3σ bands, "VWAP is a framework, not a signal",
  common VWAP mistakes.

**There is no gamma-exposure material in these images.** Gamma exposure appears only in
`AlgoEdge-OrderFlow-Fundamental-Edge-Plan.md`. Covered in §9.

## 8.2 The VWAP method as the source states it

- VWAP is **fair value**; the σ bands define **normal (±1σ) / extended (±2σ) / rare (±3σ)**.
- VWAP gives four things: fair value, trend context, extremes, confluence.
- **Mistakes:** trading every VWAP touch · blindly fading ±2σ or ±3σ · ignoring trend · ignoring
  order-flow confirmation.
- **Correct:** wait for confluence · trade with market context · use order-flow confirmation ·
  **"let VWAP guide your bias, not your entry."**
- Formula: `context + confluence + confirmation = high-probability trade`.

## 8.3 Gap analysis against `strategy_vwap/engine.py`

| Source requirement | Current code | Gap |
|---|---|---|
| σ bands (±1/±2/±3) | **Absent** — `_calculate_anchored_vwap` returns the line only | 🔴 The entire framework is missing |
| VWAP guides bias, not entry | VWAP *is* the entry trigger | 🔴 Inverted |
| Pullback must move *toward* VWAP | `latest["close"] < latest["open"]` — any red candle | 🔴 A candle accelerating away qualifies |
| First pullback only | No tracking | 🟠 |
| Order-flow / volume confirmation | None | 🟠 |
| Trend context | `vwap_rising` + 1h momentum % | 🟡 partial |
| Confluence score | hard-coded `80` | 🔴 makes tier-scaling inert |
| Extremes / mean reversion | Not modelled | 🟠 half the method is unimplemented |

## 8.4 VWAP v2 specification

**New params (`strategy_vwap/params.py`):**

```python
vwap_bands_enabled:        bool  = True
vwap_band_sigmas:          list  = [1.0, 2.0, 3.0]
vwap_band_lookback:        int   = 0        # 0 = since anchor
entry_mode:                str   = "PULLBACK_TO_VALUE"   # PULLBACK_TO_VALUE | BAND_REVERSION | BOTH
pullback_requires_convergence: bool = True  # |close-vwap| < |prev_close-vwap|
pullback_max_distance_sigma:   float = 1.0  # only take pullbacks inside +1σ
first_pullback_only:       bool  = True
reversion_min_sigma:       float = 2.0      # never fade inside 2σ
reversion_requires_rejection: bool = True   # wick rejection candle at the band
reversion_requires_trend_neutral: bool = True  # do not fade a strong trend
volume_confirmation_mult:  float = 1.2      # trigger bar volume >= 1.2x rolling mean
```

**Setup 1 — Pullback to Value (trend continuation, replaces today's logic).**
`price > VWAP` AND `VWAP rising` AND momentum up AND price is inside `+1σ` AND the current bar's distance
to VWAP is **smaller** than the previous bar's AND (if `first_pullback_only`) no prior pullback has been
taken since the anchor AND trigger-bar volume ≥ `1.2× rolling mean`. Entry next bar open. Stop below the
pullback swing low or `−1σ`, whichever is further. **Target = `+2σ`, not a fixed R-grid.**

**Setup 2 — Band Reversion (new, the other half of the method).**
Price closes beyond `±2σ` AND `VWAP` slope is flat (|slope| < threshold) AND the bar shows rejection
(wick ≥ 50% of range against the extension) AND volume ≥ `1.2×`. Entry on the close. Stop beyond `±3σ`.
**Target = VWAP (fair value).** This is the "mean reversion to value" trade and it is the classic VWAP
edge — the current engine cannot express it at all.

**Confluence score (replace the hard-coded 80):**

| Component | Points |
|---|---|
| Mandatory chain verified | 40 |
| Distance from VWAP at entry (closer to value = better, for Setup 1) | 0–15 |
| Trend agreement (VWAP slope + HTF structure + momentum all aligned) | 0–15 |
| Volume confirmation ratio | 0–15 |
| Session quality (09:00–11:00 ET best per the data) | 0–15 |

Range 40–100. Feeds `get_confluence_scaled_risk` — which today never bites because 80 is constant.

**Session/exit alignment (fixes §1.7).** With Setup 1 targeting `+2σ` and Setup 2 targeting VWAP, targets
become **reachable inside the session**, so the 47% `SESSION_END` rate collapses on its own. Add
`target_mode: SIGMA_BAND | R_GRID` and default VWAP to `SIGMA_BAND`; exempt it from the global R-grid the
same way CRT should be exempted (§6 item 9).

**Docs deliverable:** `docs/vwap_strategy_v2.md`, superseding
`docs/vwap_strategy_implementation_plan.md`.

---

# PART 9 — Order flow, DOM and gamma exposure

## 9.1 Honest feasibility assessment first

The TapeDragon material is **futures order-flow** (NQ, Bookmap, CME depth). Your execution venue is
**MT5 CFDs**. That matters:

| Data | Available on MT5 CFD? | Verdict |
|---|---|---|
| Level 2 / DOM depth | `mt5.market_book_get()` — **broker-synthesised**, often 5–10 levels, often absent on CFDs | ⚠️ partial, low fidelity |
| Time & sales / tape | `mt5.copy_ticks_range()` with `COPY_TICKS_ALL` | ✅ available, **no aggressor flag** — must be inferred from bid/ask |
| Real volume | CFDs report broker volume, not exchange volume | ⚠️ proxy only |
| Bookmap-style heatmap | requires full-depth history | ❌ not reconstructable from MT5 CFD data |
| CME/CBOE options open interest (for GEX) | not from MT5 | ❌ needs a separate vendor |

**Conclusion: do not build a Bookmap clone.** Build the two things that *are* reconstructable and *do*
transmit into price on your instruments.

## 9.2 Tier 1 — Tick-derived order flow (buildable now, MT5-only)

New module `backend/data/orderflow.py`:

- **CVD (Cumulative Volume Delta)** — classify each tick as buy/sell by proximity to bid/ask
  (`tick.last >= tick.ask` → buy, `<= tick.bid` → sell, else mid-rule). Accumulate per bar and per
  session. This is the single most useful order-flow primitive and it *is* computable from
  `copy_ticks_range`.
- **Delta divergence** — price makes a new high, CVD does not. The "exhaustion" concept from Carousel B.
- **Absorption** — high delta, minimal price movement, at a level. The "absorption" concept from
  Carousel B, expressed without a heatmap.
- **Volume profile / VPOC / value area** — from tick data per session. Gives the "where is liquidity
  resting" answer at bar resolution.

**Wire these into `MarketContext` (§7.3) as confluence contributors, not vetoes.** Concretely: a VWAP
Setup-2 band reversion with CVD divergence at the band scores +15; without it, +0. That is the
"order-flow confirmation" the source material demands, in the only form your data supports.

## 9.3 Tier 2 — Gamma exposure (needs external data)

**Mechanism.** Options dealers hedge their book. Net **long gamma** → they sell rallies and buy dips →
volatility is suppressed, price pins to large strikes → *mean-reversion regime*. Net **short gamma** →
they buy rallies and sell dips → moves amplify → *momentum/trend regime*.

**Where it transmits (be strict about this):**

| Instrument | GEX applies? |
|---|---|
| SPX500 / US500 | ✅ strongly — SPX options are the deepest gamma pool in the world |
| NAS100 / NDX100 | ✅ via NDX + QQQ |
| US30 | 🟡 weakly |
| XAUUSD | 🟡 via GLD and COMEX gold options |
| EURUSD / GBPJPY | 🟡 FX option expiries and barrier levels, different mechanism |
| BTCUSD / XRPUSD | 🟡 Deribit for BTC only; XRP has no meaningful options market |
| Deriv synthetics | ❌ **hard stop — there is no underlying and no options market** |

**Data sources** (needs a decision — this is a paid dependency):
CBOE DataShop (authoritative, paid) · Deribit API (free, BTC/ETH only) ·
Unusual Whales / SpotGamma / Menthor Q (paid, pre-computed GEX).

**Implementation:** `backend/data/gamma_service.py`, daily pull after the prior close, computing:

```
GEX(strike) = OI × gamma × contract_multiplier × spot² × 0.01 × (+1 call, −1 put)
total_gex   = Σ GEX(strike)
gamma_flip  = spot where cumulative GEX crosses zero
call_wall   = strike with max positive GEX     (resistance / pin)
put_wall    = strike with max negative GEX     (support)
```

**How it should be used — as a regime switch, never a signal:**

| Regime | Effect |
|---|---|
| `spot > gamma_flip` (long gamma) | favour mean-reversion setups (VWAP Setup 2, CRT). Down-weight breakout setups (NY-Retest, FVG-Flip) to `size_modifier 0.75`. Treat `call_wall` as a TP magnet |
| `spot < gamma_flip` (short gamma) | favour momentum/breakout. Up-weight to `1.25`. Widen stops — realised vol expands |
| Within 0.3% of a wall | reduce size; expect pinning into expiry |
| No data | `UNKNOWN`, `size_modifier = 1.0`, log it |

**Docs deliverable:** `docs/strategy-orderflow-gex.md` and `docs/orderflow_data_layer.md`.

## 9.4 What I recommend you do NOT build

- Bookmap-style liquidity heatmap on MT5 CFD data — the data does not exist.
- DOM spoofing detection — CFD depth is broker-synthesised; detecting spoofing in a synthetic book
  detects your broker, not the market.
- GEX on synthetics or XRP — no underlying options market.

---

# PART 10 — Documentation requirement & delivery sequence

## 10.1 Docs policy (as requested)

Every strategy gets a `docs/*.md` written **when the implementation plan is finalised** and **updated
when the implementation lands**, stating: the source method, the exact rules implemented, the parameters
and their defaults with reasoning, deliberate deviations from the source and why, the asset classes it is
valid on, and the measured baseline.

| Doc | Status |
|---|---|
| `docs/apa_strategy_implementation_plan.md` | exists — **update** after Phase 2 |
| `docs/vwap_strategy_implementation_plan.md` | exists — **supersede** with `docs/vwap_strategy_v2.md` |
| `docs/CRT_Strategy_Spec.md` | exists — update for the target-mode exemption |
| `docs/strategy-1-htf-fvg-flip.md` | exists — **mark deprecated**, merged into Bias-IFVG |
| `docs/strategy-2-bias-keylevel-ifvg.md` | exists — update for the absorbed FVG-Flip mode |
| `docs/strategy-3-nyopen-break-retest.md` | exists — update for M1 + limit fills |
| `docs/RiskManagement_Spec.md` | exists — **major update** for Part 4 (RR-triggered exits, blended RR) |
| `docs/strategy-orderflow-gex.md` | **new** |
| `docs/orderflow_data_layer.md` | **new** |
| `docs/backtest_report_ui.md` | **new** — the parameter schema contract from §5.2.1 |

## 10.2 Sequence

| Phase | Content | Gate to proceed |
|---|---|---|
| **0** | Observability (§3 Phase 0) | Funnel renders; APA's missing trades have named causes |
| **1** | Correctness (§3 Phase 1) | Re-run the book: **R-multiples should barely move; dollar figures will move a lot.** If R moves materially, something is still wrong |
| **2** | Filter re-disposition (§2) | Trade counts rise; expectancy does not collapse. A/B against Phase 1 |
| **3** | Exit architecture + RR triggers (Part 4) | `blended_rr` and `expected_rr` visible; BE scratch rate down |
| **4** | UI (Part 5) | Parameter report + 5 diagnostic panels shipped |
| **5** | VWAP v2 (Part 8) | σ bands + two setups; `SESSION_END` share below 15% |
| **6** | Portfolio governor + strategy consolidation (Part 7) | Five engines, cluster caps, per-strategy budgets |
| **7** | Order flow Tier 1 (§9.2) | CVD/absorption feeding `MarketContext` |
| **8** | GEX Tier 2 (§9.3) | Requires a paid-data decision from you first |

## 10.3 Decisions I need from you

1. **Margin ceiling.** Raise `max_margin_utilisation_pct` above 30%, or accept smaller crypto size and
   have the system *tell* you it capped? (My recommendation: make it a parameter, default 50%, and reject
   below `min_deployable_risk_pct = 0.25%` rather than silently taking a 0.05% position.)
2. **`max_positions_per_symbol`.** Raise to 3 (my recommendation), or keep 1 and accept the frequency?
3. **Confluence below 55** — clamp to a 25% risk floor, or keep the outright rejection?
4. **HTF FVG Flip** — retire it as a standalone engine and fold into Bias-IFVG, as recommended?
5. **Bias-IFVG direction question** (`doc_conformance_audit §5`) — IFVG on the approach leg, or after the
   tap? This blocks evaluating that strategy at all.
6. **GEX data vendor** — CBOE DataShop / SpotGamma / Menthor Q / skip Tier 2 for now?
7. ~~Deletions~~ — **resolved 2026-08-22**: `phase7.txt` was not empty (my earlier claim was wrong) and
   has not been deleted; `lookahead_audit.md` has been restored from git. See Part 14 for the new work
   `phase7.txt` describes.

---

# PART 11 — Full-codebase defect register

Every entry states **what is wrong**, **where**, **what it costs you**, **the fix**, and
**why that fix and not another**. Severity: 🔴 corrupts money/risk · 🟠 corrupts results or parity ·
🟡 correctness/maintainability.

Method: programmatic audit of all 81 `INSTRUMENT_PROFILES`, cross-layer parameter reachability check
(frontend → request model → engine), and line-by-line read of `risk/`, `backtester/`, `mt5/`,
`services/position_manager.py`, `services/bot_service.py`, all 8 strategy engines, and the four
frontend surfaces.

---

## 11.0 Index — every defect at a glance

**70 entries** (65 distinct defects; 5 are cross-references to an entry detailed under another heading). **26 🔴** money/risk-corrupting · **37 🟠** results/parity-corrupting · **7 🟡** correctness.

Each entry below the index states **Where · Cost · Fix · Why**. Scroll to the ID.


**A. Position sizing and risk**

| ID | | Defect | File |
|---|---|---|---|
| **A1** | 🔴 | The margin ceiling is a hardcoded 30% and overrides your risk setting | `position_sizer.py` |
| **A2** | 🔴 | `FALLBACK_ACCOUNT_LEVERAGE = 100.0` hardcoded, and your leverage setting is dead | `position_sizer.py` |
| **A3** | 🔴 | Lot rounding rounds *up*, and clamps *up* to `volume_min` | `position_sizer.py` |
| **A4** | 🔴 | Sizing uses the un-slipped entry; P&L uses the slipped entry | `engine.py` |
| **A5** | 🔴 | SL and TP are anchored to the signal price, not the fill | `engine.py` |
| **A6** | 🟠 | Exit slippage is never applied | — |
| **A7** | 🟠 | `get_confluence_scaled_risk` hardcodes a 4-tier risk ladder | `position_sizer.py` |
| **A8** | 🟠 | `MIN_STOP_SPREAD_MULTIPLE = 2.0` and `MIN_STOP_FLOOR_PIPS` are hardcoded | `position_sizer.py` |
| **A9** | 🟠 | Risk-cap tolerances 1.05 / 1.10 / 1.01 hardcoded | `engine.py` |
| **A10** | 🟠 | TP-split volume flooring inflates risk, then silently drops TP levels | `multi_tp.py` |
| **A11** | 🟠 | `tp1_rr` is hardcoded to 0.5 for one strategy | `multi_tp.py` |
| **A12** | 🟡 | `kelly_lot_size` returns dollars, not lots | `position_sizer.py` |

**B. Units — pips / points**

| ID | | Defect | File |
|---|---|---|---|
| **B1** | 🔴 | "One pip" means eleven different things, and every pip-denominated parameter inherits that | — |
| **B2** | 🔴 | `get_pip_size` contains two contradictory definitions of a silver pip | `position_sizer.py` |
| **B3** | 🟠 | `sl_points` on indices is multiplied by `pip_size`, defeating its own purpose | `engine.py` |
| **B4** | 🟠 | NY-Retest `stop_buffer_points` / `fixed_target_points` multiplied by `pip_size` | `engine.py` |
| **B5** | 🟠 | `MIN_STOP_FLOOR_PIPS` produces floors that differ 4× inside one asset class | `position_sizer.py` |
| **B6** | 🟠 | `deviation: 20` hardcoded on every live order | `order_manager.py` |
| **B7** | 🟠 | `min_distance = max(stops_level, spread, 2) * point` in live SL modification | `order_manager.py` |
| **B8** | 🟡 | `calculate_pips` in analytics uses a fourth conversion | `metrics.py` |

**C. Instrument profile data**

| ID | | Defect | File |
|---|---|---|---|
| **C1** | 🔴 | Thirteen FX profiles have a frozen exchange rate baked into `point_value_per_lot` | `compounding.py` |
| **C2** | 🔴 | GER40 is internally inconsistent | — |
| **C3** | 🔴 | XRPUSD and DOGUSD lot constraints are ~5,000× off what the broker reports | — |
| **C4** | 🟠 | XRPUSD via profile cannot express your risk at all | — |
| **C5** | 🟠 | Symbols with no profile silently refuse to trade | — |
| **C6** | 🟡 | `min_lot_value()` helper hardcodes 0.01 | `compounding.py` |

**D. Backtest ↔ live parity**

| ID | | Defect | File |
|---|---|---|---|
| **D1** | 🔴 | Live sizing compounds; backtest sizing does not | `engine.py` |
| **D2** | 🔴 | Twelve trailing/BE parameters reach live but not backtest | `backtest.py` |
| **D3** | 🔴 | `trail_pct` means 0.5% in one file and 50% in another | `config_schema.py` |
| **D4** | 🔴 | Live `RiskEngine` is cached on a 3-field fingerprint and ignores your other edits | `bot_service.py` |
| **D5** | 🔴 | A hardcoded `risk_pips = 20.0` fallback in live BE and trailing | `position_manager.py` |
| **D6** | 🟠 | Live and backtest use different break-even buffer formulas | — |
| **D7** | 🟠 | Live trails without break-even; backtest requires break-even first | `engine.py` |
| **D8** | 🟠 | The TP1 sibling break-even cascade ignores `be_on_tp1_hit` | `engine.py` |
| **D9** | 🟠 | Live order volume is rounded up and clamped up | `order_manager.py` |
| **D10** | 🟠 | Live `group_id = signal.symbol`; backtest uses a UUID | `bot_service.py` |

**E. Config plumbing & dead controls**

| ID | | Defect | File |
|---|---|---|---|
| **E1** | 🔴 | Three layers hold three different sets of defaults | — |
| **E2** | 🔴 | `**req.risk_config` silently overrides every typed field | `backtest.py` |
| **E3** | 🔴 | Three UI controls are wired to nothing | — |
| **E4** | 🟠 | `trail_method_tp1` is sent by the frontend and discarded | `Backtester.jsx` |
| **E5** | 🟠 | `max_positions_per_symbol` exists in the request model but not in the Backtester form | — |
| **E6** | 🟠 | The live Settings page and the Backtester page expose different parameter sets | — |
| **E7** | 🟠 | Prop-firm position limits are hardcoded | `prop_firm_validator.py` |
| **E8** | 🟠 | Minimum-trading-day credit hardcodes 0.5% | `prop_firm_validator.py` |
| **E9** | 🟡 | Crypto cost heuristics hardcoded as percentages of price | `broker_costs.py` |
| **E10** | 🟡 | No validation that `tp_splits` sums to 100 | `multi_tp.py` |

**F. Exit management**

| ID | | Defect | File |
|---|---|---|---|
| **F1** | 🔴 | `original_sl` key does not exist (full detail in §1.4) | — |
| **F2** | 🔴 | `trail_method_tp1` cannot exist → single-TP mode never trails (Part 4) | — |
| **F3** | 🟠 | Trailing is gated behind `be_applied` in backtest only (D7) | `engine.py` |
| **F4** | 🟠 | BE buffer formulas differ (D6) | `engine.py` |
| **F5** | 🟠 | TP1 sibling cascade is unconditional (D8) | `engine.py` |
| **F6** | 🟠 | `min_rr` tests only the last TP, so it measures nothing | `engine.py` |

**G. Strategy engines**

| ID | | Defect | File |
|---|---|---|---|
| **G1** | 🔴 | APA `AWAIT_RETEST` has no timeout and the daily reset protects the deadlock | `engine.py` |
| **G2** | 🔴 | APA tracks one pattern per symbol | — |
| **G3** | 🔴 | APA's "body close" test is not a body close | `engine.py` |
| **G4** | 🟠 | APA's `is_major` neckline gate destroys ~50% of detected patterns | `engine.py` |
| **G5** | 🔴 | VWAP's pullback condition is any red candle | `engine.py` |
| **G6** | 🟠 | VWAP hardcodes `confluence_score = 80` | `engine.py` |
| **G7** | 🟠 | CRT blocks 254 evaluations on `HTF bias NEUTRAL` and 80 valid setups on session | `engine.py` |
| **G8** | 🟠 | HTF-FVG-Flip has no displacement gate | `fvg.py` |

**H. Frontend**

| ID | | Defect | File |
|---|---|---|---|
| **H1** | 🔴 | The rejection funnel is read from a path that is never populated | `Backtester.jsx` |
| **H2** | 🟠 | Nested parameters render as raw JSON | `Backtester.jsx` |
| **H3** | 🟠 | `summaryEngine.tradeR` falls back to the mutated stop | `summaryEngine.js` |
| **H4** | 🟠 | `DEFAULT_FORM` is a hardcoded mirror of the backend dataclass | `Backtester.jsx` |
| **H5** | 🟡 | `max_lot_sizes` is edited as a raw JSON string | `Backtester.jsx` |
| **H6** | 🟡 | Metric cards show no denominator or sample size | `Backtester.jsx` |

**I. Reporting & diagnostics**

| ID | | Defect | File |
|---|---|---|---|
| **I1** | 🔴 | `rejection_funnel` missing from the single-symbol response (§1.2, H1) | `backtest.py` |
| **I2** | 🟠 | `run_logs` truncated to the last 100 entries | `backtest.py` |
| **I3** | 🟠 | No per-trade sizing diagnostics | — |
| **I4** | 🟠 | No blocked-signal record | — |

---

## A. Position sizing and risk

### A1 🔴 The margin ceiling is a hardcoded 30% and overrides your risk setting
**Where:** `risk/position_sizer.py:57` `MAX_MARGIN_UTILISATION_PCT = 30.0`, applied in `_margin_capped_lots`.
**Cost:** every crypto trade is pinned to $7,500 notional on a $25k account (measured: XRP median $7,496,
p10–p90 $7,455–$7,531). You set 1%; you got 0.05–0.47%.
**Fix:** delete the module constant. Add `RiskParams.max_margin_utilisation_pct: float = 0.0`, where
**0 disables the check entirely**. Read it through `risk_config`. Add
`RiskParams.min_deployable_risk_pct: float = 0.0` — when the margin cap would push realised risk below
this, **reject the signal with reason `margin_ceiling_below_min_risk`** instead of silently taking a
tiny position. Emit `margin_truncation_pct` on every trade record.
**Why this way:** the guard is real — MT5 returns retcode 10019 on an un-marginable order — so removing
it outright would just move the failure to live execution. But a *silent* clamp is worse than either
extreme: it produced 8 months of backtests that measured a strategy you never configured. Defaulting to
0 (off) hands the decision back to you, which is what you asked for; a trader who wants the guard sets a
number. Rejecting below a floor is better than a 0.05% position because a 0.05% position still consumes
a `max_positions_per_symbol` slot and a `max_daily_trades` slot while being unable to move the account.

### A2 🔴 `FALLBACK_ACCOUNT_LEVERAGE = 100.0` hardcoded, and your leverage setting is dead
**Where:** `risk/position_sizer.py:63`; `RiskParams.max_account_leverage` (`config_schema.py:102`) has
**zero backend readers** (verified by grep across `risk/`, `backtester/`, `services/`) while
`Settings/Risk.jsx:182` presents it with the hint *"Caps total notional against equity. Blocks the 100×+
sizes MT5 rejects with retcode 10019."*
**Cost:** you set leverage 30 and nothing happens; the invisible 30% notional cap of A1 is what actually
binds. The UI describes A1's behaviour and attributes it to a control that does nothing.
**Fix:** wire `max_account_leverage` into `_margin_capped_lots` as the leverage used in the
notional/leverage estimate when MT5 cannot answer, replacing the constant. `0` = use MT5's reported
account leverage, and only fall back to the parameter if MT5 is unreachable.
**Why:** a leverage figure is something only you know per account/broker. A constant cannot be right
across a 1:30 prop account and a 1:500 retail one, and a control that lies about what it does is worse
than no control.

### A3 🔴 Lot rounding rounds *up*, and clamps *up* to `volume_min`
**Where:** `risk/position_sizer.py::calculate_lot_size`:
```python
clamped = max(info["volume_min"], min(info["volume_max"], raw_lot))
rounded = round(clamped / step) * step
```
**Cost:** both operations can *increase* risk above the target. On XRPUSD's profile
(`volume_min = 50`, `step = 10`) a request for 0.6 lots becomes 50 lots — an 80× over-size. The
`max_risk_hard_cap_pct` net catches it afterwards, but only by clamping to the *cap*, not to your
*target*, so the realised risk is the cap, not what you asked for.
**Fix:** floor to the step (`math.floor(raw/step)*step`), never round. If the floored lot is below
`volume_min`, do **not** clamp up — return 0 with reason `below_broker_min_lot` and record the requested
vs minimum size, so the funnel shows "this account/stop combination cannot be traded on this symbol".
**Why:** a risk budget is a ceiling, not a target to be rounded toward. Rounding up on a 2-step position
overshoots by up to 50%. Silently clamping up to `volume_min` converts a "cannot trade this" into "trade
it at unknown risk", which is exactly the class of surprise you are trying to eliminate.

### A4 🔴 Sizing uses the un-slipped entry; P&L uses the slipped entry
**Where:** `calculate_lot_size` receives the raw `entry`; `backtester/engine.py::_calc_pnl` shifts the
effective entry by `slippage_pips` before computing P&L, and separately deducts `spread_pips`.
**Cost:** realised loss on a stop-out is `(sl_distance + slippage + spread) × lots`, systematically
above the budget. On EURUSD (0.4 slip + 1.2 spread against a 12-pip stop) that is +13% risk on every
trade.
**Fix:** compute `effective_sl_distance = sl_distance + (slippage_pips + spread_pips) × pip_size` inside
`calculate_lot_size` and size against that. Pass the resolved cost dict into the sizer rather than
re-resolving it.
**Why:** the sizer's contract is "if the stop is hit, I lose exactly `risk_pct`". Costs are part of the
loss, so they belong in the denominator. Adjusting after the fact (a post-hoc scale-down) would fight
the lot step and produce a different answer at every step boundary.

### A5 🔴 SL and TP are anchored to the signal price, not the fill
**Where:** `backtester/engine.py::_create_position` copies `sig["stop_loss"]` and `tp.tp_price` verbatim
while `entry_price` is the next bar's open.
**Cost:** measured fill drift, expressed in R: VWAP XAUUSD p10 −0.656R / p90 +0.268R; NY-Retest p10
−0.536R. **The risk actually taken ranges from roughly 0.5× to 1.5× the risk sized**, and every R-based
statistic downstream inherits the error. This is the single largest contributor to the 0.4%–2.3% risk
dispersion on FX and metals.
**Fix:** at fill time, recompute `sl = fill ∓ sl_distance` and `tp_n = fill ± n × sl_distance`, where
`sl_distance` is the geometry the strategy intended (`|signal_entry − signal_sl|`). Keep
`signal_entry_price` and `signal_stop_loss` on the record for audit. Apply the identical re-anchor in
`mt5/order_manager.py::place_market_order` so live does the same thing.
**Why:** the strategy's claim is about *distance* (a structural stop is N points behind the level), not
about an absolute price it never traded at. Re-anchoring preserves both the intended risk and the
intended R-multiple. The alternative — rejecting any fill that drifts — would throw away most signals
on gappy instruments, and `validate_at_fill_price` already handles the genuinely un-placeable cases.

### A6 🟠 Exit slippage is never applied
**Where:** `_calc_pnl` shifts only the entry. Measured: `exit_price − stop_loss == 0.0` on **216 of 216**
non-gapped SL exits in VWAP XAUUSD.
**Cost:** stops fill at the exact price in backtest and never do live. Understates every loss by roughly
one slippage increment.
**Fix:** add `exit_slippage_pips` (defaulting to `slippage_pips` when unset) and apply it adversely on
`SL`, `BE_SL`, `TRAIL_SL`, `SESSION_END` and `TIME_LIMIT` exits. Do **not** apply it on `TP` exits.
**Why:** stop and market exits cross the spread and are subject to queue slippage; take-profits are
limit orders that fill at their price or not at all. Applying one figure to both would systematically
misstate the winners in the opposite direction.

### A7 🟠 `get_confluence_scaled_risk` hardcodes a 4-tier risk ladder
**Where:** `risk/position_sizer.py:690-711` — `≥80 → 100%`, `≥65 → 75%`, `≥55 → 50%`, `<55 → 0%`.
**Cost:** an undeclared 25–50% risk reduction, and an outright rejection below 55, neither of which
appears in any config or UI. VWAP hardcodes `confluence_score = 80` so it never bites there, which means
the ladder silently applies to some strategies and not others.
**Fix:** move the ladder into `RiskParams` as `confluence_risk_tiers: list[tuple[int, float]]` defaulting
to `[(0, 1.0)]` — i.e. **no scaling unless you configure it**. Add
`reject_below_confluence: int = 0` as a separate, explicit control.
**Why:** you asked for full autonomy over risk. A confluence ladder is a legitimate technique, but as a
constant it silently overrides the number you typed. Defaulting to flat 1.0 means the system does what
you said; turning the ladder on is then a deliberate act with a visible table.

### A8 🟠 `MIN_STOP_SPREAD_MULTIPLE = 2.0` and `MIN_STOP_FLOOR_PIPS` are hardcoded
**Where:** `risk/position_sizer.py:31, 38-48`.
**Cost:** `minimum_stop_distance` refuses to size (returns 0 lots) when the stop is inside
`max(stops_level, 2 × spread, asset-class floor)`. On XAGUSD with the broker reporting `spread_pips = 94`,
that floor is 188 pips of silver. Signals vanish with no funnel entry.
**Fix:** `RiskParams.min_stop_spread_multiple: float = 2.0` and
`RiskParams.min_stop_floor_pips: dict[str, float]`, both user-editable, both surfaced in the UI, and the
rejection recorded as `stop_below_min_viable` with the driver name and the computed distance.
**Why:** the *concept* is sound — a stop inside the spread is not a stop. But 2.0 is a judgement call
that depends on your broker and holding period, and an unnamed rejection is indistinguishable from
"no setup", which is the core complaint.

### A9 🟠 Risk-cap tolerances 1.05 / 1.10 / 1.01 hardcoded
**Where:** `risk/engine.py:237` `max_risk_cap_dollars = requested_risk_dollars * 1.05`; `:257` reject at
`> 1.10`; `:284` warn at `> 1.01`.
**Cost:** a 10% silent over-risk band, then a hard rejection with no user control over either boundary.
**Fix:** `RiskParams.post_split_risk_tolerance_pct: float = 5.0` driving all three (cap = 1 + t/100,
reject = 1 + 2t/100, warn = 1 + t/500).
**Why:** the tolerance exists because lot-step quantisation makes exact matching impossible. How much
quantisation slop is acceptable depends on your account size relative to the instrument — it is a
parameter, not a constant.

### A10 🟠 TP-split volume flooring inflates risk, then silently drops TP levels
**Where:** `risk/multi_tp.py::calculate_tp_levels`:
```python
vol = math.floor(raw_vol / lot_step) * lot_step
vol = max(lot_min, round(vol, 4))          # <- inflates
```
then the cap-enforcement block drops TP levels from the end until risk fits.
**Cost:** on small accounts, three legs each floored up to `lot_min` can exceed the whole budget; the
system then silently trades **1 TP instead of 3**, so the exit plan you configured is not the exit plan
that ran. Nothing in the report says so.
**Fix:** (a) record `tp_levels_requested` and `tp_levels_placed` on the trade and in the report;
(b) before dropping levels, try re-allocating the whole budget to the *first* N levels that fit;
(c) when `tp_count == 1`, skip the split path entirely.
**Why:** dropping the far targets changes the strategy's expectancy profile — those are the legs
carrying the tail. If the account cannot express a 3-way split, you need to know that explicitly so you
can choose between a smaller split and a different instrument, rather than discovering it from a
trade-by-trade audit.

### A11 🟠 `tp1_rr` is hardcoded to 0.5 for one strategy
**Where:** `risk/multi_tp.py:121` `tp1_rr_used = 0.5 if strategy_id == "DriftJumpAlpha" else self.tp1_rr`.
**Cost:** DriftJumpAlpha ignores your TP1 setting. A strategy-specific override buried in a shared
module is also where the next such override will go.
**Fix:** delete the branch. Add `tp1_rr_override: float | None = None` to `DriftJumpAlphaParams` and
resolve strategy overrides through the params object the same way every other strategy parameter is
resolved.
**Why:** you said stop deciding risk for me. Strategy-specific exit geometry is legitimate, but it
belongs in that strategy's params where you can see and change it — not as an `if` on a string literal
inside the shared TP manager.

### A12 🟡 `kelly_lot_size` returns dollars, not lots
**Where:** `risk/position_sizer.py:673-690` — returns `balance * fraction`.
**Cost:** none today (no callers), but the name guarantees a future misuse that would size a position at
`balance × 0.25` lots.
**Fix:** rename to `kelly_risk_fraction` and return the fraction only; let the caller feed it into
`calculate_lot_size` as `risk_pct`. Or delete it — nothing uses it.
**Why:** a function whose name promises lots and returns currency is a latent 100,000× sizing bug.

---

## B. Units — the systemic pips/points problem

This is the root cause behind several of the sizing surprises and it deserves to be read as one finding.

### B1 🔴 "One pip" means eleven different things, and every pip-denominated parameter inherits that
**Measured** via `get_pip_size()` on the profiles actually in use:

| Symbol | pip_size | `min_sl_pips = 12` becomes | `trail_pips = 15` becomes |
|---|---|---|---|
| EURUSD | 0.0001 | 1.2 pips of price ✅ | 1.5 pips ✅ |
| GBPJPY | 0.01 | 12 pips ✅ | 15 pips ✅ |
| XAUUSD | 0.1 | $1.20 ✅ | $1.50 ✅ |
| **XAGUSD** | **0.001** | **$0.012** ❌ | **$0.015** ❌ |
| **NAS100** | **0.25** | **3 index points** ❌ | **3.75 points** ❌ |
| US500 | 0.1 | 1.2 points ❌ | 1.5 points ❌ |
| US30 | 1.0 | 12 points | 15 points |
| **BTCUSD** | **1.0** | **$12 stop on Bitcoin** ❌ | **$15 trail** ❌ |
| **XRPUSD** | **0.0001** | **$0.0012 on a $2 coin** ❌ | **$0.0015** ❌ |
| ETHUSD | 0.01 | $0.12 ❌ | $0.15 ❌ |

Affected parameters: `min_sl_pips`, `trail_pips`, `trail_step_pips`, `be_buffer_pips`, `spread_pips`,
`slippage_pips`, `stops_level_pips`, `MIN_STOP_FLOOR_PIPS`, `stop_buffer_points`,
`fixed_target_points`, `sl_points`, `spike_threshold_pips`, `recovery_target_pips`.

**Fix — three parts, in this order:**
1. **Introduce an explicit unit type.** Every distance parameter becomes a `{value, unit}` pair where
   `unit ∈ {PIPS, POINTS, PRICE, ATR, PCT_OF_PRICE, R}`. A single resolver
   `resolve_distance(spec, symbol, atr, price) -> float (price units)` is the only place conversion
   happens.
2. **Default every cross-asset distance parameter to `ATR` or `PCT_OF_PRICE`**, not `PIPS`. `min_sl` at
   `1.0 × ATR` means the same thing on XRP and on the DAX; `12 pips` does not.
3. **Keep `PIPS`/`POINTS` available** for single-asset tuning, and show the resolved price distance next
   to the input in the UI (*"12 pips = $12.00 on BTCUSD"*), so a nonsense value is visible before the
   run, not after.

**Why this way:** the alternative — redefining `get_pip_size` so every asset has a "sensible" pip — is
what produced the current mess, because there is no cross-asset definition of a pip that is
simultaneously correct for FX, a 0.25-tick index and a $100k coin. Making the unit explicit removes the
guess. Defaulting to ATR makes a single config work across your whole universe, which is the actual
requirement.

### B2 🔴 `get_pip_size` contains two contradictory definitions of a silver pip
**Where:** `risk/position_sizer.py::get_pip_size` — the profile branch returns `profile.point_size`
(0.001 for XAGUSD); the hardcoded fallback list two blocks below says
`silver_platinum → 0.01`. The profile branch wins, so silver is 10× off the convention used in the same
function.
**Cost:** the broker reporting `spread_pips = 94` on XAGUSD is really ~94 *points*; consumed as pips it
inflates every spread-derived floor by 10×.
**Fix:** delete the hardcoded substring lists entirely. `get_pip_size` resolves from the profile only,
and a symbol with no profile raises rather than guessing (see C5).
**Why:** two sources of truth in one function is not a tuning problem, it is a coin flip. The substring
list also matches on fragments (`"XAU" in symbol`), so a symbol like `XAUEUR` silently inherits gold's
pip.

### B3 🟠 `sl_points` on indices is multiplied by `pip_size`, defeating its own purpose
**Where:** `strategy_vwap/engine.py:202` `sl_dist = self.params.sl_points * pip_size`. The surrounding
comment says points are used "ONLY for index instruments" as a native unit.
**Cost:** `sl_points = 80` (the source strategy's NQ figure) becomes **20 index points on NAS100**
(pip 0.25), 8 on US500 (pip 0.1), and 80 on US30 (pip 1.0). The one path meant to be in native units is
mis-scaled on every index except US30.
**Fix:** with B1 in place, declare it `{value: 80, unit: POINTS}` and let the resolver multiply by
`profile.point_size`, not `pip_size`.
**Why:** `point_size` is the instrument's actual tick; `pip_size` is a derived convenience that differs
from it by 10× on FX and gold. A "point" parameter must use points.

### B4 🟠 NY-Retest `stop_buffer_points` / `fixed_target_points` multiplied by `pip_size`
**Where:** `strategy_six_ny_open_retest/engine.py:113, 168, 171`.
**Cost:** same class as B3 — `stop_buffer_points = 5` is 1.25 index points on NAS100 and $5 on BTCUSD.
**Fix:** same resolver; `POINTS` unit.
**Why:** consistency — one conversion path, not one per engine.

### B5 🟠 `MIN_STOP_FLOOR_PIPS` produces floors that differ 4× inside one asset class
**Where:** `position_sizer.py:38-48`. `INDEX: 3.0` → 0.75 points on NAS100 but 3 points on US30.
`COMMODITY: 5.0` → $0.50 on gold but $0.005 on silver. `CRYPTO: 5.0` → $5 on BTC, $0.0005 on XRP.
**Fix:** re-express as `min_stop_floor_atr_mult` (a fraction of ATR) with an optional per-symbol pip
override. Default 0.25 × ATR.
**Why:** the floor's job is "not inside the noise". Noise is measured in volatility, not in a unit whose
meaning changes per symbol.

### B6 🟠 `deviation: 20` hardcoded on every live order
**Where:** `mt5/order_manager.py:138` (`place_market_order`) and `:346` (`close_position`).
**Cost:** MT5 `deviation` is in **points**. 20 points = $20 on BTCUSD, 5 index points on NAS100,
0.002 on XRPUSD, 2 pips on EURUSD. Also completely disconnected from the backtest's `slippage_pips`, so
the two never agree.
**Fix:** `RiskParams.max_slippage` as a `{value, unit}` spec, resolved to points per symbol. Default it
from the same resolved cost model the backtester uses.
**Why:** if the backtester assumes 0.4 pips of slippage and the live order accepts 20 points, live and
backtest are not simulating the same execution — which is the parity requirement you stated.

### B7 🟠 `min_distance = max(stops_level, spread, 2) * point` in live SL modification
**Where:** `mt5/order_manager.py:235`. The literal `2` is a points floor.
**Fix:** replace with the same `minimum_stop_distance()` the sizer uses, so one function defines "how
close can a stop legally be" for sizing, backtest fills and live modifications.
**Why:** three different answers to the same question is how a stop gets sized in backtest, accepted at
entry, and then rejected on the first trailing modification.

### B8 🟡 `calculate_pips` in analytics uses a fourth conversion
**Where:** `analytics/metrics.py` calls `calculate_pips(symbol, entry, exit)` for `pnl_pips`.
**Fix:** route through the single resolver.
**Why:** reported pip figures should match the pip the sizer used, or the report is describing a
different trade.

---

## C. Instrument profile data errors

Verified programmatically: `point_value_per_lot / point_size` must equal `contract_size` for a
USD-quoted instrument. **14 of 81 profiles fail.**

### C1 🔴 Thirteen FX profiles have a frozen exchange rate baked into `point_value_per_lot`
**Where:** `risk/compounding.py`. Failing: `USDJPY, GBPJPY, EURJPY, AUDJPY, CADJPY, USDCHF, GBPCHF,
USDCAD, GBPCAD, EURGBP, EURAUD, GBPAUD, GBPNZD`.

Decoded, each one hardcodes a rate:

| Profile | `pv/lot` | implied pip value | frozen rate |
|---|---|---|---|
| USDJPY | 0.67 | $6.70 | USDJPY ≈ **149** |
| GBPJPY/EURJPY/AUDJPY/CADJPY | 6.7 | $6.70 | JPY ≈ **149** |
| USDCHF / GBPCHF | 1.15 | $11.50 | USDCHF ≈ **0.87** |
| USDCAD / GBPCAD | 0.735 | $7.35 | USDCAD ≈ **1.36** |
| EURGBP | 1.27 | $12.70 | GBPUSD ≈ **1.27** |
| EURAUD / GBPAUD | 0.65 | $6.50 | AUDUSD ≈ **0.65** |
| GBPNZD | 0.6 | $6.00 | NZDUSD ≈ **0.60** |

**Cost:** sizing error equal to the drift in that rate since the table was written — unbounded and
growing. USDJPY at 158 against a frozen 149 is a **6% under-size** on every JPY trade, silently.
**Fix:** set `point_value_per_lot = point_size × contract_size` for all thirteen (the correct
quote-currency value), and compute the account-currency conversion at sizing time:
`value_per_unit_move = contract_size / quote_to_account_rate`, sourced from MT5's
`symbol_info(quote_pair).bid` with the profile as the fallback. Cache per run under
`freeze_symbol_info()` so a run stays deterministic.
**Why:** pip value on a cross is *a function of the current rate*. Any constant is wrong the day after
it is written. Freezing per-run (rather than per-bar) keeps backtests reproducible without keeping them
wrong forever — and MT5's `tick_value` already does this correctly when it is reachable, which is why
the defect only shows up in offline/profile-fallback runs.

### C2 🔴 GER40 is internally inconsistent
**Where:** `point_size = 0.1`, `point_value_per_lot = 1.2`, `contract_size = 10` → ratio **12 vs 10**.
The 1.2 appears to bake in EURUSD ≈ 1.20, which is also stale.
**Cost:** a flat **20% sizing error** on DAX whenever the profile is used.
**Fix:** `point_value_per_lot = 1.0`, and convert EUR→USD at sizing time per C1.
**Why:** same reason; the conversion is not a property of the instrument.

### C3 🔴 XRPUSD and DOGUSD lot constraints are ~5,000× off what the broker reports
**Where:** `XRPUSD: lot_min=50.0, lot_step=10.0`; `DOGUSD: lot_min=100.0, lot_step=10.0`.
**Evidence:** the debug run placed **19.22 / 11.52 / 7.68** lots on XRPUSD — not multiples of 10, so MT5
supplied a step of ~0.01. Profile and broker disagree by three orders of magnitude.
**Cost:** with MT5 connected you get one answer; with MT5 offline (or in a CI run) the profile forces
`lot_min = 50`, which either massively over-risks or is rejected. Backtest reproducibility depends on
whether the terminal happened to be running.
**Fix:** re-derive all crypto lot constraints from a live `mt5.symbol_info()` snapshot and commit the
snapshot as the profile. Add a startup reconciliation that logs any profile field differing from MT5 by
more than 1% for every symbol you trade.
**Why:** the profiles exist as an offline fallback; a fallback that disagrees with reality by 5,000×
is not a fallback. The reconciliation check is the only way this stays true as the broker changes specs.

### C4 🟠 XRPUSD via profile cannot express your risk at all
**Where:** with `lot_max = 1000` and `value_per_unit_move = 1.0`, a $0.045 stop caps risk at
`1000 × 0.045 × 1 = $45` — **0.18% of a $25k account**, before any margin cap.
**Fix:** falls out of C3.
**Why:** noting it separately because it means "XRP risks 0.3%" has *two* independent causes (this and
A1), and fixing only the margin cap will not fully resolve it.

### C5 🟠 Symbols with no profile silently refuse to trade
**Where:** `calculate_lot_size:566` — `source == "DEFAULT"` and symbol not in `_SAFE_DEFAULTS` → return
0 lots. Verified affected: `XPDUSD` (palladium), `AUDNZD`, `EURNOK`, and every other symbol not in the
81-entry table.
**Cost:** the refusal is correct, but it is logged at ERROR and never surfaces — so a symbol you added
to a portfolio produces zero trades and looks like "no setups".
**Fix:** record it in the rejection funnel as `no_instrument_profile`, and add a **pre-run validation
pass** that checks every symbol in the request and fails fast with a clear message before burning a
backtest.
**Why:** failing at the start with "XPDUSD has no instrument profile" is worth more than an empty
equity curve.

### C6 🟡 `min_lot_value()` helper hardcodes 0.01
**Where:** `compounding.py:52` `return self.point_value_per_lot * 0.01` — assumes a 0.01 minimum lot
regardless of the profile's own `lot_min`.
**Fix:** `return self.point_value_per_lot * self.lot_min`.
**Why:** the profile already carries `lot_min`; ignoring it makes the helper wrong for every synthetic
(`lot_min` 0.1–1.0) and for XRP/DOGE.

---

## D. Backtest ↔ live parity

You asked that what you backtest is what runs live. These are the places where it currently is not.

### D1 🔴 Live sizing compounds; backtest sizing does not
**Where:** `risk/engine.py:158` states *"Position sizing MUST always use the static `initial_balance`"*
and the backtester honours it. But `services/bot_service.py:748-756` passes
`initial_balance=account_balance` for **personal** accounts — the live, growing balance.
**Cost:** the same strategy compounds live and does not in backtest. Equity curves diverge by
construction; drawdown percentages are not comparable.
**Fix:** add `RiskParams.sizing_basis: "STATIC" | "BALANCE" | "EQUITY"` (default `STATIC`) and use one
resolver in both paths. Record the resolved basis in `params_snapshot`.
**Why:** both behaviours are legitimate — you may well want compounding on a personal account — but the
choice must be yours and it must be the same on both sides. Hardcoding opposite defaults in the two
paths is the worst of both.

### D2 🔴 Twelve trailing/BE parameters reach live but not backtest
**Where:** `api/routes/backtest.py:556-604` builds `merged_risk_config` as a **hand-maintained literal
dict**. `bot_service.py:643-694` builds the live one separately. Present live, **absent in backtest**:

`trail_method_tp1` · `atr_trail_multiplier_tp1..tp5` · `trail_pct` · `trail_activation_rr` ·
`trail_step_pips` · `trail_structure_bars` · `be_on_tp1_hit` · `compounding_enabled`

**Cost:** `trail_activation_rr` falls back to `1.0` in backtest while the schema and live use `1.5`.
Trailing behaviour is different in the two systems — which is exactly the thing you are trying to
verify with a backtest.
**Fix:** delete both hand-written dicts. Serialise `UserConfigV2.risk` with
`dataclasses.asdict()` and pass that. Add a unit test that asserts
`set(backtest_risk_config) == set(live_risk_config) == set(asdict(RiskParams()))`.
**Why:** two hand-maintained whitelists of ~40 keys will drift again the next time a field is added —
this has already happened twice. Generating both from the dataclass makes drift impossible, and the test
makes it a build failure rather than a silent behaviour change.

### D3 🔴 `trail_pct` means 0.5% in one file and 50% in another
**Where:** `config_schema.py:238` `trail_pct: float = 0.5` (documented as a percent);
`position_manager.py:687` `trail_pct = getattr(risk,'trail_pct',0.5) / 100.0` ✅;
`trailing_manager.py:22` `self.trail_pct = config.get("trail_pct", 0.005)` then
`trail_distance = current_price * self.trail_pct` — **no division**. `bot_service.py:690` feeds the
schema value (0.5) straight into that config.
**Cost:** in live trading, `PCT_TRAIL` computes a stop **50% below price** via `TrailingManager` and
0.5% below price via `position_manager` — two live code paths, two answers, whichever fires first wins.
**Fix:** store the value as a fraction internally (`trail_pct_fraction`), convert once at the config
boundary, and delete both defaults inside the managers so a missing key is an error, not a guess.
**Why:** percent-vs-fraction is the most common numeric bug in trading code precisely because both
values look plausible. The fix is to make the ambiguity impossible to express, not to add another
`/100`.

### D4 🔴 Live `RiskEngine` is cached on a 3-field fingerprint and ignores your other edits
**Where:** `bot_service.py:699-706`:
```python
_rf = (risk_config["risk_per_trade_pct"], risk_config.get("tp_count", 3), risk_config.get("min_rr", 3.0))
if self.risk_engine is None or self._risk_engine_fp != _rf:
    self.risk_engine = RiskEngine(risk_config)
```
`MultiTPManager`, `BreakevenManager` and `TrailingManager` read their values **in `__init__`**.
**Cost:** change `tp1_rr`, `tp_splits`, `be_trigger_rr`, `be_buffer_atr_mult`, any `trail_method_*`, any
ATR multiplier, `trail_activation_rr`… and **the live bot keeps trading the old values** until you
happen to change one of the three fingerprinted fields.
**Fix:** fingerprint the whole dict (`hash(json.dumps(risk_config, sort_keys=True, default=str))`).
**Why:** the cache exists for construction cost, which is microseconds; the correctness cost is
unbounded. Hashing the full config keeps the optimisation and removes the failure mode.

### D5 🔴 A hardcoded `risk_pips = 20.0` fallback in live BE and trailing
**Where:** `position_manager.py:487` and `:546` — when the parent trade's `stop_loss` cannot be read.
**Cost:** `pip_size` varies by symbol, so "20 pips" of assumed risk is **$20 on BTCUSD** against a real
$1,200 stop. `current_rr` then reads ~60× too high and **break-even fires on the first tick in profit**,
scratching the trade. On XRPUSD it is $0.002 against a $0.045 stop — 22× too high.
**Fix:** if the original stop cannot be resolved, **do nothing this tick and log a WARNING** — do not
substitute a number.
**Why:** there is no safe default for "how much am I risking". Guessing produces a confident wrong
answer that moves real stops; skipping the tick is recoverable and the next poll will likely have the
data.

### D6 🟠 Live and backtest use different break-even buffer formulas
| | Backtest | Live |
|---|---|---|
| Formula | `max(live_spread, atr_mult × ATR, pips × pip_size)` | `max(atr_mult × ATR, pips × pip_size)` then `max(that, 2.0 × real_spread)` |
| Spread fallback | `position["live_spread"]` (usually `pip_value`) | `2.0 × pip_size` hardcoded |
| Spread multiple | 1× | **2.0× hardcoded** |
**Fix:** one function, `resolve_be_buffer(spread, atr, params)`, called by both. Expose
`be_spread_multiple` as a parameter (default 1.0).
**Why:** break-even placement determines whether a trade scratches or runs. Two formulas means the
backtest cannot predict the live scratch rate — and per the exit analysis, scratching is already the
largest single source of lost expectancy.

### D7 🟠 Live trails without break-even; backtest requires break-even first
**Where:** `risk/engine.py:387` `if trail_method and be_applied:` vs `position_manager.py:532` which
checks only `trail_method != "NONE"`.
**Fix:** `trail_require_be_first: bool = False` in `RiskParams`, honoured by both.
**Why:** this is a real strategy choice (protect first vs. ride first). It should be a setting, not an
accident of which file you are reading.

### D8 🟠 The TP1 sibling break-even cascade ignores `be_on_tp1_hit`
**Where:** `backtester/engine.py:918-941` and `portfolio_engine.py:455-484` move every sibling leg to BE
whenever TP1 hits, unconditionally. `BreakevenManager` honours `be_on_tp1_hit`; this block does not, and
`be_on_tp1_hit` is not in `merged_risk_config` anyway (D2).
**Fix:** gate the block on the resolved `be_mode` from Part 4.
**Why:** with TP1 at 1.5R and median MFE at ~2.0–2.3R, forcing BE at TP1 is the scratch generator
identified in the research doc. It must be switchable, and today it is not switchable at all.

### D9 🟠 Live order volume is rounded up and clamped up
**Where:** `order_manager.py:89-93`:
```python
volume = round(volume / vol_step) * vol_step
volume = max(sym_info.volume_min, min(volume, sym_info.volume_max))
```
**Cost:** the sizer's careful risk calculation is re-rounded at the broker boundary, upward. Backtest
floors (in `multi_tp`), live rounds — so the same signal takes different size.
**Fix:** floor, and reject below `volume_min` rather than clamping up (mirrors A3).
**Why:** the rounding rule must be identical on both sides or every trade differs by up to one lot step.

### D10 🟠 Live `group_id = signal.symbol`; backtest uses a UUID
**Where:** `bot_service.py:727`.
**Cost:** live structurally enforces one group per symbol regardless of `max_positions_per_symbol`, so
raising that setting will change backtest behaviour and not live behaviour.
**Fix:** UUID group ids live too; enforce concurrency through `CircuitBreaker` only.
**Why:** one mechanism for one rule. Encoding a limit in an identifier makes the limit unconfigurable.

---

## E. Config plumbing, dead controls and default drift

### E1 🔴 Three layers hold three different sets of defaults
Measured drift between `Backtester.jsx::DEFAULT_FORM`, `BacktestRequest` (Pydantic) and
`RiskParams` (`config_schema.py`):

| Parameter | Frontend form | **BacktestRequest** | RiskParams |
|---|---|---|---|
| `risk_per_trade_pct` | 0.5 | **1.0** | 0.5 |
| `tp1_rr` | 1.5 | **1.0** | 1.5 |
| `tp_splits` | `'50,30,20'` | **`'30,25,20,15,10'`** | `[50,30,20]` |
| `be_trigger_rr` | 1.5 | **1.0** | 1.5 |
| `be_buffer_pips` | 0.0 | **2.0** | 0.0 |
| `max_risk_hard_cap_pct` | 2.0 | **3.0** | 2.0 |
| `trail_method_tp4` | `NONE` | **`ATR_TRAIL`** | `ATR_TRAIL` |
| `trail_method_tp5` | `NONE` | **`STRUCTURE_TRAIL`** | `STRUCTURE_TRAIL` |

**Cost:** the request model's defaults apply whenever a field is not sent — replaying a saved run,
calling the API directly, or any future client. `be_buffer_pips = 2.0` is precisely the value your own
parameter audit called *"the probable source of the phantom profitability"*: it can place the
break-even stop above the market, where the engine treats it as a gap and fills it as unearned profit.
**Fix:** make every `BacktestRequest` field `| None = None` and resolve unset fields from
`RiskParams()` server-side. Generate `DEFAULT_FORM` from `GET /api/config/parameter_schema` at app
start instead of hardcoding it in the bundle.
**Why:** one source of truth is the only structure that cannot drift. `None` for "not specified" also
finally distinguishes *"the user chose 0"* from *"the user said nothing"* — the same distinction that
had to be retro-fitted to the cost fields for exactly this reason.

### E2 🔴 `**req.risk_config` silently overrides every typed field
**Where:** `api/routes/backtest.py:603` — the untyped dict is spread **last** into `merged_risk_config`.
**Cost:** an unvalidated blob can override `risk_per_trade_pct`, `max_risk_hard_cap_pct`, anything. No
type checking, no range checking, and a typo'd key is accepted silently.
**Fix:** remove the untyped channel. Every parameter gets a typed field; unknown keys are rejected with
`model_config = ConfigDict(extra="forbid")`.
**Why:** `extra="forbid"` is what turns "I set trail_activation_rr and nothing happened" into a 422
error naming the field. Right now Pydantic's default silently discards unknown keys — which is why
`trail_method_tp1` is sent by the frontend and vanishes (E4).

### E3 🔴 Three UI controls are wired to nothing
Verified by grep across `backend/risk`, `backend/backtester`, `backend/services`:

| Control | UI location | Backend readers | What the UI claims |
|---|---|---|---|
| `max_account_leverage` | `Settings/Risk.jsx:182` | **0** | *"Caps total notional against equity. Blocks the 100×+ sizes MT5 rejects with retcode 10019."* |
| `min_sl_pips` (global) | `Settings/Risk.jsx:181` | **0** | *"Global stop floor… Set 0 to disable."* |
| `manual_bias` | `Backtester.jsx` | **0** | directional override |

**Cost:** you configure a leverage cap and a stop floor, and neither exists. The behaviour the leverage
hint describes is performed by the hardcoded 30% constant instead (A1). This is the concrete form of
"stop deciding my risk for me".
**Fix:** wire `max_account_leverage` per A2 and the global `min_sl_pips` as an additional term in
`minimum_stop_distance()`. Either implement `manual_bias` or remove the control.
**Why:** a control that does nothing is worse than a missing one — it consumes the trust you would
otherwise spend checking the real cause.

### E4 🟠 `trail_method_tp1` is sent by the frontend and discarded
**Where:** `Backtester.jsx::DEFAULT_FORM` includes `trail_method_tp1: 'NONE'`. `BacktestRequest` has no
such field, so Pydantic drops it. `multi_tp.py:70` hardcodes `trail_methods[0] = None` regardless.
**Cost:** single-TP mode can never trail — the exact scenario in your request.
**Fix:** covered by Part 4.3.
**Why:** listed here because it is *also* a plumbing failure, not only a feature gap: even after
`multi_tp` is fixed, the value would not arrive.

### E5 🟠 `max_positions_per_symbol` exists in the request model but not in the Backtester form
**Where:** `BacktestRequest.max_positions_per_symbol: int = 1`; the Backtester page never sends it. The
live Settings page *does* expose it.
**Cost:** every backtest runs at 1, unchangeable from the backtest UI — the dominant frequency ceiling
(§1.3) is not adjustable where you would notice it.
**Fix:** add the field to the Backtester form; falls out of E1 if the form is schema-generated.
**Why:** any parameter that gates trade count must be visible on the screen where trade count is
measured.

### E6 🟠 The live Settings page and the Backtester page expose different parameter sets
**Present in `Settings/Risk.jsx`, absent from `Backtester.jsx`:** `trail_activation_rr`,
`trail_step_pips`, `trail_structure_bars`, `trail_pct`, `max_positions_per_symbol`, prop-firm drawdown
type/limits.
**Present in `Backtester.jsx`, absent from `Settings/Risk.jsx`:** simulation costs, `simulate_wicks`,
`candle_count`.
**Fix:** one `<RiskParameterForm>` component driven by the parameter schema, rendered on both pages;
simulation-only fields flagged `backtest_only` in the schema.
**Why:** you cannot configure live and backtest identically today even if you want to. Two hand-built
forms guarantee that stays true.

### E7 🟠 Prop-firm position limits are hardcoded
**Where:** `prop_firm_validator.py:371-375` — `if sym_open >= 5:` and `if self.open_positions_count >= 13:`,
both log-only. `:377` — `max_lot_sizes.get(symbol, 999.0)`.
**Cost:** `5` and `13` are one firm's rules presented as universal. `999.0` as the default lot cap means
"no cap" expressed as a magic number.
**Fix:** `PropFirmParams.max_positions_per_symbol`, `.max_total_positions`, `.default_max_lot`
(`None` = uncapped). Keep them warn-only if that is the intent, but make the numbers yours.
**Why:** you trade more than one firm. Hardcoding one firm's limits means the validator is either wrong
or inert everywhere else.

### E8 🟠 Minimum-trading-day credit hardcodes 0.5%
**Where:** `prop_firm_validator.py:203` `if self.daily_profit >= (self.initial_balance * 0.005): active_trading_days += 1`.
**Cost:** firms define a "trading day" differently — some count any closed trade, some any position
opened, some a profit threshold. A 0.5% threshold under-counts your progress toward `min_trading_days`.
**Fix:** `PropFirmParams.trading_day_rule: "ANY_TRADE" | "ANY_CLOSED" | "PROFIT_PCT"` plus
`trading_day_profit_pct`.
**Why:** this figure decides whether a passed challenge is recognised. It has to match the firm's
actual wording.

### E9 🟡 Crypto cost heuristics hardcoded as percentages of price
**Where:** `broker_costs.py:228, 231` — `CRYPTO_SPREAD_PCT_OF_PRICE = 0.0006`,
`CRYPTO_SLIPPAGE_PCT_OF_PRICE = 0.0003`; `:552` `MAX_DAILY_SWAP_PCT_OF_NOTIONAL = 0.5`;
`:69` `_COMMISSION_SANITY_MAX = 60.0`.
**Fix:** move into an editable per-asset-class cost table exposed in Settings, with the current values
as defaults.
**Why:** these are reasonable estimates, not facts. They should be visible next to the results they
shape, with provenance shown (already supported — `sources` is in the payload, just not rendered).

### E10 🟡 No validation that `tp_splits` sums to 100
**Where:** `multi_tp.py` normalises by `sum(splits)`, so `'50,30,20,10'` silently becomes 45.5/27.3/18.2/9.1.
The UI hint says *"must sum to 100"* but nothing enforces it.
**Fix:** validate server-side and reject with a clear message; show the normalised result live in the UI.
**Why:** silent renormalisation means the split you read in the report is not the split you typed.

---

## F. Exit management

### F1 🔴 `original_sl` key does not exist (full detail in §1.4)
**Fix:** `position.get("original_sl") or position.get("initial_stop_loss") or current_sl`, **and** write
`original_sl` explicitly in `_create_position` in both engines so the two names converge.
**Why:** fixing only the reader leaves the next author with two names for one concept. Writing both and
reading both is the safe migration; a follow-up commit can drop the alias.

### F2 🔴 `trail_method_tp1` cannot exist → single-TP mode never trails (Part 4)
### F3 🟠 Trailing is gated behind `be_applied` in backtest only (D7)
### F4 🟠 BE buffer formulas differ (D6)
### F5 🟠 TP1 sibling cascade is unconditional (D8)

### F6 🟠 `min_rr` tests only the last TP, so it measures nothing
**Where:** `risk/engine.py:295` — `last_tp_rr = |last_tp − entry| / risk` compared to `min_rr`.
**Cost:** with `tp3_rr = 5` and `min_rr = 3` the check passes on every signal regardless of volume
allocation. Your observation is exactly right: 50/30/20 at 1.5/3/5 has a **blended RR of 2.65**, and at
the measured hit rates an **expected RR of ~0.62** — but the gate reports 5.
**Fix:** compute and gate on `blended_rr = Σ(volume_pct_i × rr_i)`. Show `blended_rr`,
`blended_rr_after_be` and `expected_rr` (using historical per-level hit rates) in the form, in the
report and on the live signal card. Keep `last_tp_rr` as a displayed figure, not as the gate.
**Why:** the number that determines whether the system makes money is the volume-weighted one. Gating on
the furthest target rewards adding a distant TP that never fills.

---

## G. Strategy engines

### G1 🔴 APA `AWAIT_RETEST` has no timeout and the daily reset protects the deadlock
**Where:** `strategy_apa/engine.py:190-212`. The reset is skipped when
`bos_confirmed and status in (AWAIT_RETEST, AWAIT_CONFIRMATION)` — the deadlock state — while
`AWAIT_BOS`, a legitimately in-flight setup, is wiped at every UTC midnight.
**Evidence:** APA BTCUSD and EURUSD stop trading after January; XAUUSD after April; the run tail shows
the state machine parked in `BOS confirmed … Awaiting retest` on all three.
**Fix:** replace the calendar reset with an explicit per-state staleness budget —
`pattern_max_age_bars`, `bos_max_age_bars`, `retest_max_age_bars` — expiring the setup when exceeded and
logging `setup_expired_<state>`. Remove the midnight wipe entirely.
**Why:** a UTC-midnight boundary is meaningless to an H4 structural pattern, and the current
"protect committed setups" rule inverted the intent — it protects the one state that can never
self-resolve. Bar-count ageing is timeframe-relative and testable.

### G2 🔴 APA tracks one pattern per symbol
**Where:** the `state[symbol]` dict holds a single `pattern`.
**Cost:** while any setup is pending, no new pattern is scanned. Combined with G1 this is a permanent
stop.
**Fix:** `state[symbol]["candidates"]` as a bounded list (`max_concurrent_patterns`, default 3), each
with its own state and age; emit on whichever confirms first.
**Why:** H&S patterns overlap in real markets. A single slot means the first one to appear suppresses
every better one behind it.

### G3 🔴 APA's "body close" test is not a body close
**Where:** `engine.py:250-258` — `min(latest["open"], latest["close"]) < neckline` for bearish.
**Cost:** a wick that dips below the neckline passes, which admits precisely the liquidity sweeps the
strategy exists to avoid. Open finding from `doc_conformance_audit §1.2-A`.
**Fix:** `latest["close"] < neckline` (bearish) / `latest["close"] > neckline` (bullish).
**Why:** `min(open, close)` is the body's *lower edge*; a candle that opens above and closes above but
wicks through still has `min(open,close)` above the neckline — no. The real failure is the opposite: a
candle opening below the neckline and closing back above it passes the test. Only the close carries the
"structure broke" claim.

### G4 🟠 APA's `is_major` neckline gate destroys ~50% of detected patterns
**Where:** `engine.py:266-277` — requires a `major_fractal_m = 8` swing within `0.5 × ATR`.
**Evidence:** 12 of 24 patterns in the log window, logged as *"NOT a major level — liquidity sweep,
resetting"*.
**Fix:** `neckline_major_atr_tolerance` (default 1.0, was 0.5) and, instead of discarding, feed the
measured distance into the confluence score (the engine already computes `neckline_precision_atr` for
exactly this).
**Why:** you asked that filters clamp rather than block where possible. The precision is already being
measured for scoring — using it to also hard-reject double-counts it, and the 0.5 threshold is an
unjustified constant.

### G5 🔴 VWAP's pullback condition is any red candle
**Where:** `strategy_vwap/engine.py:388` `is_pullback_candle = latest["close"] < latest["open"]`.
**Cost:** a candle accelerating *away* from VWAP qualifies as a pullback *toward* it. VWAP is 56% of the
whole trade corpus, so this shapes the entire book.
**Fix:** require convergence — `abs(close − vwap) < abs(prev_close − vwap)` — plus
`pullback_max_distance_sigma` and `first_pullback_only` (Part 8.4).
**Why:** "pullback" is a claim about distance to the anchor. Candle colour is not that claim; on a
strong trend day most candles are the wrong colour at the wrong place.

### G6 🟠 VWAP hardcodes `confluence_score = 80`
**Where:** `engine.py:355`.
**Cost:** every VWAP signal lands in the same confluence tier, so `get_confluence_scaled_risk` is inert
for VWAP and active for other strategies — an undeclared cross-strategy asymmetry. It also makes
`confluence_stats` in the report meaningless.
**Fix:** the 5-component score in Part 8.4.
**Why:** a constant confluence score is a constant, not a score. Until it varies, no confluence-based
sizing can be evaluated.

### G7 🟠 CRT blocks 254 evaluations on `HTF bias NEUTRAL` and 80 valid setups on session
**Where:** `strategy_three_crt/engine.py:171` and `:183/190`.
**Evidence:** `debug/crt/ndx_logs.txt` — 19 trades from ~900 evaluations.
**Fix:** `bias_neutral_mode: "BLOCK" | "REDUCED_SIZE" | "ALLOW"` (default `REDUCED_SIZE`, applying
`size_modifier`); keep the session gate as a block but count it in the funnel and expose the window.
Add `trigger_grace_bars` (default 2) so a trigger one bar past the HTF close is not discarded.
**Why:** a neutral HTF bias is weaker context, not an invalid setup — the natural response is smaller
size. The session gate stays a block because the A/B evidence supports it (+$986 vs −$24 on gold).

### G8 🟠 HTF-FVG-Flip has no displacement gate
**Where:** `strategies/core/fvg.py` tests only `gap >= atr_multiplier × atr`; displacement is computed
in `strategy_four_htf_fvg_flip/engine.py:121` **only to populate the confluence score**.
**Cost:** the only clearly negative-expectancy engine in the book (−0.088R, 38.2% win rate).
Displacement is what separates an institutional imbalance from three bars with a hole.
**Fix:** admit an FVG only when the middle candle's range ≥ `fvg_displacement_atr_mult` (default 1.5)
× ATR(14) **and** its body ≥ `fvg_displacement_body_pct` (default 0.60) of its range.
**Why:** both numbers are already computable and one is already computed. Making it a gate rather than a
score is the intervention the source method actually specifies.

---

## H. Frontend

### H1 🔴 The rejection funnel is read from a path that is never populated
**Where:** `Backtester.jsx:857` reads `report.rejection_funnel`; the portfolio route puts it at the
response top level and the single-symbol route omits it entirely.
**Fix:** add it to the single-symbol response; read `result.rejection_funnel` with a
`report.rejection_funnel` fallback for old saved runs.
**Why:** this is the diagnostic that answers "why so few trades" and it has never once rendered.

### H2 🟠 Nested parameters render as raw JSON
**Where:** `Backtester.jsx:845-851` — `typeof v === 'object' ? JSON.stringify(v) : String(v)`.
**Cost:** `prop_firm`, `risk_config`, `strategy_params`, `resolved_cost_model` and
`manual_bias_overrides` each collapse to one unreadable line, on all four result surfaces.
**Fix:** the schema-driven `<ParameterReport>` in Part 5.2.
**Why:** `resolved_cost_model` in particular carries the per-field provenance (`USER` / `MT5` /
`ASSET_CLASS_DEFAULT`) that determines whether the run is trustworthy, and it is currently the least
readable thing on the page.

### H3 🟠 `summaryEngine.tradeR` falls back to the mutated stop
**Where:** `frontend/src/utils/summaryEngine.js:228` — `const sl = num(t.stop_loss)`.
**Cost:** grouped backtest trades carry `realized_rr` and `pnl_r`, so the fallback is dormant there —
verified. But live trades and `SUMMARY`-mode saved runs may not, and then R is divided by the
break-even stop, which is the exact division bug that produced +14.87R expectancy on a losing run.
**Fix:** fall back to `t.initial_stop_loss ?? t.original_signal?.stop_loss ?? t.stop_loss`, matching
`analytics/metrics.py` (which is already correct). Emit `initial_stop_loss` at group level so the
fallback has something to find.
**Why:** the backend was fixed and the frontend was not. One of the two will be used to make a decision.

### H4 🟠 `DEFAULT_FORM` is a hardcoded mirror of the backend dataclass
**Where:** `Backtester.jsx:1246-1325`. Its own comment says *"any drift silently overrides the backend"*
— and it has drifted (E1).
**Fix:** generate from `GET /api/config/parameter_schema`.
**Why:** the comment correctly identifies the hazard; the structure guarantees it recurs.

### H5 🟡 `max_lot_sizes` is edited as a raw JSON string
**Where:** `Backtester.jsx:1813` — a text input parsed with `JSON.parse`.
**Fix:** a symbol/value row editor.
**Why:** a malformed brace silently reverts the whole prop-firm lot cap to `{}`, i.e. uncapped.

### H6 🟡 Metric cards show no denominator or sample size
**Where:** `Backtester.jsx:890-898`.
**Fix:** every derived figure gets a hover showing formula, inputs and `n`.
**Why:** in a system where the numbers were demonstrably wrong, a figure you cannot audit is a figure
you cannot use.

---

## I. Reporting and diagnostics

### I1 🔴 `rejection_funnel` missing from the single-symbol response (§1.2, H1)
### I2 🟠 `run_logs` truncated to the last 100 entries
**Where:** `backtest.py:668` `getattr(engine,'run_logs',[])[-100:]`.
**Cost:** every `MARGIN CEILING TRIGGERED`, `HARD CAP TRIGGERED` and `SL floored` warning is discarded.
**Fix:** keep all WARNING/ERROR, sample INFO, cap at 2,000.
**Why:** the warnings that explain the run are exactly the ones being dropped.
### I3 🟠 No per-trade sizing diagnostics
**Fix:** emit `requested_risk_pct`, `confluence_scaled_pct`, `dd_scaled_pct`, `pre_cap_lots`,
`post_hardcap_lots`, `post_margin_lots`, `final_lots`, `realised_risk_pct`, `binding_constraint`.
**Why:** one column (`binding_constraint`) would have identified the crypto margin cap immediately.
### I4 🟠 No blocked-signal record
**Fix:** retain up to 500 rejected signals with timestamp, geometry and gate name.
**Why:** "was there a setup that day" must be answerable without re-running.

---

# PART 12 — Engineering rules these fixes must satisfy

Stated explicitly, because most of Part 11 exists because one of them was broken.

**1. No hardcoded risk value. Ever.**
If a number changes the size of a position, the placement of a stop, or whether a trade is taken, it is
a parameter with a default in `RiskParams` or a strategy `Params` class — never a module constant,
never a `getattr(x, 'k', <number>)` fallback, never a literal in a formula. The audit found 23 such
values. Enforcement: a CI check that greps `risk/`, `backtester/` and `services/` for numeric literals
outside the params modules, with an explicit allow-list.

**2. Defaults live in exactly one place.**
`RiskParams` and the strategy `Params` dataclasses are the sole source. The API request model uses
`| None = None` and resolves unset fields server-side. The frontend fetches the schema. Enforced by a
test asserting all three key sets are identical.

**3. Every distance carries its unit.**
`{value, unit}` with `unit ∈ {PIPS, POINTS, PRICE, ATR, PCT_OF_PRICE, R}`, one resolver, and the
resolved price distance shown next to the input.

**4. A clamp must be visible; a block must be named.**
Any code path that reduces size records `binding_constraint` on the trade. Any path that rejects a
signal records a named reason in the funnel. No silent path exists between "signal emitted" and
"trade opened".

**5. One implementation per rule.**
Break-even placement, trailing calculation, minimum stop distance, lot rounding and cost resolution each
have exactly one function, called by backtest and live alike. Where backtest and live must differ, the
difference is a parameter, not a second implementation.

**6. Simple beats clever.**
The sizing formula is `lots = (balance × risk_pct/100) / (sl_distance_incl_costs × value_per_unit_move)`,
floored to the lot step. Guards may reject; guards may not quietly rewrite the answer. If a guard
would leave a position too small to matter, it rejects and says so.

---

# PART 13 — Revised execution sequence

| Phase | Contents | Defects closed | Gate to proceed |
|---|---|---|---|
| **0 — See it** | I1–I4, H1 | 5 | Funnel renders; APA's missing trades have named causes |
| **1 — Units** | B1–B8, C1–C6 | 14 | Profile-consistency test passes for all 81 profiles; `12 pips` resolves sanely on every symbol you trade |
| **2 — Sizing truth** | A1–A5, A9, A10, D9 | 9 | Realised risk % histogram centres on your target on **every** asset class, crypto included |
| **3 — One config** | E1–E10, D2, D4 | 14 | Key-set equality test green; no `extra` keys accepted; live config edits apply immediately |
| **4 — Parity** | D1, D3, D5–D8, A6, F1–F5 | 11 | Same signal → same lots, same stop, same exits in both engines (assert on a 10-trade fixture) |
| **5 — Exits** | F6 + Part 4 (RR triggers, single-TP mode) | 2 | `blended_rr` visible pre-run; BE scratch rate down |
| **6 — Strategies** | G1–G8 | 8 | Trade counts rise; expectancy holds |
| **7 — UI** | H2–H6 + Part 5 panels | 5 | Parameter report + 5 diagnostic panels shipped |

**Total: 65 distinct defects across 70 register entries. At least 24 of them are hardcoded numeric values that override, or silently replace, a setting you control.**

Phases 0–4 are the "stop creating bugs" work: they change no strategy behaviour, only make the system do
what the configuration says. **Do not tune anything until Phase 4 is green** — every threshold tuned
before then is tuned against numbers that are still wrong.

---

# PART 14 — Per-symbol-per-strategy configuration (your Phase 7 requirement)

Recovered from `implementation/phase7.txt` (3,108 bytes — my earlier claim that file was empty was
wrong; corrected in Part 6). This is your own dictated spec, restated precisely, then broken into an
implementable design. It supersedes and completes the `InstrumentSlot` idea already flagged as
not-started in `implementation_plan.md` Phase 7 and Part 6 item 13.

## 14.1 What you asked for, verbatim intent

- A symbol can be selected **more than once**, each instance running a **different strategy** —
  e.g. USDCHF+VWAP and USDCHF+APA active simultaneously, in both portfolio backtesting and live trading.
- Each such (symbol, strategy) selection carries **its own configuration**, not a shared one.
- **§3.3's per-strategy config pattern was only ever wired up for VWAP** (`max_trades_per_day`,
  `max_losses_per_day`) — every other strategy still reads global fields only. You want the same
  autonomy extended to **every** strategy, and scoped to **(symbol, strategy)**, not just strategy.
- **`max_positions_per_symbol` already exists for backtesting but is not enforced in live trading** —
  named explicitly as a gap.
- A **global vs. per-slot split**, stated explicitly:

  | Stays GLOBAL | Becomes PER-SYMBOL-PER-STRATEGY |
  |---|---|
  | `max_daily_drawdown_pct` | `risk_per_trade_pct` |
  | `max_weekly_drawdown_pct` | `max_trades_per_day` |
  | `max_concurrent_positions` (overall) | `max_positions_per_symbol` |
  | TP/RR grid (`tp1_rr`..`tp5_rr`) | `max_losses_per_day` **(new field — currently VWAP-only, needs to exist for every strategy)** |
  | `tp_splits` / volume split | |

- A **validation rule**: configuring a per-slot `max_positions_per_symbol` that would push the
  aggregate over the global `max_concurrent_positions` must error — the same pattern already used for
  "per-trade risk % higher than the hard risk cap."
- **A caching problem, reported directly**: *"the backend caches... not efficient... persists more than
  it should, and sometimes it blocks trades."* No specific cache named — needs investigation, not a
  guessed fix. Candidates already known from Part 11: `[D4]` the live `RiskEngine` 3-field fingerprint
  cache, the MT5 symbol-info 60-second TTL cache (`position_sizer.py`), and the per-symbol cooldown /
  `_last_signal_time` staleness noted as open in `implementation_plan.md §5.2`.

## 14.2 Design — `InstrumentSlot`

Replaces the current model where `InstrumentSettings` is keyed by symbol alone.

```python
@dataclass
class InstrumentSlot:
    slot_id: str                    # stable UUID — NOT derived from symbol, so two slots can share one
    symbol: str
    strategy_id: str
    enabled: bool = True

    # Per-slot overrides — None = inherit the global RiskParams value
    risk_per_trade_pct: float | None = None
    max_trades_per_day: int | None = None
    max_positions_per_symbol: int | None = None
    max_losses_per_day: int | None = None
    strategy_params_override: dict[str, Any] = field(default_factory=dict)

    def resolve(self, global_risk: "RiskParams") -> "ResolvedSlotConfig":
        ...  # per-field fallback to global_risk
```

`UserConfigV2.instrument_settings: list[InstrumentSettings]` (today: one entry per symbol) becomes
`UserConfigV2.instrument_slots: list[InstrumentSlot]` (one entry per symbol×strategy pair, symbol
repeatable). A migration reads old single-symbol configs into one slot per symbol at the previously
active strategy, so existing saved configs and running bots do not lose settings on upgrade.

## 14.3 Backend changes

| File | Change |
|---|---|
| `core/config_schema.py` | Add `InstrumentSlot` dataclass (14.2). Add `max_losses_per_day` as a first-class field on every strategy `Params` class (today only `VWAPParams`), so a global default exists for the slot override to inherit from. |
| `risk/circuit_breaker.py` | `open_positions_by_symbol` becomes `open_positions_by_slot: dict[slot_id, int]`. `check_symbol` takes `slot_id`, resolves the slot's `max_positions_per_symbol` (falling back to global), and enforces it **identically in backtest and live** — closes the live gap named in 14.1. |
| `risk/engine.py` | `evaluate_signal` resolves `risk_per_trade_pct` from the slot first, global second. Losses-per-day tracked per slot, mirroring `VWAPEngine.notify_outcome`'s existing `losses_today` pattern, generalised so every strategy gets it for free rather than reimplementing it. |
| `services/bot_service.py` | Iterate `instrument_slots` instead of `instrument_settings`; the same symbol can now dispatch to two engine instances (one per strategy) concurrently — verify no shared mutable state leaks between them (each strategy engine already keeps its own `self.state[symbol]` dict, so this should be safe, but must be tested explicitly under this new two-engines-one-symbol condition). |
| `backtester/portfolio_engine.py` | Same iteration change; `PortfolioSymbolConfig` gains `slot_id` so two rows can share a `symbol`. |
| `api/routes/config.py` (or wherever slots are validated) | Add the cross-validation rule: reject a slot's `max_positions_per_symbol` if `Σ(slot.max_positions_per_symbol or slot.max_trades_per_day-implied-max)` across all enabled slots would exceed global `max_concurrent_positions`, mirroring the existing `risk_per_trade_pct` vs. `max_risk_hard_cap_pct` validation. |
| — | **Cache audit (investigation, not a guessed fix):** trace every cache with a TTL or fingerprint in the live path — `position_sizer.py`'s 60s symbol-info cache, `[D4]`'s `RiskEngine` fingerprint cache, `CircuitBreaker.last_trade_closed_time` cooldown, and any Redis-backed state in `bot_service.py` — and determine which one is the one you observed blocking trades. Fix that one specifically once identified; do not blanket-shorten every TTL, which would just move the cost to MT5 call volume. |

## 14.4 Frontend changes

- `Settings/Strategy.jsx` and the Backtester's portfolio-symbol picker both move from "pick a symbol,
  pick one strategy for it" to "add a slot: symbol + strategy + optional per-slot overrides", with
  repeat-symbol explicitly allowed and visually distinguished (e.g. `USDCHF (VWAP)` / `USDCHF (APA)` as
  two rows).
- Per-slot override fields render only for the four slot-scoped parameters (`risk_per_trade_pct`,
  `max_trades_per_day`, `max_positions_per_symbol`, `max_losses_per_day`); everything else stays in the
  global Risk settings panel, unchanged.
- The validation error from 14.3 surfaces inline on the offending slot, the same visual pattern as the
  existing hard-cap-vs-risk-pct warning.

## 14.5 Sequencing

This is substantial — a new config model, a migration, and a live-engine dispatch change — and depends
on Phase 3 (one config, no plumbing drift) being done first, since a second config surface built on top
of today's three-different-defaults problem would just triple the drift instead of fixing it. Sequenced
as **Phase 12** in the task list, after Phase 9's strategy consolidation (fewer engines to make
slot-aware) and before Phase 10/11's data-layer work (which does not interact with this).

---

# PART 15 — Phase 0/1 execution report and audit correction (2026-08-22)

Phase 0 and Phase 1 of the task list have been implemented, tested, and are reflected accurately in
`TASKS.md`. This part records what actually happened, including a significant correction to this
document's own earlier claims — read alongside `TASKS.md`, which has the line-by-line detail.

## 15.1 Phase 0 — shipped

`rejection_funnel` and a new `blocked_signals` list now reach the API response on both backtest routes
(previously silently dropped on single-symbol runs). `run_logs` retention changed from a flat last-100
to a level-filtered 2,000. `Backtester.jsx` reads the correct path with a fallback for old saved runs,
plus a new blocked-signals panel. Most substantially: a new `sizing_diagnostics` block on every trade,
tracing `requested_risk_pct` through confluence scaling, drawdown scaling, the hard-risk-cap guard, the
margin-ceiling guard, and the broker's own `volume_min`/`volume_max` clamp, ending in a
`binding_constraint` field naming exactly which one bound. Smoke-tested against a synthetic XRPUSD
signal and an EURUSD control case — both produced correct, explainable results.

## 15.2 Phase 1 — a correction, not just a completion

Before writing any Phase 1 fix, each of the 8 original unit-conversion claims (§B2–§B8 of Part 11) was
re-verified against the current codebase rather than assumed correct from the original audit pass.
**6 of 8 turned out to be false or substantially overstated:**

- **§B2** (`get_pip_size` "two contradictory definitions"): false. The profile branch resolves first via
  an `if size is None` guard; the hardcoded fallback lists are dead code for any symbol with a profile.
- **§B4** (NY-Retest points/pips): false. The docstring documents pip-conversion as the intended design
  for that field; the one genuinely mis-scaled path is already non-default.
- **§B6/§B7** (`order_manager.py` hardcoded values): not units bugs — both use MT5's own native `point`
  concept correctly and consistently. Real ask (make configurable) implemented anyway; §B6 is now
  `RiskParams.mt5_order_deviation_points`.
- **§B8** (`calculate_pips`): false — it already calls the identical resolver the sizer uses.
- **§C1's originally-planned fix** (rewrite the 13 "mis-priced" FX-cross `point_value_per_lot` values):
  would have been actively harmful. Back-calculating the implied exchange rate from each one shows they
  decode to plausible real rates (GBPJPY→USDJPY≈149.25, EURGBP→GBPUSD≈1.27, etc.) — the defect is
  staleness (no live refresh), not an arithmetic error. Rewriting them to `point_size × contract_size` as
  originally planned would have *introduced* a ~150x error on every JPY cross, not fixed one.
- **§C6** (`min_lot_value`): the method doesn't exist under that name; the real one
  (`get_pip_value_per_mini_lot`) has zero callers and correct logic. Nothing to fix.

**Only §B3 (VWAP's `sl_points` on certain indices) was confirmed real**, and narrower than described —
it affects NAS100/USTEC/NDX and US500/SPX/US2000/GER40/DAX specifically (where the broker tick isn't a
whole index point), not "indices" generally. Fixed directly.

Given that track record, the broader §B1 proposal (an explicit `{value, unit}` type replacing every
distance parameter across the codebase) is **descoped**, not built. It would have been solving a problem
that mostly turned out not to exist, at real complexity cost — exactly what "stop overcomplicating, give
me simple accurate calculations" asked me not to do.

**What was actually built for Phase 1:**
1. `resolve_cross_rate_point_value()` in `position_sizer.py` — the real fix for §C1's staleness: prefers
   a live MT5 quote on the conversion pair, falls back to the static snapshot when MT5 is unreachable.
2. VWAP's `sl_points` fixed at its one confirmed-bad call site, plus the stale docstring that had been
   asserting the now-fixed assumption ("get_pip_size returns 1.0 for index CFDs").
3. `mt5_order_deviation_points` added as a real, editable setting (default unchanged — no live data here
   to justify a different number).
4. A specific, named rejection reason (`no_instrument_profile`, `stop_below_min_viable`, etc.) now
   reaches the funnel instead of a generic "zero_lot_size", reusing the Phase 0 diagnostics.
5. `test_instrument_profiles.py` — whose own first draft made the same currency-convention error the
   original audit did (`ratio == contract_size` as a universal invariant), caught and corrected before
   being relied on. 80 of 81 profiles now pass; GER40 is flagged for your manual review, not guessed at.
6. **XRPUSD/DOGUSD's lot constraints were deliberately NOT changed.** The evidence (observed trade
   volumes contradicting the profile) is real but insufficient to derive a specific correct replacement
   without a live MT5 connection this environment doesn't have. Guessing a new number would repeat the
   exact mistake being corrected elsewhere in this part. Left open, pointing at the reconciliation job
   (task 1.16) as the principled fix.

## 15.3 Why this is reported this openly

The pattern above — six retracted claims against two confirmed ones — is the clearest evidence in this
entire engagement that a fast audit pass is not the same as a verified one. It is reported in full,
including the two placeholder scripts in this session that silently failed to write their changes to
disk before being caught, because the alternative (quietly fixing the true positives and letting the
false ones stand as unexamined "open tasks") would have eventually led to someone — me or you — acting on
a claim that was never true. That failure mode is the one you hired this audit to eliminate.
