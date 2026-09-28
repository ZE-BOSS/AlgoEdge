# One strategy from three findings — expiry, gamma regime, intraday timing

**2026-09-28** · `scripts/run_opex_strategy.py`, `run_expiry_study.py`, `run_vix_regime_filter.py`
· `data/opex/`, `data/expiry/`, `data/vix_regime/`

---

## 0. The answer to the $599 question, first

You asked for something that, if you pay $599/month for options data, makes that back three to
ten times over. **This strategy does not do that, and more importantly the $599 data would not
help it.**

The arithmetic: the best version below earns **+0.056% per expiry, twelve times a year**. On
$10,000 that is about $5.60 a month unleveraged. To clear $1,800/month from a 0.056% edge you
would need roughly $3.2 million of position per trade. That is the honest number and no amount of
optimising changes the order of magnitude.

What the $599 buys is **intraday options flow** — seeing individual calls and puts print in real
time. That is a *different hypothesis* from the one measured here, and I could not test it,
because testing it requires the data. §6 says exactly what I would test, what the bar is, and why
I would not spend the money before that test.

What I *can* give you is a real, statistically solid calendar effect, a filter that survived
walk-forward, and an intraday refinement that cuts the drawdown by a factor of twenty. It is
small. It is also free to run.

---

## 1. The three findings, and how they combine

| | Finding | Evidence |
|---|---|---|
| **Calendar** | Equity indices fall into monthly options expiry | Nasdaq 100, **480 expiries over 40 years**, −0.23% vs other Fridays, **t −3.14**, negative in every decade |
| **Regime** | It is much stronger when VIX is high | +0.376% vs +0.164% unfiltered, and **walk-forward confirmed** |
| **Intraday** | The move happens in the **last two hours**, not across the day | pooled t +2.39 vs +0.30 for open-to-close; drawdown 1.3% vs 4.6% |

Combined into one rule:

> **On the third Friday of each month, short the index CFD two hours before the cash close and
> cover at the close. Prefer high-VIX expiries. Skip triple witching.**

---

## 2. The calendar, which is the solid part

`scripts/run_expiry_study.py`. Every test compares expiry Fridays against **other Fridays** —
expiry is always a Friday, so comparing against all days would measure the Friday effect and
report it as an expiry effect.

**Short from the prior close to the expiry close, costed at 0.01% round trip:**

| Index | Expiries | Mean/trade | Win | t | Sharpe/yr | Total | Max DD | Worst |
|---|---|---|---|---|---|---|---|---|
| **Nasdaq 100** (1986–2026) | 480 | **+0.164%** | 53.8% | **+2.69** | 0.42 | **+110.9%** | 16.9% | −4.74% |
| S&P 500 (2016–2026) | 116 | +0.048% | 51.7% | +0.56 | 0.18 | +5.2% | 5.0% | −2.69% |
| Dow 30 (2016–2026) | 116 | −0.007% | 45.7% | −0.08 | −0.03 | −1.3% | 6.7% | −3.00% |

**The FX control is what makes this options-related rather than calendar noise.** EURUSD, GBPUSD
and USDJPY show no directional response at all (|t| < 0.35 on 193 events each). They *do* show
the volatility suppression (EURUSD range t −2.48, USDJPY t −2.61) — so **that half of the gamma
story is not options-specific and I have not built on it.**

**Era by era, Nasdaq, sign never flips:** 1986–2000 −0.305%, 2000–2010 −0.172%, 2010–2020
−0.172%, 2020–2027 −0.246%. Four independent periods.

**Honest limit:** the pooled significance leans on 1986–2006. Nasdaq on the last decade alone is
+0.149% with t +1.54 — same size, no significance. S&P and Dow show nothing on their 10 years.

---

## 3. The regime filter, which survived walk-forward

| Nasdaq 100 filter | n | Mean | t | Sharpe | Total |
|---|---|---|---|---|---|
| all expiries | 480 | +0.164% | +2.69 | 0.42 | +110.9% |
| VIX **high** tercile | 130 | **+0.376%** | **+2.29** | **0.70** | **+59.3%** |
| VIX mid | 127 | +0.136% | +1.28 | 0.39 | +17.8% |
| VIX low | 163 | +0.057% | +0.81 | 0.22 | +9.0% |
| term = backwardation | 18 | +0.328% | +0.59 | 0.49 | +5.6% |
| term = contango | 202 | +0.031% | +0.43 | 0.10 | +5.3% |
| **ordinary monthly (not triple witching)** | **319** | **+0.225%** | **+2.89** | **0.56** | **+98.8%** |
| triple witching only | 161 | +0.044% | +0.45 | 0.12 | +6.0% |

**Walk-forward — the filter chosen on pre-2016 data, scored on post-2016:**

