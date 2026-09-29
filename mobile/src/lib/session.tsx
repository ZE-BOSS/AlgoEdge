import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { AppState, Platform } from 'react-native';
import * as LocalAuthentication from 'expo-local-authentication';
import { loadSession, onSession, saveSession, type Session } from './api';
import { getItem, setItem } from './storage';

/**
 * Who is signed in, and whether the app is locked.
 *
 * The session (tokens) is kept in the secure store so the investor stays signed
 * in. Because a phone is easily left unlocked on a table, the app itself asks
 * for a fingerprint / face / device PIN when it opens, and again after five
 * minutes in the background. That is the "biometric unlock on a stored session"
 * from the plan: the tokens never leave the secure store; biometrics only gate
 * showing them.
 */

const LOCK_AFTER_MS = 5 * 60 * 1000;
const PREF = 'avq_biometric';

type Ctx = {
  ready: boolean;
  session: Session | null;
  locked: boolean;
  biometricAvailable: boolean;
  biometricOn: boolean;
  unlock: () => Promise<boolean>;
  setBiometric: (on: boolean) => Promise<void>;
  signIn: (s: Session) => Promise<void>;
  signOut: () => Promise<void>;
};

const SessionContext = createContext<Ctx | null>(null);

export function useSession(): Ctx {
  const c = useContext(SessionContext);
  if (!c) throw new Error('useSession outside SessionProvider');
  return c;
}

async function biometricsUsable(): Promise<boolean> {
  if (Platform.OS === 'web') return false;
  try {
    return (await LocalAuthentication.hasHardwareAsync()) && (await LocalAuthentication.isEnrolledAsync());
  } catch {
    return false;
  }
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [session, setSession] = useState<Session | null>(null);
  const [locked, setLocked] = useState(false);
  const [available, setAvailable] = useState(false);
  const [biometricOn, setOn] = useState(false);
  const backgroundedAt = useRef<number | null>(null);

  useEffect(() => {
    let live = true;
    (async () => {
      const [s, usable, pref] = await Promise.all([loadSession(), biometricsUsable(), getItem(PREF)]);
      if (!live) return;
      const on = usable && pref !== 'off';          // on by default where the phone supports it
      setSession(s); setAvailable(usable); setOn(on);
      setLocked(!!s && on);
      setReady(true);
    })();
    const off = onSession((s) => setSession(s));
    return () => { live = false; off(); };
  }, []);

  useEffect(() => {
    const sub = AppState.addEventListener('change', (state) => {
      if (state === 'background') backgroundedAt.current = Date.now();
      if (state === 'active' && backgroundedAt.current && session && biometricOn
          && Date.now() - backgroundedAt.current > LOCK_AFTER_MS) setLocked(true);
    });
    return () => sub.remove();
  }, [session, biometricOn]);

  const unlock = useCallback(async () => {
    const r = await LocalAuthentication.authenticateAsync({
      promptMessage: 'Unlock Alphavantiq', cancelLabel: 'Cancel',
    });
    if (r.success) setLocked(false);
    return r.success;
  }, []);

  const setBiometric = useCallback(async (on: boolean) => {
    if (on) {
      const r = await LocalAuthentication.authenticateAsync({ promptMessage: 'Turn on unlock' });
      if (!r.success) return;
    }
    await setItem(PREF, on ? 'on' : 'off');
    setOn(on);
  }, []);

  const signIn = useCallback(async (s: Session) => { await saveSession(s); setLocked(false); }, []);
  const signOut = useCallback(async () => { await saveSession(null); setLocked(false); }, []);

  return (
    <SessionContext.Provider value={{ ready, session, locked, biometricAvailable: available, biometricOn,
      unlock, setBiometric, signIn, signOut }}>
      {children}
    </SessionContext.Provider>
  );
}
