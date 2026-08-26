import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Terminal, Search, Pause, Play, Trash2, HardDrive, Layers,
  AlertTriangle, ChevronRight, ChevronDown,
} from 'lucide-react';
import { getLogs, getLogSessions, getLogFiles, getLogStats } from '../services/api';
import AnalyzeButton from '../components/AnalyzeButton';

/**
 * Log viewer — the frontend half of V2/V3.
 *
 * Before this, backend logs went to stderr and two files and nowhere else: the
 * only lines that reached the browser were the handful pushed explicitly by
 * bot_service.log_system_event. Everything a backtest actually logged stayed on
 * the terminal, which is the one place you cannot look when the run was
 * launched from the UI.
 *
 * Two sources, and the difference is real:
 *   buffer — the in-memory ring, live over the WebSocket. Structured: session
 *            ids, categories, formatted tracebacks.
 *   file   — logs/*.log, parsed back out of loguru's format. Goes back 30 days
 *            but is flatter (no session id, no traceback grouping).
 */

const LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'];

const LEVEL_COLOR = {
  DEBUG: 'var(--text-muted, #94a3b8)',
  INFO: 'var(--text-secondary, #cbd5e1)',
  SUCCESS: 'var(--green, #10b981)',
  WARNING: 'var(--amber, #f59e0b)',
  ERROR: 'var(--red, #ef4444)',
  CRITICAL: '#ff5470',
};

// The live tail is capped so a long session cannot grow the DOM without bound.
// The backend ring is the real buffer; this is just what is on screen.
const MAX_RENDERED = 3000;

function LogRow({ rec, expanded, onToggle }) {
  const hasDetail = Boolean(rec.exception);
  const time = (rec.time || '').replace('T', ' ').slice(0, 23);
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: '160px 74px 120px 1fr',
        gap: 8,
        padding: '2px 8px',
        borderBottom: '1px solid rgba(255,255,255,0.03)',
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
        fontSize: '0.72rem',
        alignItems: 'start',
        cursor: hasDetail ? 'pointer' : 'default',
      }}
      onClick={hasDetail ? onToggle : undefined}
    >
      <span style={{ color: 'var(--text-muted)', fontVariantNumeric: 'tabular-nums' }}>{time}</span>
      <span style={{ color: LEVEL_COLOR[rec.level] || 'var(--text-muted)', fontWeight: 600 }}>
        {rec.level}
      </span>
      <span style={{ color: 'var(--blue)', overflow: 'hidden', textOverflow: 'ellipsis' }} title={rec.module}>
        {rec.category}
      </span>
      <span style={{ color: 'var(--text-primary)', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
        {hasDetail && (expanded ? <ChevronDown size={11} style={{ display: 'inline' }} /> : <ChevronRight size={11} style={{ display: 'inline' }} />)}
        {rec.message}
        {expanded && rec.exception && (
          <pre style={{
            margin: '6px 0 4px', padding: 8, background: 'rgba(239,68,68,0.07)',
            border: '1px solid rgba(239,68,68,0.25)', borderRadius: 4,
            fontSize: '0.68rem', overflowX: 'auto', color: 'var(--red)',
          }}>{rec.exception}</pre>
        )}
      </span>
    </div>
  );
}

