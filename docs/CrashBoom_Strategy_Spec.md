# Crash & Boom Index Trading Strategy
## Implementation Specification for AI Coding Agents

**Source:** Derived from the disclosed generation mechanics of Deriv Crash/Boom synthetic indices (continuous drift + periodic discrete jump), not a discretionary retail methodology.
**Instruments:** Crash 300/500/1000, Boom 300/500/1000 (and variants) — config is per-instrument, not shared.
**Version:** 1.0
**Purpose:** Machine-readable + human-readable spec for a two-strategy signal engine / backtester / live trading bot. Every threshold is a **default**. All parameters MUST be exposed as user-configurable (config file, env vars, or UI settings) — no magic numbers in code.

> ⚠️ **Disclaimer:** This is a technical specification for trading synthetic indices where the counterparty (Deriv) generates the price feed. It is not financial advice. Backtest and paper-trade extensively — including the random-parameter control test in §9 — before risking live capital. Past performance does not guarantee future results, and synthetic index parameters can change without notice.

---

## 1. Master Configuration Object

```json
{
  "strategy_suite": "CrashBoom_DriftAndJump",
  "version": "1.0",

  "instrument": {
    "symbol": "CRASH500",
    "product_type": "crash",
    "documented_avg_ticks_between_jumps": 500,
    "tick_or_bar_mode": "bar",
    "execution_timeframe": "1m",
    "drift_direction": "up",
    "jump_direction": "down",
    "pip_size": 0.001,
    "point_value": 1.0
  },

  "strategy_1_drift_continuation": {
    "enabled": true,
    "regime_filter": {
      "fast_ema_period": 20,
      "slow_ema_period": 50,
      "require_fast_above_slow_for_long": true,
      "require_fast_below_slow_for_short": true,
      "min_ema_separation_atr_multiple": 0.2
    },
    "entry_trigger": {
      "method": "pullback_higher_low",
      "pullback_reference": "fast_ema",
      "pullback_max_distance_atr_multiple": 1.0,
      "confirmation_candles_required": 1,
      "require_close_beyond_prior_swing": true,
      "swing_lookback_bars": 5
    },
    "gap_awareness": {
      "enabled": true,
      "use_empirical_gap_distribution": true,
      "gap_percentile_soft_warning": 75,
      "gap_percentile_hard_reduce": 90,
      "size_reduction_pct_at_hard_threshold": 50,
      "block_new_entries_above_percentile": 97
    },
    "exit": {
      "method": "atr_trailing_stop",
      "atr_period": 14,
      "atr_multiple": 2.0,
      "min_hold_bars_before_trailing": 3,
      "hard_take_profit_enabled": false,
      "hard_take_profit_rr": 4.0
    }
  },

  "strategy_2_jump_exposure_management": {
    "enabled": true,
    "mode": "risk_survival",
    "direct_jump_entry": {
      "enabled": false,
      "note": "Off by default. Only enable after §9 control test shows real edge, not just gap-distribution artifact."
    },
    "exposure_controls": {
      "max_position_hold_bars": 400,
      "reduce_size_as_gap_percentile_rises": true,
      "size_floor_pct_of_normal": 25,
      "flatten_all_at_percentile": 99,
      "reentry_cooldown_bars_after_jump": 5
    },
    "jump_detection": {
      "min_single_bar_move_atr_multiple": 4.0,
      "confirms_as": "jump_event",
      "post_jump_regime_reset": true
    }
  },

  "gap_distribution_engine": {
    "lookback_bars_for_fit": 20000,
    "recompute_every_n_bars": 1000,
    "candidate_distributions": ["exponential", "geometric", "erlang", "empirical_histogram"],
    "min_bars_before_trusting_fit": 5000,
    "store_full_history": true
  },

  "risk_management": {
    "risk_per_trade_pct": 1.0,
    "max_risk_per_trade_pct": 2.0,
    "min_risk_per_trade_pct": 0.25,
    "max_trades_per_day": 6,
    "max_concurrent_positions": 1,
    "max_daily_risk_pct": 4.0,
    "max_weekly_drawdown_pct": 8.0,
    "max_consecutive_losses": 4,
    "cooldown_after_max_losses_hours": 12,
    "min_rrr_to_accept_trade": 1.5
  },

  "stop_loss": {
    "placement": "structure_or_atr",
    "structure_reference": "last_swing",
    "atr_multiple": 1.5,
    "buffer_atr_multiple": 0.2,
    "widen_stop_near_high_gap_percentile": true,
    "widen_multiple_at_hard_threshold": 1.5
  },

  "take_profit": {
    "number_of_tps": 2,
    "tp_rr_levels": [1.5, 3.0],
    "tp_close_pct": [60, 40],
    "move_sl_to_breakeven_after_tp_index": 0
  },

  "trailing_stop": {
    "enabled": true,
    "method": "atr",
    "activate_after_rr": 1.0,
    "atr_multiple": 2.0
  },

  "session_filter": {
    "enabled": false,
    "note": "Synthetic indices trade 24/7 with no real session liquidity structure — filter left off by default. Enable only if backtest shows a genuine time-of-day artifact in Deriv's feed."
  },

  "backtest_controls": {
    "run_random_parameter_control": true,
    "control_runs": 200,
    "control_significance_threshold": 0.05,
    "report_strategy_1_and_2_separately": true,
    "walk_forward_enabled": true,
    "walk_forward_window_bars": 10000,
    "walk_forward_step_bars": 2000
  },

  "guardrails": {
    "block_if_gap_fit_untrusted": true,
    "block_if_daily_loss_limit_hit": true,
    "block_if_max_trades_hit": true,
    "block_if_max_concurrent_hit": true,
    "require_journal_entry_per_trade": true,
    "require_control_test_pass_before_live": true
  }
}
```

