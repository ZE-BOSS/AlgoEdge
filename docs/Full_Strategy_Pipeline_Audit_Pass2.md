# AlgoEdge — Full Strategy & Risk Pipeline Audit (Pass 2\)

This expands on `Strategy_Zero_WinRate_Audit.md`. That report covered the five bugs that fully explain zero trades for every strategy except `DriftJumpAlpha_v1`. This pass goes wider: the shared risk/execution pipeline (used by *every* strategy, including the one that's currently trading) and each engine individually, looking for anything that would cause **wrong** behavior, **backtest/live divergence**, or **silent misconfiguration** — not just zero trades.

Nothing here contradicts Pass 1\. Some items below are the same root causes already flagged in your memory notes (parity gap, `be_buffer_pips` mismatch, `check_symbol` dead code) — they're restated here with the exact mechanism now traced, since this pass went deep enough to pin down precisely *how* each one breaks.

---

## Part 1 — Shared risk/execution pipeline (affects multiple or all strategies)

These are the highest-value findings in this pass: bugs in code every strategy shares, so fixing them moves every strategy at once — but they also mean a strategy that looks fine in backtest can behave differently live, or vice versa.

### 1.1 Position sizing uses a different balance in backtest vs. live (parity break)

**File:** `backend/risk/engine.py`, `RiskEngine.evaluate_signal`

base\_balance \= account\_balance

if hasattr(self, "prop\_firm\_validator") and self.prop\_firm\_validator and self.prop\_firm\_validator.enabled:

    base\_balance \= self.prop\_firm\_validator.initial\_balance

elif not self.compounding\_enabled and initial\_balance is not None:

    base\_balance \= initial\_balance

When compounding is off (the default), sizing uses `base_balance`, and which value that resolves to depends entirely on whether `initial_balance` was passed in:

- **Backtest** (`backend/backtester/engine.py`, `BacktestEngine.run`) calls `evaluate_signal(..., initial_balance=initial_balance)` — the **fixed starting balance of the whole backtest run**, never updated. So every single trade for the entire backtest is sized off day-one capital, regardless of how much the account has grown or drawn down.  
- **Live** (`backend/services/bot_service.py`) calls `risk_engine.evaluate_signal(signal_data, account_balance, compounding_risk_dollars=compounding_risk)` — **no `initial_balance` argument at all**, so it defaults to `None`, the `elif` branch is skipped, and `base_balance` falls through to `account_balance` — the **current live balance**.

Net effect: with compounding disabled, backtest sizing is flat against the starting balance for the whole run, while live sizing naturally compounds with the current balance every trade. Same `risk_per_trade_pct` setting, two different behaviors — a direct contradiction of the "what you backtest is what runs live" principle. This applies to every strategy, not just the broken ones.

### 1.2 Live trading's Compounding toggle has zero effect

**File:** `backend/services/bot_service.py`, inside the trade-execution block

risk\_config \= {

    ...

    "compounding\_enabled": config.risk.compounding\_enabled if hasattr(config.risk, 'compounding\_enabled') else False,

    ...

}

`RiskParams` (the class of `config.risk`) has no `compounding_enabled` field — compounding lives on a completely different object, `config.compounding.enabled` (a `CompoundingParams` instance, top-level on `UserConfigV2`). `hasattr(config.risk, 'compounding_enabled')` is therefore always `False`, so this line **always** evaluates to `False`, no matter what the user has toggled in Settings → Risk → Compounding.

Inside `RiskEngine.evaluate_signal`, the compounding-dollar sizing path is gated behind exactly this flag:

if self.compounding\_enabled and compounding\_risk\_dollars \> 0 and base\_balance \== account\_balance:

    total\_lots \= calculate\_lot\_from\_dollars(...)

else:

    total\_lots \= calculate\_lot\_size(base\_balance, self.risk\_pct \* size\_modifier, entry, sl, symbol)

Since `self.compounding_enabled` is always `False` in live, the `else` branch always runs — **live trading never uses the stepped-dollar compounding plan, ever, regardless of user configuration.** It silently always behaves as plain percentage risk.

Compare to `backend/api/routes/backtest.py`, where the equivalent field is built correctly:

merged\_risk\_config \= {

    ...

    "compounding\_enabled": req.compounding\_enabled,   \# sourced directly from the request body

    ...

}

So **backtests correctly honor the compounding toggle, live trading silently never does.** If you've compared live results against a backtest that had compounding on, this is why they diverge — live was never compounding to begin with.

**Fix:** change the `bot_service.py` line to `config.compounding.enabled if hasattr(config, 'compounding') and config.compounding else False`.

### 1.3 `be_buffer_pips` is applied as an ATR multiplier — confirmed mechanism

**File:** `backend/risk/breakeven_manager.py`

self.be\_atr\_multiplier \= config.get("be\_buffer\_atr\_mult", config.get("be\_buffer\_pips", 0.1))

`RiskParams` does define both fields correctly (`be_buffer_pips: float = 2.0` marked deprecated, `be_buffer_atr_mult: float = 0.1` as the real one). The bug isn't in the dataclass — it's that neither `bot_service.py` nor `backtest.py` ever put a `"be_buffer_atr_mult"` key into the flattened `risk_config` dict they hand to `RiskEngine`. Both only ever set `"be_buffer_pips": ...`. So when `BreakevenManager` looks up `config.get("be_buffer_atr_mult", config.get("be_buffer_pips", 0.1))`, the first lookup misses (key not present in the dict), and it falls back to whatever the user set as **pips** (default `2.0`, labeled `"BE Buffer (pips)"` in both `Risk.jsx` and `Backtester.jsx`) — and treats that number as an **ATR multiplier** instead.

A user who sets "2 pips" thinks they're getting a 2-pip breakeven cushion. What they actually get is `2 × ATR` — on a 15–30 pip ATR instrument that's 30–60 pips, not 2\. This is entirely inside `BacktestEngine`'s code path (`RiskEngine.manage_open_position` → `BreakevenManager.check_breakeven`), so it affects every backtested strategy's breakeven behavior.

**Important nuance:** `RiskEngine.manage_open_position` (and this bug) is **only ever called from `BacktestEngine.run`.** Live trading's breakeven logic lives entirely separately in `position_manager.py`, which has its own inline calculation:

be\_atr\_mult \= getattr(risk, 'be\_buffer\_atr\_mult', getattr(risk, 'be\_buffer\_pips', 0.1))

be\_buffer \= be\_atr\_mult \* pip\_size\_val \* 10  \# conservative fallback

Here `risk.be_buffer_atr_mult` *does* exist as a real dataclass attribute (default `0.1`), so this `getattr` succeeds immediately and returns `0.1` — meaning live's formula becomes `0.1 × pip_size × 10` \= exactly `1 pip`, always, regardless of the instrument's actual volatility. It isn't using real ATR at all — `pip_size_val * 10` is a hardcoded "assume ATR ≈ 10 pips" stand-in, not the instrument's measured ATR.

**So you have two different bugs producing two different wrong numbers**, confirming and precisely explaining the previously-flagged "backtest/live breakeven parity gap": backtest's breakeven buffer is `pips_value × ATR` (usually far too wide), live's is a flat, volatility-blind 1 pip (usually far too tight). Neither matches what the "BE Buffer (pips)" field in the UI implies.

### 1.4 STRUCTURE\_TRAIL's swing detection ignores the user's configured swing length — in backtest

**File:** `backend/backtester/engine.py`, `BacktestEngine.run`

sw\_len \= self.risk\_config.get("swing\_length", 5\)

Neither `bot_service.py` nor `backtest.py` ever puts a `"swing_length"` key into the risk\_config dict — the actual user-facing settings are `trail_structure_bars` / `structure_trail_swing_length` (both default `3`). So this always silently falls back to the hardcoded `5`, and the pre-computed swing cache used for `STRUCTURE_TRAIL` in backtest never reflects whatever the user configured in Settings.

Separately, `TrailingManager.__init__` does read the correct fields (`self.swing_length = config.get("swing_length", config.get("trail_structure_bars", 3))`), but this attribute is **never referenced anywhere inside `_structure_trail`** — it's dead code. The actual swing points come pre-baked from `BacktestEngine`'s cache (built with the wrong, hardcoded `5`), and `TrailingManager` just consumes whatever it's handed.

### 1.5 Backtest force-closes trades after 48 hours (or 400 bars); live never does

**File:** `backend/backtester/engine.py`, `BacktestEngine.run`

is\_crashboom \= "CRASH" in symbol\_upper or "BOOM" in symbol\_upper

if is\_crashboom and pos\["bars\_held"\] \>= 400:

    limit\_hit \= True

elif not is\_crashboom and ... (c\_ts \- e\_ts \>= 48 \* 3600):

    limit\_hit \= True

Every backtested trade on a non-Crash/Boom symbol gets force-closed with `exit_reason: "TIME_LIMIT"` if it's still open after 48 hours, no matter which strategy opened it. `position_manager.py` (live position management) has **no equivalent logic anywhere** — breakeven and trailing only, no time-based close. This means:

- Backtest win rate, average trade duration, and drawdown stats for any HTF-leaning strategy (SMC on H4 bias, CRT runners, etc.) are shaped by a cap that doesn't exist in live.  
- A trade that would have eventually hit TP live might get marked a time-limit exit (counted separately, generally as neither a clean win nor loss) in backtest, and vice versa — a trade that runs away against you for days keeps bleeding in live with nothing to stop it, while the backtest would have capped the damage at 48 hours.

### 1.6 `CircuitBreaker.check_symbol` is dead code — no protection against opposite-direction stacking

**File:** `backend/risk/circuit_breaker.py`

def check\_symbol(self, symbol: str) \-\> tuple\[bool, str\]:

    """Check if a new position can be opened on the given symbol.

    Enforces one active signal group per symbol at a time."""

    sym\_open \= self.open\_positions\_by\_symbol.get(symbol, 0\)

    if sym\_open \>= 1:

        return False, f"Already have an open position on {symbol} — waiting for it to close"

    return True, "OK"

This is never called from `RiskEngine.evaluate_signal`, `bot_service.py`, or `BacktestEngine.run` — confirmed dead. The only same-symbol protection that *does* exist is `BacktestEngine.run`'s own check:

already\_open \= False

for p in self.open\_positions:

    p\_is\_buy \= \_is\_buy(p.get("direction", "BUY"))

    if p.get("symbol") \== symbol and p\_is\_buy \== sig\_is\_buy:

        already\_open \= True

This only blocks a **second same-direction** position on the same symbol. It does **not** block opening a long and a short on the same symbol simultaneously (e.g., an SMC bias flip mid-trade, or two strategies assigned to overlapping symbols). Live trading's protection is `max_concurrent_positions`, which counts distinct position *groups* globally, not per-symbol-per-direction — same gap. Confirms and precisely locates the "stacked positions under rapid signal conditions" issue already noted.

### 1.7 Rejection-funnel gate numbering collision makes debugging harder

**File:** `backend/strategies/strategy_one/signals.py`, `TradeGate.validate_all`

\# Gate 7: Must be in active session (if session filter enabled)

if getattr(self.config.smc, "session\_filter\_enabled", False):

    if not context.get("in\_kill\_zone", False):

        reasons.append("Gate 6: Outside active kill zone session")

The comment says Gate 7; the actual rejection string says `"Gate 6: ..."` — colliding with the real Gate 6 (the hard-filter block covered in Pass 1's Bug 1). If you're reading `report.rejection_funnel.strategy_rejections` to diagnose why SMC isn't trading (exactly what this audit recommended), session-filter rejections and hard-filter rejections will both show up labeled `"Gate 6"`, making it look like one cause when it's actually two unrelated ones. Worth a one-line label fix so the funnel is trustworthy once Bug 1 is patched.

### 1.8 Live signal cooldown/dedup never actually triggers

**File:** `backend/services/bot_service.py`, `_scan_loop`

sig\_time \= getattr(signal, 'timestamp', None)

if not sig\_time and hasattr(signal, 'chart\_data') and signal.chart\_data:

    sig\_time \= signal.chart\_data\[-1\].get('time')

if sig\_time and sig\_time \== self.\_last\_signal\_time.get(symbol):

    continue

`TradeSignal` (`backend/strategies/base_strategy.py`) has no `timestamp` field at all — every strategy engine leaves it unset. `getattr(signal, 'timestamp', None)` therefore always returns `None`. The fallback checks `signal.chart_data`, but at this point in the code chart\_data hasn't been injected yet either — that happens a few lines *later* in the same function. So `sig_time` is `None` on every call, the `if sig_time and ...` dedup check never fires, and this cooldown mechanism is currently a no-op. For any strategy whose internal state machine doesn't naturally advance after firing (most do reset their own state, so this is partly self-masking), a signal condition that's still true on the next scan cycle could be evaluated and acted on again.

### 1.9 Every non-SMC strategy hardcodes a fake confluence score

`CRT_v1` → `confluence_score=90`, `HTFFVGFlip_v1` / `BiasIFVG_v1` / `NYOpenRetest_v1` → `confluence_score=100.0` (always, literally, every trade), `DriftJumpAlpha_v1` → `80` or `95` depending on setup type. None of these are computed from real signal-quality factors — they're constants. This doesn't block trades, but it means the "Win Rate by Score" and "Win Rate by Confluence" breakdowns in the Analytics/Backtester UI are meaningless for these strategies (every trade lands in one bucket). Worth knowing before drawing conclusions from those charts for anything but SMC\_v1.

---

## Part 2 — Per-strategy additional findings

### `SMC_v1`

- **`ipdm_phase` is computed but never consulted.** `IPDMDetector.update()` runs every H4 bar and stores real Accumulation/Manipulation/Expansion state to `self.context["ipdm"]` — but the scoring/gating context (`_build_scorer_context`) never reads it, and (per Pass 1, Bug 1\) Gate 6's `enforce_htf_pd` check reads a key that's never populated either. The IPDM tracking that's supposedly a core part of this strategy's design is fully decorative right now.  
- **Gate 3 (POI requirement) will likely become your next blocker once Gate 6 is fixed.** A candlestick pattern can be detected anywhere in the last 3 M5 candles — it doesn't guarantee price is actually inside a fresh OB/FVG/OTE/S\&D zone, which `fresh_ob`/`fvg_inside_ob`/`in_ote_zone`/`in_sd_zone` require. Expect a real (not buggy, just strict) chunk of rejections here after the Gate 6 fix — that's useful signal about setup quality, not another bug, but flagging it so it's not mistaken for a new bug.  
- **Gate 5 (spread filter) is a no-op.** `current_spread_pips` is never set anywhere in `_build_scorer_context`, so it always defaults to `0`. `0 > atr * 0.5` is never true, so this gate can never reject a signal for excessive spread — meaning spread filtering doesn't currently function at all (harmless for trade *frequency*, but it means a real spread-widening event during news wouldn't be caught by this specific gate).

### `CRT_v1`

No new blocking issues beyond Pass 1's Bugs 4 and 5\. The `"1H"` timeframe string (Pass 1, Secondary E) remains worth fixing to `"H1"` so it isn't relying on a fallback-default coincidence.

### `HTFFVGFlip_v1`

- **`m5_swing_point` uses the entire fetched candle window, not a recent lookback.** `state["m5_swing_point"] = candles["low"].min() if bias == "LONG" else candles["high"].max()` — `candles` here is whatever was passed into `on_bar`, which in live can be up to 5,000 bars. This can place the stop loss at the all-time low/high of the entire fetched history rather than a meaningful recent structural point, producing enormous, essentially arbitrary stop distances.  
- **FVG sensitivity is hardcoded, not configurable.** `FVGDetector(fvg_min_gap_atr_mult=0.2)` (HTF) and `FVGDetector(fvg_min_gap_atr_mult=0.1)` (M5) are fixed constants in `__init__`, never read from `self.params` or the SMC config. `HTFFVGFlipParams` doesn't expose an FVG-gap-size field at all, so there's currently no way to tune this from Settings.  
- **`entry_confirmation_tf` is unused.** The dataclass declares it (default `"M1"`), but the actual inversion-confirmation logic is hardcoded to `elif timeframe == "M5":` — the spec's separate 1-minute confirmation step doesn't exist in the implementation; M5-FVG-formation and inversion-confirmation are conflated into one branch.

### `BiasIFVG_v1`

- **This is explicitly a placeholder implementation**, not the Bias→KeyLevel→IFVG system described in `docs/strategy-2-bias-keylevel-ifvg.md`. The code says so in its own comments (`"Simulated key level tap for boilerplate"`, `"Simulated trigger logic for boilerplate"`), and `_detect_key_levels()` unconditionally `return []` — dead code whose result is stored but never read. Fixing Bugs 2/3 will make this strategy trade, just not on the logic its spec describes.  
- **Flat 1% stop loss, not instrument-aware:** `sl = entry * 0.99` / `entry * 1.01`. Reasonable-ish on some symbols, arbitrary on others (a 1% stop on a Volatility index vs. a 1% stop on EURUSD are wildly different risk profiles).  
- **Fragile daily-trade-counter reset:** `if current_time.hour == 23 and current_time.minute >= 50: state["trades_today"] = 0` requires `on_bar` to be called with a candle timestamped in that exact 10-minute window. If that window is ever skipped (data gaps, scan timing, non-aligned bars), the counter never resets and the strategy could silently stop trading for that symbol once `max_trades_per_day` is hit, with no self-correcting mechanism the way `CRT_v1`'s cleaner `if self.last_trade_date != dt.date()` pattern has.

### `NYOpenRetest_v1`

- **Range-marking uses exact string equality against a single candle**, not a window: `if state["status"] == "MARK_RANGE" and time_str == self.params.range_window_end:` (default `"08:15"`). If the M15 candle stream isn't perfectly aligned to that exact minute (broker time offsets, data gaps, DST edge cases), this condition can simply never be true for a given day, and the strategy stays stuck in `MARK_RANGE` — silently producing zero setups for that entire day, with no fallback. A range check (e.g., `08:00 <= time_str <= 08:15`) or a bar-count-since-session-start approach would be more robust.  
- **Fixed point-based SL/TP buffers, not instrument-normalized:** `stop_buffer_points` (default `5.0`) and `fixed_target_points` (default `15.0`) are applied as raw price deltas with no per-symbol scaling — plausible on XAUUSD, essentially meaningless (either negligible or absurd) on most other instruments.  
- **Reminder from Pass 1:** the strategy's own `take_profit` field (computed from `fixed_target_points` and the dynamic-override logic) is discarded by `RiskEngine`, which recomputes TP from the generic `tp1_rr`…`tp5_rr` ladder anyway — so even once Bugs 2/3 are fixed, the actual executed targets won't match what this strategy calculates.

### `DriftJumpAlpha_v1` (the one strategy currently working — flagging what to watch)

- **O(n²) performance issue:** `on_bar` rebuilds `self.jump_distances` by rescanning the *entire* candle history from index 1 to `len(df)-1` on every single bar call. In live (up to 5,000 candles fetched) or in a long backtest, this means the per-bar cost grows linearly with history length, making this strategy's backtests noticeably slower than the others as the date range grows. Not a correctness bug, but worth knowing if backtests on this strategy feel disproportionately slow.  
- **`aggregate_max_lots_per_symbol` is configured but never enforced.** `DriftJumpAlphaParams.aggregate_max_lots_per_symbol` (default `6.0`) is exposed in the UI and described in the strategy's own spec (`DriftJumpAlpha_Strategy_Spec_v2.md`) as a mandatory hard clamp — `"clamp_to_lot_ceiling(size, symbol) — NEW — hard clamp, always applied"` — but no code in `strategy_two/engine.py` or `risk/engine.py` ever reads or applies this field. The generic `PropFirmValidator.max_lot_sizes` mechanism does still apply if Prop Firm mode is separately enabled, but the strategy-specific ceiling described in its own spec doesn't currently exist in the code.

---

## Part 3 — Frontend/config wiring (restated from Pass 1 for completeness)

- `Backtester.jsx`'s initial form state never sets `enforce_htf_pd` / `enforce_fvg_displacement` / `enforce_asian_range_sweep`, so unchecked boxes send nothing (not `false`) to the backend, and the Python-side defaults (`True`) silently win regardless of what the UI shows. Relevant again here because it interacts directly with Part 1's Gate 6 discussion — even after fixing the missing context keys, a user trying to turn these filters off via the UI currently can't.

---

## Updated priority-ordered fix list

1. **Bug 3 / Bug 2** (Pass 1\) — `get_required_timeframes()` overrides \+ `"LONG"`/`"SHORT"` → `"BUY"`/`"SELL"`. Cheapest, unblocks 3 strategies immediately in both modes.  
2. **Bug 5** (Pass 1\) — `self.ms_detector.state` → `self.ms_detector.get_bias()`. One line, unblocks CRT in live.  
3. **Bug 4** (Pass 1\) — dynamic timeframe dispatch in the backtest route instead of hardcoded H4/M15/M5.  
4. **Bug 1** (Pass 1\) — SMC Gate 6 context keys (`active_fvgs`, `asian_range_swept`, `ipdm_phase`) — either implement them for real or default the enforce flags to `False`.  
5. **1.2** — fix the live compounding `hasattr` check. High value: this silently disables a whole feature with no error, no warning, and no UI indication anything is wrong.  
6. **1.1** — decide and document intended behavior (does % risk compound with current balance or stay fixed to starting balance?), then make backtest and live agree.  
7. **1.3** — resolve `be_buffer_pips` vs `be_buffer_atr_mult` in both `bot_service.py`'s and `backtest.py`'s risk\_config construction, and align `position_manager.py`'s live formula to actually use real ATR instead of the `pip_size × 10` stand-in.  
8. **1.5** — either add a matching time-based force-close to live, or remove it from backtest, so backtest duration/win-rate stats reflect what live will actually do.  
9. **1.4, 1.6, 1.7, 1.8, 1.9** and the Part 2 per-strategy items — lower severity, but each one will quietly distort either trade behavior or the analytics you'd use to evaluate it; worth clearing before trusting backtest results as a proxy for live performance.

