11 — Exit management: your ratchet idea, tested on your real trades
===================================================================

**2026-08-30** · scripts `research/data/exit_lab.py`,
`exit_lab_real_trades.py`, `run_exit_lab.py`

**Data provenance — no simulated or randomised prices anywhere.** Every bar comes
from `mt5.copy_rates_range()` against your Deriv terminal. Verified against the
live terminal this session: 3,000 overlapping XAUUSD M15 bars, **max close
difference 0.000000**. The cache contains 34 weekend gaps (max 73h), i.e. a real
session calendar. Gold prints at $4,458.

**Entries are your real trades.** All **7,463** trades from `backtest_trades` —
their actual entry price, actual stop, actual direction, actual timestamp —
matched to the real bar history (**7,463 matched, 0 skipped**) and walked forward
bar by bar. The only thing varied is how the trade is managed after entry.

---

## Correction to my first pass

The first version of this report was wrong and has been replaced. My walker
marked any trade still open at the horizon at its **best excursion** rather than
its final price. That credits a peak the trade never banked — the exact
high-water-mark error §0.5-1 of the plan exists to prevent — and it flattered
whichever method resolves least often.

It inflated the headline from **+0.0660 R** to a claimed +0.1510 R, and produced
nonsense per-strategy lifts of +0.37 to +0.44 R. Both harnesses now mark
unresolved trades at the **last close**. Every number below is post-fix.

---

## Result on your real trades

| method | exp R | win rate | PF | max DD |
|---|---:|---:|---:|---:|
| `fixed_TP_only` (1:5, no trailing) | +0.0634 | 22.1% | 1.07 | 704.5 |
| `BE_at_1R` | +0.0620 | 27.3% | 1.11 | 606.2 |
| `loose_ladder` | +0.0586 | 32.5% | 1.09 | 631.1 |
| `step_1R_behind` | +0.0619 | 36.2% | 1.09 | 590.2 |
| **your `user_ladder`** | +0.0528 | 40.7% | 1.09 | 568.3 |
| **`tight_ladder`** | **+0.0660** | **41.1%** | **1.12** | **466.9** |

`tight_ladder` = 1.0R→+0.05, 1.5R→+0.75, 2.0R→+1.50, 2.5R→+2.00, 3.0R→+2.50,
4.0R→+3.50, target 5R.

### What this actually says

**On expectancy alone, the ratchet is close to a wash.** `tight_ladder` beats a
plain fixed target by **+0.0026 R** — real, but small. Anyone claiming trailing
transforms your returns is reading a bug, as I was.

**On risk, it is decisive.** Max drawdown **704.5 → 466.9 R, a 34% reduction**,
and win rate **22.1% → 41.1%**. You asked to protect risk without leaving money
on the table. This is precisely that trade: the same money, taken with a third
less drawdown and nearly double the hit rate.

**Your ladder works but is slightly too loose.** `user_ladder` returns +0.0528
against `tight_ladder`'s +0.0660. The difference is that yours waits until 2.0R
to lock 1.0R, while the tight version is already at +0.75R by 1.5R. Locking
earlier and closer to the high-water mark is what wins.

### An honest limit on the comparison

The `ACTUAL (as traded)` figure is **−0.1800 R**, and every simulated method
beats it. **Do not read that as "the ladder would have made you money."** My
walker does not reproduce the live engine's session-end exits, daily trade cap,
time limits or concurrent-position rules, and it uses a fixed 400-bar horizon.
The valid comparison is **ladder against ladder**, all run through the identical
harness. The gap to `ACTUAL` measures harness differences, not edge.

---

## Best ladder per strategy

| strategy | n | best ladder | exp R |
|---|---:|---|---:|
| DriftJumpAlpha | 696 | `loose_ladder` | **+0.3117** |
| VWAP | 2,186 | `fixed_TP_only` | +0.1239 |
| NYOpenRetest | 1,164 | `BE_at_1R` | +0.0655 |
| CRT | 890 | `tight_ladder` | +0.0521 |
| BiasIFVG | 1,108 | `step_1R_behind` | +0.0181 |
| APA | 1,236 | `fixed_TP_only` | +0.0057 |
| HTFFVGFlip | 183 | `step_1R_behind` | −0.0163 |

**The best ladder is not the same for every strategy**, which is the strongest
argument for making it per-strategy configuration exactly as you proposed.

- **DriftJumpAlpha wants the loosest ladder.** Its edge is in rare large winners,
  so an early lock cuts exactly the trades that pay for everything.
- **VWAP and APA want no trailing at all.**
- **HTFFVGFlip stays negative under every exit method** — its problem is not the
  exit.

---

## Partials versus the ratchet

You worried partials shrink the capital that is working. **That is correct, and
both the arithmetic and the measurement agree.**

Half off at 1R with the rest to 5R blends to (0.5×1)+(0.5×5) = **3R instead of
5R** — you permanently trade 2R of upside for a higher hit rate. Whether that
pays depends only on how often price reaches the far target, and in your book
**only 7.1% of trades ever reach 5R**. You would be paying that premium on every
trade to protect a runner that rarely arrives.

Measured directly (report 07): partials cut DriftJumpAlpha from **+0.227 to
+0.102 R**.

**The ratchet delivers what partials promise without the cost** — partials buy
safety by permanently cutting size; the ratchet buys it by moving the stop, with
the full position still on if the move continues. The win-rate rise you would
want from partials (22% → 41%) is already delivered by the ladder.

**Recommendation: no partials on any symbol.**

---

## Regime check — why the effect is modest

Trend persistence (variance-ratio Hurst) on M15, where 0.50 is a coin flip:

| Crash 1000 | 0.513 | BTCUSD | 0.508 | Volatility 75 | 0.511 |
|---|---|---|---|---|---|
| **Crash 300** | 0.504 | **Germany 40** | 0.497 | **GBPUSD** | 0.495 |
| **EURUSD** | 0.492 | **US Tech 100** | 0.488 | **XAGUSD** | 0.484 |
| **XAUUSD** | 0.483 | **US SP 500** | 0.476 | | |

**Nothing you trade trends.** Everything sits between 0.476 and 0.513. The
literature holds that trailing only beats a fixed target above ~0.55, and that is
exactly why the expectancy gain here is +0.0026 rather than something dramatic.

What the ratchet does instead is cut the left tail: it makes the 3R-to-−1R round
trip impossible. That is a drawdown effect, not a return effect — which is
precisely the shape of the measured result.

---

## Recommendation

1. **Implement the ladder as `(trigger_R, stop_R)` configuration**, per strategy,
   alongside `tp_volume_pcts`. Rigid, inspectable, no ATR.
2. **Express the buffer in R, not pips.** Your "10 pips above entry" means
   something different on Gold than on Crash 1000; `+0.05R` is the same decision
   everywhere.
3. **Default to `tight_ladder`**, with per-strategy overrides: `loose_ladder` for
   DriftJumpAlpha, no trailing for VWAP and APA.
4. **Keep the 1:5 target. No partials. Never let the stop move backwards.**
5. **Adopt it for the drawdown, not the return.** Expect roughly a third less
   drawdown and a much higher hit rate, for about the same money.

## Caveats

- One 8-month window, one broker, M15 bars.
- Intrabar sequence unknown; a bar touching both stop and target scored as a
  stop, so real fills should be slightly better.
- The six ladders were chosen by hand, not optimised. A proper sweep of
  trigger/destination pairs is the obvious next step.
- The harness does not reproduce session-end, daily-cap or time-limit exits.
