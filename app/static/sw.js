// Service worker: makes the web app installable and keeps the app shell available offline.
// Attendance data and camera frames always go to the server (never cached).
const VERSION = 'attendance-v2';
const SHELL = [
  '/offline',
  '/static/css/app.css',
  '/static/js/camera.js',
  '/static/js/live.js',
  '/static/vendor/bootstrap/bootstrap.min.css',
  '/static/vendor/bootstrap/bootstrap.bundle.min.js',
  '/static/vendor/bootstrap-icons/bootstrap-icons.min.css',
  '/static/vendor/bootstrap-icons/fonts/bootstrap-icons.woff2',
  '/static/vendor/chartjs/chart.umd.min.js',
  '/static/icons/icon-192.png',
];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(VERSION).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== VERSION).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  const req = e.request;
  const url = new URL(req.url);
  if (req.method !== 'GET' || url.origin !== location.origin || url.pathname.startsWith('/api/')) return;
  if (url.pathname.startsWith('/static/')) {
    // static files: cache first, refresh in the background
    e.respondWith(caches.open(VERSION).then(async cache => {
      const hit = await cache.match(req);
      const net = fetch(req).then(r => { if (r.ok) cache.put(req, r.clone()); return r; }).catch(() => hit);
      return hit || net;
    }));
  } else if (req.mode === 'navigate') {
    // pages: always live data from the server; show an offline page when unreachable
    e.respondWith(fetch(req).catch(() => caches.match('/offline')));
  }
});
