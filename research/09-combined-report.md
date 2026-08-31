Combined report — what is wrong and what to do about it
=======================================================

**30 August 2026.** This replaces reports 01–08 as the thing to read. Those stay
on disk with the detail; everything important is repeated here in plain terms.

---

## First, the three words you need

Everything below is measured in **R**. One R is *the money you decided to risk on
a trade*. If you risk $100 and the trade goes to plan, +2R means you made $200.
−1R means you lost your $100.

**Expectancy** is the average result per trade in R. If expectancy is +0.10R and
you risk $100 a trade, you make about $10 per trade on average across many
trades. If it is −0.18R, you *lose* about $18 per trade on average. That is the
single number that decides whether a system makes money.

**Spread** is what the broker charges you on every trade — the gap between the
buy price and the sell price. You pay it whether you win or lose.

That is all the vocabulary needed.

---

## Correction notice (read this first)

An earlier version of this report led with a claimed bug: that seven symbols had
been backtested with **no trading costs at all**. **That was wrong, and I have
retracted it.**

All 21 symbols were charged costs. The engine stores the cost model under an
uppercased key (`"CRASH 1000 INDEX"`), and my audit looked it up under the exact
symbol name (`"Crash 1000 Index"`). It matched for symbols that are already
uppercase — BTCUSD, EURUSD, XAUUSD — and silently missed every mixed-case one.
So the seven "zero-cost" symbols were exactly the seven with spaces in their
names. Verified after fixing the lookup: **21 of 21 have costs, none missing.**

A second claimed bug — that spreads were stored in mismatched units and some
symbols mis-charged up to 1000× — **is also retracted.** I compared the backtest
figure using one conversion constant and the live figure using a different one.
Because spread and risk are stored in the *same* per-symbol unit, their ratio
(which is what reaches P&L) cancels the convention entirely.

Two scripts built on the false premise — `corrected_portfolio.py` and
`corrected_walk_forward.py` — are marked INVALID at the top of each file. Ignore
any figure from them. The valid out-of-sample analysis is `walk_forward.py`.

**What survives unchanged:** everything measured from `pnl_r` directly, which
needs no unit conversion — the friction decomposition, the confluence ranking,
the geometry results, and the out-of-sample test. Those are Findings 1–4 below.

---

## The short version

Your backtest showed **−$92,102** across 116 runs. Two things caused it.

1. **Broker costs, not bad strategies, are most of the loss.** On a normal
   market, entering at random loses you *exactly the spread* — no more, no less.
   Your strategies were mostly paying a toll they could never earn back.
2. **Four of your main confluences actively make results worse.** FVG, break of
   structure, retest and displacement all pick entries that do *worse* than
   entering at random. Two things do work: liquidity sweeps, and buying cheap /
   selling expensive within a range.

The good news: **your take-profit setting is already correct**, and you have two
or three genuinely profitable setups. The problem is that they're buried under
18 that lose.

---

## Finding 1 — friction is most of the loss

This is measured from the trades' own R results, so no unit conversion is
involved and the correction above does not touch it.

Take every trade and replace each stop-out with exactly −1.00R and each target
with exactly +5.00R — i.e. remove all spread, slippage and gap cost, change
nothing else:

| | per trade | total |
|---|---:|---:|
| what actually happened | −0.180 R | −1,343.5 R |
| with zero trading cost | −0.064 R | −479.3 R |
| **the difference — friction** | **−0.116 R** | **−864.2 R** |

**64% of the loss is friction.** Stops fill at an average of **−1.199R** instead
of −1.000R: you lose about 20% more than you intended on every losing trade.

It is very unevenly spread:

| symbol | average result at stop | (−1.000 = costless) |
|---|---:|---|
| XRPUSD | **−1.350 R** | worst |
| SOLUSD | −1.324 R | |
| ETHUSD | −1.308 R | |
| BTCUSD | −1.276 R | |
| Volatility 75 | −1.110 R | |
| Crash 1000 | −1.025 R | |
| Crash 300 | **−1.012 R** | best — almost costless |

Crypto is where the money goes. Crash indices are nearly free to trade. That
ranking alone explains a large part of which setups made money.

---

## Finding 2 — you were mostly paying a toll you couldn't earn back

I pulled **1.14 million bars straight from your MT5 terminal** (24 symbols,
January to now) and tested something completely independent of your strategies:
*if you entered at a random moment, what happens?*

The answer, on every normal market symbol, is that **you lose exactly the spread
and nothing more**:

| symbol | broker cost | result of random entry |
|---|---:|---:|
| Platinum (XPTUSD) | 0.407 R | −0.3995 R |
| EURGBP | 0.222 R | −0.2198 R |
| Hong Kong 50 | 0.180 R | −0.1869 R |
| Gold (XAUUSD) | 0.021 R | −0.0267 R |

Those two columns match almost perfectly. That is the market working normally.

**One caveat:** I read those spreads on a Saturday, and weekend spreads are wider
than weekday ones — badly so on instruments that are closed, like Hong Kong 50.
The relationship (you lose the spread) is solid; the individual cost figures for
closed markets are an over-estimate and should be re-read during the week.

