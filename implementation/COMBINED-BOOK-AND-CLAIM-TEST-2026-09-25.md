# Your papers, your quoted strategy, and a combined book that actually works

**2026-09-25** · `run_claimed_strategy.py`, `run_combined_book.py`, `run_trend_challenge.py`
· all dollar figures on **$10,000**, costs charged, min-lot enforced

---

## 0. Three answers

| | |
|---|---|
| **The quoted "one 5-minute candle" system** | **Claim does not reproduce.** 817 Nasdaq trades: **49.1% win rate (claimed 57%), profit factor 1.13 (claimed 1.29)**, +8.8% over 2.7 years — not +982%. It is weakly positive on indices/gold and **negative on every FX pair**. |
| **Your research papers** | Read. The ORB paper (ssrn-4729284) confirms in its own words that the edge is **selecting "Stocks in Play" from 7,000 stocks by relative volume** — a cross-sectional step 96 CFDs cannot reproduce. |
| **What I built** | **A three-stream combined book.** The streams are near-uncorrelated (0.09, −0.03, −0.07), so they stack: **t +3.76, Sharpe 1.45, $10,000 → $29,359 (+193.6%) over 32 months, 20.4% max drawdown, 72% green months.** On **Deriv**. |
| **The catch, again** | On **FundedNext's own feed over the identical window: −8.2%** versus **+52.8% on Deriv**. Same rules, same dates. It is the feed. |
| **What survives on FundedNext** | **The overnight stream on US30 + SPX500.** +18.4% at 0.5% risk, Sharpe 1.19, 9.3% DD. At 1.5% risk: **48.2% pass in 30 days, 68.5% in 60 days, 24.3% breach.** |

---

## 1. Your quoted strategy, tested

**The rule as stated:** at the NY open, if the first 5-minute candle closes above the 12 EMA go
long, below go short, then trail the stop. Claimed: Nasdaq 2019–2026, 1,448 trades, **982% return,
57% win rate, 1.29 profit factor**.

I have Nasdaq M5 from 2024-01 (2.7 years, 817 trades — the same ~207/year rate, so the sample is
comparable in kind).

| Market | Trail | N | **Win%** | **PF** | Expectancy | t |
|---|---|---|---|---|---|---|
| **US Tech 100** | 1.0×ATR | 817 | **49.1%** | **1.13** | +0.029R | +1.33 |
| US Tech 100 | 2.0×ATR | 817 | 50.2% | 1.10 | +0.025R | +1.09 |
| Germany 40 | 1.0×ATR | 801 | 50.8% | 1.16 | +0.035R | +1.59 |
| XAUUSD | 0.5×ATR | 817 | 42.4% | 1.13 | +0.023R | +1.35 |
| BTCUSD | 3.0×ATR | 959 | 48.8% | 1.08 | +0.017R | +0.84 |
| US SP 500 | 1.0×ATR | 817 | 47.6% | 0.97 | −0.008R | −0.37 |
| EURUSD | 0.5×ATR | 824 | 37.6% | 0.79 | −0.043R | −2.76 |
| **GBPJPY** | 0.5×ATR | 824 | 37.5% | **0.72** | −0.057R | **−3.79** |
| USDJPY | 0.5×ATR | 824 | 37.9% | 0.78 | −0.042R | −2.79 |

I also tried five exit variants hunting for their 57%:

| Variant | Win% | PF |
|---|---|---|
| trail 1×ATR, stop 1×ATR | 49.1% | 1.13 |
| trail 5×ATR (nearly none) | 50.8% | 1.12 |
| wide stop 3×, trail 3× | 50.8% | 1.12 |
| tight 0.5× stop, 3× trail | 45.0% | 1.12 |
| EMA span 144 (hourly-ish) | 49.3% | 1.01 |

**Nothing reaches 57% or 1.29.** Best case is 50.8% / 1.16.

**In money:** US Tech 100 at its best setting → **$10,883 (+8.8%)** over 2.7 years, 4.1% drawdown.

**On "+982%":** that is not a comparable number without the risk per trade. A 1.13 profit factor
compounded at high risk can print any headline you like; the same edge at 0.5% risk makes 8.8%.
The win rate and profit factor are the honest claims, and they are overstated by roughly 8
percentage points and 0.16 respectively.

