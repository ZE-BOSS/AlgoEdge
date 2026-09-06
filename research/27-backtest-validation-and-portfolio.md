27 — Are these backtests trustworthy? And what belongs in one portfolio?
========================================================================

**2026-09-05** · scripts `audit_user_backtests.py`, `validate_user_backtests.py`,
`replay_user_backtest_fills.py`, `portfolio_correlation.py`

Inputs: the **22 backtests** in `debug/backtest/` and the **8 live screenshots**
in `debug/live trade/`, re-priced against 365 days of raw ticks.

---

## 0. The verdict, up front

| question | answer |
|---|---|
| Are the backtest returns real? | **No.** Two independent inflations, ~30× and ~10×. |
| Is there a bug? | **Yes** — 96–97% of stop exits fill at exactly the stop price. |
| Is the live account doing well? | **Yes, genuinely** — but on 3 days and one bet repeated. |
| Can a single spike blow it up? | **No.** Worst spike in a year = 5.2% of equity. |
| Is the portfolio diversified? | **No.** 11 positions, all long, almost all Crash 300. |
| Can it be fixed? | **Yes** — the correlation data gives a concrete allocation. |

The honest summary: **the strategies are probably not worthless, but the
backtests overstate them by roughly 20–100×, and the live account is currently
running one concentrated bet that happens to be in its good regime.**

---

## 1. The headline numbers, and the two things inflating them

The best run reads **+16,090%** ($10,000 → $1.62M on Crash 1000). Decomposed:

### 1.1 Compounding — `sizing_basis: EQUITY`

Re-running the *same trade sequence* with fixed-dollar risk:

| symbol / strategy | n | exp R | EQUITY ret | STATIC ret | inflation |
|---|---:|---:|---:|---:|---:|
| Crash 1000 / SpikeFade | 2,216 | +0.136 | **+16,090%** | +543% | **29.6×** |
| Range Break 200 / RangeBreakout | 1,936 | +0.147 | +12,867% | +513% | 25.1× |
| Boom 1000 / SpikeFade | 2,245 | +0.116 | +8,089% | +470% | 17.2× |
| Crash 1000 / DriftJumpAlpha | 817 | +0.222 | +1,803% | +326% | 5.5× |
| Crash 300 / DriftJumpAlpha | 871 | +0.116 | +327% | +182% | 1.8× |

Compounding is not a bug — but **+16,090% is not a measure of edge**, it is
+0.136 R per trade raised to the power of 2,216 trades. The edge number is the
one to reason about.

### 1.2 The fill model — this is the actual bug

**96–97% of every stop exit is booked at exactly the stop price.**

| exit type | n | filled AT the stop |
|---|---:|---:|
| TRAIL_SL | 1,115 | **96.0%** |
| SL | 1,016 | **97.1%** |
| BE_SL | 81 | **100.0%** |

On Crash and Boom that assumption is the one that cannot hold, because the
instrument's defining feature is a one-tick jump. Spot-checking real ticks at the
moment each stop was breached:

```
stop 4653.748  ->  tick before 4660.462, first tick through 4653.295
stop 4666.809  ->  tick before 4672.140, first tick through 4663.494
stop 4675.047  ->  tick before 4684.229, first tick through 4674.687
```

Price does not walk to the stop, it **jumps past it**. Measured across every
stop exit in the file, as a fraction of the trade's own stop distance:

| symbol | n | median | mean | p99 | worst |
|---|---:|---:|---:|---:|---:|
| Crash 1000 | 2,190 | 0.158 R | **0.403 R** | 7.36 R | **18.54 R** |
| Boom 1000 | 2,230 | 0.159 R | 0.353 R | 4.94 R | 15.67 R |
| Crash 1000 (DJA) | 805 | 0.135 R | 0.334 R | 5.60 R | 17.93 R |

**Every stopped trade loses an average of 0.33–0.40 R more than the backtest
books.** The strategies earn +0.12 to +0.22 R per trade. The unbooked slippage is
larger than the entire edge.

