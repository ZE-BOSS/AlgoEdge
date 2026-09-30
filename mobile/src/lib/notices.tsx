import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { AppState } from 'react-native';
import * as Notifications from 'expo-notifications';
import { api, type NoticeFeed } from './api';

type Ctx = { feed: NoticeFeed; reload: () => void; markRead: (ids?: string[]) => Promise<void> };
const NoticeContext = createContext<Ctx>({ feed: { unread: 0, items: [] }, reload: () => {}, markRead: async () => {} });
const POLL_MS = 45_000;

/** The notification feed, shared by the header bell and the notifications screen.
 *  Refreshes on a timer while the app is open, when it comes back to the front,
 *  and the moment a push arrives. */
export function NoticeProvider({ children }: { children: ReactNode }) {
  const [feed, setFeed] = useState<NoticeFeed>({ unread: 0, items: [] });
  const reload = useCallback(() => { api.notifications().then(setFeed).catch(() => {}); }, []);
  useEffect(() => {
    reload();
    const id = setInterval(() => { if (AppState.currentState === 'active') reload(); }, POLL_MS);
    const app = AppState.addEventListener('change', (st) => { if (st === 'active') reload(); });
    const pushed = Notifications.addNotificationReceivedListener(() => reload());
    return () => { clearInterval(id); app.remove(); pushed.remove(); };
  }, [reload]);
  const markRead = useCallback(async (ids?: string[]) => {
    try { await api.readNotifications(ids); } finally { reload(); }
  }, [reload]);
  const value = useMemo(() => ({ feed, reload, markRead }), [feed, reload, markRead]);
  return <NoticeContext.Provider value={value}>{children}</NoticeContext.Provider>;
}

export const useNotices = () => useContext(NoticeContext);
