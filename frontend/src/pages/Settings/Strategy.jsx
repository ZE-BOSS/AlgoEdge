import { useState, useEffect, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Sliders, Save, Loader2, Check, Plus } from 'lucide-react';
import { getConfig, updateConfig, getParameterSchema } from '../../services/api';
import { invalidateConfigDependents } from '../../utils/invalidate';
import { useConnectionStore, useAuthStore } from '../../store';
import SlotEditor from '../../components/SlotEditor';
import SymbolPicker from '../../components/SymbolPicker';
import { useSymbolOptions } from '../../hooks/useSymbolOptions';
import { STRATEGY_GROUP, STRATEGY_OPTIONS } from '../../components/slotSpec';

/**
 * The trading book: one row per symbol + strategy, each with its OWN strategy
 * parameters and its OWN risk. That pairing is the unit everywhere now — the
 * live bot, a single backtest and a portfolio row all run a slot.
 *
 * This page used to be a wall of ~300 symbol chips followed by nine global
 * strategy parameter cards. Those cards were shared by every slot running that
 * strategy, so ORB on gold and ORB on GBPJPY could not have different ranges or
 * timeframes, and the risk that applied to them lived on another page entirely.
 * Both are gone: parameters are edited on the slot that uses them
 * (components/SlotEditor.jsx), generated from the backend's own schema.
 */


