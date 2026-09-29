import { View } from 'react-native';
import { api } from '../../lib/api';
import { day, isNeg, money } from '../../lib/format';
import { useLoad } from '../../lib/hooks';
import { Card, Loaded, Screen, T, s } from '../../components/ui';

export default function Trades() {
  const q = useLoad(api.trades);
  return (
    <Screen onRefresh={q.reload}>
      <Card title="Closed trades">
        <T tone="text2" size={13}>Results are for the whole fund, not your share of it. Your share of any result is your
          share of the fund, shown on the overview.</T>
        <Loaded q={q}>
          {(rows) => rows.length === 0 ? <T tone="text2">No trades have been published yet.</T> : (
            <View>
              {rows.map((t: any) => (
                <View key={t.id} style={s.row}>
                  <View style={{ flex: 1, gap: 2 }}>
                    <T bold>{t.symbol} <T tone="muted">{t.direction === 'BUY' ? 'Long' : 'Short'}</T></T>
                    <T tone="muted" size={13}>{day(t.closed_on)}</T>
                    {t.note ? <T size={13}>{t.note}</T> : null}
                  </View>
                  <View style={{ alignItems: 'flex-end' }}>
                    <T mono bold tone={isNeg(t.result_amount) ? 'bad' : 'good'}>{money(t.result_amount, { sign: true })}</T>
                    {t.result_pct ? <T mono tone="muted" size={12}>{t.result_pct.startsWith('-') ? '' : '+'}{t.result_pct}%</T> : null}
                  </View>
                </View>
              ))}
            </View>
          )}
        </Loaded>
      </Card>
    </Screen>
  );
}
