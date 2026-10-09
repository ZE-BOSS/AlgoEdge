/**
 * frontend/src/pages/Agents.jsx
 *
 * The Agents page: create an agent, train it, read what the gate said, read the
 * strategy it says it follows, and approve or reject that strategy.
 *
 * THE ONE THING THIS SCREEN IS FOR
 * The admin approving a charter is the only way an agent ever reaches a live
 * slot. So the Charter tab shows the thesis, every gate with its measured hit
 * rate and mean R, and the validation report that earned it — and Approve is
 * disabled until there IS a report. The server refuses it too; the disabled
 * button is the explanation, not the enforcement.
 *
 * WHAT IS DELIBERATELY NOT CLICKABLE
 *  - Approve, with no validation report. You would be signing off an intention.
 *  - Approve, on a DRAFT. A charter has to be backtestable-and-labelled-
 *    unapproved first, so there is a run you could have looked at.
 *  - Reject, with no reason. The reason is what blocks the same thing coming
 *    back, and "no" without one is unusable six months later.
 *  - Re-enabling a suspended agent is a button and not automatic: an agent that
 *    drifted and came back is still an agent whose charter no longer describes
 *    it.
 */

import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle, Ban, Check, ChevronRight, Copy, FlaskConical, Loader2,
  Play, RefreshCw, ShieldCheck, Upload, X,
} from 'lucide-react';
import { rl } from '../services/api';
import { qk } from '../services/servers';

const TABS = ['Overview', 'Charter', 'Validation', 'Proposals', 'Adherence'];

const STATUS_COLOUR = {
  DRAFT: '#8b949e', TRAINING: '#58a6ff', TRAINED: '#d29922',
  VALIDATED: '#3fb950', APPROVED: '#3fb950', REJECTED: '#f85149',
};

const CHARTER_COLOUR = {
  DRAFT: '#8b949e', PROPOSED: '#d29922', APPROVED: '#3fb950',
  SUPERSEDED: '#6e7681', REJECTED: '#f85149',
};

function Pill({ children, colour }) {
  return (
    <span style={{
      background: `${colour}22`, color: colour, border: `1px solid ${colour}66`,
      borderRadius: 999, padding: '1px 8px', fontSize: 11, fontWeight: 600,
      whiteSpace: 'nowrap',
    }}>{children}</span>
  );
}

function Num({ value, digits = 4, suffix = '', signed = true }) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return <span style={{ color: '#6e7681' }}>—</span>;
  }
  const n = Number(value);
  const colour = n > 0 ? '#3fb950' : n < 0 ? '#f85149' : '#8b949e';
  const text = `${signed && n > 0 ? '+' : ''}${n.toFixed(digits)}${suffix}`;
  return <span style={{ color: signed ? colour : 'inherit' }}>{text}</span>;
}

/** One check from the validation gate. */
function CheckRow({ check }) {
  const colour = check.skipped ? '#8b949e' : check.passed ? '#3fb950' : '#f85149';
  const label = check.skipped ? 'SKIP' : check.passed ? 'PASS' : 'FAIL';
  return (
    <tr>
      <td style={{ width: 54 }}><Pill colour={colour}>{label}</Pill></td>
      <td style={{ fontWeight: 600 }}>{check.name}</td>
      <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
        <Num value={check.value} />
      </td>
      <td style={{ textAlign: 'right', color: '#8b949e', fontVariantNumeric: 'tabular-nums' }}>
        {check.bar === null || check.bar === undefined ? '' : Number(check.bar).toFixed(2)}
      </td>
      <td style={{ color: '#8b949e', fontSize: 12 }}>{check.detail}</td>
    </tr>
  );
}

