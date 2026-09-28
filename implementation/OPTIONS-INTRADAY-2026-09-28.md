# Do option strikes move the market intraday? — the day-trading test, per pair

**2026-09-28** · `scripts/run_strike_levels.py` · `data/strike_levels/`
· companion to `OPTIONS-STRATEGY-2026-09-28.md`

---

## 0. First, a correction to my last report

**"Nine trades in a day" was my table being unclear, not the strategy.** The expiry trade fires
**twelve times a year**, once per monthly expiry. The "5 trades" in each month of that table was
*one trade on each of five instruments on the same day*. Traded as one basket it is one trade a
month. Nothing in it trades daily.

This document is about the thing you actually asked for: **day-to-day option calls and puts, and
whether they move NASDAQ, S&P, gold or BTC intraday.**

---

## 1. The question, and why it could be answered for nothing

> "When there was a call strike here, this was the movement it had on NASDAQ. When there was a
> put strike here, this was the movement it had. How can it be converted into a scalping or day
> trading strategy?"

Option strikes are not scattered. They sit on a **fixed, public, unchanging grid**:

| Underlying | Strike spacing actually listed |
|---|---|
| NASDAQ 100 | every 25 points (QQQ maps to ~50 NDX points) |
| S&P 500 | every 5 points near the money, 25 further out |
| DAX 40 | every 50 points |
| Gold (COMEX) | every $5 and $10 |
| BTC (Deribit) | every $1,000, $2,500 and $5,000 |

So "does price behave differently near a strike" is answerable from **price data alone**, on years
of it, at no cost. That is what this tests, and it had to come first: if the grid does nothing,
open-interest weighting on top of a grid that does nothing cannot create an effect.

### Three hypotheses, two of them opposite

| | Claim | A day trade would be |
|---|---|---|
| **MAGNET** | price is drawn to strikes, sessions close near one | buy/sell toward the nearest strike |
| **BARRIER** | an approach reverses more often than it breaks | fade the touch |
| **BREAK** | an approach breaks and runs | trade the break |

BARRIER and BREAK are opposites and **either one is tradable**. What kills the idea is neither.

### The control

Every test is run a second time against a **grid of the same spacing shifted half a step** — levels
that are not strikes but are spaced identically. A real strike effect has to beat that, because
"price reacts near round-ish numbers" is a different claim, and one already tested and already
negative in this project.

---

## 2. The result, per pair

M5 bars, 2024-01-22 → today, New York sessions. An "approach" is a bar closing within 0.08 of a
step of a level having been further away on the previous bar; the outcome is read 12 bars
(~60 minutes) later.

| Market / step | Sessions | Close distance | t (pin) | Approaches | Break % | Move through | t |
|---|---|---|---|---|---|---|---|
| **US Tech 100** @25 strikes | 687 | 0.2517 | +0.31 | 5,846 | 48.4% | −0.0571 | −1.40 |
| US Tech 100 @25 *control* | 687 | 0.2483 | −0.31 | 5,986 | 50.3% | +0.0453 | +1.07 |
| US Tech 100 @50 strikes | 687 | 0.2510 | +0.19 | 5,469 | 48.7% | −0.0178 | −0.80 |
| US Tech 100 @50 *control* | 687 | 0.2490 | −0.19 | 5,433 | 49.9% | +0.0268 | +1.16 |
| US Tech 100 @100 strikes | 687 | 0.2557 | +1.03 | 4,446 | 47.4% | −0.0210 | −1.66 |
| US Tech 100 @100 *control* | 687 | 0.2443 | −1.03 | 4,432 | 48.3% | −0.0159 | −1.22 |
| **US SP 500** @5 strikes | 687 | 0.2489 | −0.20 | 5,858 | 50.0% | +0.0188 | +0.43 |
| US SP 500 @5 *control* | 687 | 0.2511 | +0.20 | 5,815 | 50.7% | +0.0649 | +1.44 |
| US SP 500 @25 strikes | 687 | 0.2442 | −1.06 | 3,979 | 48.7% | +0.0081 | +0.70 |
| US SP 500 @25 *control* | 687 | 0.2558 | +1.06 | 3,913 | 49.3% | +0.0197 | +1.60 |
| US SP 500 @50 strikes | 687 | 0.2523 | +0.41 | 2,493 | 43.9% | −0.0022 | −0.27 |
| US SP 500 @50 *control* | 687 | 0.2477 | −0.41 | 2,492 | 44.7% | +0.0097 | +1.16 |
| **Germany 40** @50 strikes | 671 | **0.2360** | **−2.51** | 4,484 | 48.2% | −0.0140 | −0.76 |
| Germany 40 @50 *control* | 671 | 0.2640 | +2.51 | 4,567 | 48.0% | −0.0052 | −0.31 |
| Germany 40 @100 strikes | 671 | 0.2502 | +0.03 | 3,103 | 45.5% | +0.0036 | +0.33 |
| Germany 40 @100 *control* | 671 | 0.2498 | −0.03 | 3,119 | 44.3% | −0.0206 | −1.92 |
| **XAUUSD** @5 strikes | 688 | 0.2572 | +1.31 | 5,453 | 49.0% | −0.0213 | −0.63 |
| XAUUSD @5 *control* | 688 | 0.2428 | −1.31 | 5,415 | 47.9% | −0.0312 | −0.91 |
| XAUUSD @10 strikes | 688 | 0.2513 | +0.23 | 4,385 | 48.2% | −0.0056 | −0.27 |
| XAUUSD @10 *control* | 688 | 0.2487 | −0.23 | 4,750 | 47.1% | −0.0254 | −1.31 |
| XAUUSD @25 strikes | 688 | 0.2534 | +0.62 | 2,769 | 43.6% | −0.0160 | −1.39 |
| XAUUSD @25 *control* | 688 | 0.2466 | −0.62 | 2,865 | 45.2% | −0.0090 | −0.81 |
| **BTCUSD** @1000 strikes | 972 | 0.2510 | +0.21 | 4,933 | 44.4% | −0.0130 | −1.65 |
| BTCUSD @1000 *control* | 972 | 0.2490 | −0.21 | 4,768 | 45.6% | −0.0029 | −0.34 |
| BTCUSD @2500 strikes | 972 | 0.2500 | +0.01 | 2,481 | 36.7% | +0.0048 | +0.97 |
| BTCUSD @2500 *control* | 972 | 0.2500 | −0.01 | 2,363 | 36.4% | −0.0028 | −0.57 |
| BTCUSD @5000 strikes | 972 | 0.2524 | +0.52 | 1,201 | 26.4% | +0.0040 | +1.05 |
| BTCUSD @5000 *control* | 972 | 0.2476 | −0.52 | 1,327 | 23.6% | +0.0035 | +1.04 |