| Filter | Picked | Early | Late (filtered) | Late (unfiltered) | Better? |
|---|---|---|---|---|---|
| **VIX level** | **high** | +0.357% | **+0.443%** | +0.149% | **YES, ~3×** |
| term structure | contango | −0.033% | +0.076% | +0.149% | no |

The VIX-level filter is the only thing in this whole body of work that was chosen on one window
and did better on another. That is worth more than any in-sample number in this document.

**Triple witching being *weaker* is counterintuitive** — you would expect the quarterly expiry to
be the big one. On 319 vs 161 events it is a clean split, and it raises both the mean and the
t-statistic, so it ships as a skip rather than an emphasis.

---

## 4. The intraday refinement

Deriv's index history starts 2024-01-22 and FundedNext's M5 retention is about 100,000 bars, so
the intraday sample is **31 expiries**, not 480. Five index CFDs, costed at 0.01%:

| Window | n (pooled) | Mean | Win | t | Sharpe | Total | Max DD | Worst |
|---|---|---|---|---|---|---|---|---|
| open → close | 126 | +0.019% | 49.2% | +0.30 | 0.09 | +2.1% | 4.6% | −1.70% |
| second half of session | 126 | +0.057% | 57.1% | **+2.14** | 0.66 | +7.4% | 1.6% | −0.72% |
| **last two hours** | 126 | +0.054% | 53.2% | **+2.39** | **0.74** | +7.0% | **1.3%** | **−0.53%** |

**The whole move is in the back end of the session**, which is what the gamma-unpin story
predicts and is consistent on all five instruments. It is also a far better trade to hold: the
close-to-close version carries a −4.74% worst case and a 16.9% drawdown; the last-two-hours
version is −0.53% and 1.3%.

### The correction that matters most in this document

**Five US and EU equity index CFDs on the same expiry are not five trades. They are one bet
placed five times.** Pooling them multiplies n by five and divides the standard error by √5,
flattering the t-statistic by more than double. Collapsing each expiry to what an equal-weight
basket actually returned:

| Window | Events | Mean | Win | **t** | Sharpe | Total | Max DD | Worst |
|---|---|---|---|---|---|---|---|---|
| open → close | 31 | +0.035% | 51.6% | +0.30 | 0.19 | +1.0% | 2.5% | −1.38% |
| second half | 31 | +0.062% | 58.1% | +1.21 | 0.75 | +1.9% | 1.0% | −0.42% |
| **last two hours** | **31** | **+0.056%** | 54.8% | **+1.35** | **0.84** | +1.8% | **0.8%** | −0.33% |

**t +2.39 becomes t +1.35.** That is the number to believe. The risk profile is genuinely good —
Sharpe 0.84, 0.8% maximum drawdown, worst trade −0.33% — but 31 events does not prove it.

**In-sample / out-of-sample on the basket:** before 2026 (22 events) +0.039%; **2026 (9 events)
+0.099%.** Better out of sample, on nine events.

---

## 5. Month by month, 2026

Pooled across the five index CFDs, last two hours, 44 trades:

| Month | Trades | Sum % | Avg % | Regime |
|---|---|---|---|---|
| 2026-01 | 5 | +0.460 | +0.092 | contango |
| 2026-02 | 5 | −0.254 | −0.051 | contango |
| 2026-03 | 5 | **+2.082** | +0.416 | contango |
| 2026-04 | 5 | +0.283 | +0.057 | contango |
| 2026-05 | 5 | **+1.966** | +0.393 | contango |
| 2026-06 | 5 | +0.492 | +0.098 | — |
| 2026-07 | 5 | +1.216 | +0.243 | contango |
| 2026-08 | 5 | −0.288 | −0.058 | contango |
| 2026-09 | 4 | −1.189 | −0.297 | contango (TW) |
| **YTD** | **44** | **+4.767** | | 6 of 9 months green |

As a basket (the honest unit) that is **+0.099% per expiry across 9 expiries in 2026.**

And the Nasdaq close-to-close version over the same months: +0.060, −0.878, +1.867 (TW), −1.299,
+1.529, +1.482, −0.338, −0.680 → **+1.743% YTD on 8 expiries, 50% win.** Note the monthly numbers
swing ±1.9% on the close-to-close version against ±0.4% intraday — the overnight gap is most of
the variance and almost none of the edge.

### One number that is not real

"VIX high only, last two hours" shows 10 trades, 100% win, t +8.94. That is two expiry dates
across five correlated instruments. **It means nothing** and is printed only because hiding it
would be worse.

---

## 6. What the $599/month would actually buy, and the bar it has to clear

Everything above uses free data: FRED for the index and VIX, the MT5 terminals for the CFDs, and
a deterministic calendar. **None of it needs an options subscription.**

