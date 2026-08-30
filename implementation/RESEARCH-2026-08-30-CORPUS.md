# Strategy Optimisation Research — the January–August 2026 corpus

**Date:** 2026-08-30
**Basis:** 19 runs · 1,326 trade groups · 3,888 legs · 6 strategies · 8 instruments · 2026-01-02 → 2026-08-20
**Source data:** `debug/*/*.json` (the runs you executed)
**Reproduce:** `python3 implementation/evidence/analyse_corpus.py` → `implementation/evidence/full_analysis.txt`

All R-multiples here are **recomputed from price against entry-time risk**
(`|entry − initial_stop_loss|`). The stored `pnl_r` / `expectancy_r` fields are not used —
they divide by the mutated stop, so a break-even'd trade reads ~19R instead of ~1.7R.
Pip size is derived empirically per symbol from each leg's recorded price extreme versus
its `mfe_pips`, so nothing here needs an MT5 connection to verify.

This document **supersedes `strategy_optimization_research.md`** (2026-08-21). That document was
written against a different, smaller corpus and three of its headline conclusions do not survive
this one. Where they differ, this document is the newer measurement and says so explicitly.

---

## 0. The one-paragraph answer

The entries are not the problem and the exits are only half of it. Measured on price alone,
three of the six strategies are **positive**: NY Open Retest +0.103R, HTF FVG Flip +0.082R,
CRT +0.023R. Measured in cash, **all six are negative**. The gap is transaction cost, and it is
not small: the modelled round trip consumes **0.056R to 0.187R per trade** depending on strategy,
and up to **0.37R** on individual instrument pairings. That is the entire edge and more. The
second problem is that the exit ladder is calibrated for a market that is not there — TP1 sits at
1.5R while the median trade's best excursion is 0.71R, so 50% of every position is parked on a
target that fills 22% of the time and the other 50% on targets that fill 7% and 2%. Fix the cost
exposure first (stop-distance floor, instrument disposition), the exit second (early-armed trailing
instead of the three-tier ladder), and only then look at entries.

---

## 1. Where the corpus actually sits

| Strategy | Groups | Win% | Expectancy (price) | Expectancy (cash) | Cash P&L | Median MFE |
|---|---:|---:|---:|---:|---:|---:|
| NY Open Retest | 60 | 46.7% | **+0.103R** | −0.091R | −$1,365 | 1.00R |
| HTF FVG Flip | 48 | 39.6% | **+0.082R** | −0.051R | −$615 | 1.09R |
| CRT | 49 | 49.0% | **+0.023R** | −0.104R | −$1,269 | 1.01R |
| VWAP | 1,034 | 46.5% | −0.027R | −0.075R | −$19,500 | 0.65R |
| Bias IFVG | 83 | 38.6% | −0.079R | −0.107R | −$2,213 | 0.93R |
| APA | 52 | 44.2% | −0.085R | −0.145R | −$1,882 | 0.80R |
| **Pooled** | **1,326** | **45.8%** | **−0.021R** | **−0.081R** | **−$26,844** | **0.71R** |

*Expectancy (cash) = realised P&L ÷ 1% of $25,000, i.e. the same trade measured in the money it
actually made. The gap between the two columns is all-in realised drag.*

Two things to read off this table before anything else:

1. **Every strategy loses more in cash than in price.** The drag ranges from −0.028R (Bias IFVG)
   to −0.194R (NY Open Retest). Nothing about the entries explains that; it is friction.
2. **VWAP is 78% of the corpus.** Any pooled number is a VWAP number. Every conclusion below is
   given per strategy for that reason.

Monthly expectancy is stable and near zero throughout — +0.082R in January, −0.177R in February,
−0.045, −0.100, −0.001, +0.013, −0.023, +0.117R in August. This is not one bad window; it is a
system sitting on the line all year.

---

## 2. Friction is the binding constraint — highest-value finding in this document

`friction_R` = (spread + 2 × modelled slippage) ÷ the median stop distance the strategy actually
uses on that instrument. It is the fraction of one R handed to the broker on every round trip
before the market has moved at all.

