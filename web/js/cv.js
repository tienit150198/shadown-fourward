// On-device hand tracking (MediaPipe Tasks Vision HandLandmarker, loaded lazily from jsDelivr).
// Detects "hands were moving and are now still for ~600 ms" (or "hands left the frame")
// and calls onTrigger('still') so the Guide screen can send a frame immediately.

const MP_VERSION = '0.10.14';
const MP_BASE = `https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@${MP_VERSION}`;
const MP_BUNDLE = `${MP_BASE}/vision_bundle.mjs`;
const MP_WASM = `${MP_BASE}/wasm`;
const MODEL_URL = 'https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task';

const KEYPOINTS = [0, 4, 8, 12, 16, 20]; // wrist + fingertips
const CONNECTIONS = [
  [0, 1], [1, 2], [2, 3], [3, 4], [0, 5], [5, 6], [6, 7], [7, 8], [5, 9], [9, 10], [10, 11], [11, 12],
  [9, 13], [13, 14], [14, 15], [15, 16], [13, 17], [17, 18], [18, 19], [19, 20], [0, 17],
];

const MOVE_SPEED = 0.22;   // normalized frame units / second → "moving"
const STILL_SPEED = 0.07;  // below this → "still"
const STILL_MS = 600;
const COOLDOWN_MS = 1000;
const TICK_MS = 70;

const landmarkerPromises = new Map(); // delegate preference → Promise<HandLandmarker>

async function createLandmarker(preferCpu) {
  const vision = await import(/* @vite-ignore */ MP_BUNDLE);
  const fileset = await vision.FilesetResolver.forVisionTasks(MP_WASM);
  const options = (delegate) => ({
    baseOptions: { modelAssetPath: MODEL_URL, delegate },
    runningMode: 'VIDEO',
    numHands: 2,
    minHandDetectionConfidence: 0.5,
    minHandPresenceConfidence: 0.5,
    minTrackingConfidence: 0.5,
  });
  if (!preferCpu) {
    try {
      const lm = await vision.HandLandmarker.createFromOptions(fileset, options('GPU'));
      lm.__delegate = 'GPU';
      return lm;
    } catch (err) {
      console.info('[cv] GPU delegate unavailable, using CPU', err?.message);
    }
  }
  const lm = await vision.HandLandmarker.createFromOptions(fileset, options('CPU'));
  lm.__delegate = 'CPU';
  return lm;
}

/** Loads (once per delegate preference) the HandLandmarker. Rejects if offline / unsupported. */
export function loadHandLandmarker({ preferCpu = false } = {}) {
  const key = preferCpu ? 'cpu' : 'auto';
  if (!landmarkerPromises.has(key)) {
    landmarkerPromises.set(key, createLandmarker(preferCpu).catch((err) => {
      landmarkerPromises.delete(key); // allow retry later
      throw err;
    }));
  }
  return landmarkerPromises.get(key);
}

/**
 * @param {{video:HTMLVideoElement, overlay?:HTMLCanvasElement, onTrigger:(reason:string)=>void,
 *          onStatus?:(status:'loading'|'ready'|'failed'|'off')=>void, drawOverlay?:boolean}} opts
 */
