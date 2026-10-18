import { useRef, useState } from 'react';
import { GitCompare, Loader2, Square } from 'lucide-react';
import { getBacktestStatus, getLatestResultSummary, runBacktest } from '../services/api';
import { isAgentStrategy } from '../hooks/useAgents';

/**
 * Run several strategies - rule-based or approved agents - on the SAME symbol,
 * window and risk, one after another, and put the results side by side.
 *
 * One after another because the server runs one backtest per user at a time,
 * and because a comparison is only fair if every leg sees the same bars and
 * the same costs. Each leg is built by the Backtester's own `buildPayload`, so
 * a compared run is exactly the single run you would get by selecting it.
 *
 * Read the result with care: on a short window the ranking moves with luck,
 * so the trade count and the significance verdict matter more than the order.
 */

const POLL_MS = 3000;
const LEG_TIMEOUT_MS = 2 * 60 * 60 * 1000;

const fmt = (v, d = 2) => (v === null || v === undefined || Number.isNaN(Number(v)) ? '—'
  : Number(v).toFixed(d));
const pct = (v) => (v === null || v === undefined ? '—'
  : `${(Math.abs(Number(v)) <= 1 ? Number(v) * 100 : Number(v)).toFixed(1)}%`);

function metricsOf(summary) {
  const r = summary?.report || summary || {};
  const sig = summary?.significance || r.significance || {};
  return {
    // The trade count sits at the TOP of the summary (`total_trades`,
    // `trade_groups_total`), not inside `report`; the summary ships no trade
    // list, so counting `grouped_trades` gave 0 for every leg.
    trades: summary?.trade_groups_total ?? summary?.total_trades ?? r.total_trades ?? null,
    pnl: r.total_pnl,
    pf: r.profit_factor,
    winRate: r.win_rate,
    expectancyR: r.expectancy_r,
    sharpe: r.sharpe_ratio,
    maxDD: r.max_drawdown_pct,
    verdict: sig.verdict || null,
  };
}

export default function CompareStrategies({ options, buildPayload, runServer, disabled, current }) {
  const [picked, setPicked] = useState(() => (current ? [current] : []));
  const [rows, setRows] = useState([]);
  const [running, setRunning] = useState(null);
  const [error, setError] = useState(null);
  const stopRef = useRef(false);

  const toggle = (id) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));

  const waitForLeg = async () => {
    const t0 = Date.now();
    // Right after the POST the status can still describe the PREVIOUS run as
    // complete, so "complete" only counts once this run has been seen running -
    // or after a grace period, for a run quick enough to finish between polls.
    let sawRunning = false;
    for (;;) {
      if (stopRef.current) throw new Error('stopped');
      await new Promise((r) => setTimeout(r, sawRunning ? POLL_MS : 1000));
      const { data } = await getBacktestStatus(runServer);
      const st = data?.status;
      if (st === 'running') { sawRunning = true; continue; }
      const settled = sawRunning || Date.now() - t0 > 12000;
      if (settled && (st === 'complete' || st === 'completed')) return;
      if (settled && (st === 'error' || st === 'failed')) {
        throw new Error(data?.progress?.message || data?.error || 'the run failed');
      }
      if (Date.now() - t0 > LEG_TIMEOUT_MS) throw new Error('timed out');
    }
  };

  const run = async () => {
    stopRef.current = false;
    setError(null);
    setRows([]);
    for (const id of picked) {
      if (stopRef.current) break;
      setRunning(id);
      const label = (options.find(([o]) => o === id) || [id, id])[1];
      try {
        await runBacktest(buildPayload(id), runServer);
        await waitForLeg();
        const { data } = await getLatestResultSummary(runServer);
        setRows((r) => [...r, { id, label, ...metricsOf(data) }]);
      } catch (e) {
        const msg = e?.response?.data?.detail || e?.message || String(e);
        setRows((r) => [...r, { id, label, error: msg }]);
        if (msg === 'stopped') break;
      }
    }
    setRunning(null);
  };

  const best = rows.filter((r) => !r.error && Number(r.trades) >= 30)
    .sort((a, b) => Number(b.expectancyR ?? -1e9) - Number(a.expectancyR ?? -1e9))[0];

  return (
    <div className="card" style={{ display: 'grid', gap: 10 }}>
      <div className="card-header" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <GitCompare size={15} /> <span className="card-title">Compare</span>
      </div>
      <p className="hint" style={{ margin: 0 }}>
        Runs each selected strategy or agent on this page&apos;s symbol, dates and risk, one
        after another, so every one sees the same bars and the same costs. Rank by
        expectancy only where there are 30+ trades; below that the order is mostly luck.
      </p>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {options.map(([id, label]) => (
          <label key={id} className={`chip${picked.includes(id) ? ' is-active' : ''}`}
                 style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: '0.75rem',
                          padding: '3px 8px', border: '1px solid var(--border)', borderRadius: 999,
                          background: picked.includes(id) ? 'var(--bg-tertiary)' : 'transparent',
                          cursor: 'pointer' }}>
            <input type="checkbox" checked={picked.includes(id)} onChange={() => toggle(id)}
                   disabled={Boolean(running)} />
            {isAgentStrategy(id) ? <b>{label}</b> : label}
          </label>
        ))}
      </div>
      <div style={{ display: 'flex', gap: 8 }}>
        <button className="btn btn-primary btn-sm" onClick={run}
                disabled={disabled || Boolean(running) || picked.length < 2}
                title={picked.length < 2 ? 'Pick at least two' : ''}>
          {running ? <Loader2 size={13} className="spin" /> : <GitCompare size={13} />}
          {running ? ` Running ${rows.length + 1} of ${picked.length}…` : ` Compare ${picked.length}`}
        </button>
        {running && (
          <button className="btn btn-secondary btn-sm" onClick={() => { stopRef.current = true; }}>
            <Square size={12} /> Stop after this one
          </button>
        )}
      </div>
      {error && <div style={{ color: 'var(--red)', fontSize: '0.8rem' }}>{error}</div>}
      {rows.length > 0 && (
        <div style={{ overflowX: 'auto' }}>
          <table className="table" style={{ width: '100%', fontSize: '0.78rem' }}>
            <thead>
              <tr>
                <th style={{ textAlign: 'left' }}>Strategy</th><th>Trades</th><th>Net P&amp;L</th>
                <th>PF</th><th>Win rate</th><th>Expectancy R</th><th>Sharpe</th><th>Max DD</th>
                <th>Significance</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} style={r === best ? { background: 'var(--bg-tertiary)' } : undefined}>
                  <td style={{ textAlign: 'left' }}>{r.label}{r === best ? ' ★' : ''}</td>
                  {r.error ? (
                    <td colSpan={8} style={{ color: 'var(--red)' }}>{r.error}</td>
                  ) : (
                    <>
                      <td>{r.trades ?? '—'}</td>
                      <td style={{ color: Number(r.pnl) >= 0 ? 'var(--green)' : 'var(--red)' }}>{fmt(r.pnl)}</td>
                      <td>{fmt(r.pf)}</td>
                      <td>{pct(r.winRate)}</td>
                      <td>{fmt(r.expectancyR, 3)}</td>
                      <td>{fmt(r.sharpe)}</td>
                      <td>{pct(r.maxDD)}</td>
                      <td>{r.verdict || '—'}</td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
          <p className="hint" style={{ marginTop: 6 }}>
            ★ highest expectancy among legs with 30+ trades. The last leg is also loaded in the
            results panel below.
          </p>
        </div>
      )}
    </div>
  );
}