---

## 2. Design Premise

Crash/Boom indices are algorithmically generated: a continuous drift in one direction, interrupted by discrete jump events in the opposite direction, injected at a documented average tick frequency (e.g. "1 drop every 500 ticks" for Crash 500). There is no real order flow, so this spec deliberately avoids "smart money" narrative concepts. Two independent, separately-backtested strategies follow directly from this mechanism:

| Strategy | Targets | Core question it answers |
|---|---|---|
| **1 — Drift Continuation** | The majority-of-time drift regime | Can conventional trend-following extract the structural drift edge? |
| **2 — Jump Exposure Management** | The rare discrete jump event | How do I size/hold so a jump doesn't wreck an otherwise-good drift trade? |

`direct_jump_entry.enabled` is `false` by default — attempting to time and enter the jump itself is a separate, harder claim that requires passing the control test in §9 before being trusted.

---

## 3. Instrument Configuration

| Parameter | Default | Description |
|---|---|---|
| `symbol` | `CRASH500` | Deriv symbol; set per-instrument (Crash/Boom 300/500/1000, etc.) |
| `product_type` | `crash` | `crash` or `boom` — determines drift/jump direction defaults |
| `documented_avg_ticks_between_jumps` | 500 | From the platform's disclosed spec for this symbol; used only as a prior, never as a timing signal on its own (see §5) |
| `tick_or_bar_mode` | `bar` | Whether the gap-distribution engine measures raw ticks or execution-timeframe bars |
| `execution_timeframe` | `1m` | Bar size used for signal generation and execution |
| `drift_direction` | `up` (Crash) / `down` (Boom) | Structural direction of the continuous drift — Strategy 1 only trades this direction |
| `jump_direction` | `down` (Crash) / `up` (Boom) | Structural direction of the discrete jump |

**Rule:** `drift_direction` and `jump_direction` must always be opposite for a given instrument. Strategy 1 is long-only on Crash symbols and short-only on Boom symbols — never fight the structural drift.

---

## 4. Strategy 1 — Drift Continuation Engine

**Regime filter:**
```
regime_active(long) = EMA(fast_ema_period) > EMA(slow_ema_period)
                       AND (EMA_fast - EMA_slow) > min_ema_separation_atr_multiple × ATR
```
Only trade in `drift_direction`; the opposite regime state is not traded by Strategy 1.

**Entry trigger (pullback method):**
1. Price pulls back toward `pullback_reference` (default: fast EMA), within `pullback_max_distance_atr_multiple × ATR`.
2. A confirmation candle closes back in the drift direction, satisfying `confirmation_candles_required`.
3. If `require_close_beyond_prior_swing = true`, the confirmation close must also break the most recent swing point (lookback = `swing_lookback_bars`) — this is a plain structure check, not a discretionary "zone."

**Exit — ATR trailing stop:**
```
trail_stop = highest_price_since_entry − (atr_multiple × ATR)     # for longs
trail_stop = lowest_price_since_entry + (atr_multiple × ATR)      # for shorts
```
Trailing only activates after `min_hold_bars_before_trailing`. `hard_take_profit_enabled` is off by default — drift runs are allowed to run; enable + set `hard_take_profit_rr` if you prefer fixed targets.

