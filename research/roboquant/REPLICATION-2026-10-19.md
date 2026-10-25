# roboquant.dev templates: replication, 2026-10-19

The five templates the user had open on roboquant.dev were rebuilt from their Rust source as app strategies (`backend/strategies/strategy_roboquant/`, tests in `tests/test_roboquant_strategies.py`). They were backtested through the app's own BacktestEngine, risk layer and exit hooks on FundedNext MT5 bars, **2022-10-20 → 2026-10-19**: four years, which is all the history FundedNext holds.

The runner is `scripts/run_roboquant_replication.py` and the raw results are in `results/*.json`. Each template was run at 1% risk on a $100k balance, so the broker's minimum lot never decides which trades happen. Each was run twice, at real CFD costs and at zero cost.

The futures contracts map to CFDs as follows: NQ/MNQ → **NDX100**, ES → **SPX500**.

## Verdict first

| Template | Market / TF | Claim (roboquant) | Replicated (real costs) | Tradable? |
|---|---|---|---|---|
| **T-Mean Reversion** | NDX100 H1 | 63.4% win, PF 2.58 | **91 trades, 63.7% win, PF 2.44, +0.108R, t +3.21** | **Yes**: the best of the five |
| **Charles Monger** | NDX100 H1 | 24.9% win, PF 1.32 | **280 trades, 22.9% win, PF 1.60, +0.215R, t +1.80** | **Yes**, with a small size (long losing runs) |
| Charles Monger | SPX500 H1 | (same template) | 274 trades, 23.4% win, PF 1.39, +0.110R, t +1.36 | Weaker; 2026 is flat |
| Crazy ORB | NDX100 M15 | 81.3% win, PF 1.57 | 1,278 trades, 62.9% win, PF 0.58, −0.16R (on 15m bars). **On 1m bars: 75.4% win, PF 1.09, +0.02R, t +1.34 before costs** | No: the spread is larger than the edge |
| NY Open Range Breakout | NDX100 M15 | 74.0% win, PF 1.22 | 469 trades, 56.3% win, PF 0.50, −0.23R. **On 1m bars: 65.7% win, PF 0.77, −0.08R, t −3.71 before costs** | No: it loses even at zero cost |
| NQ Precision Bot | NDX100 M1 | 94.0% win, PF 2.48 | 240 trades, 85.4% win, PF 0.95, −0.006R (zero cost: 87.4%, PF 1.15, t +0.86); 2026 is −0.09R | No: needs 88% to break even; the spread takes the rest |

The two slow strategies that hold for days, T-Mean Reversion and Charles Monger, **replicate their published win rate and profit factor almost exactly**. The three intraday high-win-rate ORB bots do not.

## Why the high-win-rate ORB claims don't hold

The ORB templates use tiny targets: 0.35–0.4 × the stop. Often one 15-minute bar touches both the stop and the target, and the backtest must then guess which came first. On NY ORB this happens on **481 of 938 trades**. Re-scoring the same 938 signals at zero cost:

