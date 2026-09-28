# Published strategies, the real pass-time distribution, and the Crash/Boom question

**2026-09-25** · scripts: `run_published_strategies.py`, `run_orb_sweep.py`,
`run_prop_challenge_sim.py`, `run_synth_spike_check.py`

You were right that I kept answering with strategies already in the repo. This document
is the external research, implemented to the papers' own rules and tested on your two feeds.

---

## 0. The three answers

| | |
|---|---|
| **A researched strategy that hits +20R/month?** | **Not found.** Both published strategies were implemented and tested — the 5-minute ORB returns **−2.3 R/month at best** across every setting, and intraday momentum **−2.7 R/month**. The reason is specific and measurable, and it is not "the paper is wrong" (§2.3). |
| **Pass rates with the 45-day clock removed?** | **This is the real finding.** The ORB edge you already have passes **99.0% of the time at 0.5% risk — it just needs ~72 days.** The 45-day limit, not the edge, is what fails you (§3). |
| **Crash/Boom: add to an existing strategy or build new?** | **Neither — you already have it.** `DriftJumpAlpha_v1` and `BoomDriftJump_v1` are exactly this trade. The zone framing added nothing; the control earns more (§4). |

---

## 1. What I researched

| Paper | Claim | Reported |
|---|---|---|
| Zarattini & Aziz (2023), *Can Day Trading Really Be Profitable?* | 5-minute opening range breakout on QQQ, 2016–2023 | 1,484% vs 169% buy-and-hold, **Sharpe 2.4**, beta ≈ 0 |
| Zarattini, Barbon & Aziz (2024), *A Profitable Day Trading Strategy for the U.S. Equity Market* | Same, on a 7,000-stock universe, top-20 "Stocks in Play" | >1,600%, **Sharpe 2.81**, 36% annualised alpha |
| Gao, Han, Li & Zhou (2018), *Market Intraday Momentum*, JFE | First half-hour return predicts the last half-hour return | Predictive R² **1.6%** (2.6% with the 12th half-hour); stronger on volatile, high-volume, news days |
| Osler — FX price clustering / stop cascades | Reversals at round numbers, rapid trending after crossing | (tested and rejected in the previous study) |

The strategies were implemented to the stated rules, not approximations:

**5-minute ORB** — signal is the first 5-minute bar of the session (up bar → long, down → short,
no trade if unchanged); entry at the open of the second bar; stop at the opposite extreme of the
first bar; target N×R, otherwise flat at the session close.

**Intraday momentum** — sign of the first 30 minutes; enter 30 minutes before the close; exit at
the close. A stop was added (the paper trades it unlevered) because a prop account has a drawdown
limit and an unstopped position has no R to size against.

Sessions are **detected from the data** — the minute-of-day where volume peaks — so the same code
works on a 24-hour CFD and a cash index without hand-set clock times, DST or broker offsets.

---

## 2. Results — both fail here, and the reason is measurable

### 2.1 Per market, Deriv feed, 2026 YTD

| Market | Strategy | N | Expectancy | Win | t | R/month |
|---|---|---|---|---|---|---|
| XAUUSD | ORB 5m, 10R | 224 | +0.126R | 14.7% | +0.59 | **+3.22** |
| XAUUSD | intraday mom | 224 | +0.030R | 53.1% | +0.63 | +0.77 |
| BTCUSD | ORB 5m | 264 | −0.084R | 11.4% | −0.46 | −2.53 |
| GBPUSD | intraday mom | 228 | +0.058R | 49.6% | +1.12 | +1.51 |
| EURUSD | ORB 5m | 222 | −0.534R | 14.0% | −1.83 | −13.55 |
| USDJPY | ORB 5m | 224 | −1.285R | 14.7% | −2.40 | −33.03 |
| GBPJPY | ORB 5m | 225 | −1.294R | 10.7% | −4.29 | −33.30 |

As portfolios on $10,000 at 0.5%: **ORB 5m → −98.8%** (13.3% win rate, 46-trade losing streak);
**intraday momentum → −8.8%** (47.1% win, PF 0.97). Only XAUUSD is positive on ORB, at +3.22
R/month and t +0.59 — not significant.

### 2.2 The sweep: no setting works

