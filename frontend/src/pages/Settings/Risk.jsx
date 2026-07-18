import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Shield, Save, Loader2, Check } from 'lucide-react';
import { getConfig, updateConfig } from '../../services/api';
import { useConnectionStore, useAuthStore } from '../../store';

export default function RiskSettings() {
  const { status } = useConnectionStore();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const queryClient = useQueryClient();
  const [saved, setSaved] = useState(false);

  const [config, setConfig] = useState({
    risk_per_trade_pct: 1.0,
    max_daily_consecutive_losses: 3,
    max_weekly_consecutive_losses: 5,
    max_consecutive_losses: 5,
    max_daily_trades: 5,
    max_concurrent_positions: 3,
    target_profit_enabled: false,
    max_daily_profit: 500.0,
    max_weekly_profit: 2000.0,
    min_rr: 3.0,
    be_trigger_rr: 1.0,
    be_buffer_pips: 2.0,
    tp_count: 3,
    tp1_rr: 1.0,
    tp2_rr: 3.0,
    tp3_rr: 5.0,
    tp4_rr: 10.0,
    tp5_rr: 15.0,
    tp_splits: '30,25,20,15,10',
    trail_method_tp2: 'ATR_TRAIL',
    trail_method_tp3: 'STRUCTURE_TRAIL',
    trail_method_tp4: 'ATR_TRAIL',
    trail_method_tp5: 'STRUCTURE_TRAIL',
    atr_trail_multiplier: 1.5,
    trail_pips: 15,
    trail_pct: 0.5,
    trail_activation_rr: 1.0,
    trail_step_pips: 5.0,
    trail_structure_bars: 3,
    compounding_enabled: false,
    session_filter_enabled: true,
  });

  // Load current config from backend
  const { data: remoteConfig } = useQuery({
    queryKey: ['config'],
    queryFn: () => getConfig().then(r => r.data),
    enabled: status === 'ONLINE' && isAuthenticated,
  });

  useEffect(() => {
    if (remoteConfig?.config) {
      // Merge remote config with defaults (only override keys that exist)
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

  return (
    <div style={{ display: 'grid', gap: 20, maxWidth: 800 }}>
      <div className="card">
        <div className="card-header"><span className="card-title"><Shield size={14} /> Position Sizing</span></div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
          <div><label>Risk Per Trade (%)</label><input type="number" step="0.1" value={config.risk_per_trade_pct} onChange={e => update('risk_per_trade_pct', +e.target.value)} /></div>
          <div><label>Minimum R:R</label><input type="number" step="0.5" value={config.min_rr} onChange={e => update('min_rr', +e.target.value)} /></div>
        </div>
      </div>

      <div className="card">
        <div className="card-header"><span className="card-title">Circuit Breakers</span></div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16 }}>
          <div><label>Max Daily Consec. Losses</label><input type="number" step="1" min="1" value={config.max_daily_consecutive_losses} onChange={e => update('max_daily_consecutive_losses', +e.target.value)} /></div>
          <div><label>Max Weekly Consec. Losses</label><input type="number" step="1" min="1" value={config.max_weekly_consecutive_losses} onChange={e => update('max_weekly_consecutive_losses', +e.target.value)} /></div>
          <div><label>Max Consec. Losses</label><input type="number" value={config.max_consecutive_losses} onChange={e => update('max_consecutive_losses', +e.target.value)} /></div>
          <div><label>Max Daily Trades</label><input type="number" step="1" min="1" value={config.max_daily_trades} onChange={e => update('max_daily_trades', +e.target.value)} /><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>Prevents overtrading in chop</div></div>
          <div><label>Max Open Positions</label><input type="number" value={config.max_concurrent_positions} onChange={e => update('max_concurrent_positions', +e.target.value)} /></div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 16, marginBottom: 12 }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', textTransform: 'none' }}>
            <input type="checkbox" checked={config.target_profit_enabled} onChange={e => update('target_profit_enabled', e.target.checked)} style={{ width: 16, height: 16 }} />
            Enable Target Profit Halts
          </label>
        </div>
        {config.target_profit_enabled && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, padding: 12, background: 'var(--bg-tertiary)', borderRadius: 'var(--radius-xs)' }}>
            <div><label>Max Daily Profit ($)</label><input type="number" step="10" value={config.max_daily_profit} onChange={e => update('max_daily_profit', +e.target.value)} /></div>
            <div><label>Max Weekly Profit ($)</label><input type="number" step="10" value={config.max_weekly_profit} onChange={e => update('max_weekly_profit', +e.target.value)} /></div>
          </div>
        )}
      </div>

      <div className="card">
        <div className="card-header"><span className="card-title">Take Profit & Break-Even</span></div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16 }}>
          <div>
            <label>Active TP Count</label>
            <select value={config.tp_count} onChange={e => update('tp_count', +e.target.value)}>
              <option value={1}>1 TP</option>
              <option value={2}>2 TPs</option>
              <option value={3}>3 TPs (default)</option>
              <option value={4}>4 TPs</option>
              <option value={5}>5 TPs</option>
            </select>
            <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>
              All TPs open at entry. TP1 hit → move remaining to break-even.
            </div>
          </div>
          <div><label>TP1 R:R</label><input type="number" step="0.5" value={config.tp1_rr} onChange={e => update('tp1_rr', +e.target.value)} /></div>
          <div><label>TP2 R:R</label><input type="number" step="0.5" value={config.tp2_rr} onChange={e => update('tp2_rr', +e.target.value)} /></div>
          <div><label>TP3 R:R</label><input type="number" step="0.5" value={config.tp3_rr} onChange={e => update('tp3_rr', +e.target.value)} /></div>
          {config.tp_count >= 4 && (
            <div><label>TP4 R:R</label><input type="number" step="0.5" value={config.tp4_rr} onChange={e => update('tp4_rr', +e.target.value)} /></div>
          )}
          {config.tp_count >= 5 && (
            <div><label>TP5 R:R</label><input type="number" step="0.5" value={config.tp5_rr} onChange={e => update('tp5_rr', +e.target.value)} /></div>
          )}
          <div><label>TP Volume Split</label><input type="text" value={config.tp_splits} onChange={e => update('tp_splits', e.target.value)} placeholder="30,25,20,15,10" /><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>Comma-separated % per TP level (must sum to 100)</div></div>
          <div><label>BE Trigger (R)</label><input type="number" step="0.5" value={config.be_trigger_rr} onChange={e => update('be_trigger_rr', +e.target.value)} /></div>
          <div><label>BE Buffer (pips)</label><input type="number" value={config.be_buffer_pips} onChange={e => update('be_buffer_pips', +e.target.value)} /></div>
        </div>
      </div>

      <div className="card">
        <div className="card-header"><span className="card-title">Trailing Stops</span></div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16 }}>
          <div>
            <label>TP2 Trail Method</label>
            <select value={config.trail_method_tp2} onChange={e => update('trail_method_tp2', e.target.value)}>
              <option value="ATR_TRAIL">ATR Trail</option>
              <option value="FIXED_PIPS">Fixed Pips</option>
              <option value="STRUCTURE_TRAIL">Structure Trail</option>
              <option value="PCT_TRAIL">Percentage Trail</option>
            </select>
          </div>
          <div>
            <label>TP3 Trail Method</label>
            <select value={config.trail_method_tp3} onChange={e => update('trail_method_tp3', e.target.value)}>
              <option value="STRUCTURE_TRAIL">Structure Trail</option>
              <option value="ATR_TRAIL">ATR Trail</option>
              <option value="FIXED_PIPS">Fixed Pips</option>
              <option value="PCT_TRAIL">Percentage Trail</option>
            </select>
          </div>
          {config.tp_count >= 4 && (
            <div>
              <label>TP4 Trail Method</label>
              <select value={config.trail_method_tp4} onChange={e => update('trail_method_tp4', e.target.value)}>
                <option value="ATR_TRAIL">ATR Trail</option>
                <option value="STRUCTURE_TRAIL">Structure Trail</option>
                <option value="FIXED_PIPS">Fixed Pips</option>
                <option value="PCT_TRAIL">Percentage Trail</option>
              </select>
            </div>
          )}
          {config.tp_count >= 5 && (
            <div>
              <label>TP5 Trail Method</label>
              <select value={config.trail_method_tp5} onChange={e => update('trail_method_tp5', e.target.value)}>
                <option value="STRUCTURE_TRAIL">Structure Trail</option>
                <option value="ATR_TRAIL">ATR Trail</option>
                <option value="FIXED_PIPS">Fixed Pips</option>
                <option value="PCT_TRAIL">Percentage Trail</option>
              </select>
            </div>
          )}
          <div><label>ATR Multiplier</label><input type="number" step="0.1" value={config.atr_trail_multiplier} onChange={e => update('atr_trail_multiplier', +e.target.value)} /></div>
          <div><label>Fixed Trail Pips</label><input type="number" value={config.trail_pips} onChange={e => update('trail_pips', +e.target.value)} /></div>
          <div><label>Trail % (for PCT_TRAIL)</label><input type="number" step="0.1" value={config.trail_pct} onChange={e => update('trail_pct', +e.target.value)} /><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>% of price as trail distance</div></div>
          <div><label>Trail Activation (RR)</label><input type="number" step="0.1" value={config.trail_activation_rr} onChange={e => update('trail_activation_rr', +e.target.value)} /><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>Start trailing after this R</div></div>
          <div><label>Trail Step (Pips)</label><input type="number" step="0.5" value={config.trail_step_pips} onChange={e => update('trail_step_pips', +e.target.value)} /><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>Minimum SL hop distance</div></div>
          <div><label>Structure Swing Bars</label><input type="number" value={config.trail_structure_bars} onChange={e => update('trail_structure_bars', +e.target.value)} /><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>Left/Right bars for structure</div></div>
        </div>
      </div>

      <div className="card">
        <div className="card-header"><span className="card-title">Compounding</span></div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', textTransform: 'none' }}>
            <input type="checkbox" checked={config.compounding_enabled} onChange={e => update('compounding_enabled', e.target.checked)} style={{ width: 16, height: 16 }} />
            Enable Fixed-Dollar Compounding (overrides % risk)
          </label>
        </div>
        {config.compounding_enabled && (
          <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', padding: 12, background: 'var(--bg-tertiary)', borderRadius: 'var(--radius-xs)' }}>
            When enabled, the bot uses the 18-step compounding plan from CompoundingPlan_Spec.md instead of percentage-based risk.
          </div>
        )}
      </div>

      <div className="card">
        <div className="card-header"><span className="card-title">Session Filter</span></div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', textTransform: 'none' }}>
            <input type="checkbox" checked={config.session_filter_enabled} onChange={e => update('session_filter_enabled', e.target.checked)} style={{ width: 16, height: 16 }} />
            Enable Session Filter (London/NY Kill Zones only)
          </label>
        </div>
        <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: 8 }}>
          {config.session_filter_enabled
            ? 'Only trading during London and NY kill zones. Asian session signals are blocked.'
            : 'Trading during all sessions including Asian session (22:00–06:00 GMT).'
          }
        </div>
      </div>

      <button className="btn btn-primary" style={{ justifySelf: 'start' }} onClick={handleSave} disabled={mutation.isPending}>
        {mutation.isPending ? <Loader2 size={14} className="spin" /> : saved ? <Check size={14} /> : <Save size={14} />}
        {mutation.isPending ? 'Saving...' : saved ? 'Saved!' : 'Save Risk Configuration'}
      </button>
      {mutation.isError && (
        <div style={{ color: 'var(--red)', fontSize: '0.8rem' }}>
          Failed to save: {mutation.error?.response?.data?.detail || mutation.error?.message}
        </div>
      )}
    </div>
  );
}
