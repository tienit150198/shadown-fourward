import { api, conversationEvents, fetchWithAccess } from '../api.js';
import { GeminiLiveSession } from '../gemini-live.js';
import { CameraTasks } from '../camera-tasks.js';
import { startSharedXmax } from '../xmax-camera.js';
import { itemImageUrl } from '../wardrobe-api.js';
import { fileToJpeg } from '../wardrobe-ui.js';
import { captureFrame, keepScreenAwake, startCamera, stopCamera } from '../camera.js';
import { recordVoice, recordingSupported, transcribeVoice, voiceAudio } from '../cloud-voice.js';
import { cancelSpeech, hasVietnameseVoice, holdToTalk, speak, sttSupported, unlockTTS } from '../speech.js';
import { h, icon } from '../ui.js';

const ACTION_ROUTES = new Set(['wardrobe', 'stylist', 'tryon-live', 'tryon-photo', 'teach', 'home', 'guide', 'image-studio', 'rules']);
const completedTurns = [];

function actionLink(action) {
  if (!ACTION_ROUTES.has(action.route)) return null;
  const id = action.id ? '/' + encodeURIComponent(action.id) : '';
  const query = new URLSearchParams({ from: 'conversation' });
  if (action.prompt) query.set('prompt', action.prompt);
  if (Array.isArray(action.items)) query.set('outfit', JSON.stringify(action.items));
  return h('a', { class: 'chat-action', href: `#/${action.route}${id}?${query}` }, action.label, icon('right', 16));
}

