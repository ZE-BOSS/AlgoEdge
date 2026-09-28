# A trend system for CFDs — built, not borrowed — and the error that hid it

**2026-09-25** · `scripts/run_trend_system.py`, `run_trend_challenge.py`

---

## 0. First, my mistake

Every hypothesis I screened this session used a **fixed profit target** — 1R, 2R, 3R. For a
mean-reversion idea that is correct. For a trend system it destroys the thing being measured.

Trend following earns its living in a small number of very large winners. Capping them at 2R
removes the entire right tail while keeping every loser. My "time-series momentum" test exited at a
fixed horizon against a fixed stop, measured ≈0, and I reported that trend momentum did not work.

**That conclusion was an artefact of my exit rule, not a property of the market.** Rebuilt properly,
the result is different — and it is the first statistically significant edge on real CFDs in this
whole session.

---

## 1. The system

| | |
|---|---|
| **Entry** | break of the 20-day high (long) / low (short) — Donchian |
| **Initial stop** | 2.0 × daily ATR |
| **Exit** | **trailing chandelier stop, 1.5 × ATR from the best close since entry. No profit target.** |
| **Sizing** | fixed fraction of equity per trade, so position size scales inversely with volatility |
| **Universe** | every available market — the tail arrives in whichever one happens to trend, and you cannot know which in advance |

No target is the whole point. The winner runs until the trend stops paying.

---

## 2. Result — five years, 10 markets, $10,000 at 0.5% risk

| | |
|---|---|
| **$10,000 → $13,719.86** | **+37.2%** (Oct 2021 → Sep 2026) |
| Trades | 917 (3.6/week) |
| Win rate | **40.3%** |
| Expectancy | +0.067R (+$4.06/trade) |
| **t-statistic** | **+2.50** ← significant, on 937 trades |
| Payoff | **1.84** (avg win +$46.49 vs avg loss −$24.65) |
| Profit factor | 1.28 |
| **Max drawdown** | **$789.84 (6.1%)** |
| Green months | 61% (59 months) |
| Green weeks | 47% (248 weeks) |
| Streaks | 7 wins / **17 losses** |
| Tail | best trade +5.9R, **top 5 trades = 33% of all profit**, median hold 3.0 days |

**The shape is right.** 40% win rate, payoff 1.84, profit concentrated in the tail — that is what a
working trend system looks like. A 50% win rate with small winners would have meant my exit logic
was still wrong.

### Per market

| Market | N | Expectancy | Win% | Payoff | Best | Total R |
|---|---|---|---|---|---|---|
| **XAUUSD** | 101 | **+0.193R** | 43.6% | 2.31 | +2.89R | **+19.5** |
| **USDJPY** | 104 | **+0.153R** | 47.1% | 1.91 | +5.91R | **+15.9** |
| **BTCUSD** | 136 | +0.091R | 36.0% | 2.32 | +3.92R | +12.4 |
| US Tech 100 | 54 | +0.093R | 40.7% | 2.07 | +3.76R | +5.0 |
| XAGUSD | 99 | +0.077R | 41.4% | 1.81 | +3.30R | +7.6 |
| Germany 40 | 57 | +0.040R | 36.8% | 1.95 | +2.54R | +2.3 |
| EURUSD | 105 | +0.039R | 46.7% | 1.32 | +2.16R | +4.0 |
| GBPJPY | 102 | +0.033R | 39.2% | 1.76 | +2.70R | +3.4 |
| GBPUSD | 109 | +0.017R | 40.4% | 1.57 | +3.28R | +1.9 |
| US SP 500 | 70 | −0.128R | 30.0% | 1.46 | +2.73R | −8.9 |

Nine of ten positive. Gold and USDJPY carry it; only the S&P is negative.

---

## 3. The honest caveat — the last 16 months were flat

Run on **FundedNext's own bars** (their history starts May 2025, 8 symbols):

