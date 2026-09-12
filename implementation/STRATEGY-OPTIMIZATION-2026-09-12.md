# Strategy optimization, parameters and returns — 12 Sep 2026

Follow-up to `STRATEGY-RESULTS-2026-09-11.md`. This round: re-ran everything on 26 markets, tested the risk levers you asked for ($10,000 and $350, 1% and 1.8%, compounding and pyramiding on/off, 10% and 20% daily caps), searched for repeatable patterns and confluences, and — most importantly — found why the app's Backtester disagreed with reality.

---

## 1. The three things that matter

**A. Trade this:** ORB_v1 on **GBPJPY**, London open, 60-minute range, 1:3 target, session close on, **1.8% risk, compounding ON, pyramiding OFF, 10% daily cap**.

**B. Expect 1.5–3% a month, not 30–100%.** The last 8 months returned +77% on $10,000, but that was the best of three test windows. All three:

| Window | Length | Return ($10k, 1.8%, compounding) | Max drawdown | Avg month | Months up |
|---|---|---|---|---|---|
| Last 8 months (2026-01-10 → 09-11) | 9 months | **+$7,696 (+77%)** | 12.4% | +5.9% | 8/9 |
| In-sample (2024-01 → 2026-01) | 24 months | +$6,451 (+64.5%) | 27.1% | +2.6% | 13/25 |
| Holdout (2022-09 → 2024-01) | 16 months | +$2,553 (+25.5%) | 25.1% | +1.8% | 10/17 |

Higher risk makes this worse, not better (§6). Doubling the account monthly is not available from this edge.

Those are **research-simulation** figures (bar data, real spread, honest fills). Two haircuts stand between them and your statement: tick replay says the edge itself is ~85% of the simulated R (§7), and the engine's own cost model plus its confluence risk scaling currently deliver less again — the same 152 trades come out at **+$2,753 (+27.5%)** with your broker's measured costs, or +$1,436 on the app's default cost inputs (§3b). Fix the inputs and the gap mostly closes; the sober planning number for the last-8-months window at $10k/1.8% is **+28% to +65%**, and 1.5–3% a month across all three windows.

**C. Your Backtester was understating results — two bugs fixed, one setting to decide.** Every backtest entered **one M15 bar late** (fixed: all 152 GBPJPY entries now land on the same bar as the research), and commission was invented at $7/lot on a broker that charges you **$0** (fixed: it now reads your own deal history). What is left is a deliberately conservative cost model (~0.13R per trade against real ticks) and a **risk setting that isn't fully deployed**: ORB's fixed confluence score of 70 lands in the 75% tier, so 1.8% is traded as ~1.35%. Neither is a bug; both are yours to set. §3 has the ladder, the cause of every dollar of difference, and the exact values to enter so a Backtester run matches reality.

---

## 2. How this was tested

- Settings chosen on **2024-01-22 → 2026-01-10** only, then run unchanged on the **last 8 months**, and again on **2022-09-05 → 2024-01-22** (a period the search never saw; indices have no MT5 data before 2024).
- Entry at the next bar's open after the signal bar closes. Ties (bar touches stop and target) count as losses. A bar that opens beyond the stop fills at that open. The bar's own spread is charged on every trade.
- Position sizing uses your broker's real lot step and minimum lot, and **refuses** a trade whose minimum lot would exceed the risk budget (same rule as the live sizer), so "profitable but unsizable" never sneaks into a return figure.
- Daily cap stops new entries once the day's losses reach it, exactly as the live circuit breaker does.
- Reproducibility check: GBPJPY's trades were re-filled on **real MT5 ticks** (§7).

**A warning about searching too hard.** Searching 2 sessions × 3 range lengths × 2 entry types × 12 exits = 144 combinations per market across 26 markets finds winners that are mostly luck. Its in-sample pick for GBPJPY (15-minute retest, 1:4) lost in 2022–23 and was flat over the last 8 months, and a portfolio of all 26 picks **loses money** out of sample. The configuration that survives is the one picked from a small 12-point grid and then confirmed on two other periods and on ticks. That is why the recommendation did not change.

---

## 3. Backtest-vs-live fixes found (this is the "no surprises live" part)