export function mount(el) {
  let disposed = false;
  let camera = null;
  let cameraRevision = 0;
  let openingCamera = null;
  let cameraFacing = 'environment';
  let releaseScreen = null;
  let request = null;
  let recognition = null;
  let listening = false;
  let restart = null;
  let noSpeech = 0;
  let cloud = false;
  let cloudMic = !sttSupported();
  let recording = null;
  let recordingTimer = null;
  let cloudTurn = null;
  let voiceRevision = 0;
  let audioContext = null;
  let audioSource = null;
  let liveAvailable = false;
  let livePreferred = true;
  let autoStartPending = true;
  let liveBubbles = {};
  let liveQuestion = '';
  let liveAnswer = '';
  let liveActions = [];
  let liveActionBox = null;
  const configRequest = new AbortController();

  const video = h('video', { class: 'chat-video', muted: true, playsinline: true, 'aria-label': 'Camera của bạn' });
  const status = h('p', { class: 'chat-status', role: 'status' }, 'Đang chuẩn bị camera và Live…');
  const startButton = h('button', { type: 'button', class: 'chat-start', onClick: toggleMic }, icon('mic', 20), 'Bắt đầu camera & trò chuyện');
  const prompt = h('div', { class: 'chat-hero', hidden: true }, icon('sparkle', 33), h('h1', null, 'Một camera, nói là làm'),
    h('p', null, 'Thử áo, trò chuyện, học hoặc dạy một kỹ năng.'), startButton);
  const cameraButton = h('button', {
    type: 'button', class: 'chat-toggle', 'aria-label': 'Bật camera', 'aria-pressed': 'false', onClick: toggleCamera,
  }, icon('camera', 20));
  const tryonView = h('div', { class: 'chat-xmax', hidden: true, 'aria-label': 'Video thử đồ Xmax X2.1' });
  const taskCard = h('section', { class: 'chat-task', 'aria-label': 'Tác vụ camera', 'aria-live': 'polite' });
  const flipButton = h('button', { type: 'button', class: 'chat-toggle chat-flip', 'aria-label': 'Đổi camera trước sau', onClick: async () => {
    tasks.cancel('Đã đổi camera. Bạn có thể nói yêu cầu tiếp theo.');
    closeCamera();
    cameraFacing = cameraFacing === 'environment' ? 'user' : 'environment';
    try { await openCamera(); } catch (error) { status.textContent = error.userMessage || error.message; }
  } }, icon('refresh', 20));
  const stage = h('div', { class: 'chat-stage' }, video, tryonView, prompt, taskCard, flipButton, cameraButton);
  const transcript = h('div', { id: 'shadow-transcript', class: 'chat-transcript', role: 'log', 'aria-label': 'Cuộc trò chuyện', tabindex: 0 },
    h('p', { class: 'chat-bubble chat-assistant' }, 'Chào bạn. Hôm nay bạn muốn mình giúp gì?'),
    completedTurns.flatMap(({ question, answer, actions }) => [
      h('p', { class: 'chat-bubble chat-user' }, question),
      h('p', { class: 'chat-bubble chat-assistant' }, answer),
      h('div', { class: 'chat-actions' }, actions.map(actionLink)),
    ]));
  const text = h('input', { class: 'chat-input', 'aria-label': 'Câu hỏi', placeholder: 'Hỏi Shadow…', maxlength: 500 });
  const mic = h('button', {
    type: 'button', class: 'chat-toggle', 'aria-label': 'Bật micro', 'aria-pressed': 'false', onClick: toggleMic,
  }, icon('mic', 20));
  mic.hidden = cloudMic;
  const composer = h('form', {
    class: 'chat-composer',
    onSubmit: (event) => {
      event.preventDefault();
      unlockTTS();
      const question = text.value.trim();
      if (!question) return;
      if (live.sendText(question)) { text.value = ''; return; }
      cancelRecording();
      text.value = '';
      void submit(question);
    },
  }, mic, text, h('button', { class: 'chat-send', type: 'submit', 'aria-label': 'Gửi câu hỏi' }, icon('up', 19)));
  const liveMode = h('button', {
    type: 'button', class: 'chat-live-mode', disabled: true, 'aria-pressed': 'false',
    'aria-label': 'Dùng Gemini Live', onClick: () => {
      autoStartPending = false;
      setListening(false);
      cancelRecording();
      request?.abort();
      request = null;
      stopOutput();
      live.stop();
      livePreferred = !livePreferred;
      intentBadge.hidden = true;
      liveMode.setAttribute('aria-pressed', String(livePreferred));
      status.textContent = livePreferred ? 'Bấm micro để trò chuyện trực tiếp.' : 'Đã chọn chế độ hỏi từng câu.';
    },
  }, 'Live');
  composer.prepend(liveMode);
  const intentBadge = h('div', { class: 'chat-intent', hidden: true, role: 'status', 'aria-live': 'polite' });
  const transcriptToggle = h('button', {
    type: 'button', class: 'chat-history-toggle', 'aria-expanded': 'false', 'aria-controls': 'shadow-transcript',
    onClick: () => {
      const expanded = pane.classList.toggle('is-expanded');
      transcriptToggle.setAttribute('aria-expanded', String(expanded));
      transcriptToggle.replaceChildren(expanded ? 'Thu gọn' : 'Mở rộng', icon(expanded ? 'down' : 'up', 14));
      transcript.scrollTop = transcript.scrollHeight;
    },
  }, 'Mở rộng', icon('up', 14));
  const pane = h('div', { class: 'chat-pane' },
    h('div', { class: 'chat-pane-head' }, transcriptToggle),
    h('div', { class: 'chat-routing' }, intentBadge), transcript, status, composer);
  const audioUnlock = h('button', {
    type: 'button', class: 'chat-audio-unlock', hidden: true,
    onClick: () => { live.resumeAudio(); void video.play().catch(() => {}); },
  }, icon('speaker', 17), 'Chạm để bật âm thanh');
  pane.append(audioUnlock);
  el.append(
    h('header', { class: 'chat-nav' },
      h('a', { class: 'chat-brand', href: '#/conversation', 'aria-current': 'page' }, 'Shadow'),
      h('nav', { 'aria-label': 'Tài nguyên đã lưu' },
        h('a', { href: '#/wardrobe?from=conversation' }, 'Tủ đồ'),
        h('a', { href: '#/home?from=conversation' }, 'Bài đã học'))),
    h('div', { class: 'chat-layout' }, stage, pane),
  );
  transcript.scrollTop = transcript.scrollHeight;
  // Keep the overlay above mobile keyboards and leave room for task cards.
  function fitViewport() {
    const viewport = window.visualViewport;
    el.style.setProperty('--chat-view-height', `${Math.round(viewport?.height || window.innerHeight)}px`);
    el.style.setProperty('--chat-view-top', `${Math.round(viewport?.offsetTop || 0)}px`);
    transcript.scrollTop = transcript.scrollHeight;
  }
  const paneObserver = new ResizeObserver(() => {
    el.style.setProperty('--chat-shelf-height', `${Math.ceil(pane.getBoundingClientRect().height)}px`);
  });
  paneObserver.observe(pane);
  window.visualViewport?.addEventListener('resize', fitViewport);
  window.visualViewport?.addEventListener('scroll', fitViewport);
  window.addEventListener('resize', fitViewport);
  fitViewport();
  let garmentQuery = '', garmentCategory = '', garmentLimit = 24;
  const tasks = new CameraTasks({
    openCamera,
    getFrame: () => camera ? captureFrame(video, { maxSide: 512 }) : null,
    getStream: () => camera,
    startTryon: (itemId, stream, signal, onState) => startSharedXmax(itemId, stream, tryonView, { signal, onState, onError: message => {
      tasks.stopAr(); tasks.update({ status: 'error', message });
    } }),
    render: renderTask,
    notify(execution) {
      if (disposed) return;
      if (live.status === 'idle') {
        transcript.append(h('p', { class: 'chat-bubble chat-assistant' }, execution.message));
        transcript.scrollTop = transcript.scrollHeight;
      } else live.notifyTaskResult(execution);
    },
  });
  renderTask(tasks.state);

  function renderTask(state) {
    if (disposed) return;
    if (state.mode === 'teaching' && taskCard.contains(document.activeElement) && document.activeElement.matches('input')) {
      const stats = taskCard.querySelector('.chat-task-stats');
      if (stats) stats.textContent = `${state.lines || 0} câu hướng dẫn · ${state.frames || 0} khung hình`;
      return;
    }
    taskCard.dataset.mode = state.mode;
    taskCard.dataset.status = state.status;
    taskCard.replaceChildren(h('span', { class: 'chat-task-label' }, ({ chat: 'SHADOW', guiding: 'HƯỚNG DẪN', teaching: 'ĐANG GHI BÀI DẠY', compiling: 'ĐANG BIÊN SOẠN', tryon: 'XMAX X2.1' })[state.mode]),
      h('strong', null, state.title), h('p', null, state.message));
    if (state.mode === 'teaching') {
      const title = h('input', { class: 'chat-task-title', value: state.title, maxlength: 160, 'aria-label': 'Tên bài dạy', onInput: event => tasks.rename(event.target.value) });
      taskCard.append(title, h('small', { class: 'chat-task-stats' }, `${state.lines || 0} câu hướng dẫn · ${state.frames || 0} khung hình`),
        h('button', { type: 'button', class: 'chat-action', onClick: () => {
          const execution = tasks.finish();
          status.textContent = execution.message;
          live.notifyTaskResult(execution);
        } }, 'Lưu bài dạy'));
    }
    if (state.lesson?.steps?.length) {
      const steps = state.lesson.steps;
      const index = Math.max(0, Math.min(state.step || 0, steps.length - 1));
      taskCard.append(h('div', { class: 'chat-task-step' }, h('small', null, `Bước ${index + 1}/${steps.length}`),
        h('p', null, steps[index].instruction || steps[index].on_enter_speech),
        h('div', { class: 'chat-task-controls' },
          h('button', { type: 'button', class: 'chat-action', disabled: index === 0, onClick: () => tasks.update({ step: index - 1 }) }, 'Trước'),
          h('button', { type: 'button', class: 'chat-action', disabled: index === steps.length - 1, onClick: () => tasks.update({ step: index + 1 }) }, 'Tiếp'))));
    }
    const showGarments = state.status !== 'live' || state.showGarments;
    if (state.mode === 'tryon' && ['selecting', 'live', 'error'].includes(state.status) && showGarments && state.items?.length) {
      const selected = (state.selected_ids || []).map(id => state.items.find(item => item.id === id)).filter(Boolean);
      taskCard.append(h('div', { class: 'chat-task-controls', 'aria-label': 'Bộ đồ đã chọn' }, selected.map(item =>
        h('button', { type: 'button', class: 'chat-action', onClick: () => tasks.removeGarment(item.id), 'aria-label': `Bỏ ${item.name}` }, `${item.name} ×`))),
        h('button', { type: 'button', class: 'chat-action', disabled: !selected.length, onClick: () => tasks.applyOutfit() }, `Mặc thử bộ đã chọn (${selected.length})`));
      const grid = h('div', { class: 'chat-garments', 'aria-label': 'Chọn trang phục muốn thử' });
      const count = h('small');
      const more = h('button', { type: 'button', class: 'chat-action', onClick: () => { garmentLimit += 24; fillGarments(); } }, 'Xem thêm trang phục');
      const fold = value => String(value).normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/đ/g, 'd').toLowerCase();
      function fillGarments() {
        const words = fold(garmentQuery).split(/\s+/).filter(Boolean);
        const matches = state.items.filter(item => (!garmentCategory || item.category === garmentCategory) && words.every(word => fold(`${item.name} ${item.brand || ''} ${(item.colors || []).join(' ')} ${(item.seasons || []).join(' ')}`).includes(word)));
        count.textContent = `${matches.length} món trong tủ đồ SHADOW`;
        grid.replaceChildren(...matches.slice(0, garmentLimit).map(item =>
          h('button', { type: 'button', class: 'chat-garment', 'aria-pressed': String((state.selected_ids || []).includes(item.id)), onClick: () => tasks.selectGarment(item.id) },
            h('img', { src: itemImageUrl(item), alt: '', loading: 'lazy' }), h('span', null, item.name || item.id))));
        more.hidden = matches.length <= garmentLimit;
      }
      taskCard.append(h('div', { class: 'chat-task-controls' },
        h('input', { type: 'search', class: 'chat-task-title', placeholder: 'Tìm áo len, quần, UNIQLO…', 'aria-label': 'Tìm trong tủ đồ SHADOW', value: garmentQuery, onInput: event => { garmentQuery = event.target.value; garmentLimit = 24; fillGarments(); } }),
        h('select', { 'aria-label': 'Loại trang phục', onChange: event => { garmentCategory = event.target.value; garmentLimit = 24; fillGarments(); } },
          [['', 'Tất cả'], ['top', 'Áo'], ['bottom', 'Quần'], ['outerwear', 'Áo khoác'], ['dress', 'Váy']].map(([value, label]) => h('option', { value, selected: value === garmentCategory }, label)))), count, grid, more);
      fillGarments();
    }
    if (state.mode === 'tryon' && state.status === 'live') taskCard.append(h('button', { type: 'button', class: 'chat-action', onClick: () => tasks.update({ showGarments: !state.showGarments }) }, state.showGarments ? 'Ẩn trang phục' : 'Đổi trang phục'));
    if (state.mode === 'tryon' && ['selecting', 'live', 'error'].includes(state.status) && showGarments) {
      const upload = h('input', { type: 'file', accept: 'image/*', hidden: true, 'aria-label': 'Ảnh trang phục muốn mặc thử', onChange: async event => {
        const file = event.target.files?.[0];
        if (!file) return;
        try {
          const image = await fileToJpeg(file);
          if (!disposed) await tasks.addGarment(image);
        } catch (error) { if (!disposed) status.textContent = error.userMessage || error.message; }
      } });
      taskCard.append(upload, h('button', { type: 'button', class: 'chat-action', onClick: () => upload.click() }, 'Thêm ảnh trang phục vào bộ'));
    }
    if (state.status === 'unavailable') taskCard.append(h('a', { class: 'chat-action', href: 'https://platform.xmax.ai/api-keys', target: '_blank', rel: 'noopener noreferrer' }, 'Tạo API key Xmax'));
    if (['teaching', 'compiling', 'tryon', 'guiding'].includes(state.mode)) taskCard.append(
      h('button', { type: 'button', class: 'chat-task-cancel', onClick: () => {
        tasks.cancel(); live.notifyTaskResult(tasks.execution());
      } }, 'Dừng tác vụ'));
  }
  const live = new GeminiLiveSession({
    onTurnStart() {
      liveBubbles = {};
      liveQuestion = liveAnswer = '';
      liveActions = [];
      liveActionBox = null;
    },
    onTranscript(role, value) {
      if (disposed) return;
      if (!liveBubbles[role]) {
        liveBubbles[role] = h('p', { class: `chat-bubble ${role === 'user' ? 'chat-user' : 'chat-assistant'}` });
        transcript.append(liveBubbles[role]);
      }
      liveBubbles[role].textContent += value;
      if (role === 'user') liveQuestion += value;
      else liveAnswer += value;
      transcript.scrollTop = transcript.scrollHeight;
    },
    async onClassification(result, turn) {
      if (disposed) return;
      intentBadge.hidden = false;
      intentBadge.dataset.intent = result.intent;
      intentBadge.replaceChildren(h('strong', null, result.confidence < .5 ? 'Chưa rõ yêu cầu' : result.label),
        h('span', null, `${new Intl.NumberFormat('vi-VN', { style: 'percent', maximumFractionDigits: 1 }).format(result.confidence)} · Decisions`));
      liveActions = (result.actions || []).filter(action => !['teach', 'tryon-live', 'guide'].includes(action.route));
      liveActionBox?.remove();
      liveActionBox = h('div', { class: 'chat-actions' }, liveActions.map(actionLink));
      transcript.append(liveActionBox);
      transcript.scrollTop = transcript.scrollHeight;
      try { return await tasks.handle(result, turn); }
      catch (error) {
        if (turn.signal.aborted || disposed) return null;
        tasks.update({ status: 'error', message: error.userMessage || error.message || 'Chưa thực hiện được tác vụ camera.' });
        return tasks.execution();
      }
    },
    onTurnComplete() {
      if (disposed || !liveQuestion || !liveAnswer) return;
      completedTurns.push({ question: liveQuestion.slice(0, 500), answer: liveAnswer.slice(0, 500), actions: liveActions });
      if (completedTurns.length > 12) completedTurns.shift();
    },
    onState(value, detail) {
      if (disposed) return;
      const active = value !== 'idle';
      startButton.disabled = active;
      prompt.hidden = active || Boolean(camera);
      pane.dataset.live = value;
      if (value === 'idle' && ['teaching', 'tryon'].includes(tasks.state.mode)) tasks.cancel('Phiên Live đã dừng. Bản ghi chưa lưu đã được hủy.');
      mic.setAttribute('aria-pressed', String(active));
      mic.setAttribute('aria-label', active ? 'Dừng trò chuyện Live' : 'Bật micro');
      stage.classList.toggle('chat-live-active', active);
      stage.classList.toggle('chat-live-speaking', value === 'speaking');
      liveMode.disabled = value === 'connecting' || !liveAvailable;
      status.textContent = detail || ({
        idle: 'Bấm micro để bắt đầu phiên Live mới.', connecting: 'Đang mở micro và kết nối Gemini Live…',
        listening: 'Đang nghe. Nói tự nhiên rồi ngừng một nhịp để Shadow trả lời.',
        classifying: 'Decisions đang phân loại yêu cầu…', thinking: 'Đã phân loại. Shadow đang chuẩn bị trả lời…',
        speaking: 'Shadow đang nói. Bạn có thể nói chen để hỏi tiếp.',
      })[value];
    },
    onError(error) { if (!disposed) status.textContent = error.userMessage || error.message; },
    onNotice(message) { if (!disposed) status.textContent = message; },
    onAudioBlocked(blocked) { if (!disposed) audioUnlock.hidden = !blocked; },
  }, {
    fetchImpl: fetchWithAccess,
    getFrame: () => tasks.ar?.getFrame?.() || (camera ? captureFrame(video, { maxSide: 448 }) : null),
    getTaskContext: () => tasks.context(),
    getHistory: () => completedTurns.slice(-3).flatMap(({ question, answer }) => [
      { role: 'user', text: question }, { role: 'assistant', text: answer },
    ]),
  });
  function updateMicrophoneControl() {
    mic.hidden = !(liveAvailable || sttSupported() || (cloud && recordingSupported()));
  }
  api.voiceConfig({ signal: configRequest.signal }).then(config => {
    if (disposed) return;
    cloud = Boolean(config.available);
    updateMicrophoneControl();
  }).catch(() => {});
  api.liveConfig({ signal: configRequest.signal }).then(config => {
    if (disposed) return;
    liveAvailable = Boolean(config.available);
    liveMode.disabled = !liveAvailable;
    liveMode.setAttribute('aria-pressed', String(liveAvailable && livePreferred));
    updateMicrophoneControl();
    if (liveAvailable) autoStartLive();
    else {
      prompt.hidden = false;
      status.textContent = 'Live chưa được cấu hình. Bạn vẫn có thể nhập câu hỏi hoặc bật micro.';
    }
  }).catch(() => {
    if (disposed) return;
    prompt.hidden = false;
    status.textContent = 'Chưa kết nối được Live. Bấm micro để thử lại hoặc nhập câu hỏi.';
  });

  function autoStartLive() {
    if (!autoStartPending || !liveAvailable || !livePreferred || disposed || document.hidden) return;
    autoStartPending = false;
    startLive();
  }

  function startLive() {
    if (disposed || live.status !== 'idle') return;
    setListening(false);
    cancelRecording();
    request?.abort();
    request = null;
    stopOutput();
    intentBadge.hidden = true;
    void live.start();
    void openCamera().catch(error => { if (!disposed) status.textContent = error.userMessage || error.message; });
  }

  function unlockLiveAudio() { if (live.status !== 'idle') live.resumeAudio(); }
  el.addEventListener('pointerdown', unlockLiveAudio);
  el.addEventListener('keydown', unlockLiveAudio);

  function stopOutput() {
    voiceRevision += 1;
    cancelSpeech();
    try { audioSource?.stop(); } catch { /* source already ended */ }
    audioSource = null;
  }

  function unlockAudio() {
    unlockTTS();
    const Context = window.AudioContext || window.webkitAudioContext;
    if (Context) {
      try {
        audioContext ??= new Context();
        audioContext.resume().catch(() => {});
      } catch { /* browser speech or text are still available */ }
    }
  }

  async function playVoice(line, signal, revision) {
    if (!cloud || !audioContext || hasVietnameseVoice()) {
      await speak(line);
      return;
    }
    const raw = await voiceAudio(line.slice(0, 500), signal);
    const buffer = await audioContext.decodeAudioData(raw);
    if (signal.aborted || revision !== voiceRevision || audioContext.state !== 'running') return;
    const source = audioContext.createBufferSource();
    audioSource = source;
    source.buffer = buffer;
    source.connect(audioContext.destination);
    await new Promise((resolve) => {
      source.onended = resolve;
      source.start();
    });
    if (audioSource === source) audioSource = null;
  }

  async function toggleCamera() {
    if (camera) {
      tasks.cancel('Camera đã tắt. Bạn vẫn có thể trò chuyện.');
      closeCamera();
      status.textContent = 'Camera đã tắt.';
      return;
    }
    try {
      await openCamera();
      status.textContent = live.status !== 'idle'
        ? 'Camera đang mở. Shadow nhận hình ảnh trong lúc bạn trò chuyện Live.'
        : 'Camera đang mở. Bạn có thể bật micro hoặc nhập câu hỏi.';
    }
    catch (error) { if (!disposed) status.textContent = error.userMessage || 'Chưa mở được camera.'; }
  }

  async function openCamera() {
    if (camera) return camera;
    if (openingCamera) return openingCamera;
    openingCamera = openCameraStream();
    try { return await openingCamera; } finally { openingCamera = null; }
  }

  async function openCameraStream() {
    const revision = ++cameraRevision;
    cameraButton.disabled = true;
    try {
      const stream = await startCamera(video, { facingMode: cameraFacing });
      if (disposed || revision !== cameraRevision) {
        stopCamera(stream, video);
        throw new Error('Đã hủy mở camera.');
      }
      camera = stream;
      releaseScreen = keepScreenAwake();
      stage.classList.add('chat-camera-on');
      cameraButton.setAttribute('aria-label', 'Tắt camera');
      cameraButton.setAttribute('aria-pressed', 'true');
      return stream;
    } finally {
      cameraButton.disabled = false;
    }
  }

  function closeCamera() {
    cameraRevision += 1;
    stopCamera(camera, video);
    camera = null;
    releaseScreen?.();
    releaseScreen = null;
    stage.classList.remove('chat-camera-on');
    cameraButton.setAttribute('aria-pressed', 'false');
    cameraButton.setAttribute('aria-label', 'Bật camera');
  }

  function stopRecognition() {
    clearTimeout(restart);
    const current = recognition;
    recognition = null;
    current?.cancel();
  }

  function setListening(value) {
    listening = value;
    mic.setAttribute('aria-pressed', String(value));
    mic.setAttribute('aria-label', value ? 'Tắt micro' : 'Bật micro');
    if (!value) stopRecognition();
  }

  function toggleMic() {
    autoStartPending = false;
    if (liveAvailable && livePreferred) {
      if (live.status !== 'idle') { live.stop(); return; }
      startLive();
      return;
    }
    unlockAudio();
    if (cloudMic) {
      if (recording) void finishRecording();
      else void startRecording();
      return;
    }
    if (listening) {
      setListening(false);
      stopOutput();
      status.textContent = 'Micro đã tắt.';
      return;
    }
    request?.abort();
    request = null;
    stopOutput();
    noSpeech = 0;
    setListening(true);
    listen();
  }

  async function startRecording() {
    if (!cloud || cloudTurn || disposed) return;
    request?.abort();
    stopOutput();
    const controller = new AbortController();
    cloudTurn = controller;
    mic.disabled = true;
    try {
      const capture = await recordVoice();
      if (controller.signal.aborted || disposed) {
        capture.cancel();
        return;
      }
      recording = capture;
      mic.setAttribute('aria-pressed', 'true');
      mic.setAttribute('aria-label', 'Gửi đoạn ghi âm');
      status.textContent = 'Đang ghi âm. Bấm micro lần nữa để gửi.';
      recordingTimer = setTimeout(finishRecording, 15000);
    } catch {
      if (!controller.signal.aborted) status.textContent = 'Chưa mở được micro. Bạn có thể nhập câu hỏi.';
      if (cloudTurn === controller) cloudTurn = null;
    } finally {
      mic.disabled = false;
    }
  }

  async function finishRecording() {
    const capture = recording;
    const controller = cloudTurn;
    if (!capture || !controller) return;
    recording = null;
    clearTimeout(recordingTimer);
    mic.disabled = true;
    mic.setAttribute('aria-pressed', 'false');
    mic.setAttribute('aria-label', 'Bật micro');
    status.textContent = 'Đang nhận giọng nói…';
    try {
      const blob = await capture.stop();
      const question = blob?.size ? await transcribeVoice(blob, controller.signal) : '';
      if (controller.signal.aborted || disposed) return;
      if (question) void submit(question, { spoken: true });
      else status.textContent = 'Chưa nghe rõ. Bật micro để thử lại.';
    } catch (error) {
      if (!controller.signal.aborted) status.textContent = error.userMessage || 'Chưa nhận được giọng nói.';
    } finally {
      if (cloudTurn === controller) cloudTurn = null;
      mic.disabled = false;
    }
  }

  function cancelRecording() {
    if (!cloudTurn) return;
    clearTimeout(recordingTimer);
    recording?.cancel();
    recording = null;
    cloudTurn?.abort();
    cloudTurn = null;
    mic.disabled = false;
    mic.setAttribute('aria-pressed', 'false');
    mic.setAttribute('aria-label', 'Bật micro');
  }

  function listen() {
    if (!listening || disposed || recognition || document.hidden) return;
    const current = holdToTalk({
      continuous: false,
      onInterim: (value) => { if (value && recognition === current) status.textContent = value; },
      onEnd: async () => {
        if (recognition !== current) return;
        recognition = null;
        try {
          const question = await current.stop();
          if (disposed || !listening || request) return;
          if (question) {
            noSpeech = 0;
            void submit(question);
          } else if (++noSpeech < 3) {
            restart = setTimeout(listen, 220);
          } else {
            setListening(false);
            status.textContent = 'Chưa nghe rõ. Bật micro để nói tiếp.';
          }
        } catch (error) {
          setListening(false);
          if (cloud && recordingSupported() && ['network', 'service-not-allowed', 'language-not-supported'].includes(error.message)) {
            cloudMic = true;
            status.textContent = 'Bấm micro để ghi âm và gửi.';
          } else {
            status.textContent = error.userMessage || 'Chưa dùng được micro.';
          }
        }
      },
    });
    if (!current) {
      setListening(false);
      status.textContent = 'Chưa dùng được micro. Bạn có thể nhập câu hỏi.';
      return;
    }
    recognition = current;
    status.textContent = 'Đang nghe…';
  }

  async function submit(question, { spoken = false } = {}) {
    intentBadge.hidden = true;
    stopRecognition();
    request?.abort();
    stopOutput();
    const current = new AbortController();
    request = current;
    const context = completedTurns.slice(-3).flatMap(({ question: q, answer: a }) => [
      { role: 'user', text: q }, { role: 'assistant', text: a },
    ]);
    const image = camera ? captureFrame(video, { maxSide: 448 }) : null;
    const bubble = h('p', { class: 'chat-bubble chat-assistant', hidden: true });
    const actions = h('div', { class: 'chat-actions' });
    transcript.append(h('p', { class: 'chat-bubble chat-user' }, question), bubble, actions);
    transcript.scrollTop = transcript.scrollHeight;
    status.textContent = 'Shadow đang trả lời…';
    let answer = '';
    const turnActions = [];
    let unspoken = '';
    let audio = Promise.resolve();
    let done = false;
    const voiceEnabled = spoken || listening;
    const revision = voiceRevision;
    const streamVoice = voiceEnabled && (!cloud || hasVietnameseVoice());

    function queueSpeech(line) {
      if (line.trim() && voiceEnabled) {
        audio = audio.then(() => {
          if (current.signal.aborted || revision !== voiceRevision) return;
          return playVoice(line, current.signal, revision);
        }).catch(() => {});
      }
    }

    try {
      for await (const item of conversationEvents({ text: question.slice(0, 500), history: context, image }, current.signal)) {
        if (current.signal.aborted || disposed) return;
        if (item.type === 'text') {
          answer += item.text;
          bubble.textContent = answer;
          bubble.hidden = false;
          unspoken += item.text;
          const sentence = unspoken.match(/^.*?[.!?…](?:\s+|$)/s);
          if (streamVoice && sentence && sentence[0].trim().length >= 12) {
            queueSpeech(sentence[0]);
            unspoken = unspoken.slice(sentence[0].length);
          }
        } else if (item.type === 'action') {
          const link = actionLink(item);
          if (link) {
            actions.append(link);
            turnActions.push(item);
          }
        } else if (item.type === 'error') {
          throw new Error(item.text);
        } else if (item.type === 'done') {
          done = true;
        }
        transcript.scrollTop = transcript.scrollHeight;
      }
      if (!done) throw new Error('Kết nối bị gián đoạn. Bạn thử lại nhé.');
      queueSpeech(unspoken);
      if (answer) {
        completedTurns.push({ question: question.slice(0, 500), answer: answer.slice(0, 500), actions: turnActions });
        if (completedTurns.length > 12) completedTurns.shift();
      }
      status.textContent = ' ';
      await audio;
    } catch (error) {
      if (!current.signal.aborted) status.textContent = error.userMessage || error.message || 'Chưa kết nối được với Shadow.';
    } finally {
      if (request === current) {
        request = null;
        if (listening && done) restart = setTimeout(listen, 180);
        else if (!done) setListening(false);
      }
    }
  }

  function onVisibility() {
    if (!document.hidden) { autoStartLive(); return; }
    live.stop();
    tasks.cancel('Đã dừng tác vụ khi đưa ứng dụng vào nền.');
    setListening(false);
    cancelRecording();
    stopOutput();
    closeCamera();
  }
  document.addEventListener('visibilitychange', onVisibility);
  return () => {
    disposed = true;
    live.stop();
    tasks.cancel();
    configRequest.abort();
    setListening(false);
    cancelRecording();
    request?.abort();
    stopOutput();
    audioContext?.close().catch(() => {});
    closeCamera();
    paneObserver.disconnect();
    window.visualViewport?.removeEventListener('resize', fitViewport);
    window.visualViewport?.removeEventListener('scroll', fitViewport);
    window.removeEventListener('resize', fitViewport);
    el.removeEventListener('pointerdown', unlockLiveAudio);
    el.removeEventListener('keydown', unlockLiveAudio);
    document.removeEventListener('visibilitychange', onVisibility);
  };
}
