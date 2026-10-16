import React, { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Download, RefreshCw } from 'lucide-react';
import {
  getAftermath, runAftermath, exportJournal, exportSignals,
  runSignalOutcomes, getGateCost,
} from '../services/api';
import { downloadResult } from '../utils/resultCache';

/**
 * The journal as training material.
 *
 * Two questions no other screen answers:
 *   1. When you or an agent closed a trade by hand, what did it go on to do?
 *      (a stop or target exit needs no second look - the plan decided it)
 *   2. When a gate SKIPPED a signal, would that signal have won? That is the
 *      only way to see what each filter is costing.
 *
 * Both are measured by a daily job. "Measure now" runs it immediately, and a
 * larger "days" reaches further back - that is also how old journal entries
 * get backfilled.
 *
 * Symbol and strategy are free text on purpose: Deriv names such as
 * "US Tech 100" are in no fixed list, and a dropdown would hide them.
 */

const VERDICT_COLOUR = {
  LEFT_MONEY: 'var(--red)', SAVED_LOSS: 'var(--green)', FAIR: 'var(--text-secondary)',
  COSTING_MONEY: 'var(--red)', SAVING_MONEY: 'var(--green)', NEUTRAL: 'var(--text-secondary)',
};

const r = (v, d = 2) => (v === null || v === undefined ? '—' : `${v >= 0 ? '+' : ''}${Number(v).toFixed(d)}R`);
const mins = (m) => {
  if (m === null || m === undefined) return '—';
  if (m < 60) return `${m}m`;
  if (m < 1440) return `${(m / 60).toFixed(1)}h`;
  return `${(m / 1440).toFixed(1)}d`;
};

function Tile({ label, value, colour, hint }) {
  return (
    <div title={hint} style={{
      background: 'var(--bg-tertiary)', border: '1px solid var(--border)',
      borderRadius: 'var(--radius-sm)', padding: '10px 12px', minWidth: 130,
    }}>
      <div style={{ fontSize: '0.72rem', color: 'var(--text-secondary)' }}>{label}</div>
      <div style={{ fontSize: '1.1rem', fontWeight: 600, color: colour || 'var(--text-primary)' }}>{value}</div>
    </div>
  );
}

const input = {
  padding: '6px 10px', fontSize: '0.85rem', background: 'var(--bg-tertiary)',
  border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', color: 'var(--text-primary)',
};

