// Decart realtime virtual try-on helpers (model `lucy-vton-latest`, @decartai/sdk 0.2.14).
// Ported from DecartAI/tryon-examples (MIT): examples/mobile (connect + setImage + quality),
// examples/outfit-builder (top + bottom → one stacked garment image + one outfit prompt).
//
// SDK surface used (sdk repo tag v0.2.14, packages/sdk/src):
//   createDecartClient({ apiKeyProvider })                       create-client.ts
//   models.realtime('lucy-vton-latest')                          shared/model.ts
//   client.realtime.checkConnectivity() → {quality, metrics, reasons}   realtime/preflight-*.ts
//   client.realtime.connect(stream, { model, onRemoteStream, onConnectionChange,
//       onConnectionQuality, onQueuePosition, onCapacityWait, retries, speed }) → rt   realtime/client.ts
//   rt.setImage(blob, { prompt, enhance: false, timeout })      realtime/client.ts
//   rt.on('connectionChange'|'error'|'sessionEnded'|'generationTick'|'connectionQuality', fn)
//   rt.disconnect()
//
// Dev flag: `window.__DECART_MOCK__` (or `?mock=1` in the hash, or localStorage
// `shadow.decartMock` = '1') swaps the SDK for a fake that echoes the local camera back as the
// "remote" stream (black for the first ~1.2 s, like the real first frames), records every call
// in `window.__DECART_MOCK_LOG__`, emits quality reports + generation ticks, and needs no token.
// `window.__DECART_MOCK__` may also be an object exposing `createDecartClient` / `models`
// to inject a custom fake.

export const SDK_VERSION = '0.2.14';
export const SDK_URLS = [
  `https://cdn.jsdelivr.net/npm/@decartai/sdk@${SDK_VERSION}/+esm`,
  `https://esm.sh/@decartai/sdk@${SDK_VERSION}`, // fallback build, same exports
];
export const LIVE_MODEL = 'lucy-vton-latest';

let sdkPromise = null;

export function isMockEnabled() {
  try {
    if (window.__DECART_MOCK__) return true;
    const q = new URLSearchParams(location.hash.split('?')[1] || '');
    if (q.get('mock') === '1') return true;
    return localStorage.getItem('shadow.decartMock') === '1';
  } catch {
    return false;
  }
}

/** Loads the Decart SDK ESM module (or the dev mock). Rejects with a Vietnamese userMessage. */
export function loadDecartSdk() {
  if (isMockEnabled()) {
    const injected = window.__DECART_MOCK__;
    return Promise.resolve(injected && typeof injected === 'object' && injected.createDecartClient ? injected : createFakeSdk());
  }
  if (!sdkPromise) {
    sdkPromise = (async () => {
      let lastErr = null;
      for (const url of SDK_URLS) {
        try {
          const mod = await import(/* @vite-ignore */ url);
          if (typeof mod.createDecartClient === 'function' && mod.models) return mod;
          lastErr = new Error(`unexpected exports from ${url}`);
        } catch (err) {
          lastErr = err;
          console.info('[decart] SDK load failed from', url, err?.message);
        }
      }
      const e = new Error(`Decart SDK load failed: ${lastErr?.message || lastErr}`);
      e.userMessage = 'Không tải được thư viện Decart. Kiểm tra kết nối mạng rồi thử lại.';
      throw e;
    })().catch((err) => { sdkPromise = null; throw err; });
  }
  return sdkPromise;
}

// ---------------------------------------------------------------------------------------------
// Error → Vietnamese
// ---------------------------------------------------------------------------------------------

const SESSION_END_VI = {
  insufficient_credits: 'Tài khoản Decart đã hết credit. Nạp thêm tại platform.decart.ai rồi thử lại.',
  moderation_violation: 'Decart đã dừng phiên vì nội dung bị kiểm duyệt chặn.',
  policy_violation: 'Decart từ chối phiên (vi phạm chính sách).',
  session_limit: 'Tài khoản Decart đang có quá nhiều phiên cùng lúc. Đóng các phiên khác rồi thử lại.',
};

export function sessionEndMessage(reason) {
  return SESSION_END_VI[reason] || `Decart đã kết thúc phiên${reason ? ` (${reason})` : ''}.`;
}

