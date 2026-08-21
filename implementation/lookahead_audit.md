# Look-Ahead Bias & Temporal-Integrity Audit

**Scope:** `backend/backtester/`, `backend/strategies/` (7 engines + 10 shared detectors),
`backend/api/routes/backtest.py` (signal-generation harness), `backend/mt5/data_fetcher.py`,
`backend/services/bot_service.py` (live path), `backend/risk/` (BE/trail ordering),
`backend/utils/trade_grouper.py` + `backend/analytics/reports.py` (reporting).
**Empirical corpus:** 61 of 62 saved runs in `debug/<strategy>/<asset>.json`
(4,380 signal groups / 13,024 legs). One file is unreadable — see O-6.

---

## Verdict

**Genuine look-ahead in the code path that produced the 62 runs: none material.**

I could not find a single place where the backtest lets a strategy see a bar that had
not closed, or an indicator value derived from a bar in its future, on the single-symbol
path (`BacktestEngine` + `generate_signals_simulated`) that produced every one of those
runs. The three specific mechanisms the brief asked about — the vectorised swing cache,
the fractal detectors, and the multi-timeframe alignment — are all provably causal, and I
give the arithmetic below rather than asserting it. The empirical tells all point the same
way: 100% of trades fill exactly one bar after their signal bar, first-bar outcomes are
85–100% stop-outs (a look-ahead engine produces the opposite), and fill prices are
symmetric-to-adverse versus the strategies' theoretical entries.

Three real leaks exist (F-1, F-2, F-3) but they are all in code paths that either did not
run for these 62 files or are inert against MT5-shaped data. **If look-ahead were the
explanation for these results, it is not.**

**What *is* wrong is measurement.** Two defects — one in `trade_grouper.py`, one in
`analytics/reports.py` — mean that every R-multiple figure in every saved report is
fabricated, in the optimistic direction, by roughly an order of magnitude. A run showing
`expectancy_r = +1.73R` alongside `total_pnl = -$3,612` is not a paradox; it is arithmetic
error. These are M-1 and M-2 and I have fixed both. A third (M-3) and fourth (M-4) remain
open because fixing them changes results and that should be your decision, not mine.

---

## 1. The unclosed-bar convention (established first, then checked)

### Backtest path — `iloc[-1]` IS a closed bar

`backend/api/routes/backtest.py:511-516`

```python
if tf == primary_tf:
    tf_end = i
    tf_start_idx = max(0, tf_end - meta["window"])
    slice_tf = sorted_tf.iloc[tf_start_idx:tf_end]
```

`tf_end = i`, and Python slicing is half-open, so the slice's last row is bar `i-1`.
`current_time = primary_times[i]` (`:501`) is bar `i`'s **open** time. Therefore the bar
handed to the strategy as `candles.iloc[-1]` closed exactly at `current_time`. It is the
most-recently-closed bar, and the forming bar `i` is excluded. **Confirmed clean.**

### Live path — also a closed bar, via an explicit strip

`backend/mt5/data_fetcher.py:137` fetches `mt5.copy_rates_from_pos(symbol, tf_code, 0, count)`,
and position `0` in MT5 **is** the currently-forming bar. That raw frame therefore ends on
an unclosed bar. But `backend/services/bot_service.py:522` strips it before dispatch:

```python
closed_data = tf_data.iloc[:-1] if len(tf_data) > 1 else tf_data
res = await current_engine.on_bar(symbol, tf, _index_candles(closed_data))
```

**The two paths agree.** `iloc[-1]` means "last closed bar" in both. Every one of the seven
strategy engines consumes `candles.iloc[-1]` as the current closed bar, consistently with
that convention. Two shared detectors (`order_blocks.py:52`, `supply_demand.py:50`) still
treat `iloc[-2]` as "most recent closed candle" — that is the conservative off-by-one your
prior pass was fixing, not a leak, and I left it alone.

**Exception — live position management does NOT strip.** `position_manager.py:707` and
`:810` both call `DataFetcher.get_historical_data(...)` and use the frame as-is, so live
trailing-stop structure and live ATR are computed including the forming bar and repaint
within it. This is live-only (no backtest counterpart) so it cannot have affected the runs,
but it is a real live/backtest divergence — see M-6.

### Consequence: decision-to-fill latency is 2 bars, not 1

The strategy decides on the close of bar `i-1`. The signal is stamped
`sig["time"] = primary_times[i]` (`backtest.py:539`). The engine's gate is

`backend/backtester/engine.py:1021-1026`

```python
sig_time = float(sig.get("time", float("inf")))
if sig_time >= current_timestamp:
    break
```

so the signal is only actioned on the first bar whose timestamp is *strictly greater*, i.e.
bar `i+1`, filling at `opens_arr[i+1]` (`:1088`). Earliest legitimate fill would have been
bar `i`'s open. **The backtest is one full bar more conservative than reality permits.**
Verified empirically: `entry_time - original_signal.time` is exactly 300s for 4,374 of
4,380 groups (the 6 exceptions are session/weekend gaps). This *deflates* results.

---

## 2. FINDINGS — genuine look-ahead

### F-1 — Portfolio engine fills at the strategy's theoretical price when the symbol has no bar at the fill timestamp

**File:** `backend/backtester/portfolio_engine.py:636-639`
**Severity:** HIGH (in the portfolio path) · **Direction:** INFLATES ·
**Confidence:** HIGH on mechanism, HIGH that it did not affect your 62 runs

