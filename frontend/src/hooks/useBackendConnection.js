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
      retryRef.current = 1000; // Reset backoff
    };

    ws.onclose = (event) => {
      setWsConnected(false);
      wsRef.current = null;

      // Auth failure codes: 4001 (invalid token), 4003 (forbidden), 1008 (policy violation)
      // HTTP 403 manifests as close code 1006 (abnormal) or the server sends 4001/4003
      const isAuthError = [4001, 4003, 1008].includes(event.code);
      const isAbnormalWithReason = event.code === 1006 && event.reason?.includes('403');

      if (isAuthError || isAbnormalWithReason) {
        // Try refresh once; if unavailable or fails, logout
        if (typeof refreshToken === 'function') {
          refreshToken().catch(() => logout());
        } else {
          logout();
        }
        return; // Don't schedule reconnect with stale token
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
