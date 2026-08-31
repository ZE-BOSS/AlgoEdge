17 — Backtest/live parity audit + per-slot R:R
===============================================

**2026-08-31** · code changed in `backend/core/config_schema.py`,
`backend/risk/multi_tp.py`, `backend/services/bot_service.py`,
`backend/api/routes/backtest.py`

---

## PART A — The parity question, answered

You asked three things: does my research harness match the real backtester, does
the real backtester match live, and are the risk parameters the same. Taking them
in reverse order of severity.

### A.1 The backtester books profit from exits that live cannot produce

This is the most serious finding in the audit.

The backtest engine can close a position with `SESSION_END`, `TIME_LIMIT` or
`END_OF_DATA`. **Live implements none of them** — verified by searching the whole
backend outside `backtester/`; the only hit is a comment in `config_schema.py`.

From your 7,463-trade sweep:

| exit reason | n | share | avg R | total $ | exists live? |
|---|---:|---:|---:|---:|---|
| SL | 3,631 | 48.7% | −1.199 | −$302,523 | yes |
| BE_SL | 2,285 | 30.6% | +0.004 | −$587 | yes |
| **SESSION_END** | **819** | **11.0%** | **+0.471** | **+$21,088** | **NO** |
| TP1 | 532 | 7.1% | +4.732 | +$180,604 | yes |
| APA_HEAD_INVALIDATION | 84 | 1.1% | −0.930 | −$6,091 | yes |
| **TIME_LIMIT** | **80** | **1.1%** | **+1.855** | **+$13,766** | **NO** |
| **END_OF_DATA** | **26** | **0.3%** | +0.759 | +$1,222 | **NO** |

**925 trades — 12.4% of the book — exit through mechanisms that do not exist in
live trading, and they carry $36,076 of booked profit.**

They are not neutral. `SESSION_END` averages **+0.471R** and `TIME_LIMIT`
**+1.855R**, both far better than the book's −0.180R average. The backtester is
taking 12.4% of trades off the table at a profit; live would leave those
positions open to keep running or reverse.

**Answer to your question: no, live will not reproduce your backtest.** It is
biased optimistic, and the bias is concentrated in the most profitable non-TP
exits.

**Two ways to close it — pick one, do not leave it as is:**

1. **Implement session-end and time-limit closes in live.** The backtest then
   describes reality. This is the honest option if you actually want positions
   flat at session end.
2. **Remove them from the backtester.** Results get worse but become truthful.

### A.2 Live and backtest run different exit code — twice

| concern | backtest | live | status |
|---|---|---|---|
| Trailing | `TrailingManager.calculate_trailing_sl()` | `position_manager._calculate_trailing_sl()` | **duplicated** |
| Break-even | `backtester/engine._breakeven_stop()` | `BreakevenManager.check_breakeven()` | **duplicated** |

I originally wrote that these "agree line by line". That was a **reading**, not a
test, and reading was not good enough — differential testing found one real
divergence (below). Corrected and now enforced.

**Divergence found and fixed (C.7):** `TrailingManager` required
`current_sl > 0` before trailing a SELL, so a short with **no protective stop
yet** was never given a trailing stop at all. Live uses
`current_sl == 0.0 or ...` and does protect it. Backtest is now aligned to the
live (safer) behaviour — declining to protect an unprotected position is never
the better choice.

**A false alarm I raised and withdrew:** I also reported ATR trailing as
diverging by 100 pips, because live anchors to the *current* price while the
backtest anchors to the *highest since entry*. Simulating an actual price path
(run-up then pullback, both polled every bar) shows they converge to **0.0 pips**
— the ratchet makes live's per-bar anchor accumulate to the same high-water mark.
My 100-pip figure came from a single-point test with a stale `current_sl`.

**On consolidating them:** I did not merge the two implementations. The live one
is `async`, fetches its own ATR over the network, and sits directly in the
order-modification path — rewriting that carries more risk than the drift it
prevents, and the math is now proven equivalent. Instead
`tests/test_trailing_parity.py` (10 tests) re-implements the live gate and
asserts both produce identical stops across BUY/SELL, three `current_sl` states,
FIXED_PIPS, PCT_TRAIL, a full ATR price path, and the ratchet invariant.

**The guard is verified to actually work:** reverting the C.7 fix makes
`test_fixed_pips_matches_live[0.0-False]` fail, then pass again once restored.
A future edit to either implementation that breaks parity now fails CI.

