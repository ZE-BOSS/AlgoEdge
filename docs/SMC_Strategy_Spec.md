# Smart Money Concepts (SMC) Trading Strategy
## Implementation Specification for AI Coding Agents

**Source:** Distilled from *"Mind of the Market: The Smart Money Blueprint"* by Pamela Donald
**Version:** 1.0
**Purpose:** This document is a machine-readable + human-readable spec for implementing an SMC-based signal engine / trading bot / backtester. Every threshold in this strategy is a **default** — all parameters MUST be exposed as user-configurable (config file, env vars, or UI settings), not hardcoded.

> ⚠️ **Disclaimer:** This is a technical specification of a discretionary retail trading methodology, not financial advice. Forex/CFD trading carries substantial risk of loss. Any implementation should be backtested and paper-traded extensively before live use. Past performance of this or any strategy does not guarantee future results.

---

## 1. Master Configuration Object

This is the canonical config. Implement it as a single JSON/YAML object that the agent loads at startup and can hot-reload. Every downstream module reads from this object — **no magic numbers in code**.

```json
{
  "strategy_name": "SMC_Blueprint",
  "version": "1.0",

  "timeframes": {
    "htf": "4H",
    "itf": "1H",
    "execution_tf": "15m",
    "require_htf_ltf_alignment": true
  },

  "market_structure": {
    "swing_lookback_bars": 5,
    "bos_confirmation_method": "close",
    "choch_min_zones_broken": 2,
    "choch_requires_htf_context": true,
    "structure_label_history_length": 50
  },

  "liquidity": {
    "equal_high_low_tolerance_pips": 3,
    "equal_high_low_tolerance_pct": 0.05,
    "trendline_min_touches": 3,
    "sweep_confirmation_pips": 2,
    "sweep_confirmation_atr_multiple": 0.1,
    "session_windows_utc": {
      "london": ["07:00", "10:00"],
      "new_york": ["12:00", "15:00"],
      "london_ny_overlap": ["12:00", "15:00"]
    },
    "target_external_liquidity_first": true,
    "inducement": {
      "enabled": true,
      "min_retrace_into_idm_pct": 0.2,
      "max_retrace_into_idm_pct": 0.7
    }
  },

  "order_blocks": {
    "lookback_bars": 20,
    "min_impulse_atr_multiple": 1.5,
    "require_liquidity_grab_before": true,
    "require_bos_or_choch_after": true,
    "refine_to_equilibrium_50pct": true,
    "refine_with_inner_fvg": true,
    "max_mitigation_touches_allowed": 1,
    "types_traded": ["bullish", "bearish", "continuation", "reversal"],
    "discard_after_full_mitigation": true
  },

  "supply_demand": {
    "max_base_candles": 3,
    "min_impulse_atr_multiple": 1.5,
    "require_liquidity_grab": true,
    "require_structure_break_after": true,
    "freshness_states_tradeable": ["fresh", "first_mitigation"],
    "zone_padding_pips": 1
  },

  "fair_value_gap": {
    "min_gap_size_pips": 2,
    "min_gap_size_atr_multiple": 0.1,
    "entry_reference": "50pct",
    "require_ob_confluence": false,
    "use_htf_fvg_for_bias": true,
    "use_ltf_fvg_for_entry": true,
    "max_age_bars": 100,
    "invalidate_on_full_fill": true
  },

  "fibonacci_zone": {
    "enabled": true,
    "swing_reference": "last_impulse_leg",
    "discount_zone_uptrend": [0.618, 0.79],
    "premium_zone_downtrend": [0.618, 0.79],
    "equilibrium_level": 0.5,
    "ote_zone": [0.62, 0.79],
    "require_ote_for_entry": false
  },

  "ipdm": {
    "phases": ["accumulation", "manipulation", "expansion"],
    "min_accumulation_bars": 10,
    "accumulation_max_range_atr_multiple": 2.0,
    "manipulation_requires_sweep": true,
    "expansion_confirmation": ["bos", "choch"],
    "expansion_requires_displacement": true,
    "displacement_min_atr_multiple": 1.5
  },

  "entry_model": {
    "bias_source": "htf_structure",
    "required_confirmations": [
      "itf_choch_mandatory",
      "liquidity_sweep",
      "poi_identified_on_itf",
      "m15_candlestick_confirmation"
    ],
    "poi_types_accepted": ["order_block", "fvg", "supply_demand_zone", "fib"],
    "min_signal_score": 55,
    "entry_trigger": "candlestick_inside_poi",
    "alternate_entry_trigger": "ltf_bos_after_choch_or_wick_tap"
  },

  "risk_management": {
    "risk_per_trade_pct": 1.0,
    "max_risk_per_trade_pct": 2.0,
    "min_risk_per_trade_pct": 0.25,
    "max_trades_per_day": 2,
    "max_daily_risk_pct": 2.0,
    "max_weekly_drawdown_pct": 6.0,
    "max_consecutive_losses": 3,
    "cooldown_after_max_losses_hours": 24,
    "cooldown_after_big_win_loss_enabled": true,
    "min_rrr_to_accept_trade": 2.0
  },

  "stop_loss": {
    "placement": "liquidity_or_2swing",
    "buffer_pips": 2,
    "buffer_atr_multiple": 0.1,
    "use_last_2_swings": true
  },

  "take_profit": {
    "number_of_tps": 3,
    "tp_rr_levels": [2, 3, 5],
    "tp_close_pct": [50, 30, 20],
    "move_sl_to_breakeven_after_tp_index": 1
  },

  "trailing_stop": {
    "enabled": true,
    "method": "structure",
    "activate_after_rr": 1.0,
    "atr_multiple": 1.5,
    "structure_reference": "last_swing_or_ob"
  },

  "session_filter": {
    "tradeable_sessions": ["london", "new_york", "london_ny_overlap"],
    "avoid_news_minutes_before": 30,
    "avoid_news_minutes_after": 30,
    "high_impact_news_only": true
  },

  "guardrails": {
    "block_chasing_price": true,
    "require_fresh_poi_only": true,
    "require_htf_bias_alignment": true,
    "block_if_daily_loss_limit_hit": true,
    "block_if_max_trades_hit": true,
    "require_journal_entry_per_trade": true
  }
}
```

