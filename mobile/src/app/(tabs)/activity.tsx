import { useState } from 'react';
import { View } from 'react-native';
import { api, shareStatement } from '../../lib/api';
import { STATE_LABEL, day, isNeg, money, monthLabel, units } from '../../lib/format';
import { useLoad } from '../../lib/hooks';
import { Badge, Button, Card, Loaded, Problem, Screen, T, s } from '../../components/ui';

const TONE: Record<string, 'good' | 'bad' | 'info'> = { paid: 'good', confirmed: 'good', declined: 'bad', rejected: 'bad', approved: 'info' };

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
                <T bold>{monthLabel(r.year, r.month)}</T>
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
      <Statements />
      <Loaded q={q}>
        {({ ledger, deposits, withdrawals }: any) => (
          <>
            <Card title="Withdrawals">
              {withdrawals.length === 0 ? <T tone="text2">None yet.</T> : withdrawals.map((w: any) => (
                <View key={w.id} style={s.row}>
                  <View style={{ flex: 1, gap: 2 }}>
                    <T mono bold>{money(w.amount_paid || w.amount_requested)}</T>
                    <T tone="muted" size={13}>Requested {day(w.created_at)}{w.expected_by ? ` · expected by ${day(w.expected_by)}` : ''}
                      {w.paid_at ? ` · paid ${day(w.paid_at)}` : ''}{w.payment_reference ? ` · ref ${w.payment_reference}` : ''}</T>
                    {w.reason ? <T size={13}>{w.reason}</T> : null}
                  </View>
                  <Badge label={STATE_LABEL[w.state] || w.state} tone={TONE[w.state]} />
                </View>
              ))}
            </Card>
            <Card title="Money in">
              {deposits.length === 0 ? <T tone="text2">None yet.</T> : deposits.map((d: any) => (
                <View key={d.id} style={s.row}>
                  <View style={{ flex: 1, gap: 2 }}>
                    <T mono bold>{money(d.amount_confirmed || d.amount_claimed)}</T>
                    <T tone="muted" size={13}>{d.priced_on ? `Units bought ${day(d.priced_on)}` : `Told us ${day(d.created_at)}`}</T>
                    {d.reason ? <T size={13}>{d.reason}</T> : null}
                  </View>
                  <Badge label={STATE_LABEL[d.state] || d.state} tone={TONE[d.state]} />
                </View>
              ))}
            </Card>
            <Card title="Every movement of your units">
              {ledger.length === 0 ? <T tone="text2">None yet.</T> : ledger.map((t: any) => (
                <View key={t.id} style={s.row}>
                  <View style={{ flex: 1, gap: 2 }}>
                    <T bold>{t.label}</T>
                    <T tone="muted" size={13}>{day(t.date)} · at {units(t.nav_per_unit)}</T>
                  </View>
                  <View style={{ alignItems: 'flex-end' }}>
                    <T mono tone={isNeg(t.units) ? 'bad' : undefined}>{units(t.units)}</T>
                    <T mono tone="muted" size={13}>{money(t.amount)}</T>
                  </View>
                </View>
              ))}
            </Card>
          </>
        )}
      </Loaded>
    </Screen>
  );
}