/** Create: the pre-registration, frozen before any training happens. */
function CreatePanel({ features, onCreated }) {
  const [name, setName] = useState('');
  const [symbols, setSymbols] = useState('');
  const [timeframe, setTimeframe] = useState('M5');
  const [exitHead, setExitHead] = useState(true);
  const [seeds, setSeeds] = useState('1,2,3,4,5');
  const [chosen, setChosen] = useState([]);
  const [error, setError] = useState('');

  const groups = useMemo(() => {
    const out = {};
    (features || []).forEach((f) => { (out[f.group] = out[f.group] || []).push(f); });
    return out;
  }, [features]);

  const create = useMutation({
    mutationFn: () => rl.create({
      name: name.trim(),
      symbols: symbols.split(',').map((s) => s.trim()).filter(Boolean),
      attempted_symbols: symbols.split(',').map((s) => s.trim()).filter(Boolean),
      timeframe,
      features: chosen.length ? chosen : null,
      seeds: seeds.split(',').map((s) => parseInt(s.trim(), 10)).filter((n) => !Number.isNaN(n)),
      exit_head: exitHead,
    }),
    onSuccess: (r) => { setError(''); onCreated(r.data); },
    onError: (e) => setError(e?.response?.data?.detail || String(e)),
  });

  return (
    <div className="card" style={{ padding: 16 }}>
      <h3 style={{ marginTop: 0 }}>New agent</h3>
      <p style={{ color: '#8b949e', fontSize: 12, marginTop: -6 }}>
        This is a pre-registration. The symbol list is frozen and content-hashed
        before training starts, and the report names every symbol attempted —
        including the ones that turn out badly. Reporting only the ones that
        worked is survivorship bias in the experiment, which no leak probe catches.
      </p>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <label>Name
          <input value={name} onChange={(e) => setName(e.target.value)}
                 placeholder="crash-drift" />
        </label>
        <label>Timeframe
          <select value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>
            {['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'].map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </label>
        <label style={{ gridColumn: '1 / -1' }}>Symbols (comma separated)
          {/* free text, never a select: Deriv's "US Tech 100" is in no
              canonical instrument list */}
          <input value={symbols} onChange={(e) => setSymbols(e.target.value)}
                 placeholder="Crash 1000 Index, Boom 1000 Index" />
        </label>
        <label>Seeds
          <input value={seeds} onChange={(e) => setSeeds(e.target.value)} />
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 20 }}>
          <input type="checkbox" checked={exitHead}
                 onChange={(e) => setExitHead(e.target.checked)} />
          Exit head (can close a running trade early)
        </label>
      </div>

      <details style={{ marginTop: 12 }}>
        <summary style={{ cursor: 'pointer' }}>
          Features — {chosen.length ? `${chosen.length} chosen` : 'the default set'}
        </summary>
        <p style={{ color: '#8b949e', fontSize: 12 }}>
          Every one of these is prefix-stable: its value at a bar does not change
          when later bars arrive. None of them carries a balance, a lot size, a
          stop distance or a risk percentage — the agent has no access to money
          management because there is nowhere to put it.
        </p>
        {Object.entries(groups).map(([group, list]) => (
          <div key={group} style={{ marginBottom: 8 }}>
            <div style={{ fontWeight: 600, fontSize: 12, color: '#8b949e' }}>{group}</div>
            {list.map((f) => (
              <label key={f.name} style={{ display: 'block', fontSize: 12, paddingLeft: 8 }}>
                <input
                  type="checkbox"
                  checked={chosen.includes(f.name)}
                  onChange={(e) => setChosen(e.target.checked
                    ? [...chosen, f.name]
                    : chosen.filter((n) => n !== f.name))}
                /> <code>{f.name}</code> — {f.plain}
              </label>
            ))}
          </div>
        ))}
      </details>

      {error && <div style={{ color: '#f85149', fontSize: 12, marginTop: 8 }}>{error}</div>}
      <button className="btn btn-primary" style={{ marginTop: 12 }}
              disabled={!name.trim() || !symbols.trim() || create.isPending}
              onClick={() => create.mutate()}>
        {create.isPending ? <Loader2 size={14} className="spin" /> : <FlaskConical size={14} />}
        {' '}Pre-register
      </button>
    </div>
  );
}

