# Does the drift resume after a Boom/Crash spike? — measured, and the answer is no

**2026-09-27** · `scripts/run_spike_resumption.py` · `data/spike_resumption/`

---

## 0. The short version

The pattern you spotted on Boom 900 is real to look at and does not survive out of sample.

| Window | N | Expectancy | t | Return | Max DD |
|---|---|---|---|---|---|
| 2024-09 → 2026-01 — **where I chose the settings** | 122 | **+0.131R** | +0.81 | +7.8% | 8.1% |
| 2026-01 → today — held out | 65 | **+0.220R** | +0.96 | +7.1% | 5.1% |
| 2021-10 → 2024-09 — **never looked at** (Boom/Crash 1000) | 118 | **−0.098R** | −0.57 | −6.1% | 13.5% |

For a while it looked genuine. The held-out expectancy matched the selection window almost
exactly, and the shifted-entry control was negative at every shift in both recent windows —
the edge died the moment entry was decoupled from the spike, which is the signature you want
and the opposite of what the first parameter set did. Then the three years I had never tuned
on came back negative.

**§5 is the part that settles it.**

---

## 1. The rule, as tested

From your two Boom 900 M15 screenshots and your description. **Boom** spikes UP and drifts DOWN:

1. **Context** — an established downward drift.
2. **The spike** — ONE M15 bar breaking it with a sharp buy. The next bar must not also be a
   spike; *"the candlestick for spike should just be one."*
3. **Confirmation** — the next bar closes bearish. *"When it has completed one sell
   candlestick, then you now enter your position for sell."*
4. **Entry** — SELL, filled at the following bar's open (the app's own fill).
5. **Target** — back past the spike bar's low; *"it must come below the last buy spike."*
6. **Hold** — you observed about 3 bars, sometimes 4 or 5.

**Crash** is the exact mirror. Tested on all eight: Boom and Crash 300, 500, 900 and 1000.

### Why this needed its own strategy rather than a tweak

`BoomDriftJump_v1`'s Setup A enters on a pullback to the fast EMA during an active drift, and
carries this line:

> `# Do not sell into a fresh up-spike; the mirror of DJA's block.`

**The shipped strategy explicitly refuses the setup you described.** Yours uses the spike as the
trigger; that one treats a fresh spike as a reason to stand aside and takes EMA pullbacks
anywhere in the drift instead. That is why its entries feel arbitrary relative to where the last
spike was — they are, by design. So this was measured from scratch, not as a parameter.

### The fill model, which is the whole ballgame

A bar backtest fills a stop AT the stop. On Boom and Crash every spike happens INSIDE a bar by
construction, so a stop on the spike side is taken out by a jump and fills far past it. Measured
from ticks, that is worth about 0.3R per stopped trade, and ignoring it had random Boom shorts
booking **+0.76R each against −0.03R on real ticks.** This setup sits right next to a spike,
where the error is largest, so every number here is priced with the measured λ.

**A bug found doing this:** Boom 900 and Crash 900 were missing from `SPIKE_FILLS` entirely, so
`get_spike_fill` returned `None` and the app priced their spike-side stops as if they filled at
the stop. They are now in the table at the family's most adverse measured value (0.80, the
1000s'), explicitly marked **assumed, not measured** — the 900s were not in the tick study.

---

## 2. Full detail, the chosen settings

3×ATR spike · close under the slow EMA for 20 straight bars · 1 confirming bar · 1×ATR stop ·
target back past the spike · max hold 5 bars · $10,000 at 0.5% risk · λ fills on.

### Selection window, 2024-09 → 2026-01, all eight instruments

| | |
|---|---|
| $10,000 → **$10,780.21** | **+7.8%** |
| Max drawdown | $845.40 (8.1%) |
| Trades | 122 |
| Win rate | 50.0% · payoff 1.18 · PF 1.17 |
| Expectancy | +0.131R = **+$6.40/trade** |
| t / Sharpe | +0.81 / 1.23 |
| Avg win / loss | $86.33 / −$73.54 |
| Months green | 56% of 16 (streak 4W/3L) · weeks green 51% of 57 |
| Trade streaks | 5W / 5L |
| Exits | 55 stops, 52 max-hold, 15 targets |
| R per month | +1.04 |

### Held out, 2026-01 → today

