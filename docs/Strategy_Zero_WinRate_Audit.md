# AlgoEdge Multi-Strategy Audit: Why Only Drift & Jump Alpha Is Trading

**Scope:** `SMC_v1`, `CRT_v1`, `HTFFVGFlip_v1`, `BiasIFVG_v1`, `NYOpenRetest_v1` — backtesting and live trading. **Method:** Static code audit of every strategy engine plus the shared risk/execution pipeline (`RiskEngine`, `MultiTPManager`, `BacktestEngine`, `bot_service._scan_loop`, `backtest.py`'s signal-generation loop). No code was executed — findings are traced to specific functions/lines and cross-checked against how each pipeline actually calls them.

## Bottom line

This isn't strategy logic being "too strict." Five independent, concrete bugs sit in the plumbing that every non-Drift/Jump strategy has to pass through before a trade can be opened. `DriftJumpAlpha_v1` happens to be the one strategy that doesn't trip any of them — which is exactly why it's the only one producing trades. Fix the five bugs below and every other strategy has a real chance to trade (whether it trades *well* is a separate question you'll only be able to answer once the pipeline stops silently eating every signal).

| \# | Bug | Where | Blocks |
| :---- | :---- | :---- | :---- |
| 1 | Two "hard filter" gates check context keys that are never populated, so they reject on every single signal | `SignalGenerator`/`TradeGate`, SMC engine | `SMC_v1` — backtest & live |
| 2 | Signals use `"LONG"`/`"SHORT"` as `direction`, which the risk engine's direction parser doesn't recognize | 3 strategy engines | `HTFFVGFlip_v1`, `BiasIFVG_v1`, `NYOpenRetest_v1` — backtest & live |
| 3 | `get_required_timeframes()` isn't overridden, so calling it throws `NotImplementedError`, silently swallowed and mislabeled | 3 strategy engines | `HTFFVGFlip_v1`, `BiasIFVG_v1`, `NYOpenRetest_v1` — **live only** |
| 4 | Backtest route hardcodes `H4`/`M15`/`M5` dispatch instead of asking the strategy what timeframes it needs | `backend/api/routes/backtest.py` | `CRT_v1`, `HTFFVGFlip_v1` — **backtest only** |
| 5 | Bias lookup references an attribute that doesn't exist on the class | `CRTEngine._get_htf_bias()` | `CRT_v1` — live (and backtest, once \#4 is fixed) |

---

## Bug 1 — SMC\_v1's Gate 6 "hard filters" reject every signal, unconditionally

**File:** `backend/strategies/strategy_one/signals.py`, `TradeGate.validate_all` **Also relevant:** `backend/strategies/strategy_one/engine.py`, `SMCEngine._build_scorer_context` **Severity:** Blocker — 100% rejection rate, every signal, every symbol.

`SMCParams` ships with three "hard filter" flags, **all defaulting to `True`**:

enforce\_htf\_pd: bool \= True

enforce\_fvg\_displacement: bool \= True

enforce\_asian\_range\_sweep: bool \= True

Gate 6 checks them like this:

if getattr(self.config.smc, "enforce\_fvg\_displacement", False):

    if not context.get("active\_fvgs", \[\]):

        reasons.append("Gate 6: Hard Filter — Signal lacks FVG displacement")

if getattr(self.config.smc, "enforce\_asian\_range\_sweep", False):

    if not context.get("asian\_range\_swept", False):

        reasons.append("Gate 6: Hard Filter — Asian Range has not been swept")

The problem: `_build_scorer_context()` — the function that builds the `context` dict passed into this gate — **never sets `active_fvgs`, `asian_range_swept`, or `ipdm_phase` at all.** Its actual return dict is:

return {

    "symbol": symbol, "signal\_direction": bias, "htf\_bias": ...,

    "m15\_bos": ..., "m15\_choch": ..., "liquidity\_sweep": ...,

    "fresh\_ob": fresh\_ob, "fvg\_present": fvg\_present, "fvg\_inside\_ob": fvg\_inside\_ob,

    "in\_ote\_zone": ..., "in\_sd\_zone": ..., "candle\_tier": candle\_tier,

    "in\_kill\_zone": in\_kill\_zone, "is\_backtesting": self.is\_backtesting,

}

