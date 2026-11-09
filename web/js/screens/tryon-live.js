// Mặc thử trực tiếp — live virtual try-on on the front camera with Decart `lucy-vton-latest`.
// Route: #/tryon-live?outfit=<encodeURIComponent(JSON array of item ids)>
//   (also accepts a JSON array of arrays = several outfits, `cap=<seconds>` hard cap,
//    `idle=<seconds>` idle cut-off (≥5), `speed=fast`, and `mock=1` for the dev fake SDK —
//    see js/decart-live.js).
// Flow (port of DecartAI/tryon-examples examples/mobile): preflight (camera, framing, network,
// server) → "Bắt đầu mặc thử" → connect (token from POST /api/tryon/live-token, fetched by the
// SDK's apiKeyProvider right before each dial) → setImage(garment, prompt) → switch outfits with
// setImage again, no reconnect. Cost guard: idle auto-disconnect, hard cap, stop on page hide.
import { h, icon, toast, navigate, createScope, openSheet, fmtDuration } from '../ui.js';
import { startCamera, stopCamera, keepScreenAwake } from '../camera.js';
import { wardrobeApi, itemImageUrl } from '../wardrobe-api.js';
import { fetchWithAccess } from '../api.js';
import { outfitHash, categoryLabel, categoryEmoji } from '../wardrobe-ui.js';
import {
  loadDecartSdk, isMockEnabled, LIVE_MODEL, LIVE_CATEGORIES, slotsFromItems, slotItems, buildOutfitPrompt,
  composeGarmentImage, decartErrorMessage, sessionEndMessage,
} from '../decart-live.js';

const DEFAULT_COST_PER_SEC = 0.02;    // USD, standard tier (PLAN.md; fast tier is billed 2x)
const DEFAULT_IDLE_SEC = 60;
const DEFAULT_CAP_SEC = 180;
const MAX_IDLE_SEC = 300;
const IDLE_WARN_SEC = 10;             // warn this long before the idle cut-off
const CAP_WARN_SEC = 15;
const FIRST_FRAME_FALLBACK_MS = 6000; // show the remote video even if it still looks black
const APPLY_DEBOUNCE_MS = 450;

const QUALITY_VI = { good: 'Tốt', fair: 'Khá', poor: 'Yếu', critical: 'Rất yếu' };
const QUALITY_BARS = { good: 4, fair: 3, poor: 2, critical: 1 };
const LIMIT_VI = {
  bandwidth: 'băng thông thấp', latency: 'độ trễ cao', loss: 'mất gói tin', stall: 'khung hình bị đứng', cpu: 'máy đang quá tải', none: 'mạng chập chờn',
};

function hashQuery() {
  return new URLSearchParams(location.hash.split('?')[1] || '');
}

/** `outfit` = JSON array of ids, JSON array of arrays (several outfits), or a comma list. */
function parseOutfits(q) {
  const raw = q.get('outfit') || q.get('outfits');
  if (!raw) return [];
  try {
    const v = JSON.parse(raw);
    if (Array.isArray(v)) {
      if (v.length && v.every(Array.isArray)) return v.map((a) => a.map(String).filter(Boolean)).filter((a) => a.length);
      const ids = v.map(String).filter(Boolean);
      return ids.length ? [ids] : [];
    }
  } catch { /* comma list */ }
  const ids = raw.split(',').map((s) => s.trim()).filter(Boolean);
  return ids.length ? [ids] : [];
}

const slotKey = (slots) => slotItems(slots).map((it) => it.id).sort().join('|');

function money(usd) {
  return `$${usd < 10 ? usd.toFixed(2) : usd.toFixed(1)}`;
}

function liveError(message, extra = {}) {
  const e = new Error(message);
  e.userMessage = message;
  Object.assign(e, extra);
  return e;
}

function thumb(item, cls = '') {
  const src = itemImageUrl(item);
  return h('span', { class: `tl-thumb ${cls}` },
    h('span', { class: 'tl-thumb-emoji', 'aria-hidden': 'true' }, categoryEmoji(item?.category)),
    src ? h('img', { src, alt: '', loading: 'lazy', draggable: 'false', onError: (e) => e.target.remove() }) : null);
}

