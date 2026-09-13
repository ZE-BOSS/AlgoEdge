import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Sliders, Save, Loader2, Check } from 'lucide-react';
import { getConfig, updateConfig } from '../../services/api';
import { invalidateConfigDependents } from '../../utils/invalidate';
import { useConnectionStore, useAuthStore } from '../../store';

export default function StrategySettings() {
  const { status } = useConnectionStore();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const queryClient = useQueryClient();
  const [saved, setSaved] = useState(false);

  const [config, setConfig] = useState({
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
  });

  // Load current config from backend
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
        // [P2.1] A config saved before this page wrote slots carries only
        // `instrument_settings`. Project it to one slot per symbol — the same
        // migration config_schema.py performs server-side, so what the form
        // shows is what the backend already resolved.
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
      // Every cached response that embeds config, not just ['config'] itself —
      // the dashboard, the live-account risk resolution and the stats all
      // derive from these values and kept serving pre-save copies.
      invalidateConfigDependents(queryClient);
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    },
  });

  const updateNested = (section, key, val) => {
    setConfig({
      ...config,
      [section]: {
        ...(config[section] || {}),
        [key]: val
      }
    });
  };
  const handleSave = () => mutation.mutate(config);

  const allSymbols = [
    'XAUUSD', 'Gold', 'XAU', 'XAGUSD', 'Silver', 'XAG', 'XPTUSD', 'Platinum', 'XPT',
    'EURUSD', 'GBPUSD', 'AUDUSD', 'Aussie', 'GBPJPY', 'Geppy', 'GJ',
    'GBPNZD', 'GBPAUD', 'GBPCHF', 'EURJPY', 'EURAUD', 'USDJPY',
    'USDCHF', 'USDCAD', 'NZDUSD', 'Kiwi', 'AUDJPY', 'CADJPY', 'GBPCAD', 'EURGBP',
    'US30', 'Wall Street 30', 'WS30', 'DJI', 'DOW', 'YM',
    'NAS100', 'US100', 'USTEC', 'NDX', 'NQ',
    'SPX500', 'US500', 'SPX', 'SP500', 'S&P500', 'ES',
    'GER40', 'DAX', 'DE40', 'GER30', 'HK50', 'Hang Seng', 'HSI',
    'US2000', 'RUT', 'UK100', 'FTSE100', 'FRA40', 'CAC40',
    'EU50', 'EUSTX50', 'NTH25', 'AEX25', 'SWI20', 'SMI20',
    'AUS200', 'ASX200', 'JP225', 'Nikkei',
    'USOIL', 'WTI', 'Crude Oil', 'OIL', 'XTIUSD', 'US Oil',
    'UKOIL', 'Brent', 'UK Brent Oil', 'XCUUSD', 'Copper',
    'NG', 'XNGUSD', 'Natural Gas',
    'BTCUSD', 'Bitcoin', 'BTC', 'ETHUSD', 'ETH', 'Ethereum',
    'DOGUSD', 'Dogecoin', 'DOGE', 'SOLUSD', 'Solana', 'SOL',
    'XRPUSD', 'Ripple', 'XRP', 'LTCUSD', 'Litecoin', 'LTC',
    'Volatility 10 Index', 'Volatility 25 Index', 'Volatility 50 Index',
    'Volatility 75 Index', 'Volatility 100 Index', 'Volatility 150 Index', 'Volatility 250 Index',
    'Volatility 10 (1s) Index', 'Volatility 25 (1s) Index', 'Volatility 50 (1s) Index',
    'Volatility 75 (1s) Index', 'Volatility 100 (1s) Index', 'Volatility 150 (1s) Index', 'Volatility 250 (1s) Index',
    'Boom 300 Index', 'Boom 500 Index', 'Boom 1000 Index',
    'Crash 300 Index', 'Crash 500 Index', 'Crash 1000 Index',
    'Jump 10 Index', 'Jump 25 Index', 'Jump 50 Index', 'Jump 75 Index', 'Jump 100 Index',
    'Step Index', 'Range Break 100 Index', 'Range Break 200 Index',
  ];

  // ── [P2.1] Slot model ────────────────────────────────────────────────────
  // A slot is one (symbol, strategy) pairing. The same symbol may appear in as
  // many slots as you like; each gets its own engine instance, its own daily
  // budget and its own position quota on the backend.
  const slots = config.instrument_slots || [];
  const activeSymbols = [...new Set(slots.filter(s => s.enabled).map(s => s.symbol))];

  // Everything that writes slots goes through here, so `symbols` (which
  // /bot/start uses to decide what is live) and the legacy
  // `instrument_settings` projection can never fall out of step with them.
  const commitSlots = (nextSlots) => {
    const enabled = nextSlots.filter(s => s.enabled);
    const symbols = [...new Set(enabled.map(s => s.symbol))];
    // One legacy row per symbol — first enabled slot wins. Read-only as far as
    // this page is concerned; the backend prefers instrument_slots when present.
    const legacy = symbols.map(sym => {
      const first = enabled.find(s => s.symbol === sym);
      return { symbol: sym, strategy_id: first.strategy_id, enabled: true };
    });
    setConfig({
      ...config,
      instrument_slots: nextSlots,
      instrument_settings: legacy,
      symbols,
    });
  };

  const newSlotId = () =>
    (crypto?.randomUUID?.() || Math.random().toString(16).slice(2).padEnd(12, '0'))
      .replace(/-/g, '').slice(0, 12);

  const addSlot = (symbol, strategyId = 'APA_v1') => {
    if (!symbol) return;
    commitSlots([...slots, { slot_id: newSlotId(), symbol, strategy_id: strategyId, enabled: true }]);
  };

  const updateSlot = (slotId, key, val) =>
    commitSlots(slots.map(s => (s.slot_id === slotId ? { ...s, [key]: val } : s)));

  const removeSlot = (slotId) => commitSlots(slots.filter(s => s.slot_id !== slotId));

  const duplicateSlot = (slotId) => {
    const src = slots.find(s => s.slot_id === slotId);
    if (!src) return;
    commitSlots([...slots, { ...src, slot_id: newSlotId() }]);
  };

  // Toggling a symbol chip adds a first slot for it, or disables every slot on
  // it. Removing individual strategies is done on the slot row itself.
  const toggleSymbol = (sym) => {
    const existing = slots.filter(s => s.symbol === sym);
    if (existing.length === 0) { addSlot(sym); return; }
    const anyEnabled = existing.some(s => s.enabled);
    commitSlots(slots.map(s => (s.symbol === sym ? { ...s, enabled: !anyEnabled } : s)));
  };

  // A slot pairing is only meaningful once — two identical (symbol, strategy)
  // rows would give one strategy two independent daily budgets on one symbol,
  // which is double-counting, not diversification.
  const duplicatePairings = new Set(
    slots
      .map(s => `${s.symbol}::${s.strategy_id}`)
      .filter((k, i, arr) => arr.indexOf(k) !== i)
  );



  return (
    <div style={{ display: 'grid', gap: 20, maxWidth: 800 }}>
      <div className="card">
        <div className="card-header"><span className="card-title"><Sliders size={14} /> Active Symbols</span></div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
          {[...new Set([...allSymbols, ...activeSymbols])].map(sym => (
            <button
              key={sym}
              className={`btn btn-sm ${activeSymbols.includes(sym) ? 'btn-primary' : 'btn-secondary'}`}
              onClick={() => toggleSymbol(sym)}
            >
              {sym}
            </button>
          ))}
          <input
            type="text"
            placeholder="Add custom symbol... (Enter)"
            className="input"
            style={{ width: 220, height: 32, fontSize: '0.875rem' }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && e.target.value.trim()) {
                const newSym = e.target.value.trim();
                if (!activeSymbols.includes(newSym)) {
                  toggleSymbol(newSym);
                }
                e.target.value = '';
              }
            }}
          />
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          <span className="card-title">Strategy Slots</span>
        </div>
        <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginBottom: 12 }}>
          One row = one symbol running one strategy. Add the same symbol more than once to
          run several strategies on it at the same time — each slot gets its own engine,
          its own daily budget and its own position quota, and they cannot block each
          other's entries. This is the same pairing the portfolio backtester uses, so a
          basket you tested there can be reproduced here row for row.
        </div>

        {duplicatePairings.size > 0 && (
          <div style={{ marginBottom: 12, padding: 10, background: 'var(--bg-warning)', border: '1px solid var(--yellow)', borderRadius: 'var(--radius-xs)', color: 'var(--yellow)', fontSize: '0.8rem' }}>
            <strong>Duplicate slot{duplicatePairings.size > 1 ? 's' : ''}:</strong>{' '}
            {[...duplicatePairings].join(', ')} — the same strategy is on the same symbol
            twice. That gives one strategy two independent daily budgets and two position
            quotas on one instrument, which doubles exposure without adding a signal.
            Remove one, or change its strategy.
          </div>
        )}

        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {slots.length === 0 && (
            <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', padding: '12px 0' }}>
              No slots configured. Pick a symbol above, or add one below.
            </div>
          )}
          {slots.map(slot => {
            const dup = duplicatePairings.has(`${slot.symbol}::${slot.strategy_id}`);
            return (
              <div
                key={slot.slot_id}
                style={{
                  display: 'flex', alignItems: 'center', gap: 12, padding: '10px 14px',
                  background: 'var(--bg-tertiary)', borderRadius: 'var(--radius-sm)',
                  border: `1px solid ${dup ? 'var(--yellow)' : 'var(--border)'}`,
                  opacity: slot.enabled ? 1 : 0.5,
                }}
              >
                <input
                  type="checkbox"
                  checked={slot.enabled !== false}
                  onChange={e => updateSlot(slot.slot_id, 'enabled', e.target.checked)}
                  title={slot.enabled ? 'Enabled — the bot scans this slot' : 'Disabled'}
                  style={{ width: 14, height: 14 }}
                />
                <select
                  value={slot.symbol}
                  onChange={e => updateSlot(slot.slot_id, 'symbol', e.target.value)}
                  style={{ fontSize: '0.8rem', padding: '4px 8px', width: 200 }}
                >
                  {[...new Set([...allSymbols, slot.symbol])].map(sym => (
                    <option key={sym} value={sym}>{sym}</option>
                  ))}
                </select>
                <select
                  value={slot.strategy_id || 'APA_v1'}
                  onChange={e => updateSlot(slot.slot_id, 'strategy_id', e.target.value)}
                  style={{ fontSize: '0.8rem', padding: '4px 8px', flex: 1, minWidth: 200 }}
                >
                      <option value="APA_v1">APA (Adv. Price Action)</option>
                      <option value="VWAP_v1">VWAP Institutional</option>
                      <option value="ORB_v1">Opening Range Breakout</option>
                      <option value="DriftJumpAlpha_v1">Drift &amp; Jump Alpha</option>
                      <option value="BoomDriftJump_v1">Boom Drift &amp; Jump</option>
                      <option value="Donchian_v1">Donchian Breakout</option>
                      <option value="EMAPullback_v1">EMA Trend Pullback</option>
                      <option value="RSI2_v1">RSI(2) Reversion</option>
                      <option value="BollingerFade_v1">Bollinger Fade</option>
                      <option value="VolBreakout_v1">Tick-Volume Breakout</option>
                      <option value="TSMOM_v1">Daily Momentum (TSMOM)</option>
                </select>
                <label
                  style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '0.75rem', whiteSpace: 'nowrap', cursor: 'pointer' }}
                  title="On: this slot runs the settings measured for this symbol (the Backtester's 'Use the measured settings' box). Off: it runs the strategy parameters below, exactly as the Backtester does with that box unticked."
                >
                  <input
                    type="checkbox"
                    checked={slot.use_measured_params !== false}
                    onChange={e => updateSlot(slot.slot_id, 'use_measured_params', e.target.checked)}
                    style={{ width: 14, height: 14 }}
                  />
                  Measured settings
                </label>
                <button
                  className="btn btn-secondary btn-sm"
                  onClick={() => duplicateSlot(slot.slot_id)}
                  title="Add another strategy on this symbol"
                >
                  Duplicate
                </button>
                <button
                  className="btn btn-secondary btn-sm"
                  onClick={() => removeSlot(slot.slot_id)}
                  title="Remove this slot"
                >
                  Remove
                </button>
              </div>
            );
          })}
        </div>

        <div style={{ marginTop: 12, display: 'flex', gap: 8, alignItems: 'center' }}>
          <select
            id="new-slot-symbol"
            defaultValue={allSymbols[0]}
            style={{ fontSize: '0.8rem', padding: '4px 8px', width: 200 }}
          >
            {allSymbols.map(sym => <option key={sym} value={sym}>{sym}</option>)}
          </select>
          <button
            className="btn btn-primary btn-sm"
            onClick={() => addSlot(document.getElementById('new-slot-symbol')?.value)}
          >
            + Add slot
          </button>
          <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
            {activeSymbols.length} symbol{activeSymbols.length === 1 ? '' : 's'} ·{' '}
            {slots.filter(x => x.enabled).length} active slot
            {slots.filter(x => x.enabled).length === 1 ? '' : 's'}
          </span>
        </div>
      </div>



      <div className="card">
        <div className="card-header"><span className="card-title">APA (Advanced Price Action) Parameters</span></div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16 }}>
          <div><label>Setup</label><select value={config.apa?.setup_mode || 'HEAD_AND_SHOULDERS'} onChange={e => updateNested('apa', 'setup_mode', e.target.value)}><option value="HEAD_AND_SHOULDERS">Head &amp; shoulders (original)</option><option value="SESSION_BREAKOUT_TREND">Session breakout with the trend (edge lab)</option></select><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>Session breakout = the ORB-trend price-action setup under this APA slot. Don't also run ORB_v1 on the same symbol, or both take the same trade.</div></div>
          <div><label>Breakout Session</label><select value={config.apa?.breakout_session || 'native'} onChange={e => updateNested('apa', 'breakout_session', e.target.value)}><option value="native">Native</option><option value="alt">The other one</option><option value="london">London</option><option value="ny">New York</option></select></div>
          <div><label>Structure Timeframe</label><input type="text" value={config.apa?.structure_timeframe || 'M15'} onChange={e => updateNested('apa', 'structure_timeframe', e.target.value)} /></div>
          <div><label>Entry Timeframe</label><input type="text" value={config.apa?.entry_timeframe || 'M5'} onChange={e => updateNested('apa', 'entry_timeframe', e.target.value)} /></div>
          <div><label>Minor Fractal (M)</label><input type="number" value={config.apa?.minor_fractal_m ?? 3} onChange={e => updateNested('apa', 'minor_fractal_m', +e.target.value)} /></div>
          <div><label>Major Fractal (M)</label><input type="number" value={config.apa?.major_fractal_m ?? 8} onChange={e => updateNested('apa', 'major_fractal_m', +e.target.value)} /></div>
          <div><label>Shoulder Symmetry (× ATR)</label><input type="number" step="0.05" min="0" value={config.apa?.shoulder_symmetry_tolerance_atr ?? 0.3} onChange={e => updateNested('apa', 'shoulder_symmetry_tolerance_atr', +e.target.value)} /></div>
          <div><label>Tight Level Threshold (× ATR)</label><input type="number" step="0.05" min="0" value={config.apa?.tight_level_threshold_atr ?? 0.35} onChange={e => updateNested('apa', 'tight_level_threshold_atr', +e.target.value)} /><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>Head and Shoulder closer than this × ATR → SL covers both wicks (the wider, survivable branch).</div></div>
          <div><label>SL Buffer (× ATR)</label><input type="number" step="0.05" min="0" value={config.apa?.sl_buffer_atr_mult ?? 0.5} onChange={e => updateNested('apa', 'sl_buffer_atr_mult', +e.target.value)} /></div>
          <div><label>Min SL (pips)</label><input type="number" step="0.5" min="0" value={config.apa?.min_sl_pips ?? 12.0} onChange={e => updateNested('apa', 'min_sl_pips', +e.target.value)} /><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>Absolute stop floor. 0 disables.</div></div>
          <div><label>Min SL (× ATR)</label><input type="number" step="0.1" min="0" value={config.apa?.min_sl_atr_mult ?? 1.0} onChange={e => updateNested('apa', 'min_sl_atr_mult', +e.target.value)} /><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>Volatility-relative floor. Larger floor wins. 0 disables.</div></div>
          <div>
            <label>Invalidation Zone Source</label>
            <select value={config.apa?.invalidation_zone_source || 'right_shoulder'} onChange={e => updateNested('apa', 'invalidation_zone_source', e.target.value)}>
              <option value="right_shoulder">Right Shoulder (conservative)</option>
              <option value="both">Left + Right Shoulder (wider)</option>
            </select>
          </div>
          <div><label>Session Start (UTC)</label><input type="text" value={config.apa?.session_start || '07:00'} onChange={e => updateNested('apa', 'session_start', e.target.value)} /></div>
          <div><label>Session Cutoff (UTC)</label><input type="text" value={config.apa?.session_cutoff || '16:00'} onChange={e => updateNested('apa', 'session_cutoff', e.target.value)} /></div>
          <div><label>ATR Lookback</label><input type="number" value={config.apa?.atr_lookback ?? 14} onChange={e => updateNested('apa', 'atr_lookback', +e.target.value)} /></div>
          <div>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', marginTop: 12 }}>
              <input type="checkbox" checked={config.apa?.session_filter_enabled ?? true} onChange={e => updateNested('apa', 'session_filter_enabled', e.target.checked)} />
              Enable Session Filter
            </label>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-header"><span className="card-title">VWAP Institutional Parameters</span></div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16 }}>
          <div><label>VWAP Anchor (min)</label><input type="number" value={config.vwap?.vwap_anchor_minutes ?? 15} onChange={e => updateNested('vwap', 'vwap_anchor_minutes', +e.target.value)} /></div>
          <div><label>Entry Timeframe</label><input type="text" value={config.vwap?.entry_timeframe || 'M5'} onChange={e => updateNested('vwap', 'entry_timeframe', e.target.value)} /></div>
          <div><label>Momentum Lookback (bars)</label><input type="number" value={config.vwap?.momentum_lookback_bars ?? 4} onChange={e => updateNested('vwap', 'momentum_lookback_bars', +e.target.value)} /></div>
          <div><label>Momentum Threshold (%)</label><input type="number" step="0.01" value={config.vwap?.momentum_threshold_pct ?? 0.1} onChange={e => updateNested('vwap', 'momentum_threshold_pct', +e.target.value)} /></div>
          <div>
            <label>SL Method</label>
            <select value={config.vwap?.sl_method || 'auto'} onChange={e => updateNested('vwap', 'sl_method', e.target.value)}>
              <option value="auto">Auto (by instrument class)</option>
              <option value="fixed_points">Fixed Points</option>
              <option value="atr_multiple">ATR Multiple</option>
            </select>
            <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>Auto: index CFDs/futures use SL Points; FX, metals, crypto and synthetics use the ATR multiple.</div>
          </div>
          <div><label>SL Points (index only)</label><input type="number" step="1" min="0" value={config.vwap?.sl_points ?? 80.0} onChange={e => updateNested('vwap', 'sl_points', +e.target.value)} /><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>Native index points, not pipettes. Ignored on FX.</div></div>
          <div><label>SL ATR Multiplier</label><input type="number" step="0.1" min="0" value={config.vwap?.sl_atr_multiplier ?? 3.0} onChange={e => updateNested('vwap', 'sl_atr_multiplier', +e.target.value)} /></div>
          <div><label>Min SL (pips)</label><input type="number" step="0.5" min="0" value={config.vwap?.min_sl_pips ?? 8.0} onChange={e => updateNested('vwap', 'min_sl_pips', +e.target.value)} /></div>
          <div><label>Min SL (× Spread)</label><input type="number" step="0.5" min="0" value={config.vwap?.min_sl_spread_mult ?? 4.0} onChange={e => updateNested('vwap', 'min_sl_spread_mult', +e.target.value)} /></div>
          <div><label>Target R:R</label><input type="number" step="0.1" min="0" value={config.vwap?.target_rr ?? 2.0} onChange={e => updateNested('vwap', 'target_rr', +e.target.value)} /></div>
          <div><label>Session Open</label><input type="text" value={config.vwap?.session_open || '09:30'} onChange={e => updateNested('vwap', 'session_open', e.target.value)} /></div>
          <div><label>Session Exclude End</label><input type="text" value={config.vwap?.session_exclude_end || '10:30'} onChange={e => updateNested('vwap', 'session_exclude_end', e.target.value)} /></div>
          <div><label>Entry Cutoff</label><input type="text" value={config.vwap?.entry_cutoff || '15:30'} onChange={e => updateNested('vwap', 'entry_cutoff', e.target.value)} /></div>
          <div><label>Hard Close</label><input type="text" value={config.vwap?.hard_close || '15:55'} onChange={e => updateNested('vwap', 'hard_close', e.target.value)} /></div>
          <div><label>Max Trades / Day</label><input type="number" value={config.vwap?.max_trades_per_day ?? 4} onChange={e => updateNested('vwap', 'max_trades_per_day', +e.target.value)} /></div>
          <div><label>Max Losses / Day</label><input type="number" value={config.vwap?.max_losses_per_day ?? 2} onChange={e => updateNested('vwap', 'max_losses_per_day', +e.target.value)} /></div>
          <div><label>Drawdown Kill (%)</label><input type="number" step="0.5" value={config.vwap?.drawdown_kill_pct ?? 10.0} onChange={e => updateNested('vwap', 'drawdown_kill_pct', +e.target.value)} /></div>
          <div><label>Entry Mode</label><select value={config.vwap?.entry_mode || 'PULLBACK_TO_VALUE'} onChange={e => updateNested('vwap', 'entry_mode', e.target.value)}><option value="PULLBACK_TO_VALUE">Pullback to value (original)</option><option value="BAND_REVERSION">Band reversion</option><option value="BOTH">Both</option><option value="SESSION_TREND">Session trend (Zarattini &amp; Aziz)</option><option value="SESSION_PULLBACK">Session pullback</option></select></div>
          <div><label>Session-Mode Session</label><select value={config.vwap?.session_mode_session || 'native'} onChange={e => updateNested('vwap', 'session_mode_session', e.target.value)}><option value="native">Native</option><option value="alt">The other one</option><option value="london">London</option><option value="ny">New York</option></select></div>
          <div><label>Session-Mode Confluences</label><input type="text" value={(config.vwap?.session_mode_gates || ['day_dir', 'early']).join(', ')} onChange={e => updateNested('vwap', 'session_mode_gates', e.target.value.split(',').map(s => s.trim()).filter(Boolean))} /><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>Session modes only: day_dir, gap_dir, prev_day_dir, early, htf_trend, vwap_slope, vwap_side, vol_surge, rel_vol_open, strong_body, noise_out, nr7, inside_day.</div></div>
        </div>
      </div>

      <div className="card">
        <div className="card-header"><span className="card-title">Drift & Jump Alpha Parameters</span></div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16 }}>
          <div><label>Drift EMA Fast</label><input type="number" value={config.drift_jump_alpha?.drift_ema_fast || 20} onChange={e => updateNested('drift_jump_alpha', 'drift_ema_fast', +e.target.value)} /></div>
          <div><label>Drift EMA Slow</label><input type="number" value={config.drift_jump_alpha?.drift_ema_slow || 50} onChange={e => updateNested('drift_jump_alpha', 'drift_ema_slow', +e.target.value)} /></div>
          <div><label>Min ADX to Trade</label><input type="number" value={config.drift_jump_alpha?.min_adx_to_trade || 20} onChange={e => updateNested('drift_jump_alpha', 'min_adx_to_trade', +e.target.value)} /></div>
          <div><label>Jump Entry Threshold (%)</label><input type="number" value={config.drift_jump_alpha?.jump_entry_percentile_threshold || 95.0} onChange={e => updateNested('drift_jump_alpha', 'jump_entry_percentile_threshold', +e.target.value)} /></div>
          <div><label>Max Lots per Symbol</label><input type="number" step="0.1" value={config.drift_jump_alpha?.aggregate_max_lots_per_symbol || 6.0} onChange={e => updateNested('drift_jump_alpha', 'aggregate_max_lots_per_symbol', +e.target.value)} /></div>
          <div><label>Spike Threshold (pips)</label><input type="number" step="0.5" min="0" value={config.drift_jump_alpha?.spike_threshold_pips ?? 0.0} onChange={e => updateNested('drift_jump_alpha', 'spike_threshold_pips', +e.target.value)} /><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>0 = auto (derived from the spike percentile).</div></div>
          <div><label>Recovery Target (pips)</label><input type="number" step="0.5" min="0" value={config.drift_jump_alpha?.recovery_target_pips ?? 0.0} onChange={e => updateNested('drift_jump_alpha', 'recovery_target_pips', +e.target.value)} /><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>0 = auto.</div></div>
          <div><label>Max Trades / Day</label><input type="number" min="0" value={config.drift_jump_alpha?.max_trades_per_day ?? 6} onChange={e => updateNested('drift_jump_alpha', 'max_trades_per_day', +e.target.value)} /></div>
          <div><label>Max Daily Risk (%)</label><input type="number" step="0.5" min="0" value={config.drift_jump_alpha?.max_daily_risk_pct ?? 4.0} onChange={e => updateNested('drift_jump_alpha', 'max_daily_risk_pct', +e.target.value)} /></div>
          <div><label>Max Consecutive Losses</label><input type="number" min="0" value={config.drift_jump_alpha?.max_consecutive_losses ?? 4} onChange={e => updateNested('drift_jump_alpha', 'max_consecutive_losses', +e.target.value)} /></div>
          <div><label>Cooldown After Max Losses (h)</label><input type="number" min="0" value={config.drift_jump_alpha?.cooldown_after_max_losses_hours ?? 12} onChange={e => updateNested('drift_jump_alpha', 'cooldown_after_max_losses_hours', +e.target.value)} /></div>
          <div><label>Min RRR to Accept Trade</label><input type="number" step="0.1" min="0" value={config.drift_jump_alpha?.min_rrr_to_accept_trade ?? 1.5} onChange={e => updateNested('drift_jump_alpha', 'min_rrr_to_accept_trade', +e.target.value)} /></div>
          <div>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', marginTop: 12 }}>
              <input type="checkbox" checked={config.drift_jump_alpha?.trade_jumps_enabled ?? false} onChange={e => updateNested('drift_jump_alpha', 'trade_jumps_enabled', e.target.checked)} />
              Enable Jump Trades (Setup B)
            </label>
          </div>
          <div>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', marginTop: 12 }}>
              <input type="checkbox" checked={config.drift_jump_alpha?.control_test_passed ?? false} onChange={e => updateNested('drift_jump_alpha', 'control_test_passed', e.target.checked)} />
              Control Test Passed
            </label>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-header"><span className="card-title">Opening Range Breakout Parameters</span></div>
        <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginBottom: 12 }}>Trades the first close beyond the opening range, once per session, and flattens at the session close. Target = the slot TP1 R:R. On slots with "Measured settings" on, the measured per-symbol values apply instead of these: M5 break of the 60-minute range only with the H1 trend, 1:3 — GBPJPY London; US Tech 100, XAUUSD, BTCUSD New York.</div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16 }}>
          <div><label>Session</label><select value={config.orb?.session || 'london'} onChange={e => updateNested('orb', 'session', e.target.value)}><option value="london">London (08:00 UK)</option><option value="ny">New York (09:30 ET)</option></select></div>
          <div><label>Range (minutes)</label><select value={config.orb?.range_minutes ?? 60} onChange={e => updateNested('orb', 'range_minutes', +e.target.value)}>{[15, 30, 45, 60, 90, 120].map(m => <option key={m} value={m}>{m}</option>)}</select></div>
          <div><label>Breakout Window (minutes)</label><input type="number" step="15" min="15" value={config.orb?.breakout_window_minutes ?? 180} onChange={e => updateNested('orb', 'breakout_window_minutes', +e.target.value)} /></div>
          <div><label>Side</label><select value={config.orb?.side || 'both'} onChange={e => updateNested('orb', 'side', e.target.value)}><option value="both">Both</option><option value="long">Long only</option><option value="short">Short only</option></select></div>
          <div><label>Min Stop (× ATR)</label><input type="number" step="0.05" min="0" value={config.orb?.min_stop_atr ?? 0.25} onChange={e => updateNested('orb', 'min_stop_atr', +e.target.value)} /></div>
          <div style={{ gridColumn: '1 / -1' }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: '0.8rem' }}>
              <input type="checkbox" checked={config.orb?.close_at_session_end ?? true} onChange={e => updateNested('orb', 'close_at_session_end', e.target.checked)} />
              Close at session end (part of the tested rule)
            </label>
          </div>
          <div><label>Breakout Timeframe</label><select value={config.orb?.breakout_timeframe || 'M15'} onChange={e => updateNested('orb', 'breakout_timeframe', e.target.value)}><option value="M15">M15 (original)</option><option value="M5">M5 (edge lab)</option></select></div>
          <div>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', marginTop: 24, fontSize: '0.8rem' }}>
              <input type="checkbox" checked={config.orb?.require_trend ?? false} onChange={e => updateNested('orb', 'require_trend', e.target.checked)} />
              Only with the H1 trend (M5 form)
            </label>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-header"><span className="card-title">Classic Strategy Families</span></div>
        <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginBottom: 12 }}>
          Donchian, EMA pullback, RSI(2), Bollinger fade, tick-volume breakout and daily momentum. Each engine runs the research code itself; exits (ATR trail, channel, mean, flip, time limit) are the strategy's own and run live as well as in backtests. Slots with "Measured settings" on use the per-symbol values instead of these.
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16 }}>
          <div><label>Donchian Channel (H1 bars)</label><input type="number" min="5" value={config.donchian?.channel_bars ?? 20} onChange={e => updateNested('donchian', 'channel_bars', parseInt(e.target.value, 10))} /></div>
          <div><label>Donchian Stop (× ATR)</label><input type="number" step="0.5" min="0.5" value={config.donchian?.stop_atr ?? 2.0} onChange={e => updateNested('donchian', 'stop_atr', +e.target.value)} /></div>
          <div><label>Donchian Exit / Side</label><div style={{ display: 'flex', gap: 6 }}><select value={config.donchian?.exit_mode || 'trail'} onChange={e => updateNested('donchian', 'exit_mode', e.target.value)}><option value="trail">3×ATR trail</option><option value="channel">Channel</option></select><select value={config.donchian?.side || 'both'} onChange={e => updateNested('donchian', 'side', e.target.value)}><option value="both">Both</option><option value="long">Long only</option></select></div></div>
          <div><label>EMA Pullback Fast / Slow</label><div style={{ display: 'flex', gap: 6 }}><input type="number" min="2" value={config.ema_pullback?.fast_ema ?? 20} onChange={e => updateNested('ema_pullback', 'fast_ema', parseInt(e.target.value, 10))} /><input type="number" min="3" value={config.ema_pullback?.slow_ema ?? 50} onChange={e => updateNested('ema_pullback', 'slow_ema', parseInt(e.target.value, 10))} /></div></div>
          <div><label>EMA Pullback Side</label><select value={config.ema_pullback?.side || 'both'} onChange={e => updateNested('ema_pullback', 'side', e.target.value)}><option value="both">Both</option><option value="long">Long only</option></select></div>
          <div><label>RSI(2) Threshold / Max Hold (H1)</label><div style={{ display: 'flex', gap: 6 }}><input type="number" step="1" min="1" value={config.rsi2?.threshold ?? 10} onChange={e => updateNested('rsi2', 'threshold', +e.target.value)} /><input type="number" min="1" value={config.rsi2?.max_hold_bars ?? 24} onChange={e => updateNested('rsi2', 'max_hold_bars', parseInt(e.target.value, 10))} /></div></div>
          <div><label>RSI(2) Side</label><select value={config.rsi2?.side || 'both'} onChange={e => updateNested('rsi2', 'side', e.target.value)}><option value="both">Both</option><option value="long">Long only</option></select></div>
          <div><label>Bollinger Band (σ) / Side</label><div style={{ display: 'flex', gap: 6 }}><input type="number" step="0.1" min="0.5" value={config.bollinger_fade?.band_sigma ?? 2.0} onChange={e => updateNested('bollinger_fade', 'band_sigma', +e.target.value)} /><select value={config.bollinger_fade?.side || 'both'} onChange={e => updateNested('bollinger_fade', 'side', e.target.value)}><option value="both">Both</option><option value="long">Long only</option></select></div></div>
          <div><label>Vol Breakout Channel / Volume ×</label><div style={{ display: 'flex', gap: 6 }}><input type="number" min="5" value={config.vol_breakout?.channel_bars ?? 20} onChange={e => updateNested('vol_breakout', 'channel_bars', parseInt(e.target.value, 10))} /><input type="number" step="0.1" min="1" value={config.vol_breakout?.volume_mult ?? 1.5} onChange={e => updateNested('vol_breakout', 'volume_mult', +e.target.value)} /></div></div>
          <div><label>Vol Breakout Side</label><select value={config.vol_breakout?.side || 'both'} onChange={e => updateNested('vol_breakout', 'side', e.target.value)}><option value="both">Both</option><option value="long">Long only</option></select></div>
          <div><label>TSMOM Lookback (days) / Side</label><div style={{ display: 'flex', gap: 6 }}><input type="number" min="5" value={config.tsmom?.lookback_days ?? 60} onChange={e => updateNested('tsmom', 'lookback_days', parseInt(e.target.value, 10))} /><select value={config.tsmom?.side || 'both'} onChange={e => updateNested('tsmom', 'side', e.target.value)}><option value="both">Both</option><option value="long">Long only</option></select></div></div>
        </div>
      </div>

      <div className="card">
        <div className="card-header"><span className="card-title">Boom Drift &amp; Jump Parameters</span></div>
        <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginBottom: 12 }}>
          Boom mirror of Drift &amp; Jump Alpha: sells the downward grind, buys after an up-spike. Shipped defaults from research/25.
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16 }}>
          <div><label>Drift EMA Fast</label><input type="number" value={config.boom_drift_jump?.drift_ema_fast ?? 20} onChange={e => updateNested('boom_drift_jump', 'drift_ema_fast', +e.target.value)} /></div>
          <div><label>Drift EMA Slow</label><input type="number" value={config.boom_drift_jump?.drift_ema_slow ?? 50} onChange={e => updateNested('boom_drift_jump', 'drift_ema_slow', +e.target.value)} /></div>
          <div><label>Min ADX to Trade</label><input type="number" value={config.boom_drift_jump?.min_adx_to_trade ?? 20} onChange={e => updateNested('boom_drift_jump', 'min_adx_to_trade', +e.target.value)} /><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>Measured cost of this gate: 0.069 R/trade on Boom.</div></div>
          <div><label>Jump Entry Threshold (%)</label><input type="number" step="0.5" value={config.boom_drift_jump?.jump_entry_percentile_threshold ?? 95.0} onChange={e => updateNested('boom_drift_jump', 'jump_entry_percentile_threshold', +e.target.value)} /></div>
          <div><label>Target R:R</label><input type="number" step="0.5" min="0" value={config.boom_drift_jump?.tp1_rr ?? 5.0} onChange={e => updateNested('boom_drift_jump', 'tp1_rr', +e.target.value)} /></div>
          <div><label>Min RRR to Accept Trade</label><input type="number" step="0.1" min="0" value={config.boom_drift_jump?.min_rrr_to_accept_trade ?? 1.5} onChange={e => updateNested('boom_drift_jump', 'min_rrr_to_accept_trade', +e.target.value)} /></div>
          <div><label>Max Trades / Day</label><input type="number" min="0" value={config.boom_drift_jump?.max_trades_per_day ?? 6} onChange={e => updateNested('boom_drift_jump', 'max_trades_per_day', +e.target.value)} /></div>
          <div><label>Max Daily Risk (%)</label><input type="number" step="0.5" min="0" value={config.boom_drift_jump?.max_daily_risk_pct ?? 4.0} onChange={e => updateNested('boom_drift_jump', 'max_daily_risk_pct', +e.target.value)} /></div>
          <div><label>ADX Gate Mode</label><select value={config.boom_drift_jump?.adx_gate_mode || 'REDUCED_SIZE'} onChange={e => updateNested('boom_drift_jump', 'adx_gate_mode', e.target.value)}><option value="REDUCED_SIZE">Reduced size</option><option value="BLOCK">Block</option></select></div>
          <div style={{ gridColumn: '1 / -1' }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: '0.8rem' }}>
              <input type="checkbox" checked={config.boom_drift_jump?.trade_jumps_enabled ?? false} onChange={e => updateNested('boom_drift_jump', 'trade_jumps_enabled', e.target.checked)} />
              Trade jump entries (Setup B)
            </label>
          </div>
        </div>
      </div>

      <button className="btn btn-primary" style={{ justifySelf: 'start' }} onClick={handleSave} disabled={mutation.isPending}>
        {mutation.isPending ? <Loader2 size={14} className="spin" /> : saved ? <Check size={14} /> : <Save size={14} />}
        {mutation.isPending ? 'Saving...' : saved ? 'Saved!' : 'Save Strategy Configuration'}
      </button>
      {mutation.isError && (
        <div style={{ color: 'var(--red)', fontSize: '0.8rem' }}>
          Failed to save: {mutation.error?.response?.data?.detail || mutation.error?.message}
        </div>
      )}
    </div>
  );
}