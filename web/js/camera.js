// Rear camera access + frame capture (JPEG data URL, longest side 512px).

export class CameraError extends Error {
  constructor(message, code) {
    super(message);
    this.name = 'CameraError';
    this.code = code;
    this.userMessage = message;
  }
}

function explain(err) {
  if (!window.isSecureContext) {
    return new CameraError(
      'Camera chỉ chạy trên kết nối bảo mật (HTTPS). Hãy mở app bằng đường dẫn https:// (ví dụ link ngrok) hoặc http://localhost.',
      'insecure',
    );
  }
  if (!navigator.mediaDevices?.getUserMedia) {
    return new CameraError('Trình duyệt này không hỗ trợ camera. Hãy dùng Safari (iOS) hoặc Chrome (Android) bản mới.', 'unsupported');
  }
  const name = err?.name || '';
  if (name === 'NotAllowedError' || name === 'SecurityError') {
    return new CameraError('Bạn chưa cho phép dùng camera. Mở cài đặt trang web → cho phép Camera, rồi thử lại.', 'denied');
  }
  if (name === 'NotFoundError' || name === 'OverconstrainedError') {
    return new CameraError('Không tìm thấy camera trên thiết bị này.', 'notfound');
  }
  if (name === 'NotReadableError' || name === 'AbortError') {
    return new CameraError('Camera đang được ứng dụng khác sử dụng. Đóng ứng dụng đó rồi thử lại.', 'busy');
  }
  return new CameraError(`Không mở được camera${err?.message ? `: ${err.message}` : ''}.`, 'unknown');
}

/**
 * Starts the camera, optionally attaching it to a preview <video> element.
 * @returns {Promise<MediaStream>}
 */
export async function startCamera(video, { facingMode = 'environment' } = {}) {
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) throw explain(null);
  const attempts = [
    { video: { facingMode: { ideal: facingMode }, width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false },
    { video: { facingMode: { ideal: facingMode } }, audio: false },
    { video: true, audio: false },
  ];
  let lastErr = null;
  for (const constraints of attempts) {
    try {
      const stream = await navigator.mediaDevices.getUserMedia(constraints);
      if (video) {
        video.setAttribute('playsinline', '');
        video.setAttribute('autoplay', '');
        video.muted = true;
        video.srcObject = stream;
        try { await video.play(); } catch { /* autoplay may resolve later on user gesture */ }
      }
      return stream;
    } catch (err) {
      lastErr = err;
      // permission errors won't be fixed by relaxing constraints
      if (err?.name === 'NotAllowedError' || err?.name === 'SecurityError') break;
    }
  }
  throw explain(lastErr);
}

export function stopCamera(stream, video) {
  try { stream?.getTracks().forEach((t) => t.stop()); } catch { /* ignore */ }
  if (video) {
    try { video.pause(); } catch { /* ignore */ }
    video.srcObject = null;
  }
}

let sharedCanvas = null;

/**
 * Grabs the current video frame as a JPEG data URL (longest side ≤ maxSide).
 * Returns null when the video has no frame yet.
 */
export function captureFrame(video, { maxSide = 512, quality = 0.7 } = {}) {
  if (!video || video.readyState < 2 || !video.videoWidth || !video.videoHeight) return null;
  const w = video.videoWidth;
  const hgt = video.videoHeight;
  const scale = Math.min(1, maxSide / Math.max(w, hgt));
  const cw = Math.round(w * scale);
  const ch = Math.round(hgt * scale);
  sharedCanvas = sharedCanvas || document.createElement('canvas');
  if (sharedCanvas.width !== cw) sharedCanvas.width = cw;
  if (sharedCanvas.height !== ch) sharedCanvas.height = ch;
  const ctx = sharedCanvas.getContext('2d');
  ctx.drawImage(video, 0, 0, cw, ch);
  try {
    return sharedCanvas.toDataURL('image/jpeg', quality);
  } catch {
    return null;
  }
}

/** Keeps the screen awake while a camera screen is open. Returns a release function. */
export function keepScreenAwake() {
  let lock = null;
  let released = false;
  const acquire = async () => {
    if (released || !('wakeLock' in navigator) || document.visibilityState !== 'visible') return;
    try {
      lock = await navigator.wakeLock.request('screen');
    } catch { /* not allowed – fine */ }
  };
  const onVis = () => { if (document.visibilityState === 'visible') acquire(); };
  document.addEventListener('visibilitychange', onVis);
  acquire();
  return () => {
    released = true;
    document.removeEventListener('visibilitychange', onVis);
    try { lock?.release(); } catch { /* ignore */ }
  };
}