---

## 2. Timeframe Roles

| Role | Default | Config Key | Notes |
|---|---|---|---|
| HTF (Bias / IPDM) | `4H` | `timeframes.htf` | Determines directional bias; only trade in this direction |
| ITF (Zones & Structure) | `1H` | `timeframes.itf` | Mandatory ChoCH for shift, optional BOS for score. Spot POIs (OB, FVG, S&D, FIB) & Liquidity. |
| Execution TF (M5) | `15m` | `timeframes.execution_tf` | Timeframe the order is actually triggered on (Candlestick / Fallbacks) |

Rule: a signal is only valid if `require_htf_ltf_alignment = true` and the LTF confirmation direction matches the HTF bias.

---

## 3. Market Structure Engine

**Swing detection (fractal method):**
A bar `i` is a **swing high** if its high is greater than the highs of `swing_lookback_bars` candles on both sides. Swing low is the inverse.

```
swing_high(i) = high[i] > max(high[i-N:i]) AND high[i] > max(high[i+1:i+N+1])
swing_low(i)  = low[i]  < min(low[i-N:i])  AND low[i]  < min(low[i+1:i+N+1])
where N = market_structure.swing_lookback_bars
```

**BOS (Break of Structure):**
Trend continuation. Confirmed when price closes (`bos_confirmation_method = "close"`, alt: `"wick"`) beyond the most recent relevant swing high (bullish BOS) or swing low (bearish BOS) **in the direction of the existing trend**.

**CHOCH (Change of Character):**
Potential reversal signal. Confirmed when price breaks structure **against** the prevailing trend. Per `choch_min_zones_broken` (default 2), require the break to invalidate at least 2 prior supply/demand zones or swing points for higher validity — weaker/noisier CHOCH signals below this threshold should be flagged low-confidence, not auto-traded.

**Labeling:** Maintain a rolling structure array of `HH / HL / LH / LL` labels (length = `structure_label_history_length`) per timeframe, used to classify trend state: `uptrend | downtrend | ranging`.

---

## 4. Liquidity Engine