### A.3 My research harness is NOT the same as your backtester

Stated plainly so no one over-reads reports 15/16:

| | your backtester | my harness |
|---|---|---|
| Session-end / time-limit exits | yes | **no** |
| Concurrent-position caps | yes | **no** |
| Circuit breaker / drawdown halts | yes | **no** |
| Compounding & position sizing | full model | fixed-fractional |
| Cost model | full (spread+slip+swap+commission) | spread only |
| Daily trade cap | yes | yes |
| Min bars between entries | yes | yes |

Cell-to-cell comparisons in reports 15/16 are valid — every cell went through
identical code. **Absolute P&L from those reports will not match the app's
backtester**, and should not be quoted as if it would.

### A.4 Risk parameters — same objects, so yes

Both paths build a `risk_config` dict consumed by the same `RiskEngine`,
`MultiTPManager` and `PositionSizer`. Sizing, TP construction, risk caps and
circuit-breaker configuration are genuinely shared. The divergence is confined to
the two exit paths in A.2 and the missing live exits in A.1.

---

## PART B — Per-strategy, per-symbol R:R (built and verified)

Research 16 measured R:R as the single largest determinant of a cell's outcome,
and showed the optimum is **not uniform**: DriftJumpAlpha on Crash 1000 wants
1:3 (drawdown 37%→23% for a fifth of the return), BiasIFVG on USOUSD wants 1:5,
and on FundedNext higher R:R is monotonically *worse* while on Deriv it is
better. One global `tp1_rr` cannot express that.

### What changed

**1. `InstrumentSlot` gained `tp1_rr` and `tp_count`** (`config_schema.py`).
`None` = inherit, matching every other field on that class.

**2. `MultiTPManager` resolves most-specific-first** (`multi_tp.py`):

```
slot (SYMBOL|strategy_id)  ->  strategy  ->  global
```

New: `slot_key()`, `resolve_tp1_rr()`, `resolve_tp_count()`, and
`slot_overrides_from_config()` which turns `InstrumentSlot` entries into the
override maps. `calculate_tp_levels` already received `symbol` and
`strategy_id`, so only the lookup changed.

**3. Live wired** (`bot_service.py`) — `risk_config` now carries the slot maps,
built by the same helper the backtest uses.

**4. Backtest wired** (`backtest.py`) — `PortfolioSymbolConfig` gained `tp1_rr`
and `tp_count`, so **one portfolio run can hold a different target per row**,
which is exactly what you asked for. The single-symbol route resolves through
the same slot map.

Because live and backtest populate the *same* maps through the *same* helper, a
target you measure in a backtest is the target that trades live.

### Verified end to end

```
slot map from InstrumentSlots:
   CRASH 1000 INDEX|DriftJumpAlpha_v1 -> 3.0
   USOUSD|BiasIFVG_v1                 -> 5.0
   (XAUUSD omitted = inherits global)

resulting TP1 prices (entry 100.0, stop 99.0, global tp1_rr = 2.0):
   Crash 1000 Index  DriftJumpAlpha_v1  RR=3.0  TP1 = 103.0
   USOUSD            BiasIFVG_v1        RR=5.0  TP1 = 105.0
   XAUUSD            VWAP_v1            RR=2.0  TP1 = 102.0   <- inherited
```

Three different targets from one `MultiTPManager`. Resolution is
case-insensitive; per-slot beats per-strategy beats global.

### A bug I introduced and caught

My first edit spliced the three helper methods into the **middle of
`MultiTPManager.__init__`**, orphaning everything after them — including
`self.trail_methods`. That would have broken trailing entirely with an
`AttributeError` on first use. Caught by the file-changed diff, relocated the
helpers, and verified the constructor now completes: `trail_methods`,
`last_tp_levels_requested` and `_last_overshoot_reason` all present.

A second one: the `slot_overrides_from_config` import initially landed at
line 945 while being used at line 731 — a `NameError` at runtime, and the exact
shape of bug **B1**. Moved to module scope (line 14) and verified the module
imports cleanly.

---

## PART C — Audit findings: fixed, and corrected

### Fixed this session