/** Maps a DecartSDKError ({code, message}) or any Error to a Vietnamese sentence. */
export function decartErrorMessage(err) {
  if (!err) return 'Có lỗi khi kết nối Decart.';
  if (err.userMessage) return err.userMessage;
  const code = err.code || '';
  const msg = String(err.message || err);
  switch (code) {
    case 'INVALID_API_KEY':
    case 'TOKEN_INVALID':
      return 'Token Decart không hợp lệ. Kiểm tra DECART_API_KEY trên máy chủ.';
    case 'TOKEN_EXPIRED':
      return 'Token Decart đã hết hạn. Bấm thử lại để lấy token mới.';
    case 'WEBRTC_ICE_ERROR':
      return 'Mạng đang chặn kết nối video trực tiếp (WebRTC). Thử Wi-Fi khác hoặc 4G.';
    case 'WEBRTC_TIMEOUT_ERROR':
      return 'Kết nối tới Decart quá lâu. Kiểm tra mạng rồi thử lại.';
    case 'WEBRTC_WEBSOCKET_ERROR':
      return 'Không mở được kết nối tới máy chủ Decart. Kiểm tra mạng.';
    case 'WEBRTC_SIGNALING_ERROR':
      return 'Lỗi bắt tay kết nối với Decart. Thử lại sau giây lát.';
    case 'WEBRTC_SERVER_ERROR':
      if (/capacity/i.test(msg)) return 'Máy chủ Decart đang quá tải. Thử lại sau ít phút.';
      return `Máy chủ Decart báo lỗi: ${msg}`;
    default:
      if (/401|unauthori[sz]ed|invalid api key/i.test(msg)) return 'Token Decart không hợp lệ. Kiểm tra DECART_API_KEY trên máy chủ.';
      if (/insufficient|credit/i.test(msg)) return SESSION_END_VI.insufficient_credits;
      return `Không kết nối được Decart: ${msg}`;
  }
}

// ---------------------------------------------------------------------------------------------
// Outfit → garment image + prompt
// ---------------------------------------------------------------------------------------------

/** Categories the live model can render (shoes/accessories are skipped for now). */
export const LIVE_CATEGORIES = ['top', 'bottom', 'dress', 'outerwear'];

const COLOR_EN = [
  ['xanh navy', 'navy'], ['xanh bạc hà', 'mint green'], ['xanh cổ vịt', 'teal'], ['cam đào', 'peach'], ['xanh than', 'charcoal navy'], ['xanh dương', 'blue'], ['xanh da trời', 'sky blue'], ['xanh lá', 'green'], ['xanh rêu', 'olive green'],
  ['xanh ngọc', 'teal'], ['xanh lam', 'blue'], ['xanh', 'blue'], ['trắng', 'white'], ['đen', 'black'], ['xám', 'grey'], ['ghi', 'grey'],
  ['be', 'beige'], ['kem', 'cream'], ['nâu', 'brown'], ['đỏ đô', 'burgundy'], ['đỏ', 'red'], ['hồng', 'pink'], ['vàng', 'yellow'],
  ['cam', 'orange'], ['tím', 'purple'], ['bạc', 'silver'], ['vàng kim', 'gold'], ['nhiều màu', 'multicolor'],
];
const PATTERN_EN = { 'trơn': '', 'kẻ': 'striped', 'kẻ sọc': 'striped', 'sọc': 'striped', 'caro': 'plaid', 'kẻ caro': 'plaid', 'hoa': 'floral', 'chấm bi': 'polka dot', 'họa tiết': 'printed', 'hoạ tiết': 'printed', 'in hình': 'graphic print' };
const MATERIAL_EN = { 'cotton': 'cotton', 'vải': '', 'jean': 'denim', 'bò': 'denim', 'denim': 'denim', 'len': 'knit wool', 'lụa': 'silk', 'da': 'leather', 'kaki': 'khaki cotton', 'linen': 'linen', 'lanh': 'linen', 'thun': 'jersey', 'nỉ': 'fleece', 'voan': 'chiffon', 'polyester': 'polyester' };
// Order matters: more specific first.
const GARMENT_EN = [
  ['áo sơ mi', 'button-down shirt'], ['sơ mi', 'button-down shirt'], ['áo thun', 't-shirt'], ['áo phông', 't-shirt'], ['áo polo', 'polo shirt'],
  ['áo len', 'sweater'], ['áo cardigan', 'cardigan'], ['cardigan', 'cardigan'], ['hoodie', 'hoodie'], ['áo nỉ', 'sweatshirt'],
  ['áo blazer', 'blazer'], ['blazer', 'blazer'], ['áo vest', 'suit jacket'], ['áo măng tô', 'trench coat'], ['áo khoác jean', 'denim jacket'],
  ['áo khoác bò', 'denim jacket'], ['áo phao', 'puffer jacket'], ['áo gió', 'windbreaker jacket'], ['áo khoác', 'jacket'], ['áo dài', 'ao dai dress'],
  ['áo kiểu', 'blouse'], ['áo croptop', 'crop top'], ['áo hai dây', 'camisole top'], ['áo ba lỗ', 'tank top'],
  ['quần jean', 'jeans'], ['quần jeans', 'jeans'], ['quần bò', 'jeans'], ['quần tây', 'tailored trousers'], ['quần âu', 'tailored trousers'],
  ['quần kaki', 'chino trousers'], ['quần short', 'shorts'], ['quần đùi', 'shorts'], ['quần ống rộng', 'wide-leg trousers'], ['quần jogger', 'jogger pants'],
  ['quần thể thao', 'track pants'], ['quần legging', 'leggings'], ['chân váy', 'skirt'], ['váy liền', 'dress'], ['đầm', 'dress'], ['váy', 'dress'],
];
const CATEGORY_NOUN = { top: 'top', bottom: 'trousers', dress: 'dress', outerwear: 'jacket' };

