// Teach: record the task with the rear camera while talking → AI compiles a skill.
import { api } from '../api.js';
import { startCamera, stopCamera, captureFrame, keepScreenAwake } from '../camera.js';
import { ContinuousTranscriber, sttSupported } from '../speech.js';
import { h, icon, toast, errorToast, navigate, fmtDuration, createScope, confirmSheet } from '../ui.js';
import { setDraft, getSettings, updateSettings } from '../store.js';

const FRAME_EVERY_MS = 2000;
const MAX_FRAMES_SENT = 24;
const MAX_FRAMES_KEPT = 96;

const LOADING_LINES = [
  'Đang nghe lại lời bạn nói…',
  'Đang xem các khung hình…',
  'Đang chia thành từng bước…',
  'Đang viết lời nhắc cho từng bước…',
  'Đang chuẩn bị cách nhận biết đúng / sai…',
  'Sắp xong rồi…',
];

/** Evenly keep `max` items (always keeps first and last). */
export function pickEvenly(items, max) {
  if (items.length <= max) return items.slice();
  const out = [];
  for (let i = 0; i < max; i += 1) out.push(items[Math.round((i * (items.length - 1)) / (max - 1))]);
  return out;
}

export function mount(el, { query = new URLSearchParams() } = {}) {
  const scope = createScope();
  const canListen = sttSupported();
  let stream = null;
  let state = 'preview'; // preview | recording | form | compiling
  let clockStart = 0;
  let frames = [];
  let transcript = [];
  let transcriber = null;
  let sttFailed = !canListen;
  let tickTimer = null;
  let frameTimer = null;
  let compileAbort = null;

  // ---------- DOM ----------
  const video = h('video', { class: 'cam', playsinline: true, muted: true, autoplay: true });
  const timerLabel = h('span', { class: 'rec-time' }, '00:00');
  const recPill = h('div', { class: 'rec-pill' }, h('i', { class: 'rec-dot' }), timerLabel);
  const modeLabel = h('div', { class: 'mode-label' }, 'Chế độ dạy');
  const frameCount = h('span', null, '0');
  const lineCount = h('span', null, '0');
  const counters = h('div', { class: 'counters' },
    h('span', { class: 'chip' }, icon('image', 16), frameCount),
    canListen ? h('span', { class: 'chip' }, icon('mic', 16), lineCount) : null);
  const transcriptList = h('div', { class: 'transcript-lines' });
  const interimLine = h('div', { class: 'transcript-interim' });
  const transcriptPanel = h('div', { class: 'transcript hidden' }, transcriptList, interimLine);
  const hint = h('div', { class: 'teach-hint' },
    h('div', { class: 'teach-hint-icon' }, icon('mic', 28)),
    h('p', { class: 'teach-hint-title' }, 'Vừa làm vừa nói từng bước'),
    h('p', { class: 'teach-hint-sub' }, '“Đầu tiên, đặt phin lên cốc… Lưu ý đừng rót nước quá nhanh…”'),
    !canListen ? h('p', { class: 'teach-hint-warn' }, icon('alert', 16), 'Trình duyệt không hỗ trợ nhận giọng nói — bạn sẽ gõ ghi chú sau khi quay.') : null);
  const recordBtn = h('button', { class: 'record-btn', type: 'button', 'aria-label': 'Bắt đầu ghi' }, h('span', { class: 'record-inner' }));
  const recordCaption = h('div', { class: 'record-caption' }, 'Bấm để ghi');
  const camError = h('div', { class: 'cam-error hidden' });
  const flash = h('div', { class: 'shutter-flash' });

  el.append(
    h('div', { class: 'cam-wrap' }, video, flash),
    h('div', { class: 'cam-shade' }),
    h('header', { class: 'cam-top' },
      h('button', { class: 'icon-btn glass', type: 'button', 'aria-label': 'Đóng', onClick: () => leave() }, icon('close', 22)),
      h('div', { class: 'cam-top-center' }, modeLabel, recPill),
      counters),
    hint,
    camError,
    h('div', { class: 'cam-bottom' },
      transcriptPanel,
      h('div', { class: 'record-row' }, recordBtn, recordCaption)),
  );
  recPill.classList.add('hidden');

  // ---------- camera ----------
  async function openCamera() {
    camError.classList.add('hidden');
    try {
      stream = await startCamera(video);
      if (scope.disposed) stopCamera(stream, video);
    } catch (err) {
      if (scope.disposed) return;
      camError.replaceChildren(
        h('div', { class: 'cam-error-icon' }, icon('camera', 30)),
        h('p', null, err.userMessage || err.message),
        h('button', { class: 'btn btn-primary', type: 'button', onClick: openCamera }, 'Thử lại'));
      camError.classList.remove('hidden');
    }
  }
  scope.add(() => stopCamera(stream, video));
  scope.add(keepScreenAwake());
  openCamera();

  // ---------- recording ----------
  function now() { return Math.round(performance.now() - clockStart); }

  function grab(kind) {
    if (state !== 'recording') return;
    const image = captureFrame(video, { maxSide: 512, quality: 0.7 });
    if (!image) return;
    frames.push({ t: now(), image, kind });
    if (frames.length > MAX_FRAMES_KEPT) {
      // thin out old interval frames, keep speech-aligned ones
      let toggle = false;
      frames = frames.filter((f) => f.kind === 'speech' || (toggle = !toggle));
    }
    frameCount.textContent = String(frames.length);
    flash.classList.remove('go');
    void flash.offsetWidth;
    flash.classList.add('go');
  }

  function addLine(seg) {
    transcript.push(seg);
    lineCount.textContent = String(transcript.length);
    transcriptList.append(h('div', { class: 'transcript-line' }, h('span', { class: 't' }, fmtDuration(seg.t0)), seg.text));
    while (transcriptList.children.length > 4) transcriptList.firstElementChild.remove();
    interimLine.textContent = '';
    // keyframe right after each finished sentence
    scope.timeout(() => grab('speech'), 120);
  }

  function startRecording() {
    if (!stream) { toast('Camera chưa sẵn sàng', { kind: 'error' }); return; }
    state = 'recording';
    clockStart = performance.now();
    frames = [];
    transcript = [];
    frameCount.textContent = '0';
    lineCount.textContent = '0';
    transcriptList.replaceChildren();
    interimLine.textContent = canListen ? 'Đang nghe…' : '';
    el.classList.add('is-recording');
    hint.classList.add('hidden');
    modeLabel.classList.add('hidden');
    recPill.classList.remove('hidden');
    transcriptPanel.classList.toggle('hidden', !canListen);
    recordBtn.setAttribute('aria-label', 'Dừng ghi');
    recordCaption.textContent = 'Bấm để dừng';
    navigator.vibrate?.(20);

    if (canListen && !sttFailed) {
      transcriber = new ContinuousTranscriber({
        onSegment: addLine,
        onInterim: (text) => { interimLine.textContent = text; },
        onFatal: (code) => {
          sttFailed = true;
          console.warn('STT fatal', code);
          toast('Không nhận được giọng nói (micro bị chặn?). Bạn có thể gõ ghi chú sau khi dừng.', { kind: 'error', ms: 5000 });
          interimLine.textContent = '';
        },
      });
      transcriber.start(clockStart);
    }
    grab('interval');
    frameTimer = setInterval(() => grab('interval'), FRAME_EVERY_MS);
    tickTimer = setInterval(() => { timerLabel.textContent = fmtDuration(now()); }, 250);
  }

  async function stopRecording() {
    state = 'stopping';
    clearInterval(frameTimer);
    clearInterval(tickTimer);
    recordBtn.disabled = true;
    recordCaption.textContent = 'Đang dừng…';
    if (transcriber) {
      await transcriber.stop();
      transcriber = null;
    }
    if (scope.disposed) return;
    state = 'form';
    el.classList.remove('is-recording');
    recordBtn.disabled = false;
    recordCaption.textContent = 'Bấm để ghi';
    recordBtn.setAttribute('aria-label', 'Bắt đầu ghi');
    showForm();
  }

  recordBtn.addEventListener('click', () => {
    if (state === 'preview') startRecording();
    else if (state === 'recording') stopRecording();
  });

  scope.add(() => {
    clearInterval(frameTimer);
    clearInterval(tickTimer);
    transcriber?.abort();
    compileAbort?.abort();
  });

  // ---------- form ----------
  let formEl = null;
  function showForm() {
    const settings = getSettings();
    const needNotes = sttFailed || !transcript.length;
    const duration = frames.length ? frames[frames.length - 1].t : now();
    const titleInput = h('input', { class: 'input', type: 'text', placeholder: 'VD: Pha cà phê phin', maxlength: '80', autocomplete: 'off', enterkeyhint: 'next' });
    const teacherInput = h('input', { class: 'input', type: 'text', placeholder: 'Tên của bạn', maxlength: '40', autocomplete: 'name', value: settings.teacherName || '' });
    const notesInput = h('textarea', {
      class: 'input textarea',
      rows: needNotes ? '6' : '3',
      placeholder: needNotes
        ? 'Gõ từng bước bạn vừa làm, mỗi dòng một bước.\nVD:\nĐặt phin lên cốc\nCho 2 thìa cà phê vào phin\nRót một ít nước sôi để ủ'
        : 'Mẹo, lưu ý thêm cho người học (tuỳ chọn)',
    });

    const transcriptBlock = transcript.length
      ? h('details', { class: 'form-transcript' },
        h('summary', null, `Lời bạn nói (${transcript.length} câu)`),
        h('ol', null, transcript.map((s) => h('li', null, h('span', { class: 't' }, fmtDuration(s.t0)), s.text))))
      : null;

    const submit = h('button', { class: 'btn btn-primary btn-lg', type: 'submit' }, icon('sparkle', 20), 'AI học ngay');
    formEl = h('form', { class: 'teach-form', novalidate: true },
      h('div', { class: 'form-head' },
        h('h2', null, 'Đặt tên cho kỹ năng'),
        h('div', { class: 'form-stats' },
          h('span', { class: 'chip' }, icon('clock', 15), fmtDuration(duration)),
          h('span', { class: 'chip' }, icon('image', 15), `${frames.length} khung hình`),
          h('span', { class: 'chip' }, icon('mic', 15), `${transcript.length} câu nói`))),
      h('label', { class: 'field' }, h('span', { class: 'field-label' }, 'Tên kỹ năng'), titleInput),
      h('label', { class: 'field' }, h('span', { class: 'field-label' }, 'Người dạy'), teacherInput),
      h('label', { class: 'field' }, h('span', { class: 'field-label' }, needNotes ? 'Các bước (mỗi dòng một bước)' : 'Ghi chú thêm'), notesInput),
      transcriptBlock,
      h('div', { class: 'form-actions' },
        h('button', { class: 'btn btn-ghost', type: 'button', onClick: redo }, icon('refresh', 18), 'Quay lại'),
        submit),
    );
    formEl.addEventListener('submit', (e) => {
      e.preventDefault();
      const title = titleInput.value.trim();
      const teacher = teacherInput.value.trim();
      const notes = notesInput.value.trim();
      if (!title) { titleInput.focus(); toast('Hãy đặt tên cho kỹ năng', { kind: 'error' }); return; }
      if (!transcript.length && !notes) {
        notesInput.focus();
        toast('Chưa có lời nói nào — hãy gõ các bước vào ghi chú', { kind: 'error' });
        return;
      }
      updateSettings({ teacherName: teacher });
      compile({ title, teacher, notes });
    });
    el.append(h('div', { class: 'form-backdrop' }), formEl);
    el.classList.add('has-form');
    setTimeout(() => titleInput.focus({ preventScroll: true }), 350);
  }

  async function redo() {
    if (frames.length || transcript.length) {
      const ok = await confirmSheet('Bỏ bản ghi này và quay lại từ đầu?', { okLabel: 'Quay lại', danger: true });
      if (!ok) return;
    }
    closeForm();
    state = 'preview';
    frames = [];
    transcript = [];
    frameCount.textContent = '0';
    lineCount.textContent = '0';
    recPill.classList.add('hidden');
    modeLabel.classList.remove('hidden');
    hint.classList.remove('hidden');
    transcriptPanel.classList.add('hidden');
    timerLabel.textContent = '00:00';
  }

  function closeForm() {
    el.querySelectorAll('.teach-form, .form-backdrop').forEach((n) => n.remove());
    el.classList.remove('has-form');
    formEl = null;
  }

  // ---------- compile ----------
  async function compile({ title, teacher, notes }) {
    state = 'compiling';
    const sent = pickEvenly(frames, MAX_FRAMES_SENT).map(({ t, image }) => ({ t, image }));
    const body = { title, teacher_name: teacher, transcript: transcript.map(({ t0, t1, text }) => ({ t0, t1, text })), frames: sent, notes };

    let lineIdx = 0;
    const lineEl = h('p', { class: 'loading-line' }, LOADING_LINES[0]);
    const elapsedEl = h('div', { class: 'loading-elapsed', 'aria-hidden': 'true' });
    const startedAt = Date.now();
    const overlay = h('div', { class: 'loading-overlay' },
      h('div', { class: 'orb', 'aria-hidden': 'true' }, h('i'), h('i'), h('i')),
      h('h2', { class: 'loading-title' }, 'AI đang học…'),
      lineEl,
      h('div', { class: 'loading-stats' }, `${transcript.length} câu nói · ${sent.length} khung hình`),
      h('div', { class: 'progress-bar' }, h('i')),
      elapsedEl,
      h('button', { class: 'btn btn-ghost btn-sm', type: 'button', onClick: () => compileAbort?.abort() }, 'Huỷ'));
    el.append(overlay);
    requestAnimationFrame(() => overlay.classList.add('in'));
    // A real model needs ~10–30 s: show elapsed time so the screen never looks frozen.
    const tick = setInterval(() => {
      const sec = Math.round((Date.now() - startedAt) / 1000);
      elapsedEl.textContent = sec < 4 ? '' : sec < 35
        ? `${sec} giây · thường mất 10–30 giây`
        : `${sec} giây · mạng chậm, AI vẫn đang làm…`;
    }, 1000);
    const rotate = setInterval(() => {
      lineIdx = Math.min(lineIdx + 1, LOADING_LINES.length - 1);
      lineEl.classList.remove('swap');
      void lineEl.offsetWidth;
      lineEl.classList.add('swap');
      lineEl.textContent = LOADING_LINES[lineIdx];
    }, 2600);

    compileAbort = new AbortController();
    try {
      const res = await api.compile(body, { signal: compileAbort.signal });
      if (scope.disposed) return;
      const skill = res?.skill || res;
      if (!skill || !Array.isArray(skill.steps)) throw new Error('Phản hồi không hợp lệ từ máy chủ');
      const compiledBy = res?.compiled_by || skill.compiled_by || null;
      const fallbackReason = res?.fallback_reason || null;
      setDraft({ skill, draft_id: res?.draft_id ?? skill.draft_id ?? null, isNew: true, compiledBy, fallbackReason });
      navigator.vibrate?.([20, 40, 20]);
      navigate('#/review');
    } catch (err) {
      if (scope.disposed) return;
      if (compileAbort?.signal.aborted) toast('Đã huỷ', { kind: 'info' });
      else errorToast(err, 'AI chưa học được, thử lại nhé');
      overlay.remove();
      state = 'form';
    } finally {
      clearInterval(rotate);
      clearInterval(tick);
      compileAbort = null;
    }
  }

  async function leave() {
    if (state === 'recording' || ((state === 'form' || state === 'compiling') && (frames.length || transcript.length))) {
      const ok = await confirmSheet('Thoát và bỏ bản ghi đang làm?', { okLabel: 'Thoát', danger: true });
      if (!ok) return;
    }
    navigate(query.get('from') === 'conversation' ? '#/conversation' : '#/home');
  }

  return () => scope.dispose();
}