### 1.3 Repricing the user's own trades from ticks

Same trades, same volumes, same targets — only stop exits re-priced at the first
tick that actually traded through the level:

| symbol / strategy | booked P&L | tick-fill P&L | change |
|---|---:|---:|---:|
| Crash 1000 / SpikeFade | +$1,609,033 | **+$137,974** | −91.4% |
| Range Break 100 / RangeBreakout | +$896,855 | **+$100,299** | −88.8% |
| **Boom 1000 / SpikeFade** | +$808,918 | **−$203,072** | **−125.1%** |
| **Crash 1000 / DriftJumpAlpha** | +$180,328 | **−$58,276** | **−132.3%** |

**Two of the four flip from large profits to losses.** The other two keep about a
tenth of what they showed.

*Method note, because the first version of this got it wrong:* `exit_time` is the
M5 bar **open**, not the fill moment — measured against ticks, the first crossing
sits +98 s (p75) to +282 s (p99) after it. Searching backwards from `exit_time`
finds the trail level at some earlier price it had already visited, a different
event entirely, and produced overstated losses. The window above is
`[exit_time, exit_time + 310]`, which matches **99%** of stop exits (2,190/2,212).

---

## 2. These backtests do not test the strategies I shipped

Exit mix for Crash 1000 / SpikeFade:

| exit | n | share of net P&L |
|---|---:|---:|
| **TRAIL_SL** | 1,115 | **+299%** |
| SL | 1,016 | −203% |
| TP1 | **4** | +3% |
| BE_SL | 81 | +1% |

**The take-profit fired 4 times in 2,216 trades.** The entire result is the ATR
trailing stop — the single most fill-sensitive exit there is, and the one
re-priced hardest in §1.3.

The runs used `trail_method_tp1: ATR_TRAIL`, `be_mode: EITHER`, `be_trigger_rr: 1`.
The shipped defaults I committed set **trailing OFF, break-even OFF, one target**
— because that is how research/26 measured them, and because research/25 §4.1
measured break-even costing up to 0.154 R/trade on Boom.

So these 22 files measure a *different strategy* from the one in the repo. That
is not wrong to try — but the numbers cannot be compared to research/26, and the
research/26 numbers do not validate these.

---

## 3. The live account — what is actually happening

From the screenshots: **balance $11,165.18, equity $11,662.96**, so roughly
**+$1,663 on ~$10,000 in about three days.** That is real money and a real result.

Two things about it are worth separating.

**What is genuinely fine.** A single spike cannot hurt you badly. Your open book
is 24.10 lots long Crash 1000 at ~6,085, and Crash 1000 pays $1 per point per lot:

| event | move | loss | % of equity |
|---|---:|---:|---:|
| median spike | 0.091% | −$133 | 1.14% |
| p90 spike | 0.197% | −$289 | 2.48% |
| p99 spike | 0.285% | −$418 | 3.59% |
| **worst spike in 365 days** | 0.415% | −$608 | **5.22%** |

And spikes do not cluster — of 313 p99-sized spikes in the year, **zero** arrived
within 200 ticks of another. That matches the memoryless arrival in research/20
and means "spike after spike after spike" is not the failure mode to fear.

**What is not fine — concentration.** The 4 Sep screenshot shows **11 simultaneous
positions, every one long, almost all Crash 300**. That is not 11 bets, it is one
bet at 11× size. Your four open Crash 1000 longs share a stop zone ~49 points
below price: if that zone is reached, all four go together for **≈$1,190, or 10.2%
of equity**, against a nominal 1.8% per trade.

Your risk per *event* is therefore about 10%, not 1.8%. Three such events in a
week is a 30% drawdown, and nothing in the configuration prevents them.

The reason it is working right now is that **Crash has been grinding up**, which
is exactly the regime a long-the-grind book wins in. That is not evidence of edge
yet — three days cannot distinguish edge from regime.

---

## 4. Correlation: what actually belongs in one portfolio

### 4.1 The instruments are genuinely independent