| Strategy × instrument | n | Spread | Slippage | Round trip | Median stop | **friction_R** | Price exp. | Cash exp. |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| CRT × BTCUSD | 13 | 22.36 | 23.27 | 68.89 | 187.56 | **0.367** | +0.406 | +0.004 |
| APA × GBPJPY | 8 | 10.00 | 0.70 | 0.114 | 0.362 | **0.315** | +0.117 | −0.470 |
| APA × SPX500 | 5 | 7.30 | 3.00 | 1.33 | 5.26 | **0.253** | +0.503 | +0.453 |
| CRT × XAUUSD | 17 | 13.90 | 1.00 | 1.59 | 7.56 | **0.210** | +0.459 | +0.270 |
| APA × XAGUSD | 16 | 96.00 | 10.00 | 0.116 | 0.605 | **0.192** | +0.028 | −0.206 |
| NY Retest × XAUUSD | 60 | 13.90 | 1.00 | 1.59 | 9.53 | **0.167** | +0.103 | −0.091 |
| VWAP × XAGUSD | 203 | 96.00 | 10.00 | 0.116 | 0.923 | **0.126** | −0.001 | −0.112 |
| VWAP × EURUSD | 156 | 1.20 | 0.40 | 0.0002 | 0.00163 | **0.123** | −0.046 | −0.191 |
| VWAP × XAUUSD | 222 | 13.90 | 1.00 | 1.59 | 25.53 | **0.062** | −0.039 | −0.064 |
| Bias IFVG × XAUUSD | 83 | 13.90 | 1.00 | 1.59 | 28.49 | **0.056** | −0.079 | −0.107 |

Read the CRT × BTCUSD row. That pairing has the best price expectancy in the entire corpus
(+0.406R) and made **$12**. Thirty-seven percent of every R went to the round trip.

Per strategy, weighted:

| Strategy | Spread portion | Modelled slippage | **Total friction** |
|---|---:|---:|---:|
| CRT | 0.148R | 0.039R | **0.187R** |
| NY Open Retest | 0.146R | 0.021R | **0.167R** |
| APA | 0.122R | 0.038R | **0.160R** |
| VWAP | 0.063R | 0.041R | **0.104R** |
| HTF FVG Flip | 0.054R | 0.008R | **0.061R** |
| Bias IFVG | 0.049R | 0.007R | **0.056R** |

### 2.1 The stop is too tight for the instrument, not the other way round

Friction is a ratio. It falls when the stop widens. Here is the same data expressed as "how many
round trips does the current stop cover":

| Symbol | Round trip | Median stop in use | Cover | Stop needed for friction < 10% of R |
|---|---:|---:|---:|---:|
| GBPJPY | 0.114 | 0.362 | **3.2×** | 1.140 |
| SPX500 | 1.33 | 5.26 | **4.0×** | 13.30 |
| XAGUSD | 0.116 | 0.874 | **7.5×** | 1.160 |
| BTCUSD | 68.89 | 549.61 | **8.0×** | 688.93 |
| EURUSD | 0.00020 | 0.00163 | **8.2×** | 0.00200 |
| XRPUSD | 0.00182 | 0.01909 | 10.5× | 0.01825 |
| XAUUSD | 1.59 | 22.27 | 14.0× | 15.90 |
| NDX100 | 1.91 | 46.17 | 24.1× | 19.12 |

The existing guard for this is `min_stop_spread_multiple`, default **2.0**. Two times the spread
means the broker takes half of every R. That default is not a safety floor; it is a licence to
trade instruments that cannot pay for themselves.

**Measured effect of raising it.** Rejecting every signal whose stop is below N × round-trip cost,
on the corpus as it stands:

| N | Trades kept | Price expectancy | Cash P&L | Cash / trade |
|---:|---:|---:|---:|---:|
| 0 (today) | 1,326 (100%) | −0.021R | −$26,844 | −$20.24 |
| 6× | 1,013 (76%) | −0.015R | −$15,024 | −$14.83 |
| 8× | 814 (61%) | −0.017R | −$11,194 | −$13.75 |
| **10×** | **649 (49%)** | **+0.019R** | **−$3,504** | **−$5.40** |
| **15×** | **363 (27%)** | **+0.042R** | **+$415** | **+$1.14** |
| 20× | 195 (15%) | −0.033R | −$3,187 | −$16.34 |

A single threshold, changing no strategy logic, moves the book from −$26,844 to break-even. It
turns over at 20× (too few trades, and the survivors are a different population), so 10–15× is the
band. **This is the highest-value single change available and it is a one-line risk gate.**

### 2.2 How much of this friction is real and how much is a modelling choice

Worth separating, because they have different remedies.

