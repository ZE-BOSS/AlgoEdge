# roboquant.dev templates: re-test and nine new ports, 2026-10-20

This supersedes the 2026-10-19 note on three points:
- the period each template was tested on;
- why NQ Precision and NY ORB don't replicate;
- Crazy ORB, which does.

## What changed in the method

roboquant publishes each template's whole backtest, not just a summary: source, showcase parameters, run config and **every trade**. It comes from `/api/templates/{id}/backtest` and is saved for all 25 templates in `source/templates_dump.json`, with the Rust sources in `source/rs/`. This allowed three checks that weren't possible before:

1. **The right window.** Every template is tested on its own date range, on **CME futures tick data** (NQ/MNQ/ES/MES), with 0.005% slippage and $0.50–2.50 per contract commission. Two examples:
   - Crazy ORB's published result covers **Sep 2025 → Sep 2026 only**.
   - NQ Precision covers Oct 2023 → Oct 2026.
   On 10-19 I had tested everything from 2022.
2. **Trade by trade.** `compare_trades.py` and `compare_entries.py` match our entries to theirs: same day, same direction, entry within one bar.
3. **How long their winners last.** Each published trade has entry and exit timestamps to the millisecond.

The app also got two fixes that these strategies exposed:
- **A bar that touches both the stop and the target** was always booked as a stop (a conservative guess). It is now settled from one-minute bars (`backend/backtester/intrabar.py`), in both backtesters. The backtest route turns this on automatically when a strategy's target is smaller than its stop, or on request with `intrabar_resolution`.
- **Strategy exits were being dropped in backtests.** Both backtesters pass a position's timeframe, which positions don't carry, so the new engines ignored every trailing stop and every strategy exit. This is fixed, with a regression test.

## The three that "didn't replicate", re-tested

| Template | Our entries match theirs | Their win rate → app (real costs, their window) | Why |
|---|---|---|---|
| **Crazy ORB** (NQ 15m) | **81%** of their trades | 81.3% → **79.7%**, PF 1.57 → **1.34**, t +2.6, +33% at 1% risk, DD 7.9% | **Replicates.** The earlier failure came from testing 4 years. The showcase is the best 12 months: 2023 was −0.04R, 2024 +0.02R, 2025 +0.01R. |
| **NQ Precision Bot** (NQ 1m) | **72%**; on the 226 identical trades the outcome agrees 92.5% | 94.0% → 86.5%, PF 2.48 → 1.03 (break-even) | Logic identical. **72% of their trades close within 10 seconds of entry, all of them winners. Their trades lasting over 10 s average −0.05R.** |
| **NY Open Range Breakout** (MNQ 15m) | **67%**; outcome agrees 85.6% on the 616 identical trades | 74.0% → 67.7%, PF 1.22 → 0.80 | Same cause: **51% of their trades close within 10 s, and trades lasting over 10 s average −0.10R.** |

**What was missing.** It wasn't a parameter or the timeframe: both match, and our stop distances are within 2% of theirs. NQ Precision's and NY ORB's whole published edge sits in the first seconds after the entry tick, on futures tick data:
- The target is 0.14–0.4× the stop, a few points.
- A tick-level fill model books the target whenever price touches it in the first second.

A bot that trades CFD bars can't capture that. Its order reaches the broker a few seconds into the bar: the live loop wakes 2 s after the bar opens, then scans. By then most of those trades have already closed at roboquant. A futures trader running on ticks with sub-second execution might capture some of it, but that isn't this account. **Don't trade NQ Precision or NY ORB here.**

## Nine new templates, ported line for line

They were chosen from roboquant's 25 for profit factor ≥1.2 and positive results in most years of their own trade list. Each is a direct port of the Rust `on_bar` through `strategy_roboquant/replay.py`:
- the template's state is replayed from a fixed anchor, so a backtest and live give **identical** signals (24 parity tests);
- a simulated position stands in for `ctx.position()`.

Every default is the showcase preset. The app results below use real FundedNext costs, each template's own start date, and minute-by-minute settling of ambiguous bars.

