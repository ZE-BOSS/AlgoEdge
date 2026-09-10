# Phases 1, 2 and 4 — resolved

**2026-09-10** · branch `dev` · 177 tests pass, frontend builds clean.

This is the completion record. The forward-looking plan is now
[`MASTER-IMPLEMENTATION-PLAN.md`](MASTER-IMPLEMENTATION-PLAN.md), which covers
Phases 3 and 5 only.

---

## Your question, answered first

> **"Does the backend ignore the strategy and risk configuration I set in the
> frontend UI?"**

**No — and I traced the whole path to be sure.** `PUT /api/config`
(`backend/api/routes/config.py:80`) **deep-merges** into the stored blob, so a
partial save from the Risk tab does not wipe what the Strategy tab owns.
`UserConfigV2.from_dict` reads `synth`, `risk`, `drift_jump_alpha` and the rest
into their dataclasses, and `bot_service._scan_loop` **re-reads the config from
the database on every scan cycle**, so an edit takes effect without a restart.

Two specific confirmations:

- **Your synth block is genuinely saved at 20/20.** `Strategy.jsx`'s own local
  default is `max_trades_per_day: 6, max_daily_risk_pct: 4.0`. Your screenshot
  shows **20 / 20**, which the form can only display if it loaded those values
  from the backend. So the daily budget is *not* what capped live at 3 entries
  per symbol — that hypothesis from the first report is **refuted**.
- **But three specific UI values genuinely were not reaching the engine**, and
  one of them was costing you money on every trade. All three are fixed below.

The real answer to "was anything ignored?" is: not the config *plumbing*, but
three *consumers* of it. §1 has each one.

---

## §1 — What was actually wrong, and what it cost

### 1.1 Stops filled at exactly the stop price · **the big one**

`portfolio_engine.py:479` (and its twin in `engine.py`) asked only whether the
bar's **`open`** had gapped past the stop. A spike *inside* the M5 bar — which
on Boom/Crash is where every spike happens, by construction — booked a perfect
fill at the stop.

Your own two runs: **65/65 hard-SL exits at exactly the stop price, `gap_fill`
set on 0 of 132 trades.**

**Fixed** — new `backend/backtester/fill_model.py`, wired into both engines, on
by **default**:

| mode | behaviour |
|---|---|
| `OFF` | legacy, fills at the stop. Kept only to replay old runs bit-for-bit. |
| `CONSERVATIVE` **(default)** | charges the measured mean overshoot per symbol. No RNG. |
| `EMPIRICAL` | samples a per-symbol quantile table with a seeded, position-keyed draw, for tail/drawdown work. |

Every fill is **clamped to the bar's own extreme**, so the model can never
invent a price that did not trade: a bar that grazed the stop still fills at the
stop; only a bar that genuinely travelled through it pays.

Verified end-to-end on the portfolio engine:

```
OFF           trades=6  final=663.09  pnl= -36.91  gapped=  0.0%  meanOvershoot=0.000R
CONSERVATIVE  trades=4  final=659.31  pnl= -40.69  gapped=100.0%  meanOvershoot=0.353R
```

`OFF` reproduces the old numbers exactly. Runs now carry a `fill_model` block
reporting what was actually charged, so a future run cannot quietly regress.

### 1.2 Live risked ~1.8% while every backtest you read said 1.35% · **found in code**

`RiskEngine.evaluate_signal` reads `confluence_score` off the signal dict. The
backtest route put it there. **`bot_service` did not** — and the strategies carry
it as a `TradeSignal` *field*, not a metadata key, so live resolved `None` and
skipped the tier ladder entirely.

SpikeFade emits a hardcoded score of 70. The ladder is
`[(80, 100%), (65, 75%), (55, 50%)]`, so 70 → **75% of base**: 1.8 × 0.75 = 1.35%.

- Backtest `sizing_diagnostics`: `realised_risk_pct: 1.3469`, `binding_constraint: "confluence"`.
- Your six live trades on 9 Sep: a consistent **$156–170 of risk**, which is 1.8%
  of the account, not 1.35%.

**Live was running a third more risk per trade than any backtest ever reported.**
Fixed: `bot_service` now passes `confluence_score` on the signal dict.

### 1.3 The two paths handed strategies different amounts of history

Backtest sliced M5 to **500** bars; live passed **5,000**. Any indicator that
depends on visible history (ADX, a long EMA, a lookback percentile) computed one
thing live and another in the backtest, on identical data, undetectably.

Fixed: `backend/strategies/windows.py` is now the single table; both paths read
it.