export default function LearningData() {
  const qc = useQueryClient();
  const [symbol, setSymbol] = useState('');
  const [strategy, setStrategy] = useState('');
  const [days, setDays] = useState(90);
  const [signalScope, setSignalScope] = useState('ALL');
  const [busy, setBusy] = useState('');
  const [notice, setNotice] = useState('');

  const params = {
    ...(symbol.trim() ? { symbol: symbol.trim() } : {}),
    ...(strategy.trim() ? { strategy_id: strategy.trim() } : {}),
    days,
  };

  const after = useQuery({
    queryKey: ['aftermath', params],
    queryFn: () => getAftermath({ ...params, limit: 200 }).then((x) => x.data),
  });
  const gates = useQuery({
    queryKey: ['gate-cost', params],
    queryFn: () => getGateCost(params).then((x) => x.data),
  });

  const say = (e) => e?.response?.data?.detail || e?.message || String(e);

  const measure = async () => {
    setBusy('measure'); setNotice('Fetching bars after every close and every signal - this can take a few minutes the first time.');
    try {
      const [a, s] = await Promise.all([runAftermath({ days }), runSignalOutcomes({ days })]);
      setNotice(`Measured ${a.data.measured} hand-closed trade(s) and ${s.data.measured} signal(s). `
        + `${a.data.already_complete + s.data.already_complete} were already finished; `
        + `${a.data.skipped_planned} trade(s) hit their stop or target and need no second look.`);
      qc.invalidateQueries({ queryKey: ['aftermath'] });
      qc.invalidateQueries({ queryKey: ['gate-cost'] });
    } catch (e) {
      setNotice(`Measuring failed: ${say(e)}`);
    } finally { setBusy(''); }
  };

  const stamp = () => new Date().toISOString().slice(0, 16).replace(/[:T]/g, '');
  const tag = () => [symbol.trim(), strategy.trim()].filter(Boolean).join('_').replace(/\s+/g, '-') || 'all';

  const downloadJournal = async () => {
    setBusy('journal');
    try {
      const { data } = await exportJournal(params);
      downloadResult(data, `journal_${tag()}_${days}d_${stamp()}.json`);
      setNotice(data.note || `Exported ${data.counts.trades} trade(s), ${data.counts.with_aftermath} with a post-close measurement.`);
    } catch (e) { setNotice(`Export failed: ${say(e)}`); } finally { setBusy(''); }
  };

  const downloadSignals = async () => {
    setBusy('signals');
    try {
      const scope = signalScope === 'ALL' ? {} : { acted_on: signalScope === 'TAKEN' };
      const { data } = await exportSignals({ ...params, ...scope });
      downloadResult(data, `signals_${signalScope.toLowerCase()}_${tag()}_${days}d_${stamp()}.json`);
      setNotice(`Exported ${data.counts.signals} signal(s): ${data.counts.taken} taken, ${data.counts.skipped} skipped`
        + (data.counts.truncated_at_limit ? ' - TRUNCATED at the row limit, narrow the filter.' : '.'));
    } catch (e) { setNotice(`Export failed: ${say(e)}`); } finally { setBusy(''); }
  };

  const sm = after.data?.summary;
  const rows = after.data?.rows || [];
  const gateRows = gates.data?.gates || [];

  return (
    <div className="card">
      <div className="card-header" style={{ flexWrap: 'wrap', gap: 8 }}>
        <span className="card-title">Learning data — what happened after, and what the gates cost</span>
      </div>

      {/* filters + actions */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', margin: '12px 0' }}>
        <input style={input} placeholder="Symbol (any)" value={symbol} onChange={(e) => setSymbol(e.target.value)} />
        <input style={input} placeholder="Strategy (any)" value={strategy} onChange={(e) => setStrategy(e.target.value)} />
        <select style={input} value={days} onChange={(e) => setDays(Number(e.target.value))}>
          {[7, 30, 90, 180, 365, 730].map((d) => <option key={d} value={d}>Last {d} days</option>)}
        </select>
        <button className="btn btn-secondary btn-sm" disabled={!!busy} onClick={measure}
                title="Measure every hand-closed trade and every signal not yet measured. A longer range backfills older history.">
          <RefreshCw size={14} style={{ marginRight: 4 }} className={busy === 'measure' ? 'spin' : undefined} />
          {busy === 'measure' ? 'Measuring…' : 'Measure now'}
        </button>
        <button className="btn btn-primary btn-sm" disabled={!!busy} onClick={downloadJournal}
                title="Every trade in the filter, with the post-close measurement joined on for the ones you or an agent closed.">
          <Download size={14} style={{ marginRight: 4 }} />{busy === 'journal' ? 'Building…' : 'Export trades'}
        </button>
        <select style={input} value={signalScope} onChange={(e) => setSignalScope(e.target.value)}>
          <option value="ALL">All signals</option>
          <option value="TAKEN">Taken only</option>
          <option value="SKIPPED">Skipped only</option>
        </select>
        <button className="btn btn-primary btn-sm" disabled={!!busy} onClick={downloadSignals}
                title="Every signal raised, with why it was skipped and what its own plan would have done.">
          <Download size={14} style={{ marginRight: 4 }} />{busy === 'signals' ? 'Building…' : 'Export signals'}
        </button>
      </div>
      {notice && (
        <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', background: 'var(--bg-tertiary)',
                      border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)',
                      padding: '6px 10px', marginBottom: 12 }}>{notice}</div>
      )}

      {/* 1. hand-closed trades */}
      <h4 style={{ margin: '16px 0 8px' }}>Trades closed by you or an agent</h4>
      <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', margin: '0 0 10px' }}>
        Each one is followed for as long as it could still have been held. A trade that hit its stop or target is left out — the plan decided those.
      </p>
      {after.isLoading ? <p>Loading…</p> : after.isError ? <p style={{ color: 'var(--red)' }}>{say(after.error)}</p> : (
        <>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
            <Tile label="Scored" value={sm?.scored ?? 0} />
            <Tile label="Closed too early" value={sm?.pct_left_money == null ? '—' : `${sm.pct_left_money}%`}
                  colour={sm?.pct_left_money > 50 ? 'var(--red)' : undefined}
                  hint="Share of hand-closes where the trade went on to do materially better." />
            <Tile label="Median R given up" value={r(sm?.median_forgone_r)}
                  hint="How much further the best point went than where it was closed. Negative = the close beat anything after it." />
            <Tile label="Went on to hit TP" value={sm?.reached_tp_after_close ?? 0} />
            <Tile label="Went on to hit SL" value={sm?.reached_sl_after_close ?? 0} />
            <Tile label="Still watching" value={sm?.still_watching ?? 0}
                  hint="Their holding window has not elapsed yet; these are provisional." />
          </div>
          <div className="table-wrapper">
            <table>
              <thead>
                <tr>
                  <th>Closed</th><th>Symbol</th><th>Strategy</th><th>Dir</th><th>By</th>
                  <th>Closed at</th><th>Best after</th><th>When</th><th>TP?</th><th>SL?</th>
                  <th>Given up</th><th>Verdict</th>
                </tr>
              </thead>
              <tbody>
                {rows.length ? rows.map((x) => (
                  <tr key={x.trade_id} title={x.note || ''}>
                    <td>{x.closed_at ? x.closed_at.slice(0, 16).replace('T', ' ') : '—'}</td>
                    <td><strong>{x.symbol}</strong></td>
                    <td>{x.strategy_id}</td>
                    <td>{x.direction}</td>
                    <td>{x.closed_by}</td>
                    <td>{r(x.closed_r)}</td>
                    <td>{r(x.best_r)}</td>
                    <td>{mins(x.best_minutes)}</td>
                    <td>{x.hit_tp ? `yes, ${mins(x.tp_minutes)}` : 'no'}</td>
                    <td>{x.hit_sl ? `yes, ${mins(x.sl_minutes)}` : 'no'}</td>
                    <td>{r(x.forgone_r)}</td>
                    <td style={{ color: VERDICT_COLOUR[x.verdict], fontWeight: 600 }}>
                      {x.verdict}{!x.watch_complete ? ' (provisional)' : ''}
                    </td>
                  </tr>
                )) : (
                  <tr><td colSpan={12} style={{ textAlign: 'center', padding: '30px 0' }}>
                    Nothing measured in this range yet. Press "Measure now".
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>
        </>
      )}

      {/* 2. what each gate cost */}
      <h4 style={{ margin: '24px 0 8px' }}>What each gate cost</h4>
      <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', margin: '0 0 10px' }}>
        Every signal a gate skipped, scored against its own entry, stop and first target. Positive means the skipped signals would have made money — the gate is costing you.
      </p>
      {gates.isLoading ? <p>Loading…</p> : gates.isError ? <p style={{ color: 'var(--red)' }}>{say(gates.error)}</p> : (
        <>
          <div className="table-wrapper">
            <table>
              <thead>
                <tr><th>Gate</th><th>Skipped</th><th>Would win</th><th>Would lose</th>
                  <th>Win rate</th><th>Net</th><th>Per signal</th><th>Verdict</th></tr>
              </thead>
              <tbody>
                {gateRows.length ? gateRows.map((g) => (
                  <tr key={g.gate}>
                    <td><strong>{g.gate}</strong></td>
                    <td>{g.skipped}</td><td>{g.would_win}</td><td>{g.would_lose}</td>
                    <td>{g.win_rate == null ? '—' : `${g.win_rate}%`}</td>
                    <td style={{ color: VERDICT_COLOUR[g.verdict], fontWeight: 600 }}>{r(g.net_r)}</td>
                    <td>{r(g.mean_r, 3)}</td>
                    <td style={{ color: VERDICT_COLOUR[g.verdict] }}>{g.verdict}</td>
                  </tr>
                )) : (
                  <tr><td colSpan={8} style={{ textAlign: 'center', padding: '30px 0' }}>
                    No skipped signals measured in this range yet.
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>
          {gates.data?.taken_signals?.n > 0 && (
            <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
              For comparison, the {gates.data.taken_signals.n} signals that WERE taken would have made {r(gates.data.taken_signals.net_r)} by their own plan ({r(gates.data.taken_signals.mean_r, 3)} each).
            </p>
          )}
          {gates.data?.assumption && (
            <p style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', fontStyle: 'italic' }}>
              {gates.data.assumption}
            </p>
          )}
        </>
      )}
    </div>
  );
}