export default function StrategySettings() {
  const { status } = useConnectionStore();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const queryClient = useQueryClient();
  const [saved, setSaved] = useState(false);
  const [newSymbol, setNewSymbol] = useState('XAUUSD');
  const [newStrategy, setNewStrategy] = useState('APA_v1');

  const [config, setConfig] = useState({
    // [per-slot] Read-only here: the account defaults a slot inherits when it
    // sets nothing of its own. Shown under each field in the slot's Risk panel,
    // and stripped from this page's save so it can never overwrite the Risk page.
    risk: {},
    symbols: ['XAUUSD', 'XAGUSD', 'XPTUSD', 'EURUSD', 'GBPUSD'], // legacy support
    instrument_settings: [
      { symbol: 'XAUUSD', strategy_id: 'APA_v1', enabled: true },
      { symbol: 'EURUSD', strategy_id: 'APA_v1', enabled: true },
      { symbol: 'GBPUSD', strategy_id: 'APA_v1', enabled: true }
    ],
    // [P2.1] The authoritative symbol x strategy configuration. The backend has
    // supported InstrumentSlot (UUID-keyed, so ONE symbol may appear in several
    // slots under different strategies) since [12.1] — slot-aware engines,
    // circuit breaker and risk engine are all already in place. This page was
    // the only thing still writing the symbol-keyed `instrument_settings`
    // array, and config_schema then auto-migrated it to exactly one slot per
    // symbol, so the multi-slot path was never reachable from the UI.
    // `instrument_settings` is still written below as a derived projection,
    // because other screens read it.
    instrument_slots: [],
    // Section names and field names below must match the backend config
    // dataclasses exactly (backend/core/config_schema.py and each strategy's
    // params.py) — TradingConfig.from_dict() filters every section through a
    // hasattr()-style check and silently drops anything it does not recognise.
    // Defaults mirror the authoritative backend values.
    apa: {
      structure_timeframe: 'M15',
      entry_timeframe: 'M5',
      minor_fractal_m: 3,
      major_fractal_m: 8,
      shoulder_symmetry_tolerance_atr: 0.3,
      tight_level_threshold_atr: 0.35,
      sl_buffer_atr: 0.05,
      sl_buffer_atr_mult: 0.5,
      min_sl_pips: 12.0,
      min_sl_atr_mult: 1.0,
      invalidation_zone_source: 'right_shoulder',
      session_filter_enabled: true,
      session_start: '07:00',
      session_cutoff: '16:00',
      atr_lookback: 14,
    },
    vwap: {
      vwap_anchor_minutes: 15,
      entry_timeframe: 'M5',
      momentum_lookback_bars: 4,
      momentum_threshold_pct: 0.1,
      sl_method: 'auto',
      sl_points: 80.0,
      sl_atr_multiplier: 3.0,
      min_sl_pips: 8.0,
      min_sl_spread_mult: 4.0,
      target_rr: 2.0,
      session_open: '09:30',
      session_exclude_end: '10:30',
      entry_cutoff: '15:30',
      hard_close: '15:55',
      max_trades_per_day: 4,
      max_losses_per_day: 2,
      drawdown_kill_pct: 10.0,
    },
    // Boom mirror of DriftJumpAlpha (BoomDriftJumpParams).
    boom_drift_jump: {
      drift_ema_fast: 20,
      drift_ema_slow: 50,
      min_adx_to_trade: 20,
      jump_entry_percentile_threshold: 95.0,
      trade_jumps_enabled: false,
      min_rrr_to_accept_trade: 1.5,
      max_trades_per_day: 6,
      max_daily_risk_pct: 4.0,
      adx_gate_mode: 'REDUCED_SIZE',
      adx_gate_min_size_modifier: 0.1,
      tp1_rr: 5.0,
    },
    drift_jump_alpha: {
      // spike_lookback_bars removed — no such field on DriftJumpAlphaParams and no
      // reference anywhere in backend/, so it was silently dropped by the hasattr filter.
      drift_ema_fast: 20,
      drift_ema_slow: 50,
      min_adx_to_trade: 20,
      jump_entry_percentile_threshold: 95.0,
      trade_jumps_enabled: false,
      control_test_passed: false,
      aggregate_max_lots_per_symbol: 6.0,
      spike_threshold_pips: 0.0,
      recovery_target_pips: 0.0,
      max_trades_per_day: 6,
      max_daily_risk_pct: 4.0,
      max_consecutive_losses: 4,
      cooldown_after_max_losses_hours: 12,
      min_rrr_to_accept_trade: 1.5,
    },
    orb: {
      session: 'london',
      range_minutes: 60,
      breakout_window_minutes: 180,
      side: 'both',
      min_stop_atr: 0.25,
      close_at_session_end: true,
    },
    ivw: {
      wall_percentile: 90,
      lookback_days: 60,
      regime_filter: 'low',
      require_bubble: true,
      forecast_filter: 'any',
      stop_width_frac: 0.5,
      side: 'both',
      close_at_day_end: true,
    },
    // Shared by SpikeFade / RangeRevert / RangeBreakout / TrendDrift — all four
    // read the same backend dataclass (SynthParams), so one section serves them.
    synth: {
      stop_atr_multiple: 5.0,
      tp1_rr: 5.0,
      spike_k_atr: 3.0,
      revert_k_atr: 2.0,
      breakout_lookback: 20,
      ema_fast: 20,
      ema_slow: 50,
      require_adx: true,
      min_adx_to_trade: 20,
      max_trades_per_day: 6,
      max_daily_risk_pct: 4.0,
    },
    htf_fvg_flip: {
      session_filter_enabled: true,
      session_start: '09:30',
      session_cutoff: '16:00',
      htf_timeframe: 'H1',
      entry_confirmation_tf: 'M5',
      target_rr: 2.0,
      require_unfilled_htf_fvg: true,
      sl_buffer_atr_mult: 0.5,
      min_sl_pips: 12.0,
      min_sl_atr_mult: 1.0,
    },
    bias_ifvg: {
      session_start: '09:30',
      session_cutoff: '11:00',
      max_trades_per_day: 2,
      target_rr: 2.0,
      sl_buffer_atr_mult: 0.5,
      min_sl_pips: 12.0,
      min_sl_atr_mult: 1.0,
      a_plus_confluence_threshold: 90,
      rejection_min_body_atr_mult: 0.15,
    },
  });

  // Load current config from backend

  const { data: schemaResp } = useQuery({
    queryKey: ['parameter-schema'],
    queryFn: () => getParameterSchema().then(r => r.data),
    enabled: status === 'ONLINE' && isAuthenticated,
    staleTime: 60 * 60 * 1000,
  });
  const schema = schemaResp?.fields;

  const { data: remoteConfig } = useQuery({
    queryKey: ['config'],
    queryFn: () => getConfig().then(r => r.data),
    enabled: status === 'ONLINE' && isAuthenticated,
  });

  useEffect(() => {
    if (remoteConfig?.config) {
      setConfig(prev => {
        const merged = {
          ...prev,
          ...Object.fromEntries(
            Object.entries(remoteConfig.config).filter(([k]) => k in prev).map(([k, v]) => {
              if (typeof v === 'object' && v !== null && !Array.isArray(v) && typeof prev[k] === 'object') {
                return [k, { ...prev[k], ...v }];
              }
              return [k, v];
            })
          ),
        };
        // A config saved before this page wrote slots carries only
        // `instrument_settings`. Project it to one slot per symbol — the same
        // migration config_schema.py performs server-side.
        if (!merged.instrument_slots?.length && merged.instrument_settings?.length) {
          merged.instrument_slots = merged.instrument_settings.map((i, n) => ({
            slot_id: `legacy${String(n).padStart(7, '0')}`,
            symbol: i.symbol,
            strategy_id: i.strategy_id || 'APA_v1',
            enabled: i.enabled !== false,
          }));
        }
        return merged;
      });
    }
  }, [remoteConfig]);

  const mutation = useMutation({
    mutationFn: (newConfig) => updateConfig({ config: newConfig }),
    onSuccess: () => {
      invalidateConfigDependents(queryClient);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    },
  });

  const handleSave = () => {
    // `risk` belongs to the Risk page. This page only reads it (to show what a
    // slot inherits), so sending it back could overwrite a change made there
    // while this page was open.
    const toSave = { ...config };
    delete toSave.risk;
    mutation.mutate(toSave);
  };

  const slots = useMemo(() => config.instrument_slots || [], [config.instrument_slots]);

  // Everything that writes slots goes through here, so `symbols` (which
  // /bot/start reads) and the legacy `instrument_settings` projection can never
  // fall out of step with them.
  const commitSlots = (nextSlots) => {
    const enabled = nextSlots.filter(s => s.enabled !== false);
    const symbols = [...new Set(enabled.map(s => s.symbol))];
    const legacy = symbols.map(sym => {
      const first = enabled.find(s => s.symbol === sym);
      return { symbol: sym, strategy_id: first.strategy_id, enabled: true };
    });
    setConfig({ ...config, instrument_slots: nextSlots, instrument_settings: legacy, symbols });
  };

  const newSlotId = () =>
    (crypto?.randomUUID?.() || Math.random().toString(16).slice(2).padEnd(12, '0'))
      .replace(/-/g, '').slice(0, 12);

  const addSlot = () => {
    if (!newSymbol) return;
    commitSlots([...slots, {
      slot_id: newSlotId(), symbol: newSymbol, strategy_id: newStrategy,
      enabled: true, strategy_params: {}, risk: {},
    }]);
  };

  // Broker names first (Deriv lists the Nasdaq as "US Tech 100"), then the
  // symbols this book already trades. Typing anything else is still allowed.
  const symbolOptions = useSymbolOptions(
    (config.instrument_slots || []).map(sl => sl.symbol).filter(Boolean),
  );
  const updateSlot = (slotId, next) =>
    commitSlots(slots.map(s => (s.slot_id === slotId ? { ...next, slot_id: slotId } : s)));
  const removeSlot = (slotId) => commitSlots(slots.filter(s => s.slot_id !== slotId));
  const duplicateSlot = (slotId) => {
    const src = slots.find(s => s.slot_id === slotId);
    if (src) commitSlots([...slots, { ...src, slot_id: newSlotId() }]);
  };

  const duplicatePairings = useMemo(() => {
    const seen = new Set();
    const dupes = new Set();
    slots.forEach(s => {
      const key = `${s.symbol}::${s.strategy_id}`;
      if (seen.has(key)) dupes.add(key);
      seen.add(key);
    });
    return dupes;
  }, [slots]);

  // What the book risks when everything goes wrong at once. Nothing caps it any
  // more — each slot stops only itself — so it is shown rather than enforced.
  const exposure = useMemo(() => {
    const enabled = slots.filter(s => s.enabled !== false);
    const acct = config.risk || {};
    const num = (v, d) => (v === undefined || v === null || v === '' ? d : Number(v));
    const worstDay = enabled.reduce((sum, s) =>
      sum + num(s.risk?.max_daily_drawdown_pct, num(acct.max_daily_drawdown_pct, 0)), 0);
    const openRisk = enabled.reduce((sum, s) =>
      sum + num(s.risk?.risk_per_trade_pct, num(s.risk_per_trade_pct, num(acct.risk_per_trade_pct, 0)))
        * num(s.risk?.max_positions_per_symbol, num(s.max_positions_per_symbol, num(acct.max_positions_per_symbol, 1))), 0);
    return { count: enabled.length, worstDay, openRisk };
  }, [slots, config.risk]);

  return (
    <div style={{ display: 'grid', gap: 20, maxWidth: 1100 }}>
      <div className="card">
        <div className="card-header">
          <span className="card-title"><Sliders size={14} /> Trading Book</span>
          <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
            {slots.length} slot{slots.length === 1 ? '' : 's'}
          </span>
        </div>

        <p style={{ fontSize: '0.78rem', color: 'var(--text-muted)', marginTop: 0 }}>
          One row is one symbol running one strategy, with its own parameters and its own risk.
          Add the same symbol twice to run two strategies on it: each gets its own engine, its own
          daily budget and its own position quota, and neither can block the other. This is the
          same unit the Backtester runs, so the slot you test is the slot that trades.
        </p>

        {exposure.count > 0 && (
          <div style={{
            marginBottom: 12, padding: 10, background: 'var(--bg-tertiary)',
            border: '1px solid var(--border)', borderRadius: 'var(--radius-xs)',
            fontSize: '0.75rem', color: 'var(--text-secondary)',
          }}>
            <strong>{exposure.count} slot{exposure.count === 1 ? '' : 's'} enabled.</strong>{' '}
            Each stops itself and nothing stops them together: if every one hit its own daily limit
            on the same day, that day would cost <strong>{exposure.worstDay.toFixed(1)}%</strong> of
            the account, and with one position open in each,{' '}
            <strong>{exposure.openRisk.toFixed(1)}%</strong> is at risk.
          </div>
        )}

        {duplicatePairings.size > 0 && (
          <div style={{
            marginBottom: 12, padding: 10, background: 'var(--bg-warning)',
            border: '1px solid var(--yellow)', borderRadius: 'var(--radius-xs)',
            color: 'var(--yellow)', fontSize: '0.78rem',
          }}>
            <strong>Duplicate slot{duplicatePairings.size > 1 ? 's' : ''}:</strong>{' '}
            {[...duplicatePairings].join(', ')} — the same strategy on the same symbol twice gives it
            two budgets and two quotas on one instrument, doubling exposure without adding a signal.
            Remove one, or change its strategy.
          </div>
        )}

        <div className="slot-list">
          {slots.length === 0 && (
            <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', padding: '12px 0' }}>
              No slots yet. Add one below — it starts from the account defaults on the Risk page and
              the measured settings for that symbol.
            </div>
          )}
          {slots.map(slot => (
            <SlotEditor
              key={slot.slot_id}
              slot={slot}
              schema={schema}
              accountRisk={config.risk}
              strategyDefaults={config[STRATEGY_GROUP[slot.strategy_id]] || {}}
              symbols={symbolOptions}
              collapsible
              defaultOpen={false}
              onChange={next => updateSlot(slot.slot_id, next)}
              onRemove={() => removeSlot(slot.slot_id)}
              onDuplicate={() => duplicateSlot(slot.slot_id)}
            />
          ))}
        </div>

        <div className="slot-add">
          <SymbolPicker
            className="slot-add__symbol"
            value={newSymbol}
            onChange={setNewSymbol}
            options={symbolOptions}
            placeholder="Symbol"
          />
          <select className="input input-sm" value={newStrategy} onChange={e => setNewStrategy(e.target.value)}>
            {STRATEGY_OPTIONS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
          </select>
          <button className="btn btn-primary btn-sm" onClick={addSlot}>
            <Plus size={13} /> Add slot
          </button>
        </div>
      </div>

      <button className="btn btn-primary" style={{ justifySelf: 'start' }} onClick={handleSave} disabled={mutation.isPending}>
        {mutation.isPending ? <Loader2 size={14} className="spin" /> : saved ? <Check size={14} /> : <Save size={14} />}
        {mutation.isPending ? 'Saving...' : saved ? 'Saved!' : 'Save Trading Book'}
      </button>
      {mutation.isError && (
        <div style={{ color: 'var(--red)', fontSize: '0.8rem' }}>
          Failed to save: {mutation.error?.response?.data?.detail || mutation.error?.message}
        </div>
      )}
    </div>
  );
}
