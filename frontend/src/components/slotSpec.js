/**
 * What a slot is made of: which strategies exist, which schema group holds each
 * one's parameters, and which risk fields belong to the slot rather than the
 * account. Kept out of SlotEditor.jsx so that file exports a component only
 * (fast refresh), and so the Backtester can read the same lists.
 */

export const STRATEGY_OPTIONS = [
  ['APA_v1', 'APA (Adv. Price Action)', 'apa'],
  ['VWAP_v1', 'VWAP Institutional', 'vwap'],
  ['ORB_v1', 'Opening Range Breakout', 'orb'],
  ['IVW_v1', 'IV Walls Breakout', 'ivw'],
  ['DriftJumpAlpha_v1', 'Drift & Jump Alpha', 'drift_jump_alpha'],
  ['BoomDriftJump_v1', 'Boom Drift & Jump', 'boom_drift_jump'],
  ['HTFFVGFlip_v1', 'HTF FVG Flip', 'htf_fvg_flip'],
  ['BiasIFVG_v1', 'Bias KeyLevel IFVG', 'bias_ifvg'],
  ['SpikeFade_v1', 'Spike Fade', 'synth'],
  ['RangeRevert_v1', 'Range Revert', 'synth'],
  ['RangeBreakout_v1', 'Range Breakout', 'synth'],
  ['TrendDrift_v1', 'Trend Drift', 'synth'],
];

export const STRATEGY_GROUP = Object.fromEntries(STRATEGY_OPTIONS.map(([id, , g]) => [id, g]));
export const STRATEGY_LABEL = Object.fromEntries(STRATEGY_OPTIONS.map(([id, l]) => [id, l]));

// The risk a slot owns, in the order a trade meets it. Everything else in
// RiskParams is either an account fact (margin, leverage) or legacy.
export const SLOT_RISK_SECTIONS = [
  ['Sizing', ['risk_per_trade_pct', 'max_risk_hard_cap_pct', 'sizing_basis',
    'sizing_static_balance', 'slot_brake_r', 'min_sl_pips']],
  // max_concurrent_positions and the profit halts are read by THIS slot's
  // circuit breaker, so they cap this slot and nothing else.
  ['Entry limits', ['max_daily_trades', 'max_concurrent_positions', 'max_positions_per_symbol',
    'max_daily_drawdown_pct', 'max_weekly_drawdown_pct', 'min_bars_between_entries',
    'allow_pyramiding']],
  ['Profit halts', ['target_profit_enabled', 'max_daily_profit', 'max_weekly_profit']],
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

