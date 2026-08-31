The independent market analysis
===============================

**Produced 2026-08-30.** Data pulled **directly from MT5**, not from the saved
backtest: 24 symbols × 5 timeframes, 1 Jan 2026 → 30 Aug 2026, **1.14 million
bars** cached locally in `research/data/cache/`.

Scripts: `fetch_cache.py`, `excursion_engine.py`, `optimise_exits.py`,
`confluence_atoms.py`, `measure_atoms.py`.

Why this exists: the previous reports measured the saved backtest and stopped
where its instrumentation stopped. That was the wrong move. This measures the
market itself, independently, and then uses it to audit the backtest.

---

## 1. RETRACTED — there was no zero-cost bug

This section originally claimed that 7 of 21 symbols were backtested with no
cost model. **That was wrong.** All 21 have one. The engine keys the cost model
by `symbol.upper()`, and my audit looked it up by the exact symbol string, so it
missed every symbol whose name contains a space or mixed case. The "seven
affected symbols" were precisely the seven with spaces in their names.

The follow-up claim of a unit-conversion bug (spread stored in points for some
classes, pips for others, mis-charging up to 1000x) is **also retracted**: I
converted the backtest figure with `get_pip_size()` and the live figure with MT5
`point`, which differ by a per-symbol factor of 1, 10 or 1000. Since spread and
risk share the same unit inside the engine, the ratio that reaches P&L is
unaffected.

Full write-up in `debug/backtest-bugs.md` under **B8 RETRACTED** and
**B9 RETRACTED**.

**What replaces it.** Friction is still real and still the largest single drag —
but it is measured from `pnl_r` directly, with no unit conversion and no cost
model involved. Stops fill at −1.199R against an intended −1.000R; removing all
friction moves the book from −0.180R to −0.064R per trade. That is report 01
section "Where the money actually goes", and it stands unchanged.

**Also worth flagging:** the live spreads used in sections 2–3 below were read on
a Saturday. Weekend spreads are inflated, severely so for closed markets (Hong
Kong 50, Netherlands 25, Germany 40). The *relationship* those sections establish
holds; the absolute cost figures for closed markets are over-estimates and should
be re-measured during trading hours.

## 2. Random-entry expectancy equals minus the spread, almost exactly

At every M15 bar, both directions, stop-aware forward excursion over 24 hours,
across every stop distance and target. The best achievable expectancy per
symbol, against that symbol's cost:

| symbol | cost at best stop | best expectancy | difference |
|---|---:|---:|---:|
| XPTUSD | 0.407 R | −0.3995 R | 0.008 |
| EURGBP | 0.222 R | −0.2198 R | 0.002 |
| Hong Kong 50 | 0.180 R | −0.1869 R | −0.007 |
| Netherlands 25 | 0.176 R | −0.1787 R | −0.003 |
| SOLUSD | 0.131 R | −0.1383 R | −0.007 |
| USDCHF | 0.126 R | −0.1146 R | 0.011 |
| XAUUSD | 0.021 R | −0.0267 R | −0.006 |

**Expectancy = −cost, to within a hundredth of an R, on every real-market
symbol.** That is the market being efficient: with no edge you lose exactly the
spread, no more and no less.

Two consequences, and they are the backbone of everything below:

1. **Cost ranking *is* tradeability ranking.** XPTUSD at 0.407 R needs a
   confluence worth +0.41 R just to break even. The best atom measured below is
   worth +0.027 R. XPTUSD, EURGBP, Hong Kong 50 and Netherlands 25 are not
   tradeable by any strategy in this codebase, at any settings.
2. **The strategies were not the problem on those symbols.** No entry logic can
   overcome a 0.4 R toll.

---

## 3. Optimal stop distance — wider, not tighter

Cost as a fraction of R scales inversely with stop distance:

| symbol | 0.5×ATR | 1.0×ATR | 2.0×ATR | 3.0×ATR |
|---|---:|---:|---:|---:|
| XAUUSD | 0.123 R | 0.062 R | 0.031 R | **0.021 R** |
| BTCUSD | 0.183 R | 0.092 R | 0.046 R | **0.031 R** |
| EURUSD | 0.414 R | 0.207 R | 0.103 R | **0.069 R** |
| SOLUSD | 0.788 R | 0.394 R | 0.197 R | **0.131 R** |
| XPTUSD | 2.440 R | 1.220 R | 0.610 R | **0.407 R** |

