12 — Riptide: building a strategy from only the winning confluences
====================================================================

**2026-08-30** · scripts `riptide.py`, `run_riptide.py`,
`riptide_confluence_sweep.py`
Data: real MT5 M15 bars, 24 symbols, Jan–Aug 2026. $10,000 per asset, 1% risk.

---

## The verdict, first

**It does not work. The winning atoms do not stack into an edge.**

I built exactly what you described — liquidity sweep plus premium/discount,
confluence-scored, nothing else — and tested it on every asset class at 1:2, 1:3
and 1:5. It loses on 22 of 24 symbols at every target. Raising the confluence
threshold does not rescue it.

This is a negative result worth having: it says the atoms measured in report 08
are **descriptive, not tradeable**, and it saves building the strategy into the
live system to find that out.

---

## What was built

**Riptide** — named for the pattern: water drags out past the shoreline, then
returns. Price sweeps past a level to take stops, then comes back.

Entry: price trades below a prior swing low (the sweep), closes back above it
(the reclaim), and sits in the lower part of its range (discount). Mirror for
sells. Stop beyond the sweep extreme plus a buffer. Fixed R target.

**Confluence score, 1 point each (max 5):**

| # | condition |
|---|---|
| 1 | sweep and reclaim happened |
| 2 | in discount (buy) / premium (sell) |
| 3 | in the extreme third, not just the near half |
| 4 | the swept level was clean — untouched for the lookback |
| 5 | the reclaim bar closes in the top/bottom third of its range |

Deliberately excluded: FVG, break of structure, retest, displacement, HTF bias,
volume. All measured neutral or negative in report 08.

---

## Results at minimum confluence 2

P&L on $10,000 per asset:

| class | symbol | signals | 1:2 | 1:3 | 1:5 |
|---|---|---:|---:|---:|---:|
| metal | **XAUUSD** | 600 | −900 | −1,420 | **−331** |
| crypto | **BTCUSD** | 798 | −351 | −769 | −1,456 |
| fx | **GBPJPY** | 678 | −4,259 | −4,581 | **−1,382** |
| index | US Tech 100 | 634 | −4,198 | −5,124 | −5,385 |
| index | US SP 500 | 695 | −5,645 | −6,108 | −3,712 |
| fx | EURUSD | 672 | −7,553 | −6,171 | −5,946 |
| crypto | ETHUSD | 796 | −6,851 | −7,429 | −6,790 |
| crypto | SOLUSD | 799 | −8,719 | −8,530 | −9,123 |
| index | Netherlands 25 | 598 | −8,816 | −8,892 | −8,615 |
| fx | EURGBP | 724 | −8,987 | −9,194 | −8,764 |
| metal | **XPTUSD** | 714 | −9,957 | −9,963 | **−9,968** |

Best case is XAUUSD at 1:5, still −$331 with PF 1.02. Worst is Platinum, which
loses **99.7% of the account**.

Drawdowns run 27% to 99.6%. Nothing here is tradeable.

---

## Does raising the confluence bar help? No.

Your design intent was to trade only the highest-confluence setups. Swept 2→5
across 8 symbols:

| min score | 1:2 exp R | 1:3 exp R | 1:5 exp R | profitable symbols |
|---:|---:|---:|---:|---|
| 2 | −0.1132 | −0.1053 | −0.0696 | 2 of 8 |
| 3 | −0.1158 | −0.1109 | −0.0717 | 1 of 8 |
| 4 | −0.1117 | −0.0959 | −0.0626 | 1 of 8 |
| 5 | −0.1152 | −0.0976 | −0.1013 | 3 of 8 |

**Flat.** If the confluences genuinely stacked, expectancy would climb as the bar
rose — fewer, better trades. It does not move. Demanding all five conditions
gives the same result as demanding two.

One exception: **EURUSD at score 5, 1:5 → +0.1036 R, PF 1.11, +$1,430.** That is
1 symbol of 8 on 189 trades. Under §0.5-6 that is a candidate, not a finding.

---

## Why it fails, and it is not subtle

The atom edges from report 08 are **+0.018 to +0.027 R**. The cost of trading is
**+0.02 R on US Tech 100, 0.16 R on GBPUSD, 0.21 R on EURUSD, 0.41 R on
Platinum.**

