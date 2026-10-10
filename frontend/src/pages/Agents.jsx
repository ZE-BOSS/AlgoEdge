/**
 * frontend/src/pages/Agents.jsx
 *
 * The Agents page: build an agent, feed it data, watch it train, read the
 * strategy it says it follows, and approve or reject that strategy.
 *
 * WHAT THIS SCREEN HAD TO FIX
 * The first version put every control on one page and explained none of them.
 * You could not tell when training was happening, how far along it was, what
 * data was going in, which strategy a mode-B agent was built on, or whether
 * any of it was supervised learning or reinforcement learning. Every button was
 * clickable at every moment, including during a 30-minute run.
 *
 * So the page is now built around a LIFECYCLE RAIL. There are seven steps, they
 * happen in order, exactly one is next, and the rail says which. Each step says
 * what it does before you press it, and a step that cannot run yet is disabled
 * with the reason on it rather than failing on the server.
 *
 * WHERE THE WORDS COME FROM
 * The plain-English description of an agent - the "it does not look for its own
 * entries, ORB_v1 finds the setups" paragraph - is served by the backend, both
 * for a saved agent (`learning` on the detail response) and for a form that has
 * not been submitted yet (`POST /api/rl/describe`). It is deliberately not
 * written twice: the client copy would drift the first time the backend's rules
 * changed, and nobody would notice, because wording has no tests.
 *
 * WHAT IS DELIBERATELY NOT CLICKABLE
 *  - Anything, while the agent is TRAINING. A second run would race the first
 *    over the same progress file and the same policy row.
 *  - A lifecycle step whose predecessor has not finished.
 *  - Approve, with no validation report, or on a DRAFT charter. You would be
 *    signing off an intention rather than a measurement.
 *  - Reject, with no reason. The reason is what stops the same idea coming back
 *    as new, and "no" without one is unusable six months later.
 *  - Re-enabling a suspended agent. An agent that drifted and came back is
 *    still an agent whose charter no longer describes it.
 */

import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Activity, AlertTriangle, Ban, Brain, Check, ChevronRight, Copy, Database,
  FlaskConical, GraduationCap, Info, Loader2, Play, Plus, RefreshCw,
  ShieldCheck, Target, Trash2, Upload, X,
} from 'lucide-react';
import { rl } from '../services/api';
import { qk } from '../services/servers';
import { useBaseStrategies } from '../hooks/useAgents';

const C = {
  dim: '#8b949e', faint: '#6e7681', blue: '#58a6ff', green: '#3fb950',
  amber: '#d29922', red: '#f85149', line: '#30363d', sunk: '#0d1117',
  raised: '#21262d', purple: '#bc8cff',
};

const TABS = ['Overview', 'Data', 'Training', 'Charter', 'Validation',
  'Proposals', 'Live'];

const STATUS_COLOUR = {
  DRAFT: C.dim, TRAINING: C.blue, TRAINED: C.amber,
  VALIDATED: C.green, APPROVED: C.green, REJECTED: C.red,
};

const CHARTER_COLOUR = {
  DRAFT: C.dim, PROPOSED: C.amber, APPROVED: C.green,
  SUPERSEDED: C.faint, REJECTED: C.red,
};

const TIMEFRAMES = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'];

// index.css sets `input { width: 100% }`, which makes a checkbox 367px wide
// and shoves its own label to the far side of the row.
const CHECKBOX = { width: 'auto', flex: '0 0 auto', margin: 0 };

// ── small shared pieces ────────────────────────────────────────────────────

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
    return <span style={{ color: C.faint }}>—</span>;
  }
  const n = Number(value);
  const colour = n > 0 ? C.green : n < 0 ? C.red : C.dim;
  const text = `${signed && n > 0 ? '+' : ''}${n.toFixed(digits)}${suffix}`;
  return <span style={{ color: signed ? colour : 'inherit' }}>{text}</span>;
}

/** A titled card with the reason it exists written under the title. */
function Section({ title, why, icon, children, right }) {
  return (
    <div className="card" style={{ padding: 16, marginBottom: 12 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <h3 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: 6 }}>
          {icon}{title}
        </h3>
        <div style={{ marginLeft: 'auto' }}>{right}</div>
      </div>
      {why && (
        <p style={{ color: C.dim, fontSize: 12, margin: '6px 0 12px', lineHeight: 1.55 }}>
          {why}
        </p>
      )}
      {children}
    </div>
  );
}

/** A labelled control with the hint that stops you having to guess. */
function Field({ label, hint, children, span, indent }) {
  return (
    <label style={{
      display: 'block', gridColumn: span ? '1 / -1' : undefined,
      marginLeft: indent ? 24 : undefined, minWidth: 0,
      // index.css uppercases every label and spaces its letters out. Right
      // for a one-word field name, unreadable for the sentence of
      // explanation under it - which is the entire point of this screen.
      textTransform: 'none', letterSpacing: 'normal',
    }}>
      <div style={{
        fontSize: 11, fontWeight: 600, marginBottom: 3, color: C.dim,
        textTransform: 'uppercase', letterSpacing: '0.04em',
      }}>{label}</div>
      {children}
      {hint && (
        <div style={{
          color: C.faint, fontSize: 11.5, marginTop: 4, lineHeight: 1.55,
          fontWeight: 400, textTransform: 'none',
        }}>{hint}</div>
      )}
    </label>
  );
}

/** A radio card: the choice, and what choosing it means. */
function Choice({ checked, onChange, title, children, colour = C.blue }) {
  return (
    <button type="button" onClick={onChange} style={{
      display: 'block', textAlign: 'left', width: '100%', cursor: 'pointer',
      background: checked ? `${colour}18` : 'transparent',
      border: `1px solid ${checked ? `${colour}88` : C.line}`,
      borderRadius: 8, padding: 10, color: 'inherit', marginBottom: 8,
      whiteSpace: 'normal',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontWeight: 600, fontSize: 13 }}>
        <span style={{
          width: 12, height: 12, borderRadius: 999, flexShrink: 0,
          border: `1px solid ${checked ? colour : C.dim}`,
          background: checked ? colour : 'transparent',
        }} />
        {title}
      </div>
      <div style={{ color: C.dim, fontSize: 11.5, marginTop: 4, lineHeight: 1.55, paddingLeft: 20 }}>
        {children}
      </div>
    </button>
  );
}

function KV({ k, children }) {
  return (
    <div style={{ display: 'flex', gap: 8, fontSize: 12, padding: '2px 0' }}>
      <span style={{ color: C.dim, minWidth: 148 }}>{k}</span>
      <span style={{ fontVariantNumeric: 'tabular-nums' }}>{children}</span>
    </div>
  );
}

/** One check from the validation gate. */
function CheckRow({ check }) {
  const colour = check.skipped ? C.dim : check.passed ? C.green : C.red;
  const label = check.skipped ? 'SKIP' : check.passed ? 'PASS' : 'FAIL';
  return (
    <tr>
      <td style={{ width: 54 }}><Pill colour={colour}>{label}</Pill></td>
      <td style={{ fontWeight: 600 }}>{check.name}</td>
      <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
        <Num value={check.value} />
      </td>
      <td style={{ textAlign: 'right', color: C.dim, fontVariantNumeric: 'tabular-nums' }}>
        {check.bar === null || check.bar === undefined ? '' : Number(check.bar).toFixed(2)}
      </td>
      <td style={{ color: C.dim, fontSize: 12 }}>{check.detail}</td>
    </tr>
  );
}

/** What an agent is, in the backend's words. Never re-worded here. */
function LearningSummary({ learning, compact }) {
  if (!learning?.sentences?.length) return null;
  return (
    <div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
        <Pill colour={C.purple}>mode {learning.mode} · {learning.mode_label}</Pill>
        <Pill colour={C.blue}>reinforcement</Pill>
        {learning.supervised
          ? <Pill colour={C.green}>+ supervised director</Pill>
          : <Pill colour={C.faint}>no supervised model</Pill>}
      </div>
      <ul style={{
        margin: 0, paddingLeft: 18, fontSize: compact ? 12 : 12.5, lineHeight: 1.65,
      }}>
        {learning.sentences.map((s, i) => <li key={i} style={{ marginBottom: 3 }}>{s}</li>)}
      </ul>
      <div style={{
        marginTop: 10, padding: 8, borderRadius: 6, fontSize: 11.5,
        background: `${C.red}11`, border: `1px solid ${C.red}44`, color: C.dim,
      }}>
        <b style={{ color: C.red }}>It can never:</b> {(learning.never || []).join(' · ')}.
        {' '}None of these is in its observation, so there is nowhere for it to
        learn them even if the reward pushed it that way.
      </div>
    </div>
  );
}

// ── the lifecycle: seven steps, in order, exactly one of them next ─────────

/**
 * Where this agent is, derived rather than stored.
 *
 * Derived from the record on every render because the alternative - a status
 * column that is written at each step - goes stale the moment a step is run
 * twice or a run dies halfway, and then the screen confidently shows a stage
 * the database does not actually hold.
 */
function lifecycle(agent) {
  const charters = agent.charters || [];
  const approved = charters.find((c) => c.status === 'APPROVED');
  // Liveness, not the status column. The column says TRAINING until Collect
  // writes it back, so a run that died left the rail announcing "Train is
  // running now" directly above a banner saying nothing was running.
  const training = agent.status === 'TRAINING' && Boolean(agent.run?.looks_alive);
  const failed = Boolean(agent.run?.failed);
  // A run cannot start without a source for every symbol, so a run in flight
  // IS the proof that step 2 happened - `trained_on` is only written when the
  // run finishes, and until then the rail was telling the admin to go and
  // attach data while the data was being read in front of them.
  const hasData = (agent.trained_on || []).length > 0 || training
    || (agent.progress?.datasets || []).length > 0;
  const trained = Boolean(agent.training?.best);
  const collected = Boolean(agent.policy_hash);
  const hasCharter = charters.length > 0;
  const validated = Boolean(agent.validation);

  const steps = [
    {
      key: 'register', label: 'Pre-register', done: true,
      what: 'Done when the agent was created. The symbol list, the features and '
        + 'the reward weights are frozen and content-hashed from here on, so the '
        + 'report cannot quietly become a report about different symbols.',
    },
    {
      key: 'data', label: 'Attach data', done: hasData || trained,
      what: 'One source per pre-registered symbol - an upload or live MT5. A file '
        + 'is validated, not accepted: wrong intervals for its timeframe, or highs '
        + 'that do not contain their closes, and it is refused with the reason.',
    },
    {
      key: 'train', label: 'Train', done: trained, busy: training, failed,
      what: 'Every seed against every purged CPCV path, in a separate process. '
        + 'The seeds are the experiment: if they disagree by more than 1.0 Sharpe '
        + 'the gate rejects the whole run later.',
    },
    {
      key: 'collect', label: 'Collect', done: collected,
      what: 'Stores the best fold\'s policy and its scaler as this agent. Until '
        + 'you press it the run is a result on disk and not an agent.',
    },
    {
      key: 'charter', label: 'Distil charter', done: hasCharter,
      what: 'Turns the trained weights into readable rules - gates, tiers, an exit '
        + 'rule - with the measured hit rate and mean R beside each one. This is '
        + 'the thing you approve; the weights are not reviewable by a human.',
    },
    {
      key: 'gate', label: 'Run the gate', done: validated,
      what: 'Sample size, seed dispersion, CPCV paths, PBO, deflated Sharpe, a '
        + 'regime split, cost stress, the leak probe, adherence and charter '
        + 'fidelity. It rejects far more often than it passes, by design.',
    },
    {
      key: 'approve', label: 'Approve', done: Boolean(approved),
      what: 'Yours alone, and the only way an agent ever reaches a live slot.',
    },
  ];
  // While a run is in flight the current step is the run, not the next thing
  // that has not happened yet.
  const firstUndone = steps.findIndex((s) => !s.done);
  const currentIdx = training ? steps.findIndex((s) => s.key === 'train') : firstUndone;
  return {
    steps, currentIdx, training, failed, trained, collected, hasCharter,
    validated, hasData, approved,
  };
}

