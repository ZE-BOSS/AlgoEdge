21 — Live-market structure: where an edge can actually exist
============================================================

**2026-09-04** · scripts `live_bar_harvest.py`, `live_structure.py`,
`live_meanrev_test.py`, `live_meanrev_validate.py`, `live_meanrev_execution.py`

Data: MT5 daily bars for **28 live-market symbols**, 2.6–16.0 years each,
against the synthetic baseline from report 20.

Report 20 closed the synthetic universe. This applies the same three
measurements to real markets, because real markets are supposed to differ on
exactly these axes — and it turns out they do, on two of the three.

---

## 1. Volatility clustering is the clean line between the two universes

The sharpest test in report 20 was the autocorrelation of squared returns: a
generated constant-volatility process has none, a real market has a great deal.
Under the null, Ljung–Box Q(20) is χ²(20), whose 99.9th percentile is ≈45.

| | ACF(r²) lag 1 | LB Q(20) |
|---|---|---|
| **Synthetics** (Boom/Crash 500 & 1000) | −0.017 … +0.023 | **11 – 23** |
| **Live markets** (28 symbols) | +0.044 … +0.411 | **38 – 3,788** |

**Every synthetic sits below the null threshold. Every live market sits above it,
some by two orders of magnitude** — the strongest reach Q ≈ 3,788 against a
threshold of 45. Netherlands 25 has ACF(r²) of 0.411 at lag 1; EURUSD, USDCAD,
Germany 40 and all four crypto show strong persistent clustering.

This matters more than it may look. Clustering is not a directional edge, so it
cannot be arbitraged away — it describes *risk*, not return. It is what makes
volatility targeting, position sizing and regime filters work at all. **On the
synthetics none of those tools could ever have added value, because there was no
volatility structure to respond to.** On live markets they can.

## 2. Risk premium — and a correction to my own test

Across 28 symbols, the Itô-corrected test flags five: four crypto and gold.
**Four of those five should not be believed as stated**, and the reason is worth
recording because the test is the same one that worked cleanly on the synthetics.

| symbol | years | vol | −σ²/2 | "excess"%/yr | t | **actual CAGR** |
|---|---:|---:|---:|---:|---:|---:|
| XRPUSD | 11.5 | 203.6% | **−207.3** | +242.5 | 4.05 | **+42.2%** |
| ETHUSD | 11.1 | 107.4% | −57.7 | +125.4 | 3.88 | +96.7% |
| LTCUSD | 13.9 | 114.6% | −65.6 | +111.9 | 3.64 | +58.8% |
| BTCUSD | 13.9 | 74.9% | −28.0 | +91.7 | 4.56 | +89.1% |
| **XAUUSD** | 15.7 | 17.0% | −1.4 | **+8.8** | 2.05 | **+7.6%** |

**The Itô correction assumes lognormal returns, and at 100–200% annualised
volatility it stops being a correction and becomes the answer.** For XRP the
−σ²/2 term is −207%/yr, so 85% of its headline "+242% excess" is the adjustment
rather than anything observed; a holder actually earned 42.2%/yr. The test is a
good approximation at gold's 17% vol and BTC's 75%, and misleading above that.
This is exactly the mirror of the Jump 100 trap in report 20 §2 — there the Itô
term hid a false positive, here it manufactures one.

Two further reasons not to act on the crypto row:

- **Survivorship.** BTC, ETH, XRP and LTC are the coins that survived and got
  listed. The thousands that did not are absent from the sample. This alone can
  manufacture the entire result, and no amount of history on the survivors fixes
  it.
- **Multiple comparisons.** Across 28 symbols the null expects a maximum |t| of
  about 2.4, so **gold's 2.05 is not notable at all** — it is inside the noise
  band for a search this wide.

What survives as solid:

**The FX result is a genuine null.** Sixteen years, every major indistinguishable
from fair (t between −0.44 and +1.83). That is the textbook expectation —
currencies are relative prices, not claims on productive assets. It also means a
directional FX strategy must earn everything from timing.

**The equity result is a data limitation, not a finding.** Deriv's index CFDs
begin in **January 2024** — 2.6 years. All eight point estimates are large and
positive (+5.5% to +25.2%) with t between 1.3 and 1.9: exactly what an
underpowered sample of a real equity premium looks like. The premium is among the
best-documented facts in finance; the failure to confirm it here is about the
history on this server and nothing else.

## 3. The one directional candidate: daily reversal in equity indices

Return autocorrelation — the thing a directional entry rule actually needs — is
absent everywhere except the equity indices, where **all eight are negative at
lag 1**:

| symbol | ACF(1) | t |
|---|---:|---:|
| Netherlands 25 | **−0.219** | 6.1 |
| US SP 500 | −0.174 | 5.0 |
| US Tech 100 | −0.166 | 4.7 |
| France 40 | −0.140 | 4.0 |
| Australia 200 | −0.119 | 3.4 |
| Germany 40 | −0.106 | 3.0 |
| UK 100 | −0.098 | 2.7 |
| Japan 225 | −0.094 | 2.7 |

Sixteen years of FX shows nothing (max |t| 0.8–2.6, mostly under 2). BTCUSD over
13.9 years: −0.0000.

### 3.1 Costs are not the obstacle here — which is new

The whole synthetic programme died on the fact that no edge cleared its costs.
Here the arithmetic runs the other way:

> US SP 500: daily sd **0.931%**, round-trip spread **0.0047%**.
> A daily-reversal rule needs |ρ| > **0.006** to break even. Measured ρ = **−0.174**,
> which is **29× the required threshold.**

