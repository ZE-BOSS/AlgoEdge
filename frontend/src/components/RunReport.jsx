import { useMemo, useState } from 'react';
import {
  Filter, Gauge, LogOut, Ban, Receipt, ChevronDown, ChevronRight, Info, Activity,
} from 'lucide-react';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell, ReferenceLine } from 'recharts';
import AnalyzeButton from './AnalyzeButton';
import { streakProfile, streaksBy, drawdownSpan, streakR, exitMs, tradeStrategy } from '../utils/summaryEngine';

/**
 * <RunReport> — tasks 7.12 through 7.17, as one component shared by backtest
 * and live (7.17).
 *
 * The backend data for all six panels shipped in Phase 0: `rejection_funnel`,
 * `blocked_signals`, and per-trade `sizing_diagnostics` have been in the
 * response for months with nothing rendering them. This is the missing half.
 *
 * Design rules from the plan's §5.4, applied throughout:
 *   - neutral grey carries structure; a single green/red pair carries sign
 *   - amber means capped or clamped, never "bad"
 *   - blue means informational or provenance
 *   - every derived number shows its inputs and sample size on hover, because
 *     a rate without a denominator is not a measurement (7.11)
 */

const GREY = 'var(--text-muted, #94a3b8)';
const GREEN = 'var(--green, #10b981)';
const RED = 'var(--red, #ef4444)';
const AMBER = 'var(--amber, #f59e0b)';
const BLUE = 'var(--blue, #3b82f6)';

function Panel({ title, icon: Icon, children, hint, right }) {
  const [open, setOpen] = useState(true);
  return (
    <div className="card" style={{ padding: 0, marginBottom: 12 }}>
      <div
        onClick={() => setOpen(o => !o)}
        style={{
          display: 'flex', alignItems: 'center', gap: 8, padding: '10px 14px',
          cursor: 'pointer', borderBottom: open ? '1px solid var(--border)' : 'none',
        }}
      >
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <Icon size={15} style={{ color: GREY }} />
        <span style={{ fontWeight: 600, fontSize: '0.88rem' }}>{title}</span>
        {hint && (
          <span title={hint} style={{ display: 'flex', color: GREY }}>
            <Info size={12} />
          </span>
        )}
        <div style={{ marginLeft: 'auto' }} onClick={e => e.stopPropagation()}>{right}</div>
      </div>
      {open && <div style={{ padding: 14 }}>{children}</div>}
    </div>
  );
}