**Reading the columns.** *Close distance* is how far the session close sat from the nearest level,
in steps; a uniformly random price gives **0.25**, below is pinned, above is repelled. *Break %* is
how many approaches ended the hour beyond the level. *Move through* is the mean move past the
level in steps — positive is continuation, negative is a fade.

---

## 3. What it says

**Nothing. On any market, at any spacing.**

- **Magnet: dead.** Every market sits at 0.2360–0.2572 from the nearest strike against the 0.25 a
  random price gives. Price closes no nearer a strike than chance.
- **Barrier/break: dead.** Break rates 44–50% (coin flips), mean move through the level around one
  hundredth of a step. **Not a single \|t\| ≥ 2 in the entire "move through" column** — strikes or
  controls.
- **Where strikes look slightly negative, the controls often look slightly positive.** No
  consistent direction anywhere.

### The one cell that reaches significance, and why it is not evidence

Germany 40 @50 shows a close distance of 0.2360, t −2.51 — a mild pin. Two reasons to discard it:

1. It is one cell out of thirteen market/spacing combinations. At that count you expect about 0.65
   hits at \|t\| ≥ 2 by chance. One hit is one hit.
2. **The magnet test's "control" is not independent.** Distance to a half-step-shifted grid is
   mechanically `0.5 − distance to the real grid`, so its t is always the exact mirror. That column
   is uninformative for the magnet test and is printed only because removing it would be a quieter
   kind of dishonesty. The barrier/break control IS independent and shows nothing.

### What this does and does not rule out

**Ruled out, and well powered:** strike-ness alone. 2,500–5,900 approaches per cell, 671–972
sessions. This is better powered than almost anything else in this project.

**Not ruled out:** that the two or three strikes carrying *enormous open interest* behave
differently from the other forty. Testing every strike equally is the weaker form of the
hypothesis — on a 25-point NDX grid price is always near some strike, so "near a strike" carries
almost no information. A genuine "call wall" is one level, not forty.

But the grid being completely inert means any effect has to come **entirely** from open-interest
concentration, with no help at all from the strike level itself. That is a narrower claim than
the one that started this, and it needs data nobody gives away.

---

## 4. Why the flow version could not be backtested

| Source | What it gives | History | Cost |
|---|---|---|---|
| **Deribit** (BTC/ETH) | full chain with open interest; every option trade with strike, call/put, aggressor side, size, **and the underlying price at that instant** | **~36 hours** | free, no key |
| ORATS / OptionData / Databento | the same for US equities | years | from ~$99–599/mo |

