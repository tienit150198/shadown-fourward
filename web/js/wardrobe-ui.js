// Shared bits for the "Trợ lý tủ đồ" screens: labels, colour chips, image helpers,
// tap-to-talk mic, full-screen camera capture (with body guide + self-timer), outfit hash params.
import { h, icon, toast } from './ui.js';
import { startCamera, stopCamera, captureFrame, keepScreenAwake } from './camera.js';
import { holdToTalk, sttSupported } from './speech.js';
import { itemImageUrl } from './wardrobe-api.js';

export const CATEGORIES = [
  { id: 'top', label: 'Áo', emoji: '👕' },
  { id: 'bottom', label: 'Quần', emoji: '👖' },
  { id: 'dress', label: 'Váy', emoji: '👗' },
  { id: 'outerwear', label: 'Áo khoác', emoji: '🧥' },
  { id: 'shoes', label: 'Giày', emoji: '👟' },
  { id: 'accessory', label: 'Phụ kiện', emoji: '👜' },
];
const CAT_BY_ID = new Map(CATEGORIES.map((c) => [c.id, c]));
export function categoryLabel(id) { return CAT_BY_ID.get(id)?.label || id || 'Khác'; }
/** Card label: a skirt is stored as `bottom` but reads better as "Chân váy". */
export function itemKindLabel(item) {
  if (item?.category === 'bottom' && /váy/i.test(item?.name || '')) return 'Chân váy';
  return categoryLabel(item?.category);
}
export function categoryEmoji(id) { return CAT_BY_ID.get(id)?.emoji || '🏷️'; }

export const EVENTS = ['Đi làm', 'Đi chơi', 'Hẹn hò', 'Đám cưới', 'Phỏng vấn', 'Thể thao', 'Du lịch', 'Ở nhà'];
export const OCCASIONS = ['đi làm', 'đi chơi', 'hẹn hò', 'đám cưới', 'phỏng vấn', 'thể thao', 'du lịch', 'ở nhà', 'dự tiệc'];
export const STYLES = ['công sở', 'thường ngày', 'thể thao', 'dự tiệc', 'thanh lịch', 'năng động', 'tối giản', 'cổ điển'];
export const WARMTH_LABELS = ['', 'Rất mỏng', 'Mỏng', 'Vừa', 'Ấm', 'Rất ấm'];

// Vietnamese colour names → swatch. Longest names first so "xanh navy" wins over "xanh".
const COLOR_TABLE = [
  ['xanh cổ vịt', '#0f6e74'], ['cam đào', '#f4a582'], ['bạc hà', '#9fd9c1'], ['xanh da trời', '#7cc3f0'], ['xanh dương', '#2f6fdb'], ['xanh navy', '#1d2b53'], ['xanh than', '#22304f'],
  ['xanh lá', '#3a9d5d'], ['xanh rêu', '#5d6b3a'], ['xanh mint', '#9fe3c9'], ['xanh ngọc', '#2bb3a6'],
  ['xanh đậm', '#1f3a68'], ['xanh nhạt', '#a9cdf2'], ['vàng kim', '#d4af37'], ['vàng nhạt', '#f6e7a1'],
  ['hồng phấn', '#f4c2c9'], ['đỏ đô', '#7a1f2b'], ['đỏ rượu', '#7a1f2b'], ['xám nhạt', '#c4c4cc'], ['xám đậm', '#4b4b55'],
  ['trắng', '#f7f7f7'], ['đen', '#141414'], ['xám', '#8a8a94'], ['ghi', '#8a8a94'], ['bạc', '#c9ccd1'],
  ['kem', '#f1e6cf'], ['be', '#d9c4a3'], ['nâu', '#7b5135'], ['bò', '#b08c62'], ['đỏ', '#d63a3a'],
  ['hồng', '#ec7fa9'], ['cam', '#f08a32'], ['vàng', '#f2c94c'], ['tím', '#8e5cc9'], ['xanh', '#3f7fd6'],
  ['navy', '#1d2b53'], ['denim', '#46628c'], ['jean', '#46628c'], ['họa tiết', 'conic-gradient(#d63a3a,#f2c94c,#3a9d5d,#2f6fdb,#d63a3a)'],
];
export const COLOR_PRESETS = ['trắng', 'đen', 'xám', 'be', 'kem', 'nâu', 'xanh navy', 'xanh dương', 'xanh lá', 'đỏ', 'hồng', 'vàng', 'cam', 'tím'];