- **Spread is real** and comes from the live terminal. But two values need verifying against
  `symbol_info` before being trusted: XAGUSD at 96 pips (= $0.096 on silver, roughly 2–4× a
  typical retail CFD) and XAUUSD at 13.9 pips (= $1.39 on gold, roughly 4–9× typical). If either
  is a points-read-as-pips error the friction on those two symbols is overstated — and they carry
  524 of the 1,326 groups.
- **Slippage is modelled, never observed** (`broker_costs.py` says so explicitly). BTCUSD's
  23.27 pips = $23.27 per side is a percent-of-price heuristic — defect E9 in the register, still
  open. On CRT × BTCUSD that assumption alone is 0.12R of the 0.367R.

So the friction number is directionally certain and precisely uncertain. The stop-distance floor
in §2.1 is robust to that uncertainty; it helps under every plausible cost value.

### 2.3 One corpus caveat, stated plainly

`debug/apa/*.json` was produced on 2026-08-21, **before** the swap-units fix
(`MAX_ABS_DAILY_SWAP_PER_LOT`, added 2026-08-26). Its GBPJPY cost model carries
`swap_short = −4,427.8/lot/day`, which is not a real number — real GBPJPY financing is single- to
low-double-digit dollars. **APA × GBPJPY's cash figures are therefore worthless** (its price
figures are fine: +0.117R). Every other run in the corpus is from 2026-08-26. I have not excluded
the row above, but do not act on its −$941.

---

## 3. The exit ladder is calibrated for excursions that do not happen

Share of trade groups whose best excursion ever reached each R level:

| Strategy | n | ≥0.5R | ≥1R | ≥1.5R | ≥2R | ≥3R | ≥5R |
|---|---:|---:|---:|---:|---:|---:|---:|
| HTF FVG Flip | 48 | 70.8% | 54.2% | 37.5% | 29.2% | 16.7% | 6.2% |
| Bias IFVG | 83 | 65.1% | 48.2% | 36.1% | 19.3% | 10.8% | 2.4% |
| APA | 52 | 65.4% | 40.4% | 34.6% | 28.8% | 23.1% | 3.8% |
| NY Open Retest | 60 | 65.0% | 50.0% | 40.0% | 30.0% | 18.3% | 10.0% |
| CRT | 49 | 59.2% | 51.0% | 36.7% | 28.6% | 20.4% | 8.2% |
| VWAP | 1,034 | 59.4% | 32.1% | 17.9% | 10.5% | 4.0% | 0.8% |
| **All** | **1,326** | **60.6%** | **35.7%** | **22.1%** | **14.0%** | **6.9%** | **1.9%** |

Against that, the configuration every run used: **TP1 1.5R / TP2 3R / TP3 5R at 50/30/20**.

- TP1 at 1.5R fills on 22% of trades and carries **half the position**.
- TP2 at 3R fills on 6.9% and carries **30%**.
- TP3 at 5R fills on 1.9% and carries **20%**.

Half of every position is allocated to two targets that between them fill once in eleven trades.
The realised exit mix confirms it — TP1 319 legs, TP2 54, TP3 22, against 1,534 stop-outs.

### 3.1 Break-even at 1.5R is a scratch generator with nothing to protect

Among groups that got as far as B, the share that finished at or below zero:

| Reached | n | Finished ≤ 0R | Mean final R | Median further MFE |
|---:|---:|---:|---:|---:|
| 0.5R | 804 | 31.3% | +0.471 | 1.16R |
| 1.0R | 474 | 14.3% | +0.951 | 1.74R |
| **1.5R** | **293** | **4.4%** | +1.371 | 2.36R |
| 2.0R | 186 | 0.5% | +1.702 | 2.98R |

A break-even stop armed at 1.5R is insuring against a 4.4% event. What it actually did: **151
BE_SL legs, median MFE 1.69R, a third of them having already exceeded 2R, netting −$412.** It
converted trades with real unrealised profit into nothing, and the risk it was protecting against
had largely passed by the time it armed.

### 3.2 Trailing is the best exit in the book, and it is armed far too late

| Exit | Legs | Median MFE | Median realised | Capture | Cash |
|---|---:|---:|---:|---:|---:|
| TP3 | 22 | 3.62R | +4.20R | 97% | +$3,328 |
| TP2 | 54 | 2.95R | +2.71R | 93% | +$8,096 |
| TP1 | 319 | 1.41R | +1.25R | 92% | +$40,959 |
| **TRAIL_SL** | **420** | **1.83R** | **+1.06R** | **59%** | **+$16,863** |
| SESSION_END | 1,388 | 0.71R | +0.18R | 27% | +$5,190 |
| BE_SL | 151 | 1.69R | +0.07R | 5% | −$412 |
| SL | 1,534 | 0.44R | −1.00R | — | −$100,868 |

