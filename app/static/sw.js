/* Budget Buddy service worker (v10.13) — deliberately MINIMAL, no offline
 * promises. Only same-origin GETs under /static/ are handled, with
 * stale-while-revalidate (NOT cache-first: htmx.min.js and apexcharts.min.js
 * carry no cache-bust param, so SWR keeps them at most one page-load stale
 * across deploys; style.css?v=<hash> is safe either way). Everything else —
 * pages, POSTs, auth — falls straight through to the network untouched.
 * Served at /sw.js by a Flask route so its scope covers '/', which
 * installability requires. Bump the cache name to force a purge. */
// v4 (#234): chart.umd.min.js was replaced by apexcharts.min.js — the bump
// evicts the retired 208KB library instead of leaving it cached forever.
const CACHE = 'bb-static-v4';

self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k.startsWith('bb-static-') && k !== CACHE)
            .map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== self.location.origin ||
      !url.pathname.startsWith('/static/')) {
    return; // no respondWith — pure network passthrough
  }
  event.respondWith(
    caches.open(CACHE).then((cache) =>
      cache.match(event.request).then((cached) => {
        const refresh = fetch(event.request).then((resp) => {
          if (resp.ok) cache.put(event.request, resp.clone());
          return resp;
        });
        return cached || refresh;
      })
    )
  );
});

/* Bill-due push reminders (#33). The daily job sends {title, body, url}; this
 * renders it and, on tap, focuses an already-open tab rather than piling up new
 * ones. iOS only delivers these to a PWA added to the home screen. */
self.addEventListener('push', (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch (e) {
    payload = { title: 'Budget Buddy', body: event.data ? event.data.text() : '' };
  }
  const title = payload.title || 'Budget Buddy';
  event.waitUntil(self.registration.showNotification(title, {
    body: payload.body || '',
    icon: '/static/icons/icon-192.png',
    badge: '/static/icons/icon-192.png',
    /* Collapses repeats of the same bill into one notification rather than
     * stacking them if a device comes back online holding several. */
    tag: payload.url || 'bb-reminder',
    data: { url: payload.url || '/' }
  }));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true })
      .then((windows) => {
        for (const client of windows) {
          if (client.url.includes(target) && 'focus' in client) return client.focus();
        }
        for (const client of windows) {
          if ('focus' in client) { client.navigate(target); return client.focus(); }
        }
        return self.clients.openWindow(target);
      })
  );
});
