// Text-to-speech (speechSynthesis, vi-VN) + speech-to-text (Web Speech API, vi-VN).

const LANG = 'vi-VN';
const synth = typeof window !== 'undefined' ? window.speechSynthesis : undefined;

// ===================== TTS =====================
let voice = null;
let unlocked = false;
let muted = false;
let current = null; // keep a reference: Chrome garbage-collects utterances and drops onend
let speakToken = 0;

function scoreVoice(v) {
  const lang = (v.lang || '').replace('_', '-').toLowerCase();
  const name = (v.name || '').toLowerCase();
  let score = 0;
  if (lang === 'vi-vn') score += 5;
  if (/natural|neural|online/.test(name)) score += 4;
  if (/premium|enhanced|nâng cao/.test(name)) score += 3;
  if (/hoaimy|linh|google/.test(name)) score += 2;
  if (v.localService) score += 1;
  return score;
}

function pickVoice() {
  if (!synth) return null;
  const vi = synth.getVoices().filter((v) => (v.lang || '').toLowerCase().replace('_', '-').startsWith('vi'));
  vi.sort((a, b) => scoreVoice(b) - scoreVoice(a));
  voice = vi[0] || null;
  return voice;
}

if (synth) {
  pickVoice();
  synth.addEventListener?.('voiceschanged', pickVoice);
}

export function ttsSupported() {
  return !!synth && typeof window.SpeechSynthesisUtterance === 'function';
}

export function hasVietnameseVoice() {
  return !!(voice || pickVoice());
}

export function setMuted(value) {
  muted = !!value;
  if (muted) cancelSpeech();
}

/** Must be called synchronously inside a user tap (iOS needs a gesture before speech works). */
export function unlockTTS() {
  if (!ttsSupported() || unlocked) return;
  try {
    const u = new SpeechSynthesisUtterance(' ');
    u.volume = 0;
    u.lang = LANG;
    synth.speak(u);
    unlocked = true;
  } catch { /* ignore */ }
  pickVoice();
}

export function cancelSpeech() {
  speakToken += 1;
  if (!synth) return;
  try { synth.cancel(); } catch { /* ignore */ }
  current = null;
}

export function isSpeaking() {
  return !!synth && (synth.speaking || synth.pending);
}

/**
 * Speaks text in Vietnamese. New speech cancels old.
 * @returns {Promise<void>} resolves when finished (or immediately when muted / unsupported)
 */
export function speak(text, { rate = 1.05 } = {}) {
  const line = String(text || '').trim();
  if (!line || muted || !ttsSupported()) return Promise.resolve();
  const wasBusy = synth.speaking || synth.pending;
  cancelSpeech();
  const token = speakToken;
  return new Promise((resolve) => {
    const run = () => {
      if (token !== speakToken) return resolve();
      const u = new SpeechSynthesisUtterance(line);
      u.lang = LANG;
      const v = voice || pickVoice();
      if (v) u.voice = v;
      u.rate = rate;
      u.pitch = 1;
      let done = false;
      const finish = () => { if (!done) { done = true; clearTimeout(guard); resolve(); } };
      u.onend = finish;
      u.onerror = finish;
      // Safety net: some engines never fire onend.
      const guard = setTimeout(finish, Math.max(4000, line.length * 120));
      current = u;
      try { synth.resume?.(); } catch { /* ignore */ }
      try { synth.speak(u); } catch { finish(); }
    };
    // Chrome sometimes drops an utterance queued in the same tick as cancel().
    if (wasBusy) setTimeout(run, 60); else run();
  });
}

// ===================== STT =====================
function RecognitionCtor() {
  return window.SpeechRecognition || window.webkitSpeechRecognition || null;
}

export function sttSupported() {
  return !!RecognitionCtor();
}

const FATAL_ERRORS = new Set(['not-allowed', 'service-not-allowed', 'audio-capture', 'language-not-supported']);

/**
 * Continuous dictation for Teach mode. Auto-restarts while running.
 * Each final result becomes {t0, t1, text} in ms relative to `clockStart` (performance.now()).
 */
export class ContinuousTranscriber {
  constructor({ lang = LANG, onSegment, onInterim, onFatal } = {}) {
    this.lang = lang;
    this.onSegment = onSegment || (() => {});
    this.onInterim = onInterim || (() => {});
    this.onFatal = onFatal || (() => {});
    this.running = false;
    this.rec = null;
    this.clockStart = 0;
    this.lastFinalT1 = 0;
    this.pending = null; // {t0, text} of the current interim hypothesis
    this.restartTimer = null;
    this.quickFails = 0;
    this.endWaiters = [];
  }

  now() {
    return Math.round(performance.now() - this.clockStart);
  }

  start(clockStart = performance.now()) {
    if (!sttSupported()) return false;
    this.clockStart = clockStart;
    this.running = true;
    this.lastFinalT1 = 0;
    this.pending = null;
    this._spawn();
    return true;
  }