export default function Logs() {
  const [source, setSource] = useState('buffer');
  const [level, setLevel] = useState('');
  const [category, setCategory] = useState('');
  const [sessionId, setSessionId] = useState('');
  const [search, setSearch] = useState('');
  const [file, setFile] = useState('backend.log');
  const [live, setLive] = useState(true);
  const [streamed, setStreamed] = useState([]);
  const [expanded, setExpanded] = useState({});
  const bottomRef = useRef(null);
  const scrollRef = useRef(null);
  // Read inside the WS handler, which must not be re-subscribed on every
  // keystroke in the search box.
  const filterKeyRef = useRef('');

  const { data: sessions } = useQuery({
    queryKey: ['log-sessions'],
    queryFn: () => getLogSessions().then(r => r.data),
    refetchInterval: 20000,
  });
  const { data: files } = useQuery({
    queryKey: ['log-files'],
    queryFn: () => getLogFiles().then(r => r.data),
    enabled: source === 'file',
  });
  const { data: stats } = useQuery({
    queryKey: ['log-stats'],
    queryFn: () => getLogStats().then(r => r.data),
    refetchInterval: 15000,
  });

  const { data: fetched, refetch, isFetching } = useQuery({
    queryKey: ['logs', source, level, category, sessionId, search, file],
    queryFn: () => getLogs({
      source,
      level: level || undefined,
      category: category || undefined,
      session_id: sessionId || undefined,
      search: search || undefined,
      file: source === 'file' ? file : undefined,
      limit: 2000,
    }).then(r => r.data),
  });

  // Live tail. The backend pushes `log_batch` frames from the loguru sink; the
  // initial page load comes from the query above so the panel is never empty
  // while waiting for the next log line.
  useEffect(() => {
    if (!live || source !== 'buffer') return;
    const onMsg = (e) => {
      const m = e.detail;
      if (m?.type !== 'log_batch' || !Array.isArray(m.logs)) return;
      setStreamed(prev => {
        const tagged = m.logs.map(r => ({ ...r, _fk: filterKeyRef.current }));
        const next = prev.concat(tagged);
        return next.length > MAX_RENDERED ? next.slice(-MAX_RENDERED) : next;
      });
    };
    window.addEventListener('ws-message', onMsg);
    return () => window.removeEventListener('ws-message', onMsg);
  }, [live, source]);

  // The streamed tail belongs to whichever filter was active when it arrived.
  // Tagging it with the filter signature and reading only matching entries
  // replaces a setState-in-effect reset (which cascades an extra render) with a
  // plain derivation.
  const filterKey = `${source}|${level}|${category}|${sessionId}|${search}|${file}`;

  useEffect(() => { filterKeyRef.current = filterKey; }, [filterKey]);

  const records = useMemo(() => {
    const base = fetched?.records || [];
    if (source !== 'buffer' || !live) return base;

    // Client-side filter on the streamed tail, matching the server's semantics
    // (level is a floor, not an exact match).
    const floor = LEVELS.indexOf(level);
    const needle = search.toLowerCase();
    const extra = streamed.filter(r => {
      if (r._fk !== filterKey) return false;   // arrived under a different filter
      if (floor >= 0 && LEVELS.indexOf(r.level) < floor) return false;
      if (category && r.category !== category) return false;
      if (sessionId && r.session_id !== sessionId) return false;
      if (needle && !(r.message || '').toLowerCase().includes(needle)) return false;
      return true;
    });

    // De-dupe on seq: the initial fetch and the live stream overlap by however
    // many records landed between the two.
    const seen = new Set(base.map(r => r.seq).filter(Boolean));
    return base.concat(extra.filter(r => !r.seq || !seen.has(r.seq)));
  }, [fetched, streamed, source, live, level, category, sessionId, search, filterKey]);

  const categories = useMemo(
    () => Array.from(new Set(records.map(r => r.category).filter(Boolean))).sort(),
    [records],
  );

  const errorCount = useMemo(
    () => records.filter(r => r.level === 'ERROR' || r.level === 'CRITICAL').length,
    [records],
  );

  // Follow the tail only when already at the bottom, so scrolling up to read
  // something is not yanked back by the next incoming line.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || !live) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    if (atBottom) bottomRef.current?.scrollIntoView({ block: 'end' });
  }, [records, live]);

  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
        <h1 style={{ display: 'flex', alignItems: 'center', gap: 8, margin: 0 }}>
          <Terminal size={22} /> Logs
        </h1>
        {stats && (
          <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
            {stats.buffered?.toLocaleString?.() ?? stats.buffered} buffered
            {stats.attached === false && ' · stream not attached'}
            {stats.dropped > 0 && (
              <span style={{ color: 'var(--amber)', marginLeft: 6 }}>
                · {stats.dropped} dropped under load
              </span>
            )}
          </span>
        )}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
          {errorCount > 0 && (
            <span style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: '0.72rem', color: 'var(--red)' }}>
              <AlertTriangle size={13} /> {errorCount} error{errorCount === 1 ? '' : 's'}
            </span>
          )}
          <AnalyzeButton
            targetType="logs"
            targetId={sessionId || null}
            compact
            question="What went wrong in this session? Identify errors, their root cause, and what to fix."
          />
        </div>
      </div>

      <div className="card" style={{ padding: 12, marginBottom: 12 }}>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <div style={{ display: 'flex', gap: 2 }}>
            {['buffer', 'file'].map(s => (
              <button
                key={s}
                className={`btn btn-sm ${source === s ? 'btn-primary' : 'btn-secondary'}`}
                onClick={() => setSource(s)}
                title={s === 'buffer' ? 'Live in-memory ring — structured, with sessions and tracebacks'
                                      : 'On-disk log files — 30-day retention, flatter records'}
              >
                {s === 'buffer' ? <Layers size={12} /> : <HardDrive size={12} />} {s}
              </button>
            ))}
          </div>

          <select value={level} onChange={e => setLevel(e.target.value)} className="input input-sm" style={{ minWidth: 110 }}>
            <option value="">All levels</option>
            {LEVELS.map(l => <option key={l} value={l}>{l}+</option>)}
          </select>

          {source === 'buffer' && (
            <>
              <select value={category} onChange={e => setCategory(e.target.value)} className="input input-sm" style={{ minWidth: 130 }}>
                <option value="">All categories</option>
                {categories.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
              <select value={sessionId} onChange={e => setSessionId(e.target.value)} className="input input-sm" style={{ minWidth: 210 }}>
                <option value="">All sessions</option>
                {(sessions?.sessions || []).map(s => {
                  const errs = (s.counts?.ERROR || 0) + (s.counts?.CRITICAL || 0);
                  return (
                    <option key={s.id} value={s.id}>
                      {s.label} · {(s.started_at || '').slice(0, 19).replace('T', ' ')}
                      {errs ? ` · ${errs} err` : ''}{s.ended_at ? '' : ' · live'}
                    </option>
                  );
                })}
              </select>
            </>
          )}

          {source === 'file' && (
            <select value={file} onChange={e => setFile(e.target.value)} className="input input-sm" style={{ minWidth: 200 }}>
              {(files?.files || [{ name: 'backend.log' }]).map(f => (
                <option key={f.name} value={f.name}>
                  {f.name}{f.size_bytes ? ` (${(f.size_bytes / 1024 / 1024).toFixed(1)} MB)` : ''}
                </option>
              ))}
            </select>
          )}

          <div style={{ position: 'relative', flex: 1, minWidth: 180 }}>
            <Search size={13} style={{ position: 'absolute', left: 8, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
            <input
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search messages…"
              className="input input-sm"
              style={{ width: '100%', paddingLeft: 26 }}
            />
          </div>

          {source === 'buffer' && (
            <button className={`btn btn-sm ${live ? 'btn-green' : 'btn-secondary'}`} onClick={() => setLive(l => !l)}>
              {live ? <><Pause size={12} /> Pause</> : <><Play size={12} /> Live</>}
            </button>
          )}
          <button className="btn btn-secondary btn-sm" onClick={() => { setStreamed([]); refetch(); }}>
            <Trash2 size={12} /> Clear
          </button>
        </div>
      </div>

      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <div
          ref={scrollRef}
          style={{ maxHeight: '64vh', overflowY: 'auto', background: 'var(--bg-secondary)' }}
        >
          {records.length === 0 ? (
            <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)', fontSize: '0.82rem' }}>
              {isFetching ? 'Loading…' : 'No records match these filters.'}
            </div>
          ) : (
            records.map((r, i) => (
              <LogRow
                key={r.seq ?? `${r.ts}-${i}`}
                rec={r}
                expanded={!!expanded[r.seq ?? i]}
                onToggle={() => setExpanded(e => ({ ...e, [r.seq ?? i]: !e[r.seq ?? i] }))}
              />
            ))
          )}
          <div ref={bottomRef} />
        </div>
        <div style={{ padding: '6px 10px', borderTop: '1px solid var(--border)', fontSize: '0.68rem', color: 'var(--text-muted)', display: 'flex', gap: 10 }}>
          <span>{records.length.toLocaleString()} shown</span>
          {fetched?.truncated && <span style={{ color: 'var(--amber)' }}>tail only — narrow the filter to see more</span>}
          {source === 'buffer' && live && <span style={{ color: 'var(--green)' }}>● live</span>}
        </div>
      </div>
    </div>
  );
}
