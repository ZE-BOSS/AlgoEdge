# Backtest sweep — bug log

Bugs observed during Stage A. **Logged, not fixed mid-sweep** — fixing while the
sweep runs makes the runs incomparable, which is the whole point of a clean
baseline. The exception is a bug that *blocks* the sweep from producing any
result at all; those are fixed immediately and noted as such.

| # | found | severity | issue | status |
|---|---|---|---|---|
| B1 | 2026-08-28, first cell (VWAP × BTCUSD) | **blocker** | `Backtest failed: name 'asyncio' is not defined` | **fixed immediately** |

---

## B1 — `asyncio` not defined during finalisation *(blocker, fixed)*

**Symptom.** Every run completed its simulation, logged a correct-looking result
(`Backtest complete: BTCUSD | 150 trades | P&L=$-2613.56 | WR=26.0%`), and then
immediately failed with:

```
[BACKTEST] Backtest failed: name 'asyncio' is not defined
```

The result was discarded, nothing persisted, and the sweep driver saw the run as
never completing.

**Cause.** Mine, introduced with the T2.1 event-loop fixes. Those moved
`_sanitize` and `json.dumps` off the event loop via `asyncio.to_thread(...)`, but
`asyncio` was only ever imported inside *nested* function scopes in
`backend/api/routes/backtest.py` (three separate local `import asyncio`
statements). The new call sites sit at a scope none of those reach.

**Why it was not caught earlier.** The failure happens *after* the simulation
completes, in the finalisation path. The unit tests do not exercise the HTTP
route end to end, and every verification I ran called the engine directly rather
than through the API — so the whole finalisation path was untested. That is the
real lesson here, not the missing import.

**Fix.** One module-level `import asyncio` in `backend/api/routes/backtest.py`.

**Follow-up (not yet done):**
- [ ] An end-to-end test that drives `POST /api/backtest` and asserts a run
      persists — the gap that let this through.
- [ ] Audit the module for other function-scope imports relied on at module scope.

---

## B2 — an interrupted client blocks the user for an hour *(product issue, not fixed)*

**Symptom.** After a sweep client was killed mid-run, **every** subsequent
backtest request failed:

```
HTTP 400: {"detail":"A backtest is already running for this user."}
```

19 of 19 cells failed this way. The block persists until the Redis key's 3600s
TTL expires.

**Cause.** `POST /api/backtest` refuses to start when
`get_backtest_status()` reports `status == "running"`. That flag is set when a
run starts and cleared when it finishes — so a client (or backend) that dies
between those two points leaves it set with nothing to clear it.

**Why this matters beyond the sweep.** A user whose browser tab closes, or whose
backend restarts mid-run, is locked out of backtesting for up to an hour with no
in-product way to recover and no message explaining why. `POST /api/stop` clears
it, but nothing in the UI tells the user that.

**Worked around in the sweep driver** (clears state up front and on each 400,
then retries) — the underlying behaviour is unchanged.

**Suggested fixes, for later:**
- [ ] Store a heartbeat/started-at with the running flag and treat a run with no
      progress for N minutes as dead, so it self-clears.
- [ ] Surface a "a run appears stuck — clear it?" action in the UI when the
      status endpoint reports `running` but the percentage has not moved.
- [ ] Include the stale run's age in the 400 response so the cause is obvious.

---

## B3 — every save failed: `chart_data_h1` was never a column *(blocker, fixed)*

**Symptom.** Runs completed correctly — APA × BTCUSD produced 65 trades,
−$1,994.88, WR 9.23%, PF 0.492, DD 29.03% — and then **every save returned
HTTP 500**:

```
TypeError: 'chart_data_h1' is an invalid keyword argument for BacktestTrade
```

Result: 0 rows in the database after a run that had genuinely finished.

**Cause.** Mine, from the B6 work. I threaded `candles_h1` through the engine and
wired `chart_data_h1=...` into **both** save paths, on the assumption that the
column already existed because the trade viewer reads it. It did not — only
`chart_data`, `chart_data_m15` and `chart_data_m5` were ever defined on
`BacktestTrade`.

**Why it was not caught.** Same root cause as B1: every test I ran called the
engine or the grouper directly. The grouper *did* produce `chart_data_h1`
correctly (verified: 45 H1 bars), so the unit test passed — the failure is one
layer further down, at the ORM boundary, which nothing exercised.

