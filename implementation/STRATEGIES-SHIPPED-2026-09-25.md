# Three strategies, in the app — measured as the app actually trades them

**2026-09-25** · `backend/strategies/strategy_trend/`, `strategy_overnight/`, `strategy_opening_drive/`
· `scripts/run_app_form_check.py` · `tests/test_shipped_strategies_2026_09_25.py`

---

## 0. Read this first

The research numbers in the earlier documents this session were **not** numbers the app
could produce. Three of the liberties the research took, the engine cannot:

| The research did | The engine does | What it cost |
|---|---|---|
| Entered **at** the 20-day channel on a resting stop order, filled intrabar | Signals on a bar CLOSE, fills at the **next bar's open** | Trend: **+0.067R → +0.063R** on M15. On D1 it would have been +0.026R. |
| Held the overnight trade with **no stop** (`0.5 × ATR` was only a reporting normaliser) | Always places a real stop, which gets hit | Nothing, as it turns out: +0.065R either way, and the stop was re-swept. |
| Grouped sessions at a **fixed minute of day** from the volume profile, which cannot follow DST | Resolves the New York session through `pytz` on true-UTC bars | Improved it. Opening Drive t **+1.57 → +1.89**. |

So every number below was re-measured through the app's own entry and exit mechanics before
anything was written. That is what `scripts/run_app_form_check.py` is, and the numbers in this
document are the shipped ones.

**One more honest caveat, stated once and applying to all three:** the backtester calls
`on_position_bar` with a slice that includes the bar it then checks for stop hits, so a
trail computed from a bar's close is applied to that same bar — one bar earlier than live,
where the level applies from the next bar. A trail can only ever *tighten* a stop, so the
bias is conservative, and it was measured both ways: +0.052R vs +0.050R on H1, +0.063R vs
+0.062R on M15. It is not doing the work.

---

## 1. What went in

| Strategy | Timeframe | Entry | Exit | Where it works |
|---|---|---|---|---|
| **`TrendBreakout_v1`** | M15 | close beyond the 20-day Donchian channel | 1.5 × daily-ATR chandelier trail, **no target** | gold, USDJPY, BTC, indices — 9 of 10 markets |
| **`OvernightSession_v1`** | M5 | the cash close, long only | the next cash open | equity indices only |
| **`OpeningDrive_v1`** | M5 | first M5 bar of the session vs the 12 EMA | 1 × ATR chandelier trail, flat at the close | US Tech 100, and essentially nowhere else |

---

## 2. `TrendBreakout_v1` — the only significant real-CFD edge found

**Deriv M15, 2021-10 → 2026-09, 10 markets, $10,000 at 0.5% risk per trade**

| | |
|---|---|
| **$10,000 → $13,262** | **+32.6%** |
| Trades | 876 |
| Win rate | **40.0%** |
| Payoff | **1.86** (avg win $46.96 vs avg loss −$24.48) |
| Expectancy | +0.063R = **+$3.80 per trade** |
| Profit factor | 1.26 |
| **t-statistic** | **+2.17** |
| Sharpe / annual vol | 1.34 / 8.8% |
| **Max drawdown** | **$781 (6.3%)** |
| Best / worst trade | +$470 / −$133 |
| Months green | 51% of 59 (best +$760, worst −$388) |
| Month streaks | 4 up / 4 down |
| Weeks green | 48% of 244 |
| Week streaks | 6 up / 9 down |
| Trade streaks | 7 wins / **15 losses** |
| R per month | +0.94 |

**Per market**

| Market | N | Expectancy | Win% | Payoff | t | Total R |
|---|---|---|---|---|---|---|
| **XAUUSD** | 96 | **+0.173R** | 42.7% | 2.27 | +1.88 | +16.6 |
| **USDJPY** | 100 | **+0.122R** | 46.0% | 1.81 | +1.42 | +12.2 |
| **BTCUSD** | 120 | +0.115R | 33.3% | 2.71 | +1.01 | +13.8 |
| US Tech 100 | 52 | +0.085R | 42.3% | 1.88 | +0.77 | +4.4 |
| XAGUSD | 92 | +0.078R | 42.4% | 1.76 | +0.93 | +7.2 |
| Germany 40 | 53 | +0.052R | 39.6% | 1.80 | +0.47 | +2.7 |
| EURUSD | 98 | +0.034R | 44.9% | 1.40 | +0.51 | +3.3 |
| GBPJPY | 98 | +0.016R | 36.7% | 1.83 | +0.21 | +1.5 |
| GBPUSD | 102 | +0.013R | 39.2% | 1.62 | +0.17 | +1.3 |
| US SP 500 | 65 | **−0.121R** | 32.3% | 1.36 | −1.39 | −7.9 |

