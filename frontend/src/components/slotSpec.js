/**
 * What a slot is made of: which strategies exist, which schema group holds each
 * one's parameters, and which risk fields belong to the slot rather than the
 * account. Kept out of SlotEditor.jsx so that file exports a component only
 * (fast refresh), and so the Backtester can read the same lists.
 */

export const STRATEGY_OPTIONS = [
  ['VWAP_v1', 'VWAP Institutional', 'vwap'],
  ['ORB_v1', 'Opening Range Breakout', 'orb'],
  ['TrendBreakout_v1', 'Trend Breakout (Donchian)', 'trend_breakout'],
  ['FibTrend_v1', 'Fib Trend Pullback (H4 fib + trail)', 'fib_trend'],
  ['OvernightSession_v1', 'Overnight Session (index)', 'overnight_session'],
  ['SpikeResumption_v1', 'Spike Resumption (Boom/Crash)', 'spike_resumption'],
  ['DriftJumpAlpha_v1', 'Drift & Jump Alpha', 'drift_jump_alpha'],
  ['BoomDriftJump_v1', 'Boom Drift & Jump', 'boom_drift_jump'],
  ['SpikeFade_v1', 'Spike Fade', 'synth'],
  ['RangeRevert_v1', 'Range Revert', 'synth'],
  ['RangeBreakout_v1', 'Range Breakout', 'synth'],
  ['TrendDrift_v1', 'Trend Drift', 'synth'],
  // Reads forecasts the Kronos page precomputes; no parameter group of its own.
  ['KRONOS_v1', 'Kronos forecast (confident calls)', 'kronos_strategy'],
  // Replicated from roboquant.dev templates, 2026-10-19 (research/roboquant).
  ['RQ_NQPrecision_v1', 'NQ Precision Bot (roboquant)', 'rq_nq_precision'],
  ['RQ_TMeanReversion_v1', 'T-Mean Reversion (roboquant)', 'rq_t_mean_reversion'],
  ['RQ_CharlesMonger_v1', 'Charles Monger (roboquant)', 'rq_charles_monger'],
  ['RQ_IVB_v1', 'IVB Initial Volatility Breakout (roboquant)', 'rq_ivb'],
  ['RQ_GigaORB_v1', 'Giga ORB (roboquant)', 'rq_giga_orb'],
  ['RQ_SigmaBreakout_v1', 'Sigma Breakout (roboquant)', 'rq_sigma_breakout'],
  // AlphaVantiq research, 27 Nov 2026 (order-flow zones and price action). The order-flow
  // ones need Databento candles (the market-data layer); on MT5 bars they do not trade.
  ['AV_GoldenEngulfFlip_v1', 'Golden Engulf Flip (gold, order flow)', 'av_golden_engulf_flip'],
  ['AV_GoldenTwinAbsorb_v1', 'Golden Twin Absorb (gold, order flow)', 'av_golden_twin_absorb'],
  ['AV_NasdaqRejectionFlip_v1', 'Nasdaq Rejection Flip (order flow)', 'av_nasdaq_rejection_flip'],
  ['AV_NasdaqPrevDayDoji_v1', 'Nasdaq Prev-Day Doji (price action)', 'av_nasdaq_prev_day_doji'],
  ['AV_SPFreshDoji_v1', 'S&P Fresh Doji (order flow)', 'av_sp_fresh_doji'],
  ['AV_SPTwinRejection_v1', 'S&P Twin Rejection (order flow)', 'av_sp_twin_rejection'],
  ['AV_SilverAbsorbFlip_v1', 'Silver Absorb Flip (order flow)', 'av_silver_absorb_flip'],
  ['AV_DowBigTradePin_v1', 'Dow Big-Trade Pin (US30, order flow)', 'av_dow_big_trade_pin'],
  ['AV_DowAbsorptionZone_v1', 'Dow Absorption Zone (US30, order flow)', 'av_dow_absorption_zone'],
  ['AV_DowDojiRetest_v1', 'Dow Doji Retest (US30, price action)', 'av_dow_doji_retest'],
  ['AV_OilTrendEngulf_v1', 'Oil Trend Engulf (price action)', 'av_oil_trend_engulf'],
  ['AV_OilRetestDoji_v1', 'Oil Retest Doji (price action)', 'av_oil_retest_doji'],
  ['AV_NikkeiTrendEngulf_v1', 'Nikkei Trend Engulf (price action)', 'av_nikkei_trend_engulf'],
  ['AV_DAXRetestRejection_v1', 'DAX Retest Rejection (price action)', 'av_dax_retest_rejection'],
  ['AV_CableLiquiditySweep_v1', 'Cable Liquidity Sweep (GBP/USD, order flow)', 'av_cable_liquidity_sweep'],
  ['QL_IBS_v1', 'IBS Mean Reversion (quant lab, US indices)', 'ql_ibs'],
  ['QL_DipBuy_v1', 'Dip-Buy Combo: IBS / cumRSI / 3-down (quant lab, NAS & SPX)', 'ql_dipbuy'],
  ['RQ_DailyVWAP_v1', 'Daily Anchored VWAP (roboquant)', 'rq_daily_vwap'],
  ['RQ_FridayRevisit_v1', 'Friday Range Revisit Monday Short (roboquant)', 'rq_friday_revisit'],
  ['RQ_M30ORB_v1', 'M30 ORB + M5 Breakout (roboquant)', 'rq_m30_orb'],
  ['RQ_SRSweep_v1', 'S&R Sweep (roboquant)', 'rq_sr_sweep'],
  ['ZeroLossBoomCrash_v32', 'Zero Loss Boom & Crash v3.2', 'zero_loss_boom_crash'],
];

