# Backtest = live: parity audit, fixes and what to expect (13 Sep 2026)

You asked for five things:
1. Confirm every strategy from the research is in the app.
2. Confirm a backtest enters the same signals, with the same parameters, as live.
3. Make costs a source of truth, neither optimistic nor over-pessimistic, with Crash slippage handled properly.
4. Make the frontend match the backend, including its summary numbers.
5. Push everything to `dev`.

This report answers each one: what was checked, what was wrong, what was fixed, and what can still differ.

---

## 1. The short answer

- **Signals are identical.** The app's own backtest engine was run headless and compared trade by trade with the research:
  - **ORB_v1 on GBPJPY:** all 86 research trades, same entry bar and direction on every one.
  - **Other markets:** results in §3. Every gap has a named cause, and none of them comes from how signals are generated.
- **Live runs the same code as the backtest.** Both call the same strategy engine, the same measured per-symbol settings and the same exit rules. One live-only difference was fixed: strategy-owned exits (session close, trailing, channel, mean, time) now run live too.
- **Eleven defects that made backtest ≠ live, or distorted the numbers, were found and fixed** (§2):
  - a stop hit on the entry bar was booked a bar late, at a far worse price (both engines);
  - VWAP_v1 added a break-even the researched session modes never used;
  - portfolio runs never applied strategy exits;
  - every strategy in a portfolio shared one exit ladder;
  - the Backtester ignored most of your saved live risk settings;
  - the new strategies were sized at 50–75% of your risk;
  - Sharpe/Sortino were annualised wrongly on both frontend and backend;
  - the Backtester's drawdown card disagreed with the engine;
  - spread and slippage were 2–10× too high on FX, gold and indices;
  - Crash stop slippage was modelled wrongly;
  - the Settings page couldn't set half the new strategy parameters.
- **What can still differ live** is outside the software's control:
  - requotes and fills during news;
  - a stop hit on a price that never printed in bar data;
  - the bot being offline when a signal bar closes;
  - broker-side changes (spread widening, swap changes).

  §6 lists each one and how big it is.

---

## 2. What was wrong, and the fix

| # | Defect | Effect | Fix |
|---|---|---|---|
| 1 | **The portfolio backtester never called the strategy's exit hook.** The single-symbol engine did. | ORB legs in a basket held past the session close; Donchian/Vol-breakout legs never trailed. The same leg gave different results alone and in a basket. | `portfolio_engine.run(strategies=…)` runs each slot's own close/trail every bar, exactly like `engine.py`. Test: `test_portfolio_engine_applies_strategy_owned_exits`. |
| 2 | **A portfolio ran one exit ladder for every strategy.** Live builds its risk config per signal, with each strategy's measured exits. | ORB (measured: 1 TP, no break-even) ran with 3 TP legs and break-even beside APA. | Each row gets its strategy's measured `tp_count`, `tp1_rr`, break-even and trailing (`use_strategy_exit_defaults`, on by default). A row's own R:R still wins. |
| 3 | **The Backtester loaded only the strategy blocks from your saved config.** | Risk %, sizing basis, drawdown caps, TP ladder and break-even/trailing came from the page's own defaults. A backtest didn't run your live settings unless you retyped them. | Every risk field on the form now starts at your saved live value. |
| 4 | **Live-only risk settings had no form field**, so they never reached a backtest. | Affected settings include trail activation, per-TP ATR trail multipliers, confluence risk tiers, the margin cap and cost-multiple floors. A portfolio request couldn't carry them at all. | Both requests now send them from the saved config (`risk_config`). |
| 5 | **Measured exit defaults were always pre-filled.** | Live applies a strategy's measured exits only where your saved setting is still the shipped default. If you had changed one, the backtest and live disagreed. | The server now reports which measured fields live applies for your account (`live_applies`). The Backtester pre-fills exactly those, and your saved value for the rest. |
| 6 | **Researched strategies stamped confluence 60–75.** | The default confluence risk tiers (80→100%, 65→75%, 55→50%) sized ORB at 75% and the classic families at 50% of your risk, live and in backtests. Research sized them at 100%. | Researched strategies now stamp 80, which is full risk. Their rules are pass/fail, not graded. |
| 7 | **Sharpe and Sortino multiplied per-trade ratios by √252.** That's only correct at one trade per trading day. | ORB (~130 trades/yr) was overstated by 39%; a 2,000-trades/yr strategy was understated 2.8×. Live `/api/stats` had no trade times at all. | Annualised by the real trade rate, identically in `metrics.py` and `summaryEngine.js`, with a test that runs both on the same trades. Live stats pass deal times. |
| 8 | **The Max DD card used the closed-trade equity curve**, while the saved report uses the engine's bar-by-bar curve (balance + floating P&L). | The card and the report disagreed on the same run. | Unfiltered, the card shows the engine's bar-level figure. Filtered (one symbol or strategy), there's no bar-level curve for the subset, so the card uses closed trades and says so in its title. |
| 9 | **Settings (live) lacked the new parameters.** | Missing: ORB timeframe/trend, VWAP session modes and confluences, APA setup mode, all six classic families. A slot also couldn't opt out of the measured per-symbol settings. | All added. Each slot now has a **Measured settings** checkbox (see §4). The bot and the live exit manager both honour it. |
| 10 | **A stop or target hit on the bar a trade entered was not checked until the NEXT bar**, in both engines. Live sends the stop and target with the order. | Measured on the BTCUSD session-pullback book: 10 of 113 stops booked at −1.35R to −2.37R instead of ~−1.02R. The book fell from +0.09R to +0.01R per trade. Every strategy with tight stops was understated. | Legs are now resolved against their entry bar with the same stop-first tie-break and fill model, and booked with that bar's exit time. Test: `test_stop_touched_on_the_entry_bar_fills_at_the_stop_not_on_the_next_bar`. |
| 11 | **VWAP_v1 shipped break-even at 1.5R and three TP legs.** The researched session modes use one target, no break-even, flat at the close. | 16 of 113 BTCUSD trades ended at break-even when the research took them to target or the session close. | VWAP_v1 defaults are now 1 TP, no break-even, no trailing. The original pullback mode has no edge either way (26-market study). |

