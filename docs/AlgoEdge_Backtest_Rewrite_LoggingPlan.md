# AlgoEdge — Backtest Engine Rewrite & Intensive Logging Plan

**Purpose:** Fix the signal-starvation bugs found across the detection/risk/backtest
pipeline, and rebuild the backtest execution path so every decision — pass or reject —
is logged with enough detail to answer "why didn't this bar produce a trade?" without
re-reading code.

**Scope reviewed:** 20 files across `backend/risk/`, `backend/backtester/`,
`backend/strategies/smc/`, `backend/strategies/core/`.

**⚠️ Blocking gap:** `backend/backtester/engine.py` (the actual `BacktestEngine` class —
the main candle-by-candle loop that `optimizer.py` and `runner.py` both call) was never
provided. It's the single most important file for this plan, since it's where the
per-candle loop and per-signal logging hooks need to live. Everything below assumes its
existence and describes what needs to be added to it, but **it needs to be reviewed
directly before implementation starts** — the plan for it is written from inference,
not from reading its actual code.

---

## 1. Logging Philosophy

Right now, rejection reasons are actively *suppressed* during backtesting:

```python
# signals.py — SignalGenerator.generate()
if not passed:
    is_bt = context.get("is_backtesting", False)
    if not is_bt:
        bot_service.log_system_event(f"Signal rejected: {reason}", "DEBUG", "SIGNAL")
    return None
```

This is backwards. Live trading should log sparingly (rate-limited, since it runs
continuously); backtesting is exactly where you want a full, structured audit trail,
because you're running it once over a finite window specifically to understand
behavior. The rewrite inverts this.

### Standard applied to every file below

1. **Log at every gate, not just on final rejection.** Each gate function should log
   its own pass/fail and the values it compared, not just bubble up one string.
2. **Structured, not prose.** Every log call emits a dict/JSON payload with fixed keys
   (`stage`, `symbol`, `timeframe`, `bar_time`, `result`, `reason`, `values`), never a
   free-text sentence as the only output. Prose reasons are fine *as one field inside*
   the structured payload for human readability.
3. **Counters, not just lines.** A 4-month M5 backtest can throw millions of log lines;
   nobody reads them all. Every rejection also increments an in-memory `Counter` keyed
   by `(module, reason)`. The backtest report ends with a **rejection funnel** — a
   ranked table of "how many candles/signals died at each gate" — so the first thing
   you see after a run is *where* the bottleneck is, not a wall of text.
4. **Three log levels, used consistently:**
   - `TRACE` (opt-in, default off): every gate evaluated on every candle — this is the
     "for every step" firehose. Off by default because it's enormous; toggleable per
     backtest run.
   - `DEBUG`: every signal that reached candidate stage (passed structure/POI check)
     and its full context dict, pass or fail.
   - `INFO`: every trade actually opened/closed, every circuit breaker trip, every
     backtest run start/end summary.
