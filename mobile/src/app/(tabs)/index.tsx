import { useState, type ReactNode } from 'react';
import { Pressable, View } from 'react-native';
import { router } from 'expo-router';
import { api } from '../../lib/api';
import { day, greeting, isNeg, monthName, pct, sinceLaunch } from '../../lib/format';
import { useLoad } from '../../lib/hooks';
import { usePrefs } from '../../lib/prefs';
import { color } from '../../lib/theme';
import { BalanceChart, CapitalBar, MonthBars } from '../../components/Charts';
import { Amount, Button, Card, Direction, Icon, Loaded, Screen, Stat, T, s as ui } from '../../components/ui';

function Row({ label, op, children, total, tone }: {
  label: string; op?: string; children: ReactNode; total?: boolean; tone?: 'good' | 'bad';
}) {
  return (
    <View style={[{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: 12, paddingVertical: 4 },
      total && { borderTopColor: color.line, borderTopWidth: 1, paddingTop: 8, marginBottom: 6 }]}>
      <T tone={tone ?? (total ? undefined : 'text2')} bold={total} size={14}>{label}</T>
      <View style={{ flexDirection: 'row', gap: 6, alignItems: 'center' }}>
        {op ? <T tone="muted">{op}</T> : null}{children}
      </View>
    </View>
  );
}

/** The capital-vs-profit message: one of profit, capital being eaten, or not invested yet. */
function CapitalMessage({ c }: { c: any }) {
  const tone = c.state === 'profit' ? color.good : c.state === 'capital_loss' ? color.bad : color.info;
  return (
    <View style={{ flexDirection: 'row', gap: 12, padding: 14, borderRadius: 14, borderWidth: 1,
      borderColor: tone + '55', backgroundColor: tone + '14' }}>
      <Icon name={c.state === 'profit' ? 'up' : c.state === 'capital_loss' ? 'down' : 'info'} tint={tone} size={22} />
      <View style={{ flex: 1, gap: 4 }}>
        {c.state === 'not_invested' ? (
          <>
            <T bold>Your account opens when your first transfer arrives.</T>
            <T tone="text2" size={13}>From then on this screen shows your balance, the money you put in, and whether
              it is earning profit or losing capital.</T>
          </>
        ) : c.state === 'profit' ? (
          <>
            <T bold>Your capital is intact, with profit on top.</T>
            <T tone="text2" size={13}>{pct(c.profit_pct_of_capital)} on the money you have put in, after fees.</T>
            <View style={{ flexDirection: 'row', gap: 6 }}><T size={13} tone="text2">Profit</T><Amount v={c.profit} sign tone="good" size={13} /></View>
          </>
        ) : (
          <>
            <T bold>Losses are eating into your capital.</T>
            <T tone="text2" size={13}>Your balance is {pct(c.profit_pct_of_capital)} below the money you put in.
              No performance fee is charged until this is earned back.</T>
            <View style={{ flexDirection: 'row', gap: 6 }}><T size={13} tone="text2">Capital lost</T><Amount v={`-${c.capital_eroded}`} tone="bad" size={13} /></View>
          </>
        )}
      </View>
    </View>
  );
}

function RecentTrades() {
  const q = useLoad(api.trades);
  return (
    <Card title="Your share of recent trades" right={<Button label="All trades" kind="link" onPress={() => router.push('/trades')} />}>
      <Loaded q={q}>
        {({ trades }) => {
          const mine = trades.filter((t) => t.your_amount !== null).slice(0, 5);
          if (!mine.length) return <T tone="text2" size={14}>Trades closed while your money is in the fund appear here, with your share of each result.</T>;
          return (
            <View>
              {mine.map((t) => (
                <View key={t.id} style={[ui.row, { alignItems: 'center' }]}>
                  <Direction d={t.direction} />
                  <View style={{ flex: 1 }}>
                    <T bold>{t.symbol}</T>
                    <T tone="muted" size={12}>{day(t.closed_on)}</T>
                  </View>
                  <View style={{ alignItems: 'flex-end' }}>
                    <Amount v={t.your_amount} sign tone={isNeg(t.your_amount) ? 'bad' : 'good'} bold />
                    {t.result_pct ? <T tone="muted" size={11}>{pct(t.result_pct)} on the fund</T> : null}
                  </View>
                </View>
              ))}
            </View>
          );
        }}
      </Loaded>
    </Card>
  );
}

