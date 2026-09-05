import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Sliders, Save, Loader2, Check } from 'lucide-react';
import { getConfig, updateConfig } from '../../services/api';
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
    crt: {
      htf_timeframe: 'H1',
      ltf_timeframe: 'M5',
      target_r_multiple: 1.5,
      max_trades_per_session: 1,
      session_start: '09:30',
      session_cutoff: '12:00',
      bypass_session_synthetics: true,
      min_sl_pips: 15.0,
      sl_atr_mult: 1.0,
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
    ny_open_retest: {
      range_window_start: '08:00',
      range_window_end: '08:15',
      earliest_valid_break_time: '09:30',
      session_end: '11:00',
      stop_buffer_points: 5.0,
      fixed_target_points: 50.0,
      dynamic_target_override: true,
      target_mode: 'rr',
      target_rr: 2.0,
      sl_buffer_atr_mult: 1.0,
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
      setConfig(prev => ({
        ...prev,
        ...Object.fromEntries(
          Object.entries(remoteConfig.config).filter(([k]) => k in prev).map(([k, v]) => {
            if (typeof v === 'object' && v !== null && !Array.isArray(v) && typeof prev[k] === 'object') {
              return [k, { ...prev[k], ...v }];
            }
            return [k, v];
          })
        ),
      }));
    }
  }, [remoteConfig]);

  const mutation = useMutation({
    mutationFn: (newConfig) => updateConfig({ config: newConfig }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['config'] });
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

  const activeSymbols = config.instrument_settings ? config.instrument_settings.filter(i => i.enabled).map(i => i.symbol) : config.symbols;

  const toggleSymbol = (sym) => {
    let settings = [...(config.instrument_settings || [])];
    const exists = settings.find(i => i.symbol === sym);

    if (exists) {
      exists.enabled = !exists.enabled;
    } else {
      settings.push({ symbol: sym, strategy_id: 'APA_v1', enabled: true });
    }

    const active = settings.filter(i => i.enabled).map(i => i.symbol);
    setConfig({ ...config, instrument_settings: settings, symbols: active });
  };

  const updateSymbolSetting = (sym, key, val) => {
    let settings = [...(config.instrument_settings || [])];
    let exists = settings.find(i => i.symbol === sym);
    if (!exists) {
      exists = { symbol: sym, strategy_id: 'APA_v1', enabled: true };
      settings.push(exists);
    }
    exists[key] = val;
    setConfig({ ...config, instrument_settings: settings });
  };



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
        <div className="card-header"><span className="card-title">Per-Symbol Strategy Configuration</span></div>
        <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginBottom: 12 }}>
          Assign a specific trading engine and parameters to each active symbol.
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {activeSymbols.map(sym => {
            const symConfig = (config.instrument_settings || []).find(i => i.symbol === sym) || {};
            return (
              <div key={sym} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 14px', background: 'var(--bg-tertiary)', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border)' }}>
                <strong style={{ fontSize: '0.9rem', width: '150px' }}>{sym}</strong>
                <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'center' }}>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                    <label style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>Strategy Engine</label>
                    <select
                      value={symConfig.strategy_id || 'APA_v1'}
                      onChange={e => updateSymbolSetting(sym, 'strategy_id', e.target.value)}
                      style={{ fontSize: '0.8rem', padding: '4px 8px' }}
                    >
                      <option value="APA_v1">APA (Adv. Price Action)</option>
                      <option value="VWAP_v1">VWAP Institutional</option>
                      <option value="DriftJumpAlpha_v1">Drift & Jump Alpha</option>
                      <option value="CRT_v1">CRT Strategy</option>
                      <option value="HTFFVGFlip_v1">HTF FVG Flip</option>
                      <option value="BiasIFVG_v1">Bias KeyLevel IFVG</option>
                      <option value="NYOpenRetest_v1">NY Open Break Retest</option><option value="BoomDriftJump_v1">Boom Drift &amp; Jump</option><option value="SpikeFade_v1">Spike Fade (synthetics)</option><option value="RangeRevert_v1">Range Revert (synthetics)</option><option value="RangeBreakout_v1">Range Breakout (synthetics)</option><option value="TrendDrift_v1">Trend Drift (synthetics)</option>
                    </select>
                  </div>



                </div>
              </div>
            );
          })}
        </div>
      </div>



      <div className="card">
        <div className="card-header"><span className="card-title">APA (Advanced Price Action) Parameters</span></div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16 }}>
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
        <div className="card-header"><span className="card-title">CRT Strategy Parameters</span></div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16 }}>
          <div>
            <label>HTF Timeframe</label>
            {/* Must be one of the fetcher's timeframe codes (H1, not "1H") — an
                unrecognised code fails the run immediately with "No data". */}
            <select value={config.crt?.htf_timeframe || 'H1'} onChange={e => updateNested('crt', 'htf_timeframe', e.target.value)}>
              {['M15', 'M30', 'H1', 'H4', 'D1'].map(t => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
          <div>
            <label>LTF Timeframe</label>
            <select value={config.crt?.ltf_timeframe || 'M5'} onChange={e => updateNested('crt', 'ltf_timeframe', e.target.value)}>
              {['M1', 'M5', 'M15'].map(t => <option key={t} value={t}>{t}</option>)}
            </select>
            <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>M1 over a multi-month range exceeds what MT5 returns in a single call.</div>
          </div>
          <div><label>Target R-Multiple</label><input type="number" step="0.1" value={config.crt?.target_r_multiple || 1.5} onChange={e => updateNested('crt', 'target_r_multiple', +e.target.value)} /></div>
          <div><label>Max Trades / Session</label><input type="number" value={config.crt?.max_trades_per_session || 1} onChange={e => updateNested('crt', 'max_trades_per_session', +e.target.value)} /></div>
          <div><label>Session Start (ET)</label><input type="text" value={config.crt?.session_start || '09:30'} onChange={e => updateNested('crt', 'session_start', e.target.value)} /></div>
          <div><label>Session Cutoff (ET)</label><input type="text" value={config.crt?.session_cutoff || '12:00'} onChange={e => updateNested('crt', 'session_cutoff', e.target.value)} /></div>
          <div><label>Min SL (pips)</label><input type="number" step="0.5" min="0" value={config.crt?.min_sl_pips ?? 15.0} onChange={e => updateNested('crt', 'min_sl_pips', +e.target.value)} /><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>Hard stop floor so a tiny CRT candle cannot produce a sub-spread stop. 0 disables.</div></div>
          <div><label>SL ATR Multiplier</label><input type="number" step="0.1" min="0" value={config.crt?.sl_atr_mult ?? 1.0} onChange={e => updateNested('crt', 'sl_atr_mult', +e.target.value)} /><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>SL must also be ≥ this × ATR(14). Larger floor wins. 0 disables.</div></div>
          <div>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', marginTop: 12 }}>
              <input type="checkbox" checked={config.crt?.bypass_session_synthetics ?? true} onChange={e => updateNested('crt', 'bypass_session_synthetics', e.target.checked)} />
              Bypass Session Filter (Synthetics)
            </label>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-header"><span className="card-title">HTF FVG Flip Parameters</span></div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16 }}>
          <div>
            <label>HTF Timeframe</label>
            <select value={config.htf_fvg_flip?.htf_timeframe || 'H1'} onChange={e => updateNested('htf_fvg_flip', 'htf_timeframe', e.target.value)}>
              {['M15', 'M30', 'H1', 'H4', 'D1'].map(t => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
          <div>
            <label>Entry Confirm TF</label>
            <select value={config.htf_fvg_flip?.entry_confirmation_tf || 'M5'} onChange={e => updateNested('htf_fvg_flip', 'entry_confirmation_tf', e.target.value)}>
              {['M1', 'M5', 'M15'].map(t => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
          <div><label>Target RR</label><input type="number" step="0.1" value={config.htf_fvg_flip?.target_rr ?? 2.0} onChange={e => updateNested('htf_fvg_flip', 'target_rr', +e.target.value)} /></div>
          <div><label>Session Start</label><input type="text" value={config.htf_fvg_flip?.session_start || '09:30'} onChange={e => updateNested('htf_fvg_flip', 'session_start', e.target.value)} /></div>
          <div><label>Session Cutoff</label><input type="text" value={config.htf_fvg_flip?.session_cutoff || '16:00'} onChange={e => updateNested('htf_fvg_flip', 'session_cutoff', e.target.value)} /></div>
          <div><label>SL Buffer (× ATR)</label><input type="number" step="0.05" min="0" value={config.htf_fvg_flip?.sl_buffer_atr_mult ?? 0.5} onChange={e => updateNested('htf_fvg_flip', 'sl_buffer_atr_mult', +e.target.value)} /></div>
          <div><label>Min SL (pips)</label><input type="number" step="0.5" min="0" value={config.htf_fvg_flip?.min_sl_pips ?? 12.0} onChange={e => updateNested('htf_fvg_flip', 'min_sl_pips', +e.target.value)} /><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>Absolute stop floor. 0 disables.</div></div>
          <div><label>Min SL (× ATR)</label><input type="number" step="0.1" min="0" value={config.htf_fvg_flip?.min_sl_atr_mult ?? 1.0} onChange={e => updateNested('htf_fvg_flip', 'min_sl_atr_mult', +e.target.value)} /><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>Volatility-relative floor. Larger floor wins. 0 disables.</div></div>
          <div>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', marginTop: 12 }}>
              <input type="checkbox" checked={config.htf_fvg_flip?.session_filter_enabled ?? true} onChange={e => updateNested('htf_fvg_flip', 'session_filter_enabled', e.target.checked)} />
              Enable Session Filter
            </label>
          </div>
          <div>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', marginTop: 12 }}>
              <input type="checkbox" checked={config.htf_fvg_flip?.require_unfilled_htf_fvg ?? true} onChange={e => updateNested('htf_fvg_flip', 'require_unfilled_htf_fvg', e.target.checked)} />
              Require Unfilled HTF FVG
            </label>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-header"><span className="card-title">Bias KeyLevel IFVG Parameters</span></div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16 }}>
          <div><label>Target RR</label><input type="number" step="0.1" value={config.bias_ifvg?.target_rr ?? 2.0} onChange={e => updateNested('bias_ifvg', 'target_rr', +e.target.value)} /></div>
          <div><label>Max Trades / Day</label><input type="number" value={config.bias_ifvg?.max_trades_per_day || 2} onChange={e => updateNested('bias_ifvg', 'max_trades_per_day', +e.target.value)} /></div>
          <div><label>A+ Confluence Threshold</label><input type="number" min="0" max="100" value={config.bias_ifvg?.a_plus_confluence_threshold ?? 90} onChange={e => updateNested('bias_ifvg', 'a_plus_confluence_threshold', +e.target.value)} /><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>Score a setup must reach to be taken (0–100).</div></div>
          <div><label>Session Start</label><input type="text" value={config.bias_ifvg?.session_start || '09:30'} onChange={e => updateNested('bias_ifvg', 'session_start', e.target.value)} /></div>
          <div><label>Session Cutoff</label><input type="text" value={config.bias_ifvg?.session_cutoff || '11:00'} onChange={e => updateNested('bias_ifvg', 'session_cutoff', e.target.value)} /></div>
          <div><label>Rejection Min Body (× ATR)</label><input type="number" step="0.05" min="0" value={config.bias_ifvg?.rejection_min_body_atr_mult ?? 0.15} onChange={e => updateNested('bias_ifvg', 'rejection_min_body_atr_mult', +e.target.value)} /><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>Minimum rejection-candle body, so a doji cannot count as displacement.</div></div>
          <div><label>SL Buffer (× ATR)</label><input type="number" step="0.05" min="0" value={config.bias_ifvg?.sl_buffer_atr_mult ?? 0.5} onChange={e => updateNested('bias_ifvg', 'sl_buffer_atr_mult', +e.target.value)} /></div>
          <div><label>Min SL (pips)</label><input type="number" step="0.5" min="0" value={config.bias_ifvg?.min_sl_pips ?? 12.0} onChange={e => updateNested('bias_ifvg', 'min_sl_pips', +e.target.value)} /><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>Absolute stop floor. 0 disables.</div></div>
          <div><label>Min SL (× ATR)</label><input type="number" step="0.1" min="0" value={config.bias_ifvg?.min_sl_atr_mult ?? 1.0} onChange={e => updateNested('bias_ifvg', 'min_sl_atr_mult', +e.target.value)} /><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>Volatility-relative floor. Larger floor wins. 0 disables.</div></div>
        </div>
      </div>

      <div className="card">
        <div className="card-header"><span className="card-title">NY Open Break Retest Parameters</span></div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16 }}>
          <div><label>Range Start</label><input type="text" value={config.ny_open_retest?.range_window_start || '08:00'} onChange={e => updateNested('ny_open_retest', 'range_window_start', e.target.value)} /></div>
          <div><label>Range End</label><input type="text" value={config.ny_open_retest?.range_window_end || '08:15'} onChange={e => updateNested('ny_open_retest', 'range_window_end', e.target.value)} /></div>
          <div><label>Earliest Break Time</label><input type="text" value={config.ny_open_retest?.earliest_valid_break_time || '09:30'} onChange={e => updateNested('ny_open_retest', 'earliest_valid_break_time', e.target.value)} /></div>
          <div><label>Session End</label><input type="text" value={config.ny_open_retest?.session_end || '11:00'} onChange={e => updateNested('ny_open_retest', 'session_end', e.target.value)} /></div>
          <div><label>Stop Buffer (points)</label><input type="number" step="0.1" value={config.ny_open_retest?.stop_buffer_points || 5.0} onChange={e => updateNested('ny_open_retest', 'stop_buffer_points', +e.target.value)} /></div>
          <div><label>SL Buffer (× ATR)</label><input type="number" step="0.1" min="0" value={config.ny_open_retest?.sl_buffer_atr_mult ?? 1.0} onChange={e => updateNested('ny_open_retest', 'sl_buffer_atr_mult', +e.target.value)} /></div>
          <div>
            <label>Target Mode</label>
            <select value={config.ny_open_retest?.target_mode || 'rr'} onChange={e => updateNested('ny_open_retest', 'target_mode', e.target.value)}>
              <option value="rr">R-Multiple (scale-free)</option>
              <option value="points">Fixed Points</option>
            </select>
          </div>
          <div>
            <label>Target R:R</label>
            <input type="number" step="0.1" min="0" value={config.ny_open_retest?.target_rr ?? 2.0}
              disabled={(config.ny_open_retest?.target_mode || 'rr') !== 'rr'}
              style={(config.ny_open_retest?.target_mode || 'rr') === 'rr' ? undefined : { opacity: 0.5 }}
              onChange={e => updateNested('ny_open_retest', 'target_rr', +e.target.value)} />
            <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>
              {(config.ny_open_retest?.target_mode || 'rr') === 'rr' ? 'Target = this × the realised stop distance.' : 'Inactive while Target Mode is Fixed Points.'}
            </div>
          </div>
          <div>
            <label>Fixed Target (points)</label>
            <input type="number" step="0.1" value={config.ny_open_retest?.fixed_target_points ?? 50.0}
              disabled={(config.ny_open_retest?.target_mode || 'rr') === 'rr'}
              style={(config.ny_open_retest?.target_mode || 'rr') === 'rr' ? { opacity: 0.5 } : undefined}
              onChange={e => updateNested('ny_open_retest', 'fixed_target_points', +e.target.value)} />
            <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>
              {(config.ny_open_retest?.target_mode || 'rr') === 'rr' ? 'Inactive while Target Mode is R-Multiple.' : 'NQ-native point ceiling; unreachable on most FX pairs.'}
            </div>
          </div>
          <div>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', marginTop: 12 }}>
              <input type="checkbox" checked={config.ny_open_retest?.dynamic_target_override ?? true} onChange={e => updateNested('ny_open_retest', 'dynamic_target_override', e.target.checked)} />
              Dynamic Target Override
            </label>
          </div>
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

      <div className="card">
        <div className="card-header"><span className="card-title">Synthetic Template Strategies</span></div>
        <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginBottom: 12 }}>
          Shared by Spike Fade, Range Revert, Range Breakout and Trend Drift — all four read one backend params block, so a change here applies to all of them. Per-symbol shipped values live in strategy_defaults.py (SYNTH_SLOT_PARAMS) and override these.
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16 }}>
          <div><label>Stop (x ATR)</label><input type="number" step="0.5" min="0.1" value={config.synth?.stop_atr_multiple ?? 5.0} onChange={e => updateNested('synth', 'stop_atr_multiple', +e.target.value)} /><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>Keep wide. At 0.5x ATR the unmodelled spike gap is ~1 R/trade.</div></div>
          <div><label>Target R:R</label><input type="number" step="0.5" min="0.1" value={config.synth?.tp1_rr ?? 5.0} onChange={e => updateNested('synth', 'tp1_rr', +e.target.value)} /></div>
          <div><label>Spike Size (x ATR)</label><input type="number" step="0.5" min="0.5" value={config.synth?.spike_k_atr ?? 3.0} onChange={e => updateNested('synth', 'spike_k_atr', +e.target.value)} /><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>Spike Fade only.</div></div>
          <div><label>Stretch (x ATR)</label><input type="number" step="0.5" min="0.5" value={config.synth?.revert_k_atr ?? 2.0} onChange={e => updateNested('synth', 'revert_k_atr', +e.target.value)} /><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>Range Revert only.</div></div>
          <div><label>Breakout Lookback (bars)</label><input type="number" min="2" value={config.synth?.breakout_lookback ?? 20} onChange={e => updateNested('synth', 'breakout_lookback', +e.target.value)} /><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>Range Breakout only.</div></div>
          <div><label>Min ADX to Trade</label><input type="number" min="0" value={config.synth?.min_adx_to_trade ?? 20} onChange={e => updateNested('synth', 'min_adx_to_trade', +e.target.value)} /><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>Trend Drift only.</div></div>
          <div><label>EMA Fast</label><input type="number" min="2" value={config.synth?.ema_fast ?? 20} onChange={e => updateNested('synth', 'ema_fast', +e.target.value)} /></div>
          <div><label>EMA Slow</label><input type="number" min="3" value={config.synth?.ema_slow ?? 50} onChange={e => updateNested('synth', 'ema_slow', +e.target.value)} /></div>
          <div><label>Max Trades / Day</label><input type="number" min="0" value={config.synth?.max_trades_per_day ?? 6} onChange={e => updateNested('synth', 'max_trades_per_day', +e.target.value)} /></div>
          <div><label>Max Daily Risk (%)</label><input type="number" step="0.5" min="0" value={config.synth?.max_daily_risk_pct ?? 4.0} onChange={e => updateNested('synth', 'max_daily_risk_pct', +e.target.value)} /></div>
          <div style={{ gridColumn: '1 / -1' }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: '0.8rem' }}>
              <input type="checkbox" checked={config.synth?.require_adx ?? true} onChange={e => updateNested('synth', 'require_adx', e.target.checked)} />
              Require ADX trend filter (Trend Drift)
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