| How a bar that touches both is resolved | NY ORB win / mean R / PF | Crazy ORB win / mean R / PF |
|---|---|---|
| Target first (optimistic) | 87% / +0.22R / 2.73 | 79% / +0.07R / 1.34 |
| Stop first (the app's 15m backtest) | 36% / −0.50R / 0.22 | 61% / −0.18R / 0.55 |
| **What actually happened, replayed on 1-minute bars** | **66% / −0.08R / 0.77** | **75% / +0.02R / 1.09** |

The published figures sit near the optimistic row. The 1-minute replay is the closest to the truth.

Even before any cost, NY ORB loses and Crazy ORB is almost break-even. Then the spread has to be paid:
- FundedNext NDX100 costs about 1.2 points.
- NY ORB's stop is about 0.4 × ATR(15m), roughly 15–20 points, so the spread costs about **0.07R per trade**.
- Crazy ORB's stop is about 1 × ATR, roughly 40 points, so the spread costs about **0.03R per trade**. That is more than its +0.02R edge.

A 75% win rate with a 0.35R target needs at least 74% just to break even, so a 1% slip in win rate erases it.

**Lesson for "higher win rates":** a high win rate bought with a target smaller than the stop is fragile. It depends on which way bar ambiguity is resolved, on spread, and on slippage. The two templates that hold up have *normal or low* win rates and big winners.

## How the two that work are traded

### T-Mean Reversion: NDX100, weekly

**Rule:** buy at **Friday 18:00 UTC** if Friday is down. Exit at the first bar at or after **Wednesday 10:00 UTC**; if the bot misses that bar, it exits on the next one. The only stop is a 5% disaster stop.

**Results:**
- About 23 trades a year.
- Every year from 2023 onward is positive: 2023 +0.06R, 2024 +0.09R, 2025 +0.09R, 2026 +0.29R.
- Swap costs about 0.01R per 5-night hold, already included.

**Sizing:** the R is measured against the 5% disaster stop, so "1% risk" means a position of only 0.2× account value. That run returned **+10.3% over 4 years with a 1.3% drawdown**.

roboquant's +708% is essentially the same edge at full notional or leveraged futures sizing. At **1× notional (5% risk to the disaster stop)** the same trades give roughly +50–55% over the period. The drawdown is about 5× larger, around 6–7%.

**Minimum capital:**
- 0.01 lot of NDX100 is about $2,500 notional, so a 5% disaster stop is about $125.
- At 1% risk that needs about **$12,500**.
- At 1× notional (5% risk to the disaster stop) it needs about **$2,500**.

**Risks:**
- Every trade is held over the weekend, so a weekend gap can go through the 5% stop. The stop is not a guaranteed fill.
- The sample is small: 91 trades in 4 years, so t +3.2 is good but not conclusive.
- It is long only, and depends on the index's long-run upward drift. A bear year like 2022 would hurt; 2022 here has only 4 trades, at −0.24R.

### Charles Monger: NDX100 (better) or SPX500, H1 trend

**Rule:**
- Go long when the close moves above an EMA200 band: EMA200 of the high on top, EMA200 of the close below.
- Exit when the regime turns red, with a 1% stop. Long only.

**Results:**

| Market | Win rate | Return at 1% risk | Max drawdown |
|---|---|---|---|
| NDX100 | 23% | **+73%** | **15%** |
| SPX500 | 23% | +32% | 9.9% |

On SPX500 the edge has faded: 2025 +0.04R, 2026 0.00R. Prefer NDX100.

**Minimum capital:** a 1% stop at 0.01 lot is about $25, so about **$2,500 at 1% risk**.

**Risks:**
- A 23% win rate means losing runs of 10–15 trades are normal.
- It is long only, so a sideways or bear tape bleeds slowly.
- It trades the same index as T-Mean. On NDX100 both strategies are bets on the same market drifting up, so they are not independent.

## Recommendations

1. **Trade T-Mean Reversion on NDX100** as the core.
   - Size at 2–5% risk to the 5% disaster stop, i.e. 0.4–1× notional, rather than 1%: the stop is a disaster stop, not the typical loss.
   - Put the slot on FundedNext or Deriv "US Tech 100". Check Deriv's weekend swap first, because the trade holds over every weekend.
2. **Add Charles Monger on NDX100 at 0.5–1% risk** as the trend leg.
   - Do not stack both at full size: they share the same index and the same upward-drift exposure. Treat the pair as one NDX100 exposure and cap the two combined at about 1.5–2× notional.
3. **Do not run NQ Precision Bot or NY ORB.** NQ Precision is break-even at best and negative in 2026. NY ORB loses on every resolution and every year except 2024, before costs.
4. **Do not run Crazy ORB** at CFD spreads. It might break even on futures with tiny commissions, but that is not this account.
5. **Any strategy whose target is smaller than its stop** needs testing on 1-minute bars, or it should not be trusted. The app's 15m backtest resolves a bar that touches both levels as stop-first, which is *pessimistic*. roboquant's resolution is plainly optimistic.
6. **For more consistent returns, combine T-Mean with an uncorrelated stream** rather than a second NDX100 bet. Options from earlier studies:
   - GBPJPY London ORB 60m 1:3, which held in all three walk-forward periods;
   - the OvernightSession book, the only one that survives FundedNext costs;
   - the synthetic drift book on Deriv.
   A low-correlation mix lifts consistency more than raising any single strategy's win rate.

## NQ Precision Bot

This template has a 0.30 ATR target against a 2.2 ATR stop: a 7:1 adverse ratio. It trades once a day at 09:59 ET and closes after 8 minutes. To break even it needs to win **88%** of trades before costs.

**Results** (NDX100, 1-minute bars, so bar ambiguity is not an issue here):
- **Zero cost: 357 trades, 87.4% win, +0.015R, PF 1.15, t +0.86.** It sits right at break-even.
- **Real costs: 240 trades, 85.4% win, −0.006R, PF 0.95.**
  - 123 of the 363 signals could not be placed: with a 0.3 ATR target, the 1-minute spread and the next-bar fill often land beyond the target.
  - Of the trades that did fill, the spread took the rest of the edge.

| Year | Trades | Win rate | Mean R |
|---|---|---|---|
| 2023 | 71 | 91.6% | +0.06 |
| 2024 | 96 | 87.5% | +0.03 |
| 2025 | 97 | 92.8% | +0.06 |
| **2026** | 93 | **78.5%** | **−0.09** |

At 87% win the 0.136R winners only just pay for the 1R losers, and in 2026 the win rate dropped nine points. The claimed 94% / PF 2.48 is not reproduced.

**Verdict: no.** The risk profile is the worst of the five. Each stop erases about seven winners, and the edge is smaller than one spread.
