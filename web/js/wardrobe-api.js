// REST client for "Trợ lý tủ đồ" (PLAN.md Phần 2 → API). Same origin, JSON.
// Responses are normalised defensively so the UI keeps working if the backend shape drifts a bit.
import { ApiError, fetchWithAccess, statusMessage } from './api.js';

function detailToText(detail) {
  if (!detail) return '';
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map((d) => (d && d.msg) || JSON.stringify(d)).join('; ');
  if (typeof detail === 'object') return detail.message || JSON.stringify(detail);
  return String(detail);
}

async function request(method, path, { body, timeoutMs = 20000, signal } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(new DOMException('timeout', 'TimeoutError')), timeoutMs);
  const onAbort = () => controller.abort(signal.reason);
  if (signal?.aborted) controller.abort(signal.reason);
  signal?.addEventListener('abort', onAbort, { once: true });
  let res;
  try {
    res = await fetchWithAccess(path, {
      method,
      headers: body !== undefined ? { 'Content-Type': 'application/json', Accept: 'application/json' } : { Accept: 'application/json' },
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: controller.signal,
      cache: 'no-store',
    });
  } catch (err) {
    if (signal?.aborted) throw err;
    if (controller.signal.aborted) {
      throw new ApiError(`${method} ${path} timed out`, { userMessage: 'Máy chủ phản hồi quá lâu. Thử lại nhé.' });
    }
    throw new ApiError(`${method} ${path} failed: ${err.message}`, { userMessage: 'Không kết nối được máy chủ. Kiểm tra mạng.' });
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', onAbort);
  }
  const text = await res.text();
  let data = null;
  if (text) {
    try { data = JSON.parse(text); } catch { data = text; }
  }
  if (!res.ok) {
    const detail = detailToText(data && typeof data === 'object' ? (data.message ?? data.detail ?? data.error) : data);
    throw new ApiError(`${method} ${path} → ${res.status} ${detail}`, {
      status: res.status,
      userMessage: statusMessage(res.status, detail) || (detail ? `Lỗi ${res.status}: ${detail}` : `Lỗi máy chủ (${res.status})`),
    });
  }
  return data;
}

const enc = encodeURIComponent;

function asList(data, key) {
  if (Array.isArray(data)) return data;
  if (data && Array.isArray(data[key])) return data[key];
  if (data && Array.isArray(data.data)) return data.data;
  return [];
}

function asItem(data) {
  if (data && typeof data === 'object' && data.item && typeof data.item === 'object') return data.item;
  return data;
}

function query(params) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : '';
}

export const wardrobeApi = {
  listItems: async () => asList(await request('GET', '/api/wardrobe/items'), 'items'),
  /** {image, name?, category?} → item (AI labelled) */
  addItem: async (body, opts = {}) => asItem(await request('POST', '/api/wardrobe/items', { body, timeoutMs: 90000, ...opts })),
  updateItem: async (id, item) => asItem(await request('PUT', `/api/wardrobe/items/${enc(id)}`, { body: item, timeoutMs: 20000 })),
  deleteItem: (id) => request('DELETE', `/api/wardrobe/items/${enc(id)}`),
  listRules: async () => asList(await request('GET', '/api/wardrobe/rules'), 'rules'),
  /** {text, taught_by} → rule */
  addRule: async (body) => {
    const data = await request('POST', '/api/wardrobe/rules', { body });
    return data && data.rule ? data.rule : data;
  },
  deleteRule: (id) => request('DELETE', `/api/wardrobe/rules/${enc(id)}`),
  /** {city?, date?, lat?, lon?} → {city, date, t_min, t_max, rain_prob, code, summary_vi} */
  weather: (params, opts = {}) => request('GET', `/api/weather${query(params)}`, { timeoutMs: 15000, ...opts }),
  /** {event, city?, lat?, lon?, date?, current_image?, anchor_item_id?, notes?} → {weather, wearing, outfits, rules_used} */
  suggest: (body, opts = {}) => request('POST', '/api/stylist/suggest', { body, timeoutMs: 150000, ...opts }),
  tryonConfig: () => request('GET', '/api/tryon/config', { timeoutMs: 8000 }),
  tryonArConfig: () => request('GET', '/api/tryon/ar/config', { timeoutMs: 8000 }),
  /** {person_image, item_ids} → {image_url, provider, seconds, note?} */
  tryonPhoto: (body, opts = {}) => request('POST', '/api/tryon/photo', { body, timeoutMs: 300000, ...opts }),
};

/** <img src> for an item (image_url preferred, else the relative `image` path under /wardrobe-media/). */
export function itemImageUrl(item) {
  if (!item) return null;
  const url = item.image_url || item.thumb_url || item.image;
  if (!url) return null;
  if (/^(https?:|data:|blob:|\/)/.test(url)) return url;
  return `/wardrobe-media/${url.replace(/^\.?\//, '')}`;
}

/** Normalises a media URL returned by try-on (image_url or inline data). */
export function resultImageUrl(res) {
  if (!res) return null;
  const url = res.image_url || res.url || res.image || res.result_url;
  if (!url) return null;
  if (/^(https?:|data:|blob:|\/)/.test(url)) return url;
  return `/wardrobe-media/${url.replace(/^\.?\//, '')}`;
}
