// Service worker for the installable app (the iOS route, since there is no
// Apple developer account — see INVESTOR-PLATFORM §8).
//
// It caches the app SHELL only, so the icon opens instantly and shows a proper
// "you're offline" state. It never caches API responses: a balance served from
// a cache is a stale number presented as current, which on a money screen is
// worse than no number at all.
const SHELL = 'avq-shell-v1';

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