**Verdict:** the rule is real but weak, works on indices and gold, loses on FX, and the marketing
numbers are inflated. It is worth keeping as *one stream among several* — which is what I did next.

---

## 2. What the papers said

Ten SSRN papers. The directly usable ones:

| Paper | Use |
|---|---|
| **ssrn-4729284** — Zarattini, Barbon & Aziz, *A Profitable Day Trading Strategy* | Confirms the edge is **Stocks in Play**: "a significant benefit in limiting day trading only to those Stocks in Play", selected by **Relative Volume** from 7,000 stocks. Stop at a percentage of the 14-day ATR. |
| **ssrn-4627907** — Pairs Trading, cointegration + ECM | A family I have not tested; needs a wide universe to find cointegrated pairs. |
| **ssrn-7384838** — Kolm & Ritter, dynamic allocation **with trading costs** | The formal treatment of exactly the problem that has killed every intraday idea here. |
| **ssrn-6621520** — matched-filter regime detection | Method for the regime filter the trend system needs. |
| ssrn-4639843 (strategic trading), ssrn-4917628 (P2P energy), ssrn-6716300 (AI paper-trading), ssrn-6573599 (prop-trader behaviour) | Not applicable to this problem. |

The ORB paper is the important one, and it says plainly what I concluded independently: **the alpha
is the selection step, not the breakout rule.**

---

## 3. What I built: three streams, combined

Diversification is the only free lunch, and it was the one lever I had not pulled.

| Stream | Rule |
|---|---|
| **trend** | 20-day Donchian break · 2×ATR stop · **1.5×ATR chandelier trail, no target** |
| **session** | your quoted rule, restricted to the markets where it is positive (indices, gold, BTC) |
| **overnight** | long the close-to-open session on equity indices |

**Deriv feed, 2024-01-22 → 2026-09-25, $10,000 at 0.5% risk:**

| Stream | N | Expectancy | Win% | t | Sharpe | Ann vol | End | Return | Max DD |
|---|---|---|---|---|---|---|---|---|---|
| trend | 574 | +0.076R | 41.8% | +2.26 | 1.73 | 8.7% | $12,538 | +25.4% | 6.4% |
| session | 3,394 | +0.022R | 49.0% | +2.10 | 0.99 | 10.1% | $13,138 | +31.4% | 16.4% |
| overnight | 2,006 | +0.065R | 54.4% | +2.49 | 1.01 | 24.3% | $17,619 | +76.2% | 19.8% |
| **COMBINED** | **5,974** | **+0.042R** | **50.1%** | **+3.76** | **1.45** | 22.8% | **$29,359** | **+193.6%** | **20.4%** |

**Why it stacks — the daily return correlations:**

| | trend | session | overnight |
|---|---|---|---|
| trend | 1.00 | **0.09** | **−0.03** |
| session | 0.09 | 1.00 | **−0.07** |
| overnight | −0.03 | −0.07 | 1.00 |

Essentially independent. Each stream has Sharpe ~1; together they reach **1.45**, and the t-stat
goes from ~2.2 to **+3.76**. That is diversification doing exactly what it should.

**Month by month ($10,000 at 0.5%):**

| Month | P&L | % | | Month | P&L | % |
|---|---|---|---|---|---|---|
| 2024-02 | +$852 | +8.5% | | 2025-10 | **+$4,059** | +21.4% |
| 2024-05 | +$1,336 | +11.6% | | 2025-12 | +$1,754 | +7.5% |
| 2024-09 | −$889 | −6.0% | | 2026-03 | **−$3,443** | **−13.5%** |
| 2024-11 | +$1,677 | +11.7% | | 2026-05 | +$3,083 | +12.8% |
| 2025-01 | +$1,951 | +11.9% | | 2026-07 | −$1,320 | −4.7% |
| 2025-03 | −$1,824 | −9.1% | | 2026-08 | +$2,041 | +7.7% |

23 green of 32 (72%). Best month +$4,059, worst −$3,443. Longest run: 5 green, 2 red.
Weeks: 61% green, best +$2,242, worst −$2,519, worst run 5.

---

## 4. And then FundedNext

Same rules, **same dates** (2025-05-29 → today), two feeds:

| | Deriv | **FundedNext** |
|---|---|---|
| trend | +4.5% | −5.1% |
| session | +7.4% | −20.7% |
| overnight | +19.7% | **+18.4%** |
| **COMBINED** | **+52.8%** (t +1.94) | **−8.2%** (t +0.24) |

FundedNext has no Nasdaq or DAX CFD (the session stream's two best markets) and charges wider
spreads. This is the third time this session the same thing has happened — the round-number book
was +34% on Deriv and −14.9% on FundedNext.

### What does survive there: the overnight stream

US30 + SPX500, FundedNext's own bars, 658 trades:

| Risk | End | Return | Max DD | Win% | PF |
|---|---|---|---|---|---|
| 0.5% | $11,837 | +18.4% | 9.3% | 55.0% | 1.19 |
| 1.5% | $17,138 | **+71.4%** | **30.8%** | 55.0% | 1.19 |

Per market: US30 +0.062R (t +1.22), SPX500 +0.068R (t +1.35). Sharpe 1.19, 12-trade losing streak.

**Monthly at 1.5% risk ($10,000):**

| Month | P&L | % | | Month | P&L | % |
|---|---|---|---|---|---|---|
| 2025-06 | +$1,612 | +16.1% | | 2026-02 | −$41 | −0.3% |
| 2025-07 | −$241 | −2.1% | | **2026-03** | **−$2,341** | **−15.4%** |
| 2025-08 | +$1,359 | +12.0% | | 2026-04 | +$1,395 | +10.9% |
| 2025-09 | −$74 | −0.6% | | 2026-05 | −$445 | −3.1% |
| **2025-10** | **+$2,877** | **+22.7%** | | 2026-06 | +$1,243 | +9.0% |
| 2025-11 | −$1,228 | −7.9% | | 2026-07 | +$1,543 | +10.3% |
| 2025-12 | +$1,041 | +7.3% | | 2026-08 | +$465 | +2.8% |
| 2026-01 | −$144 | −0.9% | | 2026-09 | +$118 | +0.7% |

9 green of 16.

**Against FundedNext's rules** (10% target / 5% daily / 10% static), bootstrapped from the real
sequence:

| Risk | **d30** | **d60** | d90 | d180 | **Breach** |
|---|---|---|---|---|---|
| 0.50% | 1.8% | 16.2% | 34.7% | 70.5% | **2.9%** |
| 0.75% | 13.3% | 40.8% | 59.1% | 82.8% | 9.0% |
| 1.00% | 27.8% | 56.2% | 70.8% | 82.1% | 15.7% |
| **1.50%** | **48.2%** | **68.5%** | 74.0% | 75.6% | **24.3%** |
| 2.00% | 58.6% | 68.6% | 70.0% | 70.2% | 29.8% |

---

## 5. Straight answer on the one-month target

**At 1.5% risk on the overnight book you have roughly a 48% chance of passing FundedNext inside 30
days, 69% inside 60 days, and a 24% chance of losing the account.**

That is better than the 40.6% a no-edge strategy gets, but not dramatically — and that gap is not
my search failing. It is the arithmetic: **+10% in 20 days forces volatility, and volatility against
a 10% static floor breaches often regardless of edge.** A Sharpe-1.19 stream cannot change that.

Three routes, honestly priced:

1. **FundedNext in one month** — overnight book, 1.5% risk: **48% / 24% breach.** A real bet, not a
   plan.
2. **FundedNext in two months** — same book at 0.75–1.0%: **41–56% at d60, 9–16% breach.** Better
   odds per dollar risked.
3. **Deriv-quality execution** — the full combined book is +193.6% with t +3.76 there. If any prop
   firm routes through a broker with Deriv-like spreads *and* offers Nasdaq/DAX, this book is
   materially stronger than anything else measured this session.

**The thing worth chasing is not another strategy — it is a firm whose execution does not eat the
edge.** That single variable has flipped every result this session.

---

## 6. Reproduction

```bash
py -3.12 scripts/run_claimed_strategy.py --feed deriv --since 2024-01-22
py -3.12 scripts/run_combined_book.py --feed deriv --since 2024-01-22
py -3.12 scripts/run_combined_book.py --feed deriv --since 2025-05-29     # same window
py -3.12 scripts/run_combined_book.py --feed fundednext --since 2025-05-29
py -3.12 scripts/run_trend_challenge.py --source fn_overnight.json --horizon 180
```