Deribit's public trade feed is *exactly* the right shape — `BTC-27NOV26-99000-C buy 1.0 index
83045.03` tells you a call at 99,000 was bought while BTC was at 83,045. It just does not go back
far enough to test anything: probing 12h, 24h, 48h, 7d, 30d, 90d, 1y and 2y, the first zero
appears at 48 hours.

So the flow hypothesis is **untested, not disproven.** Two honest routes:

1. **Collect forward.** A small poller against Deribit's public endpoint accumulates real option
   flow with no key and no cost. In a few weeks there is a sample; in a few months, a test.
2. **Buy one month of historical OPRA** (a few hundred dollars, not a subscription) and answer the
   lead/lag question for SPX offline. That remains the purchase I would make before any feed.

---

## 5. What came out of this instead, and it is the best filter yet

Chasing the regime idea to something a strategy can compute **for itself** — no VIX, no external
feed, works on any instrument — produced the largest measured improvement in the book.

**The measure:** the instrument's own 20-day realised volatility divided by its 60-day.
Self-normalising, needs 60 days of its own bars, answers "is this market speeding up or winding
down relative to its own normal".

**TrendBreakout_v1, ten markets, bucket chosen on 2021-10 → 2024-09 and scored unchanged after:**

| Bucket | In-sample | Out-of-sample |
|---|---|---|
| **expanding** (ratio > 1.15) | **+0.216R** (n 66, t +2.13, PF 1.92) | **+0.232R** (n 56, t +1.79, PF 2.89, +11.6%, DD 2.8%) |
| middle (0.85–1.15) | +0.078R (n 180) | −0.054R (n 199) |
| quiet (< 0.85) | +0.099R (n 171) | +0.043R (n 177) |
| **unfiltered** | +0.102R (n 444) | **+0.023R** (n 432) |

**Ten times the unfiltered expectancy out of sample, and it came out higher than it went in**
(+0.216 → +0.232) with the bucket ordering preserved. The mechanism is not mysterious: a breakout
is a bet on continuation, and continuation is likelier when the market is speeding up than when
it is winding down.

Compared against the VIX versions, which were also tested:

| Filter | Pick | Out-of-sample | vs base +0.023R |
|---|---|---|---|
| **rv20/rv60 ratio** | **expanding** | **+0.232R** | **10×** |
| realised-vol tercile | low | +0.085R | 3.7× |
| VIX level tercile | low | +0.063R | 2.7× |
| VIX term structure | contango | +0.018R | worse |

The self-contained measure beat both VIX versions — which is fortunate, because VIX is a daily
FRED series a live strategy could not read in time anyway.

**Shipped as `vol_filter` on `TrendBreakoutParams`, default `"off"`.** It rests on 56
out-of-sample trades (t +1.79) and cuts trade count by about 85%, so it is offered rather than
imposed. Turning it on is the single biggest improvement measured for that strategy. 11 tests
cover the mechanics; `WINDOW_BARS` widens from 2,016 to 6,144 bars only when it is switched on.

**What was deliberately not done:** "avoid the middle bucket", even though mid is the only
significantly negative cell out of sample (−0.054R, and −0.162R at t −3.56 on the tercile
version). In-sample mid was +0.078R, so nobody could have chosen to avoid it in advance. A filter
chosen with hindsight is not a filter.

**It did nothing for the other three strategies.** ORB, OpeningDrive and OvernightSession show no
usable regime split on any of the four measures.

---

## 6. A bug this work uncovered

Running the same backtest twice — once with `vol_filter` on — returned **byte-identical results**.
The filter never reached the engine.

**`BacktestRequest` has exactly one field for strategy parameters, `strategy_params`. The Strategy
Lab page sent them under a GROUP-KEYED block** (`{trend_breakout: {...}}`, `{ivw: {...}}`) because
that is how parameters are addressed everywhere else — in the schema, in `UserConfigV2`, in
`slotSpec.js`. Pydantic ignores fields it does not declare, so:

- **"Run preview" ran the dataclass defaults** and reported them as if they were your settings.
  No error, no warning, HTTP 200.
- **"Promote" wrote the same block to localStorage**, where the Backtester's `NESTED_FORM_KEYS`
  does not read it — so a promoted config arrived stripped a second time.

The whole Strategy Lab "create → preview → promote" loop silently ignored every parameter it was
given. Fixed on both paths, and `tests/test_frontend_request_contract.py` now parses both pages'
request literals and fails on any key `BacktestRequest` does not declare — including a computed
key, which is the exact shape of this bug.

Verified after the fix: the same XAUUSD run goes from 435 signals / 22 trades to **52 signals /
3 trades** with `vol_filter="expanding"`.

---

## 7. What I would do

1. **Stop pursuing strike levels.** The grid is inert and the test was well powered.
2. **Turn `vol_filter="expanding"` on for TrendBreakout** and watch it. It is the one thing here
   with walk-forward evidence behind it.
3. **Start the Deribit collector** if you want the flow hypothesis tested at all. It is free and
   the only thing standing between here and a real answer is elapsed time.
4. **Do not buy a feed yet.** One month of historical OPRA settles lead/lag for a few hundred
   dollars; a subscription does not settle anything faster.

---

## 8. Reproduction

```bash
py -3.12 scripts/run_strike_levels.py
py -3.12 scripts/run_vix_regime_filter.py
py -3.12 -m pytest tests/test_frontend_request_contract.py tests/test_shipped_strategies_2026_09_25.py -q
```
