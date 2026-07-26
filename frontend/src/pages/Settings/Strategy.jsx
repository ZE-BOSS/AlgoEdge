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
    symbols: ['XAUUSD', 'EURUSD', 'GBPUSD'], // legacy support
    instrument_settings: [
      { symbol: 'XAUUSD', strategy_id: 'SMC_v1', enabled: true, compounding_enabled: false },
      { symbol: 'EURUSD', strategy_id: 'SMC_v1', enabled: true, compounding_enabled: false },
      { symbol: 'GBPUSD', strategy_id: 'SMC_v1', enabled: true, compounding_enabled: false }
    ],
    smc: {
      confluence_threshold: 60,
      swing_length_htf: 5,
      swing_length_ltf: 3,
      ob_impulse_ratio: 2.0,
      fvg_min_gap_pips: 5.0,
      liq_sweep_min_pips: 5.0,
      max_spread_pips: 3.0,
      session_filter_enabled: true,
      news_filter_enabled: true,
      enforce_htf_pd: true,
      enforce_fvg_displacement: false,
      enforce_asian_range_sweep: false,
    },
    drift_jump_alpha: {
      spike_lookback_bars: 50,
      drift_ema_fast: 20,
      drift_ema_slow: 50,
      min_adx_to_trade: 20,
      jump_entry_percentile_threshold: 95.0,
      trade_jumps_enabled: false,
      control_test_passed: false,
      aggregate_max_lots_per_symbol: 6.0,
    },
    crt: {
      htf_timeframe: '1H',
      ltf_timeframe: 'M1',
      target_r_multiple: 1.5,
      max_trades_per_session: 1,
      session_start: '09:30',
      session_cutoff: '12:00',
      bypass_session_synthetics: true,
    },
    manual_bias_overrides: {},
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
    'XAUUSD', 'EURUSD', 'GBPUSD', 'USDJPY', 'US30', 'NAS100', 'US100', 'USTEC', 'NDX', 'NDX100', 'US Tech100', 'US Tech 100', 'USTECH', 'NQ100', 'NQ', 'SPX500', 'US500', 'SPX', 'SP500', 'S&P500', 'S&P 500', 'US 500', 'INX', 'ES', 'BTCUSD',
    'Volatility 10 Index', 'Volatility 25 Index', 'Volatility 50 Index',
    'Volatility 75 Index', 'Volatility 100 Index', 'Volatility 150 Index', 'Volatility 250 Index',
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
      settings.push({ symbol: sym, strategy_id: 'SMC_v1', enabled: true, compounding_enabled: false });
    }

    const active = settings.filter(i => i.enabled).map(i => i.symbol);
    setConfig({ ...config, instrument_settings: settings, symbols: active });
  };

  const updateSymbolSetting = (sym, key, val) => {
    let settings = [...(config.instrument_settings || [])];
    let exists = settings.find(i => i.symbol === sym);
    if (!exists) {
      exists = { symbol: sym, strategy_id: 'SMC_v1', enabled: true, compounding_enabled: false };
      settings.push(exists);
    }
    exists[key] = val;
    setConfig({ ...config, instrument_settings: settings });
  };

  const updateBias = (sym, bias) => {
    const smc = config.smc || {};
    const next = { ...(smc.manual_bias_overrides || {}) };
    if (bias === 'NONE') delete next[sym];
    else next[sym] = bias;
    setConfig({ ...config, smc: { ...smc, manual_bias_overrides: next } });
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
                      value={symConfig.strategy_id || 'SMC_v1'}
                      onChange={e => updateSymbolSetting(sym, 'strategy_id', e.target.value)}
                      style={{ fontSize: '0.8rem', padding: '4px 8px' }}
                    >
                      <option value="SMC_v1">SMC Multi-TF</option>
                      <option value="DriftJumpAlpha_v1">Drift & Jump Alpha</option>
                      <option value="CRT_v1">CRT Strategy</option>
                      <option value="HTFFVGFlip_v1">HTF FVG Flip</option>
                      <option value="BiasIFVG_v1">Bias KeyLevel IFVG</option>
                      <option value="NYOpenRetest_v1">NY Open Break Retest</option>
                    </select>
                  </div>

                  <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                    <label style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>Manual HTF Bias</label>
                    <select
                      value={(config.smc?.manual_bias_overrides || {})[sym] || 'NONE'}
                      onChange={e => updateBias(sym, e.target.value)}
                      style={{ fontSize: '0.8rem', padding: '4px 8px' }}
                    >
                      <option value="NONE">Auto-Detect</option>
                      <option value="BULLISH">Force Bullish</option>
                      <option value="BEARISH">Force Bearish</option>
                    </select>
                  </div>

                  <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', fontSize: '0.8rem', marginTop: 16 }}>
                    <input
                      type="checkbox"
                      checked={symConfig.compounding_enabled || false}
                      onChange={e => updateSymbolSetting(sym, 'compounding_enabled', e.target.checked)}
                      style={{ width: 14, height: 14 }}
                    />
                    Compounding
                  </label>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="card">
        <div className="card-header"><span className="card-title">SMC Parameters</span></div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16 }}>
          <div>
            <label>Confluence Threshold</label>
            <input type="number" value={config.smc?.min_signal_score || config.smc?.confluence_threshold || 60} onChange={e => updateNested('smc', 'min_signal_score', +e.target.value)} />
            <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>Min score to execute (0-100)</div>
          </div>
          <div><label>Swing Length HTF</label><input type="number" value={config.smc?.swing_length_htf || 5} onChange={e => updateNested('smc', 'swing_length_htf', +e.target.value)} /></div>
          <div><label>Swing Length LTF</label><input type="number" value={config.smc?.swing_length_ltf || 3} onChange={e => updateNested('smc', 'swing_length_ltf', +e.target.value)} /></div>
          <div><label>OB Impulse Ratio</label><input type="number" step="0.1" value={config.smc?.ob_impulse_ratio || 2.0} onChange={e => updateNested('smc', 'ob_impulse_ratio', +e.target.value)} /></div>
          <div><label>FVG Min Gap (pips)</label><input type="number" value={config.smc?.fvg_min_gap_pips || 5.0} onChange={e => updateNested('smc', 'fvg_min_gap_pips', +e.target.value)} /></div>
          <div><label>Liq Sweep Min (pips)</label><input type="number" value={config.smc?.liq_sweep_min_pips || 5.0} onChange={e => updateNested('smc', 'liq_sweep_min_pips', +e.target.value)} /></div>
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
          <div><label>HTF Timeframe</label><input type="text" value={config.crt?.htf_timeframe || '1H'} onChange={e => updateNested('crt', 'htf_timeframe', e.target.value)} /></div>
          <div><label>LTF Timeframe</label><input type="text" value={config.crt?.ltf_timeframe || 'M1'} onChange={e => updateNested('crt', 'ltf_timeframe', e.target.value)} /></div>
          <div><label>Target R-Multiple</label><input type="number" step="0.1" value={config.crt?.target_r_multiple || 1.5} onChange={e => updateNested('crt', 'target_r_multiple', +e.target.value)} /></div>
          <div><label>Max Trades / Session</label><input type="number" value={config.crt?.max_trades_per_session || 1} onChange={e => updateNested('crt', 'max_trades_per_session', +e.target.value)} /></div>
          <div><label>Session Start (ET)</label><input type="text" value={config.crt?.session_start || '09:30'} onChange={e => updateNested('crt', 'session_start', e.target.value)} /></div>
          <div><label>Session Cutoff (ET)</label><input type="text" value={config.crt?.session_cutoff || '12:00'} onChange={e => updateNested('crt', 'session_cutoff', e.target.value)} /></div>
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
          <div><label>HTF Timeframe</label><input type="text" value={config.htf_fvg_flip?.htf_timeframe || 'H1'} onChange={e => updateNested('htf_fvg_flip', 'htf_timeframe', e.target.value)} /></div>
          <div><label>Entry Confirm TF</label><input type="text" value={config.htf_fvg_flip?.entry_confirmation_tf || 'M1'} onChange={e => updateNested('htf_fvg_flip', 'entry_confirmation_tf', e.target.value)} /></div>
          <div><label>Target RR</label><input type="number" step="0.1" value={config.htf_fvg_flip?.target_rr || 1.0} onChange={e => updateNested('htf_fvg_flip', 'target_rr', +e.target.value)} /></div>
          <div><label>Session Start</label><input type="text" value={config.htf_fvg_flip?.session_start || '09:30'} onChange={e => updateNested('htf_fvg_flip', 'session_start', e.target.value)} /></div>
          <div><label>Session Cutoff</label><input type="text" value={config.htf_fvg_flip?.session_cutoff || '11:00'} onChange={e => updateNested('htf_fvg_flip', 'session_cutoff', e.target.value)} /></div>
          <div>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', marginTop: 12 }}>
              <input type="checkbox" checked={config.htf_fvg_flip?.session_filter_enabled ?? false} onChange={e => updateNested('htf_fvg_flip', 'session_filter_enabled', e.target.checked)} />
              Enable Session Filter
            </label>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-header"><span className="card-title">Bias KeyLevel IFVG Parameters</span></div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16 }}>
          <div><label>Stop Method</label><input type="text" value={config.bias_ifvg?.stop_method || 'swing_high_low'} onChange={e => updateNested('bias_ifvg', 'stop_method', e.target.value)} /></div>
          <div><label>Target RR Range Min</label><input type="number" step="0.1" value={config.bias_ifvg?.target_rr_range_min || 1.0} onChange={e => updateNested('bias_ifvg', 'target_rr_range_min', +e.target.value)} /></div>
          <div><label>Target RR Range Max</label><input type="number" step="0.1" value={config.bias_ifvg?.target_rr_range_max || 3.0} onChange={e => updateNested('bias_ifvg', 'target_rr_range_max', +e.target.value)} /></div>
          <div><label>Max Trades / Day</label><input type="number" value={config.bias_ifvg?.max_trades_per_day || 2} onChange={e => updateNested('bias_ifvg', 'max_trades_per_day', +e.target.value)} /></div>
          <div><label>Session Start</label><input type="text" value={config.bias_ifvg?.session_start || '09:30'} onChange={e => updateNested('bias_ifvg', 'session_start', e.target.value)} /></div>
          <div><label>Session Cutoff</label><input type="text" value={config.bias_ifvg?.session_cutoff || '11:00'} onChange={e => updateNested('bias_ifvg', 'session_cutoff', e.target.value)} /></div>
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
          <div><label>Fixed Target (points)</label><input type="number" step="0.1" value={config.ny_open_retest?.fixed_target_points || 15.0} onChange={e => updateNested('ny_open_retest', 'fixed_target_points', +e.target.value)} /></div>
        </div>
      </div>

      <div className="card">
        <div className="card-header"><span className="card-title">Filters</span></div>
        <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', textTransform: 'none' }}>
            <input type="checkbox" checked={config.smc?.session_filter_enabled ?? true} onChange={e => updateNested('smc', 'session_filter_enabled', e.target.checked)} style={{ width: 16, height: 16 }} />
            Session Filter (London/NY Kill Zones only)
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', textTransform: 'none' }}>
            <input type="checkbox" checked={config.smc?.news_filter_enabled ?? true} onChange={e => updateNested('smc', 'news_filter_enabled', e.target.checked)} style={{ width: 16, height: 16 }} />
            News Filter (block ±30min HIGH impact)
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', textTransform: 'none' }}>
            <input type="checkbox" checked={config.smc?.enforce_htf_pd ?? true} onChange={e => updateNested('smc', 'enforce_htf_pd', e.target.checked)} style={{ width: 16, height: 16 }} />
            Enforce HTF Premium/Discount
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', textTransform: 'none' }}>
            <input type="checkbox" checked={config.smc?.enforce_fvg_displacement ?? false} onChange={e => updateNested('smc', 'enforce_fvg_displacement', e.target.checked)} style={{ width: 16, height: 16 }} />
            Enforce FVG Displacement
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', textTransform: 'none' }}>
            <input type="checkbox" checked={config.smc?.enforce_asian_range_sweep ?? false} onChange={e => updateNested('smc', 'enforce_asian_range_sweep', e.target.checked)} style={{ width: 16, height: 16 }} />
            Enforce Asian Range Sweep
          </label>
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