/** Datasets, training, and the run that follows. */
function TrainPanel({ agent, onDone }) {
  const [files, setFiles] = useState({});      // symbol -> upload result
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const symbols = agent?.spec?.symbols || [];

  const datasets = useMemo(() => {
    const out = {};
    Object.entries(files).forEach(([sym, info]) => {
      out[sym] = info === 'mt5' ? 'mt5' : { path: info.path, timeframe: info.timeframe };
    });
    return out;
  }, [files]);

  const ready = symbols.length > 0 && symbols.every((s) => files[s]);

  const upload = async (symbol, file) => {
    setBusy(symbol); setError('');
    try {
      const r = await rl.upload(file, { symbol, timeframe: agent.spec.timeframe });
      setFiles((f) => ({ ...f, [symbol]: r.data }));
    } catch (e) {
      setError(e?.response?.data?.detail || String(e));
    } finally { setBusy(''); }
  };

  const start = useMutation({
    mutationFn: () => rl.train(agent.agent_id, { datasets }),
    onSuccess: onDone,
    onError: (e) => setError(e?.response?.data?.detail || String(e)),
  });

  return (
    <div className="card" style={{ padding: 16 }}>
      <h3 style={{ marginTop: 0 }}>Data</h3>
      <p style={{ color: '#8b949e', fontSize: 12, marginTop: -6 }}>
        Every pre-registered symbol needs a source, including the ones you expect
        to fail. A file is validated rather than accepted: if its intervals do not
        match its timeframe, or its highs do not contain its closes, it is refused
        with the reason — resampling it quietly would move every feature by an
        amount nothing downstream can detect.
      </p>
      <table className="table" style={{ fontSize: 12 }}>
        <tbody>
          {symbols.map((s) => (
            <tr key={s}>
              <td style={{ fontWeight: 600 }}>{s}</td>
              <td>
                {files[s] === 'mt5' ? <Pill colour="#58a6ff">live MT5</Pill>
                  : files[s] ? (
                    <span style={{ color: '#8b949e' }}>
                      {files[s].rows.toLocaleString()} bars,{' '}
                      {String(files[s].start_iso).slice(0, 10)} to{' '}
                      {String(files[s].end_iso).slice(0, 10)}
                      {files[s].gaps ? `, ${files[s].gaps} gaps` : ''}
                    </span>
                  ) : <span style={{ color: '#6e7681' }}>no source</span>}
              </td>
              <td style={{ textAlign: 'right' }}>
                <button className="btn btn-sm"
                        onClick={() => setFiles((f) => ({ ...f, [s]: 'mt5' }))}>
                  Use MT5
                </button>{' '}
                <label className="btn btn-sm" style={{ cursor: 'pointer' }}>
                  {busy === s ? <Loader2 size={12} className="spin" /> : <Upload size={12} />}
                  {' '}Upload
                  <input type="file" hidden accept=".csv,.tsv,.json,.npz,.parquet"
                         onChange={(e) => e.target.files?.[0] && upload(s, e.target.files[0])} />
                </label>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {(files && Object.values(files).some((f) => f !== 'mt5' && f?.notes?.length > 0)) && (
        <ul style={{ color: '#d29922', fontSize: 12 }}>
          {Object.entries(files).flatMap(([s, f]) => (f === 'mt5' ? [] : (f.notes || [])
            .map((n) => <li key={`${s}-${n}`}>{s}: {n}</li>)))}
        </ul>
      )}
      {error && <div style={{ color: '#f85149', fontSize: 12 }}>{error}</div>}
      <button className="btn btn-primary" disabled={!ready || start.isPending}
              onClick={() => start.mutate()}>
        <Play size={14} /> Train
      </button>
      <span style={{ color: '#8b949e', fontSize: 11, marginLeft: 8 }}>
        Runs in a separate process behind a queue — never on the API, which this
        project has already stalled once by doing heavy work on the event loop.
      </span>
    </div>
  );
}

function ProgressBar({ progress }) {
  const pct = Math.max(0, Math.min(100, Number(progress?.pct || 0)));
  if (!progress || !Object.keys(progress).length) return null;
  const eta = progress.eta_s ? `${Math.round(progress.eta_s / 60)} min left` : '';
  return (
    <div style={{ margin: '8px 0' }}>
      <div style={{ height: 6, background: '#21262d', borderRadius: 3, overflow: 'hidden' }}>
        <div style={{ width: `${pct}%`, height: '100%', background: '#58a6ff' }} />
      </div>
      <div style={{ color: '#8b949e', fontSize: 11, marginTop: 4 }}>
        {progress.stage} {pct.toFixed(1)}%
        {progress.seed !== undefined && ` · seed ${progress.seed}`}
        {progress.fold !== undefined && ` · path ${progress.fold}`}
        {progress.iteration ? ` · iteration ${progress.iteration}/${progress.iterations}` : ''}
        {progress.optimiser ? ` · ${progress.optimiser}` : ''}
        {eta ? ` · ${eta}` : ''}
      </div>
    </div>
  );
}

function CharterTab({ agent, onChanged }) {
  const [reason, setReason] = useState('');
  const [error, setError] = useState('');
  const charters = agent.charters || [];
  const current = charters.find((c) => c.status === 'APPROVED')
    || charters.find((c) => c.status === 'PROPOSED')
    || charters[0];
  const hasReport = Boolean(agent.validation);

  const act = (fn) => async () => {
    setError('');
    try { await fn(); onChanged(); } catch (e) {
      setError(e?.response?.data?.detail || String(e));
    }
  };

  if (!current) {
    return (
      <p style={{ color: '#8b949e' }}>
        No charter yet. Train the agent, then distil one — the charter is the
        strategy it says it follows, and it is what the 0.30 adherence term is
        scored against.
      </p>
    );
  }

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <h3 style={{ margin: 0 }}>v{current.version}</h3>
        <Pill colour={CHARTER_COLOUR[current.status] || '#8b949e'}>{current.status}</Pill>
        <code style={{ color: '#6e7681', fontSize: 11 }}>{current.content_hash}</code>
      </div>

      {current.status === 'PROPOSED' && (
        <div style={{
          background: '#d2992222', border: '1px solid #d2992266', borderRadius: 6,
          padding: 10, fontSize: 12, marginBottom: 10,
        }}>
          <AlertTriangle size={13} /> Unapproved. This charter can be backtested
          and cannot reach a live slot.
        </div>
      )}

      <p style={{ lineHeight: 1.6 }}>{current.thesis}</p>

      <h4>Gates</h4>
      <table className="table" style={{ fontSize: 12 }}>
        <thead>
          <tr><th>Gate</th><th>Condition</th><th style={{ textAlign: 'right' }}>Fires</th>
            <th style={{ textAlign: 'right' }}>Mean R</th></tr>
        </thead>
        <tbody>
          {(current.gates || []).map((g) => (
            <tr key={g.name}>
              <td><code>{g.name}</code></td>
              <td style={{ color: '#8b949e' }}>
                {g.feature} {g.op} {Array.isArray(g.threshold)
                  ? g.threshold.map((x) => Number(x).toFixed(2)).join(' … ')
                  : Number(g.threshold).toFixed(3)}
                {g.direction !== 'EITHER' ? ` (${g.direction} only)` : ''}
              </td>
              <td style={{ textAlign: 'right' }}>
                {g.hit_rate === null || g.hit_rate === undefined ? '—'
                  : `${(g.hit_rate * 100).toFixed(0)}%`}
              </td>
              <td style={{ textAlign: 'right' }}><Num value={g.mean_r} digits={3} /></td>
            </tr>
          ))}
        </tbody>
      </table>

      <h4>Setups</h4>
      <ul style={{ fontSize: 12, color: '#8b949e' }}>
        {(current.tiers || []).map((t) => (
          <li key={t.name}>
            <b>{t.name}</b>: {t.min_gates} of {t.gates.length}
            {t.required?.length ? `, always ${t.required.join(', ')}` : ''}
            {current.entry_tier === t.name ? ' — the entry tier' : ''}
          </li>
        ))}
      </ul>
      <p style={{ fontSize: 12, color: '#8b949e' }}>
        Exit: {current.exit_rule || '—'}<br />
        Adherence floor {current.adherence_floor} — below it for{' '}
        {current.drift_trades} consecutive trades and the agent is suspended from
        live and you are emailed, with the gates it has been skipping named.
      </p>

      {!hasReport && (
        <div style={{ color: '#d29922', fontSize: 12, marginBottom: 8 }}>
          <AlertTriangle size={13} /> No validation report yet. Approve is
          disabled until the gate has run — otherwise you would be signing off an
          intention rather than a measurement.
        </div>
      )}

      {error && <div style={{ color: '#f85149', fontSize: 12 }}>{error}</div>}

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 8 }}>
        {current.status === 'DRAFT' && (
          <button className="btn" onClick={act(() => rl.propose(agent.agent_id))}>
            <ChevronRight size={14} /> Propose (makes it backtestable)
          </button>
        )}
        {current.status === 'PROPOSED' && (
          <button className="btn btn-primary" disabled={!hasReport}
                  onClick={act(() => rl.approveCharter(agent.agent_id))}>
            <ShieldCheck size={14} /> Approve
          </button>
        )}
        {['DRAFT', 'PROPOSED'].includes(current.status) && (
          <>
            <input value={reason} onChange={(e) => setReason(e.target.value)}
                   placeholder="reason (required to reject)" style={{ flex: 1 }} />
            <button className="btn" disabled={!reason.trim()}
                    onClick={act(() => rl.rejectCharter(agent.agent_id, { reason }))}>
              <X size={14} /> Reject
            </button>
          </>
        )}
      </div>

      {charters.length > 1 && (
        <details style={{ marginTop: 12 }}>
          <summary style={{ cursor: 'pointer' }}>Earlier versions ({charters.length - 1})</summary>
          <p style={{ color: '#8b949e', fontSize: 12 }}>
            Kept rather than overwritten, so every historical run can still be
            reproduced against the charter it actually ran under.
          </p>
          {charters.filter((c) => c !== current).map((c) => (
            <div key={c.version} style={{ fontSize: 12, marginBottom: 6 }}>
              <Pill colour={CHARTER_COLOUR[c.status] || '#8b949e'}>{c.status}</Pill>{' '}
              v{c.version} <code style={{ color: '#6e7681' }}>{c.content_hash}</code>
              {c.rejection_reason ? ` — ${c.rejection_reason}` : ''}
            </div>
          ))}
        </details>
      )}
    </div>
  );
}