| # | Finding | Effect | Status |
|---|---|---|---|
| 1 | **Entries one bar late.** The route stamped each signal with the *next* bar's open, and the engine fills at the first bar after the stamp — so every entry was delayed 15 minutes. | +21.7R instead of +32.0R on 152 GBPJPY trades (+28% instead of +61% at $10k, 1.8%) | **Fixed** (`backtest.py`, `run_app_backtest.py`, pinned by `tests/test_entry_timing.py`) |
| 2 | **JPY pip size 10× too small.** With MT5 connected, GBPJPY/EURJPY/AUDJPY/CADJPY got a 0.001 pip instead of 0.01, shrinking every pip-based distance (break-even buffers, trailing, spread cost, minimum-stop floors). | wrong exit management on all JPY crosses, live and backtest | **Fixed** (`position_sizer.get_pip_size`, `tests/test_pip_size.py`) |
| 3 | **Your risk setting is scaled by confluence score.** `confluence_risk_tiers` defaults to (80 → 100%, 65 → 75%, 55 → 50%) and ORB_v1 stamps a fixed score of **70**, so the engine deploys **75%** of the configured risk: 1.8% becomes ~1.35%, about $120 per trade instead of $180. | dollars ~25% below what the risk number implies | Configuration, not a bug — §4 says what to set. A real ORB score is a TODO (§11). |
| 3b | **The margin ceiling was NOT the constraint here.** With MT5 connected the engine uses the broker's own margin figure and the 30% cap never bound — lifting it to 100% changed the result by $130. (The broken JPY-notional estimate in fix 7 only bites when MT5 is unavailable.) | none | No action |
| 4 | **$7/lot commission was invented.** Your real Deriv deal history shows **$0 commission** on CADJPY, AUDJPY, BTCUSD and the synthetics; GBPJPY had no history of its own, so it fell through to the FX-cross average. | ~0.03R per trade; the same run went from +$805 to +$1,436 once corrected | **Fixed** — a symbol with no history now uses the account's observed commission for that instrument kind (`broker_costs._account_wide_commission`, `tests/test_commission_from_history.py`) |
| 5 | **Spread is whatever the live spread was when the run started**, clamped into an asset-class band — 3.1 pips on GBPJPY against a real ~1.9. The bars carry their own spread and it is not used. | ~0.03R per trade | TODO (§11) |
| 6 | **Slippage modelled at 0.7 pips each way**; tick replay measures the entry cost, spread included, at ~0.02R. | ~0.04R per trade | TODO (§11) — calibrate against ticks |
| 7 | **Margin fallback treats JPY notional as dollars.** When MT5's own margin call isn't available it computed "$146,580 margin" for 0.69 lots and clamped to the minimum lot. | can silently shrink live positions to 0.01 lots | TODO (§11) |

Every historical saved backtest predates fixes 1, 2 and 4, so re-run anything you plan to rely on. Note that bugs 1 and 2 partly cancelled: the late entry cost money while the 10×-too-small pip made costs look cheap, which is why the old figure looked closer to the truth than it deserved.

### 3b. The cost ladder — the same 152 GBPJPY trades, priced four ways

| Version | Per trade | What it assumes |
|---|---|---|
| Research simulation | **+0.211R** | the bar's own recorded spread, nothing else |
| **Tick replay (ground truth)** | **+0.180R** | real bid/ask, real stop slippage, real entry fills |
| App engine, measured costs entered by hand | **≈ +0.15R** (+$2,753, PF 1.31, DD 7.9%) | 1.9-pip spread, $0 commission, 0.3-pip slippage, CONSERVATIVE stop fills |
| App engine, current defaults | **+0.049R** (+$1,436, PF 1.16) | 3.1-pip spread, 0.7-pip slippage each way, assumed 0.042R stop overshoot |

Two separate things shrink the app's dollars: the cost model above (~0.13R per trade against tick reality), and the **confluence risk tier**, which deployed ~$120 per trade instead of the $180 that 1.8% of $10,000 implies. On the same 152 trades the research figure of +$6,103 becomes +$2,753 in the engine — roughly two thirds from costs, one third from size.

Expect roughly **85% of the research R** in the market (the tick number). To get the research *dollars* you also have to deploy the risk you think you are deploying — see §4.

**To make a Backtester run match reality on GBPJPY**, set: spread 1.9 pips, commission $0, slippage 0.3 pips, stop fill model CONSERVATIVE, `max_margin_utilisation_pct` 100 (or accept ~1.3% effective risk at 1.8%).

---

## 4. The recommended configuration, in full

