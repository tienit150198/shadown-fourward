// Trợ lý tủ đồ — hub (Phối đồ / Tủ đồ / Dạy trợ lý) + wardrobe grid, add item (AI labels), item editor.
import { wardrobeApi, itemImageUrl } from '../wardrobe-api.js';
import { h, icon, toast, errorToast, navigate, createScope, openSheet, confirmSheet, actionSheet } from '../ui.js';
import {
  CATEGORIES, OCCASIONS, STYLES, WARMTH_LABELS, COLOR_PRESETS, categoryLabel, colorDots, colorChip, itemThumb,
  fileToJpeg, pickImageFile, fill, itemKindLabel,
} from '../wardrobe-ui.js';

const TAB_KEY = 'shadow.wd.tab';
// Only official shop pages get a "Xem trên …" link (product_url comes from the demo seed data).
const PRODUCT_HOSTS = new Set(['www.uniqlo.com', 'yame.vn', 'www.yame.vn']);

function safeProductUrl(value) {
  if (typeof value !== 'string' || !value.startsWith('https://')) return null;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && PRODUCT_HOSTS.has(url.hostname) && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

function productLine(it) {
  const brand = typeof it.brand === 'string' ? it.brand.trim().slice(0, 20) : '';
  const href = safeProductUrl(it.product_url);
  const price = Number.isFinite(it.price_vnd) && it.price_vnd > 0 ? `${Math.round(it.price_vnd).toLocaleString('vi-VN')} ₫` : '';
  if (!brand && !href) return null;
  return h('div', { class: 'wd-product' },
    brand ? h('span', { class: 'wd-card-brand static' }, brand) : null,
    price ? h('span', { class: 'wd-product-price' }, price) : null,
    href ? h('a', { class: 'wd-product-link', href, target: '_blank', rel: 'noopener noreferrer' }, `Xem trên ${brand || 'shop'}`, icon('right', 16)) : null);
}

export function mount(el) {
  const scope = createScope();
  let items = null; // null = loading
  let rulesCount = null;
  let tab = (() => { try { return sessionStorage.getItem(TAB_KEY) || 'all'; } catch { return 'all'; } })();

  const itemsCount = h('span', { class: 'wd-hub-count' }, '…');
  const rulesCountEl = h('span', { class: 'wd-hub-count' }, '…');
  const tabs = h('div', { class: 'wd-tabs', role: 'tablist', 'aria-label': 'Loại đồ' });
  const grid = h('div', { class: 'wd-grid' });
  const gridSection = h('section', { class: 'wd-closet', id: 'wd-closet' },
    h('div', { class: 'section-head wd-section-head' }, h('h2', null, 'Tủ đồ của bạn'),
      h('button', { class: 'btn btn-ghost btn-sm wd-add-inline', type: 'button', onClick: () => addItemFlow() }, icon('plus', 18), 'Thêm đồ')),
    tabs, grid);

  el.append(
    h('header', { class: 'app-bar' },
      h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Quay lại', onClick: () => navigate('#/home') }, icon('back', 24)),
      h('div', { class: 'app-bar-title' }, 'Trợ lý tủ đồ'),
      h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Thêm đồ', onClick: () => addItemFlow() }, icon('camera', 24))),
    h('div', { class: 'wd-scroll' },
      h('section', { class: 'wd-hub' },
        h('div', { class: 'wd-hub-kicker' }, h('span', { 'aria-hidden': 'true' }, '👗'), 'Kỹ năng làm sẵn của SHADOW'),
        h('h1', { class: 'wd-hub-title' }, 'Hôm nay mặc gì?', h('br'), h('span', { class: 'accent' }, 'Để trợ lý phối cho bạn.')),
        h('button', { class: 'wd-hub-main', type: 'button', onClick: () => navigate('#/stylist') },
          h('span', { class: 'wd-hub-main-icon', 'aria-hidden': 'true' }, icon('sparkle', 28)),
          h('span', { class: 'cta-text' }, h('span', { class: 'cta-title' }, 'Phối đồ hôm nay'), h('span', { class: 'cta-sub' }, 'Theo thời tiết, sự kiện & tủ đồ của bạn')),
          icon('right', 22)),
        h('div', { class: 'wd-hub-row' },
          h('button', { class: 'wd-hub-tile', type: 'button', onClick: () => gridSection.scrollIntoView({ behavior: 'smooth', block: 'start' }) },
            h('span', { class: 'wd-hub-emoji', 'aria-hidden': 'true' }, '🧺'), h('span', { class: 'wd-hub-label' }, 'Tủ đồ'), itemsCount),
          h('button', { class: 'wd-hub-tile', type: 'button', onClick: () => navigate('#/rules') },
            h('span', { class: 'wd-hub-emoji', 'aria-hidden': 'true' }, '🎓'), h('span', { class: 'wd-hub-label' }, 'Dạy trợ lý'), rulesCountEl))),
      gridSection),
  );

  function setTab(id) {
    tab = id;
    try { sessionStorage.setItem(TAB_KEY, id); } catch { /* ignore */ }
    render();
  }

  function render() {
    const list = items || [];
    itemsCount.textContent = items ? `${list.length} món` : '…';
    rulesCountEl.textContent = rulesCount === null ? '…' : `${rulesCount} quy tắc`;
    const counts = new Map();
    list.forEach((it) => counts.set(it.category, (counts.get(it.category) || 0) + 1));
    tabs.replaceChildren(
      ...[{ id: 'all', label: 'Tất cả' }, ...CATEGORIES].map((c) => {
        const n = c.id === 'all' ? list.length : (counts.get(c.id) || 0);
        return h('button', {
          class: `wd-tab${tab === c.id ? ' on' : ''}`, type: 'button', role: 'tab', 'aria-selected': String(tab === c.id),
          onClick: () => setTab(c.id),
        }, c.label, items && n ? h('span', { class: 'wd-tab-n' }, String(n)) : null);
      }));

    if (!items) {
      grid.replaceChildren(...[0, 1, 2, 3].map(() => h('div', { class: 'wd-card skeleton' }, h('div', { class: 'wd-card-img sk' }), h('div', { class: 'sk-line w70' }))));
      return;
    }
    if (!list.length) {
      grid.replaceChildren(h('div', { class: 'empty wd-empty' },
        h('div', { class: 'empty-icon' }, h('span', { style: { fontSize: '30px' } }, '🧺')),
        h('p', { class: 'empty-title' }, 'Tủ đồ còn trống'),
        h('p', null, 'Chụp từng món đồ — AI tự nhận diện loại, màu và dịp mặc. Hoặc nạp tủ đồ mẫu để thử ngay.'),
        h('div', { class: 'wd-empty-actions' },
          h('button', { class: 'btn btn-primary', type: 'button', onClick: () => addItemFlow() }, icon('camera', 20), 'Chụp đồ'),
          seedButton('btn btn-ghost'))));
      return;
    }
    const shown = tab === 'all' ? list : list.filter((it) => it.category === tab);
    if (!shown.length) {
      grid.replaceChildren(h('div', { class: 'wd-empty-tab' },
        h('p', null, `Chưa có ${categoryLabel(tab).toLowerCase()} nào.`),
        h('button', { class: 'btn btn-ghost btn-sm', type: 'button', onClick: () => addItemFlow({ category: tab }) }, icon('plus', 18), `Thêm ${categoryLabel(tab).toLowerCase()}`)));
      return;
    }
    grid.replaceChildren(...shown.map(card), h('div', { class: 'wd-grid-foot' }, seedButton('btn btn-ghost btn-sm', 'Nạp thêm tủ đồ mẫu')));
  }

  function card(it) {
    const brand = typeof it.brand === 'string' ? it.brand.trim().slice(0, 20) : '';
    return h('button', { class: 'wd-card', type: 'button', 'aria-label': `Sửa ${it.name || 'món đồ'}`, onClick: () => openEditor(it) },
      itemThumb(it, { cls: 'wd-card-img' }),
      brand ? h('span', { class: 'wd-card-brand' }, brand) : null,
      h('span', { class: 'wd-card-body' },
        h('span', { class: 'wd-card-name' }, it.name || categoryLabel(it.category)),
        it.needs_confirmation ? h('span', { class: 'wd-ai-tag' }, icon('alert', 14), 'Cần xác nhận') : null,
        h('span', { class: 'wd-card-meta' }, colorDots(it.colors), h('span', { class: 'wd-card-cat' }, itemKindLabel(it)))));
  }

  function seedButton(cls, label = 'Nạp tủ đồ mẫu') {
    const btn = h('button', { class: cls, type: 'button' }, icon('sparkle', 18), label);
    btn.addEventListener('click', () => {
      if (btn.disabled) return;
      actionSheet('Chọn tủ đồ mẫu', [
        { label: 'Đồ nam mùa hè (shop YaMe)', icon: 'sparkle', onSelect: () => runSeed(btn, 'shop') },
        { label: 'Bộ mẫu cơ bản', icon: 'image', onSelect: () => runSeed(btn, 'default') },
      ]);
    });
    return btn;
  }

  async function runSeed(btn, set) {
    btn.disabled = true;
    btn.classList.add('loading');
    try {
      const res = await wardrobeApi.seedDemo(set);
      const added = res?.added ?? res?.count ?? (Array.isArray(res?.items) ? res.items.length : Array.isArray(res) ? res.length : null);
      toast(added ? `Đã nạp ${added} món đồ mẫu` : 'Tủ đồ mẫu đã có sẵn', { kind: 'ok' });
      if (res?.warning) toast(res.warning, { kind: 'error', ms: 6000 });
      await loadItems();
    } catch (err) {
      errorToast(err, 'Không nạp được tủ đồ mẫu');
    } finally {
      btn.disabled = false;
      btn.classList.remove('loading');
    }
  }

  async function loadItems() {
    try {
      const list = await wardrobeApi.listItems();
      if (scope.disposed) return;
      items = list.slice().sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')));
      render();
    } catch (err) {
      if (scope.disposed) return;
      errorToast(err, 'Không tải được tủ đồ');
      grid.replaceChildren(h('div', { class: 'empty' },
        h('div', { class: 'empty-icon' }, icon('refresh', 30)),
        h('p', null, 'Không tải được tủ đồ.'),
        h('button', { class: 'btn btn-ghost', type: 'button', onClick: () => { items = null; render(); loadItems(); } }, 'Thử lại')));
    }
  }

  async function loadRules() {
    try {
      const rules = await wardrobeApi.listRules();
      if (scope.disposed) return;
      rulesCount = rules.length;
    } catch { rulesCount = 0; }
    if (!scope.disposed) render();
  }

  // ---------- add item ----------
  function addItemFlow(preset = {}) {
    actionSheet('Thêm món đồ', [
      { label: 'Chụp ảnh món đồ', icon: 'camera', onSelect: () => pickAndUpload({ capture: 'environment', ...preset }) },
      { label: 'Chọn ảnh có sẵn', icon: 'image', onSelect: () => pickAndUpload(preset) },
    ]);
  }

  async function pickAndUpload({ capture, category } = {}) {
    const file = await pickImageFile({ capture });
    if (!file || scope.disposed) return;
    let image;
    try { image = await fileToJpeg(file, { maxSide: 1024, quality: 0.85 }); } catch (err) { errorToast(err, 'Không đọc được ảnh'); return; }
    const body = h('div', { class: 'wd-detect' },
      h('div', { class: 'wd-detect-img' }, h('img', { src: image, alt: '' }), h('div', { class: 'wd-scan', 'aria-hidden': 'true' })),
      h('div', { class: 'wd-detect-text' }, icon('sparkle', 20), 'AI đang nhận diện…'),
      h('p', { class: 'wd-muted' }, 'Loại đồ, màu sắc, phong cách, độ ấm và dịp mặc.'));
    const controller = new AbortController();
    let cancelled = false;
    const sheet = openSheet(body, { title: 'Thêm món đồ', onClose: () => { cancelled = true; controller.abort(); } });
    try {
      const item = await wardrobeApi.addItem(category && category !== 'all' ? { image, category } : { image }, { signal: controller.signal });
      if (cancelled || scope.disposed) return;
      sheet.close();
      if (!item || !item.id) throw new Error('Máy chủ không trả về món đồ');
      items = [item, ...(items || []).filter((x) => x.id !== item.id)];
      render();
      const qualityWarnings = Array.isArray(item.image_quality?.warnings) ? item.image_quality.warnings : [];
      if (qualityWarnings.length) {
        toast(qualityWarnings.map((warning) => warning.message).filter(Boolean).join(' '), { kind: 'error', ms: 7000 });
      }
      setTimeout(() => openEditor(item, { isNew: true }), 300);
    } catch (err) {
      if (cancelled || scope.disposed) return;
      sheet.close();
      errorToast(err, 'AI chưa nhận diện được, thử lại nhé');
    }
  }

  // ---------- editor ----------
  function openEditor(original, { isNew = false } = {}) {
    const it = JSON.parse(JSON.stringify(original));
    it.colors = Array.isArray(it.colors) ? it.colors : [];
    it.occasions = Array.isArray(it.occasions) ? it.occasions : [];
    let warmth = Math.min(5, Math.max(1, Number(it.warmth) || 3));

    const nameIn = h('input', { class: 'input', type: 'text', value: it.name || '', placeholder: 'VD: Áo sơ mi trắng', maxlength: '80' });
    const catSel = h('select', { class: 'input wd-select' },
      CATEGORIES.map((c) => h('option', { value: c.id, selected: c.id === it.category }, `${c.emoji} ${c.label}`)));
    if (!CATEGORIES.some((c) => c.id === it.category)) catSel.value = 'top';
    const styleList = h('datalist', { id: 'wd-style-list' }, STYLES.map((s) => h('option', { value: s })));
    const styleIn = h('input', { class: 'input', type: 'text', value: it.style || '', placeholder: 'công sở, thường ngày…', list: 'wd-style-list' });

    const colorWrap = h('div', { class: 'wd-chip-wrap' });
    const colorIn = h('input', { class: 'input wd-color-input', type: 'text', placeholder: 'Thêm màu…', enterkeyhint: 'done' });
    const presetWrap = h('div', { class: 'wd-presets' });
    const renderColors = () => {
      fill(colorWrap, ...it.colors.map((c) => colorChip(c, { onRemove: () => { it.colors = it.colors.filter((x) => x !== c); renderColors(); } })),
        it.colors.length ? null : h('span', { class: 'wd-muted' }, 'Chưa có màu'));
      presetWrap.replaceChildren(...COLOR_PRESETS.filter((c) => !it.colors.includes(c)).map((c) => h('button', {
        class: 'wd-preset', type: 'button', 'aria-label': `Thêm màu ${c}`, title: c, onClick: () => { it.colors.push(c); renderColors(); },
      }, colorChip(c))));
    };
    const addColor = () => {
      const v = colorIn.value.trim().toLowerCase();
      if (v && !it.colors.includes(v)) it.colors.push(v);
      colorIn.value = '';
      renderColors();
    };
    colorIn.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); addColor(); } });
    colorIn.addEventListener('blur', addColor);
    renderColors();

    const warmthVal = h('div', { class: 'wd-step-val' });
    const minus = h('button', { class: 'wd-step-btn', type: 'button', 'aria-label': 'Giảm độ ấm', onClick: () => setWarmth(warmth - 1) }, '−');
    const plus = h('button', { class: 'wd-step-btn', type: 'button', 'aria-label': 'Tăng độ ấm', onClick: () => setWarmth(warmth + 1) }, '+');
    const setWarmth = (v) => {
      warmth = Math.min(5, Math.max(1, v));
      warmthVal.replaceChildren(h('b', null, String(warmth)), h('span', null, WARMTH_LABELS[warmth]),
        h('span', { class: 'wd-flames', 'aria-hidden': 'true' }, Array.from({ length: 5 }, (_, i) => h('i', { class: i < warmth ? 'on' : '' }))));
      minus.disabled = warmth <= 1;
      plus.disabled = warmth >= 5;
    };
    setWarmth(warmth);

    const occWrap = h('div', { class: 'wd-chip-wrap' });
    const allOcc = [...new Set([...OCCASIONS, ...it.occasions])];
    const renderOcc = () => occWrap.replaceChildren(...allOcc.map((o) => h('button', {
      class: `wd-toggle${it.occasions.includes(o) ? ' on' : ''}`, type: 'button', 'aria-pressed': String(it.occasions.includes(o)),
      onClick: () => { it.occasions = it.occasions.includes(o) ? it.occasions.filter((x) => x !== o) : [...it.occasions, o]; renderOcc(); },
    }, o)));
    renderOcc();

    const saveBtn = h('button', { class: 'btn btn-primary grow', type: 'button' }, icon('check', 20), 'Lưu');
    const delBtn = h('button', { class: 'icon-btn danger wd-del', type: 'button', 'aria-label': 'Xoá món đồ' }, icon('trash', 22));
    const src = itemImageUrl(it);

    const form = h('div', { class: 'wd-editor' },
      h('div', { class: 'wd-editor-top' },
        h('div', { class: 'wd-editor-img' }, src ? h('img', { src, alt: '' }) : h('span', { class: 'wd-thumb-emoji' }, '🏷️')),
        h('div', { class: 'wd-editor-head' },
          (isNew || it.needs_confirmation) ? h('div', { class: 'wd-ai-tag' }, icon('sparkle', 16), 'AI đã nhận diện — kiểm tra loại và màu rồi bấm Lưu') : null,
          h('label', { class: 'field' }, h('span', { class: 'field-label' }, 'Tên'), nameIn),
          productLine(it))),
      h('div', { class: 'wd-grid2' },
        h('label', { class: 'field' }, h('span', { class: 'field-label' }, 'Loại'), catSel),
        h('label', { class: 'field' }, h('span', { class: 'field-label' }, 'Phong cách'), styleIn, styleList)),
      h('div', { class: 'field' }, h('span', { class: 'field-label' }, 'Màu sắc'), colorWrap, presetWrap, colorIn),
      h('div', { class: 'field' }, h('span', { class: 'field-label' }, 'Độ ấm'),
        h('div', { class: 'wd-stepper' }, minus, warmthVal, plus)),
      h('div', { class: 'field' }, h('span', { class: 'field-label' }, 'Dịp mặc'), occWrap),
      h('div', { class: 'wd-editor-actions' }, delBtn, saveBtn));

    const sheet = openSheet(form, { title: isNew ? 'Món đồ mới' : 'Sửa món đồ', className: 'wd-sheet' });

    saveBtn.addEventListener('click', async () => {
      addColor();
      const payload = {
        ...it,
        name: nameIn.value.trim() || it.name || categoryLabel(catSel.value),
        category: catSel.value,
        style: styleIn.value.trim(),
        warmth,
        colors: it.colors,
        occasions: it.occasions,
      };
      saveBtn.disabled = true;
      saveBtn.classList.add('loading');
      try {
        const saved = await wardrobeApi.updateItem(it.id, payload);
        const merged = saved && saved.id ? saved : payload;
        items = (items || []).map((x) => (x.id === merged.id ? merged : x));
        render();
        sheet.close();
        toast('Đã lưu', { kind: 'ok' });
      } catch (err) {
        errorToast(err, 'Không lưu được');
      } finally {
        saveBtn.disabled = false;
        saveBtn.classList.remove('loading');
      }
    });

    delBtn.addEventListener('click', async () => {
      sheet.close();
      const ok = await confirmSheet(`Xoá “${it.name || 'món đồ này'}” khỏi tủ đồ?`, { okLabel: 'Xoá', danger: true });
      if (!ok) return;
      try {
        await wardrobeApi.deleteItem(it.id);
        items = (items || []).filter((x) => x.id !== it.id);
        render();
        toast('Đã xoá món đồ', { kind: 'ok' });
      } catch (err) {
        errorToast(err, 'Không xoá được');
      }
    });
  }

  render();
  loadItems();
  loadRules();
  return () => scope.dispose();
}