Paid intraday flow ([ORATS live intraday $599/mo](https://orats.com/data-api),
[OptionData](https://www.optiondata.io/), [Databento OPRA](https://databento.com/datasets/OPRA.PILLAR))
buys one thing this work could not get: **individual option trades as they print** — strike, size,
call or put, and whether they hit the bid or the ask.

The mechanism is not in doubt. When a dealer sells you a call they buy the index to hedge, and
that buying is real order flow. [Cboe reported 0DTE at about 63% of all SPX option volume in
February 2026](https://spotgamma.com/0dte/), and at-the-money gamma on a same-day option runs
20–50× a 30-day option, so the hedging is concentrated into hours.

**What is in doubt is whether it is tradable by us, and three things have to be true:**

1. **The flow has to lead the index, not lag it.** Dealer hedging is fast. If the index has
   already moved by the time the print is visible, there is nothing to trade.
2. **The edge has to survive CFD costs.** We trade a dealer quote, not the exchange. A signal
   worth 0.03% is worth nothing after a 0.01% spread and slippage.
3. **It has to be worth more than the free calendar.** If intraday GEX just reproduces "sell into
   the expiry afternoon", we already have that for nothing.

**The bar:** at $599/month the data needs to add at least **$7,200 a year** before it breaks even,
and you asked for 3–10×, so call it **$22,000–$72,000 a year**. On a $10,000 account that is
220–720% annually. Nothing in the free evidence suggests an effect of that size — the whole
calendar edge is +0.056% twelve times a year. **I would not spend the money on this evidence.**

**What would change my mind:** most of these vendors sell historical flow separately from the
live feed. One month of *historical* intraday OPRA for SPX — a few hundred dollars, not a
subscription — is enough to answer question 1 offline. That is the purchase I would make, and only
then the feed.

---

## 7. The VIX filter on the strategies we already run

`scripts/run_vix_regime_filter.py`. Every trade from the shipped strategies, tagged with the VIX
regime on its entry date, chosen on 2021-10 → 2024-09 and scored after. Terciles are
**expanding-window**, so no bucket boundary uses the future.

| Strategy / key | Picked in-sample | IN expR | OUT expR | OUT unfiltered | Better? |
|---|---|---|---|---|---|
| **TrendBreakout / VIX level** | **low** | +0.207 | **+0.063** | +0.023 | **YES (~3×)** |
| OvernightSession / VIX level | mid | +0.282 | +0.086 | +0.060 | YES |
| OvernightSession / term | contango | +0.088 | +0.069 | +0.060 | YES |
| TrendBreakout / term | contango | +0.112 | +0.018 | +0.023 | no |
| OpeningDrive / VIX level | mid | −0.000 | +0.004 | +0.012 | no |
| OpeningDrive / term | contango | −0.007 | +0.007 | +0.012 | no |
| ORB / VIX level | high | −0.384 | −0.351 | −0.385 | "yes" (less bad) |
| ORB / term | backwardation | −0.454 | −0.175 | −0.385 | "yes" (less bad) |

**5 of 8 improved out of sample — barely above the 4 of 8 you would get from coin flips.** Read
the magnitudes instead:

**TrendBreakout + low VIX is the one worth having.** Out-of-sample expectancy went from +0.023R
to **+0.063R**, nearly tripling. But on 55 trades with t +0.54, and the strategy's own base
weakened over the same period (+0.102R in-sample, t +2.31 → +0.023R out, t +0.61), so part of
what the filter is doing is picking the least-damaged bucket.

**ORB's rows are an artefact of this harness, not a statement about shipped ORB** — it is run
here with a plain 3R target rather than the measured per-symbol configuration, hence the −0.385R
base. Ignore those two rows.

**VWAP is not in this table.** It generates signals through `edge_lab`'s session-context builder
rather than a standalone function, so it needs its own harness. That is the obvious next test if
you want this extended.

---

## 8. What I would actually do with this

1. **Run the OPEX last-two-hours trade.** It is free, it is twelve times a year, it has a 0.8%
   drawdown and it is uncorrelated with everything else in the book by construction — it is a
   calendar rule, not a price-action one. Diversification was the one lever that measurably
   worked when the combined book was built. Do not expect it to fund anything on its own.
2. **Apply the low-VIX filter to TrendBreakout.** It is the only filter in this work that was
   chosen on one window and did better on another.
3. **Do not buy the $599 feed yet.** Buy one month of historical intraday OPRA instead and answer
   the lead/lag question offline first.

---

## 9. Reproduction

```bash
py -3.12 scripts/run_expiry_study.py --eras --cfd
py -3.12 scripts/run_vix_regime_filter.py
py -3.12 scripts/run_opex_strategy.py --monthly
```

All three use free data. FRED responses are cached under `data/`, so only the first run touches
the network.
