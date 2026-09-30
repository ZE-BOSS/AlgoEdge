import { useMemo, useState } from 'react';
import { View } from 'react-native';
import { router } from 'expo-router';
import { api, shareStatement } from '../../lib/api';
import { STATE_LABEL, day, isNeg, money, monthLabel } from '../../lib/format';
import { useLoad } from '../../lib/hooks';
import { color } from '../../lib/theme';
import { Amount, Badge, Button, Card, Chips, Icon, Loaded, Problem, Screen, Stat, T, s } from '../../components/ui';

const TONE: Record<string, 'good' | 'bad' | 'info'> = { paid: 'good', confirmed: 'good', declined: 'bad', rejected: 'bad', approved: 'info' };
type Kind = 'all' | 'in' | 'out' | 'fee';
const KINDS: [Kind, string][] = [['all', 'All'], ['in', 'Money in'], ['out', 'Money out'], ['fee', 'Fees']];

type Ev = { key: string; type: Kind | 'fix'; icon: string; when: string; title: string; amount: string | null;
  sign: 1 | -1; state?: string; detail?: string | null };

/** Everything that has happened to the money, newest first, in one list. */
function events({ ledger, deposits, withdrawals }: any): Ev[] {
  const out: Ev[] = [];
  for (const d of deposits) {
    out.push({
      key: `d${d.id}`, type: 'in', icon: 'arrowIn', when: d.priced_on || d.created_at,
      title: d.state === 'confirmed' ? 'Money received' : d.state === 'rejected' ? 'Transfer not received' : 'Transfer on its way',
      amount: d.amount_confirmed || d.amount_claimed, sign: 1, state: d.state,
      detail: d.amount_confirmed && d.amount_claimed && d.amount_confirmed !== d.amount_claimed
        ? `You told us ${money(d.amount_claimed)}; ${money(d.amount_confirmed)} arrived and was invested.`
        : d.state === 'confirmed' ? 'Invested the day it arrived.' : d.reason,
    });
  }
  for (const w of withdrawals) {
    out.push({
      key: `w${w.id}`, type: 'out', icon: 'arrowOut', when: w.paid_at || w.created_at,
      title: w.state === 'paid' ? 'Withdrawal paid' : w.state === 'declined' ? 'Withdrawal declined' : 'Withdrawal requested',
      amount: w.amount_paid || w.amount_requested, sign: -1, state: w.state,
      detail: [w.expected_by && `Expected by ${day(w.expected_by)}`, w.payment_reference && `Ref ${w.payment_reference}`,
        w.reason].filter(Boolean).join(' · '),
    });
  }
  for (const t of ledger) {
    if (t.kind === 'FEE') {
      out.push({ key: `l${t.id}`, type: 'fee', icon: 'fee', when: t.date, title: 'Fee charged', amount: t.amount, sign: -1,
        detail: 'Management and performance fees for the period, taken from your balance.' });
    } else if (t.kind === 'CORRECTION') {
      out.push({ key: `l${t.id}`, type: 'fix', icon: 'fix', when: t.date, title: 'Correction', amount: t.amount,
        sign: isNeg(t.units) ? -1 : 1, detail: 'An adjustment to your balance by the fund.' });
    }
  }
  return out.sort((a, b) => String(b.when).localeCompare(String(a.when)));
}

const sum = (list: Ev[], type: string) => list.filter((e) => e.type === type
  && (!e.state || e.state === 'confirmed' || e.state === 'paid'))
  .reduce((a, e) => a + Math.abs(Number(e.amount || 0)), 0).toFixed(2);

