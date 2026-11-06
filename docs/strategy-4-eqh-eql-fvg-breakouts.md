# Strategy 4: EQH/EQL FVG Breakouts (NY session)

## Overview
Equal highs / equal lows mark pools of resting stop losses. When a candle
sweeps a pool with a big body **and** leaves a fair value gap behind it, trade
in the breakout direction. "No gap, no signal."

> **Source note:** LuxAlgo TikTok video (BTCUSDT 15m) plus the "New York zones"
> version shown at its end. The video gives no stop or target; the trade layer
> below is ours and was chosen by testing.

## Files
- `mql5/Experts/EQH_EQL_FVG_Breakouts_EA.mq5`: MT5 Expert Advisor (Deriv "US Tech 100", M1).
- `tradingview/eqh_eql_fvg_breakouts_strategy.pine`: TradingView strategy used for the tests
  (video defaults; NY filter and ATR stop are inputs).

## Rules (LuxAlgo defaults)
- **Pivots:** 10 candles left, 2 right.
- **Equal highs/lows:** two pivots within **0.03%** of each other and **≤ 30 candles** apart.
  Zone = box between the two prices, labelled with the summed pivot volume.
- **Zone life:** unswept zones are removed after 30 candles; max 60 active.
- **NY version:** keep a zone only if the later pivot candle is inside
  09:30–16:00 America/New_York, Mon–Fri.
- **Sweep:** high > EQH top (or low < EQL bottom) turns the zone grey ("Swept").
- **Breakout signal:** the sweeping candle closes beyond the zone in the
  breakout direction, its body is ≥ 0.5 × ATR(14), and the next candle leaves
  an FVG ≥ 0.1 × ATR (bull: `low[0] > high[2]`).
- **Entry:** market at the open of the candle after the gap candle.
  EQH break = long, EQL break = short. One position at a time.

## Trade layer (EA defaults)
- Stop: 1 × ATR(14) from entry. Target: 1R.
- Size: 1% of equity per trade, lot computed with `OrderCalcProfit` (spread
  included); skip if below broker minimum lot; margin-checked.

## Test results (TradingView, 6 Nov 2026, 1% risk on $10k, Deriv spread as cost)
| Setup | Trades | Win % | PF | Net |
|---|---|---|---|---|
| Video defaults (all hours), 6 Deriv assets × 15m/5m/1m | — | — | < 1 in almost every run | loss |
| **NY version, US Tech 100 M1, ATR 1.0 stop, 1R** | 26 | 73% | 2.35 | +10.7% (DD 2.9%) |
| Same on CME MNQ M1 (same 2 weeks) | 23 | 74% | 2.34 | — |
| Same settings, US Tech 100 M2 / M3 | 9 / 13 | 22% / 31% | 0.27 / 0.41 | loss |

**Status: unproven.** TradingView has only ~2 weeks of M1 history and the
neighbouring timeframes fail. Next step: MT5 Strategy Tester on Deriv
history ("Every tick based on real ticks", as many months as available),
then 4–6 weeks on demo before any live allocation.

## Known differences EA vs TradingView
- Pivot tie-break: the EA allows equal highs on the left side of a pivot
  (TradingView's exact rule isn't documented).
- Volume labels use MT5 tick volume. Signals do not depend on volume.