const norm = (s) => String(s || '').toLowerCase().normalize('NFC').trim();

function translateColors(colors) {
  const out = [];
  for (const c of (Array.isArray(colors) ? colors : [colors]).map(norm).filter(Boolean)) {
    const hit = COLOR_EN.find(([vi]) => c === vi) || COLOR_EN.find(([vi]) => c.includes(vi));
    const en = hit ? hit[1] : (/^[a-z\s-]+$/.test(c) ? c : null);
    if (en && !out.includes(en)) out.push(en);
  }
  return out.slice(0, 2);
}

function lookup(map, value) {
  const v = norm(value);
  if (!v) return '';
  if (v in map) return map[v];
  const key = Object.keys(map).find((k) => k && v.includes(k));
  if (key !== undefined) return map[key];
  return /^[a-z\s-]+$/.test(v) ? v : '';
}

const PLURAL_HEAD = /\b(jeans|trousers|pants|shorts|leggings|sneakers|shoes|boots|chinos|joggers)\b/i;

/** "Dark blue denim jeans with gold stitching." → "dark blue denim jeans with gold stitching";
 *  "Beige pleated A-line midi skirt" → "a beige pleated A-line midi skirt". */
function nounPhrase(text) {
  let t = String(text).trim().replace(/[.\s]+$/, '').replace(/\s+/g, ' ').slice(0, 140);
  // lower-case the first word unless it looks like an acronym ("A-line", "USA")
  t = t.replace(/^([A-Z])([a-z])/, (m, a, b) => a.toLowerCase() + b);
  if (/^(a|an|the|one|two|pair of|a pair of)\b/i.test(t)) return t;
  const head = t.split(/\b(with|in|and|featuring)\b/i)[0];
  if (PLURAL_HEAD.test(head)) return t;
  return `${/^[aeiou]/i.test(t) ? 'an' : 'a'} ${t}`;
}

/** English noun phrase for one wardrobe item, e.g. "a navy striped cotton button-down shirt". */
export function describeItemEn(item) {
  if (!item) return '';
  // Prefer an English description if the backend ever provides one.
  const direct = item.prompt_en || item.description_en || item.tryon_prompt_en;
  if (direct && typeof direct === 'string' && direct.trim()) return nounPhrase(direct);
  const name = norm(item.name);
  const garmentHit = GARMENT_EN.find(([vi]) => name.includes(vi));
  const garment = garmentHit ? garmentHit[1] : (CATEGORY_NOUN[item.category] || 'garment');
  const colors = translateColors(item.colors);
  const pattern = lookup(PATTERN_EN, item.pattern);
  let material = lookup(MATERIAL_EN, item.material);
  if (material && garment.includes(material)) material = '';
  const parts = [colors.join(' and '), pattern, material, garment].filter(Boolean).join(' ');
  return nounPhrase(parts);
}

/**
 * Normalises a list of items into live slots. Later items win per slot; a dress replaces
 * top + bottom and vice versa. Returns {outerwear, top, bottom, dress} (items or undefined).
 */