Going from a 0.5×ATR stop to 3.0×ATR cuts the spread toll **6×**. On every
real-market symbol the optimal stop was **3.0×ATR** — the widest tested — purely
because that minimises the toll.

**This inverts the MAE finding from report 02.** Winners only take 0.23–0.43 R
of heat, which looks like an argument for tight stops. It is the opposite:
tighter stops shrink R, and the spread is fixed in price terms, so the fraction
of R paid to the broker rises. Report 02's caution was right and this confirms
it from independent data.

**The synthetics are the exception, and the reason is instructive.** On Crash
indices the optimal stop is **0.5×ATR** with a **5 R** target — the tightest and
the furthest. Their edge lives in a fat tail (slow drift up, violent gap down),
so a small R denominator maximises the multiple when the tail pays. Cost
matters less there because the spread is small relative to ATR.

---

## 4. Where a real edge exists — including symbols never backtested

Best geometry per symbol and direction, random entry, costs charged:

| symbol | side | stop | TP | P(hit) | expectancy |
|---|---|---:|---:|---:|---:|
| **Crash 1000 Index** | BUY | 0.5×ATR | 5 R | 29.5% | **+0.6335 R** |
| **Crash 500 Index** | BUY | 0.5×ATR | 5 R | 23.3% | **+0.3029 R** ← never backtested |
| **Crash 300 Index** | BUY | 0.5×ATR | 5 R | 22.6% | **+0.2052 R** |
| **US Tech 100** | BUY | 0.75×ATR | 5 R | 18.0% | **+0.0512 R** |
| **Jump 100 Index** | SELL | 0.5×ATR | 5 R | 18.3% | **+0.0447 R** ← never backtested |
| **XAUUSD** | SELL | 1.0×ATR | 5 R | 18.2% | **+0.0291 R** |
| **US SP 500** | BUY | 1.5×ATR | 3 R | 26.1% | **+0.0121 R** |
| Volatility 75 | BUY | 2.0×ATR | 2.5 R | 29.9% | +0.0049 R |

Everything else is negative in both directions.

**Crash 1000 BUY at +0.6335 R with random entry** is the single largest number
in this whole programme. DriftJumpAlpha earns +0.249 R on the same instrument —
*less than a coin flip with the right geometry*. That is worth saying plainly:
on Crash indices the geometry is the edge, and the strategy's entry logic is
currently subtracting from it.

**Crash 1000 SELL is −0.1343 R.** Independent confirmation, from a completely
different method, that the sell side does not work — this time without any
appeal to tick resolution or arrival statistics.

**Crash 500 (+0.3029 R) and Jump 100 SELL (+0.0447 R) were never run.** Both are
live on the terminal.

> **Now tested — see `research/10`.** DriftJumpAlpha on Crash 500 **loses $696**
> over the same window it makes $417 on Crash 1000, so "the strongest untested
> candidate" was wrong as a *strategy* recommendation. The geometry finding
> stands; the strategy cannot harvest it. Jump 100 could not be run at all —
> DriftJumpAlpha is hard-gated to Crash symbols (`crash_symbol_only` blocked
> 19,700 of 19,700).

---

## 5. Confluence atoms — ranked by measured lift

Each structure reduced to its smallest unit and measured on all 24 symbols,
M15, 1×ATR stop, 2 R target. `lift` is expectancy with the atom true minus
expectancy over all bars in the same direction.