5. **No silent `except: pass`.** Several files currently swallow exceptions silently
   (e.g. `runner.py`'s websocket broadcast). Logging additions should log the
   exception at `WARNING` even when the code deliberately continues.
6. **Every ATR-multiplier / repurposed-parameter calculation logs its inputs.** Given
   how much damage the repurposed-pip-as-ATR-multiplier bug did silently, every place
   that does `threshold = param_value * atr` must log `param_value`, `atr`, and the
   resulting `threshold` at TRACE level, so a value like "3× ATR required" is visible
   in the log instead of only in a code comment.

### Standard log record shape

```python
{
    "run_id": "...",
    "stage": "market_structure.bos_check",
    "symbol": "Volatility 75 Index",
    "timeframe": "H4",
    "bar_time": 1783670167,
    "result": "REJECTED",              # or "PASSED" / "TRIGGERED"
    "reason": "hl_not_confirmed",
    "values": {"curr_sh": 37401.2, "prev_sh": 37398.9, "curr_sl": 37201.0, "prev_sl": 37210.5},
}
```

---

## 2. Global fix applied once, referenced everywhere

**The repurposed-parameter pattern** appears in exactly four places
(confirmed via `grep -rn "repurpos"`): `breakeven_manager.py`, `fvg.py`,
`liquidity.py`, `signals.py` (spread gate). All four take a parameter *named and
documented* as a pip distance and silently treat it as an ATR multiplier, while
`params.py` still ships pip-scale defaults (3.0, 5.0, 10.0, 50.0) for them.

**Fix, applied identically in all four files:**
- Rename the config keys to reflect what they actually do:
  `fvg_min_gap_pips` → `fvg_min_gap_atr_mult`, `liq_sweep_min_pips` →
  `liq_sweep_min_atr_mult`, `be_buffer_pips` → `be_buffer_atr_mult`,
  `max_spread_pips` → `max_spread_atr_mult`. Keep the old names as deprecated aliases
  for one release, logged as `WARNING` when read, so nothing silently breaks.
- Re-derive sane ATR-scale defaults (roughly 0.1–0.3 for gap/spread/BE-buffer
  multipliers, not 3–50). This needs a short calibration pass: run the corrected
  detectors over historical data and check FVG/sweep detection rates land in a
  reasonable range (dozens per month, not zero, not hundreds).
- Update `InstrumentProfile` overrides in `compounding.py`
  (`liq_sweep_min_pips_override=10.0`, `fvg_min_gap_pips_override=5.0` on
  Volatility 75 Index, etc.) to match the corrected scale — these overrides are
  currently making the bug *worse* specifically for the synthetic-index backtests
  you've been running.
- Every one of these four sites logs `{param_name, raw_value, atr, computed_threshold}`
  at TRACE so the actual number in force is always inspectable per bar.

---

## 3. File-by-file plan

### `backend/strategies/smc/market_structure.py` — **Priority 1**

**Issue:** BOS *and* ChoCH are both gated behind `hh and hl` (or `lh and ll`)
being true simultaneously. A genuine ChoCH structurally can't satisfy this at the
moment it happens — the newly-forming low swing is still part of the old trend's
sequence. This silently prevents `trend`/`htf_bias` from updating in the majority of
real reversals, which cascades into Gate 1 of `signals.py` rejecting almost
everything.

**Fix:**
- Decouple the "did price close beyond the relevant swing point" check from the
  `hh`/`hl` classification. New order of operations per candle:
  1. Compute `latest_close` vs `prev_sh`/`prev_sl` independently of swing pairing.
  2. If `trend != BULLISH` and `latest_close > prev_sh` with the broken-swing-count
     rule → fire ChoCH regardless of what the low-side swing is doing.
  3. If `trend == BULLISH` and `latest_close > prev_sh` → fire continuation BOS,
     again independent of `hl`.
  4. Use `hh`/`hl`/`lh`/`ll` only for labeling/quality metadata (e.g. "this BOS also
     coincided with a fresh HL" as a *bonus* confluence flag), never as a prerequisite
     to run the check at all.
- Add a regression test with a synthetic price series that has a clean textbook
  reversal (LL→LH downtrend, rally through prior high, later HL) and assert ChoCH
  fires at the rally bar, not one full swing cycle later.

**Logging additions:**
- TRACE every bar: current `trend`, `consecutive_bos`, last 2 high/low swing prices,
  and the boolean results of `hh`/`hl`/`lh`/`ll` — this makes the exact failure mode
  above visible in logs today, before the fix even lands.
- INFO on every BOS/ChoCH firing: direction, level broken, `consecutive_bos` count,
  whether `trend_confirmed` flipped.
- WARNING if `trend` stays unchanged for more than N candles (configurable, e.g. 200)
  — flags a stalled structure detector during a backtest run.

---

### `backend/strategies/smc/fvg.py` — **Priority 1**

**Issue:** `min_gap_pips` repurposed as ATR multiplier; default (3.0, or 5.0 for
Volatility 75 via `InstrumentProfile` override) means a valid FVG needs a 3–5× ATR
gap — essentially never occurs.

**Fix:** covered by the global fix in §2. Additionally:
- Guard against `atr == 0` more defensively — currently falls back to the last
  candle's own range when `tr_list` is empty, which is reasonable, but log when this
  fallback path is taken since it changes behavior on thin data.
- The FVG-fill/shrink logic (`fvg["top"] = latest["low"]` etc.) never logs when an
  active FVG is invalidated/removed — add it, since silently losing FVGs mid-backtest
  makes POI availability hard to audit.

**Logging additions:**
- TRACE every bar: `atr`, `atr_multiplier`, `min_required_gap`, actual gap size if a
  3-candle pattern was checked, PASS/FAIL.
- DEBUG on every new FVG created: type, top/bottom/ce, originating bar index.
- DEBUG on every FVG removed (filled): which FVG, what filled it.

---

### `backend/strategies/core/liquidity.py` — **Priority 1**

**Issue:** Same repurposing pattern (`sweep_min_pips` as ATR multiplier); defaults of
5–50× ATR make sweep detection nearly impossible, especially for gold (50×) and
synthetics (10×).

**Fix:** covered by §2. Additionally:
- The IDM (inducement) detection logic only checks pools already in `ssl_pools`/
  `bsl_pools` against retracement bands — if the relevant pool was pruned by the
  "keep last 10" trimming before the retracement check runs, IDM silently returns
  nothing. Add a log when a pool is dropped due to the size cap, and consider
  increasing the cap or pruning by age instead of raw count.
- `pool["swept"] = True` permanently marks a pool as used — confirm this is desired
  (a pool can only ever be swept once) and log when a sweep is *skipped* because the
  pool was already marked swept, so it's distinguishable from "no candidate pools at
  all."

**Logging additions:**
- TRACE every bar: `atr`, `atr_multiplier`, `min_sweep_depth`, and for every
  unswept pool in range: distance from price to trigger the sweep.
- INFO on every sweep detected: type (BSL/SSL), level, wick depth achieved.
- DEBUG on every new BSL/SSL pool added and every pool pruned by the size cap.

---

### `backend/strategies/smc/order_blocks.py` — **Priority 2**

**Issues:**
1. `max_touches` is accepted in `__init__` but never referenced in `update()` — touch-
   based invalidation from `params.py`'s `ob_max_touch_count` is dead code; only full
   mitigation removes an OB.
2. Only ever inspects a fixed `iloc[-3]`/`iloc[-2]` pair per call — multi-candle bases
   or impulses are invisible to the detector.

**Fix:**
- Implement the touch-count check: when `ob["touches"] >= self.max_touches`, mark it
  mitigated/invalid, matching the documented "1 = fresh only, 2 = first+second" spec.
- Extend detection to scan a short trailing window (e.g. last `ob_lookback_bars`, which
  is already a defined-but-unused param at 20) for a qualifying base+impulse pair
  instead of only the immediately-preceding two candles, so multi-candle impulses
  aren't missed.

**Logging additions:**
- DEBUG on every new OB created: type, top/bottom, impulse ratio achieved.
- DEBUG on every OB touch: touch count so far vs `max_touches`.
- INFO on every OB invalidated: reason (`mitigated` vs `touch_limit`).

---

### `backend/strategies/smc/asian_range.py` — **Priority 2**

**Issue:** `update()` scans the trailing 100 candles for anything matching the Asian
hour window without filtering by calendar date, so if more than one day's Asian
session falls inside the lookback, their highs/lows get merged into one blended range
instead of isolating the most recent session.

**Fix:**
- Group matching candles by `local_dt.date()` first, then take only the most recent
  complete date's high/low as "the" Asian range. Keep prior days available (e.g. as a
  small history list) for future features, but `self.high`/`self.low` should reflect
  one session only.
- Reduce the lookback window dynamically based on timeframe rather than a fixed 100
  bars, or explicitly document/assert the timeframe this is meant to run on.

**Logging additions:**
- DEBUG whenever the range is (re)mapped: date used, high, low, candle count that
  contributed.
- WARNING if candles from more than one calendar date were found matching the hour
  window in a single `update()` call (this is exactly the bug condition — log it even
  after the fix, as a canary).
- TRACE on `check_sweep()`: current price, direction, range high/low, result.

---

### `backend/strategies/smc/supply_demand.py` — **Priority 2**

**Issue:** `supply_zones`/`demand_zones` grow unbounded — no mitigation/expiry logic
exists at all (unlike `order_blocks.py`, which prunes on mitigation). Old zones from
months earlier remain "active" indefinitely, both a correctness bug (`in_sd_zone` can
trigger against ancient, irrelevant levels) and a performance/memory issue over a long
backtest.

**Fix:**
- Add a mitigation check mirroring `order_blocks.py`: a demand zone is invalidated
  when price closes below its bottom; a supply zone when price closes above its top.
- Add an age-based expiry as a backstop (e.g. drop zones older than
  `ob_lookback_bars`-equivalent), independent of mitigation, so stale untouched zones
  don't linger forever either.

**Logging additions:**
- DEBUG on every new zone created and every zone invalidated (mitigation vs. age
  expiry, distinguished).
- INFO with running zone counts periodically (e.g. every N bars) during long
  backtests, to catch unbounded growth if the fix regresses.

---

### `backend/strategies/smc/ipdm.py` — **Priority 3**

**Issue:** Fallback branch
`self.current_phase = "MANIPULATION" if self.current_phase == "ACCUMULATION" else self.current_phase`
transitions straight to `MANIPULATION` on bars where nothing was actually detected,
just because the prior phase was `ACCUMULATION`. This can mislabel bars as
manipulation without a real sweep+rejection occurring.

**Fix:**
- Replace the guessed fallback with an explicit `"TRANSITIONAL"` or "hold previous
  phase" state, and only assign `MANIPULATION` when `_check_manipulation()` actually
  returns `True`.
- Confirm downstream consumers (confluence scoring, if IPDM feeds it) treat this new
  state sensibly — none of the reviewed gate files currently hard-gate on IPDM phase,
  so this is lower urgency than the structure/FVG/liquidity fixes, but worth
  correcting since it's scored/displayed either way.

**Logging additions:**
- DEBUG every phase transition: from-phase, to-phase, which of the three checks
  (`is_accum`, `manipulation_detected`, `is_expansion_displacement`) fired or fell
  through to the guessed fallback — make the fallback path visible so it can be
  monitored until removed.

---

### `backend/strategies/smc/candlestick.py` — **Priority 4 (no bugs found)**

No structural issues identified on review — pattern detection, tiering, and the
master `detect_confirmation_pattern()` scan look internally consistent.

**Logging additions only:**
- TRACE per candle scanned in `detect_confirmation_pattern()`: which detectors ran,
  which returned a pattern, which pattern won the tier/confidence tiebreak.
- DEBUG on the final selected pattern per signal: name, tier, confidence, entry
  candle index.

---

### `backend/strategies/smc/premium_discount.py` — **Priority 4 (no bugs found)**

Pure Fibonacci math, looks correct.

**Logging additions only:**
- TRACE per calculation: swing_high, swing_low, resulting equilibrium/OTE levels —
  cheap to log, useful when auditing why `in_ote_zone` did or didn't trigger.

---

### `backend/strategies/smc/confluence.py` — **Priority 3**

**No new correctness bugs found on re-review**, but given §2/§3 fixes will change how
often `sweep`, `fresh_ob`, and `fvg_inside_ob` are populated, this file needs no logic
changes — it needs much better visibility into what it's scoring.

**Logging additions:**
- Replace the existing `print(f"DEBUG SCORER: ...")` with a structured `DEBUG` log
  call through the shared logger (this is currently a raw `print`, which won't show up
  in your log files/levels at all — a real gap for a backtest you intend to audit).
- Log the full `breakdown` dict at DEBUG for every scored signal, win or lose the
  `min_signal_score` gate, so it's possible to see e.g. "this signal scored 60/100 and
  was rejected, and would have passed if the sweep component had fired."

---

### `backend/strategies/smc/signals.py` — **Priority 1 (gate logging)**

**Issue recap:** rejection reasons are explicitly *not* logged during backtesting
(`is_bt` guard), which is the opposite of what's needed. Also carries the fourth
repurposed-parameter instance (`max_spread_pips` as ATR multiplier — §2).

**Fix:**
- Remove the `is_bt` suppression entirely; log every gate result unconditionally.
- Break `validate_all()` into per-gate logging rather than one aggregate
  pass/fail — currently it returns on the *first* failing gate, which means you never
  learn whether Gate 8 (confluence score) would also have failed. For diagnostic runs,
  add a non-short-circuiting mode that evaluates *all* gates and reports every failure,
  not just the first — critical for understanding which gate is the actual bottleneck
  when several might all be failing at once.
- Apply the §2 rename to the spread-check parameter.

**Logging additions:**
- INFO/DEBUG per gate (1, 3, 4, 5, 6, 7, 8 — note Gate 2 doesn't exist; confirm
  whether it was deliberately removed or is a leftover numbering gap from a deleted
  liquidity-sweep gate, and either restore or renumber): PASS/FAIL + the exact values
  compared.
- A run-level `Counter` incremented per `(gate_name, reason)` on every rejection,
  surfaced in the final backtest report as the **rejection funnel** (see §4).

---

### Risk module (`backend/risk/`)

#### `engine.py` (RiskEngine) — **Priority 2**

No new bugs found on re-review beyond what was already covered (strict risk
enforcement clamp, TP1-vs-min_rr note from earlier — confirmed non-issue at your
actual `min_rr=1.0` setting).

**Logging additions:**
- INFO on every `evaluate_signal()` call: final decision (`APPROVED`/rejection
  reason), lot size, `requested_risk_dollars` vs `actual_risk_dollars`, last-TP RR.
- DEBUG on every `manage_open_position()` action returned (BE trigger, trail update)
  with before/after SL and the reason.
- This file already logs reasonably well (`logger.warning`/`logger.info` calls exist)
  — the main gap is these aren't structured dicts, just f-strings. Convert to
  structured logging for consistency with the rest of the plan, keep the human-
  readable message as one field.

#### `multi_tp.py` — **Priority 3**

**Issue recap:** `_validate_tp()` raises `ValueError` on a bad TP placement instead of
returning a rejection — this can crash an entire multi-month backtest on one bad
signal.

**Fix:**
- Catch the validation failure inside `calculate_tp_levels()` and return `[]` (already
  handled as "no valid TP levels" upstream in `engine.py`) instead of letting the
  exception propagate. Log at `ERROR` with full context before returning, since a TP
  landing on the wrong side of entry indicates a real upstream direction bug worth
  knowing about, not just silently skipping.
- Fix the default `tp_splits` mismatch: `MultiTPManager`'s internal fallback
  (`[30, 25, 20, 15, 10]`) should match `RiskParams.tp_splits` default
  (`[40, 35, 25]`) or derive from `tp_count` rather than hardcoding a 5-length list.

**Logging additions:**
- DEBUG on every `calculate_tp_levels()` call: input RR multipliers, computed TP
  prices, volume splits pre/post "Dynamic TP Collapse," and whether the remainder
  sweep or smart-clamping paths were triggered (both currently silent).

#### `position_sizer.py` — **Priority 3**

No bugs found; multiple fallback paths (InstrumentProfile → MT5 → hardcoded default)
are reasonable, but currently silent about *which* path was taken.

**Logging additions:**
- DEBUG on every lot calculation: which data source was used (`InstrumentProfile`,
  MT5, or hardcoded default), the resulting `pip_value`/`tick_value`, raw lots before
  clamping, and final lots after clamping+rounding. This matters because the fallback
  hardcoded defaults (`tick_value=1.0`, `contract_size=100000`) are forex-shaped and
  silently wrong for synthetics/gold if `InstrumentProfile` lookup ever fails.

#### `breakeven_manager.py` — **Priority 1 (part of §2 global fix)**

Covered by §2. Additionally log the `buffer = max(live_spread, atr_buffer)` decision —
right now it's invisible which of the two values won.

**Logging additions:**
- DEBUG on every `check_breakeven()` call regardless of trigger: `unrealized_r`,
  which trigger condition fired (RR threshold vs TP1-hit), `live_spread` vs
  `atr_buffer` and which was used, final `new_sl`.

#### `trailing_manager.py` — **Priority 3**

No bugs found; ratchet logic (never move SL backward) looks correct across all 4
methods.

**Logging additions:**
- DEBUG on every `calculate_trailing_sl()` call: method used, candidate `new_sl`
  before the ratchet check, whether the ratchet check accepted or rejected it.
- Specifically flag `STRUCTURE_TRAIL` calls that return `None` due to no
  `swing_points` provided — silent no-ops here should be visible, since it means the
  position simply never trails under that method.

#### `circuit_breaker.py` — **Priority 2**

**Issue recap:** dead code in `_check_daily_reset` (`if "Daily" in self.pause_reason`
after `pause_reason` was already cleared by `manual_resume()`); otherwise logic is
sound.

**Fix:**
- Remove the dead branch, or restructure so reset and resume are one atomic step
  instead of two overlapping checks.

**Logging additions:**
- INFO on every state-changing event: position opened/closed, daily/weekly reset,
  pause triggered (with which specific check tripped it), manual resume.
- This file already has decent `check_all()` return-string diagnostics — extend those
  into structured `Counter`-backed rejection tracking too, feeding the same funnel as
  the signal gates (a circuit-breaker block is just as much a reason "no trade
  happened" as a failed SMC gate, and belongs in the same report).

#### `compounding.py` — **Priority 4 (only relevant if compounding is enabled)**

No bugs found in the reviewed sections (step table, advance/downgrade state machine,
lot conversion). Not currently in the critical path for your flat-10%-risk backtests.

**Logging additions (only if/when compounding is toggled on):**
- INFO on every `update_state()` step change: step before/after, reason
  (`ADVANCE`/`DOWNGRADE_THRESHOLD`/`DOWNGRADE_LOSS_COUNT`), balance at time of change.
- Note the `InstrumentProfile` overrides here (`liq_sweep_min_pips_override`,
  `fvg_min_gap_pips_override`) need the same §2 rename/rescale, since they feed
  straight into the buggy multiplier logic in `fvg.py`/`liquidity.py`.

---

### Backtester module (`backend/backtester/`)

#### `engine.py` (BacktestEngine) — **Priority 1, but not yet reviewed — see blocker above**

Once provided, this needs:
- A per-candle log record (TRACE) showing symbol, timeframe, candle time, and which
  detectors were invoked that bar.
- A per-signal-attempt log record (DEBUG) showing full context dict at the moment
  `SignalGenerator.generate()` was called, and its result.
- A single run-level summary object collecting every `Counter` from the gate files
  above (signals.py gates, circuit breaker blocks, confluence score misses) into one
  **rejection funnel** table, attached to the backtest report (see §4).
- Explicit try/except around each candle's processing so one bad candle/signal can't
  silently abort the whole run (relates to the `multi_tp.py` `ValueError` issue —
  belt-and-suspenders at the loop level too).

#### `optimizer.py` — **Priority 3**

Currently catches and logs grid-search failures per combination
(`logger.warning(f"Backtest failed for params {param_set}: {e}")`) — reasonable, but:

**Fix:**
- Log the full traceback, not just `{e}` — grid searches run unattended and a bare
  exception message often isn't enough to diagnose after the fact.

**Logging additions:**
- INFO at start: total combinations, keys being swept.
- INFO per combination completed: rank metric value, so progress is visible during a
  long grid search, not just the final sorted list.

#### `report.py` — **Priority 2**

Very thin currently — just wraps `compute_portfolio_stats`.

**Fix / additions:**
- Attach the rejection funnel (from `engine.py`) to the report object so
  "why so few trades" is answered inside every backtest report by default, not just
  when someone manually greps logs.
- Populate `equity_curve`/`drawdown_curve`/`monthly_returns` — currently returned as
  empty structures (`[]`/`{}`) regardless of `stats` content; either they're populated
  elsewhere before this or this is an incomplete implementation worth flagging.

#### `runner.py` — **Priority 3**

**Issue:** websocket broadcast failures are silently swallowed
(`except Exception: pass`) in both `_broadcast_progress` and `_broadcast_notification`.

**Fix:**
- Log these at `WARNING` instead of silently passing — a disconnected websocket during
  a long backtest currently gives zero indication anything went wrong with progress
  reporting.

**Logging additions:**
- INFO at each broadcast stage transition (already partially present via
  `_broadcast_progress` payloads — extend to also write these to the structured log,
  not just the websocket, so a completed backtest has a log-based timeline even if no
  one was watching the UI live).

---

## 4. The Rejection Funnel (new artifact)

Every fix above feeds one shared `Counter`-based structure, attached to every backtest
result:

```
REJECTION FUNNEL — 2026-04-27 to 2026-06-20, Volatility 75 Index
─────────────────────────────────────────────────────────────────
Candles processed:                          11,520
Structure updates (BOS/ChoCH fired):            340
Signals attempted (POI + bias aligned):         812
  ├─ Gate 1  HTF bias NEUTRAL/mismatch:         640   (78.8%)
  ├─ Gate 3  No POI (OB/FVG/OTE/S&D):            98   (12.1%)
  ├─ Gate 4  RR below minimum:                    4   ( 0.5%)
  ├─ Gate 5  Spread exceeds limit:                0   ( 0.0%)
  ├─ Gate 6  Outside kill zone:                   0   ( 0.0%)
  ├─ Gate 7  Blocked by news:                      0   ( 0.0%)
  ├─ Gate 8  Confluence score < 65:               46   ( 5.7%)
  └─ Circuit breaker blocks:                       2   ( 0.2%)
Signals passed all gates:                        22
Trades opened:                                    22
```

This single table is the point of the whole plan — after the fixes above land, this is
what tells you at a glance whether the bottleneck moved (e.g. from Gate 1 to Gate 8)
rather than having to re-derive it by re-reading code every time.

---

## 5. Execution order

1. **`market_structure.py` fix** — everything downstream depends on `htf_bias` and
   `trend` actually updating. Do this first and re-run with logging alone (no other
   fixes yet) to confirm BOS/ChoCH frequency looks sane before touching anything else.
2. **§2 global repurposed-parameter fix** (`fvg.py`, `liquidity.py`,
   `breakeven_manager.py`, `signals.py` spread gate, plus `compounding.py` overrides).
   Re-run again — FVG/sweep detection rates should now be non-trivial.
3. **`signals.py` gate logging rewrite** (remove `is_bt` suppression, non-short-
   circuiting diagnostic mode, structured Counters). This is what actually produces
   the rejection funnel — do it once the upstream detectors are trustworthy, so the
   funnel reflects real bottlenecks rather than the bugs already fixed above.
4. **`order_blocks.py`, `asian_range.py`, `supply_demand.py`** correctness fixes —
   these affect POI/sweep accuracy but are secondary to the structure/FVG/liquidity
   fixes in scale of impact.
5. **Risk module logging** (`engine.py`, `multi_tp.py`, `circuit_breaker.py`, etc.) —
   can happen in parallel with step 4, since it doesn't block signal generation, only
   affects trade management once a signal exists.
6. **`ipdm.py`, `ipdm`-adjacent, `report.py`, `runner.py`, `optimizer.py` cleanup** —
   lowest urgency, do last.
7. **`backend/backtester/engine.py`** — needs to be reviewed as soon as it's
   available; likely needs to happen in parallel with step 1, since the per-candle
   loop is where TRACE-level logging hooks get wired in for everything else.

After each numbered step, re-run the same fixed backtest window (same symbol, same
date range, same config) and diff the rejection funnel against the previous run —
that's the acceptance criteria for "did this fix actually do anything," rather than
judging by win rate or PnL, which won't be meaningful until trade count is in the
dozens at minimum.
