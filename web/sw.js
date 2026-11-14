// SKI service worker — caches the app shell only.
// Cached shell renders immediately; refresh it in the background.
// Never touches /api, /session or /skills-media, and ignores cross-origin requests (CDN, fonts).
const CACHE = 'shadow-shell-v30-camera-continuity';
const SHELL = [
  './',
  'index.html',
  'styles.css',
  'app.js',
  'manifest.webmanifest',
  'js/live-pcm-worklet.js',
  'icons/icon-32.png?v=ski-1',
  'icons/icon-180.png?v=ski-1',
  'icons/icon-192.png?v=ski-1',
  'icons/icon-512.png?v=ski-1',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((cache) => cache.addAll(SHELL))
      .catch(() => { /* partial cache is fine */ })
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

function isBypassed(url) {
  return url.origin !== self.location.origin
    || url.pathname.startsWith('/api')
    || url.pathname.startsWith('/session')
    || url.pathname.startsWith('/skills-media')
    || url.pathname.startsWith('/wardrobe-media')
    || url.searchParams.has('code'); // demo access-code link: never store the code in the cache
}

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (isBypassed(url)) return; // let the network handle it, uncached

  event.respondWith((async () => {
    const cache = await caches.open(CACHE).catch(() => null);
    if (!cache) return fetch(request);
    const cached = await cache.match(request);
    const refresh = fetch(request).then(async response => {
      if (response.ok && response.type === 'basic') await cache.put(request, response.clone()).catch(() => {});
      return response;
    });
    if (cached && !['/app.js','/styles.css','/','/index.html'].includes(url.pathname) && !url.pathname.startsWith('/js/')) {
      event.waitUntil(refresh.catch(() => {}));
      return cached;
    }
    try {
      return await refresh;
    } catch (err) {
      if (cached) return cached;
      if (request.mode === 'navigate') {
        const shell = await cache.match('index.html');
        if (shell) return shell;
      }
      throw err;
    }
  })());
});