```python
_bar_for_fill = symbol_cache.get(symbol, {}).get("bars", {}).get(current_time)
bar_open_price = (
    _bar_for_fill["open"] if _bar_for_fill and "open" in _bar_for_fill else sig["entry_price"]
)
```

The portfolio engine iterates a *global* timeline built from the union of every symbol's
timestamps (`:181-188`). A signal is consumed on the first global timestamp strictly after
`sig_time` — which need not be a timestamp at which *this* symbol has a bar. When it isn't,
the fill silently falls back to `sig["entry_price"]`, the strategy's theoretical entry.

**Worked example.** NY Open Retest emits `entry_price = state["range_mid"]`
(`strategy_six_ny_open_retest/engine.py:105`) — a *limit* level that price has, by
construction, only just touched. Suppose XAUUSD (M5, trades 23h/day) signals at 21:00:00
with `range_mid = 2412.50`, and BTCUSD (M5, 24/7) has a bar at 21:00:30 that XAUUSD does
not. The global timeline's next entry is 21:00:30; `symbol_cache["XAUUSD"]["bars"]` has no
key for it; the position opens at exactly 2412.50.

- *What it sees:* a guaranteed fill at the exact retest level, with zero adverse selection.
- *What it could legitimately have had:* the open of XAUUSD's next actual bar, which is
  whatever price did after the retest — the very thing the trade is a bet on.

This is a same-bar limit fill at a price the engine chose after seeing that price was
reached. It also defeats `validate_at_fill_price`, since SL/TP were derived from that exact
entry and so trivially pass.

**Why your runs are unaffected:** the portfolio engine records `entry_time = sig_time`
(`:767`), not the fill bar's time. Every one of the 4,380 groups shows
`entry_time - signal_time == 300s`, which only `BacktestEngine._create_position`
(`engine.py:1112`, passing `current_time`) produces. **All 62 runs came from the
single-symbol engine.** F-1 is latent, not realised.

**Fix.** Do not fill on a timestamp the symbol has no bar for — defer to the symbol's next
real bar:

```python
_bar_for_fill = symbol_cache.get(symbol, {}).get("bars", {}).get(current_time)
if _bar_for_fill is None or "open" not in _bar_for_fill:
    continue          # not yet fillable; re-evaluated on this symbol's next bar
bar_open_price = _bar_for_fill["open"]
```

This needs a small holding queue so the signal isn't consumed-and-dropped by the
`signal_idx += 1` at `:569`; that is why I have not applied it (it is not a one-liner).

---

### F-2 — `bfill()` in the anchored-VWAP session groups can pull a value backwards in time

**File:** `backend/strategies/strategy_vwap/engine.py:90`
**Severity:** LOW · **Direction:** would INFLATE · **Confidence:** HIGH on mechanism,
HIGH that it is currently inert

```python
vwap_vals.loc[group_idx] = (cum_tp_vol / cum_vol).ffill().bfill()
```

The cumulative sums themselves are strictly causal (`:84-85`) and the session grouping
(`:78`) is correct. `.ffill()` within a group is safe. `.bfill()` is not: if the first bars
of a session produce NaN, `bfill` fills them from a **later** bar in the same session —
a bar in their future.

The only way to get NaN at a session start is `cum_vol == 0` at the first bar, i.e. a
zero-volume opening bar. Its VWAP would then be taken from whenever volume first appears.

**Why it is currently inert:** `mt5.copy_rates_*` returns columns
`time, open, high, low, close, tick_volume, spread, real_volume`. There is no column named
`volume`, so the guard at `:60` (`if "volume" in candles.columns`) is False and the branch
at `:64-65` runs: `vol = pd.Series(1.0, ...)`. `cum_vol` is then `1, 2, 3, …` and can never
be zero, so no NaN is ever produced and `bfill` never fires. (This also means the "VWAP" in
these runs is an unweighted cumulative average price, not a volume-weighted one — a
separate accuracy question, not a temporal one.)

The bug arms itself the moment anyone renames `tick_volume` to `volume` on ingest.

**Fix.** Drop the `.bfill()`; leave leading NaN as NaN and let the caller's guards handle it:

```python
vwap_vals.loc[group_idx] = (cum_tp_vol / cum_vol).ffill()
```

---

### F-3 — The backtest data path never strips the in-progress final bar

**Files:** `backend/mt5/data_fetcher.py:236-240` (`get_data_range` — no trim),
`backend/backtester/engine.py:719` (`for i in range(len(candles))`)
**Severity:** VERY LOW · **Direction:** INFLATES or DEFLATES (random) ·
**Confidence:** HIGH

`get_data_range` trims to `[start_ts, end_ts]` but does nothing about a final bar whose
open is inside the range while its close is still in the future. Your runs used
`end_date = 2026-08-21`, i.e. today, so the last M5 bar in each fetch is very likely a
partial bar.

The *strategies* are immune: the harness slice `iloc[tf_start_idx:i]` never reaches
`len-1`, so the final bar is never presented as closed. But the *engine's* bar loop runs to
`len(candles)-1`, so SL/TP resolution, MAE/MFE, trailing and the END_OF_DATA force-close can
all transact against a bar with an incomplete high/low.

**Blast radius: one bar per run.** Negligible, but it is a real "not stripped" answer to
the brief's question.

**Fix.** In `get_data_range`, drop a trailing bar whose `time + tf_seconds > now`.

---

## 3. FINDINGS — measurement defects that make the saved numbers fiction

