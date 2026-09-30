import { Pressable, View } from 'react-native';
import { router, type Href } from 'expo-router';
import { ago } from '../../lib/format';
import { useNotices } from '../../lib/notices';
import { color } from '../../lib/theme';
import { Button, Card, Icon, Screen, T } from '../../components/ui';

const KIND_ICON: Record<string, string> = {
  trade_opened: 'chart', trade_published: 'trades', deposit_claimed: 'arrowIn', deposit_confirmed: 'arrowIn',
  deposit_rejected: 'arrowIn', withdrawal_received: 'arrowOut', withdrawal_approved: 'arrowOut',
  withdrawal_paid: 'arrowOut', withdrawal_declined: 'arrowOut', fee_charged: 'fee', statement: 'doc',
  password_changed: 'shield', payout_changed: 'bank', closure_requested: 'user',
};
const TINT: Record<string, string> = { trade: color.gold, deposit: color.good, withdrawal: color.info };

export default function NotificationsScreen() {
  const { feed, reload, markRead } = useNotices();
  return (
    <Screen onRefresh={reload}>
      <Card title="Notifications" right={feed.unread > 0 ? <Button label="Mark all read" kind="link" onPress={() => markRead()} /> : undefined}>
        {feed.items.length === 0 ? (
          <View style={{ alignItems: 'center', gap: 8, paddingVertical: 24 }}>
            <Icon name="bell" size={32} tint={color.muted} />
            <T tone="text2" size={14} style={{ textAlign: 'center' }}>Nothing yet. Trades, money moving in and out,
              and fees will show up here.</T>
          </View>
        ) : feed.items.map((n) => {
          const tint = TINT[n.kind.split('_')[0]] ?? color.text2;
          return (
            <Pressable key={n.id} accessibilityRole="button"
              onPress={() => { if (!n.read) markRead([n.id]); router.navigate((n.link || '/') as Href); }}
              style={({ pressed }) => ({ flexDirection: 'row', gap: 12, padding: 10, borderRadius: 12,
                backgroundColor: pressed ? color.surface2 : n.read ? 'transparent' : color.gold + '14' })}>
              <View style={{ width: 34, height: 34, borderRadius: 17, alignItems: 'center', justifyContent: 'center',
                backgroundColor: tint + '22' }}><Icon name={KIND_ICON[n.kind] || 'info'} size={16} tint={tint} /></View>
              <View style={{ flex: 1, gap: 2 }}>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                  {!n.read && <View style={{ width: 7, height: 7, borderRadius: 4, backgroundColor: color.gold }} />}
                  <T bold size={14} style={{ flexShrink: 1 }}>{n.title}</T>
                </View>
                {n.body ? <T tone="text2" size={13}>{n.body}</T> : null}
                <T tone="muted" size={11}>{ago(n.created_at)}</T>
              </View>
            </Pressable>
          );
        })}
      </Card>
    </Screen>
  );
}