function ValidationTab({ agent }) {
  const v = agent.validation;
  if (!v) {
    return <p style={{ color: '#8b949e' }}>The gate has not run yet.</p>;
  }
  return (
    <div>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 10 }}>
        <Pill colour={v.passed ? '#3fb950' : '#f85149'}>
          {v.passed ? 'PASSED' : 'REJECTED'}
        </Pill>
        <span style={{ fontSize: 12, color: '#8b949e' }}>
          {v.trades} trades · <Num value={v.expectancy_r} />R ·{' '}
          spec <code>{v.spec_hash}</code> · {v.optimiser}
        </span>
      </div>
      {!v.passed && (
        <p style={{ fontSize: 12, color: '#8b949e' }}>
          Rejections are kept. The record of what did not work is worth more than
          another candidate, and it stops the same idea being retried as new.
        </p>
      )}
      <table className="table" style={{ fontSize: 12 }}>
        <thead>
          <tr><th /><th>Check</th><th style={{ textAlign: 'right' }}>Measured</th>
            <th style={{ textAlign: 'right' }}>Bar</th><th>Detail</th></tr>
        </thead>
        <tbody>
          {(v.checks || []).map((c) => <CheckRow key={c.name} check={c} />)}
        </tbody>
      </table>
      {(v.symbols_attempted || []).length > 0 && (
        <p style={{ fontSize: 12, color: '#8b949e' }}>
          Symbols attempted: {v.symbols_attempted.join(', ')} — named whether or
          not they worked, because picking the ones that looked good afterwards is
          survivorship bias in the experiment.
        </p>
      )}
    </div>
  );
}

