import { useEffect, useMemo, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Shield, Save, Loader2, Check } from 'lucide-react';
import { getConfig, getParameterSchema, updateConfig } from '../../services/api';
import { invalidateConfigDependents } from '../../utils/invalidate';
import { useConnectionStore, useAuthStore } from '../../store';
import SchemaForm from '../../components/SchemaForm';
import { ACCOUNT_ONLY_KEYS, SLOT_RISK_SECTIONS } from '../../components/slotSpec';

/**
 * Settings > Defaults
 *
 * What this page is, now that risk and strategy parameters belong to the slot
 * (symbol x strategy):
 *
 *   1. the values a slot uses until it sets its own - rendered from the SAME
 *      schema sections the slot editor shows, so the two cannot drift;
 *   2. the few things a slot cannot own, because they describe the account or
 *      the broker rather than a trading policy (leverage, margin, prop firm).
 *
 * It no longer carries strategy parameters. Those were per strategy but shared
 * by every symbol running it, which is exactly what per-slot parameters
 * replaced - and one block (CRT) configured a strategy the backend no longer
 * has, so its fields wrote a key nothing read.
 */

// Account facts: a slot may not override these (risk/slot_book.py
// ACCOUNT_ONLY_KEYS), so they are edited here and nowhere else.
// The account's own fields: ACCOUNT_ONLY_KEYS (a slot may not override those)
// plus `use_strategy_exit_defaults`, which bot_service and position_manager read
// off the account and which decides whether a strategy's measured exits replace
// the defaults above. Derived, so a key added to the backend list appears here.
const ACCOUNT_KEYS = ['use_strategy_exit_defaults', ...ACCOUNT_ONLY_KEYS];

