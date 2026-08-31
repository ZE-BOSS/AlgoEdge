# 07 — Final verdict & implementation decisions

**Stage G** · produced 2026-08-30 · evidence: 116 runs / 7,463 trades, plus live
MT5 measurement. Every claim below carries its sample size.

---

## The one-paragraph answer

The book loses $92,102 over eight months, and **the cause is not the strategies'
exit configuration** — it is that six of seven strategies have no measurable
directional edge, and that 64% of the loss is broker friction. Changing
risk-to-reward, trailing, or partial take-profits moves expectancy by less than
0.05R against a −0.180R deficit; none of it can work. Two changes do work, both
validated out-of-sample: **stop trading instruments where the spread is large
relative to the stop**, and **stop trading the strategy × symbol cells that lose**.
Together they take the book from −0.180R to **+0.215R per trade on 512
out-of-sample trades**.

---

## Answering the R:R question directly

You asked which risk-to-reward is better. **The measurement says the question
does not matter, and that is the more useful answer.**

A counterfactual sweep over every trade (a target at *k*R fills whenever the
trade's stop-aware MFE reached *k*R; otherwise the trade keeps its real outcome):

| TP | P(hit) | exp R | win rate | PF |
|---:|---:|---:|---:|---:|
| 1.0 R | 46.8% | −0.220 | 49.3% | 0.61 |
| 1.5 R | 31.7% | −0.195 | 43.5% | 0.68 |
| 2.0 R | 22.5% | −0.190 | 40.6% | 0.70 |
| 3.0 R | 13.8% | −0.176 | 38.1% | 0.72 |
| **4.0 R** | 9.8% | **−0.171** | 36.8% | 0.73 |
| 5.0 R *(current)* | 7.1% | −0.179 | 35.9% | 0.72 |

The entire curve spans **0.049R**. The best setting still loses money. This is
what a system with no entry edge looks like: the exit cannot rescue it, because
there is nothing to harvest.

Per strategy the optima scatter — APA 3.5R, VWAP 4.0R, CRT 1.5R, BiasIFVG 2.0R,
NYOpenRetest 0.75R, DriftJumpAlpha 4.0R — with gains of 0.02–0.08R each. That
scatter is noise fitting, not seven different truths.

**Partial take-profits make it worse.** Splitting the position (leg A at 1.0–2.5R,
runner at 5R) was tested across every fraction from 25% to 100%:

- **ALL trades:** no split beats no split. 50% at 1.5R gives −0.187R vs −0.179R.
- **DriftJumpAlpha:** splitting is actively harmful — +0.227R falls to **+0.102R**
  at 50%/1R. Its edge lives entirely in the tail.

This is worth stating carefully because it is counter-intuitive. **30.6% of all
trades reach +1R, get moved to break-even, and die there for +0.004R** — having
reached a *median 1.76R* first (p90 3.55R). That looks exactly like money left
on the table. But banking part of it costs more in capped winners than it
recovers in rescued break-evens, and the two cancel.

**Recommendations on exits:**

- **Keep TP at 1:5.** It is within 0.008R of the measured optimum and it is what
  the working strategy wants. Do not re-tune it per strategy.
- **Do not add partial take-profits.** Especially not on DriftJumpAlpha.
- **Keep break-even at 1R.** It is not the leak it appears to be — it converts
  2,285 would-be losers into scratches. Removing it looks tempting and is
  unmeasurable from this data (once BE closed the trade, the rest of the path is
  unobservable). If you want that answer, it needs a re-run with BE off, and
  that is the *only* exit experiment worth spending a sweep on.

---

## Ranked changes, by measured impact

### 1. Enforce a minimum stop-to-spread ratio — **+0.163R/trade**

`min_stop_spread_multiple` exists as a parameter and is **`null`** in all 116
runs. Nothing enforces it.

Stops fill at an average **−1.199R** instead of −1.000R. Removing all friction
(SL → exactly −1.00R, TP → exactly +5.00R) moves the book from −0.180R to
−0.064R: **64% of the total loss is friction.**

Dropping symbols whose observed stop overrun exceeds 0.15R:

| filter | trades | exp R | total R |
|---|---:|---:|---:|
| all | 7,463 | −0.180 | −1,343.5 |
| overrun ≤ 0.20R | 4,019 | −0.104 | −416.2 |
| **overrun ≤ 0.15R** | 2,417 | **−0.017** | **−42.2** |

Near break-even from cost control alone, changing no strategy logic. The
surviving symbols are Crash 300, Crash 1000, Volatility 75, US Tech 100,
US SP 500, XAUUSD.

*Caveat:* this drops 68% of the book, so it is a large intervention. Set the
threshold as a ratio (`min_stop_spread_multiple ≥ 10`) rather than a symbol
blacklist, so it adapts when spreads change.

### 2. Retire the losing strategy × symbol cells — **+0.279R/trade, out-of-sample**

Selecting cells on **January–April** and trading them **May–August**:

| selection rule | cells | OOS trades | OOS exp R | vs whole book |
|---|---:|---:|---:|---:|
| n ≥ 20 and positive | 15 | 1,008 | +0.009 | +0.187 |
| n ≥ 30 and positive | 11 | 891 | +0.036 | +0.213 |
| **n ≥ 50 and positive** | 7 | 714 | **+0.101** | **+0.279** |
| *(whole book OOS)* | 115 | 3,698 | −0.178 | — |

**The stricter the minimum sample, the better the out-of-sample result.** That is
the signature of a real effect — if this were overfitting, tighter selection
would fit the noise harder and degrade. It does the opposite.

Per-strategy expectancy is also stable across the split (drift ≤ 0.10R on all
seven), so this is not regime drift.

### 3. Combine 1 and 2 — **+0.215R/trade out-of-sample, PF 1.44**

| book | trades | exp R | OOS exp R |
|---|---:|---:|---:|
| whole book | 7,463 | −0.180 | −0.178 |
| low-friction symbols only | 2,417 | −0.017 | −0.025 |
| IS-selected cells | 1,388 | +0.161 | +0.101 |
| **selected cells ∧ low friction** | 1,012 | **+0.207** | **+0.215** |

Out-of-sample expectancy **exceeds** in-sample. On 512 OOS trades, PF 1.44.

### 4. Extend DriftJumpAlpha to Crash 500 — untested, high prior

DriftJumpAlpha is the only strategy with a real edge (+0.249R, PF 1.56,
P(1R) 55.9%, and it *improves* OOS to +0.284R). Crash 500 was in §0.2, was never
run, is live on the terminal, and sits between Crash 300 and Crash 1000 on every
measured property (arrival rate, depth, 23.5× depth-to-spread). See report 06.

### 5. Fix the confluence score, or stop shipping it as a control

Three of seven strategies emit a **constant** confluence score — CRT and
NYOpenRetest always 90–99, DriftJumpAlpha always 80–89. On those,
`reject_below_confluence` and `confluence_risk_tiers` cannot change any outcome.
On VWAP the score is **inverted** (corr −0.80: higher score, worse trades). It
works only on APA (+0.75) and HTFFVGFlip (+0.96, n=157).

---

## Per-strategy verdict

| strategy | n | exp R | verdict |
|---|---:|---:|---|
| **DriftJumpAlpha** | 696 | +0.249 | **Keep and extend.** Crash 300 + 1000, add Crash 500. No partials — the edge is in the tail. |
| **VWAP** | 2,186 | −0.159 | **Keep on Volatility 75 only** (+25.4R). Retire on crypto (−222.9R / 795 trades — the worst cell group in the book) and metals. Its confluence score is inverted; do not tier risk on it. |
| **BiasIFVG** | 1,108 | −0.193 | **Metals only** (+46.2R / 133). Retire elsewhere. Note it did not clear the n ≥ 50 OOS bar — treat as provisional. |
| **APA** | 1,236 | −0.177 | **Provisional.** BTCUSD/XRPUSD looked good in-sample and went negative out-of-sample. Highest-quality confluence score of the seven — worth keeping through the B7 re-run before deciding. |
| **HTFFVGFlip** | 183 | −0.085 | **Insufficient sample.** 183 trades across 19 symbols is ~10 per cell. No verdict is possible; also only 1 instrumented gate. |
| **NYOpenRetest** | 1,164 | −0.331 | **Retire.** Worst net P&L (−$29,704), gets *worse* OOS (−0.289 → −0.376), constant confluence score. |
| **CRT** | 890 | −0.376 | **Retire.** Worst expectancy, PF 0.50, gets worse OOS (−0.333 → −0.423), constant confluence score. |

## Recommended portfolio

**DriftJumpAlpha × {Crash 300, Crash 1000, +Crash 500}** and
**VWAP × Volatility 75.**

Measured on the three cells that have data (report 05): +198.9R, annualised
Sharpe 3.31, max drawdown 17.7R, return/drawdown 11.21. Pairwise correlations
−0.08 to +0.04. Adding VWAP × Vol 75 to DriftJumpAlpha alone raises Sharpe
3.01 → 3.31 *and* cuts drawdown 25.6R → 17.7R.

**The honest caveat:** this is a three-component book, and it is concentrated in
one broker's synthetic pricing model rather than in any market. If Deriv changes
that model, all of it goes at once. Diversifying *out* of synthetics is the real
open problem, and nothing in this programme has found a real-market strategy
that earns a place yet.

---

## What was tested and found NOT to work

Recorded so the same ground is not re-covered.

| tested | result |
|---|---|
| Re-tuning risk-to-reward (0.5R → 5R) | Entire curve spans 0.049R. No setting is profitable. |
| Partial take-profits (any fraction, any level) | Worse everywhere. −0.008R on the book; **−0.125R on DriftJumpAlpha**. |
| Per-strategy TP optimisation | Optima scatter 0.75R–4.0R with 0.02–0.08R gains. Noise. |
| Tightening stops on MAE evidence | Winners take only 0.23–0.43R median heat, but tighter stops *raise* the friction share of R — makes the dominant problem worse. |
| Session filtering | Asian-session edge is a symbol confound: +128R of +157R is DriftJumpAlpha. DJA is flat across all sessions (+0.235 to +0.279). |
| Direction bias | BUY −0.161 vs SELL −0.204. No usable asymmetry. |
| Confluence score as a universal control | Constant on 3 of 7 strategies; inverted on VWAP. |
| Crash sell-side: tick detection | 1 Hz feed, spikes complete in **one tick**. Impossible. |
| Crash sell-side: gap-percentile timing | Arrival is memoryless, CV 0.938–0.988. Zero timing information. |
| Crash sell-side: short-and-hold | Net move is ~1% of gross flow; signs disagree across sibling symbols. No edge. |
| Fundamentals as a standalone strategy | No point-in-time history exists anywhere; all four gates no-op in backtest; no strategy wires them. Unmeasurable, not unpromising. |

---

## Implementation order

1. **Fix B7** (enable `GateRecorder` on the backtest path) — one config change,
   unblocks all of Stage B.
2. **Fix B4/B5** (progress callbacks) — unblocks watching a sweep, which A.2
   needs and which is the user-visible complaint.
3. **Re-run the sweep** at 50,000 bars (confirmed available) with gate telemetry
   on, plus the trailing-on grid and the never-run cells (AUDUSD, Crash 500,
   Jump 100). One re-run satisfies A.3, B.1, B.2 and item 4 above.
4. **Then** implement `min_stop_spread_multiple` and the cell retirement.
5. **Then** T5.1 point-in-time fundamental storage, and start accumulating.

Nothing in step 4 should ship before step 3 confirms it on the larger sample.
