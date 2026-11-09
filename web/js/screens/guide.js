// Guide: the AI watches through the camera and coaches the learner step by step.
import { api } from '../api.js';
import { ControlSocket } from '../ws.js';
import { startCamera, stopCamera, captureFrame, keepScreenAwake } from '../camera.js';
import { speak, cancelSpeech, unlockTTS, setMuted, holdToTalk, sttSupported, ttsSupported, hasVietnameseVoice, isSpeaking } from '../speech.js';
import { createStillnessDetector } from '../cv.js';
import { stepImageUrl } from './review.js';
import { h, icon, toast, errorToast, navigate, createScope, onLongPress, openSheet } from '../ui.js';
import { getSettings, updateSettings, setDoneStats } from '../store.js';

const PERIODIC_MS = 1500;
const MIN_GAP_MS = 1200;
const MAX_BUFFERED = 512 * 1024;
const RESULT_HOLD_MS = 1400;
// Low-priority lines (AI hints, teacher tips) never cut off the instruction being read out:
// they wait until the current line has finished (and are dropped if they go stale).
const LOW_PRIORITY_SPEECH = new Set(['hint', 'tip']);
const QUEUED_SPEECH_MAX_AGE_MS = 12000;

export function mount(el, { params }) {
  const skillId = params[0];
  const scope = createScope();
  if (!skillId) {
    navigate('#/home', { replace: true });
    return () => scope.dispose();
  }

  let settings = getSettings();
  setMuted(!settings.tts);

  // ---------- runtime state ----------
  let skill = null;              // full skill JSON (optional, for names / images / judging)
  let stream = null;
  let sock = null;
  let ready = false;             // got session.ready on the current connection
  let userStarted = false;       // tapped "Bắt đầu" (TTS unlocked)
  let sessionStarted = false;
  let phase = 'WAITING_FOR_START';
  let currentStep = null;
  let lastStepIndex = 0;
  let pendingResume = 0;
  let lastSpokenEpoch = -1;
  let lastSentAt = 0;
  let lastResultAt = 0;
  let scheduledSend = null;
  let framesSent = 0;
  let framesChecked = 0;
  let corrections = 0;
  let startedAt = 0;
  let finished = false;
  let cv = null;
  let asking = false;
  let cvStatus = 'off';
  let cvStatusEl = null;
  let keepSpeech = false;
  let completeSpoken = false;
  let queuedSpeech = null;       // {text, kind, stepId, at} waiting for the current line to end
  let speechSeq = 0;
  let aiDown = false;            // backend reported the vision AI as unreachable
  // eval capture mode (labelled frames for measuring the vision model)
  let capturing = !!settings.evalCapture;
  let evalCounts = {};           // {<step_id>: {done, not_done}} from the backend
  let countsSeq = 0;             // request sequence → ignore out-of-order responses
  let countsApplied = 0;
  let countsStepId;             // step whose counts were last fetched (undefined → fetch on next render)
  const undoStacks = new Map();  // step_id → labels captured this session (most recent last)
  const pendingByStep = new Map(); // step_id → Set<Promise> of in-flight captures
  let undoChain = Promise.resolve();
  let evalStatusTimer = null;

  // ---------- DOM ----------
  const video = h('video', { class: 'cam', playsinline: true, muted: true, autoplay: true });
  const overlay = h('canvas', { class: 'hand-overlay', 'aria-hidden': 'true' });
  const stepLabel = h('div', { class: 'step-label' }, 'Chuẩn bị');
  const skillLabel = h('div', { class: 'skill-label' }, '');
  const pillText = h('span', { class: 'pill-text' }, 'Đang kết nối…');
  const pillLatency = h('span', { class: 'pill-latency' });
  const pill = h('div', { class: 'status-pill is-idle' }, h('i', { class: 'pill-dot' }), pillText, pillLatency);
  const instruction = h('div', { class: 'hud-instruction' }, 'Đang tải kỹ năng…');
  const chips = h('div', { class: 'hud-chips' });
  const dots = h('div', { class: 'progress-dots', role: 'progressbar' });
  const hud = h('div', { class: 'hud glass' }, instruction, chips, dots);
  const refImg = h('img', { alt: 'Ảnh mẫu của người dạy' });
  const refThumb = h('button', { class: 'ref-thumb hidden', type: 'button', 'aria-label': 'Xem ảnh mẫu' }, refImg, h('span', { class: 'ref-tag' }, 'Mẫu'));
  const bubbleText = h('div', { class: 'bubble-text' });
  const bubbleWho = h('div', { class: 'bubble-who' }, 'SHADOW');
  const bubble = h('div', { class: 'speech-bubble hidden', 'aria-live': 'polite' },
    h('div', { class: 'bubble-avatar', 'aria-hidden': 'true' }), h('div', { class: 'bubble-body' }, bubbleWho, bubbleText));
  const debugBar = h('div', { class: `debug-bar${settings.debug || capturing ? '' : ' hidden'}` },
    h('button', { class: 'icon-btn glass', type: 'button', 'aria-label': 'Bước trước', onClick: () => debugStep('backward') }, icon('left', 22)),
    h('span', null, 'debug'),
    h('button', { class: 'icon-btn glass', type: 'button', 'aria-label': 'Bước sau', onClick: () => debugStep('forward') }, icon('right', 22)));
  const evalStep = h('div', { class: 'eval-step' }, 'Chưa có bước');
  const evalSub = h('div', { class: 'eval-sub' }, 'Bấm “Bắt đầu” để vào bước 1');
  const evalDoneCount = h('b', { class: 'eval-n-done' }, '0');
  const evalNotCount = h('b', { class: 'eval-n-not' }, '0');
  const evalCountsEl = h('div', { class: 'eval-counts', 'aria-label': 'Số mẫu của bước này' },
    h('span', { class: 'eval-mark ok' }, '✓'), evalDoneCount, h('span', { class: 'eval-dot' }, '·'),
    h('span', { class: 'eval-mark bad' }, '✗'), evalNotCount);
  const evalUndoBtn = h('button', { class: 'eval-undo', type: 'button', disabled: true, onClick: () => undoCapture() },
    h('span', { 'aria-hidden': 'true' }, '↶'), 'Hoàn tác');
  const evalDoneBtn = h('button', { class: 'eval-btn eval-done', type: 'button', disabled: true, onClick: () => captureSample('done') },
    h('span', { class: 'eval-btn-mark', 'aria-hidden': 'true' }, '✓'), 'Đã xong');
  const evalNotBtn = h('button', { class: 'eval-btn eval-not', type: 'button', disabled: true, onClick: () => captureSample('not_done') },
    h('span', { class: 'eval-btn-mark', 'aria-hidden': 'true' }, '✗'), 'Chưa xong');
  const evalBar = h('section', { class: `eval-bar glass${capturing ? '' : ' hidden'}`, 'aria-label': 'Thu mẫu eval' },
    h('div', { class: 'eval-head' },
      h('div', { class: 'eval-titles' }, evalStep, evalSub),
      evalCountsEl, evalUndoBtn),
    h('div', { class: 'eval-actions' }, evalDoneBtn, evalNotBtn));
  const shutter = h('div', { class: 'shutter-flash' });
  if (capturing) el.classList.add('eval-on');
  const micLabel = h('span', { class: 'mic-label' }, 'Giữ để hỏi Shadow');
  const micBtn = h('button', { class: 'mic-btn', type: 'button', 'aria-label': 'Giữ để hỏi Shadow' }, icon('mic', 30));
  const muteBtn = h('button', { class: 'icon-btn glass', type: 'button', 'aria-label': 'Bật/tắt giọng nói' }, icon('speaker', 22));
  const camError = h('div', { class: 'cam-error hidden' });
  const startTitle = h('h1', { class: 'start-title' }, 'Đang tải…');
  const startMeta = h('div', { class: 'start-meta' });
  const startBtn = h('button', { class: 'btn btn-primary btn-xl', type: 'button' }, icon('play', 22), 'Bắt đầu');
  const startConn = h('div', { class: 'start-conn' }, 'Đang kết nối máy chủ…');
  el.classList.add('pre-start');
  const startOverlay = h('div', { class: 'start-overlay' },
    h('div', { class: 'start-card' },
      h('div', { class: 'start-kicker' }, icon('eye', 16), 'SHADOW sẽ nhìn và hướng dẫn bạn'),
      startTitle, startMeta,
      h('ul', { class: 'start-tips' },
        h('li', null, icon('camera', 18), 'Đặt điện thoại sao cho camera thấy rõ tay và đồ vật.'),
        h('li', null, icon('speaker', 18), 'Bật âm lượng để nghe hướng dẫn.'),
        h('li', null, icon('mic', 18), 'Giữ nút micro để hỏi khi bí.')),
      startBtn, startConn));

  el.append(
    h('div', { class: 'cam-wrap' }, video, overlay, shutter),
    h('div', { class: 'cam-shade' }),
    h('header', { class: 'guide-top' },
      h('div', { class: 'guide-row' },
        h('button', { class: 'icon-btn glass', type: 'button', 'aria-label': 'Thoát', onClick: () => navigate('#/home') }, icon('close', 22)),
        h('div', { class: 'guide-titles' }, stepLabel, skillLabel),
        pill),
      hud,
      h('div', { class: 'guide-side' }, refThumb, debugBar)),
    camError,
    h('div', { class: 'guide-bottom' },
      bubble,
      evalBar,
      h('div', { class: 'guide-controls' },
        h('button', { class: 'icon-btn glass', type: 'button', 'aria-label': 'Cài đặt', onClick: openSettings }, icon('sliders', 22)),
        h('div', { class: 'mic-wrap' }, micBtn, micLabel),
        muteBtn)),
    startOverlay,
  );
  updateMuteBtn();

  // ---------- skill (for start card, images, judging) ----------
  api.getSkill(skillId).then((s) => {
    if (scope.disposed) return;
    skill = s;
    startTitle.textContent = s.display_name || skillId;
    skillLabel.textContent = s.display_name || '';
    startMeta.replaceChildren(...[
      s.learned_from ? h('span', { class: 'chip' }, icon('user', 15), `Học từ ${s.learned_from}`) : null,
      h('span', { class: 'chip' }, icon('list', 15), `${(s.steps || []).length} bước`)].filter(Boolean));
    if (phase === 'WAITING_FOR_START') instruction.textContent = s.summary || 'Bấm “Bắt đầu” khi bạn đã sẵn sàng.';
    if (currentStep) renderStep(currentStep);
  }).catch((err) => {
    if (scope.disposed) return;
    errorToast(err, 'Không tải được kỹ năng');
    startTitle.textContent = 'Không tải được kỹ năng';
  });

  // ---------- camera ----------
  async function openCamera() {
    camError.classList.add('hidden');
    try {
      stream = await startCamera(video);
      if (scope.disposed) { stopCamera(stream, video); return; }
      if (userStarted) startCv();
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

  // ---------- websocket ----------
  sock = new ControlSocket({
    onStatus: (status) => {
      if (status === 'open') {
        startConn.textContent = 'Đã kết nối';
        startConn.classList.add('ok');
      } else if (status === 'closed' || status === 'connecting') {
        if (ready) {
          // lost a running session: remember where we were
          ready = false;
          sessionStarted = false;
          if (phase === 'GUIDING') pendingResume = lastStepIndex;
          setPill('idle', 'Mất kết nối…');
        }
        startConn.textContent = status === 'connecting' ? 'Đang kết nối máy chủ…' : 'Mất kết nối, đang thử lại…';
        startConn.classList.remove('ok');
      }
    },
    onMessage: handleMessage,
  });
  sock.connect();
  scope.add(() => sock.close());

  function handleMessage(msg) {
    if (scope.disposed) return;
    switch (msg.type) {
      case 'session.ready':
        ready = true;
        lastSpokenEpoch = -1;
        if (userStarted) sendStart();
        else setPill('idle', 'Sẵn sàng');
        break;
      case 'hud.state':
        onHud(msg);
        break;
      case 'speech':
        onSpeech(msg);
        break;
      case 'vision.result':
        onVision(msg);
        break;
      case 'hud.error':
        if (msg.code === 'vision_unavailable') {
          aiDown = true;
          setPill('warn', 'Mất kết nối AI');
          toast(msg.message, { kind: 'error', ms: 6500 });
        } else {
          toast(msg.message || 'Có lỗi xảy ra', { kind: 'error', ms: 4200 });
        }
        break;
      default:
        break;
    }
  }

  function sendStart() {
    if (!ready || sessionStarted) return;
    if (sock.send({ type: 'session.start', skill_id: skillId })) {
      sessionStarted = true;
      if (!startedAt) startedAt = Date.now();
      setPill('idle', 'Đang bắt đầu…');
    }
  }

  startBtn.addEventListener('click', () => {
    unlockTTS(); // must happen synchronously inside the tap (iOS)
    userStarted = true;
    startOverlay.classList.add('leaving');
    el.classList.remove('pre-start');
    setTimeout(() => startOverlay.remove(), 350);
    if (ttsSupported() && !hasVietnameseVoice()) {
      toast('Máy chưa có giọng đọc tiếng Việt — hướng dẫn vẫn hiện bằng chữ.', { kind: 'info', ms: 4500 });
    }
    sendStart();
    if (!ready) setPill('idle', 'Đang kết nối…');
    startCv();
  });

  // ---------- HUD ----------
  function onHud(msg) {
    const prevPhase = phase;
    phase = msg.phase || phase;
    if (msg.recipe_name) skillLabel.textContent = msg.recipe_name;

    renderDots(msg.tasks || [], msg.active_task_id);
    const step = msg.step || fallbackStep(msg);
    if (step) {
      currentStep = step;
      if (typeof step.index === 'number') lastStepIndex = step.index;
      renderStep(step);
    }
    renderEvalBar();

    if (phase === 'GUIDING' && pendingResume > 0) {
      const n = pendingResume;
      pendingResume = 0;
      for (let i = 0; i < n; i += 1) scope.timeout(() => sock.send({ type: 'debug.step', direction: 'forward' }), 120 * (i + 1));
      toast('Đã kết nối lại — tiếp tục từ bước đang làm', { kind: 'info' });
    }
    if (phase === 'GUIDING' && prevPhase !== 'GUIDING') {
      if (capturing) setPill('seen', 'Đang thu mẫu');
      else {
        setPill('looking', 'Đang nhìn…');
        sendFrame('interval', true);
      }
    }
    if (phase === 'COMPLETED') finish();
    if (phase === 'ERROR') setPill('bad', 'Lỗi phiên');
  }

  function fallbackStep(msg) {
    // older backend without `step`: derive from tasks
    const tasks = msg.tasks || [];
    const idx = tasks.findIndex((t) => t.id === msg.active_task_id);
    if (idx < 0) return null;
    return { id: msg.active_task_id, index: idx, total: tasks.length, instruction: tasks[idx].text };
  }

  function findSkillStep(stepId) {
    return skill?.steps?.find((s) => s.id === stepId) || null;
  }

  function renderStep(step) {
    const total = step.total || skill?.steps?.length || 0;
    const index = (step.index ?? 0) + 1;
    stepLabel.textContent = total ? `Bước ${index}/${total}` : `Bước ${index}`;
    const text = step.instruction || findSkillStep(step.id)?.instruction || '';
    if (instruction.textContent !== text) {
      instruction.textContent = text;
      hud.classList.remove('pop');
      void hud.offsetWidth;
      hud.classList.add('pop');
    }
    chips.replaceChildren(...[
      step.tip ? h('span', { class: 'hud-chip tip' }, icon('bulb', 15), step.tip) : null,
      step.warning ? h('span', { class: 'hud-chip warn' }, icon('alert', 15), step.warning) : null].filter(Boolean));
    const full = findSkillStep(step.id);
    const img = step.reference_image_url || (full ? stepImageUrl(full, skillId) : null);
    if (img) {
      if (refImg.getAttribute('src') !== img) refImg.src = img;
      refThumb.classList.remove('hidden');
    } else {
      refThumb.classList.add('hidden');
    }
  }

  function renderDots(tasks, activeId) {
    if (!tasks.length) { dots.replaceChildren(); return; }
    const done = tasks.filter((t) => t.completed).length;
    dots.setAttribute('aria-valuenow', String(done));
    dots.setAttribute('aria-valuemax', String(tasks.length));
    dots.replaceChildren(...tasks.map((t) => h('i', {
      class: `pdot${t.completed ? ' done' : ''}${t.id === activeId && !t.completed ? ' active' : ''}`,
      title: t.text,
    })));
  }

  refImg.addEventListener('error', () => refThumb.classList.add('hidden'));
  refThumb.addEventListener('click', () => {
    if (!refImg.src) return;
    openSheet(h('div', { class: 'image-view' }, h('img', { src: refImg.src, alt: 'Ảnh mẫu' }),
      currentStep?.instruction ? h('p', { class: 'image-caption' }, currentStep.instruction) : null),
    { title: 'Người dạy làm thế này', className: 'sheet-image' });
  });

  // hidden debug controls: long-press the HUD
  scope.add(onLongPress(hud, () => {
    settings = updateSettings({ debug: !settings.debug });
    updateDebugBar();
    toast(settings.debug ? 'Đã bật nút debug ◀ ▶' : 'Đã tắt nút debug', { kind: 'info', ms: 1500 });
  }, 800));

  function updateDebugBar() {
    debugBar.classList.toggle('hidden', !(settings.debug || capturing));
  }

  function debugStep(direction) {
    if (!sock.send({ type: 'debug.step', direction })) toast('Chưa kết nối', { kind: 'error' });
  }

  // ---------- speech ----------
  function showBubble(text, who = 'SHADOW', kind = '') {
    bubbleWho.textContent = who;
    bubbleText.textContent = text;
    bubble.className = `speech-bubble${kind ? ` kind-${kind}` : ''}`;
    bubble.classList.remove('pop');
    void bubble.offsetWidth;
    bubble.classList.add('pop');
  }

  function onSpeech(msg) {
    const text = String(msg.text || '').trim();
    if (!text) return;
    if (typeof msg.epoch === 'number') {
      if (msg.epoch <= lastSpokenEpoch) return; // duplicate / stale
      lastSpokenEpoch = msg.epoch;
    }
    if (msg.kind === 'mismatch' || msg.kind === 'hint') {
      corrections += 1;
      setPill('bad', 'Chưa đúng');
      navigator.vibrate?.(60);
    } else if (msg.kind === 'success' || msg.kind === 'complete') {
      if (msg.kind === 'complete') completeSpoken = true;
      setPill('ok', 'Đúng rồi');
      navigator.vibrate?.([15, 30, 15]);
    }
    if (LOW_PRIORITY_SPEECH.has(msg.kind) && !asking && isSpeaking()) {
      // keep only the newest low-priority line; it plays when the current one ends
      queuedSpeech = { text, kind: msg.kind, stepId: currentStep?.id, at: Date.now() };
      return;
    }
    playLine(text, msg.kind);
  }

  function playLine(text, kind) {
    queuedSpeech = null;
    showBubble(text, 'SHADOW', kind);
    if (asking) return;
    const seq = ++speechSeq;
    speak(text).then(() => {
      // only the line that is still current may hand over to a queued one
      if (seq === speechSeq && !scope.disposed) scope.timeout(flushQueuedSpeech, 250);
    });
  }

  function flushQueuedSpeech() {
    const q = queuedSpeech;
    if (!q || isSpeaking() || asking || finished) return;
    queuedSpeech = null;
    if (q.stepId !== currentStep?.id || Date.now() - q.at > QUEUED_SPEECH_MAX_AGE_MS) return;
    playLine(q.text, q.kind);
  }

  // ---------- vision status ----------
  function setPill(state, text, latency) {
    pill.className = `status-pill is-${state}`;
    pillText.textContent = text;
    pillLatency.textContent = latency ? `${latency} ms` : '';
  }

  function judge(msg) {
    const result = msg.result;
    if (!result || typeof result !== 'object') return null;
    const step = findSkillStep(msg.step_id || currentStep?.id);
    const det = step && skill?.detectors?.[step.detector_key];
    if (step && det) {
      const value = result[det.field];
      const ignored = [...(step.ignored_values || []), ...(step.ignore_values || [])].map(String);
      if (value === undefined || value === null || ignored.includes(String(value))) return null;
      // true = done · false = visibly wrong · 'pending' = not done yet (no mistake seen)
      switch (step.evaluation_mode) {
        case 'momentary_true_complete':
          if (value === (step.complete_on ?? true)) return true;
          return result.hint ? false : 'pending';
        case 'match_value':
          return String(value).toLowerCase() === String(step.expected_value).toLowerCase();
        case 'enum_progress_once_then_complete':
          if (String(value) === String(step.complete_value)) return true;
          if (String(value) === String(step.progress_value)) return 'progress';
          return result.hint ? false : 'pending';
        default:
          break;
      }
    }
    if (result.flag === true) return true;
    if (result.hint) return false;
    if (result.flag === false) return 'pending';
    return null;
  }

  function onVision(msg) {
    lastResultAt = performance.now();
    if (msg.error) {
      setPill('warn', aiDown ? 'Mất kết nối AI' : 'AI chưa nhìn được');
      return;
    }
    if (aiDown) {
      aiDown = false;
      toast('AI đã kết nối lại', { kind: 'ok', ms: 2200 });
    }
    framesChecked += 1;
    if (msg.stale) return;
    if (msg.confirming) sendFrame('confirm', true); // server judges this one without its usual gap
    const verdict = judge(msg);
    if (verdict === true) setPill('ok', '✓ Đúng', msg.latency_ms);
    else if (verdict === false) setPill('bad', '✗ Chưa đúng', msg.latency_ms);
    else if (verdict === 'progress') setPill('looking', 'Đang làm…', msg.latency_ms);
    else if (verdict === 'pending') setPill('seen', 'Chưa xong', msg.latency_ms);
    else setPill('seen', 'Đã xem', msg.latency_ms);
  }

  // ---------- frame sending ----------
  function canSend() {
    return !capturing && phase === 'GUIDING' && sessionStarted && sock.isOpen && document.visibilityState === 'visible'
      && sock.bufferedAmount < MAX_BUFFERED && !finished;
  }

  function sendFrame(trigger, force = false) {
    if (!canSend()) return false;
    const now = performance.now();
    if (!force && now - lastSentAt < MIN_GAP_MS) return false;
    const image = captureFrame(video, { maxSide: 512, quality: 0.7 });
    if (!image) return false;
    if (!sock.send({ type: 'vision.frame', image, ts: Date.now(), trigger })) return false;
    lastSentAt = now;
    framesSent += 1;
    if (now - lastResultAt > RESULT_HOLD_MS && !aiDown) setPill('looking', trigger === 'still' ? 'Đang nhìn… (tay dừng)' : 'Đang nhìn…', pillLatency.textContent.replace(' ms', ''));
    return true;
  }

  function requestStillFrame() {
    const wait = MIN_GAP_MS - (performance.now() - lastSentAt);
    if (wait <= 0) { sendFrame('still'); return; }
    if (scheduledSend) return;
    scheduledSend = setTimeout(() => { scheduledSend = null; sendFrame('still'); }, wait);
  }
  scope.add(() => clearTimeout(scheduledSend));

  scope.interval(() => {
    if (performance.now() - lastSentAt >= PERIODIC_MS) sendFrame('interval');
  }, 250);

  // ---------- CV ----------
  function startCv() {
    if (!settings.cv || cv || !stream) return;
    cv = createStillnessDetector({
      video,
      overlay,
      drawOverlay: settings.handOverlay,
      onTrigger: () => requestStillFrame(),
      onStatus: (s) => { cvStatus = s; if (cvStatusEl) cvStatusEl.textContent = cvStatusText(); },
    });
    cv.start();
  }
  function stopCv() {
    cv?.stop();
    cv = null;
  }
  scope.add(stopCv);

  const cvStatusText = () => ({
    loading: 'Đang tải MediaPipe…', ready: 'Đang theo dõi tay', failed: 'Không tải được — chỉ gửi định kỳ', off: 'Tắt',
  }[cvStatus] || '');

  // ---------- settings sheet ----------
  function toggleRow(label, sub, key, onChange) {
    const input = h('input', { type: 'checkbox', class: 'switch-input' });
    input.checked = !!settings[key];
    input.addEventListener('change', () => {
      settings = updateSettings({ [key]: input.checked });
      onChange?.(input.checked);
    });
    return h('label', { class: 'toggle-row' },
      h('span', { class: 'toggle-text' }, h('span', { class: 'toggle-label' }, label), sub ? h('span', { class: 'toggle-sub' }, sub) : null),
      input, h('span', { class: 'switch', 'aria-hidden': 'true' }));
  }

  function openSettings() {
    cvStatusEl = h('span', null, cvStatusText());
    const body = h('div', { class: 'settings' },
      toggleRow('Nhận biết tay (MediaPipe)', cvStatusEl, 'cv', (on) => { if (on) startCv(); else stopCv(); }),
      toggleRow('Hiện khung tay', 'Vẽ điểm theo dõi bàn tay lên camera', 'handOverlay', (on) => cv?.setOverlay(on)),
      toggleRow('Đọc to hướng dẫn', ttsSupported() ? (hasVietnameseVoice() ? 'Giọng tiếng Việt' : 'Không có giọng tiếng Việt trên máy') : 'Trình duyệt không hỗ trợ', 'tts', (on) => { setMuted(!on); updateMuteBtn(); }),
      toggleRow('Nút debug ◀ ▶', 'Hoặc nhấn giữ khung hướng dẫn', 'debug', () => updateDebugBar()),
      toggleRow('Thu mẫu eval', 'Chụp ảnh gắn nhãn từng bước để đo độ chính xác AI. AI tạm ngừng tự chuyển bước — dùng ◀ ▶.', 'evalCapture', (on) => setCapturing(on)),
      h('div', { class: 'settings-stats' },
        h('span', null, `Đã gửi ${framesSent} khung hình`),
        h('span', null, `AI đã xem ${framesChecked}`)));
    openSheet(body, { title: 'Cài đặt', onClose: () => { cvStatusEl = null; } });
  }

  function updateMuteBtn() {
    muteBtn.classList.toggle('off', !settings.tts);
    muteBtn.setAttribute('aria-pressed', String(!settings.tts));
  }
  muteBtn.addEventListener('click', () => {
    settings = updateSettings({ tts: !settings.tts });
    setMuted(!settings.tts);
    updateMuteBtn();
    toast(settings.tts ? 'Đã bật giọng nói' : 'Đã tắt giọng nói', { kind: 'info', ms: 1400 });
  });

  // ---------- "Hỏi Shadow" ----------
  let holder = null;
  async function askQuestion(question) {
    const q = question.trim();
    if (!q) { showBubble('Mình chưa nghe rõ, bạn giữ nút và nói lại nhé.', 'SHADOW'); return; }
    showBubble(q, 'Bạn hỏi', 'user');
    micLabel.textContent = 'Shadow đang nghĩ…';
    micBtn.classList.add('thinking');
    try {
      const res = await api.ask({
        skill_id: skillId,
        step_id: currentStep?.id ?? null,
        question: q,
        image: captureFrame(video, { maxSide: 512, quality: 0.7 }),
      });
      if (scope.disposed) return;
      const answer = String(res?.answer || '').trim() || 'Mình chưa có câu trả lời.';
      const offline = res?.source === 'fallback';
      showBubble(answer, offline ? 'SHADOW · chưa kết nối AI' : 'SHADOW', 'answer');
      if (offline) toast('AI chưa trả lời được — đây là hướng dẫn có sẵn của người dạy.', { kind: 'info', ms: 4000 });
      queuedSpeech = null;
      speechSeq += 1;
      speak(answer);
    } catch (err) {
      if (!scope.disposed) errorToast(err, 'Shadow chưa trả lời được');
    } finally {
      if (!scope.disposed) {
        micBtn.classList.remove('thinking');
        micLabel.textContent = 'Giữ để hỏi Shadow';
      }
    }
  }

  function typeQuestion() {
    const input = h('input', { class: 'input', type: 'text', placeholder: 'VD: Cái chốt này vặn chiều nào?', enterkeyhint: 'send' });
    let api2;
    const form = h('form', { class: 'ask-form' }, input,
      h('button', { class: 'btn btn-primary', type: 'submit' }, 'Hỏi'));
    form.addEventListener('submit', (e) => { e.preventDefault(); const q = input.value; api2.close(); askQuestion(q); });
    api2 = openSheet(form, { title: 'Hỏi Shadow' });
    setTimeout(() => input.focus(), 300);
  }

  micBtn.addEventListener('contextmenu', (e) => e.preventDefault());
  micBtn.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    unlockTTS();
    if (micBtn.classList.contains('thinking')) return;
    if (!sttSupported()) { typeQuestion(); return; }
    cancelSpeech();
    asking = true;
    try { micBtn.setPointerCapture(e.pointerId); } catch { /* ignore */ }
    holder = holdToTalk({ onInterim: (t) => showBubble(t || '…', 'Bạn đang nói', 'user') });
    if (!holder) { asking = false; typeQuestion(); return; }
    micBtn.classList.add('listening');
    micLabel.textContent = 'Đang nghe… thả tay để gửi';
    showBubble('…', 'Bạn đang nói', 'user');
    navigator.vibrate?.(15);
  });
  const release = async () => {
    if (!holder) return;
    const hd = holder;
    holder = null;
    micBtn.classList.remove('listening');
    micLabel.textContent = 'Giữ để hỏi Shadow';
    try {
      const text = await hd.stop();
      asking = false;
      if (!scope.disposed) await askQuestion(text);
    } catch (err) {
      asking = false;
      errorToast(err, 'Không nghe được');
    }
  };
  micBtn.addEventListener('pointerup', release);
  micBtn.addEventListener('pointercancel', release);
  scope.add(() => { holder?.cancel(); holder = null; });

  // ---------- eval capture ----------
  function setCapturing(on) {
    if (capturing === on) return;
    capturing = on;
    el.classList.toggle('eval-on', on);
    evalBar.classList.toggle('hidden', !on);
    updateDebugBar();
    if (on) {
      clearTimeout(scheduledSend);
      scheduledSend = null;
      countsStepId = undefined;
      renderEvalBar();
      if (phase === 'GUIDING') setPill('seen', 'Đang thu mẫu');
      toast('Đang thu mẫu eval — AI tạm ngừng chấm', { kind: 'info', ms: 1800 });
    } else if (phase === 'GUIDING') {
      setPill('looking', 'Đang nhìn…');
      sendFrame('interval', true);
    }
  }

  const stepCounts = (stepId) => evalCounts?.[stepId] || { done: 0, not_done: 0 };
  const pendingFor = (stepId) => pendingByStep.get(stepId)?.size || 0;

  function applyCounts(counts, seq) {
    if (seq < countsApplied || !counts || typeof counts !== 'object') return;
    countsApplied = seq;
    evalCounts = counts;
  }

  function refreshCounts() {
    const seq = ++countsSeq;
    api.evalCounts(skillId).then((res) => {
      if (scope.disposed) return;
      applyCounts(res?.counts, seq);
      renderEvalBar();
    }).catch((err) => { if (!scope.disposed) errorToast(err, 'Không tải được số mẫu'); });
  }

  function renderEvalBar() {
    if (!capturing) return;
    const stepId = currentStep?.id ?? null;
    if (stepId !== countsStepId) {
      countsStepId = stepId;
      // a confirmation from the previous step must not linger on the new one
      clearTimeout(evalStatusTimer);
      evalStatusTimer = null;
      evalBar.classList.remove('flash-ok', 'flash-bad', 'flash-undo');
      refreshCounts();
    }
    const has = stepId !== null && stepId !== undefined && !finished;
    evalDoneBtn.disabled = !has;
    evalNotBtn.disabled = !has;
    if (!has) {
      evalStep.textContent = finished ? 'Đã hoàn thành' : 'Chưa có bước';
      if (!evalStatusTimer) evalSub.textContent = finished ? '' : 'Bấm “Bắt đầu” để vào bước 1';
      evalUndoBtn.disabled = true;
      return;
    }
    evalStep.textContent = stepLabel.textContent;
    const c = stepCounts(stepId);
    if (evalDoneCount.textContent !== String(c.done ?? 0) || evalNotCount.textContent !== String(c.not_done ?? 0)) {
      evalDoneCount.textContent = String(c.done ?? 0);
      evalNotCount.textContent = String(c.not_done ?? 0);
      evalCountsEl.classList.remove('pop');
      void evalCountsEl.offsetWidth;
      evalCountsEl.classList.add('pop');
    }
    const pending = pendingFor(stepId);
    evalUndoBtn.disabled = !((undoStacks.get(stepId)?.length || 0) + pending);
    if (!evalStatusTimer) {
      evalSub.textContent = pending ? `Đang lưu ${pending} ảnh…` : String(stepId);
      evalSub.classList.toggle('busy', !!pending);
    }
  }

  function flashEval(kind, text) {
    evalBar.classList.remove('flash-ok', 'flash-bad', 'flash-undo');
    void evalBar.offsetWidth;
    evalBar.classList.add(`flash-${kind}`);
    clearTimeout(evalStatusTimer);
    evalSub.classList.remove('busy');
    evalSub.textContent = text;
    evalStatusTimer = setTimeout(() => { evalStatusTimer = null; renderEvalBar(); }, 900);
  }
  scope.add(() => clearTimeout(evalStatusTimer));

  function captureSample(label) {
    const stepId = currentStep?.id;
    if (!capturing || stepId === undefined || stepId === null) return;
    const image = captureFrame(video, { maxSide: 512, quality: 0.7 });
    if (!image) { toast('Camera chưa sẵn sàng', { kind: 'error' }); return; }
    shutter.classList.remove('go');
    void shutter.offsetWidth;
    shutter.classList.add('go');
    navigator.vibrate?.(12);
    const seq = ++countsSeq;
    const set = pendingByStep.get(stepId) || new Set();
    pendingByStep.set(stepId, set);
    const p = api.evalCapture({ skill_id: skillId, step_id: stepId, label, image })
      .then((res) => {
        if (scope.disposed) return;
        applyCounts(res?.counts, seq);
        const stack = undoStacks.get(stepId) || [];
        stack.push(label);
        undoStacks.set(stepId, stack);
        if (currentStep?.id === stepId) flashEval(label === 'done' ? 'ok' : 'bad', label === 'done' ? 'Đã lưu ✓ Đã xong' : 'Đã lưu ✗ Chưa xong');
      })
      .catch((err) => { if (!scope.disposed) errorToast(err, 'Không lưu được mẫu'); })
      .finally(() => {
        set.delete(p);
        if (!scope.disposed) renderEvalBar();
      });
    set.add(p);
    renderEvalBar();
  }

  function undoCapture() {
    const stepId = currentStep?.id;
    if (!capturing || stepId === undefined || stepId === null) return;
    evalUndoBtn.disabled = true;
    // serialize undos, and let in-flight captures of this step land first so we delete the right file
    undoChain = undoChain.then(async () => {
      await Promise.allSettled([...(pendingByStep.get(stepId) || [])]);
      if (scope.disposed) return;
      const stack = undoStacks.get(stepId);
      const label = stack?.pop();
      if (!label) { renderEvalBar(); return; }
      const seq = ++countsSeq;
      try {
        const res = await api.evalUndo(skillId, stepId, label);
        if (scope.disposed) return;
        applyCounts(res?.counts, seq);
        if (res?.removed) {
          if (currentStep?.id === stepId) flashEval('undo', `Đã hoàn tác ${label === 'done' ? '✓' : '✗'}`);
        } else toast('Không còn ảnh để hoàn tác', { kind: 'info', ms: 1600 });
      } catch (err) {
        stack.push(label);
        if (!scope.disposed) errorToast(err, 'Không hoàn tác được');
      }
      if (!scope.disposed) renderEvalBar();
    });
  }
  renderEvalBar();

  // ---------- completion ----------
  function finish() {
    if (finished) return;
    finished = true;
    stopCv();
    renderEvalBar();
    setPill('ok', 'Hoàn thành');
    // the backend sends hud.state(COMPLETED) just before the completion speech, so collect the
    // stats when leaving (otherwise `spoken` is still false and Done talks over the AI's line)
    scope.timeout(() => { keepSpeech = true; saveDoneStats(); navigate('#/done'); }, 2600);
    el.classList.add('completed');
  }

  function saveDoneStats() {
    setDoneStats({
      skillId,
      skillName: skill?.display_name || skillLabel.textContent || '',
      learnedFrom: skill?.learned_from || null,
      durationMs: startedAt ? Date.now() - startedAt : 0,
      corrections,
      framesChecked: framesChecked || framesSent,
      framesSent,
      steps: currentStep?.total || skill?.steps?.length || 0,
      spoken: completeSpoken,
    });
  }

  scope.add(() => { if (!keepSpeech) cancelSpeech(); });

  return () => scope.dispose();
}
