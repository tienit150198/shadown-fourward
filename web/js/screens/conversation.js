import { api, conversationEvents } from '../api.js';
import { captureFrame, keepScreenAwake, startCamera, stopCamera } from '../camera.js';
import { recordVoice, recordingSupported, transcribeVoice, voiceAudio } from '../cloud-voice.js';
import { cancelSpeech, hasVietnameseVoice, holdToTalk, speak, sttSupported, unlockTTS } from '../speech.js';
import { h, icon } from '../ui.js';
import { getSettings } from '../store.js';

const ACTION_ROUTES = new Set(['wardrobe', 'stylist', 'tryon-live', 'tryon-photo', 'teach', 'home', 'guide', 'image-studio']);
const SESSION_KEY = 'shadow:conversation';
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const shortText = (value, max) => typeof value === 'string' ? value.slice(0, max).trim() : '';
function normalizeOutfits(value) {
  const seen = new Set();
  return (Array.isArray(value) ? value : []).flatMap((outfit) => {
    if (!outfit || typeof outfit !== 'object') return [];
    const item_ids = [...new Set((Array.isArray(outfit.item_ids) ? outfit.item_ids : []).map((id) => shortText(id, 120)).filter(Boolean))].slice(0, 20);
    const key = JSON.stringify(item_ids);
    if (!item_ids.length || seen.has(key)) return [];
    seen.add(key);
    const items = (Array.isArray(outfit.items) ? outfit.items : []).filter((item) => item && item_ids.includes(item.id)).slice(0, 20).map((item) => {
      const image = shortText(item.image_url, 2000);
      return { id: shortText(item.id, 120), name: shortText(item.name, 160), category: shortText(item.category, 80),
        image_url: /^(https?:\/\/|\/(?!\/))/.test(image) ? image : '' };
    });
    return [{ title: shortText(outfit.title, 160), reason: shortText(outfit.reason, 1000), tips: shortText(outfit.tips, 500), item_ids, items }];
  }).slice(0, 3);
}
function normalizeActivities(value) {
  return (Array.isArray(value) ? value : []).filter((entry) => entry && ['done', 'error'].includes(entry.state) && typeof entry.label === 'string' && !['Đã chuẩn bị lựa chọn', 'Đang chuẩn bị lựa chọn…'].includes(entry.label))
    .slice(0, 30).map((entry) => ({ state: entry.state, label: shortText(entry.label, 120) }));
}
function activityRow(entry) {
  return h('div', { class: 'chat-activity', dataset: { state: entry.state } },
    h('span', { class: 'chat-activity-symbol', 'aria-hidden': 'true' }, entry.state === 'done' ? '✓' : entry.state === 'error' ? '!' : ''),
    h('span', null, entry.label), h('span', { class: 'chat-loading-text' }, entry.state === 'running' ? ' — đang thực hiện' : entry.state === 'done' ? ' — hoàn tất' : ' — chưa hoàn tất'));
}
function normalizeTimeline(value) {
  const blocks = (Array.isArray(value) ? value : []).slice(0, 80);
  const cardSelections = new Set(blocks.filter(b => b?.type === 'outfits').flatMap(b => normalizeOutfits(b.outfits)).map(o => JSON.stringify(o.item_ids)));
  return blocks.flatMap((block) => {
    if (!block || typeof block !== 'object') return [];
    if (block.type === 'text') return [{ type: 'text', text: shortText(block.text, 4000) }];
    if (block.type === 'tool_status') {
      const entry = normalizeActivities([block])[0];
      return entry ? [{ type: 'tool_status', ...entry }] : [];
    }
    if (block.type === 'outfits') return [{ type: 'outfits', outfits: normalizeOutfits(block.outfits) }];
    if (block.type === 'action' && ['wardrobe', 'stylist', 'tryon-live', 'tryon-photo', 'teach', 'home', 'guide', 'image-studio'].includes(block.action?.route)) {
      const action = block.action;
      if (['tryon-photo', 'tryon-live'].includes(action.route) && cardSelections.has(JSON.stringify(action.items))) return [];
      return [{ type: 'action', action: { route: action.route, label: shortText(action.label, 120),
        id: shortText(action.id, 120), prompt: shortText(action.prompt, 500),
        items: (Array.isArray(action.items) ? action.items : []).map(id => shortText(id, 120)).filter(Boolean).slice(0, 20) } }];
    }
    return [];
  });
}
function loadConversation() {
  try {
    const value = JSON.parse(sessionStorage.getItem(SESSION_KEY) || 'null');
    // Accept the original transcript array as well as the newer object that
    // stores the conversation ID beside its turns.
    const storedTurns = Array.isArray(value) ? value : value?.turns;
    const turns = Array.isArray(storedTurns) ? storedTurns.slice(-12).filter((turn) =>
      turn && typeof turn.question === 'string' && typeof turn.answer === 'string'
        && turn.question.length <= 500 && turn.answer.length <= 500,
    ).map((turn) => ({ question: turn.question, answer: turn.answer, actions: [], ...(normalizeTimeline(turn.timeline).length ? { timeline: normalizeTimeline(turn.timeline) } : {}), ...(normalizeActivities(turn.activities).length ? { activities: normalizeActivities(turn.activities) } : {}), ...(normalizeOutfits(turn.outfits).length ? { outfits: normalizeOutfits(turn.outfits) } : {}) })) : [];
    return { conversationId: typeof value?.conversationId === 'string' && UUID_RE.test(value.conversationId) ? value.conversationId : null, turns };
  } catch { return { conversationId: null, turns: [] }; }
}
const conversationState = loadConversation();
const completedTurns = conversationState.turns;

