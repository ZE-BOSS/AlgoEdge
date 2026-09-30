import { useEffect } from 'react';
import { router, Tabs, type Href } from 'expo-router';
import { Pressable, Text, View } from 'react-native';
import * as Notifications from 'expo-notifications';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { NoticeProvider, useNotices } from '../../lib/notices';
import { PrefsProvider, usePrefs } from '../../lib/prefs';
import { color } from '../../lib/theme';
import { Icon } from '../../components/ui';

const TABS: Record<string, { title: string; icon: string }> = {
  index: { title: 'Overview', icon: 'home' },
  money: { title: 'Money', icon: 'money' },
  trades: { title: 'Trades', icon: 'trades' },
  activity: { title: 'Activity', icon: 'activity' },
  account: { title: 'Settings', icon: 'settings' },
};

/** A floating bar: the open tab becomes a gold pill with its name; the rest are icons. */
// The navigator's props, typed loosely: @react-navigation/bottom-tabs is not a direct dependency.
function TabBar({ state, navigation }: { state: any; navigation: any }) {
  const insets = useSafeAreaInsets();
  return (
    <View style={{ backgroundColor: color.bg, paddingHorizontal: 12, paddingTop: 6, paddingBottom: Math.max(insets.bottom, 10) }}>
      <View style={{ flexDirection: 'row', backgroundColor: color.surface, borderColor: color.line, borderWidth: 1,
        borderRadius: 22, padding: 6, gap: 4 }}>
        {state.routes.map((route: { key: string; name: string }, i: number) => {
          const tab = TABS[route.name];
          if (!tab) return null;
          const on = state.index === i;
          const press = () => {
            const e = navigation.emit({ type: 'tabPress', target: route.key, canPreventDefault: true });
            if (!on && !e.defaultPrevented) navigation.navigate(route.name);
          };
          return (
            <Pressable key={route.key} onPress={press} accessibilityRole="tab" accessibilityLabel={tab.title}
              accessibilityState={{ selected: on }}
              style={({ pressed }) => ({
                flex: on ? 1.9 : 1, height: 46, borderRadius: 16, flexDirection: 'row', alignItems: 'center',
                justifyContent: 'center', gap: 6, backgroundColor: on ? color.gold : pressed ? color.surface2 : 'transparent',
              })}>
              <Icon name={tab.icon} size={20} tint={on ? color.goldInk : color.text2} />
              {on && <Text numberOfLines={1} style={{ color: color.goldInk, fontWeight: '700', fontSize: 13 }}>{tab.title}</Text>}
            </Pressable>
          );
        })}
      </View>
    </View>
  );
}

/** Eye in the header: hides every balance on the screen, or shows them for now. */
function PrivacyButton() {
  const { hidden, reveal, prefs, update } = usePrefs();
  const toggle = () => (prefs.hide_balances ? reveal() : update({ hide_balances: true }).catch(() => {}));
  return (
    <Pressable onPress={toggle} hitSlop={10} style={{ paddingHorizontal: 16 }} accessibilityRole="button"
      accessibilityLabel={hidden ? 'Show balances' : 'Hide balances'}>
      <Icon name={hidden ? 'eyeOff' : 'eye'} size={22} tint={hidden ? color.gold : color.text2} />
    </Pressable>
  );
}

/** Bell in the header, with the unread count. */
function BellButton() {
  const { feed } = useNotices();
  const n = feed.unread;
  return (
    <Pressable onPress={() => router.navigate('/notifications')} hitSlop={10} style={{ paddingHorizontal: 8 }}
      accessibilityRole="button" accessibilityLabel={`Notifications${n ? `, ${n} unread` : ''}`}>
      <Icon name="bell" size={22} tint={n ? color.gold : color.text2} />
      {n > 0 && (
        <View style={{ position: 'absolute', top: -4, right: 2, minWidth: 18, height: 18, borderRadius: 9, paddingHorizontal: 4,
          backgroundColor: color.bad, alignItems: 'center', justifyContent: 'center', borderWidth: 2, borderColor: color.bg }}>
          <Text style={{ color: '#fff', fontSize: 10, fontWeight: '700' }}>{n > 9 ? '9+' : n}</Text>
        </View>
      )}
    </Pressable>
  );
}

/** Tapping a notification (even one that started the app) opens the screen it is about. */
function OpenTappedNotification() {
  const last = Notifications.useLastNotificationResponse();
  const { reload } = useNotices();
  useEffect(() => {
    const link = last?.notification.request.content.data?.link;
    if (typeof link === 'string' && link.startsWith('/')) {
      reload();
      router.navigate(link as Href);
    }
  }, [last, reload]);
  return null;
}

export default function TabsLayout() {
  return (
    <PrefsProvider>
      <NoticeProvider>
        <OpenTappedNotification />
        <Tabs tabBar={(props) => <TabBar state={props.state} navigation={props.navigation} />} screenOptions={{
          headerStyle: { backgroundColor: color.bg }, headerTintColor: color.text, headerShadowVisible: false,
          headerTitleStyle: { fontWeight: '700' },
          headerRight: () => <View style={{ flexDirection: 'row', alignItems: 'center' }}><BellButton /><PrivacyButton /></View>,
          sceneStyle: { backgroundColor: color.bg },
        }}>
          {Object.entries(TABS).map(([name, t]) => <Tabs.Screen key={name} name={name} options={{ title: t.title }} />)}
          {/* reached from the bell, not the bar */}
          <Tabs.Screen name="notifications" options={{ title: 'Notifications' }} />
        </Tabs>
      </NoticeProvider>
    </PrefsProvider>
  );
}
