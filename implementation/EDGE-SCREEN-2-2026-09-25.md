# Round two: overnight, time-series momentum, turn-of-month

**2026-09-25** · `scripts/run_edge_screen2.py` · 2026-01-01 → 25 Sep 2026 · both feeds
· $10,000 at 0.5% risk, bar spread + slippage charged, one position at a time

---

## 0. Result

| Hypothesis | FundedNext | Deriv | R/month | Verdict |
|---|---|---|---|---|
| **Overnight (long close→open)** | **+5.1%**, DD 9.8%, t +0.72 | **+4.4%**, DD 15.3%, t +0.51 | **+1.6 / +1.7** | Real sign, both feeds. **Far too small.** |
| Day session (short open→close) | −2.6%, t −0.18 | −4.4%, t −0.55 | −0.4 / −1.4 | No |
| Time-series momentum, 5-day | +1.0%, t −0.31 | −1.3%, t −0.75 | −0.5 / −1.5 | No |
| Turn of the month | 8 trades in 9 months | — | — | Too infrequent to matter |

**Nothing reaches +20 R/month.** The best is the overnight effect at **+1.6 R/month** — about
one-twelfth of what a 30-day challenge needs.

This round also caught a bug of mine that would have reported a fake winner. See §3.

---

## 1. Why these three, and why not more intraday patterns

The first screen established the constraint: cost per trade dominates, and widening the stop moves
expectancy to **zero**, not to profit. So round two deliberately avoided intraday patterns and went
after effects that do not require beating the spread repeatedly.

**H5 Overnight effect.** Equity index returns accrue almost entirely while the cash market is shut.
Recent measurement (Q3-2020 → Q3-2025): SPY **+47.1% close-to-open vs +29.9% open-to-close**; QQQ
**+53.5% vs +30.3%**; for QQQ since 1999, **92.6%** of all gains came overnight. Mechanism: risk
transfer at the close, futures repricing on overnight news, dealers unwilling to carry inventory
through the gap. One trade per index per day — you pay the spread once.

**H6 Time-series momentum.** Moskowitz, Ooi & Pedersen (2012): an instrument's own past return
predicts its next, across 58 futures. Tested at 5/10/20-day lookbacks because McLean & Pontiff
(2016) measure **58% post-publication decay** — assuming it still works would be naive.

**H7 Turn of the month.** Xu & McConnell: index returns cluster at the month boundary (pension
inflows, infrequent rebalancing). Four trades a month, so almost no spread paid.

**Control.** All three are long-biased on instruments that mostly rose. Each is therefore reported
against buy-and-hold over the identical window — a strategy does not get credit for being long.

---

## 2. Detail

### 2.1 Overnight — the only one positive on both feeds

| Market | N | Expectancy | Win | t | Trades/mo | R/mo |
|---|---|---|---|---|---|---|
| US30 overnight long | 175 | +0.036R | 53.1% | +0.49 | 21.7 | +0.79 |
| SPX500 overnight long | 175 | +0.036R | 50.3% | +0.52 | 21.7 | +0.78 |
| US30 day short | 175 | +0.026R | 54.3% | +0.38 | 21.7 | +0.56 |
| SPX500 day short | 175 | −0.043R | 44.6% | −0.60 | 21.7 | −0.94 |

Both indices give the same sign overnight, on both brokers' data, with win rates just above 50% —
exactly the shape the literature describes. As a portfolio: **+5.1% on $10,000 with a 9.8%
drawdown** over nine months.

But note what that is: **+0.036R a night**. The overnight effect is a *return* phenomenon (a few
basis points per session, compounding over decades), not a high-R trading edge. Sized as a
leveraged R-multiple trade it is a rounding error, and it is not significant (t +0.72).

**Against the control:** SPX500 buy-and-hold returned **+12.0%** in price over the same window while
overnight-only capture returned ~+5% at this sizing. Holding the index beat trading its nights —
with a bigger drawdown, but it beat it.

### 2.2 Time-series momentum — nothing at any lookback

| Market | Lookback | N | Expectancy | Win | t |
|---|---|---|---|---|---|
| US30 | 5d | 33 | +0.049R | 57.6% | +0.44 |
| SPX500 | 5d | 33 | −0.121R | 39.4% | −0.92 |
| XAUUSD | 5d | 33 | −0.028R | 42.4% | −0.19 |
| BTCUSD | 5d | 35 | +0.090R | 45.7% | +0.57 |
| EURUSD | 5d | 35 | +0.058R | 51.4% | +0.48 |
| GBPJPY | 5d | 35 | −0.108R | 42.9% | −0.86 |

Scattered around zero, no |t| above 1. 10- and 20-day lookbacks produce 16–18 trades in nine months
— too few to judge, which is itself the answer for a challenge that needs volume.