export default function RiskSettings() {
  const { status } = useConnectionStore();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const queryClient = useQueryClient();
  const [saved, setSaved] = useState(false);

  // Only the two blocks this page owns. Field defaults come from the schema
  // (generated from RiskParams itself), so there is no hand-maintained mirror
  // of the dataclass here any more.
  const [config, setConfig] = useState({
    risk: {},
    prop_firm: {
      account_mode: 'personal',
      firm_name: '',
      challenge_type: 'none',
      account_size: 10000.0,
      initial_balance: 10000.0,
      drawdown_type: 'trailing',
      max_daily_loss_pct: 5.0,
      max_total_drawdown_pct: 10.0,
      drawdown_uses_equity: true,
      overnight_holding_allowed: true,
      weekend_holding_allowed: true,
      news_trading_allowed: true,
      news_blackout_before_min: 15,
      news_blackout_after_min: 45,
      max_lot_sizes: {},
      profit_target_pct: 0.0,
      min_trading_days: 0,
    },
  });

  const { data: remoteConfig } = useQuery({
    queryKey: ['config'],
    queryFn: () => getConfig().then(r => r.data),
    enabled: status === 'ONLINE' && isAuthenticated,
  });

  const { data: schemaResp } = useQuery({
    queryKey: ['parameterSchema'],
    queryFn: () => getParameterSchema().then(r => r.data),
    enabled: status === 'ONLINE' && isAuthenticated,
    staleTime: 300000,
  });
  const schema = schemaResp?.fields;

  useEffect(() => {
    if (!remoteConfig?.config) return;
    const cfg = remoteConfig.config;
    setConfig(prev => ({
      risk: { ...(cfg.risk || {}) },
      prop_firm: cfg.prop_firm || prev.prop_firm,
    }));
  }, [remoteConfig]);

  const mutation = useMutation({
    mutationFn: (newConfig) => updateConfig({ config: newConfig }),
    onSuccess: () => {
      // Every cached response that embeds config, not just ['config'] itself.
      invalidateConfigDependents(queryClient);
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    },
  });

  // `trail_activation_rr` is the legacy name for `trail_trigger_rr`. The engine
  // prefers the latter and config_schema warns when they disagree, so keep them
  // in lockstep rather than emitting a warning on every save.
  const setRisk = (next) => setConfig(prev => {
    const risk = { ...next };
    if (risk.trail_trigger_rr !== prev.risk.trail_trigger_rr) risk.trail_activation_rr = risk.trail_trigger_rr;
    else if (risk.trail_activation_rr !== prev.risk.trail_activation_rr) risk.trail_trigger_rr = risk.trail_activation_rr;
    return { ...prev, risk };
  });

  const update = (key, val) => {
    if (key === 'prop_firm') { setConfig(prev => ({ ...prev, prop_firm: val })); return; }
    setRisk({ ...config.risk, [key]: val });
  };

  // One schema feeds both cards, so a field can only appear in one of them.
  const riskByName = useMemo(() => Object.fromEntries(
    (schema || []).filter(r => r.group === 'risk')
      .map(r => [r.key.split('.').slice(1).join('.'), r]),
  ), [schema]);

  const defaultSections = useMemo(
    () => SLOT_RISK_SECTIONS
      .map(([title, keys]) => [title, keys.map(k => riskByName[k]).filter(Boolean)])
      .filter(([, rows]) => rows.length),
    [riskByName],
  );
  const accountRows = useMemo(
    () => ACCOUNT_KEYS.map(k => riskByName[k]).filter(Boolean),
    [riskByName],
  );

  const valueOf = (k) => (config.risk?.[k] ?? riskByName[k]?.default);
  const pct = (k) => Number(valueOf(k) ?? 0);

  const riskWarnings = [];
  if (pct('risk_per_trade_pct') > pct('max_risk_hard_cap_pct')) riskWarnings.push(`Risk per trade (${pct('risk_per_trade_pct')}%) is above the hard cap (${pct('max_risk_hard_cap_pct')}%)`);
  if (pct('risk_per_trade_pct') > pct('max_daily_drawdown_pct')) riskWarnings.push(`Risk per trade (${pct('risk_per_trade_pct')}%) is above the daily drawdown limit (${pct('max_daily_drawdown_pct')}%), so one loss stops the slot for the day`);
  if (pct('max_daily_drawdown_pct') > pct('max_weekly_drawdown_pct')) riskWarnings.push(`Daily drawdown (${pct('max_daily_drawdown_pct')}%) is above weekly (${pct('max_weekly_drawdown_pct')}%)`);
  if (config.prop_firm?.account_mode === 'prop_firm') {
    const dayCap = config.prop_firm?.max_daily_loss_pct ?? 5.0;
    const totalCap = config.prop_firm?.max_total_drawdown_pct ?? 10.0;
    if (pct('risk_per_trade_pct') > dayCap) riskWarnings.push(`Risk per trade (${pct('risk_per_trade_pct')}%) is above the firm daily loss limit (${dayCap}%)`);
    if (pct('max_weekly_drawdown_pct') > totalCap) riskWarnings.push(`Weekly drawdown (${pct('max_weekly_drawdown_pct')}%) is above the firm total drawdown limit (${totalCap}%)`);
    if (pct('max_daily_drawdown_pct') > dayCap) riskWarnings.push(`Daily drawdown (${pct('max_daily_drawdown_pct')}%) is above the firm daily loss limit (${dayCap}%)`);
  }

  // Only the two blocks this page owns are posted. Strategy parameters and the
  // slots themselves are saved by Settings > Trading Book, so a save here can
  // no longer overwrite them with this page's idea of a default.
  const handleSave = () => mutation.mutate({ risk: config.risk, prop_firm: config.prop_firm });

  return (
    <div style={{ display: 'grid', gap: 20, maxWidth: 860 }}>
      <div style={{ padding: 12, background: 'var(--bg-tertiary)', border: '1px solid var(--border)',
                    borderRadius: 'var(--radius-sm)', fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
        <strong>These are starting values, not account-wide limits.</strong>{' '}
        Every symbol + strategy slot runs on its own risk engine and its own circuit breaker, so a
        limit here counts only that slot&apos;s trades: one slot can never use up another&apos;s daily
        budget, positions or drawdown. A slot that sets its own value ignores the one here.
        Give a slot its own numbers in <em>Settings &gt; Trading Book</em>, or on the row you are
        testing in the <em>Backtester</em>.
      </div>

      <div className="card">
        <div className="card-header">
          <span className="card-title"><Shield size={14} /> Defaults every slot starts from</span>
        </div>
        <p style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: -4, marginBottom: 12 }}>
          The same fields a slot shows under <em>Risk &amp; exits</em>. Change one here and every slot
          that has not overridden it follows.
        </p>
        {!schema ? (
          <div style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>Loading parameters...</div>
        ) : defaultSections.map(([title, rows]) => (
          <div key={title} style={{ marginBottom: 16 }}>
            <SchemaForm
              schema={rows.map(r => ({ ...r, group: 'defaults' }))}
              group="defaults"
              title={title}
              values={config.risk}
              onChange={(next) => setRisk(next)}
              showFilter={false}
              minWidth={175}
            />
          </div>
        ))}
      </div>

      <div className="card">
        <div className="card-header"><span className="card-title">Account &amp; broker</span></div>
        <p style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: -4, marginBottom: 12 }}>
          Properties of the account itself. A slot cannot override these.
        </p>
        {accountRows.length ? (
          <SchemaForm
            schema={accountRows.map(r => ({ ...r, group: 'account' }))}
            group="account"
            values={config.risk}
            onChange={(next) => setRisk(next)}
            showFilter={false}
            minWidth={200}
          />
        ) : (
          <div style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>Loading parameters...</div>
        )}
      </div>

      {riskWarnings.length > 0 && (
        <div className="card" style={{ borderColor: 'var(--yellow)' }}>
          <div className="card-header"><span className="card-title" style={{ color: 'var(--yellow)' }}>Check these</span></div>
          <div style={{ display: 'grid', gap: 6 }}>
            {riskWarnings.map((msg, i) => (
              <div key={i} style={{ color: 'var(--yellow)', fontSize: '0.75rem', display: 'flex', gap: 6 }}>
                <span>&bull;</span><span>{msg}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="card">
        <div className="card-header"><span className="card-title">Prop Firm / Broker Settings</span></div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', textTransform: 'none' }}>
            <input type="checkbox" checked={config.prop_firm?.account_mode === 'prop_firm'} onChange={e => update('prop_firm', { ...config.prop_firm, account_mode: e.target.checked ? 'prop_firm' : 'personal' })} style={{ width: 16, height: 16 }} />
            Enable Prop Firm Rules
          </label>
          <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>Activates hard circuit breakers for challenge/funded account rules</span>
        </div>

        {config.prop_firm?.account_mode === 'prop_firm' && (
          <div style={{ display: 'grid', gap: 16, padding: 12, background: 'var(--bg-tertiary)', borderRadius: 'var(--radius-xs)' }}>
            {/* Row 1: Identity */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 12 }}>
              <div>
                <label>Firm Name</label>
                <input type="text" placeholder="e.g. FundedNext, FIVR, Exness" value={config.prop_firm.firm_name ?? ''} onChange={e => update('prop_firm', { ...config.prop_firm, firm_name: e.target.value })} />
              </div>
              <div>
                <label>Challenge Type</label>
                <input type="text" placeholder="e.g. 1-step, 2-step, express, funded" value={config.prop_firm.challenge_type ?? 'none'} onChange={e => update('prop_firm', { ...config.prop_firm, challenge_type: e.target.value })} />
              </div>
              <div>
                <label>Account Size</label>
                <input type="number" step="1000" value={config.prop_firm.account_size} onChange={e => update('prop_firm', { ...config.prop_firm, account_size: +e.target.value })} />
              </div>
              <div>
                <label>Initial Balance (DD Baseline)</label>
                <input type="number" step="1000" value={config.prop_firm.initial_balance} onChange={e => update('prop_firm', { ...config.prop_firm, initial_balance: +e.target.value })} />
              </div>
            </div>

            {/* Row 2: Drawdown rules */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 12 }}>
              <div>
                <label>Drawdown Type</label>
                <select value={config.prop_firm.drawdown_type ?? 'trailing'} onChange={e => update('prop_firm', { ...config.prop_firm, drawdown_type: e.target.value })}>
                  <option value="trailing">Trailing (from high-water mark)</option>
                  <option value="static">Static (from initial balance)</option>
                </select>
              </div>
              <div>
                <label>Max Daily Loss (%)</label>
                <input type="number" step="0.1" min="0.1" max="20"
                  value={config.prop_firm.max_daily_loss_pct ?? 5.0}
                  onChange={e => update('prop_firm', { ...config.prop_firm, max_daily_loss_pct: +e.target.value })} />
              </div>
              <div>
                <label>Max Overall Drawdown (%)</label>
                <input type="number" step="0.1" min="0.1" max="50"
                  value={config.prop_firm.max_total_drawdown_pct ?? 10.0}
                  onChange={e => update('prop_firm', { ...config.prop_firm, max_total_drawdown_pct: +e.target.value })} />
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                <label>Drawdown Uses Equity</label>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontWeight: 400, cursor: 'pointer' }}>
                  <input type="checkbox"
                    checked={config.prop_firm.drawdown_uses_equity ?? true}
                    onChange={e => update('prop_firm', { ...config.prop_firm, drawdown_uses_equity: e.target.checked })} />
                  <span style={{ fontSize: '0.8rem' }}>
                    {(config.prop_firm.drawdown_uses_equity ?? true) ? '✅ Balance + unrealized P&L' : '⚠️ Closed balance only'}
                  </span>
                </label>
              </div>
            </div>

            {/* Row 3: Trading rules */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr 1fr', gap: 12, padding: '8px 0', borderTop: '1px solid var(--border-subtle)' }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontWeight: 400, cursor: 'pointer', fontSize: '0.8rem' }}>
                <input type="checkbox" checked={config.prop_firm.overnight_holding_allowed ?? true}
                  onChange={e => update('prop_firm', { ...config.prop_firm, overnight_holding_allowed: e.target.checked })} />
                Overnight Holding
              </label>
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontWeight: 400, cursor: 'pointer', fontSize: '0.8rem' }}>
                <input type="checkbox" checked={config.prop_firm.weekend_holding_allowed ?? true}
                  onChange={e => update('prop_firm', { ...config.prop_firm, weekend_holding_allowed: e.target.checked })} />
                Weekend Holding
              </label>
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontWeight: 400, cursor: 'pointer', fontSize: '0.8rem' }}>
                <input type="checkbox" checked={config.prop_firm.news_trading_allowed ?? true}
                  onChange={e => update('prop_firm', { ...config.prop_firm, news_trading_allowed: e.target.checked })} />
                News Trading
              </label>
              <div>
                <label style={{ fontSize: '0.7rem' }}>Blackout Before (min)</label>
                <input type="number" min="0" max="120" value={config.prop_firm.news_blackout_before_min ?? 15}
                  onChange={e => update('prop_firm', { ...config.prop_firm, news_blackout_before_min: +e.target.value })} />
              </div>
              <div>
                <label style={{ fontSize: '0.7rem' }}>Blackout After (min)</label>
                <input type="number" min="0" max="120" value={config.prop_firm.news_blackout_after_min ?? 45}
                  onChange={e => update('prop_firm', { ...config.prop_firm, news_blackout_after_min: +e.target.value })} />
              </div>
            </div>

            {/* Row 4: Challenge pass conditions */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, padding: '8px 0', borderTop: '1px solid var(--border-subtle)' }}>
              <div>
                <label>Profit Target (%)</label>
                <input type="number" step="0.5" min="0" value={config.prop_firm.profit_target_pct ?? 0}
                  onChange={e => update('prop_firm', { ...config.prop_firm, profit_target_pct: +e.target.value })} />
                <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>Challenge pass target. Set 0 for funded/personal accounts.</div>
              </div>
              <div>
                <label>Min Trading Days</label>
                <input type="number" step="1" min="0" value={config.prop_firm.min_trading_days ?? 0}
                  onChange={e => update('prop_firm', { ...config.prop_firm, min_trading_days: +e.target.value })} />
                <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>Minimum days with at least 1 trade. Set 0 if no requirement.</div>
              </div>
            </div>

            {/* Max Lot Sizes per Asset */}
            <div style={{ padding: '8px 0', borderTop: '1px solid var(--border-subtle)' }}>
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

            {/* Reset buttons */}
            <div style={{ display: 'flex', gap: 12, alignItems: 'center', padding: '8px 0', borderTop: '1px solid var(--border-subtle)' }}>
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
