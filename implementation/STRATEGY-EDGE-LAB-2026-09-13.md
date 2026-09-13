# Strategy edge lab: VWAP and price action from the research, tested and wired in (13 Sep 2026)

## 1. The short answer

The main markets are US Tech 100, XAUUSD, BTCUSD and GBPJPY. Everything below was chosen **only** on 2022–2025 data and then run unchanged on **1 Jan – 12 Sep 2026** (8 months nothing was picked on).

| What to trade | Where | Last 8 months, $10,000 at 1% (fixed risk) | Max DD | PF | Sharpe | Sortino | Evidence |
|---|---|---|---|---|---|---|---|
| **ORB_v1, M5 break of the 60-min range with the H1 trend, 1:3** (Crabel price action) | GBPJPY | **+$1,919 (+19.2%)**, 7/9 months up | 5.3% | 1.46 | 1.85 | 3.76 | Strongest: positive in all 3 windows on all 4 markets |
| same, one setting | XAUUSD | +$494 (+4.9%) | 6.4% | 1.25 | 0.98 | 1.68 | same |
| same | US Tech 100 | +$356 (+3.6%) | 4.6% | 1.17 | 0.80 | 1.22 | same |
| same | BTCUSD | +$76 (+0.8%) | 4.8% | 1.03 | 0.17 | 0.28 | same (flat in 2026) |
| **VWAP_v1 SESSION_TREND** (Zarattini & Aziz VWAP cross): day direction + gap direction + first 2 hours, 1:5 | XAUUSD | **+$2,864 (+28.6%)** | 8.8% | 1.83 | 2.27 | 6.68 | Positive in all 3 windows, but picked on this market alone |
| **VWAP_v1 SESSION_TREND**: day direction + opening volume + first 2 hours, 1:10 | US Tech 100 | **+$2,285 (+22.9%)** | 4.4% | 1.95 | 1.91 | 6.30 | Weaker: index data starts 2024, so there is no 2022–23 check |
| **VWAP_v1 SESSION_PULLBACK**: gap direction + first 2 hours, 1:5 | BTCUSD | **+$2,998 (+30.0%)** | 9.8% | 1.44 | 1.68 | 4.78 | Positive in all 3 windows, picked on this market alone |
| **All seven books together** | 4 markets | **+$9,016 (+90.2%)**, 8/9 months up | 10.6% | 1.47 | 3.34 | 8.37 | Reshuffled 300×: 5th percentile +46%, 0% chance of loss |

**What did not work:**
- **Pooled across markets, VWAP does not repeat.** Every VWAP variant chosen on all four markets at once fell to about zero or below in 2026. The VWAP results above hold **per market only**, which carries more selection risk (see §5).
- **The APA head-and-shoulders setup still has no edge.** APA can now trade the price-action setup that does work (the ORB-trend break) through a new `setup_mode`.
- **Five of the other strategy families lost or were flat:** RSI(2), Bollinger fade, TSMOM, the liquidity sweep (fails 2022–23) and "last half-hour momentum" (a fraction of an R).
- **Not every earlier family survived per market.** Donchian, EMA pullback, tick-volume breakout and the session-close ORB each held on some markets and failed on others. §6 has the full table.
- **On $350, most books cannot be sized.** The broker's minimum lot on US Tech 100, XAUUSD and mostly BTCUSD risks more than 1% of $350. **GBPJPY is the one that fits** (§7).

---

## 2. What the research says, and what was built from it

