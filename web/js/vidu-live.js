// Vidu controls editing over WebSocket; its managed Agora channel carries video.
import { fetchWithAccess, statusMessage } from './api.js';

export const LIVE_MODEL = 'vidu-s2-editing';
export const LIVE_CATEGORIES = ['top', 'bottom', 'dress', 'outerwear'];
const AGORA_SDK_URL = 'https://download.agora.io/sdk/release/AgoraRTC_N-4.24.8.js';
let sdkPromise;

function liveError(message, code) {
  const error = new Error(message);
  error.userMessage = message;
  error.code = code;
  return error;
}

export function loadAgoraSdk() {
  if (!sdkPromise) {
    sdkPromise = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      const timeout = setTimeout(() => finish(false), 12000);
      function finish(loaded) {
        clearTimeout(timeout);
        script.onload = null;
        script.onerror = null;
        if (loaded && window.AgoraRTC?.createClient) {
          window.AgoraRTC.setLogLevel(4);
          resolve(window.AgoraRTC);
          return;
        }
        script.remove();
        reject(liveError('Không tải được thư viện truyền video. Kiểm tra mạng rồi thử lại.'));
      }
      script.src = AGORA_SDK_URL;
      script.onload = () => finish(true);
      script.onerror = () => finish(false);
      document.head.append(script);
    }).catch((error) => { sdkPromise = null; throw error; });
  }
  return sdkPromise;
}

export async function requestViduSession(referenceImage, signal) {
  let response;
  try {
    response = await fetchWithAccess('/api/tryon/live-session', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ reference_image: referenceImage }),
      cache: 'no-store',
      signal,
    });
  } catch {
    if (signal?.aborted) throw signal.reason;
    throw liveError('Không kết nối được máy chủ SKI. Kiểm tra mạng rồi thử lại.');
  }
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = data?.detail;
    const message = typeof detail === 'string' ? detail : detail?.message;
    throw liveError(statusMessage(response.status, message) || message || 'Máy chủ không mở được phiên Vidu.', detail?.code);
  }
  if (!data?.live_id || !data.ws_url || !data.client_secret || !data.render_uid || data.rtc?.provider !== 'agora') {
    throw liveError('Máy chủ trả thông tin phiên Vidu không đầy đủ.');
  }
  return data;
}

export function slotsFromItems(items) {
  const slots = {};
  for (const item of items) {
    if (!item || !LIVE_CATEGORIES.includes(item.category)) continue;
    if (item.category === 'dress') { delete slots.top; delete slots.bottom; }
    if (item.category === 'top' || item.category === 'bottom') delete slots.dress;
    slots[item.category] = item;
  }
  return slots;
}

export function slotItems(slots) {
  return [slots.outerwear, slots.dress || slots.top, slots.bottom].filter(Boolean);
}

function loadGarmentImage(url) {
  return new Promise((resolve, reject) => {
    if (!url) { reject(liveError('Món đồ đã chọn chưa có ảnh.')); return; }
    const image = new Image();
    image.crossOrigin = 'anonymous';
    image.decoding = 'async';
    image.onload = () => resolve(image);
    image.onerror = () => reject(liveError('Không tải được ảnh món đồ.'));
    image.src = url;
  });
}

/** Vidu accepts one reference image. Arrange the selected garments top to bottom. */
export async function composeGarmentReference(items, sourceOf) {
  const images = await Promise.all(items.map((item) => loadGarmentImage(sourceOf(item))));
  const width = images.length === 1 ? 768 : 512;
  const maxHeight = images.length === 1 ? 1024 : 512;
  const tiles = images.map((image) => {
    const scale = Math.min(width / image.naturalWidth, maxHeight / image.naturalHeight);
    return { image, width: Math.max(1, Math.round(image.naturalWidth * scale)), height: Math.max(1, Math.round(image.naturalHeight * scale)) };
  });
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = tiles.reduce((sum, tile) => sum + tile.height, 0);
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#fff';
  ctx.fillRect(0, 0, width, canvas.height);
  let top = 0;
  for (const tile of tiles) {
    ctx.drawImage(tile.image, Math.round((width - tile.width) / 2), top, tile.width, tile.height);
    top += tile.height;
  }
  return canvas.toDataURL('image/jpeg', 0.88);
}

function agoraUid(rtc) {
  if (rtc.agora_int_uid) return rtc.agora_int_uid;
  const uid = String(rtc.user_id);
  const number = Number(uid);
  return Number.isInteger(number) && number > 0 && number <= 0xffffffff && String(number) === uid ? number : uid;
}

function connectionQuality(stats) {
  const score = Math.max(stats.uplinkNetworkQuality || 0, stats.downlinkNetworkQuality || 0);
  return [null, 'good', 'good', 'fair', 'poor', 'critical', 'critical'][score] || null;
}

function signalError(code) {
  const messages = {
    SIP_READY_TIMEOUT: 'Vidu không sẵn sàng kịp thời. Mở phiên mới để thử lại.',
    LIVE_ENDED: 'Phiên mặc thử đã kết thúc. Mở phiên mới để thử lại.',
    SWITCH_PROMPT_INVALID: 'Vidu không chấp nhận ảnh tham chiếu này.',
  };
  return liveError(messages[code] || 'Vidu không xử lý được yêu cầu mặc thử. Thử lại nhé.', code);
}