function saveConversation() {
  try {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify({
      conversationId: conversationState.conversationId,
      turns: completedTurns.slice(-12).map(({ question, answer, outfits, activities, timeline }) => ({ question, answer, ...(normalizeTimeline(timeline).length ? { timeline: normalizeTimeline(timeline) } : {}), ...(normalizeActivities(activities).length ? { activities: normalizeActivities(activities) } : {}), ...(normalizeOutfits(outfits).length ? { outfits: normalizeOutfits(outfits) } : {}) })),
    }));
  } catch { /* storage unavailable or full; keep this session in memory */ }
}

function actionLink(action) {
  if (!ACTION_ROUTES.has(action.route)) return null;
  const id = action.id ? '/' + encodeURIComponent(action.id) : '';
  const query = new URLSearchParams({ from: 'conversation' });
  if (action.prompt) query.set('prompt', action.prompt);
  if (Array.isArray(action.items)) query.set('outfit', JSON.stringify(action.items));
  return h('a', { class: 'chat-action', href: `#/${action.route}${id}?${query}` }, action.label, icon('right', 16));
}

function outfitCards(outfits = []) {
  return h('div', { class: 'chat-outfits' }, outfits.map((outfit, index) =>
    h('article', { class: 'chat-outfit' },
      h('h2', null, outfit.title || `Bộ ${index + 1}`),
      h('ul', { class: 'chat-outfit-items' }, outfit.items.map((item) => h('li', null,
        item.image_url ? h('img', { src: item.image_url, alt: item.name || 'Món đồ', loading: 'lazy' }) : null,
        h('span', null, item.name || item.category || 'Món đồ')))),
      outfit.reason ? h('p', null, outfit.reason) : null,
      outfit.tips ? h('p', null, outfit.tips) : null,
      h('div', { class: 'chat-actions' },
        actionLink({ route: 'tryon-photo', label: 'Ảnh mặc thử', items: outfit.item_ids }),
        actionLink({ route: 'tryon-live', label: 'Mặc thử trực tiếp', items: outfit.item_ids })))));
}