No `active_fvgs`, no `asian_range_swept`. So every call to `context.get("active_fvgs", [])` returns `[]` (falsy), and every call to `context.get("asian_range_swept", False)` returns `False` (falsy) — **regardless of what actually happened on the chart.** Both `if not ...:` checks are therefore always `True`, and both rejection reasons get appended to `reasons` on literally every signal the engine ever builds, real setup or not.

Since `TradeGate.validate_all` returns `passed = len(reasons) == 0`, `passed` is always `False`. That flows into the signal's metadata:

signal \= TradeSignal(..., metadata={..., "passed\_gates": passed, "rejection\_reasons": reasons})

And both the backtester and live bot check this before ever reaching the risk engine:

\# backend/backtester/engine.py

passed\_gates \= sig.get("metadata", {}).get("passed\_gates", True)

if not passed\_gates:

    ...

    continue   \# signal never reaches RiskEngine.evaluate\_signal at all

**Net effect:** SMC\_v1 can generate a textbook-perfect, fully-confluent signal and it will *still* be discarded before position sizing is even computed, because two of its own quality filters are checking for data that no code path ever fills in.

### A related dead-code twin in the same gate

The third sub-check in Gate 6, `enforce_htf_pd`, has the opposite problem — it never fires at all, silently:

ipdm\_phase \= context.get("ipdm\_phase", "")

if direction \== "BUY" and "PREMIUM" in ipdm\_phase:

    ...

elif direction \== "SELL" and "DISCOUNT" in ipdm\_phase:

    ...

`direction` here is `context.get("signal_direction", "")`, which for SMC is always `"BULLISH"`/`"BEARISH"` — never the literal strings `"BUY"`/`"SELL"`. So `direction == "BUY"` and `direction == "SELL"` are always `False`, and this filter can never reject anything even when it should. It's not blocking your trades, but it means the "buy in discount / sell in premium" enforcement described in the spec is currently a no-op.

### How to confirm this is your issue

Run any SMC\_v1 backtest and look at `report.rejection_funnel.strategy_rejections` in the response (also rendered in the Backtester UI under "Signal Rejection Funnel"). If this diagnosis is correct, you should see `"Gate 6: Hard Filter — Signal lacks FVG displacement"` and `"Gate 6: Hard Filter — Asian Range has not been swept"` as the dominant (likely *only*) rejection reasons, with `approved: 0`.

### Fix

Either:

- Populate `active_fvgs` and `asian_range_swept` in `_build_scorer_context()` with real values (there's already an `AsianRange` detector class in `backend/strategies/core/asian_range.py` that's built but never wired into `SMCEngine`, and `fvg_present`/`active_fvgs` should reasonably be the same underlying FVG list), or  
- Default `enforce_fvg_displacement` and `enforce_asian_range_sweep` to `False` until the supporting context is actually implemented.

Also fix the `enforce_htf_pd` direction comparison to check `"BULLISH"`/`"BEARISH"` instead of `"BUY"`/`"SELL"` so it isn't silently dead.

---

## Bug 2 — `"LONG"`/`"SHORT"` direction strings are invisible to the risk engine

**Files:** `backend/strategies/strategy_four_htf_fvg_flip/engine.py`, `backend/strategies/strategy_five_bias_ifvg/engine.py`, `backend/strategies/strategy_six_ny_open_retest/engine.py` **Root cause location:** `backend/risk/multi_tp.py` **Severity:** Blocker — 100% rejection rate for the three affected strategies.

The shared risk pipeline normalizes direction through two helpers:

\_BUY\_DIRECTIONS \= {"BUY", "BULLISH"}

\_SELL\_DIRECTIONS \= {"SELL", "BEARISH"}

def \_is\_buy(direction: str) \-\> bool:

    return direction.upper() in \_BUY\_DIRECTIONS

def \_is\_sell(direction: str) \-\> bool:

    return direction.upper() in \_SELL\_DIRECTIONS

`SMC_v1` and `DriftJumpAlpha_v1` both correctly emit `"BUY"`/`"SELL"` (SMC explicitly converts `BULLISH`/`BEARISH` → `BUY`/`SELL` in `SignalGenerator.generate`). `CRT_v1` also emits `"BUY"`/`"SELL"` directly. But three strategies build their `TradeSignal.direction` from an internal `state["bias"]` variable that is only ever set to `"LONG"` or `"SHORT"`:

\# strategy\_five\_bias\_ifvg/engine.py

if candles.iloc\[-1\]\["close"\] \> sma20: return "LONG"

return "SHORT"

...

return TradeSignal(symbol=symbol, direction=state\["bias"\], ...)

\# strategy\_four\_htf\_fvg\_flip/engine.py

state\["bias"\] \= "LONG"   \# / "SHORT"

...

return TradeSignal(symbol=symbol, direction=state\["bias"\], ...)

\# strategy\_six\_ny\_open\_retest/engine.py

state\["bias"\] \= "LONG"   \# / "SHORT"

...

return TradeSignal(symbol=symbol, direction=state\["bias"\], ...)

`"LONG"` and `"SHORT"` are in **neither** `_BUY_DIRECTIONS` nor `_SELL_DIRECTIONS`. Walking a `"LONG"` signal through `RiskEngine.evaluate_signal`:

is\_buy \= \_is\_buy(direction)          \# "LONG" → False

if is\_buy and sl \>= entry: ...       \# skipped, is\_buy is False

if not is\_buy and sl \<= entry:       \# True and (sl \< entry, since it's a long stop) → True

    return False, "SELL Stop Loss must be above entry", \[\]

The stop loss was correctly placed *below* entry for a long trade, but because `direction` doesn't parse as "buy," the code falls into the SELL-shaped validation branch and rejects it as an inverted stop. Every `"LONG"` signal dies here, every time.

`"SHORT"` signals slip past that specific check (a SL above entry happens to satisfy the SELL-shaped validation by accident) but die one step later in `MultiTPManager.calculate_tp_levels`:

if \_is\_buy(direction):

    sign \= 1

elif \_is\_sell(direction):

    sign \= \-1

else:

    logger.error(f"Unknown direction '{direction}' — cannot calculate TPs")

    return \[\]

`_is_sell("SHORT")` is also `False` (`"SELL"`/`"BEARISH"` only), so this falls into the `else` branch, logs `"Unknown direction 'SHORT'"`, and returns an empty list. Back in `evaluate_signal`:

if not tp\_levels:

    return False, "No valid TP levels calculated", \[\]