These are not look-ahead. They are the reason your reports disagree with your P&L.

### M-1 — [FIXED] Group-level `stop_loss` is the *mutated* stop, so every R metric is inflated

**File:** `backend/utils/trade_grouper.py:82` (before fix) → now `:97,107`
**Severity:** CRITICAL · **Direction:** INFLATES (massively) · **Confidence:** CERTAIN
(proven in code and measured in your data)

`pos["stop_loss"]` is mutated in place by break-even and trailing
(`engine.py:903`, `portfolio_engine.py:445`). `group_trades` seeded the group dict from the
first leg **to close**, taking that leg's *final* stop:

```python
"stop_loss": t.get("stop_loss", 0),      # ← the moved stop, not the entry stop
```

Everything downstream divides by `abs(entry_price - stop_loss)`:
`realized_rr`, `max_rr_achieved`, `planned_rr`, `pnl_r` (`trade_grouper.py:174-243`) and
`expectancy_r`, `avg_win_r`, `best_trade_r`, `total_pnl_r`
(`analytics/reports.py:113-131`, which reads the same group-level key). Once BE has moved
the stop to ≈ entry, the risk distance collapses and R explodes.

**Worked example — `debug/apa/cadjpy_sf_off.json`, group `e220c127`:**

| quantity | value |
|---|---|
| signal SL (entry-time, `original_signal.stop_loss`) | 113.512 |
| fill (next bar's open) | 113.622 |
| **true risk distance** | 0.110 = **11.0 pips** |
| TP1 leg's stop after BE (`be_applied: true`) | 113.632 |
| **risk distance the report used** | 0.010 = **1.0 pip** |
| `realized_rr` reported | **20.532R** |
| `max_rr_achieved` reported | 26.357R |
| `planned_rr` reported | 61.0R |
| **true volume-weighted R** (legs 1.727R×1.25, 2.396R×0.74, 1.422R×0.49) | **+1.866R** |

An 11× inflation on a trade that made $264.

**Corpus-wide:** 479 of 4,380 groups (10.9%) carried a mutated stop. Effect on the R
distribution, measured before vs after correcting the stop:

| strategy | groups | mutated | avg win (reported → correct) | max R (reported → correct) |
|---|---|---|---|---|
| apa | 310 | 8.7% | +3.92R → **+1.69R** | 110.6 → **7.8** |
| biaskeylevelifvg | 645 | 9.3% | +8.92R → **+1.73R** | 190.9 → **7.2** |
| crt | 119 | 8.4% | +4.73R → **+1.73R** | 75.0 → **6.9** |
| htffvgflip | 474 | 10.8% | +12.10R → **+1.68R** | 239.2 → **5.6** |
| nyopenretest | 474 | 9.7% | +5.31R → **+1.99R** | 109.1 → **10.4** |
| vwap | 2358 | 12.1% | +6.45R → **+1.76R** | 259.5 → **18.3** |

A max of 239R on a 1.5/3/5-R TP grid is prima facie impossible; that number alone should
have been the tripwire. Note also that a BE_SL exit, measured against the BE stop, lands at
exactly ±1.00R by construction — the same "exactly +1.00R" signature that
`_breakeven_stop`'s docstring attributes to the (separately real, separately fixed) phantom-gap
P&L bug. **Two independent mechanisms produced that tell**, and only one of them had been
found. Dollar P&L is unaffected by M-1.

**Fix applied** at `trade_grouper.py:97,107`: prefer `original_signal["stop_loss"]`, which
is never mutated, falling back to the leg's value only when the signal is absent. Reporting
only — simulated P&L is byte-identical.

---

### M-2 — [FIXED] `expectancy_r` has a sign error and can never be negative

**File:** `backend/analytics/reports.py:329` (before fix) → now `:338`
**Severity:** CRITICAL · **Direction:** INFLATES · **Confidence:** CERTAIN

```python
loss_r = [r for r in r_values if r <= 0]          # :131 — ALREADY NEGATIVE
...
expectancy_r = (win_rate * avg_win_r) - ((1 - win_rate) * avg_loss_r)   # :329 (pre-fix)
```

Subtracting an already-negative average *adds* the losses. With `wr=0.40`,
`avg_win=+2.0R`, `avg_loss=-1.0R`, the correct expectancy is `0.4(2.0) + 0.6(-1.0) = +0.20R`;
the code returned `0.4(2.0) - 0.6(-1.0) = +1.40R`. The expression is structurally incapable
of returning a negative value whenever `avg_win_r > 0`.

**Worked example.** `debug/apa/cadjpy_sf_off.json` reports
`expectancy_r = +1.7318` and `total_pnl = -$3,612.58` in the same object. Every losing run
in the corpus reports a large positive expectancy: btcusd +1.11, cadjpy_sf_on +2.36,
gbpjpy +3.50, hk50 +1.79, spx500 +8.41.

**Combined effect of M-1 + M-2**, per strategy (mean R per group, best-leg basis):

| strategy | as reported | after both fixes |
|---|---|---|
| apa | +2.42R | +0.22R |
| biaskeylevelifvg | +4.59R | +0.23R |
| crt | +2.81R | +0.23R |
| htffvgflip | +5.55R | +0.08R |
| nyopenretest | +2.95R | +0.34R |
| vwap | +3.58R | +0.30R |

**Fix applied**: `+ ((1 - win_rate) * avg_loss_r)`.

---

### M-3 — [NOT FIXED] Report-level R uses the *best leg's* exit price, not a volume-weighted one

**Files:** `backend/utils/trade_grouper.py:144` (`g["exit_price"] = g["best_exit"]…`),
consumed by `backend/analytics/reports.py:116`
**Severity:** HIGH · **Direction:** INFLATES · **Confidence:** HIGH

`generate_risk_report` treats each group as one trade and reads `t["exit_price"]`, which
`trade_grouper` sets to the exit price of whichever leg had the **highest P&L**. For a
50/30/20 three-TP group where TP1 banks +1.5R and TP2/TP3 later stop out at break-even, the
group's R is recorded as TP1's +1.5R, ignoring 50% of the volume.

`trade_grouper` already computes the correct figure — `realized_rr` (`:217`) is properly
volume-weighted — the report just doesn't use it.

**Measured** (both series computed on the corrected entry-time stop, so this isolates M-3
from M-1): mean R per group, best-leg basis vs volume-weighted basis —

| strategy | best-leg R | volume-weighted R | overstatement |
|---|---|---|---|
| apa | +0.219 | **+0.046** | +0.17R |
| biaskeylevelifvg | +0.235 | **+0.077** | +0.16R |
| crt | +0.227 | **+0.013** | +0.21R |
| htffvgflip | +0.084 | **−0.023** | +0.11R |
| nyopenretest | +0.344 | **+0.001** | +0.34R |
| vwap | +0.298 | **+0.046** | +0.25R |

The volume-weighted column is the one that reconciles with the dollar P&L (all six
strategies lose money; the R win rates 0.41–0.47 match the dollar win rates 0.37–0.46 to
within 1–4 points, the gap being transaction costs). **This column is your true expectancy,
and it is within ±0.08R of zero for every strategy — exactly what you had independently
concluded.** That is now confirmed from the raw leg data.

**Fix.** In `generate_risk_report`, prefer the pre-computed volume-weighted figure:

```python
r_val = t["realized_rr"] if t.get("realized_rr") is not None else <existing computation>
```

Not applied: it changes every saved report's headline number and you should re-run
deliberately.

---

### M-4 — [NOT FIXED] `original_sl` is never written, so the trailing-activation gate is defeated after break-even

**File:** `backend/risk/engine.py:346` (read), `:388` (used); never set by either engine
**Severity:** HIGH · **Direction:** DEFLATES · **Confidence:** HIGH

```python
original_sl = position.get("original_sl", current_sl)     # :346
...
risk_distance = abs(entry - original_sl)                  # :388
unrealized_r  = (current_price - entry) / risk_distance
if unrealized_r < self.config.get("trail_activation_rr", 1.0):
    return actions
```

`grep -rn "original_sl" backend/` finds **no writer** in `backtester/engine.py`,
`backtester/portfolio_engine.py`, or anywhere else that builds a position dict. The key
never exists, so `original_sl` always degrades to `current_sl` — the *live, mutated* stop.

On the first call that is harmless (the stop is still the entry stop). But once BE fires,
`current_sl ≈ entry`, so `risk_distance` collapses to the BE buffer (with your
`be_buffer_pips: 0`, that is `max(spread, 1 pip)` ≈ 0.1–1 pip), `unrealized_r` becomes
enormous, and the `trail_activation_rr` gate passes unconditionally on the very next bar.

**Worked example (same group `e220c127`).** `be_trigger_rr = 1.5` equals `tp1_rr = 1.5`, so
BE and TP1 fire together. On the next bar the TP2 leg's `risk_distance` is
`|113.622 − 113.632| = 1.0 pip` instead of 11.0, `unrealized_r` reads ~19 instead of ~1.7,
and `ATR_TRAIL` engages immediately. That leg exited `TRAIL_SL` at 113.886 rather than
running to its 113.992 target. Across the corpus **TRAIL_SL is the second most common leg
exit (2,340 of 13,024 legs, 18%)**, ahead of TP1 (1,760).

The same fallback also feeds `check_breakeven(stop_loss=original_sl, …)` (`:372`) and the
ATR fallback `abs(entry - original_sl) * 0.1` (`:376`).

**Fix** (one line per engine — both engines already have the value in hand):

```python
# backend/backtester/engine.py, in _create_position()'s returned dict (~:1357)
"stop_loss": sig.get("stop_loss", 0),
"original_sl": sig.get("stop_loss", 0),     # ← never mutated; risk-distance reference

# backend/backtester/portfolio_engine.py, in new_pos (~:771)
"stop_loss": sig["stop_loss"],
"original_sl": sig["stop_loss"],
```

Not applied: unlike M-1/M-2 this **changes simulated P&L** on every trade that reaches BE.
It will make trailing activate later, which should let winners run further. Deliberately
left to you.

---

### M-5 — [NOT FIXED] Portfolio engine stamps `entry_time` with the *signal* time, not the fill time

**File:** `backend/backtester/portfolio_engine.py:767-768`
**Severity:** MEDIUM · **Direction:** neutral on P&L, corrupts time-keyed metrics ·
**Confidence:** HIGH

```python
"entry_time": sig_time,
"entry_time_iso": _epoch_to_iso(sig_time),
```

The fill happens at `current_time` with `bar_open_price`, but the record says the trade
opened one bar earlier, at a timestamp at which that price did not exist.
`BacktestEngine._create_position` does this correctly (`engine.py:1112` passes
`current_time`). Consequences in the portfolio path: `duration_minutes` inflated by one
bar; `entry_session` derived from the wrong instant (`:723`); `_swap_cost` given a start
that can be on the other side of a rollover boundary (`engine.py:516-522`).

**Fix:** `"entry_time": current_time` (and the same for `entry_time_iso` /
`detect_session`). Not applied — I did not want to change portfolio-run outputs mid-audit.

---

### M-6 — [NOT FIXED] Live and backtest dispatch signals differently

**Files:** `backend/services/bot_service.py:519-531` vs `backend/api/routes/backtest.py:506-533`
**Severity:** MEDIUM · **Direction:** unquantified · **Confidence:** HIGH

Three divergences, none of which is look-ahead but all of which mean "what you backtest"
is not "what runs live":

1. **Signal selection.** Backtest: `for tf in required_tfs: s = await on_bar(...); if s: sig = s`
   — *any* timeframe may produce the signal. Live: `if i == len(req_tfs) - 1: signal = res`
   — only the **last** timeframe's return is kept, and it is assigned even when `None`,
   discarding an earlier TF's signal. For APA (`["M15","M5"]`) the entry TF is last so they
   agree; for any strategy whose emitting TF is not last they do not.
2. **Call frequency.** Backtest calls a TF's `on_bar` exactly once per newly-closed bar
   (the `last_tf_time != prev_time_by_tf[tf]` guard, `:529`). Live calls **every scan
   cycle** with the same closed bar. Only `strategy_three_crt` defends against this
   (`engine.py:130-134`); the stateful state machines in APA, BiasIFVG, HTFFVGFlip,
   NYOpenRetest and VWAP will re-run their transitions repeatedly within one bar.
3. **Unclosed bars in live position management.** `position_manager.py:707,810` fetch
   candles without the `iloc[:-1]` strip, so live trailing structure and live ATR repaint
   within the forming bar while the backtest's do not.

---

### M-7 — [NOT FIXED, currently inert] `MarketStructureDetector._last_bos_index` drifts because the slice window slides

**File:** `backend/strategies/core/market_structure.py:150` (and `:181`)
**Severity:** LOW (latent) · **Direction:** would INFLATE signal count ·
**Confidence:** HIGH on mechanism, HIGH that it is currently unread

```python
if self._last_bos_index != high_swings[-1]["bar_idx"]:
```

`bar_idx` is `int(i)` — a **positional** index into the slice passed to `update()`
(`:84`). The harness passes a rolling window (`iloc[i-window : i]`), so once the window
reaches its cap the same physical swing's `bar_idx` decreases by 1 every bar. The dedup
comparison therefore always reports "different", and BOS re-fires on every bar for as long
as price stays beyond `curr_sh`, incrementing `consecutive_bos` without bound and setting
`trend_confirmed = True` almost immediately.

**Why it is inert:** `grep -rn "trend_confirmed|consecutive_bos|bos_history|last_bos"`
across `backend/` finds no consumer outside the detector itself. The only field any
strategy reads is `get_bias()` → `self.trend`, which is set exclusively in the ChoCH branch
and is unaffected by this dedup. CRT and HTFFVGFlip both use only `get_bias()`.

**Fix:** key the dedup on the swing's absolute timestamp (`s["time"]`, already populated at
`:83`) instead of its positional `bar_idx`.

