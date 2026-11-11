import { api, conversationEvents } from '../api.js';
import { captureFrame, keepScreenAwake, startCamera, stopCamera } from '../camera.js';
import { recordVoice, recordingSupported, transcribeVoice, voiceAudio } from '../cloud-voice.js';
import { cancelSpeech, hasVietnameseVoice, holdToTalk, speak, sttSupported, unlockTTS } from '../speech.js';
import { h, icon } from '../ui.js';

const ACTION_ROUTES = new Set(['wardrobe', 'stylist', 'tryon-live', 'tryon-photo', 'teach', 'home', 'guide', 'image-studio']);
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
  const configRequest = new AbortController();

  const video = h('video', { class: 'chat-video', muted: true, playsinline: true, 'aria-label': 'Camera của bạn' });
  const status = h('p', { class: 'chat-status', role: 'status' }, 'Bạn có thể nói hoặc nhập câu hỏi.');
  const prompt = h('div', { class: 'chat-hero' }, icon('sparkle', 33), h('h1', null, 'Trò chuyện với Shadow'));
  const cameraButton = h('button', {
    type: 'button', class: 'chat-toggle', 'aria-label': 'Bật camera', 'aria-pressed': 'false', onClick: toggleCamera,
  }, icon('camera', 20));
  const stage = h('div', { class: 'chat-stage' }, video, prompt, cameraButton);
  const transcript = h('div', { class: 'chat-transcript', role: 'log', 'aria-label': 'Cuộc trò chuyện' },
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
      cancelRecording();
      text.value = '';
      void submit(question);
    },
  }, mic, text, h('button', { class: 'chat-send', type: 'submit', 'aria-label': 'Gửi câu hỏi' }, icon('up', 19)));
  el.append(
    h('header', { class: 'chat-nav' },
      h('a', { class: 'chat-brand', href: '#/conversation', 'aria-current': 'page' }, 'Shadow'),
      h('nav', { 'aria-label': 'Điều hướng' },
        h('a', { href: '#/wardrobe?from=conversation' }, 'Tủ đồ'),
        h('a', { href: '#/teach?from=conversation' }, 'Dạy kỹ năng'))),
    h('div', { class: 'chat-layout' }, stage, h('div', { class: 'chat-pane' }, transcript, status, composer)),
  );
  transcript.scrollTop = transcript.scrollHeight;
  api.voiceConfig({ signal: configRequest.signal }).then((config) => {
    if (disposed) return;
    cloud = config.available;
    if (cloudMic && cloud && recordingSupported()) mic.hidden = false;
  }).catch(() => {});

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
      closeCamera();
      status.textContent = 'Camera đã tắt.';
      return;
    }
    const revision = ++cameraRevision;
    cameraButton.disabled = true;
    try {
      const stream = await startCamera(video);
      if (disposed || revision !== cameraRevision) {
        stopCamera(stream, video);
        return;
      }
      camera = stream;
      releaseScreen = keepScreenAwake();
      stage.classList.add('chat-camera-on');
      cameraButton.setAttribute('aria-label', 'Tắt camera');
      cameraButton.setAttribute('aria-pressed', 'true');
      status.textContent = 'Camera đang mở. Shadow nhận một khung hình mỗi khi bạn gửi câu hỏi.';
    } catch (error) {
      if (!disposed) status.textContent = error.userMessage || 'Chưa mở được camera.';
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
    if (!document.hidden) return;
    setListening(false);
    cancelRecording();
    stopOutput();
    closeCamera();
  }
  document.addEventListener('visibilitychange', onVisibility);
  return () => {
    disposed = true;
    configRequest.abort();
    setListening(false);
    cancelRecording();
    request?.abort();
    stopOutput();
    audioContext?.close().catch(() => {});
    closeCamera();
    document.removeEventListener('visibilitychange', onVisibility);
  };
}