function Timeline({ data }: { data: any }) {
  const [kind, setKind] = useState<Kind>('all');
  const all = useMemo(() => events(data), [data]);
  const list = kind === 'all' ? all : all.filter((e) => e.type === kind);
  return (
    <>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 10 }}>
        <Stat label="Received" icon="arrowIn"><Amount v={sum(all, 'in')} bold size={17} /></Stat>
        <Stat label="Paid out" icon="arrowOut"><Amount v={sum(all, 'out')} bold size={17} /></Stat>
        <Stat label="Fees" icon="fee"><Amount v={sum(all, 'fee')} bold size={17} /></Stat>
        <Stat label="Events" icon="activity">{String(all.length)}</Stat>
      </View>
      <Card title="Timeline">
        <Chips value={kind} options={KINDS} onChange={setKind} />
        {all.length === 0 ? (
          <View style={{ alignItems: 'center', gap: 8, paddingVertical: 20 }}>
            <Icon name="activity" size={32} tint={color.muted} />
            <T bold>Nothing here yet</T>
            <T tone="text2" size={13} style={{ textAlign: 'center' }}>Your transfers, withdrawals and fees will appear here as
              they happen, newest first.</T>
            <Button label="Add money" kind="link" onPress={() => router.push('/money')} />
          </View>
        ) : list.length === 0 ? <T tone="text2">Nothing of this kind yet.</T> : (
          <View>
            {list.map((e, i) => {
              const tint = e.type === 'in' ? color.good : e.type === 'out' ? color.info : e.type === 'fee' ? color.gold : color.text2;
              return (
                <View key={e.key} style={{ flexDirection: 'row', gap: 12 }}>
                  <View style={{ alignItems: 'center' }}>
                    <View style={{ width: 34, height: 34, borderRadius: 17, alignItems: 'center', justifyContent: 'center',
                      backgroundColor: tint + '22' }}><Icon name={e.icon} size={16} tint={tint} /></View>
                    {i < list.length - 1 && <View style={{ flex: 1, width: 2, backgroundColor: color.line, marginVertical: 2 }} />}
                  </View>
                  <View style={{ flex: 1, gap: 2, paddingBottom: 16 }}>
                    <View style={{ flexDirection: 'row', justifyContent: 'space-between', gap: 8 }}>
                      <T bold size={14} style={{ flexShrink: 1 }}>{e.title}</T>
                      <Amount v={e.amount && (e.sign < 0 ? `-${String(e.amount).replace(/^-/, '')}` : String(e.amount).replace(/^-/, ''))}
                        sign tone={e.sign < 0 ? undefined : 'good'} bold size={14} />
                    </View>
                    <View style={{ flexDirection: 'row', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                      <T tone="muted" size={12}>{day(e.when)}</T>
                      {e.state ? <Badge label={STATE_LABEL[e.state] || e.state} tone={TONE[e.state]} /> : null}
                    </View>
                    {e.detail ? <T tone="text2" size={12}>{e.detail}</T> : null}
                  </View>
                </View>
              );
            })}
          </View>
        )}
      </Card>
    </>
  );
}

function Statements() {
  const q = useLoad(api.statements);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<Error | null>(null);
  return (
    <Card title="Monthly statements">
      <Loaded q={q}>
        {(rows) => rows.length === 0 ? <T tone="text2">Your first statement appears after your first full month.</T> : (
          <View>
            {rows.slice(0, 24).map((r) => (
              <View key={r.label} style={[s.row, { alignItems: 'center' }]}>
                <View style={{ flexDirection: 'row', gap: 10, alignItems: 'center', flex: 1 }}>
                  <Icon name="doc" size={18} tint={color.gold} />
                  <T bold>{monthLabel(r.year, r.month)}</T>
                </View>
                <Button label={busy === r.label ? 'Opening…' : 'PDF'} kind="link" disabled={!!busy}
                  onPress={() => { setBusy(r.label); setError(null);
                    shareStatement(r.year, r.month).catch(setError).finally(() => setBusy(null)); }} />
              </View>
            ))}
          </View>
        )}
      </Loaded>
      <Problem error={error} />
    </Card>
  );
}

export default function Activity() {
  const q = useLoad(api.activity);
  return (
    <Screen onRefresh={q.reload}>
      <Loaded q={q}>{(data) => <Timeline data={data} />}</Loaded>
      <Statements />
    </Screen>
  );
}