---

## 5. Gap Distribution Engine (shared by both strategies)

This replaces discretionary "the crash is due" reasoning with an explicit, testable statistical model.

| Parameter | Default | Description |
|---|---|---|
| `lookback_bars_for_fit` | 20,000 | Historical window used to fit the gap distribution |
| `recompute_every_n_bars` | 1,000 | Re-fit frequency, since Deriv can adjust generator parameters |
| `candidate_distributions` | exponential, geometric, erlang, empirical histogram | Fit each; select via goodness-of-fit (e.g. KS test) rather than assuming memorylessness |
| `min_bars_before_trusting_fit` | 5,000 | Below this, `guardrails.block_if_gap_fit_untrusted` prevents any gap-based sizing logic from engaging |

**Interpretation rule, not a parameter — implement as logic:**
- If the best-fit distribution is exponential/geometric (memoryless), ticks-since-last-jump carries **no predictive information**. Gap-percentile-based sizing in §6/§7 still functions as pure risk management (reducing exposure as time-in-trade grows), but must NOT be described or logged as a "signal."
- If the best-fit distribution deviates significantly from memoryless (e.g. Erlang-like with a minimum spacing), the deviation itself — not the raw tick count — is the only legitimate statistical edge, and must be validated via §9 before being used for entries/exits rather than just position sizing.

---

## 6. Strategy 2 — Jump Exposure Management

**Purpose:** treat the jump as a risk event to survive within an otherwise-good drift trade, not a pattern to predict.

| Parameter | Default | Description |
|---|---|---|
| `mode` | `risk_survival` | Default mode; `direct_jump_entry` is a separate opt-in, not part of this mode |
| `max_position_hold_bars` | 400 | Forces reassessment of exposure as holding time approaches the historical average jump gap |
| `reduce_size_as_gap_percentile_rises` | true | Position size scales down as current ticks-since-last-jump climbs through the fitted distribution's percentiles |
| `size_floor_pct_of_normal` | 25 | Minimum size floor — never fully zero out an otherwise-valid Strategy 1 trade purely on gap timing |
| `flatten_all_at_percentile` | 99 | Hard flatten trigger — exits all Strategy-1-opened positions on this symbol |
| `reentry_cooldown_bars_after_jump` | 5 | Bars to wait after a detected jump before Strategy 1 can re-enter, letting the post-jump regime stabilize |

**Jump detection (for logging/regime-reset, not prediction):**
```
is_jump_event(bar) = abs(bar.close - bar.open) > min_single_bar_move_atr_multiple × ATR
                      AND direction(bar) == instrument.jump_direction
```
On detection: log the event, reset the gap-distribution counter to zero, and if `post_jump_regime_reset = true`, require the regime filter (§4) to re-qualify before Strategy 1 re-enters.

**Direct jump entry (opt-in, off by default):**
Only set `direct_jump_entry.enabled = true` after §9's control test shows a statistically significant edge over a matched-parameter synthetic control. Until then, this block should remain disabled in both backtest and live config.

---

## 7. Risk Management Engine

| Parameter | Default | Description |
|---|---|---|
| `risk_per_trade_pct` | 1.0% | % of equity risked per trade |
| `max_risk_per_trade_pct` | 2.0% | Hard ceiling, not overridable by user config |
| `min_risk_per_trade_pct` | 0.25% | Floor to avoid negligible sizing |
| `max_trades_per_day` | 6 | Higher than typical FX default since drift-continuation setups recur more often on synthetics |
| `max_concurrent_positions` | 1 | Per-symbol; raise deliberately if running multi-symbol |
| `max_daily_risk_pct` | 4.0% | Sum of open + realized risk today |
| `max_weekly_drawdown_pct` | 8.0% | Circuit breaker |
| `max_consecutive_losses` | 4 | Triggers cooldown |
| `cooldown_after_max_losses_hours` | 12 | Pause duration |
| `min_rrr_to_accept_trade` | 1.5 | Reject setups below this |

**Position sizing formula (identical structure to standard, with gap-aware modifier):**
```
base_size = (account_equity × risk_per_trade_pct/100) / (entry_price − stop_loss_price)

gap_pct = current_gap_percentile()
IF gap_pct >= exposure_controls.flatten_all_at_percentile:
    size = 0
ELSE IF gap_pct >= strategy_1.gap_awareness.gap_percentile_hard_reduce:
    size = base_size × (1 − size_reduction_pct_at_hard_threshold/100)
ELSE:
    size = base_size

size = max(size, base_size × exposure_controls.size_floor_pct_of_normal/100)
```