export const STRATEGY_GROUP = Object.fromEntries(STRATEGY_OPTIONS.map(([id, , g]) => [id, g]));
export const STRATEGY_LABEL = Object.fromEntries(STRATEGY_OPTIONS.map(([id, l]) => [id, l]));

// The risk a slot owns, in the order a trade meets it. Everything else in
// RiskParams is either an account fact (margin, leverage) or legacy.
export const SLOT_RISK_SECTIONS = [
  ['Sizing', ['risk_per_trade_pct', 'max_risk_hard_cap_pct', 'min_lot_floor', 'sizing_basis',
    'sizing_static_balance', 'slot_brake_r', 'min_sl_pips']],
  // max_concurrent_positions and the profit halts are read by THIS slot's
  // circuit breaker, so they cap this slot and nothing else.
  ['Entry limits', ['max_daily_trades', 'max_concurrent_positions', 'max_positions_per_symbol',
    'max_daily_drawdown_pct', 'max_weekly_drawdown_pct', 'min_bars_between_entries',
    'allow_pyramiding']],
  // A target is (scope, basis, action, amount), resolved per symbol x strategy.
  // The amounts are ordered under the switches that decide which of them is read
  // at all, because a $50 per-trade amount means nothing until TRADE is armed.
  ['Profit targets', ['target_profit_enabled', 'profit_target_scopes', 'profit_target_basis',
    'profit_target_action', 'profit_target_is_pct',
    'max_trade_profit', 'max_daily_profit', 'max_weekly_profit', 'max_monthly_profit',
    // where DAY/WEEK/MONTH roll over; WAT (+1) by default, not midnight UTC
    'accounting_utc_offset_hours']],
  // The full ladder: every field here is read by multi_tp / trailing_manager /
  // exit_replay, and the per-TP rows are hidden above this slot's TP count (see
  // SlotEditor) so a 1-TP slot is not asked about TP5.
  ['Targets', ['tp_count', 'tp1_rr', 'tp2_rr', 'tp3_rr', 'tp4_rr', 'tp5_rr',
    'tp_splits', 'min_rr']],
  ['Break-even', ['be_mode', 'be_trigger_rr', 'be_trigger_tp_level', 'be_buffer_pips',
    'be_buffer_atr_mult', 'be_spread_multiple']],
  ['Trailing', ['trail_mode', 'trail_trigger_rr', 'trail_trigger_tp_level',
    'trail_method_tp1', 'trail_method_tp2', 'trail_method_tp3', 'trail_method_tp4',
    'trail_method_tp5',
    'atr_trail_multiplier', 'atr_trail_multiplier_tp1', 'atr_trail_multiplier_tp2',
    'atr_trail_multiplier_tp3', 'atr_trail_multiplier_tp4', 'atr_trail_multiplier_tp5',
    'trail_pips', 'trail_pct', 'trail_step_pips', 'trail_structure_bars',
    'trail_require_be_first']],
];

// Rows that only mean something up to a slot's TP count: `tp4_rr` on a slot
// that takes one target is noise, and hiding it is why the ladder can be
// complete without the panel becoming a wall.
export const TP_LEVEL_OF = (name) => {
  const m = /(?:^tp(\d)_rr$)|(?:_tp(\d)$)/.exec(name);
  return m ? Number(m[1] || m[2]) : null;
};

export const SLOT_RISK_KEYS = SLOT_RISK_SECTIONS.flatMap(([, keys]) => keys);

// Mirrors backend/risk/slot_book.py ACCOUNT_ONLY_KEYS: these describe the
// account, the broker or the simulation, so a slot may not override them and
// they are edited once, in Settings > Defaults. Everything else in RiskParams
// belongs to the slot — which is why the editor renders the leftovers rather
// than a second hand-kept list that can fall behind the dataclass.
export const ACCOUNT_ONLY_KEYS = [
  'prop_firm', 'is_backtest', 'mt5_account', 'max_account_leverage',
  'max_margin_utilisation_pct', 'mt5_order_deviation_points', 'simulate_wicks',
  'stop_fill_model', 'stop_fill_lambda', 'stop_fill_seed',
  'simulate_backtest_only_exits',
];

/** A one-line summary of what this slot will do, for the collapsed row. */
export function slotSummary(slot, accountRisk = {}) {
  const r = slot.risk || {};
  const pick = (k, fallback) => (r[k] ?? accountRisk[k] ?? fallback);
  const bits = [
    `${pick('risk_per_trade_pct', slot.risk_per_trade_pct ?? 1)}% risk`,
    `${pick('max_daily_trades', slot.max_trades_per_day ?? '—')}/day`,
    `1:${pick('tp1_rr', slot.tp1_rr ?? '—')}`,
  ];
  const be = pick('be_mode', 'NONE');
  const trail = pick('trail_mode', 'NONE');
  bits.push(be === 'NONE' ? 'BE off' : `BE ${be}`);
  bits.push(trail === 'NONE' ? 'trail off' : `trail ${trail}`);
  const brake = Number(pick('slot_brake_r', 0));
  if (brake > 0) bits.push(`brake ${brake}R`);
  return bits.join(' · ');
}