function ProposalsTab({ agent, onChanged }) {
  const [reason, setReason] = useState({});
  const [error, setError] = useState('');
  const open = (agent.proposals || []).filter((p) => p.status === 'OPEN');
  const decided = (agent.proposals || []).filter((p) => p.status !== 'OPEN');

  const act = (fn) => async () => {
    setError('');
    try { await fn(); onChanged(); } catch (e) {
      setError(e?.response?.data?.detail || String(e));
    }
  };

  return (
    <div>
      <p style={{ color: '#8b949e', fontSize: 12 }}>
        The agent cannot edit an approved charter. When continued training finds
        something the charter does not express, it proposes a change — and a
        proposal must already beat the approved charter on the same gate, the same
        seeds and the same folds before it can even be submitted. You should never
        be asked to approve something that has not earned it. While a proposal is
        open, live keeps trading the approved charter.
      </p>
      {error && <div style={{ color: '#f85149', fontSize: 12 }}>{error}</div>}
      {!open.length && <p style={{ color: '#6e7681' }}>No open proposals.</p>}
      {open.map((p) => (
        <div key={p.id} className="card" style={{ padding: 12, marginBottom: 10 }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <Pill colour="#d29922">{p.kind}</Pill>
            <span style={{ fontSize: 12, color: '#8b949e' }}>
              from v{p.from_version} · <code>{p.diff_hash}</code>
            </span>
          </div>
          <p style={{ fontSize: 13 }}>{p.why}</p>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, fontSize: 12 }}>
            <div>
              <b>Approved</b>
              <div>expectancy <Num value={p.evidence_approved?.expectancy_r} /></div>
              <div>PBO {p.evidence_approved?.pbo ?? '—'}</div>
              <div>deflated Sharpe <Num value={p.evidence_approved?.deflated_sharpe} digits={3} /></div>
            </div>
            <div>
              <b>Proposed</b>
              <div>expectancy <Num value={p.evidence_proposed?.expectancy_r} /></div>
              <div>PBO {p.evidence_proposed?.pbo ?? '—'}</div>
              <div>deflated Sharpe <Num value={p.evidence_proposed?.deflated_sharpe} digits={3} /></div>
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
            <button className="btn btn-primary" onClick={act(() => rl.approveProposal(p.id))}>
              <Check size={14} /> Approve (bumps to v{p.from_version + 1})
            </button>
            <input value={reason[p.id] || ''} style={{ flex: 1 }}
                   onChange={(e) => setReason({ ...reason, [p.id]: e.target.value })}
                   placeholder="reason (required, and it blocks this exact change coming back)" />
            <button className="btn" disabled={!(reason[p.id] || '').trim()}
                    onClick={act(() => rl.rejectProposal(p.id, { reason: reason[p.id] }))}>
              <X size={14} /> Reject
            </button>
          </div>
        </div>
      ))}
      {decided.length > 0 && (
        <details>
          <summary style={{ cursor: 'pointer' }}>Decided ({decided.length})</summary>
          {decided.map((p) => (
            <div key={p.id} style={{ fontSize: 12, marginTop: 6 }}>
              <Pill colour={p.status === 'ACCEPTED' ? '#3fb950' : '#8b949e'}>{p.status}</Pill>{' '}
              {p.kind} from v{p.from_version}
              {p.reason ? ` — ${p.reason}` : ''}
              {p.decided_by ? ` (${p.decided_by})` : ''}
            </div>
          ))}
        </details>
      )}
    </div>
  );
}