function timelineNodes(timeline) {
  return timeline.flatMap((block) => {
    if (block.type === 'text') return [h('p', { class: 'chat-bubble chat-assistant' }, block.text)];
    if (block.type === 'tool_status') return [activityRow(block)];
    if (block.type === 'outfits') return [outfitCards(block.outfits)];
    if (block.type === 'action') return [h('div', { class: 'chat-actions' }, actionLink(block.action))];
    return [];
  });
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
    completedTurns.flatMap(({ question, answer, actions, outfits, activities, timeline }) => [
      h('p', { class: 'chat-bubble chat-user' }, question),
      ...(timeline?.length ? timelineNodes(timeline) : [
        h('div', { class: 'chat-activities' }, (activities || []).map(activityRow)),
        h('p', { class: 'chat-bubble chat-assistant' }, answer),
        h('div', { class: 'chat-actions' }, actions.map(actionLink)), outfitCards(outfits),
      ]),
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
    let bubble = null;
    const timeline = [];
    const activities = new Map();
    let turnOutfits = [];
    transcript.append(h('p', { class: 'chat-bubble chat-user' }, question));
    transcript.scrollTop = transcript.scrollHeight;
    status.textContent = 'Shadow đang trả lời…';
    let answer = '';
    const turnActions = [];
    let unspoken = '';
    let audio = Promise.resolve();
    let done = false;
    let returnedConversationId = null;
    const voiceEnabled = spoken || listening;
    const revision = voiceRevision;
    const streamVoice = voiceEnabled && (!cloud || hasVietnameseVoice());
    const city = shortText(getSettings().wardrobeCity, 80) || 'Hà Nội';
    const payload = {
      ...(city ? { configured_city: city } : {}),
      text: question.slice(0, 500),
      history: context,
      image,
      ...(conversationState.conversationId ? { conversation_id: conversationState.conversationId } : {}),
    };

    function queueSpeech(line) {
      if (line.trim() && voiceEnabled) {
        audio = audio.then(() => {
          if (current.signal.aborted || revision !== voiceRevision) return;
          return playVoice(line, current.signal, revision);
        }).catch(() => {});
      }
    }

    try {
      for await (const item of conversationEvents(payload, current.signal)) {
        if (current.signal.aborted || disposed) return;
        if (item.type === 'text') {
          answer += item.text;
          if (timeline.at(-1)?.type !== 'text') {
            timeline.push({ type: 'text', text: '' });
            bubble = h('p', { class: 'chat-bubble chat-assistant' });
            transcript.append(bubble);
          }
          timeline.at(-1).text += item.text;
          bubble.textContent = timeline.at(-1).text;
          unspoken += item.text;
          const sentence = unspoken.match(/^.*?[.!?…](?:\s+|$)/s);
          if (streamVoice && sentence && sentence[0].trim().length >= 12) {
            queueSpeech(sentence[0]);
            unspoken = unspoken.slice(sentence[0].length);
          }
        } else if (item.type === 'tool_status') {
          const id = shortText(item.id, 160);
          const label = shortText(item.label, 120);
          if (id && label && ['running', 'done', 'error'].includes(item.state)) {
            let row = activities.get(id);
            if (!row && activities.size < 30) {
              row = h('div', { class: 'chat-activity' });
              activities.set(id, row);
              row.block = { type: 'tool_status', state: item.state, label };
              timeline.push(row.block);
              transcript.append(row);
            }
            if (row) {
              row.dataset.state = item.state;
              row.activity = { state: item.state, label };
              Object.assign(row.block, row.activity);
              row.replaceChildren(h('span', { class: 'chat-activity-symbol', 'aria-hidden': 'true' }, item.state === 'done' ? '✓' : item.state === 'error' ? '!' : ''),
                h('span', null, label), h('span', { class: 'chat-loading-text' }, item.state === 'running' ? ' — đang thực hiện' : item.state === 'done' ? ' — hoàn tất' : ' — chưa hoàn tất'));
            }
          }
        } else if (item.type === 'outfits') {
          const known = new Set(turnOutfits.map(o => JSON.stringify(o.item_ids)));
          const fresh = normalizeOutfits(item.outfits).filter(o => !known.has(JSON.stringify(o.item_ids))).slice(0, 3 - turnOutfits.length);
          if (fresh.length) {
            turnOutfits.push(...fresh);
            timeline.push({ type: 'outfits', outfits: fresh });
            transcript.append(outfitCards(fresh));
          }
        } else if (item.type === 'action') {
          const link = actionLink(item);
          if (link) {
            timeline.push({ type: 'action', action: item });
            transcript.append(h('div', { class: 'chat-actions' }, link));
            turnActions.push(item);
          }
        } else if (item.type === 'error') {
          throw new Error(item.text);
        } else if (item.type === 'done') {
          done = true;
          if (typeof item.conversation_id === 'string' && UUID_RE.test(item.conversation_id)) {
            returnedConversationId = item.conversation_id;
          }
        }
        transcript.scrollTop = transcript.scrollHeight;
      }
      if (!done) throw new Error('Kết nối bị gián đoạn. Bạn thử lại nhé.');
      if (returnedConversationId) conversationState.conversationId = returnedConversationId;
      queueSpeech(unspoken);
      if (answer || turnOutfits.length) {
        completedTurns.push({ question: question.slice(0, 500), answer: answer.slice(0, 500), actions: turnActions, timeline: normalizeTimeline(timeline), activities: normalizeActivities([...activities.values()].map((row) => row.activity)), ...(turnOutfits.length ? { outfits: turnOutfits } : {}) });
        if (completedTurns.length > 12) completedTurns.shift();
        saveConversation();
      } else if (done) {
        saveConversation();
      }
      status.textContent = ' ';
      await audio;
    } catch (error) {
      if (!current.signal.aborted) status.textContent = error.userMessage || error.message || 'Chưa kết nối được với Shadow.';
    } finally {
      for (const row of activities.values()) {
        if (row.dataset.state === 'running') {
          row.dataset.state = 'error';
          row.activity = { state: 'error', label: row.activity.label };
          row.replaceChildren(...activityRow(row.activity).childNodes);
        }
      }
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
