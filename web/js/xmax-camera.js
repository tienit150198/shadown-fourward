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

export function xmaxCreditCode(error) {
  const raw = error?.apiCode ?? error?.code;
  const code = Number(raw);
  if (code === 46019 || /temporary.*(?:limit|cap)|(?:points|credit).*limit.*(?:reached|exceeded)/i.test(error?.message || '')) return null;
  if ([46003, 46017].includes(code)) return code;
  if (error?.httpStatus === 402) return 402;
  if (/insufficient[ _-]*(?:account[ _-]*)?(?:credits?|balance|funds|points)|not enough (?:credits?|balance|funds|points)|(?:credits?|balance|funds|points).*(?:depleted|exhausted)|余额不足|积分不足/i.test(error?.message || '')) return 46017;
  return null;
}

// The same controller covers startup retries and recovery after generation starts.
export async function recoverXmaxSession(startAttempt, { signal, onState = () => {}, onError = () => {} } = {}) {
  const controller = new AbortController();
  const abort = () => { void stop(); };
  signal?.addEventListener('abort', abort, { once: true });
  if (signal?.aborted) controller.abort();
  let current, attempts = 0, budget = 1, stopped = false, recovering = false, timedOut = false, startupTimer, sessionTimer;
  const check = () => {
    if (timedOut) throw new Error('Thay đồ mất nhiều thời gian hơn dự kiến. Bạn thử lại nhé.');
    if (stopped || controller.signal.aborted) throw new DOMException('Đã dừng thử đồ.', 'AbortError');
  };
  const stop = async () => {
    if (stopped) return;
    stopped = true;
    clearTimeout(startupTimer); clearTimeout(sessionTimer);
    signal?.removeEventListener('abort', abort);
    controller.abort();
    await current?.stop();
  };
  const failRuntime = async error => {
    if (recovering || stopped) return;
    recovering = true;
    try {
      if (!error.creditRetryable || attempts >= budget) throw error;
      current = await connect();
    } catch (failure) {
      await stop();
      if (failure.name !== 'AbortError') onError(tryonErrorMessage(failure));
    } finally { recovering = false; }
  };
  const connect = async () => {
    while (attempts < budget) {
      check();
      if (attempts) onState('Đang thử kết nối lại, bạn chờ một chút nhé…');
      attempts++;
      try {
        const result = await startAttempt({ signal: controller.signal, onState,
          onPoolSize: count => { if (Number.isInteger(count)) budget = Math.max(1, Math.min(count, 8)); },
          onFailure: failRuntime });
        if (stopped || controller.signal.aborted) { await result.stop(); check(); }
        return result;
      } catch (error) {
        check();
        if (!error.creditRetryable || attempts >= budget) throw error;
      }
    }
    throw new Error('Hiện chưa thể thay đồ. Bạn thử lại sau ít phút nhé.');
  };
  // One total startup deadline and one total session duration, even across retries.
  startupTimer = setTimeout(() => { timedOut = true; void stop(); }, 90000);
  try {
    current = await connect();
    clearTimeout(startupTimer);
    sessionTimer = setTimeout(() => { void stop(); onError('Đã hết phiên thử đồ 2 phút. Bạn có thể chọn đồ để thử tiếp.'); }, 120000);
    return { stop, provider: 'xmax', model: 'x2.1', getFrame: () => current?.getFrame?.() || null };
  } catch (error) { await stop(); throw error; }
}

export async function startSharedXmax(itemIds, stream, container, options = {}) {
  return recoverXmaxSession(attempt => startXmaxAttempt(itemIds, stream, container,
    { ...options, ...attempt }), options);
}

export async function connectXmaxVideo(manager, local, context, view, { check = () => {}, onRetry = () => {} } = {}) {
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      check();
      const remote = await manager.connect(local);
      check();
      view.remoteTrack = remote.videoTrack;
      return await manager.startGeneration({ localStream: local, context });
    } catch (error) {
      check();
      if (attempt || error.name === 'AbortError' || !/timed out|timeout/i.test(error.message || '')) throw error;
      // A failed allocation can time out before sending a first frame. Retry
      // once with the same manager, temporary key, credit cap and camera.
      view.remoteTrack = undefined;
      await manager.disconnect().catch(() => {});
      check();
      onRetry();
    }
  }
}

