import { useEffect, useMemo, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  FlaskConical, Play, Send, Sparkles, Loader2, BookOpen, Settings2, AlertTriangle,
  Factory, Plus, Trash2, GitBranch, CheckCircle, PackagePlus,
} from 'lucide-react';
import { getParameterSchema, runBacktest, getBacktestStatus, getLatestBacktestResult,
         listFactoryStrategies, generateStrategy, activateStrategy, deleteStrategy } from '../services/api';
import SchemaForm from '../components/SchemaForm';
import AnalyzeButton from '../components/AnalyzeButton';
import ModelPicker from '../components/ModelPicker';
import { useModelSelection } from '../hooks/useModelSelection';
import BacktestReplay from '../components/BacktestReplay';

/**
 * Strategy Lab — Phase 13 §D.1.
 *
 * Create → preview → promote, in one screen:
 *   Create   schema-driven parameter form fed by /config/parameter_schema.
 *            `core/schema_introspection.py` shipped in Phase 7 with nothing
 *            consuming it; this is the consumer it was waiting for.
 *   Preview  runs the strategy over a window and renders it on the replay
 *            chart with the strategy's own markings, so "does it fire where I
 *            expect, on the levels I expect" is answerable before committing
 *            to a full run.
 *   Promote  hands the config to the Backtester unchanged.
 *
 * Claude is available on the config itself: the analysis carries the parameters
 * AND the strategy's spec document, so "do these match the spec" is a real
 * question rather than a vibe check.
 */

const STRATEGIES = [
  { id: 'APA_v1', group: 'apa', label: 'APA (H&S inversion)' },
  { id: 'VWAP_v1', group: 'vwap', label: 'VWAP reversion' },
  { id: 'CRT_v1', group: 'crt', label: 'CRT (candle range theory)' },
  { id: 'HTFFVGFlip_v1', group: 'htf_fvg_flip', label: 'HTF FVG flip' },
  { id: 'BiasIFVG_v1', group: 'bias_ifvg', label: 'Bias + key level IFVG' },
  { id: 'NYOpenRetest_v1', group: 'ny_open_retest', label: 'NY open break & retest' },
  { id: 'DriftJumpAlpha_v1', group: 'drift_jump_alpha', label: 'Drift / jump alpha' },
];

const SYMBOLS = ['EURUSD', 'GBPUSD', 'USDJPY', 'USDCHF', 'GBPJPY', 'XAUUSD', 'XAGUSD',
                 'NAS100', 'US30', 'GER40', 'BTCUSD', 'ETHUSD'];

const PROMOTE_KEY = 'algoedge_bt_config_v2';