/** A metric with its formula, inputs and sample size on hover (7.11). */
function Metric({ label, value, formula, n, tone }) {
  const title = [formula, n != null ? `n = ${n}` : null].filter(Boolean).join('\n');
  return (
    <div
      title={title || undefined}
      style={{
        padding: '8px 12px', background: 'var(--bg-tertiary)', borderRadius: 6,
        minWidth: 120, cursor: title ? 'help' : 'default',
      }}
    >
      <div style={{ fontSize: '0.62rem', color: GREY, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
        {label}
      </div>
      <div style={{ fontSize: '1.05rem', fontWeight: 600, fontVariantNumeric: 'tabular-nums', color: tone || 'var(--text-primary)' }}>
        {value}
      </div>
      {n != null && <div style={{ fontSize: '0.6rem', color: GREY }}>n={n}</div>}
    </div>
  );
}

const num = (v, nd = 2) =>
  v == null || Number.isNaN(v) ? '—' : Number(v).toFixed(nd);

/* ── 7.12 Signal Funnel ─────────────────────────────────────────────────── */

function SignalFunnel({ funnel, blocked, trades }) {
  const stages = useMemo(() => {
    const f = funnel || {};
    const evaluated = f.evaluated ?? f.total_evaluated ?? null;
    const strategyOk = f.strategy_approved ?? f.signals_generated ?? null;
    const riskOk = f.risk_approved ?? f.approved ?? null;
    const filled = trades?.length ?? null;
    const closed = trades?.filter(t => t.exit_time)?.length ?? null;
    return [
      { name: 'Evaluated', v: evaluated, color: GREY },
      { name: 'Strategy-approved', v: strategyOk, color: BLUE },
      { name: 'Risk-approved', v: riskOk, color: BLUE },
      { name: 'Filled', v: filled, color: GREEN },
      { name: 'Closed', v: closed, color: GREEN },
    ].filter(s => s.v != null);
  }, [funnel, trades]);

  // Gate breakdown from blocked_signals — what each stage actually lost, and to
  // which rule. This is the half that makes a funnel actionable rather than
  // just descriptive.
  const gates = useMemo(() => {
    const counts = {};
    for (const b of blocked || []) {
      const k = b.reason || b.gate || b.stage || 'unknown';
      counts[k] = (counts[k] || 0) + 1;
    }
    return Object.entries(counts).sort((a, b) => b[1] - a[1]);
  }, [blocked]);

  if (!stages.length && !gates.length) {
    return <div style={{ color: GREY, fontSize: '0.8rem' }}>No funnel data on this run.</div>;
  }

  const top = stages[0]?.v || 1;

  return (
    <>
      {stages.map((s, i) => {
        const prev = i > 0 ? stages[i - 1].v : null;
        const lost = prev != null ? prev - s.v : null;
        return (
          <div key={s.name} style={{ marginBottom: 6 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: '0.75rem' }}>
              <span style={{ width: 140, color: GREY }}>{s.name}</span>
              <div style={{ flex: 1, height: 18, background: 'rgba(255,255,255,0.04)', borderRadius: 3, overflow: 'hidden' }}>
                <div style={{ width: `${Math.max(1, (s.v / top) * 100)}%`, height: '100%', background: s.color, opacity: 0.75 }} />
              </div>
              <span style={{ width: 70, textAlign: 'right', fontVariantNumeric: 'tabular-nums', fontWeight: 600 }}>
                {s.v?.toLocaleString?.() ?? s.v}
              </span>
              <span style={{ width: 92, textAlign: 'right', fontSize: '0.68rem', color: lost ? RED : GREY }}>
                {lost != null && lost > 0 ? `−${lost.toLocaleString()} (${((lost / prev) * 100).toFixed(0)}%)` : ''}
              </span>
            </div>
          </div>
        );
      })}

      {gates.length > 0 && (
        <div style={{ marginTop: 14 }}>
          <div style={{ fontSize: '0.68rem', textTransform: 'uppercase', letterSpacing: '0.04em', color: GREY, marginBottom: 6 }}>
            What blocked them ({(blocked || []).length} recorded)
          </div>
          {gates.map(([gate, n]) => (
            <div key={gate} style={{ display: 'flex', gap: 8, fontSize: '0.73rem', padding: '2px 0' }}>
              <span style={{ flex: 1, color: 'var(--text-secondary)' }}>{gate}</span>
              <span style={{ fontVariantNumeric: 'tabular-nums', color: RED }}>{n}</span>
            </div>
          ))}
        </div>
      )}
    </>
  );
}

/* ── 7.13 Risk Deployment ───────────────────────────────────────────────── */

function RiskDeployment({ trades, target }) {
  const { bins, constraints, stats } = useMemo(() => {
    const vals = [];
    const cons = {};
    for (const t of trades || []) {
      const d = t.sizing_diagnostics || t.sub_trades?.[0]?.sizing_diagnostics;
      if (!d) continue;
      if (d.realised_risk_pct != null) vals.push(+d.realised_risk_pct);
      const c = d.binding_constraint || 'none';
      cons[c] = (cons[c] || 0) + 1;
    }
    if (!vals.length) return { bins: [], constraints: [], stats: null };

    vals.sort((a, b) => a - b);
    const lo = vals[0], hi = vals[vals.length - 1];
    const nb = 20;
    const width = (hi - lo) / nb || 1;
    const counts = new Array(nb).fill(0);
    for (const v of vals) counts[Math.min(nb - 1, Math.floor((v - lo) / width))]++;

    return {
      bins: counts.map((c, i) => ({ from: lo + i * width, to: lo + (i + 1) * width, count: c })),
      constraints: Object.entries(cons).sort((a, b) => b[1] - a[1]),
      stats: {
        n: vals.length,
        median: vals[Math.floor(vals.length / 2)],
        min: lo,
        max: hi,
      },
    };
  }, [trades]);

  if (!stats) {
    return (
      <div style={{ color: GREY, fontSize: '0.8rem' }}>
        No sizing diagnostics on these trades. They ship on runs from Phase 0 onward —
        an older saved run will not have them.
      </div>
    );
  }

  const maxCount = Math.max(...bins.map(b => b.count), 1);
  // Deviation from the configured target is the actual question this panel
  // answers: "did I deploy the risk I asked for".
  const drift = target ? ((stats.median - target) / target) * 100 : null;

  return (
    <>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 14 }}>
        <Metric label="Median risk" value={`${num(stats.median, 3)}%`} n={stats.n}
                formula="median(realised_risk_pct) across trades with sizing diagnostics" />
        {target != null && <Metric label="Target" value={`${num(target, 2)}%`} formula="risk_per_trade_pct from the run config" />}
        {drift != null && (
          <Metric label="Drift vs. target" value={`${drift > 0 ? '+' : ''}${num(drift, 1)}%`}
                  tone={Math.abs(drift) > 10 ? AMBER : GREEN}
                  formula="(median − target) / target × 100. Within ±10% is the Phase 2 acceptance bar." />
        )}
        <Metric label="Range" value={`${num(stats.min, 3)} – ${num(stats.max, 3)}%`} n={stats.n} />
      </div>

      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 2, height: 90, marginBottom: 6 }}>
        {bins.map((b, i) => {
          const inTarget = target != null && b.from <= target && target <= b.to;
          return (
            <div
              key={i}
              title={`${num(b.from, 3)}% – ${num(b.to, 3)}%: ${b.count} trades`}
              style={{
                flex: 1,
                height: `${(b.count / maxCount) * 100}%`,
                minHeight: b.count ? 2 : 0,
                background: inTarget ? GREEN : GREY,
                opacity: inTarget ? 0.9 : 0.45,
                borderRadius: '2px 2px 0 0',
              }}
            />
          );
        })}
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.64rem', color: GREY, fontVariantNumeric: 'tabular-nums' }}>
        <span>{num(stats.min, 3)}%</span>
        <span>realised risk per trade</span>
        <span>{num(stats.max, 3)}%</span>
      </div>

      <div style={{ marginTop: 14 }}>
        <div style={{ fontSize: '0.68rem', textTransform: 'uppercase', letterSpacing: '0.04em', color: GREY, marginBottom: 6 }}>
          Binding constraint
        </div>
        <div style={{ display: 'flex', height: 20, borderRadius: 3, overflow: 'hidden' }}>
          {constraints.map(([c, n]) => (
            <div
              key={c}
              title={`${c}: ${n} trades`}
              style={{
                width: `${(n / stats.n) * 100}%`,
                // amber = capped/clamped; grey = nothing bound, which is the
                // outcome you want.
                background: c === 'none' ? GREY : AMBER,
                opacity: c === 'none' ? 0.35 : 0.85,
              }}
            />
          ))}
        </div>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginTop: 6, fontSize: '0.7rem' }}>
          {constraints.map(([c, n]) => (
            <span key={c} style={{ color: c === 'none' ? GREY : AMBER }}>
              {c} — {n} ({((n / stats.n) * 100).toFixed(0)}%)
            </span>
          ))}
        </div>
      </div>
    </>
  );
}