async function startXmaxAttempt(itemIds, stream, container, { signal, onState = () => {}, onFailure = () => {}, onPoolSize = () => {}, fetchImpl = fetchWithAccess } = {}) {
  const source = stream?.getVideoTracks().find(track => track.readyState === 'live');
  if (!source) throw new Error('Cần mở camera để thử đồ.');
  let session, manager, view, stopped = false, generating = false, startupTimer, failurePromise;
  let resolveReady, rejectReady, rejectCreditFailure;
  const creditFailure = new Promise((_, reject) => { rejectCreditFailure = reject; });
  void creditFailure.catch(() => {});
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
    clearTimeout(startupTimer);
    signal?.removeEventListener('abort', onAbort);
    container.hidden = true;
    view?.detach();
    container.replaceChildren();
    await Promise.allSettled([manager?.close(), closeKey()]);
  };
  const prepareFailure = raw => {
    if (failurePromise) return failurePromise;
    failurePromise = (async () => {
      if (raw?.name === 'AbortError') return raw;
      const error = new Error(cleanMessage(raw));
      const code = xmaxCreditCode(raw);
      if (session?.token && code !== null && !signal?.aborted) {
        try {
          const response = await fetchImpl('/api/tryon/xmax/credit-failure', {
            method:'POST', headers:{'Content-Type':'application/json'},
            body:JSON.stringify({token:session.token,code}), signal,
          });
          const result = await response.json();
          error.creditRetryable = response.ok && result.retryable === true;
        } catch { error.creditRetryable = false; }
      }
      return error;
    })();
    return failurePromise;
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
    const sdk = await import('/vendor/xmax/xmax.js');
    check();
    const response = await fetchImpl('/api/tryon/xmax/session', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ item_ids: Array.isArray(itemIds) ? itemIds : [itemIds] }), signal,
    });
    session = await response.json();
    if (!response.ok) throw new Error(session.detail || 'Chưa tạo được phiên thay đồ. Bạn thử lại nhé.');
    onPoolSize(session.keyPoolSize || 1);
    if (stopped || signal?.aborted) await closeKey();
    check();
    const client = new sdk.XmaxClient(new sdk.XmaxConfiguration({ apiKey: session.token, environment: session.environment }));
    manager = client.createRealtimeManager(new sdk.RealtimeConfiguration({ model: sdk.RealtimeModel.x2_1 }));
    await manager.setStateListener(state => {
      if (stopped) return;
      if (state.reason?.kind === 'failure') {
        const raw = state.reason.error;
        if (xmaxCreditCode(raw) !== null) rejectCreditFailure(raw);
        rejectReady(raw);
        if (generating) void (async () => {
          const error = await prepareFailure(raw);
          await stop();
          onFailure(error);
        })();
      }
      if (state.connectionState === 'Generating') { generating = true; resolveReady(); }
    });
    check();
    view = new sdk.XmaxRealtimeDualVideoView({ isInteractionEnabled: false });
    Object.assign(view.element.style, { width: '100%', height: '100%' });
    view.attach(container);
    onState('Đang chuẩn bị bộ đồ đã chọn…');
    const image = await fetchImpl(session.reference_image_url || session.item.image_url, { signal });
    if (!image.ok) throw new Error('Chưa lấy được ảnh trang phục.');
    const file = await client.createStorageService().uploadImage({ data: await image.blob(), fileName: 'shadow-outfit.jpg', contentType: 'image/jpeg' });
    check();
    const settings = source.getSettings();
    const local = await manager.createExternalVideoStream({
      videoTrack: source,
      videoFormat: new sdk.RealtimeVideoFormat({ width: settings.width || 1280, height: settings.height || 720, fps: 24 }),
    });
    check();
    view.localTrack = local.videoTrack;
    container.hidden = false;
    onState('Chúng tôi đang thay đồ cho bạn…');
    // Bind the remote placeholder before starting so its first frame has an
    // active renderer, including browsers that defer media without a view.
    await Promise.race([connectXmaxVideo(manager, local, new sdk.RealtimeContext({ prompt: session.prompt, referencePath: file.url }), view, {
      check, onRetry() { resetReady(); onState('Đang thử kết nối lại, bạn chờ một chút nhé…'); },
    }), creditFailure]);
    await ready;
    check();
    clearTimeout(startupTimer);

    return { stop, provider: 'xmax', model: 'x2.1', getFrame: () => {
      if (stopped || !generating) return null;
      const remoteVideo = [...container.querySelectorAll('video')].at(-1);
      return captureFrame(remoteVideo, { maxSide: 448 });
    } };
  } catch (error) { const failure = await prepareFailure(error); await stop(); throw failure; }
}