| Paper | What it claims | What I built |
|---|---|---|
| Zarattini & Aziz (2023), *VWAP: The Holy Grail for Day Trading Systems*, [SSRN 4631351](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4631351) | Long above session VWAP, short below, on QQQ 2018–23: 671% return, 9.4% max DD, Sharpe 2.1 | `vwap_trend`: trade the M5 close that crosses session VWAP; exit on the cross back, a fixed R:R, or the close |
| Zarattini, Aziz & Barbon (2024), *Beat the Market: An Effective Intraday Momentum Strategy for SPY*, [SSRN 4824172](https://ssrn.com/abstract=4824172) | Noise area = average move from the open at that time of day over 14 days. Trade breaks on the half hour, trail at max(boundary, VWAP): Sharpe 1.33, 19.6%/yr | `noise_mom`: the same boundary, half-hour checks and trail |
| Zarattini, Barbon & Aziz (2024), *A Profitable Day Trading Strategy for the U.S. Equity Market*, [SSRN 4729284](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4729284) | Trade in the direction of the first 5-min candle, stop 10% of ATR, 10R or the close. Relative volume does the selecting: −0.02R below 100%, +0.08R above | `orb_candle`: 5/15/30-min candle, ATR or range stop, relative-volume confluences |
| Gao, Han, Li & Zhou (2018), *Market Intraday Momentum*, [SSRN 2552752](https://papers.ssrn.com/sol3/Delivery.cfm/SSRN_ID2585766_code16976.pdf?abstractid=2552752&mirid=1) | The first half hour predicts the last half hour. Seen in stocks, Bitcoin, gold and oil ETFs, stronger on volatile days | `last_half`: enter 30 min before the close in the first half hour's direction |
| Crabel (1990), *Day Trading with Short-Term Price Patterns and Opening Range Breakout* | The opening-range break works best after narrow (NR7) or inside days | `orb_break`: first M5 close beyond the 15/30/60-min range, with NR7, inside-day and trend confluences |
| Connors & Raschke (1995), "Turtle Soup" / the price-action liquidity sweep | Fade a failed break of the prior high or low | `pdhl_sweep`: price trades through the prior session's high/low and closes back inside |
| Lo, Mamaysky & Wang (2000); Savin et al. on head and shoulders | Chart patterns carry some information, but stand-alone H&S trading is not profitable after costs | Matches APA's result on 26 markets; not rebuilt |
| Maróy (2025), *Improvements to Intraday Momentum Strategies*, [SSRN 5095349](https://papers.ssrn.com/sol3/Delivery.cfm/5095349.pdf?abstractid=5095349&mirid=1) | Parameter tuning and VWAP-based exits lift the noise-area strategy's Sharpe above 3 | Tested as the "trail" exit variant (the PDF was not readable here) |
| Li, Sakkas & Urquhart, *Intraday Time Series Momentum: Global Evidence* ([SSRN 3460965](https://papers.ssrn.com/sol3/Delivery.cfm/SSRN_ID3762700_code2938409.pdf?abstractid=3460965&mirid=1)) | Intraday momentum is significant in 16 markets, stronger when volatile | Became the `high_vol` confluence |

**Every confluence each family was tested with (14):**
- **Trend and direction:**
  - `htf_trend`: close on the same side of a 600-bar M5 EMA (~50 H1 bars), with the EMA sloping that way.
  - `day_dir`: price on the trade's side of the session open.
  - `gap_dir`: the open gapped the trade's way from the prior close.
  - `prev_day_dir`: the prior session closed the trade's way.
- **VWAP:**
  - `vwap_side`: price on the trade's side of VWAP.
  - `vwap_slope`: VWAP rising or falling over the last 30 min.
  - `noise_out`: price outside the noise area.
- **Volume and volatility:**
  - `vol_surge`: tick volume at least 1.5× its 20-bar average, the only order-flow history MT5 keeps.
  - `rel_vol_open`: opening-range volume at least the 14-day average (Zarattini's "stocks in play").
  - `high_vol`: 14-day session range above its 60-day median.
- **Range patterns:**
  - `nr7`: the prior session was the narrowest of 7.
  - `inside_day`: the prior session sat inside the one before it.
- **Timing and candle:**
  - `early`: within the first 2 hours.
  - `strong_body`: body at least 60% of the bar.

Two setup-specific extras: `wick_reject` for the sweep, and `big_first` for the last half hour.

---

## 3. How it was tested

- **Data:** MT5 M5 bars, Oct 2021 – Sep 2026. Indices start 2024-01-22 because that is all MT5 has.
- **Sessions (DST-aware):** New York 09:30–16:00 ET for US Tech 100, XAUUSD and BTCUSD; London 08:00–16:30 UK for GBPJPY. The other session was also tested for every family.
- **Candidates:** every family records every candidate with all confluences **as booleans, not enforced**, and scores every exit on the same candidate:
  - targets 1:1, 1:1.5, 1:2, 1:3, 1:5 and 1:10;
  - hold to the session close;
  - trail (VWAP or noise boundary).
- **Combinations:** each is a filter on one candidate list, so every combination is exact, not an approximation:
  - 2 sessions × up to 12 setup variants × every subset of up to 3 of 10 confluences × 8 exits;
  - 1,888–9,744 combinations per family.
- **Honest trade rules:**
  - one trade per session (the first candidate that passes), so trades never overlap;
  - entry at the next bar's open;
  - the bar's spread charged on every trade, plus half a spread of slippage on stops;
  - a bar touching both stop and target counts as a loss;
  - stops under 4× spread are floored, and stops under 2% of the daily range are discarded.
- **Three windows:**
  - **2022–23:** holdout (not available for indices);
  - **2024–25:** selection;
  - **1 Jan – 12 Sep 2026:** the last 8 months, never used to choose anything.
- **Two selection rules:**
  1. Chosen on 2024–25, pooled across the four markets.
  2. **Robust:** must be positive pooled in 2022–23 **and** 2024–25, in all markets but one, ranked by the weaker of the two t-stats. This is what was shipped.
- **Money:** `backend/analytics/money_sim.py` on the broker's real lot grid.
  - $350 and $10,000, at 1% and 1.8% risk, fixed and compounding.
  - 10% daily loss cap.
  - A trade whose minimum lot risks more than the budget is **refused**, not shrunk.
  - Sharpe and Sortino are new: annualised from **daily** returns, flat days counted as 0%.

---

## 4. ORB with the trend: the price-action edge

One setting on all four markets: **M5 close beyond the 60-minute opening range, only with the H1 trend, stop at the far side of the range, 1:3, flat at the session close.**

| Market | 2022–23 (unseen) | 2024–25 (selection) | 2026 (unseen) |
|---|---|---|---|
| US Tech 100 | — | 247 trades, +0.044R, PF 1.16 | 106, **+0.045R**, PF 1.20 |
| XAUUSD | 255, +0.054R, PF 1.22 | 243, +0.070R, PF 1.30 | 85, **+0.108R**, PF 1.38 |
| BTCUSD | 256, +0.246R, PF 1.71 | 287, +0.112R, PF 1.38 | 94, **+0.009R**, PF 1.03 |
| GBPJPY | 268, +0.092R, PF 1.20 | 255, +0.120R, PF 1.27 | 86, **+0.225R**, PF 1.46 |
| **Pooled** | **779, +0.130R, t 3.3** | **1,032, +0.088R, t 3.0** | **371, +0.092R, PF 1.29, t 1.9** |

- **The trend filter is what makes it work.** It helped on 4 of 4 markets in both the selection window and 2026 (+0.036R and +0.026R).
- **Other confluences:**
  - `inside_day` hurt: −0.237R in 2026.
  - `rel_vol_open` hurt in both windows.
  - `nr7`, Crabel's own filter, added nothing (+0.013R, then −0.008R).
- **13 other markets, same setting:**
  - pooled positive in every window, but small: +0.038R, +0.029R, +0.013R;
  - 1:2 instead of 1:3 was positive on 13 of 17 markets in 2026;
  - it carries best to XAGUSD, EURJPY, GBPUSD, Germany 40, US SP 500 and Japan 225;
  - it does not work on USDCAD, AUDUSD or Wall Street 30.

**Money, last 8 months:**

| Account | US Tech 100 | XAUUSD | BTCUSD | GBPJPY | All four |
|---|---|---|---|---|---|
| $350, 1%, fixed | can't size (0 of 106) | can't size (0 of 85) | can't size (0 of 94) | **+$35 (+10.1%)**, DD 4.7%, Sharpe 1.33 | +$35 (GBPJPY only) |
| $350, 1.8%, fixed | can't size | can't size | +$10 (20 of 94 sized) | **+$105 (+30.1%)**, DD 7.4%, PF 1.48, Sharpe 1.93 | +$115 (+33.0%), DD 7.4% |
| $10k, 1%, fixed | +$356, DD 4.6% | +$494, DD 6.4% | +$76, DD 4.8% | +$1,919, DD 5.3% | **+$2,845 (+28.4%)**, DD 10.7%, PF 1.26, Sharpe 2.05, Sortino 3.64 |
| $10k, 1.8%, compounding | +$745, DD 9.6% | +$1,371, DD 11.9% | +$68, DD 8.8% | +$3,756, DD 10.4% | **+$6,902 (+69.0%)**, DD 23.3%, Sharpe 2.16 |

---

## 5. VWAP: what the research variants did

**Pooled (one setting on all four markets):** no VWAP variant survived 2026.

| Variant (robust pick) | 2022–23 | 2024–25 | 2026 |
|---|---|---|---|
| VWAP trend + `htf_trend`, 1:5 | +0.177R | +0.133R | **−0.034R** |
| Noise-area momentum + trend + early, 1:3 | +0.163R | +0.141R | **−0.182R** |
| VWAP pullback + day direction, hold to close | +0.242R | +0.166R | **−0.072R** |

**Across all 17 markets,** the best shared VWAP setting (pullback, day + gap direction + early, hold to close) stayed positive in every window, but thinly: +0.108R, +0.101R, **+0.026R**.

**Per market (robust rule on one market), where VWAP does hold:**

| Market | Setting | 2022–23 | 2024–25 | 2026 | Grade |
|---|---|---|---|---|---|
| **XAUUSD** | SESSION_TREND: `day_dir` + `gap_dir` + `early`, 1:5 | 191, +0.257R | 182, +0.356R | 67, **+0.422R**, PF 1.70 | Held in 3 windows |
| **BTCUSD** | SESSION_PULLBACK: `gap_dir` + `early`, 1:5 | 346, +0.300R | 295, +0.372R | 114, **+0.130R** | Held in 3 windows |
| **US Tech 100** | SESSION_TREND: `day_dir` + `rel_vol_open` + `early`, 1:10 | — | 144, +0.585R | 49, **+0.389R** | Selection window + 2026 only |
| GBPJPY | SESSION_TREND: `day_dir` + `vol_surge` + `early`, 1:5 | 43, +0.856R | 29, +0.994R | 11, +0.418R | Too few trades; **not shipped** |

**Caveat:** each per-market pick was chosen from ~2,500 combinations on one market.
- Being positive in the 2026 window it never saw is real evidence.
- It is still weaker evidence than the shared ORB setting, which had to work on four markets at once.
- Trade these at the lower risk (1%) until live results confirm them.

**The confluence pattern that repeats:** with the VWAP trend, **`early` (the first 2 hours) and `day_dir`** are in the winning set on XAUUSD, US Tech 100 and GBPJPY. `vwap_slope` was the most harmful gate on the trend variant (−0.15R to −0.24R).

**Money, last 8 months** (compounding is similar; everything is in `data/edge_lab/final_book.json`):

| Book | $350, 1% | $350, 1.8% | $10k, 1% fixed | $10k, 1.8% fixed |
|---|---|---|---|---|
| XAUUSD SESSION_TREND | can't size (0 of 67) | −$25, 4 trades sized | **+$2,864 (+28.6%)**, DD 8.8%, PF 1.83, exp +0.49R ($44.75), Sharpe 2.27, Sortino 6.68 | +$5,434 (+54.3%), DD 13.3% |
| US Tech 100 SESSION_TREND | 1 of 49 sized | +$62 (+17.8%), 14 of 48 sized | **+$2,285 (+22.9%)**, DD 4.4%, PF 1.95, exp +0.51R, Sharpe 1.91, Sortino 6.30 | +$4,097 (+41.0%), DD 7.5% |
| BTCUSD SESSION_PULLBACK | **+$97 (+27.7%)**, DD 7.5%, PF 1.60 | +$155 (+44.2%), DD 11.6% | **+$2,998 (+30.0%)**, DD 9.8%, PF 1.44, exp +0.36R, Sharpe 1.68, Sortino 4.78 | +$2,826 (+28.3%), DD 14.4% |

---

## 6. The strategy families from the 11 Sep report: rebuilt in the backend and retested

**Selection:** settings chosen on 2024–25 per market, then run on 2022–23 and 2026. Rows marked *holds* were positive in every window that has data.

| Family (new strategy) | Market | Setting | 2022–23 | 2024–25 | 2026 | $10k at 1%, 2026 |
|---|---|---|---|---|---|---|
| ORB, session close (ORB_v1, M15) | GBPJPY | London 60m, hold to close | +0.060R | +0.079R | **+0.188R** | +$3,088 (+30.9%), DD 5.6%, PF 1.45, Sharpe 2.31, *holds* |
| | XAUUSD | London 30m, hold to close | +0.005R | +0.053R | **+0.184R** | +$3,131 (+31.3%), DD 12.9%, PF 1.31, *holds (thin)* |
| | US Tech 100 | London 30m, hold to close | — | +0.095R | **+0.175R** | +$3,264 (+32.6%), **DD 17.4%**, PF 1.29 |
| | BTCUSD | NY 60m, 1:1.5 | +0.076R | +0.065R | +0.051R | +$619 (+6.2%), DD 7.7%, *holds* |
| Donchian (**Donchian_v1**) | XAUUSD | 55-bar, long, channel exit | −0.002R | +0.630R | **+0.466R** | +$887 (+8.9%), DD 7.4%, PF 1.88 |
| | BTCUSD | 55-bar, long, channel exit | +0.629R | +0.254R | +0.083R | +$638, **DD 24.6%**, *holds* |
| | US Tech 100 | 55-bar, long, trail | — | +0.215R | +0.074R | +$798 (+8.0%), DD 5.3% |
| | GBPJPY | 20-bar, long, channel | +0.188R | +0.066R | −0.009R | +$39, fails 2026 |
| EMA trend pullback (**EMAPullback_v1**) | XAUUSD | EMA 50/200, long, **1:10** | +0.317R | +1.027R | **+0.382R** | +$998 (+10.0%), DD 11.7%, PF 1.55, *holds* |
| | BTCUSD | EMA 50/200, long, **1:10** | +0.079R | +0.485R | +0.198R | +$566 (+5.7%), DD 10.4%, *holds* |
| | US Tech 100, GBPJPY | 50/200 1:7; 20/50 1:7 | | | −0.13R / −0.12R | fails 2026 |
| Tick-volume breakout (**VolBreakout_v1**) | XAUUSD | 55-bar, 1.5× volume, long | −0.048R | +0.310R | +0.184R | +$527 (+5.3%), DD 5.7%, PF 1.67 |
| | BTCUSD | 20-bar, 2× volume | +0.462R | +0.279R | +3.67R (**10 trades**) | +$3,755, too few trades to believe |
| Session VWAP pullback (M15, strategy-search form) | US Tech 100 | London, hold to close | — | +0.058R | +0.097R | +$5,609, but 209 trades unsizable → covered by VWAP_v1 session modes |
| RSI(2) (**RSI2_v1**) | all four | <10, long, 24h | ±0.01R | ±0.03R | −0.03R to +0.03R | −$340 to +$53: **no edge** |
| Bollinger fade (**BollingerFade_v1**) | all | 2.0/2.5σ, long | | | −0.16R to 0.00R | **no edge** |
| Daily TSMOM (**TSMOM_v1**) | all | 20–60 days | | | 4–15 trades | **too few trades to judge** |

**Every family is now a live strategy on your backend.** RSI(2), Bollinger and TSMOM ship with evidence strings saying they have no edge.

---

## 7. $350 vs $10,000: why the small account can't trade most of this

At $350 and 1% the risk budget is $3.50. The broker's minimum lot on a sensible stop costs more than that on:

| Market | Min lot | Typical ORB/VWAP stop | Risk at min lot | Sizable at $350? |
|---|---|---|---|---|
| US Tech 100 | 0.1 | 40–80 pts | $4–8 (1.1–2.3%) | **No** at 1%; partly at 1.8% |
| XAUUSD | 0.01 | $5–10 | $5–10 (1.4–2.9%) | **No** |
| BTCUSD | 0.01 | $400–900 | $4–9 | Mostly no (VWAP pullback: 81 of 95 sized) |
| **GBPJPY** | 0.01 | 20–40 pips | ~$1.3–2.6 | **Yes** (79 of 86 at 1%) |

**What $350 can run now:**
- **ORB-trend on GBPJPY:** +$35 (+10.1%) at 1%, or +$105 (+30.1%) at 1.8%, DD 7.4%.
- **VWAP session pullback on BTCUSD:** +$97 (+27.7%) at 1%, DD 7.5%.

**The rest needs about $3,000–10,000.**

---

## 8. Compounding, pyramiding, and why 1.8% isn't always more

- **Compounding** raises the upside and the drawdown together.
  - $10k all-seven portfolio at 1%: fixed +90.2% with DD 10.6%; compounding +149.9% with DD 21.9%.
  - At 1.8%: fixed +130.7% with DD 10.8%; compounding +284.2% with DD 24.4%.
- **1.8% isn't always more money** because of the 10% daily loss cap and the lot grid. At 1.8% more trades are refused on busy days: 110 unsizable or blocked vs 45 at 1% in the portfolio. BTCUSD's VWAP book made *less* at 1.8% fixed (+28.3%) than at 1% (+30.0%).
- **Pyramiding** is not part of these books. None of the winning setups was measured with an add-on position, so none is claimed.
- **Monthly returns** ($10k, 1%, fixed, all seven books), with only July negative:

  | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep |
  |---|---|---|---|---|---|---|---|---|
  | +28.8% | +11.7% | +10.2% | +8.4% | +7.4% | +7.4% | **−7.9%** | +2.8% | +1.3% |

- **The 30–100%/month target:** nothing here does that without a drawdown that would end the account. The honest ceiling measured is **~10%/month on average at 1% fixed**, with a losing month of about −8%.

---

## 9. What changed in the code

**New research modules:**
- `backend/analytics/edge_lab.py` covers the seven research families, the 14 confluences, exact exit scoring, the robust two-window search, and the live form of the research (`live_signal`), so engines trade exactly what was measured.
- `money_sim.py` now reports **Sharpe and Sortino**.

**New strategies (`backend/strategies/strategy_classic/`):**
- Donchian_v1, EMAPullback_v1, RSI2_v1, BollingerFade_v1, VolBreakout_v1 and TSMOM_v1.
- Each engine calls the research builder itself.
- Their exits (ATR trail, channel, mean, flip, time) run through `on_position_bar`.

**Changes to the three existing strategies:**
- **ORB_v1:** `breakout_timeframe` ("M15" or "M5") and `require_trend`. Per-symbol defaults now point all four main markets at the M5 trend form, 1:3.
- **VWAP_v1:** `entry_mode` gains `SESSION_TREND` and `SESSION_PULLBACK`, plus `session_mode_session` and `session_mode_gates`. Per-symbol defaults: XAUUSD and US Tech 100 trend, BTCUSD pullback.
- **APA_v1:** `setup_mode = "SESSION_BREAKOUT_TREND"` runs the ORB-trend price-action setup under APA's slot. The default is still `HEAD_AND_SHOULDERS`, so live APA slots behave as before.

**Engine plumbing:**
- The backtester applies a strategy's `MODIFY_SL` (trailing) and a longer position window.
- Strategies can ask for longer history (`WINDOW_BARS`) on both the backtest and live paths.
- The live position manager gained a generic strategy-exit hook (`_check_strategy_position_exit`): strategy-owned trails, exits and session closes now happen live, not only in backtests.

**Frontend:**
- The Backtester, Settings and Strategy Lab list the new strategies.
- The Backtester has parameter panels for them, plus the new ORB and VWAP fields. "Use the measured settings for this symbol" is on by default.

**Tests:**
- `tests/test_edge_lab.py`, `tests/test_classic_strategies.py`, `tests/test_session_modes.py`, plus updates to the ORB and measured-defaults tests.
- Signal-for-signal parity with the research is checked for every new engine and mode.
- **All pass:** the full suite (457), the new parity suites (10 classic + 6 session-mode), and the frontend production build.

**Scripts:** `scripts/run_edge_lab.py` (fetch, build, search `--robust`, confirm), `scripts/run_classic_book.py`, `scripts/run_final_book.py`.

**Data:** `data/edge_lab/` holds the searches, confirmations, the classic book and the final book (JSON and logs).

---

## 10. TODO

- [ ] **Deploy** to the VPS, restart the backend and rebuild the frontend (git stays local-only).
- [ ] **Check parity in the app.** Run the app Backtester on the last 8 months for ORB_v1 on GBPJPY and XAUUSD, and VWAP_v1 on XAUUSD and BTCUSD, with measured defaults on. Each should land near §4/§5 in R. Tick replay is not done for the new modes.
- [ ] **Slots:**
  - [ ] Add ORB_v1 on US Tech 100, XAUUSD and BTCUSD (GBPJPY already exists; it now runs the M5 trend form).
  - [ ] Add VWAP_v1 on XAUUSD, US Tech 100 and BTCUSD.
  - [ ] Remove VWAP_v1 slots on other markets.
- [ ] **Risk:** start the VWAP per-market books at 1%. On $350, run only GBPJPY ORB and BTCUSD VWAP pullback.
- [ ] **Forward-test gate:** pause any book whose first 30 live trades average below 0R, or whose DD passes 15%.
- [ ] **APA:** the Backtester panel doesn't show `setup_mode` yet (it is in the Strategy Lab schema). Only set it to SESSION_BREAKOUT_TREND on a slot that doesn't already run ORB_v1 on the same market, or the two slots would double the same trade.
- [ ] Re-run `scripts/run_final_book.py` monthly. It re-scores the shipped books on new data without choosing anything.
- [ ] Engine fixes still open from 2026-09-12: per-bar spread, slippage calibration, margin fallback currency.
- [ ] Not urgent, still open: the Claude summarize-backtest and journal-analysis features in the frontend.
