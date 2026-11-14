// Review: edit the AI-generated (or existing) skill before saving it.
import { api, mediaUrl } from '../api.js';
import { h, icon, toast, errorToast, navigate, createScope, confirmSheet, deepClone, openSheet } from '../ui.js';
import { getDraft, clearDraft, setDraft } from '../store.js';

export function stepImageUrl(step, skillId) {
  if (step.reference_image_url) return mediaUrl(step.reference_image_url, skillId);
  const ref = step.reference_image;
  if (!ref) return null;
  if (/^(https?:|data:|blob:|\/)/.test(ref)) return ref;
  return mediaUrl(ref.includes('/') ? ref : `frames/${ref}`, skillId);
}

/**
 * Produces the object sent to PUT /api/skills/{id}: re-chains steps, keeps tasks/detectors in sync,
 * preserves unknown fields and draft_id.
 */
export function finalizeSkill(work, { draftId = null, editedInstructions = new Set() } = {}) {
  const skill = deepClone(work);
  const steps = Array.isArray(skill.steps) ? skill.steps : [];
  const ids = new Set(steps.map((s) => s.id));

  steps.forEach((s, i) => {
    s.next_step_id = steps[i + 1]?.id ?? null;
    if (Array.isArray(s.milestones)) {
      s.milestones.forEach((m) => { if (m && m.next_step_id && !ids.has(m.next_step_id)) m.next_step_id = s.next_step_id; });
    }
  });
  skill.start_step_id = steps[0]?.id ?? null;

  // tasks: ordered by first use, orphans removed, missing ones created
  const taskById = new Map((skill.tasks || []).map((t) => [t.id, t]));
  const usage = new Map();
  for (const s of steps) {
    if (!s.task_id) s.task_id = s.id;
    usage.set(s.task_id, (usage.get(s.task_id) || 0) + 1);
  }
  const orderedTaskIds = [...new Set(steps.map((s) => s.task_id))];
  skill.tasks = orderedTaskIds.map((id) => {
    const step = steps.find((s) => s.task_id === id);
    const task = taskById.get(id) || { id, text: step.instruction || step.on_enter_speech || id };
    // a task owned by one step follows that step's edited instruction
    if (usage.get(id) === 1 && editedInstructions.has(step.id) && step.instruction) task.text = step.instruction;
    return task;
  });

  // detectors: drop the ones no step uses anymore
  if (skill.detectors && typeof skill.detectors === 'object') {
    const used = new Set(steps.map((s) => s.detector_key));
    for (const key of Object.keys(skill.detectors)) if (!used.has(key)) delete skill.detectors[key];
  }

  if (draftId) skill.draft_id = draftId;
  return skill;
}

function autoGrow(textarea) {
  const fit = () => { textarea.style.height = 'auto'; textarea.style.height = `${textarea.scrollHeight + 2}px`; };
  textarea.addEventListener('input', fit);
  requestAnimationFrame(fit);
  return textarea;
}