| atom | symbol-sides | mean lift | positive on |
|---|---:|---:|---:|
| **sweep_low** (sell-side liquidity taken, closed back in) | 24 | **+0.0267 R** | 16/24 |
| **discount** (lower 38% of the 50-bar range) | 24 | **+0.0226 R** | 15/24 |
| **premium** (upper 38%, for sells) | 24 | **+0.0194 R** | 16/24 |
| **sweep_high** | 24 | **+0.0177 R** | 15/24 |
| session_asia | 48 | +0.0064 R | 25/48 |
| htf_aligned | 48 | +0.0028 R | **27/48** |
| session_london | 48 | +0.0003 R | 19/48 |
| session_ny | 48 | −0.0032 R | 23/48 |
| wick_reject | 48 | −0.0039 R | 20/48 |
| displacement | 47 | −0.0144 R | 24/47 |
| **fvg** | 48 | **−0.0172 R** | 24/48 |
| volspike | 36 | −0.0213 R | 15/36 |
| **retest** | 48 | **−0.0269 R** | 12/48 |
| **bos** | 48 | **−0.0367 R** | 11/48 |

### What this says

**Liquidity sweeps and premium/discount positioning are the only atoms with
consistent positive lift.** Both are mean-reversion signals: price overshot a
level and came back, or price sits at an extreme of its range.

**FVG, BOS, retest and displacement all have negative lift.** They are not
neutral — they select entries *worse* than random on the same instrument. BOS is
the worst at −0.0367 R and is positive on only 11 of 48 symbol-sides. These four
are momentum/continuation signals, and on this asset set over this window,
continuation did not pay.

**HTF bias is a coin flip: positive on 27 of 48, mean +0.0028 R.** It is one of
the most heavily weighted concepts in the strategy set and it carries
essentially no information.

**This maps directly onto why the book lost money.** The strategies are built
predominantly on FVG, BOS, retest and displacement — the four negative atoms —
and make little use of sweeps and premium/discount, the positive ones.

### Honest limits on this table

- Lift is measured at one geometry (1×ATR, 2 R). The ranking should be re-run
  across geometries before anything is retired on it.
- The magnitudes are small — +0.027 R at best — against costs of 0.02 to 1.22 R.
  **No confluence rescues an expensive symbol.** Cost control dominates.
- "positive on 16/24" means these are tendencies, not laws. Per-symbol tables
  are in `measure_atoms.out`.
- An atom firing on 43% of bars (discount) selects far less than one firing on
  5% (displacement); frequency and lift must be read together.

---

## 6. Direct answers

**Best stop distance.** 3.0×ATR on real-market symbols — it minimises the spread
toll, which is the dominant term. 0.5×ATR on Crash indices, where the edge is in
the tail. Not the same answer everywhere, and the mechanism differs.

**Best risk:reward.** 5 R on every instrument that has an edge (Crash 300/500/
1000, US Tech 100, Jump 100, XAUUSD sell). On instruments with no edge the
"best" R:R is 0.5 R, but that only means losing slowest — not a trade worth
taking. **Your current 1:5 is correct.** Do not change it.

**Partials.** No. On Crash 1000 expectancy climbs monotonically with the target
(−0.377 R at 0.5 R → +0.162 R at 5 R, pooled). Cutting the runner cuts the edge.
This agrees with the independent finding from your backtest data (report 07),
reached by a different route.

**Risk management.** The largest available gain is not in sizing or trailing —
it is in refusing to trade instruments whose toll exceeds any obtainable edge.
Enforce a minimum stop-to-spread ratio and the four worst symbols disqualify
themselves.

**Am I leaving money on the table?** Yes, in one specific place: Crash 1000 BUY
random entry with 0.5×ATR/5 R returns +0.6335 R, while DriftJumpAlpha returns
+0.249 R on the same instrument. The entry filter is currently costing more than
it adds.

---

## 7. What changes in the earlier reports

- **Report 01** — the friction finding is confirmed independently, by a method
  that shares none of its assumptions: random entry loses exactly the spread.
  Report 01's decomposition (64% of the loss is friction) stands.
- **Report 02** — "blocked" was too quick. Confluence value *can* be measured
  without the gate recorder, by measuring the market. Section 5 is the answer
  Stage B wanted. The gate recorder is still needed to measure the strategies'
  *implementations* of these atoms, which is a different question.
- **Report 07** — "keep TP at 1:5" and "no partials" both survive. The claim
  that only synthetics work is **wrong**: US Tech 100, US SP 500 and XAUUSD
  (sell side) carry positive geometry-only expectancy. The recommended portfolio
  should widen accordingly, and CRT × Volatility 75 should come out of it.
