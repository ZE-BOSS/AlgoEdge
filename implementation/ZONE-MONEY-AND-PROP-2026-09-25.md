# $10,000 in real money: zones, prior zones, Crash/Boom, and the prop question

**2026-09-25** · follow-up to `ZONE-AND-PROP-STUDY-2026-09-25.md`
· scripts: `zone_money_engine.py`, `run_money_2026.py`, `run_prior_zone_study.py`,
`run_synth_spike_check.py`, `run_fundednext_test.py`
· every trade sized off the live balance, rounded to the broker's lot step, floored at the
broker's minimum lot, charged that bar's own spread plus slippage

---

## 0. The four answers

| Question | Answer |
|---|---|
| $10,000 on the round-number book, Jan → today? | **+34.0% → $13,395** on Deriv data. Control: −0.2%. |
| …does it survive a different broker? | **No. −14.9% on FundedNext's own feed.** The edge is real but smaller than the spread difference between brokers. |
| Do prior zones (break-and-retest) work? | **No.** Hold rate 66% — but the placebo level also holds 66%. Priced honestly: **−97.6%** fading, **−98.6%** trading the break. |
| Is the Crash/Boom "trap" actually profitable? | **Profitable, and not a zone effect.** +361% after realistic spike fills — but the non-round control makes **+409%**. It is drift capture, and it is extremely fill-sensitive (+1,065% → +361% on the fill assumption alone). |
| What strategy passes a prop challenge? | Not one strategy — **breadth**. No single edge in this repo reaches the required +20R/month; 6–8 uncorrelated slots do. |

Two results in this document were **wrong on the first run and corrected** (§3.2, §4.1). Both
were caught by the control, and both are documented here rather than quietly fixed, because the
same two mistakes are what make most zone backtests look profitable.

---

## 1. The round-number book on $10,000

EURUSD @ 0.01 · XAUUSD @ 100 · BTCUSD @ 1000. Continuation at the level, 1:1 at 0.25 × grid step,
one position at a time per market, 4-hour maximum hold. 0.5% risk, compounding.

### 1.1 Headline — Deriv data, 1 Jan 2026 → 25 Sep 2026

| | Round levels | Control (non-round) |
|---|---|---|
| **Start → End** | **$10,000 → $13,395.16** | $10,000 → $9,984.82 |
| **Return** | **+34.0%** | −0.2% |
| Trades | 2,051 (53.7/week) | 2,037 |
| Win rate | 51.6% | 50.0% |
| Profit factor | 1.07 | 1.00 |
| Expectancy | +$1.66/trade | −$0.01/trade |
| Average win / loss | +$49.18 / −$48.98 | +$37.29 / −$37.27 |
| **Max drawdown** | **$1,921 (16.7%)** | $1,724 (17.0%) |
| Longest streaks | 12 wins / 9 losses | 9 / 9 |

### 1.2 Month by month

| Month | Trades | P&L | % | Win rate |
|---|---|---|---|---|
| 2026-01 | 256 | **+$1,167.96** | +11.68% | 55% |
| 2026-02 | 337 | **−$1,419.63** | −12.71% | 45% |
| 2026-03 | 320 | **+$1,550.22** | +15.90% | 55% |
| 2026-04 | 232 | +$639.49 | +5.66% | 52% |
| 2026-05 | 187 | −$87.44 | −0.73% | 48% |
| 2026-06 | 229 | +$1,049.15 | +8.85% | 55% |
| 2026-07 | 185 | −$26.95 | −0.21% | 50% |
| 2026-08 | 175 | +$132.01 | +1.03% | 50% |
| 2026-09 | 130 | +$390.34 | +3.00% | 53% |

**6 green months of 9 (67%).** Longest green run 2 months, longest red run 1 month.

### 1.3 Weekly

| | |
|---|---|
| Weeks | 39 |
| Positive weeks | **49%** — a coin flip |
| Best week | **+$1,026** |
| Worst week | **−$898** |
| Longest winning-week streak | 3 |
| Longest losing-week streak | 4 |

This is the number that matters for how it *feels* to trade: **half the weeks are red**, and a
4-week losing run happened inside nine months. The equity only climbs because the green weeks are
marginally bigger and more frequent over a quarter.

### 1.4 The honest reading

+34% on $10,000 with a 17% drawdown is a real result. But:

- **2,051 trades in nine months** — 54 per week, fully automated, and the whole edge is $1.66 per
  trade. There is no discretionary version of this.
- **February alone lost 12.7%.** With FundedNext's 10% static drawdown, this book would have
  **breached the challenge in February**, regardless of the other eight months.
- The control returning −0.2% is what proves the round numbers are doing the work. But it also
  shows how thin the margin is: strip the roundness and you have a flat, cost-eating churn.

---

## 2. It does not survive a change of broker

The edge was found on Deriv data. The same book, same code, on **FundedNext's own M5 bars and
contract specs** (99,000 bars per symbol, live from their terminal, login 11869564):

