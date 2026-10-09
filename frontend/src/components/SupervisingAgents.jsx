/**
 * frontend/src/components/SupervisingAgents.jsx
 *
 * Two controls that belong together on the Backtester, because they answer the
 * two questions an RL agent raises about a run.
 *
 * `SupervisingAgents` — which approved agents may close or tighten this run's
 * trades early. The run then reports three columns from ONE bar sequence:
 * unsupervised, supervised, and the difference. Only agents with an APPROVED
 * charter appear, because only those are registered.
 *
 * `InSampleGuard` — the banner for whether the window you picked is inside
 * what the selected agent trained on. The verdict itself comes from
 * `useOverlap` in hooks/useAgents.js: the Backtester needs it for the banner
 * AND for whether Run is clickable, so it is a query read twice rather than a
 * component reporting state back through a callback. A full overlap DISABLES Run rather than warning: a warning
 * you can click past is a warning that gets clicked past, and an in-sample
 * backtest of a trained agent is the most convincing wrong number this system
 * can produce.
 */

import { AlertTriangle, Ban, CheckCircle2 } from 'lucide-react';
import { useApprovedAgents } from '../hooks/useAgents';

export function SupervisingAgents({ value = [], onChange, symbol }) {
  const { data: agents = [] } = useApprovedAgents();
  if (!agents.length) return null;

  const toggle = (sid) => onChange(
    value.includes(sid) ? value.filter((s) => s !== sid) : [...value, sid]);

  return (
    <div style={{ marginTop: 8 }}>
      <label style={{ fontSize: 12, fontWeight: 600 }}>Supervising agents</label>
      <p style={{ color: '#8b949e', fontSize: 11, margin: '2px 0 6px' }}>
        An agent selected here never opens a trade. It watches the positions this
        strategy opens and may only close them or tighten their stop — and the
        strategy&rsquo;s own exit is asked first and wins, so a measured rule is
        never overridden. The run reports unsupervised, supervised and the
        difference side by side.
      </p>
      {agents.map((a) => (
        <label key={a.strategy_id} style={{ display: 'block', fontSize: 12 }}>
          <input type="checkbox" checked={value.includes(a.strategy_id)}
                 onChange={() => toggle(a.strategy_id)} />
          {' '}<code>{a.strategy_id}</code>
          {a.suspended && (
            <span style={{ color: '#f85149' }}> — suspended, it will not act</span>
          )}
          {a.expectancy_r !== null && a.expectancy_r !== undefined && (
            <span style={{ color: '#8b949e' }}>
              {' '}· validated at {Number(a.expectancy_r).toFixed(4)}R over {a.trades} trades
            </span>
          )}
        </label>
      ))}
      {symbol && value.length > 0 && (
        <p style={{ color: '#8b949e', fontSize: 11 }}>
          An agent only acts on the strategies and symbols its own frozen spec
          permits — one trained on Crash 1000 will not touch {symbol} unless it
          was trained on it.
        </p>
      )}
    </div>
  );
}

/** The banner. Purely presentational - the verdict comes from `useOverlap`. */
export function InSampleGuard({ state }) {
  if (!state) return null;
  const colour = state.runnable
    ? (state.state === 'clean' ? '#3fb950' : '#d29922')
    : '#f85149';
  const Icon = state.runnable
    ? (state.state === 'clean' ? CheckCircle2 : AlertTriangle)
    : Ban;

  return (
    <div style={{
      background: `${colour}18`, border: `1px solid ${colour}55`, borderRadius: 6,
      padding: 8, fontSize: 12, marginTop: 8, color: colour,
    }}>
      <Icon size={13} /> {state.message}
      {!state.runnable && (
        <div style={{ color: '#8b949e', marginTop: 4 }}>
          Run is disabled. Backtesting an agent on the bars it trained on does not
          measure anything.
        </div>
      )}
    </div>
  );
}

/** The three-column result, when a run had supervisors. */
export function SupervisionReport({ supervision }) {
  if (!supervision) return null;
  if (!supervision.available) {
    return (
      <div className="card" style={{ padding: 12, fontSize: 12, color: '#d29922' }}>
        <AlertTriangle size={13} /> {supervision.note}
        {supervision.unavailable?.length > 0 && (
          <ul>{supervision.unavailable.map((u) => <li key={u}>{u}</li>)}</ul>
        )}
      </div>
    );
  }
  const row = (label, key, digits = 4) => (
    <tr>
      <td style={{ fontWeight: 600 }}>{label}</td>
      <td style={{ textAlign: 'right' }}>{fmt(supervision.unsupervised[key], digits)}</td>
      <td style={{ textAlign: 'right' }}>{fmt(supervision.supervised[key], digits)}</td>
      <td style={{ textAlign: 'right' }}>{diff(supervision.unsupervised[key],
                                              supervision.supervised[key], digits)}</td>
    </tr>
  );

  return (
    <div className="card" style={{ padding: 12, marginTop: 12 }}>
      <h3 style={{ marginTop: 0 }}>Supervision</h3>
      <p style={{ color: '#8b949e', fontSize: 12, marginTop: -6 }}>{supervision.note}</p>
      <table className="table" style={{ fontSize: 12 }}>
        <thead>
          <tr><th /><th style={{ textAlign: 'right' }}>Unsupervised</th>
            <th style={{ textAlign: 'right' }}>Supervised</th>
            <th style={{ textAlign: 'right' }}>Difference</th></tr>
        </thead>
        <tbody>
          {row('Trades', 'trades', 0)}
          {row('Expectancy (R)', 'expectancy_r')}
          {row('P&L ($)', 'total_pnl', 2)}
          {row('Win rate', 'win_rate', 3)}
          {row('Max drawdown (%)', 'max_drawdown_pct', 2)}
        </tbody>
      </table>
      <p style={{ fontSize: 12, color: '#8b949e' }}>
        {supervision.closed_early} exits were the agent&rsquo;s, averaging{' '}
        {fmt(supervision.mean_r_closed_early, 4)}R against{' '}
        {fmt(supervision.mean_r_let_run, 4)}R for the trades it let run.
      </p>
      {supervision.cutting_winners && (
        <div style={{ color: '#f85149', fontSize: 12 }}>
          <AlertTriangle size={13} /> It is cutting winners: the trades it closed
          early averaged better than the ones it let run. That is the one failure
          mode a supervisor has, and this project has measured it twice before.
        </div>
      )}
      {!supervision.measurable && (
        <div style={{ color: '#d29922', fontSize: 12 }}>
          <AlertTriangle size={13} /> Fewer than 100 supervised exits — not enough
          to conclude anything either way.
        </div>
      )}
      {supervision.unavailable?.length > 0 && (
        <div style={{ color: '#d29922', fontSize: 12 }}>
          Not used: {supervision.unavailable.join('; ')}
        </div>
      )}
    </div>
  );
}

function fmt(v, digits) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '—';
  return Number(v).toFixed(digits);
}

function diff(a, b, digits) {
  if ([a, b].some((v) => v === null || v === undefined || Number.isNaN(Number(v)))) return '—';
  const d = Number(b) - Number(a);
  const colour = d > 0 ? '#3fb950' : d < 0 ? '#f85149' : '#8b949e';
  return <span style={{ color: colour }}>{d > 0 ? '+' : ''}{d.toFixed(digits)}</span>;
}
