// Small DOM + UI helpers shared by every screen (no framework).

export function h(tag, props, ...children) {
  const el = document.createElement(tag);
  if (props) {
    for (const [key, value] of Object.entries(props)) {
      if (value === undefined || value === null || value === false) continue;
      if (key === 'class') el.className = value;
      else if (key === 'style' && typeof value === 'object') Object.assign(el.style, value);
      else if (key === 'html') el.innerHTML = value;
      else if (key === 'dataset') Object.assign(el.dataset, value);
      else if (key.startsWith('on') && typeof value === 'function') el.addEventListener(key.slice(2).toLowerCase(), value);
      else if (key === 'value' && 'value' in el) el.value = value;
      else if (value === true) el.setAttribute(key, '');
      else el.setAttribute(key, String(value));
    }
  }
  appendChildren(el, children);
  return el;
}

function appendChildren(el, children) {
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    if (Array.isArray(child)) appendChildren(el, child);
    else if (child instanceof Node) el.appendChild(child);
    else el.appendChild(document.createTextNode(String(child)));
  }
}

const PATHS = {
  back: '<path d="M15 18l-6-6 6-6"/>',
  close: '<path d="M18 6L6 18M6 6l12 12"/>',
  mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
  sliders: '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/><circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  up: '<path d="M6 15l6-6 6 6"/>',
  down: '<path d="M6 9l6 6 6-6"/>',
  left: '<path d="M15 18l-6-6 6-6"/>',
  right: '<path d="M9 6l6 6-6 6"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  more: '<circle cx="5" cy="12" r="1.4" fill="currentColor"/><circle cx="12" cy="12" r="1.4" fill="currentColor"/><circle cx="19" cy="12" r="1.4" fill="currentColor"/>',
  play: '<path d="M8 5.5v13l10.5-6.5z" fill="currentColor"/>',
  sparkle: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M19 15.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z"/>',
  edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M14 6l4 4"/>',
  image: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 16l-5-5-9 9"/>',
  bulb: '<path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-4 10.5c.7.7 1 1.5 1 2.5h6c0-1 .3-1.8 1-2.5A6 6 0 0 0 12 3z"/>',
  alert: '<path d="M12 3.5l9.5 16.5h-19z"/><path d="M12 10v4.5M12 17.5v.01"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  list: '<path d="M9 6h11M9 12h11M9 18h11"/><path d="M4.5 6h.01M4.5 12h.01M4.5 18h.01"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  speaker: '<path d="M4 10v4h4l5 4V6L8 10z"/><path d="M16.5 8.5a5 5 0 0 1 0 7M19 6a8.5 8.5 0 0 1 0 12"/>',
  expand: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
  refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/>',
  eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  hand: '<path d="M8 13V5.5a1.5 1.5 0 0 1 3 0V11M11 10.5V4a1.5 1.5 0 0 1 3 0v6.5M14 10.5V5.5a1.5 1.5 0 0 1 3 0V13M17 9.5a1.5 1.5 0 0 1 3 0V14a7 7 0 0 1-7 7h-1a7 7 0 0 1-5.6-2.8L3.8 15a1.6 1.6 0 0 1 2.4-2.1L8 14.5"/>',
  wand: '<path d="M4 20L15 9M13 7l2-2 4 4-2 2z"/><path d="M8 3v3M6.5 4.5h3M19 14v3M17.5 15.5h3"/>',
  camera: '<path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/>',
  bug: '<rect x="7" y="7" width="10" height="13" rx="5"/><path d="M9 7a3 3 0 0 1 6 0M3 13h4M17 13h4M4 8l3 2M20 8l-3 2M4 19l3-2M20 19l-3-2"/>',
};

export function icon(name, size = 24) {
  const span = document.createElement('span');
  span.className = 'ic';
  span.setAttribute('aria-hidden', 'true');
  span.innerHTML = `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${PATHS[name] || ''}</svg>`;
  return span;
}

// ---------- toasts ----------
export function toast(message, { kind = 'info', ms = 3200 } = {}) {
  const host = document.getElementById('toasts');
  if (!host || !message) return;
  const el = h('div', { class: `toast toast-${kind}`, role: kind === 'error' ? 'alert' : 'status' }, String(message));
  host.appendChild(el);
  requestAnimationFrame(() => el.classList.add('in'));
  const remove = () => {
    el.classList.remove('in');
    setTimeout(() => el.remove(), 260);
  };
  el.addEventListener('click', remove);
  setTimeout(remove, ms);
  // keep at most 3 toasts
  while (host.children.length > 3) host.firstElementChild.remove();
}

export function errorToast(err, fallback = 'Có lỗi xảy ra') {
  const msg = (err && (err.userMessage || err.message)) || fallback;
  toast(msg, { kind: 'error', ms: 4200 });
}

