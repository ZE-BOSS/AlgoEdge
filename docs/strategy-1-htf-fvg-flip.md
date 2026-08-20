# Strategy 1: HTF Key Level → 5-Minute FVG → Inversion Flip

## Overview
A discretionary intraday futures strategy (ICT-style) that looks for order-flow
to "flip" after price taps an untested higher-timeframe Fair Value Gap (FVG),
then confirms the reversal with a lower-timeframe FVG and its inversion before
entering. Works symmetrically long and short.

> **Source note:** This spec is distilled from a promotional trading-education
> video. Reported stats (see Backtest Reference below) are the creator's
> self-reported figures, not independently verified. Treat as a hypothesis to
> validate with your own backtest, not a guaranteed edge.

## Definitions
- **Fair Value Gap (FVG):** 3-candle imbalance. For a bullish FVG, candle 1's
  high is below candle 3's low (gap = candle1.high → candle3.low), created by
  a strong displacement candle 2. Bearish FVG is the mirror case.
- **Untested/unfilled FVG:** an FVG price has not traded back into since it formed.
- **Inversion (IFVG):** once price closes (body close, not wick) through an
  FVG, that FVG flips polarity — a bullish FVG that gets closed through
  becomes bearish resistance, and vice versa. The inversion candle's close is
  the trigger.
- **Internal high/low:** the local swing point immediately before price
  manipulated down/up into the 5-minute FVG. Used as the break-even level.

## Trade Logic (state machine)
1. **AWAIT_HTF_TAP** — On the 1H or 4H chart, identify an untested FVG that is
   counter to the recent trend (i.e., a bullish FVG below price during a
   downtrend, or bearish FVG above price during an uptrend). Wait for price to
   trade into it for the first time.
2. **AWAIT_5M_FVG** — After the HTF tap, wait for a new FVG to form on the
   5-minute chart in the anticipated reversal direction.
3. **AWAIT_5M_RETEST** — Wait for price to trade back into that 5-minute FVG.
4. **AWAIT_INVERSION** — Watch a lower timeframe (commonly 5m) for a
   body close back through the 5-minute FVG in the reversal direction. This is
   the inversion / confirmation signal.
5. **ENTER** — Enter in the direction of the inversion once the confirming
   candle closes.
6. **MANAGE**
   - Stop loss: the swing high/low that traded into the 5-minute FVG.
   - Break-even trigger: price reaching the internal high/low.
   - Take profit: the next significant liquidity pool (nearest opposing swing
     high/low). Default target ratio is 1:1; allow an optional discretionary
     override to a further liquidity level if the setup has a clear draw.

## Pseudocode
```
state = AWAIT_HTF_TAP
htf_fvg = None
m5_fvg = None
bias = None  # 'long' or 'short'

on_new_htf_candle(candle):
    if state == AWAIT_HTF_TAP:
        gaps = detect_fvgs(htf_candles, min_untested=True)
        for g in gaps:
            if g.is_counter_trend(current_trend) and price_has_entered(g):
                htf_fvg = g
                bias = 'long' if g.type == 'bullish' else 'short'
                state = AWAIT_5M_FVG

on_new_m5_candle(candle):
    if state == AWAIT_5M_FVG:
        g = detect_new_fvg(m5_candles, direction=bias)
        if g:
            m5_fvg = g
            state = AWAIT_5M_RETEST
    elif state == AWAIT_5M_RETEST:
        if price_enters(m5_fvg):
            state = AWAIT_INVERSION

on_new_low_tf_candle(candle):  # 5m
    if state == AWAIT_INVERSION:
        if body_closes_through(candle, m5_fvg, direction=bias):
            entry = candle.close
            stop = swing_point_that_tapped(m5_fvg)
            be_trigger = internal_high_or_low(m5_fvg)
            target = next_liquidity_pool(bias, default_rr=1.0)
            place_trade(entry, stop, target, be_trigger)
            state = DONE
```

## Configurable Parameters
| Parameter | Default | Notes |
|---|---|---|
| `htf_timeframe` | 1H (alt: 4H) | Key-level timeframe |
| `entry_confirmation_tf` | 5m | Can widen if too noisy |
| `target_rr` | ~~1.0~~ **2.0** | Ratio to next liquidity pool; user-overridable — see note |
| `require_unfilled_htf_fvg` | true | Only take first tap of a gap |
| `session_filter_enabled` | ~~(none)~~ **true** | Added 2026-08 — see note |
| `session_start` / `session_cutoff` | **09:30 / 16:00 ET** | US RTH (engine converts to America/New_York) |
| `sl_buffer_atr_mult` | **0.5** | ATR(14) cushion added to the structural stop |
| `min_sl_pips` / `min_sl_atr_mult` | **12.0 / 1.0** | Absolute + volatility-relative stop floors |

> **Revision note — 2026-08 cost-realism audit.**
> **`target_rr` 1.0 → 2.0.** Round-trip friction on an FX major is ~3.0 pips (2.0 spread +
> 0.4 slippage + ~0.6 commission), ≈0.25R against the 12-pip stops this config now
> produces. A 1.0R target therefore nets 0.75R while a loss costs 1.25R → **62.5%**
> break-even win rate. The "Backtest Reference" above self-reports ~75% on 27 unverified
> trades; needing 62.5% merely to break even leaves no margin for out-of-sample decay.
> At 2.0R: nets 1.75R against 1.25R → **41.7%** break-even.
>
> **Session filter enabled.** This answers the first item under "Open Questions for
> Implementation" ("consider adding one if false signals cluster outside RTH") — forensic
> review of real runs found exactly that clustering.
>
> **Stop floors added.** `engine.py:199` places the stop flush at `m5_swing_point` with no
> buffer and no floor — i.e. exactly on the visible swing wick every other participant can
> see. ⚠ `sl_buffer_atr_mult`, `min_sl_pips` and `min_sl_atr_mult` are **not yet read by
> the engine** and are inert until wired.

## Data Requirements
5m, 15m, and 1H/4H OHLC candles for the traded instrument, synchronized to the
same session timezone.

## Backtest Reference (self-reported, unverified)
Creator claims a one-month backtest of a closely related setup produced: 27
trades, 18 wins / 6 losses / ~3–5 breakeven, ~75% win rate, average R:R ≈ 1.0,
largest single R ≈ 2.26, ~13% account return risking 1% per trade. Use this
only as a rough sanity-check target, not as validated performance.

## Open Questions for Implementation
- No explicit session/time filter was given for this strategy (unlike
  Strategy 3) — consider adding one if false signals cluster outside RTH.
- "Next significant liquidity pool" is subjective; implement a swing-high/low
  detector with a configurable lookback and let the user tune it.