export function colorSwatch(name) {
  const n = String(name || '').toLowerCase().trim();
  for (const [key, css] of COLOR_TABLE) if (n.includes(key)) return css;
  return '#555560';
}

export function colorDots(colors, { max = 4 } = {}) {
  const list = (Array.isArray(colors) ? colors : []).filter(Boolean);
  return h('span', { class: 'wd-dots', title: list.join(', ') },
    list.slice(0, max).map((c) => h('i', { class: 'wd-dot', style: { background: colorSwatch(c) } })),
    list.length > max ? h('span', { class: 'wd-dots-more' }, `+${list.length - max}`) : null);
}

export function colorChip(name, { onRemove } = {}) {
  return h('span', { class: 'wd-color-chip' },
    h('i', { class: 'wd-dot', style: { background: colorSwatch(name) } }),
    h('span', null, name),
    onRemove ? h('button', { type: 'button', class: 'wd-chip-x', 'aria-label': `Bỏ màu ${name}`, onClick: onRemove }, icon('close', 14)) : null);
}

/** Square-ish thumbnail for an item (falls back to the category emoji). */
export function itemThumb(item, { cls = '' } = {}) {
  const src = itemImageUrl(item);
  return h('div', { class: `wd-thumb ${cls}` },
    h('span', { class: 'wd-thumb-emoji', 'aria-hidden': 'true' }, categoryEmoji(item?.category)),
    src ? h('img', { src, alt: item?.name || '', loading: 'lazy', onError: (e) => e.target.remove() }) : null);
}

/** replaceChildren() that skips null/false (replaceChildren would print "null"). */
export function fill(el, ...kids) {
  el.replaceChildren(...kids.flat(Infinity).filter((k) => k !== null && k !== undefined && k !== false));
  return el;
}

// ---------- images ----------
function loadImage(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error('Không đọc được ảnh'));
    img.src = src;
  });
}

/** File/Blob → JPEG data URL, longest side ≤ maxSide (browser applies EXIF orientation). */
export async function fileToJpeg(file, { maxSide = 1024, quality = 0.85 } = {}) {
  const url = URL.createObjectURL(file);
  try {
    const img = await loadImage(url);
    const w = img.naturalWidth;
    const hgt = img.naturalHeight;
    if (!w || !hgt) throw new Error('Ảnh rỗng');
    const scale = Math.min(1, maxSide / Math.max(w, hgt));
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(w * scale);
    canvas.height = Math.round(hgt * scale);
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#fff';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL('image/jpeg', quality);
  } finally {
    URL.revokeObjectURL(url);
  }
}

/** Opens the OS file picker (optionally straight into the camera). Resolves File|null. */
export function pickImageFile({ capture } = {}) {
  return new Promise((resolve) => {
    const input = h('input', { type: 'file', accept: 'image/*', class: 'wd-hidden-input' });
    if (capture) input.setAttribute('capture', capture);
    let settled = false;
    const done = (file) => {
      if (settled) return;
      settled = true;
      input.remove();
      resolve(file || null);
    };
    input.addEventListener('change', () => done(input.files?.[0]));
    input.addEventListener('cancel', () => done(null));
    document.body.appendChild(input);
    input.click();
  });
}

// ---------- outfit hash param ----------
/** Reads `?outfit=<json ids>` (or comma list) from the current hash. */
export function outfitFromHash() {
  const raw = location.hash.split('?')[1] || '';
  const value = new URLSearchParams(raw).get('outfit');
  if (!value) return [];
  try {
    const parsed = JSON.parse(value);
    if (Array.isArray(parsed)) return parsed.map(String).filter(Boolean);
  } catch { /* fall through */ }
  return value.split(',').map((s) => s.trim()).filter(Boolean);
}

export function outfitHash(route, ids) {
  return `#/${route}?outfit=${encodeURIComponent(JSON.stringify(ids || []))}`;
}

export const PROVIDER_LABELS = { fashn: 'FASHN AI', worker: 'Mac mini', hf_space: 'Hugging Face', huggingface: 'Hugging Face', hf: 'Hugging Face', mock: 'Demo' };
export function providerLabel(p) { return PROVIDER_LABELS[p] || p || 'Không rõ'; }

/** Normalises a rules_used entry (id / text / {text, taught_by}) against the known rules. */
export function resolveRule(entry, rules = []) {
  if (!entry) return null;
  if (typeof entry === 'object') {
    const known = entry.id ? rules.find((r) => r.id === entry.id) : null;
    return { ...(known || {}), ...entry };
  }
  const s = String(entry);
  return rules.find((r) => r.id === s) || rules.find((r) => r.text === s) || { text: s };
}