/* ── 7.14 Exit Attribution ──────────────────────────────────────────────── */

function ExitAttribution({ trades }) {
  const rows = useMemo(() => {
    const by = {};
    for (const g of trades || []) {
      for (const t of g.sub_trades || [g]) {
        const r = t.exit_reason || 'unknown';
        by[r] = by[r] || { n: 0, pnl: 0, mfe: [] };
        by[r].n++;
        by[r].pnl += t.pnl || 0;
        if (t.mfe != null) by[r].mfe.push(+t.mfe);
      }
    }
    return Object.entries(by)
      .map(([reason, v]) => {
        const sorted = v.mfe.sort((a, b) => a - b);
        return {
          reason, n: v.n, pnl: v.pnl,
          medianMfe: sorted.length ? sorted[Math.floor(sorted.length / 2)] : null,
          mfeN: sorted.length,
        };
      })
      .sort((a, b) => b.n - a.n);
  }, [trades]);

  if (!rows.length) return <div style={{ color: GREY, fontSize: '0.8rem' }}>No closed legs.</div>;
  const total = rows.reduce((s, r) => s + r.n, 0);

  return (
    <table style={{ width: '100%', fontSize: '0.75rem', borderCollapse: 'collapse' }}>
      <thead>
        <tr style={{ color: GREY, textAlign: 'left' }}>
          <th style={{ padding: '4px 6px' }}>Exit reason</th>
          <th style={{ padding: '4px 6px', textAlign: 'right' }}>Legs</th>
          <th style={{ padding: '4px 6px', textAlign: 'right' }}>Share</th>
          <th style={{ padding: '4px 6px', textAlign: 'right' }}>Net P&L</th>
          <th style={{ padding: '4px 6px', textAlign: 'right' }} title="Median maximum favourable excursion — how far the trade went your way before this exit fired">
            Median MFE
          </th>
        </tr>
      </thead>
      <tbody>
        {rows.map(r => (
          <tr key={r.reason} style={{ borderTop: '1px solid rgba(255,255,255,0.04)' }}>
            <td style={{ padding: '4px 6px' }}>{r.reason}</td>
            <td style={{ padding: '4px 6px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{r.n}</td>
            <td style={{ padding: '4px 6px', textAlign: 'right', color: GREY }}>{((r.n / total) * 100).toFixed(0)}%</td>
            <td style={{ padding: '4px 6px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: r.pnl >= 0 ? GREEN : RED }}>
              {num(r.pnl)}
            </td>
            <td style={{ padding: '4px 6px', textAlign: 'right', color: GREY, fontVariantNumeric: 'tabular-nums' }}
                title={r.mfeN ? `n = ${r.mfeN}` : 'MFE not recorded on these legs'}>
              {r.medianMfe != null ? num(r.medianMfe) : '—'}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/* ── 7.16 Cost Impact ───────────────────────────────────────────────────── */

function CostImpact({ trades, costModel }) {
  const s = useMemo(() => {
    let gross = 0, net = 0, costs = 0, n = 0;
    for (const g of trades || []) {
      for (const t of g.sub_trades || [g]) {
        const pnl = t.pnl || 0;
        const c = (t.commission || 0) + (t.spread_cost || 0) + (t.slippage_cost || 0);
        net += pnl;
        costs += c;
        gross += pnl + c;
        n++;
      }
    }
    return { gross, net, costs, n };
  }, [trades]);

  if (!s.n) return <div style={{ color: GREY, fontSize: '0.8rem' }}>No legs to cost.</div>;

  const hasCosts = Math.abs(s.costs) > 1e-9;

  return (
    <>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <Metric label="Gross P&L" value={num(s.gross)} n={s.n}
                formula="Σ(pnl + commission + spread + slippage) per leg"
                tone={s.gross >= 0 ? GREEN : RED} />
        <Metric label="Costs" value={num(s.costs)} n={s.n}
                formula="Σ(commission + spread_cost + slippage_cost)" tone={AMBER} />
        <Metric label="Net P&L" value={num(s.net)} n={s.n}
                formula="Σ(pnl) — what the account actually saw"
                tone={s.net >= 0 ? GREEN : RED} />
        {hasCosts && (
          <Metric label="Cost drag" value={`${num((s.costs / Math.abs(s.gross || 1)) * 100, 1)}%`}
                  formula="costs / |gross| × 100" tone={AMBER} />
        )}
      </div>
      {!hasCosts && (
        <div style={{ marginTop: 10, fontSize: '0.72rem', color: AMBER }}>
          No per-leg cost fields on these trades — gross and net are identical here, which
          means costs were not attributed rather than that they were zero.
        </div>
      )}
      {/* The cost model is keyed by SYMBOL, so each value is itself an object.
          This used to stringify that object, which put a raw JSON blob on the
          page. Rendered per symbol instead, with each field's provenance —
          whether the number came from MT5, from your config, or from an
          asset-class default — which is the part that actually matters when a
          cost looks wrong. */}
      {costModel && Object.keys(costModel).length > 0 && (
        <div style={{ marginTop: 10, fontSize: '0.7rem' }}>
          {Object.entries(costModel).map(([symbol, model]) => {
            if (!model || typeof model !== 'object') {
              return (
                <div key={symbol} style={{ color: GREY }}>
                  {symbol}: <b style={{ color: BLUE }}>{String(model)}</b>
                </div>
              );
            }
            const sources = model.sources || {};
            const fields = Object.entries(model).filter(
              ([k, v]) => k !== 'sources' && v !== null && v !== undefined && typeof v !== 'object',
            );
            return (
              <div key={symbol} style={{ marginBottom: 8 }}>
                <div style={{ color: GREY, marginBottom: 3 }}>
                  Cost model — <b style={{ color: 'var(--text-primary)' }}>{symbol}</b>
                </div>
                <div style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fill, minmax(190px, 1fr))',
                  gap: '2px 14px',
                }}>
                  {fields.map(([k, v]) => (
                    <div key={k} style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                      <span style={{ color: GREY, overflowWrap: 'anywhere' }}>{k}</span>
                      <span style={{ flexShrink: 0, fontVariantNumeric: 'tabular-nums' }}>
                        <b style={{ color: BLUE }}>
                          {typeof v === 'number' ? Number(v.toPrecision(6)) : String(v)}
                        </b>
                        {sources[k] && (
                          <span
                            title={`Source: ${sources[k]}`}
                            style={{ color: GREY, marginLeft: 5, fontSize: '0.62rem' }}
                          >
                            {' '}({String(sources[k]).replace(/_/g, ' ').toLowerCase()})
                          </span>
                        )}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}

/* ── 7.15 Blocked-Signal Timeline ───────────────────────────────────────── */

function BlockedTimeline({ blocked, trades }) {
  const marks = useMemo(() => {
    const items = (blocked || [])
      .map(b => ({ t: b.time || b.timestamp, reason: b.reason || b.gate || 'blocked' }))
      .filter(m => m.t);
    if (!items.length) return { items: [], lo: 0, hi: 1, palette: {} };
    const times = items.map(m => +m.t);
    const tradeTimes = (trades || []).map(t => +t.entry_time).filter(Boolean);
    const lo = Math.min(...times, ...(tradeTimes.length ? tradeTimes : times));
    const hi = Math.max(...times, ...(tradeTimes.length ? tradeTimes : times));
    const reasons = Array.from(new Set(items.map(m => m.reason)));
    const hues = [200, 40, 320, 160, 260, 0, 90];
    const palette = Object.fromEntries(reasons.map((r, i) => [r, `hsl(${hues[i % hues.length]},70%,58%)`]));
    return { items, lo, hi: hi === lo ? lo + 1 : hi, palette };
  }, [blocked, trades]);

  if (!marks.items.length) {
    return <div style={{ color: GREY, fontSize: '0.8rem' }}>No blocked signals recorded on this run.</div>;
  }

  const span = marks.hi - marks.lo;
  return (
    <>
      <div style={{ position: 'relative', height: 46, background: 'rgba(255,255,255,0.03)', borderRadius: 4, marginBottom: 8 }}>
        {(trades || []).map((t, i) => t.entry_time && (
          <div key={`t${i}`} title={`Trade ${t.symbol} @ ${t.entry_time}`}
               style={{
                 position: 'absolute', left: `${((+t.entry_time - marks.lo) / span) * 100}%`,
                 top: 4, width: 2, height: 18,
                 background: (t.combined_pnl || 0) >= 0 ? GREEN : RED, opacity: 0.85,
               }} />
        ))}
        {marks.items.map((m, i) => (
          <div key={`b${i}`} title={`${m.reason} @ ${m.t}`}
               style={{
                 position: 'absolute', left: `${((+m.t - marks.lo) / span) * 100}%`,
                 bottom: 4, width: 2, height: 16,
                 background: marks.palette[m.reason], opacity: 0.7,
               }} />
        ))}
      </div>
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', fontSize: '0.68rem' }}>
        <span style={{ color: GREY }}>Top: filled trades (green/red by outcome)</span>
        {Object.entries(marks.palette).map(([r, c]) => (
          <span key={r} style={{ display: 'flex', alignItems: 'center', gap: 4, color: GREY }}>
            <span style={{ width: 8, height: 8, background: c, borderRadius: 2 }} /> {r}
          </span>
        ))}
      </div>
    </>
  );
}

/* ── Shell ──────────────────────────────────────────────────────────────── */

/**
 * Winning and losing streaks, with the scope they are counted over made
 * explicit and every number traceable to the trades behind it.
 */
const SCOPES = [
  { id: 'pair', label: 'Strategy · symbol', key: t => `${tradeStrategy(t)} · ${t.symbol || '?'}` },
  { id: 'symbol', label: 'Symbol', key: t => t.symbol || '?' },
  { id: 'strategy', label: 'Strategy', key: t => tradeStrategy(t) },
];
const fmtDay = v => { const ms = exitMs({ exit_time: v }); return ms ? new Date(ms).toISOString().slice(0, 10) : '-'; };
const fmtWhen = v => { const ms = exitMs({ exit_time: v }); return ms ? new Date(ms).toISOString().slice(5, 16).replace('T', ' ') : '-'; };
const sgn = (v, d = 2) => (v == null || !Number.isFinite(v) ? '-' : `${v >= 0 ? '+' : ''}${v.toFixed(d)}`);
const money = v => (v == null || !Number.isFinite(Number(v)) ? '-' : Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 }));
const th = { padding: '4px 6px', fontWeight: 500, whiteSpace: 'nowrap' };
const td = { padding: '3px 6px', whiteSpace: 'nowrap' };

function StreakTrades({ run }) {
  if (!run) return null;
  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', fontSize: '0.72rem', borderCollapse: 'collapse' }}>
        <thead>
          <tr style={{ color: GREY, textAlign: 'right' }}>
            <th style={{ ...th, textAlign: 'left' }}>#</th><th style={{ ...th, textAlign: 'left' }}>Closed</th>
            <th style={{ ...th, textAlign: 'left' }}>Strategy</th><th style={{ ...th, textAlign: 'left' }}>Symbol</th>
            <th style={{ ...th, textAlign: 'left' }}>Side</th><th style={{ ...th, textAlign: 'left' }}>Exit</th>
            <th style={th}>R</th><th style={th}>P&amp;L</th><th style={th}>Balance after</th>
          </tr>
        </thead>
        <tbody>
          {run.items.map((t, i) => (
            <tr key={t.group_id || t.id || i} style={{ textAlign: 'right', borderTop: '1px solid var(--border, #1e293b)' }}>
              <td style={{ ...td, textAlign: 'left', color: GREY }}>{i + 1}</td>
              <td style={{ ...td, textAlign: 'left' }}>{fmtWhen(t.exit_time)}</td>
              <td style={{ ...td, textAlign: 'left' }}>{tradeStrategy(t)}</td>
              <td style={{ ...td, textAlign: 'left' }}>{t.symbol}</td>
              <td style={{ ...td, textAlign: 'left' }}>{t.direction || t.side || ''}</td>
              <td style={{ ...td, textAlign: 'left', color: GREY }}>{t.exit_reason || ''}</td>
              <td style={{ ...td, color: (streakR(t) ?? 0) < 0 ? RED : GREEN }}>{sgn(streakR(t))}</td>
              <td style={{ ...td, color: (t.pnl || 0) < 0 ? RED : GREEN }}>{sgn(Number(t.pnl), 0)}</td>
              <td style={td}>{money(t.balance_after)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function StreakCard({ title, run, tone, risk, children }) {
  const full = run && risk ? (1 - (1 - risk / 100) ** run.length) * 100 : null;
  return (
    <div style={{ flex: '1 1 240px', border: '1px solid var(--border, #1e293b)', borderRadius: 6, padding: '8px 10px' }}>
      <div style={{ fontSize: '0.7rem', color: GREY }}>{title}</div>
      {run ? (
        <>
          <div style={{ fontSize: '1.3rem', fontWeight: 600, color: tone }}>{run.length} trades in a row</div>
          <div style={{ fontSize: '0.74rem', lineHeight: 1.5 }}>
            <b style={{ color: tone }}>{sgn(run.r)}R</b>
            {run.pct != null && <> · <b style={{ color: tone }}>{sgn(run.pct, 1)}%</b> of the account ({money(run.startBal)} → {money(run.startBal + run.pnl)})</>}
            <br /><span style={{ color: GREY }}>{fmtDay(run.start)} → {fmtDay(run.end)}</span>
            {full != null && run.type === 'L' && (
              <><br /><span style={{ color: GREY }}>{run.length} full −1R stops at {risk}% would be −{full.toFixed(1)}%</span></>
            )}
          </div>
        </>
      ) : <div style={{ fontSize: '0.8rem', color: GREY }}>none</div>}
      {children}
    </div>
  );
}

function Streaks({ trades, risk }) {
  const [view, setView] = useState('all'); // 'all' or `${scope}::${key}`
  const [scope, setScope] = useState('pair');
  const [sort, setSort] = useState({ col: 'maxLoss', dir: -1 });
  const [open, setOpen] = useState('L');
  const options = useMemo(() => SCOPES.map(sc => ({
    ...sc, keys: Array.from(new Set((trades || []).map(sc.key))).sort(),
  })), [trades]);
  const picked = useMemo(() => {
    if (view === 'all') return trades || [];
    const [sid, key] = view.split('::');
    const sc = SCOPES.find(x => x.id === sid);
    return (trades || []).filter(t => sc.key(t) === key);
  }, [trades, view]);
  const all = useMemo(() => streakProfile(picked), [picked]);
  const dd = useMemo(() => (view === 'all' ? drawdownSpan(trades || []) : null), [trades, view]);
  const groups = useMemo(() => {
    const sc = SCOPES.find(x => x.id === scope);
    const rows = streaksBy(trades, sc.key).map(g => ({
      ...g,
      lossR: g.worstLoss?.r ?? 0,
      lossPct: g.worstLoss?.pct ?? 0,
      winRate: g.trades ? (g.runs.filter(x => x.type === 'W').reduce((a, x) => a + x.length, 0) / g.trades) * 100 : 0,
    }));
    const v = r => (typeof r[sort.col] === 'string' ? r[sort.col] : Number(r[sort.col]) || 0);
    return rows.sort((x, y) => (v(x) > v(y) ? 1 : v(x) < v(y) ? -1 : 0) * sort.dir);
  }, [trades, scope, sort]);
  const seq = useMemo(() => all.runs.map((r, i) => ({
    i: i + 1, v: r.type === 'W' ? r.length : -r.length, when: fmtDay(r.end), r: r.r, pct: r.pct,
  })), [all]);
  const dist = useMemo(() => {
    const n = Math.max(all.maxWin, all.maxLoss, 1);
    return Array.from({ length: n }, (_, k) => ({ len: k + 1, W: all.dist.W[k + 1] || 0, L: all.dist.L[k + 1] || 0 }));
  }, [all]);
  if (!(trades || []).length) return <div style={{ fontSize: '0.75rem', color: GREY }}>No closed trades.</div>;

  const scopeLabel = view === 'all' ? 'all trades together' : view.split('::')[1];
  const lossesUnderHalfR = (all.worstLoss?.items || []).filter(t => (streakR(t) ?? -1) > -0.5).length;
  const head = (col, label, left) => (
    <th style={{ ...th, textAlign: left ? 'left' : 'right', cursor: 'pointer', color: sort.col === col ? '#e2e8f0' : GREY }}
        onClick={() => setSort(s => ({ col, dir: s.col === col ? -s.dir : (left ? 1 : -1) }))}>
      {label}{sort.col === col ? (sort.dir > 0 ? ' ▲' : ' ▼') : ''}
    </th>
  );
  const sel = { background: 'var(--bg-input, #0f172a)', color: 'inherit', border: '1px solid var(--border, #334155)', borderRadius: 4, fontSize: '0.75rem', padding: '3px 6px' };

  return (
    <div>
      <div style={{ fontSize: '0.72rem', color: GREY, lineHeight: 1.55, marginBottom: 10, padding: '6px 10px', background: 'rgba(255,255,255,0.03)', borderRadius: 4 }}>
        <b style={{ color: '#e2e8f0' }}>How it is counted.</b> The trades in the chosen scope are lined up in the order they <i>closed</i>.
        Any trade that closed below zero is a loss – a −0.1R time exit counts the same as a −1R stop – and a trade at exactly 0 ends the streak.
        So a streak is a count of trades, not of risk: each card shows what the streak really cost in R and in % of the account.
        Max drawdown is a different thing: the deepest fall from a balance peak, with wins mixed in, so it can be bigger than any single streak.
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 10, fontSize: '0.75rem' }}>
        <span style={{ color: GREY }}>Count streaks over</span>
        <select value={view} onChange={e => setView(e.target.value)} style={sel}>
          <option value="all">All trades together ({(trades || []).length})</option>
          {options.map(sc => (
            <optgroup key={sc.id} label={`One ${sc.label.toLowerCase()}`}>
              {sc.keys.map(k => <option key={k} value={`${sc.id}::${k}`}>{k}</option>)}
            </optgroup>
          ))}
        </select>
        {view !== 'all' && <button type="button" onClick={() => setView('all')} style={{ ...sel, cursor: 'pointer' }}>Back to all</button>}
        <span style={{ color: GREY }}>{all.trades} trades · now {all.current.type ? `${all.current.length} ${all.current.type === 'W' ? 'wins' : 'losses'} in a row` : '-'}</span>
      </div>

      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 8 }}>
        <StreakCard title={`Longest losing streak – ${scopeLabel}`} run={all.worstLoss} tone={RED} risk={risk}>
          {all.worstLoss && (
            <button type="button" onClick={() => setOpen(o => (o === 'L' ? null : 'L'))} style={{ ...sel, marginTop: 6, cursor: 'pointer' }}>
              {open === 'L' ? 'Hide' : 'Show'} the {all.worstLoss.length} trades
            </button>
          )}
        </StreakCard>
        <StreakCard title={`Longest winning streak – ${scopeLabel}`} run={all.bestWin} tone={GREEN}>
          {all.bestWin && (
            <button type="button" onClick={() => setOpen(o => (o === 'W' ? null : 'W'))} style={{ ...sel, marginTop: 6, cursor: 'pointer' }}>
              {open === 'W' ? 'Hide' : 'Show'} the {all.bestWin.length} trades
            </button>
          )}
        </StreakCard>
        {dd && dd.items.length > 0 && (
          <div style={{ flex: '1 1 240px', border: '1px solid var(--border, #1e293b)', borderRadius: 6, padding: '8px 10px' }}>
            <div style={{ fontSize: '0.7rem', color: GREY }}>Max drawdown (closed-trade balance)</div>
            <div style={{ fontSize: '1.3rem', fontWeight: 600, color: RED }}>−{dd.pct.toFixed(1)}%</div>
            <div style={{ fontSize: '0.74rem', lineHeight: 1.5 }}>
              {dd.items.length} trades: <b style={{ color: RED }}>{dd.losses} losses</b> and <b style={{ color: GREEN }}>{dd.wins} wins</b> mixed, {sgn(dd.r)}R
              <br />{money(dd.peakBal)} → {money(dd.troughBal)}
              <br /><span style={{ color: GREY }}>{fmtDay(dd.start)} → {fmtDay(dd.end)}</span>
            </div>
            <button type="button" onClick={() => setOpen(o => (o === 'D' ? null : 'D'))} style={{ ...sel, marginTop: 6, cursor: 'pointer' }}>
              {open === 'D' ? 'Hide' : 'Show'} the {dd.items.length} trades
            </button>
          </div>
        )}
      </div>
      {open === 'L' && all.worstLoss && (
        <div style={{ marginBottom: 10 }}>
          {lossesUnderHalfR > 0 && (
            <div style={{ fontSize: '0.7rem', color: GREY, margin: '2px 0 4px' }}>
              {lossesUnderHalfR} of these {all.worstLoss.length} lost less than 0.5R (time, session or trailing exits) – that is why the streak cost less than {all.worstLoss.length} full stops.
            </div>
          )}
          <StreakTrades run={all.worstLoss} />
        </div>
      )}
      {open === 'W' && all.bestWin && <div style={{ marginBottom: 10 }}><StreakTrades run={all.bestWin} /></div>}
      {open === 'D' && dd && <div style={{ marginBottom: 10 }}><StreakTrades run={dd} /></div>}

      <div style={{ fontSize: '0.7rem', color: GREY, margin: '6px 0 4px' }}>Every streak in order – {scopeLabel} (up = wins in a row, down = losses in a row)</div>
      <ResponsiveContainer width="100%" height={140}>
        <BarChart data={seq} margin={{ top: 4, right: 8, left: -18, bottom: 0 }}>
          <XAxis dataKey="i" hide />
          <YAxis allowDecimals={false} tick={{ fontSize: 10, fill: '#94a3b8' }} />
          <ReferenceLine y={0} stroke="#475569" />
          <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #334155', fontSize: 12 }}
            formatter={(v, _n, p) => [`${Math.abs(v)} ${v > 0 ? 'wins' : 'losses'} in a row: ${sgn(p.payload.r)}R${p.payload.pct != null ? `, ${sgn(p.payload.pct, 1)}% of the account` : ''}`, `ended ${p.payload.when}`]}
            labelFormatter={() => ''} />
          <Bar dataKey="v" isAnimationActive={false}>
            {seq.map(d => <Cell key={d.i} fill={d.v > 0 ? '#10b981' : '#ef4444'} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      <div style={{ fontSize: '0.7rem', color: GREY, margin: '8px 0 4px' }}>How often each streak length happened – {scopeLabel}</div>
      <ResponsiveContainer width="100%" height={120}>
        <BarChart data={dist} margin={{ top: 4, right: 8, left: -18, bottom: 0 }}>
          <XAxis dataKey="len" tick={{ fontSize: 10, fill: '#94a3b8' }} />
          <YAxis allowDecimals={false} tick={{ fontSize: 10, fill: '#94a3b8' }} />
          <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #334155', fontSize: 12 }} labelFormatter={l => `${l} in a row`} />
          <Bar dataKey="W" name="winning streaks" fill="#10b981" isAnimationActive={false} />
          <Bar dataKey="L" name="losing streaks" fill="#ef4444" isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>

      <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '12px 0 4px', fontSize: '0.75rem', flexWrap: 'wrap' }}>
        <b>Each one on its own</b>
        <span style={{ color: GREY }}>– streaks counted inside one</span>
        <select value={scope} onChange={e => setScope(e.target.value)} style={sel}>
          {SCOPES.map(sc => <option key={sc.id} value={sc.id}>{sc.label.toLowerCase()}</option>)}
        </select>
        <span style={{ color: GREY }}>at a time. Click a column to sort, a row to look at it above.</span>
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', fontSize: '0.74rem', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ textAlign: 'right' }}>
              {head('key', SCOPES.find(x => x.id === scope).label, true)}
              {head('trades', 'Trades')}{head('winRate', 'Win %')}
              {head('maxLoss', 'Longest L')}{head('lossR', 'R lost in it')}{head('lossPct', '% of account')}
              {head('maxWin', 'Longest W')}<th style={{ ...th, color: GREY }}>When (longest L)</th><th style={{ ...th, color: GREY }}>Now</th>
            </tr>
          </thead>
          <tbody>
            {groups.map(g => {
              const v = `${scope}::${g.key}`;
              return (
                <tr key={g.key} onClick={() => { setView(v); setOpen('L'); }}
                    style={{ textAlign: 'right', borderTop: '1px solid var(--border, #1e293b)', cursor: 'pointer', background: view === v ? 'rgba(59,130,246,0.12)' : undefined }}>
                  <td style={{ ...td, textAlign: 'left' }}>{g.key}</td>
                  <td style={td}>{g.trades}</td>
                  <td style={td}>{g.winRate.toFixed(0)}%</td>
                  <td style={{ ...td, color: RED, fontWeight: 600 }}>{g.maxLoss}</td>
                  <td style={{ ...td, color: RED }}>{sgn(g.lossR)}</td>
                  <td style={{ ...td, color: RED }}>{g.worstLoss?.pct != null ? `${sgn(g.lossPct, 1)}%` : '-'}</td>
                  <td style={{ ...td, color: GREEN, fontWeight: 600 }}>{g.maxWin}</td>
                  <td style={{ ...td, color: GREY }}>{g.worstLoss ? `${fmtDay(g.worstLoss.start)} → ${fmtDay(g.worstLoss.end)}` : '-'}</td>
                  <td style={td}>{g.current.type ? `${g.current.length}${g.current.type}` : '-'}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div style={{ fontSize: '0.68rem', color: GREY, marginTop: 4 }}>
        "% of account" is that streak's P&amp;L over the balance just before its first trade closed – the hit those trades alone caused; other slots kept trading in between.
      </div>
    </div>
  );
}

export default function RunReport({ result, backtestId = null }) {
  if (!result) return null;

  const trades = result.grouped_trades || [];
  const funnel = result.rejection_funnel || result.report?.rejection_funnel;
  const blocked = result.blocked_signals || [];
  const target = result.params_snapshot?.risk_per_trade_pct;

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
        <h3 style={{ margin: 0, fontSize: '1rem' }}>Run report</h3>
        <span style={{ fontSize: '0.7rem', color: GREY }}>
          {trades.length} trades · {blocked.length} blocked signals
        </span>
        <div style={{ marginLeft: 'auto' }}>
          <AnalyzeButton
            targetType={result.symbols || result.is_portfolio ? 'portfolio' : 'backtest'}
            targetId={backtestId}
            compact
            question="Read the funnel, risk deployment and exit attribution together. Where is the edge actually being lost?"
          />
        </div>
      </div>

      <Panel title="Winning and losing streaks" icon={Activity}
             hint="Wins and losses in a row, in the order trades closed. Pick the scope: all trades together, or one strategy, symbol or strategy on a symbol.">
        <Streaks trades={trades} risk={target} />
      </Panel>

      <Panel title="Signal funnel" icon={Filter}
             hint="Evaluated → strategy-approved → risk-approved → filled → closed, with the gate that removed each drop.">
        <SignalFunnel funnel={funnel} blocked={blocked} trades={trades} />
      </Panel>

      <Panel title="Risk deployment" icon={Gauge}
             hint="Distribution of realised risk per trade against the configured target, and which constraint bound each size.">
        <RiskDeployment trades={trades} target={target} />
      </Panel>

      <Panel title="Exit attribution" icon={LogOut}
             hint="Which exit mechanism closed each leg, and how far the trade had gone your way first.">
        <ExitAttribution trades={trades} />
      </Panel>

      <Panel title="Blocked-signal timeline" icon={Ban}
             hint="When signals were blocked relative to the trades that filled — clustering here usually means a gate is mistuned rather than working.">
        <BlockedTimeline blocked={blocked} trades={trades} />
      </Panel>

      <Panel title="Cost impact" icon={Receipt}
             hint="Gross versus net, and what the spread/commission/slippage model actually charged.">
        <CostImpact trades={trades} costModel={result.cost_model} />
      </Panel>
    </div>
  );
}
