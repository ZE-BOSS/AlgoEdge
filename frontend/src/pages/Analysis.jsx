import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Sparkles, Loader2, Trash2, Terminal, RefreshCw, Search, AlertTriangle, Clock,
} from 'lucide-react';
import {
  deleteAnalysis, getAnalysisHistory, getAnalysisProviders, getLogFile, getLogs,
  getLogSessions, runAnalysis,
} from '../services/api';
import { useConnectionStore, useAuthStore } from '../store';
import ModelPicker from '../components/ModelPicker';
import { useModelSelection } from '../hooks/useModelSelection';

/**
 * Analysis — Claude-powered review of the platform's own data, plus the log
 * viewer that feeds it.
 *
 * The two live on one page deliberately: the workflow this exists to serve is
 * "see the error, analyse it in place", and splitting them across routes would
 * put a navigation step in the middle of it.
 *
 * The API key never reaches this page. The browser calls the backend; the
 * backend calls Anthropic.
 */

const TARGETS = [
  { id: 'backtest', label: 'Backtest', hint: 'The current or a saved run' },
  { id: 'portfolio', label: 'Portfolio', hint: 'Multi-leg run, per-leg breakdown' },
  { id: 'trades', label: 'Live trades', hint: 'Closed trades from the journal' },
  { id: 'signals', label: 'Signals', hint: 'Fired vs. blocked, and which gate' },
  { id: 'logs', label: 'Logs', hint: 'A session slice, error-weighted' },
  // [Phase 13] Claude reaches every surface, not just run results. `trade` and
  // `strategy_config` take an id (a group_id / a strategy id); the last two are
  // normally launched from their own panels, which pass the fetched payload
  // through so the analysis sees exactly what is on screen.
  { id: 'trade', label: 'Single trade', hint: 'One trade plus the markings its strategy emitted' },
  { id: 'strategy_config', label: 'Strategy config', hint: 'Live parameters against the strategy spec' },
  { id: 'orderflow', label: 'Order flow', hint: 'CVD / OFI for a symbol — launch from Fundamentals' },
  { id: 'fundamentals', label: 'Fundamentals', hint: 'Options, GEX, correlation — launch from Fundamentals' },
];

