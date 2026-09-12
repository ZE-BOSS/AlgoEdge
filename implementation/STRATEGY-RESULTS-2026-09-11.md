# Strategy results — 11 Sep 2026

## The short answer

- **Trade this:** **ORB_v1 (opening-range breakout) on GBPJPY**, London open, 60-minute range, 1:3 target, 1% risk.
  Its settings were picked using only Jan 2024 – Jan 2026 data. It then made money in **all three** periods:
  2022–23 ($350 → $397), 2024–25 ($350 → $474) and the **last 8 months ($350 → $476, 8 of 9 months up, max drawdown 7%)**.
  It is live-ready on your profile now.
- **XAUUSD, US Tech 100, US SP 500:** nothing I tested made steady money over the last 8 months with settings chosen beforehand.
  Gold's ORB was slightly positive in every period, but at $350 the broker's smallest lot risks ~8% per trade. It needs about **$3,000** to trade at 1%.
- **BTCUSD:** ORB (New York open, 60-minute range, 1:1.5) was profitable in all three periods. It needs about **$800** to trade at 1%.
- **LLM:** don't use one to call market direction. The Claude Haiku test on data it could not have seen **lost −0.12R per trade**, and its probabilities were worse than guessing the base rate. Cost: $1.58.
- **Deleted** 9 strategies. **Kept:** APA, VWAP, DriftJumpAlpha (Crash), BoomDriftJump (Boom). **Added:** ORB_v1.

How every number here was made:
1. Settings were chosen on 22 Jan 2024 – 10 Jan 2026.
2. The out-of-sample results cover **10 Jan – 11 Sep 2026** with those settings unchanged. They start at $350, risk 1%, and charge the spread on every trade.
3. Where MT5 has the data (gold, FX, BTC), the same settings were re-run on 5 Sep 2022 – 22 Jan 2024, a period the search never saw.
4. Stop slippage beyond the spread is not modelled, so expect slightly less.

---

## Your three markets — every strategy family, last 8 months ($350 start)

| Strategy family | XAUUSD | US Tech 100 | US SP 500 |
|---|---|---|---|
| Opening-range breakout (session close) | **$481** | **$471** | $349 |
| Opening-range breakout (fixed R:R) | $370 | $306 | $349 |
| VWAP pullback (session VWAP, tick-volume weighted) | $354 | $392 (32% drawdown) | $294 |
| Breakout + tick-volume surge (order-flow proxy) | $353 | $354 | $379 |
| Donchian trend breakout | $376 | $359 | $314 |
| EMA trend pullback (incl. 1:5 / 1:7 / 1:10 targets) | $352 | $341 | $373 |
| RSI(2) mean reversion | $359 | $343 | $364 |
| Bollinger band fade | $315 | $341 | $304 |
| Daily time-series momentum | $348 | $348 | $354 |
| **Tradable at $350 / 1%?** | **No** (min lot = 5–8% risk) | **No** (min lot = 2–3% risk) | **Mostly** (58% of trades) |

- The two bold ORB results on gold and US Tech 100 **did not repeat**. Gold's version lost money in 2022–23 ($350 → $289). US Tech 100's version lost money once the session-close exit was replaced by fixed targets.
- None of these clears a strict statistical bar on its own.
- US SP 500 had one lead worth forward-testing: ORB on the New York open, held to its target instead of closed at the session end. It made +0.13R per trade over the last 8 months and +0.06R in 2024–25.

## All eight markets — the best candidate each

| Market | Best candidate | Last 8 months | Worked 2022–23 too? | Money needed for 1% risk |
|---|---|---|---|---|
| **GBPJPY** | **ORB London 60m 1:3** | **$476, 8/9 months up** | **Yes ($397)** | **~$210 — fits $350** |
| BTCUSD | ORB New York 60m 1:1.5 | $382 | Yes ($524) | ~$800 |
| XAUUSD | ORB New York 30m 1:2 | $370 | Yes ($442) | ~$3,000 |
| EURUSD | ORB London 30m, session close | $426 | No ($271) | ~$150 |
| US SP 500 | ORB New York 30m | $349 (flat) | no MT5 data | ~$325 |
| US Tech 100 | ORB London 30m, session close | $471 | no MT5 data | ~$900 |
| XAGUSD | Donchian breakout | $374 | not checked | ~$3,000 |
| XPTUSD | nothing profitable | — | — | — |

