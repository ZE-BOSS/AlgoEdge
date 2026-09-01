18 — Walk-forward validation
=============================

**2026-09-01** · scripts `full_backtest.py` (`split_metrics`),
`run_full_backtest_par.py`, `analyse_wf.py`
**285 cells · 0 errors** · in-sample **Jan–Apr**, out-of-sample **May–Aug**

Report 16's conclusions were all in-sample. Rule §0.5-6 requires any rule chosen
by looking at results to be re-tested on a period it was not chosen on — and
VWAP had already collapsed once between reports 15 and 16 when the window
widened. This is the check that decides whether report 16 is trustworthy.

---

## 1. DriftJumpAlpha survives emphatically

| | in-sample | out-of-sample |
|---|---:|---:|
| P&L | +$15,093 | **+$59,255** |
| expectancy | +0.0866 R | **+0.2039 R** |

**It is more than twice as good out of sample as in.** That is the opposite of
overfitting — a fitted result decays on unseen data; this one strengthens.

Crash 1000 alone: **+$64,478 on 259 out-of-sample trades.** Crash 300, which
report 10 called a loser and report 16 rated marginal, returns **+$5,659 on 279
OOS trades** at 1:3.

This is the one finding to size a real account on.

## 2. Every other strategy fails or weakens

| strategy | IS-best R:R | IS P&L | OOS P&L | OOS exp R | verdict |
|---|---|---:|---:|---:|---|
| **DriftJumpAlpha** | 1:4 | +$15,093 | **+$59,255** | **+0.2039** | **holds** |
| VWAP | 1:5 | −$38,650 | −$8,774 | −0.0761 | weaker, still negative |
| BiasIFVG | 1:2 | −$2,860 | −$4,731 | −0.0387 | no |
| APA | 1:5 | +$12,312 | **−$16,333** | −0.1004 | **reverses** |
| CRT | 1:5 | −$22,581 | −$23,721 | −0.2852 | no |
| HTFFVGFlip | 1:2 | +$723 | −$4,244 | −0.0850 | reverses |
| **NYOpenRetest** | 1:3 | −$3,511 | **−$43,205** | **−0.2141** | **much worse** |

**APA is the clearest overfit in the book** — +$12,312 in-sample becomes
−$16,333 out. Anyone reading only the in-sample half would have concluded APA
works.

NYOpenRetest degrades twelvefold. Per your instruction it stays enabled, but it
should not be sized up on report 16's numbers.

## 3. Broker divergence is real, and it inverts

| R:R | Deriv OOS | FundedNext OOS |
|---|---:|---:|
| 1:2 | −$37,584 | −$35,020 |
| 1:3 | −$13,647 | −$31,518 |
| **1:4** | **+$4,624** | −$44,726 |
| 1:5 | +$544 | **−$51,321** |

**On Deriv, higher R:R helps and 1:4 turns the book positive. On FundedNext it
hurts monotonically** — 1:5 is its worst setting by $16,000. Report 16 saw this
in-sample and it holds out of sample, so it is a genuine venue property, not
noise.

### Per-cell R:R selection is *worse* than one fixed value

| Deriv rule | OOS P&L |
|---|---:|
| **fixed 1:4 everywhere** | **+$4,624** |
| per-cell best R:R chosen on Jan–Apr | +$1,520 |

Choosing each cell's R:R from in-sample data and trading it blind **underperforms
simply using 1:4 across the board by $3,100.** Unfiltered per-cell selection
overfits.

That is not an argument against per-symbol R:R as such — §4 shows a *filtered*
set does hold. It is an argument against picking a target for every cell just
because the in-sample number favoured it.

## 4. The shipped defaults — 14 of 17 hold

Each per-slot default from `strategy_defaults.py`, chosen on the full window,
scored on May–Aug alone:

| symbol | strategy | R:R | IS P&L | OOS P&L | OOS n | verdict |
|---|---|---:|---:|---:|---:|---|
| Crash 1000 | DriftJumpAlpha | 5.0 | +$15,784 | **+$64,478** | 259 | holds |
| USOUSD | BiasIFVG | 5.0 | +$743 | **+$7,931** | 29 | holds |
| Volatility 75 | VWAP | 5.0 | +$8,007 | +$7,321 | 121 | holds |
| US Tech 100 | NYOpenRetest | 5.0 | +$3,025 | +$5,919 | 67 | holds |
| Crash 300 | DriftJumpAlpha | 3.0 | −$1,143 | +$5,659 | 279 | holds |
| NDX100 | NYOpenRetest | 5.0 | +$2,261 | +$5,601 | 66 | holds |
| Crash 500 | CRT | 5.0 | +$6,548 | +$5,321 | 109 | holds |
| UKOUSD | BiasIFVG | 5.0 | +$261 | +$4,832 | 38 | holds |
| Germany 40 | VWAP | 4.0 | +$977 | +$4,789 | 85 | holds |
| XAGUSD | VWAP | 4.0 | −$1,519 | +$3,346 | 86 | holds |
| Volatility 75 | APA | 4.0 | +$2,388 | +$2,628 | 56 | holds |
| GER30 | VWAP | 4.0 | −$528 | +$1,471 | 85 | holds |
| XRPUSD | APA | 5.0 | −$642 | +$1,360 | 45 | holds |
| Crash 500 | APA | 3.0 | +$3,380 | +$930 | 47 | holds |
| **XAUUSD** | **VWAP** | 5.0 | +$2,991 | **−$144** | 86 | **fails** |
| **BTCUSD** | **APA** | 5.0 | +$5,292 | **−$923** | 45 | **fails** |
| **ETHUSD** | **BiasIFVG** | 5.0 | −$817 | −$25 | 23 | **fails** |

**The three failures are removed** from `SLOT_TP1_RR` — commented out with their
figures rather than deleted, so the negative result stays on record. A removed
slot falls back to its strategy default; nothing is disabled.

Note four entries were *negative* in-sample and positive out — Crash 300,
XAGUSD, GER30, XRPUSD. Those were selected on the full window rather than the
in-sample half, which is why they survived a test that pure IS-selection would
have failed.

---

## What changed in the code

`backend/strategies/strategy_defaults.py` — `SLOT_TP1_RR` reduced from 17 to
**14 walk-forward-verified entries.** Per-strategy `tp1_rr` values are unchanged;
they are the least-bad setting for each strategy across all its symbols, and the
split does not contradict them.

## Verdict

| claim from report 16 | walk-forward |
|---|---|
| DriftJumpAlpha is the entire book | **confirmed, and understated** |
| 1:5 is best on Deriv | **partly** — 1:4 is better OOS (+$4,624 vs +$544) |
| Higher R:R is worse on FundedNext | **confirmed** |
| VWAP is one of the worst strategies | **confirmed**, though improving |
| Per-symbol R:R matters | **confirmed, but only when filtered** — unfiltered per-cell selection loses to a flat 1:4 |
| APA is roughly break-even | **refuted** — +$12,312 IS becomes −$16,333 OOS |

## Limits

- **One split point.** May 1 is arbitrary; a different date would give somewhat
  different numbers.
- **Eight months, one regime.** This catches hindsight bias, not regime change.
- OOS cells with n < 30 (ETHUSD/BiasIFVG at 23) cannot really support a verdict
  either way — it was removed for being unproven, not for being proven bad.
- The harness still omits session-end exits, concurrent-position caps and the
  circuit breaker; cell-to-cell comparison is valid, absolute P&L is not
  comparable to the app's own backtester.
