import { api, conversationEvents, fetchWithAccess } from '../api.js';
import { requestLocation, locationForRequest } from '../location.js';
import { lessonPanel } from '../live-lessons.js';
import { LiveOutfits } from '../live-outfits.js';
import { GeminiLiveSession } from '../gemini-live.js';
import { CameraTasks } from '../camera-tasks.js';
import { startSharedXmax } from '../xmax-camera.js';
import { wardrobeApi, itemImageUrl } from '../wardrobe-api.js';
import { fileToJpeg } from '../wardrobe-ui.js';
import { captureFrame, keepScreenAwake, startCamera, stopCamera } from '../camera.js';
import { recordVoice, recordingSupported, transcribeVoice, voiceAudio } from '../cloud-voice.js';
import { cancelSpeech, hasVietnameseVoice, holdToTalk, speak, sttSupported, unlockTTS } from '../speech.js';
import { h, icon } from '../ui.js';
import { getSettings } from '../store.js';

const ACTION_ROUTES = new Set(['wardrobe', 'stylist', 'tryon-live', 'tryon-photo', 'teach', 'home', 'guide', 'image-studio', 'rules']);
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
    if (block.type === 'outfits') return []; // Historic clothing cards belong on the video overlay.
    if (block.type === 'action') return [h('div', { class: 'chat-actions' }, actionLink(block.action))];
    return [];
  });
}