So **every** signal from these three strategies is rejected — longs at the stop-loss-direction check, shorts at the TP-calculation step — in both backtesting (`BacktestEngine.run` → `risk_engine.evaluate_signal`) and live trading (`bot_service.py`'s execution block calls the identical `risk_engine.evaluate_signal`).

### Fix

Change `direction=state["bias"]` to `direction="BUY" if state["bias"] == "LONG" else "SELL"` in all three engines (or simplify further and have the state machines use `"BUY"`/`"SELL"` internally to begin with, matching `CRT_v1`'s convention).

---

## Bug 3 — Live trading crashes before fetching data for 3 strategies, logged as the wrong error

**File:** `backend/services/bot_service.py`, `_scan_loop` **Severity:** Blocker, **live trading only** — these strategies never even reach `on_bar`.

req\_tfs \= current\_engine.get\_required\_timeframes() if hasattr(current\_engine, 'get\_required\_timeframes') else \["H4", "M15", "M5"\]

`hasattr(current_engine, 'get_required_timeframes')` is checking whether the *method exists*, not whether it's *implemented*. It always returns `True`, because every strategy inherits from `BaseStrategy`, which defines the method — as a stub:

\# backend/strategies/base\_strategy.py

def get\_required\_timeframes(self) \-\> list\[str\]:

    """Return list of timeframes this strategy needs."""

    raise NotImplementedError

`SMC_v1`, `DriftJumpAlpha_v1`, and `CRT_v1` all override this method with a real implementation. **`HTFFVGFlip_v1`, `BiasIFVG_v1`, and `NYOpenRetest_v1` do not** — grep their engine files and the method simply isn't there. So for these three, `current_engine.get_required_timeframes()` executes the base class's `raise NotImplementedError` immediately.

This happens *before* any candle data is fetched, inside the outer `try` block whose `except` clause reads:

except Exception as e:

    self.\_log\_event(f"Data fetch error for {symbol}: {str(e)\[:150\]}", "ERROR", "DATA")

Two consequences:

1. **These three strategies never fetch candles or call `on_bar` in live trading at all.** Not "produce bad signals" — they never run.  
2. The error is logged as **`"Data fetch error for {symbol}: "`** — and since `str(NotImplementedError())` with no message is an empty string, the log line literally ends right after the colon. This is actively misleading if you were grepping the activity log for the actual cause, since no data fetch was ever attempted.

### Fix

Add a `get_required_timeframes()` override to each of the three engines, returning the timeframes their `on_bar` logic actually checks against (see Bug 4 for what those need to be once the backtest side is also fixed).

---

## Bug 4 — Backtest route hardcodes `H4`/`M15`/`M5`, ignoring what each strategy declares

**File:** `backend/api/routes/backtest.py`, inside `_run_backtest_task` → `generate_signals_simulated` **Severity:** Blocker for `CRT_v1`, `HTFFVGFlip_v1` — **backtest only.**

The backtest route only ever fetches three timeframes:

candles\_h4 \= await DataFetcher.get\_historical\_data(req.symbol, "H4", count=req.candle\_count)

candles\_m15 \= await DataFetcher.get\_historical\_data(req.symbol, "M15", count=req.candle\_count)

candles\_m5 \= await DataFetcher.get\_historical\_data(req.symbol, "M5", count=req.candle\_count)

and the simulation loop only ever calls `on_bar` with those three literal strings:

s \= await engine.on\_bar(req.symbol, "H4", slice\_h4)

s \= await engine.on\_bar(req.symbol, "M15", slice\_m15)

s \= await engine.on\_bar(req.symbol, "M5", slice\_m5)

It never calls `engine.get_required_timeframes()` to find out what a given strategy actually wants. This happens to line up for `SMC_v1` (`["H4","M15","M5"]`) and `DriftJumpAlpha_v1` (`["M5"]`) — which is a coincidence, not a design guarantee — and breaks the strategies whose declared timeframes don't match those three literal strings:

- **`CRT_v1`** defaults to `htf_timeframe="1H"`, `ltf_timeframe="M1"` (`CRTParams` in `backend/core/config_schema.py`). `CRTEngine.on_bar` checks `if timeframe == htf:` / `elif timeframe == ltf:` — since the backtest only ever passes `"H4"`/`"M15"`/`"M5"`, **neither branch ever matches**, so `on_bar` is a complete no-op for the entire backtest. No signals, no errors, no trades — which is exactly the "0% win rate" symptom (in this case, 0 trades).  
- **`HTFFVGFlip_v1`** defaults to `htf_timeframe="H1"`. Its `on_bar` has `if timeframe == self.params.htf_timeframe:` for the HTF-FVG-tap detection — never matches `"H4"`. Its M5-branch logic (FVG formation \+ retest \+ inversion) does have `elif timeframe == "M5":` hardcoded, so that half technically runs, but since the HTF branch never fires, the state machine can never leave `AWAIT_HTF_TAP` — the M5 logic never has anything to act on.

`BiasIFVG_v1` happens to survive this particular bug in backtest only because its bias branch checks `if timeframe in ["H1", "H4", "D1"]:` (which includes `"H4"`) and its entry branch checks `elif timeframe == "M5":` — both of which are satisfied by the backtest's hardcoded set purely by luck. It still fails via Bug 2 and Bug 3\.

### Fix

Make the backtest route call `engine.get_required_timeframes()` (after Bug 3's fix makes that safe for all six strategies) and dynamically fetch/dispatch whatever timeframes each strategy actually declares, instead of assuming `H4`/`M15`/`M5` universally. This is the same underlying design flaw as Bug 3 — the backtest and live paths each hardcode an assumption about timeframes instead of asking the strategy — just manifesting differently in each path.

---

## Bug 5 — `CRTEngine._get_htf_bias()` references an attribute that doesn't exist

**File:** `backend/strategies/strategy_three_crt/engine.py` **Severity:** Blocker for `CRT_v1` in live trading (and in backtest, the moment Bug 4 is fixed).

def \_get\_htf\_bias(self) \-\> str:

    """Use MS detector to get bias on HTF."""

    last\_choch \= self.ms\_detector.state.get("last\_choch")

`self.ms_detector` is a `MarketStructureDetector` (`backend/strategies/core/market_structure.py`). Its `__init__` sets `self.swings`, `self.trend`, `self.bos_history`, `self.consecutive_bos`, `self.trend_confirmed`, `self._last_bos_level` — **there is no `self.state` attribute anywhere in that class.** The class does expose a working accessor for exactly this purpose:

def get\_bias(self) \-\> str:

    return self.trend

`_get_htf_bias()` should be calling `self.ms_detector.get_bias()` (or reading `self.ms_detector.trend` directly), not `self.ms_detector.state.get(...)`. As written, calling `_get_htf_bias()` raises `AttributeError: 'MarketStructureDetector' object has no attribute 'state'`.

This function is only called once a C1 candle is already recorded and a second HTF candle arrives to be evaluated as a candidate C2 — i.e., right in the middle of the core setup-detection logic. In **live trading**, where Bug 4 doesn't apply (the live loop correctly feeds `on_bar` with `self.params.htf_timeframe`/`ltf_timeframe`), this line will throw on the second HTF bar every single cycle, caught by the inner `except Exception as e: self._log_event(f"Strategy error on {symbol}: ...")` handler in `bot_service.py` — meaning `CRT_v1` will spam `"Strategy error on {symbol}: 'MarketStructureDetector' object has no attribute 'state'"` and never produce a signal, even after Bug 3 doesn't apply to it and even though it isn't currently reachable in backtest due to Bug 4\.

**Important:** because Bug 4 currently makes the HTF branch unreachable in backtest, this specific bug is *masked* there right now — fixing Bug 4 without also fixing this will make `CRT_v1`'s backtest start throwing instead of silently doing nothing.

### Fix

def \_get\_htf\_bias(self) \-\> str:

    return self.ms\_detector.get\_bias()

---

## Per-strategy summary

| Strategy | Backtest status | Live status |
| :---- | :---- | :---- |
| **`DriftJumpAlpha_v1`** | ✅ Working (declares `["M5"]`, correctly matches hardcoded fetch; uses `"BUY"`/`"SELL"`) | ✅ Working |
| **`SMC_v1`** | ❌ Bug 1 — Gate 6 always rejects (`active_fvgs`/`asian_range_swept` never populated) | ❌ Same as backtest — the gate check runs before any live-only code path |
| **`CRT_v1`** | ❌ Bug 4 — `on_bar` never matches the hardcoded `H4`/`M15`/`M5`, so it's a complete no-op | ❌ Bug 5 — `AttributeError` on every setup evaluation |
| **`HTFFVGFlip_v1`** | ❌ Bug 4 — HTF branch (`"H1"`) never reached; state stuck in `AWAIT_HTF_TAP` | ❌ Bug 3 — `NotImplementedError` before any data is even fetched |
| **`BiasIFVG_v1`** | ❌ Bug 2 — reaches `RiskEngine` (timeframes happen to line up) but every signal rejected on direction parsing | ❌ Bug 3 — `NotImplementedError` before any data is even fetched |
| **`NYOpenRetest_v1`** | ❌ Bug 2 — reaches `RiskEngine` (its own timeframes, `"M15"`/`"M5"`, already match the hardcoded set) but every signal rejected on direction parsing | ❌ Bug 3 — `NotImplementedError` before any data is even fetched |

---

## Secondary findings (lower severity, but worth fixing while you're in this code)

### A. Backtester.jsx UI silently can't disable SMC's hard filters even if you try

`Backtester.jsx`'s initial form state never initializes `enforce_htf_pd`, `enforce_fvg_displacement`, or `enforce_asian_range_sweep`. The checkboxes are wired (`checked={form.enforce_fvg_displacement}`), but until you explicitly click them, those fields are `undefined`. `JSON.stringify` drops `undefined` properties entirely, so an unchecked box does **not** send `false` to the backend — it sends nothing, and `SMCParams`'s Python-side default (`True`) silently wins regardless of what the checkboxes visually show. Even after fixing Bug 1's root cause, a user trying to toggle these filters off via the UI wouldn't actually be able to, until the form's initial state explicitly sets these to `false`/`true`.

### B. The TP price a strategy computes is thrown away and replaced

None of `CRT_v1`, `HTFFVGFlip_v1`, `BiasIFVG_v1`, or `NYOpenRetest_v1`'s calculated `take_profit` value is actually used by the risk engine. `RiskEngine.evaluate_signal` only reads `entry_price` and `stop_loss` off the signal, then derives its **own** TP ladder via `MultiTPManager.calculate_tp_levels`, using the generic `tp1_rr`...`tp5_rr` config values (defaults `1.0, 3.0, 5.0, 10.0, 15.0`× the signal's risk distance). So even once the above bugs are fixed:

- `CRT_v1`'s carefully-computed `sl_dist = tp_dist / target_r_multiple` (meant to hit a specific 1.5R target at the C1 extreme) is honored for the stop, but the take-profit itself is recalculated generically and will land somewhere else entirely.  
- `NYOpenRetest_v1`'s fixed 15-point target and `HTFFVGFlip_v1`'s liquidity-pool target are silently discarded the same way.

This won't block trades, but it means "what the strategy intends" and "what actually gets executed" can diverge substantially — worth knowing before you trust a backtest's TP-hit-rate breakdown for these strategies.

### C. `BiasIFVG_v1` is a placeholder, not the documented strategy

The engine's own comments say as much — `"Simulated key level tap for boilerplate"`, `"Simulated trigger logic for boilerplate"`, and `_detect_key_levels()` just `return []` unconditionally (dead code, stored to state but never read). The actual entry condition is a simplified "close breaks the previous candle's high/low" check, not the FVG/CISD/Rejection-Block key-level system described in `docs/strategy-2-bias-keylevel-ifvg.md`. Even after fixing Bugs 2 and 3, this strategy will generate trades — just not the ones the spec describes.

### D. `NYOpenRetest_v1`'s SL/TP buffers are raw price units, not instrument-normalized

`stop_buffer_points` (default `5.0`) and `fixed_target_points` (default `15.0`) are applied as raw price deltas (`state["range_low"] - buffer`), with no per-symbol scaling. That's plausible on XAUUSD ($5/$15) but is either meaningless or absurd on most other instruments (e.g., a $5 stop buffer on EURUSD near 1.08, or $5 on a Volatility index priced in the hundreds/thousands). This won't block trades outright, but will produce nonsensical position sizing (via `risk_amount / (sl_distance × point_value)`) on any symbol other than the one these defaults were tuned for.

### E. `CRT_v1`'s `"1H"` timeframe string only works by coincidence

`DataFetcher._get_timeframe_code` maps known strings (`"M1"`, `"M5"`, `"M15"`, `"M30"`, `"H1"`, `"H4"`, `"D1"`, `"W1"`, `"MN1"`) — `"1H"` isn't one of them, so `mapping.get("1H".upper(), mt5.TIMEFRAME_H1)` falls through to the **default fallback value**, which happens to equal `TIMEFRAME_H1` — the value you actually wanted. It works today purely because the fallback and the intended value are identical. Rename `CRTParams.htf_timeframe`'s default to `"H1"` (matching the mapping table) so this isn't relying on a coincidence that would silently break if the fallback default were ever changed.

---

## Suggested fix order

1. **Bug 3** (`get_required_timeframes()` overrides) — cheapest fix, unblocks live data fetching for 3 strategies immediately.  
2. **Bug 2** (`"LONG"`/`"SHORT"` → `"BUY"`/`"SELL"`) — one-line change × 3 files, unblocks the risk engine for the same 3 strategies.  
3. **Bug 5** (`self.ms_detector.state` → `self.ms_detector.get_bias()`) — one-line fix for `CRT_v1`.  
4. **Bug 4** (dynamic timeframe dispatch in the backtest route) — slightly more involved (needs to fetch whatever timeframes each strategy declares, not just H4/M15/M5), but is the one change that brings backtest parity with live for `CRT_v1` and `HTFFVGFlip_v1`.  
5. **Bug 1** (SMC Gate 6 context keys) — requires deciding whether to actually implement Asian-range-sweep/FVG-displacement tracking or disable those filters by default; the highest-value fix since SMC\_v1 is presumably your primary strategy.  
6. Secondary items (B–E) — worth addressing before trusting backtest results from these strategies, but don't block trade generation.

After 1–5, re-run each backtest and check `report.rejection_funnel` — if new rejection reasons show up (e.g., min RR, spread, confluence score), that's the pipeline finally working as intended and surfacing the *next* layer of tuning to do, rather than a plumbing bug.  