Portfolio R/month across every opening-range length × target:

| Range | tgt 1R | tgt 2R | tgt 3R | tgt 5R | tgt 10R |
|---|---|---|---|---|---|
| **5m** | −143.5 | −127.7 | −115.6 | −116.9 | −92.8 |
| **15m** | −73.9 | −71.9 | −67.2 | −66.4 | −34.9 |
| **30m** | −34.7 | −27.8 | −25.5 | −27.3 | −28.6 |
| **60m** | **−2.3** | −9.3 | −15.1 | −8.5 | −11.7 |
| **120m** | −10.0 | −7.3 | −8.6 | −13.4 | −14.2 |

Every cell is negative. The best is 60-minute range at 1R: −2.3 R/month, expectancy −0.009R,
t −0.43. A challenge needs **+20 R/month**.

Note the shape: results improve monotonically as the range lengthens from 5m to 60m. That is the
clue.

### 2.3 Why: cost as a fraction of R

The opening-range stop *is* R. A 5-minute bar on EURUSD is a few pips; the spread is one.

| Market | 5m | 15m | 30m | 60m | 120m |
|---|---|---|---|---|---|
| GBPJPY | **12.0%** | 7.5% | 5.9% | 4.0% | 3.4% |
| XAGUSD | **11.1%** | 6.8% | 4.7% | 3.5% | 3.0% |
| EURUSD | **9.4%** | 5.5% | 3.9% | 2.9% | 2.1% |
| USDJPY | 8.4% | 5.3% | 4.0% | 2.5% | 2.2% |
| US Tech 100 | 2.1% | 1.2% | 0.9% | 0.6% | 0.6% |
| BTCUSD | 2.0% | 1.2% | 0.8% | 0.6% | 0.5% |

**On GBPJPY the 5-minute ORB starts 12% of R in the hole on every trade.** At a 14% win rate,
where the edge must come from rare large winners, a 12% haircut on each attempt is decisive.

### 2.4 The deeper reason — the alpha is in the selection, and you cannot reproduce it

The 2024 paper's headline is not the ORB rule. It is *which* instruments it is applied to: the
top-20 **"Stocks in Play"** each day, chosen from **more than 7,000 US stocks** by relative volume
after news. The paper states plainly that these "significantly outperform regular stocks".

That is a **cross-sectional selection effect**. It needs a universe wide enough that, on any given
day, twenty names are doing something genuinely unusual.

Your instrument set is 96 symbols on FundedNext and a fixed handful of majors. There is nothing to
select from — you are trading EURUSD whether or not EURUSD is in play. The rule survives the
transfer; **the selection step, which is where the alpha lives, does not.**

To run this strategy as published you would need an equities broker with a few thousand symbols
and a relative-volume scanner — not a CFD prop account.

---

## 3. The pass-time distribution, with the clock removed

You asked what happens without the 45-day limit. This is the most useful table in the document.
3,000 Monte Carlo challenges per cell, all other FundedNext rules enforced.

### 3.1 ORB 60m + H1 trend — the edge you already have (≈35% at 1:3, 0.6 trades/day)

| Risk | d30 | d45 | d60 | d90 | d120 | d180 | **d365** | **Breached** |
|---|---|---|---|---|---|---|---|---|
| 0.25% | 0.0% | 0.5% | 2.1% | 12.0% | 28.6% | 62.2% | **96.9%** | **0.0%** |
| **0.50%** | 8.5% | 22.5% | 39.1% | 62.5% | 78.7% | 91.8% | **99.0%** | **0.7%** |
| 0.75% | 22.5% | 41.4% | 56.4% | 76.0% | 86.1% | 94.0% | 96.7% | 3.3% |
| 1.00% | 34.5% | 52.2% | 65.0% | 79.4% | 86.9% | 91.0% | 92.3% | 7.7% |
| 1.50% | 40.2% | 56.6% | 66.1% | 76.7% | 80.5% | 82.9% | 83.5% | 16.5% |
| 2.00% | 40.3% | 53.0% | 59.9% | 67.5% | 69.8% | 71.2% | 71.4% | 28.6% |
| 3.00% | 37.2% | 49.3% | 56.3% | 62.0% | 64.0% | 65.0% | 65.2% | 34.8% |