**Fix.** Added `chart_data_h1 = Column(Text)` to `BacktestTrade` and the matching
`ALTER TABLE` migration. Verified the column is present after `init_db()`.

**Pattern worth noting.** B1 and B3 are the same mistake twice: a change verified
at the layer I changed, never through the path a real run takes. The follow-up
from B1 — an end-to-end test that POSTs `/api/backtest` and asserts a run
persists — would have caught both.

---

## B4 — progress callbacks never reach the browser *(FIXED 2026-08-30, verified)*

**Symptom (reported by the user, 2026-08-30).** During a backtest the frontend
hangs and lags, the progress bar sits nearly stationary and "moves a bit but
doesn't move enough", the chart does not follow the backtest, and results only
appear after a manual refresh — while the backend finishes quickly.

**Cause.** `backend/backtester/runner.py:133` correctly offloads the CPU-bound
engine to a worker thread:

```python
results = await asyncio.to_thread(engine.run, ...progress_cb...)
```

so `progress_cb` — `_sim_progress` in `backend/api/routes/backtest.py:1055` —
executes **on the worker thread**. Inside it:

```python
asyncio.create_task(ws_manager.broadcast_to_user(...))
```

`asyncio.create_task()` requires a running event loop *in the calling thread*.
On a worker thread there is none, so every call raises `RuntimeError: no
running event loop`, which the enclosing `except Exception: pass` swallows.

**Not a single "Simulating trades…" frame is ever broadcast.** The bar holds at
whatever the event loop last emitted (35%, or the 10%/70% from
`_broadcast_progress`) and then jumps to 100% when the thread returns. Each
failed call also leaves an un-awaited coroutine behind.

Four call sites share the bug — `backtest.py` lines **823, 1066, 1578, 1698** —
covering both the single-symbol and the portfolio path.

**Fix, for later:** capture the loop before `to_thread`
(`loop = asyncio.get_running_loop()`) and use
`asyncio.run_coroutine_threadsafe(coro, loop)` from the callback. Also stop
swallowing the exception — a bare `except: pass` is what hid this.

## B5 — the simulation reports progress ~4 times per run *(FIXED 2026-08-30, verified)*

`backend/backtester/engine.py:798` throttles the callback with
`(i & 0x3FF) == 0` — once every **1024 bars**. The sweep ran 5,000 bars per
run, so the callback fires **4–5 times total**, and the 0.5 s rate limit in
`_sim_progress` can drop some of those. Even with B4 fixed the bar would move
in four steps.

Compounding it, the callback writes `USER_BACKTEST_STATE` (process-local) but
never calls `_save_state`, so `GET /api/backtest/status` — which reads the
Redis-backed state — sees nothing new either. That is the "I have to refresh to
get the results" half of the report.

**Fix, for later:** throttle on elapsed time, not bar count (target ~2–4 Hz),
and persist state on the same tick so polling and WebSocket agree.

## B6 — the sweep ran 5,000 bars, not the 50,000 the plan specifies *(config)*

Every one of the 116 runs carries `"candle_count": 5000` in its
`params_snapshot`. §0.1 of the plan calls for 50,000.

Verified against the live terminal (`research/data/mt5_probe.py`) that this was
not a broker limit: `copy_rates_from_pos` returns **50,000 M1 bars** for every
symbol tested. The real ceiling is `maxbars` = 100,000, and requesting exactly
100,000 fails with `(-2, 'Terminal: Invalid params')` while **99,999 succeeds** —
an off-by-one in the terminal worth documenting for T8.1.

Not a defect in the results (the window is consistent across all 116 runs, so
they remain comparable), but the sample is ~10× smaller than intended.

## B7 — gate telemetry was disabled for the entire sweep *(FIXED 2026-08-30, verified)*

`strategy_rejections` is empty and `by_confirmation` contains only the single
lumped `base_structure` bucket in **all 116 runs**.

The cause is *not* missing instrumentation — the engines carry **49
`self.gate(...)` call sites** and `backend/backtester/ablation.py` implements a
full recording pass. The cause is that `base_strategy.py:92` constructs
`GateRecorder(enabled=False)` and **nothing on the `/api/backtest` path ever
sets `enabled = True`**; only `ablation.run_recording_pass` does, and that is
not reachable from the route.

`engine.py:1381` guards on `getattr(_gates, "enabled", False)`, so it silently
contributed nothing for every run.

**Consequence.** Gate frequency, block rate and per-gate excursion — the whole
of Stage B.1/B.2 — cannot be computed from this sweep. See
`research/02-technical-confluences.md`.

