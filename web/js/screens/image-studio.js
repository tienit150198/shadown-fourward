import { api } from '../api.js';
import { h, icon } from '../ui.js';

export function mount(el, { query }) {
  let active = null;
  const prompt = h('textarea', {
    class: 'chat-input', rows: 4, maxlength: 1200, required: true,
    'aria-label': 'Mô tả ảnh', placeholder: 'Ví dụ: Áo khoác denim xanh nhạt trên nền kem…',
  });
  const previous = query.get('prompt') || '';
  prompt.value = previous.replace(/^(?:tạo|vẽ|sinh)\s+(?:một\s+)?(?:bức\s+)?(?:ảnh|hình)\s*/i, '').trim();
  const submit = h('button', { type: 'submit', class: 'btn btn-primary' }, icon('sparkle', 20), 'Tạo ảnh');
  const status = h('p', { class: 'chat-status', role: 'status' });
  const result = h('div', { class: 'studio-result' });
  const form = h('form', {
    class: 'studio-form',
    onSubmit: async (event) => {
      event.preventDefault();
      const description = prompt.value.trim();
      if (!description || active) return;
      const controller = new AbortController();
      active = controller;
      submit.disabled = true;
      status.textContent = 'Đang tạo ảnh. Tác vụ có thể mất khoảng một phút.';
      try {
        const response = await api.generateImage({ prompt: description }, { signal: controller.signal });
        if (controller.signal.aborted) return;
        result.replaceChildren(
          h('img', { src: response.image, alt: description }),
          h('a', { class: 'btn btn-ghost', href: response.image, download: 'shadow-image.jpg' }, 'Tải ảnh'),
        );
        status.textContent = '';
      } catch (error) {
        if (!controller.signal.aborted) status.textContent = error.userMessage || 'Chưa tạo được ảnh. Thử lại nhé.';
      } finally {
        active = null;
        submit.disabled = false;
      }
    },
  }, prompt, submit);
  el.append(h('header', { class: 'chat-nav' },
    h('a', { class: 'chat-brand', href: '#/conversation' }, icon('back', 21), 'Shadow')),
    h('div', { class: 'studio' }, h('h1', null, 'Tạo ảnh'), form, status, result));
  return () => active?.abort();
}