**Why this matters so much:** platinum costs you 0.407R per trade. The best
confluence I could find anywhere is worth about 0.027R. So on platinum you would
need a signal **fifteen times better than anything that exists in your system**
just to break even. Platinum, EURGBP, Hong Kong 50 and Netherlands 25 cannot be
traded profitably by any strategy you have, at any settings. It isn't a strategy
problem.

**The cheapest symbols are the only ones worth trading.** Gold at 0.021R and US
Tech 100 at 0.021R are the cheap end. That ranking *is* the ranking of what you
should trade.

---

## Finding 3 — which of your confluences actually work

I broke every structure your strategies use into its smallest form and measured
each one on all 24 symbols. The question was simple: *when this condition is
true, does price go further in your favour than when it isn't?*

**These help:**

| confluence | value per trade | worked on |
|---|---:|---|
| Liquidity sweep (low taken, price closes back above) | **+0.027 R** | 16 of 24 symbols |
| Buying in the cheap third of the range | **+0.023 R** | 15 of 24 |
| Selling in the expensive third of the range | **+0.019 R** | 16 of 24 |
| Liquidity sweep (high taken, closes back below) | **+0.018 R** | 15 of 24 |

**These do nothing:**

| confluence | value | worked on |
|---|---:|---|
| Higher-timeframe bias | +0.003 R | **27 of 48 — a coin flip** |
| Session (London / NY / Asia) | ≈ 0 | roughly half |
| Wick rejection | −0.004 R | 20 of 48 |

**These actively hurt you:**

| confluence | value | worked on |
|---|---:|---|
| Displacement | −0.014 R | 24 of 47 |
| **Fair Value Gap (FVG)** | **−0.017 R** | 24 of 48 |
| Volume spike | −0.021 R | 15 of 36 |
| **Retest** | **−0.027 R** | 12 of 48 |
| **Break of structure (BOS)** | **−0.037 R** | **11 of 48** |

Read that last block carefully. These aren't just useless — trades taken *because*
of them do **worse than entering at random on the same instrument**.

**And this is exactly what your strategies are built on.** FVG, break of
structure, retest and displacement are the backbone of APA, CRT, BiasIFVG,
HTFFVGFlip and NYOpenRetest. Liquidity sweeps and premium/discount — the two
things that do work — are barely used.

That is the clearest explanation of why the book is red.

**One caution:** these are averages. "Worked on 16 of 24" means it helps more
often than not, not always. And all these numbers are small compared to the
broker costs in Finding 2. **No confluence rescues an expensive symbol.** Fix
costs first, confluences second.

---

## Finding 4 — your settings

**Take-profit: keep 1:5. It is already right.**
I tested every target from 0.5R to 5R. Across your whole book the difference
between the best and worst setting is 0.05R — nothing, against a 0.18R loss. On
the setups that genuinely work, the further target is clearly better. Don't
change it.

**Partial take-profits: no. Don't add them.**
Taking money off early caps your winners and the maths doesn't work. On
DriftJumpAlpha — your best system — partials cut it from +0.227R to +0.102R.
Its profit comes from a small number of very large winners. Cut those short and
you have nothing.

**Stop distance: go wider, not tighter.**
This one is counter-intuitive. On every normal market symbol the best stop was
the widest I tested (3× ATR). The reason: the spread is a fixed cost in points.
The wider your stop, the smaller that cost is *as a share of what you risk*.
Going from a 0.5× ATR stop to 3× ATR cuts your cost by **six times**.

The exception is the Crash indices, where a tight stop (0.5× ATR) with a far
target is correct, because their profit comes from rare, very large moves.

**Break-even at 1R: keep it.**
It looks like a leak — 30% of your trades reach +1R, get moved to break-even and
end at zero, after reaching an average of 1.76R first. That looks like money left
on the table. But it isn't: those same trades would otherwise mostly become full
losses, and banking them early costs more than it saves.

---

## Finding 5 — what you should actually trade

I tested the selection honestly: pick the setups using only **January–April**
data, then trade them blind through **May–August** with the costs corrected.

| approach | result per trade, out of sample |
|---|---:|
| Trade everything (all 116 setups) | **−0.178 R** |
| Keep only setups with 50+ trades that were profitable Jan–Apr | **+0.101 R** |

The stricter the filter, the better it held up out of sample (+0.009R at 20+
trades, +0.036R at 30+, +0.101R at 50+). That pattern is the signature of a real
effect — overfitting gets *worse* as you tighten, not better.

Only three survived when traded blind:

| setup | result per trade, May–Aug |
|---|---:|
| DriftJumpAlpha on Crash 1000 | **+0.313 R** |
| DriftJumpAlpha on Crash 300 | **+0.257 R** |
| VWAP on Volatility 75 | **+0.160 R** |

Four others looked good in January–April and **lost money** May–August: CRT on
Volatility 75, APA on Bitcoin, APA on XRP, VWAP on Silver. That is why the
out-of-sample test matters — without it you'd have traded all seven.