function LifecycleRail({ life, onJump }) {
  const { steps, currentIdx } = life;
  return (
    <div className="card" style={{ padding: 12, marginBottom: 12 }}>
      <div style={{ display: 'flex', gap: 4, alignItems: 'stretch' }}>
        {steps.map((s, i) => {
          const state = s.busy ? 'busy'
            : s.failed ? 'failed'
              : s.done ? 'done'
                : i === currentIdx ? 'next' : 'locked';
          const colour = {
            done: C.green, busy: C.blue, next: C.amber, locked: C.faint, failed: C.red,
          }[state];
          return (
            <button key={s.key} type="button" onClick={() => onJump?.(s.key)}
                    title={s.what}
                    style={{
                      flex: 1, textAlign: 'left', cursor: 'pointer', color: 'inherit',
                      background: state === 'locked' ? 'transparent' : `${colour}14`,
                      border: `1px solid ${state === 'locked' ? C.line : `${colour}66`}`,
                      borderRadius: 6, padding: '6px 8px', minWidth: 0,
                    }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                <span style={{
                  width: 16, height: 16, borderRadius: 999, flexShrink: 0, fontSize: 10,
                  display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                  background: state === 'done' ? colour : 'transparent',
                  border: `1px solid ${colour}`,
                  color: state === 'done' ? '#0d1117' : colour, fontWeight: 700,
                }}>
                  {state === 'done' ? <Check size={10} strokeWidth={4} />
                    : state === 'busy' ? <Loader2 size={10} className="spin" />
                      : state === 'failed' ? '!' : i + 1}
                </span>
                <span style={{
                  fontSize: 11.5, fontWeight: 600, color: state === 'locked' ? C.faint : colour,
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}>{s.label}</span>
              </div>
            </button>
          );
        })}
      </div>
      <div style={{ fontSize: 12, color: C.dim, marginTop: 8, lineHeight: 1.55 }}>
        {currentIdx === -1 ? (
          <><Check size={12} color={C.green} /> <b style={{ color: C.green }}>Approved.</b>{' '}
            This agent is registered as a strategy and can be put in a live slot.
            It is scored against its charter on every closed trade from here on.</>
        ) : life.failed ? (
          <><AlertTriangle size={12} color={C.red} /> <b style={{ color: C.red }}>
            Step {currentIdx + 1}, {steps[currentIdx].label}, started and
            failed.</b>{' '} The reason is in the banner above and in that
            phase&rsquo;s event log on the Training tab.</>
        ) : life.training ? (
          <><Loader2 size={12} className="spin" /> <b style={{ color: C.blue }}>
            Step {currentIdx + 1}, {steps[currentIdx].label}, is running now.</b>{' '}
            {steps[currentIdx].what} Nothing else can be done until it finishes
            or you cancel it.</>
        ) : (
          <><b style={{ color: C.amber }}>Next — step {currentIdx + 1},
            {' '}{steps[currentIdx].label}:</b> {steps[currentIdx].what}</>
        )}
      </div>
    </div>
  );
}

// ── training: the thing that was previously invisible ──────────────────────

/**
 * What the run is doing right now, not merely how far through it is.
 *
 * The percentage alone was the whole of the old display, and a percentage
 * cannot tell you that seed 4 has produced 11 trades where seed 1 produced
 * 180 - which is the single most useful thing to know while it is running,
 * because it means the result is already dead and the remaining 20 minutes
 * are wasted.
 */
function TrainingMonitor({ agent, busy, onCancel }) {
  const p = agent.progress || {};
  if (!Object.keys(p).length) return null;
  // A run that FINISHED is not a run that died, and telling them apart needs
  // the phase's own stage - not the agent's status.
  //
  // The agent stays at status TRAINING until Collect runs, because Collect is
  // what stores the result. So every successful run looked exactly like a
  // crashed one from here: status TRAINING, nothing writing any more, and a
  // banner reading "Training stopped - which is exactly what a restart looks
  // like". Reported 2026-10-10 by someone whose run had completed all 50 jobs
  // perfectly well.
  const stage = String(p.stage || '');
  const finished = ['complete', 'scored', 'failed', 'cancelled'].includes(stage);
  const dead = agent.status === 'TRAINING' && !agent.run?.looks_alive && !finished;
  // Finished, but the result has not been stored yet - the normal state
  // between Train and Collect, and the one that needs an instruction.
  const uncollected = finished && agent.status === 'TRAINING';
  const pct = Math.max(0, Math.min(100, Number(p.pct || 0)));
  const events = [...(p.events || [])].reverse();
  const done = p.jobs_done ?? 0;
  const total = p.total_jobs ?? 0;

  return (
    <Section
      icon={<Loader2 size={15} className={busy ? 'spin' : undefined} />}
      title={busy ? 'Training now'
        : dead ? 'Training stopped'
          : uncollected ? 'Training finished - not collected yet'
            : 'Last training run'}
      why={busy
        ? 'Running in a separate process behind a queue, never on the API - this '
          + 'project has already stalled its own backend once by doing heavy work '
          + 'on the event loop. Every control on this page is disabled until it '
          + 'finishes, because a second run would race this one over the same '
          + 'progress file and the same policy row.'
        : 'Kept after the run so the numbers are still here when you come back.'}
      right={busy && (
        <button className="btn btn-sm" onClick={onCancel}>
          <X size={12} /> Cancel
        </button>
      )}
    >
      <div style={{
        height: 8, background: C.raised, borderRadius: 4, overflow: 'hidden',
      }}>
        <div style={{
          width: `${pct}%`, height: '100%',
          background: busy ? C.blue : pct >= 100 ? C.green : C.amber,
          transition: 'width .4s',
        }} />
      </div>
      <div style={{ fontSize: 12, color: C.dim, marginTop: 6 }}>
        <b style={{ color: busy ? C.blue : dead ? C.amber : C.dim }}>
          {dead ? 'stopped' : stage === 'complete' ? 'finished' : stage || '—'}
        </b> · {pct.toFixed(1)}%
        {total ? ` · job ${done} of ${total}` : ''}
        {busy && p.eta_s ? ` · about ${Math.round(p.eta_s / 60)} min left` : ''}
        {p.age_s !== undefined && (
          <span style={{ color: dead ? C.amber : C.faint }}>
            {' · last wrote '}
            {p.age_s < 90 ? `${Math.round(p.age_s)}s` : `${Math.round(p.age_s / 60)} min`}
            {' ago'}
          </span>
        )}
      </div>
      {dead && (
        <p style={{ fontSize: 11.5, color: C.amber, marginTop: 6, lineHeight: 1.55 }}>
          Nothing has written to this run&rsquo;s progress file for{' '}
          {Math.round((p.age_s || 0) / 60)} minutes, and this backend does not own
          the job &mdash; which is exactly what a restart looks like from in here.
          These numbers are where it got to, not where it is.
        </p>
      )}
      {uncollected && (
        <p style={{ fontSize: 11.5, color: C.dim, marginTop: 6, lineHeight: 1.55 }}>
          All {total || '—'} jobs ran and the run ended normally. The agent still
          says TRAINING because <b>Collect</b> is what stores the result &mdash;
          that is step 2 below, and nothing is lost until you run it.
          {p.no_trades && (
            <b style={{ color: C.amber }}>
              {' '}There is nothing worth collecting from this one, though: no
              fold took a trade. Clone the agent with more iterations instead.
            </b>
          )}
        </p>
      )}

      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(118px, 1fr))',
        gap: 8, margin: '12px 0',
      }}>
        {[
          ['optimiser', p.optimiser],
          ['symbol', p.symbol],
          ['seed', p.seed === undefined ? null : `${p.seed}`],
          ['CPCV path', p.fold === undefined ? null : `${p.fold}`],
          ['iteration', p.iteration ? `${p.iteration} / ${p.iterations}` : null],
          ['decisions in batch', p.decisions ?? null],
          ['policy entropy', p.entropy ?? null],
          ['trades last fold', p.trades ?? null],
          ['mean R last fold', p.mean_r === undefined ? null : p.mean_r],
          ['best score', p.best_so_far ?? p.best ?? null],
          ['seed dispersion', p.dispersion ?? null],
        ].filter(([, v]) => v !== null && v !== undefined && v !== '').map(([k, v]) => (
          <div key={k} style={{
            background: C.sunk, border: `1px solid ${C.line}`, borderRadius: 6, padding: '6px 8px',
          }}>
            <div style={{ fontSize: 10, color: C.faint, textTransform: 'uppercase' }}>{k}</div>
            <div style={{ fontSize: 13, fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>
              {typeof v === 'number' ? (Number.isInteger(v) ? v : v.toFixed(4)) : v}
            </div>
          </div>
        ))}
      </div>

      {p.entropy !== undefined && busy && (
        <p style={{ fontSize: 11, color: C.faint, marginTop: -4 }}>
          Entropy falling towards zero means the policy has stopped exploring and
          is committing to one answer. Falling in the first few iterations is the
          failure mode worth watching: it has usually committed to &ldquo;never
          trade&rdquo;, which scores well on adherence and earns nothing.
        </p>
      )}

      {events.length > 0 && (
        <details open={busy}>
          <summary style={{ cursor: 'pointer', fontSize: 12, fontWeight: 600 }}>
            Event log ({events.length}) — newest first
          </summary>
          <div style={{
            maxHeight: 240, overflowY: 'auto', background: C.sunk, borderRadius: 6,
            border: `1px solid ${C.line}`, padding: 8, marginTop: 6,
            fontFamily: 'ui-monospace, monospace', fontSize: 11, lineHeight: 1.7,
          }}>
            {events.map((e, i) => (
              <div key={i} style={{ display: 'flex', gap: 8 }}>
                <span style={{ color: C.faint, flexShrink: 0 }}>
                  {String(e.at || '').slice(11, 19)}
                </span>
                <span>{e.text}</span>
              </div>
            ))}
          </div>
        </details>
      )}
    </Section>
  );
}

/**
 * Every phase of the pipeline at once: what has run, what is running, and
 * what each one said while it ran.
 *
 * The four phases report into four separate files for one reason: the
 * training worker is a child process and the gate runs in the API, so a
 * single shared file would have two processes writing it and a crash
 * mid-write could take the training record with it. The server merges them
 * on read; this renders the merge.
 */
function PhaseTimeline({ pipeline, phase }) {
  const st = pipeline?.phases?.[phase.key] || {};
  const running = pipeline?.running === phase.key;
  const started = Boolean(st.stage);
  const terminal = ['complete', 'scored'].includes(st.stage);
  const failed = st.stage === 'failed';
  const colour = failed ? C.red : running ? C.blue : terminal ? C.green : C.faint;
  const pct = Math.max(0, Math.min(100, Number(st.pct || 0)));
  const events = [...(st.events || [])].reverse();

  return (
    <div style={{
      border: `1px solid ${started ? `${colour}55` : C.line}`, borderRadius: 8,
      padding: 12, marginBottom: 10,
      background: running ? `${C.blue}0d` : 'transparent',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{
          width: 18, height: 18, borderRadius: 999, flexShrink: 0, fontSize: 10,
          display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
          background: terminal ? colour : 'transparent', border: `1px solid ${colour}`,
          color: terminal ? '#0d1117' : colour, fontWeight: 700,
        }}>
          {terminal ? <Check size={11} strokeWidth={4} />
            : running ? <Loader2 size={11} className="spin" />
              : failed ? '!' : phase.n}
        </span>
        <b style={{ fontSize: 13, color: started ? 'inherit' : C.faint }}>{phase.label}</b>
        {running && <Pill colour={C.blue}>running</Pill>}
        {terminal && <Pill colour={C.green}>done</Pill>}
        {failed && <Pill colour={C.red}>failed</Pill>}
        {!started && <Pill colour={C.faint}>not run yet</Pill>}
        {st.age_s !== undefined && (
          <span style={{
            marginLeft: 'auto', fontSize: 11, color: st.stale ? C.amber : C.faint,
          }}>
            {st.stale ? 'stopped writing ' : 'last wrote '}
            {st.age_s < 90 ? `${Math.round(st.age_s)}s` : `${Math.round(st.age_s / 60)} min`}
            {' ago'}
          </span>
        )}
      </div>

      <div style={{ color: C.dim, fontSize: 11.5, margin: '4px 0 0 26px', lineHeight: 1.55 }}>
        {phase.what}
      </div>

      {started && (
        <div style={{ marginLeft: 26, marginTop: 8 }}>
          <div style={{ height: 5, background: C.raised, borderRadius: 3, overflow: 'hidden' }}>
            <div style={{
              width: `${terminal ? 100 : pct}%`, height: '100%', background: colour,
              transition: 'width .4s',
            }} />
          </div>
          <div style={{ fontSize: 11.5, color: C.dim, marginTop: 4 }}>
            <b style={{ color: colour }}>{st.stage}</b>
            {st.stage_detail ? ` \u2014 ${st.stage_detail}` : ''}
            {!terminal && ` \u00b7 ${pct.toFixed(0)}%`}
            {st.checks_done !== undefined && ` \u00b7 ${st.checks_done} checks done`}
            {st.folds_total ? ` \u00b7 path ${(st.folds_done ?? 0) + 1} of ${st.folds_total}` : ''}
            {st.jobs_done !== undefined && st.total_jobs
              ? ` \u00b7 job ${st.jobs_done} of ${st.total_jobs}` : ''}
          </div>
          {st.error && (
            <div style={{ color: C.red, fontSize: 11.5, marginTop: 4 }}>{st.error}</div>
          )}
          {/* A run where nothing ever traded reports 100% and a seed
              dispersion of 0.00 — every seed agreeing on doing nothing — and
              looks exactly like a success from the progress bar alone. */}
          {(st.no_trades || st.thin_result) && (
            <div style={{
              color: C.amber, fontSize: 11.5, marginTop: 6, padding: 8,
              background: `${C.amber}14`, border: `1px solid ${C.amber}55`,
              borderRadius: 6, lineHeight: 1.55,
            }}>
              <AlertTriangle size={12} />{' '}
              {st.no_trades
                ? 'This run finished without taking a single trade on any fold. '
                  + 'It scores zero, its 0.00 seed dispersion means the seeds agreed '
                  + 'on doing nothing, and the gate will reject it. Raise iterations '
                  + 'or give it more bars.'
                : 'The best fold took very few trades — far short of what the '
                  + "gate's sample-size check needs, so it will be rejected."}
            </div>
          )}

          {/* The gate's checks, as they land. This is the point of the whole
              exercise: a 32-minute run that says nothing is indistinguishable
              from a hang, and a list that grows is not. */}
          {(st.checks || []).length > 0 && (
            <table className="table" style={{ fontSize: 11.5, marginTop: 8 }}>
              <tbody>
                {st.checks.map((c, i) => (
                  <tr key={i}>
                    <td style={{ width: 48 }}>
                      <Pill colour={c.skipped ? C.dim : c.passed ? C.green : C.red}>
                        {c.skipped ? 'SKIP' : c.passed ? 'PASS' : 'FAIL'}
                      </Pill>
                    </td>
                    <td style={{ fontWeight: 600 }}>{c.name}</td>
                    <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                      <Num value={c.value} />
                    </td>
                    <td style={{ color: C.dim, textAlign: 'right', whiteSpace: 'nowrap' }}>
                      {c.bar === null || c.bar === undefined
                        ? '' : `bar ${Number(c.bar).toFixed(2)}`}
                    </td>
                    <td style={{ color: C.dim }}>{c.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {events.length > 0 && (
            <details open={running} style={{ marginTop: 8 }}>
              <summary style={{ cursor: 'pointer', fontSize: 11.5, color: C.dim }}>
                {events.length} event{events.length === 1 ? '' : 's'} &mdash; newest first
              </summary>
              <div style={{
                maxHeight: 200, overflowY: 'auto', background: C.sunk, borderRadius: 6,
                border: `1px solid ${C.line}`, padding: 8, marginTop: 6,
                fontFamily: 'ui-monospace, monospace', fontSize: 11, lineHeight: 1.7,
              }}>
                {events.map((e, i) => (
                  <div key={i} style={{ display: 'flex', gap: 8 }}>
                    <span style={{ color: C.faint, flexShrink: 0 }}>
                      {String(e.at || '').slice(11, 19)}
                    </span>
                    <span>{e.text}</span>
                  </div>
                ))}
              </div>
            </details>
          )}
        </div>
      )}
    </div>
  );
}

const PIPELINE_PHASES = [
  {
    key: 'train',
    n: 1,
    label: 'Train',
    what: 'Every seed against every purged CPCV path, in a separate process. '
      + 'Reports the job it is on, the iteration, how many decisions were in the '
      + 'batch, and how much the policy is still exploring.',
  },
  {
    key: 'collect',
    n: 2,
    label: 'Collect',
    what: "Takes the best fold's weights and scaler off disk and stores them as "
      + 'this agent. Seconds.',
  },
  {
    key: 'distil',
    n: 3,
    label: 'Distil charter',
    what: 'Replays the policy over the bars to capture its decisions, then fits '
      + 'readable gates to them. The replay and the fitting are reported '
      + 'separately, because the replay is the slow half.',
  },
  {
    key: 'validate',
    n: 4,
    label: 'Run the gate',
    what: 'Thirteen stages and up to twenty-five checks. It runs in the '
      + 'background and reports every stage as it starts and every check as it '
      + 'lands \u2014 the last full run took 32 minutes, which is far too long to '
      + 'be a spinner.',
  },
];

/** Every (seed, path) result. The table that says whether to believe any of it. */
function FoldResults({ training }) {
  const rows = training?.results || [];
  if (!rows.length) return null;
  const disp = training.sharpe_dispersion;
  const best = training.best || {};
  return (
    <Section
      icon={<Target size={15} />}
      title={`Per-fold results (${rows.length})`}
      why={`One row per seed per purged CPCV path. This is the table the gate reads,
        and reading it yourself is how you tell a real edge from one lucky
        initialisation: a result that only exists on one seed, or only on one
        path, is not a result.`.replace(/\s+/g, ' ')}
      right={disp === null || disp === undefined ? null : (
        <Pill colour={disp <= 1 ? C.green : C.red}>
          seed dispersion {Number(disp).toFixed(2)} {disp <= 1 ? 'ok' : '— too high'}
        </Pill>
      )}
    >
      {disp > 1 && (
        <div style={{
          background: `${C.red}18`, border: `1px solid ${C.red}55`, borderRadius: 6,
          padding: 10, fontSize: 12, marginBottom: 10,
        }}>
          <AlertTriangle size={13} /> The seeds disagree by more than 1.0 Sharpe.
          There is no result here yet — the gate will reject it, and it should.
          Changing the strategy in response to this would be fitting to noise.
        </div>
      )}
      <div style={{ overflowX: 'auto' }}>
        <table className="table" style={{ fontSize: 12 }}>
          <thead>
            <tr>
              <th>Symbol</th><th>Seed</th><th>Path</th>
              <th style={{ textAlign: 'right' }}>Train bars</th>
              <th style={{ textAlign: 'right' }}>Test bars</th>
              <th style={{ textAlign: 'right' }}>Trades</th>
              <th style={{ textAlign: 'right' }}>Mean R</th>
              <th style={{ textAlign: 'right' }}>Sharpe</th>
              <th style={{ textAlign: 'right' }}>Adherence</th>
              <th style={{ textAlign: 'right' }}>Took</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const isBest = r.seed === best.seed && r.fold === best.fold
                && r.symbol === best.symbol;
              return (
                <tr key={i} style={isBest ? { background: `${C.green}14` } : undefined}>
                  <td>{r.symbol || '—'}{isBest && <Pill colour={C.green}> best</Pill>}</td>
                  <td>{r.seed}</td>
                  <td>{r.fold}</td>
                  <td style={{ textAlign: 'right', color: C.dim }}>
                    {(r.train_rows || 0).toLocaleString()}
                  </td>
                  <td style={{ textAlign: 'right', color: C.dim }}>
                    {(r.test_rows || 0).toLocaleString()}
                  </td>
                  <td style={{
                    textAlign: 'right',
                    color: (r.test_trades || 0) < 30 ? C.amber : 'inherit',
                  }}>{r.test_trades}</td>
                  <td style={{ textAlign: 'right' }}><Num value={r.test_mean_r} /></td>
                  <td style={{ textAlign: 'right' }}><Num value={r.sharpe} digits={2} /></td>
                  <td style={{ textAlign: 'right', color: C.dim }}>
                    {r.test_adherence === undefined || r.test_adherence === null ? '—'
                      : Number(r.test_adherence).toFixed(2)}
                  </td>
                  <td style={{ textAlign: 'right', color: C.faint }}>
                    {r.seconds ? `${Math.round(r.seconds)}s` : '—'}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p style={{ fontSize: 11, color: C.faint, marginTop: 8 }}>
        Trade counts under 30 are amber because a mean R over 20 trades is not a
        measurement of anything. Adherence is the trained policy scored against
        the distilled charter on the test rows — it is how the charter fidelity
        check in the gate gets its number.
      </p>
    </Section>
  );
}

// ── create: a form that reads itself back to you ──────────────────────────

const DEFAULT_FORM = {
  name: '', symbols: '', timeframe: 'M5', seeds: '1,2,3,4,5',
  base_strategy: '', director: false, director_horizon: 12,
  exit_head: true, exit_timeframe: '',
  risk_head: true, stop_atr_choices: '0.75,1,1.5,2,3', rr_choices: '1,1.5,2,3,5',
  htf: '', iterations: 40, population: 24, cpcv_groups: 8, cpcv_test_groups: 2,
  max_cpcv_paths: 10, embargo_bars: 288, max_hold_bars: 288,
  money_weight: 0.7, adherence_weight: 0.3, downside_penalty: 1,
  elite_frac: 0.25, init_sigma: 0.5, notes: '',
};

const nums = (s) => String(s).split(',').map((x) => parseFloat(x.trim()))
  .filter((n) => !Number.isNaN(n));
const strs = (s) => String(s).split(',').map((x) => x.trim()).filter(Boolean);

function CreatePanel({ features, onCreated, onCancel }) {
  const [f, setF] = useState(DEFAULT_FORM);
  // Held separately from `base_strategy` rather than derived from it: the
  // strategy list arrives over the network, so deriving the radio from
  // "is a strategy chosen" meant clicking mode B before the list loaded
  // selected nothing and looked broken.
  const [mode, setMode] = useState('A');
  const [chosen, setChosen] = useState([]);
  const [error, setError] = useState('');
  const strategies = useBaseStrategies();
  const set = (k) => (v) => setF((prev) => ({ ...prev, [k]: v }));

  const payload = useMemo(() => ({
    name: f.name.trim() || 'unnamed',
    symbols: strs(f.symbols),
    attempted_symbols: strs(f.symbols),
    timeframe: f.timeframe,
    htf: strs(f.htf),
    features: chosen.length ? chosen : null,
    seeds: nums(f.seeds).map((n) => Math.round(n)),
    base_strategy: mode === 'B' ? f.base_strategy : '',
    director: f.director,
    director_horizon: Number(f.director_horizon) || 12,
    exit_head: f.exit_head,
    exit_timeframe: f.exit_timeframe,
    risk_head: f.risk_head,
    stop_atr_choices: nums(f.stop_atr_choices),
    rr_choices: nums(f.rr_choices),
    iterations: Number(f.iterations) || 40,
    population: Number(f.population) || 24,
    cpcv_groups: Number(f.cpcv_groups) || 8,
    cpcv_test_groups: Number(f.cpcv_test_groups) || 2,
    max_cpcv_paths: Number(f.max_cpcv_paths) || 10,
    embargo_bars: Number(f.embargo_bars) || 288,
    max_hold_bars: Number(f.max_hold_bars) || 288,
    money_weight: Number(f.money_weight),
    adherence_weight: Number(f.adherence_weight),
    downside_penalty: Number(f.downside_penalty),
    elite_frac: Number(f.elite_frac),
    init_sigma: Number(f.init_sigma),
    notes: f.notes,
  }), [f, chosen, mode]);

  // The read-back comes from the server, on a spec it has not saved. One
  // source of wording for the form and for the saved agent: a second copy
  // in JavaScript would drift the first time the rules changed.
  const { data: described } = useQuery({
    queryKey: qk('rl-describe', JSON.stringify(payload)),
    queryFn: () => rl.describe(payload).then((r) => r.data),
    enabled: payload.symbols.length > 0,
    staleTime: Infinity,
    placeholderData: (prev) => prev,
    retry: false,
  });

  const groups = useMemo(() => {
    const out = {};
    (features || []).forEach((x) => { (out[x.group] = out[x.group] || []).push(x); });
    return out;
  }, [features]);

  const create = useMutation({
    mutationFn: () => rl.create(payload),
    onSuccess: (r) => { setError(''); onCreated(r.data); },
    onError: (e) => setError(apiError(e)),
  });

  // Mode B with nothing chosen is the one half-filled state that would
  // otherwise reach the server, where it silently becomes a mode-A agent.
  const ready = f.name.trim() && strs(f.symbols).length > 0
    && (mode === 'A' || strategies.includes(f.base_strategy));

  return (
    <div style={{
      display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 320px', gap: 16,
      alignItems: 'start',
    }}>
      <div style={{ minWidth: 0 }}>
        <Section
          icon={<Brain size={15} />} title="1 · What should it do?"
          why="This is the choice everything else follows from, and it is the one
            that was impossible to make on the old screen - which could only ever
            create the first of these two."
        >
          <Choice checked={mode === 'A'} onChange={() => setMode('A')}
                  title="Find its own entries — mode A">
            It looks at every bar and decides whether there is a trade. Hardest to
            get a real result from, because it has to learn direction, timing and
            selection at once; it is also the only mode that can find something
            none of the existing strategies express.
          </Choice>
          <Choice checked={mode === 'B'} colour={C.purple}
                  onChange={() => setMode('B')}
                  title="Improve an existing strategy — mode B">
            A strategy you already have finds the setups; the agent only decides
            which of them to take, where the stop goes and what to aim at. Far
            fewer decisions to learn, so a usable result needs far less data —
            and the thing it is improving has already been measured, which means
            you can see whether the agent actually added anything.
          </Choice>
          {mode === 'B' && (
            <Field label="Strategy to optimise"
                   hint={`The agent sees this strategy's signals as candidates. It cannot
                     invent an entry the strategy did not offer, so its ceiling is
                     "this strategy, better selected and better exited".`.replace(/\s+/g, ' ')}>
              <select value={f.base_strategy} onChange={(e) => set('base_strategy')(e.target.value)}>
                <option value="">— choose —</option>
                {strategies.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </Field>
          )}
        </Section>

        <Section
          icon={<GraduationCap size={15} />} title="2 · How should it learn?"
          why="Both of these are available and they are not alternatives - the
            second is the first with a supervised model bolted on in front of it.
            This is the question the old screen could not express at all."
        >
          <Choice checked={!f.director} onChange={() => set('director')(false)}
                  title="Reinforcement learning only">
            The policy learns direction and timing together, from the reward: 70%
            money (realised R, plus credit for capturing a move and for cutting a
            loser early) and 30% adherence to its own charter, minus named
            penalties for losses and for breaches. Nothing tells it which way
            price went; it has to discover that from what it earned.
          </Choice>
          <Choice checked={f.director} colour={C.green} onChange={() => set('director')(true)}
                  title="Supervised direction model + reinforcement">
            A logistic regression is fitted <i>first</i>, on the training fold
            only, to label which way price went over the next N bars. It is then
            <b> frozen</b>, and its call and its confidence become two extra
            inputs the reinforcement policy can read. Supervised learning answers
            &ldquo;which way&rdquo;; reinforcement answers &ldquo;is that worth
            trading, and when do I get out&rdquo; — which is the part a label
            cannot teach, because the right exit depends on what the policy
            itself does next.
          </Choice>
          {f.director && (
            <Field label="Direction horizon (bars)"
                   hint="What the supervised model is asked to call: the sign of the
                     move this many bars ahead. Fitted inside each fold's training
                     rows and never refitted on test rows - a model fitted once on
                     everything would leak the answer into every fold.">
              <input type="number" min="1" value={f.director_horizon}
                     onChange={(e) => set('director_horizon')(e.target.value)} />
            </Field>
          )}
        </Section>

        <Section
          icon={<ShieldCheck size={15} />} title="3 · What may it decide?"
          why="The boundary of the agent's authority. Everything outside this list
            belongs to the risk layer, and the agent has no input that could even
            represent it."
        >
          <label style={{
            display: 'flex', gap: 8, alignItems: 'center', fontSize: 13,
            textTransform: 'none', letterSpacing: 'normal', color: 'inherit',
          }}>
            <input type="checkbox" checked={f.risk_head} style={CHECKBOX}
                   onChange={(e) => set('risk_head')(e.target.checked)} />
            <b>Stop distance and reward:risk</b>
          </label>
          <p style={{ color: C.dim, fontSize: 11.5, margin: '4px 0 8px 24px', lineHeight: 1.55 }}>
            It picks from the menus below — a multiple of ATR for the stop, and a
            ratio for the target. It still never sees or sets a lot size, a risk
            percentage or a balance: the risk gate turns its chosen stop into a
            position, and caps it. Choosing a <i>wider</i> stop therefore makes
            the position smaller, not the risk bigger.
          </p>
          {f.risk_head && (
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginLeft: 24 }}>
              <Field label="Stop, as multiples of ATR"
                     hint="A menu, not a free number: a continuous stop dial lets the
                       policy shrink the stop until the backtest's fill model flatters
                       it, which is this project's oldest measured trap.">
                <input value={f.stop_atr_choices}
                       onChange={(e) => set('stop_atr_choices')(e.target.value)} />
              </Field>
              <Field label="Reward:risk targets"
                     hint="1.5 means a target 1.5x the stop distance away. The agent
                       learns which of these pays on this instrument.">
                <input value={f.rr_choices}
                       onChange={(e) => set('rr_choices')(e.target.value)} />
              </Field>
            </div>
          )}

          <label style={{
            display: 'flex', gap: 8, alignItems: 'center', fontSize: 13, marginTop: 12,
            textTransform: 'none', letterSpacing: 'normal', color: 'inherit',
          }}>
            <input type="checkbox" checked={f.exit_head} style={CHECKBOX}
                   onChange={(e) => set('exit_head')(e.target.checked)} />
            <b>Exit head</b>
          </label>
          <p style={{ color: C.dim, fontSize: 11.5, margin: '4px 0 8px 24px', lineHeight: 1.55 }}>
            Lets it close, part-close or tighten a running trade on each closed
            bar. It can only ever tighten a stop, never widen one. Without this
            the stop, the target and the max hold are the only ways a trade ends.
          </p>
          {f.exit_head && (
            <Field label="Judge exits on a faster timeframe (optional)" indent
                   hint="Blank means the entry timeframe. A faster frame gives the exit
                     head more chances to act within one entry bar, which is the only
                     place sub-minute reaction is worth anything - the entry decision
                     itself is made on closed bars and always will be.">
              <select value={f.exit_timeframe}
                      onChange={(e) => set('exit_timeframe')(e.target.value)}>
                <option value="">same as entry ({f.timeframe})</option>
                {TIMEFRAMES.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </Field>
          )}
        </Section>

        <Section
          icon={<Database size={15} />} title="4 · What should it train on?"
          why="Frozen on creation and content-hashed. The validation report names
            every symbol attempted, including the ones that turn out badly -
            reporting only the ones that worked is survivorship bias in the
            experiment itself, and no leak probe can catch it."
        >
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <Field label="Name" hint="Used in the strategy id a live slot refers to.">
              <input value={f.name} onChange={(e) => set('name')(e.target.value)}
                     placeholder="crash-drift" />
            </Field>
            <Field label="Entry timeframe" hint="The bars every entry decision is made on.">
              <select value={f.timeframe} onChange={(e) => set('timeframe')(e.target.value)}>
                {TIMEFRAMES.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </Field>
            <Field span label="Symbols, comma separated"
                   hint={`Free text, never a dropdown: "US Tech 100" is Deriv's own name
                     for it and appears in no canonical instrument list.`.replace(/\s+/g, ' ')}>
              <input value={f.symbols} onChange={(e) => set('symbols')(e.target.value)}
                     placeholder="Crash 1000 Index, Boom 1000 Index" />
            </Field>
            <Field label="Seeds"
                   hint="Five is the floor. The gate rejects the run if Sharpe moves by
                     more than 1.0 across them, because that is a sample of one lucky
                     initialisation rather than an edge.">
              <input value={f.seeds} onChange={(e) => set('seeds')(e.target.value)} />
            </Field>
            <Field label="Max hold (bars)"
                   hint="A trade is force-closed after this. 288 M5 bars is one day.">
              <input type="number" value={f.max_hold_bars}
                     onChange={(e) => set('max_hold_bars')(e.target.value)} />
            </Field>
            <Field span label="Why you are running this (optional)"
                   hint="Stored with the frozen spec. Worth a sentence: a rejected run
                     six months old is only useful if it says what it was testing.">
              <input value={f.notes} onChange={(e) => set('notes')(e.target.value)}
                     placeholder="does ORB selection improve if the agent picks the stop?" />
            </Field>
            <Field span label="Higher timeframes for context (optional)"
                   hint="Comma separated, e.g. H1, H4. Each one adds trend and range
                     columns taken from its last CLOSED bar - so an H1 column at 10:05
                     carries the 09:00 bar, never the one still forming. Blank means the
                     agent sees only its entry timeframe.">
              <input value={f.htf} onChange={(e) => set('htf')(e.target.value)}
                     placeholder="H1, H4" />
            </Field>
          </div>
        </Section>

        <Section
          icon={<FlaskConical size={15} />}
          title="5 · How hard to look, and how it is scored"
          why="The compute dials and the reward shape, exposed rather than hidden.
            The first group is the honest trade-off between how long a run takes and
            how much you can believe it; the second is what the agent is actually
            being paid for."
        >
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
            <Field label="Iterations" hint="Per seed, per path.">
              <input type="number" value={f.iterations}
                     onChange={(e) => set('iterations')(e.target.value)} />
            </Field>
            <Field label="Population" hint="Candidates per iteration, CEM only.">
              <input type="number" value={f.population}
                     onChange={(e) => set('population')(e.target.value)} />
            </Field>
            <Field label="Max CPCV paths" hint="Capped from C(groups, test groups).">
              <input type="number" value={f.max_cpcv_paths}
                     onChange={(e) => set('max_cpcv_paths')(e.target.value)} />
            </Field>
            <Field label="CPCV groups" hint="The history is cut into this many blocks.">
              <input type="number" value={f.cpcv_groups}
                     onChange={(e) => set('cpcv_groups')(e.target.value)} />
            </Field>
            <Field label="Test groups per path" hint="Held out, purged and embargoed.">
              <input type="number" value={f.cpcv_test_groups}
                     onChange={(e) => set('cpcv_test_groups')(e.target.value)} />
            </Field>
            <Field label="Embargo (bars)"
                   hint="Dropped either side of every test block, so a trade that
                     straddles the cut cannot be scored on rows the policy trained
                     on.">
              <input type="number" value={f.embargo_bars}
                     onChange={(e) => set('embargo_bars')(e.target.value)} />
            </Field>
            <Field label="Elite fraction" hint="Top slice CEM keeps each iteration.">
              <input type="number" step="0.05" value={f.elite_frac}
                     onChange={(e) => set('elite_frac')(e.target.value)} />
            </Field>
            <Field label="Initial sigma"
                   hint="How wide CEM's first guess is. Too small and it never leaves
                     where it started.">
              <input type="number" step="0.1" value={f.init_sigma}
                     onChange={(e) => set('init_sigma')(e.target.value)} />
            </Field>
          </div>

          <div style={{ borderTop: `1px solid ${C.line}`, margin: '14px 0 10px' }} />
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
            <Field label="Money weight"
                   hint="Realised R, plus credit for capturing a move and for cutting a
                     loser early. Split into those compartments so an exit that was
                     right but small is not scored as a failure to hold.">
              <input type="number" step="0.05" value={f.money_weight}
                     onChange={(e) => set('money_weight')(e.target.value)} />
            </Field>
            <Field label="Adherence weight"
                   hint="How much of the score is following its own charter. Signed
                     around the floor: below it the term goes negative, so a breach
                     costs rather than merely earning nothing.">
              <input type="number" step="0.05" value={f.adherence_weight}
                     onChange={(e) => set('adherence_weight')(e.target.value)} />
            </Field>
            <Field label="Downside penalty"
                   hint="How much more a loss counts than an equal gain. 1.0 is
                     symmetric; above it the reward is Sortino-flavoured and the policy
                     will trade less and cut sooner."
            >
              <input type="number" step="0.1" min="1" value={f.downside_penalty}
                     onChange={(e) => set('downside_penalty')(e.target.value)} />
            </Field>
          </div>
          {Math.abs(Number(f.money_weight) + Number(f.adherence_weight) - 1) > 0.001 && (
            <div style={{ color: C.amber, fontSize: 11.5, marginTop: 8 }}>
              <AlertTriangle size={12} /> The two weights do not sum to 1. That is
              allowed — it scales the whole reward — but it also makes this agent's
              scores incomparable with every other agent's, including in the gate's
              deflated Sharpe.
            </div>
          )}
        </Section>

        <Section
          icon={<Info size={15} />} title="6 · Which inputs may it see?"
          why={`Every feature here is prefix-stable: its value at a bar does not change
            when later bars arrive, and that is checked by a probe at several cut
            points rather than one. None of them carries a balance, a lot size or a
            risk percentage - the agent has no access to money management because
            there is nowhere to put it.`.replace(/\s+/g, ' ')}
        >
          <details>
            <summary style={{ cursor: 'pointer', fontSize: 12 }}>
              {chosen.length ? `${chosen.length} chosen` : 'The default set'} — click to change
            </summary>
            {Object.entries(groups).map(([group, list]) => (
              <div key={group} style={{ marginBottom: 8, marginTop: 8 }}>
                <div style={{ fontWeight: 600, fontSize: 12, color: C.dim }}>{group}</div>
                {list.map((x) => (
                  <label key={x.name} style={{
                    display: 'block', fontSize: 12, paddingLeft: 8, fontWeight: 400,
                    textTransform: 'none', letterSpacing: 'normal', color: 'inherit',
                  }}>
                    <input type="checkbox" style={CHECKBOX}
                           checked={chosen.includes(x.name)}
                           onChange={(e) => setChosen(e.target.checked
                             ? [...chosen, x.name]
                             : chosen.filter((n) => n !== x.name))} />
                    {' '}<code>{x.name}</code> — {x.plain}
                  </label>
                ))}
              </div>
            ))}
          </details>
        </Section>

        {error && (
          <div style={{
            color: C.red, fontSize: 12, marginBottom: 8, background: `${C.red}14`,
            border: `1px solid ${C.red}44`, borderRadius: 6, padding: 10,
          }}>{error}</div>
        )}
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn btn-primary" disabled={!ready || create.isPending}
                  onClick={() => create.mutate()}>
            {create.isPending ? <Loader2 size={14} className="spin" /> : <FlaskConical size={14} />}
            {' '}Pre-register this agent
          </button>
          <button className="btn" onClick={onCancel}>Cancel</button>
        </div>
      </div>

      {/* The read-back. Sticky, because it is the point of the form. */}
      <div style={{ position: 'sticky', top: 12 }}>
        <Section icon={<Check size={15} />} title="What you are building"
                 why="Written by the backend from the form as it stands, not by this
                   screen - so it cannot tell you something the engine will not
                   do.">
          {described?.ok === false && (
            <p style={{ color: C.amber, fontSize: 12 }}>{described.error}</p>
          )}
          {!described && (
            <p style={{ color: C.faint, fontSize: 12 }}>
              Add at least one symbol and this fills in.
            </p>
          )}
          {described?.ok && <LearningSummary learning={described.learning} compact />}
          {described?.estimate && (
            <div style={{
              marginTop: 12, paddingTop: 10, borderTop: `1px solid ${C.line}`,
            }}>
              <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>
                Work this will do
              </div>
              <div style={{ fontSize: 22, fontWeight: 700, color: C.blue }}>
                {described.estimate.jobs}
                <span style={{ fontSize: 12, color: C.dim, fontWeight: 400 }}> training runs</span>
              </div>
              <p style={{ fontSize: 11, color: C.dim, lineHeight: 1.55, marginTop: 4 }}>
                {described.estimate.note}
              </p>
            </div>
          )}
        </Section>
      </div>
    </div>
  );
}

// ── data ──────────────────────────────────────────────────────────────────

/** Attach a source per symbol, then start the run. */
/**
 * The Data tab's choices, kept per agent and across reloads.
 *
 * THE BUG THIS FIXES
 * `files` and the training window were `useState` INSIDE `DataTab`, and
 * `DataTab` only renders while its tab is selected. Switching to Training to
 * watch the run and coming back unmounted the component and threw both away —
 * so the sources went back to "no source yet" and the window silently reset to
 * its three-months-ago default. Reported after a run was started against a
 * date range that had already been discarded.
 *
 * localStorage rather than lifting the state up, because lifting it would
 * survive a tab switch and still not survive the page reload that the admin
 * does after a deploy. Wrapped in try/catch: a private window has no storage
 * and the form must still work there.
 */
function dataChoicesKey(agentId) {
  return `rl-data-choices:${agentId}`;
}

function loadChoices(agentId) {
  try {
    const raw = localStorage.getItem(dataChoicesKey(agentId));
    if (raw) return JSON.parse(raw);
  } catch { /* no storage, or corrupt - fall through to the defaults */ }
  return null;
}

function saveChoices(agentId, value) {
  try {
    localStorage.setItem(dataChoicesKey(agentId), JSON.stringify(value));
  } catch { /* nothing to do: the form still works, it just will not persist */ }
}

/**
 * What actually went wrong, in words that point somewhere.
 *
 * `String(e)` on an axios failure gives "AxiosError: Network Error", which
 * was on screen for an hour meaning nothing. The three cases need different
 * actions and must not look alike:
 *   - the server answered with a reason   -> fix the request
 *   - the request never reached it        -> check the backend or the link
 *   - the client gave up waiting          -> it may still be running
 */
function apiError(e) {
  const said = e?.response?.data?.detail;
  if (said) return typeof said === 'string' ? said : JSON.stringify(said);
  if (e?.response) return `the server returned ${e.response.status}`;
  if (e?.code === 'ECONNABORTED') {
    return 'the browser stopped waiting for a reply - the request may still be '
      + 'running on the server; check the Logs tab before retrying';
  }
  return 'the request never reached the server - the backend may be down, or '
    + 'the connection dropped. Nothing was changed.';
}

/** Today minus N months, as YYYY-MM-DD. */
function monthsAgo(n) {
  const d = new Date();
  d.setMonth(d.getMonth() - n);
  return d.toISOString().slice(0, 10);
}

function DataTab({ agent, busy, onStarted }) {
  const saved = loadChoices(agent.agent_id);
  const [files, setFilesRaw] = useState(() => saved?.files || {});
  const [uploading, setUploading] = useState('');
  const [error, setError] = useState('');
  const [ranges, setRanges] = useState({});

  const probe = async (symbol) => {
    setRanges((r) => ({ ...r, [symbol]: null }));
    try {
      const { data } = await rl.mt5Range(symbol, agent.spec.timeframe);
      setRanges((r) => ({ ...r, [symbol]: data }));
    } catch (e) {
      setRanges((r) => ({
        ...r, [symbol]: { ok: false, error: apiError(e) },
      }));
    }
  };
  // Ends three months ago by default, and that default is the point. MT5
  // hands back the most recent N bars, so an unbounded run trains right up to
  // today and leaves nothing after it to forward test in — and the
  // Backtester refuses a window that sits entirely inside the training
  // range, so every backtest is blocked too.
  const [win, setWinRaw] = useState(
    () => saved?.win || { start: '', end: monthsAgo(3) });

  // Every change is written through, so the choice survives a tab switch,
  // a navigation away and a reload.
  const persist = (nextFiles, nextWin) =>
    saveChoices(agent.agent_id, { files: nextFiles, win: nextWin });
  const setFiles = (updater) => setFilesRaw((prev) => {
    const next = typeof updater === 'function' ? updater(prev) : updater;
    persist(next, win);
    return next;
  });
  const setWin = (next) => { persist(files, next); setWinRaw(next); };
  const symbols = agent?.spec?.symbols || [];
  const progressed = agent.progress?.datasets || [];
  const trained = agent.trained_on || [];

  const datasets = useMemo(() => {
    const out = {};
    const w = { start: win.start || undefined, end: win.end || undefined };
    Object.entries(files).forEach(([sym, info]) => {
      out[sym] = info === 'mt5'
        ? { source: 'mt5', ...w }
        : { path: info.path, timeframe: info.timeframe, ...w };
    });
    return out;
  }, [files, win]);

  const ready = symbols.length > 0 && symbols.every((s) => files[s]);

  const upload = async (symbol, file) => {
    setUploading(symbol); setError('');
    try {
      const r = await rl.upload(file, { symbol, timeframe: agent.spec.timeframe });
      setFiles((x) => ({ ...x, [symbol]: r.data }));
    } catch (e) {
      setError(apiError(e));
    } finally { setUploading(''); }
  };

  const start = useMutation({
    mutationFn: () => rl.train(agent.agent_id, { datasets }),
    onSuccess: onStarted,
    onError: (e) => setError(apiError(e)),
  });

  return (
    <>
      <Section
        icon={<Database size={15} />} title="Sources"
        why={`Every pre-registered symbol needs one, including the ones you expect to
          fail. A file is validated rather than accepted: if its intervals do not
          match its timeframe, or its highs do not contain its closes, it is refused
          with the reason - resampling it quietly would move every feature by an
          amount nothing downstream could detect.`.replace(/\s+/g, ' ')}
      >
        <div style={{
          background: C.sunk, border: `1px solid ${C.line}`, borderRadius: 8,
          padding: 12, marginBottom: 12,
        }}>
          <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>
            Training window
          </div>
          <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap' }}>
            <Field label="From" hint="Blank means as far back as the source goes.">
              <input type="date" value={win.start} disabled={busy}
                     onChange={(e) => setWin({ ...win, start: e.target.value })} />
            </Field>
            <Field label="To"
                   hint="Everything after this date is kept out of training, and is
                     what you forward test on.">
              <input type="date" value={win.end} disabled={busy}
                     onChange={(e) => setWin({ ...win, end: e.target.value })} />
            </Field>
            <div style={{ display: 'flex', gap: 6, paddingBottom: 18 }}>
              {[1, 3, 6, 12].map((m) => (
                <button key={m} className="btn btn-sm" disabled={busy}
                        title={`End the training range ${m} month${m > 1 ? 's' : ''} ago`}
                        onClick={() => setWin({ ...win, end: monthsAgo(m) })}>
                  leave {m}m out
                </button>
              ))}
              <button className="btn btn-sm" disabled={busy}
                      title="Train right up to the latest bar"
                      onClick={() => setWin({ ...win, end: '' })}>
                up to now
              </button>
            </div>
          </div>
          {!win.end ? (
            <div style={{ color: C.amber, fontSize: 11.5, marginTop: 6, lineHeight: 1.55 }}>
              <AlertTriangle size={12} /> With no end date the agent trains up to
              the most recent bar. The purged CPCV folds still hold blocks out, so
              it is not untested — but there is no period <i>after</i> training to
              forward test in, and the Backtester will refuse every window that
              falls inside this range. Leave a few months out unless you have a
              reason not to.
            </div>
          ) : (
            <div style={{ color: C.dim, fontSize: 11.5, marginTop: 6 }}>
              Bars after <b>{win.end}</b> are held back. That is your forward
              test, and it is the only part of the record the agent has never seen.
            </div>
          )}
        </div>

        <table className="table" style={{ fontSize: 12 }}>
          <tbody>
            {symbols.map((s) => (
              <tr key={s}>
                <td style={{ fontWeight: 600 }}>{s}</td>
                <td>
                  {files[s] === 'mt5' ? (
                    <>
                      <Pill colour={C.blue}>live MT5</Pill>
                      <span style={{ color: C.dim, marginLeft: 6 }}>
                        fetched for the window above
                      </span>
                      {/* What the terminal actually carries. A window that
                          starts before this returns nothing, which used to
                          surface as "no bars" with no explanation. */}
                      {ranges[s] === undefined ? (
                        <button className="btn btn-sm" style={{ marginLeft: 8 }}
                                onClick={() => probe(s)}>
                          check history
                        </button>
                      ) : ranges[s] === null ? (
                        /* The first deep read of a symbol makes the terminal
                           download that history from the broker, which took
                           25 s for XAUUSD M15 when measured. Without this
                           branch the waiting state fell through to the error
                           branch and said "the terminal has no history for
                           this" while it was busy fetching it. */
                        <span style={{ color: C.dim, marginLeft: 8 }}>
                          asking the terminal… the first check on a symbol can
                          take half a minute while MT5 downloads its history
                        </span>
                      ) : ranges[s]?.ok ? (
                        <span style={{ color: C.faint, marginLeft: 8 }}>
                          {ranges[s].capped ? 'at least' : 'has'}{' '}
                          {Number(ranges[s].bars).toLocaleString()} bars,{' '}
                          <b>{ranges[s].first.slice(0, 10)}</b> to{' '}
                          {ranges[s].last.slice(0, 10)}
                          {/* `capped` means the figure is this terminal's
                              per-request limit, not the end of the data —
                              so a start before it is not a reason to worry,
                              and the warning below must not fire. */}
                          {ranges[s].capped && (
                            <span style={{ color: C.dim }}>
                              {' '}— that is this terminal's
                              {' '}{Number(ranges[s].request_cap).toLocaleString()}-bar
                              {' '}request limit, not the end of its history
                            </span>
                          )}
                          {!ranges[s].capped && win.start
                            && win.start < ranges[s].first.slice(0, 10) && (
                            <b style={{ color: C.amber }}>
                              {' '}— your start is before this, so it would return nothing
                            </b>
                          )}
                        </span>
                      ) : (
                        <span style={{ color: C.red, marginLeft: 8 }}>
                          {ranges[s]?.error || 'the terminal has no history for this'}
                        </span>
                      )}
                    </>
                  )
                    : files[s] ? (
                      <span style={{ color: C.dim }}>
                        {files[s].rows.toLocaleString()} bars,{' '}
                        {String(files[s].start_iso).slice(0, 10)} to{' '}
                        {String(files[s].end_iso).slice(0, 10)}
                        {files[s].gaps ? `, ${files[s].gaps} gaps` : ''}
                      </span>
                    ) : <span style={{ color: C.faint }}>no source yet</span>}
                </td>
                <td style={{ textAlign: 'right' }}>
                  <button className="btn btn-sm" disabled={busy}
                          title={busy ? 'A run is in progress' : 'Pull bars from the live terminal'}
                          onClick={() => setFiles((x) => ({ ...x, [s]: 'mt5' }))}>
                    Use MT5
                  </button>{' '}
                  <label className="btn btn-sm"
                         style={{ cursor: busy ? 'not-allowed' : 'pointer', opacity: busy ? 0.5 : 1 }}>
                    {uploading === s ? <Loader2 size={12} className="spin" /> : <Upload size={12} />}
                    {' '}Upload
                    <input type="file" hidden disabled={busy}
                           accept=".csv,.tsv,.json,.npz,.parquet"
                           onChange={(e) => e.target.files?.[0] && upload(s, e.target.files[0])} />
                  </label>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {Object.values(files).some((x) => x !== 'mt5' && x?.notes?.length > 0) && (
          <ul style={{ color: C.amber, fontSize: 12 }}>
            {Object.entries(files).flatMap(([s, x]) => (x === 'mt5' ? [] : (x.notes || [])
              .map((n) => <li key={`${s}-${n}`}>{s}: {n}</li>)))}
          </ul>
        )}
        {error && <div style={{ color: C.red, fontSize: 12, marginBottom: 8 }}>{error}</div>}
        <button className="btn btn-primary" disabled={!ready || busy || start.isPending}
                title={busy ? 'Already training'
                  : !ready ? 'Every pre-registered symbol needs a source first' : ''}
                onClick={() => start.mutate()}>
          <Play size={14} /> Start training
        </button>
        {!ready && !busy && (
          <span style={{ color: C.amber, fontSize: 11, marginLeft: 8 }}>
            {symbols.filter((s) => !files[s]).length} of {symbols.length} symbols
            still need a source.
          </span>
        )}
      </Section>

      {progressed.length > 0 && (
        <Section
          icon={<Database size={15} />} title="What the last run actually read"
          why={`Reported by the training process itself, not by this form - so it is
            what went in, not what was selected.`.replace(/\s+/g, ' ')}
        >
          <table className="table" style={{ fontSize: 12 }}>
            <thead>
              <tr><th>Symbol</th><th style={{ textAlign: 'right' }}>Bars</th>
                <th>From</th><th>To</th>
                <th style={{ textAlign: 'right' }}>Base signals</th></tr>
            </thead>
            <tbody>
              {progressed.map((d) => (
                <tr key={d.symbol}>
                  <td style={{ fontWeight: 600 }}>{d.symbol}</td>
                  <td style={{ textAlign: 'right' }}>{(d.bars || 0).toLocaleString()}</td>
                  <td style={{ color: C.dim }}>
                    {new Date(d.start * 1000).toISOString().slice(0, 10)}
                  </td>
                  <td style={{ color: C.dim }}>
                    {new Date(d.end * 1000).toISOString().slice(0, 10)}
                  </td>
                  <td style={{ textAlign: 'right', color: d.base_signals ? 'inherit' : C.faint }}>
                    {agent.spec?.base_strategy ? (d.base_signals || 0).toLocaleString() : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {agent.spec?.base_strategy && progressed.some((d) => !d.base_signals) && (
            <div style={{ color: C.red, fontSize: 12, marginTop: 8 }}>
              <AlertTriangle size={13} /> A symbol produced no{' '}
              <code>{agent.spec.base_strategy}</code> signals at all. A mode-B
              agent can only choose among that strategy&rsquo;s setups, so there
              was nothing for it to learn on that instrument.
            </div>
          )}
        </Section>
      )}

      {trained.length > 0 && (
        <Section
          icon={<ShieldCheck size={15} />} title="Frozen training range"
          why={`What the Backtester checks a window against. A run entirely inside this
            is disabled rather than warned about, because its result would be the
            training score wearing a backtest's clothes.`.replace(/\s+/g, ' ')}
        >
          <table className="table" style={{ fontSize: 12 }}>
            <tbody>
              {trained.map((c, i) => (
                <tr key={i}>
                  <td style={{ fontWeight: 600 }}>{c.symbol}</td>
                  <td>{c.timeframe}</td>
                  <td style={{ color: C.dim }}>
                    {new Date(c.start * 1000).toISOString().slice(0, 10)} to{' '}
                    {new Date(c.end * 1000).toISOString().slice(0, 10)}
                  </td>
                  <td><code style={{ color: C.faint, fontSize: 11 }}>{c.hash}</code></td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      )}

      <Section
        icon={<Info size={15} />}
        title={`Observation columns (${(agent.spec?.features || []).length})`}
        why={`The whole of what the agent sees on a bar. If something is not in this
          list the agent cannot condition on it, cannot learn it, and cannot be
          accused of it.`.replace(/\s+/g, ' ')}
      >
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {(agent.spec?.features || []).map((n) => (
            <code key={n} style={{
              fontSize: 11, background: C.sunk, border: `1px solid ${C.line}`,
              borderRadius: 4, padding: '2px 6px',
            }}>{n}</code>
          ))}
        </div>
        {agent.spec?.director && (
          <p style={{ fontSize: 12, color: C.green, marginTop: 10 }}>
            Plus two from the supervised director: its direction call and its
            confidence. Those are produced by a model fitted on the training rows
            of each fold and then frozen, so they carry no information from the
            test rows.
          </p>
        )}
        <p style={{ fontSize: 12, color: C.dim, marginTop: 10 }}>
          Plus the position vector while a trade is open — unrealised R, bars
          held, how far the stop has moved — which is what makes an exit decision
          possible at all. No balance, no lot size, no risk percentage.
        </p>
      </Section>
    </>
  );
}

// ── training tab ──────────────────────────────────────────────────────────

function TrainingTab({ agent, busy, life, step }) {
  const t = agent.training;
  const steps = [
    {
      n: 1, label: 'Collect', icon: <Check size={14} />,
      run: () => rl.collect(agent.agent_id), done: life.collected,
      blocked: !life.trained && 'Nothing to collect until a run has finished',
      what: 'Takes the best fold\'s weights and scaler and stores them as this '
        + 'agent. Nothing before this point is an agent.',
    },
    {
      n: 2, label: 'Distil charter', icon: <ChevronRight size={14} />,
      run: () => rl.distil(agent.agent_id, { datasets: {} }), done: life.hasCharter,
      blocked: !life.collected && 'Collect first — there is no policy to read',
      what: 'Reads the weights and writes the rules they imply, with the measured '
        + 'hit rate and mean R of each gate. This is what you approve, and what '
        + 'the 0.30 adherence term is scored against for the rest of its life.',
    },
    {
      n: 3, label: 'Run the gate', icon: <FlaskConical size={14} />,
      run: () => rl.validate(agent.agent_id, { datasets: {} }), done: life.validated,
      blocked: !life.hasCharter && 'Distil a charter first — fidelity is one of the checks',
      what: 'Sample size, seed dispersion, CPCV paths, PBO, deflated Sharpe, a '
        + 'regime split, a cost-stress run, the leak probe, adherence and charter '
        + 'fidelity. It rejects most candidates; a rejection is kept, not deleted. '
        + 'Starts in the background and returns at once - the last full run took 32 '
        + 'minutes, so watch it above rather than waiting on this button.',
    },
  ];

  return (
    <>
      <Section
        icon={<FlaskConical size={15} />} title="Plan for a run"
        why={`What pressing Start would do, before it does it. The job count is exact
          and it is the number that surprises people: five seeds over eight purged
          paths is forty runs per symbol, not one.`.replace(/\s+/g, ' ')}
      >
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 4 }}>
          <KV k="Optimiser">{agent.optimiser || 'chosen at run time'}</KV>
          <KV k="Seeds">{(agent.spec?.seeds || []).join(', ') || '—'}</KV>
          <KV k="CPCV">
            {agent.spec?.cpcv_groups} groups, {agent.spec?.cpcv_test_groups} held out,
            {' '}max {agent.spec?.max_cpcv_paths} paths
          </KV>
          <KV k="Embargo">{agent.spec?.embargo_bars} bars either side of each cut</KV>
          <KV k="Iterations">{agent.spec?.iterations} per seed per path</KV>
          <KV k="Max hold">{agent.spec?.max_hold_bars} bars</KV>
          <KV k="Reward split">
            {Math.round((agent.spec?.money_weight ?? 0.7) * 100)}% money /{' '}
            {Math.round((agent.spec?.adherence_weight ?? 0.3) * 100)}% adherence
          </KV>
          <KV k="Min confidence">{agent.spec?.min_confidence}</KV>
        </div>
      </Section>

      <TrainingMonitor agent={agent} busy={busy}
                       onCancel={() => step(() => rl.cancel(agent.agent_id), 'Cancelling')} />

      <Section
        icon={<Activity size={15} />} title="Every phase, live"
        why={`What has run, what is running right now, and what each one said while
          it ran. The four phases write to four separate files and the server merges
          them, so this is one poll rather than four.`.replace(/\s+/g, ' ')}
        right={agent.pipeline?.running
          ? <Pill colour={C.blue}>{agent.pipeline.running} running</Pill>
          : <Pill colour={C.faint}>nothing running</Pill>}
      >
        {PIPELINE_PHASES.map((ph) => (
          <PhaseTimeline key={ph.key} pipeline={agent.pipeline} phase={ph} />
        ))}
      </Section>

      <Section
        icon={<ChevronRight size={15} />} title="After a run finishes"
        why="Three steps in order. Each one is disabled until the one before it has
          happened, with the reason on the button - the server refuses them too, but
          a disabled button is an explanation and a 400 is not."
      >
        {steps.map((s) => (
          <div key={s.n} style={{
            display: 'flex', gap: 12, alignItems: 'flex-start', padding: '10px 0',
            borderTop: s.n > 1 ? `1px solid ${C.line}` : undefined,
          }}>
            <button className={`btn ${s.done ? '' : 'btn-primary'}`}
                    style={{ minWidth: 150, justifyContent: 'center' }}
                    disabled={busy || Boolean(s.blocked)}
                    title={busy ? 'Training is running' : s.blocked || ''}
                    onClick={() => step(s.run, s.label)}>
              {s.icon} {s.n} · {s.label}
            </button>
            <div style={{ fontSize: 12, color: C.dim, lineHeight: 1.55 }}>
              {s.done && <Pill colour={C.green}>done</Pill>}
              {s.blocked && !busy && <Pill colour={C.faint}>{s.blocked}</Pill>}
              <div style={{ marginTop: s.done || s.blocked ? 4 : 0 }}>{s.what}</div>
            </div>
          </div>
        ))}
      </Section>

      <FoldResults training={t} />

      {t?.error && (
        <Section icon={<AlertTriangle size={15} />} title="The run failed"
                 why="Kept rather than cleared, because a run that died halfway and a
                   run that found nothing look identical once the error is
                   gone.">
          <pre style={{
            fontSize: 11, color: C.red, whiteSpace: 'pre-wrap', margin: 0,
            background: C.sunk, padding: 10, borderRadius: 6,
          }}>{t.error}</pre>
        </Section>
      )}
    </>
  );
}

// ── the remaining tabs ────────────────────────────────────────────────────

function CharterTab({ agent, busy, onChanged }) {
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
      setError(apiError(e));
    }
  };

  if (!current) {
    return (
      <Section icon={<ShieldCheck size={15} />} title="No charter yet"
               why="The charter is the strategy the agent says it follows, written out
                 of the trained weights. It is what you approve and what the 0.30
                 adherence term is scored against on every live trade.">
        <p style={{ color: C.dim, fontSize: 12, margin: 0 }}>
          Train the agent, press Collect, then Distil charter on the Training tab.
        </p>
      </Section>
    );
  }

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <h3 style={{ margin: 0 }}>v{current.version}</h3>
        <Pill colour={CHARTER_COLOUR[current.status] || C.dim}>{current.status}</Pill>
        <code style={{ color: C.faint, fontSize: 11 }}>{current.content_hash}</code>
      </div>

      {current.status === 'PROPOSED' && (
        <div style={{
          background: `${C.amber}22`, border: `1px solid ${C.amber}66`, borderRadius: 6,
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
              <td style={{ color: C.dim }}>
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
      <ul style={{ fontSize: 12, color: C.dim }}>
        {(current.tiers || []).map((t) => (
          <li key={t.name}>
            <b>{t.name}</b>: {t.min_gates} of {t.gates.length}
            {t.required?.length ? `, always ${t.required.join(', ')}` : ''}
            {current.entry_tier === t.name ? ' — the entry tier' : ''}
          </li>
        ))}
      </ul>
      <p style={{ fontSize: 12, color: C.dim }}>
        Exit: {current.exit_rule || '—'}<br />
        Adherence floor {current.adherence_floor} — below it for{' '}
        {current.drift_trades} consecutive trades and the agent is suspended from
        live and you are notified, with the gates it has been skipping named.
      </p>

      {!hasReport && (
        <div style={{ color: C.amber, fontSize: 12, marginBottom: 8 }}>
          <AlertTriangle size={13} /> No validation report yet. Approve is
          disabled until the gate has run — otherwise you would be signing off an
          intention rather than a measurement.
        </div>
      )}

      {error && <div style={{ color: C.red, fontSize: 12 }}>{error}</div>}

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 8 }}>
        {current.status === 'DRAFT' && (
          <button className="btn" disabled={busy}
                  onClick={act(() => rl.propose(agent.agent_id))}>
            <ChevronRight size={14} /> Propose (makes it backtestable)
          </button>
        )}
        {current.status === 'PROPOSED' && (
          <button className="btn btn-primary" disabled={!hasReport || busy}
                  title={!hasReport ? 'Run the gate first' : ''}
                  onClick={act(() => rl.approveCharter(agent.agent_id))}>
            <ShieldCheck size={14} /> Approve
          </button>
        )}
        {['DRAFT', 'PROPOSED'].includes(current.status) && (
          <>
            <input value={reason} onChange={(e) => setReason(e.target.value)}
                   placeholder="reason (required to reject)" style={{ flex: 1 }} />
            <button className="btn" disabled={!reason.trim() || busy}
                    onClick={act(() => rl.rejectCharter(agent.agent_id, { reason }))}>
              <X size={14} /> Reject
            </button>
          </>
        )}
      </div>

      {charters.length > 1 && (
        <details style={{ marginTop: 12 }}>
          <summary style={{ cursor: 'pointer' }}>
            Earlier versions ({charters.length - 1})
          </summary>
          <p style={{ color: C.dim, fontSize: 12 }}>
            Kept rather than overwritten, so every historical run can still be
            reproduced against the charter it actually ran under.
          </p>
          {charters.filter((c) => c !== current).map((c) => (
            <div key={c.version} style={{ fontSize: 12, marginBottom: 6 }}>
              <Pill colour={CHARTER_COLOUR[c.status] || C.dim}>{c.status}</Pill>{' '}
              v{c.version} <code style={{ color: C.faint }}>{c.content_hash}</code>
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
    return (
      <Section icon={<FlaskConical size={15} />} title="The gate has not run yet"
               why="Run it from the Training tab, after a charter has been distilled.">
        <p style={{ color: C.dim, fontSize: 12, margin: 0 }}>
          Thirteen checks, each with the number it measured and the bar it had to
          clear. Nothing can be approved before this exists.
        </p>
      </Section>
    );
  }
  return (
    <div>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 10 }}>
        <Pill colour={v.passed ? C.green : C.red}>
          {v.passed ? 'PASSED' : 'REJECTED'}
        </Pill>
        <span style={{ fontSize: 12, color: C.dim }}>
          {v.trades} trades · <Num value={v.expectancy_r} />R ·{' '}
          spec <code>{v.spec_hash}</code> · {v.optimiser}
        </span>
      </div>
      {!v.passed && (
        <p style={{ fontSize: 12, color: C.dim }}>
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
        <p style={{ fontSize: 12, color: C.dim }}>
          Symbols attempted: {v.symbols_attempted.join(', ')} — named whether or
          not they worked, because picking the ones that looked good afterwards is
          survivorship bias in the experiment.
        </p>
      )}
    </div>
  );
}

function ProposalsTab({ agent, busy, onChanged }) {
  const [reason, setReason] = useState({});
  const [error, setError] = useState('');
  const open = (agent.proposals || []).filter((p) => p.status === 'OPEN');
  const decided = (agent.proposals || []).filter((p) => p.status !== 'OPEN');

  const act = (fn) => async () => {
    setError('');
    try { await fn(); onChanged(); } catch (e) {
      setError(apiError(e));
    }
  };

  return (
    <div>
      <p style={{ color: C.dim, fontSize: 12, lineHeight: 1.6 }}>
        The agent cannot edit an approved charter. When continued training finds
        something the charter does not express, it proposes a change — and a
        proposal must already beat the approved charter on the same gate, the same
        seeds and the same folds before it can even be submitted. You should never
        be asked to approve something that has not earned it. While a proposal is
        open, live keeps trading the approved charter.
      </p>
      {error && <div style={{ color: C.red, fontSize: 12 }}>{error}</div>}
      {!open.length && <p style={{ color: C.faint }}>No open proposals.</p>}
      {open.map((p) => (
        <div key={p.id} className="card" style={{ padding: 12, marginBottom: 10 }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <Pill colour={C.amber}>{p.kind}</Pill>
            <span style={{ fontSize: 12, color: C.dim }}>
              from v{p.from_version} · <code>{p.diff_hash}</code>
            </span>
          </div>
          <p style={{ fontSize: 13 }}>{p.why}</p>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, fontSize: 12 }}>
            <div>
              <b>Approved</b>
              <div>expectancy <Num value={p.evidence_approved?.expectancy_r} /></div>
              <div>PBO {p.evidence_approved?.pbo ?? '—'}</div>
              <div>deflated Sharpe{' '}
                <Num value={p.evidence_approved?.deflated_sharpe} digits={3} /></div>
            </div>
            <div>
              <b>Proposed</b>
              <div>expectancy <Num value={p.evidence_proposed?.expectancy_r} /></div>
              <div>PBO {p.evidence_proposed?.pbo ?? '—'}</div>
              <div>deflated Sharpe{' '}
                <Num value={p.evidence_proposed?.deflated_sharpe} digits={3} /></div>
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
            <button className="btn btn-primary" disabled={busy}
                    onClick={act(() => rl.approveProposal(p.id))}>
              <Check size={14} /> Approve (bumps to v{p.from_version + 1})
            </button>
            <input value={reason[p.id] || ''} style={{ flex: 1 }}
                   onChange={(e) => setReason({ ...reason, [p.id]: e.target.value })}
                   placeholder="reason (required, and it blocks this exact change coming back)" />
            <button className="btn" disabled={!(reason[p.id] || '').trim() || busy}
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
              <Pill colour={p.status === 'ACCEPTED' ? C.green : C.dim}>{p.status}</Pill>{' '}
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

function LiveTab({ agent }) {
  const { data } = useQuery({
    queryKey: qk('rl-adherence', agent.agent_id),
    queryFn: () => rl.adherence(agent.agent_id, 180).then((r) => r.data),
  });
  const points = data?.points || [];
  const floor = data?.floor ?? 0.6;

  return (
    <div>
      <p style={{ color: C.dim, fontSize: 12, lineHeight: 1.6 }}>
        One point per closed live trade, scored against the <b>approved</b>{' '}
        charter — never against whatever the policy currently prefers. Per trade
        rather than per bar, because adherence credited per bar would make
        &ldquo;never trade&rdquo; the highest-adherence policy and this chart
        would never move.
      </p>
      {data?.suspended && (
        <div style={{
          background: `${C.red}22`, border: `1px solid ${C.red}66`, borderRadius: 6,
          padding: 10, fontSize: 12, marginBottom: 10,
        }}>
          <Ban size={13} /> Suspended from live. {data.suspended_reason}
          <div style={{ marginTop: 6 }}>
            It does not fall back to trading unsupervised — the slot stops taking
            its signals.
          </div>
        </div>
      )}
      {!points.length && <p style={{ color: C.faint }}>No live trades recorded yet.</p>}
      {points.length > 0 && (
        <svg viewBox={`0 0 ${Math.max(200, points.length * 4)} 100`}
             style={{ width: '100%', height: 140, background: C.sunk, borderRadius: 6 }}>
          <line x1="0" x2={points.length * 4} y1={100 - floor * 100} y2={100 - floor * 100}
                stroke={C.amber} strokeDasharray="3 3" strokeWidth="1" />
          {points.map((p, i) => (
            <circle key={i} cx={i * 4 + 2} cy={100 - (p.adherence || 0) * 100} r="1.6"
                    fill={p.tripped ? C.red : p.adherence >= floor ? C.green : C.amber} />
          ))}
        </svg>
      )}
    </div>
  );
}

// ── overview ──────────────────────────────────────────────────────────────

function OverviewTab({ agent, busy, step }) {
  const s = agent.spec || {};
  return (
    <>
      <Section icon={<Brain size={15} />} title="What this agent is"
               why="Written by the backend from the frozen spec, so it describes what
                 the engine will actually do rather than what this screen believes
                 about it.">
        <LearningSummary learning={agent.learning} />
      </Section>

      <Section icon={<Info size={15} />} title="Frozen spec">
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 4 }}>
          <KV k="Symbols">{(s.symbols || []).join(', ') || '—'}</KV>
          <KV k="Entry timeframe">{s.timeframe}</KV>
          <KV k="Base strategy">
            {s.base_strategy || <span style={{ color: C.faint }}>none — finds its own</span>}
          </KV>
          <KV k="Supervised director">
            {s.director ? `yes, ${s.director_horizon}-bar horizon`
              : <span style={{ color: C.faint }}>no</span>}
          </KV>
          <KV k="Chooses stop">
            {s.risk_head ? `${(s.stop_atr_choices || []).join(', ')} × ATR`
              : <span style={{ color: C.faint }}>no — the slot's stop applies</span>}
          </KV>
          <KV k="Chooses target">
            {s.risk_head ? (s.rr_choices || []).map((r) => `1:${r}`).join(', ')
              : <span style={{ color: C.faint }}>no</span>}
          </KV>
          <KV k="Exit head">
            {s.exit_head ? (s.exit_timeframe ? `yes, on ${s.exit_timeframe}` : 'yes')
              : <span style={{ color: C.faint }}>no</span>}
          </KV>
          <KV k="Higher timeframes">
            {(s.htf || []).join(', ')
              || <span style={{ color: C.faint }}>none — entry timeframe only</span>}
          </KV>
          <KV k="Observation columns">{(s.features || []).length}</KV>
          <KV k="Downside penalty">
            {s.downside_penalty}{Number(s.downside_penalty) > 1 ? '× on losses' : ' (symmetric)'}
          </KV>
          <KV k="Seeds">{(s.seeds || []).join(', ')}</KV>
          <KV k="Reward split">
            {Math.round((s.money_weight ?? 0.7) * 100)}% money /{' '}
            {Math.round((s.adherence_weight ?? 0.3) * 100)}% adherence
          </KV>
          <KV k="Spec hash"><code style={{ fontSize: 11 }}>{agent.spec_hash}</code></KV>
          <KV k="Policy hash">
            {agent.policy_hash
              ? <code style={{ fontSize: 11 }}>{agent.policy_hash}</code>
              : <span style={{ color: C.faint }}>not collected yet</span>}
          </KV>
        </div>
        {s.notes && (
          <p style={{ fontSize: 12, color: C.dim, marginTop: 10 }}>{s.notes}</p>
        )}
      </Section>

      <TrainingMonitor agent={agent} busy={busy}
                       onCancel={() => step(() => rl.cancel(agent.agent_id), 'Cancelling')} />
    </>
  );
}

// ── page ──────────────────────────────────────────────────────────────────

export default function Agents() {
  const qc = useQueryClient();
  // Null means "whichever is first", resolved below. Stored as null rather
  // than written by an effect: setting state inside an effect to pick a
  // default cascades a render for something that is a pure derivation.
  const [picked, setPicked] = useState(null);
  const [tab, setTab] = useState('Overview');
  const [creating, setCreating] = useState(false);
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
    // 2s while a run is in flight: the event log is the point of the screen
    // during training, and a 15s poll makes it feel broken.
    refetchInterval: (q) => (q.state.data?.status === 'TRAINING' ? 2000 : false),
  });

  const refreshAll = () => { refetch(); refetchAgent(); qc.invalidateQueries(); };

  const step = async (fn, label) => {
    setNotice(`${label}…`);
    try {
      const r = await fn();
      setNotice(r?.data?.note || `${label} done`);
      refreshAll();
    } catch (e) {
      setNotice(apiError(e));
    }
  };

  // Every control is off while a run is in flight. A second run would race
  // the first over the same progress file and the same policy row, and the
  // loser writes nonsense into whichever the admin reads next.
  //
  // "In flight" is NOT the same as `status === 'TRAINING'`. That column is
  // written when a job is submitted and only ever written back by Collect,
  // and the worker is a child of the API process - so a restart leaves the
  // row saying TRAINING with nothing running and every control disabled for
  // a run that ended hours ago. Liveness comes from the run itself: the
  // queue still holding its future, or the progress file still being
  // written to.
  const claimsTraining = agent?.status === 'TRAINING';
  const alive = Boolean(agent?.run?.looks_alive);
  const busy = claimsTraining && alive;
  // Covers both "the backend restarted under it" and "the worker raised on
  // its first line", which look identical from the status column and need
  // very different things said about them.
  const stuck = claimsTraining && !alive;
  const life = agent ? lifecycle(agent) : null;

  const jump = (key) => setTab(
    { register: 'Overview', data: 'Data', train: 'Training', collect: 'Training',
      charter: 'Charter', gate: 'Validation', approve: 'Charter' }[key] || 'Overview',
  );

  return (
    <div className="page">
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <h1 style={{ margin: 0 }}>Agents</h1>
        {leak && (
          <Pill colour={leak.ok ? C.green : C.red}>
            leak probe {leak.ok ? `clean · ${leak.features} features` : 'FAILING'}
          </Pill>
        )}
        <button className="btn btn-sm" onClick={refreshAll}><RefreshCw size={12} /></button>
      </div>
      <p style={{ color: C.dim, fontSize: 12, maxWidth: 880, lineHeight: 1.6 }}>
        An agent decides <b>whether</b> to take a setup, <b>which way</b>,{' '}
        <b>how far away the stop goes</b> and <b>what to aim at</b>, and — if it
        has an exit head — when to get out. It never sizes a position, never sets
        a risk percentage and never overrides a halt: those numbers are not in its
        observation at all. Nothing reaches a live slot until you have approved the
        strategy it says it follows.
      </p>

      <div style={{
        display: 'grid', gridTemplateColumns: '264px minmax(0, 1fr)', gap: 16,
        alignItems: 'start',
      }}>
        <div className="card" style={{ padding: 8 }}>
          <button className="btn btn-primary btn-sm"
                  style={{ width: '100%', justifyContent: 'center', marginBottom: 8 }}
                  onClick={() => setCreating(true)}>
            <Plus size={13} /> New agent
          </button>
          {(list?.agents || []).map((a) => (
            <button key={a.agent_id}
                    onClick={() => { setPicked(a.agent_id); setCreating(false); }}
                    style={{
                      display: 'block', width: '100%', textAlign: 'left', padding: 8,
                      background: !creating && a.agent_id === selected ? C.raised : 'transparent',
                      border: 0, borderRadius: 6, cursor: 'pointer', color: 'inherit',
                    }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 6 }}>
                <b style={{ fontSize: 13 }}>{a.name}</b>
                <Pill colour={STATUS_COLOUR[a.status] || C.dim}>{a.status}</Pill>
              </div>
              <div style={{ fontSize: 11, color: C.dim }}>
                {a.strategy_id || a.agent_id}
                {a.suspended ? ' · suspended' : ''}
                {a.open_proposals ? ` · ${a.open_proposals} proposal` : ''}
              </div>
            </button>
          ))}
          {!list?.agents?.length && (
            <p style={{ color: C.faint, fontSize: 12, padding: 8 }}>
              No agents yet. Press New agent — the form explains each choice as you
              make it.
            </p>
          )}
        </div>

        <div>
          {creating && (
            <CreatePanel features={feats?.features} onCancel={() => setCreating(false)}
                         onCreated={(a) => {
                           setPicked(a.agent_id); setCreating(false);
                           setTab('Data'); refetch();
                         }} />
          )}
          {!creating && !agent && <p style={{ color: C.faint }}>Select an agent.</p>}
          {!creating && agent && (
            <>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                <h2 style={{ margin: 0 }}>{agent.name}</h2>
                <Pill colour={STATUS_COLOUR[agent.status] || C.dim}>{agent.status}</Pill>
                {agent.learning?.mode && (
                  <Pill colour={C.purple}>mode {agent.learning.mode}</Pill>
                )}
                {agent.spec?.director && <Pill colour={C.green}>supervised + RL</Pill>}
                {agent.suspended && <Pill colour={C.red}>suspended</Pill>}
                <button className="btn btn-sm" disabled={busy}
                        title={busy ? 'Training is running' : 'Same spec, new symbols'}
                        onClick={() => step(
                          () => rl.clone(agent.agent_id, { symbols: agent.spec.symbols }),
                          'Cloning')}>
                  <Copy size={12} /> Clone
                </button>
                {agent.suspended && (
                  <button className="btn btn-sm" disabled={busy}
                          onClick={() => step(() => rl.unsuspend(agent.agent_id), 'Re-enabling')}>
                    Re-enable
                  </button>
                )}
                {/* There was no way to remove an agent from any screen, which
                    is how the list fills with half-started drafts. The server
                    refuses it for an APPROVED charter and while a run is
                    alive; the confirm is here because this cannot be undone. */}
                <button className="btn btn-sm" disabled={busy}
                        style={{ marginLeft: 'auto', color: C.red }}
                        title={busy ? 'Training is running'
                          : 'Delete this agent and its files'}
                        onClick={() => {
                          const ok = window.confirm(
                            `Delete "${agent.name}" and everything it has `
                            + 'produced? Its charters, proposals and training '
                            + 'artefacts go with it. This cannot be undone.');
                          if (!ok) return;
                          step(async () => {
                            const r = await rl.remove(agent.agent_id);
                            setPicked(null);
                            return r;
                          }, 'Deleting');
                        }}>
                  <Trash2 size={12} /> Delete
                </button>
              </div>

              {busy && (
                <div style={{
                  background: `${C.blue}18`, border: `1px solid ${C.blue}55`, borderRadius: 6,
                  padding: '8px 10px', fontSize: 12, margin: '10px 0',
                }}>
                  <Loader2 size={13} className="spin" /> <b>Training.</b> Every
                  control on this page is disabled until it finishes - a second run
                  would race this one over the same files. Watch it on the Training
                  tab; you can cancel from there.
                  {agent.progress?.age_s !== undefined && (
                    <> Last reported <b>{Math.round(agent.progress.age_s)}s</b> ago.</>
                  )}
                </div>
              )}

              {stuck && (
                <div style={{
                  background: `${C.amber}18`, border: `1px solid ${C.amber}66`,
                  borderRadius: 6, padding: '10px 12px', fontSize: 12, margin: '10px 0',
                  lineHeight: 1.6,
                }}>
                  <AlertTriangle size={13} color={C.amber} />{' '}
                  <b>{agent.run?.failed
                    ? 'The run started and then failed.'
                    : 'This agent says it is training, but nothing is running.'}</b>
                  {agent.run?.failed && (agent.run?.error || agent.progress?.error) && (
                    <pre style={{
                      whiteSpace: 'pre-wrap', margin: '6px 0', padding: 8,
                      background: C.sunk, border: `1px solid ${C.red}55`,
                      borderRadius: 6, color: C.red, fontSize: 11.5,
                    }}>{agent.run?.error || agent.progress?.error}</pre>
                  )}
                  <div style={{ marginTop: 4 }}>
                    {agent.run?.failed ? (
                      <>The worker raised before it finished. Its data sources and
                        everything it managed to say are on the Training tab.</>
                    ) : (
                      <>The training worker is a child of the backend process, so
                        restarting the backend ends the run. The status column is
                        only ever written back by Collect, which is why it is still
                        stuck on TRAINING.</>
                    )}
                    {agent.progress?.age_s !== undefined && (
                      <> The run last wrote to its progress file{' '}
                        <b>{Math.round(agent.progress.age_s / 60)} minutes</b> ago.
                      </>
                    )}
                    {agent.progress?.pct !== undefined && (
                      <> It had reached <b>{Number(agent.progress.pct).toFixed(1)}%</b>
                        {agent.progress.jobs_done !== undefined
                          && ` (job ${agent.progress.jobs_done} of `
                            + `${agent.progress.total_jobs})`}.
                      </>
                    )}
                  </div>
                  <button className="btn btn-sm" style={{ marginTop: 8 }}
                          onClick={() => step(
                            () => rl.resetTraining(agent.agent_id), 'Resetting')}>
                    <RefreshCw size={12} /> Clear this and let me use the page
                  </button>
                  <div style={{ color: C.dim, marginTop: 6, fontSize: 11 }}>
                    If the run finished before the restart its policy is still on
                    disk and this lands on TRAINED - press Collect next. If it did
                    not, it goes back to DRAFT and has to be run again.
                  </div>
                </div>
              )}

              <LifecycleRail life={life} onJump={jump} />
              {notice && (
                <div style={{
                  fontSize: 12, color: C.dim, marginBottom: 8, background: C.sunk,
                  border: `1px solid ${C.line}`, borderRadius: 6, padding: '6px 10px',
                }}>{notice}</div>
              )}

              <div style={{ display: 'flex', gap: 6, margin: '10px 0', flexWrap: 'wrap' }}>
                {TABS.map((t) => (
                  <button key={t} className={`btn btn-sm ${tab === t ? 'btn-primary' : ''}`}
                          onClick={() => setTab(t)}>
                    {t}
                    {t === 'Proposals' && agent.proposals?.some((p) => p.status === 'OPEN')
                      && ' •'}
                  </button>
                ))}
              </div>

              {tab === 'Overview' && <OverviewTab agent={agent} busy={busy} step={step} />}
              {tab === 'Data' && (
                <DataTab agent={agent} busy={busy} onStarted={() => {
                  setTab('Training'); refreshAll();
                }} />
              )}
              {tab === 'Training' && (
                <TrainingTab agent={agent} busy={busy} life={life} step={step} />
              )}
              {tab === 'Charter' && (
                <CharterTab agent={agent} busy={busy} onChanged={refreshAll} />
              )}
              {tab === 'Validation' && <ValidationTab agent={agent} />}
              {tab === 'Proposals' && (
                <ProposalsTab agent={agent} busy={busy} onChanged={refreshAll} />
              )}
              {tab === 'Live' && <LiveTab agent={agent} />}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