Full numbers: `data/strategy_search/search_20260911_124240.json` (rerun: `python scripts/run_strategy_search.py`).

---

## Order book, order flow, gamma, VWAP, volume profile

- **VWAP:** session-anchored and weighted by tick volume, tested on all 8 markets. Positive only on US Tech 100 (+$42 with a 32% drawdown). It lost on US SP 500, EURUSD and BTCUSD.
- **Order flow:** MT5 keeps no buy/sell traded-volume history for CFDs, only tick counts. The closest honest test was "breakout confirmed by a tick-volume surge". It gave small gains on gold, US SP 500 and silver, but not steadily.
- **Volume profile:** tested in the earlier regime study (price position vs. the value area). It added nothing.
- **Order book (DOM) and gamma:** **no history exists** on MT5 or Deriv, so neither can be backtested.
  - The only honest route is to start recording order-book snapshots now and test after 3–6 months.
  - Gamma for the S&P 500 and Nasdaq also needs paid options open-interest data (CBOE or Polygon). See TODO.

---

## The 11 research papers — what each says, and what I took from it

| File | What it says | What I used |
|---|---|---|
| 5351012 | Probabilistic toolkit: Kelly sizing, Bayesian updating of confidence, decision trees, regime (HMM) switching, stacking weak signals. | Size by evidence: start ORB at 1% (or 0.5%) and raise only as live results confirm. |
| 6354961 | "Apex Quant": three LLM agents debate trades on US and China stocks (2025–26). Debate steadies decisions, but persona rhetoric biases them; no audited returns. | Not a strategy; a warning about LLM debate systems. |
| 6417099 | Since LLMs appeared, 46–61% of post-news price drift is absorbed by the next day. News-sentiment trading earned ~0.2% the next day, over 2× more during LLM outages. | The LLM edge is **reading news fast**, and it is being competed away. It is not chart prediction. |
| 6533018 | LLM market makers: a maker only profits if the spread beats twice the adverse selection. LLM self-checks understate their true error (0.03 measured vs. up to 0.44 real). | Don't trust an LLM's own confidence. |
| 6687518 | Multi-agent LLM forecasting fails 41–87% of tasks. The best LLM scored Brier 0.122 vs. superforecasters' 0.096. Anchoring on market prices gave negative returns. | Designed the Haiku test with blinding and post-cutoff data. The result matched the paper. |
| 7110758 | Favourite–longshot bias: long-shot bets are overpriced and win only ~4%. | Explains APA at 1:7 and 1:10: far targets are long-shot bets that hit less often than they appear to. |
| 7283081 | History of quant finance: trend following, stat-arb, and the main machine-learning traps (leakage, testing too many ideas). | The walk-forward design here: pick on old data, judge on new, penalise many tries. |
| 7398280 | Does an LLM's published forecast move the market? No measurable effect (p = 0.90). | Nothing actionable. |
| ade14ab9 | LLM judges catch 94–99% of reasoning defects, yet pick the eventually-correct argument only ~50% of the time. Fluent writing persuades them. | A convincing LLM trade explanation is not evidence that the trade is right. |
| ssrn_2047846 | (2011) Prediction markets that use Twitter networks aggregate information better. | Nothing actionable for CFDs. |
| ssrn_4714721 | Rebalancing with trading costs: no-trade bands beat calendar rebalancing, which pays in mean-reverting markets. Portfolio insurance is momentum. | Cost-aware "don't trade small signals": ORB takes one trade per session only. |

**Is an LLM engine better for market analysis?** No, not for deciding direction. The papers (6417099, 6687518, ade14ab9) and our own test agree. The one role worth testing is a cheap Haiku **news-day filter** (skip ORB on high-impact news days). See TODO.

---

## Why APA at 1:5 / 1:7 / 1:10 isn't tradable yet, and what would change that