export function connectVidu({
  sdk, session, camera, signal, onStarted, onRemoteStream, onQuality, onDisconnected, onSwitchError,
}) {
  const { rtc, live_id: liveId, render_uid: renderUid } = session;
  const connId = crypto.randomUUID();
  const wsUrl = new URL(session.ws_url);
  wsUrl.search = new URLSearchParams({ live_id: liveId, conn_id: connId, client_secret: session.client_secret });
  const rtcClient = sdk.createClient({ mode: 'rtc', codec: 'h264' });
  let socket;
  let seq = 0;
  let initRetry;
  let initAttempts = 0;
  let rejectInit;
  let stopped = false;
  let joined = false;
  let initialized = false;
  let published = false;
  let cameraTrack;
  let localTrack;
  let remoteTrack;

  function send(type, payload) {
    if (socket?.readyState !== WebSocket.OPEN) throw liveError('Mất kết nối điều khiển với Vidu.');
    socket.send(JSON.stringify({ type, live_id: liveId, conn_id: connId, seq_id: ++seq, payload }));
  }

  function leaveChannel() {
    if (!joined) return;
    joined = false;
    void rtcClient.leave().catch(() => {});
  }

  function disconnect(error = new DOMException('Phiên đã dừng.', 'AbortError')) {
    if (stopped) return;
    stopped = true;
    signal?.removeEventListener('abort', abort);
    clearTimeout(initRetry);
    rejectInit?.(error);
    if (socket?.readyState === WebSocket.OPEN) {
      try { send(5, { hangup: { hangup_reason: 'user_hangup' } }); } catch { /* socket is closing */ }
    }
    socket?.close();
    cameraTrack?.stop();
    localTrack?.close();
    leaveChannel();
  }

  function abort() { disconnect(signal.reason); }

  function fail(error) {
    if (stopped) return;
    disconnect(error);
    if (initialized) onDisconnected?.(error);
  }

  function openControlSocket() {
    return new Promise((resolve, reject) => {
      rejectInit = reject;
      socket = new WebSocket(wsUrl);
      function init() {
        if (stopped || initialized) return;
        initAttempts += 1;
        try { send(1, { conn_init: { version: 1 } }); } catch (error) { fail(error); }
      }
      socket.addEventListener('open', init, { once: true });
      socket.addEventListener('error', () => fail(liveError('Không kết nối được với Vidu.')));
      socket.addEventListener('close', () => fail(liveError('Kết nối với Vidu đã kết thúc.')));
      socket.addEventListener('message', ({ data }) => {
        if (stopped || typeof data !== 'string') return;
        let message;
        try { message = JSON.parse(data); } catch { return; }
        if (message?.type === 2 && !initialized) {
          const ack = message.payload?.conn_init_ack;
          if (ack?.success === true) {
            initialized = true;
            clearTimeout(initRetry);
            onStarted?.();
            resolve();
          } else if (ack?.error_code === 'NOT_READY' && initAttempts < 8) {
            clearTimeout(initRetry);
            initRetry = setTimeout(init, 1000);
          } else {
            fail(signalError(ack?.error_code));
          }
        } else if (message?.type === 14) {
          onSwitchError?.(signalError(message.payload?.switch_prompt_ack?.error_code));
        } else if (message?.type === 5) {
          fail(signalError('LIVE_ENDED'));
        }
      });
    });
  }

  function showRemoteTrack() {
    if (!stopped && published && remoteTrack) onRemoteStream?.(new MediaStream([remoteTrack]));
  }

  rtcClient.on('user-published', async (user, mediaType) => {
    if (stopped || mediaType !== 'video' || String(user.uid) !== renderUid) return;
    try {
      const track = await rtcClient.subscribe(user, 'video');
      remoteTrack = track.getMediaStreamTrack();
      showRemoteTrack();
    } catch {
      fail(liveError('Không nhận được video mặc thử từ Vidu.'));
    }
  });
  rtcClient.on('network-quality', (stats) => {
    const quality = connectionQuality(stats);
    if (!stopped && quality) onQuality?.({ quality });
  });
  rtcClient.on('connection-state-change', (state) => {
    if (state === 'DISCONNECTED' && joined) fail(liveError('Mất kết nối video với Vidu.'));
  });
  rtcClient.on('user-left', (user) => {
    if (String(user.uid) === renderUid) fail(liveError('Vidu đã dừng video mặc thử.'));
  });

  const ready = (async () => {
    try {
      if (signal?.aborted) throw signal.reason;
      signal?.addEventListener('abort', abort, { once: true });
      const sourceTrack = camera.getVideoTracks()[0];
      if (!sourceTrack || sourceTrack.readyState !== 'live') throw liveError('Camera trước đã tắt. Mở lại camera để mặc thử.');
      await openControlSocket();
      if (stopped) return;
      await rtcClient.join(rtc.app_id, rtc.channel_id, rtc.token, agoraUid(rtc));
      joined = true;
      if (stopped) { leaveChannel(); return; }
      cameraTrack = sourceTrack.clone();
      localTrack = sdk.createCustomVideoTrack({ mediaStreamTrack: cameraTrack });
      await rtcClient.publish(localTrack);
      published = true;
      showRemoteTrack();
    } catch (error) {
      disconnect();
      if (error?.userMessage || error?.name === 'AbortError' || signal?.aborted) throw error;
      throw liveError('Không kết nối được kênh video của Vidu. Kiểm tra mạng rồi thử lại.');
    }
  })();

  return {
    ready,
    disconnect,
    switchReference(image) {
      send(13, { switch_prompt: { prompts: [{ type: 'image', content: image }], editing_type: 'virtual_tryon' } });
    },
  };
}