export function mount(el) {
  const scope = createScope();
  const mock = isMockEnabled();
  const q = hashQuery();
  const presetIds = parseOutfits(q);

  // ---------- state ----------
  let phase = 'preflight'; // preflight | blocked | connecting | live | ended
  let config = null;
  let liveAvailable = null; // null = unknown (config missing)
  let costPerSec = DEFAULT_COST_PER_SEC;
  // URL overrides may only shorten the guards (the server/Decart enforce the real per-session cap).
  let idleSec = Number(q.get('idle')) >= 5 ? Math.min(Number(q.get('idle')), MAX_IDLE_SEC) : DEFAULT_IDLE_SEC;
  let capSec = Number(q.get('cap')) > 0 ? Math.min(Number(q.get('cap')), DEFAULT_CAP_SEC) : DEFAULT_CAP_SEC;
  let speed = q.get('speed') === 'fast' ? 'fast' : null;

  let wardrobe = null; // null = loading, [] = empty
  let itemsById = new Map();
  let presets = [];     // [{label, slots}]
  let current = {};     // slots
  let camStream = null;
  let camOk = false;
  let sdk = null;
  let decart = null;    // createDecartClient(...) result
  let rt = null;        // realtime client
  let session = 0;      // bumps on every start/stop; stale callbacks compare against it
  let stopping = false;
  let connectedAt = 0;
  let tickSec = null;   // server-reported generation seconds (generationTick)
  let lastActivity = performance.now();
  let remoteShown = false;
  let appliedKey = null;
  let applying = false;
  let applyAgain = false;
  let applyTimer = null;
  let qualityDismissed = false;
  let gestureOn = true;
  let pose = null;
  let poseStatus = 'off';
  let framing = null;
  let netReport = null;
  let lastStats = null;
  const blobCache = new Map();

  // ---------- DOM ----------
  const localVideo = h('video', { class: 'tl-video tl-local', playsinline: true, autoplay: true, muted: true });
  const remoteVideo = h('video', { class: 'tl-video tl-remote', playsinline: true, autoplay: true, muted: true });
  remoteVideo.muted = true; // the model returns video only; muted keeps autoplay allowed everywhere
  const guide = h('div', { class: 'tl-guide', 'aria-hidden': 'true' },
    h('div', { class: 'tl-guide-body' }),
    h('div', { class: 'tl-guide-label' }, 'Đầu'),
    h('div', { class: 'tl-guide-label knee' }, 'Đầu gối'));
  const flash = h('div', { class: 'tl-flash' });
  const stage = h('div', { class: 'tl-stage' }, localVideo, remoteVideo, guide, flash);

  // top bar
  const backBtn = h('button', { class: 'icon-btn glass tl-back', type: 'button', 'aria-label': 'Quay lại', onClick: () => leave() }, icon('back', 24));
  const qualityBars = h('span', { class: 'tl-bars', 'aria-hidden': 'true' }, h('i'), h('i'), h('i'), h('i'));
  const liveChip = h('span', { class: 'chip tl-live-chip' }, h('span', { class: 'tl-live-dot' }), 'TRỰC TIẾP', qualityBars);
  const costTime = h('b', null, '00:00');
  const costUsd = h('span', { class: 'tl-cost-usd' }, '~$0.00');
  const costRate = h('span', { class: 'tl-cost-rate' }, '~ $0.02/giây');
  const costChip = h('span', { class: 'chip tl-cost' }, icon('clock', 15), costTime, h('span', { class: 'tl-dot' }, '·'), costUsd);
  const aiChip = h('span', { class: 'chip tl-ai-chip', title: 'Video do AI tạo (Decart), có watermark' }, icon('sparkle', 14), 'AI');
  const topBar = h('header', { class: 'tl-top' }, backBtn,
    h('div', { class: 'tl-top-chips' }, h('div', { class: 'tl-chip-row' }, liveChip, aiChip), h('div', { class: 'tl-chip-row' }, costChip, costRate)));

  // right-side live controls
  const snapBtn = h('button', { class: 'icon-btn glass', type: 'button', 'aria-label': 'Chụp ảnh', onClick: () => snapshot() }, icon('camera', 24));
  const gestureBtn = h('button', { class: 'icon-btn glass tl-gesture-btn hidden', type: 'button', 'aria-label': 'Bật/tắt đổi đồ bằng cử chỉ giơ tay', onClick: () => toggleGesture() }, icon('hand', 22));
  const stopBtn = h('button', { class: 'icon-btn glass danger', type: 'button', 'aria-label': 'Dừng mặc thử', onClick: () => stopSession('user') }, icon('close', 24));
  const sideBar = h('div', { class: 'tl-side' }, snapBtn, gestureBtn, stopBtn);

  // banners
  const applyingChip = h('div', { class: 'tl-applying hidden', role: 'status' }, h('span', { class: 'tl-spin sm' }), 'Đang thay đồ…');
  const framingBanner = h('div', { class: 'tl-banner tl-banner-warn hidden', role: 'status' });
  const qualityBanner = h('div', { class: 'tl-banner tl-banner-warn hidden', role: 'status' });
  const timerBanner = h('div', { class: 'tl-banner tl-banner-info hidden', role: 'status' });
  const dwellRing = h('div', { class: 'tl-dwell hidden', 'aria-hidden': 'true' }, h('span', { class: 'tl-dwell-arrow' }), h('span', { class: 'tl-dwell-label' }));
  const banners = h('div', { class: 'tl-banners' }, applyingChip, timerBanner, framingBanner, qualityBanner);

  // bottom outfit strip (live)
  const strip = h('div', { class: 'tl-strip', role: 'listbox', 'aria-label': 'Chọn bộ đồ' });
  const gestureHint = h('div', { class: 'tl-gesture-hint hidden' }, icon('hand', 14), 'Giơ tay phải 1,5 giây: bộ tiếp · tay trái: bộ trước');
  const stripWrap = h('div', { class: 'tl-strip-wrap' }, gestureHint,
    h('div', { class: 'tl-strip-row' },
      h('button', { class: 'tl-strip-add', type: 'button', 'aria-label': 'Chọn đồ từ tủ đồ', onClick: () => openPicker() }, icon('plus', 22), h('span', null, 'Tủ đồ')),
      strip));

  // connecting overlay
  const connectText = h('div', { class: 'tl-connect-text' }, 'Đang kết nối Decart…');
  const connectSub = h('div', { class: 'tl-connect-sub' }, 'Thường mất khoảng 3 giây');
  const connectOverlay = h('div', { class: 'tl-connect hidden' },
    h('span', { class: 'tl-spin' }), connectText, connectSub,
    h('button', { class: 'btn btn-ghost btn-sm', type: 'button', onClick: () => stopSession('user') }, 'Huỷ'));

  // preflight card
  const rowCamera = checkRow('camera', 'Camera trước');
  const rowFraming = checkRow('user', 'Khung hình');
  const rowNet = checkRow('refresh', 'Mạng');
  const rowServer = checkRow('sparkle', 'Máy chủ mặc thử');
  const outfitThumbs = h('div', { class: 'tl-pf-thumbs' });
  const outfitName = h('div', { class: 'tl-pf-outfit-name' });
  const startBtn = h('button', { class: 'btn btn-primary btn-xl', type: 'button', onClick: () => startSession() }, icon('play', 20), 'Bắt đầu mặc thử');
  const photoLink = h('a', { class: 'btn btn-ghost block tl-photo-link', href: '#/tryon-photo' }, icon('image', 20), 'Ảnh mặc thử (miễn phí, chậm hơn)');
  const pfNote = h('p', { class: 'tl-pf-note' });
  const preflightCard = h('div', { class: 'tl-card tl-preflight' },
    h('div', { class: 'tl-kicker' }, icon('sparkle', 16), mock ? 'Mặc thử trực tiếp · CHẾ ĐỘ GIẢ LẬP' : 'Mặc thử trực tiếp · AI'),
    h('h1', { class: 'tl-title' }, 'Đứng lùi lại để thấy từ đầu đến đầu gối'),
    h('ul', { class: 'tl-checks' }, rowCamera.el, rowFraming.el, rowNet.el, rowServer.el),
    h('div', { class: 'tl-pf-outfit' }, outfitThumbs, outfitName,
      h('button', { class: 'btn btn-ghost btn-sm', type: 'button', onClick: () => openPicker() }, 'Đổi đồ')),
    pfNote,
    h('p', { class: 'tl-ai-note' }, icon('alert', 14),
      h('span', null, 'Video do AI (Decart) tạo ra và có watermark. Hình camera được gửi tới Decart để xử lý trực tiếp, SHADOW không lưu lại.')),
    startBtn, photoLink);

  // message card (blocked / error / ended)
  const msgCard = h('div', { class: 'tl-card tl-msg hidden', role: 'alert' });

  const root = h('div', { class: 'tl-root', dataset: { phase } },
    stage, topBar, sideBar, banners, dwellRing, stripWrap, connectOverlay,
    h('div', { class: 'tl-bottom' }, preflightCard, msgCard));
  el.append(root);

  function checkRow(ic, label) {
    const status = h('span', { class: 'tl-check-status' }, 'Đang kiểm tra…');
    const li = h('li', { class: 'tl-check wait' }, h('span', { class: 'tl-check-ic' }, icon(ic, 18)), h('span', { class: 'tl-check-label' }, label), status);
    return {
      el: li,
      set(state, text) { li.className = `tl-check ${state}`; status.textContent = text; },
    };
  }

  // ---------- phase ----------
  function setPhase(p) {
    phase = p;
    root.dataset.phase = p;
    preflightCard.classList.toggle('hidden', p !== 'preflight');
    msgCard.classList.toggle('hidden', !(p === 'blocked' || p === 'ended'));
    connectOverlay.classList.toggle('hidden', p !== 'connecting');
    if (p !== 'live') { applyingChip.classList.add('hidden'); timerBanner.classList.add('hidden'); qualityBanner.classList.add('hidden'); hideDwell(); }
    if (p === 'preflight' || p === 'connecting' || p === 'live') pose?.resetFraming();
    updateGestureUi();
    updateStartButton();
  }

  // ---------- data ----------
  function photoHref() {
    const ids = slotItems(current).map((it) => it.id);
    return outfitHash('tryon-photo', ids.length ? ids : (presetIds[0] || []));
  }

  function buildPresets() {
    const missing = [];
    const skipped = [];
    presets = presetIds.map((ids, i) => {
      const items = ids.map((id) => { const it = itemsById.get(id); if (!it) missing.push(id); return it; }).filter(Boolean);
      items.forEach((it) => { if (!LIVE_CATEGORIES.includes(it.category)) skipped.push(it); });
      const slots = slotsFromItems(items);
      return { label: presetIds.length > 1 ? `Bộ ${i + 1}` : 'Bộ đã chọn', slots };
    }).filter((p) => slotItems(p.slots).length);
    if (missing.length) toast(`${missing.length} món không còn trong tủ đồ nên đã bỏ qua.`, { kind: 'error' });
    if (skipped.length) toast(`${[...new Set(skipped.map((it) => categoryLabel(it.category)))].join(', ')} chưa mặc thử trực tiếp được — đã bỏ qua.`);
    if (!slotItems(current).length && presets[0]) current = { ...presets[0].slots };
  }

  async function loadWardrobe() {
    try {
      const list = await wardrobeApi.listItems();
      if (scope.disposed) return;
      wardrobe = Array.isArray(list) ? list.filter((it) => it && it.id != null).map((it) => ({ ...it, id: String(it.id) })) : [];
    } catch (err) {
      if (scope.disposed) return;
      wardrobe = [];
      toast(err?.userMessage || 'Không tải được tủ đồ.', { kind: 'error' });
    }
    itemsById = new Map(wardrobe.map((it) => [it.id, it]));
    buildPresets();
    renderOutfit();
  }

  async function loadConfig() {
    if (mock) { liveAvailable = true; rowServer.set('ok', 'Giả lập (không tốn phí)'); updateStartButton(); return; }
    try {
      config = await wardrobeApi.tryonConfig();
    } catch (err) {
      config = null;
      console.info('[tryon-live] config unavailable', err?.message);
    }
    if (scope.disposed) return;
    const live = (config && config.live) || {};
    if (Number(live.cost_per_second) > 0) costPerSec = Number(live.cost_per_second);
    if (!(Number(q.get('idle')) >= 5) && Number(live.idle_seconds) > 0) idleSec = Number(live.idle_seconds);
    const cfgCap = Number(live.max_session_seconds || live.max_seconds);
    if (cfgCap > 0) capSec = Number(q.get('cap')) > 0 ? Math.min(Number(q.get('cap')), cfgCap) : cfgCap;
    if (!speed && live.speed === 'fast') speed = 'fast';
    if (speed === 'fast' && !(Number(live.cost_per_second) > 0)) costPerSec = DEFAULT_COST_PER_SEC * 2;
    costRate.textContent = `~ ${money(costPerSec)}/giây`;
    renderNote();
    if (live.available === false) {
      liveAvailable = false;
      rowServer.set('bad', 'Chưa có DECART_API_KEY');
      showBlocked(live.detail || live.reason || live.message || '');
      return;
    }
    liveAvailable = live.available === true ? true : null;
    rowServer.set(live.available === true ? 'ok' : 'neutral', live.available === true ? `Sẵn sàng · ${live.model || LIVE_MODEL}` : 'Sẽ kiểm tra khi bắt đầu');
    updateStartButton();
  }

  // ---------- render ----------
  function renderNote() {
    pfNote.textContent = `Chi phí ~ ${money(costPerSec)}/giây khi đang kết nối${speed === 'fast' ? ' (chế độ nhanh)' : ''}. `
      + `Tự ngắt sau ${idleSec} giây không thao tác, tối đa ${fmtMinutes(capSec)} mỗi phiên.`;
  }

  function fmtMinutes(sec) {
    const m = Math.floor(sec / 60); const s = Math.round(sec % 60);
    if (!m) return `${s} giây`;
    return s ? `${m} phút ${s} giây` : `${m} phút`;
  }

  function renderOutfit() {
    const items = slotItems(current);
    outfitThumbs.replaceChildren(...(items.length ? items.map((it) => thumb(it, 'sm')) : [h('span', { class: 'tl-thumb sm empty' }, icon('plus', 18))]));
    outfitName.textContent = wardrobe === null ? 'Đang tải tủ đồ…'
      : items.length ? items.map((it) => it.name || categoryLabel(it.category)).join(' + ')
        : (wardrobe.length ? 'Chưa chọn đồ' : 'Tủ đồ trống');
    photoLink.href = photoHref();
    renderStrip();
    updateStartButton();
  }

  function stripEntries() {
    const garments = (wardrobe || []).filter((it) => LIVE_CATEGORIES.includes(it.category));
    return [
      ...presets.map((p, i) => ({ kind: 'preset', key: `p${i}`, preset: p })),
      ...garments.map((it) => ({ kind: 'item', key: `i${it.id}`, item: it })),
    ];
  }

  function entryActive(entry) {
    if (entry.kind === 'preset') return slotKey(entry.preset.slots) === slotKey(current);
    return slotItems(current).some((it) => it.id === entry.item.id);
  }

  function renderStrip() {
    const entries = stripEntries();
    strip.replaceChildren(...entries.map((entry) => {
      const active = entryActive(entry);
      const items = entry.kind === 'preset' ? slotItems(entry.preset.slots) : [entry.item];
      return h('button', {
        class: `tl-card-item${active ? ' active' : ''}${entry.kind === 'preset' ? ' preset' : ''}`,
        type: 'button', role: 'option', 'aria-selected': active ? 'true' : 'false', dataset: { key: entry.key },
        onClick: () => { markActivity(); selectEntry(entry); },
      },
      h('span', { class: `tl-card-imgs n${Math.min(items.length, 3)}` }, items.slice(0, 3).map((it) => thumb(it))),
      h('span', { class: 'tl-card-label' }, entry.kind === 'preset' ? entry.preset.label : (entry.item.name || categoryLabel(entry.item.category))));
    }));
    if (!entries.length) strip.append(h('div', { class: 'tl-strip-empty' }, wardrobe === null ? 'Đang tải tủ đồ…' : 'Tủ đồ chưa có áo/quần/váy.'));
  }

  function selectEntry(entry) {
    if (entry.kind === 'preset') current = { ...entry.preset.slots };
    else toggleItem(entry.item);
    renderOutfit();
    scheduleApply();
    const node = strip.querySelector(`[data-key="${CSS.escape(entry.key)}"]`);
    node?.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
  }

  function toggleItem(item) {
    const items = slotItems(current);
    if (items.some((it) => it.id === item.id)) {
      if (items.length > 1) current = slotsFromItems(items.filter((it) => it.id !== item.id));
      return;
    }
    current = slotsFromItems([...items, item]);
  }

  function stepEntry(dir) {
    const entries = stripEntries();
    if (!entries.length) return;
    let idx = entries.findIndex((e) => e.kind === 'preset' && entryActive(e));
    if (idx < 0) idx = entries.findIndex((e) => entryActive(e));
    const next = entries[(idx + (dir === 'next' ? 1 : -1) + entries.length) % entries.length];
    if (next.kind === 'item') current = slotsFromItems([...slotItems(current), next.item]);
    else current = { ...next.preset.slots };
    renderOutfit();
    scheduleApply();
    strip.querySelector(`[data-key="${CSS.escape(next.key)}"]`)?.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
  }

  function updateStartButton() {
    const hasOutfit = slotItems(current).length > 0;
    startBtn.disabled = phase !== 'preflight' || !camOk || !hasOutfit || liveAvailable === false;
    const critical = netReport?.quality === 'critical';
    startBtn.lastChild.textContent = critical ? 'Vẫn thử (mạng rất yếu)' : 'Bắt đầu mặc thử';
  }

  // ---------- picker ----------
  function openPicker() {
    markActivity();
    let tab = 'top';
    const tabs = h('div', { class: 'tl-pick-tabs', role: 'tablist' });
    const grid = h('div', { class: 'tl-pick-grid' });
    const summary = h('div', { class: 'tl-pick-summary' });
    const render = () => {
      tabs.replaceChildren(...LIVE_CATEGORIES.map((c) => h('button', {
        class: `tl-pick-tab${c === tab ? ' active' : ''}`, type: 'button', role: 'tab', 'aria-selected': c === tab ? 'true' : 'false',
        onClick: () => { tab = c; render(); },
      }, categoryLabel(c))));
      const list = (wardrobe || []).filter((it) => it.category === tab);
      const chosen = new Set(slotItems(current).map((it) => it.id));
      grid.replaceChildren(...(list.length ? list.map((it) => h('button', {
        class: `tl-pick-item${chosen.has(it.id) ? ' active' : ''}`, type: 'button', 'aria-pressed': chosen.has(it.id) ? 'true' : 'false',
        onClick: () => { markActivity(); toggleItem(it); renderOutfit(); scheduleApply(); render(); },
      }, thumb(it, 'lg'), h('span', { class: 'tl-pick-name' }, it.name || categoryLabel(it.category)), chosen.has(it.id) ? h('span', { class: 'tl-pick-check' }, icon('check', 16)) : null))
        : [h('div', { class: 'tl-pick-empty' }, wardrobe === null ? 'Đang tải tủ đồ…' : `Chưa có ${categoryLabel(tab).toLowerCase()} trong tủ đồ.`,
          h('a', { href: '#/wardrobe', class: 'tl-link' }, 'Mở tủ đồ'))]));
      const items = slotItems(current);
      summary.textContent = items.length ? `Đang chọn: ${items.map((it) => it.name || categoryLabel(it.category)).join(' + ')}` : 'Chọn áo + quần, hoặc một chiếc váy.';
    };
    render();
    let sheet;
    const body = h('div', { class: 'tl-pick' }, tabs, grid, summary,
      h('p', { class: 'tl-pick-hint' }, 'Chạm để thêm/bỏ. Áo và quần được ghép thành một bộ; váy thay cho cả áo + quần.'),
      h('button', { class: 'btn btn-primary block', type: 'button', onClick: () => sheet.close() }, 'Xong'));
    sheet = openSheet(body, { title: 'Chọn đồ để mặc thử', className: 'tl-pick-sheet' });
  }

  // ---------- camera + checks ----------
  async function openCamera() {
    rowCamera.set('wait', 'Đang mở…');
    try {
      camStream = await startCamera(localVideo, { facingMode: 'user' });
      if (scope.disposed) { stopCamera(camStream); return; }
      camOk = true;
      const s = camStream.getVideoTracks()[0]?.getSettings?.() || {};
      rowCamera.set('ok', s.width ? `Sẵn sàng · ${s.width}×${s.height}` : 'Sẵn sàng');
      startPose();
    } catch (err) {
      camOk = false;
      rowCamera.set('bad', err?.userMessage || 'Không mở được camera');
      rowFraming.set('neutral', 'Cần camera');
    }
    updateStartButton();
  }

  async function startPose() {
    rowFraming.set('wait', 'Đang tải nhận dạng dáng…');
    try {
      const { createPoseWatcher } = await import('../tryon-pose.js');
      if (scope.disposed) return;
      pose = createPoseWatcher({
        video: localVideo,
        needKnees: () => !!(current.bottom || current.dress),
        onStatus: (s) => {
          poseStatus = s;
          if (s === 'failed') { rowFraming.set('neutral', 'Tự căn: thấy từ đầu đến đầu gối'); framingBanner.classList.add('hidden'); }
          if (s === 'ready' && framing == null) rowFraming.set('neutral', 'Chưa thấy người');
          updateGestureUi();
        },
        onFraming: (v) => { framing = v; renderFraming(); },
        onGesture: (dir) => {
          if (phase !== 'live' || !gestureOn) return;
          markActivity();
          navigator.vibrate?.(25);
          stepEntry(dir);
          toast(dir === 'next' ? 'Bộ tiếp theo' : 'Bộ trước', { ms: 1200 });
        },
        onDwell: (dir, progress) => {
          if (phase !== 'live' || !gestureOn || !dir) { hideDwell(); return; }
          dwellRing.classList.remove('hidden');
          dwellRing.dataset.dir = dir;
          dwellRing.style.setProperty('--p', String(progress));
          dwellRing.lastChild.textContent = dir === 'next' ? 'Bộ tiếp' : 'Bộ trước';
        },
      });
      scope.add(() => pose?.dispose());
      pose.start();
    } catch (err) {
      console.info('[tryon-live] pose disabled', err?.message);
      rowFraming.set('neutral', 'Tự căn: thấy từ đầu đến đầu gối');
    }
  }

  function hideDwell() { dwellRing.classList.add('hidden'); }

  function renderFraming() {
    const kneeText = (current.bottom || current.dress) ? 'đầu gối' : 'hông';
    if (framing === 'ok') rowFraming.set('ok', `Đã thấy từ đầu đến ${kneeText}`);
    else if (framing === 'far') rowFraming.set('warn', `Lùi lại để thấy đến ${kneeText}`);
    else if (framing === 'head') rowFraming.set('warn', 'Hạ máy hoặc lùi lại để thấy cả đầu');
    else rowFraming.set('neutral', poseStatus === 'failed' ? 'Tự căn: thấy từ đầu đến đầu gối' : 'Chưa thấy người');
    guide.classList.toggle('ok', framing === 'ok');
    const show = (phase === 'live' || phase === 'connecting') && (framing === 'far' || framing === 'head');
    framingBanner.classList.toggle('hidden', !show);
    if (show) {
      framingBanner.replaceChildren(icon('alert', 16),
        h('span', null, framing === 'head' ? 'Hạ máy hoặc lùi lại để thấy cả đầu' : `Đứng lùi lại để thấy từ đầu đến ${kneeText}`));
    }
  }

  function updateGestureUi() {
    const available = !!pose && pose.gestureAvailable && poseStatus !== 'failed';
    gestureBtn.classList.toggle('hidden', !available);
    gestureBtn.classList.toggle('off', !gestureOn);
    gestureBtn.setAttribute('aria-pressed', gestureOn ? 'true' : 'false');
    pose?.setGesture(available && gestureOn && phase === 'live');
    gestureHint.classList.toggle('hidden', !(available && gestureOn && phase === 'live' && stripEntries().length > 1));
  }

  function toggleGesture() {
    markActivity();
    gestureOn = !gestureOn;
    updateGestureUi();
    toast(gestureOn ? 'Đã bật đổi đồ bằng cử chỉ giơ tay' : 'Đã tắt cử chỉ', { ms: 1500 });
  }

  async function checkNetwork() {
    rowNet.set('wait', 'Đang kiểm tra…');
    try {
      sdk = await loadDecartSdk();
      if (scope.disposed) return;
      decart = decart || sdk.createDecartClient({ apiKeyProvider: fetchLiveToken });
      const controller = new AbortController();
      scope.add(() => controller.abort());
      const report = await decart.realtime.checkConnectivity({ signal: controller.signal });
      if (scope.disposed) return;
      netReport = report;
      const rtt = report?.metrics?.rttMs;
      const transport = report?.metrics?.transport;
      const detail = [QUALITY_VI[report.quality] || report.quality, rtt != null ? `${Math.round(rtt)} ms` : null, transport === 'relay' ? 'qua relay' : null].filter(Boolean).join(' · ');
      const state = report.quality === 'good' || report.quality === 'fair' ? 'ok' : report.quality === 'poor' ? 'warn' : 'bad';
      rowNet.set(state, report.quality === 'critical' ? `${detail} — có thể không chạy được` : detail);
    } catch (err) {
      if (scope.disposed) return;
      rowNet.set('neutral', err?.userMessage || 'Không kiểm tra được');
    }
    updateStartButton();
  }

  // ---------- token ----------
  /** apiKeyProvider for the SDK: POST /api/tryon/live-token → client token string. */
  async function fetchLiveToken() {
    if (mock) return 'ek_mock';
    let res;
    try {
      res = await fetchWithAccess('/api/tryon/live-token', { method: 'POST', headers: { Accept: 'application/json' }, cache: 'no-store' });
    } catch {
      throw liveError('Không kết nối được máy chủ SHADOW để lấy token Decart. Kiểm tra mạng.');
    }
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = text; }
    const detail = data && typeof data === 'object' ? (typeof data.detail === 'string' ? data.detail : (data.detail?.message || data.error || data.message || '')) : (typeof data === 'string' ? data.slice(0, 200) : '');
    if (res.status === 503) throw liveError(detail || 'Chưa cấu hình DECART_API_KEY.', { code: 'NO_DECART_KEY', status: 503, detail });
    if (res.status === 429) throw liveError(detail || 'Đang có quá nhiều yêu cầu mặc thử trực tiếp — đợi một chút rồi thử lại nhé.', { status: 429 });
    if (res.status === 401) throw liveError('Cần mã truy cập để mặc thử trực tiếp. Tải lại trang rồi nhập mã nhé.', { status: 401 });
    if (res.status === 404) throw liveError('Máy chủ chưa hỗ trợ mặc thử trực tiếp (thiếu /api/tryon/live-token). Cập nhật backend rồi thử lại.', { status: 404 });
    if (!res.ok) throw liveError(`Máy chủ không cấp được token Decart (lỗi ${res.status})${detail ? `: ${detail}` : ''}.`, { status: res.status });
    const key = typeof data === 'string' ? data
      : (data?.apiKey || data?.api_key || data?.client_token || data?.token?.apiKey || (typeof data?.token === 'string' && data.token.startsWith('ek_') ? data.token : null) || data?.key || data?.token);
    if (!key || typeof key !== 'string') throw liveError('Máy chủ trả token Decart không đúng định dạng (thiếu apiKey).');
    return key;
  }

  // ---------- session ----------
  async function startSession() {
    if (phase === 'connecting' || phase === 'live') return;
    if (!slotItems(current).length) { toast('Chọn ít nhất một món đồ trước nhé.'); openPicker(); return; }
    if (!camStream || !camOk) { await openCamera(); if (!camOk) return; }
    const my = ++session;
    stopping = false;
    remoteShown = false;
    appliedKey = null;
    tickSec = null;
    connectedAt = 0;
    qualityDismissed = false;
    connectText.textContent = 'Đang kết nối Decart…';
    connectSub.textContent = 'Thường mất khoảng 3 giây';
    setPhase('connecting');
    markActivity();
    // Pre-compose the first garment image while the connection is being set up.
    void garmentFor(current).catch(() => {});
    try {
      sdk = sdk || await loadDecartSdk();
      if (my !== session) return;
      decart = decart || sdk.createDecartClient({ apiKeyProvider: fetchLiveToken });
      const client = await decart.realtime.connect(camStream, {
        model: sdk.models.realtime(LIVE_MODEL),
        onRemoteStream: (stream) => { if (my === session) attachRemote(stream, my); },
        onConnectionChange: (state) => { if (my === session) onConnectionChange(state); },
        onConnectionQuality: (report) => { if (my === session) onQuality(report); },
        onQueuePosition: (qp) => { if (my === session && qp) connectSub.textContent = `Đang xếp hàng: vị trí ${qp.position}/${qp.queueSize}`; },
        onCapacityWait: (w) => { if (my === session && w) connectSub.textContent = `Decart đang bận, tự thử lại sau ${Math.ceil((w.retryAfterMs || 0) / 1000)} giây…`; },
        retries: 2,
        capacityRetryBudgetMs: 30000,
        ...(speed ? { speed } : {}),
      });
      if (my !== session) { try { client.disconnect(); } catch { /* ignore */ } return; }
      rt = client;
      connectedAt = performance.now();
      rt.on('error', (err) => {
        if (my !== session) return;
        console.warn('[tryon-live] realtime error', err);
        toast(decartErrorMessage(err), { kind: 'error', ms: 4500 });
      });
      rt.on('sessionEnded', (e) => { if (my === session) stopSession('server', sessionEndMessage(e?.reason)); });
      rt.on('generationTick', (e) => { if (my === session && Number.isFinite(e?.seconds)) tickSec = e.seconds; });
      connectSub.textContent = 'Đang chờ khung hình đầu tiên…';
      scheduleApply(0);
    } catch (err) {
      if (my !== session) return;
      console.warn('[tryon-live] connect failed', err);
      rt = null;
      if (err?.code === 'NO_DECART_KEY') { liveAvailable = false; rowServer.set('bad', 'Chưa có DECART_API_KEY'); showBlocked(err.detail || ''); return; }
      showEnded('error', decartErrorMessage(err));
    }
  }

  function onConnectionChange(state) {
    if (state === 'reconnecting') {
      if (phase === 'live') { connectText.textContent = 'Mất kết nối, đang nối lại…'; connectSub.textContent = ''; connectOverlay.classList.remove('hidden'); }
    } else if (state === 'connected' || state === 'generating') {
      if (phase === 'live') connectOverlay.classList.add('hidden');
    } else if (state === 'disconnected' && rt && !stopping) {
      stopSession('dropped');
    }
  }

  function onQuality(report) {
    if (!report) return;
    const bars = QUALITY_BARS[report.quality] || 0;
    qualityBars.dataset.q = report.quality;
    [...qualityBars.children].forEach((b, i) => b.classList.toggle('on', i < bars));
    liveChip.title = `Kết nối: ${QUALITY_VI[report.quality] || report.quality}`;
    const low = !report.warmingUp && (report.quality === 'poor' || report.quality === 'critical');
    if (low && !qualityDismissed && phase === 'live') {
      qualityBanner.replaceChildren(icon('alert', 16),
        h('span', null, `Mạng ${report.quality === 'critical' ? 'rất yếu' : 'yếu'} (${LIMIT_VI[report.limitingFactor] || LIMIT_VI.none}) — video có thể giật.`),
        h('button', { class: 'tl-banner-x', type: 'button', 'aria-label': 'Ẩn cảnh báo', onClick: () => { qualityDismissed = true; qualityBanner.classList.add('hidden'); } }, icon('close', 16)));
      qualityBanner.classList.remove('hidden');
    } else if (!low) {
      qualityBanner.classList.add('hidden');
    }
  }

  function attachRemote(stream, my) {
    remoteVideo.srcObject = stream;
    remoteVideo.play().catch(() => {});
    // The first frames of a session can be black: keep the local preview until real frames arrive.
    const probe = document.createElement('canvas');
    probe.width = 24; probe.height = 32;
    const pctx = probe.getContext('2d', { willReadFrequently: true });
    const t0 = performance.now();
    const check = () => {
      if (my !== session || remoteShown) return;
      let real = false;
      if (remoteVideo.readyState >= 2 && remoteVideo.videoWidth) {
        try {
          pctx.drawImage(remoteVideo, 0, 0, probe.width, probe.height);
          const d = pctx.getImageData(0, 0, probe.width, probe.height).data;
          let sum = 0; let max = 0;
          for (let i = 0; i < d.length; i += 4) { const l = (d[i] + d[i + 1] + d[i + 2]) / 3; sum += l; if (l > max) max = l; }
          real = sum / (d.length / 4) > 12 || max > 60;
        } catch { real = true; }
      }
      if (real || performance.now() - t0 > FIRST_FRAME_FALLBACK_MS) { showRemote(); return; }
      scope.timeout(check, 120);
    };
    check();
  }

  function showRemote() {
    remoteShown = true;
    root.classList.add('remote-on');
    setPhase('live');
    renderFraming();
    updateMeter();
  }

  function garmentFor(slots) {
    const key = slotKey(slots);
    if (!blobCache.has(key)) {
      const p = composeGarmentImage(slotItems(slots), itemImageUrl);
      p.catch(() => blobCache.delete(key));
      blobCache.set(key, p);
    }
    return blobCache.get(key);
  }

  function scheduleApply(delay = APPLY_DEBOUNCE_MS) {
    clearTimeout(applyTimer);
    if (!rt) return;
    applyTimer = setTimeout(runApply, delay);
  }
  scope.add(() => clearTimeout(applyTimer));

  async function runApply() {
    if (!rt) return;
    const slots = { ...current };
    const key = slotKey(slots);
    if (!key || key === appliedKey) return;
    if (applying) { applyAgain = true; return; }
    applying = true;
    const my = session;
    if (phase === 'live') applyingChip.classList.remove('hidden');
    try {
      const prompt = buildOutfitPrompt(slots);
      const blob = await garmentFor(slots);
      if (!rt || my !== session) return;
      // Same call as tryon-examples mobile/page.tsx applyOutfit(): enhance off (prompt is
      // already descriptive), 30 s ack timeout.
      await rt.setImage(blob, { prompt, enhance: false, timeout: 30000 });
      if (my === session) appliedKey = key;
    } catch (err) {
      if (my === session) {
        console.warn('[tryon-live] setImage failed', err);
        toast(`Không đổi được đồ: ${decartErrorMessage(err)}`, { kind: 'error' });
      }
    } finally {
      applying = false;
      applyingChip.classList.add('hidden');
      if (applyAgain && my === session) { applyAgain = false; runApply(); } else applyAgain = false;
    }
  }

  function activeSeconds() {
    if (!connectedAt) return 0;
    const local = (performance.now() - connectedAt) / 1000;
    return tickSec != null ? Math.max(tickSec, Math.min(local, tickSec + 2)) : local;
  }

  function updateMeter() {
    const sec = activeSeconds();
    costTime.textContent = fmtDuration(sec * 1000);
    costUsd.textContent = `~${money(sec * costPerSec)}`;
  }

  function markActivity() {
    lastActivity = performance.now();
    if (!timerBanner.classList.contains('hidden') && timerBanner.dataset.kind === 'idle') timerBanner.classList.add('hidden');
  }

  // 4 Hz watchdog: meter, idle cut-off, hard cap.
  scope.interval(() => {
    if (phase !== 'live' && phase !== 'connecting') return;
    if (phase === 'live') updateMeter();
    const idleLeft = idleSec - (performance.now() - lastActivity) / 1000;
    const capLeft = connectedAt ? capSec - activeSeconds() : Infinity;
    if (capLeft <= 0) { stopSession('cap'); return; }
    if (idleLeft <= 0) { stopSession('idle'); return; }
    if (phase !== 'live') return;
    if (capLeft <= CAP_WARN_SEC) {
      timerBanner.dataset.kind = 'cap';
      timerBanner.replaceChildren(icon('clock', 16), h('span', null, `Phiên sẽ kết thúc sau ${Math.ceil(capLeft)} giây (giới hạn ${fmtMinutes(capSec)}).`));
      timerBanner.classList.remove('hidden');
    } else if (idleLeft <= IDLE_WARN_SEC) {
      timerBanner.dataset.kind = 'idle';
      timerBanner.replaceChildren(icon('clock', 16), h('span', null, `Tự ngắt sau ${Math.ceil(idleLeft)} giây nếu không thao tác — chạm màn hình để tiếp tục.`));
      timerBanner.classList.remove('hidden');
    } else {
      timerBanner.classList.add('hidden');
    }
  }, 250);

  function stopSession(reason, message) {
    if (phase !== 'connecting' && phase !== 'live') return;
    stopping = true;
    session += 1;
    const sec = activeSeconds();
    lastStats = connectedAt ? { sec, usd: sec * costPerSec } : null;
    clearTimeout(applyTimer);
    try { rt?.disconnect(); } catch (err) { console.warn('disconnect failed', err); }
    rt = null;
    connectedAt = 0;
    remoteShown = false;
    root.classList.remove('remote-on');
    remoteVideo.srcObject = null;
    framingBanner.classList.add('hidden');
    const texts = {
      user: 'Bạn đã dừng phiên mặc thử.',
      idle: `Đã tự ngắt vì ${idleSec} giây không thao tác (để tiết kiệm chi phí).`,
      cap: `Đã hết thời lượng tối đa ${fmtMinutes(capSec)} cho một phiên.`,
      hidden: 'Đã ngắt khi bạn rời màn hình để không tốn phí.',
      dropped: 'Mất kết nối với Decart.',
    };
    if (reason === 'leave') return;
    if (reason === 'user' && !lastStats) { setPhase('preflight'); return; }
    showEnded(reason, message || texts[reason] || 'Phiên đã kết thúc.');
  }

  // ---------- message cards ----------
  function showBlocked(detail) {
    const href = photoHref();
    msgCard.replaceChildren(
      h('div', { class: 'tl-msg-ic bad' }, icon('alert', 26)),
      h('h2', { class: 'tl-msg-title' }, 'Chưa bật mặc thử trực tiếp'),
      detail ? h('p', { class: 'tl-msg-detail' }, detail) : null,
      h('p', { class: 'tl-msg-body' },
        'Mặc thử trực tiếp dùng Decart (', h('code', null, LIVE_MODEL), '). Người quản trị cần đặt ',
        h('code', null, 'DECART_API_KEY'), ' trong file ', h('code', null, '.env'), ' của máy chủ SHADOW rồi chạy lại ',
        h('code', null, './run.sh'), '. Khoá chỉ nằm trên máy chủ; trình duyệt chỉ nhận token ngắn hạn.'),
      h('p', { class: 'tl-msg-body' }, 'Trong lúc chờ, bạn vẫn có thể dùng Ảnh mặc thử (chụp một tấm ảnh toàn thân, chờ 25–60 giây).'),
      h('a', { class: 'btn btn-primary block tl-fallback', href }, icon('image', 20), 'Ảnh mặc thử'),
      h('button', { class: 'btn btn-ghost block', type: 'button', onClick: () => leave() }, 'Quay lại'));
    setPhase('blocked');
  }

  function showEnded(reason, message) {
    const isError = reason === 'error' || reason === 'server' || reason === 'dropped';
    msgCard.replaceChildren(
      h('div', { class: `tl-msg-ic ${isError ? 'bad' : ''}` }, icon(isError ? 'alert' : 'check', 26)),
      h('h2', { class: 'tl-msg-title' }, isError ? 'Không mặc thử được' : 'Phiên đã kết thúc'),
      h('p', { class: 'tl-msg-body' }, message),
      lastStats ? h('div', { class: 'tl-msg-stats' },
        h('span', null, icon('clock', 15), fmtDuration(lastStats.sec * 1000)),
        h('span', null, `ước tính ~${money(lastStats.usd)}`)) : null,
      h('button', { class: 'btn btn-primary block', type: 'button', onClick: () => startSession() }, icon('refresh', 20), isError ? 'Thử lại' : 'Mặc thử tiếp'),
      h('div', { class: 'row gap' },
        h('a', { class: 'btn btn-ghost grow', href: photoHref() }, 'Ảnh mặc thử'),
        h('button', { class: 'btn btn-ghost grow', type: 'button', onClick: () => setPhase('preflight') }, 'Đổi đồ')));
    setPhase('ended');
  }

  // ---------- snapshot ----------
  function snapshot() {
    markActivity();
    const v = remoteShown ? remoteVideo : null;
    if (!v || !v.videoWidth) { toast('Chưa có hình để chụp.'); return; }
    const c = document.createElement('canvas');
    c.width = v.videoWidth; c.height = v.videoHeight;
    const ctx = c.getContext('2d');
    // Saved un-mirrored (true orientation) so text/logos on garments read correctly.
    ctx.drawImage(v, 0, 0);
    const fs = Math.max(14, Math.round(c.width / 36));
    ctx.font = `600 ${fs}px "Be Vietnam Pro", system-ui, sans-serif`;
    const label = 'Ảnh do AI tạo · SHADOW × Decart';
    const tw = ctx.measureText(label).width;
    ctx.fillStyle = 'rgba(0,0,0,0.45)';
    ctx.fillRect(fs * 0.6, c.height - fs * 2.4, tw + fs, fs * 1.7);
    ctx.fillStyle = '#fff';
    ctx.fillText(label, fs * 1.1, c.height - fs * 1.1);
    flash.classList.remove('go'); void flash.offsetWidth; flash.classList.add('go');
    c.toBlob((blob) => {
      if (!blob) { toast('Không chụp được ảnh.', { kind: 'error' }); return; }
      const url = URL.createObjectURL(blob);
      const stamp = new Date().toISOString().slice(0, 19).replace(/[-:T]/g, '');
      const a = h('a', { href: url, download: `shadow-mac-thu-${stamp}.jpg` });
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
      window.__TRYON_LIVE_LAST_SNAPSHOT__ = { bytes: blob.size, w: c.width, h: c.height };
      toast('Đã lưu ảnh mặc thử', { kind: 'ok', ms: 1800 });
    }, 'image/jpeg', 0.92);
  }

  // ---------- swipe on the video to switch outfits ----------
  let swipe = null;
  scope.listen(stage, 'pointerdown', (e) => { swipe = { x: e.clientX, y: e.clientY, t: performance.now() }; });
  scope.listen(stage, 'pointerup', (e) => {
    if (!swipe || phase !== 'live') { swipe = null; return; }
    const dx = e.clientX - swipe.x; const dy = e.clientY - swipe.y;
    if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.5 && performance.now() - swipe.t < 800) stepEntry(dx < 0 ? 'next' : 'prev');
    swipe = null;
  });

  // ---------- activity / lifecycle ----------
  scope.listen(root, 'pointerdown', () => markActivity(), { passive: true });
  scope.listen(window, 'keydown', () => markActivity());
  scope.listen(document, 'visibilitychange', () => {
    if (document.visibilityState === 'hidden') {
      stopSession('hidden');
      pose?.stop();
    } else if (camOk) {
      pose?.start();
    }
  });

  function leave() {
    stopSession('leave');
    if (history.length > 1) history.back();
    else navigate('#/wardrobe');
  }

  const releaseWake = keepScreenAwake();
  scope.add(releaseWake);
  scope.add(() => {
    stopping = true;
    session += 1;
    try { rt?.disconnect(); } catch { /* ignore */ }
    rt = null;
    remoteVideo.srcObject = null;
    stopCamera(camStream, localVideo);
    camStream = null;
  });

  // ---------- boot ----------
  costRate.textContent = `~ ${money(costPerSec)}/giây`;
  renderNote();
  renderOutfit();
  setPhase('preflight');
  if (mock) console.info('[tryon-live] Decart MOCK mode — no token, no cost');
  loadConfig().then(() => {
    if (scope.disposed || phase === 'blocked') return;
    openCamera();
    checkNetwork();
  });
  loadWardrobe();

  return () => scope.dispose();
}