export function slotsFromItems(items) {
  const slots = {};
  for (const it of items) {
    if (!it || !LIVE_CATEGORIES.includes(it.category)) continue;
    if (it.category === 'dress') { delete slots.top; delete slots.bottom; }
    if (it.category === 'top' || it.category === 'bottom') delete slots.dress;
    slots[it.category] = it;
  }
  return slots;
}

/** Ordered list of the items that make up the garment image (outerwear, top|dress, bottom). */
export function slotItems(slots) {
  return [slots.outerwear, slots.dress || slots.top, slots.dress ? null : slots.bottom].filter(Boolean);
}

/**
 * Builds the try-on prompt following the substitute/add patterns from the tryon-examples README
 * and outfit-builder /api/outfit-prompt ("Substitute the current outfit with X on top and Y on
 * the bottom"). Generated locally from wardrobe labels (no LLM round trip at switch time).
 */
export function buildOutfitPrompt(slots) {
  const outer = slots.outerwear && describeItemEn(slots.outerwear);
  const top = slots.top && describeItemEn(slots.top);
  const bottom = slots.bottom && describeItemEn(slots.bottom);
  const dress = slots.dress && describeItemEn(slots.dress);
  const upper = outer && (top || dress)
    ? `${outer} worn open over ${top || dress}`
    : (outer || top || dress);
  if (dress) return `Substitute the person's current clothing with ${upper}`;
  if ((outer || top) && bottom) return `Substitute the current outfit with ${upper} on top and ${bottom} on the bottom`;
  if (bottom) return `Substitute the current bottoms with ${bottom}`;
  if (outer && !top) return `Add ${outer} over the person's current top`;
  if (upper) return `Substitute the current top with ${upper}`;
  return '';
}

function loadImg(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.decoding = 'async';
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`Không tải được ảnh đồ: ${src}`));
    img.src = src;
  });
}

function canvasToBlob(canvas, quality = 0.9) {
  return new Promise((resolve, reject) => {
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error('Không tạo được ảnh đồ'))), 'image/jpeg', quality);
  });
}

/**
 * Garment reference image for the slots: one image as-is (on white, ≥512 px wide), or several
 * stacked vertically at 512 px width on a white background — port of outfit-builder
 * lib/image-utils.ts combineClothingImages().
 * @param {(item) => string} srcOf  image URL for an item (same origin, so the canvas stays clean)
 */
export async function composeGarmentImage(items, srcOf) {
  const imgs = await Promise.all(items.map((it) => loadImg(srcOf(it))));
  const targetWidth = imgs.length === 1 ? Math.max(512, Math.min(1024, imgs[0].naturalWidth || 512)) : 512;
  const heights = imgs.map((img) => Math.round((img.naturalHeight || 512) * (targetWidth / (img.naturalWidth || 512))));
  const canvas = document.createElement('canvas');
  canvas.width = targetWidth;
  canvas.height = heights.reduce((a, b) => a + b, 0);
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#FFFFFF';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  let y = 0;
  imgs.forEach((img, i) => { ctx.drawImage(img, 0, y, targetWidth, heights[i]); y += heights[i]; });
  return canvasToBlob(canvas);
}

// ---------------------------------------------------------------------------------------------
// Fake SDK (dev / tests)
// ---------------------------------------------------------------------------------------------