export function createStillnessDetector({ video, overlay, onTrigger, onStatus, drawOverlay = true }) {
  let landmarker = null;
  let running = false;
  let timer = null;
  let lastVideoTime = -1;
  let prev = null;          // {x, y, t}
  let speed = 0;            // smoothed
  let moving = false;
  let stillSince = null;
  let handsPresent = false;
  let lastTrigger = 0;
  let showOverlay = drawOverlay;
  let errorStreak = 0;
  let recovering = false;
  const status = (s) => { try { onStatus?.(s); } catch { /* ignore */ } };

  function fire(reason) {
    const now = performance.now();
    if (now - lastTrigger < COOLDOWN_MS) return;
    lastTrigger = now;
    try { onTrigger(reason); } catch (err) { console.error(err); }
  }

  function clearOverlay() {
    if (!overlay) return;
    const ctx = overlay.getContext('2d');
    ctx.clearRect(0, 0, overlay.width, overlay.height);
  }

  function draw(hands) {
    if (!overlay) return;
    const rect = overlay.getBoundingClientRect();
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const W = Math.round(rect.width * dpr);
    const H = Math.round(rect.height * dpr);
    if (overlay.width !== W) overlay.width = W;
    if (overlay.height !== H) overlay.height = H;
    const ctx = overlay.getContext('2d');
    ctx.clearRect(0, 0, W, H);
    if (!showOverlay || !hands.length || !video.videoWidth) return;
    // map normalized video coords into an object-fit: cover box
    const vw = video.videoWidth;
    const vh = video.videoHeight;
    const scale = Math.max(W / vw, H / vh);
    const ox = (W - vw * scale) / 2;
    const oy = (H - vh * scale) / 2;
    const px = (p) => [ox + p.x * vw * scale, oy + p.y * vh * scale];
    ctx.lineWidth = 2.5 * dpr;
    ctx.lineCap = 'round';
    ctx.strokeStyle = moving ? 'rgba(255,210,63,0.75)' : 'rgba(255,255,255,0.55)';
    ctx.fillStyle = moving ? 'rgba(255,210,63,0.95)' : 'rgba(255,255,255,0.85)';
    for (const hand of hands) {
      ctx.beginPath();
      for (const [a, b] of CONNECTIONS) {
        const [x1, y1] = px(hand[a]);
        const [x2, y2] = px(hand[b]);
        ctx.moveTo(x1, y1);
        ctx.lineTo(x2, y2);
      }
      ctx.stroke();
      for (const i of KEYPOINTS) {
        const [x, y] = px(hand[i]);
        ctx.beginPath();
        ctx.arc(x, y, 3.5 * dpr, 0, Math.PI * 2);
        ctx.fill();
      }
    }
  }

  function tick() {
    timer = null;
    if (!running) return;
    try {
      if (landmarker && video.readyState >= 2 && document.visibilityState === 'visible' && video.currentTime !== lastVideoTime) {
        lastVideoTime = video.currentTime;
        const now = performance.now();
        const result = landmarker.detectForVideo(video, now);
        const hands = result?.landmarks || [];
        errorStreak = 0;
        analyse(hands, now);
        draw(hands);
      }
    } catch (err) {
      errorStreak += 1;
      if (errorStreak === 1) console.warn('[cv] detect failed', err?.message || err);
      if (errorStreak >= 3) { recover(); return; }
    }
    timer = setTimeout(tick, TICK_MS);
  }

  // GPU delegate can initialise but fail at runtime (no WebGL) → retry once on CPU, else give up quietly.
  async function recover() {
    if (recovering) return;
    recovering = true;
    const wasGpu = landmarker?.__delegate === 'GPU';
    landmarker = null;
    if (wasGpu) {
      try {
        landmarker = await loadHandLandmarker({ preferCpu: true });
        errorStreak = 0;
        recovering = false;
        if (running) timer = setTimeout(tick, TICK_MS);
        return;
      } catch (err) {
        console.warn('[cv] CPU fallback failed', err?.message || err);
      }
    }
    console.warn('[cv] hand tracking disabled — periodic frames only');
    running = false;
    recovering = false;
    clearOverlay();
    status('failed');
  }

  function analyse(hands, now) {
    if (!hands.length) {
      if (handsPresent && moving) fire('still'); // hands left the frame after working
      handsPresent = false;
      moving = false;
      stillSince = null;
      prev = null;
      speed = 0;
      return;
    }
    let sx = 0; let sy = 0; let n = 0;
    for (const hand of hands) {
      for (const i of KEYPOINTS) { sx += hand[i].x; sy += hand[i].y; n += 1; }
    }
    const cur = { x: sx / n, y: sy / n, t: now, count: hands.length };
    if (prev) {
      const dt = Math.max(0.016, (now - prev.t) / 1000);
      let inst = Math.hypot(cur.x - prev.x, cur.y - prev.y) / dt;
      if (cur.count !== prev.count) inst = Math.max(inst, MOVE_SPEED * 1.2); // hand appeared/disappeared
      speed = speed * 0.5 + inst * 0.5;
    }
    prev = cur;
    handsPresent = true;
    if (speed > MOVE_SPEED) {
      moving = true;
      stillSince = null;
    } else if (speed < STILL_SPEED) {
      stillSince = stillSince ?? now;
      if (moving && now - stillSince >= STILL_MS) {
        moving = false;
        fire('still');
      }
    } else {
      stillSince = null;
    }
  }

  return {
    async start() {
      if (running) return true;
      running = true;
      status('loading');
      try {
        landmarker = await loadHandLandmarker();
      } catch (err) {
        console.warn('[cv] MediaPipe unavailable — falling back to periodic frames only', err);
        running = false;
        status('failed');
        return false;
      }
      if (!running) return false; // stopped while loading
      status('ready');
      tick();
      return true;
    },
    stop() {
      running = false;
      clearTimeout(timer);
      timer = null;
      prev = null;
      moving = false;
      clearOverlay();
      status('off');
    },
    setOverlay(on) {
      showOverlay = !!on;
      if (!showOverlay) clearOverlay();
    },
    get isMoving() { return moving; },
  };
}
