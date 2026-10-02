# Vol over Crash/Boom — 2026-01-01 to 2026-09-29

$10,000 opening balance, 1.0% risk per trade, run through the app's own backtest path with the **saved** strategy settings.

```json
{
 "DriftJumpAlpha_v1": {
  "spike_lookback_bars": 50,
  "drift_ema_fast": 20,
  "drift_ema_slow": 50,
  "min_adx_to_trade": 20,
  "jump_entry_percentile_threshold": 95,
  "trade_jumps_enabled": true,
  "aggregate_max_lots_per_symbol": 10,
  "spike_threshold_pips": 0,
  "recovery_target_pips": 0,
  "max_trades_per_day": 20,
  "max_daily_risk_pct": 20,
  "max_consecutive_losses": 20,
  "cooldown_after_max_losses_hours": 0,
  "min_rrr_to_accept_trade": 1.5
 },
 "BoomDriftJump_v1": {
  "drift_ema_fast": 20,
  "drift_ema_slow": 50,
  "min_adx_to_trade": 20,
  "jump_entry_percentile_threshold": 95,
  "trade_jumps_enabled": false,
  "min_rrr_to_accept_trade": 1.5,
  "max_trades_per_day": 20,
  "max_daily_risk_pct": 20,
  "adx_gate_mode": "REDUCED_SIZE",
  "adx_gate_min_size_modifier": 0.1,
  "tp1_rr": 5
 },
 "TrendDrift_v1": {
  "stop_atr_multiple": 5,
  "tp1_rr": 5,
  "spike_k_atr": 3,
  "revert_k_atr": 2,
  "breakout_lookback": 20,
  "ema_fast": 20,
  "ema_slow": 50,
  "require_adx": true,
  "min_adx_to_trade": 20,
  "max_trades_per_day": 6,
  "max_daily_risk_pct": 4
 }
}
```

## Flat risk — compare strategies on this

| Symbol | Strategy | Trades | WR | P&L $ | Return % | Max DD % (peak) | PF | Exp R | Ret/DD |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Vol over Boom 750 | BoomDriftJump_v1 | 4839 | 33% | $111,454 | +1114.5% | 54.5% | 1.50 | +0.000 | 20.47 |
| Vol over Boom 550 | BoomDriftJump_v1 | 4798 | 33% | $94,734 | +947.3% | 22.5% | 1.44 | +0.000 | 42.09 |
| Vol over Crash 750 | DriftJumpAlpha_v1 | 3248 | 33% | $89,183 | +891.8% | 40.5% | 1.46 | +0.000 | 22.05 |
| Vol over Boom 400 | BoomDriftJump_v1 | 4637 | 33% | $87,850 | +878.5% | 32.6% | 1.43 | +0.000 | 26.95 |
| Vol over Crash 400 | DriftJumpAlpha_v1 | 3231 | 28% | $26,038 | +260.4% | 90.5% | 1.13 | +0.000 | 2.88 |
| Vol over Crash 550 | TrendDrift_v1 | 1084 | 33% | $25,247 | +252.5% | 12.8% | 1.46 | +0.000 | 19.76 |
| Vol over Crash 550 | DriftJumpAlpha_v1 | 3058 | 27% | $16,615 | +166.1% | 57.4% | 1.09 | +0.000 | 2.89 |
| Vol over Boom 550 | TrendDrift_v1 | 1084 | 30% | $13,893 | +138.9% | 37.3% | 1.24 | +0.000 | 3.72 |
| Vol over Crash 400 | TrendDrift_v1 | 1084 | 27% | $5,343 | +53.4% | 43.7% | 1.09 | +0.000 | 1.22 |
| Vol over Boom 400 | TrendDrift_v1 | 1084 | 27% | $5,303 | +53.0% | 40.9% | 1.09 | +0.000 | 1.30 |
| Vol over Boom 750 | TrendDrift_v1 | 1084 | 26% | $3,494 | +34.9% | 49.0% | 1.06 | +0.000 | 0.71 |
| Vol over Crash 750 | TrendDrift_v1 | 1084 | 23% | $-7,932 | -79.3% | 112.5% | 0.88 | +0.000 | -0.71 |
| Vol over Crash 400 | BoomDriftJump_v1 | 0 | — | — | — | — | — | — | — |
| Vol over Crash 550 | BoomDriftJump_v1 | 0 | — | — | — | — | — | — | — |
| Vol over Crash 750 | BoomDriftJump_v1 | 0 | — | — | — | — | — | — | — |
| Vol over Boom 400 | DriftJumpAlpha_v1 | 0 | — | — | — | — | — | — | — |
| Vol over Boom 550 | DriftJumpAlpha_v1 | 0 | — | — | — | — | — | — | — |
| Vol over Boom 750 | DriftJumpAlpha_v1 | 0 | — | — | — | — | — | — | — |