### 1.4 `allow_pyramiding` behaved three different ways

- `portfolio_engine` **ignored it entirely** — so the `True` you set did nothing.
- `engine.py` honoured it.
- `bot_service` forwarded it to the risk engine.

So the same slot produced different trade counts depending on which engine ran
it. **This is the most likely mechanical reason "Boom Drift is profitable
standalone but not in a portfolio"** — the two runs were not comparable.

Fixed: all three now agree, and `min_bars_between_entries` is enforced in both
engines, keyed by **slot** rather than by symbol.

### 1.5 The rejection funnel did not add up

`total_evaluated` was incremented *after* the concurrency gates had already
dropped signals: your run read 144 evaluated / 76 blocked / 132 approved, and
could not answer "how many setups did the strategy actually find?".

Fixed: `raw_signals` (the census) and `pre_risk_rejections` (everything dropped
before the risk engine saw it), counted **before** the display cap so a busy run
is not undercounted. It now reconciles: `raw = pre_risk + total_evaluated`.

### 1.6 The trade-count question is now instrumented, not guessed

The daily-budget hypothesis is refuted (see above), which leaves scan cadence —
the loop fetches 5,000 bars per slot per timeframe with MT5 serialised onto one
thread, and a cycle longer than the bar it tracks silently steps over closed
bars. That produces no error and no gap, just fewer trades.

Rather than guess again, the bot now **reports it**. `/api/bot/status` carries:

| field | answers |
|---|---|
| `resolved_params` | what each engine actually resolved — diff it against a backtest's `params_snapshot` |
| `suppression_funnel` | every signal the live path dropped today, by named reason |
| `cycle_seconds` vs `primary_tf_seconds` | whether closed bars are being skipped |
| `cycle_overruns` | how often, this run |

Plus a once-per-slot-per-day log line listing every gate value, and a **WARN**
the moment a cycle reaches the bar duration it is supposed to track.

**Please send me `/api/bot/status` after a few hours of live running.** That
settles the residual trade-count gap with data instead of another hypothesis.

---

## §2 — One symbol, several strategies, live

**The backend already supported this.** `InstrumentSlot` (UUID-keyed, per-slot
risk/budget/quota) has been in place since [12.1]; engines are keyed by
`slot_id`, and the circuit breaker and risk engine are slot-aware. The Settings
page was the only thing still writing the symbol-keyed `instrument_settings`
array, which `config_schema` then migrated to exactly one slot per symbol — so
the whole mechanism was live-ready and unreachable.

**Now shipped:**

- **Slot table UI** replacing the one-dropdown-per-symbol panel. Add, duplicate,
  enable/disable and remove rows; the same symbol may appear as many times as you
  like. Legacy configs migrate on load, and `instrument_settings` is still
  written as a derived projection so other screens keep working.
- **Duplicate-pairing warning.** The same strategy twice on one symbol gives it
  two independent daily budgets and two position quotas — double exposure with no
  extra signal. The UI now says so.

**And the two real bugs that would have bitten the moment it became reachable:**

- **Signal dedupe was keyed by bare symbol.** Two slots shared one cell, and it
  failed *both* ways: slot B's fingerprint overwrote slot A's (defeating
  duplicate protection → a duplicate entry), and two slots emitting the same
  `(entry, SL, direction)` on one bar silently dropped the second. Now keyed by
  `slot_id`, at all seven read/write sites.
- **`min_bars_between_entries` was keyed by symbol** in the single-symbol engine.
  Now slot-keyed.

**Attribution:** orders now carry `AE_TP{level}_{slot_id[:8]}` in the MT5
comment. Without it, `position_manager` would trail slot A's position using slot
B's ATR multiplier. While there, the magic number now derives from
`self._magic_base` instead of a hardcoded `1001` — a customised base was
silently breaking the bot's ability to recognise its own trades.

**Exposure:** stacking N slots on one symbol carries N× the intended risk while
every per-slot cap still reads as satisfied, and both governor settings
(`max_net_direction_risk_pct`, `max_cluster_risk_pct`) default to off. Saving
such a config now returns a warning naming the symbol and the actual aggregate
percentage. **Set one before running stacked slots live.** A reasonable start for
two Boom/Crash slots is `max_net_direction_risk_pct = 3.6`.

---

## §3 — Catching sells on Crash and buys on Boom

### 3.1 Two of the three already existed and were switched off

