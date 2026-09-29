import { Platform } from 'react-native';
import * as SecureStore from 'expo-secure-store';

// Tokens live in the Android Keystore-backed secure store, never in plain
// storage. The web build (used only for previews) falls back to localStorage.
const web = Platform.OS === 'web';

export async function getItem(key: string): Promise<string | null> {
  if (web) return globalThis.localStorage?.getItem(key) ?? null;
  return SecureStore.getItemAsync(key);
}

export async function setItem(key: string, value: string): Promise<void> {
  if (web) { globalThis.localStorage?.setItem(key, value); return; }
  await SecureStore.setItemAsync(key, value);
}

export async function removeItem(key: string): Promise<void> {
  if (web) { globalThis.localStorage?.removeItem(key); return; }
  await SecureStore.deleteItemAsync(key);
}
