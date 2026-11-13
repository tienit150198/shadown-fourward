import { fetchWithAccess } from './api.js';

export function liveVoiceSupported() {
  const Context = globalThis.AudioContext || globalThis.webkitAudioContext;
  return !!(Context && globalThis.AudioWorkletNode && globalThis.navigator?.mediaDevices?.getUserMedia && globalThis.WebSocket);
}

export async function liveVoiceConfig(signal) {
  const response = await fetchWithAccess('/api/voice/live/config', { signal, cache: 'no-store' });
  if (!response.ok) throw new Error('Không tải được cấu hình giọng nói trực tiếp.');
  return response.json();
}

export function pcmFromBase64(encoded) {
  if (typeof encoded !== 'string' || !encoded.length || encoded.length > 262144) throw new Error('Invalid audio frame');
  const binary = atob(encoded);
  if (binary.length % 2) throw new Error('Invalid PCM frame');
  const bytes = Uint8Array.from(binary, char => char.charCodeAt(0));
  const view = new DataView(bytes.buffer);
  return Float32Array.from({ length: binary.length / 2 }, (_, i) => view.getInt16(i * 2, true) / 32768);
}

export function bytesToBase64(bytes) {
  let binary = '';
  for (const value of bytes) binary += String.fromCharCode(value);
  return btoa(binary);
}

// The relay owns the Google key, app tools and server-enforced duration cap.
export async function startGeminiLive({ configuredCity, history = [], conversationId, onEvent, onClose, signal }) {
  const Context = globalThis.AudioContext || globalThis.webkitAudioContext;
  const context = new Context();
  let stream, source, capture, silent, socket, ready = false, closed = false, timer;
  let nextAudio = 0;
  const playing = new Set();
  let rejectReady;
  const cancelPlayback = () => {
    for (const node of playing) { try { node.stop(); } catch { /* already stopped */ } }
    playing.clear();
    nextAudio = 0;
  };
  const close = (reason = 'Đã tắt giọng nói trực tiếp.') => {
    if (closed) return;
    closed = true;
    clearTimeout(timer);
    signal?.removeEventListener('abort', abort);
    cancelPlayback();
    if (capture) { capture.port.onmessage = null; capture.disconnect(); }
    source?.disconnect(); silent?.disconnect();
    stream?.getTracks().forEach(track => track.stop());
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: 'stop' }));
      socket.close(1000);
    } else if (socket && socket.readyState < WebSocket.CLOSING) socket.close();
    void context.close().catch(() => {});
    rejectReady?.(new Error(reason));
    onClose?.(reason);
  };
  const abort = () => close();
  signal?.addEventListener('abort', abort, { once: true });
  try {
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
    // Resume is initiated by the mic tap, before permission or network awaits.
    await context.resume();
    stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true }, video: false });
    if (closed || signal?.aborted) { stream.getTracks().forEach(track => track.stop()); throw new DOMException('Aborted', 'AbortError'); }
    await context.audioWorklet.addModule(new URL('./pcm-capture-worklet.js', import.meta.url));
    if (closed) throw new DOMException('Aborted', 'AbortError');
    source = context.createMediaStreamSource(stream);
    capture = new AudioWorkletNode(context, 'shadow-pcm-capture', { numberOfInputs: 1, numberOfOutputs: 1, channelCount: 1 });
    capture.onprocessorerror = () => close('Không xử lý được âm thanh micro. Mở lại phiên để tiếp tục.');
    silent = context.createGain(); silent.gain.value = 0;
    source.connect(capture); capture.connect(silent); silent.connect(context.destination);
    const url = new URL('/session/voice', location.href);
    url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    socket = new WebSocket(url);
    const connected = new Promise((resolve, reject) => {
      rejectReady = reject;
      timer = setTimeout(() => close('Kết nối giọng nói quá lâu. Bạn có thể thử lại.'), 20000);
      socket.onopen = () => socket.send(JSON.stringify({ type: 'start', configured_city: configuredCity, history: history.slice(-6), ...(conversationId ? { conversation_id: conversationId } : {}) }));
      socket.onerror = () => close('Không kết nối được giọng nói trực tiếp.');
      socket.onclose = () => close('Phiên giọng nói trực tiếp đã kết thúc.');
      socket.onmessage = event => {
        if (closed) return;
        try {
          if (typeof event.data !== 'string' || event.data.length > 600000) throw new Error('Invalid live message');
          const message = JSON.parse(event.data);
          if (message.type === 'ready') {
            clearTimeout(timer); ready = true;
            const seconds = Math.min(900, Math.max(1, Number(message.max_session_seconds) || 180));
            timer = setTimeout(() => close('Đã hết thời gian phiên giọng nói. Bấm micro để mở lại.'), seconds * 1000);
            resolve();
          } else if (message.type === 'audio') {
            if (!ready || message.sample_rate !== 24000) throw new Error('Unsupported output audio');
            const pcm = pcmFromBase64(message.data);
            if (playing.size >= 512 || Math.max(nextAudio, context.currentTime) + pcm.length / 24000 - context.currentTime > 15) throw new Error('Âm thanh bị chậm. Mở lại phiên để tiếp tục.');
            const buffer = context.createBuffer(1, pcm.length, 24000);
            buffer.copyToChannel(pcm, 0);
            const node = context.createBufferSource(); node.buffer = buffer; node.connect(context.destination);
            playing.add(node); node.onended = () => { playing.delete(node); node.disconnect(); };
            nextAudio = Math.max(nextAudio, context.currentTime + 0.025);
            node.start(nextAudio); nextAudio += buffer.duration;
          } else if (message.type === 'interrupted') cancelPlayback();
          else if (message.type === 'error') {
            onEvent?.(message);
            close(message.message || 'Dịch vụ giọng nói gặp lỗi.');
            return;
          } else if (message.type === 'closed') { close(message.reason || 'Phiên giọng nói đã kết thúc.'); return; }
          onEvent?.(message);
        } catch (error) { close(error.message || 'Không đọc được phản hồi giọng nói.'); }
      };
    });
    capture.port.onmessage = ({ data }) => {
      if (closed || !ready || socket.readyState !== WebSocket.OPEN) return;
      if (socket.bufferedAmount > 128000) { close('Mạng quá chậm để truyền giọng nói trực tiếp.'); return; }
      socket.send(JSON.stringify({ type: 'audio', data: bytesToBase64(new Uint8Array(data)) }));
    };
    await connected;
    rejectReady = null;
    return { stop: close, sendText(text) {
      if (!closed && ready && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'text', text: String(text).slice(0, 500) }));
    } };
  } catch (error) {
    close(error.message || 'Không mở được micro.');
    throw error;
  }
}
