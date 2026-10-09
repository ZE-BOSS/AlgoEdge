import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { cash } from '../services/api';
import { decimate } from '../utils/decimate';

/**
 * The account balance, the capital put in, and what investors are owed — on
 * ONE date axis.
 *
 * Why one chart and not three: the shape only means something in comparison.
 * A balance falling while investor claims hold flat is a different story from
 * both falling together, and fetching the series separately invites plotting
 * two of them from different days. The endpoint returns them aligned for that
 * reason.
 *
 * Two deliberate choices about honesty:
 *
 *   The balance starts at the OPENING ANCHOR, not at zero. A curve beginning
 *   at zero implies the account did, and on a funded account that is a lie.
 *
 *   The NAV line is carried forward between valuations rather than
 *   interpolated. A unit price is a statement made on a day; drawing a slope
 *   between two of them invents prices nobody was ever quoted.
 */

const COLOURS = {
  balance: '#58a6ff',
  capital: '#8b949e',
  investor_claims: '#bc8cff',
  shortfall: '#f0883e',
};

const LABELS = {
  balance: 'Account balance',
  capital: 'Capital in, net',
  investor_claims: 'Owed to investors',
  shortfall: 'Gap',
};

const money = (v) => (v === null || v === undefined ? '—'
  : `${v < 0 ? '−' : ''}$${Math.abs(v).toLocaleString(undefined, {
      minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);

export default function MoneyCurves({ login = null }) {
  const q = useQuery({
    queryKey: ['cash', 'curves', login],
    queryFn: () => cash.curves(login).then((r) => r.data),
  });

  // Decimated for the same reason the trade curve is: Recharts cannot resolve
  // more than a few hundred points, and drawing 70k of them hung this page.
  const rows = useMemo(() => {
    const d = q.data;
    if (!d?.days?.length) return [];
    const keys = ['balance', 'capital', 'investor_claims', 'shortfall'];
    const idx = decimate(d.days.map((_, i) => i), 400);
    return idx.map((i) => {
      const point = { day: d.days[i] };
      keys.forEach((k) => { point[k] = d.series[k]?.[i] ?? null; });
      return point;
    });
  }, [q.data]);

  if (q.isLoading) return <div className="card">Loading…</div>;
  if (!rows.length) {
    return (
      <div className="card">
        <div className="card-header"><span className="card-title">Money over time</span></div>
        <div className="empty-state">
          <h3>Nothing to draw yet</h3>
          <p>{q.data?.note
            || 'Sync an MT5 account from the Journal’s Money tab to build this.'}</p>
        </div>
      </div>
    );
  }

  const last = rows[rows.length - 1];
  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title">Money over time</span>
        <span className="inv-hint" style={{ fontSize: '0.72rem' }}>
          {q.data.accounts.join(', ')} · from {rows[0].day} · opens at{' '}
          {money(q.data.opening_balance)}
        </span>
      </div>
      <ResponsiveContainer width="100%" height={320}>
        <LineChart data={rows}>
          <CartesianGrid strokeDasharray="3 3" stroke="#30363d" />
          <XAxis dataKey="day" stroke="#484f58" fontSize={11} minTickGap={40} />
          <YAxis stroke="#484f58" fontSize={11}
                 tickFormatter={(v) => `$${(v / 1000).toFixed(1)}k`} />
          <Tooltip
            contentStyle={{ background: '#161b22', border: '1px solid #30363d',
                            borderRadius: 8, color: '#e6edf3', fontSize: 12 }}
            formatter={(v, name) => [money(v), LABELS[name] || name]} />
          <Legend formatter={(name) => LABELS[name] || name} wrapperStyle={{ fontSize: 12 }} />
          {Object.keys(COLOURS).map((k) => (
            <Line key={k} type="monotone" dataKey={k} stroke={COLOURS[k]}
                  strokeWidth={k === 'shortfall' ? 1.5 : 2}
                  strokeDasharray={k === 'shortfall' ? '4 3' : undefined}
                  dot={false} connectNulls />
          ))}
        </LineChart>
      </ResponsiveContainer>
      <div className="inv-hint" style={{ marginTop: 8, lineHeight: 1.6 }}>
        Latest: balance {money(last.balance)} · capital in {money(last.capital)} ·
        owed to investors {money(last.investor_claims)} ·
        gap {money(last.shortfall)}.
        {' '}The gap is what investors are owed less what the account holds; it is
        normally positive while deposits are confirmed but not yet transferred to
        the broker, which the Journal’s Money tab breaks down.
      </div>
    </div>
  );
}
