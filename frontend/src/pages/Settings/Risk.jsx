import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Shield, Save, Loader2, Check } from 'lucide-react';
import { getConfig, updateConfig } from '../../services/api';
import { invalidateConfigDependents } from '../../utils/invalidate';
import { useConnectionStore, useAuthStore } from '../../store';

export default function RiskSettings() {
  const { status } = useConnectionStore();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const queryClient = useQueryClient();
  const [saved, setSaved] = useState(false);

  // Every default below mirrors the authoritative backend RiskParams dataclass
  // (backend/core/config_schema.py). This form posts each value explicitly, so
  // a stale default here silently overrides the backend rather than deferring
  // to it.
  const [config, setConfig] = useState({
    risk_per_trade_pct: 0.5,
    max_risk_hard_cap_pct: 2.0,
    max_daily_drawdown_pct: 3.0,
    max_weekly_drawdown_pct: 6.0,
    max_daily_trades: 5,
    max_concurrent_positions: 3,
    max_positions_per_symbol: 1,
    target_profit_enabled: false,
    max_daily_profit: 500.0,
    max_weekly_profit: 2000.0,
    min_rr: 3.0,
    be_trigger_rr: 1.5,
    be_buffer_pips: 0.0,
    be_buffer_atr_mult: 0.10,
    min_sl_pips: 10.0,
    max_account_leverage: 30.0,
    // [sizing] RiskParams.sizing_basis. Falls into `riskParams` on save, so it
    // reaches the live sizer through the existing channel. STATIC matches the
    // backend default; BALANCE/EQUITY are the compounding modes.
    sizing_basis: 'STATIC',
    tp_count: 3,
    tp1_rr: 1.5,
    tp2_rr: 3.0,
    tp3_rr: 5.0,
    tp4_rr: 10.0,
    tp5_rr: 15.0,
    tp_splits: '50,30,20',
    // [T2.3] These three were missing from live Risk settings entirely, which
    // is the live-trading half of the bug that left trail_method NULL on
    // 3,528/3,528 backtest trades: with tp_count=1 the ONLY leg had no trail
    // method, so RiskEngine's `if trail_method and ...` guard short-circuited
    // on every tick and no position ever trailed.
    trail_method_tp1: 'NONE',
    trail_mode: 'EITHER',
    trail_trigger_rr: 2.0,
    trail_activation_rr: 2.0,   // legacy alias, kept in sync by `update`
    // [17.6] Previously absent from this defaults block, so they were dropped
    // from the saved payload entirely and could never be set from the UI.
    trail_trigger_tp_level: 1,
    trail_require_be_first: false,
    be_trigger_tp_level: 1,
    be_spread_multiple: 2.0,
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
    // [17.7] `trail_activation_rr: 1.5` was declared a SECOND time here.
    // JS object literals are last-wins, so it silently overrode the 2.0 set
    // above and shipped defaults where the UI showed 2.0R while the engine
    // (which reads trail_activation_rr) armed at 1.5R. Removed; the single
    // declaration beside trail_trigger_rr is now authoritative.
    trail_step_pips: 5.0,
    trail_structure_bars: 3,

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
    // FEAT-1: Live strategy params
    vwap: {
      sl_method: 'auto',
      sl_points: 80.0,
      sl_atr_multiplier: 3.0,
      min_sl_pips: 8.0,
      min_sl_spread_mult: 4.0,
      target_rr: 2.0,
      max_trades_per_day: 4,
    },
    crt: {
      min_sl_pips: 15.0,
      sl_atr_mult: 1.0,
      target_r_multiple: 1.5,
    },
    apa: {
      sl_buffer_atr_mult: 0.5,
      min_sl_pips: 12.0,
      min_sl_atr_mult: 1.0,
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
        // Strategy sub-configs (FEAT-1)
        vwap: { ...prev.vwap, ...(cfg.vwap || {}) },
        crt: { ...prev.crt, ...(cfg.crt || {}) },
        apa: { ...prev.apa, ...(cfg.apa || {}) },
      }));
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

  const update = (key, val) => {
    // `trail_activation_rr` is the legacy name for the same threshold. The
    // engine prefers `trail_trigger_rr`, but config_schema warns when the two
    // disagree ("the UI shows one number while the engine uses another"), so
    // keep them in lockstep rather than emitting a warning on every save.
    // [17.7] Sync BOTH ways. This previously only handled trail_trigger_rr,
    // so editing the "Trail Activation (RR)" control left trail_trigger_rr
    // stale and the two disagreed again.
    if (key === 'trail_trigger_rr' || key === 'trail_activation_rr') {
      setConfig({ ...config, trail_trigger_rr: val, trail_activation_rr: val });
      return;
    }
    setConfig({ ...config, [key]: val });
  };

  const riskWarnings = [];
  if (config.risk_per_trade_pct > config.max_risk_hard_cap_pct) riskWarnings.push(`Risk Per Trade (${config.risk_per_trade_pct}%) > Max Risk Hard Cap (${config.max_risk_hard_cap_pct}%)`);
  if (config.risk_per_trade_pct > config.max_daily_drawdown_pct) riskWarnings.push(`Risk Per Trade (${config.risk_per_trade_pct}%) > Max Daily Drawdown (${config.max_daily_drawdown_pct}%)`);
  if (config.risk_per_trade_pct > config.max_weekly_drawdown_pct) riskWarnings.push(`Risk Per Trade (${config.risk_per_trade_pct}%) > Max Weekly Drawdown (${config.max_weekly_drawdown_pct}%)`);
  if (config.max_daily_drawdown_pct > config.max_weekly_drawdown_pct) riskWarnings.push(`Max Daily Drawdown (${config.max_daily_drawdown_pct}%) > Max Weekly Drawdown (${config.max_weekly_drawdown_pct}%)`);
  if (config.max_risk_hard_cap_pct > config.max_daily_drawdown_pct) riskWarnings.push(`Max Risk Hard Cap (${config.max_risk_hard_cap_pct}%) > Max Daily Drawdown (${config.max_daily_drawdown_pct}%)`);
  if (config.max_risk_hard_cap_pct > config.max_weekly_drawdown_pct) riskWarnings.push(`Max Risk Hard Cap (${config.max_risk_hard_cap_pct}%) > Max Weekly Drawdown (${config.max_weekly_drawdown_pct}%)`);
  if (config.prop_firm?.account_mode === 'prop_firm') {
    if (config.risk_per_trade_pct > (config.prop_firm?.max_total_drawdown_pct ?? 10.0)) riskWarnings.push(`Risk Per Trade (${config.risk_per_trade_pct}%) > Prop Firm Max Total Drawdown (${config.prop_firm?.max_total_drawdown_pct ?? 10.0}%)`);
    if (config.risk_per_trade_pct > (config.prop_firm?.max_daily_loss_pct ?? 5.0)) riskWarnings.push(`Risk Per Trade (${config.risk_per_trade_pct}%) > Prop Firm Max Daily Loss (${config.prop_firm?.max_daily_loss_pct ?? 5.0}%)`);
    if (config.max_weekly_drawdown_pct > (config.prop_firm?.max_total_drawdown_pct ?? 10.0)) riskWarnings.push(`Max Weekly Drawdown (${config.max_weekly_drawdown_pct}%) > Prop Firm Max Total Drawdown (${config.prop_firm?.max_total_drawdown_pct ?? 10.0}%)`);
    if (config.max_daily_drawdown_pct > (config.prop_firm?.max_daily_loss_pct ?? 5.0)) riskWarnings.push(`Max Daily Drawdown (${config.max_daily_drawdown_pct}%) > Prop Firm Max Daily Loss (${config.prop_firm?.max_daily_loss_pct ?? 5.0}%)`);
  }

  const handleSave = () => {
    const { prop_firm, vwap, crt, apa, ...riskParams } = config;
    mutation.mutate({
      risk: riskParams,
      prop_firm: prop_firm,
      // Strategy sub-configs (FEAT-1)
      vwap,
      crt,
      apa,
    });
  };

  return (
    <div style={{ display: 'grid', gap: 20, maxWidth: 800 }}>
      <div className="card">
        <div className="card-header"><span className="card-title"><Shield size={14} /> Position Sizing</span></div>
        {riskWarnings.length > 0 && (
          <div style={{ marginBottom: 16, padding: 12, background: 'var(--bg-warning)', border: '1px solid var(--yellow)', borderRadius: 'var(--radius-xs)', color: 'var(--yellow)', fontSize: '0.85rem' }}>
            <strong style={{ display: 'block', marginBottom: 6 }}>⚠️ Risk Parameter Mismatches Detected:</strong>
            <ul style={{ margin: 0, paddingLeft: 20 }}>
              {riskWarnings.map((w, i) => <li key={i}>{w}</li>)}
            </ul>
          </div>
        )}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16 }}>
          <div><label>Risk Per Trade (%)</label><input type="number" step="0.1" value={config.risk_per_trade_pct} onChange={e => update('risk_per_trade_pct', +e.target.value)} /></div>
          <div><label>Max Risk Hard Cap (%)</label><input type="number" step="0.1" value={config.max_risk_hard_cap_pct} onChange={e => update('max_risk_hard_cap_pct', +e.target.value)} /><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>Absolute position sizer safety net</div></div>
          <div><label>Minimum R:R</label><input type="number" step="0.5" value={config.min_rr} onChange={e => update('min_rr', +e.target.value)} /></div>
          <div><label>Min SL (pips)</label><input type="number" step="0.5" min="0" value={config.min_sl_pips} onChange={e => update('min_sl_pips', +e.target.value)} /><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>Global stop floor. Prevents a sub-spread stop sizing into an untradeable position. Set 0 to disable.</div></div>
          <div><label>Max Account Leverage (×)</label><input type="number" step="1" min="0" value={config.max_account_leverage} onChange={e => update('max_account_leverage', +e.target.value)} /><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>Caps total notional against equity. Blocks the 100×+ sizes MT5 rejects with retcode 10019. Set 0 to disable.</div></div>
          <div>
            <label>Size Against</label>
            <select value={config.sizing_basis ?? 'STATIC'} onChange={e => update('sizing_basis', e.target.value)}>
              <option value="STATIC">Starting balance — no compounding</option>
              <option value="BALANCE">Closed balance — compounds with realised P&amp;L</option>
              <option value="EQUITY">Floating equity — compounds incl. open P&amp;L</option>
            </select>
            <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>
              This is the compounding switch. Set it the same here and in the Backtester, or live and backtest results will not be comparable.
            </div>
          </div>
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
          <div><label>TP Volume Split</label><input type="text" value={config.tp_splits} onChange={e => update('tp_splits', e.target.value)} placeholder="50,30,20" /><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>Comma-separated % per TP level (must sum to 100)</div></div>
          <div><label>BE Trigger (R)</label><input type="number" step="0.5" value={config.be_trigger_rr} onChange={e => update('be_trigger_rr', +e.target.value)} /></div>
          <div><label>BE Buffer (pips)</label><input type="number" step="0.5" min="0" value={config.be_buffer_pips} onChange={e => update('be_buffer_pips', +e.target.value)} /><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>Keep at 0. A non-zero pip buffer can put the break-even stop beyond the market, where it fills as fabricated profit. Use the ATR buffer instead.</div></div>
          <div><label>BE Buffer (× ATR)</label><input type="number" step="0.05" min="0" value={config.be_buffer_atr_mult} onChange={e => update('be_buffer_atr_mult', +e.target.value)} /><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>Scale-aware break-even cushion, as a multiple of ATR(14). This is the safe way to sit slightly above entry.</div></div>
        </div>
      </div>

      <div className="card">
        <div className="card-header"><span className="card-title">Trailing Stops</span></div>

        {/* [T2.3] Trail activation. Without these, RR-mode trailing had no
            trigger and TP1 had no method, so a single-TP position that ran to
            3R and reversed went all the way back to its original stop. */}
        <div style={{
          display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16,
          padding: 12, marginBottom: 16, borderRadius: 'var(--radius-sm)',
          background: 'rgba(59,130,246,0.05)', border: '1px solid rgba(59,130,246,0.25)',
        }}>
          <div>
            <label>Trail Trigger Mode</label>
            <select value={config.trail_mode} onChange={e => update('trail_mode', e.target.value)}>
              <option value="RR">RR — at an R-multiple</option>
              <option value="TP_HIT">TP_HIT — when TP1 closes</option>
              <option value="EITHER">Either, whichever first</option>
              <option value="NONE">Never trail</option>
            </select>
          </div>
          <div>
            <label>Trail Start (R)</label>
            <input
              type="number" step="0.1" min="0"
              value={config.trail_trigger_rr}
              onChange={e => update('trail_trigger_rr', +e.target.value)}
              disabled={config.trail_mode === 'TP_HIT' || config.trail_mode === 'NONE'}
            />
          </div>
          {/* [17.6] These four existed in the schema and were read by the
              engine, but had no control anywhere in the UI — so they could only
              ever hold their defaults, and a user had no way to see or change
              behaviour that was actively affecting live positions. */}
          <div>
            <label>Trail Start (TP level)</label>
            <select
              value={config.trail_trigger_tp_level ?? 1}
              onChange={e => update('trail_trigger_tp_level', +e.target.value)}
              disabled={config.trail_mode === 'RR' || config.trail_mode === 'NONE'}
            >
              {[1, 2, 3, 4, 5].map(n => <option key={n} value={n}>TP{n} closes</option>)}
            </select>
            <small style={{ color: 'var(--text-dim)' }}>Which TP closing arms the trail (TP_HIT / Either modes).</small>
          </div>
          <div>
            <label>Require Break-even First</label>
            <select
              value={config.trail_require_be_first ? 'yes' : 'no'}
              onChange={e => update('trail_require_be_first', e.target.value === 'yes')}
              disabled={config.trail_mode === 'NONE'}
            >
              <option value="no">No — trail as soon as it triggers</option>
              <option value="yes">Yes — only after break-even is set</option>
            </select>
            <small style={{ color: 'var(--text-dim)' }}>Stops the trail arming while the position can still lose.</small>
          </div>
          <div>
            <label>Break-even Trigger (TP level)</label>
            <select
              value={config.be_trigger_tp_level ?? 1}
              onChange={e => update('be_trigger_tp_level', +e.target.value)}
              disabled={config.be_mode === 'RR' || config.be_mode === 'NONE'}
            >
              {[1, 2, 3, 4, 5].map(n => <option key={n} value={n}>TP{n} closes</option>)}
            </select>
            <small style={{ color: 'var(--text-dim)' }}>Which TP closing moves the stop to break-even.</small>
          </div>
          <div>
            <label>Break-even Spread Multiple</label>
            <input
              type="number" step="0.5" min="0"
              value={config.be_spread_multiple ?? 2.0}
              onChange={e => update('be_spread_multiple', +e.target.value)}
              disabled={config.be_mode === 'NONE'}
            />
            <small style={{ color: 'var(--text-dim)' }}>Break-even sits this many spreads above entry, so it cannot close at a loss.</small>
          </div>
          <div>
            <label>TP1 Trail Method</label>
            <select value={config.trail_method_tp1} onChange={e => update('trail_method_tp1', e.target.value)}>
              <option value="NONE">None</option>
              <option value="ATR_TRAIL">ATR Trail</option>
              <option value="FIXED_PIPS">Fixed Pips</option>
              <option value="STRUCTURE_TRAIL">Structure Trail</option>
              <option value="PCT_TRAIL">Percentage Trail</option>
            </select>
            {config.trail_method_tp1 === 'ATR_TRAIL' && (
              <div style={{ marginTop: 8 }}>
                <label>ATR Multiplier</label>
                <input type="number" step="0.1" value={config.atr_trail_multiplier_tp1} onChange={e => update('atr_trail_multiplier_tp1', +e.target.value)} />
              </div>
            )}
          </div>
          <div style={{ gridColumn: '1 / -1', fontSize: '0.72rem', color: 'var(--text-muted)' }}>
            <strong>RR</strong> arms the trail once the position reaches this R-multiple, measured
            from the ORIGINAL stop. <strong>TP_HIT</strong> arms it when TP1 closes — which never
            fires on a single-TP setup, so leave it on RR or Either if <code>tp_count = 1</code>.
            TP1 must have a trail method other than None or nothing trails at all.
          </div>
        </div>

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
          {/* [17.7] Removed: a second control for the same setting as
              "Trail Start (R)" above. Two inputs writing one value let a user
              set them to different numbers, and only one of them synced. */}
          <div><label>Trail Step (Pips)</label><input type="number" step="0.5" value={config.trail_step_pips} onChange={e => update('trail_step_pips', +e.target.value)} /><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>Minimum SL hop distance</div></div>
          <div><label>Structure Swing Bars</label><input type="number" value={config.trail_structure_bars} onChange={e => update('trail_structure_bars', +e.target.value)} /><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>Left/Right bars for structure</div></div>
        </div>
      </div>

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

      {/* FEAT-1: Strategy-Specific Parameters (live trading) */}
      <div className="card">
        <div className="card-header"><span className="card-title">⚙ Strategy Parameters</span></div>
        <div style={{ display: 'grid', gap: 20 }}>

          {/* VWAP */}
          <div>
            <div style={{ fontSize: '0.75rem', fontWeight: 700, color: 'var(--blue)', marginBottom: 8 }}>VWAP Strategy</div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
              <div>
                <label>SL Method</label>
                <select value={config.vwap?.sl_method ?? 'auto'}
                  onChange={e => update('vwap', { ...config.vwap, sl_method: e.target.value })}>
                  <option value="auto">Auto (by instrument class)</option>
                  <option value="fixed_points">Fixed Points</option>
                  <option value="atr_multiple">ATR Multiple</option>
                </select>
                <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', marginTop: 3 }}>
                  Auto routes index CFDs and index futures to the fixed-points stop and everything else (FX, metals, crypto, synthetics) to the ATR multiple. Default: auto.
                </div>
              </div>
              <div>
                <label>SL Points (index only)</label>
                <input type="number" step="1" min="10" value={config.vwap?.sl_points ?? 80}
                  onChange={e => update('vwap', { ...config.vwap, sl_points: +e.target.value })} />
                <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', marginTop: 3 }}>
                  Stop distance in native INDEX points, used only when the resolved SL method is Fixed Points. Not a pipette figure — it does not apply to FX. Default: 80 points.
                </div>
              </div>
              <div>
                <label>SL ATR Multiplier</label>
                <input type="number" step="0.1" min="0" value={config.vwap?.sl_atr_multiplier ?? 3.0}
                  onChange={e => update('vwap', { ...config.vwap, sl_atr_multiplier: +e.target.value })} />
                <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', marginTop: 3 }}>
                  Stop = this × ATR of the M5 entry bars, for every non-index instrument. 0 disables the ATR path. Default: 3.0.
                </div>
              </div>
              <div>
                <label>Min SL Pips (Floor)</label>
                <input type="number" step="0.5" min="0" value={config.vwap?.min_sl_pips ?? 8.0}
                  onChange={e => update('vwap', { ...config.vwap, min_sl_pips: +e.target.value })} />
                <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', marginTop: 3 }}>
                  Absolute stop floor for the low-volatility tail, where 3×ATR alone is only ~2.5× spread. Lower than APA's 12 because VWAP is a scalper with a hard session close. Default: 8 pips.
                </div>
              </div>
              <div>
                <label>Min SL (× Spread)</label>
                <input type="number" step="0.5" min="0" value={config.vwap?.min_sl_spread_mult ?? 4.0}
                  onChange={e => update('vwap', { ...config.vwap, min_sl_spread_mult: +e.target.value })} />
                <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', marginTop: 3 }}>
                  Instrument-neutral form of the floor above: SL must be at least this × the live spread, so it adapts to news-time spread blowouts. Default: 4.0×.
                </div>
              </div>
              <div>
                <label>Target R:R</label>
                <input type="number" step="0.1" min="0" value={config.vwap?.target_rr ?? 2.0}
                  onChange={e => update('vwap', { ...config.vwap, target_rr: +e.target.value })} />
                <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', marginTop: 3 }}>
                  Reward-to-risk multiple applied to the realised stop distance. Default: 2.0R.
                </div>
              </div>
              <div>
                <label>Max Trades / Day</label>
                <input type="number" step="1" min="1" value={config.vwap?.max_trades_per_day ?? 4}
                  onChange={e => update('vwap', { ...config.vwap, max_trades_per_day: +e.target.value })} />
                <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', marginTop: 3 }}>
                  Maximum signals the VWAP engine will generate per day (independent of, but also bounded by, the global Max Daily Trades circuit breaker above).
                </div>
              </div>
            </div>
          </div>

          {/* CRT */}
          <div>
            <div style={{ fontSize: '0.75rem', fontWeight: 700, color: 'var(--yellow)', marginBottom: 8 }}>CRT Strategy — Stop-Loss Floors</div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
              <div>
                <label>Min SL Pips (Hard Floor)</label>
                <input type="number" step="0.5" min="0" value={config.crt?.min_sl_pips ?? 15}
                  onChange={e => update('crt', { ...config.crt, min_sl_pips: +e.target.value })} />
                <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', marginTop: 3 }}>
                  Hard minimum SL distance in pips. Prevents tiny CRT candles from producing unrealistically tight stops. Set 0 to disable. Default: 15 pips.
                </div>
              </div>
              <div>
                <label>SL ATR Multiplier</label>
                <input type="number" step="0.1" min="0" value={config.crt?.sl_atr_mult ?? 1.0}
                  onChange={e => update('crt', { ...config.crt, sl_atr_mult: +e.target.value })} />
                <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', marginTop: 3 }}>
                  SL must be at least N × ATR(14). Whichever is larger (ATR floor or Pips floor) wins. Set 0 to disable ATR floor. Default: 1.0×.
                </div>
              </div>
              <div>
                <label>Target R-Multiple</label>
                <input type="number" step="0.1" min="0.5" value={config.crt?.target_r_multiple ?? 1.5}
                  onChange={e => update('crt', { ...config.crt, target_r_multiple: +e.target.value })} />
                <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', marginTop: 3 }}>
                  Minimum reward-to-risk ratio for CRT setups. TP is extended to maintain this R-multiple when the SL floor is applied. Default: 1.5R.
                </div>
              </div>
            </div>
          </div>

          {/* APA/SMC SL Buffer */}
          <div>
            <div style={{ fontSize: '0.75rem', fontWeight: 700, color: 'var(--green)', marginBottom: 8 }}>APA / SMC Strategy — Stop-Loss Floors</div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
              <div>
                <label>SL Buffer (× ATR)</label>
                <input type="number" step="0.05" min="0" max="2" value={config.apa?.sl_buffer_atr_mult ?? 0.5}
                  onChange={e => update('apa', { ...config.apa, sl_buffer_atr_mult: +e.target.value })} />
                <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', marginTop: 3 }}>
                  Extra cushion added beyond the structure extreme for the SL, as a multiple of ATR(14). At 0.5× a normal structural stop clears round-trip spread by roughly 1.5–2×. Default: 0.5.
                </div>
              </div>
              <div>
                <label>Min SL Pips (Hard Floor)</label>
                <input type="number" step="0.5" min="0" value={config.apa?.min_sl_pips ?? 12.0}
                  onChange={e => update('apa', { ...config.apa, min_sl_pips: +e.target.value })} />
                <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', marginTop: 3 }}>
                  Absolute minimum stop distance. APA's retest entry can sit a fraction of a pip from the shoulder wick; 12 pips is ~4× round-trip friction. Set 0 to disable. Default: 12 pips.
                </div>
              </div>
              <div>
                <label>Min SL (× ATR)</label>
                <input type="number" step="0.1" min="0" value={config.apa?.min_sl_atr_mult ?? 1.0}
                  onChange={e => update('apa', { ...config.apa, min_sl_atr_mult: +e.target.value })} />
                <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', marginTop: 3 }}>
                  Volatility-relative floor for regimes where 12 pips is itself noise. Whichever floor is larger wins. Set 0 to disable. Default: 1.0×.
                </div>
              </div>
            </div>
          </div>

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