---

## 4. CHECKED AND CLEAN — negative results, rigorously established

Each of these was traced to specific lines and the arithmetic checked, not skimmed.

### C-1 — Vectorised swing-point cache (`engine.py:700-716`, `portfolio_engine.py:226-238`)

```python
for i in range(swing_lookback, len(candles)):
    for j in range(max(sw_len, i - swing_lookback), i - sw_len):
        window_h = highs_arr[j - sw_len : j + sw_len + 1]
```

**Proof.** The inner range is half-open, so `j_max = i - sw_len - 1`. The highest index the
window touches is `j_max + sw_len = i - 1`. **Every bar contributing to `swing_cache[i]` has
index ≤ i-1** — strictly in the past — for any non-negative `sw_len`. The right-hand
confirmation bars a fractal needs are all bars the loop has already passed. This is exactly
the textbook-correct construction and it is correct in **both** engines (identical
arithmetic). `sw_len` resolves to 5 in your runs (`trail_structure_bars` and `swing_length`
are both absent from every `params_snapshot`).

Consumed by `STRUCTURE_TRAIL`, which your VWAP runs use on TP3 — so this mattered, and it
is clean.

### C-2 — Precomputed ATR (`engine.py:696-698`)

```python
atr_array[i] = np.mean(tr_all[i - atr_period : i])
```