function AdherenceTab({ agent }) {
  const { data } = useQuery({
    queryKey: qk('rl-adherence', agent.agent_id),
    queryFn: () => rl.adherence(agent.agent_id, 180).then((r) => r.data),
  });
  const points = data?.points || [];
  const floor = data?.floor ?? 0.6;

  return (
    <div>
      <p style={{ color: '#8b949e', fontSize: 12 }}>
        One point per closed trade, scored against the <b>approved</b> charter —
        never against whatever the policy currently prefers. Per trade rather than
        per bar, because adherence credited per bar would make &ldquo;never
        trade&rdquo; the highest-adherence policy and this chart would never move.
      </p>
      {data?.suspended && (
        <div style={{
          background: '#f8514922', border: '1px solid #f8514966', borderRadius: 6,
          padding: 10, fontSize: 12, marginBottom: 10,
        }}>
          <Ban size={13} /> Suspended from live. {data.suspended_reason}
          <div style={{ marginTop: 6 }}>
            It does not fall back to trading unsupervised — the slot stops taking
            its signals.
          </div>
        </div>
      )}
      {!points.length && <p style={{ color: '#6e7681' }}>No live trades recorded yet.</p>}
      {points.length > 0 && (
        <svg viewBox={`0 0 ${Math.max(200, points.length * 4)} 100`}
             style={{ width: '100%', height: 140, background: '#0d1117', borderRadius: 6 }}>
          <line x1="0" x2={points.length * 4} y1={100 - floor * 100} y2={100 - floor * 100}
                stroke="#d29922" strokeDasharray="3 3" strokeWidth="1" />
          {points.map((p, i) => (
            <circle key={i} cx={i * 4 + 2} cy={100 - (p.adherence || 0) * 100} r="1.6"
                    fill={p.tripped ? '#f85149' : p.adherence >= floor ? '#3fb950' : '#d29922'} />
          ))}
        </svg>
      )}
    </div>
  );
}

