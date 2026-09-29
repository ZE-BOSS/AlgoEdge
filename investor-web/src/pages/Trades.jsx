import { api } from '../api';
import { useLoad } from '../hooks';
import { Card, Loaded } from '../components/ui';
import { day, isNeg, money } from '../format';

export default function Trades() {
  const q = useLoad(api.trades);
  return (
    <Card title="Closed trades">
      <p className="muted small">
        Results are for the whole fund, not your share of it. Your share of any result is your
        share of the fund, shown on the overview.
      </p>
      <Loaded q={q}>
        {(rows) => rows.length === 0 ? <p className="muted">No trades have been published yet.</p> : (
          <ul className="rows">
            {rows.map((t) => (
              <li key={t.id}>
                <div>
                  <strong>{t.symbol} <span className="muted">{t.direction === 'BUY' ? 'Long' : 'Short'}</span></strong>
                  <span className="muted small">{day(t.closed_on)}</span>
                  {t.note && <span className="small">{t.note}</span>}
                </div>
                <div className="right">
                  <strong className={`num ${isNeg(t.result_amount) ? 'bad' : 'good'}`}>
                    {money(t.result_amount, { sign: true })}</strong>
                  {t.result_pct && <span className="muted small num">{t.result_pct.startsWith('-') ? '' : '+'}{t.result_pct}% of the fund</span>}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Loaded>
    </Card>
  );
}