| Parameter | Default | Description |
|---|---|---|
| `equal_high_low_tolerance_pips` | 3 | Max distance between two highs/lows to be treated as "equal highs/lows" |
| `equal_high_low_tolerance_pct` | 0.05% | Alternate % based tolerance; use the stricter of the two |
| `trendline_min_touches` | 3 | Min touches to validate a liquidity trendline |
| `sweep_confirmation_pips` / `_atr_multiple` | 2 pips / 0.1×ATR | How far beyond a level price must wick to count as "swept" |
| `session_windows_utc` | London 07:00–10:00, NY 12:00–15:00, overlap 12:00–15:00 | Prime liquidity-grab windows (must be configurable per broker/instrument timezone) |

**Internal vs External Liquidity:**
- Internal = liquidity resting inside the current dealing range (pullback highs/lows).
- External = liquidity outside the range (prior equal highs/lows, session highs/lows).
- Rule (`target_external_liquidity_first`): a directional bias is only "active" after external liquidity has been swept; entries are then taken from internal POIs.

**Inducement (IDM):** A minor liquidity pool/false POI positioned between the sweep and the true POI, designed to trap early entries. Detect as a local high/low forming between `min_retrace_into_idm_pct` and `max_retrace_into_idm_pct` of the impulse leg. Do not treat IDM zones as valid entry POIs.

---

## 5. Order Block (OB) Engine

| Parameter | Default | Description |
|---|---|---|
| `lookback_bars` | 20 | How far back to search for the originating candle |
| `min_impulse_atr_multiple` | 1.5 | Move away from the OB candle must be ≥ 1.5× ATR(14) to qualify as "impulsive" |
| `require_liquidity_grab_before` | true | OB only valid if preceded by a liquidity sweep |
| `require_bos_or_choch_after` | true | OB only valid if the impulse move caused a structural break |
| `refine_to_equilibrium_50pct` | true | Entry refined to the 50% level of the OB candle's range |
| `refine_with_inner_fvg` | true | If an FVG sits inside/adjacent to the OB, prefer that as the tighter entry |
| `max_mitigation_touches_allowed` | 1 | OB is discarded/deprioritized after this many touches (fresh/first-touch only, per source material) |
| `types_traded` | bullish, bearish, continuation, reversal | All four OB categories are tradeable if other filters pass |

**Validity checklist (all must be true):**
1. Strong impulsive move follows the candle (BOS or CHOCH)
2. Move was preceded by/coincides with a liquidity grab
3. OB has not exceeded `max_mitigation_touches_allowed`
4. OB aligns with current HTF bias

---

## 6. Supply & Demand (S&D) Zone Engine

| Parameter | Default | Description |
|---|---|---|
| `max_base_candles` | 3 | Max consolidation candles allowed before the impulse (the "base") |
| `min_impulse_atr_multiple` | 1.5 | Same impulsive-move filter as OBs |
| `require_liquidity_grab` | true | Zone must originate near/after a liquidity sweep |
| `require_structure_break_after` | true | Departure from the zone must cause BOS/CHOCH |
| `freshness_states_tradeable` | fresh, first_mitigation | Only trade zones in these states; `mitigated` (2+ touches) zones are deprioritized |
| `zone_padding_pips` | 1 | Padding added to the base's high/low to define the tradeable zone boundary |

**Zone types:** `fresh`, `mitigated`, `continuation`, `reversal` — classify and store this state per zone so the agent can filter out stale zones automatically.

**Confluence check before trading a zone:**
- ✅ Followed a liquidity grab
- ✅ Caused a BOS or CHOCH
- ✅ Contains an OB or FVG inside it
- ✅ Aligned with HTF bias

---

## 7. Fair Value Gap (FVG) Engine

**3-candle detection algorithm:**
```
Bullish FVG at candle[i]:
  low[i+1] > high[i-1]   →  gap = [high[i-1], low[i+1]]

Bearish FVG at candle[i]:
  high[i+1] < low[i-1]   →  gap = [high[i+1], low[i-1]]
```

| Parameter | Default | Description |
|---|---|---|
| `min_gap_size_pips` / `_atr_multiple` | 2 pips / 0.1×ATR | Minimum gap size to register as tradeable (filters noise) |
| `entry_reference` | `"50pct"` | Options: `wick_tap`, `50pct`, `full_fill` |
| `require_ob_confluence` | false | If true, only trade FVGs that overlap an OB (highest-probability setups) |
| `use_htf_fvg_for_bias` | true | HTF FVGs inform directional bias |
| `use_ltf_fvg_for_entry` | true | LTF FVGs used for precise entry |
| `max_age_bars` | 100 | FVG expires/is deprioritized after this many bars unfilled |
| `invalidate_on_full_fill` | true | Once price fully closes the gap, remove it from active POI list |

