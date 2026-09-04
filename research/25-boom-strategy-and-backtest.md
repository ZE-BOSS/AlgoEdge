25 — BoomDriftJump: the strategy, and what it actually earns
=============================================================

**2026-09-04** · `backend/strategies/strategy_boom/engine.py`,
`research/data/phase5_boom_backtest.py`
Backtest window: **1 January 2026 → 4 September 2026**, executed on raw ticks.

**Built as asked. It loses money.** The Boom mirror of DriftJumpAlpha shows
**+0.2880 R** per trade in a conventional bar backtest and **−0.1399 R** with
realistic fills. The whole apparent edge is the gap artifact from research/24 §4.1.

---

## 1. What was built

`BoomDriftJump_v1`, registered in the strategy registry alongside the other seven.
It is DriftJumpAlpha reflected, because Boom is Crash reflected:

| | Crash (DJA) | Boom (this) |
|---|---|---|
| Setup A | **BUY** the upward grind | **SELL** the downward grind |
| Setup B | **SELL** after a down-spike | **BUY** after an up-spike |
| Regime | EMA fast > EMA slow | EMA fast < EMA slow |
| Confirmation | break of swing **low** | break of swing **high** |
| Gap-blocked side | drift buys blocked after a spike | drift sells blocked after a spike |

One thing is deliberately **not** symmetric. On Crash the down-spike gaps through
a *long's* stop, so Setup A carried the gap risk. On Boom the up-spike gaps
through a *short's* stop — so on this instrument the drift setup is the exposed
side. The stop is set wide (`max(2.5 × ATR, sep + ATR)`) for that reason, and
`trail_method` is `NONE`.

## 2. The backtest, and why it disagrees with the app's

Two differences, both from research/24:

- **Fills come from ticks.** A stop is a market order and fills at the first tick
  *through* the level. On Boom that is wherever the spike lands. Both existing
  harnesses book a flat −1.0 R instead.
- **Significance accounts for overlap.** The independent (non-overlapping) subset
  is reported alongside the raw figure.

Indicators are imported from the production DJA engine, so ATR and ADX are the
exact code that runs live.

### Headline

| strategy | n | win | **naive R** | **real R** | slip | t | indep n | indep R | indep t |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **Boom 1000** drift SELL | 1,472 | 21.5% | **+0.2880** | **−0.1399** | −0.4280 | −1.97 | 362 | −0.2284 | −1.65 |
| **Boom 500** drift SELL | 1,469 | 19.8% | +0.1886 | −0.0526 | −0.2411 | −0.80 | 339 | −0.0608 | −0.44 |
| Crash 1000 drift BUY *(control)* | 1,467 | 24.3% | +0.4601 | +0.0747 | −0.3855 | +1.01 | 345 | +0.1214 | +0.78 |
| Crash 500 drift BUY *(control)* | 1,463 | 13.9% | −0.1634 | −0.4141 | −0.2507 | −7.23 | 342 | −0.2450 | −1.92 |

**The `naive` column is what your current backtester would print.** The `real`
column is what the same trades earn when stops fill where the price actually
went. On Boom 1000 that is the difference between a strategy you would deploy and
one that loses 0.14 R a trade.

This is, precisely, the thing you described at the start: strategies that look
good in research and cannot be reproduced live.

## 3. Geometry sweep — is any version better?

Sixteen stop/target combinations per symbol.

**Boom 1000 — every geometry has a positive naive expectancy and a
non-positive real one:**

| stop | target | naive R | **real R** | slip | indep t |
|---:|---:|---:|---:|---:|---:|
| 0.5 × ATR | 1.5 R | +0.9429 | −0.0872 | **−1.0302** | −1.13 |
| 0.5 × ATR | 3.0 R | +1.5163 | −0.1108 | **−1.6271** | −1.45 |
| 0.5 × ATR | 5.0 R | +2.0122 | −0.0816 | **−2.0938** | −0.12 |
| 0.5 × ATR | 8.0 R | +2.3200 | −0.2283 | **−2.5482** | −0.75 |
| 1.0 × ATR | 5.0 R | +1.0462 | −0.1384 | −1.1846 | −0.65 |
| 2.5 × ATR | 1.5 R | +0.2891 | **+0.0277** | −0.2613 | +0.43 |
| 2.5 × ATR | 5.0 R | +0.2880 | −0.1399 | −0.4280 | −1.65 |

The best Boom geometry earns **+0.0277 R at t = +0.69** — indistinguishable from
zero. Nothing here is tradeable.

**The artifact scales inversely with stop width, exactly as predicted.** At
0.5 × ATR the unmodelled slippage is **−1.03 to −2.55 R per trade**; at 5.0 × ATR
it is −0.16 to −0.20 R. A backtest at a tight stop on these instruments is not
approximately wrong, it is wrong by more than the entire signal.

## 4. Crash at wide stops looks positive — and it is the window, not an edge

The whole wide-stop row on Crash 1000 survives, consistently, which is more
interesting than one lucky cell:

| stop | target | naive R | real R | slip | t | indep R | **indep t** |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 2.5 × ATR | 8.0 R | +0.7029 | +0.2793 | −0.4236 | +2.84 | +0.2371 | +1.13 |
| 5.0 × ATR | 1.5 R | +0.2007 | +0.0433 | −0.1574 | +1.18 | +0.1217 | +1.50 |
| 5.0 × ATR | 3.0 R | +0.3798 | +0.1785 | −0.2013 | +3.32 | +0.2666 | **+2.20** |
| 5.0 × ATR | 5.0 R | +0.4467 | +0.2185 | −0.2283 | +3.09 | +0.3844 | **+2.28** |
| 5.0 × ATR | 8.0 R | +0.6230 | **+0.3758** | −0.2472 | +4.01 | **+0.5448** | **+2.24** |

Four adjacent geometries, all positive, independent t between 2.20 and 2.28. That
is a pattern, not noise — and the profit *grows monotonically with target size*,
which is the clue.

**It is realized drift in the sample window.** Over 1 Jan → 4 Sep 2026:

| | start | end | move | annualised | in sigma |
|---|---:|---:|---:|---:|---:|
| **Crash 1000** | 4,651.75 | 6,140.80 | **+32.01%** | **+47.5%/yr** | **+2.22 σ** |
| Boom 1000 | 16,774.13 | 14,605.23 | −12.93% | −19.2%/yr | −0.90 σ |

The strategy is **100% long on Crash**. In a window where the instrument rose
2.2 sigma, a long-only rule shows positive expectancy whether or not it has an
edge — and the wider the target, the longer the hold, the more of that drift it
collects. That is exactly the monotonic pattern in the table.

Over 7.4 years Crash 1000's Itô-corrected excess drift is **−4.23% ± 7.96%/yr**
(research/20 §2) — indistinguishable from zero, and *negative* at the point
estimate. The +47.5%/yr in this eight-month window is a sample fluctuation, and
the January-to-date window cannot tell the two apart.

What the sweep *does* establish is a **design principle**, and that part is solid:
on jump instruments the correct stop is **wide**, because stop width governs the
size of the gap artifact. That is the opposite of report 08's recommendation of a
0.5 × ATR stop on Crash indices, which this table shows would have been the worst
available choice by a factor of ten.

## 4.1 The ADX gate subtracts value

DriftJumpAlpha's ADX ≥ 20 trend filter, tested on and off, on both instruments:

| | n | win | real R | indep R | indep t |
|---|---:|---:|---:|---:|---:|
| Boom 1000, **with** ADX gate | 1,472 | 21.5% | −0.1399 | −0.2284 | −1.65 |
| Boom 1000, **no** ADX gate | 1,472 | 22.3% | **−0.0713** | **−0.1334** | −0.93 |
| Crash 1000, **with** ADX gate | 1,467 | 24.3% | +0.0747 | +0.1214 | +0.78 |
| Crash 1000, **no** ADX gate | 1,468 | 24.2% | +0.0634 | **+0.1298** | +0.83 |

**On Boom, removing the filter improves the result by 0.069 R per trade.** On
Crash it makes no material difference — marginally worse on the raw figure,
marginally *better* on the independent one, both well inside noise.

So the honest reading is narrower than "the gate costs money": it is that
**the gate never helps in either test, and on Boom it measurably hurts.** Both
Boom rows remain negative regardless, so this is not a route to a working
strategy — but a filter that earns nothing on its home instrument and costs
0.069 R on the mirror is worth re-examining before it is carried anywhere else.
(Trade counts barely move because the six-per-day cap binds either way.)

## 5. Verdict

- **BoomDriftJump is built, registered and shipped.** It is not recommended for
  live trading: −0.1399 R per trade on Boom 1000, −0.0526 R on Boom 500.
- **No Boom geometry is tradeable.** The best of sixteen is +0.0277 R at t = 0.69.
- **The mirror hypothesis is answered.** Boom behaves exactly as research/24
  predicted for a fair martingale with memoryless jumps: gross ≈ 0, net ≈ −cost,
  for every geometry.
- **Crash at wide stops is not an edge.** It is a long-only rule measured in a
  window where Crash 1000 rose 2.22 sigma (§4). Over 7.4 years the same
  instrument's excess drift is −4.23% ± 7.96%/yr.
- **The ADX gate never helps.** It costs 0.069 R per trade on Boom and is inside
  noise on Crash (§4.1) — worth re-examining wherever else it is used.
- **A January-to-date window cannot answer this question.** Eight months is short
  enough that realized drift dominates, and on Crash it did so by +47.5%/yr. Any
  future backtest on this book needs either a multi-year window or an explicit
  drift control.

The strategy file stays in the repo because the measurement is more convincing
with it there — anyone can re-run `phase5_boom_backtest.py` and see both columns.

## 6. If you want to keep going on this

The single change that would matter most is not another strategy: it is
**putting gap-aware fills into `backend/backtester/engine.py`**. Right now every
result the app produces on a jump instrument carries the error in §3, and it is
large enough to invert the sign of a strategy. The tick data to calibrate it
already exists in `research/data/ticks/`.