---

## 8. Stop Loss / Take Profit / Trailing Stop

**Stop Loss**

| Parameter | Default | Description |
|---|---|---|
| `placement` | `structure_or_atr` | SL at the wider of last swing point or ATR-based distance |
| `atr_multiple` | 1.5 | ATR-based SL distance |
| `buffer_atr_multiple` | 0.2 | Extra buffer beyond structure/ATR level |
| `widen_stop_near_high_gap_percentile` | true | Widen SL as gap percentile rises, since jump risk increases stop-slippage risk |
| `widen_multiple_at_hard_threshold` | 1.5× | Multiplier applied to normal SL distance once `gap_percentile_hard_reduce` is crossed |

**Take Profit**

| Parameter | Default | Description |
|---|---|---|
| `number_of_tps` | 2 | Scaled targets |
| `tp_rr_levels` | [1.5, 3.0] | RR for TP1 / TP2 |
| `tp_close_pct` | [60, 40] | % closed at each (must sum to 100) |
| `move_sl_to_breakeven_after_tp_index` | 0 | Move SL to entry after TP1 |

**Trailing Stop**

| Parameter | Default | Description |
|---|---|---|
| `enabled` | true | |
| `method` | `atr` | Simpler than structure-trailing given the drift-following nature of Strategy 1 |
| `activate_after_rr` | 1.0 | Trailing begins after 1:1 RR reached |
| `atr_multiple` | 2.0 | Trail distance |

---

## 9. Backtest Controls — Random-Parameter Control Test

This is the section that determines whether either strategy has a real statistical edge beyond the known drift+gap mechanics, and is required before `direct_jump_entry` or any live deployment.

| Parameter | Default | Description |
|---|---|---|
| `run_random_parameter_control` | true | Mandatory gate before live trading |
| `control_runs` | 200 | Number of synthetic control series generated |
| `control_significance_threshold` | 0.05 | p-value threshold for "real data outperforms control" |
| `report_strategy_1_and_2_separately` | true | Never pool results — different risk/return profiles |
| `walk_forward_enabled` | true | Avoid single-window overfitting |
| `walk_forward_window_bars` | 10,000 | In-sample window |
| `walk_forward_step_bars` | 2,000 | Step size for rolling re-fit and out-of-sample test |

**Procedure:**
1. Fit drift rate and the best-matching gap distribution (§5) from real historical data.
2. Generate `control_runs` synthetic price series using those same fitted parameters, with no additional structure beyond drift + randomly-timed jumps.
3. Run the exact same Strategy 1 / Strategy 2 rules against both real data and each synthetic control series.
4. Compare win rate, expectancy, and max drawdown distributions. If real-data performance is not statistically distinguishable from the control distribution (per `control_significance_threshold`), the strategy's edge is fully explained by drift + gap statistics — which is a legitimate, usable edge for Strategy 1, but means any additional discretionary/pattern claims (and specifically `direct_jump_entry`) are not supported and should stay disabled.

---

## 10. Guardrails

| Guardrail | Prevents |
|---|---|
| `block_if_gap_fit_untrusted` | Using gap-percentile sizing/exit logic before enough data has been collected to trust the fit |
| `block_if_daily_loss_limit_hit` | Revenge trading |
| `block_if_max_trades_hit` | Overtrading |
| `block_if_max_concurrent_hit` | Unintended pyramiding/over-exposure |
| `require_journal_entry_per_trade` | Skipping the trade journal |
| `require_control_test_pass_before_live` | Deploying a strategy live before §9's control test has run and been reviewed |

---

## 11. Trade Journal / Logging Schema

```json
{
  "timestamp": "ISO8601",
  "symbol": "string",
  "strategy": "strategy_1_drift | strategy_2_jump",
  "regime_at_entry": "drift_active | ranging",
  "gap_percentile_at_entry": 0.0,
  "gap_distribution_fit": "exponential | geometric | erlang | empirical",
  "entry_price": 0.0,
  "stop_loss": 0.0,
  "tp_levels": [0.0, 0.0],
  "risk_pct": 0.0,
  "size_adjustment_applied": "none | reduced | floored | flattened",
  "rrr_planned": 0.0,
  "result": "win | loss | breakeven | open",
  "rrr_realized": 0.0,
  "jump_event_during_trade": true,
  "rejection_reason": "string | null",
  "notes": "string"
}
```

