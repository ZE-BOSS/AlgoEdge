/**
 * Kronos: the candlestick foundation model as its own feature.
 *
 * Everything heavy runs on the server as a JOB in its own low-priority
 * process (backend/kronos_service/jobs.py); this page starts, watches and
 * reads them. Nothing here blocks: jobs poll every 2 s only while one runs.
 *
 * What the research found is shown, not hidden: the gate has no value, and
 * the one candidate is SPX500 H1 traded on confident calls - a forward-test
 * candidate, not a proven edge.
 */
import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Activity, Brain, FlaskConical, Play, RefreshCw, Square, Telescope } from 'lucide-react';

import { kronos } from '../services/api';
import { qk } from '../services/servers';
import { ActivityPulse3D, PathsFan3D } from '../components/viz/scenes';

const C = {
  dim: '#8b949e', faint: '#6e7681', green: '#3fb950', red: '#f85149', amber: '#d29922',
  blue: '#58a6ff', purple: '#bc8cff', line: '#30363d',
};

function apiError(e) {
  const said = e?.response?.data?.detail;
  if (said) return typeof said === 'string' ? said : JSON.stringify(said);
  if (e?.response) return `the server returned ${e.response.status}`;
  return 'the request never reached the server';
}

function Card({ title, icon, why, children, right }) {
  return (
    <div className="card" style={{ padding: 16, marginBottom: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: why ? 4 : 10 }}>
        {icon}<b style={{ fontSize: 14 }}>{title}</b>
        <div style={{ marginLeft: 'auto' }}>{right}</div>
      </div>
      {why && <p style={{ color: C.dim, fontSize: 12, margin: '0 0 10px', lineHeight: 1.5 }}>{why}</p>}
      {children}
    </div>
  );
}

const fmt = (v, d = 2) => (v == null || !Number.isFinite(+v) ? '—' : (+v).toFixed(d));
const when = (t) => (t ? new Date(t * 1000).toISOString().slice(0, 16).replace('T', ' ') : '—');

function call(features, thr = 0.8) {
  const p = features?.p_up;
  if (p == null) return null;
  if (p >= thr - 1e-9) return { dir: 'BUY', colour: C.green };
  if (p <= 1 - thr + 1e-9) return { dir: 'SELL', colour: C.red };
  return { dir: 'no trade', colour: C.dim };
}

function JobForm({ kind, defaults, onStart, busy, label, children }) {
  const [f, setF] = useState(defaults);
  const set = (k) => (e) => setF((p) => ({ ...p, [k]: e.target.value }));
  const payload = () => ({
    kind, ...f,
    symbols: String(f.symbols).split(',').map((s) => s.trim()).filter(Boolean),
    lookback: Number(f.lookback) || 512, pred_len: Number(f.pred_len) || 12,
    samples: Number(f.samples) || 5, hold: Number(f.hold) || 10,
    start: f.start || null, end: f.end || null,
  });
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: 8, alignItems: 'end' }}>
      <label className="form-group">Symbols<input value={f.symbols} onChange={set('symbols')} /></label>
      <label className="form-group">Timeframe
        <select value={f.tf} onChange={set('tf')}>{['M15', 'H1', 'H4'].map((t) => <option key={t}>{t}</option>)}</select>
      </label>
      <label className="form-group">Model
        <select value={f.model} onChange={set('model')}>
          <option>Kronos-mini</option><option>Kronos-small</option>
        </select>
      </label>
      {'start' in defaults && <label className="form-group">From<input type="date" value={f.start} onChange={set('start')} /></label>}
      {'end' in defaults && <label className="form-group">To<input type="date" value={f.end} onChange={set('end')} /></label>}
      {children}
      <button className="btn btn-primary btn-sm" disabled={busy} onClick={() => onStart(payload())}>
        <Play size={12} /> {label}
      </button>
    </div>
  );
}

function ForecastResult({ result }) {
  return Object.entries(result || {}).map(([sym, r]) => {
    const c = call(r.features);
    return (
      <div key={sym} style={{ marginTop: 12 }}>
        <div style={{ display: 'flex', gap: 12, alignItems: 'baseline', flexWrap: 'wrap', fontSize: 12 }}>
          <b style={{ fontSize: 14 }}>{sym}</b>
          <span style={{ color: c?.colour, fontWeight: 600 }}>{c?.dir}</span>
          <span>{Math.round((r.features?.p_up ?? 0) * 100)}% of paths end higher</span>
          <span>expected {fmt(r.features?.exp_return_pct, 3)}% ({fmt(r.features?.exp_return_atr)} ATR)</span>
          <span style={{ color: C.dim }}>dispersion {fmt(r.features?.dispersion_atr)} ATR · as of {when(r.asof)} · {fmt(r.seconds, 1)}s</span>
        </div>
        <PathsFan3D history={r.history?.close || []} paths={r.paths_close || []} height={260}
                    title="last 120 bars → each sampled future (drag to rotate)" />
      </div>
    );
  });
}