export function mount(el) {
  const scope = createScope();
  const draft = getDraft();
  if (!draft?.skill) {
    toast('Không có bản nháp để xem lại', { kind: 'error' });
    navigate('#/home', { replace: true });
    return () => scope.dispose();
  }
  const work = deepClone(draft.skill);
  if (!Array.isArray(work.steps)) work.steps = [];
  if (!Array.isArray(work.tasks)) work.tasks = [];
  const editedInstructions = new Set(draft.editedInstructions || []);
  // compiled_by "mock": the steps were split by rules from the speech / notes, not by the AI
  const compiledBy = draft.compiledBy || draft.skill.compiled_by || null;
  const isFallback = !!draft.isNew && compiledBy === 'mock';
  const fallbackText = {
    ai_failed: 'AI không phản hồi nên SKI tự chia bước theo lời nói / ghi chú (chưa có cách kiểm tra thông minh). Hãy xem kỹ từng bước, hoặc dạy lại khi có mạng.',
    mock_mode: 'Chế độ Demo: các bước được tách tự động từ lời nói / ghi chú, chưa dùng AI.',
  }[draft.fallbackReason] || 'Các bước được tách tự động từ lời nói / ghi chú, chưa dùng AI. Hãy xem kỹ từng bước.';
  let dirty = !!draft.isNew;
  let saving = false;

  const persist = () => {
    dirty = true;
    setDraft({ ...draft, skill: work, editedInstructions: [...editedInstructions] });
  };

  const field = (obj, key, { label, multiline = false, placeholder = '', rows = 2, icon: ic, cls = '', onChange } = {}) => {
    const input = multiline
      ? autoGrow(h('textarea', { class: 'input textarea', rows: String(rows), placeholder }))
      : h('input', { class: 'input', type: 'text', placeholder });
    input.value = obj[key] ?? '';
    input.addEventListener('input', () => {
      const v = input.value;
      obj[key] = v.trim() ? v : null;
      onChange?.(v);
      persist();
    });
    return h('label', { class: `field ${cls}` },
      h('span', { class: 'field-label' }, ic ? icon(ic, 15) : null, label), input);
  };

  // ---------- header ----------
  const saveBtn = h('button', { class: 'btn btn-primary btn-sm', type: 'button', onClick: save }, icon('check', 18), 'Lưu');
  const stepCount = h('span', { class: 'section-count' });
  const list = h('div', { class: 'step-list' });

  el.append(
    h('header', { class: 'app-bar' },
      h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Quay lại', onClick: back }, icon('back', 24)),
      h('div', { class: 'app-bar-title' }, draft.isNew ? (isFallback ? 'Đã tạo các bước' : 'AI đã học xong') : 'Chỉnh sửa kỹ năng'),
      saveBtn),
    h('div', { class: 'review-scroll' },
      draft.isNew ? h('div', { class: 'review-banner' }, icon('sparkle', 20),
        h('span', null, isFallback
          ? 'Kiểm tra lại các bước vừa tạo. Sửa câu chữ, đổi thứ tự hoặc xoá bước thừa rồi bấm Lưu.'
          : 'Kiểm tra lại các bước AI vừa tạo. Sửa câu chữ, đổi thứ tự hoặc xoá bước thừa rồi bấm Lưu.')) : null,
      isFallback ? h('div', { class: 'review-notice', role: 'note' }, icon('alert', 18), h('span', null, fallbackText)) : null,
      h('section', { class: 'card meta-card' },
        field(work, 'display_name', { label: 'Tên kỹ năng', placeholder: 'Tên kỹ năng', cls: 'field-title' }),
        field(work, 'learned_from', { label: 'Người dạy', placeholder: 'Tên người dạy', icon: 'user' }),
        field(work, 'summary', { label: 'Mô tả ngắn', placeholder: 'Kỹ năng này giúp làm gì?', multiline: true, rows: 2 })),
      h('div', { class: 'section-head' }, h('h2', null, 'Các bước'), stepCount),
      list,
      h('div', { class: 'review-bottom' },
        h('button', { class: 'btn btn-primary btn-lg block', type: 'button', onClick: save }, icon('check', 20), 'Lưu kỹ năng'))),
  );

  function renderSteps(highlightId) {
    stepCount.textContent = `${work.steps.length}`;
    if (!work.steps.length) {
      list.replaceChildren(h('div', { class: 'empty' }, h('p', null, 'Không còn bước nào. Hãy quay lại và dạy lại kỹ năng.')));
      return;
    }
    list.replaceChildren(...work.steps.map((step, i) => stepCard(step, i, step.id === highlightId)));
  }

  function stepCard(step, i, highlight) {
    const img = stepImageUrl(step, work.id);
    const n = work.steps.length;
    const thumb = img
      ? h('button', { class: 'step-thumb', type: 'button', 'aria-label': 'Xem ảnh mẫu', onClick: () => viewImage(img, i) },
        h('img', { src: img, alt: `Ảnh mẫu bước ${i + 1}`, loading: 'lazy', onError: (e) => { e.target.closest('.step-thumb')?.classList.add('broken'); } }))
      : h('div', { class: 'step-thumb empty' }, icon('image', 22));

    const speechFields = [
      field(step, 'on_enter_speech', { label: 'Khi bắt đầu bước, AI nói', multiline: true, rows: 1, icon: 'speaker' }),
      field(step, 'mismatch_speech', { label: 'Khi làm sai, AI nói', multiline: true, rows: 1, icon: 'speaker' }),
      'success_speech' in step ? field(step, 'success_speech', { label: 'Khi làm đúng, AI nói', multiline: true, rows: 1, icon: 'speaker' }) : null,
    ];

    const card = h('article', { class: `card step-card${highlight ? ' flash' : ''}`, dataset: { id: step.id } },
      h('div', { class: 'step-head' },
        h('span', { class: 'step-num' }, String(i + 1)),
        thumb,
        h('div', { class: 'step-tools' },
          h('button', { class: 'icon-btn sm', type: 'button', 'aria-label': 'Lên', disabled: i === 0, onClick: () => move(i, -1) }, icon('up', 20)),
          h('button', { class: 'icon-btn sm', type: 'button', 'aria-label': 'Xuống', disabled: i === n - 1, onClick: () => move(i, 1) }, icon('down', 20)),
          h('button', { class: 'icon-btn sm danger', type: 'button', 'aria-label': 'Xoá bước', onClick: () => remove(i) }, icon('trash', 20)))),
      field(step, 'instruction', {
        label: 'Hướng dẫn', multiline: true, rows: 2, placeholder: 'Người học cần làm gì ở bước này?', cls: 'field-instruction',
        onChange: () => editedInstructions.add(step.id),
      }),
      h('div', { class: 'grid-2' },
        field(step, 'tip', { label: 'Mẹo', icon: 'bulb', placeholder: 'Mẹo của người dạy', multiline: true, rows: 1, cls: 'field-tip' }),
        field(step, 'warning', { label: 'Cảnh báo', icon: 'alert', placeholder: 'Điều cần tránh', multiline: true, rows: 1, cls: 'field-warn' })),
      h('details', { class: 'step-more' }, h('summary', null, 'Câu AI sẽ nói'), speechFields),
    );
    if (highlight) setTimeout(() => card.classList.remove('flash'), 900);
    return card;
  }

  function move(i, delta) {
    const j = i + delta;
    if (j < 0 || j >= work.steps.length) return;
    const [step] = work.steps.splice(i, 1);
    work.steps.splice(j, 0, step);
    persist();
    renderSteps(step.id);
    list.querySelector(`[data-id="${CSS.escape(step.id)}"]`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }

  async function remove(i) {
    const step = work.steps[i];
    const ok = await confirmSheet(`Xoá bước ${i + 1}?${step.instruction ? ` “${step.instruction}”` : ''}`, { okLabel: 'Xoá bước', danger: true });
    if (!ok || scope.disposed) return;
    work.steps.splice(i, 1);
    editedInstructions.delete(step.id);
    // drop the step's task / detector when nothing else uses them
    if (!work.steps.some((s) => s.task_id === step.task_id)) work.tasks = work.tasks.filter((t) => t.id !== step.task_id);
    if (work.detectors && !work.steps.some((s) => s.detector_key === step.detector_key)) delete work.detectors[step.detector_key];
    persist();
    renderSteps();
  }

  function viewImage(src, i) {
    openSheet(h('div', { class: 'image-view' }, h('img', { src, alt: `Ảnh mẫu bước ${i + 1}` })), { title: `Ảnh mẫu · Bước ${i + 1}`, className: 'sheet-image' });
  }

  async function save() {
    if (saving) return;
    if (!work.display_name || !String(work.display_name).trim()) { toast('Hãy đặt tên cho kỹ năng', { kind: 'error' }); return; }
    if (!work.steps.length) { toast('Kỹ năng cần ít nhất một bước', { kind: 'error' }); return; }
    const blank = work.steps.findIndex((s) => !String(s.instruction || '').trim() && !String(s.on_enter_speech || '').trim());
    if (blank >= 0) { toast(`Bước ${blank + 1} chưa có hướng dẫn`, { kind: 'error' }); return; }
    if (!work.id) {
      work.id = String(work.display_name).normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/đ/gi, 'd')
        .toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || `skill-${Date.now()}`;
    }
    const payload = finalizeSkill(work, { draftId: draft.draft_id, editedInstructions });
    saving = true;
    el.querySelectorAll('.btn-primary').forEach((b) => { b.disabled = true; });
    saveBtn.classList.add('loading');
    try {
      await api.saveSkill(payload.id, payload);
      if (scope.disposed) return;
      clearDraft();
      dirty = false;
      toast('Đã lưu kỹ năng', { kind: 'ok' });
      navigate('#/home');
    } catch (err) {
      errorToast(err, 'Không lưu được kỹ năng');
    } finally {
      saving = false;
      if (!scope.disposed) {
        el.querySelectorAll('.btn-primary').forEach((b) => { b.disabled = false; });
        saveBtn.classList.remove('loading');
      }
    }
  }

  async function back() {
    if (dirty) {
      const ok = await confirmSheet(draft.isNew ? 'Bỏ kỹ năng AI vừa học? Bản nháp sẽ mất.' : 'Bỏ các thay đổi chưa lưu?', { okLabel: 'Bỏ', danger: true });
      if (!ok) return;
    }
    clearDraft();
    navigate('#/home');
  }

  renderSteps();
  return () => scope.dispose();
}
