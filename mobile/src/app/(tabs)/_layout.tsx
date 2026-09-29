import { Tabs } from 'expo-router';
import type { ColorValue } from 'react-native';
import Svg, { Path } from 'react-native-svg';
import { color } from '../../lib/theme';

const ICON: Record<string, string> = {
  index: 'M3 12l9-8 9 8M5 10v10h14V10',
  money: 'M12 3v18M17 7H9.5a3 3 0 000 6h5a3 3 0 010 6H6',
  activity: 'M4 6h16M4 12h16M4 18h10',
  trades: 'M3 17l6-6 4 4 8-8M15 7h6v6',
  account: 'M12 12a4 4 0 100-8 4 4 0 000 8zM4 21a8 8 0 0116 0',
};

function TabIcon({ name, color: c }: { name: string; color: ColorValue }) {
  return (
    <Svg width={22} height={22} viewBox="0 0 24 24" fill="none" stroke={c} strokeWidth={1.8}
      strokeLinecap="round" strokeLinejoin="round"><Path d={ICON[name]} /></Svg>
  );
}

function icon(name: string) {
  function Icon({ color: c }: { color: ColorValue }) { return <TabIcon name={name} color={c} />; }
  return Icon;
}

export default function TabsLayout() {
  return (
    <Tabs screenOptions={{
      headerStyle: { backgroundColor: color.bg }, headerTintColor: color.text, headerShadowVisible: false,
      tabBarStyle: { backgroundColor: color.surface, borderTopColor: color.line },
      tabBarActiveTintColor: color.gold, tabBarInactiveTintColor: color.muted,
      sceneStyle: { backgroundColor: color.bg },
    }}>
      <Tabs.Screen name="index" options={{ title: 'Overview', tabBarIcon: icon('index') }} />
      <Tabs.Screen name="money" options={{ title: 'Money', tabBarIcon: icon('money') }} />
      <Tabs.Screen name="activity" options={{ title: 'Activity', tabBarIcon: icon('activity') }} />
      <Tabs.Screen name="trades" options={{ title: 'Trades', tabBarIcon: icon('trades') }} />
      <Tabs.Screen name="account" options={{ title: 'Account', tabBarIcon: icon('account') }} />
    </Tabs>
  );
}
