import { useEffect } from 'react';
import { StatusBar } from 'expo-status-bar';
import { Stack } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { SessionProvider, useSession } from '../lib/session';
import { checkForUpdate } from '../lib/update';
import { registerForPush } from '../lib/push';
import { color } from '../lib/theme';
import LockScreen from '../components/LockScreen';

SplashScreen.preventAutoHideAsync().catch(() => {});

function Gate() {
  const { ready, session, locked } = useSession();

  useEffect(() => {
    if (ready) SplashScreen.hideAsync().catch(() => {});
  }, [ready]);

  useEffect(() => {
    if (!ready) return;
    checkForUpdate().catch(() => {});
    if (session && !locked) registerForPush().catch(() => {});
  }, [ready, session, locked]);

  if (!ready) return null;                         // the splash stays up until the session is read
  if (session && locked) return <LockScreen />;    // tokens stay in the secure store until unlocked

  return (
    <Stack screenOptions={{
      headerStyle: { backgroundColor: color.bg }, headerTintColor: color.text, headerShadowVisible: false,
      contentStyle: { backgroundColor: color.bg },
    }}>
      <Stack.Protected guard={!!session}>
        <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
        <Stack.Screen name="how" options={{ title: 'How it is calculated' }} />
      </Stack.Protected>
      <Stack.Protected guard={!session}>
        <Stack.Screen name="login" options={{ headerShown: false }} />
        <Stack.Screen name="forgot" options={{ title: 'Reset password' }} />
        <Stack.Screen name="signup" options={{ title: 'Create an account' }} />
      </Stack.Protected>
    </Stack>
  );
}

export default function Root() {
  return (
    <SafeAreaProvider>
      <SessionProvider>
        <StatusBar style="light" />
        <Gate />
      </SessionProvider>
    </SafeAreaProvider>
  );
}