Half-open: uses `tr[i-14] … tr[i-1]`, **excluding** bar `i`. Strictly causal, in fact one
bar *more* conservative than necessary. `tr_all` itself uses `np.roll(closes,1)` with
`prev_closes[0] = closes[0]` — backward-looking only.

`portfolio_engine.py:212` uses `tr_s.rolling(14, min_periods=1).mean()`, a trailing window
that **includes** bar `i`. That is still legitimate — `tr[i]` is fully known at bar `i`'s
close, and the resulting SL change is only applied from bar `i+1` (see C-6) — but it is an
**engine-vs-engine divergence**: the same run scored through the two engines will produce
slightly different trailing stops. Worth unifying; not a leak.

### C-3 — Fractal / swing detectors

- **`swing_structure.detect_swings` (`:50-70`).** For each `shift in 1..fractal_m` the
  right-side comparator is `right_high[:-shift] = highs[shift:]` with the tail initialised
  to `+inf`, so `highs > inf` is False at the tail; then `is_high[-fractal_m:] = False`
  explicitly. **A fractal at bar `j` requires all `fractal_m` bars to its right to exist
  and be lower.** Correct confirmation semantics; the most recent `fractal_m` bars can never
  be swings. Used by APA for both the minor (H&S) and major (BOS filter) swing sets.