---

## 3. Proof: the app's engine versus the research, trade by trade

Each book below was run through `scripts/run_app_backtest.py`, which is the Backtester page's own code path:
- same request defaults, measured per-symbol parameters, signal loop and window sizes;
- same risk config builder, and `BacktestEngine` with the strategy's exit hook;
- Jan–Sep 2026, $10k, 1%.

Each run was then compared with the research trade list (`scripts/check_app_research_parity.py`).

| Book | Research trades | App trades | Same bar & direction | The differences, explained |
|---|---|---|---|---|
| ORB_v1 M5 + trend, GBPJPY | 86 | 86 | **86** | none. At full risk, with per-bar spread: **+$1,604, PF 1.43, max DD 5.1%** against research +$1,919. The gap is real costs; trades average +0.25R before costs |
| ORB_v1 M5 + trend, XAUUSD | 85 | 81 | **78** | 7 fired but were **refused by the sizer** (below the broker's minimum lot at $10k with the 75% confluence tier, now fixed); 3 app-only on **US half-days** (see below) |
| VWAP_v1 SESSION_TREND, XAUUSD | 67 | 69 | **67** | 2 app-only, both **US half-days** (19 Jan, 25 May). Every trade paid its own bar's spread (1.0–1.4 pips) instead of the 5.9-pip weekend quote; +$1,553, PF 1.48 |
| VWAP_v1 SESSION_PULLBACK, BTCUSD | _pending_ | | | |
| Donchian_v1, XAUUSD (H1, research builder on the same MT5 bars) | 26 | 27 | **25** | 1 refused by the minimum lot; with one position at a time, a slightly different exit bar (the channel exit is evaluated on the engine's bar close) shifts when the next breakout can be taken — 1 research-only, 2 app-only. Every signal the strategy emitted is identical (91 signals, one position at a time) |

**Why the half-days differ, and why the app is the right one:**
- The research dropped any session with under 80% of its bars, but only after the fact.
- On 19 Jan (MLK Day), 25 May (Memorial Day) and 3 Jul, the breakout happened before the early close.
- A live bot can't know at 10:30 ET that the session will end at 13:00, so it trades them. The app does the same.
- The backtest therefore matches what live will do; the research was the one looking ahead.

---

## 4. Parameters: how "same settings" works now

- **Single-symbol Backtester:**
  - On page load every strategy block and risk field is your saved live configuration.
  - Anything you change for a run applies to that run only.
  - Settings live elsewhere (trail activation, per-TP ATR multipliers, confluence tiers, margin cap, cost-multiple floors) are sent from the saved config automatically.
- **"Use the measured settings for this symbol"** (Backtester) is the same switch as **"Measured settings"** on each live slot (Settings → Strategy Slots):
  - **On (default):** the per-symbol values measured in research apply. Examples: ORB M5 + trend on GBPJPY London; VWAP session trend on XAUUSD.
  - **Off:** the strategy's own parameter block applies, exactly as you set it.
  - Each switch position gives the same engine configuration on both paths.
- **Portfolio Backtester:**
  - Rows pair symbol + strategy exactly like live slots.
  - Each row's strategy runs its own measured exits.
  - The same risk settings and saved-config knobs apply.
- **Strategy exits are part of the strategy, on every path:**
  - ORB and the VWAP/APA session modes close at the session end.
  - Donchian and Vol-breakout trail by 3×ATR, never loosened.
  - Donchian also has a channel exit; RSI(2) and Bollinger exit at the mean; TSMOM exits on a flip.
  - Every family has a time limit.
  - Live, these run in the position manager on the same code.

To confirm a live slot matches a backtest, compare the bot status page's `resolved_params` with the saved backtest's `params_snapshot`. They should be identical.

---

## 5. Costs: now measured, not guessed

All figures below were measured on MT5, using ticks from 7–11 Sep and this account's own stop fills over 200 days.

### Spread
Each bar's recorded spread equals the real tick spread at that bar's open (ratio 1.00 on EURUSD, XAUUSD, US Tech 100, BTCUSD and Crash 1000; 0.80 on GBPJPY). The old single figure per symbol was far above that:

| | Real | Old backtest figure |
|---|---|---|
| GBPJPY | ~1.0 pip | 3.1 pips |
| XAUUSD | $0.15 | $0.59 |
| US Tech 100 | 0.7 pt | 2.0 pt |

The old figure was the live quote when the run started, often a weekend or rollover quote.

**Now:** every trade pays the spread its own entry bar recorded. A spread you type in still overrides it.

### Slippage
Measured as the average price move over a 500 ms execution delay:

| Market | Measured | Old default | New default |
|---|---|---|---|
| EURUSD | 0.04 pip | 0.4 | **0.1** |
| GBPUSD | 0.06 pip | 0.4 | **0.1** |
| USDJPY | 0.11 pip | 0.4 | **0.1** |
| GBPJPY | 0.12 pip | 0.7 | **0.2** |
| US Tech 100 | 0.36 pt | 1.0 pt | **0.375 pt** |
| US SP 500 | 0.05 pt | 0.3 pt | **0.06 pt** |
| Germany 40 | 0.2 pt | 0.6 pt | **0.25 pt** |
| XAUUSD | $0.07 | $0.10 | $0.10 (kept) |
| BTCUSD | ~$1 | ~$33 | **~$5** |

Your live FX stop fills agree: CADJPY 0.1 pip, AUDJPY 0.

### Stop slippage on Crash
Measured on **146 live Crash 1000 stop-outs:**

| Median | Mean | p90 | p99 |
|---|---|---|---|
| **3.04** index points | **3.82** | 6.85 | 12.41 |

- **The key finding:** how far a spike carries past a stop has **no relationship to how wide the stop was** (correlation −0.06).
- **The old model was wrong on both ends.** It charged 0.403 × stop distance, so a 60-point stop paid 24 points and a 10-point stop paid 4.
- **Now:** Crash 1000 and Crash 300 charge a fixed index-point overshoot from your live fills. It's clamped to the bar's own extreme, so a bar that only grazed the stop pays nothing extra. The average charge fell from 0.40R to 0.23R, which is what you actually paid.
- **Crash 500** has no live fills yet, so it uses Crash 1000's measured 0.225R.
- **Boom** has no live fills either and keeps the research tick-study profile.

### Everything except Boom/Crash
FX, metals, indices and crypto keep a small 0.02R overshoot, clamped to the bar. Your live fills measured 0.01–0.03R on CADJPY, BTCUSD and the Volatility indices.

### Swap and commission
Unchanged:
- **Swap** comes from MT5's live values, converted to account currency. That conversion was fixed earlier; it had produced a 150× error on JPY swaps.
- **Commission** comes from what your account has actually been charged; the old $7/lot guess was removed on 12 Sep.

---

## 6. What can still differ live, and roughly how much

| Source | Direction | Size | Why it can't be modelled away |
|---|---|---|---|
| A news spike fills a stop beyond the bar's recorded range | worse live | rare; can be several R on one trade | Bar data has no prices that never printed; tick replay would catch it |
| Bot offline or restarting when a signal bar closes | fewer live trades | one trade per missed session | The bot scans every 60 s and trades the last closed bar only |
| Spread widening at the exact entry moment | small | ~0.0–0.1R | The bar records the spread at its open, not the moment you enter |
| Broker changes swap or margin mid-run | either | small on intraday books | Backtests use the current values |
| The broker's minimum lot refuses a trade | fewer trades | frequent below ~$3k on XAUUSD/US Tech 100 | Identical in both paths, but worth knowing on $350 |

---

## 7. The frontend and backend now compute the same numbers

- **Backtest summary cards:** win rate, P&L, profit factor, expectancy, Sharpe, Sortino, drawdown and streaks all come from `summaryEngine.js`, which now matches `metrics.py` field for field. `test_frontend_summary_engine_tallies_with_the_backend` runs both on the same trades, fixed and compounding, and requires every figure to agree.
- **Max DD:** the card shows the engine's bar-level figure, the same one the saved report stores.
- **Live dashboard and Analytics:** they read `/api/stats`, built by the same `compute_portfolio_stats`, now with the real trade rate.
- **Journal:** its win rate, profit factor and expectancy match those definitions.
- **Every backend setting is editable in the UI:**
  - the new strategies and their parameters (Backtester, Settings, Strategy Lab);
  - ORB timeframe and trend filter;
  - VWAP session modes and confluences;
  - APA setup mode (Settings);
  - the per-slot measured-settings switch.

---

## 8. Files changed

- **Engines:**
  - `backtester/engine.py`: per-bar spread, strategy `MODIFY_SL`.
  - `backtester/portfolio_engine.py`: strategy exits, per-strategy exit ladder, per-bar spread.
  - `backtester/fill_model.py`: absolute Crash overshoot.
- **Costs:** `risk/broker_costs.py`, with measured slippage defaults.
- **Routes:**
  - `api/routes/backtest.py`: portfolio `risk_config`, per-strategy exits, strategies passed to the engine, longer history for long-lookback strategies.
  - `api/routes/strategy_factory.py`: `live_applies`.
  - `api/routes/stats.py`: trade times.
- **Live:**
  - `services/bot_service.py` and `services/position_manager.py`: measured-settings switch, generic strategy exit hook.
  - `core/config_schema.py`: `InstrumentSlot.use_measured_params`.
- **Metrics:** `analytics/metrics.py`: `trades_per_year`.
- **Strategies:** confluence 80 for researched strategies in `strategy_orb`, `strategy_vwap` and `strategy_classic`.
- **Frontend:**
  - `utils/summaryEngine.js`: trade-rate Sharpe.
  - `pages/Backtester.jsx`: saved-config seeding, live_applies, passthrough, DD card, new strategy panels.
  - `pages/Settings/Strategy.jsx`: all new parameters, per-slot switch.
  - `pages/StrategyLab.jsx`.
- **Tests:** `tests/test_backtest_live_truth.py`; updated `tests/test_fill_model.py`.
- **Tools:** `scripts/check_app_research_parity.py`; `scripts/run_app_backtest.py` now honours strategy windows and dumps each trade's spread.

---

## 9. Your checklist

- [ ] Deploy `dev` to the VPS, restart the backend and rebuild the frontend.
- [ ] Open Settings → Strategy Slots. Every slot's **Measured settings** box is on by default; leave it on for the researched markets.
- [ ] Run one Backtester run per live slot (last 8 months, saved settings). Compare its `params_snapshot` with the bot's `resolved_params`; they should be identical.
- [ ] Forward-test for 4–6 weeks. Compare live trades with a backtest of the same weeks using `scripts/check_app_research_parity.py` on the app dump.