**Read the 0.50% row.** Given enough time, this edge passes **99 times out of 100** and blows up
0.7% of the time. Under a 45-day clock it passes 22.5%. **The strategy is not the problem — the
deadline is.** Median time to pass at 0.5% risk: 72 days.

And the trade-off is stark: raising risk to 2% barely improves the 30-day number (40.3% vs 8.5%)
while multiplying breach probability by 40×, and *lowering* the eventual pass rate from 99.0% to
71.4%.

### 3.2 A no-edge coin flip, for calibration

| Risk | d30 | d45 | d90 | d365 | Breached | Still going |
|---|---|---|---|---|---|---|
| 0.25% | 0.0% | 0.0% | 0.1% | 34.8% | 0.4% | 64.9% |
| 0.50% | 0.2% | 1.7% | 14.5% | 73.8% | 9.1% | 17.1% |
| 1.00% | 17.5% | 31.3% | 54.9% | 75.2% | 24.5% | 0.3% |
| 2.00% | 39.4% | 48.4% | 57.2% | 59.7% | 40.2% | 0.0% |

A pure coin flip passes 75% of the time at 1% risk if you give it a year. **Time launders luck.**
This is why "I passed a challenge" carries so little information, and why firms sell resets.

### 3.3 The profile that beats the clock (45% at 1:2, 3 trades/day, +22R/month)

| Risk | d30 | d45 | d60 | d90 | Breached |
|---|---|---|---|---|---|
| 0.25% | 31.3% | 70.4% | 89.2% | 98.9% | 0.0% |
| **0.50%** | **84.1%** | 96.2% | 99.0% | 99.8% | **0.2%** |
| 0.75% | 89.2% | 96.9% | 98.5% | 99.1% | 0.9% |
| 1.00% | 89.8% | 95.4% | 96.6% | 96.7% | 3.2% |

### 3.4 What this means in practice

1. **Pick a firm with a long or no time limit, or plan for two attempts.** At 0.5% risk your
   existing ORB edge is a near-certainty over 6–12 months and almost never breaches.
2. **Do not raise risk to beat the clock.** Every profile shows P(pass) peaking around 1–1.5% and
   then falling while breach probability keeps climbing.
3. **The only honest way to pass in 30 days is frequency** — more uncorrelated slots, not more
   risk per slot.

---

## 4. Crash / Boom — new strategy, or add to an existing one?

**Neither. You already have it, twice.** `DriftJumpAlpha_v1` (Crash) and `BoomDriftJump_v1` (Boom)
are exactly this trade: capture the drift, respect the spike. The zone study's contribution was to
confirm that the *level* has nothing to do with it — the non-round control earned **+409%** against
the round levels' **+361%**.

So: do not build a zone strategy for Crash/Boom, and do not bolt zones onto the existing ones.
What the study *did* usefully establish is the size of the drift edge and what it costs under
honest fills.

### 4.1 Full monetary breakdown — $10,000 at 0.5%, measured spike fills

Crash 1000 @ 50 · Boom 1000 @ 100 · Boom 900 @ 100, Jan → 25 Sep 2026.

| | |
|---|---|
| **$10,000 → $46,151.87** | **+361.5%** |
| Trades | 3,770 (98.8/week) |
| Win rate | 57.0% |
| Profit factor | 1.19 |
| Expectancy | +$9.59/trade |
| Average win / loss | +$105.52 / −$117.73 |
| Best / worst trade | +$241.23 / **−$493.63** |
| **Max drawdown** | **$3,059 (10.8%)** |

**Month by month**

| Month | Trades | P&L | % | Win rate |
|---|---|---|---|---|
| 2026-01 | 423 | **−$23.78** | −0.24% | 54% |
| 2026-02 | 395 | +$1,908.01 | +19.13% | 57% |
| 2026-03 | 416 | +$560.97 | +4.72% | 54% |
| 2026-04 | 417 | +$4,304.53 | +34.59% | 60% |
| 2026-05 | 443 | +$5,469.79 | +32.66% | 59% |
| 2026-06 | 453 | +$1,802.82 | +8.11% | 55% |
| 2026-07 | 462 | +$6,056.53 | +25.21% | 58% |
| 2026-08 | 473 | +$9,853.61 | +32.76% | 59% |
| 2026-09 | 288 | +$6,219.39 | +15.57% | 58% |

