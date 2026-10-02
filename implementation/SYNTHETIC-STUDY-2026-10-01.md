# New Deriv synthetics, and whether the saved backtests can be trusted

**2026-10-01** · `scripts/run_synthetic_study.py`, `scripts/export_new_synthetics.py`,
`scripts/audit_saved_backtest.py` · data in `data/deriv_new/`, results in
`data/synthetic_study/`

---

## 1. Can the saved backtest numbers be trusted?

Short answer: **the dollar arithmetic is not broken, but the headline is not a
number you can plan with.**

The four runs in question:

| Saved run | Trades | WR | P&L |
|---|---:|---:|---:|
| Vol over Crash 750 | 402 | 37% | $306,891 |
| Vol over Crash 550 | 429 | 31% | $215,138 |
| Vol over Boom 550 | 299 | 31% | $9,398 |
| Vol over Boom 400 | 301 | 33% | $8,083 |

### It is compounding, not a calculation error

$316,891 from $10,000 over 402 trades is 31.7x, which needs a compounded gain of
**0.863% per trade**. At 1:3 with a 37% win rate that is **1.80% risk** — an
ordinary setting. Run the arithmetic forward instead of backward and the point
is sharper still:

```
2% risk, 1:3, 37% win rate:   +2.22% - 1.26% = +0.96% per trade
compounded over 402 trades:   46.6x  ->  $465,624
```

The simulation returned **less** than the naive compounding model predicts. No
pip, tick or contract-size error is needed to explain the figure, and none was
found: all six `Vol over` symbols verify at **$1.00 per unit of price per lot**
against the terminal's own `order_calc_profit`.

### Why it is still not a usable number

- It assumes the edge survives **402 consecutive trades** with no regime change.
- Position size at the end is ~32x the opening one. Max lot, margin and
  liquidity all bite long before that, and none of them are modelled.
- The result is dominated by its **last few trades** — the ones taken at the
  largest size, on the least evidence.
- A 37% win rate at 1:3 means long losing runs. The percentage drawdown is the
  number that decides whether the account survives; the dollar headline hides it.

**Read these at flat risk.** `scripts/run_synthetic_study.py` reports every run
twice — compounded (what the app shows) and flat (a fixed dollar risk off the
opening balance) — because flat is the only one that compares strategies rather
than exponents.

### What I could not check, and what I need

I could not audit the four saved runs trade-by-trade: they live in the VPS
database and the local copy stops at 2026-08-30. `scripts/audit_saved_backtest.py`
already does the per-trade work — ledger consistency, dollars-per-point
stability across trades, realised risk vs intended risk, and a flat re-pricing —
so **a copy of the VPS `algoedge.db` is all that is missing.**

---

## 2. A real 100x bug, on a different symbol

**`Volatility 75 Index`.** MT5 reports `trade_tick_value` 0.0001 against
`trade_tick_size` 0.01, implying **$0.01** per unit of price. The terminal's own
`order_calc_profit` says one lot over a 1.00 move is **$1.00**:

```
lots 1.0  move   0.01  ->  $0.01
lots 1.0  move   1.00  ->  $1.00      <- so tick_value should be 0.01, not 0.0001
lots 1.0  move 100.00  ->  $100.00
```

The app trusted the spec field, so **every V75 P&L computed with MT5 connected
was 100x too small.**

It only bit where MT5 is live. Offline research falls through to
`InstrumentProfile`, which carries the correct 0.01/0.01 — which is why the
entire historical corpus looks sane and only the VPS was wrong. That asymmetry
is why it survived this long, and why a test that only ran offline would never
have caught it.

**Fixed** (`d946583`): `_verified_tick_value()` checks the spec against
`order_calc_profit` once per symbol and trusts the calculator when they
disagree by more than 1%. Checked against 19 symbols; **V75 was the only
disagreement**. If the calculator cannot be reached the spec is returned
untouched — this must never be the thing that breaks sizing.

### A related trap worth knowing about

Any symbol MT5 cannot resolve falls through to `source=DEFAULT`, which carries
`tick_value 1.0 / tick_size 1e-05` — **$100,000 per unit of price**. It does not
produce inflated results, because the sizer refuses to size on DEFAULT and
returns zero lots. It produces **no trades at all**, silently. If a backtest
ever comes back empty for no visible reason, this is the first thing to check.

---

## 3. DEX indices: the 10-minute claim is an average, not a schedule

Deriv labels these *"Small spikes and major drops every 10 minutes on average"*,
which reads like a timetable. It is not one.

Measuring the gap between large moves across Jan–Oct 2026 (78,895 M5 bars per
symbol), at four different definitions of "large":

| Symbol | 1.5σ | 2σ | 3σ | 5σ |
|---|---:|---:|---:|---:|
| DEX 1500 DOWN | 1.03 | 1.00 | — | 0.98 |
| DEX 1500 UP | 0.97 | 1.00 | 0.99 | 0.99 |
| DEX 600 DOWN | 1.00 | 0.96 | — | 0.97 |
| DEX 600 UP | 0.96 | 0.96 | 1.00 | 0.95 |
| DEX 900 DOWN | 1.03 | 0.97 | — | 1.02 |
| DEX 900 UP | 0.98 | 0.99 | 0.97 | 1.01 |

The figure is the **coefficient of variation of the gaps**. A genuinely
scheduled event has CV near 0 — you can set a clock by it. A memoryless
(Poisson) process has CV of exactly 1.00.

Every cell sits between 0.95 and 1.03, at every threshold, on all six symbols.
**The process is memoryless: how long it has been since the last drop tells you
nothing about when the next one comes.** The "10 minutes" is a mean with no
timetable inside it.