export function ruleBadge(rule) {
  const who = rule.taught_by || rule.teacher || rule.by;
  return h('span', { class: 'wd-rule-badge', title: rule.text || '' },
    icon('user', 13), h('span', { class: 'wd-rule-badge-who' }, who ? `Học từ ${who}` : 'Quy tắc đã dạy'),
    rule.text ? h('span', { class: 'wd-rule-badge-text' }, `· ${rule.text}`) : null);
}

// ---------- tap-to-talk mic ----------
/**
 * A mic button: tap to start dictation (vi-VN), tap again (or 12s) to stop. Calls onText(text).
 * Returns {el, cancel}. Hidden when the browser has no SpeechRecognition.
 */
export function micButton({ onText, onInterim, label = 'Nói' } = {}) {
  const btn = h('button', { class: 'wd-mic', type: 'button', 'aria-label': label, title: label }, icon('mic', 22));
  if (!sttSupported()) {
    btn.disabled = true;
    btn.title = 'Trình duyệt này chưa hỗ trợ nhận giọng nói';
  }
  let session = null;
  let timer = null;
  const stop = async () => {
    clearTimeout(timer);
    const s = session;
    session = null;
    btn.classList.remove('listening');
    if (!s) return;
    btn.classList.add('thinking');
    try {
      const text = await s.stop();
      if (text) onText?.(text);
      else toast('Chưa nghe rõ, thử nói lại nhé.');
    } catch (err) {
      toast(err.userMessage || 'Không dùng được micro.', { kind: 'error' });
    } finally {
      btn.classList.remove('thinking');
    }
  };
  btn.addEventListener('click', () => {
    if (session) { stop(); return; }
    session = holdToTalk({ onInterim });
    if (!session) { toast('Không bật được nhận giọng nói.', { kind: 'error' }); return; }
    btn.classList.add('listening');
    navigator.vibrate?.(20);
    timer = setTimeout(stop, 12000);
  });
  return {
    el: btn,
    cancel() { clearTimeout(timer); try { session?.cancel(); } catch { /* ignore */ } session = null; btn.classList.remove('listening', 'thinking'); },
  };
}

// ---------- full-screen camera capture ----------
const SILHOUETTE = `<svg viewBox="0 0 100 200" preserveAspectRatio="xMidYMid meet" fill="none" stroke="currentColor" stroke-width="1.4" stroke-dasharray="4 3" stroke-linecap="round" stroke-linejoin="round">
<circle cx="50" cy="19" r="11"/>
<path d="M44 31v4M56 31v4"/>
<path d="M30 40q20-8 40 0l2 58q-22 6-44 0z"/>
<path d="M30 41l-10 33-3 30M70 41l10 33 3 30"/>
<path d="M33 98l2 48 1 48h10l3-86M67 98l-2 48-1 48H54l-3-86"/>
</svg>`;

/**
 * Full-screen camera with optional dashed body guide and 3 s self-timer.
 * Returns {result: Promise<dataURL|null>, close()}. Upload fallback included.
 */