Daily log returns, 364 usable days, 12 symbols:

**mean |pairwise correlation| = 0.037, max = 0.112.**

Deriv generates each synthetic from its own process, so cross-symbol
diversification is *real*, not assumed. This is the single most useful fact here.

### 4.2 But two strategies on the same symbol are the same bet

Strategy daily-P&L correlation, from your own 22 backtests, 160 common days:

| pair | correlation |
|---|---:|
| Crash 1000 / SpikeFade ↔ Crash 1000 / DriftJumpAlpha | **+0.50** |
| Crash 500 / DriftJumpAlpha ↔ Crash 500 / SpikeFade | **+0.48** |
| Crash 300 / DriftJumpAlpha ↔ Crash 300 / SpikeFade | **+0.29** |
| Boom 500 / SpikeFade ↔ Boom 500 / BoomDriftJump | +0.22 |
| Crash 1000 / DriftJumpAlpha ↔ Crash 1000 / TrendDrift | +0.21 |

Every one of the top pairs is **two strategies on one symbol**. Meanwhile the mean
across all 231 pairs is **+0.010**, giving **18.1 effective independent bets out
of 22 cells** — because cross-symbol pairs are essentially uncorrelated.

**The rule this gives you is simple and strong:**

> Diversify across **symbols**. Never stack two strategies on the **same** symbol —
> that is where all the correlation is, and it is the one thing your live account
> is currently doing.

### 4.3 A concrete allocation

One strategy per symbol, spread across families, using the tick-validated
configurations from research/26 rather than the trailing ones:

| symbol | strategy | why |
|---|---|---|
| Crash 1000 | TrendDrift | best Crash cell, PF 1.30, DD 20.0% |
| Boom 500 | RangeRevert | best Boom cell, PF 1.17, DD 19.7% |
| Range Break 100 | SpikeFade | PF 1.25, DD 22.2%, uncorrelated with Crash/Boom |
| Volatility 100 | RangeRevert | different family entirely; −0.25 to −0.03 vs the above |
| Jump 25 | RangeRevert | PF 1.16, and Jump/Boom pairs run negative |

Five cells, one per symbol, mean pairwise correlation near zero → roughly **5
effective bets**. At 1% risk each that is 5% at risk with genuine
diversification, versus your current ~10% concentrated in one direction on one
family.

**Avoid:** Crash 300 + Crash 1000 + Crash 500 simultaneously (all long the same
grind), and any symbol carrying two strategies at once.

---

## 5. What I would change, in order

1. **Turn off `allow_pyramiding`, or cap `max_positions_per_symbol` at 1.**
   This is the single highest-value change. It converts a ~10% per-event risk back
   into the 1.8% you intended, and it costs almost nothing in return because the
   stacked positions are ~0.5 correlated — you are paying 4× the risk for maybe
   1.5× the independent information.
2. **Fix the fill model before trusting another backtest.** `engine.py:993`
   detects gaps only at the bar **open**; on a jump instrument the gap is
   *intrabar*, so it never fires. Until that is fixed every Boom/Crash backtest
   the app produces is overstated by roughly the numbers in §1.3, and the tick
   data to calibrate it is already in `research/data/ticks/`.
3. **Re-run the 22 backtests with `sizing_basis: STATIC`** so you are reading edge
   rather than exponent. Then compare to research/26.
4. **Spread across symbols, one strategy each** (§4.3).
5. **Keep the live account running** — but judge it on 200+ trades, not 3 days,
   and expect the drawdown to look much worse than the backtests suggested.

## 6. What I cannot tell you

Whether these strategies have a real edge. Three days of live trading and a
backtest with a broken fill model cannot answer it, and research/24 gives a strong
prior that they do not. What §1.3 *does* establish is that the edge, if any, is
much smaller than 16,090% — closer to the +0.12 R/trade before slippage, and
plausibly negative after it on Boom 1000 and Crash 1000.

The live account is the only clean evidence you will get. Run it small, run it
un-stacked, and let it accumulate trades.