**So there is no timing edge on DEX, and a scalper built around the clock would
be trading noise.** That is a well-powered negative — 78,895 bars per symbol,
six symbols, four thresholds — and it is worth more than a strategy built on the
premise would have been.

It does **not** rule out a directional edge. The DOWN indices may drift up and
drop like Crash, the UP indices mirror it like Boom, in which case the drift/jump
family applies to them. That is measured in §4, not assumed here.

---

## 4. Strategy results

> Running. `data/synthetic_study/RESULTS.md` holds the generated tables; this
> section carries the reading of them.

Scope, chosen rather than crossed exhaustively — a full 12 x 7 cross-product is
~14 hours of compute and most of the cells have no reason to exist:

- **`Vol over Crash/Boom` 400/550/750** against DriftJumpAlpha, BoomDriftJump and
  TrendDrift. The drift/jump family is what spike-and-drift instruments are for;
  TrendDrift is the honest control.
- **`DEX` 600/900/1500 UP/DOWN** against the same three, to test §3's open
  question.
- Classic Boom/Crash as the baseline to beat.

Every run reports: trades, win rate, P&L in dollars, return %, maximum drawdown
%, profit factor, expectancy in R, return-over-drawdown, and payoff — **flat and
compounded**.

---

## 5. Method, and two traps avoided

The harness drives the **real strategy classes** through `BarFeed` — the same
feeder the live scan loop uses, so the bar sequence is identical — and then the
**real `BacktestEngine`** through `backtester/runner.run_backtest`. It is not a
numpy reimplementation. `run_app_form_check.py` is the right tool for a
parameter sweep and the wrong one here, because the rewrite does not carry the
app's fills, costs, sizing floors or exit handling, and that is exactly where the
edge goes.

Two things that would have faked a result, both caught before any number was
produced:

1. **The spread column.** The exporter writes `spread_points`; the engine reads
   `spread` and treats it as points. The first version of the loader defaulted a
   missing `spread` to `0.0` — charging **no spread at all**, which on a
   synthetic index is most of the round trip. It is now a rename, and raises
   rather than defaults.
2. **R per trade.** A trade record carries no risk-dollars field. R is recomputed
   from entry, the **initial** stop and the filled volume through the app's own
   `calculate_risk_dollars`. Using the trailed stop instead would shrink R as a
   trade went well and flatter every winner.

---

## 5b. Time-series momentum: what the literature actually says

The anchor paper is **Moskowitz, Ooi & Pedersen, "Time Series Momentum",
*Journal of Financial Economics* 104(2), 2012, 228-250**. Read from the source
rather than summarised second-hand, because the specification matters and most
retail write-ups of it are wrong about the sizing.

### The rule, exactly

> "we consider whether the excess return over the past k months is positive or
> negative and go long the contract if positive and short if negative, holding
> the position for h months. We set the position size to be inversely
> proportional to the instrument's ex ante volatility."

- **Signal**: sign of the instrument's own excess return over the past *k*
  months. k = 12 is the headline.
- **Direction**: long if positive, short if negative. Nothing else — no filter,
  no confirmation, no pattern.
- **Hold**: h months, h = 1 for the headline. Rebalanced monthly.
- **Size**: scaled to a constant **ex-ante volatility target of 40% annualised**
  (`40% / σ_{t-1}`). The paper is explicit that 40% "is inconsequential" and
  chosen only so the numbers compare to the literature — but the *scaling
  itself* is not optional. It is what lets 58 instruments be added together.

### The sample

24 commodities, 12 cross-currency pairs (from nine currencies), nine developed
equity indexes and 13 developed government bond futures — **58 instruments,
January 1965 to December 2009**.

### The result

Significant time-series momentum in **every one of the 58 instruments**.
Predictability persists for **1 to 12 months and then partially reverses** over
longer horizons, which the authors read as initial under-reaction followed by
delayed over-reaction. The diversified portfolio runs at about **12% annualised
volatility** and delivers abnormal returns with little exposure to standard
factors, performing best in extreme markets.

### Why this does not transfer to the account as written

Three things, and they are the reason I would not simply port it:

1. **The alpha is substantially a diversification result.** Each instrument's
   own TSMOM is noisy; the paper's headline comes from averaging 58 weakly
   correlated bets. A handful of CFD symbols is not that portfolio, and the
   single-instrument Sharpes in the paper's own Fig. 1 are far below the
   diversified figure.
2. **It is monthly, over 45 years.** A rule that rebalances twelve times a year
   needs decades to establish significance. Jan-to-date is **nine monthly
   observations** — nowhere near enough to confirm or reject it, whatever
   number comes out.
3. **The reversal beyond 12 months is part of the finding.** Longer lookbacks
   are not safer, they are worse. Anyone "optimising" the lookback upward on a
   short sample will find the reversal and mistake it for a parameter.

### What is worth testing here

The honest version is the rule as specified — 12-month lookback, sign-only,
vol-scaled, monthly rebalance — across **every market the account can reach at
once**, scored as one portfolio rather than per symbol. That is the form the
evidence supports. A daily or intraday "TSMOM" on three synthetics would share
the name and none of the result.

This is queued, not done. It also needs daily bars back well beyond 2026-01-01
to say anything at all, which is a different export from the M5 set used above.

---

## 6. Still outstanding

- Audit of the four saved VPS runs — **needs the VPS database**.
- Strategy removals: IVW_v1, OvernightSession_v1, OpeningDrive_v1, HTFFVGFlip_v1.
- Time-series momentum: the literature and a measurement on these markets.
- FLOD/LLOD and ICC: both are discretionary ICT constructs; the first task is
  deciding whether they can be specified tightly enough to test at all.
- A scalping strategy for the majors.