export default function StrategyLab() {
  const [strategyId, setStrategyId] = useState('APA_v1');
  const [symbol, setSymbol] = useState('EURUSD');
  // Default to a window long enough to be informative: a strategy taking a few
  // trades a month needs months, not the 45 days this screen used to force.
  const [endDate, setEndDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [startDate, setStartDate] = useState(
    () => new Date(Date.now() - 210 * 24 * 3600 * 1000).toISOString().slice(0, 10),
  );
  const [params, setParams] = useState({});
  const [riskParams, setRiskParams] = useState({});
  const [tab, setTab] = useState('strategy');
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState(null);
  const [promoted, setPromoted] = useState(false);
  const [progress, setProgress] = useState(null);
  const [result, setResult] = useState(null);
  const selection = useModelSelection();

  // BacktestReplay renders from props rather than subscribing itself, so the
  // page owns the socket. Same contract the Backtester uses — one replay
  // component, two hosts.
  useEffect(() => {
    const onMsg = (e) => {
      const m = e.detail;
      if (m?.type === 'backtest_progress') {
        setProgress(m);
        if (m.stage === 'complete') {
          if (m.result) setResult(m.result);
          setRunning(false);
          setTimeout(() => setProgress(null), 1500);
        }
      } else if (m?.type === 'backtest_error') {
        setProgress(null);
        setRunning(false);
        setRunError(m.message || 'Preview failed');
      }
    };
    window.addEventListener('ws-message', onMsg);
    return () => window.removeEventListener('ws-message', onMsg);
  }, []);

  const strategy = STRATEGIES.find(s => s.id === strategyId);

  const { data: schemaResp } = useQuery({
    queryKey: ['parameter-schema'],
    queryFn: () => getParameterSchema().then(r => r.data),
    staleTime: 10 * 60 * 1000,
  });
  const schema = schemaResp?.fields;

  const paramCount = useMemo(
    () => (schema || []).filter(r => r.group === strategy?.group).length,
    [schema, strategy],
  );

  /**
   * Preview = a real backtest over a short window. Deliberately not a separate
   * "signals only" code path: a preview that used different code from the real
   * run would be able to disagree with it, which is exactly the class of bug
   * the visualization plan's "one engine, not two" rule exists to prevent.
   */
  const preview = async () => {
    setRunning(true);
    setRunError(null);
    setResult(null);
    setProgress(null);
    try {
      // Window is chosen, not fixed. 45 days was hardcoded here, which is far
      // too short to judge a strategy that takes a handful of trades a month —
      // the APA corpus needed ~7 months per symbol to reach ~100 trades.
      await runBacktest({
        symbol,
        strategy_id: strategyId,
        start_date: startDate,
        end_date: endDate,
        initial_balance: 10000,
        risk_config: riskParams,
        [strategy.group]: params,
      });
    } catch (e) {
      setRunError(e?.response?.data?.detail || e.message || 'Preview failed to start');
      setRunning(false);
    }
  };

  // The run reports completion over the WebSocket; poll status as the fallback
  // for a reload mid-run.
  useQuery({
    queryKey: ['lab-status'],
    queryFn: () => getBacktestStatus().then(async (r) => {
      if (r.data?.status === 'error') setRunError(r.data?.progress?.message || 'Run failed');
      if (r.data?.status !== 'running') {
        setRunning(false);
        // A completion that arrived while the tab was backgrounded never fired
        // the WS handler, so pull the result rather than showing an empty chart.
        if (r.data?.status === 'complete' && !result) {
          try {
            const latest = await getLatestBacktestResult();
            if (latest.data && Object.keys(latest.data).length) setResult(latest.data);
          } catch { /* the chart stays on whatever it has */ }
        }
      }
      return r.data;
    }),
    enabled: running,
    refetchInterval: 3000,
  });

  const promote = () => {
    // Hand the config to the Backtester through the same localStorage key it
    // already restores from, so it arrives unmodified rather than being
    // re-entered by hand (Visualization plan §2, config portability).
    try {
      const existing = JSON.parse(localStorage.getItem(PROMOTE_KEY) || '{}');
      localStorage.setItem(PROMOTE_KEY, JSON.stringify({
        ...existing, ...riskParams,
        symbol, strategy_id: strategyId,
        [strategy.group]: { ...(existing[strategy.group] || {}), ...params },
      }));
      setPromoted(true);
      setTimeout(() => setPromoted(false), 2500);
    } catch {
      setRunError('Could not write the config to local storage — it may be full.');
    }
  };

  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
        <h1 style={{ display: 'flex', alignItems: 'center', gap: 8, margin: 0 }}>
          <FlaskConical size={22} /> Strategy Lab
        </h1>
        <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
          Create → preview → promote
        </span>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
          <ModelPicker selection={selection} />
          <AnalyzeButton
            targetType="strategy_config"
            targetId={strategyId}
            payload={{ strategy_id: strategyId, params, include_spec: true }}
            compact
            disabled={!Object.keys(params).length && !paramCount}
            question="Do these parameters match the strategy's own specification? Flag anything that contradicts the spec or is likely mis-scaled for this instrument class."
          />
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(320px, 420px) 1fr', gap: 16, alignItems: 'start' }}>
        {/* ── Config column ── */}
        <div>
          <div className="card" style={{ padding: 14, marginBottom: 12 }}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 12 }}>
              <div>
                <label style={{ fontSize: '0.7rem', color: 'var(--text-muted)', display: 'block', marginBottom: 3 }}>Strategy</label>
                <select className="input input-sm" style={{ width: '100%' }} value={strategyId}
                        onChange={e => { setStrategyId(e.target.value); setParams({}); }}>
                  {STRATEGIES.map(s => <option key={s.id} value={s.id}>{s.label}</option>)}
                </select>
              </div>
              <div>
                <label style={{ fontSize: '0.7rem', color: 'var(--text-muted)', display: 'block', marginBottom: 3 }}>Symbol</label>
                {/* Free text with suggestions, matching the Backtester. The
                    dropdown here only offered a hardcoded list, so anything not
                    on it — Volatility indices, Jump indices, Hong Kong 50 —
                    could not be previewed at all. */}
                <input
                  className="input input-sm"
                  list="lab-symbols"
                  style={{ width: '100%' }}
                  value={symbol}
                  onChange={e => setSymbol(e.target.value.toUpperCase())}
                  placeholder="Type any symbol your broker lists"
                />
                <datalist id="lab-symbols">
                  {SYMBOLS.map(s => <option key={s} value={s} />)}
                </datalist>
              </div>
            </div>

            {/* Window picker. Was hardcoded to 45 days, which is too short to
                judge a strategy taking a few trades a month. */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 8 }}>
              <div>
                <label style={{ fontSize: '0.7rem', color: 'var(--text-muted)', display: 'block', marginBottom: 3 }}>From</label>
                <input type="date" className="input input-sm" style={{ width: '100%' }}
                       value={startDate} onChange={e => setStartDate(e.target.value)} />
              </div>
              <div>
                <label style={{ fontSize: '0.7rem', color: 'var(--text-muted)', display: 'block', marginBottom: 3 }}>To</label>
                <input type="date" className="input input-sm" style={{ width: '100%' }}
                       value={endDate} onChange={e => setEndDate(e.target.value)} />
              </div>
              <div style={{ gridColumn: '1 / -1', display: 'flex', gap: 4 }}>
                {[30, 90, 210, 365].map(d => (
                  <button
                    key={d}
                    type="button"
                    className="btn btn-secondary btn-sm"
                    style={{ flex: 1, fontSize: '0.66rem' }}
                    onClick={() => {
                      const end = new Date();
                      setEndDate(end.toISOString().slice(0, 10));
                      setStartDate(new Date(end.getTime() - d * 864e5).toISOString().slice(0, 10));
                    }}
                  >
                    {d}d
                  </button>
                ))}
              </div>
            </div>

            <div style={{ display: 'flex', gap: 6, marginBottom: 12 }}>
              <button className="btn btn-primary" onClick={preview} disabled={running} style={{ flex: 1 }}>
                {running ? <><Loader2 size={14} className="spin" /> Previewing…</> : <><Play size={14} /> Run preview</>}
              </button>
              <button className="btn btn-secondary" onClick={promote} title="Send this config to the Backtester unchanged">
                <Send size={14} /> {promoted ? 'Sent' : 'Promote'}
              </button>
            </div>

            {runError && (
              <div style={{ display: 'flex', gap: 6, padding: 8, borderRadius: 6, background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.3)', color: 'var(--red)', fontSize: '0.75rem' }}>
                <AlertTriangle size={13} /> {runError}
              </div>
            )}
            {promoted && (
              <div style={{ padding: 8, borderRadius: 6, background: 'rgba(16,185,129,0.08)', color: 'var(--green)', fontSize: '0.75rem' }}>
                Config sent to the Backtester — open that page to run it in full.
              </div>
            )}
          </div>

          <div className="card" style={{ padding: 14 }}>
            <div style={{ display: 'flex', gap: 4, marginBottom: 12 }}>
              {[
                { id: 'strategy', label: strategy?.label || 'Strategy', icon: Settings2, n: paramCount },
                { id: 'risk', label: 'Risk', icon: Settings2, n: (schema || []).filter(r => r.group === 'risk').length },
                { id: 'factory', label: 'Factory', icon: Factory, n: null },
              ].map(({ id, label, icon: Icon, n }) => (
                <button key={id} className={`btn btn-sm ${tab === id ? 'btn-primary' : 'btn-secondary'}`}
                        onClick={() => setTab(id)} style={{ flex: 1 }}>
                  <Icon size={12} /> {label} {n ? <span style={{ opacity: 0.6 }}>({n})</span> : null}
                </button>
              ))}
            </div>

            {tab === 'strategy' ? (
              <SchemaForm
                schema={schema}
                group={strategy?.group}
                values={params}
                onChange={(next, replace) => setParams(replace ? next : next)}
              />
            ) : tab === 'factory' ? (
              <StrategyFactory />
            ) : (
              <SchemaForm
                schema={schema}
                group="risk"
                values={riskParams}
                onChange={(next, replace) => setRiskParams(replace ? next : next)}
              />
            )}

            <div style={{ marginTop: 12, fontSize: '0.66rem', color: 'var(--text-muted)', display: 'flex', gap: 5 }}>
              <BookOpen size={11} style={{ flexShrink: 0, marginTop: 1 }} />
              <span>
                Fields and defaults are generated from the backend dataclasses, so a parameter
                added there appears here automatically — no hardcoded mirror to drift.
              </span>
            </div>
          </div>
        </div>

        {/* ── Preview column ── */}
        <div>
          <BacktestReplay progress={progress} result={result} isRunning={running} />
          <div className="card" style={{ marginTop: 12, padding: 12, fontSize: '0.72rem', color: 'var(--text-muted)', display: 'flex', gap: 6 }}>
            <Sparkles size={13} style={{ flexShrink: 0, marginTop: 1, color: 'var(--blue)' }} />
            <span>
              The preview runs the real engine over the window you choose — not a separate
              “signals only” path. A preview that used different code from the real run
              could disagree with it, which is the one thing a preview must never do.
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}


