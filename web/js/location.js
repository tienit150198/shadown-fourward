// A foreground GPS fix shared by chat and the stylist, held only in memory.
let fix = null;
let pending = null;
let attempted = false;
let permission = null;
const MAX_AGE = 10 * 60 * 1000;

export function currentLocation() {
  if (!fix || Date.now() - fix.at > MAX_AGE || permission?.state === 'denied') return null;
  return { lat: fix.lat, lon: fix.lon };
}

export async function requestLocation({ retry = false } = {}) {
  if (!retry && currentLocation()) return currentLocation();
  if (pending) return pending;
  if (attempted && !retry) return null;
  attempted = true;
  if (!globalThis.navigator?.geolocation) return null;
  pending = (async () => {
    try {
      permission = await navigator.permissions?.query({ name: 'geolocation' });
      if (permission?.state === 'denied') { fix = null; return null; }
      if (permission) permission.onchange = () => { fix = null; attempted = false; };
    } catch { /* Safari may not expose the permission API. */ }
    return await new Promise(resolve => {
      // Permission prompts can stay open indefinitely: never block a chat turn on them.
      const timer = setTimeout(() => resolve(null), 8500);
      navigator.geolocation.getCurrentPosition(position => {
        clearTimeout(timer);
        fix = { lat: position.coords.latitude, lon: position.coords.longitude, at: Date.now() };
        resolve(currentLocation());
      }, () => { clearTimeout(timer); fix = null; resolve(null); },
      { timeout: 8000, maximumAge: MAX_AGE, enableHighAccuracy: false });
    });
  })();
  try { return await pending; } finally { pending = null; }
}

export async function locationForRequest() {
  // Refresh an expired fix, but don't repeatedly prompt after a denial/failure.
  if (fix && !currentLocation()) attempted = false;
  return currentLocation() || await requestLocation();
}