export default function Agents() {
  const qc = useQueryClient();
  // Null means "whichever is first", resolved below. Stored as null rather
  // than written by an effect: setting state inside an effect to pick a default
  // cascades a render for something that is a pure derivation of the list.
  const [picked, setPicked] = useState(null);
  const [tab, setTab] = useState('Overview');
  const [notice, setNotice] = useState('');

  const { data: list, refetch } = useQuery({
    queryKey: qk('rl-agents'),
    queryFn: () => rl.list().then((r) => r.data),
    refetchInterval: 15000,
  });
  const { data: feats } = useQuery({
    queryKey: qk('rl-features'),
    queryFn: () => rl.features().then((r) => r.data),
    staleTime: 600000,
  });
  const { data: leak } = useQuery({
    queryKey: qk('rl-leakcheck'),
    queryFn: () => rl.leakcheck().then((r) => r.data),
    staleTime: 600000,
  });
  const selected = picked || list?.agents?.[0]?.agent_id || null;
  const { data: agent, refetch: refetchAgent } = useQuery({
    queryKey: qk('rl-agent', selected),
    queryFn: () => rl.get(selected).then((r) => r.data),
    enabled: Boolean(selected),
    refetchInterval: (q) => (q.state.data?.status === 'TRAINING' ? 3000 : false),
  });

  const refreshAll = () => { refetch(); refetchAgent(); qc.invalidateQueries(); };

  const step = async (fn, label) => {
    setNotice(`${label}…`);
    try {
      const r = await fn();
      setNotice(r?.data?.note || `${label} done`);
      refreshAll();
    } catch (e) {
      setNotice(e?.response?.data?.detail || String(e));
    }
  };

  return (
    <div className="page">
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <h1 style={{ margin: 0 }}>Agents</h1>
        {leak && (
          <Pill colour={leak.ok ? '#3fb950' : '#f85149'}>
            leak probe {leak.ok ? `clean · ${leak.features} features` : 'FAILING'}
          </Pill>
        )}
        <button className="btn btn-sm" onClick={refreshAll}><RefreshCw size={12} /></button>
      </div>
      <p style={{ color: '#8b949e', fontSize: 12, maxWidth: 820 }}>
        An agent proposes a direction and, if it has an exit head, can close or
        tighten a running trade. It never sizes a position, never sets a stop and
        never overrides a halt — those numbers are not in its observation at all.
        Nothing reaches a live slot until you have approved the strategy it says
        it follows.
      </p>

      <div style={{ display: 'grid', gridTemplateColumns: '280px 1fr', gap: 16 }}>
        <div>
          <div className="card" style={{ padding: 8, marginBottom: 12 }}>
            {(list?.agents || []).map((a) => (
              <button key={a.agent_id} onClick={() => setPicked(a.agent_id)}
                      style={{
                        display: 'block', width: '100%', textAlign: 'left', padding: 8,
                        background: a.agent_id === selected ? '#21262d' : 'transparent',
                        border: 0, borderRadius: 6, cursor: 'pointer', color: 'inherit',
                      }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 6 }}>
                  <b style={{ fontSize: 13 }}>{a.name}</b>
                  <Pill colour={STATUS_COLOUR[a.status] || '#8b949e'}>{a.status}</Pill>
                </div>
                <div style={{ fontSize: 11, color: '#8b949e' }}>
                  {a.strategy_id || a.agent_id}
                  {a.suspended ? ' · suspended' : ''}
                  {a.open_proposals ? ` · ${a.open_proposals} proposal` : ''}
                </div>
              </button>
            ))}
            {!list?.agents?.length && (
              <p style={{ color: '#6e7681', fontSize: 12, padding: 8 }}>No agents yet.</p>
            )}
          </div>
          <CreatePanel features={feats?.features} onCreated={(a) => {
            setPicked(a.agent_id); refetch();
          }} />
        </div>

        <div>
          {!agent && <p style={{ color: '#6e7681' }}>Select an agent.</p>}
          {agent && (
            <>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <h2 style={{ margin: 0 }}>{agent.name}</h2>
                <Pill colour={STATUS_COLOUR[agent.status] || '#8b949e'}>{agent.status}</Pill>
                {agent.suspended && <Pill colour="#f85149">suspended</Pill>}
                <code style={{ color: '#6e7681', fontSize: 11 }}>{agent.spec_hash}</code>
                <button className="btn btn-sm" onClick={() => step(
                  () => rl.clone(agent.agent_id, { symbols: agent.spec.symbols }),
                  'Cloning')}>
                  <Copy size={12} /> Clone
                </button>
                {agent.suspended && (
                  <button className="btn btn-sm"
                          onClick={() => step(() => rl.unsuspend(agent.agent_id), 'Re-enabling')}>
                    Re-enable
                  </button>
                )}
              </div>
              <ProgressBar progress={agent.progress} />
              {notice && <div style={{ fontSize: 12, color: '#8b949e' }}>{notice}</div>}

              <div style={{ display: 'flex', gap: 6, margin: '10px 0' }}>
                {TABS.map((t) => (
                  <button key={t} className={`btn btn-sm ${tab === t ? 'btn-primary' : ''}`}
                          onClick={() => setTab(t)}>{t}</button>
                ))}
              </div>

              {tab === 'Overview' && (
                <>
                  <TrainPanel agent={agent} onDone={refreshAll} />
                  <div className="card" style={{ padding: 16, marginTop: 12 }}>
                    <h3 style={{ marginTop: 0 }}>After training</h3>
                    <p style={{ color: '#8b949e', fontSize: 12, marginTop: -6 }}>
                      Three steps, in order. Collect stores the policy; distil turns
                      it into the charter you will read; validate runs the gate.
                      Approval comes last and it is yours.
                    </p>
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                      <button className="btn" onClick={() => step(
                        () => rl.collect(agent.agent_id), 'Collecting')}>1 · Collect</button>
                      <button className="btn" onClick={() => step(
                        () => rl.distil(agent.agent_id, { datasets: {} }), 'Distilling')}>
                        2 · Distil charter
                      </button>
                      <button className="btn" onClick={() => step(
                        () => rl.validate(agent.agent_id, { datasets: {} }), 'Validating')}>
                        3 · Run the gate
                      </button>
                      {agent.status === 'TRAINING' && (
                        <button className="btn" onClick={() => step(
                          () => rl.cancel(agent.agent_id), 'Cancelling')}>Cancel training</button>
                      )}
                    </div>
                  </div>
                  {agent.trained_on?.length > 0 && (
                    <div className="card" style={{ padding: 16, marginTop: 12 }}>
                      <h3 style={{ marginTop: 0 }}>Trained on</h3>
                      <p style={{ color: '#8b949e', fontSize: 12, marginTop: -6 }}>
                        What the Backtester checks a window against. A run entirely
                        inside this is disabled, not warned about.
                      </p>
                      <ul style={{ fontSize: 12, color: '#8b949e' }}>
                        {agent.trained_on.map((c, i) => (
                          <li key={i}>
                            {c.symbol} {c.timeframe}:{' '}
                            {new Date(c.start * 1000).toISOString().slice(0, 10)} to{' '}
                            {new Date(c.end * 1000).toISOString().slice(0, 10)}{' '}
                            <code>{c.hash}</code>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </>
              )}
              {tab === 'Charter' && <CharterTab agent={agent} onChanged={refreshAll} />}
              {tab === 'Validation' && <ValidationTab agent={agent} />}
              {tab === 'Proposals' && <ProposalsTab agent={agent} onChanged={refreshAll} />}
              {tab === 'Adherence' && <AdherenceTab agent={agent} />}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
