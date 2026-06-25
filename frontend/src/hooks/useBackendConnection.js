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
  const retryRef = useRef(1000);
  const { setWsConnected } = useConnectionStore();
  const { status } = useConnectionStore();
  const user = useAuthStore((s) => s.user);
  const token = useAuthStore((s) => s.token);

  const connect = useCallback(() => {
    if (status !== 'ONLINE' || !user?.id || !token) return;

    const url = getBackendUrl().replace('http', 'ws');
    const ws = new WebSocket(`${url}/ws/${user.id}?token=${token}`);
    wsRef.current = ws;

    ws.onopen = () => {
      setWsConnected(true);
      retryRef.current = 1000; // Reset backoff
    };

    ws.onclose = () => {
      setWsConnected(false);
      // Exponential backoff reconnect: 1s → 2s → 4s → 8s → 30s cap
      const delay = Math.min(retryRef.current, 30000);
      retryRef.current = delay * 2;
      setTimeout(connect, delay);
    };

    ws.onerror = () => setWsConnected(false);

    ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        window.dispatchEvent(new CustomEvent('ws-message', { detail: data }));
      } catch {}
    };
  }, [status, user?.id]);

  useEffect(() => {
    connect();
    return () => { if (wsRef.current) wsRef.current.close(); setWsConnected(false); };
  }, [status, user?.id]);

  return wsRef;
}