The shape is what a working trend system looks like: a 40% win rate with winners nearly
twice the size of losers, and the profit concentrated in a tail. A 50% win rate with small
winners would have meant the exit logic was still wrong.

**Why M15 and not end of day.** The entry lag is one bar of whatever timeframe it runs on,
and that lag is the whole difference between the research number and the app number:

| Signal timeframe | N | Expectancy | t | Return | Max DD | median bars held |
|---|---|---|---|---|---|---|
| **M15** | **876** | **+0.063R** | **+2.17** | **+32.6%** | 6.3% | 291 |
| M30 | 859 | +0.053R | +1.81 | +26.2% | 6.4% | 155 |
| H1 | 823 | +0.053R | +1.78 | +24.6% | 6.7% | 81 |
| H4 | 764 | +0.041R | +1.24 | +9.0% | 8.5% | 23 |
| D1 | 655 | +0.026R | +0.61 | +9.8% | 12.3% | 5 |

**Why the trail window is bounded at 100 bars.** Only a bounded window can be recomputed
identically in the backtester and live — live hands the exit a fixed-length fetch, not the
whole trade. It costs nothing: 100 bars, 300 bars and unbounded all measure +0.052R to
+0.053R on H1 and +0.060R to +0.063R on M15, because the stop ratchets, so how far back the
anchor can see stops mattering the moment a new extreme prints.

### The bad news

On **FundedNext's own bars** (2025-06 → 2026-09, 7 symbols, 170 trades):
**−0.092R, t −1.75, 29.4% win, −11.6%, 12.7% max drawdown, 25% of 16 months green.**
Part of that is a genuinely flat 16 months for trend following and part is their wider
spreads. 170 trades is not evidence of anything, and trend systems are judged in years —
but it is the most recent evidence there is, and it is not encouraging.

---

## 3. `OvernightSession_v1` — the only one that survives prop costs

Long an equity index from the cash close to the next cash open. This is the overnight risk
premium: on equity indices the return earned while the cash market is **shut** has
historically been most of the total return, and the open-to-close return close to zero. It
is compensation for gap risk rather than a mispricing, which is why it should keep paying,
and it is cheap to harvest — one entry and one exit a night is one spread a night.

**Deriv M5, 2021-10 → 2026-09, $10,000 at 0.5% risk**

| | |
|---|---|
| **$10,000 → $18,008** | **+80.1%** |
| Nights | 2,006 |
| Win rate | 52.8% |
| Expectancy | +0.065R = **+$3.99 per night** |
| Payoff | 1.06 (avg win $50.87 vs avg loss −$48.43) |
| Profit factor | 1.17 |
| **t-statistic** | **+3.15** |
| Sharpe / annual vol | 1.28 / 19.1% |
| **Max drawdown** | **$4,156 (21.7%)** |
| Months green | **72% of 32** (best +$1,869, worst −$1,383) |
| Month streaks | 8 up / 3 down |
| Weeks green | 57% of 139 · streaks 9 up / 6 down |
| Trade streaks | 15 wins / 21 losses |
| R per month | +4.18 |

| Market | N | Expectancy | Win% | t |
|---|---|---|---|---|
| **US Tech 100** | 674 | **+0.090R** | 54.7% | **+2.64** |
| **US SP 500** | 674 | **+0.074R** | 54.9% | **+2.25** |
| Germany 40 | 658 | +0.031R | 48.6% | +0.77 |

**Stop sweep** (the research had no stop at all, so this had to be re-measured):

| Stop | Expectancy | Win% | t | Return | Max DD |
|---|---|---|---|---|---|
| **0.5 × ATR** | **+0.065R** | 52.8% | +3.15 | **+80.1%** | 21.7% |
| 1.0 × ATR | +0.036R | 56.4% | +3.09 | +35.8% | 9.1% |
| 1.5 × ATR | +0.023R | 56.6% | +2.78 | +21.2% | 9.0% |
| 2.0 × ATR | +0.017R | 56.6% | +2.66 | +16.9% | 9.2% |
| 3.0 × ATR | +0.011R | 56.6% | +2.63 | +10.0% | 8.7% |

