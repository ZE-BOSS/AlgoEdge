import { api } from '../api';
import { useLoad } from '../hooks';
import { Amount, Badge, Card, Copy, Loaded, Stat } from '../components/ui';
import { day, isNeg } from '../format';

/**
 * My link, the people I introduced, and what I have earned from them.
 *
 * Everything here points DOWN. What I am told about the person who introduced
 * me is only the rate I pay them - never who they are or what they hold. The
 * server enforces that; this page simply has nothing else to show.
 */
export default function Referrals() {
  const q = useLoad(api.myReferrals);
  return (
    <Loaded q={q}>
      {(d) => (
        <div className="stack">
          <Card title="Your referral link">
            <p className="muted small">
              Share this link. Anyone who opens an account through it is placed under you, and you
              earn a share of the fund's returns from their investment.
            </p>
            <div className="row" style={{ gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <code style={{ wordBreak: 'break-all' }}>{d.link}</code>
              <Copy text={d.link} />
            </div>
            <p className="muted small">Your code: <strong>{d.code}</strong></p>
            {(Number(d.rates.level_1_pct) > 0 || Number(d.rates.level_2_pct) > 0) && (
              <p className="muted small">
                You earn {d.rates.level_1_pct}% on people you introduce
                {Number(d.rates.level_2_pct) > 0 && <>, and {d.rates.level_2_pct}% on the people they introduce</>}
                {d.model === 'HOUSE_FEE' ? ', paid out of the fund’s own fees.' : ', paid out of their profit.'}
              </p>
            )}
          </Card>

          <div className="stat-grid">
            <Stat label="People you introduced" icon="user">{d.totals.direct}</Stat>
            <Stat label="Introduced by them" icon="user">{d.totals.second_level}</Stat>
            <Stat label="You have earned" icon="up" tone="good"><Amount v={d.totals.earned} /></Stat>
            <Stat label="Not yet paid to you" icon="money" note="paid after each fee period">
              <Amount v={d.totals.earned_pending} />
            </Stat>
          </div>

          <Card title="Your referrals">
            {d.people.length === 0 ? (
              <p className="muted">Nobody has joined through your link yet.</p>
            ) : (
              // A list, not a table: this app is used mostly on phones and has no
              // other table in it; six columns would not fit a phone screen.
              <ul className="doc-list">
                {d.people.map((p, i) => (
                  <li key={i} style={{ flexWrap: 'wrap', alignItems: 'flex-start' }}>
                    <div style={{ flex: '1 1 160px' }}>
                      <strong>{p.name}</strong>{' '}
                      {p.level === 2 && <Badge>via {p.introduced_by}</Badge>}
                      {p.status !== 'active' && <> <Badge>{p.status}</Badge></>}
                      <div className="muted small">joined {p.joined ? day(p.joined) : '—'}</div>
                    </div>
                    <div className="muted small" style={{ display: 'grid', gap: 2, textAlign: 'right' }}>
                      <span>Deposited <Amount v={p.deposited} /></span>
                      <span>Value <Amount v={p.current_value} /></span>
                      <span className={isNeg(p.profit) ? 'bad' : 'good'}>Profit <Amount v={p.profit} sign /></span>
                      <strong>You earned <Amount v={p.earned_from_them} /></strong>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          {d.upline_note && (
            <Card title="Your introducer">
              <p className="muted">{d.upline_note}</p>
            </Card>
          )}
        </div>
      )}
    </Loaded>
  );
}
