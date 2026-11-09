// REST client for the SHADOW backend (same origin; see PLAN.md §6).

export class ApiError extends Error {
  constructor(message, { status = 0, userMessage } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.userMessage = userMessage || message;
  }
}

function detailToText(detail) {
  if (!detail) return '';
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    // FastAPI / pydantic validation errors
    return detail.map((d) => (d && (d.msg ? `${(d.loc || []).slice(1).join('.')}: ${d.msg}` : JSON.stringify(d)))).join('; ');
  }
  if (typeof detail === 'object') return detail.message || JSON.stringify(detail);
  return String(detail);
}

// ---- demo access gate (SHADOW_ACCESS_CODE on the server) + rate-limit messages -------------
let accessPromise = null;

async function isAccessRequired(res) {
  if (res.status !== 401) return false;
  try { return (await res.clone().json())?.code === 'access_required'; } catch { return false; }
}

/** Asks for the demo access code once (shared by concurrent requests); true when accepted. */
export function promptAccessCode() {
  if (!accessPromise) {
    accessPromise = (async () => {
      for (let attempt = 0; attempt < 3; attempt += 1) {
        const code = window.prompt(attempt
          ? 'Mã chưa đúng. Nhập lại mã truy cập SHADOW:'
          : 'SHADOW đang bật mã truy cập. Nhập mã (ban tổ chức cung cấp):');
        if (code === null) return false;
        let res = null;
        try {
          res = await fetch('/api/access', {
            method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
            body: JSON.stringify({ code: code.trim() }), cache: 'no-store',
          });
        } catch { return false; }
        if (res.ok) return true;
        if (res.status === 429) { window.alert('Nhập sai quá nhiều lần. Đợi một phút rồi thử lại nhé.'); return false; }
      }
      return false;
    })().finally(() => { accessPromise = null; });
  }
  return accessPromise;
}

/** fetch() that, on 401 access_required, asks for the access code and retries once. */
export async function fetchWithAccess(path, init) {
  const res = await fetch(path, init);
  if (await isAccessRequired(res) && await promptAccessCode()) return fetch(path, init);
  return res;
}

/** Vietnamese message for an HTTP error status (null → use the server detail). */
export function statusMessage(status, detail) {
  if (status === 401) return 'Cần mã truy cập để dùng SHADOW. Tải lại trang rồi nhập mã nhé.';
  if (status === 429) return detail || 'Bạn thao tác nhanh quá — đợi một chút rồi thử lại nhé.';
  if (status === 413) return 'Dữ liệu gửi lên quá lớn (ảnh quá nặng?).';
  return null;
}

async function request(method, path, { body, timeoutMs = 20000, signal } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(new DOMException('timeout', 'TimeoutError')), timeoutMs);
  const onAbort = () => controller.abort(signal.reason);
  signal?.addEventListener('abort', onAbort, { once: true });
  let res;
  try {
    res = await fetchWithAccess(path, {
      method,
      headers: body !== undefined ? { 'Content-Type': 'application/json', Accept: 'application/json' } : { Accept: 'application/json' },
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: controller.signal,
      cache: 'no-store',
    });
  } catch (err) {
    if (signal?.aborted) throw err;
    if (controller.signal.aborted) {
      throw new ApiError(`${method} ${path} timed out`, { userMessage: 'Máy chủ phản hồi quá lâu. Thử lại nhé.' });
    }
    throw new ApiError(`${method} ${path} failed: ${err.message}`, { userMessage: 'Không kết nối được máy chủ. Kiểm tra mạng.' });
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', onAbort);
  }

  const text = await res.text();
  let data = null;
  if (text) {
    try { data = JSON.parse(text); } catch { data = text; }
  }
  if (!res.ok) {
    const detail = detailToText(data && typeof data === 'object' ? (data.detail ?? data.error ?? data.message) : data);
    throw new ApiError(`${method} ${path} → ${res.status} ${detail}`, {
      status: res.status,
      userMessage: statusMessage(res.status, detail) || (detail ? `Lỗi ${res.status}: ${detail}` : `Lỗi máy chủ (${res.status})`),
    });
  }
  return data;
}

const enc = encodeURIComponent;

export const api = {
  health: () => request('GET', '/api/health', { timeoutMs: 6000 }),
  listSkills: () => request('GET', '/api/skills'),
  getSkill: (id) => request('GET', `/api/skills/${enc(id)}`),
  saveSkill: (id, skill) => request('PUT', `/api/skills/${enc(id)}`, { body: skill, timeoutMs: 30000 }),
  deleteSkill: (id) => request('DELETE', `/api/skills/${enc(id)}`),
  /** body: {title, teacher_name, transcript:[{t0,t1,text}], frames:[{t,image}], notes} → {skill, draft_id} */
  compile: (body, opts = {}) => request('POST', '/api/teach/compile', { body, timeoutMs: 180000, ...opts }),
  /** body: {skill_id, step_id, question, image} → {answer} */
  ask: (body, opts = {}) => request('POST', '/api/ask', { body, timeoutMs: 45000, ...opts }),
  // ---- eval capture (labelled frames for measuring the vision model) ----
  /** body: {skill_id, step_id, label: 'done'|'not_done', image} → {ok, counts: {<step_id>: {done, not_done}}} */
  evalCapture: (body, opts = {}) => request('POST', '/api/eval/capture', { body, timeoutMs: 20000, ...opts }),
  /** → {counts: {<step_id>: {done, not_done}}} */
  evalCounts: (skillId) => request('GET', `/api/eval/${enc(skillId)}`, { timeoutMs: 10000 }),
  /** Remove the most recent capture of `label` for a step → {removed, counts} */
  evalUndo: (skillId, stepId, label) => request('DELETE', `/api/eval/${enc(skillId)}/${enc(stepId)}/${enc(label)}/last`, { timeoutMs: 10000 }),
};

/** Resolve a media path from the backend to a usable <img src>. */
export function mediaUrl(url, skillId) {
  if (!url) return null;
  if (/^(https?:|data:|blob:|\/)/.test(url)) return url;
  // relative path such as "frames/step1.jpg" → served under /skills-media/<id>/
  if (skillId) return `/skills-media/${enc(skillId)}/${url.replace(/^\.?\//, '')}`;
  return url;
}