- **Win rate needed just to break even (before spread):** 1:5 → 16.7%, 1:7 → 12.5%, 1:10 → 9.1%.
- **Losing streaks:** at a ~10% win rate, a streak of 20 losses is more likely than not within 200 trades. At 1% risk that is an 18% drawdown before the next win.
- **Your saved 2026 results:** APA lost money on **16 of 19** markets.
- **The rest:** see the TODO list below. VWAP made money on 3 of 19 saved runs (Volatility 75, USDCHF, EURUSD) and gets the same treatment.

---

## What changed in the code

- **Deleted:** CRT_v1, HTFFVGFlip_v1, BiasIFVG_v1, NYOpenRetest_v1, SpikeFade_v1, SpikeRide_v1, RangeRevert_v1, RangeBreakout_v1, TrendDrift_v1.
  - The committed versions are recoverable from git.
  - SpikeRide was never committed; a copy of its folder is in this session's scratchpad (`deleted_strategies/`).
  - They are gone from the registry, config, Settings, Backtester, Strategy Lab, defaults and tests.
- **Safety:** a saved slot on the VPS that still names a deleted strategy is now **skipped with one warning**. Before, it logged a "data fetch error" every scan.
- **Added ORB_v1:**
  - Strategy code: `backend/strategies/strategy_orb/`.
  - Per-market settings (GBPJPY London 60m 1:3, BTCUSD NY 60m 1:1.5, XAUUSD NY 30m 1:2) are applied automatically.
  - The session close is enforced in the backtester and live (`position_manager._check_orb_session_close`).
  - Parameters are on the Settings page and in the Backtester.
  - `tests/test_orb_strategy.py` proves the live engine fires on exactly the bars, directions and stops that were measured.
- **Research tools:** `backend/analytics/strategy_search.py` and `scripts/run_strategy_search.py`.

---

## TODO

**Go live with ORB**
- [ ] Deploy to the VPS (git is local-only: copy or pull the way you normally do). Restart the backend and rebuild the frontend.
- [ ] Settings → slots: delete slots that use removed strategies, then add **ORB_v1 on GBPJPY**.
- [ ] Run ORB_v1 / GBPJPY in the app Backtester (last 8 months, $350, 1%, stop fill model **CONSERVATIVE**). It should land near +0.2R per trade (~$470).
- [ ] Demo forward-test for 4–6 weeks at 1%. Pause if drawdown passes 15%, or if 30 trades average below 0R.
- [ ] Add BTCUSD ORB at ~$800 balance and XAUUSD ORB at ~$3,000.

**Make APA tradable**
- [ ] Keep APA only on its measured markets: Volatility 75 (1:4), XRPUSD (1:5), Crash 500 (1:3). Disable it elsewhere.
- [ ] Test **scale-out** against fixed 1:5 / 1:7 / 1:10: take 1/3 at 2R, move the stop to entry, and run the rest to 5–10R. Choose on 2024–25 and judge on the last 8 months.
- [ ] Test a higher-timeframe exhaustion filter: only take reversals after an extended move.
- [ ] Trade at 0.5% risk until 50 live trades confirm.

**Make VWAP tradable**
- [ ] Re-test its measured slots (Volatility 75 1:5, Germany 40 1:4, XAGUSD 1:4) on the last 8 months with the fill model. Disable any that fail.
- [ ] Remove its XAUUSD, US SP 500, EURUSD and BTCUSD slots; all four lost money in the tick-volume VWAP test.

**Tradability, all strategies**
- [ ] Settings warning: flag any slot where the minimum lot risks more than the risk budget. The sizer already refuses those trades silently.
- [ ] The portfolio backtester never calls the strategy's per-bar exit hook, so its ORB runs lack the session close. Single-symbol runs are correct.

**Data you asked about**
- [ ] Start logging MT5 order-book snapshots for GBPJPY, XAUUSD, US Tech 100 and US SP 500. Re-test order-book signals after 3–6 months.
- [ ] Gamma: decide whether to buy S&P 500 / Nasdaq options open-interest data. Without it, gamma can't be tested.

**LLM (cost-efficient, Anthropic only)**
- [ ] Test a Haiku news-day filter on ORB, using post-cutoff data only (<$2). Ship it only if it beats "no filter".

**Kept as asked**
- [ ] DriftJumpAlpha (Crash) and BoomDriftJump (Boom) are unchanged. Keep sizes small: research/24 measured these instruments as having no memory.