  _spawn() {
    if (!this.running) return;
    const Ctor = RecognitionCtor();
    const rec = new Ctor();
    rec.lang = this.lang;
    rec.continuous = true;
    rec.interimResults = true;
    rec.maxAlternatives = 1;
    const segStart = new Map();
    const emitted = new Set();
    const spawnedAt = performance.now();
    let fatal = false;

    rec.onresult = (event) => {
      const t = this.now();
      let interim = '';
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i];
        const text = (result[0]?.transcript || '').trim();
        if (!segStart.has(i)) segStart.set(i, Math.max(this.lastFinalT1, t - 1200));
        if (result.isFinal) {
          if (emitted.has(i) || !text) continue;
          emitted.add(i);
          const seg = { t0: segStart.get(i), t1: t, text };
          this.lastFinalT1 = t;
          this.pending = null;
          this.onSegment(seg);
        } else {
          interim += (interim ? ' ' : '') + text;
          this.pending = { t0: segStart.get(i), text: interim };
        }
      }
      this.onInterim(interim);
    };
    rec.onerror = (event) => {
      if (FATAL_ERRORS.has(event.error)) {
        fatal = true;
        this.running = false;
        this.onFatal(event.error);
      }
    };
    rec.onend = () => {
      if (this.rec === rec) this.rec = null;
      if (this.running && !fatal) {
        // avoid a hot loop if the engine keeps ending immediately
        this.quickFails = performance.now() - spawnedAt < 1000 ? this.quickFails + 1 : 0;
        if (this.quickFails > 6) {
          this.running = false;
          this.onFatal('restart-loop');
        } else {
          this.restartTimer = setTimeout(() => this._spawn(), this.quickFails ? 400 : 120);
        }
      }
      if (!this.running) this._resolveEnd();
    };
    this.rec = rec;
    try {
      rec.start();
    } catch (err) {
      console.warn('SpeechRecognition start failed', err);
      this.restartTimer = setTimeout(() => this._spawn(), 500);
    }
  }

  _resolveEnd() {
    const waiters = this.endWaiters.splice(0);
    waiters.forEach((fn) => fn());
  }

  /** Stops dictation; resolves after the last result is flushed. */
  stop() {
    this.running = false;
    clearTimeout(this.restartTimer);
    const rec = this.rec;
    return new Promise((resolve) => {
      const finish = () => {
        clearTimeout(guard);
        if (this.pending?.text) {
          this.onSegment({ t0: this.pending.t0, t1: this.now(), text: this.pending.text });
          this.pending = null;
        }
        resolve();
      };
      const guard = setTimeout(() => { this.endWaiters = this.endWaiters.filter((f) => f !== finish); finish(); }, 1500);
      if (!rec) { finish(); return; }
      this.endWaiters.push(finish);
      try { rec.stop(); } catch { finish(); }
    });
  }

  abort() {
    this.running = false;
    clearTimeout(this.restartTimer);
    try { this.rec?.abort(); } catch { /* ignore */ }
    this.rec = null;
    this._resolveEnd();
  }
}

/**
 * Press-and-hold recognition of one utterance (Guide "Hỏi Shadow").
 * Call inside the pointerdown handler. Returns {stop():Promise<string>, cancel()} or null if unsupported.
 */
export function holdToTalk({ lang = LANG, onInterim } = {}) {
  const Ctor = RecognitionCtor();
  if (!Ctor) return null;
  const rec = new Ctor();
  rec.lang = lang;
  rec.continuous = true;
  rec.interimResults = true;
  rec.maxAlternatives = 1;
  let finalText = '';
  let interimText = '';
  let ended = false;
  let errorCode = null;
  let endResolve = null;
  const endPromise = new Promise((resolve) => { endResolve = resolve; });

  rec.onresult = (event) => {
    let interim = '';
    let fin = '';
    for (let i = 0; i < event.results.length; i += 1) {
      const t = event.results[i][0]?.transcript || '';
      if (event.results[i].isFinal) fin += t; else interim += t;
    }
    finalText = fin.trim();
    interimText = interim.trim();
    onInterim?.(`${finalText} ${interimText}`.trim());
  };
  rec.onerror = (event) => { errorCode = event.error; };
  rec.onend = () => { ended = true; endResolve(); };
  try {
    rec.start();
  } catch (err) {
    console.warn('holdToTalk start failed', err);
    return null;
  }

  return {
    async stop() {
      if (!ended) {
        try { rec.stop(); } catch { /* ignore */ }
        await Promise.race([endPromise, new Promise((r) => setTimeout(r, 2500))]);
      }
      if (FATAL_ERRORS.has(errorCode)) {
        const err = new Error(errorCode);
        err.userMessage = 'Không dùng được micro. Hãy cho phép quyền micro cho trang này.';
        throw err;
      }
      return (finalText || interimText).trim();
    },
    cancel() {
      try { rec.abort(); } catch { /* ignore */ }
    },
  };
}