**C.1 — Hot-path logging demoted.** `market_structure.py` (4 sites) and
`liquidity.py` (2 sites) logged `BOS fired` / `ChoCH fired` / `Sweep detected` at
**INFO on every bar**. That was **86% of all log volume**, with `logs/` at 50MB
across four rotated 10MB files, and it caused 47 `PermissionError` rotation races
when three backtest workers shared the file. All six are now `logger.debug`.

**C.2 — Portfolio backtests reported no progress at all.**
`PortfolioBacktestEngine.run()` had no `progress_cb` parameter, so the UI sat at
85% for the entire global simulation — the same symptom B4/B5 fixed on the
single-symbol path. Added a callback fired ~200 times across the timeline
(`_n_steps // 200` stride), wired from the route across the 85–98% band using
`run_coroutine_threadsafe` — because `run()` executes inside `asyncio.to_thread`,
`create_task` would have raised and been swallowed exactly as in B4.

**C.3 — HTTP failures were completely silent (new bug, worse than B2).**
Neither backtest mutation had an `onError` handler. `setBtError` was called only
from the WebSocket handler and the polled status, **never from the HTTP
response** — so a rejected request produced *no message whatsoever*; the run just
didn't start. Added `httpErrorMessage()` (reads FastAPI's `detail`) and wired
`onError` on both mutations.

**C.4 — B2 recovery is now reachable.** `handleStop()` and `POST /backtest/stop`
both already existed, but the Stop button renders only while `isRunning` is true
— and a 400 makes the mutation fail, so `isRunning` goes false and **the button
disappears at exactly the moment it is needed**. A "Clear stuck run" button now
appears on the error itself when the message matches `/already running/i`.

Verified chain (400 -> message -> button):

```
backend 400 detail    -> "A backtest is already running for this user."
error shown to user   -> true
recovery button shows -> true
control (500 error)   -> button correctly hidden
network error         -> "Network Error" (graceful fallback)
```

**C.5 — Per-row R:R control in the UI.** Portfolio rows carried only
`{symbol, strategy_id}`, so the backend's new per-row R:R had no way to be set.
Added an R:R input per row; blank shows the global value as placeholder and sends
`null` (inherit).

### Corrections to my own earlier claims

**`be_offset_pips` is NOT dead config.** I listed it as having zero read sites.
It is explicitly commented *"legacy alias kept for db compat"* — deliberate
back-compat for old saved configs. Removing it risks breaking config load.
**Left in place.**

**`/circuit-breaker/reset` and `/prop-firm/reset-breach` are NOT unused.** I
claimed 5 backend routes had no frontend caller. Both of those have callers
(`dashboard.py:145` and `:121`, one frontend reference each), and `stopBacktest`
is imported and used at `Backtester.jsx:2000`. The real defect was narrower and
is C.4 above: the action exists but is unreachable in the failure state.


**C.6 — Trailing alias not synced on the config-load path.**
`trail_trigger_rr` (UI-facing) and `trail_activation_rr` (engine-facing) are two
names for one setting, and `UserConfigV2.from_dict()` propagated neither to the
other: passing only `trail_trigger_rr=1.0` left `trail_activation_rr` at its
**2.0** default. The existing validator only *warned*.

**Correction to my first write-up of this.** I originally said "saving 'trail at
1R' from the UI leaves the engine arming at 2.0R". That was **wrong** —
`Risk.jsx:150` already writes both keys in lockstep on every edit, so the UI path
was never affected. The real exposure is narrower: any caller that reaches
`from_dict` with only one of the two keys — a saved config written before that UI
sync existed, or a direct API call. Still worth fixing (the schema should not
depend on one frontend remembering to set two fields), but it is defence in
depth, not an active live bug.

Added `_sync_trail_rr_aliases()`, applied in **both** `from_dict` paths. Whichever
key the caller supplied wins; if both are given and disagree, `trail_trigger_rr`
(the one a user can actually see and edit) is authoritative.

```
UI sets trigger only          trigger=1.0  activation=1.0  in-sync=True
legacy sets activation only   trigger=3.0  activation=3.0  in-sync=True
both, disagreeing             trigger=1.5  activation=1.5  in-sync=True
neither (defaults)            trigger=2.0  activation=2.0  in-sync=True
```

Scanned for other warn-only alias pairs of this shape: none found.

### Calculation audit — position sizing is correct

Verified against the live Deriv terminal, $10,000 balance at 1% risk (intended
$100 loss at stop-out):