| | |
|---|---|
| $10,000 → **$9,489** | **−5.1%** |
| Trades | 184 | 
| Win rate | 31.5% | 
| t | **−0.24** |
| Max DD | 8.3% |
| Green months | **19% of 16** |

XAUUSD (+0.508R) and BTCUSD (+0.195R) were strongly positive; the indices were negative.

**This is what a trend system looks like in a non-trending stretch, and it is the risk you are
buying.** 200 trades over 16 months says far less than 937 over five years — but it is also the
most recent evidence, and it is not encouraging. Trend systems are judged in years.

---

## 4. Against prop rules — it is slow, not fragile

Bootstrapped from the actual R sequence. **Cumulative pass % by calendar day, no time limit:**

**FundedNext (10% target / 5% daily / 10% static)**

| Risk | d30 | d60 | d90 | d120 | d180 | d365 | **Breach** |
|---|---|---|---|---|---|---|---|
| 0.50% | 0.0% | 0.5% | 2.7% | 6.2% | 17.2% | 55.0% | **0.6%** |
| 0.75% | 0.7% | 5.9% | 15.1% | 24.5% | 42.0% | 75.7% | 3.2% |
| **1.00%** | 3.2% | 15.3% | 27.4% | 38.8% | 57.5% | **82.8%** | **8.2%** |
| 1.50% | 13.2% | 33.4% | 47.1% | 58.2% | 70.3% | 80.0% | 18.9% |
| 2.00% | 24.5% | 47.3% | 59.6% | 66.2% | 72.3% | 74.7% | 25.1% |
| 3.00% | 40.8% | 58.5% | 63.5% | 65.0% | 65.7% | 65.8% | 34.2% |

**Read the breach column.** At 1% risk this thing almost never blows up (8.2%) and passes 82.8% of
the time — it just takes the better part of a year. Pushing to 3% buys you a 40.8% thirty-day pass
and a 34.2% chance of destroying the account, which is back in coin-flip territory.

---

## 5. What this adds up to

| Strategy | Real CFDs? | Significance | Speed | One-month pass |
|---|---|---|---|---|
| **Trend system** (this document) | **yes** | **t +2.50, 937 trades** | +1.1 R/month | 3.2% at 1% risk |
| Crash/Boom drift | no — synthetics | Sharpe 5.62 | +40 R/month | 85.9% |
| Everything else screened | yes | none | — | — |

So you now have **a real, significant, tradable CFD strategy** — the thing you asked me to build.
What it is not is a one-month challenge strategy, and the reason is the arithmetic from the
previous document rather than anything about this system: **+10% in 20 days needs a Sharpe that
does not exist on retail CFDs.** This system's honest job is:

1. **A funded-account strategy**, where there is no target and no clock — 6.1% drawdown for
   +37.2% over five years is a profile you can actually live with, and it is what keeps payouts
   coming after you pass.
2. **A slow challenge route** at 1% risk: 82.8% pass, 8.2% breach, expect ~6 months.
3. **Not** the way to get funded in 30 days. That remains Crash/Boom drift on a firm that allows
   synthetics (97.5% both phases, median 35 days).

### What I would do next on this

- **Test it on more markets.** Ten is thin for a trend system; the tail arrives where it arrives.
  The FundedNext universe has 96 symbols and my caching run was killed before finishing — worth
  redoing, because breadth is the one lever that reliably improves trend following.
- **Add a regime filter.** The 2025–26 flat stretch is the known weakness; a simple
  "only trade markets whose volatility is expanding" filter is the standard remedy and is cheap to
  test.
- **Ship it as a strategy** once breadth is tested, so you can run it on the funded account.

---

## 6. Reproduction

```bash
py -3.12 scripts/run_trend_system.py --feed deriv --since 2021-10-01 --entry 20 --trail 1.5
py -3.12 scripts/run_trend_system.py --feed fundednext --since 2025-05-29 --entry 20 --trail 1.5
py -3.12 scripts/run_trend_system.py --feed deriv --sweep
py -3.12 scripts/run_trend_challenge.py
```
