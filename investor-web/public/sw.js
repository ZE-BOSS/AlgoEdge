// Service worker for the installable app (the iOS route, since there is no
// Apple developer account — see INVESTOR-PLATFORM §8).
//
// It caches the app SHELL only, so the icon opens instantly and shows a proper
// "you're offline" state. It never caches API responses: a balance served from
// a cache is a stale number presented as current, which on a money screen is
// worse than no number at all.
const SHELL = 'avq-shell-v2';

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(['/', '/manifest.webmanifest', '/mark.svg'])));
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((keys) =>
    Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k)))));
  self.clients.claim();
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.pathname.startsWith('/api')
      || url.origin !== self.location.origin) return;        // network only
  if (e.request.mode === 'navigate') {
    // network first; the cached shell only when offline
    e.respondWith(fetch(e.request).catch(() => caches.match('/')));
    return;
  }
  e.respondWith(caches.match(e.request).then((hit) => hit || fetch(e.request).then((res) => {
    if (res.ok && url.pathname.startsWith('/assets/')) {
      const copy = res.clone();
      caches.open(SHELL).then((c) => c.put(e.request, copy));
    }
    return res;
  })));
});

// Notifications pushed by the server (Web Push). The payload carries a title,
// a short line and the in-app page to open; never more than a lock screen may show.
self.addEventListener('push', (e) => {
  let msg = {};
  try { msg = e.data ? e.data.json() : {}; } catch { msg = { title: 'Alphavantiq', body: e.data && e.data.text() }; }
  const data = msg.data || {};
  e.waitUntil(self.registration.showNotification(msg.title || 'Alphavantiq', {
    body: msg.body || '', icon: '/icon-192.png', badge: '/icon-192.png',
    tag: data.kind ? `${data.kind}-${Date.now()}` : undefined, data,
  }));
});

self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const link = (e.notification.data && e.notification.data.link) || '/';
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((list) => {
    for (const c of list) {
      if (c.url.startsWith(self.location.origin)) {
        c.focus();
        return c.navigate ? c.navigate(link) : undefined;
      }
    }
    return self.clients.openWindow(link);
  }));
});
