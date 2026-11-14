// Ảnh mặc thử — full-body photo (front camera + body guide + 3 s timer, or upload) → POST /api/tryon/photo
// → progress with elapsed seconds (cancel) → before/after compare slider, provider, save/share.
import { wardrobeApi, resultImageUrl } from '../wardrobe-api.js';
import { h, icon, toast, errorToast, navigate, createScope } from '../ui.js';
import {
  categoryLabel, itemThumb, openCameraCapture, outfitFromHash, providerLabel as legacyProviderLabel, outfitHash, pickImageFile, fileToJpeg, fill,
} from '../wardrobe-ui.js';

const PHOTO_KEY = 'shadow.wd.person';
const STEPS = [
  [0, 'Đang gửi ảnh…'],
  [3, 'AI đang nhận dáng người…'],
  [9, 'Đang mặc đồ cho bạn…'],
  [22, 'Đang hoàn thiện chi tiết vải…'],
  [40, 'Sắp xong rồi, chờ chút nhé…'],
  [70, 'Máy chủ hơi bận, vẫn đang làm…'],
];
const EXPECT = { worker: 'khoảng 20–40 giây', hf_space: 'khoảng 25–60 giây', mock: 'vài giây' };
const providerLabel = (provider) => provider === 'vidu' ? 'Vidu' : legacyProviderLabel(provider);
function expectText(config) {
  const p = config?.photo?.provider;
  const sec = Number(config?.photo?.expected_seconds);
  if (EXPECT[p]) return EXPECT[p];
  if (Number.isFinite(sec) && sec > 0) return `khoảng ${Math.round(sec)} giây`;
  return '25–60 giây';
}