A wider stop lowers the drawdown *and* the return, because a wider stop is a bigger R for
the same move. 0.5 ships; 1.0 is the setting to use if 21.7% is more than you want to sit
through.

**On FundedNext's own bars** (US30 and SPX500, 654 nights): **+0.056R, t +1.73, Sharpe 1.16,
+13.8% at 0.5% risk, 9.0% max drawdown, 69% of 16 months green.** This is the only strategy
in the whole 2026-09-25 book that is still positive on a prop feed, and it is therefore the
one to run on a prop account.

**Read the drawdown before the return.** 21.7% at 0.5% risk is a lot for an expectancy this
small, and it is structural: the losers are gap-downs, they are correlated across indices,
and they arrive together. Against a static 10% prop limit that matters more than the return
does.

---

## 4. `OpeningDrive_v1` — the marketed system, tested

The claim, verbatim: *"At the New York open, the algo watches just one thing: the first
5-minute candle. If it closes above the 12 EMA, it goes long. If it closes below, it goes
short... tested on Nasdaq from 2019 to 2026 across 1,448 trades. 982% historical return, 57%
win rate, 1.29 profit factor."*

**The 57% and the 1.29 do not reproduce.** Over 5,081 sessions on seven markets, costs
charged: **49.8% wins and a 1.07 profit factor.** On US Tech 100 alone, the market the claim
names: **54.5% wins, 1.07 profit factor.** And 982% is not a comparable number at all
without the risk per trade and whether it compounds, neither of which was stated — a 57%
win rate at 1.29 is a modest edge, and turning it into 982% is a leverage choice, not a
strategy property.

**What is actually left — Deriv M5, 2021-10 → 2026-09, $10,000 at 0.5% risk**

| | |
|---|---|
| $10,000 → $12,652 | +26.5% |
| Sessions | 5,081 |
| Win rate | 49.8% |
| Expectancy | +0.012R = **+$0.52 per session** |
| Payoff | 1.08 (avg win $16.53 vs avg loss −$15.34) |
| Profit factor | 1.07 |
| t-statistic | +1.89 |
| Sharpe | 0.59 |
| Max drawdown | $1,065 (10.1%) |
| Months green | 60% of 60 (best +$726, worst −$410) · streaks 7 up / 6 down |
| Weeks green | 53% of 262 · streaks 9 up / 5 down |

| Market | N | Expectancy | Win% | t | |
|---|---|---|---|---|---|
| **US Tech 100** | 675 | **+0.052R** | 54.5% | **+2.49** | the only one that stands alone |
| US SP 500 | 675 | +0.025R | 51.3% | +1.17 | |
| XAUUSD | 1,271 | +0.012R | 49.7% | +0.92 | |
| Germany 40 | 659 | +0.007R | 51.9% | +0.45 | |
| BTCUSD | 1,801 | **−0.005R** | 46.7% | −0.47 | do not run it here |
| every FX pair tested | | negative | | | |

**On FundedNext's bars it is NEGATIVE** (1,313 sessions, −0.008R, −7.8%, 17.3% max
drawdown). It is a Deriv-cost strategy. **Run it on US Tech 100 or not at all.**

It ships because it is measurable and positive on indices, and because it was worth knowing
whether the claim held. It does not ship as the system it was sold as.

---

## 5. The thing all three say together

| | Deriv | FundedNext | |
|---|---|---|---|
| TrendBreakout_v1 | **+32.6%** (t +2.17) | −11.6% (t −1.75) | sign flips |
| OvernightSession_v1 | **+80.1%** (t +3.15) | **+13.8%** (t +1.73) | survives |
| OpeningDrive_v1 | +26.5% (t +1.89) | −7.8% | sign flips |

Two of three change sign on the prop feed, on the same rule over overlapping dates.
**Execution cost, not strategy selection, is still the binding variable** — which is what
this whole session has been finding, and it is why `OvernightSession_v1` is the one
recommendation for a prop account and the other two belong on Deriv.