- **`market_structure.MarketStructureDetector.update` (`:47-51`).**
  `candles['high'].rolling(window=2*swing_length+1, center=True).max()` with pandas' default
  `min_periods = window` yields NaN for the first and last `swing_length` positions, and
  the code additionally guards with `if not pd.isna(high_roll_vals[i])` (`:78`). So the
  latest confirmable swing sits at most `swing_length` bars back, and the BOS/ChoCH test
  (`latest_close` vs `high_swings[-1]["price"]`, `:132`) compares the current closed bar's
  close against a swing that was fully confirmed before it. **No hindsight swing.**

This was the highest-prior-probability place for a leak in an ICT/SMC codebase. It is clean.

### C-4 — Multi-timeframe alignment (`backtest.py:517-524`, `:985-991`)

```python
cutoff  = current_time - np.timedelta64(np_td, np_unit)      # t − one HTF period
tf_end  = int(np.searchsorted(tf_times, cutoff, side='right'))
slice_tf = sorted_tf.iloc[tf_start_idx:tf_end]
```

`tf_times` are HTF bar **open** times. `searchsorted(..., side='right')` returns the index
just past the last open `≤ cutoff`, so the slice's last bar has `open ≤ t − period`, hence
`close = open + period ≤ t`. **The HTF bar supplied is always one that had closed at or
before the M5 bar's timestamp** — never the HTF bar containing `t`.

Checked at boundaries: M5 `t = 10:05`, H1 → cutoff `09:05` → bar `09:00` (closed 10:00) ✓.
`t = 10:00` exactly → cutoff `09:00` → bar `09:00`, which closed at 10:00 ✓ (tight, not
early). `t = 10:55` → cutoff `09:55` → bar `09:00`, correctly refusing the still-forming
`10:00` bar ✓. Same construction verified for M15 (`t=10:15` → cutoff `10:00` → bar
`10:00`, closed at 10:15 ✓) and H4 (`t=10:05` → cutoff `06:05` → bar `04:00`, closed at
08:00, with `08:00–12:00` correctly withheld ✓).

The proof generalises to any TF whose bar duration equals `TF_META[tf]["np_td"]`, and gaps
(weekends, holidays) only ever make it *more* conservative. This covers NY Retest's M15
range → M5 trade, HTF FVG Flip's H1 FVG → M5 entry, CRT's H1/M5, and BiasIFVG's H4/M15/M5.
**Identical code in the single-symbol and portfolio harnesses.** Clean.

### C-5 — Same-bar entry

Established in §1: the gate is `if sig_time >= current_timestamp: break`, so a signal
stamped with bar `i`'s time cannot be actioned before bar `i+1`, and the fill is
`opens_arr[i+1]` — bar `i+1`'s **open**, never its close and never any intra-bar price.
Identical in `portfolio_engine.py:564-568`. **No strategy path bypasses it**: strategies
return a `TradeSignal`; only the engine opens positions; `_create_position` overwrites
`entry_price` with `bar_open_price` unconditionally (`engine.py:1264`) and keeps the
strategy's theoretical price only inside `original_signal` and the confirmations text.

Empirically confirmed on all 4,380 groups: `entry_time − original_signal.time = 300s`
(exactly one M5 bar) in 4,374 cases; the 6 exceptions are 3,000–3,900s, i.e. session gaps.
**Zero same-bar fills.**

Second empirical check — is the fill systematically *favourable* versus the strategy's
theoretical entry (the tell for a fill chosen with foresight)? Signed, in units of R:

| strategy | n | mean | median | favourable / adverse |
|---|---|---|---|---|
| apa | 310 | −0.025R | +0.000R | 152 / 152 |
| biaskeylevelifvg | 645 | −0.011R | −0.004R | 308 / 329 |
| crt | 119 | −0.105R | −0.067R | 52 / 67 |
| htffvgflip | 474 | +0.002R | +0.011R | 260 / 210 |
| nyopenretest | 474 | −0.068R | −0.067R | 206 / 268 |
| vwap | 2358 | −0.033R | +0.006R | 1188 / 1160 |

Symmetric to mildly adverse. CRT and NY Retest are systematically adverse because both
emit limit-style theoretical entries (`range_mid`, the C2 trigger level) that are then
filled at market a bar later. **Nothing here resembles foresight.**

Third empirical check — first-bar outcomes. A look-ahead engine shows implausibly good
first-bar results. Legs with `bars_held == 1`:

| strategy | n | win rate | exit mix |
|---|---|---|---|
| apa | 47 | 0.234 | SL 36, TP1 11 |
| biaskeylevelifvg | 18 | 0.000 | SL 18 |
| crt | 75 | 0.160 | SL 63, TP1 12 |
| htffvgflip | 9 | 0.000 | SL 9 |
| nyopenretest | 205 | 0.122 | SL 180, TP1 25 |
| vwap | 814 | 0.151 | SL 690, TP1 118, SESSION_END 6 |

**0–23% win rate, 85–100% stop-outs.** The exact opposite of a foresight signature.