| | |
|---|---|
| $10,000 → **$10,709.72** | **+7.1%** |
| Max drawdown | $529.78 (5.1%) |
| Trades | 65 |
| Win rate | 50.8% · payoff 1.27 · PF 1.30 |
| Expectancy | +0.220R = **+$10.92/trade** |
| t / Sharpe | +0.96 / 2.13 |
| Months green | 67% of 9 (streak 3W/1L) · weeks green 50% of 32 |
| Trade streaks | 4W / 4L |
| Exits | 30 stops, 25 max-hold, 10 targets |

### Never looked at, 2021-10 → 2024-09, Boom/Crash 1000

| | |
|---|---|
| $10,000 → **$9,391.47** | **−6.1%** |
| Max drawdown | $1,466.14 (13.5%) |
| Trades | 118 |
| Win rate | 45.8% · payoff 1.06 · PF 0.89 |
| Expectancy | **−0.098R = −$5.16/trade** |
| t / Sharpe | −0.57 / −0.86 |
| Months green | 44% of 34 (streak 4W/**5L**) · weeks green 46% of 85 |
| Trade streaks | 4W / **8L** |

---

## 3. The control, which is the part that nearly convinced me

The same spikes and the same trend test, with entry shifted N bars later. If the edge is in the
setup it should die; if it survives, the "edge" was a property of the drift and nothing to do
with the spike.

| Control | 2024-09 → 2026-01 | 2026-01 → today | 2021-10 → 2024-09 |
|---|---|---|---|
| entry +3 bars | +0.011R | **−0.183R** | −0.151R |
| entry +5 bars | −0.253R | **−0.182R** | −0.117R |
| entry +10 bars | +0.038R | **−0.182R** | −0.332R |

In the held-out window every control is around −0.18R against the real setup's +0.220R. That is
textbook: the timing relative to the spike is doing the work. It is also why I kept testing
rather than stopping at a good-looking number — a control this clean on 65 trades still only
says "not obviously noise", not "real".

---

## 4. The sweep, in full

Reported whole rather than as a best cell. One parameter at a time from the first baseline
(3×ATR spike, EMA trend, 50-bar look, 1 confirming bar, 2×ATR stop, spike target, 5-bar hold),
all eight instruments, 2024-09 → today.

| Variant | N | expR | win% | payoff | t | Sharpe | return | DD |
|---|---|---|---|---|---|---|---|---|
| k_atr=2.0 | 2859 | −0.026 | 55.8% | 0.75 | −1.43 | −0.87 | −33.0% | 37.6% |
| **k_atr=3.0** | 554 | +0.020 | 55.8% | 0.83 | +0.48 | 0.37 | +5.1% | 9.2% |
| k_atr=5.0 | — | too few trades | | | | | | |
| trend=ema | 554 | +0.020 | 55.8% | 0.83 | +0.48 | 0.37 | +5.1% | 9.2% |
| trend=ret | 393 | +0.013 | 55.7% | 0.82 | +0.24 | 0.22 | +2.0% | 6.5% |
| **trend=below** | 168 | **+0.097** | 60.1% | 0.83 | +1.25 | 1.64 | +8.2% | 3.9% |
| trend_look=20 | 451 | −0.005 | 54.1% | 0.84 | −0.10 | −0.09 | −1.6% | 8.6% |
| trend_look=50 | 554 | +0.020 | 55.8% | 0.83 | +0.48 | 0.37 | +5.1% | 9.2% |
| trend_look=100 | 530 | −0.024 | 53.4% | 0.83 | −0.56 | −0.46 | −6.7% | 11.1% |
| confirm=1 | 554 | +0.020 | 55.8% | 0.83 | +0.48 | 0.37 | +5.1% | 9.2% |
| confirm=2 | 345 | +0.016 | 59.1% | 0.72 | +0.31 | 0.28 | +2.4% | 8.3% |
| **stop=1×ATR** | 554 | **+0.042** | 47.5% | 1.17 | +0.55 | 0.44 | +9.8% | 20.5% |
| stop=2×ATR | 554 | +0.020 | 55.8% | 0.83 | +0.48 | 0.37 | +5.1% | 9.2% |
| stop=3×ATR | 554 | +0.021 | 57.4% | 0.80 | +0.73 | 0.57 | +5.8% | 7.0% |
| target=spike | 554 | +0.020 | 55.8% | 0.83 | +0.48 | 0.37 | +5.1% | 9.2% |
| target=rr | 554 | +0.026 | 55.6% | 0.85 | +0.60 | 0.47 | +6.7% | 9.8% |
| target_rr 1/2/3/5/8 | 554 | +0.014 … +0.026 | | | | | | |
| max_hold=3 | 555 | +0.011 | 54.6% | 0.86 | +0.31 | 0.25 | +2.6% | 10.3% |
| max_hold=4 | 555 | +0.018 | 57.5% | 0.77 | +0.46 | 0.36 | +4.5% | 8.9% |
| max_hold=5 | 554 | +0.020 | 55.8% | 0.83 | +0.48 | 0.37 | +5.1% | 9.2% |
| max_hold=10 | 553 | −0.048 | 49.9% | 0.92 | −0.96 | −0.77 | −13.4% | 20.8% |

Two things to notice. **The largest-sample cell is the worst**: `k_atr=2.0` gives 2,859 trades at
−0.026R and −33%. And nothing in the table clears t = 1.5. The combination that looked good —
`trend=below` with a 1×ATR stop — was assembled from the two best single cells, which is exactly
the move a control exists to check.

### A flaw in my own sweep, fixed

The first version swept `target_rr` at 2/3/5 and got three **identical** rows. That was not
"this parameter does not matter" — it was "this parameter was never read", because with
`target="spike"` the R multiple is only a fallback for when the spike level has already been
passed. Re-swept properly against `target="rr"`: 1.0 gives +0.014R, and everything from 3.0
upward is +0.026R, i.e. the target stops binding.

---

## 5. What settles it

If Deriv had changed how these instruments are generated around 2024 — which would be a real
reason for a pattern to exist only recently — then the **baseline** settings would have flipped
sign too. They did not.

Boom/Crash 1000, baseline settings, same two windows:

| Window | N | Expectancy | t | Return |
|---|---|---|---|---|
| 2021-10 → 2024-09 | **411** | **+0.002R** | +0.03 | −0.2% |
| 2024-09 → today | **314** | **+0.036R** | +0.65 | +5.4% |

Flat in both, on 725 trades. The instruments did not change. Only the *specific combination I
picked by reading the sweep* is strongly positive in the recent window and negative before it.

That is the fingerprint of parameter selection, not of an edge.

The cells that made it look good are also the small ones: Boom 900 at +0.525R on **24 trades**
in the selection window, Crash 1000 at +1.084R on **21 trades** in the held-out one. Twenty-trade
samples will produce numbers like that regularly whether or not anything is there.

---

## 6. Verdict, and the two things that could change it

**Not shipped.** It does not clear its own control on the longest history available, and the
positive result lives entirely in the window its parameters were chosen on.

Two places where I may have encoded your description wrong. You watched this on a chart; I built
it from words, and either of these is a different strategy rather than a different setting:

1. **What counts as a spike.** I used the BODY — close minus open ≥ k × ATR — on the reasoning
   that a Boom spike ends the bar far above where it started, while a wide range with a small
   body is ordinary noise. If what your eye picks out is better described by the bar's RANGE, or
   by "enormous relative to its neighbours", that is a different filter.
2. **When the entry happens.** You said *"whether it has sold mid, or while it has completed at
   least one candlestick in sell"*. I took the conservative reading: enter at the close of the
   first completed bearish bar, filled at the next bar's open. Entering mid-candle on a
   15-minute bar in a fast drift is a materially different price and a materially different
   trade.

Say which and it gets re-run against the same three windows and the same control.

### What the 300s and 500s said

They were added to the cache for this and barely trade under the rule: they spike too often for a
long clean drift to form, and the strict trend filter almost never holds. Adding four instruments
took the selection window from 110 trades to 122. That is a finding about where the pattern could
live at all — only on the slower-spiking members of the family — and it also means the whole
result rests on the 900s and 1000s however many symbols are in the list.

---

## 7. Reproduction

```bash
py -3.12 scripts/fetch_zone_bars.py --terminal deriv --start 2024-08-30 --markets "Boom 300 Index" "Boom 500 Index" "Crash 300 Index" "Crash 500 Index"
py -3.12 scripts/run_spike_resumption.py --sweep
py -3.12 scripts/run_spike_resumption.py --combo --placebo --since 2024-09-01 --until 2026-01-01
py -3.12 scripts/run_spike_resumption.py --combo --placebo --since 2026-01-01
py -3.12 scripts/run_spike_resumption.py --combo --placebo --markets "Boom 1000 Index" "Crash 1000 Index" --since 2021-10-01 --until 2024-09-01
```

`--terminal deriv` reads from the Deriv terminal by explicit path, so caching synthetic data
does not require changing `MT5_PATH` in `.env`.