**Fix, for later:** enable the recorder on the backtest path (it is near-free
when the run is a backtest) and re-run the sweep.

## B8 — RETRACTED. Not a bug; my lookup was wrong.

**Claimed:** seven symbols (Crash 300/1000, US Tech 100, Volatility 75, Germany
40, Netherlands 25, Hong Kong 50) were backtested with no cost model at all.

**This was false.** All 21 symbols have a resolved cost model in every run. The
engine stores it under an **uppercased** key (`engine.py:_costs_for` does
`key = (symbol or "").upper()`), so `resolved_cost_model["Crash 1000 Index"]`
misses while `["CRASH 1000 INDEX"]` hits. My audit looked up the exact symbol
string, which matched only the symbols that are already uppercase (BTCUSD,
EURUSD, XAUUSD...) and missed every mixed-case one.

Verified after fixing the lookup: **21 of 21 symbols have costs, none missing.**

## B9 — RETRACTED. Unit naming is inconsistent; the arithmetic is not.

**Claimed:** spread is stored in points for crypto/metals and pips for FX, so
some symbols are mis-charged by up to 1000x.

**Not demonstrated.** `get_pip_size()` returns a different pip/point ratio per
symbol (1000 for BTCUSD and ETHUSD, 10 for most, 1 for Volatility 75). My
comparison converted the backtest figure with `pip_size` and the live figure with
MT5 `point` — two different constants — which manufactured the discrepancy. This
is the same class of mistake as B8.

Because `spread_pips` and `risk_pips` are expressed in the **same** per-symbol
unit, their ratio — which is what actually reaches P&L — cancels the convention.
BTCUSD: 18,424 / 359,939 = 0.051 R, which matches its observed overrun. The
"pips" label is misleading, but nothing is mis-charged.

**What survives.** The only cost figures that need no unit conversion, because
they come from `pnl_r` alone:

| symbol | stop exits | avg R at stop (−1.000 = costless) |
|---|---:|---:|
| XRPUSD | 256 | **−1.350** |
| SOLUSD | 262 | −1.324 |
| ETHUSD | 276 | −1.308 |
| BTCUSD | 272 | −1.276 |
| Volatility 75 | 290 | −1.110 |
| Crash 1000 | 149 | −1.025 |
| Crash 300 | 154 | **−1.012** |

Friction is real and unevenly distributed, and report 01's decomposition (64% of
the loss) holds — it is computed from `pnl_r` and `exit_reason` only. What is
**not** true is that any of it stems from a missing or miscalibrated cost model.

## B10 — MT5 tick history is too slow to be usable *(observation)*

`copy_ticks_range` for a single symbol-day in January ran for **20+ minutes
without returning**, on a terminal that serves 50,000 M1 bars in under a second.
Tick history exists but must be bulk-downloaded ahead of time; it cannot be
fetched on demand inside an analysis run.

Relevant to Stage C: order flow *can* be reconstructed historically from ticks
(contrary to `research/03`, which assumed no history at all), but only after an
overnight bulk pull.


---

# Fixes applied 2026-08-30 — and how each was verified

The lesson recorded under B3 was that every earlier fix was checked at the layer
it changed and never through the path a real run takes. So each of these was
driven against real objects and real MT5 bars, not asserted.

## B4 — fixed
`backend/api/routes/backtest.py`, `_sim_progress`. Captured the loop with
`asyncio.get_running_loop()` *before* `run_backtest` hands the engine to
`asyncio.to_thread`, and replaced `asyncio.create_task(...)` with
`asyncio.run_coroutine_threadsafe(coro, loop)`. The bare `except Exception: pass`
that hid the failure now logs.

**Verified** (`research/data/verify_fixes.py`): calling `asyncio.create_task`
from inside `asyncio.to_thread` raises **RuntimeError**; `run_coroutine_threadsafe`
does not. That RuntimeError is precisely what was being swallowed on every
progress tick.

**Correction to the original report:** I wrote that four call sites shared this
bug (823, 1066, 1578, 1698). Only **one** does — `_sim_progress` at 1066. The
other three run on the event loop (they `await` either side) and were always
fine. The portfolio path has no `progress_cb` at all, which is a separate gap.