| Strategy (app id) | Entries match theirs | Claim (win / PF) | App: trades, win, R/trade, PF, t, return @1%, max DD | Verdict |
|---|---|---|---|---|
| **Flash Trigger ORB** (`RQ_FlashTriggerORB_v1`, M15) | 74% | 61.6% / 1.36 | 779, 50.8%, +0.074R, **1.15**, 1.64, **+67%**, 16% | **Trade it.** Positive 2024–26. The lower win rate is the tick-level trail approximated on bars. |
| **Sigma Breakout** (`RQ_SigmaBreakout_v1`, M30) | 69% | 55.3% / 1.39 | 799, 51.6%, +0.025R*, **1.20**, 1.86, +21%, **8%** | **Trade it.** Steady, low DD. *R is measured on a 2% disaster stop, so at 1× notional the return roughly doubles. |
| **Giga ORB** (`RQ_GigaORB_v1`, M15) | 64% | 55.9% / 1.22 | 680, 53.4%, +0.051R, 1.10, 1.28, +36%, 16% | Marginal. Positive, but 2023 and 2026 are flat. |
| **Scarface ORB** (`RQ_ScarfaceORB_v1`, M1) | 48% (92% same direction on shared days) | 41.5% / 1.35 | 459, 36.6%, +0.074R, 1.11, 1.08, +34%, 23% | Marginal. Positive in 3 of 4 years, but the DD is high for the edge. |
| **Friday Range Revisit** (`RQ_FridayRevisit_v1`, M5) | 57% | 22.6% / 2.96 | 34, 26.5%, **+0.60R**, 1.79, 1.05, +20%, 6% | Watch list. Large edge per trade, but only ~9 trades a year, so 34 trades can't confirm it. |
| **IVB** (`RQ_IVB_v1`, M5) | 18% | 52.0% / 1.37 | 371, 44.2%, +0.022R, 1.04, 0.36 | No (on CFD data). |
| **M30 ORB + M5** (`RQ_M30ORB_v1`, M5) | 36% | 47.6% / 1.22 | 662, 45.5%, −0.003R, 0.99 | No (on CFD data). |
| **Daily Anchored VWAP** (`RQ_DailyVWAP_v1`, 3m from M1, SPX500) | 24% | 56.6% / 1.84 | 327, 56.6%, 0.000R, 1.00 | No (on CFD data). |
| **S&R Sweep** (`RQ_SRSweep_v1`, M5) | 50% | 52.1% / 1.25 | 938, 53.8%, +0.052R, 1.11, 1.55, **+55%**, 14% | **Trade it.** Positive EVERY year 2023-26 (+0.04 to +0.06R), trades round the clock, so it is less tied to the NY open than the ORBs. |

**Why IVB, M30 ORB and DAVWAP match so few entries.** All three filter on **volume**: IVB needs a volume surge, M30 ORB needs 1.4× relative volume, and DAVWAP weights its VWAP by volume. Upstream that is CME exchange volume; on MT5 it is the CFD's tick volume.
- Two-thirds of roboquant's M30 ORB and IVB entries happen between 10:00 and 11:00, when futures volume surges after the opening range.
- Ours spread across the afternoon.
- On shared days the direction still agrees 79–84%, so the logic is right. The data feeding the filter is different.

These three should not be run on CFD tick volume as they stand.

**Not ported: NY ORB Retest (PDH/PDL).** It enters with a resting limit order at the broken level. The app's backtester and order manager place market orders only, and a market fill at the next open is a different strategy. Porting it means adding pending-order support to both.

## How they trade, and the risks

- **Markets:** all NQ templates run on US Tech 100 / NDX100; the ES/MES ones on US SP 500 / SPX500.
- **Sizing:** the app sizes from the stop (risk % of balance), not from roboquant's fixed contracts. Results are compared in R and win rate for that reason.
- **Target smaller than the stop** (Crazy ORB 0.35R, NY ORB 0.4R, NQ Precision 0.14R):
  - A 1R loss erases 3–7 wins.
  - Break-even needs a 74–88% win rate.
  - A one-point cost or a few seconds of entry delay decides the result.
  - Crazy ORB survives only because its stop is 1 ATR (about 40 pts), so the ~1.2-pt spread is 0.03R.
- **Overnight and weekend holds:**
  - Flash Trigger ORB and Giga ORB never flatten (upstream as-is), so trades can run into the next day.
  - T-Mean Reversion holds over every weekend.
- **Correlation:** nearly everything here is the same index (NDX) on the same morning session. Running five ORBs on US Tech 100 is closer to one bet at 5× size than to five strategies. Cap the combined exposure.

## What to run (best first)

1. **T-Mean Reversion, NDX100 H1.** 97% of their entries match. 63.7% win, PF 2.44, t +3.2. Size it at 2–5% risk on its 5% disaster stop.
2. **Crazy ORB, NDX100 M15.** 79.7% win, PF 1.34, t +2.6 over the last 12 months. Run it at a reduced size: over 4 years it was near break-even, so treat it as a regime that is working now, not a permanent edge.
3. **Flash Trigger ORB, NDX100 M15.** +67% at 1% risk since Sep 2023, PF 1.15.
4. **Sigma Breakout, NDX100 M30.** PF 1.20, max DD 8%, and a different exit style: band flip, flat at the close.
5. **S&R Sweep, NDX100 M5.** The only port positive in every year (+55% at 1% risk). It trades 24 hours a day, so it is the least correlated with the morning ORBs.
6. **Charles Monger, NDX100 H1.** A trend leg at 0.5–1% risk.

Treat 1–6 as one NDX100 book: total open risk across them at most about 2–3%. Watch-list (paper or minimum size): Friday Revisit, Giga, Scarface.

## Files

- `source/templates_dump.json`, `source/rs/*.rs`: what roboquant publishes.
- `compare_trades.py`, `compare_entries.py`, `results/entries_NDX100.json`: the trade-level checks.
- `scripts/run_roboquant_replication.py --template-window`: the app backtests; results are in `results/RQ_*.json`.
- `backend/strategies/strategy_roboquant/{replay,engine_more}.py`, `backend/backtester/intrabar.py`.
- Tests: `tests/test_roboquant_ports.py`, `tests/test_intrabar_resolution.py`.
