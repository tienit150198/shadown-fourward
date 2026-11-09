// App state that must survive a screen change (memory + sessionStorage/localStorage).

const mem = { draft: null, done: null };

const STYLIST_SESSION_KEY = 'shadow.stylist.chat';
const STYLIST_MAX_MESSAGES = 40;

function trimStylistSession(value) {
  if (!value || typeof value !== 'object') return null;
  const messages = Array.isArray(value.messages) ? value.messages.slice(-STYLIST_MAX_MESSAGES)
    .filter((m) => m && (m.role === 'user' || m.role === 'assistant') && typeof m.text === 'string')
    .map((m) => ({ role: m.role, text: m.text.slice(0, 1000) })) : [];
  return {
    sessionId: typeof value.sessionId === 'string' ? value.sessionId.slice(0, 120) : null,
    messages,
    response: value.response && typeof value.response === 'object' ? value.response : null,
    updatedAt: Number(value.updatedAt) || Date.now(),
  };
}

export function setStylistSession(value) {
  const next = trimStylistSession(value);
  mem.stylist = next;
  writeSession(STYLIST_SESSION_KEY, next);
  return next;
}
export function getStylistSession() {
  if (mem.stylist !== undefined) return mem.stylist;
  mem.stylist = trimStylistSession(readSession(STYLIST_SESSION_KEY));
  return mem.stylist;
}
export function clearStylistSession() {
  mem.stylist = null;
  writeSession(STYLIST_SESSION_KEY, null);
}

function readSession(key) {
  try { return JSON.parse(sessionStorage.getItem(key) || 'null'); } catch { return null; }
}
function writeSession(key, value) {
  try {
    if (value === null || value === undefined) sessionStorage.removeItem(key);
    else sessionStorage.setItem(key, JSON.stringify(value));
  } catch { /* quota / private mode: memory copy still works */ }
}

/** Draft being reviewed: { skill, draft_id|null, isNew } */
export function setDraft(draft) { mem.draft = draft; writeSession('shadow.draft', draft); }
export function getDraft() { return mem.draft || (mem.draft = readSession('shadow.draft')); }
export function clearDraft() { mem.draft = null; writeSession('shadow.draft', null); }

/** Stats shown on the Done screen. */
export function setDoneStats(stats) { mem.done = stats; writeSession('shadow.done', stats); }
export function getDoneStats() { return mem.done || (mem.done = readSession('shadow.done')); }

// ---------- persistent settings ----------
const DEFAULT_SETTINGS = {
  cv: true,           // MediaPipe hand stillness trigger
  handOverlay: true,  // draw hand landmarks
  tts: true,          // speak aloud
  debug: false,       // show ◀ ▶ debug controls
  evalCapture: false, // Guide: collect labelled frames instead of sending vision.frame
  teacherName: '',
};

export function getSettings() {
  try {
    return { ...DEFAULT_SETTINGS, ...(JSON.parse(localStorage.getItem('shadow.settings') || '{}')) };
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
}

export function updateSettings(patch) {
  const next = { ...getSettings(), ...patch };
  try { localStorage.setItem('shadow.settings', JSON.stringify(next)); } catch { /* ignore */ }
  return next;
}