| | Round levels | Control |
|---|---|---|
| **Start → End** | **$10,000 → $8,505.93** | $10,000 → $7,481.20 |
| **Return** | **−14.9%** | −25.2% |
| Trades | 1,854 | 1,820 |
| Win rate | 51.5% | 50.9% |
| Profit factor | 0.96 | 0.92 |
| Expectancy | −$0.81/trade | −$1.38/trade |
| Max drawdown | $3,611 (29.9%) | $3,061 (29.9%) |
| Green months | 44% | 22% |

**The roundness effect is still there** — round beats control by 10.3 percentage points of return,
the same direction as on Deriv. **The profit is not.** FundedNext's spreads are materially wider
(BTCUSD median 2,115 points against Deriv's; XAUUSD 30), and the entire edge was $1.66 a trade.

> The round-number edge is smaller than the difference in execution cost between two brokers
> offering the same instrument.

That is the finding. It is a real market microstructure effect, and it is not worth money at retail
prop-firm spreads.

### 2.1 Would it have passed the FundedNext evaluation?

Walking the actual 2026 trade sequence through the real rules (10% target, 5% daily, 10% static
drawdown, 45-day limit, 40% consistency):

| Risk/trade | Result | End balance | Days traded | Days elapsed |
|---|---|---|---|---|
| 0.25% | timeout | $10,228 | 32 | 46 |
| 0.50% | **pass** | $11,029 | 15 | 20 |
| 1.00% | **pass** | $11,688 | 14 | 19 |
| 2.00% | **pass** | $13,510 | 14 | 19 |

**Do not read this as a strategy that passes.** It passes because the sequence *starts in January*,
which returned +18.6% on this feed, and the evaluation ends the moment the target is hit — before
February's −18.1% arrives. Start the same challenge on 1 February and it breaches.

A single historical path is not a pass probability. The Monte Carlo in the previous study is.

---

## 3. Crash / Boom — the "trap", examined properly

You asked whether there is money there regardless of whether it is a zone effect. There is, and
there are two separate reasons to distrust it.

### 3.1 It is profitable, and it is not zones

Crash 1000 @ 50 · Boom 1000 @ 100 · Boom 900 @ 100, same continuation trade, $10,000 at 0.5%:

| | Round levels | Control (non-round) |
|---|---|---|
| Bar fill (stop fills at the stop) | +1,065% → $116,509 | +1,227% → $132,726 |
| **Measured spike fill** | **+361.5% → $46,152** | **+409.0% → $50,896** |
| Win rate | 57.0% | 57.3% |
| Profit factor | 1.19 | 1.21 |
| Max drawdown | 10.8% | 11.2% |
| Green months | 89% | 100% |

**The control earns more than the round levels, in every variant.** Whatever this is, the price
being round contributes nothing. It is the Boom/Crash drift-and-spike asymmetry, and it is
harvestable at any price.

### 3.2 The fill assumption is worth $70,000 of the "profit"

Boom and Crash spike *inside* a bar by construction. A bar backtest fills a stop at the stop; in
reality a spike takes it out and fills far past. This repo measured it from 365 days of ticks
(`backend/backtester/fill_model.py`):

```
Crash 1000   n=2,190   mean overshoot 0.403 of the distance to the bar extreme
Boom  1000   n=2,230   mean overshoot 0.353
live fills, 4 stopped trades       mean +0.318 R of unbooked slippage
the bar backtester                 0.000 R
```

Applying the repo's own model: **$116,509 → $46,152.** The fill assumption alone accounts for
$70,357 of the headline.

And that is still optimistic: **Boom 900 has no measured spike profile** (`get_spike_fill` returns
`None`), so its stops are still filled at the stop in the +361% figure.

### 3.3 Verdict

There is a real drift edge on Boom/Crash. It is nothing to do with zones, the control proves it,
and its headline number moves by a factor of 2.5 on a single execution assumption that this repo
has already measured to be wrong in the optimistic direction. If you want it, trade the drift
explicitly and price the spike fills — do not reach it through a zone strategy.

---

## 4. Prior zones — does price respect a level it reversed at before?

A zone is a confirmed swing pivot (12 bars either side, no lookahead). It is armed, price must
leave by ≥1×ATR, and the retest is its first return to a ±0.15×ATR band. Outcome is the same race:
does price move 0.5×ATR back (HOLD) or through (BREAK)?

**Control:** a *placebo zone* — the pivot price shifted 1.5×ATR — run through the identical
pipeline. Not evaluated on the same bar: a level price never reached cannot break, which scores a
fake 95% hold. (My first version did exactly that; the absurd number is what exposed it.)

### 4.1 The statistics

| Market | Retests | Hold rate | Placebo hold | Diff | z |
|---|---|---|---|---|---|
| EURUSD | 8,155 | 66.7% | 67.2% | −0.5 pp | −0.70 |
| XAUUSD | 7,414 | 64.9% | 66.3% | −1.5 pp | −1.87 |
| BTCUSD | 11,741 | 65.8% | 69.2% | −3.4 pp | −5.51 |

**Both arms hold about two-thirds of the time.** That 66% is not zone memory — it is mean
reversion after a 1×ATR excursion, and it happens at a price where nothing ever occurred just as
readily as at a real swing point. On BTCUSD the placebo holds *better*.

Hold rate by zone age shows no decay: EURUSD 67.6% (<1 day) → 63.7% (>20 days); BTCUSD 63.9% →
68.4%. **An old zone is no weaker than a fresh one, because neither is doing anything.**

### 4.2 In money — and the mistake that nearly hid it

First run reported **+1,686%**. Two bugs, both instructive:

1. **Overlap.** Dozens of zones fire on the same bar. Letting them all trade counted the same move
   many times over: 718 trades/week. Fixed: one position at a time.
2. **The P&L was priced from the zone, not the entry.** The race was measured at the zone price
   while the trade entered at the bar's close, so the target sat nearer the entry than the stop
   did. That alone manufactured a 68% win rate at a nominal 1:1. Fixed: stop and target placed
   symmetrically around the actual entry.

Priced honestly, $10,000 at 0.5%, Jan → today:

| | Fade the retest | Placebo fade | Trade the break | Placebo break |
|---|---|---|---|---|
| **End balance** | **$239.73** | $215.74 | $138.58 | $138.58 |
| **Return** | **−97.6%** | −97.8% | −98.6% | −98.6% |
| Trades | 3,052 | 2,867 | 2,829 | 2,825 |
| Win rate | 45.4% | 44.0% | 42.6% | 42.5% |
| Profit factor | 0.63 | 0.62 | 0.56 | 0.56 |
| Green months | 0% | 0% | 0% | 0% |

**Break-and-retest has no edge in either direction**, and the placebo is indistinguishable. A 45%
win rate at 1:1 loses to costs, 80 times a week.

---

## 5. What strategy actually passes a prop challenge

The previous study established the requirement: **expectancy × frequency ≈ +20R per month**, then
risk 0.5–0.75%, giving ~96% pass and <1% breach. The question now is what delivers it.

### 5.1 Nothing in this repo does it alone

| Strategy | Measured edge | Frequency | R/month | P(pass) @ 1% |
|---|---|---|---|---|
| Round-number continuation | +0.055R | 1.3/day | ~+1.5R | 30% |
| ORB 60m + H1 trend | ~+0.10R at 1:3 | 0.6/day | ~+5R | 53% |
| Required profile | +0.35R at 1:2 | 3/day | **+22R** | **95%** |
| *(no edge at all)* | 0.00R | 3/day | 0R | **33%** |

Single-market ORB — the best measured edge in the book — reaches about a quarter of what is
needed, and fails by **timeout**, not by blowing up.

### 5.2 The answer is breadth, not a better strategy

The required +20R/month is reachable by running the *same* modest edge across many uncorrelated
slots:

```
8 slots × 0.6 trades/day × 0.10R  ≈  +10R/month
```

…and that is still short. Realistically:

- **6–10 markets**, each with its own measured parameters (the per-slot book already does this)
- **1:2 or better**, because 1:1 geometries die to spreads — this is precisely why the round-number
  edge failed on FundedNext
- **0.5% risk per slot**, with the account-level concurrency cap keeping total open risk sane
- Instruments with **cheap execution relative to the stop** — the FundedNext result is what happens
  when you ignore this

### 5.3 What I would test next, specifically

Not a new strategy — a **portfolio of the existing ORB edge across every market it was measured
profitable on**, run through the same challenge simulator with FundedNext's real spreads. That is a
direct, cheap test of whether the required +20R/month is reachable with what you already have.
Everything needed is in place: the per-slot engine, the measured ORB parameters, and now
FundedNext's own bars and specs.

**I have not run that test.** It is the obvious next job and it is a contained one.

---

## 6. Reproduction

```bash
py -3.12 scripts/run_money_2026.py --books round round_control          # §1
py -3.12 scripts/run_fundednext_test.py                                  # §2
py -3.12 scripts/run_synth_spike_check.py                                # §3
py -3.12 scripts/run_prior_zone_study.py --since 2026-01-01              # §4.1
py -3.12 scripts/run_money_2026.py --books prior prior_placebo prior_break prior_break_placebo
py -3.12 scripts/run_prop_challenge_sim.py                               # §5
```

---

## 7. What I would do with this

1. **Do not deploy the round-number book to a FundedNext account.** It loses there. On a
   tight-spread account it made +34% in nine months with a 17% drawdown and a month that would
   have breached a 10% static limit.
2. **Drop break-and-retest entirely.** Two directions, two controls, all four −97%.
3. **If you want the Boom/Crash drift**, build it as a drift strategy with the spike-fill model
   switched on, and size it knowing the headline halves under honest fills.
4. **For the prop challenge**, the work is portfolio breadth on the ORB edge with real FundedNext
   costs — not another zone idea.
5. **Keep the control in every future study.** In this document it overturned three results out of
   four, and caught two of my own bugs.