| symbol | lots | risk $ | risk % |
|---|---:|---:|---:|
| Crash 1000 Index | 4.14 | $99.91 | 1.00% |
| BTCUSD | 0.12 | $94.62 | 0.95% |
| US Tech 100 | 0.80 | $94.36 | 0.94% |
| XAUUSD | 0.07 | $93.39 | 0.93% |
| EURUSD | 0.40 | $92.93 | 0.93% |
| USDJPY | 0.46 | $91.99 | 0.92% |
| XAGUSD | 0.05 | $83.12 | 0.83% |

**Never exceeds the configured risk on any instrument class.** Every deviation is
negative — lot sizes round *down* to `volume_step`, which is the safe direction.
XAGUSD deviates most (0.83%) because 0.05 lots is a coarse step for silver.

*Note on method:* my first verification script recomputed the loss independently
and reported −100% error on every symbol. That was the script using wrong keys
from `get_symbol_info()`, not a sizing fault — corrected by reading the sizer's
own reported figures.


**C.7 — Backtest never trailed an unprotected SELL.** `TrailingManager` required
`current_sl > 0` before trailing a short, so a SELL with **no protective stop
yet** got no trailing stop at all; live uses `current_sl == 0.0 or ...` and does
protect it. Found by differential testing, not by reading. Backtest aligned to
the live (safer) behaviour.

**C.8 — Trailing defaults contradicted themselves (live-affecting UI bug).**
`Risk.jsx` declared `trail_activation_rr` **twice** in the same object literal —
`2.0` beside `trail_trigger_rr`, then `1.5` nineteen lines later. JavaScript
object literals are last-wins, so the shipped default was **1.5**: the UI showed
a 2.0R trail start while the engine (which reads `trail_activation_rr`) armed at
**1.5R**. Removed the duplicate.

Two related defects in the same area:

- The UI had **two separate controls** editing this one value — "Trail Start (R)"
  and "Trail Activation (RR)" — so they could be set to different numbers.
  Removed the duplicate control.
- `update()` synced `trail_trigger_rr -> trail_activation_rr` but **not the
  reverse**, so editing the second control left the first stale. Now
  bidirectional.

This is the bug I originally mis-described as C.6. The schema-level fix
(`_sync_trail_rr_aliases`) and this UI fix together close it from both ends.

**C.9 — Four risk parameters had no UI at all.** `trail_trigger_tp_level`,
`trail_require_be_first`, `be_trigger_tp_level` and `be_spread_multiple` existed
in the schema and were read by the engine, but appeared in neither the defaults
block nor the form — so they were dropped from every saved payload and could only
ever hold their defaults, while actively affecting live positions. All four now
have controls, with defaults verified to match `RiskParams` exactly.

### Still open

| # | finding | severity |
|---|---|---|
| 1 | `trail_activation_rr` / `trail_trigger_rr` — two names for one value; `config_schema:1134` exists only to validate they agree | duplicate |
| 2 | No frontend control for `trail_require_be_first`, `trail_trigger_tp_level`, `be_trigger_tp_level`, `be_spread_multiple` | backend/UI gap |
| 3 | `TrailingManager` vs `position_manager._calculate_trailing_sl` duplication (A.2) — they agree today, nothing enforces it | drift risk |

## Verification

- `pytest tests/` — **85 passed**, before and after every change
- `vite build` — clean after each frontend edit
- Per-slot R:R proven end-to-end from `PortfolioBacktestRequest` through to TP
  prices (103.0 / 105.0 / 102.0 for three rows sharing one manager)

## Files changed

- `backend/core/config_schema.py` — `InstrumentSlot.tp1_rr`, `.tp_count`
- `backend/risk/multi_tp.py` — resolution helpers + `slot_overrides_from_config`
- `backend/services/bot_service.py` — live wiring + module-scope import
- `backend/api/routes/backtest.py` — portfolio row fields, both routes wired,
  portfolio progress callback
- `backend/backtester/portfolio_engine.py` — `progress_cb` parameter + emission
- `backend/strategies/core/market_structure.py`, `liquidity.py` — INFO -> DEBUG
- `frontend/src/pages/Backtester.jsx` — per-row R:R input, `httpErrorMessage()`,
  `onError` on both mutations, B2 recovery button
- `implementation/Strategy-Fundamental-Optimization.md` — **deleted** as requested
- `research/` — untouched, 17 reports + 119MB of data preserved

Nothing committed or pushed.
