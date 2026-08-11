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
    max_daily_drawdown_pct: 3.0,
    max_weekly_drawdown_pct: 6.0,
    max_daily_trades: 5,
    max_concurrent_positions: 3,
    max_positions_per_symbol: 1,
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
    atr_trail_multiplier_tp1: 1.5,
    atr_trail_multiplier_tp2: 1.5,
    atr_trail_multiplier_tp3: 1.5,
    atr_trail_multiplier_tp4: 1.5,
    atr_trail_multiplier_tp5: 1.5,
    trail_pips: 15,
    trail_pct: 0.5,
    trail_activation_rr: 1.0,
    trail_step_pips: 5.0,
    trail_structure_bars: 3,
    compounding_enabled: false,

    prop_firm: {
      account_mode: 'personal',
      challenge_type: 'none',
      account_size: 10000.0,
      initial_balance: 10000.0,
      max_lot_sizes: {}
    }
  });

  // Load current config from backend
  const { data: remoteConfig } = useQuery({
    queryKey: ['config'],
    queryFn: () => getConfig().then(r => r.data),
    enabled: status === 'ONLINE' && isAuthenticated,
  });

  useEffect(() => {
    if (remoteConfig?.config) {
      const cfg = remoteConfig.config;
      // Merge remote config with defaults
      setConfig(prev => ({
        ...prev,
        // Flat mapping for risk parameters
        ...Object.fromEntries(
          Object.entries(cfg.risk || {}).filter(([k]) => k in prev)
        ),
        // Additional top-level mapping
        prop_firm: cfg.prop_firm || prev.prop_firm,
        compounding_enabled: cfg.compounding?.compounding_enabled ?? prev.compounding_enabled,
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

  const handleSave = () => {
    const { prop_firm, compounding_enabled, ...riskParams } = config;
    mutation.mutate({
      risk: riskParams,
      prop_firm: prop_firm,
      compounding: { compounding_enabled }
    });
  };

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
          <div><label>Max Daily Drawdown (%)</label><input type="number" step="0.1" min="0.1" value={config.max_daily_drawdown_pct} onChange={e => update('max_daily_drawdown_pct', +e.target.value)} /></div>
          <div><label>Max Weekly Drawdown (%)</label><input type="number" step="0.1" min="0.1" value={config.max_weekly_drawdown_pct} onChange={e => update('max_weekly_drawdown_pct', +e.target.value)} /></div>
          <div><label>Max Daily Trades</label><input type="number" step="1" min="1" value={config.max_daily_trades} onChange={e => update('max_daily_trades', +e.target.value)} /><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>Prevents overtrading in chop</div></div>
          <div><label>Max Open Positions</label><input type="number" value={config.max_concurrent_positions} onChange={e => update('max_concurrent_positions', +e.target.value)} /><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>Global across all symbols</div></div>
          <div><label>Max Positions / Symbol</label><input type="number" value={config.max_positions_per_symbol} onChange={e => update('max_positions_per_symbol', +e.target.value)} /><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>Limit trades per symbol</div></div>
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
              <option value="NONE">None</option>
              <option value="ATR_TRAIL">ATR Trail</option>
              <option value="FIXED_PIPS">Fixed Pips</option>
              <option value="STRUCTURE_TRAIL">Structure Trail</option>
              <option value="PCT_TRAIL">Percentage Trail</option>
            </select>
            {config.trail_method_tp2 === 'ATR_TRAIL' && (
              <div style={{ marginTop: 8 }}>
                <label>ATR Multiplier</label>
                <input type="number" step="0.1" value={config.atr_trail_multiplier_tp2} onChange={e => update('atr_trail_multiplier_tp2', +e.target.value)} />
              </div>
            )}
          </div>
          <div>
            <label>TP3 Trail Method</label>
            <select value={config.trail_method_tp3} onChange={e => update('trail_method_tp3', e.target.value)}>
              <option value="NONE">None</option>
              <option value="STRUCTURE_TRAIL">Structure Trail</option>
              <option value="ATR_TRAIL">ATR Trail</option>
              <option value="FIXED_PIPS">Fixed Pips</option>
              <option value="PCT_TRAIL">Percentage Trail</option>
            </select>
            {config.trail_method_tp3 === 'ATR_TRAIL' && (
              <div style={{ marginTop: 8 }}>
                <label>ATR Multiplier</label>
                <input type="number" step="0.1" value={config.atr_trail_multiplier_tp3} onChange={e => update('atr_trail_multiplier_tp3', +e.target.value)} />
              </div>
            )}
          </div>
          {config.tp_count >= 4 && (
            <div>
              <label>TP4 Trail Method</label>
              <select value={config.trail_method_tp4} onChange={e => update('trail_method_tp4', e.target.value)}>
                <option value="NONE">None</option>
                <option value="ATR_TRAIL">ATR Trail</option>
                <option value="STRUCTURE_TRAIL">Structure Trail</option>
                <option value="FIXED_PIPS">Fixed Pips</option>
                <option value="PCT_TRAIL">Percentage Trail</option>
              </select>
              {config.trail_method_tp4 === 'ATR_TRAIL' && (
                <div style={{ marginTop: 8 }}>
                  <label>ATR Multiplier</label>
                  <input type="number" step="0.1" value={config.atr_trail_multiplier_tp4} onChange={e => update('atr_trail_multiplier_tp4', +e.target.value)} />
                </div>
              )}
            </div>
          )}
          {config.tp_count >= 5 && (
            <div>
              <label>TP5 Trail Method</label>
              <select value={config.trail_method_tp5} onChange={e => update('trail_method_tp5', e.target.value)}>
                <option value="NONE">None</option>
                <option value="STRUCTURE_TRAIL">Structure Trail</option>
                <option value="ATR_TRAIL">ATR Trail</option>
                <option value="FIXED_PIPS">Fixed Pips</option>
                <option value="PCT_TRAIL">Percentage Trail</option>
              </select>
              {config.trail_method_tp5 === 'ATR_TRAIL' && (
                <div style={{ marginTop: 8 }}>
                  <label>ATR Multiplier</label>
                  <input type="number" step="0.1" value={config.atr_trail_multiplier_tp5} onChange={e => update('atr_trail_multiplier_tp5', +e.target.value)} />
                </div>
              )}
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
        <div className="card-header"><span className="card-title">Prop Firm Settings</span></div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', textTransform: 'none' }}>
            <input type="checkbox" checked={config.prop_firm?.account_mode === 'prop_firm'} onChange={e => update('prop_firm', { ...config.prop_firm, account_mode: e.target.checked ? 'prop_firm' : 'personal' })} style={{ width: 16, height: 16 }} />
            Enable Prop Firm Rules (BloomFunded strict rules)
          </label>
        </div>
        {/* max_risk_hard_cap_pct: applies to ALL account modes — it is the absolute safety cap on
            the position sizer and is stored in prop_firm config but always active. */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 12, padding: '10px 12px', background: 'var(--bg-tertiary)', borderRadius: 'var(--radius-xs)', border: '1px solid var(--border-subtle)' }}>
          <div>
            <label>Max Risk Hard Cap (% of balance)</label>
            <input type="number" step="0.1" min="0.5" max="10"
              value={config.prop_firm?.max_risk_hard_cap_pct ?? 3.0}
              onChange={e => update('prop_firm', { ...config.prop_firm, max_risk_hard_cap_pct: +e.target.value })} />
            <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)', display: 'block', marginTop: 2 }}>
              Absolute position sizer cap — overrides lot sizing. Active for ALL account modes. Reset to 3.0 to restore default.
            </span>
          </div>
          <div style={{ display: 'flex', alignItems: 'flex-end', paddingBottom: 4 }}>
            <button type="button" className="btn btn-secondary" style={{ fontSize: '0.78rem', padding: '5px 12px' }}
              onClick={() => update('prop_firm', { ...config.prop_firm, max_risk_hard_cap_pct: 3.0 })}>
              Reset Cap to Default (3%)
            </button>
          </div>
        </div>

        {config.prop_firm?.account_mode === 'prop_firm' && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16, padding: 12, background: 'var(--bg-tertiary)', borderRadius: 'var(--radius-xs)' }}>
            <div>
              <label>Challenge Type</label>
              <select value={config.prop_firm.challenge_type} onChange={e => update('prop_firm', { ...config.prop_firm, challenge_type: e.target.value })}>
                <option value="none">None / Funded</option>
                <option value="1-step">1-Step / Flex (Trailing DD)</option>
                <option value="2-step">2-Step (Static DD)</option>
              </select>
            </div>
            <div>
              <label>Account Size</label>
              <input type="number" step="1000" value={config.prop_firm.account_size} onChange={e => update('prop_firm', { ...config.prop_firm, account_size: +e.target.value })} />
            </div>
            <div>
              <label>Initial Balance (DD Baseline)</label>
              <input type="number" step="1000" value={config.prop_firm.initial_balance} onChange={e => update('prop_firm', { ...config.prop_firm, initial_balance: +e.target.value })} />
            </div>
            <div>
              <label>Max Daily Drawdown — Hard Block (%)</label>
              <input type="number" step="0.1" min="0.1" max="20"
                value={config.prop_firm.max_daily_loss_pct ?? (config.prop_firm.challenge_type === '1-step' ? 4.0 : 5.0)}
                onChange={e => update('prop_firm', { ...config.prop_firm, max_daily_loss_pct: +e.target.value })} />
              <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>When breached, no new signals until manually reset</span>
            </div>
            <div>
              <label>Max Overall Drawdown — Hard Block (%)</label>
              <input type="number" step="0.1" min="0.1" max="50"
                value={config.prop_firm.max_total_drawdown_pct ?? (config.prop_firm.challenge_type === '1-step' ? 6.0 : 10.0)}
                onChange={e => update('prop_firm', { ...config.prop_firm, max_total_drawdown_pct: +e.target.value })} />
              <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>From peak/initial balance</span>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <label>Drawdown Uses Floating Equity</label>
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontWeight: 400, cursor: 'pointer' }}>
                <input type="checkbox"
                  checked={config.prop_firm.drawdown_uses_equity ?? true}
                  onChange={e => update('prop_firm', { ...config.prop_firm, drawdown_uses_equity: e.target.checked })} />
                <span style={{ fontSize: '0.8rem' }}>
                  {(config.prop_firm.drawdown_uses_equity ?? true) ? '✅ Balance + unrealized P&L' : '⚠️ Closed balance only'}
                </span>
              </label>
            </div>

            <div style={{ gridColumn: '1 / -1', marginTop: '4px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                <label style={{ margin: 0 }}>Max Lot Sizes per Asset</label>
                <button type="button" className="btn btn-secondary" style={{ padding: '4px 8px', fontSize: '0.75rem' }} onClick={() => {
                  update('prop_firm', {
                    ...config.prop_firm,
                    max_lot_sizes: { ...(config.prop_firm.max_lot_sizes || {}), 'SYMBOL': 1.0 }
                  });
                }}>+ Add Symbol Limit</button>
              </div>
              <div style={{ display: 'grid', gap: 8 }}>
                {Object.entries(config.prop_firm.max_lot_sizes || {}).map(([symbol, maxLots]) => (
                  <div key={symbol} style={{ display: 'grid', gridTemplateColumns: '1fr 1fr auto', gap: 8, alignItems: 'center' }}>
                    <input type="text" placeholder="Symbol" defaultValue={symbol} onBlur={(e) => {
                      const newSym = e.target.value.toUpperCase();
                      if (newSym && newSym !== symbol) {
                        const newLots = { ...config.prop_firm.max_lot_sizes };
                        delete newLots[symbol];
                        newLots[newSym] = maxLots;
                        update('prop_firm', { ...config.prop_firm, max_lot_sizes: newLots });
                      }
                    }} />
                    <input type="number" step="0.1" placeholder="Max Lots" value={maxLots} onChange={(e) => {
                      const newLots = { ...config.prop_firm.max_lot_sizes, [symbol]: +e.target.value };
                      update('prop_firm', { ...config.prop_firm, max_lot_sizes: newLots });
                    }} />
                    <button type="button" className="btn btn-secondary" style={{ padding: '4px 8px' }} onClick={() => {
                      const newLots = { ...config.prop_firm.max_lot_sizes };
                      delete newLots[symbol];
                      update('prop_firm', { ...config.prop_firm, max_lot_sizes: newLots });
                    }}>X</button>
                  </div>
                ))}
                {Object.keys(config.prop_firm.max_lot_sizes || {}).length === 0 && (
                  <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>No max lot limits defined (defaults to unlimited).</div>
                )}
              </div>
            </div>
            {/* Reset breach button — visible only when prop firm is in breached state */}
            <div style={{ gridColumn: '1 / -1', marginTop: 8, display: 'flex', gap: 12, alignItems: 'center', padding: '8px 0', borderTop: '1px solid var(--border-subtle)' }}>
              <button type="button" className="btn btn-secondary" style={{ fontSize: '0.8rem', padding: '6px 14px', borderColor: 'var(--yellow)', color: 'var(--yellow)' }}
                onClick={async () => {
                  try {
                    const { api } = await import('../../services/api');
                    const res = await api.post('/api/prop-firm/reset-breach');
                    alert(res.data?.message || 'Breach reset.');
                  } catch (e) { alert('Failed to reset breach: ' + e.message); }
                }}>
                ⚠️ Reset Drawdown Breach
              </button>
              <button type="button" className="btn btn-secondary" style={{ fontSize: '0.8rem', padding: '6px 14px', borderColor: 'var(--red)', color: 'var(--red)' }}
                onClick={async () => {
                  if (!confirm('Reset circuit breaker? This will clear all loss streaks and resume trading.')) return;
                  try {
                    const { api } = await import('../../services/api');
                    const res = await api.post('/api/circuit-breaker/reset');
                    alert(res.data?.message || 'Circuit breaker reset.');
                  } catch (e) { alert('Failed to reset CB: ' + e.message); }
                }}>
                🔄 Reset Circuit Breaker
              </button>
              <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
                Use these if the bot is incorrectly blocked after a restart or manual trade intervention.
              </span>
            </div>
          </div>
        )}
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