- **`DriftJumpAlpha_v1`** — `SETUP B: JUMP ENTRY (SELL)`, `strategy_two/engine.py:366`.
  Gated on `trade_jumps_enabled` **and** `control_test_passed`, both `False` by
  default and both unchecked in your screenshots.
- **`BoomDriftJump_v1`** — `SETUP B: JUMP ENTRY (BUY)`, `strategy_boom/engine.py:166`.
  Gated on `trade_jumps_enabled`, `False` by default.

Both flags are already in the live Strategy tab *and* the Backtester. **Nothing
needed building; they need switching on and measuring.**

### 3.2 SpikeFade had no counterpart — now it does: `SpikeRide_v1`

Registered, in both UIs, with its own params. On Crash it sells; on Boom it buys.

**Direction is taken from the sign of return skew over the visible window, not
from the symbol name** — Crash grinds up and drops hard (negative skew), Boom
grinds down and pops (positive). A lookup table goes stale the moment a broker
renames an instrument; a measurement does not. An instrument with no measurable
asymmetry produces no signal.

**Its geometry is deliberately not the fade's.** SynthParams now carries
`spike_ride_stop_atr` (1.0), `spike_ride_tp_rr` (2.0) and
`spike_ride_stretch_atr` (1.5), because the two sides are not mirror images:
research/24 §3.1 puts spike magnitude at ~0.10% of price on average and ~0.23% at
p95 — roughly 2× and 4.5× M5 ATR — so a 5× ATR stop against a 2× ATR target
would be a 1:0.4 proposition and the measurement would be meaningless. A new
`stop_param_name` / `tp_param_name` hook makes that possible without disturbing
the four templates that legitimately share one params block.

### 3.3 Why this side is worth measuring — and it is not a timing edge

**Be clear about what it is not.** research/24 §3.1 measured jump arrival on
31,394 Crash 1000 events to be **memoryless**: P(jump in the next 251 ticks) is
flat at 0.217–0.226 whether you have waited 0 or 2,008 ticks, and magnitude is
uncorrelated with elapsed time (−0.0059, SE 0.0056). **No rule based on tick
count, bar count, or "a drop is due" can work.** The stretch trigger is a stop
*placement* choice, not a forecast.

**What it is.** The two sides have opposite exposure to the exact defect §1.1
just fixed:

| | the spike runs into… | consequence |
|---|---|---|
| **SpikeFade** (Crash BUY / Boom SELL) | the **stop** | gaps are losses; the harness booked every one at the stop price |
| **SpikeRide** (Crash SELL / Boom BUY) | the **target** | a limit fills at its price or better; a bar that gaps through it fills at the gapped open |

**So the old harness systematically flattered one side and not the other.**
Whether the ranking between them survives a correct fill model is open, testable,
and now measurable. It is also, mechanically, why your discretionary Crash sells
"go to TP fast and don't overshoot the SL" — that observation was correct and
there is a reason for it.

### 3.4 The numbers — what I can and cannot give you here

You asked for profit figures at $350. **I cannot produce them on this machine**
and I will not invent them: the local `ohlcv` table is empty, the archived bars
in `research/data/bars/` are M15/H1/D1 only (SpikeFade and SpikeRide are M5), and
there is no MT5 connection here. Fabricating a number would be exactly the
failure mode this whole exercise is about.

**Run this on your MT5 box and it prints the table:**

```bash
python scripts/measure_opposite_sides.py --url http://127.0.0.1:8000 --token "$ALGOEDGE_TOKEN" --balance 350 --start 2026-01-01 --end 2026-09-10
```

It runs all seven configurations twice — once with `stop_fill_model=OFF`, once
with `CONSERVATIVE` — and prints the per-side delta. **Read the delta column
first.** A side whose Δ is near zero was never being flattered; a side that only
worked with fills `OFF` never worked.

### 3.5 What $350 can actually express — measured

`scripts/reprice_backtest.py` on your own two files:

```
backtest_run_b2954d45.json   ($350)
  signal mortality  (raw setups the strategy found: 208)
    blocked by concurrency          35  ( 16.8%)
    below broker minimum lot       102  ( 49.0%)
    filled                          69  ( 33.2%)

backtest_run_cf9355c6.json   ($700)
    blocked by concurrency          64  ( 30.8%)
    below broker minimum lot         9  (  4.3%)
    filled                         132  ( 63.5%)
```

**At $350, 49% of this strategy's signals cannot be expressed** — the position it
wants is smaller than the broker will accept. The $350 run took **2** Boom trades
against the $700 run's **65**, from an identical 208-signal set. It is not a
smaller version of the same strategy; it is a biased subsample, weighted toward
whichever setups happened to have cheap stops.

