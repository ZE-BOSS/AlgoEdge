import { useMemo, useState } from 'react';
import { api } from '../api';
import { useLoad } from '../hooks';
import { Amount, Card, Loaded, Stat } from '../components/ui';
import { SymbolBars } from '../components/Charts';
import LiveTrades from '../components/LiveTrades';
import { day, isNeg, pct } from '../format';

const FILTERS = [['all', 'All'], ['mine', 'While invested'], ['win', 'Gains'], ['loss', 'Losses']];

export default function Trades() {
  const q = useLoad(api.trades);
  const [filter, setFilter] = useState('mine');
  const [symbol, setSymbol] = useState('');
  return (
    <Loaded q={q}>
      {({ trades, summary }) => {
        const symbols = [...new Set(trades.map((t) => t.symbol))].sort();
        return (
          <div className="stack">
            <LiveTrades />
            <div className="stat-grid">
              <Stat label="Your result from trades" icon="chart" tone={isNeg(summary.your_total) ? 'bad' : 'good'}
                    note="your share, before fees"><Amount v={summary.your_total} sign /></Stat>
              <Stat label="Gains" icon="up" tone="good">{summary.wins}</Stat>
              <Stat label="Losses" icon="down" tone={summary.losses ? 'bad' : undefined}>{summary.losses}</Stat>
              <Stat label="Win rate" icon="trades">{summary.win_rate_pct ? `${summary.win_rate_pct}%` : '—'}</Stat>
            </div>

            {summary.by_symbol.length > 0 && (
              <Card title="Your result by market"><SymbolBars rows={summary.by_symbol} /></Card>
            )}

            <Card title="Closed trades">
              <p className="muted small">Each result is <strong>your share</strong>: the trade&rsquo;s result
                multiplied by your share of the fund when it closed. Everyone&rsquo;s shares add up to the
                trade&rsquo;s result exactly. The percentage is the trade&rsquo;s effect on the fund, the same for
                every investor. Your share is added to (or taken from) your balance the moment the trade is published,
                so your overview always includes it. Fees are charged separately, every two months.</p>
              <div className="filters">
                <div className="chips">
                  {FILTERS.map(([k, label]) => (
                    <button key={k} className={`chip ${filter === k ? 'on' : ''}`} onClick={() => setFilter(k)}>{label}</button>
                  ))}
                </div>
                {symbols.length > 1 && (
                  <select value={symbol} onChange={(e) => setSymbol(e.target.value)} aria-label="Market">
                    <option value="">All markets</option>
                    {symbols.map((s) => <option key={s}>{s}</option>)}
                  </select>
                )}
              </div>
              <TradeList trades={trades} filter={filter} symbol={symbol} />
            </Card>
          </div>
        );
      }}
    </Loaded>
  );
}

function TradeList({ trades, filter, symbol }) {
  const rows = useMemo(() => trades.filter((t) => {
    if (symbol && t.symbol !== symbol) return false;
    if (filter === 'mine') return t.your_amount !== null;
    if (filter === 'win') return t.your_amount !== null && Number(t.your_amount) > 0;
    if (filter === 'loss') return t.your_amount !== null && Number(t.your_amount) < 0;
    return true;
  }), [trades, filter, symbol]);
  if (!trades.length) return <p className="muted">No trades have been published yet.</p>;
  if (!rows.length) return <p className="muted">No trades match this filter.</p>;
  return (
    <ul className="trade-list">
      {rows.map((t) => (
        <li key={t.id} className={t.your_amount === null ? 'dim' : ''}>
          <span className={`dir ${t.direction === 'BUY' ? 'long' : 'short'}`}>{t.direction === 'BUY' ? 'Buy' : 'Sell'}</span>
          <div>
            <strong>{t.symbol}</strong>
            <span className="muted small">{day(t.closed_on)}
              {t.your_share_pct && ` · you held ${t.your_share_pct}% of the fund`}</span>
            {t.note && <span className="small">{t.note}</span>}
          </div>
          <div className="right">
            {t.your_amount === null
              ? <span className="muted small">before you joined</span>
              : <Amount v={t.your_amount} sign className={isNeg(t.your_amount) ? 'bad' : 'good'} />}
            {t.result_pct && <span className="muted small">{pct(t.result_pct)} on the fund</span>}
          </div>
        </li>
      ))}
    </ul>
  );
}