### 2.3 Turn of the month — 8 trades

Nine months gives eight month-boundaries. Even if each were worth +0.5R, that is +0.44 R/month. The
effect may well be real; it cannot move a challenge.

---

## 3. The bug this round caught

The first pass of this screen reported:

```
tsmom_5d   290 trades | exp +0.490R | +17.1 R/mo | $17,092 (+70.9%) | DD 25.9%
overnight  376 trades | exp +0.401R | +17.5 R/mo
XAUUSD tsmom_5d  +1.849R expectancy
```

Two things were wrong with that, and both were visible in the output before I believed it:

1. **A 5–11% win rate on a five-day hold is impossible.** A trade held five days with no target
   should win near 50% of the time.
2. **Positive expectancy in R alongside −55.8% in money** on the overnight book. Those cannot both
   be true unless R is not what the account actually risks.

Cause: my `atr()` helper averages the true range of **M5 bars**. Using it as a multi-day stop means
a "2×ATR stop" is twice the range of a *five-minute* bar — stopped out within minutes, while the
rare survivor ran for days and booked a huge R multiple. Nominal R was tiny, so R-multiples were
enormous and position sizing against them was wildly leveraged.

Fixed with a real `atr_daily()` (true range of daily bars). Everything then collapsed to the noise
in §2 — tsmom from +0.490R to −0.014R, overnight from +0.401R to +0.036R.

**+70.9% was entirely the bug.** Same pattern as the +1,686% prior-zone result and the +1,065%
Crash/Boom result earlier: every apparent winner this session has been a measurement artefact,
caught by a control or an internal inconsistency.

---

## 4. Where this leaves the search

Twelve hypotheses tested across this session, on two brokers' data, all costed:

| | Result |
|---|---|
| Round-number reversal | Negative, significantly (z −10.6 on BTC) |
| Round-number continuation | +34% on Deriv, **−14.9% on FundedNext** — smaller than the broker spread difference |
| Prior-zone retest (fade) | −97.6% |
| Prior-zone retest (break) | −98.6% |
| Published 5-min ORB | Negative at **all 25** range×target settings |
| Published intraday momentum | −8.8% |
| Compression breakout | −40.7 R/mo |
| Opening drive on pullback | −52.2 R/mo |
| Prior-day high/low | −59.2 R/mo |
| Lead-lag (SPX500→US30) | −31.3 R/mo |
| **Overnight effect** | **+1.6 R/mo** — real sign, both feeds, not significant |
| Time-series momentum | ≈0 |
| Turn of the month | 8 trades |
| **Crash/Boom drift** | **+361%** — real, synthetic-only, already implemented |

One positive on real assets (overnight, +1.6 R/month), one real edge overall (Crash/Boom drift,
which FundedNext does not offer).

**I do not think another screen is the right use of your time.** The pattern is not "we haven't
found the right rule yet" — it is that on a handful of retail CFDs, simple rules sit at roughly zero
before costs and below zero after them. Three separate lines of evidence say the same thing: the
R-scaling test (§4 of the previous document), the two-broker comparison, and the fact that the only
robust edge found all session lives on instruments with a structural, non-price mechanism.

### What I would do instead

1. **Take the 99% route.** Your existing ORB at 0.5% risk passes a challenge 99% of the time given
   a year, with 0.7% breach risk. Buy time — a firm with a long or no limit, or budget two attempts.
2. **If frequency is non-negotiable, change the universe, not the rule.** The one published strategy
   with a strong Sharpe gets it from selecting 20 in-play names out of 7,000 daily. That needs an
   equities broker and a scanner.
3. **Fund the personal account with Crash/Boom drift**, which is measured, implemented, and
   profitable — with `stop_fill_model = CONSERVATIVE`.

---

## 5. Reproduction

```bash
py -3.12 scripts/run_edge_screen2.py --feed fundednext
py -3.12 scripts/run_edge_screen2.py --feed deriv
```

**Sources:** [STOXX, overnight effect](https://stoxx.com/when-do-returns-come-from-an-analysis-of-the-overnight-effect-in-equities-trading/) ·
[Overnight vs intraday returns](https://hmaquant.substack.com/p/overnight-vs-intraday-returns-the) ·
[Moskowitz, Ooi & Pedersen, *Time Series Momentum*](https://w4.stern.nyu.edu/facdir/lpederse/papers/TimeSeriesMomentum.pdf) ·
[Alpha Architect, trend-following refresh](https://alphaarchitect.com/time-series-momentum-aka-trend-following-the-historical-evidence/) ·
[Xu & McConnell, *Equity Returns at the Turn of the Month*](https://www.chesler.us/resources/academia/turn_of_the_month_stock_returns.pdf) ·
[Quantpedia, turn of the month](https://quantpedia.com/strategies/turn-of-the-month-in-equity-indexes)
