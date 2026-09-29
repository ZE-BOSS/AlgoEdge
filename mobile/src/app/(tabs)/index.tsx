import { View } from 'react-native';
import { router } from 'expo-router';
import { api } from '../../lib/api';
import { day, isNeg, money, units } from '../../lib/format';
import { useLoad } from '../../lib/hooks';
import { color } from '../../lib/theme';
import NavChart from '../../components/NavChart';
import { Button, Card, Loaded, Pairs, Screen, T } from '../../components/ui';

export default function Overview() {
  const me = useLoad(api.me);
  const nav = useLoad(api.nav);
  return (
    <Screen onRefresh={() => { me.reload(); nav.reload(); }}>
      <Loaded q={me}>
        {({ investor, statement: s, lockup_until: lockup }: any) => (
          <>
            <View style={{ gap: 4, paddingVertical: 4 }}>
              <T tone="muted" size={12} style={{ letterSpacing: 1.4 }}>YOUR HOLDING · {day(s.as_of).toUpperCase()}</T>
              <T size={40} bold style={{ letterSpacing: -1, lineHeight: 48 }}>{money(s.current_value)}</T>
              <T tone={isNeg(s.profit) ? 'bad' : 'good'}>{money(s.profit, { sign: true })}
                <T tone="text2"> since you joined</T></T>
              <Button label="How this is calculated" kind="link" onPress={() => router.push('/how')} />
            </View>

            {investor.status === 'closing' && (
              <Card><T tone="gold">You have asked to close your account. We will pay out your holding and confirm by email.</T></Card>
            )}
            {investor.status === 'pending' && (
              <Card><T tone="gold">Your account opens when your first transfer is confirmed.</T>
                <Button label="Add money" kind="link" onPress={() => router.push('/money')} /></Card>
            )}

            <Card>
              <Pairs rows={[
                ['Paid in', money(s.capital_in)], ['Paid out to you', money(s.withdrawn)],
                ['Units held', units(s.units)], ['Price per unit', units(s.nav_per_unit)],
                ['Your share of the fund', `${s.share_of_pool_pct}%`],
              ]} />
            </Card>

            <Card title="Price per unit">
              <Loaded q={nav}>{(points) => <NavChart points={points} />}</Loaded>
            </Card>

            <Card title="Available to withdraw" right={<Button label="Withdraw" kind="link" onPress={() => router.push('/money?tab=withdraw')} />}>
              <T size={28} bold>{money(s.withdrawable_now)}</T>
              <T tone="text2" size={14}>
                {s.withdrawable_explanation.charAt(0).toUpperCase() + s.withdrawable_explanation.slice(1)}.
                {lockup && lockup > new Date().toISOString().slice(0, 10) ? ` Your money is in its lock-up period until ${day(lockup)}.` : ''}
              </T>
            </Card>
            <View style={{ height: 1, backgroundColor: color.bg }} />
          </>
        )}
      </Loaded>
    </Screen>
  );
}