| Setting | Value | Why |
|---|---|---|
| Strategy | ORB_v1 | only setup profitable in all three windows and confirmed on ticks |
| Market | GBPJPY | see §8; the only market that both holds up and sizes on a small account |
| Session | London (08:00 UK, DST-aware) | measured best; New York is worse on this pair |
| Opening range | 60 minutes | 30m and 15m are worse out of sample |
| Entry | first M15 **close** beyond the range, next bar's open | retest entries look better in-sample and fail more often out of sample (§9) |
| Stop | far side of the range, floor 0.25 × ATR(14) | range-based, not fixed pips |
| Target | **1:3** of the realised stop | 1:1–1:4 all positive in-sample; 1:3 best and stable |
| Session close | ON (flatten at 16:30 UK) | without it BTCUSD/XAUUSD go flat; GBPJPY tolerates either |
| Trades per session | 1 (first break only) | |
| Risk per trade | 1.8% (1.0% while forward-testing) | |
| **Effective risk** | **1.35% of the 1.8% you set** | ORB's fixed confluence score of 70 lands in the 75% tier. To actually deploy 1.8%: add `(70, 100.0)` to `confluence_risk_tiers`, or set risk to 2.4%, or wait for a real ORB score (§11). Either way, know which one you are running. |
| Sizing | **compounding** (`sizing_basis: BALANCE`) | +77% vs +61% over the last 8 months |
| Pyramiding | **OFF** | adds trades, lowers expectancy 0.225R → 0.154R, raises drawdown 12.4% → 16.4% |
| Daily drawdown cap | 10% | 20% never binds on one market; on a multi-market book 10% cut drawdown from 31% to 17% |
| Break-even / trailing | OFF | measured: trailing truncates the winners this setup depends on |

---

## 5. Returns by capital, risk, compounding and pyramiding

**GBPJPY, ORB_v1, last 8 months.** "exp R" is expectancy per trade; drawdown is on closed balance.

| Capital | Risk | Compound | Pyramid | Net $ | Return | Max DD | PF | Exp $ | Exp R | Trades | Months up |
|---|---|---|---|---|---|---|---|---|---|---|---|
| $10,000 | 1.0% | no | no | +3,403 | +34.0% | 5.8% | 1.52 | 22.39 | +0.225 | 152 | 8/9 |
| $10,000 | 1.0% | yes | no | +3,862 | +38.6% | 7.0% | 1.49 | 25.41 | +0.225 | 152 | 8/9 |
| $10,000 | 1.8% | no | no | +6,103 | +61.0% | 9.0% | 1.52 | 40.15 | +0.225 | 152 | 8/9 |
| **$10,000** | **1.8%** | **yes** | **no** | **+7,696** | **+77.0%** | **12.4%** | **1.47** | **50.63** | **+0.225** | **152** | **8/9** |
| $10,000 | 1.8% | yes | yes | +6,854 | +68.5% | 16.4% | 1.29 | 31.88 | +0.154 | 215 | 5/9 |
| $350 | 1.0% | no | no | +67 | +19.0% | 4.7% | 1.42 | 0.50 | +0.199 | 132 | 8/9 |
| $350 | 1.0% | yes | no | +95 | +27.2% | 5.2% | 1.50 | 0.69 | +0.191 | 139 | 8/9 |
| $350 | 1.8% | no | no | +151 | +43.2% | 8.6% | 1.44 | 1.02 | +0.205 | 149 | 8/9 |
| **$350** | **1.8%** | **yes** | **no** | **+220** | **+62.8%** | **10.6%** | **1.46** | **1.44** | **+0.225** | **152** | **8/9** |
| $350 | 1.8% | yes | yes | +203 | +58.0% | 14.6% | 1.29 | 0.94 | +0.154 | 215 | 6/9 |

The 10% and 20% daily caps give identical results here — a single market never loses 10% in a day at these sizes.

**$350 vs $10,000.** Percentage returns are close, so the edge itself doesn't care about account size. The difference is *which trades you can take*: at $350, 20 of 152 trades were refused at 1% risk (minimum lot too big) and 0–3 at 1.8%. On wider-stop markets the refusals dominate — see §8.

**Month by month, $10,000, 1.8%, compounding:**
- Last 8 months: +11.6, +4.3, -1.9, +23.3, +1.7, +1.1, +4.7, +9.3, +6.9 (%)
- In-sample worst months: -15.1, -15.7, -8.8; best: +26.8, +24.3, +17.7
- Holdout worst: -18.8, -8.6; best: +22.9, +14.5, +14.3