## B5 — fixed
`backend/backtester/engine.py`. `(i & 0x3FF) == 0` — a fixed 1024-bar stride —
became `i % max(1, _n_bars // 200) == 0`, so the callback fires ~200 times at
any bar count. `_sim_progress` also now writes through `_save_state`, so the
polled status endpoint and the WebSocket agree; that was the "I have to refresh
to see results" half.

**Verified:** at 5,000 bars the old stride fired **4** times and the new one
fires **200**; at 50,000 bars, **49** vs **200**.

## B7 — fixed
`backend/api/routes/backtest.py`. Added `engine.gates.enabled = True` on the
single-symbol path and `strategy_engine.gates.enabled = True` on the portfolio
path. Nothing else was needed — the 49 `self.gate(...)` sites, the
`begin_candidate()` calls in all seven engines, and the collection code in
`engine.py:1381` were all already correct and simply never switched on.

**Verified** (`research/data/verify_b7.py`), CRT over 1,200 real XAUUSD M15 bars:

```
candidates_recorded : 1200
candidates_blocked  : 1200
distinct gates seen : 2
  session_filter        evaluated=1200 passed= 143
  session_trade_cap     evaluated=1200 passed=1200
strategy_rejections : {'session_filter': 1057}
```

`strategy_rejections` was `{}` in all 116 saved runs. It is now populated.

**Caveat, stated honestly:** NYOpenRetest recorded nothing on the same slice —
its first gate sits behind a time-of-day guard that the slice never satisfied.
That is expected behaviour rather than a failure, but it means per-strategy
coverage should be checked on the re-run rather than assumed.

---

## B11 — BOS dedup key is slice-relative, so BOS re-fires on every bar *(VERIFIED, not fixed)*

**Symptom.** `BOS fired: BULLISH | Broken level: 5875.01 | BOS count: 38 … 42` —
the same broken level reported dozens of times in a row with `consecutive_bos`
climbing. Over one Crash 500 run: **2,795 BOS events across 370 distinct
levels**, worst case 42 fires on a single price.

**Cause.** `market_structure.py:84` sets `"bar_idx": int(i)` where `i` indexes
the **DataFrame slice passed in**. Strategies call `update()` with a rolling
window (`df.iloc[i-500:i+1]`), so the window slides one bar per call and the same
physical swing gets a **different `bar_idx` every time**.

The dedup guard at line 150 is:

```python
if self._last_bos_index != high_swings[-1]["bar_idx"]:
```

Since that key changes every bar, the guard never matches and the BOS re-fires
indefinitely.

**Verified empirically**, tracking swings by (price, timestamp) across 40
consecutive sliding windows on Crash 1000 M5:

```
distinct physical swings tracked : 9
swings whose bar_idx CHANGED     : 9   (all of them)
  price=5871.97  time=2026-08-22 01:45  bar_idx seen as [461,462,463,464,465,466,467,468]
  price=5901.256 time=2026-08-22 03:35  bar_idx seen as [480,481,482,483,484,485,486,487]
```

**Why this matters.**
1. `consecutive_bos` inflates without limit, so `trend_confirmed` (threshold
   `min_bos_count`, default 2) latches True almost immediately and stays there.
2. Any strategy keying on BOS receives a flood of duplicates for one real break.
3. It is a plausible mechanical cause of the independent finding in
   `research/08` §5 that **BOS has the worst measured lift of any confluence
   (−0.037 R, positive on only 11 of 48 symbol-sides)**.

**The intent was right, the key was wrong.** The code comment explains that
price equality was rejected as a dedup key because "two distinct swings can
share a price by coincidence" — correct — but `bar_idx` is not stable across
calls. The `index` field on the same dict holds the swing's **timestamp**, which
is stable and unique. That is the correct key.

**Suggested fix:** dedupe on `high_swings[-1]["index"]` (timestamp) rather than
`bar_idx`, in all four places (`market_structure.py` lines 142, 150, 175, 181).
Not applied — it changes signal generation for every strategy that uses market
structure, so it needs its own before/after measurement.

---

## Note — `daily_trade_cap` sweep attempted and abandoned

`research/data/sweep_daily_cap.py` is marked **INVALID**. `daily_trade_cap` is a
strategy-side gate (`strategy_two/engine.py:284`) evaluated while signals are
generated; the script varied the *engine's* `max_daily_trades` against a
pre-generated signal list, so every cap returned an identical 71 trades / +$417.

A correct sweep must regenerate signals inside the loop with the strategy's own
cap changed each time (~4 minutes per value). Still worth doing — the telemetry
shows this gate blocks 77% of all candidates.