**Entry/SL/TP defaults for FVG trades:**
- Entry: `entry_reference` level of the gap
- SL: just outside the gap boundary (+ `stop_loss.buffer_pips`)
- TP: next external liquidity pool, opposing OB, or next unfilled imbalance

---

## 8. Fibonacci / Equilibrium Zone

| Parameter | Default | Description |
|---|---|---|
| `swing_reference` | `last_impulse_leg` | Which leg to draw the retracement from |
| `discount_zone_uptrend` | [0.618, 0.79] | Buy-zone retracement band in an uptrend |
| `premium_zone_downtrend` | [0.618, 0.79] | Sell-zone retracement band in a downtrend |
| `equilibrium_level` | 0.5 | The 50% "fair value" split of a leg/candle |
| `ote_zone` | [0.62, 0.79] | "Optimal Trade Entry" — highest-confluence overlap zone |
| `require_ote_for_entry` | false | If true, entries are only valid inside the OTE band |

Use this module as a **confluence filter**, not a standalone signal — combine with OB/FVG/S&D zone overlap.

---

## 9. Institutional Price Delivery Model (IPDM)

Three-phase state machine per HTF/LTF pair:

1. **Accumulation/Distribution** — sideways range detected when price stays within `accumulation_max_range_atr_multiple × ATR` for at least `min_accumulation_bars`.
2. **Manipulation** — a liquidity sweep (per Liquidity Engine, §4) occurs at the edge of the range. `manipulation_requires_sweep = true`.
3. **Expansion/Delivery** — confirmed by `expansion_confirmation` (BOS and/or CHOCH) plus a displacement move ≥ `displacement_min_atr_multiple × ATR`.

```
IF phase == "accumulation" AND sweep_detected:
    phase = "manipulation"
IF phase == "manipulation" AND (BOS_detected OR CHOCH_detected) AND displacement_ok:
    phase = "expansion"
    → trigger entry_model evaluation
```

Only evaluate entries during/after the `expansion` phase confirmation, targeting the next external liquidity pool.

---

## 10. Entry Model (Signal Generation) & Confluence Scoring

The system evaluates validity using a **Weighted Confluence Scorer (0-100 points)**.
A trade must score a minimum of 55 points to execute.

**Mandatory Conditions:**
1. **HTF (H4)**: Bias identified (Direction).
2. **ITF (M15)**: A Change of Character (ChoCH) is mandatory in the direction of the HTF bias.
3. **Execution (M5)**: A Candlestick confirmation (Engulfing or Pinbar) is the primary trigger. 
   - *Fallback*: If no candlestick exists, enter on "LTF BOS after ChoCH" or aggressively on a "POI wick tap".

**Scoring Breakdown (Max 100):**
- **HTF Bias Alignment**: 15 points
- **M15 BOS (Optional Confirmation)**: 15 points (huge booster)
- **M15 ChoCH**: 10 points
- **Liquidity Sweep**: 15 points
- **Fresh Order Block (1-Touch, 50% Entry)**: 15 points
- **FVG Present / Inside OB**: 10 points
- **M5 Candlestick Confirmation**: 15 points (Tier 1), 10 points (Tier 2), 5 points (Tier 3)
- **OTE/FIB Confluence**: 5 points

If the total score >= 55, the trade fires.

**Full pre-trade checklist (must all be ✅):**
- [ ] HTF BOS/CHOCH identified
- [ ] Liquidity zone marked and swept
- [ ] Clean, fresh POI found (OB/FVG/S&D)
- [ ] LTF CHOCH inside POI
- [ ] Entry + SL + TP levels computed from structure
- [ ] Risk validated against §11 limits
- [ ] `min_rrr_to_accept_trade` satisfied

No entry fires if any box is unchecked.

---

## 11. Risk Management Engine