export function openCameraCapture({ facingMode = 'user', title = 'Chụp ảnh', hint = '', silhouette = false, timer = false, maxSide = 1024 } = {}) {
  let facing = facingMode;
  let stream = null;
  let closed = false;
  let timerOn = !!timer;
  let countdown = null;
  let resolveFn;
  const result = new Promise((r) => { resolveFn = r; });

  const video = h('video', { class: 'wd-cam-video', playsinline: true, muted: true, autoplay: true });
  const guide = silhouette ? h('div', { class: 'wd-silhouette', 'aria-hidden': 'true', html: SILHOUETTE }) : null;
  const count = h('div', { class: 'wd-countdown hidden', 'aria-live': 'assertive' });
  const flash = h('div', { class: 'wd-cam-flash' });
  const err = h('div', { class: 'wd-cam-error hidden' });
  const timerBtn = h('button', { class: 'wd-cam-side', type: 'button', 'aria-pressed': String(timerOn), onClick: () => toggleTimer() },
    icon('clock', 22), h('span', null, '3 giây'));
  const shutter = h('button', { class: 'wd-shutter', type: 'button', 'aria-label': 'Chụp', onClick: () => shoot() }, h('i'));
  const uploadBtn = h('button', { class: 'wd-cam-side', type: 'button', onClick: () => upload() }, icon('image', 22), h('span', null, 'Tải ảnh'));
  const flipBtn = h('button', { class: 'icon-btn glass', type: 'button', 'aria-label': 'Đổi camera', onClick: () => flip() }, icon('refresh', 22));

  const wrap = h('div', { class: 'sheet-wrap wd-cam-sheet', role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
    video, guide, flash,
    h('div', { class: 'wd-cam-top' },
      h('button', { class: 'icon-btn glass', type: 'button', 'aria-label': 'Đóng', onClick: () => finish(null) }, icon('close', 22)),
      h('div', { class: 'wd-cam-title' }, title),
      flipBtn),
    hint ? h('div', { class: 'wd-cam-hint' }, hint) : null,
    count, err,
    h('div', { class: 'wd-cam-bottom' }, timer ? timerBtn : h('span', { class: 'wd-cam-side-spacer' }), shutter, uploadBtn));
  document.body.appendChild(wrap);
  const releaseWake = keepScreenAwake();

  function toggleTimer() {
    timerOn = !timerOn;
    timerBtn.setAttribute('aria-pressed', String(timerOn));
  }

  async function open() {
    stopCamera(stream, video);
    err.classList.add('hidden');
    video.classList.toggle('mirror', facing === 'user');
    try {
      stream = await startCamera(video, { facingMode: facing });
      if (closed) stopCamera(stream, video);
    } catch (e) {
      if (closed) return;
      err.replaceChildren(h('p', null, e.userMessage || e.message),
        h('div', { class: 'row gap' },
          h('button', { class: 'btn btn-ghost', type: 'button', onClick: open }, 'Thử lại'),
          h('button', { class: 'btn btn-primary', type: 'button', onClick: upload }, icon('image', 20), 'Tải ảnh lên')));
      err.classList.remove('hidden');
    }
  }

  function flip() {
    facing = facing === 'user' ? 'environment' : 'user';
    open();
  }

  function grab() {
    const frame = captureFrame(video, { maxSide, quality: 0.88 });
    if (!frame) { toast('Camera chưa sẵn sàng, thử lại nhé.'); return; }
    flash.classList.remove('go');
    void flash.offsetWidth;
    flash.classList.add('go');
    navigator.vibrate?.(30);
    setTimeout(() => finish(frame), 180);
  }

  function shoot() {
    if (countdown) { clearInterval(countdown); countdown = null; count.classList.add('hidden'); shutter.classList.remove('counting'); return; }
    if (!timerOn) { grab(); return; }
    let n = 3;
    count.textContent = String(n);
    count.classList.remove('hidden');
    shutter.classList.add('counting');
    countdown = setInterval(() => {
      n -= 1;
      if (n <= 0) {
        clearInterval(countdown); countdown = null;
        count.classList.add('hidden');
        shutter.classList.remove('counting');
        grab();
      } else {
        count.textContent = String(n);
        count.classList.remove('tick'); void count.offsetWidth; count.classList.add('tick');
      }
    }, 1000);
  }

  async function upload() {
    const file = await pickImageFile();
    if (!file || closed) return;
    try { finish(await fileToJpeg(file, { maxSide, quality: 0.88 })); } catch (e) { toast(e.message, { kind: 'error' }); }
  }

  function finish(value) {
    if (closed) return;
    closed = true;
    clearInterval(countdown);
    stopCamera(stream, video);
    releaseWake();
    wrap.remove();
    resolveFn(value);
  }

  open();
  return { result, close: () => finish(null) };
}

/** Hold an elapsed-seconds ticker; returns stop(). */
export function ticker(fn, ms = 250) {
  const t0 = performance.now();
  fn(0);
  const id = setInterval(() => fn(performance.now() - t0), ms);
  return () => clearInterval(id);
}

/** Local YYYY-MM-DD (not UTC). */
export function localDate(offsetDays = 0) {
  const d = new Date();
  d.setDate(d.getDate() + offsetDays);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export function weatherEmoji(code) {
  const c = Number(code);
  if (!Number.isFinite(c)) return '🌡️';
  if (c === 0) return '☀️';
  if (c <= 2) return '🌤️';
  if (c === 3) return '☁️';
  if (c === 45 || c === 48) return '🌫️';
  if (c >= 51 && c <= 57) return '🌦️';
  if ((c >= 61 && c <= 67) || (c >= 80 && c <= 82)) return '🌧️';
  if ((c >= 71 && c <= 77) || c === 85 || c === 86) return '❄️';
  if (c >= 95) return '⛈️';
  return '🌡️';
}