// ---------- bottom sheets ----------
export function openSheet(content, { title, onClose, className = '' } = {}) {
  const backdrop = h('div', { class: 'sheet-backdrop' });
  const sheet = h('div', { class: `sheet ${className}`, role: 'dialog', 'aria-modal': 'true' },
    h('div', { class: 'sheet-grip', 'aria-hidden': 'true' }),
    title ? h('div', { class: 'sheet-title' }, title) : null,
    content,
  );
  const wrap = h('div', { class: 'sheet-wrap' }, backdrop, sheet);
  document.body.appendChild(wrap);
  requestAnimationFrame(() => wrap.classList.add('open'));
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    wrap.classList.remove('open');
    setTimeout(() => wrap.remove(), 280);
    onClose?.();
  };
  backdrop.addEventListener('click', close);
  return { close, el: sheet };
}

export function actionSheet(title, actions) {
  let api;
  const list = h('div', { class: 'action-list' },
    actions.map((a) => h('button', {
      class: `action-item${a.danger ? ' danger' : ''}`,
      type: 'button',
      onClick: () => { api.close(); a.onSelect?.(); },
    }, a.icon ? icon(a.icon, 22) : null, h('span', null, a.label))),
    h('button', { class: 'action-item cancel', type: 'button', onClick: () => api.close() }, 'Huỷ'),
  );
  api = openSheet(list, { title });
  return api;
}

export function confirmSheet(message, { okLabel = 'Đồng ý', danger = false } = {}) {
  return new Promise((resolve) => {
    let result = false;
    let api;
    const body = h('div', { class: 'confirm' },
      h('p', { class: 'confirm-msg' }, message),
      h('div', { class: 'row gap' },
        h('button', { class: 'btn btn-ghost grow', type: 'button', onClick: () => api.close() }, 'Huỷ'),
        h('button', {
          class: `btn grow ${danger ? 'btn-danger' : 'btn-primary'}`,
          type: 'button',
          onClick: () => { result = true; api.close(); },
        }, okLabel),
      ),
    );
    api = openSheet(body, { onClose: () => resolve(result) });
  });
}

// ---------- gestures ----------
export function onLongPress(el, callback, ms = 650) {
  let timer = null;
  let startX = 0;
  let startY = 0;
  const cancel = () => { clearTimeout(timer); timer = null; };
  const down = (e) => {
    startX = e.clientX; startY = e.clientY;
    cancel();
    timer = setTimeout(() => { timer = null; navigator.vibrate?.(30); callback(e); }, ms);
  };
  const move = (e) => {
    if (timer && Math.hypot(e.clientX - startX, e.clientY - startY) > 12) cancel();
  };
  el.addEventListener('pointerdown', down);
  el.addEventListener('pointermove', move);
  el.addEventListener('pointerup', cancel);
  el.addEventListener('pointercancel', cancel);
  el.addEventListener('pointerleave', cancel);
  el.addEventListener('contextmenu', (e) => e.preventDefault());
  return () => {
    cancel();
    el.removeEventListener('pointerdown', down);
    el.removeEventListener('pointermove', move);
    el.removeEventListener('pointerup', cancel);
    el.removeEventListener('pointercancel', cancel);
    el.removeEventListener('pointerleave', cancel);
  };
}

// ---------- misc ----------
export function navigate(hash, { replace = false } = {}) {
  const target = hash.startsWith('#') ? hash : `#${hash}`;
  if (replace) {
    history.replaceState(null, '', target);
    window.dispatchEvent(new HashChangeEvent('hashchange'));
  } else if (location.hash === target) {
    window.dispatchEvent(new HashChangeEvent('hashchange'));
  } else {
    location.hash = target;
  }
}

export function fmtDuration(ms) {
  const total = Math.max(0, Math.round(ms / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

export function fmtDurationLong(ms) {
  const total = Math.max(0, Math.round(ms / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  if (m === 0) return `${s} giây`;
  return s ? `${m} phút ${s} giây` : `${m} phút`;
}

export function clamp(v, lo, hi) {
  return Math.min(hi, Math.max(lo, v));
}

export function deepClone(obj) {
  return typeof structuredClone === 'function' ? structuredClone(obj) : JSON.parse(JSON.stringify(obj));
}

/** Creates a tracker so a screen can register cleanups in one place. */
export function createScope() {
  const cleanups = [];
  let disposed = false;
  return {
    get disposed() { return disposed; },
    add(fn) { if (disposed) { try { fn(); } catch { /* ignore */ } } else cleanups.push(fn); return fn; },
    timeout(fn, ms) { const id = setTimeout(fn, ms); cleanups.push(() => clearTimeout(id)); return id; },
    interval(fn, ms) { const id = setInterval(fn, ms); cleanups.push(() => clearInterval(id)); return id; },
    listen(target, type, fn, opts) { target.addEventListener(type, fn, opts); cleanups.push(() => target.removeEventListener(type, fn, opts)); },
    dispose() {
      if (disposed) return;
      disposed = true;
      while (cleanups.length) {
        const fn = cleanups.pop();
        try { fn(); } catch (err) { console.warn('cleanup failed', err); }
      }
    },
  };
}