| Parameter | Default | Description |
|---|---|---|
| `risk_per_trade_pct` | 1.0% | % of account equity risked per trade |
| `max_risk_per_trade_pct` | 2.0% | Hard ceiling, cannot be overridden above this even by user config |
| `min_risk_per_trade_pct` | 0.25% | Floor to avoid negligible position sizing |
| `max_trades_per_day` | 2 | Hard cap on trade count/day |
| `max_daily_risk_pct` | 2.0% | Sum of open risk across all trades today |
| `max_weekly_drawdown_pct` | 6.0% | Circuit breaker — halt trading for the week if hit |
| `max_consecutive_losses` | 3 | Trigger cooldown after this many losses in a row |
| `cooldown_after_max_losses_hours` | 24 | Trading paused for this duration after hitting the streak limit |
| `min_rrr_to_accept_trade` | 2.0 | Reject any setup with projected RRR below this |

**Position sizing formula:**
```
position_size = (account_equity × risk_per_trade_pct/100) / (entry_price − stop_loss_price)
```

**Daily/session guardrail pseudocode:**
```
IF trades_today >= max_trades_per_day: block_new_entries()
IF open_risk_today_pct + new_trade_risk_pct > max_daily_risk_pct: block_new_entries()
IF consecutive_losses >= max_consecutive_losses: enter_cooldown(cooldown_after_max_losses_hours)
IF weekly_drawdown_pct >= max_weekly_drawdown_pct: halt_trading_until_next_week()
```

---

## 12. Stop Loss

| Parameter | Default | Description |
|---|---|---|
| `placement` | `liquidity_or_2swing` | SL placed beyond liquidity, and beyond the last 2 swing highs/lows |
| `buffer_pips` | 2 | Extra pip buffer |
| `buffer_atr_multiple` | 0.1 | Alternate ATR-based buffer; use the larger of the two |

**Dynamic SL Logic:**
1. **Liquidity**: If external liquidity exists, place the SL *above* liquidity for a SELL bias, and *below* liquidity for a BUY bias.
2. **2-Swing Rule**: Ensure SL safely clears the **last 2 swing highs** (for Sells) or the **last 2 swing lows** (for Buys).

---

## 13. Take Profit — Multi-TP Structure

| Parameter | Default | Description |
|---|---|---|
| `number_of_tps` | 3 | Number of scaled profit targets |
| `tp_rr_levels` | [2, 3, 5] | RR ratio for TP1 / TP2 / TP3 respectively |
| `tp_close_pct` | [50, 30, 20] | % of position closed at each TP (must sum to 100) |
| `move_sl_to_breakeven_after_tp_index` | 1 | After TP1 fills, move SL to entry (breakeven) |

**Logic:**
```
FOR each tp_index in range(number_of_tps):
    tp_price = entry ± (risk_distance × tp_rr_levels[tp_index])
    close_qty = position_size × tp_close_pct[tp_index] / 100
    IF price reaches tp_price:
        close(close_qty)
        IF tp_index == move_sl_to_breakeven_after_tp_index:
            move_sl(entry_price)
```

---

## 14. Trailing Stop

| Parameter | Default | Description |
|---|---|---|
| `enabled` | true | Toggle trailing stop on/off |
| `method` | `structure` | Options: `structure`, `atr`, `fixed_pips` |
| `activate_after_rr` | 1.0 | Trailing begins only after price reaches 1:1 RR |
| `atr_multiple` | 1.5 | Used if `method = "atr"` |
| `structure_reference` | `last_swing_or_ob` | Used if `method = "structure"` — trail behind the most recent confirmed swing point or OB |

---

## 15. Session & News Filters

| Parameter | Default | Description |
|---|---|---|
| `tradeable_sessions` | London, New York, London/NY overlap | Restrict entries to these windows |
| `avoid_news_minutes_before/after` | 30 / 30 | Block new entries around high-impact news |
| `high_impact_news_only` | true | Only filter for red-folder/high-impact events (requires an economic calendar data feed) |

---

## 16. Automated Guardrails (Mistake Prevention)

Each of these maps directly to the source material's "Top Mistakes" chapter — implement as hard boolean gates, not suggestions:

| Guardrail | Prevents |
|---|---|
| `block_chasing_price` | Entering before price reaches the identified POI |
| `require_htf_bias_alignment` | Trading against HTF structure / ignoring bias |
| `require_fresh_poi_only` | Trading over-mitigated OBs/zones |
| `block_if_max_trades_hit` | Overtrading |
| `block_if_daily_loss_limit_hit` | Revenge trading / poor risk management |
| `require_journal_entry_per_trade` | Skipping the trade journal |

---

