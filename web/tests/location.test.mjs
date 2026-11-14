import test from 'node:test';
import assert from 'node:assert/strict';

let sequence = 0;
async function setup(navigator) {
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: navigator });
  return import(`../js/location.js?test=${++sequence}`);
}

test('GPS requests are coalesced, cached in memory and explicitly refreshable', async () => {
  let callback, count = 0;
  const permission = { state: 'granted' };
  const location = await setup({ permissions: { query: async () => permission }, geolocation: {
    getCurrentPosition(success) { callback = success; count++; },
  } });
  const first = location.requestLocation(); const second = location.requestLocation();
  await Promise.resolve();
  callback({ coords: { latitude: 10.8, longitude: 106.6 } });
  assert.deepEqual(await first, { lat: 10.8, lon: 106.6 });
  assert.deepEqual(await second, await first); assert.equal(count, 1);
  await location.locationForRequest(); assert.equal(count, 1);
  const refresh = location.requestLocation({ retry: true }); await Promise.resolve();
  callback({ coords: { latitude: 21, longitude: 105 } });
  assert.deepEqual(await refresh, { lat: 21, lon: 105 }); assert.equal(count, 2);
  permission.state = 'denied'; permission.onchange();
  assert.equal(location.currentLocation(), null);
  assert.equal(await location.locationForRequest(), null);
});

test('denied GPS never guesses a city or repeatedly prompts', async () => {
  let calls = 0;
  const location = await setup({ permissions: { query: async () => ({ state: 'denied' }) }, geolocation: {
    getCurrentPosition() { calls++; },
  } });
  assert.equal(await location.locationForRequest(), null);
  assert.equal(await location.locationForRequest(), null);
  assert.equal(calls, 0);
});

test('GPS failure is retryable without needing a browser permission API', async () => {
  let count = 0;
  const location = await setup({ geolocation: { getCurrentPosition(success, failure) {
    if (++count === 1) failure({ code: 2 }); else success({ coords: { latitude: 0, longitude: 0 } });
  } } });
  assert.equal(await location.requestLocation(), null);
  assert.equal(await location.locationForRequest(), null);
  assert.deepEqual(await location.requestLocation({ retry: true }), { lat: 0, lon: 0 });
});
