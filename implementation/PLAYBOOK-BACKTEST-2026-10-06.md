# Video-queue playbook, measured

> **61 cells.** Five strategies x two brokers x six markets, flat and
> prop-constrained. Updated 2026-10-06 with S1 and S7 built from the source.
>
> **Outcome:** `FairPrice_v1` (S1) and `HourlyLevel_v1` (S7) were removed from
> the platform on the strength of this table, together with `APA_v1` on separate
> evidence — see §3b. The registry is now 12 strategies. `VWAP_v1` measured
> negative here and was kept by decision; §3 explains why that is defensible and
> where the limit is.

**2026-10-06** · `scripts/run_playbook_matrix.py` · raw cells in
`data/playbook/results.jsonl`

Source: *Strategy Backtest Playbook — Video Queue #1* (17pp, eight strategies).
Window **2026-01-01 → 2026-10-04**, $10,000, **1% flat risk** (fixed dollars, no
compounding), the app's own engine and fills, each broker's own bars and spreads.

---

## 0. The finding that outranks every strategy in the table

**8 of 15 paired cells flip sign between brokers.** Same rule, same market, same
nine months — only the feed differs:

| Strategy | Market | **Deriv** | **FundedNext** | |
|---|---|---:|---:|---|
| VWAP | NASDAQ | **+$60** | **−$3,181** | flips |
| VWAP | S&P 500 | **+$642** | **−$842** | flips |
| VWAP | Gold | **+$1,695** | **−$134** | flips |
| ORB | S&P 500 | **+$300** | **−$1,929** | flips |
| Overnight | GBPJPY | **+$1,290** | **−$4,244** | flips |
| Overnight | EURJPY | **+$347** | **−$3,947** | flips |
| Overnight | NASDAQ | +$140 | −$109 | flips |
| Overnight | Gold | −$115 | +$301 | flips |

Profitable cells: **Deriv 10/15, FundedNext 6/18.**

A backtest on Deriv therefore says almost nothing about whether something
survives at FundedNext. **Anything intended for the prop account must be
measured on the prop account's bars.** This is the same result the combined-book
work reached (t +3.76 on Deriv, −8.2% on FundedNext), now reproduced on three
independent strategies.

It also reframes the playbook itself. Those claims were measured on US equity
futures with institutional execution. What is being measured here is
substantially **the spread, not the idea**.

---

## 1. FundedNext — the account that matters

| Strategy | Symbol | n | WR | P&L | Return | Max DD | PF | exp R |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| **Overnight** | US30 | 187 | 49% | **+$1,068** | +10.7% | 10.4% | **1.16** | +0.082 |
| ORB | US30 | 185 | 35% | +$522 | +5.2% | 12.3% | 1.04 | +0.014 |
| Overnight | XAUUSD | 179 | 46% | +$301 | +3.0% | 12.9% | 1.05 | +0.022 |
| ORB | GBPJPY | 96 | 45% | +$254 | +2.5% | 9.2% | 1.05 | +0.028 |
| Overnight | SPX500 | 186 | 51% | +$244 | +2.4% | 13.1% | 1.03 | +0.015 |
| ORB | XAUUSD | 84 | 49% | +$29 | +0.3% | 5.2% | 1.01 | +0.023 |
| VWAP | EURJPY | 39 | 31% | −$36 | −0.4% | 7.2% | 0.98 | −0.041 |
| Overnight | NDX100 | 188 | 48% | −$109 | −1.1% | 12.6% | 0.98 | −0.005 |
| VWAP | XAUUSD | 73 | 32% | −$134 | −1.3% | 7.8% | 0.97 | −0.038 |
| VWAP | US30 | 140 | 26% | −$774 | −7.7% | 18.4% | 0.89 | −0.076 |
| VWAP | SPX500 | 137 | 26% | −$842 | −8.4% | 12.9% | 0.88 | −0.116 |
| ORB | NDX100 | 180 | 32% | −$990 | −9.9% | 29.9% | 0.91 | −0.064 |
| VWAP | GBPJPY | 48 | 23% | −$998 | −10.0% | 11.6% | 0.62 | −0.370 |
| ORB | SPX500 | 175 | 31% | −$1,929 | −19.3% | 35.6% | 0.83 | −0.113 |
| ORB | EURJPY | 174 | 34% | −$3,127 | −31.3% | 34.7% | 0.69 | −0.193 |
| VWAP | NDX100 | 155 | 19% | −$3,181 | −31.8% | 31.8% | 0.61 | −0.326 |
| Overnight | EURJPY | 193 | 39% | −$3,947 | −39.5% | 41.6% | 0.62 | −0.215 |
| Overnight | GBPJPY | 193 | 38% | −$4,244 | **−42.4%** | 44.5% | 0.60 | −0.235 |