## 17. Trade Journal / Logging Schema

Log every trade (taken or rejected) with at minimum:

```json
{
  "timestamp": "ISO8601",
  "instrument": "string",
  "htf_bias": "bullish | bearish | ranging",
  "poi_type": "ob | fvg | supply_demand",
  "confluences_hit": ["liquidity_sweep", "ltf_choch", "fib_ote", "..."],
  "entry_price": 0.0,
  "stop_loss": 0.0,
  "tp_levels": [0.0, 0.0, 0.0],
  "risk_pct": 0.0,
  "rrr_planned": 0.0,
  "result": "win | loss | breakeven | open",
  "rrr_realized": 0.0,
  "rejection_reason": "string | null",
  "screenshot_ref": "string | null",
  "notes": "string"
}
```

---

## 18. End-to-End Algorithm Flow (Pseudocode)

```
ON new_candle(tf):
    update_swings(tf)
    update_structure_labels(tf)     # HH/HL/LH/LL, BOS/CHOCH
    update_liquidity_pools(tf)
    update_order_blocks(tf)
    update_supply_demand_zones(tf)
    update_fvgs(tf)
    update_ipdm_phase(tf)

    IF tf in timeframes.htf:
        bias = compute_htf_bias()

    IF tf == timeframes.execution_tf:
        IF guardrails.block_if_max_trades_hit AND trades_today >= max_trades_per_day: RETURN
        IF guardrails.block_if_daily_loss_limit_hit AND daily_risk_used >= max_daily_risk_pct: RETURN
        IF in_cooldown(): RETURN
        IF NOT within_tradeable_session(): RETURN
        IF NOT news_filter_clear(): RETURN

        poi = find_valid_poi(bias)   # OB / FVG / S&D per §5-7 validity rules
        IF poi is null: RETURN

        IF NOT price_has_reached(poi): RETURN   # never chase (§16)

        IF ltf_choch_inside(poi) AND ltf_bos_confirms(bias):
            confluences = count_confluences(poi)
            IF confluences >= entry_model.min_confluences_required:
                trade = build_trade(poi, bias)
                IF trade.rrr >= risk_management.min_rrr_to_accept_trade:
                    IF passes_full_checklist(trade):   # §10 checklist
                        execute_entry(trade)
                        log_journal(trade)

ON open_position_update:
    manage_multi_tp(position)        # §13
    manage_trailing_stop(position)   # §14
    IF position.closed:
        update_consecutive_loss_counter(position.result)
        update_daily_risk_used(position)
        log_journal(position, final=true)
```

---

## 19. Configuration Overrides

- All values in §1 are defaults. Store user overrides in a separate `config.override.json` merged on top of defaults at load time — never edit defaults in place.
- Enforce **hard ceilings** regardless of user config: `risk_per_trade_pct` cannot exceed `max_risk_per_trade_pct`; `max_daily_risk_pct` cannot exceed `max_weekly_drawdown_pct`.
- Recommended safe ranges for UI validation:

| Parameter | Safe Min | Safe Max |
|---|---|---|
| `risk_per_trade_pct` | 0.25 | 2.0 |
| `max_trades_per_day` | 1 | 5 |
| `max_consecutive_losses` | 2 | 5 |
| `min_rrr_to_accept_trade` | 1.5 | 5.0 |
| `swing_lookback_bars` | 3 | 10 |

---

## 20. Glossary

| Term | Meaning |
|---|---|
| BOS | Break of Structure — trend continuation signal |
| CHOCH | Change of Character — potential reversal signal |
| OB | Order Block — last opposing candle before an institutional impulse |
| FVG | Fair Value Gap — 3-candle price imbalance |
| IDM | Inducement — false/trap liquidity pool before the real POI |
| IPDM | Institutional Price Delivery Model (Accumulation → Manipulation → Expansion) |
| POI | Point of Interest (OB, FVG, or S&D zone) |
| HTF / LTF | Higher / Lower Timeframe |
| RRR | Risk-to-Reward Ratio |
| SL / TP | Stop Loss / Take Profit |
| OTE | Optimal Trade Entry (Fibonacci confluence zone) |

---

*End of specification. Implementers should build each engine (§3–9) as an independently testable module feeding a shared POI/state store, with the Entry Model (§10) and Risk Engine (§11) as the only modules permitted to trigger or block order execution.*
