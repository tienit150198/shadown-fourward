// Dạy trợ lý — styling rules taught by a person (voice or text), "Học từ <tên>".
import { wardrobeApi } from '../wardrobe-api.js';
import { h, icon, toast, errorToast, navigate, createScope, confirmSheet } from '../ui.js';
import { getSettings, updateSettings } from '../store.js';
import { micButton } from '../wardrobe-ui.js';

const EXAMPLES = [
  'Đi đám cưới thì không mặc đồ trắng, tránh lấn át cô dâu.',
  'Trời dưới 20 độ thì luôn thêm một áo khoác mỏng.',
  'Áo sáng màu nên phối với quần tối màu cho cân đối.',
  'Đi phỏng vấn ưu tiên sơ mi trơn và giày da.',
];

function fmtDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return `${d.getDate()}/${d.getMonth() + 1}/${d.getFullYear()}`;
}

export function mount(el) {
  const scope = createScope();
  let rules = null;

  const textIn = h('textarea', { class: 'input textarea wd-rule-text', rows: '3', maxlength: '300', placeholder: 'VD: Đi đám cưới thì không mặc đồ trắng.' });
  const nameIn = h('input', { class: 'input', type: 'text', value: getSettings().teacherName || '', placeholder: 'Tên bạn (VD: Chị Lan)', maxlength: '40', autocomplete: 'name' });
  const mic = micButton({
    label: 'Nói quy tắc',
    onInterim: (t) => { textIn.value = t; },
    onText: (t) => { textIn.value = t; },
  });
  scope.add(() => mic.cancel());
  const addBtn = h('button', { class: 'btn btn-primary block', type: 'submit' }, icon('plus', 20), 'Thêm quy tắc');
  const list = h('div', { class: 'wd-rule-list' });
  const countEl = h('span', { class: 'section-count' });

  const form = h('form', { class: 'card wd-rule-form' },
    h('label', { class: 'field' }, h('span', { class: 'field-label' }, 'Quy tắc phối đồ'),
      h('div', { class: 'wd-input-row top' }, textIn, mic.el)),
    h('div', { class: 'wd-examples' }, EXAMPLES.map((ex) => h('button', { class: 'wd-example', type: 'button', onClick: () => { textIn.value = ex; textIn.focus(); } }, ex))),
    h('label', { class: 'field' }, h('span', { class: 'field-label' }, icon('user', 14), 'Người dạy'), nameIn),
    addBtn);

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const text = textIn.value.trim();
    const taughtBy = nameIn.value.trim();
    if (!text) { toast('Nhập hoặc nói một quy tắc nhé.'); textIn.focus(); return; }
    if (!taughtBy) { toast('Cho trợ lý biết ai dạy nhé.'); nameIn.focus(); return; }
    updateSettings({ teacherName: taughtBy });
    addBtn.disabled = true;
    addBtn.classList.add('loading');
    try {
      const rule = await wardrobeApi.addRule({ text, taught_by: taughtBy });
      rules = [...(rules || []), rule && rule.id ? rule : { id: `local-${Date.now()}`, text, taught_by: taughtBy, created_at: new Date().toISOString() }];
      textIn.value = '';
      render();
      toast(`Trợ lý đã học từ ${taughtBy}`, { kind: 'ok' });
      if (!rule?.id) load();
    } catch (err) {
      errorToast(err, 'Không lưu được quy tắc');
    } finally {
      addBtn.disabled = false;
      addBtn.classList.remove('loading');
    }
  });

  el.append(
    h('header', { class: 'app-bar' },
      h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Quay lại', onClick: () => navigate('#/wardrobe') }, icon('back', 24)),
      h('div', { class: 'app-bar-title' }, 'Dạy trợ lý')),
    h('div', { class: 'wd-scroll' },
      h('p', { class: 'wd-lead' }, '🎓 Những quy tắc này trợ lý sẽ áp dụng khi phối đồ.'),
      form,
      h('div', { class: 'section-head wd-section-head' }, h('h2', null, 'Đã dạy'), countEl),
      list,
      h('a', { class: 'btn btn-ghost block wd-again', href: '#/stylist' }, icon('sparkle', 18), 'Thử phối đồ với quy tắc mới')),
  );

  function render() {
    if (!rules) {
      list.replaceChildren(h('div', { class: 'wd-rule skeleton' }, h('div', { class: 'sk-line w70' }), h('div', { class: 'sk-line w40' })));
      return;
    }
    countEl.textContent = rules.length ? String(rules.length) : '';
    if (!rules.length) {
      list.replaceChildren(h('div', { class: 'empty' },
        h('div', { class: 'empty-icon' }, h('span', { style: { fontSize: '28px' } }, '🎓')),
        h('p', { class: 'empty-title' }, 'Chưa có quy tắc nào'),
        h('p', null, 'Một stylist, chị gái hay mẹ bạn có thể dạy trợ lý cách phối đồ — chỉ cần nói một câu.')));
      return;
    }
    list.replaceChildren(...rules.slice().reverse().map((r) => h('article', { class: 'wd-rule' },
      h('div', { class: 'wd-rule-body' },
        h('p', { class: 'wd-rule-t' }, `“${r.text}”`),
        h('div', { class: 'wd-rule-meta' },
          h('span', { class: 'wd-rule-by' }, icon('user', 14), `Học từ ${r.taught_by || 'ẩn danh'}`),
          fmtDate(r.created_at) ? h('span', null, fmtDate(r.created_at)) : null)),
      h('button', { class: 'icon-btn sm danger', type: 'button', 'aria-label': 'Xoá quy tắc', onClick: () => remove(r) }, icon('trash', 20)))));
  }

  async function remove(r) {
    const ok = await confirmSheet(`Xoá quy tắc “${r.text}”?`, { okLabel: 'Xoá', danger: true });
    if (!ok) return;
    try {
      await wardrobeApi.deleteRule(r.id);
      rules = (rules || []).filter((x) => x.id !== r.id);
      render();
      toast('Đã xoá quy tắc', { kind: 'ok' });
    } catch (err) {
      errorToast(err, 'Không xoá được');
    }
  }

  async function load() {
    try {
      const data = await wardrobeApi.listRules();
      if (scope.disposed) return;
      rules = data;
      render();
    } catch (err) {
      if (scope.disposed) return;
      errorToast(err, 'Không tải được quy tắc');
      rules = [];
      render();
    }
  }

  render();
  load();
  return () => scope.dispose();
}