## 2. Deriv

| Strategy | Symbol | n | WR | P&L | Return | Max DD | PF | exp R |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| VWAP | XAUUSD | — | — | **+$1,695** | +17.0% | — | **1.42** | — |
| Overnight | GBPJPY | — | — | +$1,290 | +12.9% | — | 1.16 | — |
| VWAP | US SP 500 | 137 | 28% | +$642 | +6.4% | 10.4% | 1.10 | +0.043 |
| Overnight | EURJPY | — | — | +$347 | +3.5% | — | 1.05 | — |
| ORB | US SP 500 | 194 | 30% | +$300 | +3.0% | 17.6% | 1.02 | +0.016 |
| ORB | XAUUSD | 90 | 46% | +$221 | +2.2% | 9.3% | 1.06 | +0.043 |
| Overnight | US SP 500 | 191 | 45% | +$206 | +2.1% | 17.0% | 1.02 | +0.013 |
| Overnight | US Tech 100 | 196 | 47% | +$140 | +1.4% | 15.0% | 1.02 | −0.008 |
| VWAP | US Tech 100 | 37 | 27% | +$60 | +0.6% | 4.1% | 1.02 | +0.029 |
| Overnight | XAUUSD | 182 | 44% | −$115 | −1.2% | 14.9% | 0.98 | −0.013 |
| VWAP | EURJPY | 32 | — | −$717 | −7.2% | — | 0.65 | — |
| ORB | US Tech 100 | 110 | 45% | −$1,117 | −11.2% | 16.2% | 0.77 | −0.108 |
| VWAP | GBPJPY | — | — | −$1,494 | −14.9% | — | 0.45 | — |
| ORB | EURJPY | 184 | — | −$2,336 | −23.4% | — | 0.78 | — |

---

## 2b. S1 and S7 — built from the playbook, and both lose

`FairPrice_v1` (S1) and `HourlyLevel_v1` (S7) were implemented from the source
rules, wired into every list, and measured the same way. Neither survives.

### FundedNext

| Strategy | Symbol | signals | trades | WR | P&L | DD | PF |
|---|---|---:|---:|---:|---:|---:|---:|
| FairPrice | NDX100 | 235 | 211 | 24% | −$82 | 12.4% | 0.99 |
| FairPrice | US30 | 231 | 196 | 23% | −$175 | 16.5% | 0.98 |
| FairPrice | SPX500 | 189 | 175 | 19% | −$2,629 | 27.8% | 0.66 |
| FairPrice | XAUUSD | 213 | 179 | 15% | −$3,382 | 35.3% | 0.58 |
| HourlyLevel | US30 | 13 | 13 | 15% | −$65 | 3.0% | 0.89 |
| HourlyLevel | XAUUSD | 17 | 12 | 8% | −$344 | 5.9% | 0.43 |
| HourlyLevel | SPX500 | 21 | 14 | 14% | −$428 | 5.0% | 0.27 |
| HourlyLevel | NDX100 | 29 | 27 | 19% | −$894 | 9.4% | 0.49 |

### Deriv

| Strategy | Symbol | signals | trades | WR | P&L | PF |
|---|---|---:|---:|---:|---:|---:|
| HourlyLevel | US SP 500 | 17 | 11 | 36% | **+$406** | 1.66 |
| HourlyLevel | US Tech 100 | 22 | 17 | 29% | +$93 | 1.08 |
| HourlyLevel | XAUUSD | 16 | 14 | 7% | −$708 | 0.27 |
| FairPrice | US SP 500 | 174 | 154 | 23% | −$1,155 | 0.86 |
| FairPrice | US Tech 100 | 190 | 169 | 19% | −$1,709 | 0.81 |
| FairPrice | XAUUSD | 171 | 138 | 17% | −$2,229 | 0.68 |