function JobRow({ j, onCancel }) {
  const running = j.status === 'running' || j.status === 'queued';
  const colour = { complete: C.green, failed: C.red, cancelled: C.dim, running: C.blue }[j.status] || C.dim;
  return (
    <tr>
      <td style={{ whiteSpace: 'nowrap' }} title={j.id}>
        <b style={{ fontSize: 12 }}>{j.kind}</b>
        <div style={{ fontSize: 10, color: C.faint }}>{when(j.created_at).slice(5)}</div>
      </td>
      <td style={{ color: colour }}>{j.status}</td>
      <td style={{ minWidth: 160 }}>
        <div style={{ height: 6, background: '#21262d', borderRadius: 3 }}>
          <div style={{ height: 6, width: `${Math.min(100, j.pct || 0)}%`, borderRadius: 3,
                        background: running ? `linear-gradient(90deg, ${C.blue}, ${C.purple})` : colour,
                        transition: 'width .4s ease' }} />
        </div>
        <div style={{ fontSize: 11, color: C.dim }}>{j.error || j.stage}</div>
      </td>
      <td style={{ fontSize: 11, color: C.dim }}>{(j.params?.symbols || []).join(', ')} {j.params?.tf}</td>
      <td>{running && <button className="btn btn-sm" onClick={() => onCancel(j.id)}><Square size={11} /> Stop</button>}</td>
    </tr>
  );
}

