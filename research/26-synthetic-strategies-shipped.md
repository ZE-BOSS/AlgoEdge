26 — Synthetic-index strategies: shipped, backtested, and how to reproduce
==========================================================================

**2026-09-04** · branch `synthetic-strategies-forward-test`
Backtest window: **1 January 2026 → 4 September 2026**, executed on raw ticks.

Five new strategies, eleven symbol configurations, all positive in the window,
all wired end-to-end and selectable in the frontend backtester.

---

## 1. The exact backtest parameters

Everything below uses these, and nothing else. This is the answer to "what
parameters did you use".

| setting | value |
|---|---|
| window | 2026-01-01 → 2026-09-04 (247 days) |
| timeframe | **M5**, bars built from raw ticks |
| starting balance | **$10,000** |
| risk per trade | **1.0%** |
| sizing | **STATIC** (fixed $100 risk; BALANCE reported alongside) |
| **pyramiding** | **NONE — `max_concurrent = 1`** |
| max trades/day | **6** |
| break-even | **off** |
| trailing stop | **off** (`trail_method: NONE`) |
| entry | first tick of the **next** bar, crossing the spread |
| stop fill | **market order** — fills at the first tick *through* the level |
| target fill | **limit order** — fills at the level, never better |
| costs | historical median spread per symbol (`synth_spread_audit.py`) |

**No pyramiding was used anywhere.** research/20 recorded that enabling it on
DriftJumpAlpha took the maximum consecutive-loss run from 15 to 69, which under
BALANCE sizing is ~50% of the account. Everything here is strictly sequential.

**The stop-fill rule is the one that matters.** A bar-based backtest books a
stop at exactly −1R. On these instruments the spike gaps *through* the stop, and
research/25 measured that difference at up to −2.5R per trade at tight stops.
Every number in this report uses the realistic fill.

## 2. What shipped

| strategy | logic | registered as |
|---|---|---|
| **BoomDriftJump_v1** | DriftJumpAlpha mirrored for Boom: sell the grind, buy after an up-spike | `strategy_boom/engine.py` |
| **TrendDrift_v1** | EMA-regime continuation, entered on a pullback to the fast EMA | `strategy_synth/engine.py` |
| **SpikeFade_v1** | a bar moves ≥ k × ATR one way; enter **against** it | `strategy_synth/engine.py` |
| **RangeRevert_v1** | price ≥ k × ATR from the slow EMA; enter back **toward** it | `strategy_synth/engine.py` |
| **RangeBreakout_v1** | close breaks the prior N-bar high/low; enter **with** it | `strategy_synth/engine.py` |

`TrendDrift_v1` exists for a specific reason. The search found the drift template
best on Crash 1000, Volatility 75 and Jump 100 — but `DriftJumpAlpha_v1` hard-filters
to CRASH symbols and `BoomDriftJump_v1` to BOOM symbols, so neither could ever fire
on Volatility or Jump. Shipping the measured logic under its own name keeps the
backtest and the live strategy the same code, which is the only way these numbers
are reproducible on your side.

## 3. The shipping table

Selected by: **max drawdown ≤ 35%, n ≥ 100, then best return.** The drawdown
filter is not cosmetic — the unconstrained best on Crash 1000 returned **+176.9%
with an 89.5% drawdown**, which no risk policy should authorise.

| symbol | strategy | stop | TP | n | WR% | PF | **STATIC ret%** | DD% | BAL ret% | BAL DD% | max consec loss |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Volatility 100 | RangeRevert_v1 | 2.5×ATR | 8R | 527 | 15.0 | **1.36** | **+168.6** | 29.1 | +314.9 | 54.9 | 50 |
| **Crash 1000** | **TrendDrift_v1** | 5.0×ATR | 8R | 410 | 16.1 | **1.30** | **+120.5** | **20.0** | +166.2 | 28.6 | 18 |
| Jump 25 | RangeRevert_v1 | 2.5×ATR | 8R | 646 | 13.5 | 1.16 | +97.2 | 32.4 | +96.4 | 43.0 | 45 |
| Range Break 100 | SpikeFade_v1 | 5.0×ATR | 5R | 316 | 24.7 | 1.25 | +77.7 | **22.2** | +93.8 | 21.4 | 12 |
| Volatility 25 | RangeBreakout_v1 | 2.5×ATR | 3R | 1,256 | 26.9 | 1.07 | +64.3 | 27.9 | +56.3 | 48.1 | 15 |
| Crash 500 | RangeRevert_v1 | 1.0×ATR | 5R | 1,479 | 19.9 | 1.04 | +61.7 | 27.5 | +18.4 | 48.7 | 19 |
| Boom 500 | RangeRevert_v1 | 5.0×ATR | 5R | 397 | 19.6 | 1.17 | +56.0 | **19.7** | +56.5 | 27.3 | 19 |
| Volatility 75 | TrendDrift_v1 | 2.5×ATR | 5R | 859 | 18.0 | 1.06 | +45.3 | 33.2 | +25.7 | 37.3 | 27 |
| Boom 1000 | RangeRevert_v1 | 5.0×ATR | 5R | 533 | 18.8 | 1.08 | +35.5 | 27.7 | +23.2 | 31.2 | 27 |
| Jump 100 | TrendDrift_v1 | 5.0×ATR | 5R | 235 | 19.6 | 1.18 | +35.4 | 33.9 | +33.4 | 36.6 | 41 |
| Range Break 200 | SpikeFade_v1 | 5.0×ATR | 5R | 230 | 24.3 | 1.06 | +16.4 | 24.0 | +7.7 | 26.3 | 11 |

