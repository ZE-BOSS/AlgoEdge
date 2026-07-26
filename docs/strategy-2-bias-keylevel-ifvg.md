# Strategy 2: 4-Step Bias → Key Level → IFVG Confirmation → Execution

## Overview
An expanded version of the ICT-style FVG approach that adds two extra
key-level types (CISD and rejection blocks) on top of plain FVGs, plus a
stricter confirmation rule: always take the **highest-timeframe** FVG inside
the specific price swing ("manipulation leg") that tapped the key level.

> **Source note:** Distilled from promotional trading-education content.
> Reported win rate (~70%) is a self-reported, unverified claim. Validate with
> your own backtest before relying on it.

## Step 1 — Higher-Timeframe Bias & Draw on Liquidity
- Timeframes: Daily, 4H, 1H (primary); 15m (secondary check, can flip bias
  intraday e.g. on clean equal lows/highs).
- Ask two questions:
  1. Which FVGs is price respecting vs. disrespecting? (Respecting bullish
     FVGs + disrespecting bearish FVGs → bullish bias, and vice versa.)
  2. Which swing high/low is price drawing toward? That level = draw on
     liquidity (DOL) for the session.
- Output: `bias ∈ {bullish, bearish}` and `dol_price`.

## Step 2 — Identify a Valid Key Level
Limit to 2–3 candidate levels per session. Three detector types, each
returning a price zone:

**(a) FVG / intermediate high-low**
- If the FVG is still unmitigated, the zone is the FVG itself.
- If price already tapped it (e.g., pre-market) and is now approaching from
  the other side, the raw FVG is invalid — instead wait for the internal
  high/low *inside* that FVG to be swept; that sweep point becomes the key
  level.

**(b) CISD (Change in State of Delivery)**
- Bullish: find the run of down-close candles that reached a key
  FVG/intermediate low; mark the *opening price* of that run; a body close
  above that opening price = CISD confirmed, and that opening price becomes
  the key level.
- Bearish: mirror — run of up-close candles into a key FVG/intermediate high;
  body close below the run's opening price = CISD.

**(c) Rejection Block**
- Bullish: a wick that pierced into an FVG or intermediate low, then
  rejected. Zone = candle's open-to-wick range. Track the midpoint
  ("consequent encroachment") of that range as a secondary reference.
- Bearish: mirror, using a wick into a bearish FVG/intermediate high.

Timeframes for key levels: 3m, 5m, 15m, 30m, 1H, 4H — always read against the
Step 1 bias.

## Step 3 — IFVG Confirmation
- Define the **manipulation leg**: the swing (high→low, or low→high) that
  actually touched the Step 2 key level.
- Scan the 5m timeframe for FVGs that exist *within* that specific leg (not elsewhere on chart).
- Take the **highest** timeframe FVG found inside the leg — this is the IFVG.
- Entry trigger = a body close through that FVG, on its native timeframe.
- Rule of thumb: lower-timeframe-only confirmations (e.g. jumping straight to
  a lower-timeframe signal when a higher gap exists in the same leg) are a common beginner
  error and reduce win rate, even though they'd give better R:R.

## Step 4 — Execution & Risk Management
- **Entry:** market at the IFVG body-close candle; OR a limit order back at
  the IFVG/CISD zone if the confirmation candle's close gives poor R:R.
- **Stop loss (in priority order, pick one and keep consistent):**
  swing high/low (default — safest for beginners) → candle body → FVG
  boundary → order block.
- **Take profit:** target 1:1 to 1:3 R, aimed at the nearest realistic local
  swing high/low ("low-hanging fruit"), not necessarily the full Step-1 draw
  on liquidity.
- **Trade frequency cap:** max 1–2 trades/day. Stop for the day after 1 win.
  After 1 loss, only take a second trade if it's a clearly A+ setup at a new
  key level; stop after 2 losses regardless.
- **Session window:** 9:30–11:00 AM ET primary trading window. Only trade
  past 11:00 if 9:30–11:00 produced nothing and fresh liquidity has since
  been generated.

## Pseudocode
```
bias, dol = compute_bias(daily, h4, h1, m15)

key_levels = []
key_levels += detect_fvg_levels(bias)          # (a)
key_levels += detect_cisd_levels(bias)         # (b)
key_levels += detect_rejection_blocks(bias)    # (c)
key_levels = rank_and_trim(key_levels, max_n=3)

for level in key_levels:
    wait_until(price_touches(level))
    leg = get_manipulation_leg(level)  # swing that touched the level
    candidate_tfs = ['M5']
    ifvg = highest_tf_fvg_within_leg(leg, candidate_tfs)
    if ifvg is None:
        continue
    wait_until(body_close_through(ifvg))
    if not within_session_window(9,30, 11,0):
        continue
    entry = ifvg.close_price
    stop  = pick_stop(level, method='swing')  # configurable
    target = nearest_swing_in_bias_direction(min_rr=1.0, max_rr=3.0)
    place_trade(entry, stop, target)
    if trades_today == 1 and last_trade_won:
        stop_for_day()
    if losses_today >= 2:
        stop_for_day()
```

## Configurable Parameters
| Parameter | Default |
|---|---|
| `bias_timeframes` | Daily, 4H, 1H (+15m check) |
| `key_level_timeframes` | 3m, 5m, 15m, 30m, 1H, 4H |
| `confirmation_timeframes` | 5m |
| `stop_method` | swing_high_low |
| `target_rr_range` | 1.0–3.0 |
| `max_trades_per_day` | 2 |
| `session_start / session_end` | 09:30 / 11:00 ET |

## Data Requirements
OHLC candles at 5m, 15m, 30m, 1H, 4H, and Daily resolution, all
timezone-aligned to US Eastern.

## Open Questions / Edge Cases for Implementation
- Need explicit tie-breaking logic when multiple key-level types (FVG, CISD,
  rejection block) overlap at the same zone — treat as higher-confidence
  confluence rather than separate signals.
- "Highest timeframe FVG within the leg" requires precise leg boundaries;
  define the leg as the exact swing-high-to-swing-low (or reverse) price path
  that first touched the key level, not the whole visible chart range.
- CISD and rejection-block detectors are new relative to Strategy 1 — build
  as independent, testable modules so they can be validated separately before
  combining.

## Disclaimer
This document summarizes a retail trading-education creator's discretionary
methodology for the purpose of algorithmic implementation and backtesting.
Any win-rate or profitability figures referenced are self-reported marketing
claims, not audited results. This is not financial advice; past or
hypothetical performance does not guarantee future results, and futures/CFD
trading carries substantial risk of loss.
