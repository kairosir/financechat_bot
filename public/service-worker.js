const CACHE = 'purpose-finance-shell-v14';
const SHELL = ['/', '/styles.css?v=5', '/app.js?v=9', '/manifest.webmanifest?v=3'];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key)))));
  self.clients.claim();
});

self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;
  const url = new URL(event.request.url);
  if (url.origin !== location.origin || url.pathname.startsWith('/api/')) return;

  event.respondWith(caches.match(event.request).then(cached => {
    const network = fetch(event.request).then(response => {
      if (response.ok) caches.open(CACHE).then(cache => cache.put(event.request, response.clone()));
      return response;
    });
    if (cached) {
      event.waitUntil(network.catch(() => undefined));
      return cached;
    }
    return network.catch(() => event.request.mode === 'navigate' ? caches.match('/') : Response.error());
  }));
});
