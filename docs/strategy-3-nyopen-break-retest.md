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
   - Take profit: fixed default of 50 points in the trade's direction
     (implementation-tuned default; the framework's original worked examples
     used ~15 points, but the shipped default has since been calibrated to 50).
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

on_new_m5_candle(candle):
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
            fixed_target = entry + 50 if bias == 'long' else entry - 50  # default fixed_target_points = 50
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
| `break_confirmation_tf` | 5m | Close-based, not wick-based |
| `earliest_valid_break_time` | 09:30 ET | Breaks before this are ignored |
| `stop_buffer_points` | 5 (instrument-specific) | Calibrate per market |
| `fixed_target_points` | 50 (instrument-specific) | Implementation-tuned default (was ~15 in the original framework examples); calibrate per market |
| `sl_buffer_atr_mult` | ~~0.0 (disabled)~~ **1.0** | Extra ATR(14)-multiple SL buffer, applied on top of `stop_buffer_points` — see note |
| `target_mode` | **"rr"** (was implicitly "points") | Added 2026-08 — `"rr"` = target is `target_rr` × realised stop; `"points"` = legacy `fixed_target_points` |
| `target_rr` | **2.0** | R-multiple target used when `target_mode == "rr"` |

> **Revision note — 2026-08 cost-realism audit.**
> **`sl_buffer_atr_mult` 0.0 → 1.0.** The structural stop is (range_mid → range extreme) +
> 5 points. The 08:00–08:15 ET candle on USDCHF is typically 6–10 pips tall, so half-range
> is 3–5 pips and the whole stop was ~8–10 pips — only ~3–4× the ~2.5-pip all-in cost.
> Adding 1.0 × ATR(M5) (~3 pips on USDCHF) lifts it to ~11–13 pips, i.e. 4–5× cost. Under
> realistic costs this strategy went **+$1,752 → −$7,250** in the forensic re-run, and a
> stop costing ~30% of R was the dominant term. Its measured expectancy was **−0.006R**.
>
> **`target_mode` added, defaulting to `"rr"`.** This resolves the third "Open Questions"
> item above. `fixed_target_points = 50` is retained as the NQ-native ceiling but is badly
> mis-scaled on FX: 50 pips from `range_mid` inside a 09:30–11:00 window is effectively
> unreachable on a CHF major whose entire average *daily* range is ~55 pips — in the real
> runs virtually no trade exited at the fixed target. An R-multiple is the only target
> formulation simultaneously correct on NQ, USDCHF and XAUUSD.
> ⚠ `target_mode` / `target_rr` are **not yet read by the engine** (`engine.py:114`
> computes the points target unconditionally) and are inert until wired.
> `sl_buffer_atr_mult` *is* correctly read (`engine.py:129-136`).
| `dynamic_target_override` | enabled | Use nearer HTF swing level if closer |
| `session_end` | 11:00 ET | Extend only if AM range was unproductive |
| `news_filter` | optional | Source recommends de-risking around scheduled news, not necessarily skipping the trade entirely |

## Data Requirements
5-minute and 15-minute OHLC candles, timezone-aligned to US Eastern, plus a
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