export default function Overview() {
  const me = useLoad(api.me);
  const nav = useLoad(api.nav);
  const perf = useLoad(api.performance);
  const { prefs } = usePrefs();
  const [picked, setRange] = useState<string | null>(null);
  const range = picked || prefs.chart_range || 'ALL';
  return (
    <Screen onRefresh={() => { me.reload(); nav.reload(); perf.reload(); }}>
      <Loaded q={me}>
        {({ investor, statement: s, capital: c, lockup_until: lockup }: any) => {
          const st = perf.data?.stats;
          return (
            <>
              <View style={{ backgroundColor: color.surface, borderColor: color.gold + '44', borderWidth: 1, borderRadius: 20, padding: 18, gap: 6 }}>
                <T tone="text2" size={14}>{greeting(investor.name)}</T>
                <T tone="muted" size={11} style={{ letterSpacing: 1.4 }}>YOUR BALANCE · {day(s.as_of).toUpperCase()}</T>
                <Amount v={s.current_value} size={38} bold full />
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                  <Icon name={isNeg(s.profit) ? 'down' : 'up'} size={16} tint={isNeg(s.profit) ? color.bad : color.good} />
                  <Amount v={s.profit} sign tone={isNeg(s.profit) ? 'bad' : 'good'} size={14} />
                  {c.profit_pct_of_capital ? <T size={14} tone={isNeg(s.profit) ? 'bad' : 'good'}>({pct(c.profit_pct_of_capital)})</T> : null}
                  <T size={14} tone="muted">since you joined</T>
                </View>
                <View style={{ flexDirection: 'row', gap: 10, marginTop: 10 }}>
                  <View style={{ flex: 1 }}><Button label="Add money" kind="primary" onPress={() => router.push('/money')} /></View>
                  <View style={{ flex: 1 }}><Button label="Withdraw" onPress={() => router.push('/money?tab=withdraw')} /></View>
                </View>
              </View>

              {investor.status === 'closing' && (
                <Card><T tone="gold">You have asked to close your account. We will pay out your balance and confirm by email.</T></Card>
              )}
              <CapitalMessage c={c} />

              {c.state !== 'not_invested' && <Card title="What your balance is made of"><CapitalBar c={c} /></Card>}

              <Card title="Balance vs money put in">
                <Loaded q={nav}>{(points) => <BalanceChart points={points} range={range} onRange={setRange} />}</Loaded>
              </Card>

              <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 10 }}>
                <Stat label="Total return" icon="chart" tone={isNeg(s.profit) ? 'bad' : 'good'} note="on net invested">
                  {c.profit_pct_of_capital ? pct(c.profit_pct_of_capital) : '—'}</Stat>
                <Stat label="Share of fund" icon="user">{`${s.share_of_pool_pct}%`}</Stat>
                <Stat label="Paid in" icon="arrowIn"><Amount v={c.paid_in} bold size={17} /></Stat>
                <Stat label="Paid out" icon="arrowOut"><Amount v={c.paid_out} bold size={17} /></Stat>
                <Stat label="Fees paid" icon="fee" note="management and performance"><Amount v={c.fees_paid} bold size={17} /></Stat>
                <Stat label="Days invested" icon="activity" note={st ? `since ${day(st.first_deposit)}` : undefined}>
                  {st ? String(st.days_invested) : '—'}</Stat>
                <Stat label="Best month" icon="up" note={st ? monthName(st.best_month.month) : undefined}>
                  {st ? <Amount v={st.best_month.profit} sign tone="good" bold size={17} /> : '—'}</Stat>
                <Stat label="Worst month" icon="down" note={st ? monthName(st.worst_month.month) : undefined}>
                  {st ? <Amount v={st.worst_month.profit} sign tone={isNeg(st.worst_month.profit) ? 'bad' : undefined} bold size={17} /> : '—'}</Stat>
                <Stat label="Largest fall" icon="down" note="from a high, since you joined">{st ? pct(st.largest_fall_pct) : '—'}</Stat>
                <Stat label="Months up / down" icon="chart">{st ? `${st.months_up} / ${st.months_down}` : '—'}</Stat>
              </View>

              <Card title="Your result each month">
                <Loaded q={perf}>{(p) => <MonthBars months={p.months} />}</Loaded>
              </Card>

              <RecentTrades />

              <Card title="Where your numbers come from" right={<Button label="Full explanation" kind="link" onPress={() => router.push('/how')} />}>
                <View>
                  <Row label="Money you put in"><Amount v={c.paid_in} /></Row>
                  <Row label="Money paid out to you" op="−"><Amount v={c.paid_out} /></Row>
                  <Row label="Net invested" total><Amount v={c.net_invested} bold /></Row>
                  <Row label="Your balance today"><Amount v={c.value} /></Row>
                  <Row label="Net invested" op="−"><Amount v={c.net_invested} /></Row>
                  <Row label={isNeg(c.profit) ? 'Capital lost' : 'Profit'} total tone={isNeg(c.profit) ? 'bad' : 'good'}>
                    <Amount v={c.profit} sign bold tone={isNeg(c.profit) ? 'bad' : 'good'} /></Row>
                </View>
                <View style={{ flexDirection: 'row', flexWrap: 'wrap' }}>
                  <T tone="muted" size={12}>Fees are already taken off the balance. Your balance moves by the same
                    percentage as the fund: {sinceLaunch(s.nav_per_unit)} since launch.</T>
                </View>
              </Card>

              <Pressable onPress={() => router.push('/money?tab=withdraw')} accessibilityRole="button">
                <Card title="Available to withdraw" right={<Icon name="arrowOut" size={18} tint={color.gold} />}>
                  <Amount v={s.withdrawable_now} size={28} bold />
                  <T tone="text2" size={14}>
                    {s.withdrawable_explanation.charAt(0).toUpperCase() + s.withdrawable_explanation.slice(1)}.
                    {lockup && lockup > new Date().toISOString().slice(0, 10) ? ` Your money is in its lock-up period until ${day(lockup)}.` : ''}
                  </T>
                </Card>
              </Pressable>
            </>
          );
        }}
      </Loaded>
    </Screen>
  );
}
