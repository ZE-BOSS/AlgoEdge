# Options expiry, dealer gamma, and what the free data already settles

**2026-09-27** · `scripts/run_expiry_study.py` · `data/expiry/`

---

## 0. The short version

Two claims were on the table. **One splits cleanly in half, and the FX control is what splits it.**

| Claim | Verdict |
|---|---|
| Options expiry **suppresses realised volatility** (the dealer-gamma pin) | **Not options-specific.** The same suppression shows up in EURUSD (t −2.48) and USDJPY (t −2.61), which have no equity-options expiry on that calendar at all. Something about the third Friday quiets *every* market. Do not attribute it to dealer gamma. |
| Options expiry **pushes the index down** | **Index-specific and real, but small and not proven in the modern era.** Nasdaq 100 falls 0.23% more than other Fridays over 480 expiries and 40 years (t −3.14), negative in all four eras — while FX directional response is flat (\|t\| < 0.35 on 193 events each). |

The tradable version of the second one is a short from Thursday's close to Friday's close, twelve
times a year: **+0.164% per trade after costs, 53.8% win, annual Sharpe 0.42, +110.9%
unleveraged over 40 years, 16.9% max drawdown.** But see §4 — the modern decade does not reach
significance on its own, and the S&P and Dow show nothing.

**No API key was needed for any of this, and none should be bought yet.**

---

## 1. Why this was the first options experiment

The expensive version of the options question needs a chain subscription, an assumption about who
is long and who is short, and a multi-leg fill model. This one needs none of it, because **the
dates are deterministic**: monthly expiry is the third Friday, triple witching is the third Friday
of March, June, September and December. So "does expiry move the index" is a pure event study on
daily bars — and if the answer were no, no chain subscription would conjure an effect that is not
there.

### The control that decides everything

Expiry is **always a Friday**. Comparing expiry days against all other days measures the Friday
effect and the expiry effect together and reports their sum as the second one. Every test here
compares **expiry Fridays against other Fridays**.

FX is the second control, and it is the one that mattered: EURUSD has no US equity-options expiry,
so anything that shows up there is a property of the calendar and not of options.

### The data, all free