**Only one losing month (January, −0.24%), and it is essentially flat.**

**Weekly**

| | |
|---|---|
| Weeks | 39 |
| Green weeks | **72%** (28 of 39) |
| Losing weeks | 11, totalling **−$3,807**, averaging −$346 |
| Best week | **+$4,143** |
| Worst week | **−$1,066** |
| Longest winning-week streak | 12 |
| Longest losing-week streak | 3 |

**Streaks and consecutive-loss drawdown**

| | |
|---|---|
| Longest winning trade streak | 12 |
| Longest losing trade streak | **10** |
| Longest winning-month streak | 8 |
| Longest losing-month streak | 1 |
| Max drawdown | $3,059 (**10.8%**) |

At 0.5% risk a 10-trade losing run is ~5% of the account before the spike-fill overshoot; the
measured 10.8% peak-to-trough reflects that some of those losses fill **worse than −1R** (worst
trade −$493 against an intended risk of ~$50–200).

### 4.2 The two warnings that go with those numbers

1. **The fill assumption is worth more than half the result.** With stops filling at the stop —
   what a bar backtest does by default — the same trades return **+1,065%** ($116,509). Charging
   the repo's tick-measured overshoot (Crash 0.403, Boom 0.353) takes it to +361%. **$70,357 of
   the headline was an execution assumption.**
2. **Boom 900 has no measured spike profile** (`get_spike_fill` returns `None`), so its stops are
   still filled at the stop even in the +361% figure. That leg is optimistic by an unknown amount.

A 10.8% drawdown would also **breach a FundedNext 10% static limit** — this is a personal-account
strategy, not a challenge strategy.

---

## 5. What I would do

1. **Stop looking for a new strategy to beat the 45-day clock.** §3.1 shows the edge you have
   passes 99% of the time at 0.5% risk given six months. Choose the challenge terms to fit the
   edge, rather than an edge to fit the terms.
2. **Do not port the published ORB to CFDs.** Every setting loses, and §2.3–2.4 explain why: costs
   are 2–12% of R at a 5-minute stop, and the paper's alpha is a selection effect over 7,000
   stocks that 96 CFD symbols cannot reproduce. If you want this strategy, it needs an equities
   broker and a relative-volume scanner.
3. **For Crash/Boom, use the strategies you already have** (`DriftJumpAlpha_v1`,
   `BoomDriftJump_v1`) on a personal account, with the spike-fill model on, and size for a
   10-trade losing streak where losses exceed 1R. Do not run it in a challenge with a 10% static
   floor.
4. **The frequency problem remains the real one.** Passing in 30 days needs ~+20R/month, which
   needs 6–10 uncorrelated slots. That test — the existing ORB across every market it was measured
   profitable on, priced with FundedNext's real spreads — is still the highest-value job and is
   still not done.

---

## 6. Reproduction

```bash
py -3.12 scripts/run_published_strategies.py --feed deriv        # §2.1
py -3.12 scripts/run_published_strategies.py --feed fundednext
py -3.12 scripts/run_orb_sweep.py --feed deriv                   # §2.2, §2.3
py -3.12 scripts/run_prop_challenge_sim.py --horizon 365 --trials 3000   # §3
py -3.12 scripts/run_synth_spike_check.py                        # §4.1
```

**Sources:**
[Zarattini & Aziz, *Can Day Trading Really Be Profitable?*](https://www.semanticscholar.org/paper/Can-Day-Trading-Really-Be-Profitable-Evidence-of-in-Zarattini-Aziz/4d55f526cc56f08662cb8976796cd3b719ef6d2b) ·
[Zarattini, Barbon & Aziz, *A Profitable Day Trading Strategy for the U.S. Equity Market*](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4729284) ·
[Gao, Han, Li & Zhou, *Market Intraday Momentum* (JFE)](https://www.sciencedirect.com/science/article/abs/pii/S0304405X18301351) ·
[Concretum Group summary](https://concretumgroup.com/a-profitable-day-trading-strategy-for-the-u-s-equity-market/)