## 5b. Multi-market book (shipped + 5 candidates), last 8 months

| Capital | Risk | Compound | Pyramid | Daily cap | Net $ | Return | Max DD | PF | Trades | Refused |
|---|---|---|---|---|---|---|---|---|---|---|
| $10,000 | 1.8% | no | no | 10% | +2,920 | +29.2% | 17.2% | 1.20 | 193 | 638 |
| $10,000 | 1.8% | no | no | 20% | +3,958 | +39.6% | 30.8% | 1.16 | 297 | 507 |
| $10,000 | 1.0% | yes | no | 20% | +3,978 | +39.8% | 25.1% | 1.15 | 445 | 39 |
| $350 | 1.0% | no | no | 20% | +114 | +32.4% | 11.9% | 1.30 | 245 | 577 |

Adding markets raises the return a little and the drawdown a lot, and at $350 most of its trades are refused. **One market, sized properly, is better than five you cannot size.**

---

## 6. What your monthly targets would actually require

$10,000, book, compounding, 20% daily cap. "Reshuffled" = 200 runs with the same trades in a different order.

| Risk | Avg month | Worst month | Max DD | Reshuffled return p5 | Reshuffled DD p95 | P(losing) |
|---|---|---|---|---|---|---|
| 1.0% | +6.1% | -4.3% | 25% | +23% | 25% | 0.5% |
| 1.8% | +5.9% | -9.2% | 37% | -2% | 38% | 5% |
| 3.0% | +5.2% | 0.0% | 43% | -31% | 51% | 13% |
| 5.0% | +3.7% | -19.3% | 42% | -44% | 58% | 29% |
| 8.0% | **-7.6%** | -38.0% | 53% | -55% | 66% | 38% |
| 12.0% | **-13.2%** | -52.2% | 66% | -65% | 74% | 45% |

Past ~2% risk, returns fall and ruin risk climbs: the daily cap starts cutting size after losses, and losing streaks compound against a smaller balance. **The way to more money here is more capital or more validated markets, not more risk.**

---

## 7. Does it reproduce live? Tick replay

GBPJPY ORB, last 8 months, every trade re-filled on real MT5 bid/ask ticks (market entry at the first tick after the bar closes, stop triggered on the opposite side of the book and filled at the tick that crossed it):

| | Avg per trade | Total |
|---|---|---|
| Bar simulation | +0.211R | +32.0R |
| **Tick replay** | **+0.180R** | **+27.4R** |

Same win/loss on **99%** of trades, correlation 0.97, entry fills cost +0.02R. So expect roughly **85% of the simulated figure** live: about +65% rather than +77% at $10k/1.8% compounding, before the margin cap (§3, item 3).

---

## 8. Every market tested (26), settings chosen in-sample only

Expectancy in R per trade; `$10k` is net P&L at 1.8% compounding with a 10% daily cap.