- **[FRED](https://fred.stlouisfed.org/)** — no API key, no rate limit, plain CSV. Daily Nasdaq
  100 back to **1986** (10,264 sessions), S&P 500 and Dow back to 2016.
- The Deriv and FundedNext terminals for the CFDs we would actually trade.

Stooq, the other obvious free source, is behind a JavaScript proof-of-work challenge and cannot be
scripted.

---

## 2. The directional result

**Nasdaq 100, FRED, 1986-01 → 2026-09, 10,264 sessions, 480 expiries**

| Test | n | Event | Control | Diff | t |
|---|---|---|---|---|---|
| **Expiry Friday return vs other Fridays** | 480 | −0.174% | +0.055% | **−0.230%** | **−3.14** |
| Expiry Friday \|return\| vs other Fridays | 480 | +0.963% | +1.090% | −0.127% | −2.43 |
| Triple witching \|return\| vs other Fridays | 161 | +0.917% | +1.090% | −0.173% | −2.45 |
| Triple witching return vs other Fridays | 161 | −0.054% | +0.055% | −0.109% | −1.04 |
| Expiry week vs the week after | 489 | +0.270% | +0.224% | +0.047% | +0.23 |

**S&P 500 and Dow 30 (FRED, 10 years, 116 expiries each)** — the only thing that reaches
significance is triple witching, and on both:

| | Event | Control | Diff | t |
|---|---|---|---|---|
| S&P 500 triple witching return | −0.351% | +0.104% | **−0.455%** | **−2.72** |
| Dow 30 triple witching return | −0.351% | +0.096% | **−0.447%** | **−2.75** |

### Era by era — the sign never flips

Nasdaq 100, expiry Friday return vs other Fridays:

| Era | n | Diff | t |
|---|---|---|---|
| 1986–2000 | 166 | **−0.305%** | −2.66 |
| 2000–2010 | 117 | −0.172% | −0.88 |
| 2010–2020 | 119 | −0.172% | −1.57 |
| 2020–2027 | 78 | −0.246% | −1.45 |

Four independent periods, same sign, same order of magnitude. Individually underpowered; together
that consistency is the strongest thing in this document.

---

## 3. The volatility result, and why the FX control kills it

The gamma story predicts suppressed realised volatility into expiry. It is there — and it is there
in markets that have no equity-options expiry:

| Market | Test | Diff | t |
|---|---|---|---|
| Nasdaq 100 | expiry Friday \|return\| | −0.127% | −2.43 |
| **EURUSD** | **expiry Friday range** | **−0.074%** | **−2.48** |
| **USDJPY** | **expiry Friday \|return\|** | **−0.082%** | **−2.61** |
| EURUSD | expiry Friday \|return\| | −0.047% | −1.91 |
| GBPUSD | expiry Friday \|return\| | −0.044% | −1.48 |

**4 of 21 FX tests reach \|t\| ≥ 2**, against about 1 expected by chance — on a calendar FX has no
reason to care about. Either the third Friday is a systematically quiet day across all asset
classes for reasons unrelated to equity options, or FX is picking up a spillover from equity
hedging. Either way, **the volatility half of the gamma story is not established by this data**,
and any strategy built on "index volatility is pinned into expiry" would be building on a
calendar artefact.

The directional half does NOT appear in FX: EURUSD −0.001%, GBPUSD −0.013%, USDJPY −0.005%, every
one with \|t\| < 0.35 on 193 events. That asymmetry — direction is index-only, volatility is
everywhere — is the finding.

### Multiple comparisons, counted honestly

74 tests, 10 at \|t\| ≥ 2, against 3.7 expected by chance. That is 2.7× the chance rate, which is
suggestive rather than decisive. It is also why individual cells are not quoted in isolation
anywhere above: the Germany 40 "expiry week vs the week after" at t −2.50 is one cell out of
seventy-four and is treated as noise until something replicates it.

---

## 4. Is it tradable?

Short the index from Thursday's close to Friday's close, twelve times a year. Costed at 0.01%
each way, which is about a 1-point spread on a 20,000 index:

| Index | n | Net/trade | Win | t | Sharpe/yr | Total | Max DD | Worst trade |
|---|---|---|---|---|---|---|---|---|
| **Nasdaq 100** (40y) | 480 | **+0.164%** | 53.8% | **+2.69** | **+0.42** | **+110.9%** | 16.9% | −4.74% |
| S&P 500 (10y) | 116 | +0.048% | 51.7% | +0.56 | +0.18 | +5.2% | 5.0% | −2.69% |
| Dow 30 (10y) | 116 | −0.007% | 45.7% | −0.08 | −0.03 | −1.3% | 6.7% | −3.00% |

Costs barely matter — 0.00%, 0.01% and 0.02% give +0.174%, +0.164% and +0.154%. This is a
once-a-month trade, so spread is a rounding error, which is a real advantage over everything else
in the book.

### The check that tempers it

Is the S&P's failure a real difference, or just 116 events against the Nasdaq's 480? Nasdaq on
matched sample sizes:

| Nasdaq 100 window | n | Net/trade | t | Sharpe/yr |
|---|---|---|---|---|
| 1986–2026 | 480 | +0.164% | **+2.69** | +0.42 |
| 2006–2026 | 244 | +0.054% | +0.72 | +0.16 |
| 2016–2026 | 126 | +0.149% | +1.54 | +0.47 |

**The pooled significance leans on 1986–2006.** The last decade has the same magnitude (+0.149%)
and the same Sharpe (0.47) but only t +1.54, and the middle period is weak. So the correct
statement is: a small, persistent, index-specific calendar effect whose sign has never flipped in
forty years, and which no individual modern decade proves.

Twelve trades a year at Sharpe 0.42 is not a strategy on its own. What it is worth is being
**uncorrelated with everything else in the book by construction** — it is a calendar effect, not
a price-action one — and diversification was the one lever that measurably worked when the
combined book was assembled.

### On our own instruments there is no power at all

| Instrument | Window | Expiries | Expiry Friday return diff | t |
|---|---|---|---|---|
| US Tech 100 (Deriv) | 2024-01 → today | 31 | −0.124% | −0.53 |
| US SP 500 (Deriv) | 2024-01 → today | 31 | −0.029% | −0.17 |
| US30 (FundedNext) | 2022-10 → today | 47 | — | — |
| SPX500 (FundedNext) | 2022-10 → today | 47 | −0.106% | −0.68 |

Same sign everywhere, no significance anywhere. **Deriv's index history begins 2024-01-22 and
FundedNext's 2022-10-20** — 31 and 47 expiries respectively. Nothing can be proven on our own
feeds; the effect has to be established on the index and then assumed to transfer to the CFD that
tracks it.

---

## 5. What this means for the options question

**It argues against buying data right now, and it says which data to buy when the time comes.**

- The gamma-pin premise, in the one form testable for free, **did not survive its control**. A GEX
  reconstruction built to predict "volatility is suppressed into expiry" would be predicting
  something that also happens in EURUSD.
- The effect that IS real is directional, index-specific, and needs **no options data at all** —
  the calendar is deterministic.
- So the first options subscription should be justified by a question this study could not ask,
  not by this one. The obvious candidate: does *dealer positioning* (which strike, how much open
  interest, which side) predict the direction, rather than the date alone? That genuinely needs a
  chain, and it is a different hypothesis from the one just tested.

### The data landscape, for when it is time

| Source | What | Cost | Key |
|---|---|---|---|
| **[FRED](https://fred.stlouisfed.org/)** | daily index levels, decades | **free** | none |
| **[CFTC COT](https://publicreporting.cftc.gov/)** | weekly positioning, all futures | **free** | none |
| **[Alpaca](https://alpaca.markets/options)** | chains, Greeks, paper + live trading | free tier | free |
| **[Deribit](https://www.deribit.com/)** | BTC/ETH options + testnet | **free** | free |
| [Polygon.io](https://polygon.io/) | chains, trades, quotes, OI | ~$29 + ~$79/mo | yes |
| [ThetaData](https://www.thetadata.net/) | tick options history | from ~$80/mo | yes |
| [ORATS](https://orats.com/data-api) | 25 years, IVs, Greeks | from ~$99/mo | yes |

Stooq is unusable from a script (JS challenge). Yahoo is not installed and is unreliable for
options history anyway.

---

## 6. Reproduction

```bash
py -3.12 scripts/run_expiry_study.py
py -3.12 scripts/run_expiry_study.py --eras
py -3.12 scripts/run_expiry_study.py --cfd
```

FRED responses are cached under `data/expiry/`, so only the first run touches the network.
