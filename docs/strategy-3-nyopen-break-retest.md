# Strategy 3: 8:00 AM Session-Range Break & Retest

## Overview
A session-range breakout strategy (no FVG/ICT concepts involved) built around
a single 15-minute candle: 8:00–8:15 AM Eastern Time. The premise is that this
candle captures pre-market institutional positioning ahead of the 9:30 AM
cash-market open, and that a break of its range followed by a retest of the
range's midpoint offers a repeatable scalp entry between 9:30–11:00 AM ET.

> **Source note:** Distilled from promotional trading-education content.
> Dollar figures and win-streak claims in the source video are self-reported
> and unverified; only the mechanical rules are captured below.

## Definitions
- **Range candle:** the single 15-minute candle spanning 08:00:00–08:14:59 ET.
- **Range zone:** rectangle from that candle's high wick to its low wick.
- **Midpoint:** `(range_high + range_low) / 2`.
- **Break:** on the 1-minute chart, price closes outside the range zone
  (above the high for a bullish break, below the low for a bearish break).

## Trade Logic (state machine)
1. **MARK_RANGE** — At 08:15 ET, capture `range_high`, `range_low`,
   `range_mid` from the 8:00 candle.
2. **AWAIT_BREAK** — On the 1-minute chart, watch for a close beyond
   `range_high` or `range_low`.
   - Only valid if the break occurs **at or after 09:30 ET**. A break before
     09:30 is considered low-volume/unreliable and should be ignored (keep
     watching for a later break).
   - Direction sets bias: break above → long bias; break below → short bias.
3. **AWAIT_RETEST** — Wait for price to pull back to `range_mid`.
4. **ENTER** — Fill at `range_mid` (limit order) when price retests it.
5. **MANAGE**
   - Stop loss: a small buffer beyond the *opposite* side of the range from
     entry — e.g., ~5 points beyond `range_low` for a long, ~5 points beyond
     `range_high` for a short (point value is instrument-specific; examples
     in the source use NASDAQ/NQ futures).
   - Take profit: fixed default of ~15 points in the trade's direction.
     Override to the nearest obvious higher-timeframe swing level (e.g., a
     nearby all-time high) if that level is closer than the fixed target —
     avoids holding through likely resistance/support.

## Pseudocode
```
range_high = range_low = range_mid = None
state = MARK_RANGE
bias = None

on_new_m15_candle(candle):
    if state == MARK_RANGE and candle.session_time == "08:00-08:15 ET":
        range_high = candle.high
        range_low = candle.low
        range_mid = (range_high + range_low) / 2
        state = AWAIT_BREAK

on_new_m1_candle(candle):
    if state == AWAIT_BREAK:
        if candle.time < "09:30 ET":
            return  # ignore pre-open breaks
        if candle.close > range_high:
            bias = 'long'
            state = AWAIT_RETEST
        elif candle.close < range_low:
            bias = 'short'
            state = AWAIT_RETEST

    elif state == AWAIT_RETEST:
        if price_touches(range_mid):
            entry = range_mid
            buffer = instrument_point_buffer  # e.g. 5 points on NQ
            stop = range_low - buffer if bias == 'long' else range_high + buffer
            fixed_target = entry + 15 if bias == 'long' else entry - 15
            nearby_level = nearest_htf_swing_level(bias)
            target = nearby_level if is_closer(nearby_level, fixed_target, bias) else fixed_target
            place_trade(entry, stop, target)
            state = DONE

    if candle.time >= "11:00 ET" and state != DONE:
        state = STOP_FOR_DAY  # no new entries after the session window
                               # unless the AM window was unproductive
                               # and fresh liquidity has since formed
```

## Configurable Parameters
| Parameter | Default | Notes |
|---|---|---|
| `range_window` | 08:00–08:15 ET | One 15m candle |
| `break_confirmation_tf` | 1m | Close-based, not wick-based |
| `earliest_valid_break_time` | 09:30 ET | Breaks before this are ignored |
| `stop_buffer_points` | 5 (instrument-specific) | Calibrate per market |
| `fixed_target_points` | 15 (instrument-specific) | Calibrate per market |
| `dynamic_target_override` | enabled | Use nearer HTF swing level if closer |
| `session_end` | 11:00 ET | Extend only if AM range was unproductive |
| `news_filter` | optional | Source recommends de-risking around scheduled news, not necessarily skipping the trade entirely |

## Data Requirements
1-minute and 15-minute OHLC candles, timezone-aligned to US Eastern, plus a
way to identify recent higher-timeframe swing highs/lows for the dynamic
target override.

## Open Questions / Edge Cases for Implementation
- No retest = no trade: if price breaks the range but never comes back to
  `range_mid`, the setup is skipped for the day (no fallback entry rule was
  given).
- Point-based stop/target values were demonstrated on NQ futures specifically
  and need recalibration (e.g., as ATR or % of range width) for other
  instruments.
- "Nearby HTF swing level" for the dynamic target override needs a concrete
  detector (e.g., N-bar swing high/low or prior day/week high/low) — pick one
  and keep it consistent for backtesting.

## Disclaimer
This document summarizes a retail trading-education creator's discretionary
methodology for the purpose of algorithmic implementation and backtesting.
Any profitability figures referenced in the source material are self-reported
marketing claims, not audited results. This is not financial advice; past or
hypothetical performance does not guarantee future results, and futures/CFD
trading carries substantial risk of loss.
