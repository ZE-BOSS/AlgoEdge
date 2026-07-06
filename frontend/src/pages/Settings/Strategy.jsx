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
    htf_timeframe: 'H4',
    ltf_timeframe: 'M15',
    confluence_threshold: 60,
    swing_length_htf: 5,
    swing_length_ltf: 3,
    ob_impulse_ratio: 2.0,
    fvg_min_gap_pips: 5.0,
    liq_sweep_min_pips: 5.0,
    max_spread_pips: 3.0,
    session_filter_enabled: true,
    news_filter_enabled: true,
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
          Object.entries(remoteConfig.config).filter(([k]) => k in prev)
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

  const update = (key, val) => setConfig({ ...config, [key]: val });

  const handleSave = () => mutation.mutate(config);

  const allSymbols = [
    'XAUUSD','EURUSD','GBPUSD','USDJPY','US30','BTCUSD',
    'Volatility 10 Index','Volatility 25 Index','Volatility 50 Index',
    'Volatility 75 Index','Volatility 100 Index','Volatility 150 Index','Volatility 250 Index',
    'Boom 300 Index','Boom 500 Index','Boom 1000 Index',
    'Crash 300 Index','Crash 500 Index','Crash 1000 Index',
    'Jump 10 Index','Jump 25 Index','Jump 50 Index','Jump 75 Index','Jump 100 Index',
    'Step Index','Range Break 100 Index','Range Break 200 Index',
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
    const next = { ...(config.manual_bias_overrides || {}) };
    if (bias === 'NONE') delete next[sym];
    else next[sym] = bias;
    update('manual_bias_overrides', next);
  };

  return (
    <div style={{ display: 'grid', gap: 20, maxWidth: 800 }}>
      <div className="card">
        <div className="card-header"><span className="card-title"><Sliders size={14} /> Active Symbols</span></div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          {allSymbols.map(sym => (
            <button
              key={sym}
              className={`btn btn-sm ${activeSymbols.includes(sym) ? 'btn-primary' : 'btn-secondary'}`}
              onClick={() => toggleSymbol(sym)}
            >
              {sym}
            </button>
          ))}
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
                      <option value="CrashBoom_v1">CrashBoom Drift</option>
                    </select>
                  </div>
                  
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                    <label style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>Manual HTF Bias</label>
                    <select 
                      value={(config.manual_bias_overrides || {})[sym] || 'NONE'} 
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
        <div className="card-header"><span className="card-title">Timeframes</span></div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
          <div>
            <label>HTF (Bias)</label>
            <select value={config.htf_timeframe} onChange={e => update('htf_timeframe', e.target.value)}>
              <option value="H1">H1</option>
              <option value="H4">H4</option>
              <option value="D1">D1</option>
            </select>
          </div>
          <div>
            <label>LTF (Entry)</label>
            <select value={config.ltf_timeframe} onChange={e => update('ltf_timeframe', e.target.value)}>
              <option value="M5">M5</option>
              <option value="M15">M15</option>
              <option value="M30">M30</option>
            </select>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-header"><span className="card-title">SMC Parameters</span></div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16 }}>
          <div>
            <label>Confluence Threshold</label>
            <input type="number" value={config.confluence_threshold} onChange={e => update('confluence_threshold', +e.target.value)} />
            <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>Min score to execute (0-100)</div>
          </div>
          <div><label>Swing Length HTF</label><input type="number" value={config.swing_length_htf} onChange={e => update('swing_length_htf', +e.target.value)} /></div>
          <div><label>Swing Length LTF</label><input type="number" value={config.swing_length_ltf} onChange={e => update('swing_length_ltf', +e.target.value)} /></div>
          <div><label>OB Impulse Ratio</label><input type="number" step="0.1" value={config.ob_impulse_ratio} onChange={e => update('ob_impulse_ratio', +e.target.value)} /></div>
          <div><label>FVG Min Gap (pips)</label><input type="number" value={config.fvg_min_gap_pips} onChange={e => update('fvg_min_gap_pips', +e.target.value)} /></div>
          <div><label>Liq Sweep Min (pips)</label><input type="number" value={config.liq_sweep_min_pips} onChange={e => update('liq_sweep_min_pips', +e.target.value)} /></div>
          <div><label>Max Spread (pips)</label><input type="number" value={config.max_spread_pips} onChange={e => update('max_spread_pips', +e.target.value)} /></div>
        </div>
      </div>

      <div className="card">
        <div className="card-header"><span className="card-title">Filters</span></div>
        <div style={{ display: 'flex', gap: 24 }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', textTransform: 'none' }}>
            <input type="checkbox" checked={config.session_filter_enabled} onChange={e => update('session_filter_enabled', e.target.checked)} style={{ width: 16, height: 16 }} />
            Session Filter (London/NY Kill Zones only)
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', textTransform: 'none' }}>
            <input type="checkbox" checked={config.news_filter_enabled} onChange={e => update('news_filter_enabled', e.target.checked)} style={{ width: 16, height: 16 }} />
            News Filter (block ±30min HIGH impact)
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