function ResearchTable({ results }) {
  const rows = [];
  for (const r of results || []) {
    if (!String(r.file).startsWith('skill_')) continue;
    for (const [sym, s] of Object.entries(r.results || {})) {
      if (!s?.kronos) continue;
      rows.push({ sym, tf: r.args?.tf, n: s.n, k: s.kronos, kc: s.kronos_confident, mom: s.momentum,
                  long: s.long, ic: s.rank_ic, period: `${String(s.first || '').slice(0, 7)} → ${String(s.last || '').slice(0, 7)}` });
    }
  }
  if (!rows.length) return <p style={{ color: C.faint, fontSize: 12 }}>No research results on this server yet.</p>;
  const tcol = (t) => (Math.abs(t) >= 2 ? (t > 0 ? C.green : C.red) : C.dim);
  return (
    <div style={{ overflowX: 'auto' }}>
      <table className="table" style={{ fontSize: 12 }}>
        <thead><tr><th>Symbol</th><th>Period</th><th>n</th><th>Kronos net bp (t)</th><th>Confident (t)</th>
          <th>Momentum</th><th>Always long</th><th>Rank IC</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.sym + r.period}>
              <td><b>{r.sym}</b> <span style={{ color: C.dim }}>{r.tf}</span></td>
              <td style={{ color: C.dim }}>{r.period}</td><td>{r.n}</td>
              <td style={{ color: tcol(r.k?.t) }}>{fmt(r.k?.net_bps, 1)} ({fmt(r.k?.t)})</td>
              <td style={{ color: tcol(r.kc?.t) }}>{fmt(r.kc?.net_bps, 1)} ({fmt(r.kc?.t)})</td>
              <td style={{ color: tcol(r.mom?.t) }}>{fmt(r.mom?.net_bps, 1)}</td>
              <td>{fmt(r.long?.net_bps, 1)}</td><td>{fmt(r.ic, 3)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function StrategyResult({ result }) {
  return Object.entries(result || {}).map(([sym, r]) => (
    <div key={sym} style={{ fontSize: 12, marginTop: 8 }}>
      <b>{sym}</b>{r.error ? <span style={{ color: C.red }}> {r.error}</span> : (
        <table className="table" style={{ fontSize: 12, marginTop: 4 }}>
          <thead><tr><th>Leg</th><th>Trades</th><th>Mean R</th><th>t</th><th>PF</th><th>Max DD R</th></tr></thead>
          <tbody>
            {['kronos_confident', 'kronos_all', 'always_long'].map((k) => (
              <tr key={k}><td>{k.replace('_', ' ')}</td><td>{r[k]?.trades}</td>
                <td style={{ color: (r[k]?.mean_r ?? 0) > 0 ? C.green : C.red }}>{fmt(r[k]?.mean_r, 3)}</td>
                <td>{fmt(r[k]?.t)}</td><td>{fmt(r[k]?.profit_factor)}</td><td>{fmt(r[k]?.max_dd_r)}</td></tr>
            ))}
            {r.random_on_confident_bars && (
              <tr><td>random direction ({r.random_on_confident_bars.seeds}×)</td><td>—</td>
                <td>{fmt(r.random_on_confident_bars.mean_r_mean, 3)}</td>
                <td colSpan={3} style={{ color: C.dim }}>
                  best 5% {fmt(r.random_on_confident_bars.mean_r_p95, 3)}R · Kronos beats{' '}
                  {Math.round((r.random_on_confident_bars.kronos_beats_share || 0) * 100)}% of runs
                </td></tr>
            )}
          </tbody>
        </table>
      )}
    </div>
  ));
}

export default function Kronos() {
  const qc = useQueryClient();
  const [error, setError] = useState('');
  const [watch, setWatch] = useState(null);
  const { data: status, refetch } = useQuery({
    queryKey: qk('kronos-status'), queryFn: () => kronos.status().then((r) => r.data),
    refetchInterval: 15000,
  });
  const { data: jobs } = useQuery({
    queryKey: qk('kronos-jobs'), queryFn: () => kronos.jobs().then((r) => r.data.jobs),
    refetchInterval: (q) => ((q.state.data || []).some((j) => j.status === 'running') ? 2000 : 15000),
  });
  const latest = useMemo(() => {
    const out = {};
    for (const j of jobs || []) if (j.status === 'complete' && !out[j.kind]) out[j.kind] = j.id;
    return out;
  }, [jobs]);
  const running = (jobs || []).find((j) => j.status === 'running' || j.status === 'queued');
  const shown = watch || latest.forecast;
  const { data: forecastJob } = useQuery({
    queryKey: qk('kronos-job', shown), queryFn: () => kronos.job(shown).then((r) => r.data),
    enabled: Boolean(shown), refetchInterval: (q) => (q.state.data?.status === 'running' ? 2000 : false),
  });
  const { data: stratJob } = useQuery({
    queryKey: qk('kronos-job', latest.strategy), queryFn: () => kronos.job(latest.strategy).then((r) => r.data),
    enabled: Boolean(latest.strategy),
  });
  const { data: research } = useQuery({
    queryKey: qk('kronos-results'), queryFn: () => kronos.results().then((r) => r.data.results), staleTime: 60000,
  });
  const start = useMutation({
    mutationFn: (p) => kronos.start(p).then((r) => r.data),
    onSuccess: (d) => { setError(''); if (d.kind === 'forecast') setWatch(d.id); qc.invalidateQueries({ queryKey: qk('kronos-jobs') }); },
    onError: (e) => setError(apiError(e)),
  });
  const cancel = (id) => kronos.cancel(id).then(() => qc.invalidateQueries({ queryKey: qk('kronos-jobs') }));
  const inst = status?.installed;
  const ready = inst?.torch && inst?.models?.['Kronos-mini']?.weights;
  const busy = Boolean(running) || start.isPending;

  return (
    <div className="page">
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <h1 style={{ margin: 0 }}>Kronos</h1>
        <span style={{ fontSize: 12, color: ready ? C.green : C.amber }}>
          {ready ? 'installed' : 'not installed on this server'}
        </span>
        <button className="btn btn-sm" onClick={() => refetch()}><RefreshCw size={12} /></button>
      </div>
      <p style={{ color: C.dim, fontSize: 12, maxWidth: 900, lineHeight: 1.6 }}>
        A foundation model for candlesticks (MIT, AAAI 2026) that samples possible
        futures from the last {512} bars. Advisory only: it never sizes a position,
        sets a stop or bypasses the risk engine. Every run is a job in its own
        low-priority process, so it cannot take CPU from live trading.
        <b> What the research found:</b> no value as a gate on ORB, no direction skill on
        single stocks, Nasdaq 100 or gold; its one candidate is <b>SPX500 H1, traded only
        when 4 of 5 sampled paths agree</b> (+0.15R over 440 trades, t 2.77) - a
        forward-test candidate, not a proven edge.
      </p>
      {error && <p style={{ color: C.red, fontSize: 12 }}>{error}</p>}

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 2fr) minmax(260px, 1fr)', gap: 12, alignItems: 'start' }}>
        <div style={{ minWidth: 0 }}>
          <Card icon={<Telescope size={15} />} title="Forecast now"
                why="Forecasts the next 12 bars from the latest closed bar. Green paths end higher, red lower; BUY or SELL only when at least 4 of 5 agree - the rule KRONOS_v1 trades.">
            <JobForm kind="forecast" busy={busy || !ready} label="Forecast"
                     defaults={{ symbols: 'SPX500', tf: 'H1', model: 'Kronos-mini', lookback: 512, pred_len: 12, samples: 5 }}
                     onStart={(p) => start.mutate(p)} />
            {forecastJob?.status === 'running' && <p style={{ fontSize: 12, color: C.blue }}>{forecastJob.stage}…</p>}
            {forecastJob?.status === 'failed' && <p style={{ fontSize: 12, color: C.red }}>{forecastJob.error}</p>}
            <ForecastResult result={forecastJob?.result} />
          </Card>

          <Card icon={<FlaskConical size={15} />} title="Backtest KRONOS_v1"
                why="Step 1 computes Kronos forecasts walk-forward over the dates (bars before each decision only) and stores them. Step 2 trades the rule through the app's engine with real costs and swap, against random directions on the same bars. Afterwards KRONOS_v1 can also be picked in the Backtester for the same symbol, timeframe and dates.">
            <div style={{ fontSize: 12, color: C.dim, marginBottom: 6 }}>1 · Precompute forecasts (about 3 s per forecast on this box)</div>
            <JobForm kind="precompute" busy={busy || !ready} label="Precompute"
                     defaults={{ symbols: 'SPX500', tf: 'H1', model: 'Kronos-mini', start: '2025-01-01', end: '', lookback: 512, pred_len: 12, samples: 5 }}
                     onStart={(p) => start.mutate(p)} />
            <div style={{ fontSize: 12, color: C.dim, margin: '12px 0 6px' }}>2 · Trade the rule on the stored forecasts</div>
            <JobForm kind="strategy" busy={busy} label="Run strategy test"
                     defaults={{ symbols: 'SPX500', tf: 'H1', model: 'Kronos-mini', hold: 10, lookback: 512, pred_len: 12, samples: 5 }}
                     onStart={(p) => start.mutate(p)} />
            <StrategyResult result={stratJob?.result} />
          </Card>

          <Card icon={<Brain size={15} />} title="Research results"
                why="Out of sample (after Kronos' June-2024 training cutoff), FundedNext bars, net of the measured spread. Green/red t is |t| >= 2.">
            <JobForm kind="skill" busy={busy || !ready} label="Run skill test"
                     defaults={{ symbols: 'SPX500', tf: 'H1', model: 'Kronos-mini', lookback: 512, pred_len: 12, samples: 5 }}
                     onStart={(p) => start.mutate(p)} />
            <div style={{ marginTop: 10 }}><ResearchTable results={research} /></div>
          </Card>
        </div>

        <div style={{ minWidth: 0 }}>
          <Card icon={<Activity size={15} />} title={running ? 'Running' : 'Idle'}>
            <ActivityPulse3D pct={running?.pct ?? null} active={Boolean(running)}
                             label={running ? `${running.kind} · ${Math.round(running.pct || 0)}%` : 'no job running'}
                             height={200} />
            {running && (
              <div style={{ fontSize: 11, color: C.dim, maxHeight: 140, overflowY: 'auto' }}>
                {(running.events || []).slice(-8).reverse().map((e) => <div key={e.t}>{when(e.t).slice(11)} {e.text}</div>)}
              </div>
            )}
          </Card>
          <Card title="Jobs">
            <div style={{ overflowX: 'auto' }}>
              <table className="table" style={{ fontSize: 12 }}>
                <tbody>{(jobs || []).slice(0, 12).map((j) => <JobRow key={j.id} j={j} onCancel={cancel} />)}</tbody>
              </table>
            </div>
          </Card>
          <Card title="Installed">
            <div style={{ fontSize: 12, lineHeight: 1.8 }}>
              <div>torch: <b style={{ color: inst?.torch ? C.green : C.red }}>{inst?.torch ? 'yes' : 'no'}</b></div>
              {Object.entries(inst?.models || {}).map(([m, d]) => (
                <div key={m}>{m} ({d.params}, context {d.max_context}):{' '}
                  <b style={{ color: d.weights && d.tokenizer ? C.green : C.red }}>
                    {d.weights && d.tokenizer ? `ready @${d.revision}` : 'missing'}
                  </b></div>
              ))}
              <div style={{ marginTop: 6, color: C.dim }}>Stored forecasts:</div>
              {(status?.store || []).map((s) => (
                <div key={s.symbol + s.timeframe + s.model_id} style={{ color: C.dim }}>
                  {s.symbol} {s.timeframe}: {s.rows} rows
                </div>
              ))}
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