None of these is a one-month challenge strategy. That arithmetic has not changed: +10% in 20
days needs a Sharpe that does not exist on retail CFDs, and the only thing measured this
session that clears it is Crash/Boom drift on synthetics (`DriftJumpAlpha_v1` /
`BoomDriftJump_v1`, Sharpe 5.62, 97.5% pass on both BloomFunded phases, median 35 days).

---

## 6. How it is wired

| Layer | Change |
|---|---|
| Strategies | `strategy_trend/`, `strategy_overnight/`, `strategy_opening_drive/` — `params.py` + `engine.py` each |
| Shared | `strategies/core/daily_atr.py` (daily ATR off an intraday window, cached per symbol-day), `strategies/core/trail.py` (the bounded chandelier) |
| Registry | `strategies/registry.py::_load_strategies` |
| Config | `core/config_schema.py` — `trend_breakout`, `overnight_session`, `opening_drive` blocks on `UserConfigV2`, plus `from_dict` and the `None` guards |
| Schema | `core/schema_introspection.py` — three new groups, so the slot editor renders each parameter from its own docstring |
| Backtest route | `api/routes/backtest.py::STRATEGY_PARAM_SECTION` |
| Measured exits | `strategies/strategy_defaults.py` — one target, no break-even, generic trailing OFF, plus the new `NO_MEASURED_TARGET` set |
| API | `/strategy-factory/strategy-defaults` now publishes `no_measured_target` |
| Frontend | `slotSpec.js::STRATEGY_OPTIONS`, `StrategyLab.jsx`, and a `SlotEditor` warning when a placeholder target is lowered |
| Tests | `tests/test_shipped_strategies_2026_09_25.py` — 22 tests |

### The one design decision worth knowing about

**These strategies own their exits.** RiskParams' `ATR_TRAIL` trails a multiple of the
**strategy timeframe's** ATR from the extreme **price**; these trail a multiple of the
**daily** ATR from the extreme **close**. On M15 those are two completely different stops
and only one of them was measured, so the rule travels with the strategy through
`on_position_bar` → `TradeAction(action="MODIFY_SL")`, and the generic ladder is switched
off in `strategy_defaults.py`.

They are the first strategies in the book to return `MODIFY_SL` from that hook.
`engine.py` has always applied it and `portfolio_engine.py` was fixed to during the
per-slot work — its own comment names this exact case — but nothing exercised it. Three of
the new tests do, including one that runs the same signal through both engines and requires
identical stops, exits and fills, and one that runs it with no strategy attached to prove
the trail is what is being tested.

**`tp1_rr` is a placeholder, not a setting.** None of these three has a profit target in its
measured rule, and the engine always needs a take-profit price, so it is set to 20R — beyond
the best single trade in five years (+5.9R). Lowering it does not add a target, it caps the
handful of very large winners the rule lives on. That is now machine-readable
(`NO_MEASURED_TARGET`), published by the API, warned about in the slot editor, and asserted
in the tests.

---

## 7. Two things found by running it, that are not bugs in this work

**1. `TrendBreakout_v1` on XAUUSD needs more capital than $10,000 at 0.5%.** A real app
backtest produced 429 signals and the risk engine rejected every one with *"Calculated lot
size is below this broker's minimum tradeable volume"*. A 2 × daily-ATR stop on gold is
~$80/oz, the minimum 0.01 lot is 1 oz on a 100 oz contract, so one minimum position risks
more than the $50 the account allows. This is the same capital-adequacy wall as the earlier
small-account work, and the engine reported it correctly and by name. Either raise risk per
trade, or run it on the FX pairs and indices, where the stop is a smaller fraction of the
account.

**2. The app's MT5 is bound to the FundedNext terminal.** `.env` has
`MT5_PATH=C:\Program Files\MetaTrader 5` (FundedNext) and
`DERIV_MT5_PATH=C:\Program Files\MetaTrader 5 Terminal`. A backtest on Deriv synthetics
therefore finds no data. I have not changed it — which broker the app talks to is a
deployment decision, not a code one.

---

## 8. Reproduction

```bash
py -3.12 scripts/run_app_form_check.py --feed deriv --since 2021-10-01
py -3.12 scripts/run_app_form_check.py --feed deriv --since 2021-10-01 --sweep
py -3.12 scripts/run_app_form_check.py --feed fundednext --since 2025-06-01
py -3.12 -m pytest tests/test_shipped_strategies_2026_09_25.py -q
```