## Compounded — what the app shows

| Symbol | Strategy | Trades | WR | P&L $ | Return % | Max DD % (peak) | PF | Exp R | Ret/DD |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Vol over Boom 400 | BoomDriftJump_v1 | 4851 | 31% | $1,096,544 | +10965.4% | 63.4% | 1.26 | +0.000 | 172.85 |
| Vol over Crash 750 | DriftJumpAlpha_v1 | 3269 | 33% | $983,640 | +9836.4% | 62.4% | 1.28 | +0.000 | 157.71 |
| Vol over Boom 750 | BoomDriftJump_v1 | 4719 | 33% | $947,764 | +9477.6% | 63.9% | 1.49 | +0.000 | 148.30 |
| Vol over Boom 550 | BoomDriftJump_v1 | 4941 | 33% | $856,277 | +8562.8% | 50.5% | 1.37 | +0.000 | 169.49 |
| Vol over Crash 550 | TrendDrift_v1 | 1084 | 33% | $77,052 | +770.5% | 35.2% | 1.27 | +0.000 | 21.88 |
| Vol over Boom 550 | TrendDrift_v1 | 1084 | 30% | $18,213 | +182.1% | 58.0% | 1.18 | +0.000 | 3.14 |
| Vol over Crash 400 | DriftJumpAlpha_v1 | 3037 | 29% | $5,033 | +50.3% | 87.9% | 1.01 | +0.000 | 0.57 |
| Vol over Boom 400 | TrendDrift_v1 | 1084 | 27% | $2,381 | +23.8% | 40.8% | 1.04 | +0.000 | 0.58 |
| Vol over Crash 400 | TrendDrift_v1 | 1084 | 27% | $2,379 | +23.8% | 58.6% | 1.03 | +0.000 | 0.41 |
| Vol over Boom 750 | TrendDrift_v1 | 1084 | 26% | $366 | +3.7% | 51.5% | 1.01 | +0.000 | 0.07 |
| Vol over Crash 550 | DriftJumpAlpha_v1 | 2851 | 28% | $-4,951 | -49.5% | 97.5% | 1.00 | +0.000 | -0.51 |
| Vol over Crash 750 | TrendDrift_v1 | 1084 | 23% | $-6,588 | -65.9% | 72.6% | 0.81 | +0.000 | -0.91 |
| Vol over Crash 400 | BoomDriftJump_v1 | 0 | — | — | — | — | — | — | — |
| Vol over Crash 550 | BoomDriftJump_v1 | 0 | — | — | — | — | — | — | — |
| Vol over Crash 750 | BoomDriftJump_v1 | 0 | — | — | — | — | — | — | — |
| Vol over Boom 400 | DriftJumpAlpha_v1 | 0 | — | — | — | — | — | — | — |
| Vol over Boom 550 | DriftJumpAlpha_v1 | 0 | — | — | — | — | — | — | — |
| Vol over Boom 750 | DriftJumpAlpha_v1 | 0 | — | — | — | — | — | — | — |