---

## 12. End-to-End Algorithm Flow (Pseudocode)

```
ON new_bar(symbol):
    update_atr(symbol)
    update_ema_regime(symbol)                    # §4
    update_gap_distribution_if_due(symbol)        # §5
    check_jump_event(symbol)                      # §6 — logs + resets on detection

    IF guardrails.block_if_max_trades_hit AND trades_today >= max_trades_per_day: RETURN
    IF guardrails.block_if_daily_loss_limit_hit AND daily_risk_used >= max_daily_risk_pct: RETURN
    IF in_cooldown(symbol): RETURN
    IF guardrails.block_if_gap_fit_untrusted AND NOT gap_fit_trusted(symbol): 
        allow_entries_but_skip_gap_sizing = true

    IF strategy_1_drift_continuation.enabled AND regime_active(symbol):
        IF pullback_and_confirmation_triggered(symbol):                 # §4
            gap_pct = current_gap_percentile(symbol)
            IF gap_pct >= flatten_all_at_percentile: RETURN              # §6
            trade = build_trade(symbol, size = compute_gap_aware_size()) # §7
            IF trade.rrr >= risk_management.min_rrr_to_accept_trade:
                execute_entry(trade)
                log_journal(trade)

    IF strategy_2_jump_exposure_management.direct_jump_entry.enabled:
        IF control_test_passed(symbol) AND jump_setup_triggered(symbol):
            trade = build_trade(symbol, strategy="strategy_2_jump")
            execute_entry(trade)
            log_journal(trade)

ON open_position_update(symbol):
    manage_multi_tp(position)                     # §8
    manage_trailing_stop(position)                 # §8
    gap_pct = current_gap_percentile(symbol)
    IF exposure_controls.reduce_size_as_gap_percentile_rises:
        adjust_stop_and_size_for_gap(position, gap_pct)   # §6/§8
    IF gap_pct >= flatten_all_at_percentile:
        close(position)
    IF position.closed:
        update_consecutive_loss_counter(position.result)
        update_daily_risk_used(position)
        log_journal(position, final=true)

ON scheduled_interval (e.g. weekly):
    IF backtest_controls.run_random_parameter_control:
        run_control_test(symbol)                    # §9
        flag_if_edge_not_statistically_significant()
```

---

## 13. Configuration Overrides

- All values in §1 are defaults. Store user overrides in `config.override.json`, merged over defaults at load time.
- Hard ceilings enforced regardless of user config: `risk_per_trade_pct` cannot exceed `max_risk_per_trade_pct`; `max_daily_risk_pct` cannot exceed `max_weekly_drawdown_pct`; `direct_jump_entry.enabled` cannot be set `true` if `require_control_test_pass_before_live` is `true` and no passing control test is on record.

| Parameter | Safe Min | Safe Max |
|---|---|---|
| `risk_per_trade_pct` | 0.25 | 2.0 |
| `max_trades_per_day` | 1 | 10 |
| `max_consecutive_losses` | 2 | 6 |
| `min_rrr_to_accept_trade` | 1.2 | 4.0 |
| `atr_multiple` (trailing) | 1.0 | 3.0 |
| `gap_percentile_hard_reduce` | 70 | 95 |
| `flatten_all_at_percentile` | 95 | 99.5 |

---

## 14. Glossary

| Term | Meaning |
|---|---|
| Drift regime | The continuous, structurally-guaranteed directional movement between jump events |
| Jump / drop event | The discrete, large, roughly-periodic price move opposite to the drift direction |
| Gap distribution | Statistical distribution of ticks/bars between jump events, fitted from historical data |
| Gap percentile | Where current time-since-last-jump sits within the fitted gap distribution |
| Memoryless | A distribution (e.g. exponential/geometric) where elapsed time carries no predictive information about time-to-next-event |
| Control test | Backtest comparison against synthetic data matched on drift+gap parameters, used to isolate any real edge beyond known mechanics |
| RRR | Risk-to-Reward Ratio |
| SL / TP | Stop Loss / Take Profit |
| ATR | Average True Range |

---

*End of specification. Strategy 1 and Strategy 2 should be implemented as independently testable modules sharing the gap-distribution engine (§5) as common state. Only the Risk Management Engine (§7) and Guardrails (§10) are permitted to block or resize order execution. `direct_jump_entry` remains disabled until §9's control test passes.*
