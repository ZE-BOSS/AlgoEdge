import { useEffect, useRef, useCallback } from 'react';
import { useConnectionStore, useAuthStore } from '../store';
import { checkHealth, getBackendUrl } from '../services/api';

const BASE_INTERVAL = 5000;
const MAX_INTERVAL = 30000;

export function useBackendConnection() {
  const { status, lastSeen, setStatus } = useConnectionStore();
  const intervalRef = useRef(BASE_INTERVAL);
  const timerRef = useRef(null);

  useEffect(() => {
    const poll = async () => {
      try {
        const res = await checkHealth();
        if (res.data?.status === 'ok') {
          setStatus('ONLINE');
          intervalRef.current = BASE_INTERVAL; // Reset backoff on success
        }
      } catch {
        setStatus('OFFLINE');
        // Exponential backoff: 5s → 10s → 20s → 30s cap
        intervalRef.current = Math.min(intervalRef.current * 2, MAX_INTERVAL);
      }
      // Schedule next poll with current interval
      timerRef.current = setTimeout(poll, intervalRef.current);
    };

    poll();
    return () => { if (timerRef.current) clearTimeout(timerRef.current); };
  }, []);

  return { status, lastSeen };
}

export function useWebSocket() {
  const wsRef = useRef(null);
  const timeoutRef = useRef(null);
  const retryRef = useRef(1000);
  // Caps the speculative token refresh on an ambiguous 1006 close to one
  // attempt, so a server that is simply down cannot drive a refresh loop.
  // Reset on every successful open.
  const authRetryRef = useRef(0);
  const { setWsConnected, status } = useConnectionStore();
  const user = useAuthStore((s) => s.user);
  const token = useAuthStore((s) => s.token);
  const logout = useAuthStore((s) => s.logout);
  const refreshToken = useAuthStore((s) => s.refreshToken);

  const connect = useCallback(() => {
    if (status !== 'ONLINE' || !user?.id || !token) return;

    if (wsRef.current?.readyState === WebSocket.OPEN || wsRef.current?.readyState === WebSocket.CONNECTING) {
      return;
    }

    const url = getBackendUrl().replace('http', 'ws');
    const ws = new WebSocket(`${url}/ws/${user.id}?token=${token}`);
    wsRef.current = ws;

    ws.onopen = () => {
      setWsConnected(true);
      retryRef.current = 1000;   // Reset backoff
      authRetryRef.current = 0;  // A live socket clears the refresh budget
    };

    ws.onclose = (event) => {
      setWsConnected(false);
      wsRef.current = null;

      // Auth failure codes: 4001 (invalid token), 4003 (forbidden), 1008 (policy).
      const isAuthError = [4001, 4003, 1008].includes(event.code);
      const isAbnormalWithReason = event.code === 1006 && event.reason?.includes('403');

      // 1006 with no reason is the ambiguous case: the browser reports it for a
      // genuine network drop AND for a handshake the server refused before
      // accepting. Access tokens live 15 minutes, so an expiry mid-session
      // produced exactly this — and the old logic read it as a network blip and
      // reconnected forever with the same dead token, silently killing the log
      // stream, replay feed and backtest progress until a page reload.
      //
      // The server now accepts-then-closes so 4001 survives, but a proxy or an
      // older backend can still swallow it. So: on a bare 1006, try ONE token
      // refresh before falling back to plain reconnects. `authRetryRef` stops
      // that becoming a refresh loop against a server that is simply down.
      const isBareAbnormal = event.code === 1006 && !event.reason;

      if (isAuthError || isAbnormalWithReason) {
        if (typeof refreshToken === 'function') {
          authRetryRef.current = 0;
          refreshToken().then(() => connect()).catch(() => logout());
        } else {
          logout();
        }
        return; // Don't schedule a reconnect with a stale token
      }

      if (isBareAbnormal && authRetryRef.current < 1 && typeof refreshToken === 'function') {
        authRetryRef.current += 1;
        refreshToken()
          .then(() => connect())
          .catch(() => {
            // Not an auth problem after all — fall back to normal backoff.
            const delay = Math.min(retryRef.current, 30000);
            retryRef.current = delay * 2;
            timeoutRef.current = setTimeout(connect, delay);
          });
        return;
      }

      // Exponential backoff reconnect: 1s → 2s → 4s → 8s → 30s cap
      const delay = Math.min(retryRef.current, 30000);
      retryRef.current = delay * 2;
      timeoutRef.current = setTimeout(connect, delay);
    };

    ws.onerror = () => setWsConnected(false);

    ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        window.dispatchEvent(new CustomEvent('ws-message', { detail: data }));
      } catch {}
    };
  }, [status, user?.id, token, setWsConnected, logout, refreshToken]);

  useEffect(() => {
    connect();
    return () => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
      if (wsRef.current) {
        wsRef.current.onclose = null; // Prevent reconnect on unmount
        wsRef.current.close();
        wsRef.current = null;
      }
      setWsConnected(false);
    };
  }, [connect, setWsConnected]);

  return wsRef;
}
