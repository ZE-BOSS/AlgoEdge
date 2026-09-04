23 — Phase 4: walk-forward verdict on the index reversal
========================================================

**2026-09-04** · scripts `cash_index_harvest.py`, `cash_vs_cfd_check.py`,
`phase4_walkforward.py`

Data: **18 cash indices, 166,076 index-days, 19–57 years each** (S&P 500 to 1970).

**Verdict: the candidate is dead.** The intraday-reversal effect found on 2.6
years of Deriv CFD data does not survive on 57 years of the underlying indices.
Phase 4 ends here rather than producing a strategy spec.

---

## 1. Getting the data

Stooq was the first choice and serves a JavaScript proof-of-work bot check.
That was not worked around — it was abandoned in favour of Yahoo's chart
endpoint, which returns daily open and close without any such gate. Both legs are
required, because the tradeable formulation is open-to-close.

## 2. What the cash data can and cannot be used for

Before trusting 40 years of it, the two series were compared where they overlap.
They correlate **far less than expected**:

| leg | mean correlation, cash vs CFD |
|---|---:|
| close → close | **0.565** |
| open → close | 0.639 |
| close → open | 0.089 |

And it splits cleanly by geography: **S&P 500 0.876 and Nasdaq 0.873, against
Japan 225 0.171 and Australia 200 0.299.** Deriv cuts its daily bars on a
server-day boundary that lines up with the US session and slices straight through
the Asian one, so "a day" means different things in the two datasets.

That makes the cash history the right tool for asking **whether the phenomenon
exists and persists**, and the wrong tool for predicting CFD execution P&L
day-by-day. The former is the question that matters here.

## 3. Full history: the strategy loses money

Cost charged at 1.2 bp/day, the median Deriv index spread.

| index | years | ACF(1) | ann % | t |
|---|---:|---:|---:|---:|
| **US SP 500** | 56.7 | −0.019 | **−9.2** | −4.29 |
| **Russell 2000** | 39.0 | −0.031 | **−13.3** | −4.14 |
| **US Tech 100** | 40.9 | −0.038 | **−11.0** | −3.03 |
| Brazil Bovespa | 33.3 | +0.031 | −18.6 | −3.21 |
| Swiss 20 | 35.8 | +0.034 | −9.0 | −3.61 |
| Spain 35 | 33.1 | +0.015 | −7.7 | −2.43 |
| Japan 225 | 56.7 | −0.016 | +0.1 | 0.04 |
| Australia 200 | 33.8 | −0.046 | +1.3 | 0.60 |

**Negative on 12 of 18 indices.** On the S&P 500 over 56.7 years it loses 9.2%
a year at t = −4.29. The measured daily autocorrelation is −0.019, not the −0.174
seen on 2.6 years of the CFD.

## 4. Why: the sign flips by decade

This is the finding that explains everything.

| decade | indices | n | ACF(1) | ann % | t |
|---|---:|---:|---:|---:|---:|
| 1970–1979 | 2 | 4,980 | **+0.156** | −22.3 | −10.93 |
| 1980–1989 | 7 | 9,414 | **+0.083** | −17.3 | −7.63 |
| 1990–1999 | 17 | 33,186 | **+0.062** | −16.3 | −9.13 |
| 2000–2009 | 18 | 43,312 | −0.030 | +4.8 | 2.75 |
| 2010–2019 | 18 | 45,060 | +0.009 | −6.2 | −5.47 |
| 2020–2026 | 18 | 30,106 | **−0.068** | +1.9 | 1.19 |

**Daily autocorrelation in equity indices is not a constant — it flips sign.** It
was strongly *positive* (momentum) through the 1970s–1990s and only turned
negative recently. A strategy betting on reversal loses heavily in four decades of
six.

The 2020–2026 row is the regime the Deriv CFD sample sits in, and it does show
reversal (−0.068) — the same sign as the CFD's −0.174, at roughly a third the
size, and **still not significant** on cash data (t = 1.19). So the original
finding was a real feature of a recent regime, amplified by CFD-specific bar
construction, measured over a window too short to see that the sign is unstable.

## 5. Walk-forward: nothing to trade

Fit the sign of the effect on everything before a cutoff, trade it after. No
parameter is ever scored on data that chose it.

| cutoff | train n | train ACF(1) | test n | test ann % | test t |
|---:|---:|---:|---:|---:|---:|
| 1990 | 14,394 | +0.096 | 64,303 | +2.4 | 2.00 |
| 1995 | 27,273 | +0.089 | 95,603 | +0.6 | 0.63 |
| 2000 | 47,580 | +0.071 | 113,609 | +0.2 | 0.21 |
| 2005 | 68,881 | +0.039 | 92,308 | +0.2 | 0.25 |
| 2010 | 90,892 | +0.007 | 75,166 | +2.9 | 3.20 |
| 2015 | 113,440 | +0.010 | 52,618 | +1.6 | 1.45 |
| **2020** | 135,952 | +0.010 | 30,106 | **−1.9** | −1.19 |

Six of seven cutoffs return under 3%/yr before any realistic slippage, and **the
most recent — the one that matters — is negative.** Training on 135,952 days does
not help, because the parameter being fitted is not stable.

## 6. Verdict, and what it validates

The candidate is discarded. Not "unproven" — **tested against 20× the data and
found to be a regime artifact.**

This is the phase structure working exactly as designed, and it is worth being
explicit about that:

- **Report 22's Phase 3 called this in advance.** Three of four tests passed and
  the fourth — data-mining deflation — said t = 2.30 was *inside* the band a
  search of 84 hypotheses would produce by chance. That was the correct read, and
  it is why Phase 4 was gated on getting more data rather than on writing a spec.
- Report 19 §2 committed to discarding anything that failed validation "without
  appeal". That commitment is being honoured here on the only candidate the
  programme produced.
- Had this been shipped on the 2.6-year result — 9/9 indices positive, Sharpe
  1.47, passing an artifact test — it would have been a **losing strategy in the
  most recent out-of-sample period**. That is precisely the failure mode of
  reports 16 and 18, avoided this time.

## 7. Phase status

| phase | status |
|---|---|
| 0 — data foundation | complete |
| 1 — generative characterisation | complete; synthetic universe closed |
| 2 — hypothesis-driven edge search | complete; one candidate |
| 3 — Monte Carlo validation | complete; conditional pass, flagged the risk |
| **4 — walk-forward** | **complete; candidate rejected** |
| 5 — research paper | ready to write |

**No strategy spec is produced, because nothing earned one.**

What the programme has instead: a measured, closed synthetic universe; a
live-market map showing where structure does and does not exist; and a
measurement toolkit — overlap-aware significance, gap-aware fills, tick replay,
date-aligned pooling, data-mining deflation, walk-forward — that caught four
separate errors in this work alone, two of them mine.

## 8. The one thing still standing

Report 21 §1: **volatility clustering is real, large and unexploited.** Live
markets show Ljung–Box Q(20) of 38–3,788 against a null threshold of 45;
synthetics show 11–23. Unlike everything tested in reports 21–23, clustering is
not a directional edge, so it is not competed away and does not depend on a sign
that flips by decade.

It belongs in **position sizing**, not entry logic. That is the remaining
actionable result, and it is Phase 5's main recommendation.