| Market | Config chosen | 2022–23 | In-sample | Last 8 months | $10k last 8m | DD |
|---|---|---|---|---|---|---|
| **GBPJPY (shipped 60m 1:3)** | london 60m break 1:3 | **+0.054** | **+0.076** | **+0.225** | **+7,696** | 12.4% |
| AUDUSD | london 60m retest 1:4 hold | -0.151 | +0.075 | +0.307 | +8,062 | 16.7% |
| AUDJPY | london 30m retest 1:3 hold | -0.231 | +0.153 | +0.165 | +4,279 | 19.4% |
| Germany 40 | ny 15m retest 1:3 hold | no data | +0.319 | +0.160 | +6,153 | 25.6% |
| UK 100 | ny 15m retest 1:1 hold | no data | +0.059 | +0.140 | +1,481 | 13.0% |
| EURGBP | london 15m retest 1:1 hold | +0.134 | +0.117 | +0.132 | -1,135 | 13.1% |
| US SP 500 | london 15m retest 1:4 hold | no data | +0.097 | +0.131 | +4,495 | 16.1% |
| Wall Street 30 | london 60m break 1:2 hold | no data | +0.077 | +0.106 | +3,523 | 17.9% |
| ETHUSD | london 15m break 1:2 | -0.197 | +0.075 | +0.098 | +1,820 | 23.2% |
| GBPAUD | ny 30m retest 1:4 | -0.093 | +0.109 | +0.044 | +1,490 | 15.6% |
| NZDUSD | ny 60m retest 1:3 BE | +0.039 | +0.029 | +0.043 | +975 | 9.8% |
| BTCUSD | ny 60m retest 1:3 BE | +0.082 | +0.091 | +0.035 | +901 | 13.5% |
| USDCAD | ny 30m break 1:2 hold | -0.034 | +0.103 | +0.004 | -28 | 31.2% |
| GBPJPY (wide-search pick) | london 15m retest 1:4 | -0.099 | +0.164 | -0.001 | -1,047 | 23.3% |
| EURAUD | london 15m retest 1:1 | +0.081 | +0.091 | -0.009 | -294 | 8.2% |
| EURJPY | london 30m retest 1:1.5 | +0.034 | +0.124 | -0.021 | -328 | 26.2% |
| CADJPY | london 60m retest 1:3 hold | +0.118 | +0.147 | -0.026 | +417 | 17.9% |
| US Tech 100 | ny 15m break 1:1.5 hold | no data | +0.149 | -0.028 | -1,096 | 33.1% |
| USDCHF | london 15m break 1:4 hold | -0.105 | +0.131 | -0.029 | -821 | 38.1% |
| Japan 225 | london 30m break 1:4 | no data | +0.094 | -0.032 | **unsizable** | — |
| XAUUSD | london 15m retest 1:1 | -0.038 | +0.126 | -0.064 | -1,287 | 14.1% |
| GBPCHF | ny 15m retest 1:1 | +0.095 | +0.113 | -0.068 | +1,372 | 5.6% |
| XAGUSD | london 15m retest 1:1 | -0.078 | +0.058 | -0.069 | -1,705 | 19.4% |
| EURUSD | london 15m retest 1:1.5 hold | +0.032 | +0.154 | -0.145 | -1,916 | 29.3% |
| USDJPY | ny 60m retest 1:4 hold | -0.004 | +0.258 | -0.252 | -4,446 | 51.3% |
| XPTUSD | nothing profitable in-sample | — | — | — | — | — |

Read the first three columns together, not the middle one alone: **20 of 25 markets were positive in-sample and only 13 stayed positive afterwards**, which is what selection noise looks like. Only **GBPJPY, EURGBP, NZDUSD and BTCUSD** were positive in all three windows.

**Japan 225 is untradable on either account:** minimum lot 10 contracts.

---

## 9. Patterns and confluences — what actually repeats

Each ORB setup recorded nine features measurable at the signal bar, and one filter was then chosen in-sample per market. Results:

| Confluence (tested) | What it means | Verdict |
|---|---|---|
| `trend_align` | break agrees with the 20-day average | Helped most often: GBPJPY +0.113R over the last 8 months (base flat), Wall Street 30, Japan 225, CADJPY in-sample. **The one worth keeping.** |
| `early_break` | break happens within an hour of the range closing | Held on EURGBP in all three windows (+0.180/+0.144/+0.086R) |
| `prevday_align` | break agrees with yesterday's candle direction | Helped EURJPY and USDCAD in-sample, faded after |
| `range_narrow` | opening range narrow vs its own recent average | Helped in-sample (XAGUSD, USDJPY), **failed out of sample** |
| `strong_body` | breakout candle closes near its extreme | Small, inconsistent |
| `vol_surge` | breakout bar's tick volume ≥ 1.5× the opening range average | No reliable effect — this is the closest thing to "order flow" MT5 keeps |
| `open_vol_high` | heavy participation during the opening range | Helped UK 100 and Germany 40 in-sample only |
| `beyond_pdhl` | break also clears yesterday's high/low | No consistent effect |
| `beyond_asia` | break also clears the Asian session range | No consistent effect |
| `gap_small` | small overnight gap | No consistent effect |

**Zone retests** (your "retest of a particular zone"): tested as a full entry mode — a limit back at the broken range edge instead of a market order. It wins in-sample on 17 of 25 markets and is the *first* thing to fail on the holdout (GBPJPY: in-sample +0.164R → holdout -0.099R). Retest entries get better prices on the trades that come back and miss the trades that run, and on this data the misses cost more.

**Volume clusters** were tested two ways (breakout-bar volume surge, opening-range participation) and neither survived out of sample. Real order-book clusters cannot be tested at all: MT5 keeps no depth history (see §10).

---

## 10. Non-ORB strategies that held up

From the 8-family search, these were positive on both the 2022–23 holdout and the last 8 months:

| Market | Strategy | 2022–23 | Last 8 months | Note |
|---|---|---|---|---|
| BTCUSD | breakout + tick-volume surge (H1, 55-bar, 2× volume) | +0.44R, 200 trades, t=1.98 | +3.85R on 9 trades | best non-ORB candidate; the recent sample is tiny |
| BTCUSD | Donchian 55 long, channel exit | +0.95R | +0.09R | |
| XAUUSD | EMA 50/200 pullback long, 1:10 target | +0.48R | +0.07R | the only place a 1:10 target worked |
| EURUSD | RSI(2) < 5 long with the 200-bar trend | +0.04R | +0.11R, 8/9 months up | very small edge per trade |

None is ready to ship: each was picked from a 16-point grid on one market and needs the same walk-forward and tick check ORB got. The gold EMA pullback is the honest answer to "can 1:10 work" — yes, on gold, trading with a 200-period trend, roughly 1 trade a week.

---

## 11. TODO

**Go live (in order)**
- [ ] Deploy: git is local-only, so copy/pull as usual, restart the backend, rebuild the frontend.
- [ ] Settings → slots: ORB_v1 on GBPJPY only. Risk 1.0% for the first month, `sizing_basis: BALANCE`, pyramiding off, daily cap 10%.
- [ ] Re-run ORB_v1/GBPJPY in the Backtester (2026-01-10 → 09-11, $10,000) and check it now matches §5 — the entry-timing fix is what makes those numbers appear.
- [ ] Decide how much risk you actually want deployed: add `(70, 100.0)` to `confluence_risk_tiers`, or set 2.4% to get an effective 1.8%. Do this before judging live results against §5.
- [ ] Enter the measured costs (spread 1.9, commission 0, slippage 0.3) in any GBPJPY Backtester run, or the page will keep showing ~a third of the tick-confirmed edge.
- [ ] Demo-forward-test 4–6 weeks. Stop if drawdown exceeds 20% or 40 trades average below 0R.

**Validate the next candidates the same way**
- [ ] Tick-replay and walk-forward EURGBP, Germany 40, UK 100, Wall Street 30, US SP 500 before adding any of them.
- [ ] Add `trend_align` as an optional ORB_v1 filter and test it per market (it was the only confluence that repeated).
- [ ] Give ORB_v1 a real confluence score (APA and VWAP already compute one) built from the features in §9, so risk tiers mean something and `confluence_stats` stops being a constant 70.
- [ ] Validate the four non-ORB candidates (§10) properly.

**Fix the remaining engine issues**
- [x] Commission now comes from your own deal history ($0 here) instead of a $7/lot guess.
- [ ] Charge each bar's **own** spread instead of the spread that happened to be live when the run started (the bars already carry it; costs ~0.03R per trade on GBPJPY).
- [ ] Calibrate the 0.7-pip slippage default against tick replay (~0.02R measured, including spread).
- [ ] Margin fallback estimate converts quote currency wrongly (JPY treated as USD) — clamps live positions to the minimum lot when MT5's margin call is unavailable.
- [ ] Portfolio backtester never calls the strategy's per-bar exit hook, so ORB's session close is missing in portfolio runs (single-symbol runs are correct).
- [ ] Re-run and re-save any historical backtest you rely on: everything before 2026-09-12 has the one-bar delay and the JPY pip bug.

**Data you asked about**
- [ ] Order book / gamma still cannot be backtested (no MT5 depth history, no options data). Start logging DOM snapshots now if you want them tested in 3–6 months.

**Frontend (noted, not touched)**
- [ ] Claude "summarize backtest" on fresh and saved runs, and journal analysis, don't work properly. You said not to rush it; it's recorded.

---

## 12. Files

- `backend/analytics/orb_research.py` — ORB setups, features, exit variants, pyramid outcomes, walk-forward selection.
- `backend/analytics/money_sim.py` — the account simulator (capital, risk, compounding, pyramiding, daily cap, real lot grid).
- `scripts/run_orb_research.py`, `scripts/run_book_sims.py`, `scripts/run_tick_replay.py`, `scripts/run_app_backtest.py` (headless run of the app's own engine, `--dump` for per-trade comparison).
- Data: `data/orb_research/` (setups, per-market table, book sims, risk scan).
- Tests: `tests/test_orb_research.py`, `test_money_sim.py`, `test_entry_timing.py`, `test_pip_size.py` (411 tests pass).
