import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';

const code = fs.readFileSync(new URL('../sw.js', import.meta.url), 'utf8');
function worker({ fetch, cache }) {
  const listeners = {};
  vm.runInNewContext(code, { URL, self: { location: new URL('https://shadow.example'), addEventListener: (name, callback) => { listeners[name] = callback; } }, caches: { open: async () => cache }, fetch });
  return listeners;
}

test('a cached app renders while the relay is still waiting, then refreshes in the background', async () => {
  let finish, response;
  const cached = { text: 'cached app' }, refreshed = { ok: true, type: 'basic', clone: () => ({ text: 'new app' }) };
  const updates = [], background = [];
  const listeners = worker({ fetch: () => new Promise(resolve => { finish = resolve; }), cache: {
    match: async () => cached, put: async (...args) => updates.push(args),
  } });
  listeners.fetch({ request: { method: 'GET', url: 'https://shadow.example/app.js' }, respondWith: value => { response = value; }, waitUntil: value => background.push(value) });
  assert.equal(await response, cached);
  assert.equal(updates.length, 0);
  finish(refreshed);
  await Promise.all(background);
  assert.equal(updates.length, 1);
});

test('tokens, user media and access-code links never enter the app cache', () => {
  const listeners = worker({ fetch: () => assert.fail('Must bypass service worker'), cache: {} });
  for (const path of ['/api/live/session', '/api/tryon/xmax/session', '/wardrobe-media/photo.jpg', '/skills-media/skill/step.jpg', '/session/id', '/?code=private']) {
    listeners.fetch({ request: { method: 'GET', url: 'https://shadow.example' + path }, respondWith: () => assert.fail('Sensitive path intercepted') });
  }
});

test('a full cache cannot discard a successful network response', async () => {
  let response;
  const fresh = { ok: true, type: 'basic', clone() { return this; } };
  const listeners = worker({ fetch: async () => fresh, cache: { match: async () => undefined, put: async () => { throw Error('quota'); } } });
  listeners.fetch({ request: { method: 'GET', url: 'https://shadow.example/app.js' }, respondWith: value => { response = value; } });
  assert.equal(await response, fresh);
});