**Two setups you have never tested, and should:**

- **Crash 500** — returns +0.303R on random entry alone. **Now tested, and the
  answer is not what I expected:** DriftJumpAlpha *loses* $696 on it over the
  same period it makes $417 on Crash 1000. The instrument is good; this strategy
  cannot harvest it. Worth trading with the plain geometry rule (buy, tight stop,
  far target), not with DriftJumpAlpha.
- **Jump 100 (sell side)** — +0.045R on random entry, but DriftJumpAlpha
  physically cannot trade it: it is hard-coded to Crash symbols only. Would need
  a new strategy.

**Something worth thinking hard about:** on Crash 1000, entering at *random* with
the right stop and target returns **+0.634R per trade**. DriftJumpAlpha, with all
its logic, returns +0.249R on the same instrument. Your entry rules are currently
making it worse, not better. The geometry is the edge there — not the signal.

---

## Where the stages stand

| stage | what it was | status |
|---|---|---|
| **A** Backtest baseline | run everything, get clean numbers | ✅ **done** — 116 runs |
| **B** Which confluences work | ✅ **done** — answered from raw market data |
| **C** Fundamentals | order flow, GEX, calendar | ⚠️ **partly** — order flow reconstructible, book/GEX not |
| **D** Fundamentals + technicals | ⛔ blocked, waits on C |
| **E** Portfolio | what combination to trade | ✅ **done** |
| **F** Crash sell-side | can we trade the crashes? | ✅ **done** — no, proven three ways |
| **G** Final verdict | ✅ **done** |
| **H.1** Fix the blocking bugs | ✅ **done and verified today** |
| **H.2** Re-run the sweep | ⚠️ **partly done** — 3 missing cells run, see `research/10` |
| **H.3** Apply the findings | ⏳ waits on H.2 |
| **I** Make fundamentals usable | ⏳ not started |
| **J** Charts and UI | ⏳ not started |
| **K** Outside research | ⏳ not started |

### What was fixed today

**Your frontend hang is fixed.** The cause was one callback. `run_backtest`
hands the simulation to a background thread, and the progress callback then
called `asyncio.create_task(...)` from that thread — which always raises
`RuntimeError: no running event loop` there. A bare `except: pass` swallowed it,
so **not one progress update was ever sent while a backtest ran**. The bar sat
still until the run finished and then jumped to 100%.

Three fixes, each verified against real data rather than assumed:

| bug | fix | proof |
|---|---|---|
| **B4** progress never reached the browser | capture the loop first, use `run_coroutine_threadsafe` | the old call raises RuntimeError off-loop; the new one does not |
| **B5** progress fired ~4× per run | stride is now `bars / 200` instead of a fixed 1,024 | 5,000-bar run: **4 → 200** updates |
| **B7** no gate tracking in any run | switch the recorder on for backtests | CRT now reports `session_filter: 1057 blocked`, against `{}` in all 116 runs |

B5 also makes the callback save state, so the page no longer needs a manual
refresh to show results.

*One correction:* I originally said four call sites had the B4 bug. Only one
did — the other three already ran on the event loop and were fine.

### What is genuinely left

- **H.2 — re-run the sweep.** Now worth doing: it will produce gate-level data
  for the first time, and can include the three symbols never tested (AUDUSD,
  Crash 500, Jump 100). A few hours of machine time.
- **I — fundamentals.** Order flow can be reconstructed from historical ticks
  after an overnight download. Order book and options data are gone for good
  historically — those need storage built and then months of collecting.
- **J — charts/UI** and **K — outside research** are build projects, not
  analysis. Nothing is blocking them except time.

## What I'd do, in order

1. **Stop trading the expensive symbols.** Platinum, EURGBP, Hong Kong 50,
   Netherlands 25 — the broker takes more than any signal you have can earn.
   This needs no code change, just removing them from the traded list.
2. **Fix the progress bar bugs** (B4/B5) so you can actually watch a run. This
   is the frontend hang you reported.
3. **Turn on gate tracking** (B7) and **re-run the sweep**, including the three
   symbols never tested (AUDUSD, Crash 500, Jump 100).
4. **Test removing FVG, BOS, retest and displacement** from the strategies that
   use them. All four measured negative.
5. **Sweep the daily trade cap.** Gate tracking now works, and it shows the cap
   throws away **77% of every candidate** DriftJumpAlpha considers — far more
   than all its technical filters combined. It is the most consequential
   untested parameter in the system.

Steps 2–3 are code changes I have not made. You asked me not to commit anything,
and they touch live trading logic. Say the word and I'll do them.

---

## Files

Everything is local, in `C:\Users\ikchr\Documents\AlgoEdge\`:

- `research/09-combined-report.md` — this file
- `research/01`–`08` — the detailed reports
- `research/data/*.py` — every script, re-runnable
- `research/data/cache/` — 1.14M bars of MT5 history (120 files)
- `debug/backtest-bugs.md` — bugs B1–B9
- `implementation/Strategy-Fundamental-Optimization.md` — the plan, updated
