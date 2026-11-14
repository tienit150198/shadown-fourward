import { fetchWithAccess } from './api.js';
import { captureFrame } from './camera.js';

export function tryonErrorMessage(error) {
  const message = String(error?.message || '');
  if (error?.name === 'NotAllowedError') return 'Bạn cần cho phép dùng camera để thay đồ.';
  if (/timed?\s*out|timeout|quá lâu/i.test(message)) return 'Thay đồ mất nhiều thời gian hơn dự kiến. Bạn thử lại nhé.';
  if (/quota|credit|balance|rate limit|429/i.test(message)) return 'Hiện chưa thể thay đồ. Bạn thử lại sau ít phút nhé.';
  if (/[À-ỹ]/.test(message) && !/xmax|x2[._\s]?1|api.?key|https?:|(?:uk|tk)-/i.test(message)) return message.slice(0, 220);
  return 'Chưa thể thay đồ lúc này. Bạn vui lòng thử lại.';
}

export async function connectXmaxVideo(manager, local, context, view, { check = () => {}, onRetry = () => {} } = {}) {
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      check();
      const remote = await manager.connect(local);
      check();
      view.track = remote.videoTrack;
      return await manager.startGeneration({ localStream: local, context });
    } catch (error) {
      check();
      if (attempt || error.name === 'AbortError' || !/timed out|timeout/i.test(error.message || '')) throw error;
      // A failed allocation can time out before sending a first frame. Retry
      // once with the same manager, temporary key, credit cap and camera.
      view.track = undefined;
      await manager.disconnect().catch(() => {});
      check();
      onRetry();
    }
  }
}

export async function startSharedXmax(itemIds, stream, container, { signal, onState = () => {}, onError = () => {}, fetchImpl = fetchWithAccess, loadSdk = () => import('/vendor/xmax/xmax.js') } = {}) {
  const source = stream?.getVideoTracks().find(track => track.readyState === 'live');
  if (!source) throw new Error('Cần mở camera để thử đồ.');
  let session, manager, view, inputTrack, stopped = false, generating = false, timer, startupTimer;
  let resolveReady, rejectReady;
  let ready;
  const resetReady = () => {
    ready = new Promise((resolve, reject) => { resolveReady = resolve; rejectReady = reject; });
    void ready.catch(() => {});
  };
  resetReady();
  const cleanMessage = tryonErrorMessage;
  const closeKey = () => session ? fetchImpl('/api/tryon/xmax/close', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token: session.token }), keepalive: true,
  }).catch(() => {}) : Promise.resolve();
  const stop = async () => {
    if (stopped) return;
    stopped = true;
    clearTimeout(timer); clearTimeout(startupTimer);
    signal?.removeEventListener('abort', onAbort);
    container.hidden = true;
    container.style.opacity = '';
    view?.detach();
    container.replaceChildren();
    await Promise.allSettled([manager?.close(), closeKey()]);
    inputTrack?.stop();
  };
  const onAbort = () => { rejectReady(new DOMException('Đã dừng thử đồ.', 'AbortError')); void stop(); };
  const check = () => { if (stopped || signal?.aborted) throw new DOMException('Đã dừng thử đồ.', 'AbortError'); };
  signal?.addEventListener('abort', onAbort, { once: true });
  startupTimer = setTimeout(() => {
    rejectReady(new Error('Thay đồ mất nhiều thời gian hơn dự kiến. Bạn thử lại nhé.'));
    void stop();
  }, 90000);
  try {
    check();
    onState('Đang chuẩn bị thay đồ cho bạn…');
    const sdk = await loadSdk();
    check();
    const response = await fetchImpl('/api/tryon/xmax/session', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ item_ids: Array.isArray(itemIds) ? itemIds : [itemIds] }), signal,
    });
    session = await response.json();
    if (!response.ok) throw new Error(session.detail || 'Chưa tạo được phiên thay đồ. Bạn thử lại nhé.');
    if (stopped || signal?.aborted) await closeKey();
    check();
    const client = new sdk.XmaxClient(new sdk.XmaxConfiguration({ apiKey: session.token, environment: session.environment }));
    manager = client.createRealtimeManager(new sdk.RealtimeConfiguration({ model: sdk.RealtimeModel.x2_1 }));
    await manager.setStateListener(state => {
      if (stopped) return;
      if (state.reason?.kind === 'failure') {
        const error = new Error(cleanMessage(state.reason.error));
        rejectReady(error);
        if (generating) { void stop(); onError(error.message); }
      }
    });
    check();
    // Keep the existing full-screen camera as the preview. The SDK owns only
    // the full-size result layer, never a second local camera/PiP layout.
    view = new sdk.XmaxRealtimeVideoView({ isInteractionEnabled: false });
    view.frameDisplayHandler = () => { if (!stopped) { generating = true; resolveReady(); } };
    Object.assign(view.element.style, { width: '100%', height: '100%' });
    view.attach(container);
    container.style.opacity = '0';
    container.hidden = false; // Mounted at full size so first-frame detection can run.
    onState('Đang chuẩn bị bộ đồ đã chọn…');
    const image = await fetchImpl(session.reference_image_url || session.item.image_url, { signal });
    if (!image.ok) throw new Error('Chưa lấy được ảnh trang phục.');
    const file = await client.createStorageService().uploadImage({ data: await image.blob(), fileName: 'shadow-outfit.jpg', contentType: 'image/jpeg' });
    check();
    const settings = source.getSettings();
    inputTrack = source.clone();
    const local = await manager.createExternalVideoStream({
      videoTrack: inputTrack,
      videoFormat: new sdk.RealtimeVideoFormat({ width: settings.width || 1280, height: settings.height || 720, fps: 24 }),
    });
    check();
    onState('Chúng tôi đang thay đồ cho bạn…');
    // Bind the remote placeholder before starting so its first frame has an
    // active renderer, including browsers that defer media without a view.
    await connectXmaxVideo(manager, local, new sdk.RealtimeContext({ prompt: session.prompt, referencePath: file.url }), view, {
      check, onRetry() { resetReady(); onState('Đang thử kết nối lại, bạn chờ một chút nhé…'); },
    });
    await ready;
    check();
    container.style.opacity = '1';
    clearTimeout(startupTimer);
    timer = setTimeout(() => { void stop(); onError('Đã hết phiên thử đồ 2 phút. Bạn có thể chọn đồ để thử tiếp.'); }, session.maxSeconds * 1000);
    return { stop, provider: 'xmax', model: 'x2.1', getFrame: () => {
      if (stopped || !generating) return null;
      const remoteVideo = [...container.querySelectorAll('video')].at(-1);
      return captureFrame(remoteVideo, { maxSide: 448 });
    } };
  } catch (error) { await stop(); throw error.name === 'AbortError' ? error : new Error(cleanMessage(error)); }
}
