# Backtest = live, part 2: same bars, same stops (15 Sep 2026)

## 1. The short answer

- **Signals and trade counts come from one piece of code everywhere.** The single backtest, the portfolio backtest and the live bot all feed strategies through `strategies/bar_feed.py`. Every strategy sees each closed bar exactly once, in order.
- **Stops move the same way.** Live break-even and trailing run the backtester's own decision code, once per closed bar, with each strategy's measured exit settings.
- **Proof on real MT5 bars:** 8 strategies were each run as a backtest and as a simulated live bot (60-second scans with jitter and a 45-minute outage). **Every one of the 226 backtest signals was traded by the live bot on the same bar at the same entry and stop, and live traded nothing extra.**
- **Tests:** the full suite passes, 508 tests.

## 2. What was different, and the fix

| # | Difference | What it did to live | Fix |
|---|---|---|---|
| 1 | **Live re-fed every timeframe on every 60-second scan.** The backtest feeds each closed bar once. | Strategies that count bars aged their setups about 5× too fast on M5 (five scans per bar): HTF FVG Flip, Bias IFVG, APA. DriftJumpAlpha and BoomDriftJump counted the same bar again and again in their jump history. VWAP counted a re-found signal against its daily cap. The result was fewer or different live trades than the backtest. | `LiveBarFeed` hands the strategy each closed bar exactly once, in backtest order. A second scan inside the same bar does nothing. |
| 2 | **Live skipped bars that closed during a slow scan or while the bot was offline.** It also started with an empty strategy memory. | Setups spanning those bars were never seen. After a restart, the strategy's state didn't match the backtest's. | Missed bars are replayed in order, so the strategy's state catches up. A signal on a bar the bot saw too late is logged as missed, never traded late. On start, the strategy warms up on the same history a backtest does. |
| 3 | **Live break-even and trailing ignored each strategy's measured exit settings.** It used the global risk settings instead. | SpikeFade and RangeRevert are measured with break-even OFF, but live moved their stops to break-even anyway. Trades the backtest held to target could close flat live. | The live position manager uses the same risk config the entry was placed under (`risk/live_risk_config.py`), including the strategy's measured exits. |
| 4 | **Live checked stops every ~20 seconds at the tick price, with its own trailing code.** It trailed ATR from the current price, not from the highest high or lowest low, and used its own ATR and swing rules. | Stops moved earlier, and to different levels, than in the backtest. | Once per closed bar, live replays the bars since entry through the backtester's `RiskEngine` and TP1 break-even cascade (`risk/exit_replay.py`), and moves each stop to where the backtest holds it. The old live-only trailing and ATR code is deleted. |
| 5 | **The single-symbol and portfolio backtesters used different ATR formulas.** | The same trade trailed and broke even at different levels alone and in a portfolio. | One ATR and one swing-point function for both engines and live. Single-symbol results are unchanged. Portfolio break-even and trailing levels shift slightly. |
| 6 | **Live entered up to a full scan interval after the bar closed.** | Live entry prices sat further from the backtest's fill, which is the next bar's open. | The live loop now wakes about 2 seconds after each bar opens. |

## 3. Proof

**All strategies, synthetic scan clock.** `tests/test_live_bar_feed_parity.py` runs all 11 registered strategies, each once through the backtest loop and once through the live feeder. The feeder runs on scans every ~60 s with jitter, plus a 45-minute outage and a 7-minute slow scan.
- Every call to the strategy is identical.
- Every backtest signal is either traded live or reported as missed during the outage.

**Stops.** `tests/test_live_exit_parity.py` runs the single-symbol and portfolio engines on the same bars and records every break-even and trailing decision.
- The live replay makes the same decisions, bar for bar, and ends on the same stops.
- The two engines now also agree with each other.

**Real MT5 bars.** `scripts/check_live_backtest_parity.py` covers the last 12–15 days up to 14 Sep 2026:

| Strategy | Market | Days | Backtest signals | Live traded | Same bar, entry and stop |
|---|---|---|---|---|---|
| SpikeFade_v1 | Crash 500 Index | 15 | 62 | 62 | **62** |
| TrendDrift_v1 | US SP 500 | 15 | 83 | 83 | **83** |
| DriftJumpAlpha_v1 | Crash 1000 Index | 12 | 72 | 72 | **72** |
| ORB_v1 | GBPJPY | 15 | 4 | 4 | **4** |
| APA_v1 | GBPUSD | 12 | 2 | 2 | **2** |
| VWAP_v1 | US Tech 100 | 12 | 1 | 1 | **1** |
| HTFFVGFlip_v1 | EURUSD | 12 | 1 | 1 | **1** |
| BiasIFVG_v1 | XAUUSD | 12 | 1 | 1 | **1** |

In all eight runs, the 45-minute outage happened to fall on bars with no signal. The missed-bar path is covered by the unit test instead. HTF FVG, Bias IFVG, VWAP and APA only fired once or twice in the window, so for those four the synthetic test is the stronger evidence.

## 4. What can still differ, and why

| Source | Effect | Size |
|---|---|---|
| Bot offline, or a scan runs past a bar | The signal on that bar is reported as missed and not traded; the backtest takes it. | One trade per missed signal, counted as `missed_bar_signal` in `/api/bot/status` and the activity log. |
| Entry price | Live fills ~2–5 s after the bar opens, at the ask or bid of that moment. The backtest fills at the open. | Usually well under a pip on FX. More during synthetic-index spikes. |
| The broker refuses a trade | Minimum lot, market closed or a requote: the backtest trades it, live cannot. | Shown in the suppression funnel. |
| Account balance | Live sizes on the real balance, per your sizing basis; the backtest uses its simulated one. | Differs only once the two balances drift apart. |
| Two portfolio slots signal on the same bar when a global cap allows only one | The backtest and live may pick different slots. | Rare. |

## 5. What you'll notice live

- **Slow first scan.** After a start or restart, the first scan of each strategy takes longer, because it warms up on the backtest's history first. The log says `warmed up on N bars`.
- **Missed signals are logged.** A signal on a bar the bot saw too late is logged as a warning, not traded.
- **No break-even on SpikeFade and RangeRevert.** Their stops are no longer moved to break-even live, which matches their backtests.
- **Stops move once per bar.** Stops change once per closed bar, not every 20 seconds.

## 6. Check it yourself

```
python scripts/check_live_backtest_parity.py --strategy SpikeFade_v1 --symbol "Crash 500 Index" --days 20
```

## 7. Files

- **New:**
  - `backend/strategies/bar_feed.py`
  - `backend/risk/exit_replay.py`
  - `backend/risk/live_risk_config.py`
  - `scripts/check_live_backtest_parity.py`
  - `tests/test_live_bar_feed_parity.py`
  - `tests/test_live_exit_parity.py`
- **Changed:**
  - `backend/services/bot_service.py` (feeder, shared risk config, bar-aligned scans, dedupe keyed by bar)
  - `backend/services/position_manager.py` (exit replay; old break-even and trailing code removed)
  - `backend/api/routes/backtest.py` (both routes use `BarFeed`; warm-up table shared)
  - `backend/backtester/engine.py` and `portfolio_engine.py` (shared ATR and swings)
  - `backend/strategies/windows.py`
  - `scripts/run_app_backtest.py`
  - `tests/test_backtest_live_parity.py`
  - `tests/test_trailing_parity.py`