**Step Index is deliberately not shipped.** Its best drawdown-constrained
configuration returned **+1.1% at PF 1.00** over eight months. research/24 §1.1
measured it as a fair coin to a precision of 0.009% on P(up), with no memory at
any Markov order to 10. There is nothing there.

### Reading the sizing columns

STATIC and BALANCE diverge sharply where the losing runs are long. Volatility 100
returns +168.6% STATIC and +314.9% BALANCE — but its drawdown goes 29.1% → 54.9%,
and it has a **50-trade losing run**. Jump 25 has a 45-trade run. Compounding
those is how an account dies. **Start on STATIC.**

## 4. How to reproduce, exactly

On your side, two ways.

**In the app** — all five strategies now appear in the Backtester's Strategy
Engine dropdown and in Settings → Strategy. Pick the symbol, pick the strategy,
set risk 1%, max 6 trades/day, no pyramiding, BE and trailing off, and the
per-symbol stop/TP from the table above (or leave them — they are the shipped
defaults in `SYNTH_SLOT_PARAMS`).

Expect the app's numbers to be **better** than this report's, because
`backend/backtester/engine.py` still books stops at the stop price for intrabar
gaps (research/24 §4.1). That gap is the known outstanding defect, not a
disagreement about the strategy.

**From the command line** — this is the authoritative version:

```bash
cd research/data
python run_strategy_search.py              # full 60-config grid, all 12 symbols
python pick_shipping_defaults.py           # the drawdown-filtered selection
python verify_strategies_end_to_end.py     # drives the real on_bar() interface
```

## 5. Verification performed

`verify_strategies_end_to_end.py` drives the **real strategy classes** through
the **real `on_bar` interface** with real M5 bars, and asserts on every signal
that stop/entry/target are correctly ordered, the R:R matches `tp1_rr`, and the
stop distance matches the configured ATR multiple.

| strategy | symbol | signals | BUY | SELL |
|---|---|---:|---:|---:|
| BoomDriftJump_v1 | Boom 1000 | 83 | 0 | 83 |
| SpikeFade_v1 | Boom 1000 | 82 | 0 | 82 |
| SpikeFade_v1 | Crash 1000 | 84 | 84 | 0 |
| RangeRevert_v1 | Volatility 75 | 84 | 43 | 41 |
| RangeBreakout_v1 | Range Break 100 | 82 | 44 | 38 |
| RangeBreakout_v1 | Step Index | 84 | 45 | 39 |
| TrendDrift_v1 | Crash 1000 | 84 | 53 | 31 |
| TrendDrift_v1 | Volatility 75 | 84 | 36 | 48 |
| TrendDrift_v1 | Jump 100 | 84 | 26 | 58 |

**All checks passed.** Directions are correct by construction — SpikeFade sells
Boom's up-spikes and buys Crash's down-spikes without any per-symbol config.

## 6. What was wired

| layer | change |
|---|---|
| strategies | `strategy_boom/engine.py` (new), `strategy_synth/engine.py` (new, 4 strategies) |
| registry | all five registered and importable |
| config | `BoomDriftJumpParams`, `SynthParams` added to `config_schema.py`, attached to `UserConfigV2`, wired into the dict loader |
| defaults | `SYNTH_SLOT_PARAMS` (11 entries) + `get_synth_slot_params()` in `strategy_defaults.py` |
| frontend | all five added to the Backtester dropdown, the `validStrats` guard, and Settings → Strategy |
| risk | daily trade cap and daily-risk cap honoured; BE/trailing default off |

One bug was found and fixed during wiring: `BaseStrategy` does **not** populate
`self.params` — each strategy binds its own config section (`strategy_apa` does
`self.params = config.apa`). The new strategies initially had no binding, so
every parameter lookup would have raised. Both now bind explicitly.

## 7. The honest caveat, stated once

Every configuration above is the **best of 60 grid points** on one eight-month
window. That procedure returns a positive number even on pure noise, and the
independent-sample t-statistics run **0.4 to 2.3** — none clears the bar for a
search this wide. research/24 measured all of these instruments as fair
martingales with memoryless jumps.

So: these are not validated edges. They are the best-performing configurations in
the window you asked about, shipped for forward testing, which is exactly what the
branch is for. The forward test is the experiment that settles it — and unlike a
backtest, it cannot be re-selected after the fact.

**Practical guidance if you trade them:** start on STATIC sizing, watch the
consecutive-loss columns (Volatility 100 has a 50-trade run), and treat the
first-month forward result as the real evidence.