Trailing's 59% capture is **stable across every excursion bucket** — 48% at MFE ≤1R, 61% at
1–1.5R, 63% at 1.5–2R, 59% at 2–3R, 61% at 3–5R. That stability is what makes it usable as a
design constant. A fixed TP captures 92–97%, but only on the minority of trades that reach it; a
trail captures ~60% of whatever the trade actually gives.

---

## 4. Which risk:reward is better — the direct answer

Single fixed TP at k, stop at 1R, no break-even, no trail, net of that strategy's own friction.
Trades that were closed by the current logic before touching either boundary are censored and
counted as 0R (the middle of three assumptions; the full range is in the evidence file).

| Strategy | Friction | Best fixed TP | Net at best | As traded today |
|---|---:|---:|---:|---:|
| CRT | 0.187R | **3.0R** | +0.119R | −0.164R |
| HTF FVG Flip | 0.061R | **0.75R** | +0.032R | +0.021R |
| APA | 0.160R | **2.5R** | +0.157R | −0.245R |
| Bias IFVG | 0.056R | **0.75R** | −0.011R | −0.134R |
| NY Open Retest | 0.167R | 1.5R | −0.067R | −0.064R |
| VWAP | 0.104R | 0.5R | −0.054R | −0.131R |

There is no single best R:R across the book, and that is the finding. **CRT and APA want a far
target (2.5–3R); HTF FVG Flip and Bias IFVG want a near one (0.75R); VWAP wants 0.5R.** A global
1.5/3/5 grid is wrong for all six simultaneously. This is the clearest argument in the corpus for
making the TP ladder per strategy rather than global.

But every one of those fixed-TP numbers is beaten by trailing.

### 4.1 Fixed TP vs. trailing vs. hybrid, all net of friction

| Configuration | APA | Bias IFVG | CRT | FVG Flip | NY Retest | VWAP | Pooled |
|---|---:|---:|---:|---:|---:|---:|---:|
| **As traded** (3TP 1.5/3/5, 50/30/20, BE@1.5R) | −0.245 | −0.134 | −0.164 | +0.021 | −0.064 | −0.131 | −0.123 |
| Single TP 1.5R | −0.025 | −0.116 | +0.078 | −0.082 | −0.067 | −0.177 | −0.143 |
| Single TP 1.0R | −0.122 | −0.080 | +0.099 | +0.022 | −0.100 | −0.103 | −0.084 |
| Single TP 0.75R | −0.030 | −0.011 | +0.058 | +0.032 | −0.142 | −0.062 | −0.047 |
| Trail, arm 1.0R, 50% capture | +0.109 | −0.020 | +0.355 | +0.189 | +0.139 | −0.109 | −0.050 |
| **Trail, arm 0.75R, 50% capture** | **+0.250** | **+0.136** | **+0.424** | **+0.310** | **+0.203** | −0.026 | **+0.040** |
| Trail, arm 0.75R, 60% capture | +0.389 | +0.254 | +0.583 | +0.459 | +0.357 | +0.047 | +0.128 |
| Hybrid: TP1 0.75R @40%, trail the rest | +0.222 | +0.148 | +0.373 | +0.289 | +0.157 | +0.003 | +0.058 |

### 4.2 Honest treatment of the capture assumption

The 59% capture figure is measured on legs that **exited via the trail** — a selected sample. I
checked it against every trade that reached a given level, and the model over-predicts:

| Reached | n | Mean MFE | Model at 60% | Actually realised |
|---:|---:|---:|---:|---:|
| 0.5R | 804 | 1.59R | +0.957R | +0.471R |
| 1.0R | 474 | 2.19R | +1.314R | +0.951R |
| 1.5R | 293 | 2.79R | +1.671R | +1.371R |
| 2.0R | 186 | 3.40R | +2.039R | +1.702R |

Realised capture across *all* trades reaching a level is **30% at 0.5R rising to 50% at 2R**,
under today's exits — which include the BE scratches and the 1.5R first target. A pure trail
removes those, so the truth sits between. **The conservative case is 40% capture**, and even
there:

| Capture | Arm at | APA | Bias IFVG | CRT | FVG Flip | NY Retest | VWAP | Pooled |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.40 | 0.50R | +0.189 | +0.082 | +0.269 | +0.266 | +0.136 | −0.019 | **+0.029** |
| 0.50 | 0.50R | +0.334 | +0.204 | +0.429 | +0.421 | +0.295 | +0.063 | **+0.126** |
| 0.60 | 0.50R | +0.479 | +0.325 | +0.589 | +0.575 | +0.455 | +0.146 | **+0.222** |
| 0.60 | 1.50R | +0.174 | −0.096 | +0.341 | +0.080 | +0.149 | −0.172 | −0.105 |
| *(today)* | — | −0.245 | −0.134 | −0.164 | +0.021 | −0.064 | −0.131 | −0.123 |

**At the pessimistic 40% capture, early-armed trailing still beats the current ladder by +0.15R
per trade pooled, and by +0.2R to +0.43R on the five low-frequency strategies.** The arming level
matters more than the capture ratio: arming at 1.5R (today) destroys the benefit at every capture
value in the table.

---

## 5. Recommendation — what I propose we implement

Ordered by expected value per unit of risk taken. Items 1–3 change no strategy logic.

**1. Stop-distance floor tied to real cost.** Replace `min_stop_spread_multiple = 2.0` with a
round-trip-cost multiple, default **10×**, expressed as
`min_stop >= N × (spread + 2 × slippage)`. Reject below it with a named reason so it shows in the
funnel. Measured: −$26,844 → −$3,504 at 10×, +$415 at 15×, on unchanged strategy logic.

**2. Retire the three-tier ladder; trail from 0.75R.** Set `tp_count = 1` with
`trail_method_tp1 = ATR_TRAIL`, `trail_mode = RR`, `trail_trigger_rr = 0.75`,
`trail_require_be_first = False`. The plumbing for all of this already exists — Phase 5 shipped
`trail_method_tp1`, `trail_mode`, `trail_trigger_rr` and `tp_volume_pcts`; nothing new is needed
to test it.

**3. Turn break-even off, or move it above 2R.** `be_mode = NONE`. It is protecting a 4.4% event
at 1.5R and cost −$412 on trades holding a median 1.69R. If you want it kept as insurance, arm it
at 2.5R, where the "gave it all back" rate is 0.5% — but then it is doing nothing at all, which is
the more honest reading.

**4. Per-strategy TP/trail parameters, not a global grid.** §4 shows the six strategies want
targets from 0.5R to 3R. The `InstrumentSlot` work (Stage 11) already carries
`strategy_params_override`; the exit fields need to join it.

**5. Instrument disposition — drop four pairings, keep three.**
   - **Drop:** CRT × NDX100 (n=19, −0.631R, −$2,428) and APA × NDX100 (n=14, −0.749R, −$599).
     Both are the worst rows in the corpus by a wide margin and neither is close to variance.
   - **Keep and prioritise:** CRT × XAUUSD (+0.459R), CRT × BTCUSD (+0.406R), APA × SPX500
     (+0.503R, but n=5 — provisional).
   - **VWAP × EURUSD**: 156 trades, −$7,440, friction 0.123R against a median stop of 1.6 pips.
     EURUSD cannot pay a 1.2-pip spread out of a 16-pip stop. Either widen the stop past 20 pips
     or stop trading VWAP on EURUSD.

**6. VWAP needs a coherent horizon, not a tuned target.** It is force-flat at 61 M5 bars (5.1
hours) — 45.7% of its legs exit that way — while its first target sits at 1.5R and its median
5-hour excursion is 0.69R. 389 of those 1,388 force-closed legs had already exceeded 1.0R when the
clock ran out. Either extend the horizon or bring the target to 0.5–0.75R. Doing neither is what
produced 1,034 trades at −0.027R.

**7. Re-run before trusting any of this.** Every number above is derived from excursion data
inside runs that used one exit configuration. The censoring is material — 48–64% of VWAP's grid at
high targets. Recommendations 1–3 are cheap to test and the corpus cannot decide between them at
the margin. **The single highest-value experiment is one re-run with `tp_count=1`, trail from
0.75R, no break-even, and the 10× stop floor on.**

---

## 6. Findings that are not about exits

**6.1 `min_rr` currently rejects every signal you would generate.** `risk/engine.py:522` gates on
`blended_rr` (volume-weighted across TP legs) since task 5.7. Your saved config — TP 1.5/3/5 at
50/30/20 with `min_rr = 3` — blends to **2.41–2.65**, below 3. Every signal is refused before
sizing. The corpus predates this change, which is why it has trades. Fix as part of
recommendation 2 (a single TP makes `blended_rr = tp1_rr`, so `min_rr` must come down with it).

