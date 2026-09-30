import { Tabs } from 'expo-router';
import { Pressable, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
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

export default function TabsLayout() {
  return (
    <PrefsProvider>
      <Tabs tabBar={(props) => <TabBar state={props.state} navigation={props.navigation} />} screenOptions={{
        headerStyle: { backgroundColor: color.bg }, headerTintColor: color.text, headerShadowVisible: false,
        headerTitleStyle: { fontWeight: '700' }, headerRight: () => <PrivacyButton />,
        sceneStyle: { backgroundColor: color.bg },
      }}>
        {Object.entries(TABS).map(([name, t]) => <Tabs.Screen key={name} name={name} options={{ title: t.title }} />)}
      </Tabs>
    </PrefsProvider>
  );
}