Trading the rule directly — after a down day go long, after an up day go short,
one day holding period, spread charged every day:

| | value |
|---|---:|
| indices with positive gross | **9 / 9** |
| indices with positive net | **9 / 9** |
| pooled net | **+0.046% per trade (+11.6%/yr)** |

### 3.2 But nine correlated indices are not nine experiments

This is where honest statistics matter more than the headline. Averaging nine
t-statistics would overstate the evidence, because these are one market sampled
nine ways. Building a single equal-weight portfolio series aligned by date puts
the cross-correlation inside the standard error where it belongs:

| | value |
|---|---:|
| mean of the nine individual t-stats | +1.33 |
| **correctly pooled portfolio t** | **+2.38** |
| annualised | +14.9% |
| Sharpe | 1.39 |
| mean pairwise correlation | 0.37 |
| **effective independent bets** | **~2.3, not 9** |

Split in half — noting this is not a true out-of-sample test, since the effect
was found on the whole window:

| | n | mean/day | t | annualised |
|---|---:|---:|---:|---:|
| first half | 366 | +0.0769% | +1.94 | +19.4% |
| second half | 367 | +0.0417% | +1.37 | +10.5% |

Consistent in sign, weaker in the second half, neither half conclusive alone.

### 3.3 What actually threatens it: execution

| extra slippage/day | annualised | t |
|---:|---:|---:|
| 0 bp | +14.9% | +2.38 |
| 1 bp | +12.4% | +1.97 |
| 2 bp | +9.9% | +1.57 |
| 5 bp | +2.3% | +0.37 |
| 10 bp | −10.3% | −1.63 |

**The edge dies at about 5.9 bp of round-trip slippage per day**, against a
quoted spread of 1.2 bp that is already deducted. So roughly 4.7 bp of headroom —
and the rule trades *at the daily close*, which is precisely where a real fill is
hardest to get at the quoted price.

There is also a standing reason to be suspicious of negative daily
autocorrelation: it is the classic signature of a measurement artifact — stale or
non-synchronous constituent prices, or a quote oscillating around a slower true
value. Such an effect is completely real in the recorded series and completely
untradeable, because you cannot transact at the price that produced it.

### 3.4 It passes the artifact test — and improves

The decisive check is to move execution off the close. The signal comes from
yesterday's close, so it is known before today's open; entering at the open puts
**both fills away from the close print**. If the effect only exists between one
close and the next, it lives inside that print and cannot be traded.

| execution | mean/day | t | annualised | Sharpe |
|---|---:|---:|---:|---:|
| close → close (original) | +0.0592% | +2.38 | +14.9% | 1.39 |
| **open → close (intraday)** | **+0.0613%** | **+2.52** | **+15.4%** | **1.47** |
| close → open (overnight) | −0.0138% | **−2.88** | −3.5% | −1.69 |

**The intraday leg carries the whole effect on its own, and slightly more than
the original.** That is the opposite of what a close-print artifact would show,
and it removes the main structural objection to the finding.

The overnight leg is separately interesting: it runs **against** the signal at
t = −2.88. The index gaps in the direction of the prior day's move and then
reverses during the session. So the practical form of this strategy is
**intraday-only** — which is both better performing (Sharpe 1.47 vs 1.39) and
strictly lower risk, since it carries no overnight gap exposure.

Per index, intraday leg: **8 of 9 positive**, with Netherlands 25 (+21.2%/yr,
t = 2.58), France 40 (+19.7%, t = 2.40) and UK 100 (+14.4%, t = 2.25) strongest;
Hong Kong 50 the sole negative at −4.3%.

## 4. Verdict

**This is the first real candidate the research has produced**, and it is
genuinely different in kind from anything in the synthetic universe: the effect
is large relative to its costs rather than swamped by them, it is a documented
market phenomenon rather than a fitted pattern, and it appears in every member of
its family.

It survived the test most likely to kill it (§3.4): the effect is intraday, not a
close-print artifact, and the tradeable form is *better* than the one discovered.

It is still **not established**:

- t = +2.52 on ~2.3 effective independent bets, not 9
- 2.6 years, one regime, and the effect was found on that same window
- ~4.7 bp of execution headroom — real but not generous
- eight of the nine indices are the same trade in different currencies

**The binding constraint is history, and it is fixable.** Deriv's index CFDs
start in January 2024, but cash-index daily data runs for decades and is freely
available. Thirty years of S&P 500 opens and closes would move this from t = 2.5
to either conclusive or dead, in an afternoon — and unlike everything in the
synthetic universe, that is a question worth the afternoon.

## 5. Next

1. **Get long index history from outside MT5** (cash indices, decades, with
   opens). Single highest-value step; settles §3 either way. The intraday
   formulation needs open and close, which most free sources provide.
2. ~~Test execution realism~~ — **done, §3.4. Passes: the effect is intraday and
   does not depend on the close print.**
3. ~~Decompose overnight vs intraday~~ — **done, §3.4. Entirely intraday; the
   overnight leg is significantly negative, so the strategy should not hold
   positions overnight.**
4. **Size the effect against a volatility filter.** §1 shows clustering is
   strong and real; short-term reversal is known to be stronger in high-volatility
   states. That is a structural interaction, not a fitted parameter.
5. Volatility clustering (§1) is real, robust and currently unexploited — it
   belongs in **position sizing** across the whole book, independent of any entry
   signal. This is the lowest-risk improvement available anywhere in the project.
