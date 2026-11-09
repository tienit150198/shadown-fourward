// On-device body pose for the live try-on screen (MediaPipe Tasks Vision PoseLandmarker,
// same pinned jsDelivr build as cv.js). Two jobs, both optional and silent on failure:
//   1. framing check  — are the head and at least one knee inside the camera frame?
//      (port of tryon-examples/examples/mobile hooks/usePoseBodyVisibility.ts + useFitGate.ts)
//   2. raise-hand gesture — wrist above shoulder held for 1.5 s → 'next' / 'prev'
//      (port of tryon-examples/examples/mobile-fitting-room hooks/useHandGesture.ts)
// Detection runs in IMAGE mode on a downscaled canvas copy of the LOCAL camera (never the AI
// video), at a few Hz. If a detect() call is slow on this device the loop backs off and the
// gesture is switched off, so the WebRTC session keeps priority.

const MP_VERSION = '0.10.14';
const MP_BASE = `https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@${MP_VERSION}`;
const MP_BUNDLE = `${MP_BASE}/vision_bundle.mjs`;
const MP_WASM = `${MP_BASE}/wasm`;
const MODEL_URL = 'https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task';

// MediaPipe pose landmark indices
const NOSE = 0;
const L_SHOULDER = 11;
const R_SHOULDER = 12;
const L_WRIST = 15;
const R_WRIST = 16;
const L_HIP = 23;
const R_HIP = 24;
const L_KNEE = 25;
const R_KNEE = 26;

// From tryon-examples/examples/mobile lib/constants.ts: a knee below the frame reads y > 1
// (MediaPipe keeps predicting off-screen landmarks), which is more reliable than visibility.
// The example uses 1.0; we keep a 5 % margin because a knee cut exactly at the bottom edge is
// predicted at y ≈ 0.99–1.00 (measured on a waist-up photo), which would otherwise pass.
const KNEE_IN_FRAME_MAX_Y = 0.95;
const LOAD_TIMEOUT_MS = 8000;

// From mobile-fitting-room useHandGesture.ts
const DWELL_MS = 1500;
const LOST_GRACE_MS = 500;

const BASE_INTERVAL_MS = 320;
const SLOW_INTERVAL_MS = 900;
const SLOW_DETECT_MS = 90; // average detect() cost above this → back off + disable gesture
const DETECT_MAX_SIDE = 320;

let landmarkerPromise = null;

async function createLandmarker() {
  const vision = await import(/* @vite-ignore */ MP_BUNDLE);
  const fileset = await vision.FilesetResolver.forVisionTasks(MP_WASM);
  const options = (delegate) => ({
    baseOptions: { modelAssetPath: MODEL_URL, delegate },
    // IMAGE, not VIDEO: VIDEO mode tracks a pose across calls and keeps reporting the knees
    // as visible after the person steps in close (rationale from usePoseBodyVisibility.ts).
    runningMode: 'IMAGE',
    numPoses: 1,
    minPoseDetectionConfidence: 0.4,
    minPosePresenceConfidence: 0.4,
    minTrackingConfidence: 0.4,
  });
  try {
    return await vision.PoseLandmarker.createFromOptions(fileset, options('GPU'));
  } catch (err) {
    console.info('[pose] GPU delegate unavailable, using CPU', err?.message);
    return vision.PoseLandmarker.createFromOptions(fileset, options('CPU'));
  }
}

/** Loads the PoseLandmarker once. Resolves null (never rejects) when it cannot be loaded. */
export function loadPoseLandmarker() {
  if (!landmarkerPromise) {
    landmarkerPromise = createLandmarker().catch((err) => {
      console.info('[pose] unavailable:', err?.message || err);
      landmarkerPromise = null; // allow a later retry
      return null;
    });
  }
  return landmarkerPromise;
}

const vis = (p) => (p && (p.visibility === undefined || p.visibility > 0.5));

/**
 * Framing verdict for one pose.
 * @returns {'ok'|'far'|'head'|null} null = cannot tell (no pose).
 *   'far'  → knees (or hips, for a top-only outfit) are out of shot: step / move the phone back.
 *   'head' → head is cut off at the top.
 */
export function framingVerdict(pose, { needKnees = true } = {}) {
  if (!pose) return null;
  const nose = pose[NOSE];
  if (nose && nose.y < 0) return 'head';
  const pts = needKnees ? [pose[L_KNEE], pose[R_KNEE]] : [pose[L_HIP], pose[R_HIP]];
  const ys = pts.filter(Boolean).map((p) => p.y);
  if (!ys.length) return null;
  return Math.min(...ys) <= KNEE_IN_FRAME_MAX_Y ? 'ok' : 'far';
}

/**
 * Raise-hand dwell gesture (pure; port of mobile-fitting-room useHandGesture.ts): a wrist above
 * its shoulder for DWELL_MS fires once; the hand must come down before the same side fires again.
 * The person's RIGHT hand → 'next', LEFT hand → 'prev' (with the mirrored preview the right hand
 * is on the right of the screen, matching the "next" arrow).
 */