**6.2 The confluence score is a constant in three of six engines.** CRT emits 90 on every trade,
NY Open Retest 92, VWAP 80 — zero variance across 1,143 groups. The score cannot be predictive of
anything because it was never computed. Where it does vary, it is inconsistent: APA's top bucket
(93–100) is its *worst* (−1.099R on n=8), while HTF FVG Flip's top bucket is its best (+0.249R vs
−0.369R). Confluence-scaled risk sizing is currently scaling by a constant on half the book.

**6.3 A systematic long/short asymmetry.** SELLs beat BUYs on five of six strategies: APA
−0.423 vs +0.279, NY Retest −0.200 vs +0.334, CRT +0.174 vs −0.197 (reversed), VWAP −0.086 vs
+0.031 on n=1,034. The VWAP figure is the only one with the sample size to be worth acting on, and
0.117R is a large spread. Worth a directional-bias diagnostic before it is worth a rule.

**6.4 Session concentration reproduces independently.** Entries cluster in stored-clock hours
13:00–18:00. Best is 16:00 (48.1% win, +0.005R); worst is 13:00 (38.3% win, −0.031R). If the
stored feed is broker time at UTC+3, those are 09:00 ET and 06:00 ET respectively — exactly the
best and worst hours the previous research doc identified on a different corpus. That the same
two hours come out of two independent datasets is the strongest single piece of session evidence
we have.

---

## 7. Two earlier conclusions this corpus overturns

**7.1 "Break-even must move beyond the median MFE of 2.0–2.3R"** — `strategy_optimization_research.md`
§1.2. That corpus measured median MFE of 2.31R on BE_SL legs and 2.02R on trailed legs. This one
measures **0.71R across all groups** and 1.69R on BE legs. The advice to move `be_trigger_rr` to
2.5R would, on this data, arm break-even above a level 91% of trades never reach — it would be
identical to switching it off. The conclusion (BE at 1.5R is harmful) survives; the prescription
(move it to 2.5R) is superseded by "turn it off".

**7.2 "APA's MFE is ~0R; the entries are wrong immediately"** — `PHASE-14-STRATEGY-FACTORY-PLAN.md`
Part B3. That was measured on **5 trades in one window**. Across all 52 APA groups: median MFE
**0.79R**, mean 1.49R, best 9.33R, 65% reached 0.5R, 35% reached 1.5R. The claim does not hold for
APA, and B3.5's corollary — that the exit fixes are latent and will show no effect — is wrong: the
exit change is worth +0.43R per APA trade in §4.1.

The *mechanism* B3 described is real, but it belongs to different strategies. Share of stop-outs
that died within 3 bars having never travelled 0.1R:

| Strategy | Stopped ≤3 bars | …and MFE < 0.1R | Median bars to stop |
|---|---:|---:|---:|
| **CRT** | **56.0%** | **44.0%** | **3** |
| **NY Open Retest** | **37.5%** | **18.8%** | **6** |
| APA | 20.9% | 14.0% | 13 |
| VWAP | 18.0% | 12.4% | 11 |
| HTF FVG Flip | 7.1% | 3.5% | 39 |
| Bias IFVG | 2.1% | 0.0% | 24 |

**CRT is the strategy entering one bar too early**, not APA — 44% of its losses never tick in its
favour at all. B3's proposed remedy (require a rejection candle rather than a zone touch) should be
applied to CRT's C2 trigger and NY Open Retest's retest, and reconsidered for APA on this evidence.

---

## 8. What I am not claiming

- **No edge is established here.** §4.1's positive numbers are what the exits would have returned
  *given the excursions these entries produced*. They are a re-allocation of value the current
  configuration throws away, not new alpha.
- **The censoring is real.** Between 4% and 64% of the grid is unobserved depending on strategy and
  target, because the trade was closed before touching either boundary. Every table states its
  censored share; the evidence file gives all three assumptions.
- **Five of six strategies have n < 90.** APA n=52, CRT n=49, FVG Flip n=48, NY Retest n=60,
  Bias IFVG n=83. Instrument-level rows go down to n=2. Only VWAP (n=1,034) supports a confident
  per-instrument conclusion. The stop-distance floor in §2.1 is the one recommendation whose
  evidence base is the whole corpus rather than a slice of it.