// ───────────────────────────────────────────────────────────────────────────────
// Strategy Factory panel [Phase 14 Stream 3]
// ───────────────────────────────────────────────────────────────────────────────

const STATUS_COLOR = { active: 'var(--green)', generated: 'var(--amber)', default: 'var(--text-muted)' };

function StrategyFactory() {
  const qc = useQueryClient();
  const [form, setForm] = useState({ strategy_id: '', display_name: '', description: '', timeframes: 'H1,M15' });
  const [activating, setActivating] = useState(null);
  const [activateResult, setActivateResult] = useState(null);
  const [genError, setGenError] = useState(null);
  const [genOk, setGenOk] = useState(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ['factory-strategies'],
    queryFn: () => listFactoryStrategies().then(r => r.data),
    staleTime: 10000,
  });

  const genMut = useMutation({
    mutationFn: (d) => generateStrategy(d).then(r => r.data),
    onSuccess: (res) => {
      setGenOk(`Scaffold created at ${res.path}`);
      setGenError(null);
      setForm({ strategy_id: '', display_name: '', description: '', timeframes: 'H1,M15' });
      qc.invalidateQueries(['factory-strategies']);
    },
    onError: (e) => setGenError(e?.response?.data?.detail || e.message || 'Generate failed'),
  });

  const handleGenerate = () => {
    setGenError(null); setGenOk(null);
    if (!form.strategy_id.trim()) { setGenError('strategy_id is required'); return; }
    genMut.mutate({
      ...form,
      timeframes: form.timeframes.split(',').map(t => t.trim()).filter(Boolean),
    });
  };

  const handleActivate = async (sid) => {
    setActivating(sid);
    setActivateResult(null);
    try {
      const res = await activateStrategy(sid);
      setActivateResult({ ok: true, msg: `Committed on ${res.data.branch}${res.data.pr_url ? ` — PR: ${res.data.pr_url}` : ''}` });
      qc.invalidateQueries(['factory-strategies']);
    } catch (e) {
      setActivateResult({ ok: false, msg: e?.response?.data?.detail || e.message || 'Activate failed' });
    } finally {
      setActivating(null);
    }
  };

  const handleDelete = async (sid) => {
    if (!window.confirm(`Delete scaffold for '${sid}'? This cannot be undone.`)) return;
    try {
      await deleteStrategy(sid);
      qc.invalidateQueries(['factory-strategies']);
    } catch (e) {
      alert(e?.response?.data?.detail || 'Delete failed');
    }
  };

  return (
    <div style={{ fontSize: '0.8rem' }}>
      <div style={{ fontSize: '0.7rem', textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--text-muted)', marginBottom: 10 }}>
        Registered + generated strategies
      </div>

      {isLoading && <div style={{ color: 'var(--text-muted)' }}>Loading…</div>}
      {error && <div style={{ color: 'var(--red)' }}>{error.message}</div>}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 16 }}>
        {(data?.strategies || []).map(s => (
          <div key={s.strategy_id} style={{
            padding: '8px 10px', borderRadius: 6, background: 'var(--bg-tertiary)',
            display: 'flex', alignItems: 'center', gap: 8,
          }}>
            <span style={{ width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
              background: STATUS_COLOR[s.status] || STATUS_COLOR.default }} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontWeight: 600, fontSize: '0.78rem' }}>{s.strategy_id}</div>
              <div style={{ color: 'var(--text-muted)', fontSize: '0.68rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {s.description || s.scaffold_path || ''}
              </div>
            </div>
            <span style={{ fontSize: '0.62rem', padding: '2px 6px', borderRadius: 10,
              background: 'rgba(255,255,255,0.06)', color: STATUS_COLOR[s.status] || 'inherit' }}>
              {s.status}
            </span>
            {s.status === 'generated' && (
              <>
                <button
                  className="btn btn-sm btn-primary"
                  disabled={activating === s.strategy_id}
                  onClick={() => handleActivate(s.strategy_id)}
                  title="Commit to dev branch and open GitHub PR"
                >
                  {activating === s.strategy_id
                    ? <Loader2 size={11} className="spin" />
                    : <GitBranch size={11} />} Activate
                </button>
                <button
                  className="btn btn-sm btn-secondary"
                  onClick={() => handleDelete(s.strategy_id)}
                  title="Delete scaffold"
                >
                  <Trash2 size={11} />
                </button>
              </>
            )}
          </div>
        ))}
      </div>

      {activateResult && (
        <div style={{
          marginBottom: 12, padding: '6px 10px', borderRadius: 6, fontSize: '0.74rem',
          background: activateResult.ok ? 'rgba(16,185,129,0.08)' : 'rgba(239,68,68,0.08)',
          border: `1px solid ${activateResult.ok ? 'rgba(16,185,129,0.3)' : 'rgba(239,68,68,0.3)'}`,
          color: activateResult.ok ? 'var(--green)' : 'var(--red)',
        }}>
          {activateResult.ok ? <CheckCircle size={12} style={{ marginRight: 4 }} /> : <AlertTriangle size={12} style={{ marginRight: 4 }} />}
          {activateResult.msg}
        </div>
      )}

      {/* Generate form */}
      <div style={{ fontSize: '0.7rem', textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--text-muted)', marginBottom: 8 }}>
        <PackagePlus size={11} style={{ marginRight: 4 }} /> Generate new scaffold
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {[['strategy_id', 'ID (e.g. MyAlpha_v1)'], ['display_name', 'Display name'], ['description', 'Description (optional)'], ['timeframes', 'Timeframes (comma-separated)']
        ].map(([k, ph]) => (
          <input
            key={k} className="input input-sm"
            placeholder={ph}
            value={form[k]}
            onChange={e => setForm(f => ({ ...f, [k]: e.target.value }))}
          />
        ))}
        <button className="btn btn-primary" onClick={handleGenerate} disabled={genMut.isPending}>
          {genMut.isPending ? <Loader2 size={13} className="spin" /> : <Plus size={13} />} Generate scaffold
        </button>
        {genError && <div style={{ color: 'var(--red)', fontSize: '0.72rem' }}>{genError}</div>}
        {genOk   && <div style={{ color: 'var(--green)', fontSize: '0.72rem' }}>{genOk}</div>}
      </div>
    </div>
  );
}