export function createFakeSdk({ blackMs = 1200, connectMs = 700 } = {}) {
  const log = (window.__DECART_MOCK_LOG__ = window.__DECART_MOCK_LOG__ || []);
  const record = (method, data = {}) => { log.push({ method, t: Math.round(performance.now()), ...data }); };

  function createDecartClient(opts = {}) {
    record('createDecartClient', { hasProvider: typeof opts.apiKeyProvider === 'function' });
    return {
      realtime: {
        async checkConnectivity() {
          record('checkConnectivity');
          await new Promise((r) => setTimeout(r, 300));
          return { quality: 'good', metrics: { transport: 'udp', rttMs: 42 }, reasons: [] };
        },
        async connect(stream, options) {
          record('connect', { model: options?.model?.name, speed: options?.speed || null });
          const listeners = new Map();
          const emit = (ev, data) => { (listeners.get(ev) || []).forEach((fn) => { try { fn(data); } catch (e) { console.error(e); } }); };
          let state = 'connecting';
          const setState = (s) => { state = s; options.onConnectionChange?.(s); emit('connectionChange', s); };
          options.onConnectionChange?.('connecting');
          await new Promise((r) => setTimeout(r, connectMs));

          // Remote = canvas that is black for `blackMs`, then echoes the local camera.
          const src = document.createElement('video');
          src.muted = true; src.playsInline = true; src.srcObject = stream;
          src.play().catch(() => {});
          const canvas = document.createElement('canvas');
          canvas.width = 360; canvas.height = 640;
          const ctx = canvas.getContext('2d');
          const t0 = performance.now();
          let garment = null; // ImageBitmap of the last setImage
          let disconnected = false;
          let raf = 0;
          const draw = () => {
            if (disconnected) return;
            ctx.fillStyle = '#000';
            ctx.fillRect(0, 0, canvas.width, canvas.height);
            if (performance.now() - t0 > blackMs && src.videoWidth) {
              const s = Math.max(canvas.width / src.videoWidth, canvas.height / src.videoHeight);
              const w = src.videoWidth * s; const hh = src.videoHeight * s;
              ctx.drawImage(src, (canvas.width - w) / 2, (canvas.height - hh) / 2, w, hh);
              if (garment) {
                ctx.globalAlpha = 0.85;
                const gw = 120; const gh = gw * (garment.height / garment.width);
                ctx.drawImage(garment, (canvas.width - gw) / 2, canvas.height * 0.3, gw, gh);
                ctx.globalAlpha = 1;
              }
              ctx.fillStyle = 'rgba(0,0,0,.5)'; ctx.fillRect(8, canvas.height - 30, 92, 22);
              ctx.fillStyle = '#fff'; ctx.font = 'bold 13px sans-serif'; ctx.fillText('DECART MOCK', 14, canvas.height - 14);
            }
            raf = requestAnimationFrame(draw);
          };
          draw();
          const remote = canvas.captureStream(15);
          setState('connected');
          setTimeout(() => options.onRemoteStream?.(remote), 50);

          let seconds = 0;
          const tick = setInterval(() => {
            if (state !== 'generating' && state !== 'connected') return;
            seconds += 1;
            emit('generationTick', { seconds });
            const report = { quality: 'good', limitingFactor: 'none', warmingUp: seconds < 3, metrics: {} };
            options.onConnectionQuality?.(report);
            emit('connectionQuality', report);
          }, 1000);

          const rt = {
            async setImage(image, opts = {}) {
              record('setImage', { prompt: opts.prompt || null, enhance: opts.enhance, bytes: image?.size ?? null, type: image?.type ?? null });
              await new Promise((r) => setTimeout(r, 250));
              if (disconnected) throw new Error('not connected');
              try { garment = image instanceof Blob ? await createImageBitmap(image) : null; } catch { garment = null; }
              if (state === 'connected') setState('generating');
            },
            async set(input) { record('set', { prompt: input?.prompt || null }); return rt.setImage(input?.image ?? null, input); },
            async setPrompt(prompt) { record('setPrompt', { prompt }); },
            isConnected: () => !disconnected,
            getConnectionState: () => state,
            getConnectionQuality: () => null,
            on(ev, fn) { if (!listeners.has(ev)) listeners.set(ev, []); listeners.get(ev).push(fn); },
            off(ev, fn) { listeners.set(ev, (listeners.get(ev) || []).filter((f) => f !== fn)); },
            disconnect() {
              if (disconnected) return;
              record('disconnect');
              disconnected = true;
              cancelAnimationFrame(raf);
              clearInterval(tick);
              remote.getTracks().forEach((t) => t.stop());
              src.srcObject = null;
              state = 'disconnected';
            },
            sessionId: 'mock-session',
          };
          // Test hooks: simulate server-side events.
          window.__DECART_MOCK_SESSION__ = {
            emitError: (e) => emit('error', e),
            endSession: (reason) => emit('sessionEnded', { reason }),
            setState,
            quality: (q) => { const r = { quality: q, limitingFactor: 'bandwidth', warmingUp: false, metrics: {} }; options.onConnectionQuality?.(r); emit('connectionQuality', r); },
          };
          return rt;
        },
      },
    };
  }

  return {
    __mock: true,
    createDecartClient,
    models: { realtime: (name) => ({ name, urlPath: '/v1/stream', fps: { ideal: 30, max: 30 }, width: 1280, height: 720, supportedSpeeds: ['fast'] }) },
  };
}
