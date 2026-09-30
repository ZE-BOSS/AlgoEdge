import { Platform } from 'react-native';
import Constants from 'expo-constants';
import * as Application from 'expo-application';
import * as Device from 'expo-device';
import * as Notifications from 'expo-notifications';
import { api } from './api';
import { getItem, removeItem, setItem } from './storage';

const TOKEN_KEY = 'avq_push_token';

// Show notifications that arrive while the app is open, quietly.
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowBanner: true, shouldShowList: true, shouldPlaySound: false, shouldSetBadge: false,
  }),
});

/** Ask once, then register this phone with the backend. Silent on refusal:
 *  notifications are a convenience, every one of them is also an email. */
export async function registerForPush(): Promise<void> {
  if (Platform.OS === 'web' || !Device.isDevice) return;
  if (Platform.OS === 'android') {
    await Notifications.setNotificationChannelAsync('account', {
      name: 'Your account', importance: Notifications.AndroidImportance.HIGH,
      description: 'Trades opening and closing, money in and out, fees and statements',
    });
  }
  let { status } = await Notifications.getPermissionsAsync();
  if (status !== 'granted') status = (await Notifications.requestPermissionsAsync()).status;
  if (status !== 'granted') return;
  const projectId = (Constants.expoConfig?.extra as { eas?: { projectId?: string } } | undefined)?.eas?.projectId;
  if (!projectId) return;                 // set by `eas init`; without it Expo cannot issue a token
  const { data: token } = await Notifications.getExpoPushTokenAsync({ projectId });
  await api.registerDevice(token, Platform.OS, Application.nativeApplicationVersion);
  await setItem(TOKEN_KEY, token);
}

/** On sign-out, so the next person to use the phone gets nothing of ours. */
export async function unregisterPush(): Promise<void> {
  const token = await getItem(TOKEN_KEY);
  if (!token) return;
  try { await api.forgetDevice(token); } catch { /* signed out anyway */ }
  await removeItem(TOKEN_KEY);
}