const LEVELS = ['', 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'];

const LEVEL_COLOR = {
  CRITICAL: 'var(--red)',
  ERROR: 'var(--red)',
  WARNING: 'var(--yellow)',
  SUCCESS: 'var(--green)',
  INFO: 'var(--text-secondary)',
  DEBUG: 'var(--text-muted)',
};

export default function Analysis() {
  const { status } = useConnectionStore();
  const isAuth = useAuthStore((s) => s.isAuthenticated);
  const enabled = status === 'ONLINE' && isAuth;
  const qc = useQueryClient();

  const [target, setTarget] = useState('backtest');
  const [targetId, setTargetId] = useState('');
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState(null);
  // Same picker as every other Claude surface — model, thinking effort, and an
  // output ceiling that defaults to the model's real maximum.
  const selection = useModelSelection();

  const { data: providers } = useQuery({
    queryKey: ['analysis-providers'],
    queryFn: () => getAnalysisProviders().then((r) => r.data),
    enabled,
  });

  const { data: history } = useQuery({
    queryKey: ['analysis-history'],
    queryFn: () => getAnalysisHistory({ limit: 30 }).then((r) => r.data),
    enabled,
  });

  const anthropic = providers?.providers?.anthropic;

  const mutation = useMutation({
    mutationFn: () => runAnalysis({
      target_type: target,
      target_id: targetId || null,
      question: question || null,
      ...selection.requestFields(),
    }).then((r) => r.data),
    onSuccess: (data) => {
      setAnswer(data);
      qc.invalidateQueries({ queryKey: ['analysis-history'] });
    },
  });

  const del = useMutation({
    mutationFn: (id) => deleteAnalysis(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['analysis-history'] }),
  });

  return (
    <div style={{ padding: 24, maxWidth: 1500, margin: '0 auto' }}>
      <header style={{ marginBottom: 20 }}>
        <h1 style={{ fontSize: '1.35rem', margin: 0, display: 'flex', alignItems: 'center', gap: 8 }}>
          <Sparkles size={20} color="var(--green)" /> Analysis
        </h1>
        <p style={{ color: 'var(--text-secondary)', fontSize: '0.8rem', marginTop: 4 }}>
          Ask Claude about your backtests, live trades, signal flow, and logs — without
          copying anything out of the platform.
        </p>
      </header>

      {anthropic && !anthropic.configured && (
        <div className="card" style={{
          padding: 12, marginBottom: 16, borderColor: 'var(--yellow)',
          display: 'flex', gap: 10, alignItems: 'flex-start',
        }}>
          <AlertTriangle size={16} color="var(--yellow)" style={{ flexShrink: 0, marginTop: 2 }} />
          <div style={{ fontSize: '0.78rem' }}>
            <strong>No Anthropic API key configured.</strong>{' '}
            {anthropic.installed ? (
              <>
                Add one in <a href="/settings/ai" style={{ color: 'var(--blue)' }}>Settings → AI</a>
                {' '}(stored encrypted, never returned to the browser), or set{' '}
                <code>ANTHROPIC_API_KEY</code> in the backend environment and restart. Either way
                the key stays server-side — your backend calls Anthropic, this page never does.
              </>
            ) : (
              'The anthropic SDK is not installed on the backend (pip install anthropic).'
            )}
          </div>
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 360px', gap: 20, alignItems: 'start' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 20, minWidth: 0 }}>
          {/* ── Ask ── */}
          <div className="card">
            <div className="card-header"><span className="card-title">Ask</span></div>

            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 12 }}>
              {TARGETS.map((t) => (
                <button
                  key={t.id}
                  onClick={() => { setTarget(t.id); setTargetId(''); }}
                  title={t.hint}
                  className={`btn btn-sm ${target === t.id ? 'btn-primary' : 'btn-secondary'}`}
                >{t.label}</button>
              ))}
            </div>

            <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', marginBottom: 10 }}>
              {TARGETS.find((t) => t.id === target)?.hint}
            </div>

            <input
              value={targetId}
              onChange={(e) => setTargetId(e.target.value)}
              placeholder={
                target === 'logs'
                  ? 'Log session id (blank = most recent records)'
                  : 'Backtest id (blank = the run currently loaded)'
              }
              style={inputStyle}
            />

            <textarea
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="Your question — leave blank for a general review of this target."
              rows={3}
              style={{ ...inputStyle, marginTop: 8, resize: 'vertical', fontFamily: 'inherit' }}
            />

            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 10 }}>
              <button
                className="btn btn-primary"
                onClick={() => mutation.mutate()}
                disabled={mutation.isPending || !enabled}
              >
                {mutation.isPending
                  ? <><Loader2 size={14} className="spin" /> Analysing…</>
                  : <><Sparkles size={14} /> Analyse</>}
              </button>

              {/* Full model / effort / ceiling choice, replacing the old
                  binary "fast model" checkbox. */}
              <ModelPicker selection={selection} />

              {mutation.isError && (
                <span style={{ color: 'var(--red)', fontSize: '0.74rem' }}>
                  {mutation.error?.response?.data?.detail || mutation.error?.message}
                </span>
              )}
            </div>
          </div>

          {/* ── Answer ── */}
          {answer && (
            <div className="card">
              <div className="card-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span className="card-title">Result</span>
                <span style={{ fontSize: '0.65rem', color: 'var(--text-muted)', fontFamily: 'ui-monospace, monospace' }}>
                  {answer.model} · {answer.label}
                </span>
              </div>
              <Markdownish text={answer.analysis} />
            </div>
          )}

          {/* ── Logs ── */}
          <LogViewer
            enabled={enabled}
            onAnalyseSession={(sessionId) => {
              setTarget('logs');
              setTargetId(sessionId || '');
              window.scrollTo({ top: 0, behavior: 'smooth' });
            }}
          />
        </div>

        {/* ── History ── */}
        <div className="card" style={{ position: 'sticky', top: 20 }}>
          <div className="card-header"><span className="card-title">Saved analyses</span></div>
          <div style={{ maxHeight: 640, overflowY: 'auto' }}>
            {!history?.analyses?.length && (
              <div style={{ padding: 14, fontSize: '0.74rem', color: 'var(--text-muted)' }}>
                Nothing saved yet.
              </div>
            )}
            {history?.analyses?.map((a) => (
              <div key={a.id} style={{ padding: '10px 12px', borderBottom: '1px solid var(--border)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
                  <span className="badge badge-blue" style={{ fontSize: '0.6rem' }}>{a.context_type}</span>
                  <button
                    className="btn btn-sm btn-secondary"
                    onClick={() => del.mutate(a.id)}
                    title="Delete"
                  ><Trash2 size={11} /></button>
                </div>
                <button
                  onClick={() => setAnswer({ analysis: a.analysis_text, model: a.model, label: a.label || '' })}
                  style={{
                    display: 'block', width: '100%', textAlign: 'left', background: 'none',
                    border: 'none', cursor: 'pointer', color: 'var(--text-primary)',
                    padding: '6px 0 0', fontSize: '0.74rem',
                  }}
                >
                  <div style={{ color: 'var(--text-secondary)' }}>
                    {a.label || a.question || 'General review'}
                  </div>
                  <div style={{ color: 'var(--text-muted)', fontSize: '0.64rem', marginTop: 3 }}>
                    {new Date(a.created_at).toLocaleString()} · {a.model}
                  </div>
                </button>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Log viewer ───────────────────────────────────────────────────────────
function LogViewer({ enabled, onAnalyseSession }) {
  const [level, setLevel] = useState('');
  const [search, setSearch] = useState('');
  const [sessionId, setSessionId] = useState('');
  const [source, setSource] = useState('live');   // live ring buffer | file history
  const [fileName, setFileName] = useState('backend.log');
  const [live, setLive] = useState(true);
  const [streamed, setStreamed] = useState([]);

  const { data: sessions } = useQuery({
    queryKey: ['log-sessions'],
    queryFn: () => getLogSessions().then((r) => r.data),
    enabled,
    refetchInterval: 15000,
  });

  const { data, refetch, isFetching } = useQuery({
    queryKey: ['logs', source, level, search, sessionId, fileName],
    queryFn: () => (source === 'live'
      ? getLogs({ level: level || undefined, search: search || undefined, session_id: sessionId || undefined, limit: 500 })
      : getLogFile({ name: fileName, level: level || undefined, search: search || undefined, limit: 2000 })
    ).then((r) => r.data),
    enabled,
  });

  // Live tail from the WebSocket sink. Filtered client-side against the same
  // controls so the streamed tail and the fetched page agree on what is shown.
  useEffect(() => {
    if (!live || source !== 'live') return;
    const onMessage = (e) => {
      const m = e.detail;
      if (m?.type !== 'log_batch' || !Array.isArray(m.logs)) return;
      setStreamed((prev) => [...prev, ...m.logs].slice(-1000));
    };
    window.addEventListener('ws-message', onMessage);
    return () => window.removeEventListener('ws-message', onMessage);
  }, [live, source]);

  const rows = useMemo(() => {
    const base = data?.logs || [];
    if (source !== 'live' || !live) return base;
    const seen = new Set(base.map((e) => e.seq));
    const extra = streamed.filter((e) => !seen.has(e.seq));
    return [...base, ...extra].filter((e) => {
      if (level && LEVEL_RANK[e.level] < LEVEL_RANK[level]) return false;
      if (sessionId && e.session_id !== sessionId) return false;
      if (search && !String(e.message).toLowerCase().includes(search.toLowerCase())) return false;
      return true;
    }).slice(-1000);
  }, [data, streamed, live, source, level, sessionId, search]);

  return (
    <div className="card">
      <div className="card-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
        <span className="card-title">
          <Terminal size={13} style={{ display: 'inline', marginRight: 6 }} />
          Logs
        </span>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <button
            className={`btn btn-sm ${source === 'live' ? 'btn-primary' : 'btn-secondary'}`}
            onClick={() => setSource('live')}
          >Live buffer</button>
          <button
            className={`btn btn-sm ${source === 'file' ? 'btn-primary' : 'btn-secondary'}`}
            onClick={() => setSource('file')}
            title="Rotated files on disk — reaches back further than the in-process buffer"
          >History</button>
          <button className="btn btn-sm btn-secondary" onClick={() => refetch()}>
            <RefreshCw size={12} className={isFetching ? 'spin' : ''} />
          </button>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
        <select value={level} onChange={(e) => setLevel(e.target.value)} style={selectStyle}>
          {LEVELS.map((l) => <option key={l} value={l}>{l || 'All levels'}</option>)}
        </select>

        {source === 'live' ? (
          <select value={sessionId} onChange={(e) => setSessionId(e.target.value)} style={{ ...selectStyle, minWidth: 220 }}>
            <option value="">All sessions</option>
            {sessions?.sessions?.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label} {s.counts?.ERROR ? `· ${s.counts.ERROR} errors` : ''}
              </option>
            ))}
          </select>
        ) : (
          <input value={fileName} onChange={(e) => setFileName(e.target.value)} style={{ ...selectStyle, minWidth: 160 }} />
        )}

        <div style={{ position: 'relative', flex: 1, minWidth: 180 }}>
          <Search size={12} style={{ position: 'absolute', left: 8, top: 9, color: 'var(--text-muted)' }} />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search message text"
            style={{ ...inputStyle, paddingLeft: 26 }}
          />
        </div>

        {source === 'live' && (
          <label style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: '0.7rem', color: 'var(--text-secondary)' }}>
            <input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} />
            Tail
          </label>
        )}

        <button
          className="btn btn-sm btn-secondary"
          onClick={() => onAnalyseSession(sessionId)}
          title="Send this log selection to Claude"
        >
          <Sparkles size={12} /> Analyse
        </button>
      </div>

      {sessionId && sessions?.sessions && (
        <SessionSummary session={sessions.sessions.find((s) => s.id === sessionId)} />
      )}

      <div style={{
        maxHeight: 460, overflowY: 'auto', background: 'var(--bg-primary)',
        borderRadius: 'var(--radius-xs)', border: '1px solid var(--border)',
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: '0.68rem',
      }}>
        {rows.length === 0 && (
          <div style={{ padding: 16, color: 'var(--text-muted)' }}>No records match.</div>
        )}
        {rows.map((e, i) => (
          <div
            key={`${e.seq ?? i}-${i}`}
            style={{
              display: 'flex', gap: 8, padding: '3px 10px',
              borderBottom: '1px solid rgba(255,255,255,0.03)',
              background: e.level === 'ERROR' || e.level === 'CRITICAL' ? 'rgba(248,81,73,0.06)' : undefined,
            }}
          >
            <span style={{ color: 'var(--text-muted)', flexShrink: 0 }}>
              {String(e.time).slice(11, 23)}
            </span>
            <span style={{ color: LEVEL_COLOR[e.level] || 'var(--text-secondary)', width: 62, flexShrink: 0 }}>
              {e.level}
            </span>
            <span style={{ color: 'var(--purple)', width: 90, flexShrink: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {e.category}
            </span>
            <span style={{ color: 'var(--text-primary)', wordBreak: 'break-word' }}>{e.message}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function SessionSummary({ session }) {
  if (!session) return null;
  const counts = session.counts || {};
  return (
    <div style={{
      display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap',
      padding: '6px 10px', marginBottom: 8, borderRadius: 'var(--radius-xs)',
      background: 'var(--bg-tertiary)', fontSize: '0.68rem',
    }}>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, color: 'var(--text-secondary)' }}>
        <Clock size={11} /> {new Date(session.started_at).toLocaleString()}
      </span>
      {Object.entries(counts).map(([lvl, n]) => (
        <span key={lvl} style={{ color: LEVEL_COLOR[lvl] || 'var(--text-secondary)' }}>
          {lvl} {n}
        </span>
      ))}
      {!session.ended_at && <span style={{ color: 'var(--green)' }}>running</span>}
    </div>
  );
}

// ── Minimal markdown rendering ───────────────────────────────────────────
// Deliberately not a markdown library: the response is trusted-ish backend
// output but still model-generated, and pulling in a full renderer (plus a
// sanitiser to make it safe) is a lot of surface for headings, bold, lists and
// code fences. Everything here goes through React's own text escaping.
function Markdownish({ text = '' }) {
  const blocks = useMemo(() => text.split(/\n{2,}/), [text]);
  return (
    <div style={{ fontSize: '0.82rem', lineHeight: 1.65, color: 'var(--text-primary)' }}>
      {blocks.map((block, i) => {
        if (block.startsWith('```')) {
          return (
            <pre key={i} style={{
              background: 'var(--bg-primary)', padding: 10, borderRadius: 'var(--radius-xs)',
              overflowX: 'auto', fontSize: '0.72rem', border: '1px solid var(--border)',
            }}>{block.replace(/^```\w*\n?/, '').replace(/```$/, '')}</pre>
          );
        }
        const heading = block.match(/^(#{1,4})\s+(.*)$/);
        if (heading) {
          const size = ['1.05rem', '0.95rem', '0.87rem', '0.82rem'][heading[1].length - 1];
          return (
            <div key={i} style={{ fontSize: size, fontWeight: 600, margin: '16px 0 6px' }}>
              {heading[2]}
            </div>
          );
        }
        if (/^\s*[-*]\s+/m.test(block)) {
          return (
            <ul key={i} style={{ margin: '6px 0', paddingLeft: 20 }}>
              {block.split('\n').filter(Boolean).map((li, j) => (
                <li key={j} style={{ marginBottom: 3 }}>{inline(li.replace(/^\s*[-*]\s+/, ''))}</li>
              ))}
            </ul>
          );
        }
        return <p key={i} style={{ margin: '8px 0' }}>{inline(block)}</p>;
      })}
    </div>
  );
}

function inline(s) {
  // Split on **bold** and `code`, keeping the delimiters via a capture group.
  return s.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, i) => {
    if (part.startsWith('**') && part.endsWith('**')) {
      return <strong key={i}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith('`') && part.endsWith('`')) {
      return (
        <code key={i} style={{
          background: 'var(--bg-tertiary)', padding: '1px 4px', borderRadius: 3,
          fontSize: '0.9em', fontFamily: 'ui-monospace, monospace',
        }}>{part.slice(1, -1)}</code>
      );
    }
    return part;
  });
}

const LEVEL_RANK = { TRACE: 0, DEBUG: 1, INFO: 2, SUCCESS: 3, WARNING: 4, ERROR: 5, CRITICAL: 6 };

const inputStyle = {
  width: '100%', padding: '6px 9px', borderRadius: 'var(--radius-xs)',
  border: '1px solid var(--border)', background: 'var(--bg-primary)',
  color: 'var(--text-primary)', fontSize: '0.75rem',
};

const selectStyle = { ...inputStyle, width: 'auto' };
