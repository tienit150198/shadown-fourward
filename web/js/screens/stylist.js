// Phối đồ — event + date + weather (+ optional "what I'm wearing" photo / anchor item) → 2–3 outfit cards.
import { wardrobeApi } from '../wardrobe-api.js';
import { h, icon, toast, errorToast, navigate, createScope, openSheet } from '../ui.js';
import { speak, cancelSpeech, unlockTTS, setMuted, ttsSupported } from '../speech.js';
import { getSettings, updateSettings } from '../store.js';
import {
  EVENTS, CATEGORIES, categoryLabel, itemThumb, colorDots, micButton, openCameraCapture, outfitHash,
  resolveRule, ruleBadge, localDate, weatherEmoji,
} from '../wardrobe-ui.js';

const STATE_KEY = 'shadow.wd.stylist';

function readState() {
  try { return JSON.parse(sessionStorage.getItem(STATE_KEY) || 'null') || {}; } catch { return {}; }
}
function writeState(s) {
  try { sessionStorage.setItem(STATE_KEY, JSON.stringify(s)); } catch {
    // quota (current_image is large) → keep everything except the photo
    try { sessionStorage.setItem(STATE_KEY, JSON.stringify({ ...s, currentImage: null })); } catch { /* ignore */ }
  }
}

function savedText(value, maxLength) {
  return typeof value === 'string' ? value.slice(0, maxLength) : '';
}

function fmtDateVi(iso) {
  if (!iso) return '';
  const [y, m, d] = iso.split('-').map(Number);
  const dt = new Date(y, m - 1, d);
  const wd = ['CN', 'T2', 'T3', 'T4', 'T5', 'T6', 'T7'][dt.getDay()];
  return `${wd}, ${d}/${m}`;
}

