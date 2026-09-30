import { api } from './api';

/** Browser notifications (Web Push). Works where the service worker runs: the
 *  built site in Chrome, Edge, Firefox, and Safari when added to the home screen. */

export function supported() {
  return 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
}

function keyBytes(base64) {
  const pad = '='.repeat((4 - (base64.length % 4)) % 4);
  const raw = atob((base64 + pad).replace(/-/g, '+').replace(/_/g, '/'));
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
}

async function registration() {
  return (await navigator.serviceWorker.getRegistration()) || navigator.serviceWorker.register('/sw.js');
}

/** 'on' | 'off' | 'blocked' | 'unsupported' */
export async function status() {
  if (!supported()) return 'unsupported';
  if (Notification.permission === 'denied') return 'blocked';
  const reg = await navigator.serviceWorker.getRegistration();
  const sub = reg && await reg.pushManager.getSubscription();
  return sub ? 'on' : 'off';
}

export async function enable() {
  if (!supported()) throw new Error('This browser cannot show notifications.');
  const { public_key: key } = await api.webpushKey();
  if (!key) throw new Error('Browser notifications are not set up on our side yet. Email and the app still work.');
  const permission = await Notification.requestPermission();
  if (permission !== 'granted') throw new Error('Notifications are blocked for this site in your browser settings.');
  const reg = await registration();
  await navigator.serviceWorker.ready;
  const sub = (await reg.pushManager.getSubscription())
    || await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(key) });
  await api.webpushSubscribe(sub.toJSON());
}

export async function disable() {
  const reg = supported() && await navigator.serviceWorker.getRegistration();
  const sub = reg && await reg.pushManager.getSubscription();
  if (!sub) return;
  try { await api.webpushUnsubscribe(sub.endpoint); } catch { /* removed on our side next time it fails */ }
  await sub.unsubscribe();
}
