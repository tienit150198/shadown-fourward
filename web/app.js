// SHADOW web app — hash router. Each screen exports mount(el, ctx) → cleanup().
import * as home from './js/screens/home.js';
import * as teach from './js/screens/teach.js';
import * as review from './js/screens/review.js';
import * as guide from './js/screens/guide.js';
import * as done from './js/screens/done.js';
import * as wardrobe from './js/screens/wardrobe.js';
import * as stylist from './js/screens/stylist.js';
import * as tryonPhoto from './js/screens/tryon-photo.js';
import * as rules from './js/screens/rules.js';
import * as tryonLive from './js/screens/tryon-live.js';
import { h } from './js/ui.js';

const routes = { home, teach, review, guide, done, wardrobe, stylist, 'tryon-photo': tryonPhoto, 'tryon-live': tryonLive, rules };
const root = document.getElementById('app');
let current = null; // { name, cleanup }

function parseHash() {
  const [raw, query = ''] = location.hash.replace(/^#\/?/, '').split('?');
  const [name, ...rest] = raw.split('/').filter(Boolean);
  return {
    name: name || 'home',
    params: rest.map((p) => { try { return decodeURIComponent(p); } catch { return p; } }),
    query: new URLSearchParams(query),
  };
}

function render() {
  const { name, params, query } = parseHash();
  const screen = routes[name];
  if (!screen) {
    history.replaceState(null, '', '#/home');
    render();
    return;
  }

  if (current?.cleanup) {
    try { current.cleanup(); } catch (err) { console.error('screen cleanup failed', err); }
  }
  current = null;

  // Close any sheet left open by the previous screen.
  document.querySelectorAll('.sheet-wrap').forEach((el) => el.remove());

  const el = h('section', { class: `screen screen-${name}` });
  root.replaceChildren(el);
  document.body.dataset.screen = name;
  window.scrollTo(0, 0);
  requestAnimationFrame(() => el.classList.add('screen-in'));

  let cleanup = null;
  try {
    cleanup = screen.mount(el, { params, query });
  } catch (err) {
    console.error(`mount ${name} failed`, err);
    el.replaceChildren(h('div', { class: 'fatal' },
      h('h2', null, 'Ối, có lỗi'),
      h('p', null, String(err?.message || err)),
      h('a', { class: 'btn btn-primary', href: '#/home' }, 'Về trang chủ')));
  }
  current = { name, cleanup: typeof cleanup === 'function' ? cleanup : null };
}

window.addEventListener('hashchange', render);
window.addEventListener('pagehide', () => {
  try { current?.cleanup?.(); } catch { /* ignore */ }
  current = null;
});
// bfcache restore: re-mount so camera / sockets come back
window.addEventListener('pageshow', (e) => { if (e.persisted) render(); });

// Shared demo link https://<host>/?code=<access code>: the server already set the cookie; drop the
// code from the address bar so it is not shown on screen / copied along.
if (new URLSearchParams(location.search).has('code')) {
  history.replaceState(null, '', location.pathname + (location.hash || '#/home'));
}
if (!location.hash) history.replaceState(null, '', '#/home');
render();

// Service worker: caches only the app shell (never /api, /session, /skills-media).
if ('serviceWorker' in navigator && window.isSecureContext) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('sw.js').catch((err) => console.info('SW registration failed', err));
  });
}
