// SHADOW service worker — caches the app shell only.
// Network-first (so a fresh deploy is picked up immediately), cache fallback when offline.
// Never touches /api, /session or /skills-media, and ignores cross-origin requests (CDN, fonts).
const CACHE = 'shadow-shell-v4';
const SHELL = [
  './',
  'index.html',
  'styles.css',
  'conversation.css',
  'app.js',
  'manifest.webmanifest',
  'js/api.js',
  'js/ws.js',
  'js/camera.js',
  'js/speech.js',
  'js/cloud-voice.js',
  'js/cv.js',
  'js/ui.js',
  'js/store.js',
  'js/screens/home.js',
  'js/screens/conversation.js',
  'js/screens/image-studio.js',
  'js/screens/teach.js',
  'js/screens/review.js',
  'js/screens/guide.js',
  'js/screens/done.js',
  'wardrobe.css',
  'js/wardrobe-api.js',
  'js/wardrobe-ui.js',
  'js/screens/wardrobe.js',
  'js/screens/stylist.js',
  'js/screens/tryon-photo.js',
  'js/screens/rules.js',
  'tryon-live.css',
  'js/vidu-live.js',
  'js/tryon-pose.js',
  'js/screens/tryon-live.js',
  'js/screens/tryon-ar.js',
  'icons/icon.svg',
  'icons/icon-180.png',
  'icons/icon-192.png',
  'icons/icon-512.png',
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
    try {
      const response = await fetch(request);
      if (response.ok && response.type === 'basic') {
        const copy = response.clone();
        caches.open(CACHE).then((cache) => cache.put(request, copy)).catch(() => {});
      }
      return response;
    } catch (err) {
      const cached = await caches.match(request, { ignoreSearch: true });
      if (cached) return cached;
      if (request.mode === 'navigate') {
        const shell = await caches.match('index.html');
        if (shell) return shell;
      }
      throw err;
    }
  })());
});