**S1 is negative on both brokers** — the cleanest verdict in the study, because
it does not depend on whose spread you pay. **S7 flips** (negative on FundedNext
indices, positive on Deriv's), which is the broker again, not the idea.

### The win rate is the diagnosis

S1 targets 1.5R and needs ~40% to break even. It gets **15–24%**.
S7 targets 2R and needs ~33%. It gets **8–19%**.

These are not marginal misses. The entry triggers — displacement, break of
structure, wick-and-close at a level — are not selecting better-than-random
points once the spread is paid.

### Two cells to ignore, and why

`FairPrice · GBPJPY · FundedNext` shows +$235 at PF 2.78 — from **4 trades out
of 187 signals**. On Deriv the same pair produced **172 signals and 0 trades**,
and `HourlyLevel · GBPJPY` produced 13 and 17 signals with zero trades on both
brokers. Something systematically rejects these before they are sized, almost
certainly the minimum-stop floor against a JPY-pair stop measured in ATR. The
+$235 is noise and the zero-trade cells are a measurement failure, not a result.
Both are listed for completeness and neither should be read as evidence.

---

## 2c. Prop-firm rules barely matter

FundedNext's 5% daily / 10% overall limits, against the same runs unconstrained:

| Symbol | Strategy | flat | with prop rules |
|---|---|---:|---:|
| US30 | Overnight | +$1,068 | **+$1,118** |
| XAUUSD | Overnight | +$301 | **+$733** |
| SPX500 | Overnight | +$244 | +$191 |
| NDX100 | Overnight | −$109 | −$193 |
| US30 | ORB | +$522 | +$225 |
| NDX100 | VWAP | −$3,181 | −$2,752 |

The rules are close to neutral and on the two best cells they **helped** — the
daily-loss pause stops a bad day compounding. The limits are not what stands
between these strategies and profitability; the spread is.

---

## 2d. Whole study, one table

Net P&L per strategy per broker, flat risk, cells that actually traded:

| Strategy | Deriv | FundedNext |
|---|---:|---:|
| **Overnight** | **+$1,867** (4/5 positive) | −$6,688 (3/6) |
| VWAP | +$187 (3/5) | −$5,965 (**0/6**) |
| HourlyLevel | −$209 (2/3) | −$1,732 (0/4) |
| ORB | −$2,919 (3/5) | −$5,241 (3/6) |
| FairPrice | −$5,093 (**0/3**) | −$6,033 (1/5) |

**Nothing is positive on FundedNext in aggregate.** Overnight's +10.7% on US30
is the one genuinely good cell in 61, and it is outweighed by the same strategy
applied to FX, where it has no mechanism and loses 40%.

The honest summary of the whole exercise: **of five strategies measured across
two brokers and six markets, exactly one configuration is worth keeping —
Overnight, on indices, and nowhere else.**

---

## 3. Verdicts on the three playbook strategies already in the platform

### S4d Overnight hold — **keep, restricted to indices**

The playbook rates it priority 1; it was **deleted on 2026-10-02 as
unprofitable** while carrying its own evidence that it was *"the ONLY strategy in
this book still positive on FundedNext's bars"*. The deletion was wrong and it
has been restored.

On FundedNext indices it is positive on **US30 (+10.7%, PF 1.16)** and SPX500,
and flat on NDX100. **US30 is the best cell in the entire 33-cell study.**

But the edge is modest — +10.7% over nine months against a 10.4% drawdown, a
return-over-drawdown near 1.0. Nothing resembling the playbook's claims.

### S4b VWAP cross — **measured negative; kept by decision**

The playbook cites a paper claiming ~671% over five years on QQQ. On FundedNext
it loses on **all six symbols**, worst at PF 0.61 on NASDAQ. It is positive on
four Deriv symbols, which is the broker talking, not the strategy. This
independently reproduces the existing 26-market finding that VWAP has no edge
here.

**Kept in the platform anyway, on the owner's instruction (2026-10-06).** Two
things to know about that:

1. It is the only thing the live book is actually running — the three enabled
   slots on the VPS (XAUUSD, US Tech 100, Step Index 500) are all `VWAP_v1`.
   Unregistering it would have silently disabled 60% of the live book.
2. Those three slots do not run the form this study measured. They run the
   per-market `SESSION_TREND` / `SESSION_PULLBACK` picks from the 2026-09-13
   edge lab, which were positive in all three out-of-sample windows. The cells
   above are the generic cross, on default gates. The two are not the same
   strategy wearing one name, and this table does not falsify the shipped picks.

What this study *does* say is that the generic VWAP cross should not be the
default for a new symbol. Point a new VWAP slot at a measured per-market entry
in `SLOT_TP1_RR` / `SYNTH_SLOT_PARAMS` or measure it first.

### S4a Opening-range breakout — **keep per market, not as a default**

Positive on US30, GBPJPY and gold; clearly negative on NASDAQ, S&P and EURJPY.
That matches the walk-forward result where GBPJPY London held across all three
periods and indices did not. It is a per-market tool.

---

## 3b. What was removed from the platform, and on what evidence

Three strategies are gone from the registry as of 2026-10-06. Each needs its own
evidence, because only two of the three were in this study.

| Removed | Evidence | In this study? |
|---|---|---|
| `FairPrice_v1` (S1) | **0 of 4** profitable Deriv cells, −$5,093 over 461 trades; **1 of 5** on FundedNext, −$6,033 over 765. Win rate 15–24% against a 1.5R target needing ~40%. | yes |
| `HourlyLevel_v1` (S7) | **0 of 5** on FundedNext, −$1,732; 2 of 4 on Deriv but −$209 net — and only **108 trades in total**, so even the positive cells are thin. | yes |
| `APA_v1` | Its own shipped default records the aggregate: at its **best** fixed target of the four measured (1:3), **−$3,689 over n=3,225**; 1:2 was −$12,116. The retest gate measured **−0.53 R, profitable on 0 of 21 markets**. Three per-symbol cells were positive (V75, XRPUSD, Crash 500) and are the only thing it had. | **no** |

`APA_v1` was removed on the owner's instruction, not on a playbook cell — it is
not a playbook strategy. The numbers above are the pre-existing measurements
that support the call, and they are consistent with it.

**Nothing live was touched.** Checked against the VPS database directly:
`APA_v1` held **14 slots, 0 of them enabled** (EURUSD, GBPUSD, BTCUSD, DJI,
Volatility 100, Jump 75, UKOIL, DOGE, AUDJPY, AUDUSD, XAG, XAGUSD, Kiwi,
Aussie). The five enabled slots are:

| Symbol | Strategy | |
|---|---|---|
| XAUUSD | `VWAP_v1` | survives |
| US Tech 100 | `VWAP_v1` | survives |
| Step Index 500 | `VWAP_v1` | survives |
| Boom 900 Index | `BoomDriftJump_v1` | survives |
| Crash 1000 Index | `DriftJumpAlpha_v1` | survives |

This is the reason the VWAP decision mattered and the APA one did not.

Disabled slots still naming `APA_v1`, `NYOpenRetest_v1` or `HTFFVGFlip_v1` will
show as an unknown strategy in the slot editor until they are repointed or
deleted. They cannot trade.

Registry after the change — **12 strategies**:

```
BiasIFVG_v1        BoomDriftJump_v1   DriftJumpAlpha_v1  ORB_v1
OvernightSession_v1 RangeBreakout_v1  RangeRevert_v1     SpikeFade_v1
SpikeResumption_v1 TrendBreakout_v1   TrendDrift_v1      VWAP_v1
```

`APA_v1` was the hard-coded default strategy id in eleven places (the
`BacktestRequest` and `PortfolioSymbolConfig` models, two DB column defaults,
`BaseStrategy.strategy_id`, the `SMC_v1` legacy alias, bot_service's
legacy-slot migration, and four frontend screens). Those now default to
`VWAP_v1` on the backend and `ORB_v1` on the Backtester and Settings screens.
`backend/analytics/apa_research.py` and its research scripts stay: they are the
measurement record, not the shipped engine.

### A bug this removal uncovered

Deleting the earlier strategies by line number damaged
`backend/api/routes/backtest.py` in three places — the deletions took lines that
also carried surviving code:

- `_target = {...}.get(req.strategy_id)` was removed while the next line still
  read `_target`. Every backtest of a strategy with a measured
  `session_filter_enabled` would have raised `NameError`.
- Both VWAP daily-cap guards (`if req.strategy_id == "VWAP_v1":` and
  `if strat_id == "VWAP_v1" and ...`) were removed, which left the cap
  assignment nested under the *"strategy_params keys ignored"* warning — so it
  applied only when a request had bad keys, and never otherwise.

All three are restored. It parses either way, which is why only reading the
diff against HEAD found them.

## 4. A correction to my own first reading

On the FundedNext numbers alone I wrote that Overnight's FX losses were
**mechanistic** — no closing auction, no overnight risk premium in FX, so the
idea simply does not apply there.

**The Deriv data contradicts that.** Overnight on GBPJPY made **+$1,290 (PF
1.16)** on Deriv and lost **$4,244** on FundedNext. If the mechanism were
absent, it would fail on both. The sign is set by **cost**, not by mechanism, and
my explanation was a story fitted to one broker's numbers.

The defensible version: Overnight on FX is **not supported by any mechanism and
is cost-fragile**, so it should still be restricted to indices — but for want of
a reason to believe it, not because the data proves the mechanism missing.

---

## 5. What is NOT in this table, and why

- **Sharpe** — `run_app_backtest.py` does not emit it; the summary carries
  expectancy in R, profit factor and drawdown instead. Adding it means storing
  per-trade returns, which is a change to the runner rather than a re-run.
- **Three cells show blanks** for WR and DD: `HourlyLevel·GBPJPY·FundedNext`,
  `FairPrice·GBPJPY·Deriv` and `HourlyLevel·GBPJPY·Deriv`. Re-checked against
  `results.jsonl` — they are blank because those cells took **0 trades**, not
  because an older invocation stored fewer fields. See §2b: the minimum-stop
  floor against a JPY-pair ATR stop rejects every signal before sizing. A
  re-run will not fill them; fixing the floor interaction would.
- **Prop-firm rules on** (5% daily / 10% overall): **done**, 10 cells, and the
  verdict is in §2c — the constraints barely bite. FundedNext only, because
  Deriv imposes no prop rules; running them against Deriv's bars would measure
  a rulebook that does not apply to that account.
- **In/out-of-sample split**: **not computable from what is stored.**
  `results.jsonl` holds per-cell summaries only, no trade list, so the split
  cannot be derived after the fact — it needs each cell run twice, on each half
  of the window. For the three surviving strategies that is 36 cells × 2 ≈
  3.5 hours of terminal time. It is the next measurement worth paying for,
  because §0's broker finding means an edge that only holds in one half is
  indistinguishable from an edge that only holds at one broker.

## 6. The five playbook strategies not tested

| | Why not |
|---|---|
| S4c post-earnings drift | needs EPS actual vs consensus and a stock universe |
| S5 gamma-wall reversal | needs paid intraday options OI — and strikes already measured inert (5 markets, 2,500–5,900 approaches/cell, no \|t\| ≥ 2) |
| S6 market-cycle swing | needs a US growth-stock universe and sector tags |
| S8 small-cap shorts | needs float, market cap, borrow availability |
| S2 statistical structure | needs volume profile and GARCH regimes |

AlgoEdge trades CFDs and synthetics through MT5. FundedNext carries ~30 US
stocks, which is far short of a screening universe. These are a different
product, not a missing strategy.

**S1 Fair Pricing Theory** and **S7 hourly break-retest** are the two genuinely
new, fully mechanical, price-only strategies worth building — and worth building
*after* this table, because it shows what the cost environment does to ideas
imported from US equity futures.