export function mount(el) {
  const scope = createScope();
  const ids = outfitFromHash();
  let items = [];
  let config = null;
  let person = (() => { try { return sessionStorage.getItem(PHOTO_KEY) || null; } catch { return null; } })();
  let controller = null;
  let camApi = null;
  scope.add(() => camApi?.close());
  scope.add(() => controller?.abort());

  const strip = h('div', { class: 'wd-strip' });
  const main = h('section', { class: 'wd-tryon-main' });

  el.append(
    h('header', { class: 'app-bar' },
      h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Quay lại', onClick: () => navigate('#/stylist') }, icon('back', 24)),
      h('div', { class: 'app-bar-title' }, 'Ảnh mặc thử')),
    h('div', { class: 'wd-scroll' }, strip, main),
  );

  function renderStrip() {
    if (!ids.length) {
      strip.replaceChildren(h('div', { class: 'wd-hint' }, 'Chưa chọn bộ đồ. Quay lại Phối đồ để chọn một bộ.'));
      return;
    }
    strip.replaceChildren(
      h('div', { class: 'wd-strip-k' }, 'Bộ đồ sẽ mặc thử'),
      h('div', { class: 'wd-strip-row' }, ids.map((id) => {
        const it = items.find((x) => String(x.id) === id) || { id, name: '…' };
        return h('div', { class: 'wd-strip-item' }, itemThumb(it), h('span', null, it.name || categoryLabel(it.category)));
      })));
  }

  const hasBottoms = () => ids.some((id) => items.find((x) => String(x.id) === id)?.category === 'bottom');

  // ---------- step 1: photo ----------
  function renderCapture() {
    const frame = h('div', { class: 'wd-person-frame' },
      person ? h('img', { src: person, alt: 'Ảnh toàn thân của bạn' }) : h('div', { class: 'wd-person-empty' },
        h('div', { class: 'wd-person-guide', 'aria-hidden': 'true' }, '🧍'),
        h('p', null, 'Ảnh toàn thân, đứng thẳng, thấy rõ từ đầu tới chân.'),
        h('ul', { class: 'wd-person-tips' },
          h('li', null, 'Đặt điện thoại ngang ngực, lùi xa 2–3 m'),
          h('li', null, 'Nền gọn, đủ sáng, tay để xuôi'),
          h('li', null, 'Bật hẹn giờ 3 giây để kịp tạo dáng'))));
    const camBtn = h('button', { class: `btn ${person ? 'btn-ghost' : 'btn-primary'} grow`, type: 'button', onClick: () => takePhoto() },
      icon('camera', 20), person ? 'Chụp lại' : 'Chụp ảnh toàn thân');
    const goBtn = person ? h('button', { class: 'btn btn-primary btn-xl', type: 'button', disabled: !ids.length, onClick: () => run() }, icon('wand', 22), 'Mặc thử ngay') : null;
    fill(main, frame, h('div', { class: 'wd-row2' }, camBtn,
      h('button', { class: 'btn btn-ghost', type: 'button', onClick: () => uploadPhoto() }, icon('image', 20), 'Tải ảnh')),
      goBtn,
      config ? h('p', { class: 'wd-muted wd-center' }, `Máy mặc thử: ${providerLabel(config?.photo?.provider)} · ${expectText(config)}`) : null,
      config?.photo?.notice ? h('div', { class: 'wd-note' }, icon('alert', 18), h('div', null, String(config.photo.notice))) : null,
      config?.photo && config.photo.renders_bottoms === false && hasBottoms()
        ? h('p', { class: 'wd-muted wd-center' }, 'Máy này chỉ vẽ áo/váy — quần chưa hiện trong ảnh.') : null);
  }

  function setPerson(img) {
    person = img;
    try { sessionStorage.setItem(PHOTO_KEY, img); } catch { /* quota: memory only */ }
    renderCapture();
  }

  async function takePhoto() {
    camApi = openCameraCapture({
      facingMode: 'user', title: 'Ảnh toàn thân', hint: 'Đứng vào khung nét đứt · thấy cả đầu và chân', silhouette: true, timer: true, maxSide: 1024,
    });
    const img = await camApi.result;
    camApi = null;
    if (img && !scope.disposed) setPerson(img);
  }

  async function uploadPhoto() {
    const file = await pickImageFile();
    if (!file || scope.disposed) return;
    try { setPerson(await fileToJpeg(file, { maxSide: 1024, quality: 0.88 })); } catch (err) { errorToast(err, 'Không đọc được ảnh'); }
  }

  // ---------- step 2: run ----------
  async function run() {
    if (!person || !ids.length) return;
    controller = new AbortController();
    const line = h('div', { class: 'loading-line' }, STEPS[0][1]);
    const secs = h('div', { class: 'wd-secs' }, '0');
    const bar = h('i');
    main.replaceChildren(h('div', { class: 'wd-progress' },
      h('div', { class: 'wd-progress-img' }, h('img', { src: person, alt: '' }), h('div', { class: 'wd-scan', 'aria-hidden': 'true' })),
      h('div', { class: 'wd-progress-row' }, secs, h('span', { class: 'wd-secs-unit' }, 'giây')),
      line,
      h('div', { class: 'wd-bar' }, bar),
      h('p', { class: 'wd-muted wd-center' }, `Thường mất ${expectText(config)}. Bạn cứ để máy chạy.`),
      h('button', { class: 'btn btn-ghost', type: 'button', onClick: () => controller?.abort() }, icon('close', 18), 'Huỷ')));
    const t0 = performance.now();
    const tau = Math.max(1, (Number(config?.photo?.expected_seconds) || 35) * 0.75);
    let stage = 0;
    const tick = setInterval(() => {
      const s = (performance.now() - t0) / 1000;
      secs.textContent = String(Math.floor(s));
      bar.style.width = `${Math.min(96, 100 * (1 - Math.exp(-s / tau)))}%`;
      let next = 0;
      STEPS.forEach(([at], i) => { if (s >= at) next = i; });
      if (next !== stage) { stage = next; line.textContent = STEPS[stage][1]; line.classList.remove('swap'); void line.offsetWidth; line.classList.add('swap'); }
    }, 250);
    try {
      const res = await wardrobeApi.tryonPhoto({ person_image: person, item_ids: ids }, { signal: controller.signal });
      if (scope.disposed) return;
      const url = resultImageUrl(res);
      if (!url) throw new Error('Máy chủ không trả về ảnh');
      renderResult(res, url, (performance.now() - t0) / 1000);
    } catch (err) {
      if (scope.disposed) return;
      if (controller?.signal.aborted) toast('Đã huỷ mặc thử');
      else errorToast(err, 'Chưa mặc thử được, thử lại nhé');
      renderCapture();
    } finally {
      clearInterval(tick);
      // provider availability may have changed (e.g. Hugging Face quota used up) → refresh hints
      wardrobeApi.tryonConfig().then((c) => { if (!scope.disposed && c) config = c; }).catch(() => {});
      controller = null;
    }
  }

  // ---------- step 3: compare ----------
  function renderResult(res, url, clientSeconds) {
    const before = h('img', { class: 'wd-cmp-before', src: person, alt: 'Trước', draggable: 'false' });
    const after = h('img', { class: 'wd-cmp-after', src: url, alt: 'Sau khi mặc thử', draggable: 'false' });
    const handle = h('div', { class: 'wd-cmp-handle', 'aria-hidden': 'true' }, h('span', null, icon('left', 16), icon('right', 16)));
    const range = h('input', { class: 'wd-cmp-range', type: 'range', min: '0', max: '100', value: '50', 'aria-label': 'Kéo để so sánh trước và sau' });
    const cmp = h('div', { class: 'wd-cmp' }, after, before, handle,
      h('span', { class: 'wd-cmp-tag l' }, 'Trước'), h('span', { class: 'wd-cmp-tag r' }, 'Sau'), range);
    const setPos = (p) => {
      const v = Math.min(100, Math.max(0, p));
      cmp.style.setProperty('--pos', `${v}%`);
      range.value = String(Math.round(v));
    };
    setPos(50);
    after.addEventListener('load', () => {
      if (after.naturalWidth && after.naturalHeight) cmp.style.aspectRatio = `${after.naturalWidth} / ${after.naturalHeight}`;
    });
    range.addEventListener('input', () => setPos(Number(range.value)));
    let dragging = false;
    const fromEvent = (e) => { const r = cmp.getBoundingClientRect(); setPos(((e.clientX - r.left) / r.width) * 100); };
    cmp.addEventListener('pointerdown', (e) => { dragging = true; cmp.setPointerCapture?.(e.pointerId); fromEvent(e); });
    cmp.addEventListener('pointermove', (e) => { if (dragging) fromEvent(e); });
    const end = () => { dragging = false; };
    cmp.addEventListener('pointerup', end);
    cmp.addEventListener('pointercancel', end);
    // A little "peek" so people discover the slider.
    scope.timeout(() => { cmp.classList.add('peek'); setPos(70); scope.timeout(() => { setPos(50); cmp.classList.remove('peek'); }, 700); }, 600);

    const seconds = Number(res.seconds ?? clientSeconds);
    const note = res.note || res.notes || res.warning || res.message;
    const notes = Array.isArray(note) ? note.filter(Boolean) : (note ? [note] : []);
    const skipped = (Array.isArray(res.skipped_item_ids) ? res.skipped_item_ids : []).map(String);
    if (skipped.length && !notes.length) {
      notes.push(`Chưa vẽ được: ${skipped.map((id) => items.find((x) => String(x.id) === id)?.name || id).join(', ')}.`);
    }
    // AI provider failed and the backend fell back (usually to the offline demo composite): say so plainly.
    const fallback = res.fallback_reason ? String(res.fallback_reason) : '';
    let fallbackNote = null;
    if (fallback) {
      // "hf_space: <Vietnamese reason>; worker: <reason>" → one line per failed provider.
      const reasons = fallback.split(/;\s*(?=[a-z_]+:)/).map((part) => {
        const m = part.match(/^([a-z_]+):\s*(.*)$/s);
        if (!m) return part;
        const label = providerLabel(m[1]);
        return m[2].startsWith(label) ? m[2] : `${label}: ${m[2]}`;
      }).filter(Boolean);
      fallbackNote = h('div', { class: 'wd-note wd-note-fallback', role: 'status' }, icon('alert', 18),
        h('div', null,
          h('b', null, 'Máy mặc thử AI chưa chạy được.'),
          reasons.map((r) => h('div', null, r)),
          h('div', null, res.provider === 'mock'
            ? 'Ảnh trên chỉ là ảnh minh hoạ ghép tạm, không phải kết quả AI.'
            : `Đã chuyển sang ${providerLabel(res.provider)}.`),
          h('button', { class: 'wd-link', type: 'button', onClick: () => run() }, 'Thử lại với AI')));
      if (res.provider === 'mock') notes.splice(0, notes.length, ...notes.filter((n) => !/giả lập|demo/i.test(String(n))));
    }
    const fileName = `shadow-mac-thu-${Date.now()}.jpg`;
    const save = h('a', { class: 'btn btn-primary grow', href: url, download: fileName }, icon('image', 20), 'Lưu ảnh');
    const shareBtn = navigator.share ? h('button', { class: 'btn btn-ghost', type: 'button', onClick: () => share(url, fileName) }, 'Chia sẻ') : null;

    fill(main,
      cmp,
      h('div', { class: 'wd-result-meta' },
        h('span', { class: `wd-provider p-${res.provider || 'x'}` }, '⚙️ ', providerLabel(res.provider)),
        Number.isFinite(seconds) ? h('span', { class: 'chip' }, icon('clock', 14), `${Math.round(seconds)} giây`) : null,
        res.provider === 'mock' ? h('span', { class: 'chip' }, 'Ảnh minh hoạ') : null),
      fallbackNote,
      notes.length ? h('div', { class: 'wd-note' }, icon('alert', 18), h('div', null, notes.map((n) => h('div', null, String(n))))) : null,
      h('div', { class: 'wd-row2' }, save, shareBtn),
      h('div', { class: 'wd-row2' },
        h('button', { class: 'btn btn-ghost grow', type: 'button', onClick: () => renderCapture() }, icon('camera', 18), 'Chụp lại'),
        h('button', { class: 'btn btn-ghost grow', type: 'button', onClick: () => navigate('#/stylist') }, 'Bộ khác')),
      config?.live?.available ? h('a', { class: 'btn btn-ghost block', href: outfitHash('tryon-live', ids) }, '🎥 Mặc thử trực tiếp') : null);
  }

  async function share(url, fileName) {
    try {
      const blob = await (await fetch(url)).blob();
      const file = new File([blob], fileName, { type: blob.type || 'image/jpeg' });
      if (navigator.canShare?.({ files: [file] })) await navigator.share({ files: [file], title: 'Mặc thử với SKI' });
      else await navigator.share({ title: 'Mặc thử với SKI', url: new URL(url, location.href).href });
    } catch (err) {
      if (err?.name !== 'AbortError') toast('Không chia sẻ được — dùng “Lưu ảnh” nhé.');
    }
  }

  renderStrip();
  renderCapture();
  Promise.allSettled([wardrobeApi.listItems(), wardrobeApi.tryonConfig()]).then(([a, c]) => {
    if (scope.disposed) return;
    items = a.status === 'fulfilled' ? a.value : [];
    config = c.status === 'fulfilled' ? c.value : null;
    renderStrip();
    if (!controller && main.querySelector('.wd-person-frame')) renderCapture();
  });
  return () => scope.dispose();
}