### C-6 — Exit-side: SL/TP resolution and BE/trail ordering

`_resolve_sl_tp_hit` (`engine.py:265-313`) receives only `open_p, high, low` of the bar
being evaluated and the position's current `stop_loss`/`take_profit`. **No later bar is
reachable from it.** Same-bar SL+TP ambiguity resolves toward SL if *either* heuristic
favours it — deliberately conservative. `_gap_adjusted_fill_price` uses only that bar's
open. Both engines call the identical shared helper.

The brief's specific worry — a stop updated using a value that already incorporates the
current bar's full range, *before* that bar's own SL check — does **not** occur. Per-position
order in `engine.py`'s loop is:

1. `:765-767` — `highest_price = max(prev, high_of_bar_i)`
2. `:841-848` — SL/TP evaluated against the stop **carried in from bar `i-1`**
3. `:866-892` — close and `continue` if hit
4. `:895-907` — `manage_open_position(...)` → any `MODIFY_SL`

The trail can only see bar `i` *after* bar `i` has already been resolved against the old
stop, so a new stop derived from `highest_price` or `current_atr` first takes effect on bar
`i+1`. `portfolio_engine.py:384-449` has the identical ordering. The TP1→break-even sibling
block (`:909-931` / `:451-480`) also runs after the exit checks, and the `_tp1_closing_this_bar`
pre-pass exists precisely to stop a sibling being resolved on the bar its BE has not yet
been applied. **Clean.**

Empirically: SL legs exit at exactly their stop 93–99% of the time and TP legs at exactly
their target, with the residual being gap fills — the expected signature of level-based
resolution, not of foresight. (It is *optimistic* in assuming a stop always fills at its
exact level, but that is a fill-realism assumption, not a temporal one.)

### C-7 — `original_signal` is not recomputed from end-of-trade state

`sig` is stored by reference at `engine.py:1374` / `portfolio_engine.py:795` and the engine
never writes back into it. The only keys the engine adds are `group_id` (`:1045`). Its
`entry_price` / `stop_loss` / `take_profit` are the strategy's originals and are what let
me measure M-1 and C-5 at all. `_slim_sub_trades` (`runner.py:38-58`) strips it from
*persisted per-leg* records only; the group-level copy survives.

The one thing that *was* being recomputed from end-of-trade state is the group's
`stop_loss` — that is M-1, and it is fixed.

### C-8 — Other shared detectors

- **`fvg.py`** — mitigation uses `candles.iloc[-1]` (last closed); new gaps are read from
  `iloc[-3], iloc[-2], iloc[-1]`, and a bullish gap requires `c3.low > c1.high`, fully known
  at `c3`'s close. Stateful `active_fvgs` accumulate forward only. Clean.
- **`liquidity.py`** — pools built from already-confirmed swings; sweep test uses only
  `candles.iloc[-1]`'s high/low/close. Clean.
- **`order_blocks.py`**, **`supply_demand.py`** — index backwards only
  (`iloc[-2]`, `iloc[-4:-1]`); over-conservative, never forward. Clean.
- **`ipdm.py`** — `tr.rolling(14).mean().iloc[-1]`, trailing; all `.iloc[-1]`/`.iloc[-10:]`
  references backward. Clean.
- **`asian_range.py`** — scans backwards from the last closed bar and breaks on leaving the
  session. Clean.
- **`premium_discount.py`** — pure arithmetic on levels passed in. Clean.
- **`candlestick.py`** — all detectors index `idx = n-1-offset` with `avg_body(candles.iloc[:idx])`
  (strictly prior bars). Clean, and **dead**: `grep` finds no caller in any strategy.

### C-9 — Indicator warm-up and rolling windows

- `strategy_two/engine.py:42-66` — `calculate_atr` / `calculate_adx` use `.shift(1)` and
  `.rolling(period).mean()` only; trailing and causal. (`df['atr'] = calculate_atr(df,…)`
  correctly assigns a **Series** here because this module shadows the scalar-returning
  `swing_structure.calculate_atr` with its own — worth knowing, since assigning the scalar
  version would have back-filled the current ATR onto every historical bar.)
- `swing_structure.calculate_atr` (`:20`) — `candles.iloc[-(lookback+1):]`, trailing.
- VWAP momentum (`strategy_vwap/engine.py:360`) — `candles["close"].iloc[-(actual_lookback+1)]`,
  a past bar; VWAP slope (`:283`) — `vwap_series.iloc[-(bar_multiplier+1)]`, a past bar.
- VWAP anchored cumulative sums — causal within each session group (F-2 covers the `bfill`).
- No `.shift(-n)` anywhere in `backend/strategies/` or `backend/backtester/`.

### C-10 — Per-strategy consumption of the closed-bar convention

| strategy | entry price source | verdict |
|---|---|---|
| APA | `latest["close"]` (`:395`) | closed bar ✓ |
| BiasIFVG | `latest["close"]` (`:526`) | closed bar ✓ |
| HTF FVG Flip | `latest["close"]` (`:335`) | closed bar ✓ |
| CRT | `current_bar['close']` (`:199`) | closed bar ✓ |
| NY Open Retest | `state["range_mid"]` (`:105`) | past level ✓ (see note) |
| VWAP | `latest["open"]` (`:295`) | closed bar's open ✓ (see note) |
| DriftJumpAlpha | `current_bar` from `df.iloc[-1]` | closed bar ✓ |

Two notes, neither a leak:

- **NY Retest** `dynamic_target_override` (`:178-192`) takes `candles.iloc[-50:]["high"].max()`
  — the last 50 **closed** bars, a past resistance level, not a future extreme. I checked
  this specifically because "use the nearer swing" phrasing is a common place to reach
  forward. It does not. `breakout_extreme` (`:91,96`) likewise reads `iloc[-10:]`.
- **VWAP** intends "enter at the bar after the trigger" but computes `entry = latest["open"]`,
  where `latest` is the last *closed* bar — an open that is already in the past by the time
  the bar closed. The engine ignores this for the actual fill (it uses bar `i+1`'s open), so
  the effect is a ~2-bar gap between the price the SL/TP grid was built around and the price
  actually filled. Distorting, conservative, not look-ahead.

### C-11 — Costs are real in these runs

Not a temporal question, but relevant to "is this fiction": `cost_model` in each file shows
non-zero broker-sourced costs (EURUSD: 0.1p spread, 0.4p slippage, $7/lot commission,
−13.47 swap long), sourced `MT5_PARTIAL`. These runs were **not** zero-cost runs.

### C-12 — Break-even fabrication is genuinely gone from these runs

`_breakeven_stop`'s docstring documents a prior bug where 100% of BE_SL legs exited at
exactly +1.00R. Measured on the corrected (entry-time) stop, BE_SL legs now show a healthy
near-zero distribution with real variance: mean +0.046R (apa, n=44), +0.074R (ifvg, n=85),
+0.119R (crt, n=35), +0.084R (htffvgflip, n=43), +0.122R (nyopen, n=178), +0.085R (vwap,
n=649), with no modal value and no clustering at 1.00. **The clamp works.** (See M-1 for
why the *reported* R still showed ±1.00R on those legs.)

---

## 5. Could not be determined statically

- **O-1 — Cost of the skipped fill bar.** The position is appended to `open_positions`
  (`engine.py:1127`) *after* that bar's management loop has run, so bar `i+1` — the bar the
  trade fills on — is never evaluated for SL/TP and never contributes to MAE/MFE. Verified
  in the data: minimum leg duration is exactly 300s across all 13,024 legs and there are
  zero same-bar exits. Since SL sits closer than TP1 (1.5R grid), skipping this bar
  disproportionately skips stop-outs, so the bias is toward **INFLATION** — but quantifying
  it needs the OHLC bars, which are not in the saved JSON (`chart_data` is empty in all 61
  readable files).
- **O-2 — Broker server-time vs UTC.** `_normalize_df` (`data_fetcher.py:48-60`) leaves an
  integer `time` column untouched, and MT5 returns epochs in *server* time. If the broker
  runs UTC+2/+3 (typical), every timestamp is shifted, and every session gate — VWAP's
  09:30/15:55 ET, NY Retest's 08:00/09:30 ET, CRT's and APA's session windows — fires 2–3
  hours off. This is unverifiable without a live terminal, but it is the single highest-value
  thing to check before optimising anything session-gated.
- **O-3 — Whether live re-entrancy corrupts strategy state.** M-6(2) is a certain
  divergence; whether it produces duplicate or missing signals depends on per-strategy
  state-machine idempotence, which I could not test without executing the code (pandas is
  blocked in this environment).
- **O-4 — Realised effect of M-4.** Fixing `original_sl` will change trailing behaviour on
  every trade that reaches BE; the magnitude needs a re-run.
- **O-5 — Portfolio-engine behaviour generally.** F-1 and M-5 are read from code only; no
  portfolio run exists in `debug/` to check them against.
- **O-6 — `debug/apa/usdchf_session-filter_on.json` is not valid JSON** (empty or truncated;
  `JSONDecodeError` at char 0). 61 of 62 files parsed. Worth regenerating.

---

## 6. Changes applied

Both are **reporting-only**; simulated P&L, trade lists and equity curves are byte-identical.
Both compile (`python -m py_compile`).

| file | change |
|---|---|
| `backend/utils/trade_grouper.py:97,107` | Group `stop_loss` now taken from `original_signal["stop_loss"]` (entry-time, never mutated) instead of the closed leg's final, BE/trail-moved stop. Fixes M-1. |
| `backend/analytics/reports.py:338` | `expectancy_r` now **adds** the (already negative) average loss instead of subtracting it. Fixes M-2. |

**Left deliberately unapplied**, with exact patches given above: F-1, F-2, F-3, M-3, M-4,
M-5, M-7. M-4 and M-3 are the two that matter most and both change numbers.

---

## 7. Recommended order of work

1. **Re-run one strategy** and confirm `expectancy_r` now goes negative for losing runs.
   This is the sanity check that the reporting layer is trustworthy again.
2. **Apply M-3** (use `realized_rr`). Until then the report's R is the best leg's R, worth
   +0.11 to +0.34R per trade of pure overstatement.
3. **Resolve O-2** (server-time offset). If the sessions are off by hours, every
   session-gated strategy is being evaluated on the wrong hours of the day and no
   optimisation over session parameters means anything.
4. **Apply M-4** (`original_sl`) and re-run. This is the one open defect likely to *improve*
   results, by letting trails activate when they were configured to.
5. Only then optimise. The true volume-weighted expectancy — apa +0.046R, ifvg +0.077R,
   crt +0.013R, htffvgflip −0.023R, nyopen +0.001R, vwap +0.046R — confirms your
   independent read: **these are mechanics-dominated results with no measurable edge, and
   look-ahead is not the reason.**