export function mount(el) {
  const scope = createScope();
  const settings = getSettings();
  setMuted(!settings.tts);
  scope.add(() => cancelSpeech());

  const saved = readState();
  const st = {
    event: saved.event ?? 'Đi làm',
    text: saved.text || '',
    clothingPreferences: savedText(saved.clothingPreferences, 200),
    gender: savedText(saved.gender, 60),
    ageRange: savedText(saved.ageRange, 40),
    fitPreferences: savedText(saved.fitPreferences, 200),
    dateMode: saved.dateMode || 'today',
    customDate: saved.customDate || localDate(2),
    city: saved.city || settings.wardrobeCity || 'Hà Nội',
    useGeo: !!settings.wardrobeUseGeo,
    coords: null,
    currentImage: saved.currentImage || null,
    anchorId: saved.anchorId || null,
    result: saved.result || null,
  };
  let items = [];
  let rules = [];
  let config = null;
  let weather = null;
  let weatherSeq = 0;
  let busy = false;
  let controller = null;

  const persist = () => writeState({
    event: st.event, text: st.text,
    clothingPreferences: st.clothingPreferences, gender: st.gender, ageRange: st.ageRange, fitPreferences: st.fitPreferences,
    dateMode: st.dateMode, customDate: st.customDate, city: st.city,
    currentImage: st.currentImage, anchorId: st.anchorId, result: st.result,
  });
  const dateValue = () => (st.dateMode === 'today' ? localDate(0) : st.dateMode === 'tomorrow' ? localDate(1) : st.customDate);

  // ---------- event ----------
  const chipsEl = h('div', { class: 'wd-chip-wrap wd-events' });
  const renderEvents = () => chipsEl.replaceChildren(...EVENTS.map((ev) => h('button', {
    class: `wd-toggle lg${st.event === ev ? ' on' : ''}`, type: 'button', 'aria-pressed': String(st.event === ev),
    onClick: () => { st.event = st.event === ev ? '' : ev; renderEvents(); persist(); },
  }, ev)));
  renderEvents();
  const textIn = h('input', { class: 'input', type: 'text', value: st.text, placeholder: 'Hoặc gõ / nói: đi cà phê với bạn cũ…', maxlength: '160', enterkeyhint: 'done' });
  textIn.addEventListener('input', () => { st.text = textIn.value; persist(); });
  const mic = micButton({
    label: 'Nói sự kiện',
    onInterim: (t) => { textIn.value = t; },
    onText: (t) => { textIn.value = t; st.text = t; persist(); },
  });
  scope.add(() => mic.cancel());

  // ---------- optional personal preferences ----------
  const profileField = (key, label, placeholder, maxLength) => {
    const input = h('input', {
      class: 'input', type: 'text', value: st[key], placeholder, maxlength: String(maxLength), autocomplete: 'off',
    });
    input.addEventListener('input', () => {
      st[key] = input.value.slice(0, maxLength);
      persist();
    });
    return h('label', { class: 'field' }, h('span', { class: 'field-label' }, label), input);
  };
  const profileFields = h('section', { class: 'wd-block' },
    h('h2', { class: 'wd-q' }, 'Phong cách của bạn ', h('span', { class: 'wd-muted' }, '(không bắt buộc)')),
    h('p', { class: 'wd-muted' }, 'Bạn tự mô tả điều mình muốn chia sẻ; có thể bỏ trống mọi mục.'),
    profileField('clothingPreferences', 'Ưu tiên trang phục', 'Ví dụ: đồ nam, nữ, unisex, casual', 200),
    profileField('gender', 'Giới tính (tự mô tả)', 'Ví dụ: nữ, nam, phi nhị nguyên…', 60),
    profileField('ageRange', 'Độ tuổi', 'Ví dụ: 25–34', 40),
    profileField('fitPreferences', 'Độ vừa / phom dáng', 'Ví dụ: rộng, ôm vừa, thoải mái', 200));

  // ---------- date ----------
  const dateSeg = h('div', { class: 'wd-seg', role: 'radiogroup', 'aria-label': 'Ngày' });
  const dateIn = h('input', { class: 'input wd-date', type: 'date', value: st.customDate, min: localDate(0), max: localDate(15) });
  dateIn.addEventListener('change', () => { if (dateIn.value) { st.customDate = dateIn.value; persist(); loadWeather(); } });
  const renderDate = () => {
    dateSeg.replaceChildren(...[['today', 'Hôm nay'], ['tomorrow', 'Ngày mai'], ['custom', 'Chọn ngày']].map(([id, label]) => h('button', {
      class: `wd-seg-btn${st.dateMode === id ? ' on' : ''}`, type: 'button', role: 'radio', 'aria-checked': String(st.dateMode === id),
      onClick: () => {
        st.dateMode = id; renderDate(); persist(); loadWeather();
        if (id === 'custom') { try { dateIn.showPicker?.(); } catch { /* needs gesture / unsupported */ } }
      },
    }, label)));
    dateIn.classList.toggle('hidden', st.dateMode !== 'custom');
  };
  renderDate();

  // ---------- weather ----------
  const weatherCard = h('div', { class: 'wd-weather' });
  function renderWeather(state, err) {
    const cityBtn = h('button', { class: 'wd-city', type: 'button', onClick: editCity }, icon('edit', 14),
      st.coords ? (weather?.city || 'Vị trí của bạn') : st.city);
    const geoBtn = !st.coords ? h('button', { class: 'wd-geo', type: 'button', onClick: () => useLocation(true) }, '📍 Dùng vị trí') : null;
    if (state === 'loading') {
      weatherCard.replaceChildren(h('div', { class: 'wd-weather-emoji sk' }), h('div', { class: 'wd-weather-body' },
        h('div', { class: 'wd-weather-top' }, cityBtn, geoBtn), h('div', { class: 'sk-line w70' })));
      return;
    }
    if (state === 'error') {
      const notFound = /không tìm thấy/i.test(err || '');
      weatherCard.replaceChildren(h('div', { class: 'wd-weather-emoji' }, '🌡️'), h('div', { class: 'wd-weather-body' },
        h('div', { class: 'wd-weather-top' }, cityBtn, geoBtn),
        h('div', { class: 'wd-weather-sum' }, err ? `${err} Trợ lý vẫn phối được.` : 'Chưa lấy được thời tiết — trợ lý vẫn phối được.'),
        notFound
          ? h('button', { class: 'wd-link', type: 'button', onClick: editCity }, 'Sửa tên thành phố')
          : h('button', { class: 'wd-link', type: 'button', onClick: () => loadWeather() }, 'Thử lại')));
      return;
    }
    const w = weather || {};
    const tmin = w.t_min ?? w.temp_min;
    const tmax = w.t_max ?? w.temp_max;
    const rain = w.rain_prob ?? w.precipitation_probability;
    weatherCard.replaceChildren(
      h('div', { class: 'wd-weather-emoji', 'aria-hidden': 'true' }, weatherEmoji(w.code)),
      h('div', { class: 'wd-weather-body' },
        h('div', { class: 'wd-weather-top' }, cityBtn, geoBtn),
        h('div', { class: 'wd-weather-temp' },
          tmin != null && tmax != null ? `${Math.round(tmin)}° – ${Math.round(tmax)}°C` : (tmax != null ? `${Math.round(tmax)}°C` : '—'),
          rain != null ? h('span', { class: 'wd-rain' }, `💧 ${Math.round(rain)}%`) : null),
        h('div', { class: 'wd-weather-sum' }, [w.description_vi || w.summary_vi, fmtDateVi(w.date || dateValue())].filter(Boolean).join(' · '))));
  }

  async function loadWeather() {
    const seq = ++weatherSeq;
    renderWeather('loading');
    const params = { date: dateValue() };
    if (st.coords) { params.lat = st.coords.lat.toFixed(4); params.lon = st.coords.lon.toFixed(4); } else params.city = st.city;
    try {
      const w = await wardrobeApi.weather(params);
      if (scope.disposed || seq !== weatherSeq) return;
      if (!w || w.available === false) {
        weather = null;
        renderWeather('error', w?.message);
        return;
      }
      weather = w;
      renderWeather('ok');
    } catch (err) {
      if (scope.disposed || seq !== weatherSeq) return;
      weather = null;
      renderWeather('error', err.status === 404 ? 'Không tìm thấy thành phố này.' : null);
    }
  }

  function editCity() {
    const input = h('input', { class: 'input', type: 'text', value: st.coords ? '' : st.city, placeholder: 'VD: Hà Nội, Đà Nẵng, TP HCM', enterkeyhint: 'search' });
    const ok = h('button', { class: 'btn btn-primary block', type: 'submit' }, 'Xem thời tiết');
    const form = h('form', { class: 'wd-city-form' }, input, ok,
      h('button', { class: 'btn btn-ghost block', type: 'button', onClick: () => { sheet.close(); useLocation(true); } }, '📍 Dùng vị trí hiện tại'));
    const sheet = openSheet(form, { title: 'Thành phố' });
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      const v = input.value.trim();
      if (!v) return;
      st.city = v;
      st.coords = null;
      updateSettings({ wardrobeCity: v, wardrobeUseGeo: false });
      persist();
      sheet.close();
      loadWeather();
    });
    setTimeout(() => input.focus(), 320);
  }

  function useLocation(ask) {
    if (!navigator.geolocation) { if (ask) toast('Máy không hỗ trợ định vị.'); loadWeather(); return; }
    if (ask) renderWeather('loading');
    navigator.geolocation.getCurrentPosition((pos) => {
      if (scope.disposed) return;
      st.coords = { lat: pos.coords.latitude, lon: pos.coords.longitude };
      updateSettings({ wardrobeUseGeo: true });
      loadWeather();
    }, () => {
      if (scope.disposed) return;
      if (ask) toast('Không lấy được vị trí — dùng thành phố nhé.');
      st.coords = null;
      loadWeather();
    }, { timeout: 8000, maximumAge: 30 * 60 * 1000, enableHighAccuracy: false });
  }

  async function initWeather() {
    let granted = false;
    try { granted = (await navigator.permissions?.query({ name: 'geolocation' }))?.state === 'granted'; } catch { /* ignore */ }
    if (scope.disposed) return;
    if (granted || (st.useGeo && navigator.geolocation)) useLocation(false); else loadWeather();
  }

  // ---------- optional: wearing photo / anchor ----------
  const extrasEl = h('div', { class: 'wd-extras' });
  function renderExtras() {
    const anchor = items.find((it) => it.id === st.anchorId);
    extrasEl.replaceChildren(
      st.currentImage
        ? h('div', { class: 'wd-extra on' }, h('img', { class: 'wd-extra-img', src: st.currentImage, alt: 'Đồ đang mặc' }),
          h('span', { class: 'wd-extra-label' }, 'Đồ đang mặc'),
          h('button', { class: 'wd-chip-x', type: 'button', 'aria-label': 'Bỏ ảnh', onClick: () => { st.currentImage = null; persist(); renderExtras(); } }, icon('close', 16)))
        : h('button', { class: 'wd-extra', type: 'button', onClick: snapWearing }, h('span', { class: 'wd-extra-icon' }, '📷'), h('span', { class: 'wd-extra-label' }, 'Chụp đồ đang mặc')),
      anchor
        ? h('div', { class: 'wd-extra on' }, itemThumb(anchor, { cls: 'wd-extra-img' }),
          h('span', { class: 'wd-extra-label' }, h('small', null, 'Giữ món này'), anchor.name || categoryLabel(anchor.category)),
          h('button', { class: 'wd-chip-x', type: 'button', 'aria-label': 'Bỏ chọn', onClick: () => { st.anchorId = null; persist(); renderExtras(); } }, icon('close', 16)))
        : h('button', { class: 'wd-extra', type: 'button', onClick: pickAnchor }, h('span', { class: 'wd-extra-icon' }, '📌'), h('span', { class: 'wd-extra-label' }, 'Giữ món này')));
  }

  let camApi = null;
  scope.add(() => camApi?.close());
  async function snapWearing() {
    camApi = openCameraCapture({ facingMode: 'user', title: 'Đồ đang mặc', hint: 'Đứng lùi lại để camera thấy rõ bộ đồ', timer: true, maxSide: 768 });
    const img = await camApi.result;
    camApi = null;
    if (!img || scope.disposed) return;
    st.currentImage = img;
    persist();
    renderExtras();
  }

  function pickAnchor() {
    if (!items.length) { toast('Tủ đồ còn trống — thêm đồ trước nhé.'); return; }
    const groups = CATEGORIES.map((c) => ({ c, list: items.filter((it) => it.category === c.id) })).filter((g) => g.list.length);
    const others = items.filter((it) => !CATEGORIES.some((c) => c.id === it.category));
    let sheet;
    const choose = (it) => { st.anchorId = it.id; persist(); renderExtras(); sheet.close(); };
    const tile = (it) => h('button', { class: 'wd-pick', type: 'button', onClick: () => choose(it) }, itemThumb(it), h('span', null, it.name || categoryLabel(it.category)));
    const body = h('div', { class: 'wd-picker' },
      groups.map((g) => [h('div', { class: 'wd-picker-head' }, g.c.label), h('div', { class: 'wd-picker-grid' }, g.list.map(tile))]),
      others.length ? [h('div', { class: 'wd-picker-head' }, 'Khác'), h('div', { class: 'wd-picker-grid' }, others.map(tile))] : null);
    sheet = openSheet(body, { title: 'Giữ món nào? Trợ lý sẽ phối quanh món này', className: 'wd-sheet' });
  }

  // ---------- results ----------
  const resultsEl = h('section', { class: 'wd-results', 'aria-live': 'polite' });
  const ctaBtn = h('button', { class: 'btn btn-primary btn-xl wd-cta', type: 'button' }, icon('sparkle', 22), 'Gợi ý cho tôi');

  function itemById(id) { return items.find((it) => String(it.id) === String(id)); }

  function renderLoading({ withPhoto = false, withRules = false } = {}) {
    // Real AI takes ~5–15 s (more with a photo): show the steps that actually happen, elapsed seconds
    // and the expected wait so it never looks frozen.
    const lines = [
      'Đang xem thời tiết…',
      withPhoto ? 'Đang xem bạn đang mặc gì…' : null,
      'Đang lục tủ đồ của bạn…',
      withRules ? 'Đang áp dụng quy tắc đã học…' : null,
      'Đang phối màu cho hợp…',
      'Đang viết lời khuyên…',
      'Sắp xong rồi…',
    ].filter(Boolean);
    const line = h('div', { class: 'loading-line' }, lines[0]);
    const elapsed = h('div', { class: 'loading-elapsed' }, '0 giây');
    const hint = h('p', { class: 'wd-muted wd-loading-hint' }, withPhoto ? 'Thường mất 10–20 giây (có ảnh nên lâu hơn chút).' : 'Thường mất 5–15 giây.');
    resultsEl.replaceChildren(h('div', { class: 'wd-loading' },
      h('div', { class: 'orb', 'aria-hidden': 'true' }, h('i'), h('i'), h('i')),
      h('div', { class: 'wd-loading-title' }, 'Trợ lý đang phối đồ'), line, elapsed, hint,
      h('button', { class: 'btn btn-ghost btn-sm', type: 'button', onClick: () => controller?.abort() }, 'Huỷ')));
    const t0 = Date.now();
    let i = 0;
    const swap = (text) => { line.textContent = text; line.classList.remove('swap'); void line.offsetWidth; line.classList.add('swap'); };
    const id = setInterval(() => {
      const s = Math.round((Date.now() - t0) / 1000);
      elapsed.textContent = `${s} giây`;
      if (s === 25) { swap('AI hơi chậm lúc này, vẫn đang phối…'); hint.textContent = 'Bạn có thể huỷ và thử lại sau.'; return; }
      if (s < 25 && s && s % 3 === 0 && i < lines.length - 1) swap(lines[++i]);
    }, 1000);
    return () => clearInterval(id);
  }

  function renderResult(res, { speakFirst = false } = {}) {
    const outfits = Array.isArray(res?.outfits) ? res.outfits : [];
    const topRules = (Array.isArray(res?.rules_used) ? res.rules_used : []).map((r) => resolveRule(r, rules)).filter(Boolean);
    const liveOk = !!config?.live?.available;
    const nodes = [];
    nodes.push(h('div', { class: 'section-head wd-section-head' }, h('h2', null, 'Gợi ý cho bạn'),
      h('span', { class: 'section-count' }, outfits.length ? `${outfits.length} bộ` : '')));
    if (res?.wearing) {
      nodes.push(h('div', { class: 'wd-wearing' }, h('span', { class: 'wd-wearing-icon', 'aria-hidden': 'true' }, '👀'),
        h('div', null, h('div', { class: 'wd-wearing-k' }, 'Trợ lý thấy bạn đang mặc'), h('div', null, res.wearing))));
    }
    if (!outfits.length) {
      nodes.push(h('div', { class: 'empty' }, h('p', { class: 'empty-title' }, 'Chưa phối được bộ nào'),
        h('p', null, res?.message || 'Tủ đồ có thể còn ít món. Thêm vài áo, quần rồi thử lại.'),
        h('a', { class: 'btn btn-ghost', href: '#/wardrobe' }, 'Mở tủ đồ')));
    }
    outfits.forEach((o, idx) => {
      const ids = (Array.isArray(o.item_ids) ? o.item_ids : Array.isArray(o.items) ? o.items.map((x) => (typeof x === 'object' ? x.id : x)) : []).map(String);
      const its = ids.map((id) => itemById(id) || { id, name: 'Món đồ', category: '' });
      const ownRules = (Array.isArray(o.rules_used) ? o.rules_used : []).map((r) => resolveRule(r, rules)).filter(Boolean);
      const rulesShown = ownRules.length ? ownRules : (idx === 0 ? topRules : []);
      const tips = Array.isArray(o.tips) ? o.tips : (o.tips ? [o.tips] : []);
      const speakBtn = ttsSupported() ? h('button', { class: 'icon-btn sm wd-speak', type: 'button', 'aria-label': 'Đọc lý do',
        onClick: () => { unlockTTS(); setMuted(false); speak([o.title, o.reason].filter(Boolean).join('. ')); } }, icon('speaker', 20)) : null;
      const liveBtn = h('button', { class: 'btn btn-ghost wd-try', type: 'button', disabled: !liveOk || !ids.length,
        onClick: () => navigate(outfitHash('tryon-live', ids)) }, '🎥 Mặc thử trực tiếp');
      const photoBtn = h('button', { class: 'btn btn-primary wd-try', type: 'button', disabled: !ids.length,
        onClick: () => navigate(outfitHash('tryon-photo', ids)) }, '📸 Ảnh mặc thử');
      nodes.push(h('article', { class: 'wd-outfit', style: { animationDelay: `${idx * 80}ms` } },
        h('div', { class: `wd-collage n${Math.min(its.length, 4)}` }, its.slice(0, 4).map((it) => itemThumb(it, { cls: 'wd-collage-cell' })),
          its.length > 4 ? h('span', { class: 'wd-collage-more' }, `+${its.length - 4}`) : null,
          h('span', { class: 'wd-outfit-n' }, `Bộ ${idx + 1}`)),
        h('div', { class: 'wd-outfit-body' },
          h('div', { class: 'wd-outfit-head' }, h('h3', { class: 'wd-outfit-title' }, o.title || `Bộ ${idx + 1}`), speakBtn),
          h('ul', { class: 'wd-outfit-items' }, its.map((it) => h('li', null, colorDots(it.colors, { max: 2 }), it.name || categoryLabel(it.category)))),
          o.reason ? h('p', { class: 'wd-outfit-reason' }, o.reason) : null,
          tips.length ? h('div', { class: 'wd-tips' }, tips.map((t) => h('div', { class: 'wd-tip' }, icon('bulb', 16), h('span', null, t)))) : null,
          rulesShown.length ? h('div', { class: 'wd-rules-used' }, rulesShown.map(ruleBadge)) : null,
          h('div', { class: 'wd-outfit-actions' }, liveBtn, photoBtn),
          !liveOk ? h('div', { class: 'wd-hint' }, config ? 'Mặc thử trực tiếp chưa được bật trên máy chủ.' : 'Đang kiểm tra mặc thử trực tiếp…') : null)));
    });
    nodes.push(h('button', { class: 'btn btn-ghost block wd-again', type: 'button', onClick: () => { el.querySelector('.wd-form')?.scrollIntoView({ behavior: 'smooth' }); } }, icon('refresh', 18), 'Đổi sự kiện / phối lại'));
    resultsEl.replaceChildren(...nodes);
    if (speakFirst && outfits[0]) {
      const first = outfits[0];
      speak([first.title, first.reason].filter(Boolean).join('. '));
    }
  }

  ctaBtn.addEventListener('click', async () => {
    if (busy) return;
    unlockTTS(); // synchronously inside the tap (iOS)
    const text = (textIn.value || '').trim();
    const event = st.event || text;
    if (!event) { toast('Chọn hoặc nói sự kiện bạn sắp đi nhé.'); return; }
    const body = {
      event,
      date: dateValue(),
      clothing_preferences: st.clothingPreferences.slice(0, 200).trim(),
      gender: st.gender.slice(0, 60).trim(),
      age_range: st.ageRange.slice(0, 40).trim(),
      fit_preferences: st.fitPreferences.slice(0, 200).trim(),
    };
    if (st.event && text) body.notes = text;
    if (st.coords) { body.lat = st.coords.lat; body.lon = st.coords.lon; } else body.city = st.city;
    if (st.currentImage) body.current_image = st.currentImage;
    if (st.anchorId) body.anchor_item_id = st.anchorId;
    busy = true;
    ctaBtn.disabled = true;
    controller = new AbortController();
    const stopLoading = renderLoading({ withPhoto: !!body.current_image, withRules: rules.length > 0 });
    resultsEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
    try {
      const res = await wardrobeApi.suggest(body, { signal: controller.signal });
      if (scope.disposed) return;
      if (res?.weather && typeof res.weather === 'object' && res.weather.available !== false && !weather) { weather = res.weather; renderWeather('ok'); }
      st.result = res;
      persist();
      renderResult(res, { speakFirst: getSettings().tts });
      requestAnimationFrame(() => resultsEl.scrollIntoView({ behavior: 'smooth', block: 'start' }));
    } catch (err) {
      if (scope.disposed) return;
      if (controller.signal.aborted) { resultsEl.replaceChildren(); toast('Đã huỷ'); } else {
        errorToast(err, 'Trợ lý chưa phối được');
        resultsEl.replaceChildren(h('div', { class: 'empty' }, h('p', null, err.userMessage || 'Trợ lý chưa phối được.'),
          h('button', { class: 'btn btn-ghost', type: 'button', onClick: () => ctaBtn.click() }, 'Thử lại')));
      }
    } finally {
      stopLoading();
      busy = false;
      ctaBtn.disabled = false;
      controller = null;
    }
  });
  scope.add(() => controller?.abort());

  // ---------- layout ----------
  el.append(
    h('header', { class: 'app-bar' },
      h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Quay lại', onClick: () => navigate('#/wardrobe') }, icon('back', 24)),
      h('div', { class: 'app-bar-title' }, 'Phối đồ'),
      h('a', { class: 'icon-btn', href: '#/rules', 'aria-label': 'Dạy trợ lý' }, h('span', { 'aria-hidden': 'true', style: { fontSize: '22px' } }, '🎓'))),
    h('div', { class: 'wd-scroll' },
      h('div', { class: 'wd-form' },
        h('section', { class: 'wd-block' },
          h('h2', { class: 'wd-q' }, 'Bạn sắp đi đâu?'),
          chipsEl,
          h('div', { class: 'wd-input-row' }, textIn, mic.el)),
        h('section', { class: 'wd-block' },
          h('h2', { class: 'wd-q' }, 'Khi nào?'),
          dateSeg, dateIn, weatherCard),
        profileFields,
        h('section', { class: 'wd-block' },
          h('h2', { class: 'wd-q' }, 'Tuỳ chọn ', h('span', { class: 'wd-muted' }, '(không bắt buộc)')),
          extrasEl)),
      h('div', { class: 'wd-cta-bar' }, ctaBtn),
      resultsEl),
  );

  // ---------- data ----------
  renderExtras();
  initWeather();
  Promise.allSettled([wardrobeApi.listItems(), wardrobeApi.listRules(), wardrobeApi.tryonConfig()]).then(([a, b, c]) => {
    if (scope.disposed) return;
    items = a.status === 'fulfilled' ? a.value : [];
    rules = b.status === 'fulfilled' ? b.value : [];
    config = c.status === 'fulfilled' ? c.value : { live: { available: false } };
    if (a.status === 'rejected') errorToast(a.reason, 'Không tải được tủ đồ');
    renderExtras();
    if (st.result) renderResult(st.result);
  });
  if (st.result) renderResult(st.result);

  return () => scope.dispose();
}