export function mount(el, {query=new URLSearchParams()}={}) {
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
  let liveTimeline = [], liveOutfits = [], liveActivities = new Map();
  let liveCommitted = false, liveNarrated = false;
  const configRequest = new AbortController();
  const locationButton = h('button', { type: 'button', class: 'chat-toggle', 'aria-label': 'Dùng GPS để tư vấn theo thời tiết',
    onClick: () => updateLocation(true) }, 'GPS');
  async function updateLocation(retry = false) {
    const location = await requestLocation({ retry });
    if (disposed) return;
    locationButton.textContent = location ? 'GPS ✓' : 'Bật GPS';
    locationButton.title = location ? 'Dùng vị trí hiện tại để xem thời tiết' : 'Cho phép vị trí để tư vấn theo thời tiết nơi bạn ở';
  }
  void updateLocation();

  const video = h('video', { class: 'chat-video', muted: true, playsinline: true, 'aria-label': 'Camera của bạn' });
  const status = h('p', { class: 'chat-status', role: 'status' }, 'Đang chuẩn bị camera và Live…');
  const startButton = h('button', { type: 'button', class: 'chat-start', onClick: toggleMic }, icon('mic', 20), 'Bắt đầu camera & trò chuyện');
  const prompt = h('div', { class: 'chat-hero', hidden: true }, icon('sparkle', 33), h('h1', null, 'Một camera, nói là làm'),
    h('p', null, 'Thử áo, trò chuyện, học hoặc dạy một kỹ năng.'), startButton);
  const cameraButton = h('button', {
    type: 'button', class: 'chat-toggle', 'aria-label': 'Bật camera', 'aria-pressed': 'false', onClick: toggleCamera,
  }, icon('camera', 20));
  const tryonView = h('div', { class: 'chat-xmax', hidden: true, 'aria-label': 'Video thử đồ trực tiếp' });
  const taskCard = h('section', { class: 'chat-task', 'aria-label': 'Tác vụ camera', 'aria-live': 'polite' });
  const flipButton = h('button', { type: 'button', class: 'chat-toggle chat-flip', 'aria-label': 'Đổi camera trước sau', onClick: async () => {
    tasks.cancel('Đã đổi camera. Bạn có thể nói yêu cầu tiếp theo.');
    closeCamera();
    cameraFacing = cameraFacing === 'environment' ? 'user' : 'environment';
    try { await openCamera(); } catch (error) { status.textContent = error.userMessage || error.message; }
  } }, icon('refresh', 20));
  const outfitOverlay = h('section', {class:'live-outfit-overlay', hidden:true, 'aria-label':'Gợi ý trang phục', 'aria-live':'polite'});
  const stage = h('div', { class: 'chat-stage' }, video, tryonView, prompt, taskCard, outfitOverlay, flipButton, cameraButton);
  const transcript = h('div', { id: 'shadow-transcript', class: 'chat-transcript', role: 'log', 'aria-label': 'Cuộc trò chuyện', tabindex: 0 },
    h('p', { class: 'chat-bubble chat-assistant' }, 'Chào bạn. Hôm nay bạn muốn mình giúp gì?'),
    completedTurns.flatMap(({ question, answer, actions, timeline }) => [
      h('p', { class: 'chat-bubble chat-user' }, question),
      ...(timeline?.length ? timelineNodes(timeline) : [h('p', { class: 'chat-bubble chat-assistant' }, answer),
        h('div', { class: 'chat-actions' }, (actions || []).map(actionLink))]),
    ]));
  const text = h('input', { class: 'chat-input', 'aria-label': 'Câu hỏi', placeholder: 'Hỏi SKI…', maxlength: 500 });
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
    'aria-label': 'Trò chuyện bằng giọng nói trực tiếp', onClick: () => {
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
  composer.append(h('button', {type:'button', class:'chat-toggle chat-clear', 'aria-label':'Xóa trò chuyện', title:'Xóa trò chuyện', onClick:clearChat}, icon('trash',18)));
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
  const fallbackNotice=h('p',{class:'chat-fallback-notice',hidden:true,role:'status'});
  const pane = h('div', { class: 'chat-pane' },
    h('div', { class: 'chat-pane-head' }, transcriptToggle),
    h('div', { class: 'chat-routing' }, intentBadge), transcript, fallbackNotice, status, composer);
  const audioUnlock = h('button', {
    type: 'button', class: 'chat-audio-unlock', hidden: true,
    onClick: () => { live.resumeAudio(); void video.play().catch(() => {}); },
  }, icon('speaker', 17), 'Chạm để bật âm thanh');
  pane.append(audioUnlock);
  el.append(
    h('header', { class: 'chat-nav' },
      h('div', { class: 'chat-topline' },
        h('a', { class: 'chat-brand', href: '#/conversation', 'aria-current': 'page' }, h('img', { class: 'chat-brand-logo', src: 'icons/icon-192.png?v=ski-1', alt: '', width: 34, height: 34 }), 'SKI'), locationButton),
      h('nav', { 'aria-label': 'Tài nguyên đã lưu' },
        h('a', {href:'#/wardrobe?from=conversation'}, 'Tủ đồ'),
        h('a', {href:'#/home?from=conversation'}, 'Bài đã học'),
        h('button', {type:'button', onClick:()=>{
          if (tasks.state.mode==='tryon') { tasks.cancel(); live.notifyTaskResult(tasks.execution()); }
          else if (outfitPanel.outfits.length) void tryOverlayOutfit(outfitPanel.outfits[outfitPanel.index]);
          else void askInPlace('Mình muốn thử một bộ đồ ngay trong camera này.');
        }}, 'Thử đồ'))),
    h('div', { class: 'chat-layout' }, stage, pane),
  );
  function clearChat() {
    const resumeLive = live.status !== 'idle';
    request?.abort(); request = null;
    setListening(false); cancelRecording(); stopOutput();
    live.stop();
    completedTurns.splice(0);
    conversationState.conversationId = null;
    liveBubbles = {}; liveQuestion = liveAnswer = '';
    liveActions = []; liveActionBox = null;
    liveTimeline = []; liveOutfits = []; liveActivities = new Map();
    liveCommitted = false; liveNarrated = false; liveOutfitIntroduction = '';
    outfitPanel.show([], '');
    intentBadge.hidden = true;
    text.value = '';
    transcript.replaceChildren(h('p', {class:'chat-bubble chat-assistant'}, 'Cuộc trò chuyện mới. Bạn muốn mình giúp gì?'));
    saveConversation();
    status.textContent = 'Đã xóa trò chuyện và bắt đầu ngữ cảnh mới.';
    if (resumeLive) startLive();
  }
  async function askInPlace(question) {
    if (live.sendText(question)) return;
    await submit(question);
  }
  function localAction(event) {
    const link=event.target.closest?.('a.chat-action');
    if (!link) return;
    event.preventDefault();
    const url=new URL(link.href,location.href);
    const [path,search='']=url.hash.replace(/^#\//,'').split('?');
    const query=new URLSearchParams(search);
    if (path.startsWith('tryon-')) {
      let ids=[]; try { ids=JSON.parse(query.get('outfit') || '[]'); } catch { /* invalid legacy link */ }
      if (ids.length) void tryOverlayOutfit({title:'Bộ đã chọn',item_ids:ids});
      else void askInPlace('Mình muốn thử đồ trực tiếp trong camera này.');
    } else if (path.startsWith('guide/')) {
      void runLessonControl({command:'start_guidance',skill_id:decodeURIComponent(path.slice(6))});
    } else void askInPlace(({wardrobe:'Cho xem đồ trong tủ và gợi ý bộ phù hợp.',stylist:'Gợi ý trang phục cho mình.',home:'Liệt kê bài đã học.',teach:'Mình muốn dạy bạn một bài mới.'})[path] || `Hãy giúp mình ngay trong camera này: ${link.textContent}`);
  }
  el.addEventListener('click',localAction);
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
        if (execution.speech) void speak(execution.speech);
        transcript.append(h('p', { class: 'chat-bubble chat-assistant' }, execution.message));
        transcript.scrollTop = transcript.scrollHeight;
      } else live.notifyTaskResult(execution);
    },
  });
  let liveOutfitIntroduction = '';
  let originalView = false;
  const outfitPanel = new LiveOutfits(outfitOverlay, {
    onTry: outfit => { void tryOverlayOutfit(outfit); },
    onOther: () => {
      if (!live.sendText('Gợi ý bộ khác từ tủ đồ nhé.')) status.textContent = 'Bật micro Live để nhờ chọn bộ khác.';
    },
  });
  async function tryOverlayOutfit(outfit) {
    if (tasks.state.mode === 'teaching' || tasks.state.mode === 'compiling') {
      status.textContent = 'Hãy lưu hoặc hủy bài dạy trước khi thử đồ.'; return;
    }
    outfitPanel.dismiss();
    try {
      const inventory=outfit.items?.length ? outfit.items : await wardrobeApi.listItems();
      const selected=outfit.item_ids.map(id=>inventory.find(item=>item.id===id)).filter(Boolean);
      if (selected.length!==outfit.item_ids.length) throw new Error('Một món đồ không còn trong tủ. Hãy nhờ chọn lại bộ nhé.');
      const garments=selected.filter(item=>['top','bottom','outerwear','dress'].includes(item.category));
      const categories=garments.map(item=>item.category);
      if (!garments.length || garments.length>3 || new Set(categories).size!==categories.length || (categories.includes('dress') && categories.some(c=>['top','bottom'].includes(c)))) throw new Error('Thử trực tiếp hỗ trợ một áo, một quần và một áo khoác, hoặc váy. Hãy chọn các lớp muốn thử.');
      await tasks.handle({intent:'tryon_live',source:'tool_call',label:'Thử bộ đã chọn',camera_command:'route_request'}, {itemIds:garments.map(item=>item.id),request:`Thử bộ ${outfit.title}`});
      live.notifyTaskResult(tasks.execution());
    } catch (error) { status.textContent = error.userMessage || error.message; outfitPanel.reopen(); }
  }
  function setLiveView(view) {
    if (view === 'dismiss_outfits') { outfitPanel.dismiss(); return {status:'dismissed'}; }
    if (view === 'show_outfits') return outfitPanel.reopen();
    if (view === 'tryon' && !['loading','live'].includes(tasks.state.status)) return {status:'unavailable',message:'Chưa có phiên thử đồ đang chạy.'};
    originalView = view === 'original';
    stage.classList.toggle('chat-show-original',originalView);
    renderTask(tasks.state);
    return {status:'displayed',view};
  }
  renderTask(tasks.state);

  function renderTask(state) {
    if (disposed) return;
    const lessonMode=['teaching','compiling','reviewing','guiding'].includes(state.mode);
    taskCard.classList.toggle('chat-lesson-panel',lessonMode);
    taskCard.classList.toggle('chat-lesson-guidance',state.mode==='guiding');
    taskCard.classList.toggle('chat-lesson-minimized',state.mode==='reviewing' && Boolean(state.reviewHidden));
    if (lessonMode) {
      originalView=false; stage.classList.remove('chat-show-original');
      outfitPanel.dismiss(); taskCard.classList.remove('chat-fitting-bar');
      taskCard.dataset.mode=state.mode; taskCard.dataset.status=state.status;
      taskCard.replaceChildren(lessonPanel(state,{
        onConfirm:()=>{void runLessonControl({command:'save',revision:tasks.lessonRevision});},
        onDismiss:()=>{
          if (state.mode==='reviewing') tasks.update({reviewHidden:true});
          else {tasks.cancel();live.notifyTaskResult(tasks.execution());}
        },
      }));
      return;
    }
    if (state.mode === 'tryon' && ['loading','live'].includes(state.status) && !state.showGarments) {
      outfitPanel.dismiss();
      taskCard.dataset.mode = 'tryon'; taskCard.dataset.status = state.status;
      taskCard.classList.add('chat-fitting-bar');
      const ids = state.applied_ids?.length ? state.applied_ids : state.selected_ids || [];
      const items = ids.map(id => state.items?.find(item => item.id === id)).filter(Boolean);
      taskCard.replaceChildren(
        h('div',{class:'chat-fitting-summary'}, h('div',{class:'chat-fitting-thumbs'},items.map(item => h('img',{src:itemImageUrl(item),alt:item.name || 'Trang phục'}))),
          h('div',null,h('small',null,state.status === 'loading' ? 'ĐANG CHUẨN BỊ' : 'ĐANG THỬ'),h('p',null,items.map(item => item.name).join(' + ') || state.title)),
          h('button',{type:'button',class:'live-outfit-close','aria-label':'Đổi trang phục',onClick:()=>tasks.update({showGarments:true})},icon('sliders',18))),
        h('div',{class:'chat-fitting-controls'},
          h('div',{class:'chat-fitting-segments','aria-label':'Hiển thị video'},
            h('button',{type:'button','aria-pressed':String(originalView),onClick:()=>setLiveView('original')},'Gốc'),
            h('button',{type:'button','aria-pressed':String(!originalView),onClick:()=>setLiveView('tryon')},'Thử đồ')),
          h('button',{type:'button',class:'live-outfit-close','aria-label':'Dừng thử đồ',onClick:()=>{tasks.cancel();live.notifyTaskResult(tasks.execution());}},icon('close',18))),
        ...(state.status === 'loading' && state.message ? [h('p',{role:'status'},state.message)] : []));
      return;
    }
    taskCard.classList.remove('chat-fitting-bar');
    if (state.mode !== 'tryon') { originalView=false; stage.classList.remove('chat-show-original'); }
    taskCard.dataset.mode = state.mode;
    taskCard.dataset.status = state.status;
    taskCard.replaceChildren(h('span', { class: 'chat-task-label' }, ({ chat: 'SKI', guiding: 'HƯỚNG DẪN', teaching: 'ĐANG GHI BÀI DẠY', compiling: 'ĐANG BIÊN SOẠN', tryon: state.status === 'loading' ? 'ĐANG THAY ĐỒ' : 'THỬ ĐỒ' })[state.mode]),
      h('strong', null, state.title), h('p', null, state.message));
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
        count.textContent = `${matches.length} món trong tủ đồ SKI`;
        grid.replaceChildren(...matches.slice(0, garmentLimit).map(item =>
          h('button', { type: 'button', class: 'chat-garment', 'aria-pressed': String((state.selected_ids || []).includes(item.id)), onClick: () => tasks.selectGarment(item.id) },
            h('img', { src: itemImageUrl(item), alt: '', loading: 'lazy' }), h('span', null, item.name || item.id))));
        more.hidden = matches.length <= garmentLimit;
      }
      taskCard.append(h('div', { class: 'chat-task-controls' },
        h('input', { type: 'search', class: 'chat-task-title', placeholder: 'Tìm áo len, quần, UNIQLO…', 'aria-label': 'Tìm trong tủ đồ SKI', value: garmentQuery, onInput: event => { garmentQuery = event.target.value; garmentLimit = 24; fillGarments(); } }),
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
    if (['teaching', 'compiling', 'tryon', 'guiding'].includes(state.mode)) taskCard.append(
      h('button', { type: 'button', class: 'chat-task-cancel', onClick: () => {
        tasks.cancel(); live.notifyTaskResult(tasks.execution());
      } }, 'Dừng tác vụ'));
  }
  async function runLessonControl(control,{signal}={}) {
    if (disposed || signal?.aborted) return null;
    try {
      const result=await tasks.lessonControl(control,{signal});
      if (result?.error) status.textContent=result.error;
      return result;
    } catch(error) {
      if (disposed || signal?.aborted) return null;
      status.textContent=error.userMessage || error.message || 'Chưa thực hiện được thao tác bài học.';
      return {...tasks.execution(),error:status.textContent};
    }
  }
  function liveTranscript(role, value) {
    if (disposed) return;
    if (role === 'assistant') {
      if (liveTimeline.at(-1)?.type !== 'text') {
        liveTimeline.push({ type:'text', text:'' });
        liveBubbles.assistant = null;
      }
      liveTimeline.at(-1).text += value;
    }
    if (!liveBubbles[role]) {
      liveBubbles[role] = h('p', { class:`chat-bubble ${role === 'user' ? 'chat-user' : 'chat-assistant'}` });
      transcript.append(liveBubbles[role]);
    }
    liveBubbles[role].textContent += value;
    if (role === 'user') liveQuestion += value;
    else liveAnswer += value;
    transcript.scrollTop = transcript.scrollHeight;
  }

  function liveToolEvent(event) {
    if (disposed) return;
    if (event.type === 'live_outfit_intro') { liveOutfitIntroduction=event.text || ''; return; }
    if (event.type === 'text') { liveTranscript('assistant', event.text || ''); return; }
    if (event.type === 'tool_status') {
      let entry = liveActivities.get(event.id);
      if (!entry && liveActivities.size < 30) {
        const block = {type:'tool_status',state:event.state,label:event.label};
        const row = activityRow(block);
        entry = {block,row}; liveActivities.set(event.id,entry); liveTimeline.push(block); transcript.append(row);
      }
      if (entry) { Object.assign(entry.block,{state:event.state,label:event.label}); entry.row.dataset.state=event.state; entry.row.replaceChildren(...activityRow(entry.block).childNodes); }
    } else if (event.type === 'outfits') {
      const existing = new Set(liveOutfits.map(outfit => JSON.stringify(outfit.item_ids)));
      const fresh = normalizeOutfits(event.outfits).filter(outfit => !existing.has(JSON.stringify(outfit.item_ids))).slice(0,3-liveOutfits.length);
      if (fresh.length) { liveOutfits = fresh.slice(0,1); outfitPanel.show(liveOutfits,liveOutfitIntroduction); }
    } else if (event.type === 'action') {
      const link = actionLink(event);
      if (link) { liveActions.push(event); liveTimeline.push({type:'action',action:event}); transcript.append(h('div',{class:'chat-actions'},link)); }
    }
    transcript.scrollTop = transcript.scrollHeight;
  }

  const live = new GeminiLiveSession({
    onTurnStart() {
      if (tasks.recording && !liveNarrated && liveQuestion) tasks.narrate(liveQuestion);
      for (const entry of liveActivities.values()) {
        if (entry.block.state === 'running') { Object.assign(entry.block,{state:'error',label:'Đã huỷ thao tác'}); entry.row.dataset.state='error'; entry.row.replaceChildren(...activityRow(entry.block).childNodes); }
      }
      liveBubbles = {};
      liveQuestion = liveAnswer = '';
      liveActions = [];
      liveActionBox = null;
      liveTimeline = []; liveOutfits = []; liveActivities = new Map(); liveCommitted = false; liveNarrated = false;
    },
    onTranscript: liveTranscript,
    onToolEvent: liveToolEvent,
    async onLessonControl(control,turn) {
      if (tasks.recording && ['finish_teaching','finish_and_save','save'].includes(control.command) && !liveNarrated && liveQuestion) { tasks.narrate(liveQuestion); liveNarrated=true; }
      return runLessonControl(control,turn);
    },
    onLiveControl(control, {signal}) { if (disposed || signal.aborted) return null; return setLiveView(control.view); },
    async onCameraTask(result, turn) {
      if (disposed) return;
      intentBadge.hidden = false;
      intentBadge.dataset.intent = result.intent;
      intentBadge.replaceChildren(h('strong', null, result.confidence < .5 ? 'Chưa rõ yêu cầu' : result.label),
        h('span', null, result.source === 'tool_call' ? 'Theo yêu cầu của bạn' : `${new Intl.NumberFormat('vi-VN', { style: 'percent', maximumFractionDigits: 1 }).format(result.confidence)} · Phân tích`));
      liveActions = (result.actions || []).filter(action => !['teach', 'tryon-live', 'guide'].includes(action.route));
      liveActionBox?.remove();
      liveActionBox = h('div', { class: 'chat-actions' }, liveActions.map(actionLink));
      transcript.append(liveActionBox);
      transcript.scrollTop = transcript.scrollHeight;
      if (tasks.recording && result.camera_command === 'finish_teaching' && !liveNarrated) {
        tasks.narrate(liveQuestion); liveNarrated = true;
      }
      const wasRecording = Boolean(tasks.recording);
      try { const execution = await tasks.handle(result, turn); if (wasRecording) liveNarrated = true; return execution; }
      catch (error) {
        if (turn.signal.aborted || disposed) return null;
        tasks.update({ status: 'error', message: error.userMessage || error.message || 'Chưa thực hiện được tác vụ camera.' });
        return tasks.execution();
      }
    },
    onTurnComplete() {
      if (disposed || liveCommitted) return;
      if (tasks.recording && !liveNarrated && liveQuestion) { tasks.narrate(liveQuestion); liveNarrated = true; }
      if (!liveQuestion && !liveAnswer && !liveOutfits.length) return;
      liveCommitted = true;
      completedTurns.push({question:liveQuestion.slice(0,500),answer:liveAnswer.slice(0,500),actions:liveActions,
        timeline:normalizeTimeline(liveTimeline),outfits:liveOutfits});
      if (completedTurns.length > 12) completedTurns.shift();
      conversationState.conversationId = null;
      saveConversation();
    },
    onState(value, detail) {
      if (disposed) return;
      const active = value !== 'idle';
      startButton.disabled = active;
      prompt.hidden = active || Boolean(camera);
      pane.dataset.live = value;
      if (value === 'idle') outfitPanel.dismiss();

      mic.setAttribute('aria-pressed', String(active));
      mic.setAttribute('aria-label', active ? 'Dừng trò chuyện Live' : 'Bật micro');
      stage.classList.toggle('chat-live-active', active);
      stage.classList.toggle('chat-live-speaking', value === 'speaking');
      liveMode.disabled = value === 'connecting' || !liveAvailable;
      status.textContent = detail || ({
        idle: 'Bấm micro để bắt đầu trò chuyện.', connecting: 'Đang mở micro và kết nối trò chuyện…',
        listening: 'Đang nghe. Nói tự nhiên rồi ngừng một nhịp để SKI trả lời.',
        classifying: 'Đang phân tích yêu cầu…', thinking: 'SKI đang chuẩn bị trả lời…',
        speaking: 'SKI đang nói. Bạn có thể nói chen để hỏi tiếp.',
      })[value];
    },
    onError(error) {
      if (disposed) return;
      livePreferred=false;
      liveMode.setAttribute('aria-pressed','false');
      const reason=error.userMessage || error.message;
      fallbackNotice.textContent=`${reason} Đang dùng trò chuyện thường; camera vẫn mở.`;
      fallbackNotice.hidden=false;
      if (sttSupported()) { cloudMic=false; setListening(true); listen(); }
      else if (cloud && recordingSupported()) { cloudMic=true; void startRecording(); }
      updateMicrophoneControl();
    },
    onNotice(message) { if (!disposed) status.textContent = message; },
    onAudioBlocked(blocked) { if (!disposed) audioUnlock.hidden = !blocked; },
  }, {
    fetchImpl: fetchWithAccess,
    getFrame: () => tasks.ar?.getFrame?.() || (camera ? captureFrame(video, { maxSide: 448 }) : null),
    getLocation: locationForRequest,
    getCity: () => shortText(getSettings().wardrobeCity,80),
    getTaskContext: () => ({...tasks.context(), suggested_item_ids: outfitPanel.container.hidden ? [] : outfitPanel.outfits[outfitPanel.index]?.item_ids || []}),
    getHistory: () => completedTurns.slice(-3).flatMap(({ question, answer }) => [
      { role: 'user', text: question }, { role: 'assistant', text: answer },
    ]).filter(turn => turn.text?.trim()),
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
      livePreferred=false;
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
    fallbackNotice.hidden=true;
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
      if (tasks.state.mode!=='reviewing') tasks.cancel('Camera đã tắt. Bạn vẫn có thể trò chuyện.');
      closeCamera();
      status.textContent = 'Camera đã tắt.';
      return;
    }
    try {
      await openCamera();
      status.textContent = live.status !== 'idle'
        ? 'Camera đang mở. SKI nhận hình ảnh trong lúc bạn trò chuyện Live.'
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
    if (!camera) void openCamera().catch(error=>{ if (!disposed) status.textContent=error.userMessage || error.message; });
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
    if (tasks.recording) tasks.narrate(question);
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
    let bubble = null;
    const timeline = [];
    const activities = new Map();
    let turnOutfits = [];
    transcript.append(h('p', { class: 'chat-bubble chat-user' }, question));
    transcript.scrollTop = transcript.scrollHeight;
    status.textContent = 'SKI đang trả lời…';
    let answer = '';
    const turnActions = [];
    let unspoken = '';
    let audio = Promise.resolve();
    let done = false;
    let returnedConversationId = null;
    const voiceEnabled = spoken || listening;
    const revision = voiceRevision;
    const streamVoice = voiceEnabled && (!cloud || hasVietnameseVoice());
    const city = shortText(getSettings().wardrobeCity, 80);
    const visibleOutfit=outfitPanel.outfits[outfitPanel.index];
    const payload = {
      lesson_state:tasks.lessonContext(),
      ...(visibleOutfit ? {current_outfit:{title:(visibleOutfit.title || 'Bộ hiện tại').slice(0,80),item_ids:visibleOutfit.item_ids,reason:(visibleOutfit.reason || '').slice(0,500),tips:(visibleOutfit.tips || '').slice(0,300)}} : {}),
      ...(city ? { configured_city: city } : {}),
      text: question.slice(0, 500),
      history: context.filter(turn => turn.text?.trim()),
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
      const location = await locationForRequest();
      if (current.signal.aborted || disposed) return;
      for await (const item of conversationEvents({ ...payload, location }, current.signal)) {
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
            turnOutfits = fresh.slice(0,1);
            timeline.push({ type: 'outfits', outfits: fresh });
            outfitPanel.show(turnOutfits,answer);
          }
        } else if (item.type === 'action' && item.execute && item.route==='lesson-control') {
          const execution=await runLessonControl(item.control,{signal:current.signal});
          if (item.result_token && !current.signal.aborted) {
            const response=await fetchWithAccess('/api/conversation/action-result',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:item.result_token,execution:execution ? {...execution,message:String(execution.message || '').slice(0,1000),...(execution.error?{error:String(execution.error).slice(0,1000)}:{})} : {status:'cancelled',error:'The lesson action was cancelled.'}}),signal:current.signal});
            if(!response.ok) throw new Error('Chưa xác nhận được kết quả bài học. Hãy kiểm tra trạng thái rồi thử lại.');
          }
        } else if (item.type === 'action' && item.execute && item.route==='tryon-live') {
          if (item.stop) tasks.cancel();
          else await tryOverlayOutfit({title:'Bộ đã chọn',item_ids:item.items || []});
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
      if (!current.signal.aborted) status.textContent = error.userMessage || error.message || 'Chưa kết nối được với SKI.';
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

  const entryLesson=query.get('lesson'), entrySkill=query.get('skill');
  if (entryLesson==='teach') queueMicrotask(()=>{if(!disposed) void runLessonControl({command:'start_teaching'});});
  else if (entrySkill && ['guide','review'].includes(entryLesson)) queueMicrotask(()=>{
    if(!disposed) void runLessonControl({command:entryLesson==='guide'?'start_guidance':'review',skill_id:entrySkill});
  });
  function onVisibility() {
    if (!document.hidden) { autoStartLive(); return; }
    live.stop();
    if (tasks.state.mode!=='reviewing') tasks.cancel('Đã dừng tác vụ khi đưa ứng dụng vào nền.');
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
    el.removeEventListener('click',localAction);
    el.removeEventListener('pointerdown', unlockLiveAudio);
    el.removeEventListener('keydown', unlockLiveAudio);
    document.removeEventListener('visibilitychange', onVisibility);
  };
}