export function createGestureTracker({ onGesture, onDwell, dwellMs = DWELL_MS, lostGraceMs = LOST_GRACE_MS } = {}) {
  let dwellDir = null;
  let dwellStart = null;
  let firedDir = null;
  let lastSeen = 0;
  const reset = (full = false) => {
    if (dwellDir !== null) onDwell?.(null, 0);
    dwellDir = null;
    dwellStart = null;
    if (full) firedDir = null;
  };
  return {
    reset,
    update(pose, now) {
      if (!pose) {
        if (now - lastSeen > lostGraceMs) reset(true);
        return null;
      }
      lastSeen = now;
      const lw = pose[L_WRIST]; const rw = pose[R_WRIST];
      const ls = pose[L_SHOULDER]; const rs = pose[R_SHOULDER];
      const leftUp = vis(lw) && vis(ls) && lw.y < ls.y;
      const rightUp = vis(rw) && vis(rs) && rw.y < rs.y;
      let dir = null;
      if (rightUp && leftUp) dir = rw.y < lw.y ? 'next' : 'prev';
      else if (rightUp) dir = 'next';
      else if (leftUp) dir = 'prev';
      if (!dir) { reset(true); return null; }
      if (dir === firedDir) { reset(); return null; } // lower the hand before firing again
      if (dir !== dwellDir) { dwellDir = dir; dwellStart = now; }
      const progress = Math.min((now - dwellStart) / dwellMs, 1);
      onDwell?.(dir, progress);
      if (progress >= 1) {
        firedDir = dir;
        reset();
        onGesture?.(dir);
        return dir;
      }
      return null;
    },
  };
}

/**
 * @param {{
 *   video: HTMLVideoElement,               // local camera preview (playing)
 *   needKnees?: () => boolean,              // full outfit (has bottoms/dress) → knees, else hips
 *   onFraming?: (v: 'ok'|'far'|'head'|null) => void,   // confirmed on 2 agreeing frames
 *   onGesture?: (dir: 'next'|'prev') => void,
 *   onDwell?: (dir: 'next'|'prev'|null, progress: number) => void,
 *   onStatus?: (s: 'loading'|'ready'|'failed'|'slow') => void,
 * }} opts
 */
export function createPoseWatcher({ video, needKnees = () => true, onFraming, onGesture, onDwell, onStatus }) {
  let landmarker = null;
  let timer = null;
  let running = false;
  let disposed = false;
  let gestureOn = false;
  let interval = BASE_INTERVAL_MS;
  let avgMs = 0;
  let samples = 0;
  let slow = false;
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d', { willReadFrequently: false });

  // framing: 2 agreeing frames before reporting a change
  let lastRaw;
  let reported;
  const safe = (fn, ...args) => { try { fn?.(...args); } catch (err) { console.warn('[pose] callback failed', err); } };
  const gesture = createGestureTracker({
    onGesture: (dir) => safe(onGesture, dir),
    onDwell: (dir, p) => safe(onDwell, dir, p),
  });
  const resetDwell = () => gesture.reset();
  const handleGesture = (pose, now) => { if (gestureOn && !slow) gesture.update(pose, now); };

  function tick() {
    timer = null;
    if (disposed || !running) return;
    const now = performance.now();
    try {
      if (landmarker && video.readyState >= 2 && video.videoWidth && document.visibilityState === 'visible') {
        const scale = Math.min(1, DETECT_MAX_SIDE / Math.max(video.videoWidth, video.videoHeight));
        const w = Math.round(video.videoWidth * scale);
        const hgt = Math.round(video.videoHeight * scale);
        if (canvas.width !== w) canvas.width = w;
        if (canvas.height !== hgt) canvas.height = hgt;
        ctx.drawImage(video, 0, 0, w, hgt);
        const t0 = performance.now();
        const pose = landmarker.detect(canvas)?.landmarks?.[0] || null;
        const dt = performance.now() - t0;
        samples += 1;
        avgMs = samples === 1 ? dt : avgMs * 0.8 + dt * 0.2;
        if (!slow && samples > 5 && avgMs > SLOW_DETECT_MS) {
          slow = true;
          interval = SLOW_INTERVAL_MS;
          resetDwell();
          safe(onStatus, 'slow');
        }
        const raw = framingVerdict(pose, { needKnees: needKnees() });
        if (raw === lastRaw && raw !== reported) { reported = raw; safe(onFraming, raw); }
        lastRaw = raw;
        handleGesture(pose, now);
      }
    } catch (err) {
      console.info('[pose] detect failed, stopping', err?.message);
      running = false;
      safe(onStatus, 'failed');
      return;
    }
    timer = setTimeout(tick, interval);
  }

  async function start() {
    if (disposed || running) return;
    running = true;
    if (!landmarker) {
      safe(onStatus, 'loading');
      const lm = await Promise.race([
        loadPoseLandmarker(),
        new Promise((resolve) => setTimeout(() => resolve(null), LOAD_TIMEOUT_MS)),
      ]);
      if (disposed) return;
      if (!lm) {
        running = false;
        safe(onStatus, 'failed');
        // keep warming in the background; a later start() may succeed
        return;
      }
      landmarker = lm;
      safe(onStatus, slow ? 'slow' : 'ready');
    }
    if (running && !timer) timer = setTimeout(tick, 0);
  }

  return {
    start,
    stop() { running = false; clearTimeout(timer); timer = null; resetDwell(); },
    setGesture(on) { gestureOn = !!on; if (!gestureOn) gesture.reset(true); },
    get gestureAvailable() { return !slow && !!landmarker; },
    get slow() { return slow; },
    /** Forget the last framing verdict so the next confirmed one is reported again. */
    resetFraming() { lastRaw = undefined; reported = undefined; },
    dispose() {
      disposed = true;
      running = false;
      clearTimeout(timer);
      timer = null;
      // The landmarker is shared/cached for the next visit; do not close it.
    },
  };
}