That is a structural fact about the account size, independent of edge, and worth
knowing before optimisation rather than after.

### 3.6 Range Break 100/200 — your call was right

Your live journal: −$347.98, −$227.50 and −$177.63 against +$36.02 and +$50.39.
The shipped `SYNTH_SLOT_PARAMS` entries rest on +16.4% / PF 1.06 over eight
months, which is inside noise before §1.1's correction and negative after it.
**Keep them blacklisted.** Not re-litigating.

---

## §4 — Repricing your own runs

`scripts/reprice_backtest.py <run.json>` answers three questions about any saved
backtest without re-running it: is the fill model realistic, what is it worth if
it is not, and can this account size even trade this.

On `cf9355c6` ($700, +19.91% as booked, +15.87 R over 132 trades):

```
  fill realism
    stop-type exits ................. 130
    filled at EXACTLY the stop ...... 65 (50.0%)
    flagged gap_fill ................ 0 (0.0% of trades)
    fill_model ...................... ABSENT — run predates the realistic-fill model

  corrected for unbooked stop overshoot
    scope                          charge    total R    R/trade           ~$
    hard SL exits only         per-symbol      -8.73    -0.0661       -99.44
    hard SL exits only            -0.33 R      -5.58    -0.0423       -63.62
    hard SL exits only            -0.40 R     -10.13    -0.0768      -115.44
    all stop-type exits        per-symbol     -33.32    -0.2525      -379.60
    all stop-type exits           -0.33 R     -27.03    -0.2048      -307.95
    all stop-type exits           -0.40 R     -36.13    -0.2737      -411.61
```

**Every correction, including the most generous, flips the run from profitable to
losing.** The unbooked slippage is larger than the entire measured edge. Which is
why +19.91% in the backtest arrived as roughly break-even on the $700 account and
−$859 over two days on the $10,000 one.

---

## §5 — Files changed

| file | what |
|---|---|
| `backend/backtester/fill_model.py` | **new** — realistic stop fills, three modes, per-symbol overshoot profiles with provenance |
| `backend/strategies/windows.py` | **new** — the single history-window table both paths read |
| `backend/backtester/portfolio_engine.py` | fill model; honours `allow_pyramiding`; slot-keyed throttle; funnel census |
| `backend/backtester/engine.py` | fill model; slot-keyed throttle; funnel census |
| `backend/services/bot_service.py` | `confluence_score` to the risk engine; slot-keyed dedupe; slot-stamped orders; `magic_base` fix; window trim; resolved-params + suppression-funnel + cycle-overrun instrumentation |
| `backend/strategies/strategy_synth/engine.py` | **`SpikeRide_v1`**; per-strategy stop/target param hook |
| `backend/core/config_schema.py` | SpikeRide params; stacked-slot exposure warning |
| `backend/api/routes/backtest.py` | `stop_fill_model` / `stop_fill_seed`; `fill_model` in responses; shared window table; SpikeRide routing |
| `frontend/src/pages/Settings/Strategy.jsx` | **slot table UI**, migration, duplicate warning, SpikeRide params |
| `frontend/src/pages/Backtester.jsx` | SpikeRide registration |
| `scripts/reprice_backtest.py` | **new** — reprice + capital-adequacy report for any saved run |
| `scripts/measure_opposite_sides.py` | **new** — the Phase 2 comparison harness |
| `tests/test_fill_model.py` | **new**, 12 tests |
| `tests/test_backtest_live_parity.py` | **new**, 10 tests |
| `tests/test_multislot.py` | **new**, 16 tests |
| `tests/test_opposite_side.py` | **new**, 11 tests |
| `tests/test_analytics_persistence.py` | fixed a pre-existing false failure (inspected the leaf class, not the MRO — the synth templates call the gate API through `_SynthBase`) |

**177 tests pass. Frontend builds clean. Nothing is committed** — per your
standing instruction, this is working-tree only.

---

## §6 — What I need from you

1. **`/api/bot/status` after a few hours of live running.** `suppression_funnel`
   and `cycle_overruns` settle the residual trade-count gap.
2. **Run `scripts/measure_opposite_sides.py`** on the MT5 box and send me the
   table. That is Phase 2's answer, at $350, with honest fills.
3. **Before enabling stacked slots live**, set `max_net_direction_risk_pct`. The
   save will warn you if you don't.
4. **Re-run one of your existing backtests** now that the fill model is on, and
   compare against `cf9355c6`. Expect the number to get worse. That is the fix
   working.