**On all but the very cheapest symbols the broker takes more than the signal is
worth.** Riptide then compounds it by trading 600–800 times per symbol in eight
months, paying that toll on each.

The atoms are real — they measure a genuine tilt in forward excursion. They are
just an order of magnitude too small to survive execution. A +0.027R edge needs
costs under about 0.01R to be worth trading, and only US Tech 100 comes close.

**This also revises the confluence framing in report 08.** I ranked the atoms and
called four of them "what works". More precisely: they are the *least bad*. None
is large enough to build on.

---

## Which strategies actually produced the directional returns

You asked who made the money per symbol. From the 116-run sweep:

| symbol | direction | strategy | n | total R | exp R |
|---|---|---|---:|---:|---:|
| Crash 1000 | BUY | **DriftJumpAlpha** | 340 | **+122.3** | +0.360 |
| Crash 300 | BUY | **DriftJumpAlpha** | 356 | **+51.1** | +0.144 |
| Volatility 75 | SELL | **VWAP** | 89 | +39.1 | +0.440 |
| XRPUSD | SELL | APA | 53 | +24.6 | +0.464 |
| XAUUSD | BUY | **BiasIFVG** | 29 | +23.8 | **+0.821** |
| BTCUSD | SELL | BiasIFVG | 33 | +21.6 | +0.655 |
| BTCUSD | SELL | APA | 72 | +21.2 | +0.295 |
| US Tech 100 | BUY | NYOpenRetest | 51 | +15.2 | +0.299 |
| US Tech 100 | BUY | VWAP | 54 | +13.8 | +0.255 |

Worst: APA BUY on Volatility 75 (−73.1 R), VWAP on SOLUSD both directions
(−103.8 R combined), NYOpenRetest BUY on BTCUSD (−1.001 R/trade).

**Direction skew per symbol** (all strategies pooled):

| symbol | BUY R | SELL R | verdict |
|---|---:|---:|---|
| Crash 1000 | **+122.3** | 0 | buy side only — never traded short |
| Crash 300 | **+51.1** | 0 | buy side only |
| XAUUSD | +6.2 | −10.4 | buy side only |
| US Tech 100 | +16.6 | −24.1 | buy side only |
| XAGUSD | −30.9 | +2.4 | sell side only |
| *(16 others)* | — | — | **both directions negative** |

Only **4 of 21 symbols** have a profitable side at all. That is the real shape of
the book.

---

## What the outside research says, and where it disagrees with our data

I searched before running any of this, as you asked.

**Liquidity sweeps.** Practitioner sources claim sweep setups win **65–75%** and
that a sweep into a supply/demand zone raises reversal probability sharply —
which is exactly Riptide's premise. **Our data does not support it.** Measured
across 24 symbols, the sweep atom is worth +0.027 R and Riptide's win rate at
1:2 sits near 35%, not 65%. Those claims come from instructional blogs, not
studies, and none state a sample size.

**Trailing stops.** The literature is consistent that trailing beats a fixed
target only in trending regimes (Hurst > ~0.55) and is negative in
mean-reverting ones. I measured Hurst on every symbol: **all sit between 0.476
and 0.513** — random walks, none trending. That correctly predicted the small
trailing gain found in report 11.

**Scaling out.** Sources agree partials mathematically reduce expected value per
winner, and that whether it pays depends on how often price reaches the far
target. In your book only **7.1%** of trades reach 5R — so partials are a bad
deal, matching the direct measurement.

**Session effects.** Genuine academic evidence exists for intraday seasonality in
FX returns and volatility, with activity peaking in London and the London–NY
overlap. One finding is directly useful and contradicts equity intuition:
**spreads are narrower during high-activity hours**. Since cost is the dominant
term in this book, that argues for concentrating trading in the London–NY
overlap for cost reasons alone, independent of any directional edge.

---

## Recommendation

**Do not build Riptide into the system.** It has no edge at any confluence
threshold or target.

**Do not pursue confluence-stacking as a design approach.** The measurement says
these structures are too small to trade, and stacking them does not compound.

**The lever that remains is cost, not signal.** Every result across reports 08,
11 and 12 points the same way: the confluences are worth hundredths of an R and
the spread costs tenths. Cutting the traded universe to the cheapest symbols is
worth more than any entry rule found so far.
